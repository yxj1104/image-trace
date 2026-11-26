"""原生窗口外壳。

用 pywebview 把同一套网页包成本地窗口 —— 有标题栏、能最小化、
能拖拽缩放、关掉窗口进程就退出，跟「看图程序」一样的观感。

底层是 Windows 自带的 WebView2（Edge Chromium 内核），
所以网页渲染效果和浏览器完全一致，不存在「原生控件」问题。

如果机器上没有 WebView2，自动退回浏览器模式，不让用户卡死。
"""
import json
import os
import socket
import sys
import threading
import time

from core import paths as pathmod
from core import db as dbmod

CONFIG_NAME = 'window.json'
DEFAULT_SIZE = (1360, 880)
MIN_SIZE = (960, 640)


def _config_path():
    return os.path.join(pathmod.data_dir(), CONFIG_NAME)


def load_geometry():
    """记住上次窗口位置和大小。读不到或数据异常就用默认值。"""
    fallback = {'width': DEFAULT_SIZE[0], 'height': DEFAULT_SIZE[1],
                'x': None, 'y': None}
    try:
        with open(_config_path(), 'r', encoding='utf-8') as f:
            d = json.load(f)
        w = int(d.get('w') or DEFAULT_SIZE[0])
        h = int(d.get('h') or DEFAULT_SIZE[1])
        x = d.get('x')
        y = d.get('y')

        # 尺寸合理性：太小是「最小化时存下来的」，太大是异常值
        if w < MIN_SIZE[0] or h < MIN_SIZE[1]:
            return fallback
        if w > 8000 or h > 8000:
            return fallback

        # 坐标合理性：(-32000,-32000) 是最小化哨兵值
        if isinstance(x, (int, float)) and x <= -10000:
            x = None
        if isinstance(y, (int, float)) and y <= -10000:
            y = None

        return {'width': w, 'height': h,
                'x': int(x) if isinstance(x, (int, float)) else None,
                'y': int(y) if isinstance(y, (int, float)) else None}
    except Exception:
        return fallback


def save_geometry(win):
    """保存窗口位置和尺寸。

    窗口最小化时 Windows 会把它挪到 (-32000, -32000) 并报一个很小的尺寸
    （实测 160×28）。直接存会把配置写坏，下次启动就变成一个角落里的细条。
    这里做合理性校验，明显不正常的直接丢弃。
    """
    try:
        w, h = win.width, win.height
        x, y = win.x, win.y
    except Exception:
        return

    if not w or not h:
        return
    if w < MIN_SIZE[0] or h < MIN_SIZE[1]:
        return                      # 最小化状态，跳过
    if isinstance(x, (int, float)) and x <= -10000:
        return                      # 最小化的坐标哨兵值
    if isinstance(y, (int, float)) and y <= -10000:
        return

    try:
        d = {'w': int(w), 'h': int(h)}
        if isinstance(x, (int, float)) and x > -10000:
            d['x'] = int(x)
        if isinstance(y, (int, float)) and y > -10000:
            d['y'] = int(y)
        with open(_config_path(), 'w', encoding='utf-8') as f:
            json.dump(d, f)
    except Exception:
        pass


def webview2_available():
    """检查 WebView2 运行时是否存在。

    pywebview 的 edgechromium 后端依赖它。Windows 11 自带；
    Windows 10 可能没装（需用户装 Evergreen Runtime）。
    """
    if os.name != 'nt':
        return False
    cands = [
        os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\EdgeWebView'
                           r'\Application'),
        os.path.expandvars(r'%ProgramFiles%\Microsoft\EdgeWebView'
                           r'\Application'),
        os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\EdgeWebView'
                           r'\Application'),
    ]
    for c in cands:
        if c and os.path.isdir(c):
            try:
                if any(n.startswith('1') for n in os.listdir(c)):
                    return True
            except OSError:
                pass
    return False


