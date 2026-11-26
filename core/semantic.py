"""以文字搜画面内容（中文 CLIP）。

原理：把图片和文字都编码成 512 维向量，算余弦相似度。语义接近的
（「沙滩」和一张海滩照片）向量方向接近，不依赖文件名或图里的文字。

模型是 chinese-clip-vit-base-patch16 的量化 ONNX 版（182MB）。
选中文版而不是原版 CLIP 是因为实测原版对中文查询几乎无效 ——
同一张沙滩图，'a beach' 能排到第一，但用「沙滩」查出来的是随机结果。
"""
import json
import os
import threading
import time

import numpy as np

from core import paths as pathmod

# 两个模型文件放在 models/ 下，随程序一起分发
MODEL_FILE = 'cn_clip.onnx'
TOKENIZER_FILE = 'cn_clip_tokenizer.json'
MODEL_NAME = 'cn-clip-vit-b16-q'      # 存进数据库，换模型时自动重算

MAX_LEN = 52                  # 中文 CLIP 的位置编码上限
EMB_DIM = 512
RENDER_SIDE = 384             # 编码前渲染的尺寸，实测甜点（见 encode_batch）
# CLIP 训练时的图像标准化参数，值与 preprocessor_config.json 一致
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

_lock = threading.Lock()
_state = {
    'session': None,
    'vocab': None,
    'cls': 101,
    'sep': 102,
    'failed': None,       # 加载失败原因，避免反复重试
}


def model_dir():
    """模型目录。打包后从 _MEIPASS/models 取，源码运行从项目 models 取。"""
    base = pathmod.resource_dir()
    return os.path.join(base, 'models')


def available():
    """模型文件是否齐全。缺文件时前端不该显示这个功能。"""
    d = model_dir()
    return (os.path.isfile(os.path.join(d, MODEL_FILE))
            and os.path.isfile(os.path.join(d, TOKENIZER_FILE)))


def _lazy_load(threads=4):
    """加载模型与词表，只做一次。

    threads 决定 ORT 的 intra_op 线程数。**这个参数影响很大**：实测
    batch=8 推理，1 线程 282ms/张、4 线程 116ms/张、8 线程 69ms/张。

    第一次在 1 线程下测单张图只有 30ms，就误以为线程数无所谓 —— 那是
    单张的启动开销掩盖了差异，批量时才暴露。所以默认给 4。
    """
    with _lock:
        if _state['session'] is not None:
            return True
        if _state['failed']:
            return False

        d = model_dir()
        onnx_path = os.path.join(d, MODEL_FILE)
        tok_path = os.path.join(d, TOKENIZER_FILE)
        if not (os.path.isfile(onnx_path) and os.path.isfile(tok_path)):
            _state['failed'] = '模型文件缺失'
            return False

        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.intra_op_num_threads = max(1, int(threads or 4))
            opts.inter_op_num_threads = 1
            opts.log_severity_level = 3
            sess = ort.InferenceSession(
                onnx_path, sess_options=opts,
                providers=['CPUExecutionProvider'])

            with open(tok_path, encoding='utf-8') as f:
                tok = json.load(f)
            vocab = tok['model']['vocab']
        except Exception as e:
            _state['failed'] = f'{type(e).__name__}: {e}'
            return False

        _state['session'] = sess
        _state['vocab'] = vocab
        _state['cls'] = vocab.get('[CLS]', 101)
        _state['sep'] = vocab.get('[SEP]', 102)
        return True


def load_error():
    return _state['failed']


def _tokenize_cjk(text, vocab, unk):
    """中文：按字切，优先匹配更长的词。"""
    ids = []
    i, n = 0, len(text)
    while i < n:
        hit = None
        for ln in (4, 3, 2, 1):
            piece = text[i:i + ln]
            if piece in vocab:
                hit = (vocab[piece], ln)
                break
        if hit:
            ids.append(hit[0])
            i += hit[1]
        else:
            ids.append(unk)
            i += 1
    return ids


