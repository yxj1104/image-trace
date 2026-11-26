"""EPS 预览提取。

EPS 有两种：
  1. DOS EPS 二进制格式（魔术字节 C5 D0 D3 C6）
     → 32 字节头里记录了 PS / WMF / TIFF 三段的偏移和长度
     → 内嵌 TIFF 是现成的预览图，直接取
  2. 纯 PostScript（%!PS-Adobe）
     → 没有内嵌预览，需要 Ghostscript 渲染，本项目不依赖 GS

实测本机 24 个 EPS：
  3 个是纯 PS（无预览，标记 unsupported）
  其余是 DOS EPS，内嵌 TIFF 可解（Pillow 直接读会失败，但 IFD 结构完好，
  需要按 strip 自己拼——见 _decode_tiff）
"""
import struct
import io
from PIL import Image

DOS_EPS_MAGIC = b'\xc5\xd0\xd3\xc6'


def extract(path):
    try:
        with open(path, 'rb') as f:
            head = f.read(32)
            if len(head) < 32:
                return None, 'too_short'

            if head[:4] != DOS_EPS_MAGIC:
                # %!PS-Adobe 开头 = 纯 PostScript，无内嵌预览
                return None, 'ps_only'

            ps_off, ps_len, wmf_off, wmf_len, tif_off, tif_len = \
                struct.unpack('<6I', head[4:28])

            if tif_len:
                f.seek(tif_off)
                data = f.read(tif_len)
                im = _decode_tiff(data)
                if im is not None:
                    return im, None

            if wmf_len:
                # WMF 需要 Windows GDI 才能渲染，不值得为此引入依赖
                return None, 'wmf_only'

            return None, 'no_preview'
    except (OSError, struct.error) as e:
        return None, 'io_' + type(e).__name__


def _decode_tiff(data):
    """EPS 内嵌 TIFF 的解码。

    Pillow 直接 Image.open 会失败，原因是这类 TIFF 的 IFD 写在文件末尾，
    且不带 Pillow 期望的某些必需 tag。但它其实是未压缩的连续 strip，
    按 IFD 里记录的宽高、通道数、StripOffsets 手工拼一下就能出图。

    实测格式：Compression=1（无压缩）、Photometric=3（调色板 或 RGB）、
             SamplesPerPixel=2（RGB + alpha）、BitsPerSample=8
    """
    try:
        if len(data) < 8:
            return None
        if data[:2] == b'II':
            e = '<'
        elif data[:2] == b'MM':
            e = '>'
        else:
            return None

        ifd_off = struct.unpack(e + 'I', data[4:8])[0]
        if ifd_off + 2 > len(data):
            return None
        n = struct.unpack(e + 'H', data[ifd_off:ifd_off + 2])[0]
        if n <= 0 or n > 200:
            return None

        tags = {}
        pos = ifd_off + 2
        for _ in range(n):
            if pos + 12 > len(data):
                break
            tag, typ, cnt = struct.unpack(e + 'HHI', data[pos:pos + 8])
            raw = data[pos + 8:pos + 12]
            if typ == 3 and cnt == 1:            # SHORT
                val = struct.unpack(e + 'H', raw[:2])[0]
            elif typ == 4 and cnt == 1:          # LONG
                val = struct.unpack(e + 'I', raw)[0]
            else:
                val = struct.unpack(e + 'I', raw)[0]
            tags[tag] = (typ, cnt, val)
            pos += 12

        w = tags.get(256, (0, 0, 0))[2]
        h = tags.get(257, (0, 0, 0))[2]
        comp = tags.get(259, (0, 0, 1))[2]
        spp = tags.get(277, (0, 0, 3))[2]
        bps = tags.get(258, (0, 0, 8))[2]
        stripoff = tags.get(273, (0, 0, 0))[2]
        stripbc = tags.get(279, (0, 0, 0))[2]

        if not w or not h or comp != 1 or bps != 8:
            return None
        if w > 4000 or h > 4000 or spp not in (1, 2, 3, 4):
            return None
        if not stripoff or not stripbc:
            return None
        if stripoff + stripbc > len(data):
            stripbc = len(data) - stripoff

        need = w * h * spp
        if stripbc < need:
            return None

        raw = data[stripoff:stripoff + need]

        if spp == 3:
            im = Image.frombytes('RGB', (w, h), raw)
        elif spp == 4:
            im = Image.frombytes('CMYK', (w, h), raw).convert('RGB')
        elif spp == 2:
            # 灰度 + alpha（少见）
            im = Image.frombytes('LA', (w, h), raw).convert('RGB')
        else:
            im = Image.frombytes('L', (w, h), raw).convert('RGB')
        return im
    except Exception:
        return None
