"""路径解析。

打包成 exe 后，代码在临时解压目录（sys._MEIPASS），可写数据必须放到
exe 旁边或用户目录。这是唯一区分两种运行形态的地方。
"""
import os
import sys


def is_frozen():
    return getattr(sys, 'frozen', False)


def app_dir():
    """程序所在目录。

    - 源码运行：本文件的上上级（项目根）
    - 打包运行：exe 所在目录
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir():
    """只读资源（web/ 等）所在目录。

    打包后 PyInstaller 把它们解到 _MEIPASS；源码运行就是项目根。
    """
    if is_frozen():
        return getattr(sys, '_MEIPASS', app_dir())
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    """可写数据目录（索引库 + 缩略图缓存）。

    优先放 exe 同级的 data/：便携，拷走就能带走索引。
    如果该位置不可写（比如放在 Program Files），退到用户目录。
    """
    preferred = os.path.join(app_dir(), 'data')
    try:
        os.makedirs(preferred, exist_ok=True)
        probe = os.path.join(preferred, '.write_test')
        with open(probe, 'w') as f:
            f.write('ok')
        os.remove(probe)
        return preferred
    except OSError:
        fallback = os.path.join(
            os.path.expanduser('~'), 'ImageTrace', 'data')
        os.makedirs(fallback, exist_ok=True)
        return fallback


def db_path():
    return os.path.join(data_dir(), 'index.db')


def thumb_dir():
    return os.path.join(data_dir(), 'thumbs')


def web_dir():
    return os.path.join(resource_dir(), 'web')
