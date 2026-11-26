"""按扩展名分派到对应提取器。"""
from . import psd, pdf, cdr, raster, svg, eps, office

# 源文件类型（可编辑的设计/文档源文件）
SOURCE_EXTS = {'psd', 'ai', 'cdr', 'eps', 'svg', 'indd',
               'doc', 'docx', 'docm', 'ppt', 'pptx', 'pptm',
               'xls', 'xlsx', 'xlsm'}
# 导出/位图类型
EXPORT_EXTS = {'png', 'jpg', 'jpeg', 'webp', 'tif', 'tiff', 'bmp', 'gif', 'pdf'}
# 全部支持
ALL_EXTS = SOURCE_EXTS | EXPORT_EXTS

# 仍然不提取的（InDesign 需要专用 SDK）
DEFERRED_EXTS = {'indd'}

# Office 文档（走 office.py 的多级回退）
OFFICE_EXTS = {'doc', 'docx', 'docm', 'ppt', 'pptx', 'pptm',
               'xls', 'xlsx', 'xlsm'}


def kind_of(ext):
    ext = ext.lower()
    if ext in SOURCE_EXTS:
        return 'source'
    if ext in EXPORT_EXTS:
        return 'export'
    return 'other'


def extract(path, ext):
    """返回 (PIL.Image, error_code, thumb_kind)。"""
    ext = ext.lower()
    if ext == 'psd':
        im, err = psd.extract(path)
        return im, err, 'embedded'
    if ext in ('ai', 'pdf'):
        im, err = pdf.extract(path)
        return im, err, 'rendered'
    if ext == 'cdr':
        im, err = cdr.extract(path)
        return im, err, 'embedded'
    if ext == 'svg':
        im, err = svg.extract(path)
        return im, err, 'rendered'
    if ext == 'eps':
        im, err = eps.extract(path)
        return im, err, 'embedded'
    if ext in OFFICE_EXTS:
        im, err = office.extract(path, ext)
        return im, err, 'rendered'
    if ext in ('png', 'jpg', 'jpeg', 'webp', 'tif', 'tiff', 'bmp', 'gif'):
        im, err = raster.extract(path)
        return im, err, 'native'
    return None, 'unsupported', None