def _win32():
    """取 user32 并声明关键函数的 argtypes。

    **这一步不能省。** ctypes 默认把整数参数当 32 位传，而 HWND 是 64 位
    指针。不声明 argtypes 时，`SetWindowPos(hwnd, HWND_TOPMOST=-1, ...)`
    里的 -1 会被符号扩展成 0x00000000FFFFFFFF，是个无效句柄，
    SetWindowPos 返回成功但什么也没做 —— 静默失败，最难查。
    """
    import ctypes
    u32 = ctypes.windll.user32
    if getattr(u32, '_itrace_typed', False):
        return u32

    u32.GetForegroundWindow.restype = ctypes.c_void_p
    u32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    u32.SetForegroundWindow.restype = ctypes.c_int
    u32.BringWindowToTop.argtypes = [ctypes.c_void_p]
    u32.BringWindowToTop.restype = ctypes.c_int
    u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u32.SetWindowPos.restype = ctypes.c_int
    u32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u32.ShowWindow.restype = ctypes.c_int
    u32.IsIconic.argtypes = [ctypes.c_void_p]
    u32.IsIconic.restype = ctypes.c_int
    u32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u32.GetWindowLongW.restype = ctypes.c_long
    u32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u32.GetWindowRect.restype = ctypes.c_int
    u32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    u32.GetWindowTextLengthW.restype = ctypes.c_int
    u32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                   ctypes.c_int]
    u32.GetWindowTextW.restype = ctypes.c_int
    u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    u32.AttachThreadInput.argtypes = [ctypes.c_ulong, ctypes.c_ulong,
                                      ctypes.c_int]
    u32.AttachThreadInput.restype = ctypes.c_int
    u32.SetFocus.argtypes = [ctypes.c_void_p]
    u32.SetFocus.restype = ctypes.c_void_p
    u32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte,
                                ctypes.c_uint, ctypes.c_size_t]
    u32._itrace_typed = True
    return u32


def find_window(title):
    """按标题找可见顶层窗口，返回 HWND；找不到返回 0。

    用来实现「右键菜单把已有窗口叫到前面」。不用 pywebview 的 API，
    因为右键触发的是**另一个进程**，拿不到原进程的 Window 对象。
    """
    if os.name != 'nt':
        return 0
    try:
        import ctypes
        import ctypes.wintypes as wt
    except Exception:
        return 0

    u32 = ctypes.windll.user32
    # EnumWindows 回调签名：返回 True 继续枚举
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    hits = []

    def _cb(hwnd, _lp):
        if not u32.IsWindowVisible(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            # 标题完全相等才算 —— 浏览器标签页的标题里也带「溯源」，
            # 用 in 判断会把浏览器误置前，那就白改了
            if buf.value == title:
                hits.append(hwnd)
        return True

    try:
        u32.EnumWindows(EnumProc(_cb), 0)
    except Exception:
        return 0
    return hits[0] if hits else 0


def _focus_flag_path():
    return os.path.join(pathmod.data_dir(), '.focus_request')


def request_focus():
    """请「正在运行的那个进程」把窗口叫到前面。

    右键触发的进程是短命的：它自己调 SetForegroundWindow 能恢复窗口，
    但进程一退出，Windows 就把前台还给原来的窗口。所以这里只留个标记，
    由长驻进程自己去抢前台。

    **但长驻进程自己去抢也会被拒** —— Windows 的前台锁定规则只允许
    「刚收到用户输入」的进程抢前台，而长驻进程什么都没收到。
    所以退出前调一次 `AllowSetForegroundWindow(ASFW_ANY)`：本进程是
    用户右键点出来的，手里正好有前台授权，可以把这份授权转交出去。
    """
    try:
        import ctypes
        ASFW_ANY = -1
        ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
    except Exception:
        pass

    try:
        with open(_focus_flag_path(), 'w', encoding='utf-8') as f:
            f.write(str(time.time()))
        return True
    except OSError:
        return False


def _is_foreground(title):
    """当前前台窗口是不是这个标题的窗口。"""
    try:
        import ctypes
        u32 = ctypes.windll.user32
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return False
        n = u32.GetWindowTextLengthW(hwnd)
        if not n:
            return False
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value == title
    except Exception:
        return False


def watch_focus(title='溯源', interval=0.3):
    """后台线程：盯着标记文件，一有变化就把窗口叫到前面。

    **每次右键只抢一次。** 不做「抢不到就再抢」的循环 ——
    用户右键时往往正在别的程序里干活（PS、浏览器），反复夺权会让
    他的窗口不停被打断。抢一次够了，真拿不到就靠置顶让窗口可见。
    """
    path = _focus_flag_path()
    last = 0.0
    try:
        last = os.path.getmtime(path)
    except OSError:
        pass

    while True:
        time.sleep(interval)
        try:
            m = os.path.getmtime(path)
        except OSError:
            continue
        if m <= last:
            continue
        last = m
        time.sleep(0.05)        # 等时间戳落盘
        bring_to_front(title)   # 只此一次


def _try_topmost_briefly(hwnd, seconds=2.0):
    """短暂置顶，保证用户一定看见窗口跳出来。

    为什么需要这一步：Photoshop、聊天工具这类程序会主动抢焦点，
    实测抢到前台后 1–2 秒就被夺回去。置顶能盖在上面，但长期置顶会
    挡住所有窗口，所以只保持几秒就取消。
    """
    try:
        u32 = _win32()
    except Exception:
        return False
    SWP_NOMOVE, SWP_NOSIZE = 0x0002, 0x0001
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    try:
        u32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE)

        def _undo():
            try:
                u32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE)
            except Exception:
                pass

        threading.Timer(seconds, _undo).start()
        return True
    except Exception:
        return False