def _wordpiece(word, vocab, unk):
    """英文：WordPiece 最长匹配 + ## 续接前缀。

    **不能当普通字符逐个查表。** 词表里 9614 个 token 带 `##` 前缀，
    表示「接在上一个词后面的片段」。不处理 ## 的话，`hello` 会被切成
    `he`/`ll`/`o` 三个独立词，和训练时的分词完全不一致，编码出来的
    向量没有意义（实测搜 `beach` 得到的是随机结果）。

    正确做法：从当前起点找最长的、不带 ## 的 token 作词首，
    后续片段必须带 ##。
    """
    ids = []
    start = 0
    n = len(word)
    while start < n:
        end = n
        found = None
        while end > start:
            piece = word[start:end]
            if start > 0:
                piece = '##' + piece
            if piece in vocab:
                found = (vocab[piece], end)
                break
            end -= 1
        if found:
            ids.append(found[0])
            start = found[1]
        else:
            ids.append(unk)
            start += 1
    return ids


def _tokenize(text):
    """中文 CLIP 用 BERT 式 WordPiece 分词。

    分词必须和模型训练时一致，否则向量没有意义：
      - 中文：按字做最长匹配（词表里有单字，也有少量多字词片）
      - 英文：WordPiece，后续片段要带 `##` 前缀
      - 标点、数字：单独查表
    """
    vocab = _state['vocab']
    unk = vocab.get('[UNK]', 100)
    ids = [_state['cls']]
    text = (text or '').strip().lower()

    # 先按「CJK 连续段」和「非 CJK 连续段」切开，各走各的分词
    buf = []
    mode = None       # 'cjk' | 'other'
    for ch in text:
        if ch.isspace():
            if buf:
                ids.extend(_tokenize_cjk(''.join(buf), vocab, unk)
                           if mode == 'cjk'
                           else _wordpiece(''.join(buf), vocab, unk))
                buf = []
            mode = None
            continue
        m = 'cjk' if '\u4e00' <= ch <= '\u9fff' else 'other'
        if m != mode and buf:
            ids.extend(_tokenize_cjk(''.join(buf), vocab, unk)
                       if mode == 'cjk'
                       else _wordpiece(''.join(buf), vocab, unk))
            buf = []
        mode = m
        buf.append(ch)
    if buf:
        ids.extend(_tokenize_cjk(''.join(buf), vocab, unk)
                   if mode == 'cjk'
                   else _wordpiece(''.join(buf), vocab, unk))

    ids.append(_state['sep'])
    return ids


def encode_text(text):
    """文字 -> 归一化向量。失败返回 None。"""
    if not _lazy_load():
        return None
    try:
        ids = _tokenize(text)[:MAX_LEN]
        mask = [1] * len(ids) + [0] * (MAX_LEN - len(ids))
        ids = ids + [0] * (MAX_LEN - len(ids))
        sess = _state['session']
        out = sess.run(None, {
            'input_ids': np.array([ids], dtype=np.int64),
            'attention_mask': np.array([mask], dtype=np.int64),
            'pixel_values': np.zeros((1, 3, 224, 224), dtype=np.float32),
        })
        v = out[2][0]                    # text_embeds
        n = np.linalg.norm(v)
        return v / n if n else None
    except Exception:
        return None


def encode_image(im):
    """PIL 图 -> 归一化向量。失败返回 None。"""
    if im is None or not _lazy_load():
        return None
    try:
        from PIL import Image
        im = im.convert('RGB').resize((224, 224), Image.BICUBIC)
        a = np.asarray(im, dtype=np.float32) / 255.0
        a = (a - MEAN) / STD
        x = a.transpose(2, 0, 1)[None]
        sess = _state['session']
        out = sess.run(None, {
            'pixel_values': x,
            'input_ids': np.zeros((1, MAX_LEN), dtype=np.int64),
            'attention_mask': np.zeros((1, MAX_LEN), dtype=np.int64),
        })
        v = out[3][0]                    # image_embeds
        n = np.linalg.norm(v)
        return v / n if n else None
    except Exception:
        return None


