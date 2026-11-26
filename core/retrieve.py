"""两阶段检索管线。

实测效果（真实 PSD 缩略图 vs 真实导出图）：
  纯 pHash@10：无改动 98% / 裁10% 88% / 旋转90° 14% / 旋转180° 0% / 截图 24%
  两阶段@10  ：无改动 98% / 裁10% 96% / 旋转90° 71% / 旋转180° 88% / 截图 85%
  +旋转校正  ：旋转场景 100%
"""
import numpy as np
from . import fingerprint, matcher, preprocess

RECALL_LIMIT = 50      # 一级召回候选数
ORB_THRESHOLD = 0.10   # 强匹配阈值（实测不同图 p95 = 0.014，max = 0.200）
ORB_WEAK = 0.04        # 弱匹配下限，低于此不再展示
PHASH_GATE = 20        # 一级召回的最大距离容忍
PHASH_TRUST = 8        # 此距离内即使 ORB 分低也保留（缩略图过小会拉低 ORB）


class Index:
    """内存索引。1.8 万条数据的 pHash 最近邻 0.5ms。"""

    def __init__(self, rows, with_orb=True):
        self.rows = list(rows)
        self.n = len(self.rows)
        self.bits = np.array(
            [fingerprint.phash_bits(r['phash']) for r in self.rows],
            dtype=np.uint64) if self.n else np.array([], dtype=np.uint64)
        self.orbs = []
        if with_orb:
            self.orbs = [fingerprint.blob_to_orb(r['orb']) if 'orb' in r.keys() else None
                         for r in self.rows]

    def recall(self, query_hex):
        """pHash 一级召回，返回 [(row_index, distance)]，按距离升序。"""
        if not self.n or not query_hex:
            return []
        q = np.uint64(fingerprint.phash_bits(query_hex))
        # numpy 2.x 有 bitwise_count；退化为查表
        try:
            d = np.bitwise_count(self.bits ^ q)
        except (AttributeError, TypeError):
            d = _popcount_array(self.bits ^ q)
        k = min(RECALL_LIMIT, self.n)
        idx = np.argpartition(d, k - 1)[:k]
        idx = idx[np.argsort(d[idx])]
        return [(int(i), int(d[i])) for i in idx if d[i] <= PHASH_GATE]


def _popcount_array(arr):
    lut = np.array([bin(i).count('1') for i in range(256)], dtype=np.uint8)
    b = arr.view(np.uint8).reshape(-1, 8)
    return lut[b].sum(axis=1)


def search(index, query_im, trim=True, use_orb=True, limit=30, min_orb=ORB_THRESHOLD):
    """查询。返回 [dict]，含 row、phash_dist、orb、rotation、pooled。

    流程：
      1. 查询图裁白边 + 生成 4 旋转版本（+4 个未裁边版本）
      2. 每个版本 pHash 召回 50，取并集 → 候选池
      3. 对候选池用 ORB 重排
    """
    if not index.n:
        return []

    variants = preprocess.prepare(query_im, do_trim=trim)
    pool = {}   # row_index -> {'phash_dist': int, 'rot': deg, 'trimmed': bool}
    for im, rot, trimmed in variants:
        h = fingerprint.phash_hex(im)
        if not h:
            continue
        for ri, dist in index.recall(h):
            cur = pool.get(ri)
            if cur is None or dist < cur['phash_dist']:
                pool[ri] = {'phash_dist': dist, 'rot': rot, 'trimmed': trimmed}

    if not pool:
        return []

    results = []
    if use_orb:
        # 查询侧特征：每个预处理版本各算一次，比完取最高分
        qdescs = []
        seen_ids = set()
        for im, rot, trimmed in variants:
            if id(im) in seen_ids:
                continue
            seen_ids.add(id(im))
            d = fingerprint.orb_descriptors(im)
            if d is not None and len(d) >= 8:
                qdescs.append(d)

        for ri, info in pool.items():
            lib_desc = index.orbs[ri] if ri < len(index.orbs) else None
            best = 0.0
            for d in qdescs:
                s = matcher.score(lib_desc, d)
                if s > best:
                    best = s
                    if best >= 0.98:
                        break
            info['orb'] = best
            info['row_index'] = ri
            results.append(info)

        # 排序：ORB 分优先，同分时 pHash 距离近的在前
        results.sort(key=lambda x: (-x['orb'], x['phash_dist']))

        # 三级保留策略：
        #   强匹配：ORB ≥ 0.10
        #   可信：pHash ≤ 8（缩略图过小时 ORB 会失真，但 pHash 依然可靠）
        #   弱匹配：ORB ≥ 0.04
        strong = [r for r in results if r['orb'] >= min_orb]
        trusted = [r for r in results
                   if r['orb'] < min_orb and r['phash_dist'] <= PHASH_TRUST]
        weak = [r for r in results
                if min_orb > r['orb'] >= ORB_WEAK and r['phash_dist'] > PHASH_TRUST]
        # 按 pHash 距离把 trusted 里弱的补进来
        trusted.sort(key=lambda x: x['phash_dist'])
        out = strong + trusted + weak[:5]
    else:
        results.sort(key=lambda x: x['phash_dist'])
        for r in results:
            r['orb'] = None
        out = results

    final = []
    for r in out[:limit]:
        row = index.rows[r['row_index']]
        is_weak = (r['orb'] is not None
                   and r['orb'] < min_orb
                   and r['phash_dist'] > PHASH_TRUST)
        final.append({
            'row': row,
            'phash_dist': r['phash_dist'],
            'orb': r['orb'],
            'rotation': r['rot'],
            'trimmed': r['trimmed'],
            'weak': is_weak,
            'via': 'visual',
        })
    return final


def expand_groups(index, results):
    """把高分结果所在组内的其他文件一并带出。

    实测发现：同目录同主名的 PSD 与导出图，视觉可能差很多
    （例：「球王对决 世界球迷季.psd」→「球王对决 谁是食神 美食PK赛.jpg」，
    同一套视觉框架但文案全换，pHash 距离 12、ORB 0.05）。
    这类靠视觉匹配会判为弱，但文件系统关系是铁证。

    因此：凡是视觉命中且带 group_id 的，把组内成员以「配对」身份补进结果。
    """
    if not results:
        return results
    have = {r['row']['id'] for r in results}
    gids = {r['row']['group_id'] for r in results
            if r['row']['group_id'] and not r['weak']}
    if not gids:
        return results

    # 已在结果里、且属于可信组的，改标为「配对」——它的视觉分低只是因为
    # 同一套框架换了文案，文件系统关系才是硬证据
    for r in results:
        if r['weak'] and r['row']['group_id'] in gids:
            r['weak'] = False
            r['via'] = 'paired'

    extra = []
    for r in index.rows:
        if r['group_id'] in gids and r['id'] not in have:
            extra.append({
                'row': r,
                'phash_dist': None,
                'orb': None,
                'rotation': 0,
                'trimmed': False,
                'weak': False,
                'via': 'paired',
            })
    # 源文件排在导出图前面
    extra.sort(key=lambda x: (x['row']['kind'] != 'source', x['row']['name']))
    return results + extra
