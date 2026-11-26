"""开发用：截「图上文字」和「画面内容」两个模式。

需要先跑 tools/demo/make_files.py 造数据并启动服务：

    python trace.py --db D:/_demo/data/index.db serve --port 8851

路径按需改 URL / OUT 两行即可。
"""
import base64
import os

from playwright.sync_api import sync_playwright

URL = 'http://127.0.0.1:8851/'
OUT = r'D:\figma\agent\image-trace\docs\images'
os.makedirs(OUT, exist_ok=True)


def clean(pg):
    pg.evaluate("document.querySelector('#overlay')?.classList.remove('open')")
    pg.mouse.move(1250, 860)
    pg.wait_for_timeout(600)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': 1440, 'height': 900},
                    device_scale_factor=2)
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))

    pg.goto(URL, wait_until='domcontentloaded')
    pg.wait_for_timeout(4000)
    clean(pg)

    # ---- 图上文字 ----
    pg.evaluate("setSearchMode('text')")
    pg.wait_for_timeout(1500)
    pg.fill('#q', '火锅')
    pg.keyboard.press('Enter')
    pg.wait_for_timeout(4000)
    clean(pg)
    print('文字搜索:', pg.locator('.card').count(), '张 |', pg.inner_text('#main-title'))
    pg.screenshot(path=os.path.join(OUT, '03-ocr.png'))

    # ---- 画面内容 ----
    pg.evaluate("setSearchMode('semantic')")
    pg.wait_for_timeout(1500)
    pg.fill('#q', '红色喜庆的海报')
    pg.keyboard.press('Enter')
    pg.wait_for_timeout(5000)
    clean(pg)
    print('画面搜索:', pg.locator('.card').count(), '张 |', pg.inner_text('#main-title'))
    pg.screenshot(path=os.path.join(OUT, '04-scene.png'))

    print('错误:', errs if errs else '无')
    b.close()
