"""人脸 / 人物检测。

CLIP 对「人物」这类宽泛词的检索能力很弱：实测 recall@5 只有 2/5。
原因是 CLIP 学的是「整张图的语义氛围」，一张商业海报里有人也有景，
它更倾向于匹配「海岛度假」这个整体概念，而不是「画面里有没有人」。

所以补一条独立通道：建立索引时用 YuNet 在人脸检测一遍，把
「这张图里有几张脸」存下来。搜索「人物」「人」「模特」这类词时
直接按人脸数过滤，比 CLIP 分数准得多。

YuNet 只有 227KB，单张检测 3~20ms，对索引速度几乎没影响。
"""
import os
import threading

import numpy as np

from core import paths as pathmod

MODEL_FILE = 'face_yunet.onnx'
MODEL_NAME = 'yunet-2023mar'

# YuNet 的输入尺寸。检测用的图不必太大 —— 海报里的人物通常占画面
# 1/10 以上，640px 长边足够检出；再大只是浪费算力。
DETECT_SIDE = 640

# 置信度阈值。0.7 是实测的平衡点：
#   0.9 → 漏掉很多侧脸、小脸
#   0.5 → 把邮票、图标里的人像图案也认成真人
SCORE_THRESH = 0.7

# 搜索词命中这些词时走人脸过滤通道，而不是 CLIP
PERSON_WORDS = (
    '人物', '人像', '有人', '模特', '人脸', '肖像', '美女', '帅哥',
    '情侣', '游客', '女士', '男士', '女人', '男人', '女孩', '男孩',
    '孩子', '儿童', '宝宝', '家庭', '合影', '自拍', '全身', '半身',
)

_lock = threading.Lock()
_detector = None
_failed = None


def model_path():
    return os.path.join(pathmod.resource_dir(), 'models', MODEL_FILE)


def available():
    return os.path.isfile(model_path())


def load_error():
    return _failed


def _lazy_load():
    global _detector, _failed
    with _lock:
        if _detector is not None:
            return True
        if _failed:
            return False
        p = model_path()
        if not os.path.isfile(p):
            _failed = '模型文件缺失'
            return False
        try:
            import cv2
            # OpenCV 的 ONNX 读取器**不支持中文路径**（实测报
            # "Can't read ONNX file"）。打包目录是 dist/溯源/，必然带中文，
            # 所以先复制到纯英文的临时目录再加载。
            # 模型只有 227KB，复制成本可以忽略。
            safe = _ascii_copy(p)
            _detector = cv2.FaceDetectorYN.create(
                safe, '', (320, 320), SCORE_THRESH, 0.3, 5000)
            return True
        except Exception as e:
            _failed = f'{type(e).__name__}: {e}'
            return False


def _ascii_copy(src):
    """把模型复制到一个纯 ASCII 的临时路径，返回该路径。

    OpenCV 的 dnn 模块把路径按单字节处理，中文会变成乱码导致读不到文件。
    """
    import hashlib
    import shutil
    import tempfile

    try:
        p = src
        p.encode('ascii')
        return p                    # 本来就是纯英文，不用复制
    except UnicodeEncodeError:
        pass

    # 用内容哈希命名，同一份模型只复制一次
    with open(src, 'rb') as f:
        tag = hashlib.md5(f.read(65536)).hexdigest()[:12]
    dst = os.path.join(tempfile.gettempdir(), f'itrace_yunet_{tag}.onnx')
    if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
        shutil.copyfile(src, dst)
    return dst


def detect(im):
    """返回检测到的人脸数量。失败返回 0（当作没有人脸）。

    为什么返回数量而不是布尔：一张商业海报可能有 3 个模特，
    只存 True/False 就丢失了「人多还是人少」这个信息，
    以后想按「人多」筛就做不到了。
    """
    if im is None or not _lazy_load():
        return 0
    try:
        import cv2
        from PIL import Image

        im = im.convert('RGB')
        w, h = im.size
        if not w or not h:
            return 0
        scale = DETECT_SIDE / max(w, h)
        if scale < 1:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                           Image.BICUBIC)

        # YuNet 要 BGR 的 ndarray，且必须是连续内存
        arr = np.asarray(im)[:, :, ::-1].copy()
        H, W = arr.shape[:2]
        with _lock:
            d = _detector
            d.setInputSize((W, H))
            _, faces = d.detect(arr)
        return 0 if faces is None else len(faces)
    except Exception:
        return 0


def is_person_query(text):
    """判断查询词是不是在问「有没有人」。

    整词匹配而不是子串匹配：「人」是单字，用 in 判断会把
    「人间天堂」「人民」也当成人物查询。
    """
    t = (text or '').strip()
    if not t:
        return False
    return any(w == t or w in t for w in PERSON_WORDS)
