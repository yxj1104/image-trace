"""扫描与单文件处理。

从 trace.py 抽出来：打包成 exe 后，服务在后台线程里跑索引，
不能再回头 import trace（那是入口脚本，不在包路径里）。

CLI 和后台索引作业共用这里的函数。
"""
import os
import sys
import json
import time

import extract as extractor
from . import fingerprint

# 实测噪音目录：F:\腾讯电脑管家软件搬家 单目录 33,084 个文件，
# 全是 Illustrator 安装包自带素材
EXCLUDE_PARTS = {
    '$recycle.bin', 'system volume information', '360recyclebin',
    'program files', 'program files (x86)', 'appdata', 'windows',
    '腾讯电脑管家软件搬家', 'wxwork', 'ps2026', 'coreldraw',
    'adobe photoshop', 'adobe illustrator', 'adobe',
    'steam', 'kugou', 'jianyingpro drafts', 'jianyingpro',
    'adobetemp', '_codex_archive', 'node_modules', '.git',
    'photoshop temp', 'psautorecover', 'genp',
}

# 按前缀排除：这些目录名后面常带日期/版本号
EXCLUDE_PREFIX = (
    '_codex_archive', 'photoshop temp', 'psautorecover', 'adobe temp',
    '腾讯电脑管家', '360recycle', 'backup_of_',
)


def is_excluded(path):
    lp = path.lower().replace('\\', '/')
    for part in EXCLUDE_PARTS:
        if '/' + part + '/' in lp or lp.endswith('/' + part):
            return True
    for seg in lp.split('/'):
        for part in EXCLUDE_PREFIX:
            if seg.startswith(part):
                return True
    base = os.path.basename(lp)
    if base.startswith('backup_of_'):
        return True
    # Office 打开文档时会生成 ~$xxx.docx 锁文件（几十字节，不是文档）。
    # 它们没有缩略图、没有内容，进了索引只是噪音。
    if base.startswith('~$') or base.startswith('~'):
        if base.startswith('~$'):
            return True
    # macOS 的压缩包残留：__MACOSX 目录和 ._ 前缀文件都不是真文件。
    # ._xxx.ai 是 AppleDouble 资源分支，几百字节，开头是 \x00\x05\x16\x07。
    # 它们混进索引的后果不只是噪音：pdfium 打开会抛 PdfiumError，
    # 而一次批量语义编码里只要有这种文件，整批 8 张就会被连带算作失败
    # （实测 82 个 AI 里有 3 个是这种，导致进度条频繁报「失败」）。
    if '/__macosx/' in lp or base.startswith('._'):
        return True
    return False


def norm_root(root):
    """规范化根路径。

    坑：不能简单 rstrip 掉结尾斜杠。`os.walk('E:')` 的 dirpath 是 `E:`，
    `os.path.join('E:', 'a')` 得到 `E:a` —— 变成盘符相对路径，既不是绝对
    路径，也让排除规则失效。所以盘符根必须保留分隔符。
    """
    p = os.path.normpath(os.path.abspath(root))
    if p.endswith(':'):
        p += os.sep
    return p


def walk_files(roots, verbose=True):
    """遍历给定根路径，产出 (path, ext)。"""
    seen = 0
    for root in roots:
        root = norm_root(root)
        if not os.path.isdir(root):
            _p(f'  跳过（不存在或不是目录）: {root}')
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if not is_excluded(os.path.join(dirpath, d))]
            for fn in filenames:
                if '.' not in fn:
                    continue
                ext = fn.rsplit('.', 1)[-1].lower()
                if ext not in extractor.ALL_EXTS:
                    continue
                if ext in extractor.DEFERRED_EXTS:
                    continue
                full = os.path.normpath(os.path.join(dirpath, fn))
                if is_excluded(full):
                    continue
                seen += 1
                yield full, ext
        if verbose:
            _p(f'  扫描完成 {root}: 累计 {seen} 个')


def _p(msg):
    try:
        print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass


