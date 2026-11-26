"""指纹计算：pHash（一级召回）+ ORB（二级重排）+ 宽高比 + 主色调。"""
import io
import numpy as np
import imagehash
import cv2
from PIL import Image

ORB_FEATURES = 300
ORB_MAXDIM = 400  # 长边归一化到 400px

_orb = cv2.ORB_create(nfeatures=ORB_FEATURES)


def _norm_size(w, h, maxdim):
    """长边归一化。实测比按宽度归一化准：PSD 缩略图常是 74×160，
    按宽拉到 240 会变成 240×519（上采样 3 倍反而糊），长边归一化是 185×400。"""
    s = maxdim / max(w, h)
    nw, nh = int(round(w * s)), int(round(h * s))
    return max(16, nw), max(16, nh)


def phash_hex(im):
    """64bit 感知哈希，返回 16 位十六进制字符串。"""
    try:
        return str(imagehash.phash(im.convert('RGB')))
    except Exception:
        return None


def phash_bits(hex_str):
    """十六进制转整数，供快速暴力比对用。

    只取低 63 位：SQLite INTEGER 是有符号 64 位，超过会溢出。
    距离计算仍以十六进制字符串为准，这里丢一位不影响排序。
    """
    if not hex_str:
        return 0
    try:
        return int(hex_str, 16) & 0x7FFFFFFFFFFFFFFF
    except ValueError:
        return 0


def orb_descriptors(im):
    """返回 ORB 描述子 ndarray（uint8, N×32），失败返回 None。

    长边统一到 400px。实测 60 组真实配对（PSD 缩略图 vs 导出图）：
      长边 400：中位 0.449，58/60 达 0.10 阈值
      按宽 240：中位 0.330，56/60
    """
    try:
        g = im.convert('L')
        w, h = g.size
        if w < 8 or h < 8:
            return None
        nw, nh = _norm_size(w, h, ORB_MAXDIM)
        if (nw, nh) != (w, h):
            g = g.resize((nw, nh), Image.LANCZOS)
        _, desc = _orb.detectAndCompute(np.array(g), None)
        return desc
    except Exception:
        return None


def orb_to_blob(desc):
    """序列化为 bytes 存库。"""
    if desc is None or len(desc) == 0:
        return b''
    return desc.tobytes()


def blob_to_orb(blob):
    if not blob:
        return None
    try:
        arr = np.frombuffer(blob, dtype=np.uint8)
        if arr.size % 32 != 0:
            return None
        return arr.reshape(-1, 32)
    except Exception:
        return None


def aspect(im):
    w, h = im.size
    return round(w / h, 4) if h else None


def palette(im, n=3):
    """提取主导色，返回 [[r,g,b], ...]，按面积从大到小。

    取 3 个：够用于颜色筛选，同时控制索引体积。
    若要增加到 5 个，需要全量重建索引（存量数据只有 3 个色）。
    """
    try:
        small = im.convert('RGB')
        small.thumbnail((96, 96), Image.LANCZOS)
        q = small.quantize(colors=n, method=Image.MEDIANCUT)
        pal = q.getpalette()
        counts = sorted(q.getcolors() or [], reverse=True)
        out = []
        for cnt, idx in counts[:n]:
            i = idx * 3
            if i + 2 < len(pal):
                out.append([pal[i], pal[i + 1], pal[i + 2]])
        return out
    except Exception:
        return []


def thumb_jpeg(im, width=240, quality=75):
    """生成缓存缩略图字节。实测 19.3KB/张。"""
    try:
        t = im.convert('RGB')
        w, h = t.size
        if w > width:
            t = t.resize((width, max(1, int(width * h / w))), Image.LANCZOS)
        buf = io.BytesIO()
        t.save(buf, 'JPEG', quality=quality, optimize=True)
        return buf.getvalue()
    except Exception:
        return None
