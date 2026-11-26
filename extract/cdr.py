"""CDR 缩略图提取。

现代 CDR（X4 以后）本质是 zip 包，内含 metadata/thumbnails/*.bmp。
实测 27/27 全部成功，单文件 37ms。
"""
import zipfile
import io
from PIL import Image

CANDIDATE_SUFFIXES = ('thumbnail.bmp', 'thumbnail.png', 'page1.bmp', 'page1.png')


def extract(path):
    """返回 (PIL.Image, None) 或 (None, 错误标识)。"""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except (zipfile.BadZipFile, OSError) as e:
        # 老版本 CDR 不是 zip 结构
        return None, 'not_zip_' + type(e).__name__

    # 优先 thumbnail.*，退而求其次 page1.*
    chosen = None
    for suffix in CANDIDATE_SUFFIXES:
        for n in names:
            if n.lower().endswith(suffix):
                chosen = n
                break
        if chosen:
            break

    if not chosen:
        # 兜底：任何路径里带 thumbnail 的条目
        for n in names:
            if 'thumbnail' in n.lower():
                chosen = n
                break
    if not chosen:
        return None, 'no_entry'

    try:
        with zipfile.ZipFile(path) as z:
            data = z.read(chosen)
        im = Image.open(io.BytesIO(data))
        im.load()
        return im.convert('RGB'), None
    except Exception as e:
        return None, 'decode_' + type(e).__name__