def encode_image_path(path, ext=None, side=384):
    """从文件渲染一张缩略图再编码。

    side=384 是实测的性价比点：CLIP 只吃 224×224，渲染更大没有额外收益。
    PSD 渲染 384px 约 171ms，256px 反而更慢（363ms，渲染器有最小尺寸开销），
    768px 是 189ms —— 三者 recall@5 都是 84%。
    """
    try:
        from server import _render_image
        if ext is None:
            ext = os.path.splitext(path)[1].lower()
        elif not ext.startswith('.'):
            ext = '.' + ext
        im = _render_image(path, ext, side)
        return encode_image(im)
    except Exception:
        return None


def blob(vec):
    """向量 -> 数据库 BLOB。float32 省一半空间，精度足够检索。"""
    if vec is None:
        return None
    return np.asarray(vec, dtype=np.float32).tobytes()


def unblob(data):
    if not data:
        return None
    return np.frombuffer(data, dtype=np.float32)


def encode_batch(items, threads=4, batch=8, on_image=None):
    """批量编码。items = [(path, ext), ...]，返回与输入等长的向量列表，
    失败的槽位是 None。

    批量推理比逐张快得多：实测单张 296ms，8 张一批均摊 249ms（1 线程）；
    4 线程下则从 128ms 降到 102ms。渲染是主要开销，推理能省则省。

    threads 控制 ORT 的并行度，也决定同时开几个渲染线程 —— 渲染是纯
    Python + PIL，GIL 会串行化，所以用线程池只是为了让渲染和推理重叠。

    on_image(idx, im) 是可选的旁路回调：渲染成功后拿到原图，供调用方
    顺手做别的事（比如人脸检测）。**在渲染线程里执行，必须自己保证
    线程安全**。之所以放在这里而不是让调用方重新渲染一遍，是因为
    渲染占了单张耗时的 60%，重复渲染等于白扔一半时间。
    """
    if not items:
        return []
    if not _lazy_load(threads):
        return [None] * len(items)

    out = [None] * len(items)
    from concurrent.futures import ThreadPoolExecutor

    # 索引里的缩略图路径，作为渲染失败时的兜底
    thumbs = _thumb_map([p for p, _e in items])

    # 1) 并行渲染（I/O + PIL，能被 GIL 部分释放）
    def render(idx_item):
        idx, (path, ext) = idx_item
        im = None
        try:
            from server import _render_image
            e = ext if ext.startswith('.') else '.' + ext
            im = _render_image(path, e, RENDER_SIDE)
        except Exception:
            im = None

        # 渲染不出来就退回索引时的缩略图。
        # 这类文件确实存在（旧版 AI 存的是 PostScript、Office 走 COM 失败、
        # 文件被占用），但缩略图是当初扫描时成功生成的 —— 用它总比
        # 整张图没有向量、以后搜不到要好。
        if im is None:
            tp = thumbs.get(path)
            if tp:
                try:
                    from PIL import Image
                    im = Image.open(tp)
                    im.load()
                except Exception:
                    im = None

        if im is None:
            return idx, None

        if on_image is not None:
            try:
                on_image(idx, im)
            except Exception:
                pass

        try:
            return idx, prepare_array(im)
        except Exception:
            return idx, None

    arrays = {}
    with ThreadPoolExecutor(max_workers=min(len(items), threads or 4)) as ex:
        for idx, arr in ex.map(render, list(enumerate(items))):
            if arr is not None:
                arrays[idx] = arr

    if not arrays:
        return out

    # 2) 一次推理多张（batch）
    try:
        sess = _state['session']
        order = sorted(arrays)
        x = np.stack([arrays[i] for i in order])
        zeros = np.zeros((1, MAX_LEN), dtype=np.int64)
        res = sess.run(None, {
            'pixel_values': x,
            'input_ids': zeros,
            'attention_mask': zeros,
        })
        embs = res[3]
        for slot, i in enumerate(order):
            v = embs[slot]
            n = np.linalg.norm(v)
            if n:
                out[i] = v / n
    except Exception:
        # 批量失败：退回逐张（可能是显存/内存不足）
        for i in sorted(arrays):
            try:
                out[i] = _encode_array_single(arrays[i])
            except Exception:
                out[i] = None
    return out


