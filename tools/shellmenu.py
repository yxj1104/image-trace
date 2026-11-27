"""Windows 资源管理器右键菜单注册。

注册位置全部在 **HKEY_CURRENT_USER** 下 —— 不需要管理员权限，
也不需要管理员确认框，双击就能装上。

支持三种菜单：

1. 文件右键（*.psd 等）→「用溯源查找源文件」
   传入文件路径，直接用这张图做以图搜图。

2. 文件夹右键 →「在溯源中搜索此文件夹」
   把整个目录加入索引范围并打开界面。

3. 磁盘右键（C:/D:/E: 等）→「用溯源索引此磁盘」
   同上，针对盘符根。

设计取舍：
- 全部用 HKCU，不需要管理员权限
- 菜单项挂在 `*\\shell` 会污染所有文件类型，改成按扩展名逐个注册，
  只出现在我们支持的格式上
- 图标用 exe 自己，找不到就不设图标
- 卸载时删干净，包括不会自己消失的空键
"""
import os
import sys
import winreg

APP_KEY = 'ImageTrace'          # 注册表里的键名，卸载时要靠它找回
MENU_FILE = '用溯源找源文件'
MENU_DIR = '在溯源中搜索'
MENU_DRIVE = '用溯源索引此磁盘'

# 注册了右键菜单的扩展名（都是本工具能索引的）
FILE_EXTS = ('psd', 'ai', 'cdr', 'svg', 'eps', 'png', 'jpg', 'jpeg',
             'webp', 'tif', 'tiff', 'bmp', 'gif', 'pdf',
             'doc', 'docx', 'docm', 'ppt', 'pptx', 'pptm',
             'xls', 'xlsx', 'xlsm')

# 这些菜单不挂（系统盘根目录挂上去会很吵）
SKIP_DRIVE_MENU = False


def _exe_path():
    """右键菜单里要写的命令。打包后是 exe 本身，源码运行是 python + app.py。

    **必须给 exe 路径加引号**：安装目录可能带空格（"Program Files"），
    不加引号资源管理器会把路径拆成两段，命令直接失效。
    """
    if getattr(sys, 'frozen', False):
        return f'"{os.path.abspath(sys.executable)}"'
    py = sys.executable
    script = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', 'app.py'))
    return f'"{py}" "{os.path.normpath(script)}"'


def _command(kind):
    """构造右键命令。

    %1 = 被点击的文件/目录路径（由 explorer 替换并自动加引号）。
    --from-shell 让 app 知道这是右键触发的，行为可以不同
    （比如直接对这张图搜，而不是只是打开界面）。
    """
    exe = _exe_path()
    if kind == 'file':
        return f'{exe} --from-shell --search "%1"'
    if kind == 'dir':
        return f'{exe} --from-shell --add-root "%1"'
    if kind == 'drive':
        return f'{exe} --from-shell --add-root "%1"'
    return f'{exe} --from-shell'


def _icon():
    if getattr(sys, 'frozen', False):
        return f'{os.path.abspath(sys.executable)},0'
    return ''


def _status_files():
    """扫描实际注册的扩展名。

    只检查显式注册的那批；读不到就返回空列表，
    界面据此显示「已注册 N 种格式」。
    """
    if not is_windows():
        return []
    hits = []
    for ext in FILE_EXTS:
        path = (rf'Software\Classes\SystemFileAssociations\.{ext}'
                rf'\shell\{APP_KEY}')
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path):
                hits.append(ext)
        except OSError:
            pass
    return hits


def is_windows():
    return os.name == 'nt'


def is_admin():
    if not is_windows():
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ============================================================
# 安装
# ============================================================

def install(include_file=True, include_dir=True, include_drive=True):
    """写入注册表。返回 (成功项列表, 失败项列表)。"""
    if not is_windows():
        return [], ['仅支持 Windows']

    ok, fail = [], []
    icon = _icon()

    def guard(name, fn):
        try:
            fn()
            ok.append(name)
        except OSError as e:
            fail.append(f'{name}: {e}')

    if include_file:
        for ext in FILE_EXTS:
            guard(f'.{ext}', lambda e=ext: _install_file_ext(e, icon))

    if include_dir:
        guard('文件夹', lambda: _install_dir(icon))

    if include_drive:
        guard('磁盘', lambda: _install_drive(icon))

    return ok, fail


def _set_icon(k, icon):
    """只在有真实图标时才写 Icon 值。

    写空字符串会让资源管理器画一个空白方块，比没有图标更难看。
    """
    if icon:
        winreg.SetValueEx(k, 'Icon', 0, winreg.REG_SZ, icon)


def _install_file_ext(ext, icon):
    base = rf'Software\Classes\SystemFileAssociations\.{ext}\shell\{APP_KEY}'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
        winreg.SetValueEx(k, 'MUIVerb', 0, winreg.REG_SZ, MENU_FILE)
        _set_icon(k, icon)
        # 只对单个文件显示（多选时不显示，避免语义不清）
        winreg.SetValueEx(k, 'MultiSelectModel', 0, winreg.REG_SZ, 'Single')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r'\command') as k:
        winreg.SetValueEx(k, '', 0, winreg.REG_SZ, _command('file'))


