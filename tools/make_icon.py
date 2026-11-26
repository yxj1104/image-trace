"""从 SVG 生成 Windows .ico（多尺寸）。

Windows 需要在一个 .ico 里塞多个尺寸，资源管理器在不同视图下各取所需：
  16  任务栏 / 小图标视图
  32  中等图标
  48  桌面 / 大图标
  64  超大图标
  128 详情面板
  256 Windows 10/11 应用列表
"""
import io
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import resvg_py
from PIL import Image

SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(svg_path, size):
    raw = resvg_py.svg_to_bytes(svg_path=svg_path, width=size,
                                height=size)
    im = Image.open(io.BytesIO(bytes(raw)))
    im.load()
    return im.convert('RGBA')


def build_ico(svg_path, out_path, sizes=SIZES):
    """手工写 ICO 结构，保证每帧都是无损 PNG。

    Pillow 的 save(format='ICO', sizes=[...]) 也能做，但它会重采样，
    小尺寸容易糊。这里每帧单独用 resvg 渲染，边缘最锐。
    """
    frames = []
    for s in sizes:
        im = render(svg_path, s)
        buf = io.BytesIO()
        im.save(buf, 'PNG', optimize=True)
        frames.append((s, buf.getvalue()))

    n = len(frames)
    header = struct.pack('<HHH', 0, 1, n)          # reserved, type=icon, count
    offset = 6 + 16 * n

    entries = b''
    data = b''
    for size, png in frames:
        # 目录项：宽高用 0 表示 256
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        entries += struct.pack('<BBBBHHII',
                               w, h, 0, 0,        # w,h,colors,reserved
                               1, 32,             # planes, bpp
                               len(png), offset)
        data += png
        offset += len(png)

    with open(out_path, 'wb') as f:
        f.write(header + entries + data)
    return out_path


def build_png(svg_path, out_path, size=512):
    render(svg_path, size).save(out_path, 'PNG', optimize=True)
    return out_path


if __name__ == '__main__':
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    svg = os.path.join(root, 'assets', 'icon.svg')
    ico = os.path.join(root, 'assets', 'icon.ico')
    png = os.path.join(root, 'assets', 'icon.png')
    build_ico(svg, ico)
    build_png(svg, png, 512)
    print('生成:', ico, f'{os.path.getsize(ico)//1024} KB')
    print('生成:', png, f'{os.path.getsize(png)//1024} KB')

    # 校验：读回来确认帧数和尺寸都对
    im = Image.open(ico)
    print('ICO 帧数:', len(getattr(im, 'ico', None).sizes()) if hasattr(im, 'ico')
          else 'n/a')
    print('各帧尺寸:', sorted(getattr(im, 'ico', None).sizes())
          if hasattr(im, 'ico') else 'n/a')
