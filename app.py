#!/usr/bin/env python3
"""溯源 — 打包入口。

双击运行 → 启动本地服务 → 自动打开浏览器。
窗口关闭后再双击会复用同一个实例（端口占用时自动跳过）。

用户不需要看到命令行黑框，也不需要知道 Python 在哪。
"""
import os
import sys
import socket
import threading
import time
import webbrowser

# 打包后 sys.path[0] 是 _MEIPASS，源码运行时是本文件所在目录
BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from core import paths as pathmod

APP_NAME = '溯源'
DEFAULT_PORT = 8848


def find_running(port, span=20):
    """检查是否已有实例在跑。有的话直接打开浏览器并退出。"""
    for p in range(port, port + span):
        s = socket.socket()
        s.settimeout(0.12)
        try:
            if s.connect_ex(('127.0.0.1', p)) == 0:
                return p
        finally:
            s.close()
    return None


def parse_shell_args(argv):
    """解析右键菜单传来的参数。

    资源管理器调用形式：
        溯源.exe --from-shell --search "C://图.jpg"
        溯源.exe --from-shell --add-root "D:////素材"

    返回 {'from_shell': bool, 'search': path|None, 'add_root': path|None}
    路径已去掉资源管理器自动加的引号。
    """
    out = {'from_shell': False, 'search': None, 'add_root': None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--from-shell':
            out['from_shell'] = True
        elif a in ('--search', '--add-root') and i + 1 < len(argv):
            val = argv[i + 1].strip().strip('"')
            # '--add-root'[2:] 是 'add-root'，要换成下划线才对得上键名
            out[a[2:].replace('-', '_')] = val
            i += 1
        i += 1
    return out


def handle_shell(argv):
    """右键触发时把请求转成服务端的一次动作。

    已有实例在跑 → 通过本地 HTTP 接口把动作交给它，自己不另起一个进程。
    没在跑 → 先启动服务，等服务就绪后再发动作。
    返回 0 表示已处理，None 表示不是右键调用。
    """
    sh = parse_shell_args(argv)
    if not sh['from_shell']:
        return None

    import json
    import urllib.request
    import urllib.parse

    print(f'右键触发: {sh}')

    port = find_running(DEFAULT_PORT)
    if not port:
        # 没有实例：起一个后台服务线程，再等它就绪
        from server import run
        t = threading.Thread(
            target=lambda: run(db_path=pathmod.db_path(),
                               port=DEFAULT_PORT, open_browser=False),
            daemon=True)
        t.start()
        for _ in range(120):          # 最多等 12 秒
            time.sleep(0.1)
            port = find_running(DEFAULT_PORT)
            if port:
                break
        if not port:
            print('服务未能启动')
            return 1

    base = f'http://127.0.0.1:{port}'

    def post(path, payload):
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            base + path, data=data,
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            return json.loads(urllib.request.urlopen(req, timeout=15).read())
        except Exception as e:
            print(f'{path} 失败: {e}')
            return None

    def get(path, params=None):
        url = base + path
        if params:
            url += '?' + urllib.parse.urlencode(params)
        try:
            return json.loads(urllib.request.urlopen(url, timeout=15).read())
        except Exception as e:
            print(f'{path} 失败: {e}')
            return None

    acted = False

    if sh['add_root'] and os.path.isdir(sh['add_root']):
        cur = get('/api/roots') or {}
        roots = list(cur.get('roots') or [])
        target = os.path.normpath(sh['add_root'])
        if target.endswith(':'):
            target += os.sep
        already = target in roots
        if not already:
            roots.append(target)
            post('/api/roots', {'roots': roots})
            print('已加入索引范围:', target)
        else:
            print('该目录已在索引范围内:', target)
        # 通知界面：用户是从右键进来的，要看到设置面板和索引进度
        post('/api/shell-action', {
            'action': 'root', 'path': target, 'added': not already,
            'indexing': True,
        })
        # 触发索引。409 表示已有作业在跑，那也是用户想要的，不算失败
        post('/api/index/start', {'roots': roots})
        acted = True

    elif sh['search'] and os.path.isfile(sh['search']):
        # 图像搜索：上传文件给服务端
        try:
            with open(sh['search'], 'rb') as f:
                data = f.read()
            req = urllib.request.Request(
                base + '/api/search', data=data,
                headers={'Content-Type': 'application/octet-stream'},
                method='POST')
            r = json.loads(urllib.request.urlopen(req, timeout=60).read())
            print('搜索结果:', r.get('total'))
            # 把结果和源文件名一起交给界面
            top = (r.get('results') or [{}])[0]
            post('/api/shell-action', {
                'action': 'search',
                'path': sh['search'],
                'total': r.get('total', 0),
                'top': top.get('name', ''),
            })
            acted = True
        except Exception as e:
            print('搜索失败:', e)
            post('/api/shell-action', {
                'action': 'search', 'path': sh['search'], 'error': str(e),
            })

    # 把界面呈现给用户。右键的语义是「用这个应用处理这个文件」，
    # 所以优先复用已经开着的原生窗口；没有窗口才开一个，最后才退浏览器。
    q = ''
    if sh['search']:
        q = '?shell=1&highlight=1'
    elif sh['add_root']:
        q = '?shell=1&settings=1'
    else:
        q = '?shell=1'

    url = base + '/' + q

    from core import desktop

    # 1) 已有窗口：请它自己把窗口叫到前面。
    #
    # 不能让本进程直接 SetForegroundWindow —— 本进程处理完就退出，
    # Windows 会把前台还给原来的窗口，用户看到的还是浏览器。
    # 实测：窗口能恢复（IsIconic 变 False），但前台窗口仍是浏览器。
    # 所以只留个标记文件，由长驻进程的监听线程去抢。
    if desktop.find_window(APP_NAME):
        desktop.request_focus()
        print('已请现有窗口置前')
        return 0

    # 2) 服务在跑但没有窗口（例如之前用 --browser 启动的）：
    #    在自己的进程里开一个原生窗口，并且要等它关闭才退出
    if desktop.webview2_available():
        print(f'打开原生窗口 -> {url}')
        if desktop.run_window(url, title=APP_NAME):
            return 0
        print('原生窗口启动失败，退回浏览器')

    # 3) 兜底：浏览器
    webbrowser.open(url)
    time.sleep(1.2)
    return 0


def _pdfium_version():
    """真实测一次渲染，只查版本号不够（版本属性名各版本不同）。

    不扫盘找样本 —— 别人的盘符不一样，写死一个盘会让这项无谓失败。
    直接新建一个内存文档渲染，能验证「pdfium 加载 + 渲染管线」是通的。
    """
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument.new()
    doc.new_page(100, 100)
    doc[0].render(scale=0.5).to_pil()
    doc.close()
    # 版本属性的名字跨版本变过（V_PYPDFIUM2 → PYPDFIUM_INFO），
    # 取不到就只报「渲染 OK」，不要让自检因为版本字符串挂掉
    ver = getattr(pdfium, 'PYPDFIUM_INFO', None)
    if ver is not None:
        return f'渲染 OK (pdfium {ver})'
    return '渲染 OK'


def selftest():
    """打包后自检：确认关键模块与资源都在。

    用法：溯源.exe --selftest，结果写进 data/selftest.log
    """
    lines = []
    ok = True

    def chk(name, fn):
        nonlocal ok
        try:
            r = fn()
            lines.append(f'[OK]   {name}: {r}')
        except Exception as e:
            ok = False
            lines.append(f'[FAIL] {name}: {type(e).__name__}: {e}')

    chk('web/index.html', lambda: os.path.getsize(
        os.path.join(pathmod.web_dir(), 'index.html')))
    chk('import numpy', lambda: __import__('numpy').__version__)
    chk('import cv2', lambda: __import__('cv2').__version__)
    chk('import PIL', lambda: __import__('PIL').__version__)
    chk('import imagehash', lambda: __import__('imagehash').__name__)
    chk('import pypdfium2', lambda: _pdfium_version())

    def _extract():
        import extract
        return f'{len(extract.ALL_EXTS)} 种格式'

    def _scanner():
        """扫描器自检。

        **不要写死具体目录** —— 用户的盘符和目录结构各不相同，
        写死会让这个自检项在别人机器上必然失败，误报成「程序坏了」。
        从索引的 roots 里取第一个还能访问的目录；都不行就扫当前目录。
        """
        from core import scanner, db as dbmod
        d = dbmod.DB(pathmod.db_path())
        roots = [r['path'] for r in
                 d.conn.execute("SELECT path FROM roots")]
        d.close()
        target = next((r for r in roots if os.path.isdir(r)), None)
        if not target:
            target = os.getcwd()
        fs = list(scanner.walk_files([target], verbose=False))
        return f'扫描 {len(fs)} 个（{target}）'

    def _db():
        from core import db as dbmod
        d = dbmod.DB(pathmod.db_path())
        n = d.stats()['total']
        d.close()
        return f'{n} 条记录'

    def _svg():
        """resvg 是否可用。

        原来用 glob 扫整个 G 盘找 SVG 样本 —— 在别人机器上没有 G 盘，
        这项就无谓地失败。改成从索引库里查，没样本时直接跳过。
        """
        from extract import svg
        from core import db as dbmod
        d = dbmod.DB(pathmod.db_path())
        r = d.conn.execute(
            "SELECT path FROM files WHERE ext='svg' LIMIT 1").fetchone()
        d.close()
        if not r or not os.path.isfile(r['path']):
            return '无样本（跳过）'
        im, err = svg.extract(r['path'])
        if im is None:
            raise RuntimeError(f'resvg 渲染失败: {err}')
        return f'渲染 {im.size}'

    def _eps():
        import extract
        return 'EPS 解码器已加载' if 'eps' in str(extract.ALL_EXTS) else '未注册'

    chk('extract.svg (resvg)', _svg)
    chk('extract.eps', _eps)
    chk('extract 包', _extract)
    def _shell():
        from tools import shellmenu
        st = shellmenu.status()
        return (f'running={st.get("enabled")} '
                f'formats={len(st.get("files") or [])}')

    chk('core.scanner', _scanner)
    chk('core.db', _db)
    def _office():
        from extract import office, shellthumb
        import glob
        from core import db as dbmod
        d = dbmod.DB(pathmod.db_path())
        r = d.conn.execute(
            "SELECT path FROM files WHERE ext IN ('docx','doc') LIMIT 1").fetchone()
        d.close()
        cand = [r['path']] if r and os.path.isfile(r['path']) else []
        if not cand:
            return '无样本（跳过）'
        tmp = shellthumb.get_thumbnail(cand[0], 256)
        try:
            import comtypes  # noqa: F401
            com = True
        except ImportError:
            com = False
        return f'shell={"OK" if tmp else "无"} com={com}'

    def _desktop():
        from core import desktop
        has_wv2 = desktop.webview2_available()
        try:
            import webview  # noqa: F401
            has_pw = True
        except Exception:
            has_pw = False
        return f'webview={has_pw} webview2={has_wv2}'

    def _semantic():
        """画面搜索能否工作：模型文件在、能加载、能编码文字。"""
        from core import semantic
        if not semantic.available():
            return '模型缺失'
        v = semantic.encode_text('沙滩')
        if v is None:
            err = semantic.load_error() or '编码失败'
            raise RuntimeError(err)
        return f'加载 OK 维度={len(v)}'

    def _faces():
        """人物搜索的人脸检测器。"""
        from core import faces
        if not faces.available():
            return '模型缺失'
        from PIL import Image
        blank = Image.new('RGB', (200, 200), (255, 255, 255))
        n = faces.detect(blank)
        if faces.load_error():
            raise RuntimeError(faces.load_error())
        return f'检测 OK 空白图={n} 张脸'

    def _ocr():
        """图中文搜索：OCR 模型能加载、能识别出已知文字。"""
        from core import ocr
        if not ocr.available():
            return '缺少 rapidocr_onnxruntime'
        if not ocr._lazy_load(2):
            raise RuntimeError(ocr.load_error() or '模型加载失败')
        # 画一张带汉字的图，确认识别链路完整（模型 → 检测 → 识别）。
        # 必须指定中文字体：PIL 的默认位图字体没有汉字字形，
        # 直接用默认字体会渲染成方块，测出来必然是 0 段（实测如此）。
        try:
            from PIL import Image, ImageDraw, ImageFont
            font = ImageFont.truetype('msyh.ttc', 34)
            im = Image.new('RGB', (460, 90), (255, 255, 255))
            dr = ImageDraw.Draw(im)
            dr.text((16, 24), '开业庆典 2026', fill=(0, 0, 0), font=font)
            res = ocr.recognize(im)
            txt = ' '.join(t for t, _s, _b in res)
        except OSError:
            # 系统没有微软雅黑（精简版系统）：只测英文，不阻断自检
            from PIL import Image, ImageDraw
            im = Image.new('RGB', (460, 90), (255, 255, 255))
            ImageDraw.Draw(im).text((16, 30), 'Thailand 12345', fill=(0, 0, 0))
            res = ocr.recognize(im)
            txt = ' '.join(t for t, _s, _b in res)
            if res:
                return f'识别 OK（无中文字体，仅测英文）{txt[:20]}'
            raise RuntimeError('识别不出任何文字')
        except Exception as e:
            raise RuntimeError(f'{type(e).__name__}: {e}')
        if not res:
            raise RuntimeError('识别不出文字（模型可能损坏）')
        return f'识别 OK {len(res)} 段：{txt[:20]}'

    chk('tools.shellmenu', _shell)
    chk('extract.office', _office)
    chk('core.desktop', _desktop)
    chk('core.semantic', _semantic)
    chk('core.faces', _faces)
    chk('core.ocr', _ocr)

    out = '\n'.join(lines)
    print(out)
    try:
        with open(os.path.join(pathmod.data_dir(), 'selftest.log'),
                  'w', encoding='utf-8') as f:
            f.write(out + ('\n\n全部通过' if ok else '\n\n有失败项'))
    except OSError:
        pass
    return 0 if ok else 1


def main():
    # 打包后没有控制台，所有输出写日志方便排查
    log_path = os.path.join(pathmod.data_dir(), 'app.log')
    try:
        log = open(log_path, 'a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = log
    except OSError:
        pass

    print(f'\n=== {time.strftime("%Y-%m-%d %H:%M:%S")} 启动 ===')
    print(f'frozen={pathmod.is_frozen()}  dir={pathmod.app_dir()}')
    print(f'data={pathmod.data_dir()}')

    # 自检：排查打包环境里模块缺失、路径解析错误
    if '--selftest' in sys.argv:
        return selftest()

    # 资源管理器右键触发
    handled = handle_shell(sys.argv[1:])
    if handled is not None:
        return handled

    # 窗口模式的选择要先于「已有实例」判断：
    # 用户双击 exe 是要一个窗口，不是要在浏览器里再开一个标签页
    use_browser = '--browser' in sys.argv
    use_window = not use_browser and _can_use_window()

    if use_window:
        if _run_desktop():
            return 0
        print('原生窗口启动失败，退回浏览器模式')

    running = find_running(DEFAULT_PORT)
    if running:
        print(f'已有实例运行在 {running}，直接打开界面')
        webbrowser.open(f'http://127.0.0.1:{running}')
        time.sleep(0.8)
        return 0

    from server import run
    return run(db_path=pathmod.db_path(), port=DEFAULT_PORT,
               open_browser=True)


def _can_use_window():
    """判断能否用原生窗口。

    打包后默认走原生窗口（双击就是应用，不该弹浏览器）。
    源码运行时默认也走，但可以用 --browser 强制退回。
    """
    try:
        from core import desktop
        if not desktop.webview2_available():
            print('未检测到 WebView2 运行时')
            return False
        import webview  # noqa: F401
        return True
    except Exception as e:
        print(f'原生窗口不可用: {e}')
        return False


def _run_desktop():
    """起服务 + 开原生窗口。窗口关闭后整个进程退出。"""
    import socket
    import threading
    from core import desktop

    # 已有实例在跑：直接复用它，只开窗口
    running = find_running(DEFAULT_PORT)
    if not running:
        port = DEFAULT_PORT
        threading.Thread(
            target=lambda: _serve_silent(port), daemon=True).start()
        if not desktop.wait_port(port, timeout=20):
            print('服务启动超时')
            return False
        running = port

    print(f'原生窗口 -> http://127.0.0.1:{running}')
    ok = desktop.run_window(f'http://127.0.0.1:{running}/', title=APP_NAME)
    return ok


def _serve_silent(port):
    from server import run
    try:
        run(db_path=pathmod.db_path(), port=port, open_browser=False)
    except Exception as e:
        print(f'服务异常: {e}')


if __name__ == '__main__':
    # PyInstaller 打包后必须调用：否则任何 multiprocessing 子进程都会
    # 重新执行整个入口脚本，在无控制台环境下直接崩掉
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        sys.exit(main())
    except Exception:
        import traceback
        print(traceback.format_exc())
        # 打包后无控制台，异常必须留痕，否则用户只看到「闪一下没了」
        try:
            log = os.path.join(pathmod.data_dir(), 'crash.log')
            with open(log, 'a', encoding='utf-8') as f:
                f.write(f'\n--- {time.strftime("%Y-%m-%d %H:%M:%S")} ---\n')
                f.write(traceback.format_exc())
        except Exception:
            pass
        sys.exit(1)
