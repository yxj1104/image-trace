"""位图读取：PNG / JPG / WEBP / TIF / BMP / GIF。

实测 40-51ms/张（含 pHash 计算）。
"""
from PIL import Image

# 防止解压炸弹，同时够大图正常打开
Image.MAX_IMAGE_PIXELS = 300_000_000

MAX_DIM = 1024  # 超过此尺寸先缩，省内存也省时间


def extract(path):
    """返回 (PIL.Image, None) 或 (None, 错误标识)。"""
    try:
        im = Image.open(path)
        im.load()
    except Exception as e:
        return None, 'pil_' + type(e).__name__

    try:
        if im.mode not in ('RGB', 'L'):
            # 带透明通道的转白底，避免 alpha 变黑
            if im.mode in ('RGBA', 'LA', 'P'):
                bg = Image.new('RGB', im.size, (255, 255, 255))
                rgba = im.convert('RGBA')
                bg.paste(rgba, mask=rgba.split()[-1])
                im = bg
            else:
                im = im.convert('RGB')
        if max(im.size) > MAX_DIM:
            im.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)
        return im, None
    except Exception as e:
        return None, 'convert_' + type(e).__name__
