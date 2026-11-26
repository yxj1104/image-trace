"""图里的文字（OCR）。

跟画面语义搜索是两条独立的通道：
- 语义搜索回答「画面看起来像什么」，OCR 回答「图上写了什么」
- 语义搜索靠 CLIP 向量，OCR 靠文字匹配

用户搜「开业庆典」时，真正想要的是「哪些海报上印了开业庆典这三个字」，
这是 OCR 的活，CLIP 做不了 —— 它读不出字。

模型是 RapidOCR 的 PP-OCRv3（13MB），中文识别效果好，纯 ONNX 不需要
额外装 paddle。单张 1200px 的图约 1.5s。
"""
import os
import re
import threading

_lock = threading.Lock()
_ocr = None
_failed = None

MODEL_NAME = 'rapidocr-ppocrv3'

# 渲染尺寸。实测 900px 和 1800px 识别出的文字段数完全一样（53 段），
# 但耗时从 1.43s 涨到 1.84s。选 1200 是因为它是「小字也能认出」的
# 安全边界 —— 900px 时有个别促销小字认错（「0自费」认成「0白费」）。
OCR_SIDE = 1200

# 低于这个置信度的丢弃。实测 0.5 以下基本是画面纹理被误认成字，
# 留在库里只会污染搜索结果。
MIN_SCORE = 0.5

# 纯符号/单字符的「文字」没有检索价值，丢掉
_NOISE = re.compile(r'^[\W_]+$')


def available():
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def load_error():
    return _failed


def _lazy_load(threads=4):
    global _ocr, _failed
    with _lock:
        if _ocr is not None:
            return True
        if _failed:
            return False
        try:
            from rapidocr_onnxruntime import RapidOCR
            # 限制线程数：默认会用满所有核，索引时会把用户正在用的
            # 软件卡住。4 线程实测单张 1.5s，8 线程 1.2s，代价不值。
            _ocr = RapidOCR(intra_op_num_threads=max(1, int(threads or 4)),
                            inter_op_num_threads=1)
            return True
        except Exception as e:
            _failed = f'{type(e).__name__}: {e}'
            return False


def recognize(im):
    """识别图里的文字。返回 [(文字, 置信度, 包围盒), ...]。

    包围盒归一化成 0~1 的相对坐标 [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]，
    **不是像素**。原因：OCR 跑在 1200px 的渲染图上，但前端预览图
    尺寸是任意的（缩略图 320px、详情图 760px、原图可能 4000px）。
    存相对坐标才能在任意尺寸下正确叠框，不用反过来推算缩放比。
    """
    if im is None or not _lazy_load():
        return []
    try:
        import numpy as np

        im = im.convert('RGB')
        W, H = im.size
        if not W or not H:
            return []

        res, _elapse = _ocr(np.array(im))
        if not res:
            return []
        out = []
        for item in res:
            box, text, score = item[0], item[1], float(item[2])
            text = (text or '').strip()
            if not text or score < MIN_SCORE:
                continue
            # 单字符和纯标点不要：「。」「1」「-」这类进了索引也搜不出东西
            if len(text) < 2 and not text.isascii():
                continue
            if _NOISE.match(text):
                continue
            nb = [[round(float(px) / W, 4), round(float(py) / H, 4)]
                  for px, py in box]
            out.append((text, round(score, 3), nb))
        return out
    except Exception:
        return []


def recognize_path(path, ext, side=None):
    """渲染 + 识别。渲染失败时退回索引缩略图。"""
    try:
        from server import _render_image
        e = ext if ext.startswith('.') else '.' + ext
        im = _render_image(path, e, side or OCR_SIDE)
    except Exception:
        im = None

    if im is None:
        # 跟语义编码一样：渲染不出来就用当初索引时生成的缩略图。
        # 缩略图分辨率低，小字会丢，但总比整张图没有文字好。
        try:
            import sqlite3

            from core import paths as pmod
            db_path = pmod.db_path()
            conn = sqlite3.connect(db_path, timeout=10)
            r = conn.execute("SELECT thumb_path FROM files WHERE path=?",
                             (path,)).fetchone()
            conn.close()
            if r and r[0]:
                from PIL import Image
                tp = os.path.join(os.path.dirname(os.path.abspath(db_path)),
                                  'thumbs', r[0])
                if os.path.isfile(tp):
                    im = Image.open(tp)
                    im.load()
        except Exception:
            im = None

    return recognize(im)
