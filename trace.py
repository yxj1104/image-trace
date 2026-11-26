#!/usr/bin/env python3
"""溯源 / ImageTrace — 本地设计文件反向检索。

用法：
    python trace.py index D:/workspace          建立/更新索引
    python trace.py index E: F: G: --jobs 8     多盘并行
    python trace.py find 查询图.jpg              检索
    python trace.py find 查询图.jpg --json       输出 JSON
    python trace.py stats                       索引概况
    python trace.py pair                        重新计算配对
    python trace.py serve                       启动 Web 界面
"""
import argparse
import os
import sys
import time
import json
import io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import db as dbmod
from core import fingerprint, retrieve, scanner
import extract as extractor

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'data', 'index.db')
THUMB_DIR_NAME = 'thumbs'

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

SKIP_EXT = {'tmp', 'log', 'db', 'ini', 'dat'}

# 按前缀排除：这些目录名后面常带日期/版本号
EXCLUDE_PREFIX = (
    '_codex_archive', 'photoshop temp', 'psautorecover', 'adobe temp',
    '腾讯电脑管家', '360recycle', 'backup_of_',
)


def is_excluded(path):
    return scanner.is_excluded(path)


def _norm_root(root):
    return scanner.norm_root(root)


def walk_files(roots, verbose=True):
    return scanner.walk_files(roots, verbose=verbose)


def build_record(path, ext, thumb_dir):
    return scanner.build_record(path, ext, thumb_dir)


def _worker(args):
    return scanner.worker(args)


def cmd_index(args):
    db_path = args.db or DEFAULT_DB
    data_dir = os.path.dirname(db_path)
    thumb_dir = os.path.join(data_dir, THUMB_DIR_NAME)
    os.makedirs(thumb_dir, exist_ok=True)

    roots = args.roots
    print(f'索引根路径: {roots}')
    print(f'数据库: {db_path}')

    t0 = time.time()
    files = list(walk_files(roots))
    print(f'待处理文件: {len(files)} 个  （扫描耗时 {time.time()-t0:.1f}s）')
    if not files:
        print('没有找到可索引的文件。')
        return 1

    db = dbmod.DB(db_path)
    existing = db.existing_paths()
    todo = [(p, e, thumb_dir) for p, e in files if p not in existing]
    print(f'已有记录 {len(existing)}，需新增 {len(todo)}')

    if not todo:
        print('全部已索引，跳过。')
        _reindex_removed(db, files)
        db.close()
        return 0

    jobs = args.jobs
    done = 0
    errors = 0
    t1 = time.time()
    try:
        if jobs and jobs > 1:
            from concurrent.futures import ProcessPoolExecutor, as_completed
            with ProcessPoolExecutor(jobs) as pool:
                futures = {pool.submit(_worker, t): t[0] for t in todo}
                for fut in as_completed(futures):
                    rec = fut.result()
                    done += 1
                    if rec:
                        if rec.get('extract_error'):
                            errors += 1
                        db.upsert(rec)
                    if done % 200 == 0:
                        db.commit()
                        el = time.time() - t1
                        rate = done / el if el else 0
                        eta = (len(todo) - done) / rate if rate else 0
                        print(f'  {done}/{len(todo)}  {rate:.1f} 个/s  '
                              f'剩余约 {eta/60:.1f} 分钟', file=sys.stderr)
        else:
            for t in todo:
                rec = _worker(t)
                done += 1
                if rec:
                    if rec.get('extract_error'):
                        errors += 1
                    db.upsert(rec)
                if done % 100 == 0:
                    db.commit()
                    el = time.time() - t1
                    rate = done / el if el else 0
                    eta = (len(todo) - done) / rate if rate else 0
                    print(f'  {done}/{len(todo)}  {rate:.1f} 个/s  '
                          f'剩余约 {eta/60:.1f} 分钟', file=sys.stderr)
    except KeyboardInterrupt:
        print('\n中断，已保存部分结果。', file=sys.stderr)
        db.commit()
        db.close()
        return 130

    db.commit()
    elapsed = time.time() - t1
    print(f'\n索引完成: 新增 {done} 个，其中提取失败 {errors} 个，耗时 {elapsed/60:.1f} 分钟')

    _reindex_removed(db, files)
    for r in roots:
        db.add_root(r)
    db.set_meta('last_index', int(time.time()))
    db.close()
    print('可以运行 `python trace.py pair` 计算文件配对。')
    return 0


