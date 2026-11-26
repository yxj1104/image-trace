"""只重建缩略图缓存，不重跑指纹与入库。

用途：不小心删掉了 `data/thumbs/`（比如清理磁盘时），卡片全变成灰图标。
这时不需要重跑 20 分钟的索引 —— 库里已经有每条记录的 `thumb_path`，
按原文件名重新生成缩略图即可。指纹、配对关系、OCR、语义向量全部保留。

用法：
    python tools/rebuild_thumbs.py --limit 300    # 先试 300 张看效果
    python tools/rebuild_thumbs.py                # 补全剩余的
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db as dbmod          # noqa: E402
from core import paths as pathmod     # noqa: E402
from core import fingerprint          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0,
                    help='只处理前 N 张（0 = 全部）')
    ap.add_argument('--db', default=None, help='索引库路径')
    args = ap.parse_args()

    db_path = args.db or pathmod.db_path()
    thumb_dir = os.path.join(os.path.dirname(os.path.abspath(db_path)), 'thumbs')
    os.makedirs(thumb_dir, exist_ok=True)
    print(f'索引库: {db_path}')
    print(f'缩略图目录: {thumb_dir}')

    db = dbmod.DB(db_path)
    rows = db.conn.execute(
        "SELECT id, path, ext, thumb_path FROM files "
        "WHERE thumb_path IS NOT NULL AND thumb_path != '' "
        "ORDER BY id").fetchall()
    db.close()

    todo = [r for r in rows
            if not os.path.isfile(os.path.join(thumb_dir, r['thumb_path']))]
    if args.limit:
        todo = todo[:args.limit]

    print(f'库内 {len(rows)} 条，缺缩略图 {len(todo)} 张')
    if not todo:
        print('无需处理')
        return 0

    # 按需导入：这个脚本可能被单独拷走用，不想拖 slow import
    from extract import extract as extract_file

    t0 = time.time()
    done = fail = skip = 0
    for r in todo:
        p = r['path']
        if not os.path.isfile(p):
            skip += 1
            continue
        try:
            im, err, _kind = extract_file(p, r['ext'])
            if im is None:
                fail += 1
                continue
            jpg = fingerprint.thumb_jpeg(im)
            if not jpg:
                fail += 1
                continue
            with open(os.path.join(thumb_dir, r['thumb_path']), 'wb') as f:
                f.write(jpg)
            done += 1
        except Exception:
            fail += 1
        if done and done % 100 == 0:
            el = time.time() - t0
            print(f'  {done}/{len(todo)}  {done / el:.1f} 张/秒')

    el = time.time() - t0
    print(f'完成 {done} 张，失败 {fail}，源文件不存在 {skip}，用时 {el:.0f} 秒')
    if fail:
        print('失败的通常是加密 PDF 或损坏文件，界面上会显示占位图标')
    return 0


if __name__ == '__main__':
    sys.exit(main())