def _install_dir(icon):
    base = rf'Software\Classes\Directory\shell\{APP_KEY}'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
        winreg.SetValueEx(k, 'MUIVerb', 0, winreg.REG_SZ, MENU_DIR)
        _set_icon(k, icon)
        winreg.SetValueEx(k, 'MultiSelectModel', 0, winreg.REG_SZ, 'Single')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r'\command') as k:
        winreg.SetValueEx(k, '', 0, winreg.REG_SZ, _command('dir'))

    # 文件夹背景右键（在文件夹空白处右键）
    base2 = rf'Software\Classes\Directory\Background\shell\{APP_KEY}'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base2) as k:
        winreg.SetValueEx(k, 'MUIVerb', 0, winreg.REG_SZ, MENU_DIR)
        _set_icon(k, icon)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base2 + r'\command') as k:
        # 背景右键没有 %1，用 %V 表示当前目录
        exe = _exe_path()
        winreg.SetValueEx(k, '', 0, winreg.REG_SZ,
                          f'{exe} --from-shell --add-root "%V"')


def _install_drive(icon):
    base = rf'Software\Classes\Drive\shell\{APP_KEY}'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
        winreg.SetValueEx(k, 'MUIVerb', 0, winreg.REG_SZ, MENU_DRIVE)
        _set_icon(k, icon)
        winreg.SetValueEx(k, 'MultiSelectModel', 0, winreg.REG_SZ, 'Single')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r'\command') as k:
        winreg.SetValueEx(k, '', 0, winreg.REG_SZ, _command('drive'))


# ============================================================
# 卸载 / 状态查询
# ============================================================

def _delete_tree(root, path):
    """递归删注册表键。Windows 的 DeleteKey 不能删有子键的键。"""
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(root, path + '\\' + sub)
    except FileNotFoundError:
        return False
    except OSError:
        pass
    try:
        winreg.DeleteKey(root, path)
        return True
    except OSError:
        return False


def uninstall():
    """删掉所有注册项。返回实际删掉的个数。"""
    if not is_windows():
        return 0

    paths = []
    for ext in FILE_EXTS:
        paths.append(rf'Software\Classes\SystemFileAssociations\.{ext}'
                     rf'\shell\{APP_KEY}')
    paths.append(rf'Software\Classes\Directory\shell\{APP_KEY}')
    paths.append(rf'Software\Classes\Directory\Background\shell\{APP_KEY}')
    paths.append(rf'Software\Classes\Drive\shell\{APP_KEY}')

    n = 0
    for p in paths:
        if _delete_tree(winreg.HKEY_CURRENT_USER, p):
            n += 1
    return n


def _registered_exe():
    """从注册表读回当前实际写着的 exe 路径（不是本进程的路径）。

    为什么不能直接用 sys.executable：右键菜单是**之前某次运行**写进
    注册表的，可能指向另一个位置。典型事故是打包产物目录被删了
    （PyInstaller 每次重建都会先删 dist/），注册表里的菜单项还在，
    但指向的 exe 已经不存在 —— 右键点了没反应，而且没有任何提示。

    返回 None 表示读不到（没注册 / 键结构异常）。
    """
    import re
    paths = (
        rf'Software\Classes\SystemFileAssociations\.png\shell\{APP_KEY}\command',
        rf'Software\Classes\Directory\shell\{APP_KEY}\command',
        rf'Software\Classes\Drive\shell\{APP_KEY}\command',
    )
    for p in paths:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, p) as k:
                cmd = winreg.QueryValueEx(k, '')[0]
        except OSError:
            continue
        m = re.search(r'"([^"]+\.exe)"', cmd)
        if m:
            return m.group(1)
        # 非 exe 形式（源码运行的 python.exe + app.py），取第一段
        m = re.search(r'"([^"]+)"', cmd)
        if m:
            return m.group(1)
    return None


def _stale():
    """注册项存在，但它指向的可执行文件已经不在了。

    这种情况「已注册」和「能用了」是两件事，界面必须分开显示 ——
    否则用户看到开关是开的，右键却是死的。
    """
    exe = _registered_exe()
    if not exe:
        return False
    return not os.path.exists(exe)


def status():
    """检查当前注册状态，供界面显示。"""
    if not is_windows():
        return {'supported': False}

    def exists(path):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path):
                return True
        except OSError:
            return False

    file_hits = _status_files()
    registered = _registered_exe()
    # 源码运行时也要检查：app.py 被移走/重命名，同样会让菜单失效
    stale = bool(registered) and not os.path.exists(registered)
    return {
        'supported': True,
        'admin': is_admin(),
        'files': file_hits,
        'dir': exists(rf'Software\Classes\Directory\shell\{APP_KEY}'),
        'dir_bg': exists(
            rf'Software\Classes\Directory\Background\shell\{APP_KEY}'),
        'drive': exists(rf'Software\Classes\Drive\shell\{APP_KEY}'),
        # enabled 只表示「注册表里有」，不代表能点得动
        'enabled': bool(file_hits) or exists(
            rf'Software\Classes\Drive\shell\{APP_KEY}'),
        # stale 表示「有，但指向的目标不见了」—— 界面据此显示修复入口
        'stale': stale,
        'registered': registered,
        # expected 只用于界面展示「现在会指向哪里」，去掉命令里多余的分段
        'expected': _exe_path().split('" "')[0].strip('"'),
        'command': _command('file'),
    }


def repair():
    """重新写一遍注册项，把命令指向当前这个可执行文件。

    场景：换了安装位置、重新打包换了目录、或旧产物被删。
    不需要先 uninstall —— install 会把同名键的值覆盖掉。
    """
    return install()



if __name__ == '__main__':
    import json
    if len(sys.argv) < 2:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    elif sys.argv[1] == 'install':
        o, f = install()
        print('已注册:', o)
        if f:
            print('失败:', f)
    elif sys.argv[1] == 'uninstall':
        print('已删除键数:', uninstall())
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
