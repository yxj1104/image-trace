"""查询词解析。

搜索框里用户可能输入任何东西：短词（沙滩）、短语（海边的女孩）、
长句（一个人在海边看夕阳）、英文、甚至 emoji。

CLIP 对短名词短语效果最好，长句会把多个概念平均掉变成「糊掉」的向量
（实测「一个人在海边」的结果不如「海边」准确）。所以这里做两件事：

1. **拆词**：把长句切成分开的候选词，分别编码、分别检索、合并结果
2. **判断有没有人物词**：命中人物词才启用「必须检出人脸」这个硬过滤
   —— 但不能因此丢掉「海边」这类场景词，否则「海边的女孩」就退化成
   只剩人脸过滤，把一堆不相干的合影也搜出来
"""
import re

from core import faces as faces_mod

# 中文里没意义的虚词/量词，拆词时丢掉
STOP = {
    '的', '地', '得', '了', '着', '和', '与', '及', '或', '在', '有', '是',
    '一个', '一张', '一幅', '一些', '这个', '那个', '这些', '那些',
    '里', '中', '上', '下', '边', '面', '个', '张', '幅', '只', '位', '名',
    '很', '比较', '非常', '特别', '一点', '有点', '那种', '这种',
    '图片', '照片', '图', '画面', '场景', '设计', '素材',
    '我', '要', '找', '搜', '想', '看', '帮', '请',
}

# 常见双字以上的场景词。拆句时要保住这些完整单元，
# 否则「椰子树」会被拆成「椰子」「树」而丢掉语义。
SCENE_WORDS = (
    '椰子树', '棕榈树', '向日葵', '摩天轮', '水族馆', '海洋馆', '玻璃桥',
    '会展中心', '美术馆', '天际线', '落日', '夕阳', '日出', '海滩', '海边',
    '沙滩', '泳池', '酒店', '客房', '大堂', '餐厅', '美食', '海鲜', '火锅',
    '烧烤', '自助餐', '甜品', '蛋糕', '咖啡', '雪山', '草原', '沙漠',
    '森林', '瀑布', '湖泊', '峡谷', '灯塔', '教堂', '建筑', '雕塑',
    '宫殿', '城堡', '古镇', '夜市', '街道', '码头', '机场', '飞机',
    '游轮', '游船', '火车', '汽车', '摩托车', '自行车', '地图', '票据',
    '方案', '海报', '封面', '内页', '展架', '易拉宝', '背景', '图标',
    'logo', '文字', '标题', '边框', '花纹', '春节', '新年', '中秋',
    '国庆', '母亲节', '情人节', '圣诞节', '婚礼', '生日', '毕业',
    '健身', '瑜伽', '跑步', '游泳', '潜水', '海豚', '大象', '熊猫',
    '企鹅', '老虎', '狮子', '猴子', '孔雀', '火烈鸟', '烟花', '灯光',
    '夜景', '日出', '云海', '梯田', '花海', '樱花', '荷花', '薰衣草',
)

# 人物相关词：命中才启用「必须有人脸」的硬过滤
PERSON_WORDS = faces_mod.PERSON_WORDS

_CJK = re.compile(r'[\u4e00-\u9fff]+')
_ASCII = re.compile(r'[A-Za-z][A-Za-z0-9]+')


def parse(text):
    """把查询串解析成检索计划。

    返回 dict:
        terms    : 要分别检索的候选词（已去重、已去停用词）
        person   : 是否含人物词
        scene    : 是否含场景词（去掉人物词后还剩内容）
        raw      : 原始输入
        strict   : 是否只按人过滤（既含人物词、又没有其他场景词）
    """
    raw = (text or '').strip()
    if not raw:
        return {'terms': [], 'person': False, 'scene': False,
                'raw': '', 'strict': False}

    person = faces_mod.is_person_query(raw)

    # 1) 先把已知场景词抠出来，避免被后续拆字破坏
    found = []
    rest = raw
    for w in sorted(SCENE_WORDS, key=len, reverse=True):
        if w and w in rest:
            found.append(w)
            rest = rest.replace(w, ' ')

    # 2) 中文连续段：按停用词切，剩下的每段作为一个候选
    for seg in _CJK.findall(rest):
        # 逐段尝试用停用词切开
        parts = [seg]
        for s in STOP:
            nxt = []
            for p in parts:
                nxt.extend([x for x in p.split(s) if x] if s in p else [p])
            parts = nxt
        for p in parts:
            if len(p) >= 2 and p not in STOP:
                found.append(p)

    # 3) 英文/数字串
    found.extend(_ASCII.findall(rest))

    # 去重保序
    seen, terms = set(), []
    for t in found:
        t = t.strip().lower()
        if not t or t in seen or t in STOP:
            continue
        # 单字除非本身是有效词，否则丢掉（「人」「龙」这类单字语义太弱）
        if len(t) == 1 and not _ASCII.match(t):
            continue
        seen.add(t)
        terms.append(t)

    # 原始整串也作为一个候选（短查询时它和拆出的词等价，
    # 长查询时它提供一次「整体语义」的兜底）
    whole = raw.lower()
    if whole not in seen and len(raw) <= 24:
        terms.insert(0, whole)

    if not terms:
        terms = [raw.lower()]

    scene_terms = [t for t in terms if not _is_person_term(t)]
    return {
        'terms': terms[:6],           # 最多 6 个候选，再多等于稀释
        'person': person,
        'scene': bool(scene_terms),
        'raw': raw,
        'strict': person and not scene_terms,
    }


def _is_person_term(t):
    return any(w in t for w in PERSON_WORDS)
