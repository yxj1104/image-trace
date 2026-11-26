"""索引作业：增量扫描、变更检测、进度上报。

被 CLI 和本地服务共用。服务在后台线程里跑，通过 progress 字典对外暴露状态。
"""
import os
import sys
import time
import threading
import traceback

from . import db as dbmod
from . import pairing
from . import scanner

from extract import OFFICE_EXTS


def _p(msg):
    """进度打印：必须 flush，否则重定向到日志时看不到实时进度。"""
    try:
        print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass


class IndexJob:
    """一次索引作业的状态机。

    状态：idle → scanning → indexing → pairing → done / error / cancelled
    """

    def __init__(self, db_path, roots, jobs=None, thumb_dir=None):
        self.db_path = db_path
        self.roots = [r for r in (roots or []) if r]
        # 打包后的窗口模式下，子进程无法复用主进程（PyInstaller 限制 +
        # 无控制台时 stdin/stdout 为 None），多进程会静默失败。
        # 增量更新通常只有几十个文件，单进程足够；全量重建慢一些但可靠。
        default_jobs = 1 if getattr(sys, 'frozen', False) \
            else min(8, os.cpu_count() or 4)
        self.jobs = jobs or default_jobs
        self.thumb_dir = thumb_dir or os.path.join(
            os.path.dirname(db_path), 'thumbs')

        self.state = 'idle'
        self.message = ''
        self.done = 0
        self.total = 0
        self.added = 0
        self.updated = 0
        self.removed = 0
        self.failed = 0
        self.started_at = None
        self.finished_at = None
        self.error = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    # ---------- 对外 ----------

    def snapshot(self):
        with self._lock:
            elapsed = 0
            if self.started_at:
                end = self.finished_at or time.time()
                elapsed = round(end - self.started_at, 1)
            pct = 0
            if self.total:
                pct = min(100, int(self.done * 100 / self.total))
            return {
                'state': self.state,
                'message': self.message,
                'done': self.done,
                'total': self.total,
                'percent': pct,
                'added': self.added,
                'updated': self.updated,
                'removed': self.removed,
                'failed': self.failed,
                'error': self.error,
                'elapsed': elapsed,
                'running': self.state not in ('idle', 'done', 'error', 'cancelled'),
            }

    def start(self):
        if self._thread and self._thread.is_alive():
            return False
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
        self._set(state='scanning', message='扫描文件…', started_at=time.time(),
                  error=None, done=0, total=0, added=0, updated=0,
                  removed=0, failed=0, finished_at=None)

        try:
            self._do()
        except Exception as e:
            self._set(state='error', error=f'{type(e).__name__}: {e}',
                      message='索引失败', finished_at=time.time())
            _p('索引异常:\n' + traceback.format_exc())

    def _do(self):
        t0 = time.time()
        files = list(scanner.walk_files(self.roots, verbose=False))
        if self._cancel.is_set():
            self._set(state='cancelled', finished_at=time.time())
            return

        self._set(message=f'扫描到 {len(files)} 个文件', total=len(files))

        db = dbmod.DB(self.db_path)
        try:
            # 现存记录：path -> (mtime, size)
            known = {}
            for r in db.conn.execute("SELECT path, mtime, size FROM files"):
                known[r['path']] = (r['mtime'], r['size'])

            todo = []
            for path, ext in files:
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                sig = (int(st.st_mtime), st.st_size)
                old = known.get(path)
                if old is None:
                    todo.append((path, ext, 'new'))
                elif old[0] != sig[0] or old[1] != sig[1]:
                    todo.append((path, ext, 'mod'))
                else:
                    self.done += 1

            # 已删除的文件
            #
            # 关键：只在「本次扫描的根目录范围内」判断删除。
            # 如果只更新 D:\某目录，不能因为 E 盘的文件没出现在这次扫描结果里，
            # 就把 E 盘的 1.8 万条记录全删掉。这是一个会毁掉索引的严重 bug，
            # 已实际踩到过一次。
            current = {p for p, _ in files}
            norm_roots = [scanner.norm_root(r) for r in self.roots]
            gone = []
            for p in known:
                if p in current:
                    continue
                if not _under_any(p, norm_roots):
                    continue        # 不在本次扫描范围内，保留
                gone.append(p)

            total_known = len(known)
            in_scope = sum(1 for p in known if _under_any(p, norm_roots))

            self._set(
                message=(f'{len(todo)} 个待处理（新增 '
                         f'{sum(1 for t in todo if t[2] == "new")}，'
                         f'变更 {sum(1 for t in todo if t[2] == "mod")}），'
                         f'{len(gone)} 个已删除；'
                         f'本次范围 {in_scope} 条 / 全库 {total_known} 条'))

            if not todo and not gone:
                self._set(state='pairing', message='没有变化，跳过配对')
                self._set(state='done', message='已是最新',
                          finished_at=time.time())
                db.set_meta('last_index', int(time.time()))
                return

            db.commit()

            # 处理待索引项
            self._set(state='indexing', total=len(todo) + len(gone),
                      done=0)

            if todo:
                self._index_items(db, todo)

            # 清理已删除
            if gone:
                self._set(message=f'清理 {len(gone)} 个已删除记录')
                db.conn.executemany(
                    "DELETE FROM files WHERE path=?", [(p,) for p in gone])
                db.commit()
                self._set(removed=len(gone))
                self.done += len(gone)

            # 重新配对
            self._set(state='pairing', message='重新计算文件配对…')
            self._repair(db)

            for r in self.roots:
                db.add_root(r)
            db.set_meta('last_index', int(time.time()))

            self._set(state='done',
                      message=(f'完成：新增 {self.added}，更新 {self.updated}，'
                               f'删除 {self.removed}'),
                      finished_at=time.time())
            _p(f'索引完成，用时 {time.time() - t0:.1f}s')
        finally:
            db.close()

    def _index_items(self, db, todo):
        n = len(todo)
        batch = []
        t1 = time.time()

        # Office 文档必须串行处理，理由：
        #   1. 它靠 Word/WPS 的 COM 导出，COM 对象不能跨进程共享
        #   2. 多个进程同时拉起 Word 会让彼此的 COM 调用 RPC 失败
        #      （实测 -2147023170「远程过程调用失败」）
        #   3. 单个 Word 实例复用也会在第二个文件上挂掉，只能一个文件
        #      一个实例 —— 1.7s/个，串行反而稳定
        office_todo = [t for t in todo if t[1] in OFFICE_EXTS]
        other_todo = [t for t in todo if t[1] not in OFFICE_EXTS]
        if office_todo:
            _p(f'{len(office_todo)} 个 Office 文档串行处理')
            self._index_items_single(db, office_todo)
        todo = other_todo
        if not todo:
            _p(f'索引 {n} 个文件，用时 {time.time() - t1:.1f}s')
            return

        def flush():
            nonlocal batch
            if not batch:
                return
            for rec, kind in batch:
                db.upsert(rec)
                if kind == 'new':
                    self.added += 1
                else:
                    self.updated += 1
                if rec.get('extract_error'):
                    self.failed += 1
            db.commit()
            batch = []

        if self.jobs > 1:
            from concurrent.futures import ProcessPoolExecutor, as_completed
            try:
                with ProcessPoolExecutor(self.jobs) as pool:
                    futs = {pool.submit(scanner.worker,
                                        (p, e, self.thumb_dir)): (p, kind)
                            for p, e, kind in todo}
                    for fut in as_completed(futs):
                        if self._cancel.is_set():
                            pool.shutdown(wait=False, cancel_futures=True)
                            self._set(state='cancelled',
                                      message='已取消',
                                      finished_at=time.time())
                            return
                        p, kind = futs[fut]
                        try:
                            rec = fut.result()
                        except Exception:
                            rec = None
                        if rec is not None:
                            batch.append((rec, kind))
                        self.done += 1
                        if len(batch) >= 60:
                            flush()
            except Exception as e:
                # 冻结环境或受限环境下多进程可能不可用，退回单进程
                _p(f'多进程失败（{type(e).__name__}），改单进程：{e}')
                self._set(message='多进程不可用，改用单进程…')
                self._index_items_single(db, todo)
                return
        else:
            self._index_items_single(db, todo)
            return

        flush()
        _p(f'索引 {n} 个文件，用时 {time.time() - t1:.1f}s')

    def _index_items_single(self, db, todo):
        batch = []
        for i, (p, e, kind) in enumerate(todo):
            if self._cancel.is_set():
                self._set(state='cancelled', message='已取消',
                          finished_at=time.time())
                break
            try:
                rec = scanner.worker((p, e, self.thumb_dir))
            except Exception:
                rec = None
            if rec is not None:
                batch.append((rec, kind))
            self.done += 1
            if len(batch) >= 40:
                for rec, k in batch:
                    db.upsert(rec)
                    if k == 'new':
                        self.added += 1
                    else:
                        self.updated += 1
                    if rec.get('extract_error'):
                        self.failed += 1
                batch = []
                db.commit()
        for rec, k in batch:
            db.upsert(rec)
            if k == 'new':
                self.added += 1
            else:
                self.updated += 1
            if rec.get('extract_error'):
                self.failed += 1
        db.commit()

    def _repair(self, db):
        """重算画面组。实测 1.8 万条约 1 秒。"""
        try:
            rows = db.all_files()
            db.clear_groups()
            by_name = pairing.pair_by_name(rows)
            by_hash = pairing.pair_by_hash(rows)

            parent = {}

            def find(x):
                parent.setdefault(x, x)
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a, b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

            reasons = {}
            for (a, b), rs in list(by_name.items()) + list(by_hash.items()):
                union(a, b)
                reasons.setdefault(find(a), []).extend(rs)

            clusters = {}
            for node in list(parent.keys()):
                clusters.setdefault(find(node), []).append(node)

            by_id = {r['id']: r for r in rows}
            gid = 1
            for _root, members in clusters.items():
                if len(members) < 2:
                    continue
                srcs = [m for m in members
                        if by_id[m]['kind'] == 'source'] or members
                exps = [m for m in members if by_id[m]['kind'] == 'export']
                rs = []
                for m in members:
                    for rr in reasons.get(find(m), []):
                        if rr not in rs:
                            rs.append(rr)
                conf = 0.95 if any('同目录同主名' in r for r in rs) else 0.7
                db.set_group(gid, exps[0] if exps else members[0], srcs[0],
                             conf, rs, members)
                gid += 1
            db.commit()
        except Exception as e:
            _p(f'配对失败：{e}')


def _under_any(path, roots):
    """判断路径是否落在任一给定根目录下（前缀比较，不区分大小写）。"""
    lp = os.path.normcase(os.path.normpath(path))
    for r in roots:
        rn = os.path.normcase(os.path.normpath(r))
        if not rn.endswith(os.sep):
            rn += os.sep
        if lp == rn or lp.startswith(rn):
            return True
    return False
