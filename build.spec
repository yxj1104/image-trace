# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

产物：dist/溯源/溯源.exe（目录模式，启动快、便于排查）
     data/ 与 exe 同级，首次运行自动创建，索引可随文件夹一起拷走
"""

import os

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

ROOT = os.path.abspath(SPECPATH)

# ---- 先收集，再交给 Analysis ----
# 注意：Analysis 接收的 datas/binaries 必须是 (src, dest) 二元组。
# 事后再往 a.datas 上 += 二元组会报 "not enough values to unpack"，
# 因为那时候它已经是 (dest, src, typecode) 三元组了。
datas = [
    (os.path.join(ROOT, 'web'), 'web'),
    (os.path.join(ROOT, 'assets'), 'assets'),
]

# 画面语义搜索的模型（中文 CLIP，182MB）。只打包实际用到的两个文件，
# 不要整目录拷 —— 开发过程中试过英文 CLIP，那 577MB 的模型放在
# models/ 下但不该进安装包。
_mod = os.path.join(ROOT, 'models')
for _f in ('cn_clip.onnx', 'cn_clip_tokenizer.json', 'face_yunet.onnx'):
    _p = os.path.join(_mod, _f)
    if os.path.isfile(_p):
        datas.append((_p, 'models'))

# OCR 的模型和配置（13MB）。rapidocr 从包目录里按相对路径找
# models/*.onnx 和 */config.yaml，PyInstaller 不会自动带上，
# 必须显式打包到对应位置，否则运行时报「模型文件不存在」。
try:
    import rapidocr_onnxruntime as _rr
    _rrbase = os.path.dirname(_rr.__file__)
    datas.append((os.path.join(_rrbase, 'models'),
                  'rapidocr_onnxruntime/models'))
    # 包根也有一份 config.yaml（默认配置），缺了会报
    # "config.yaml does not exist!"
    _root_cfg = os.path.join(_rrbase, 'config.yaml')
    if os.path.isfile(_root_cfg):
        datas.append((_root_cfg, 'rapidocr_onnxruntime'))
    for _sub in ('ch_ppocr_v3_det', 'ch_ppocr_v3_rec', 'ch_ppocr_v2_cls'):
        _cfg = os.path.join(_rrbase, _sub, 'config.yaml')
        if os.path.isfile(_cfg):
            datas.append((_cfg, f'rapidocr_onnxruntime/{_sub}'))
except Exception:
    pass
binaries = []

for pkg in ('cv2', 'pypdfium2', 'pypdfium2_raw', 'psd_tools', 'numpy',
            'scipy', 'resvg_py', 'webview', 'clr_loader', 'comtypes',
            'olefile'):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass
    try:
        datas += collect_data_files(pkg, include_py_files=False)
    except Exception:
        pass

# 这些库 PyInstaller 的自动分析容易漏掉，显式声明
hiddenimports = [
    'pypdfium2',
    'pypdfium2.raw',
    'pypdfium2._helpers',
    'pypdfium2_raw',
    'psd_tools',
    'psd_tools.api',
    'psd_tools.api.layers',
    'psd_tools.compression',
    'psd_tools.psd',
    'cv2',
    'numpy',
    'imagehash',
    'scipy.fftpack',
    'PIL._imaging',
    'PIL.Image',
    'PIL.ImageChops',
    'PIL.ImageDraw',
    'core.scanner',
    'core.indexer',
    'core.paths',
    'tools.shellmenu',
    'extract.svg',
    'extract.eps',
    'resvg_py',
    'webview',
    'webview.platforms.winforms',
    'webview.platforms.edgechromium',
    'clr_loader',
    'pythonnet',
    'core.desktop',
    'extract.office',
    'extract.shellthumb',
    'comtypes',
    'comtypes.client',
    'olefile',
    # 画面语义搜索
    'onnxruntime',
    'onnxruntime.capi',
    'onnxruntime.capi.onnxruntime_pybind11_state',
    'core.semantic',
    'core.embedder',
    'core.faces',
    'core.query',
    'core.ocr',
    'core.ocrindex',
    'rapidocr_onnxruntime',
    'rapidocr_onnxruntime.rapid_ocr_api',
    'rapidocr_onnxruntime.utils',
    'rapidocr_onnxruntime.ch_ppocr_v3_det',
    'rapidocr_onnxruntime.ch_ppocr_v3_rec',
    'rapidocr_onnxruntime.ch_ppocr_v2_cls',
]

excludes = [
    'tkinter', 'matplotlib', 'pandas', 'IPython', 'jupyter',
    'pytest', 'setuptools', 'pip', 'wheel',
    'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'wx',
    'playwright', 'PyInstaller',
]

a = Analysis(
    [os.path.join(ROOT, 'app.py')],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='溯源',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 双击后不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, 'assets', 'icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='溯源',
)
