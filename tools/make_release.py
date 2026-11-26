"""打完包后一键生成三个发布 zip 和校验和。

**为什么要有这个脚本**：手工压包容易出三个错 ——
  1. 把 data/ 打进去（那是本机索引，含用户绝对路径，190MB+354MB）
  2. 忘了把「启动说明.txt」放到 exe 旁边
  3. 忘了给 lite 包剔除 cn_clip.onnx，结果两个包一样大

用法（先跑完 PyInstaller）：
    python -m PyInstaller build.spec --noconfirm
    python tools/collect_licenses.py
    python tools/make_release.py --version 1.0.0
"""
import argparse
import hashlib
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, 'dist')
DOC = os.path.join(ROOT, 'docs', '启动说明.txt')

CLIP_MODEL = 'cn_clip.onnx'
MODEL_FILES = ('cn_clip.onnx', 'cn_clip_tokenizer.json', 'face_yunet.onnx')

# 不进 zip 的东西
SKIP_DIRS = {'data', '__pycache__'}
SKIP_FILES = {'index.db', 'index.db-shm', 'index.db-wal',
              'app.log', 'selftest.log', 'window.json',
              '.focus_request'}


def find_product():
    """找打包产物目录（含 <dirname>.exe 的那个）。

    跳过 `_` 开头的（_stage 是本脚本自己建的临时目录）。
    """
    for name in os.listdir(DIST):
        if name.startswith('_'):
            continue
        p = os.path.join(DIST, name)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, name + '.exe')):
            return p, name
    return None, None


def zip_filtered(src, dst, name, skip_model=None, extra_files=None):
    """直接从产物目录压缩，跳过 data/ 等不该进包的东西。

    **不再复制到临时目录再压。** 原来那样峰值要占
    「产物 533MB + 副本 533MB + zip 283MB」≈ 1.3GB，
    实测在只剩 1.5MB 的盘上直接 OSError。
    边走边压只需「产物 + zip」，省掉一整份副本。
    """
    n = 0
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, dirs, files in os.walk(src):
            # 就地裁剪目录列表，os.walk 不会进这些目录
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if f in SKIP_FILES or f == skip_model:
                    continue
                p = os.path.join(root, f)
                rel = os.path.relpath(p, src)
                z.write(p, os.path.join(name, rel))
                n += 1
        # 启动说明放 exe 旁边
        if extra_files:
            for path, arc in extra_files:
                if os.path.isfile(path):
                    z.write(path, os.path.join(name, arc))
                    n += 1
    return n, os.path.getsize(dst)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--version', default='1.0.0')
    ap.add_argument('--skip-lite', action='store_true')
    ap.add_argument('--out', default=None,
                    help='zip 输出目录（默认 dist/）。'
                         '所在盘空间不足时指到别的盘，如 C:/release')
    args = ap.parse_args()


    src, name = find_product()
    if not src:
        print('找不到打包产物。先跑 python -m PyInstaller build.spec')
        return 1

    print(f'产物目录: {src}')
    made = []

    out_dir = os.path.abspath(args.out) if args.out else DIST
    os.makedirs(out_dir, exist_ok=True)
    extra = [(DOC, '启动说明.txt')]

    # ---- 全量包 ----
    print('\n[1/3] 全量包...')
    zp = os.path.join(out_dir, f'{name}-{args.version}-win64.zip')
    n, size = zip_filtered(src, zp, name, extra_files=extra)
    print(f'  {os.path.basename(zp)}: {n} 文件, {size / 2**20:.0f}MB')
    made.append(zp)

    # ---- lite 包 ----
    if not args.skip_lite:
        print('\n[2/3] lite 包（剔除 CLIP 模型）...')
        zp = os.path.join(out_dir, f'{name}-{args.version}-lite.zip')
        n, size = zip_filtered(src, zp, name, skip_model=CLIP_MODEL,
                               extra_files=extra)
        print(f'  {os.path.basename(zp)}: {n} 文件, {size / 2**20:.0f}MB')
        made.append(zp)

    # ---- 模型包 ----
    print('\n[3/3] 模型包（源码运行用）...')
    mdir = os.path.join(ROOT, 'models')
    avail = [f for f in MODEL_FILES if os.path.isfile(os.path.join(mdir, f))]
    if len(avail) < len(MODEL_FILES):
        print(f'  ⚠️ models/ 缺文件: '
              f'{set(MODEL_FILES) - set(avail)}，跳过模型包')
    else:
        zp = os.path.join(out_dir, f'models-{args.version}.zip')
        with zipfile.ZipFile(zp, 'w', zipfile.ZIP_DEFLATED,
                             compresslevel=6) as z:
            for f in MODEL_FILES:
                z.write(os.path.join(mdir, f), 'models/' + f)
        print(f'  {os.path.basename(zp)}: '
              f'{len(MODEL_FILES)} 文件, {os.path.getsize(zp) / 2**20:.0f}MB')
        made.append(zp)

    # ---- 校验和 ----
    print('\n生成校验和...')
    lines = []
    for p in made:
        d = sha256(p)
        lines.append(f'{d}  {os.path.basename(p)}  '
                     f'({os.path.getsize(p) / 2**20:.0f} MB)')
        print('  ' + lines[-1][:20] + '...  ' + os.path.basename(p))
    sig = os.path.join(out_dir, 'SHA256SUMS.txt')
    with open(sig, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'  -> {sig}')

    print('\n完成。需要上传到 GitHub Release 的文件：')
    for p in made + [sig]:
        print('  ' + os.path.basename(p))
    return 0


if __name__ == '__main__':
    sys.exit(main())