def build_record(path, ext, thumb_dir):
    """提取单个文件并生成记录。在子进程中执行。"""
    try:
        st = os.stat(path)
        size, mtime = st.st_size, int(st.st_mtime)
    except OSError:
        return None

    im, err, thumb_kind = extractor.extract(path, ext)
    name = os.path.basename(path)
    stem = os.path.splitext(name)[0]

    rec = {
        'path': path,
        'name': name,
        'stem': stem,
        'ext': ext,
        'kind': extractor.kind_of(ext),
        'size': size,
        'mtime': mtime,
        'width': None,
        'height': None,
        'aspect': None,
        'phash': None,
        'phash_bits': 0,
        'orb': b'',
        'palette': None,
        'thumb_path': None,
        'thumb_kind': thumb_kind,
        'extract_error': err,
        'indexed_at': int(time.time()),
    }

    if im is None:
        return rec

    # width/height 用**真实画布尺寸**，不是缩略图尺寸。
    # PSD 内嵌缩略图通常只有 74×160，直接拿来当尺寸显示，
    # 设计师一眼就看出不对（他要的是 1080×2340 这种）。
    real = _real_size(path, ext)
    if real:
        rec['width'], rec['height'] = real
        rec['aspect'] = round(real[0] / real[1], 4) if real[1] else None
    else:
        rec['width'], rec['height'] = im.size
        rec['aspect'] = fingerprint.aspect(im)

    rec['phash'] = fingerprint.phash_hex(im)
    rec['phash_bits'] = fingerprint.phash_bits(rec['phash'])
    rec['orb'] = fingerprint.orb_to_blob(fingerprint.orb_descriptors(im))
    rec['palette'] = json.dumps(fingerprint.palette(im))

    jpg = fingerprint.thumb_jpeg(im)
    if jpg:
        # 用路径哈希命名，避免中文/超长路径带来的文件系统问题
        h = abs(hash(path)) % (10 ** 16)
        fn = f'{h:016x}.jpg'
        out = os.path.join(thumb_dir, fn)
        try:
            with open(out, 'wb') as f:
                f.write(jpg)
            rec['thumb_path'] = fn
        except OSError:
            pass

    return rec


def _real_size(path, ext):
    """读真实画布尺寸。拿不到返回 None，回退到缩略图尺寸。

    PSD 从文件头读（不解析图像数据，几乎零成本）。
    AI/PDF 从 PDF 页面尺寸换算。
    CDR / SVG / 位图直接用各自的头部信息或 PIL。
    """
    try:
        if ext == 'psd':
            from extract import psd as psdmod
            return psdmod.canvas_size(path)
        if ext in ('ai', 'pdf'):
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(path)
            try:
                if len(doc) == 0:
                    return None
                w, h = doc[0].get_size()
                if w and h:
                    return int(round(w)), int(round(h))
            finally:
                doc.close()
            return None
        if ext in ('png', 'jpg', 'jpeg', 'webp', 'tif', 'tiff', 'bmp', 'gif'):
            from PIL import Image as _I
            with _I.open(path) as im:
                return im.size
        if ext == 'svg':
            # 优先读 viewBox，没有就用能解析到的宽高
            import re
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                head = f.read(2048)
            m = re.search(r'viewBox\s*=\s*["\']([^"\']+)["\']', head, re.I)
            if m:
                parts = re.split(r'[\s,]+', m.group(1).strip())
                if len(parts) == 4:
                    w, h = float(parts[2]), float(parts[3])
                    if w > 0 and h > 0:
                        return int(round(w)), int(round(h))
            return None
    except Exception:
        return None
    return None


def worker(args):
    """多进程入口。args = (path, ext, thumb_dir)"""
    path, ext, thumb_dir = args
    try:
        return build_record(path, ext, thumb_dir)
    except Exception as e:
        name = os.path.basename(path)
        return {
            'path': path, 'name': name, 'stem': os.path.splitext(name)[0],
            'ext': ext, 'kind': extractor.kind_of(ext),
            'size': None, 'mtime': None,
            'width': None, 'height': None, 'aspect': None,
            'phash': None, 'phash_bits': 0, 'orb': b'',
            'palette': None, 'thumb_path': None, 'thumb_kind': None,
            'extract_error': 'worker_' + type(e).__name__,
            'indexed_at': int(time.time()),
        }