def _reindex_removed(db, files):
    keep = {p for p, _ in files}
    gone = db.delete_missing(keep)
    if gone:
        print(f'清理已删除文件记录: {gone} 条')


def cmd_stats(args):
    db = dbmod.DB(args.db or DEFAULT_DB)
    s = db.stats()
    print(f'总记录: {s["total"]}')
    print('\n按类型:')
    for k, c in s['by_kind']:
        print(f'  {k:10s} {c:7d}')
    print('\n按格式:')
    for k, c in s['by_ext']:
        print(f'  {k:10s} {c:7d}')
    if s['errors']:
        print('\n提取失败:')
        for k, c in s['errors']:
            print(f'  {k:28s} {c:6d}')
    db.close()
    return 0


def cmd_pair(args):
    db = dbmod.DB(args.db or DEFAULT_DB)
    rows = db.all_files()
    if not rows:
        print('索引为空，请先运行 index。')
        db.close()
        return 1

    print(f'计算配对，共 {len(rows)} 条记录...')
    t0 = time.time()
    from core import pairing

    db.clear_groups()
    by_name = pairing.pair_by_name(rows)
    by_hash = pairing.pair_by_hash(rows)
    print(f'  名称配对 {len(by_name)} 对  '
          f'指纹配对 {len(by_hash)} 对  ({time.time()-t0:.1f}s)')

    # 合并成一个简单并查集，同一画面的文件归到一组
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    reasons = {}
    for (a, b), rs in list(by_name.items()) + list(by_hash.items()):
        union(a, b)
        reasons.setdefault(find(a), []).extend(rs)

    clusters = {}
    for node in list(parent.keys()):
        clusters.setdefault(find(node), []).append(node)

    by_id = {r['id']: r for r in rows}
    gid = 1
    made = 0
    for root, members in clusters.items():
        if len(members) < 2:
            continue
        srcs = [m for m in members if by_id[m]['kind'] == 'source'] or members
        exps = [m for m in members if by_id[m]['kind'] == 'export']
        source_id = srcs[0]
        cover_id = exps[0] if exps else members[0]
        rs = []
        for m in members:
            for r in reasons.get(find(m), []):
                if r not in rs:
                    rs.append(r)
        conf = 0.95 if any('同目录同主名' in r for r in rs) else 0.7
        db.set_group(gid, cover_id, source_id, conf, rs, members)
        gid += 1
        made += 1

    db.commit()
    print(f'生成 {made} 个画面组，耗时 {time.time()-t0:.1f}s')
    db.close()
    return 0


def _load_query_image(spec):
    """支持本地文件路径或 -（stdin 读二进制）。"""
    from PIL import Image
    if spec == '-':
        data = sys.stdin.buffer.read()
        return Image.open(io.BytesIO(data)).convert('RGB')
    im = Image.open(spec)
    im.load()
    if im.mode != 'RGB':
        if im.mode in ('RGBA', 'LA', 'P'):
            bg = Image.new('RGB', im.size, (255, 255, 255))
            rgba = im.convert('RGBA')
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        else:
            im = im.convert('RGB')
    return im


