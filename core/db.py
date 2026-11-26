"""SQLite 存储层。"""
import sqlite3
import os
import time
import json

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY,
    path          TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    stem          TEXT NOT NULL,
    ext           TEXT NOT NULL,
    kind          TEXT NOT NULL,
    size          INTEGER,
    mtime         INTEGER,
    width         INTEGER,
    height        INTEGER,
    aspect        REAL,
    phash         TEXT,
    phash_bits    INTEGER,
    orb           BLOB,
    palette       TEXT,
    thumb_path    TEXT,
    thumb_kind    TEXT,
    extract_error TEXT,
    group_id      INTEGER,
    indexed_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_phash ON files(phash_bits);
CREATE INDEX IF NOT EXISTS idx_stem  ON files(stem);
CREATE INDEX IF NOT EXISTS idx_kind  ON files(kind);
CREATE INDEX IF NOT EXISTS idx_ext   ON files(ext);
CREATE INDEX IF NOT EXISTS idx_group ON files(group_id);

CREATE TABLE IF NOT EXISTS groups (
    id          INTEGER PRIMARY KEY,
    cover_id    INTEGER,
    source_id   INTEGER,
    confidence  REAL,
    reason      TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS roots (
    path         TEXT PRIMARY KEY,
    indexed_at   INTEGER
);

-- 画面语义向量（中文 CLIP）。单独一张表而不是给 files 加列：
--   1. 只有源文件会被编码，大部分导出图没有向量，加列会留下一整批 NULL
--   2. 向量是 2KB 的 BLOB，跟 files 放一起会让每次 SELECT * 都很重
--   3. 重建索引时可以单独清空重算，不影响文件元数据
CREATE TABLE IF NOT EXISTS embeddings (
    file_id      INTEGER PRIMARY KEY,
    vec          BLOB NOT NULL,
    model        TEXT NOT NULL,
    created_at   INTEGER
);

-- 人脸检测结果。单独一张表而非给 embeddings 加列：
-- 两者由不同模型产生，某个模型缺失时另一个仍然有效。
-- 存数量不存布尔 —— 以后想筛「人多」的场景需要这个信息。
CREATE TABLE IF NOT EXISTS face_counts (
    file_id      INTEGER PRIMARY KEY,
    faces        INTEGER NOT NULL,
    model        TEXT NOT NULL,
    created_at   INTEGER
);

-- 图里的文字（OCR）。一个文件对应多行：一张海报上通常有 10~40 段文字，
-- 每段单独一行，这样搜「开业庆典」可以精确定位到具体哪一段命中，
-- 也方便以后做「结果里标出文字位置」。
--
-- **不用 FTS5 trigram 索引**，原因：trigram 只支持 3 字以上的查询，
-- 「曼谷」「自费」这种两字词直接搜不到 —— 而中文里两字词极多。
-- 实测 40 万行数据下 LIKE '%开业庆典%' 只要 44ms，对用户完全无感，
-- 换来的是任意长度、任意位置子串都能搜到。少一层索引少一处不一致。
CREATE TABLE IF NOT EXISTS texts (
    id       INTEGER PRIMARY KEY,
    file_id  INTEGER NOT NULL,
    content  TEXT NOT NULL,
    score    REAL,
    box      TEXT,
    model    TEXT,
    created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_texts_file ON texts(file_id);
"""


class DB:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA synchronous=NORMAL')
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # ---------- 写入 ----------

    def upsert(self, rec):
        cols = ('path', 'name', 'stem', 'ext', 'kind', 'size', 'mtime',
                'width', 'height', 'aspect', 'phash', 'phash_bits', 'orb',
                'palette', 'thumb_path', 'thumb_kind', 'extract_error',
                'indexed_at')
        vals = [rec.get(c) for c in cols]
        q = (f"INSERT INTO files ({','.join(cols)}) "
             f"VALUES ({','.join('?' * len(cols))}) "
             f"ON CONFLICT(path) DO UPDATE SET "
             + ','.join(f"{c}=excluded.{c}" for c in cols if c != 'path'))
        self.conn.execute(q, vals)

    def commit(self):
        self.conn.commit()

    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)))
        self.conn.commit()

    def get_meta(self, key, default=None):
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r['value'] if r else default

    def add_root(self, path):
        # 统一成分隔符，避免 E:/ 和 E:\ 被当成两个根
        norm = os.path.normpath(os.path.abspath(path))
        if norm.endswith(':'):
            norm += os.sep
        self.conn.execute(
            "INSERT INTO roots(path,indexed_at) VALUES(?,?) "
            "ON CONFLICT(path) DO UPDATE SET indexed_at=excluded.indexed_at",
            (norm, int(time.time())))
        self.conn.commit()

    # ---------- 读取 ----------

    def all_files(self, with_orb=False):
        cols = ('id,path,name,stem,ext,kind,size,mtime,width,height,aspect,'
                'phash,phash_bits,palette,thumb_path,thumb_kind,'
                'extract_error,group_id')
        if with_orb:
            cols += ',orb'
        return self.conn.execute(f"SELECT {cols} FROM files").fetchall()

    def get(self, file_id):
        return self.conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()

    def stats(self):
        total = self.conn.execute("SELECT COUNT(*) c FROM files").fetchone()['c']
        by_ext = self.conn.execute(
            "SELECT ext, COUNT(*) c FROM files GROUP BY ext ORDER BY c DESC").fetchall()
        by_kind = self.conn.execute(
            "SELECT kind, COUNT(*) c FROM files GROUP BY kind ORDER BY c DESC").fetchall()
        errors = self.conn.execute(
            "SELECT extract_error, COUNT(*) c FROM files WHERE extract_error IS NOT NULL "
            "GROUP BY extract_error ORDER BY c DESC").fetchall()
        return {
            'total': total,
            'by_ext': [(r['ext'], r['c']) for r in by_ext],
            'by_kind': [(r['kind'], r['c']) for r in by_kind],
            'errors': [(r['extract_error'], r['c']) for r in errors],
        }

    def existing_paths(self):
        return {r['path'] for r in
                self.conn.execute("SELECT path FROM files").fetchall()}

    def delete_missing(self, keep_paths):
        """删除已不存在于磁盘的记录。"""
        cur = self.conn.execute("SELECT id,path FROM files").fetchall()
        gone = [(r['id'],) for r in cur if r['path'] not in keep_paths]
        if gone:
            self.conn.executemany("DELETE FROM files WHERE id=?", gone)
            self.conn.commit()
        return len(gone)

    def clear_groups(self):
        self.conn.execute("DELETE FROM groups")
        self.conn.execute("UPDATE files SET group_id=NULL")
        self.conn.commit()

    def set_group(self, gid, cover_id, source_id, confidence, reason,
                  member_ids):
        self.conn.execute(
            "INSERT INTO groups(id,cover_id,source_id,confidence,reason) "
            "VALUES(?,?,?,?,?)",
            (gid, cover_id, source_id, confidence,
             json.dumps(reason, ensure_ascii=False)))
        self.conn.executemany("UPDATE files SET group_id=? WHERE id=?",
                              [(gid, i) for i in member_ids])
        self.conn.commit()

    def next_group_id(self):
        r = self.conn.execute("SELECT COALESCE(MAX(id),0)+1 n FROM groups").fetchone()
        return r['n']

    # ---------- 语义向量 ----------

    def put_embedding(self, file_id, vec_bytes, model):
        self.conn.execute(
            "INSERT INTO embeddings(file_id,vec,model,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(file_id) DO UPDATE SET vec=excluded.vec, "
            "model=excluded.model, created_at=excluded.created_at",
            (file_id, vec_bytes, model, int(time.time())))

    def embedded_ids(self, model):
        """已经有当前模型向量的文件 id。换模型后自动重算。"""
        return {r['file_id'] for r in self.conn.execute(
            "SELECT file_id FROM embeddings WHERE model=?", (model,))}

    def load_embeddings(self, model):
        """取全部向量，返回 (矩阵, id 列表)。没有数据时返回 (None, [])。"""
        import numpy as np
        rows = self.conn.execute(
            "SELECT file_id, vec FROM embeddings WHERE model=?", (model,)
        ).fetchall()
        if not rows:
            return None, []
        ids = [r['file_id'] for r in rows]
        mat = np.vstack([np.frombuffer(r['vec'], dtype=np.float32)
                         for r in rows]).astype(np.float32)
        return mat, ids

    def clear_embeddings(self, model=None):
        if model:
            self.conn.execute("DELETE FROM embeddings WHERE model=?", (model,))
        else:
            self.conn.execute("DELETE FROM embeddings")
        self.conn.commit()

    def count_embeddings(self, model=None):
        if model:
            return self.conn.execute(
                "SELECT COUNT(*) c FROM embeddings WHERE model=?",
                (model,)).fetchone()['c']
        return self.conn.execute(
            "SELECT COUNT(*) c FROM embeddings").fetchone()['c']

    # ---------- 人脸数量 ----------

    def put_faces(self, file_id, count, model):
        self.conn.execute(
            "INSERT INTO face_counts(file_id,faces,model,created_at) "
            "VALUES(?,?,?,?) ON CONFLICT(file_id) DO UPDATE SET "
            "faces=excluded.faces, model=excluded.model, "
            "created_at=excluded.created_at",
            (file_id, int(count), model, int(time.time())))

    def faced_ids(self, model):
        return {r['file_id'] for r in self.conn.execute(
            "SELECT file_id FROM face_counts WHERE model=?", (model,))}

    def count_faces(self, model=None):
        if model:
            return self.conn.execute(
                "SELECT COUNT(*) c FROM face_counts WHERE model=?",
                (model,)).fetchone()['c']
        return self.conn.execute(
            "SELECT COUNT(*) c FROM face_counts").fetchone()['c']

    def ids_with_faces(self, model, min_faces=1):
        """有人脸的 file_id 集合。"""
        return {r['file_id'] for r in self.conn.execute(
            "SELECT file_id FROM face_counts WHERE model=? AND faces>=?",
            (model, min_faces))}

    # ---------- 图里的文字（OCR） ----------

    def put_texts(self, file_id, items, model):
        """写入一个文件的全部文字段。先删后插，避免重复累积。

        items 为空列表时也要写一条哨兵记录（content=''），表示
        「已识别，确认这张图没有文字」。否则这种文件永远留在待办里，
        每次继续索引都重跑一遍。
        """
        self.conn.execute("DELETE FROM texts WHERE file_id=?", (file_id,))
        now = int(time.time())
        if not items:
            self.conn.execute(
                "INSERT INTO texts(file_id,content,score,box,model,created_at) "
                "VALUES(?,?,?,?,?,?)", (file_id, '', 0.0, '[]', model, now))
            return
        self.conn.executemany(
            "INSERT INTO texts(file_id,content,score,box,model,created_at) "
            "VALUES(?,?,?,?,?,?)",
            [(file_id, text, score, json.dumps(box), model, now)
             for text, score, box in items])

    def texted_ids(self, model):
        """已经有 OCR 结果的文件 id。"""
        return {r['file_id'] for r in self.conn.execute(
            "SELECT DISTINCT file_id FROM texts WHERE model=?", (model,))}

    def count_texts(self, model=None):
        if model:
            return self.conn.execute(
                "SELECT COUNT(DISTINCT file_id) c FROM texts WHERE model=?",
                (model,)).fetchone()['c']
        return self.conn.execute(
            "SELECT COUNT(DISTINCT file_id) c FROM texts").fetchone()['c']

    def search_texts(self, terms, limit=200):
        """按文字搜图。多个词是「与」关系 —— 搜「开业庆典 帆影」要两个都有。

        用 LIKE 而不是 FTS5：trigram 索引不支持两字查询（中文里
        「曼谷」「自费」全是两字），而且 40 万行下 LIKE 只要 44ms，
        没必要为这点速度引入一个会漏结果的索引。
        """
        terms = [t for t in (terms or []) if t]
        if not terms:
            return []
        # 空 content 是「确认没字」的哨兵，不能参与匹配
        where = ' AND '.join(['t.content LIKE ?'] * len(terms))
        params = [f'%{t}%' for t in terms] + [limit]
        rows = self.conn.execute(
            f"SELECT t.file_id AS fid, t.content AS content, t.score AS score, "
            f"t.box AS box, COUNT(*) OVER (PARTITION BY t.file_id) AS n_hit "
            f"FROM texts t WHERE t.content != '' AND {where} "
            f"ORDER BY n_hit DESC, t.score DESC LIMIT ?", params).fetchall()
        return rows