def bring_to_front(title='溯源'):
    """从**应用自己的进程**里把窗口叫到前面。

    四处踩过的坑，都记在这里，改代码前先看：

    1. 必须由长驻进程做。右键触发的 exe 是短命进程，它调完
       SetForegroundWindow 就退出，Windows 随即把前台还给原来的窗口 ——
       实测窗口确实被恢复（IsIconic 变 False），但前台仍是浏览器。

    2. **argtypes 一定要声明**。ctypes 默认按 32 位传整数，HWND 却是 64 位
       指针。`SetWindowPos(hwnd, -1, ...)` 里 -1 被符号扩展成
       0xFFFFFFFF 这个无效句柄，函数返回成功但什么也没做 —— 静默失败。

    3. SetForegroundWindow 会被前台锁定规则挡住。所以依次尝试：基础请求
       → 模拟 Alt 按压（获得「刚有用户输入」的资格）→ AttachThreadInput。

    4. **不跟抢焦点的程序对抗。** Photoshop、聊天工具会在一秒内把焦点夺回去，
       这时如果反复抢，用户正在干活的窗口就不停被打断，属于骚扰。
       所以：抢到一次就收手；真拿不到前台才短暂置顶（2 秒）保证可见。
    """
    hwnd = find_window(title)
    if not hwnd:
        return False

    try:
        u32 = _win32()
        import ctypes as _ct
        k32 = _ct.windll.kernel32
    except Exception:
        return False

    try:
        if u32.IsIconic(hwnd):
            u32.ShowWindow(hwnd, 9)        # SW_RESTORE
            time.sleep(0.1)

        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)

        if not _is_foreground(title):
            # 手段二：模拟一次 Alt 按压。Windows 允许「刚收到过用户输入」
            # 的进程抢前台，按下抬起 Alt 正好满足，且不输入任何字符。
            u32.keybd_event(0x12, 0, 0, 0)
            u32.keybd_event(0x12, 0, 2, 0)
            u32.SetForegroundWindow(hwnd)

        if not _is_foreground(title):
            # 手段三：临时挂到当前前台线程的输入队列取焦点授权
            fg = u32.GetForegroundWindow()
            fg_thread = u32.GetWindowThreadProcessId(fg, None) if fg else 0
            my_thread = k32.GetCurrentThreadId()
            if fg_thread and fg_thread != my_thread:
                if u32.AttachThreadInput(my_thread, fg_thread, True):
                    try:
                        u32.BringWindowToTop(hwnd)
                        u32.SetForegroundWindow(hwnd)
                    finally:
                        u32.AttachThreadInput(my_thread, fg_thread, False)

        if _is_foreground(title):
            # 拿到前台就够了，不再置顶、不再复查抢回 ——
            # 用户此刻正在别的程序里干活，多动作就是打扰。
            return True

        # 前台确实拿不到（例如别的程序刚抢过焦点），用置顶兜底保证看得见，
        # 2 秒后自动放下来，不长期压在别人窗口上面。
        return _try_topmost_briefly(hwnd)
    except Exception:
        return False


