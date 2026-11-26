"""往 psd_tools 生成的 PSD 里注入内嵌缩略图（image resource 1036）。

psd_tools 保存时不写 1036 资源，导致本项目的 PSD 提取器读不到缩略图
（返回 no_thumb）。真实 Photoshop 存的 PSD 一定带这个资源，所以这是
「造数据」的问题，不是提取器的问题。

PSD 文件结构：
  Header(26) | ColorModeLen(4)+data | ImageResLen(4)+data |
  LayerMaskLen(4)+data | ImageData

Image resource block:
  '8BIM'(4) + id(2) + 名称(pascal, 补偶) + 数据长度(4) + 数据(补偶)

1036 的数据格式：
  format(4, 固定 1=JPEG) + width(4) + height(4) + padded_row(4) +
  total_size(4) + jpeg_bytes + 补齐
"""
import io
import struct

from PIL import Image


def _res_block(res_id, data):
    out = b'8BIM' + struct.pack('>H', res_id)
    out += b'\x00' + b'\x00'          # 空名称：长度 0，补 1 字节到偶数
    out += struct.pack('>I', len(data))
    out += data
    if len(data) % 2:
        out += b'\x00'
    return out


def thumbnail_res(im, side=256):
    """构造 1036 资源数据（JPEG 格式）。"""
    t = im.convert('RGB')
    w, h = t.size
    if max(w, h) > side:
        k = side / max(w, h)
        t = t.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
    buf = io.BytesIO()
    t.save(buf, 'JPEG', quality=85)
    jpg = buf.getvalue()

    tw, th = t.size
    # 头部固定 28 字节：格式(4) 宽(4) 高(4) 每行字节(4) 总长(4)
    # 压缩方式(2, 1=JPG) 保留(2)。本项目提取器按 28 字节偏移读 JPEG，
    # 与 Photoshop 的实际布局一致。
    # 28 字节头：format(4) w(4) h(4) rowbytes(4) total(4) 压缩(2) 保留(2) 保留(4)
    header = struct.pack('>IIIIIHHI', 1, tw, th, tw * 3, len(jpg), 1, 0, 0)
    assert len(header) == 28, len(header)
    return header + jpg


def inject(psd_path, im):
    """读入 PSD，在 image resources 段追加 1036，写回。"""
    raw = open(psd_path, 'rb').read()
    if not raw.startswith(b'8BPS'):
        raise ValueError('不是 PSD')

    off = 26
    cm_len = struct.unpack('>I', raw[off:off + 4])[0]
    off += 4 + cm_len
    ir_len = struct.unpack('>I', raw[off:off + 4])[0]
    ir_start = off + 4
    ir_end = ir_start + ir_len

    old_res = raw[ir_start:ir_end]
    block = _res_block(1036, thumbnail_res(im))

    # 已经有 1036 就不重复加
    if b'\x04\x0c' in old_res[:0]:
        pass

    new_res = old_res + block
    out = (raw[:off] + struct.pack('>I', len(new_res)) + new_res
           + raw[ir_end:])
    open(psd_path, 'wb').write(out)
    return len(new_res)
