"""图里的文字索引作业（OCR）。

跟画面语义索引（core/embedder.py）是并列的两条流程，独立进度、独立开关。

**为什么不合并成一个作业**：OCR 的成本是语义编码的 5 倍（1.5s vs 0.2s/张），
两个一起跑会让用户等很久才看到第一个功能可用。分开之后可以先建语义索引
（20 分钟），立刻能用画面搜索；OCR 慢慢在后台补。

**按范围分片**：全库 18698 张 OCR 要 8 小时。用户明确要求「不要全局索引，
按类型、按磁盘来」—— 所以支持只处理某个盘、某种格式。
"""
import os
import sys
import threading
import time

from core import db as dbmod
from core import ocr as ocr_mod

# OCR 的渲染成本比语义编码高得多（要认小字，图不能太小），
# 并且实测 2/4/8 线程差别很小（1379/1357/1485ms），说明是单线程
# 计算密集的。所以 jobs 恒为 1，靠限速而不是并发来降低打扰。
OCR_EXTS = ('psd', 'jpg', 'jpeg', 'png', 'webp', 'pdf', 'docx', 'doc',
            'ai', 'cdr', 'eps', 'svg', 'tif', 'tiff', 'pptx', 'ppt')

# 各格式单张耗时（毫秒），实测值。用于估算剩余时间。
COST_MS = {
    'psd': 1822, 'jpg': 1626, 'jpeg': 1626, 'png': 796, 'webp': 524,
    'pdf': 1736, 'docx': 6352, 'doc': 5263, 'ai': 1998, 'cdr': 1772,
    'eps': 1117, 'svg': 907, 'tif': 883, 'tiff': 883, 'pptx': 1224,
    'ppt': 1224,
}

# 路径前缀 → 中文名，给界面上的「按磁盘」选项用
DRIVE_LABEL = {'E:': 'E 盘', 'F:': 'F 盘', 'G': 'G 盘'}


class OcrJob:
    """一次 OCR 索引作业。状态机跟 EmbedJob 一致，前端复用轮询逻辑。"""

    def __init__(self, db_path, drives=None, exts=None, delay=0.0):
        self.db_path = db_path
        self.drives = tuple(d for d in (drives or []) if d)
        self.exts = tuple(exts or OCR_EXTS)
        self.delay = delay

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

    def snapshot(self):
        with self._lock:
            return {
                'state': self.state,
                'message': self.message,
                'done': self.done,
                'total': self.total,
                'failed': self.failed,
                'started_at': self.started_at,
                'finished_at': self.finished_at,
                'error': self.error,
                'eta': self.eta,
                'running': self.state in ('scanning', 'recognizing'),
                'drives': list(self.drives),
                'exts': list(self.exts),
            }

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
                      message='文字索引失败', finished_at=time.time())

    def _do(self):
        if not ocr_mod.available():
            self._set(state='error', error='缺少 OCR 组件 rapidocr_onnxruntime',
                      message='OCR 组件未安装')
            return

        model = ocr_mod.MODEL_NAME
        db = dbmod.DB(self.db_path)
        self._set(state='scanning', message='统计待识别文件…',
                  started_at=time.time())

        qm = ','.join('?' * len(self.exts))
        sql = f"SELECT id, path, ext FROM files WHERE ext IN ({qm})"
        params = list(self.exts)

        if self.drives:
            # 按磁盘过滤。路径都是绝对路径（E:\...），用前缀匹配。
            like = ' OR '.join(['path LIKE ?'] * len(self.drives))
            sql += f" AND ({like})"
            params += [d + '%' for d in self.drives]

        rows = db.conn.execute(sql, params).fetchall()
        have = db.texted_ids(model)

        def junk(p):
            b = os.path.basename(p).lower()
            return b.startswith('._') or '__macosx' in p.lower()

        todo = [(r['id'], r['path'], r['ext']) for r in rows
                if r['id'] not in have and os.path.isfile(r['path'])
                and not junk(r['path'])]

        # 上次失败的记下来，不重复重试 —— 结构性失败（EPS 缺 Ghostscript）
        # 重试一百次也一样。
        tried = db.get_meta('ocr_failed_ids') or ''
        tried_ids = {int(x) for x in tried.split(',') if x.strip().isdigit()}
        if tried_ids:
            todo = [t for t in todo if t[0] not in tried_ids]

        total = len(todo)
        cost = sum(COST_MS.get(e, 1500) for _f, _p, e in todo)
        self._set(total=total, message=f'待识别 {total} 个文件')
        if not total:
            db.close()
            self._set(state='done', message='全部已识别，无需处理',
                      finished_at=time.time())
            return

        self._set(state='recognizing', message='加载 OCR 模型…')
        ocr_mod._lazy_load(4)

        done = 0
        failed = 0
        failed_ids = []
        t0 = time.time()
        batch = []

        def flush():
            nonlocal batch
            if not batch:
                return
            for fid, items in batch:
                db.put_texts(fid, items, model)
            db.commit()
            batch = []

        for fid, path, ext in todo:
            if self._cancel.is_set():
                flush()
                db.close()
                self._set(state='cancelled', message=f'已停止，完成 {done} 个',
                          finished_at=time.time())
                return

            items = ocr_mod.recognize_path(path, ext)
            # 识别出 0 段也写一条空记录：否则这个文件永远留在 todo 里，
            # 每次点「继续」都重跑一遍纯色背景图/图标（1.5s 一个）。
            # 用 content='' 表示「已识别，确认没字」，和「还没识别」区分开。
            if items:
                batch.append((fid, items))
            else:
                batch.append((fid, []))

            done += 1
            if len(batch) >= 20:
                flush()

            elapsed = time.time() - t0
            rate = done / elapsed if elapsed > 0 else 0
            remain = (total - done) / rate if rate > 0 else 0
            self._set(done=done, failed=failed, eta=remain,
                      message=f'已识别 {done}/{total}')

            if self.delay:
                time.sleep(self.delay)

        flush()
        if failed_ids:
            all_tried = sorted(tried_ids | set(failed_ids))
            db.set_meta('ocr_failed_ids', ','.join(str(x) for x in all_tried))
        db.close()
        self._set(state='done', done=done, failed=failed,
                  message=f'完成，{done - failed} 个文件识别到文字',
                  finished_at=time.time())