def _thumb_map(paths):
    """查这批文件在索引里的缩略图绝对路径。查不到的不出现在返回里。"""
    if not paths:
        return {}
    try:
        import sqlite3

        from core import paths as pmod
        db_path = pmod.db_path()
        base = os.path.join(os.path.dirname(os.path.abspath(db_path)),
                            'thumbs')
        conn = sqlite3.connect(db_path, timeout=10)
        out = {}
        chunk = 300
        for i in range(0, len(paths), chunk):
            part = paths[i:i + chunk]
            qm = ','.join('?' * len(part))
            for row in conn.execute(
                    f"SELECT path, thumb_path FROM files "
                    f"WHERE path IN ({qm})", part):
                if row[1]:
                    out[row[0]] = os.path.join(base, row[1])
        conn.close()
        return out
    except Exception:
        return {}


def prepare_array(im):
    """PIL 图 -> CLIP 输入张量（3,224,224）float32。"""
    from PIL import Image
    im = im.convert('RGB').resize((224, 224), Image.BICUBIC)
    a = np.asarray(im, dtype=np.float32) / 255.0
    a = (a - MEAN) / STD
    return a.transpose(2, 0, 1)


def _encode_array_single(arr):
    sess = _state['session']
    zeros = np.zeros((1, MAX_LEN), dtype=np.int64)
    res = sess.run(None, {
        'pixel_values': arr[None],
        'input_ids': zeros,
        'attention_mask': zeros,
    })
    v = res[3][0]
    n = np.linalg.norm(v)
    return v / n if n else None


def search(query_vec, matrix, ids, limit=60, min_score=None, sigmas=2.2):
    """在向量矩阵里找最相似的。

    matrix 是 (N, 512) 的 float32 数组，ids 是对应的文件 id。

    **阈值必须相对计算，不能写绝对值。** 实测中文 CLIP 的余弦相似度
    集中在很窄的区间（全库 6129 张：均值 0.31~0.35，标准差只有 0.02），
    写死 0.20 这种绝对值等于不过滤。

    改用「均值 + N 个标准差」，N 的取值是全库跑完后调的：

        σ      沙滩    寺庙    无意义词
        1.3     615     661     664      ← 三条都是 600+，等于没筛
        1.8     239     294     283
        2.2      95     115     108      ← 当前
        2.3      83     104     107

    全量索引后标准差从 0.026 降到 0.021（样本多了分布更集中），
    原来 1.3σ 就够，现在要 2.2σ 才能把结果压到百条以内。

    **注意这个方案有固有限制**：无意义查询（如「完全无关的词汇」）
    也会返回百余条，因为 CLIP 对任何字符串都能算出分数，没有「匹配
    不上」这个概念。要么接受，要么在前端把低分结果弱化显示。
    """
    if query_vec is None or matrix is None or not len(matrix):
        return []
    scores = matrix @ query_vec
    order = np.argsort(-scores)

    mean = float(scores.mean())
    std = float(scores.std())
    cutoff = mean + sigmas * std
    if min_score is not None:
        cutoff = max(cutoff, min_score)

    out = []
    for i in order:
        s = float(scores[i])
        if s < cutoff:
            break
        out.append((int(ids[i]), s))
        if len(out) >= limit:
            break
    return out