def wait_port(port, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        s.settimeout(0.15)
        try:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                return True
        finally:
            s.close()
        time.sleep(0.12)
    return False


def start_server_thread(port, on_ready=None):
    """后台起服务。返回线程对象。"""
    from server import run

    def target():
        run(db_path=pathmod.db_path(), port=port, open_browser=False)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    if on_ready and wait_port(port):
        on_ready()
    return t


def run_window(url, title='溯源', on_close=None):
    """打开原生窗口。返回 True 表示正常退出，False 表示无法启动。

    调用方负责在此之前把服务起好。
    """
    try:
        import webview
    except ImportError:
        print('pywebview 未安装')
        return False

    geo = load_geometry()
    # 窗口图标：打包后从 _MEIPASS/assets 取，源码运行从项目 assets 取
    icon_path = os.path.join(pathmod.resource_dir(), 'assets', 'icon.ico')
    if not os.path.isfile(icon_path):
        icon_path = None

    kwargs = {
        'width': geo['width'],
        'height': geo['height'],
        'min_size': MIN_SIZE,
        'resizable': True,
        'confirm_close': False,
        'text_select': True,        # 允许选中复制路径
        'background_color': '#FFFFFF',
    }
    if geo['x'] is not None and geo['y'] is not None:
        kwargs['x'] = geo['x']
        kwargs['y'] = geo['y']

    win = webview.create_window(title, url, **kwargs)

    # pywebview 的事件回调签名各不相同（loaded 不传参，closed 传窗口），
    # 写死参数会在触发时报 missing argument。
    def on_closed():
        save_geometry(win)
        if on_close:
            try:
                on_close()
            except Exception:
                pass

    try:
        win.events.closed += on_closed
    except Exception:
        pass

    # 兜底：每 5 秒存一次几何。
    # 用户从任务管理器强杀进程时 closed 事件不会触发，
    # 靠这个定时器至少能保住最后一次的正常尺寸。
    def _keep_geometry():
        while True:
            time.sleep(8)
            try:
                save_geometry(win)      # 内部已过滤最小化状态
            except Exception:
                return

    threading.Thread(target=_keep_geometry, daemon=True).start()

    # 右键菜单触发的短命进程会把「请求置前」写成标记文件，
    # 由本进程（长驻）盯着它，替它抢前台
    threading.Thread(target=watch_focus, args=(title,), daemon=True).start()

    gui = 'edgechromium' if webview2_available() else None
    start_kw = {'gui': gui}
    if icon_path:
        start_kw['icon'] = icon_path
    try:
        webview.start(**start_kw)
    except TypeError:
        # 旧版 pywebview 的 start() 没有 icon 参数
        webview.start(gui=gui)
    except Exception as e:
        print(f'原生窗口启动失败: {e}')
        return False
    save_geometry(win)
    return True
