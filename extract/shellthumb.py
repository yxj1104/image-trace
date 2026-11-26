"""用 Windows Shell 取文件缩略图。

这是资源管理器里显示的那个缩略图 —— 系统已经为所有已注册的文件类型
准备了提取器（doc / xls / ppt / pdf / psd / ai ...），直接借来用。

优点：
  - 一个调用覆盖所有格式，不用为每种格式写解析
  - 结果和用户在资源管理器里看到的一致
  - 不依赖 Office / Ghostscript / LibreOffice

缺点：
  - 只能在 Windows 上用
  - 依赖系统的缩略图处理器（没装 Office 时 .doc 可能没缩略图）
  - 每个调用要过一遍 COM，比直接读文件头慢

调用链：
  SHCreateItemFromParsingName → IShellItemImageFactory.GetImage → HBITMAP → PIL
"""
import os

try:
    import ctypes
    from ctypes import wintypes
    _HAS = True
except ImportError:
    _HAS = False

from PIL import Image

# SIIGBF 标志
SIIGBF_RESIZETOFIT = 0x00
SIIGBF_BIGGERSIZEOK = 0x01
SIIGBF_ICONONLY = 0x04
SIIGBF_THUMBNAILONLY = 0x08
SIIGBF_INCACHEONLY = 0x10

IID_IShellItem = '{43826D1E-E718-42EE-BC55-A1E261C37BFE}'
IID_IShellItemImageFactory = '{BCC18B79-BA16-442F-80C4-8A59C30C463B}'

# IShellItemImageFactory 的 GetImage 在虚表里的位置：
# IUnknown 占 0/1/2（QueryInterface/AddRef/Release），GetImage 是 3
GETIMAGE_INDEX = 3


if _HAS:
    class GUID(ctypes.Structure):
        _fields_ = [
            ('Data1', ctypes.c_ulong),
            ('Data2', ctypes.c_ushort),
            ('Data3', ctypes.c_ushort),
            ('Data4', ctypes.c_ubyte * 8),
        ]
else:
    GUID = None


def _guid(s):
    s = s.strip('{}')
    p = s.split('-')
    g = GUID()
    g.Data1 = int(p[0], 16)
    g.Data2 = int(p[1], 16)
    g.Data3 = int(p[2], 16)
    rest = bytes.fromhex(p[3] + p[4])
    for i in range(8):
        g.Data4[i] = rest[i]
    return g


def _vfn(ptr, index):
    """取 COM 接口虚表里第 index 个函数地址。"""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0]
    return ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))[index]


def _release(ptr):
    try:
        fn = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
            _vfn(ptr, 2))
        fn(ptr)
    except Exception:
        pass


def _hbitmap_to_pil(hbmp):
    """HBITMAP → PIL Image。手写 GetDIBits，避免引入 pywin32。"""
    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32

    class BITMAP(ctypes.Structure):
        _fields_ = [
            ('bmType', wintypes.LONG),
            ('bmWidth', wintypes.LONG),
            ('bmHeight', wintypes.LONG),
            ('bmWidthBytes', wintypes.LONG),
            ('bmPlanes', wintypes.WORD),
            ('bmBitsPixel', wintypes.WORD),
            ('bmBits', ctypes.c_void_p),
        ]

    class BIH(ctypes.Structure):
        _fields_ = [
            ('biSize', wintypes.DWORD),
            ('biWidth', wintypes.LONG),
            ('biHeight', wintypes.LONG),
            ('biPlanes', wintypes.WORD),
            ('biBitCount', wintypes.WORD),
            ('biCompression', wintypes.DWORD),
            ('biSizeImage', wintypes.DWORD),
            ('biXPelsPerMeter', wintypes.LONG),
            ('biYPelsPerMeter', wintypes.LONG),
            ('biClrUsed', wintypes.DWORD),
            ('biClrImportant', wintypes.DWORD),
        ]

    bm = BITMAP()
    if not gdi32.GetObjectW(hbmp, ctypes.sizeof(bm), ctypes.byref(bm)):
        gdi32.DeleteObject(hbmp)
        return None
    w, h = bm.bmWidth, bm.bmHeight
    if w <= 0 or h <= 0:
        gdi32.DeleteObject(hbmp)
        return None

    bi = BIH()
    bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth = w
    bi.biHeight = -h          # 负值 = 自上而下，省一次翻转
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0

    buf = ctypes.create_string_buffer(w * h * 4)
    hdc = user32.GetDC(None)
    try:
        n = gdi32.GetDIBits(hdc, hbmp, 0, h, buf, ctypes.byref(bi), 0)
        if not n:
            return None
    finally:
        user32.ReleaseDC(None, hdc)
        gdi32.DeleteObject(hbmp)

    im = Image.frombuffer('RGBA', (w, h), buf.raw, 'raw', 'BGRA', 0, 1)
    bg = Image.new('RGB', im.size, (255, 255, 255))
    bg.paste(im, mask=im.split()[-1])
    return bg


def get_thumbnail(path, size=512, thumbnail_only=True):
    """取文件缩略图，返回 PIL.Image 或 None。

    thumbnail_only=True 时只接受真正的缩略图，不接受文件类型图标 ——
    对以图搜图来说图标毫无意义，会把所有 .doc 都判成同一张图。
    """
    if not _HAS or os.name != 'nt':
        return None
    if not os.path.isfile(path):
        return None

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32

    hr = ole32.CoInitializeEx(None, 2)
    if hr not in (0, 1, -2147417850):
        return None

    shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
    item = ctypes.c_void_p()
    iid = _guid(IID_IShellItem)
    hr = shell32.SHCreateItemFromParsingName(
        ctypes.c_wchar_p(os.path.abspath(path)), None,
        ctypes.byref(iid), ctypes.byref(item))
    if hr != 0 or not item:
        return None

    factory = ctypes.c_void_p()
    try:
        iid2 = _guid(IID_IShellItemImageFactory)
        query = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p,
            ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
            _vfn(item, 0))
        hr = query(item, ctypes.byref(iid2), ctypes.byref(factory))
        if hr != 0 or not factory:
            return None

        # SIZE 是两个 LONG 打包成 64 位按值传。
        # 拆成两个 c_int 参数会让后续参数错位，phbm 指针被写成 flags 值，
        # 直接 access violation。必须打包成 c_int64。
        packed = (size & 0xFFFFFFFF) | ((size & 0xFFFFFFFF) << 32)

        hbmp = ctypes.c_void_p()
        flags = SIIGBF_RESIZETOFIT | SIIGBF_BIGGERSIZEOK
        if thumbnail_only:
            flags |= SIIGBF_THUMBNAILONLY

        get_image = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_int64,
            ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
            _vfn(factory, GETIMAGE_INDEX))

        hr = get_image(factory, packed, flags, ctypes.byref(hbmp))
        if hr != 0 or not hbmp:
            return None
        return _hbitmap_to_pil(hbmp)
    except Exception:
        return None
    finally:
        _release(factory if factory else None)
        _release(item)


def has_thumbnail(path):
    return get_thumbnail(path, 96) is not None
