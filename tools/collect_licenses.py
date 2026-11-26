"""把全部第三方许可证收集成一份文本，放进打包产物。

**为什么需要这一步**：PyInstaller 只保留 2 个 `dist-info` 目录，其余包的
`LICENSE` 文件都被丢掉（实测 rapidocr / pillow / psd-tools / pypdfium2
的许可证在 `_internal/` 里全都找不到）。

Apache-2.0 §4(a) 明确要求「随分发物提供许可证副本」。RapidOCR（OCR 模型）
和 YuNet（人脸模型）都是 Apache-2.0，只发 exe 不给许可证文本是**不合规的**。
MIT 和 BSD 也要求「保留版权声明」。

所以在打包后跑一遍这个脚本，把每个依赖的许可证拼成
`dist/<产物名>/THIRD_PARTY_LICENSES.txt`。

用法（打包之后）：
    python tools/collect_licenses.py
"""
import importlib.metadata as md
import os
import sys


# 需要收集的包。顺序按「用户最可能关心」排：模型相关在前。
PACKAGES = [
    ('rapidocr-onnxruntime', 'RapidOCR（图中文识别的 OCR）'),
    ('onnxruntime', 'ONNX Runtime（模型推理引擎）'),
    ('pillow', 'Pillow（图像读写）'),
    ('numpy', 'NumPy（数值计算）'),
    ('opencv-python-headless', 'OpenCV（人脸检测 / 图像处理）'),
    ('imagehash', 'ImageHash（感知哈希）'),
    ('pypdfium2', 'pypdfium2 / PDFium（PDF 与 AI 渲染）'),
    ('psd-tools', 'psd-tools（PSD 解析）'),
    ('scipy', 'SciPy（FFT）'),
    ('resvg-py', 'resvg-py（SVG 渲染）'),
    ('pywebview', 'pywebview（桌面窗口外壳）'),
    ('pythonnet', 'pythonnet（.NET 互操作）'),
    ('clr-loader', 'clr-loader（.NET 运行时加载）'),
    ('comtypes', 'comtypes（Windows COM）'),
    ('olefile', 'olefile（OLE 复合文档）'),
]

# 这些包的许可证以「项目根 LICENSE 文件」形式存在，
# 而不是 dist-info/licenses/ 下
ROOT_LICENSE = {
    'onnxruntime': 'LICENSE',
    'numpy': 'LICENSE.txt',
}

# 安装包里没带许可证文件、需要项目自己存一份的。
# rapidocr-onnxruntime 1.2.3 的 wheel 里确认没有 LICENSE（实测
# `importlib.metadata` 查不到任何 LICENSE 文件），但它是 Apache-2.0。
VENDORED = {
    'rapidocr-onnxruntime': 'RapidOCR-APACHE-2.0.txt',
}


def find_project_dist():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dist = os.path.join(root, 'dist')
    if not os.path.isdir(dist):
        return None
    for name in os.listdir(dist):
        p = os.path.join(dist, name)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, name + '.exe')):
            return p
    return None


def license_texts(pkg):
    """返回 [(文件名, 文本), ...]。找不到返回空列表。"""
    try:
        d = md.distribution(pkg)
    except Exception:
        return []

    out = []
    files = [str(f) for f in (d.files or [])]

    # 1) dist-info/licenses/ 下的
    for f in files:
        u = f.upper()
        if 'LICENSE' not in u and 'COPYING' not in u:
            continue
        # 过滤掉第三方依赖清单以外的臃肿文件（pypdfium2 带几十个子依赖许可证，
        # 那些本身也要求保留，所以保留；但跳过明显是测试数据的）
        if '/TESTS/' in u or '\\TESTS\\' in u:
            continue
        try:
            path = d.locate_file(f)
            txt = open(str(path), encoding='utf-8', errors='ignore').read()
            if len(txt.strip()) < 20:
                continue
            out.append((f, txt))
        except Exception:
            continue

    # 1.5) rapidocr 的 PyPI 包没带 LICENSE（实测确认），
    # 但它是 Apache-2.0，必须随分发附带全文。从这里兜底。
    if not out and pkg in VENDORED:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'licenses', VENDORED[pkg])
        if os.path.isfile(path):
            out.append((VENDORED[pkg],
                        open(path, encoding='utf-8', errors='ignore').read()))

    # 2) 包根目录的 LICENSE（onnxruntime / numpy 就是这种）
    if not out and pkg in ROOT_LICENSE:
        try:
            path = d.locate_file(ROOT_LICENSE[pkg])
            if os.path.isfile(str(path)):
                txt = open(str(path), encoding='utf-8', errors='ignore').read()
                out.append((ROOT_LICENSE[pkg], txt))
        except Exception:
            pass

    return out


def main():
    target = find_project_dist()
    if not target:
        print('找不到打包产物（dist/<名字>/<名字>.exe）')
        return 1

    dst = os.path.join(target, 'THIRD_PARTY_LICENSES.txt')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    me = os.path.join(root, 'LICENSE')

    chunks = []
    chunks.append('本软件包含以下第三方组件，各自的许可证全文如下。\n')
    chunks.append('=' * 72 + '\n')

    # 本项目自己的
    if os.path.isfile(me):
        chunks.append('\n\n### 本项目：ImageTrace / 溯源  —— MIT\n')
        chunks.append('=' * 72 + '\n\n')
        chunks.append(open(me, encoding='utf-8').read())

    missing = []
    for pkg, desc in PACKAGES:
        texts = license_texts(pkg)
        try:
            ver = md.version(pkg)
        except Exception:
            ver = '?'
        if not texts:
            missing.append(pkg)
            chunks.append(f'\n\n### {desc}\n')
            chunks.append(f'包名: {pkg} {ver}\n')
            chunks.append('本包安装时未附带许可证文件，'
                          '请见其 PyPI 页面获取协议全文。\n')
            continue
        for fname, txt in texts:
            chunks.append(f'\n\n### {desc}\n')
            chunks.append(f'包名: {pkg} {ver}    文件: {fname}\n')
            chunks.append('=' * 72 + '\n\n')
            chunks.append(txt)

    # 模型权重的归属（不是 pip 包，单独说明）
    chunks.append('\n\n### 模型权重\n')
    chunks.append('=' * 72 + '\n\n')
    chunks.append(
        'cn_clip.onnx / cn_clip_tokenizer.json\n'
        '  来源: Chinese-CLIP (https://github.com/OFA-Sys/Chinese-CLIP)\n'
        '  许可证: MIT\n'
        '  说明: 本项目原样分发，未修改权重，未用于训练其他模型。\n\n'
        'face_yunet.onnx\n'
        '  来源: OpenCV Zoo (https://github.com/opencv/opencv_zoo)\n'
        '  许可证: Apache-2.0\n\n'
        'rapidocr_onnxruntime/models/*.onnx\n'
        '  来源: PP-OCRv3 (PaddleOCR) / RapidOCR\n'
        '  许可证: Apache-2.0\n'
    )

    text = ''.join(chunks)
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(text)

    print(f'已写入 {dst}')
    print(f'共 {len(text) // 1024} KB')
    if missing:
        print(f'⚠️ 以下包未找到许可证文件: {", ".join(missing)}')
        print('   （已在输出文件里标注，需手工补全）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
