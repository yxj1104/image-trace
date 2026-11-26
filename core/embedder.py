"""语义向量索引作业。

跟 core.indexer.IndexJob 分开是因为两者关注点完全不同：

- IndexJob 关心「文件有什么属性」—— 尺寸、指纹、缩略图，一次全量扫描
  要遍历所有根目录
- EmbedJob 关心「画面长什么样」—— 只对已经有记录的文件重新编码，
  不扫描磁盘，按批提交，可随时中断再续

放一起会让本来就复杂的 IndexJob 更难维护，而且语义索引的进度、取消、
限速策略都不一样。
"""
import os
import sys
import threading
import time

from core import db as dbmod
from core import semantic
from core import faces as faces_mod

# 只给源文件做语义编码。理由：
#   1. 导出图几乎都是源文件的副本，画面内容重复，编码等于浪费
#   2. 源文件是 6119 个，导出图有 1.8 万，全做要多花三倍时间
#   3. 用户搜「沙滩」是想找能改的 PSD，不是想找那张导出的 JPG
SOURCE_EXTS = ('psd', 'ai', 'cdr', 'eps', 'pdf', 'docx', 'doc',
               'ppt', 'pptx', 'svg', 'tif', 'tiff')

BATCH = 8              # 一批编码多少张：8 张一批比单张快 2.4 倍（实测）
COMMIT_EVERY = 40      # 每多少条提交一次事务


