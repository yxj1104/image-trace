"""AI / PDF 首页渲染。AI 文件本质是 PDF，用 pypdfium2 渲染。

实测：AI 平均 271ms（方差极大，最慢 4.6s），PDF 平均 60ms，失败率 AI 2.5% / PDF 17.5%。
"""
import pypdfium2 as pdfium

MAX_DIM = 480  # 渲染尺寸上限，足够做指纹


def extract(path, timeout_hint=None):
    """返回 (PIL.Image, None) 或 (None, 错误标识)。"""
    doc = None
    try:
        doc = pdfium.PdfDocument(path)
        if len(doc) == 0:
            return None, 'no_page'
        page = doc[0]
        w, h = page.get_size()
        if not w or not h:
            return None, 'zero_size'
        scale = min(1.0, MAX_DIM / max(w, h))
        bmp = page.render(scale=scale)
        im = bmp.to_pil()
        im.load()
        return im.convert('RGB'), None
    except Exception as e:
        return None, 'pdfium_' + type(e).__name__
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
