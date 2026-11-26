"""SVG 渲染。

用 resvg（Rust 实现，自带渲染引擎，不需要系统装 cairo）。
实测：单文件 9–52 ms，产出 RGBA PNG 可直接给 PIL 读。

备选方案都验证过但不可用：
  cairosvg      → 依赖系统 libcairo，Windows 上默认没有
  svglib+reportlab → 需要 rlPyCairo 后端，同样依赖 cairo
"""
import io
import resvg_py
from PIL import Image

MAX_DIM = 512


def extract(path):
    """返回 (PIL.Image, None) 或 (None, 错误标识)。"""
    try:
        raw = resvg_py.svg_to_bytes(svg_path=path, width=MAX_DIM)
        data = bytes(raw)
    except Exception as e:
        return None, 'resvg_' + type(e).__name__

    if not data:
        return None, 'resvg_empty'

    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:
        return None, 'svg_decode_' + type(e).__name__

    # SVG 常是透明底，转白底便于指纹计算，否则大面积 alpha=0 会干扰 pHash
    if im.mode in ('RGBA', 'LA', 'P'):
        bg = Image.new('RGB', im.size, (255, 255, 255))
        rgba = im.convert('RGBA')
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg, None
    return im.convert('RGB'), None