def cmd_find(args):
    db_path = args.db or DEFAULT_DB
    if not os.path.exists(db_path):
        print('索引不存在，请先运行 index。', file=sys.stderr)
        return 1

    qim = _load_query_image(args.image)
    db = dbmod.DB(db_path)
    rows = db.all_files(with_orb=True)
    if not rows:
        print('索引为空。', file=sys.stderr)
        db.close()
        return 1

    t0 = time.time()
    idx = retrieve.Index(rows, with_orb=True)
    t_load = time.time() - t0

    t0 = time.time()
    results = retrieve.search(idx, qim, trim=not args.no_trim,
                              use_orb=not args.no_orb, limit=args.limit)
    results = retrieve.expand_groups(idx, results)
    t_search = time.time() - t0

    if args.json:
        out = []
        for r in results:
            row = r['row']
            out.append({
                'path': row['path'],
                'name': row['name'],
                'kind': row['kind'],
                'ext': row['ext'],
                'size': row['size'],
                'width': row['width'],
                'height': row['height'],
                'phash_dist': r['phash_dist'],
                'orb': round(r['orb'], 4) if r['orb'] is not None else None,
                'rotation': r['rotation'],
                'weak': r['weak'],
                'via': r.get('via', 'visual'),
                'extract_error': row['extract_error'],
                'group_id': row['group_id'],
            })
        print(json.dumps({'results': out,
                          'load_ms': round(t_load * 1000, 1),
                          'search_ms': round(t_search * 1000, 1)},
                         ensure_ascii=False, indent=2))
        db.close()
        return 0

    print(f'库 {len(rows)} 条，载入 {t_load*1000:.0f}ms，检索 {t_search*1000:.0f}ms\n')
    if not results:
        print('没有找到匹配的文件。')
        db.close()
        return 0

    print(f'{"#":>3}  {"匹配":>6}  {"pHash":>6}  {"依据":>4}  路径')
    print('-' * 100)
    for i, r in enumerate(results, 1):
        row = r['row']
        orb = f'{r["orb"]:.3f}' if r['orb'] is not None else '  -  '
        dist = f'{r["phash_dist"]:>6}' if r['phash_dist'] is not None else '     -'
        tag = '配对' if r.get('via') == 'paired' else (' 弱' if r['weak'] else ' 视觉')
        mark = ' ★' if row['kind'] == 'source' else ''
        print(f'{i:>3}  {orb}{"":>2}  {dist}  {tag:>4}  {row["path"]}{mark}')

    # 显示找到的源文件
    srcs = [r for r in results if r['row']['kind'] == 'source']
    if srcs:
        print(f'\n★ 找到 {len(srcs)} 个源文件：')
        for r in srcs[:10]:
            print(f'  ▸ {r["row"]["path"]}')
    else:
        print('\n未找到源文件（可能已删除，或只有导出图）')

    db.close()
    return 0


def cmd_serve(args):
    from server import run
    return run(db_path=args.db or DEFAULT_DB, port=args.port,
               open_browser=not args.no_browser)


def main():
    ap = argparse.ArgumentParser(
        description='溯源 — 本地设计文件反向检索',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument('--db', help=f'数据库路径（默认 {DEFAULT_DB}）')
    sub = ap.add_subparsers(dest='cmd')

    p = sub.add_parser('index', help='建立/更新索引')
    p.add_argument('roots', nargs='+', help='要索引的目录或盘符')
    p.add_argument('--jobs', '-j', type=int, default=os.cpu_count() or 4,
                   help='并行进程数（默认 CPU 核数）')
    p.set_defaults(func=cmd_index)

    p = sub.add_parser('find', help='以图搜图')
    p.add_argument('image', help='查询图路径，或 - 表示从标准输入读')
    p.add_argument('--limit', '-n', type=int, default=30)
    p.add_argument('--json', action='store_true')
    p.add_argument('--no-trim', action='store_true', help='不做自动裁白边')
    p.add_argument('--no-orb', action='store_true', help='只用 pHash，不重排')
    p.set_defaults(func=cmd_find)

    p = sub.add_parser('stats', help='索引概况')
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser('pair', help='计算文件配对关系')
    p.set_defaults(func=cmd_pair)

    p = sub.add_parser('serve', help='启动 Web 界面')
    p.add_argument('--port', type=int, default=8848)
    p.add_argument('--no-browser', action='store_true')
    p.set_defaults(func=cmd_serve)

    args = ap.parse_args()
    if not getattr(args, 'func', None):
        ap.print_help()
        return 1
    return args.func(args) or 0


if __name__ == '__main__':
    sys.exit(main())

