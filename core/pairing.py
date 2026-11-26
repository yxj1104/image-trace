"""配对阶段：把同一画面的文件组织成组。

三路信号：
  B1 同目录同主名（实测 2,187 对，占 PSD 40%）
  B2 去掉版本后缀后同名
  B3 pHash ≤ 6 且宽高比接近
"""
import os
import re
from collections import defaultdict

VERSION_SUFFIX = re.compile(
    r'([_\-\s]*(v|ver|版本)?\s*\d+(\.\d+)?)*'
    r'([_\-\s]*(终稿|定稿|最终|final|ok|ok版|副本|copy|改|修改|新|备份|backup|back)\d*)*$',
    re.IGNORECASE,
)
TRAILING_NUM = re.compile(r'([_\-\s]*\d{1,2})?$')


def normalize_stem(stem):
    """去掉版本后缀，用于跨目录宽松配对。

    例：'海报A_终稿_v3' -> '海报A'
          'Campaign0110' -> 'campaign0110'  （日期数字保留，因为它区分版本）
    """
    s = stem.lower().strip()
    s = re.sub(r'[\s_\-]+$', '', s)
    # 只去掉明确的版本词，不动数字（数字往往是日期/版本辨识关键）
    for word in ('终稿', '定稿', '最终', '修改', '副本', '备份', '新', '改'):
        s = s.replace(word, '')
    s = re.sub(r'[_\-\s]*(final|ok|copy|backup|back)\d*$', '', s, flags=re.I)
    s = re.sub(r'[_\-\s]+v\d+(\.\d+)?$', '', s, flags=re.I)
    s = re.sub(r'[\s_\-]+$', '', s)
    return s or stem.lower()


def pair_by_name(rows):
    """B1 + B2：按目录+主名、以及归一化主名配对。

    返回 {(source_id, export_id): reason_list}
    """
    by_dir_stem = defaultdict(lambda: {'source': [], 'export': []})
    by_stem = defaultdict(lambda: {'source': [], 'export': []})

    for r in rows:
        d = os.path.dirname(r['path'])
        by_dir_stem[(d, r['stem'].lower())][r['kind']].append(r)
        by_stem[normalize_stem(r['stem'])][r['kind']].append(r)

    pairs = {}

    def add(s, e, reason):
        key = (s['id'], e['id'])
        pairs.setdefault(key, []).append(reason)

    for bucket in by_dir_stem.values():
        for s in bucket['source']:
            for e in bucket['export']:
                add(s, e, '同目录同主名')

    for bucket in by_stem.values():
        for s in bucket['source']:
            for e in bucket['export']:
                # 归一化同名跨目录，要求宽高比接近才算
                if _aspect_close(s, e, tol=0.06):
                    add(s, e, '名称归一后相同')

    return pairs


def _hamming_hex(h1, h2):
    """两个十六进制 pHash 的汉明距离。"""
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count('1')
    except (ValueError, TypeError):
        return 999


def _aspect_close(a, b, tol=0.05):
    try:
        ra, rb = a['aspect'], b['aspect']
        if not ra or not rb:
            return False
        return abs(ra - rb) / max(ra, rb) <= tol
    except Exception:
        return False


def pair_by_hash(rows, max_dist=6):
    """B3：pHash 距离 ≤ max_dist 且宽高比接近。

    用长度 16 的十六进制前缀分桶加速，避免 O(n²) 全比。
    实测不同图之间距离 ≤ 10 的仅占 0.01%。
    """
    candidates = [r for r in rows if r['phash'] and r['aspect']]
    buckets = defaultdict(list)
    for r in candidates:
        # 用前 4 个十六进制字符（16bit）分桶，同桶内再精算
        buckets[r['phash'][:4]].append(r)

    pairs = {}
    for bucket in buckets.values():
        if len(bucket) > 400:  # 桶过大退化，跳过避免卡死
            continue
        for i in range(len(bucket)):
            a = bucket[i]
            for j in range(i + 1, len(bucket)):
                b = bucket[j]
                if a['kind'] == b['kind'] and a['ext'] == b['ext']:
                    continue  # 同格式同类型的重复留给 hash 查重，不组画面组
                if not _aspect_close(a, b, tol=0.04):
                    continue
                d = _hamming_hex(a['phash'], b['phash'])
                if d <= max_dist:
                    key = tuple(sorted((a['id'], b['id'])))
                    pairs.setdefault(key, []).append(f'指纹距离 {d}')
    return pairs
