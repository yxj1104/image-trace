"""Office 文档缩略图：三条路依次尝试。

1. Windows Shell 缩略图处理器（最快，但 .doc/.docx 常没有注册处理器）
2. OOXML 包内的 docProps/thumbnail.jpeg（Word 保存时会生成，不一定有）
3. Word/WPS COM 导出首页为图片（慢，但可靠；不依赖 Shell 处理器）

.doc（OLE 格式）额外尝试 \x05SummaryInformation 里的缩略图流。
"""
import io
import os
import zipfile
import tempfile


def _from_ooxml_thumbnail(path):
    """docx/pptx/xlsx 是 zip 包，可能带 docProps/thumbnail.*"""
    try:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.startswith('docProps/thumbnail'):
                    data = z.read(n)
                    from PIL import Image
                    im = Image.open(io.BytesIO(data))
                    im.load()
                    return im.convert('RGB')
    except Exception:
        pass
    return None


def _from_doc_summary(path):
    """.doc 的 OLE 容器里，SummaryInformation 可能带缩略图。"""
    try:
        import olefile
        with olefile.OleFileIO(path) as o:
            for stream in ('\x05SummaryInformation',
                           '\x05DocumentSummaryInformation'):
                if not o.exists(stream):
                    continue
                data = o.openstream(stream).read()
                im = _parse_summary_thumbnail(data)
                if im is not None:
                    return im
    except Exception:
        pass
    return None


def _parse_summary_thumbnail(data):
    """从 SummaryInformation 属性集里取 PIDSI_THUMBNAIL(0x11)。

    格式是 [MS-OLEPS] 的 property set：
      header(28) + section list + section{ FMTID, offset, ... }
      再进到 section 里的 property 数组
    """
    try:
        import struct
        from PIL import Image

        if len(data) < 48:
            return None
        # 头 28 字节，紧接 1 个 section descriptor
        n_sections = struct.unpack('<I', data[24:28])[0]
        if n_sections < 1:
            return None
        sec_off = struct.unpack('<I', data[44:48])[0]
        if sec_off + 8 > len(data):
            return None

        size = struct.unpack('<I', data[sec_off:sec_off + 4])[0]
        count = struct.unpack('<I', data[sec_off + 4:sec_off + 8])[0]
        if count <= 0 or count > 200:
            return None

        base = sec_off
        for i in range(count):
            p = base + 8 + i * 8
            if p + 8 > len(data):
                break
            pid, poff = struct.unpack('<II', data[p:p + 8])
            if pid != 0x11:      # PIDSI_THUMBNAIL
                continue
            q = base + poff
            if q + 8 > len(data):
                return None
            vt = struct.unpack('<I', data[q:q + 4])[0]
            if vt != 0x0046:     # VT_CF（剪贴板格式）
                return None
            cb = struct.unpack('<I', data[q + 4:q + 8])[0]
            clip = data[q + 8:q + 8 + cb]
            if len(clip) < 8:
                return None
            # 剪贴板格式头：cbSize(4) + clipboardFormat(4)
            cf = struct.unpack('<I', clip[4:8])[0]
            payload = clip[8:]
            if cf in (2, 3):     # CF_BITMAP / CF_METAFILEPICT，一般后面跟 DIB
                # 跳过 BITMAPFILEHEADER 或 BITMAPINFOHEADER 里的偏移
                im = _dib_to_image(payload)
                if im is not None:
                    return im
            elif cf in (13, 14) or payload[:4] in (b'\x89PNG', b'\xff\xd8\xff\xe0'):
                im = Image.open(io.BytesIO(payload))
                im.load()
                return im.convert('RGB')
    except Exception:
        pass
    return None


def _dib_to_image(payload):
    """裸 DIB（无 BMP 文件头）→ PIL。补一个 14 字节头即可给 PIL 读。"""
    try:
        import struct
        from PIL import Image
        if len(payload) < 40:
            return None
        bi_size = struct.unpack('<I', payload[:4])[0]
        if bi_size < 40 or bi_size > 124:
            return None
        # BITMAPFILEHEADER: 'BM' + filesize(4) + reserved(4) + dataoffset(4)
        header = b'BM' + struct.pack('<IHHI', 14 + len(payload), 0, 0,
                                     14 + bi_size)
        im = Image.open(io.BytesIO(header + payload))
        im.load()
        return im.convert('RGB')
    except Exception:
        return None


def _from_word_com(path, timeout=25):
    """用 Word 或 WPS 导出首页。

    只在 Windows 且有 Word/WPS 时可用。必须带超时和清理，
    否则一个卡住的文档会拖死整个索引流程。
    """
    if os.name != 'nt':
        return None
    try:
        import ctypes
        # 用 comtypes 不需要额外装 pywin32
        import comtypes.client
    except ImportError:
        return None

    out = None
    tmp = None
    app = None
    doc = None
    try:
        # 依次尝试 Word 和 WPS —— 用户可能只装了一个。
        # 注意 clsctx 会让 WPS 的 COM 服务器起不来（它只注册了 inproc
        # 和 local 的默认组合），所以不传 clsctx，让 comtypes 自己选。
        app = None
        last_err = None
        for progid in ('Word.Application', 'KWps.Application'):
            try:
                app = comtypes.client.CreateObject(progid, dynamic=True)
                break
            except Exception as e:
                last_err = e
                continue
        if app is None:
            return None

        try:
            app.Visible = False
        except Exception:
            pass
        try:
            app.DisplayAlerts = 0
        except Exception:
            pass

        # comtypes 的 dynamic Dispatch 不接受关键字参数，必须按位置传。
        # Word 的 Open 签名：Open(FileName, ConfirmConversions, ReadOnly,
        # AddToRecentFiles, ...)
        doc = app.Documents.Open(os.path.abspath(path), False, True, False)
        # 导出为 PDF 再交给 pdfium 渲染首页：比 ExportAsFixedFormat 直接出图
        # 更省事，因为 Word 没有稳定的「导出首页为 PNG」接口
        tmp = os.path.join(tempfile.gettempdir(),
                           f'_itrace_{abs(hash(path)) % 10**10}.pdf')
        # ExportAsFixedFormat(OutputFileName, ExportFormat, ...)
        # 17 = wdExportFormatPDF
        doc.ExportAsFixedFormat(tmp, 17)
        if os.path.isfile(tmp):
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(tmp)
            try:
                if len(pdf):
                    w, h = pdf[0].get_size()
                    scale = min(1.0, 512 / max(w, h))
                    bmp = pdf[0].render(scale=scale)
                    im = bmp.to_pil().convert('RGB')
                    out = im
            finally:
                pdf.close()
    except Exception:
        out = None
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:
            pass
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return out


def extract(path, ext):
    """返回 (PIL.Image, error_code)。"""
    from . import shellthumb

    # 1. 系统缩略图处理器（最快）
    im = shellthumb.get_thumbnail(path, 512)
    if im is not None and max(im.size) >= 64:
        return im, None

    # 2. 包内 docProps 缩略图
    if ext in ('docx', 'pptx', 'xlsx', 'docm', 'pptm', 'xlsm'):
        im = _from_ooxml_thumbnail(path)
        if im is not None:
            return im, None

    # 3. .doc 的 OLE 缩略图流
    if ext == 'doc':
        im = _from_doc_summary(path)
        if im is not None:
            return im, None

    # 4. Word / WPS 导出（慢，兜底）
    if ext in ('doc', 'docx', 'docm'):
        im = _from_word_com(path)
        if im is not None:
            return im, None
        return None, 'no_preview_com'

    return None, 'no_preview'
