"""一次性迁移：把 width/height 从「缩略图尺寸」修正为「真实画布尺寸」。

背景：早期实现直接拿提取出来的缩略图尺寸入库。PSD 的内嵌缩略图
通常只有 74×160，界面上显示成「74×160」而实际画布是 1080×2340，
对设计师来说这是明显错误的数字。

本脚本只读文件头，不重新提取图像，1.8 万条约 2-3 分钟。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db as dbmod
from core.scanner import _real_size


def main(db_path):
    db = dbmod.DB(db_path)
    rows = db.conn.execute(
        "SELECT id, path, ext, width, height, aspect FROM files").fetchall()
    print(f'总记录 {len(rows)}')

    fixed = 0
    skipped = 0
    unchanged = 0
    t0 = time.time()

    for i, r in enumerate(rows, 1):
        ext = (r['ext'] or '').lower()
        real = _real_size(r['path'], ext)
        if not real:
            skipped += 1
        else:
            w, h = real
            if w == r['width'] and h == r['height']:
                unchanged += 1
            else:
                aspect = round(w / h, 4) if h else None
                db.conn.execute(
                    "UPDATE files SET width=?, height=?, aspect=? WHERE id=?",
                    (w, h, aspect, r['id']))
                fixed += 1
        if i % 1000 == 0:
            db.conn.commit()
            el = time.time() - t0
            print(f'  {i}/{len(rows)}  {i/el:.0f} 个/s  已修 {fixed}', flush=True)

    db.conn.commit()
    db.close()
    print(f'\n修正 {fixed} 条，未变 {unchanged} 条，读不到尺寸 {skipped} 条')
    print(f'耗时 {(time.time()-t0)/60:.1f} 分钟')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'data/index.db')