class EmbedJob:
    """语义向量索引作业。状态机跟 IndexJob 一致，便于前端复用轮询逻辑。"""

    def __init__(self, db_path, exts=None, threads=None, delay=0.0):
        self.db_path = db_path
        self.exts = tuple(exts or SOURCE_EXTS)
        # 线程数即「同时编码几张」。默认 4：实测 8 线程单张 83ms、
        # 4 线程 128ms，但 8 线程会吃满 8 个核，用户用 PS 时明显卡。
        # 4 线程约占用一半 CPU，配合 delay 可以更温和。
        self.threads = threads or 4
        self.delay = delay          # 每批之间歇多久（秒），0 表示不停

        self.state = 'idle'
        self.message = ''
        self.done = 0
        self.total = 0
        self.failed = 0
        self.started_at = None
        self.finished_at = None
        self.error = None
        self.eta = None

        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    # ---------- 对外 ----------

    def snapshot(self):
        with self._lock:
            d = {
                'state': self.state,
                'message': self.message,
                'done': self.done,
                'total': self.total,
                'failed': self.failed,
                'started_at': self.started_at,
                'finished_at': self.finished_at,
                'error': self.error,
                'eta': self.eta,
                'running': self.state in ('scanning', 'encoding'),
            }
        return d

    def start(self):
        if self._thread and self._thread.is_alive():
            return False
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def cancel(self):
        self._cancel.set()

    def wait(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    # ---------- 内部 ----------

    def _set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _run(self):
        try:
            self._do()
        except Exception as e:
            self._set(state='error', error=f'{type(e).__name__}: {e}',
                      message='语义索引失败', finished_at=time.time())

    def _do(self):
        if not semantic.available():
            self._set(state='error', error='模型文件缺失，无法做画面搜索',
                      message='缺少 models/cn_clip.onnx')
            return

        model = semantic.MODEL_NAME
        db = dbmod.DB(self.db_path)
        faces_ok = faces_mod.available()

        self._set(state='scanning', message='统计待编码文件…',
                  started_at=time.time())

        qm = ','.join('?' * len(self.exts))
        rows = db.conn.execute(
            f"SELECT id, path, ext FROM files WHERE ext IN ({qm})",
            self.exts).fetchall()
        have = db.embedded_ids(model)

        # 跳过 macOS 压缩包残留。._xxx.ai 是 AppleDouble 资源分支（几百字节），
        # __MACOSX 目录同理 —— 它们既渲染不出图，也没有内容可编码。
        # 不跳过的话它们会一直留在 todo 里，每次跑都记一次失败，
        # 用户看到「失败 4 个」会以为是真问题。
        def junk(p):
            b = os.path.basename(p).lower()
            return b.startswith('._') or '__macosx' in p.lower()

        # 人脸数据是后来加的，已编码的文件还没有它。
        # 所以 todo 要包含「缺向量」和「缺人脸」两种，否则老数据永远补不上。
        # 缺人脸但已有向量的会连带重算一次向量 —— 实测 436 个约 2.5 分钟，
        # 比拆成两条独立流程更好维护。
        faced = db.faced_ids(faces_mod.MODEL_NAME) if faces_ok else set()

        def need(r):
            if r['id'] not in have:
                return True
            return faces_ok and r['id'] not in faced

        todo = [(r['id'], r['path'], r['ext']) for r in rows
                if need(r) and os.path.isfile(r['path'])
                and not junk(r['path'])]

        # 上次跑失败的文件会一直留在 todo 里，每次都重试一遍、每次都失败。
        # 记下「已尝试过的失败」，下次不再重复 —— 否则用户每次点「继续」
        # 都要等几十秒重试一批注定失败的文件（EPS 缺 Ghostscript、
        # 旧版 AI 存成 PostScript 等）。
        tried = db.get_meta('embed_failed_ids') or ''
        tried_ids = {int(x) for x in tried.split(',') if x.strip().isdigit()}
        if tried_ids:
            before = len(todo)
            todo = [t for t in todo if t[0] not in tried_ids]
            skipped = before - len(todo)
            if skipped:
                print(f'跳过 {skipped} 个上次失败的文件')
        total = len(todo)
        self._set(total=total, message=f'待编码 {total} 个源文件')

        if not total:
            db.close()
            self._set(state='done', message='全部已编码，无需处理',
                      finished_at=time.time())
            return

        # 预热：第一次推理要初始化内存池，单独一次不算进进度。
        # 传 threads 进去 —— ORT 的 intra_op 线程数只在建会话时生效，
        # 之后再设置无效，所以第一次加载就必须给对。
        self._set(state='encoding', message='加载模型…')
        semantic._lazy_load(self.threads)

        done = 0
        failed = 0
        t0 = time.time()
        pending = []
        face_rows = []
        failed_ids = []

        def flush():
            nonlocal pending, face_rows
            if pending:
                for fid, vec in pending:
                    if vec is not None:
                        db.put_embedding(fid, semantic.blob(vec), model)
            if face_rows:
                for fid, n in face_rows:
                    db.put_faces(fid, n, faces_mod.MODEL_NAME)
            db.commit()
            pending = []
            face_rows = []

        for i in range(0, total, BATCH):
            if self._cancel.is_set():
                flush()
                db.close()
                self._set(state='cancelled', message=f'已停止，完成 {done} 个',
                          finished_at=time.time())
                return

            chunk = todo[i:i + BATCH]

            # 渲染时顺手做人脸检测。放在 encode_batch 的回调里是因为
            # 那里刚好有渲染好的图 —— 单独再渲一遍等于白扔 60% 的时间。
            local_faces = {}

            def collect(idx, im):
                if faces_ok:
                    local_faces[chunk[idx][0]] = faces_mod.detect(im)

            vecs = semantic.encode_batch(
                [(p, e) for _fid, p, e in chunk], threads=self.threads,
                on_image=collect)

            for (fid, path, ext), vec in zip(chunk, vecs):
                pending.append((fid, vec))
                done += 1
                if vec is None:
                    failed += 1
                    failed_ids.append(fid)
                if faces_ok:
                    # 没检出也记 0 —— 「确认没人」和「还没检测」必须能区分，
                    # 否则按人脸过滤时无法判断某张图是没人还是没跑过
                    face_rows.append((fid, local_faces.get(fid, 0)))

            if len(pending) >= COMMIT_EVERY:
                flush()

            # 进度与剩余时间
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed > 0 else 0
            remain = (total - done) / rate if rate > 0 else 0
            self._set(done=done, failed=failed, eta=remain,
                      message=f'已编码 {done}/{total}')

            if self.delay:
                # 分批之间歇一下，把 CPU 让给用户正在用的程序。
                # 用 sleep 而不是降优先级：Windows 的优先级调整对
                # 线程池里的工作线程不一定生效，而且容易被系统重置。
                time.sleep(self.delay)

        flush()

        # 记住这次失败的文件，下次不再重试。
        # 失败原因都是结构性的（EPS 缺 Ghostscript / 旧 AI 存为 PostScript），
        # 重试一百次也一样失败。
        if failed_ids:
            all_tried = sorted(tried_ids | set(failed_ids))
            db.set_meta('embed_failed_ids', ','.join(str(x) for x in all_tried))

        db.close()
        self._set(state='done', done=done, failed=failed,
                  message=f'完成，共编码 {done - failed} 个'
                          + (f'，{failed} 个无法编码' if failed else ''),
                  finished_at=time.time())
