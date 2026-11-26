"""开发用：截「浏览全部文件」界面。

需要先跑 tools/demo/make_files.py 造数据并启动服务：

    python trace.py --db D:/作品集/_data/index.db serve --port 8851

路径按需改 URL / OUT 两行即可。
"""
import os

from playwright.sync_api import sync_playwright

URL = 'http://127.0.0.1:8851/'
OUT = r'D:\figma\agent\image-trace\docs\images'

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': 1440, 'height': 900},
                    device_scale_factor=2)
    pg.goto(URL, wait_until='domcontentloaded')
    pg.wait_for_timeout(4500)
    # 关掉首次引导弹窗；不点任何带 tooltip 的控件
    pg.evaluate("document.querySelector('#overlay')?.classList.remove('open')")
    pg.wait_for_timeout(800)
    # 鼠标移到最后一张卡片上（卡片没有 tooltip 绑定到模式区）
    pg.mouse.move(700, 780)
    pg.wait_for_timeout(1200)
    pg.mouse.move(1000, 30)      # 顶栏空白
    pg.wait_for_timeout(1500)

    tips = pg.evaluate("""() => [...document.querySelectorAll('.tip,.tooltip')]
        .filter(e => getComputedStyle(e).display !== 'none' &&
                     getComputedStyle(e).opacity !== '0').length""")
    print('可见 tooltip 数:', tips)

    pg.screenshot(path=os.path.join(OUT, '01-browse.png'))
    print('已截 01-browse.png', pg.locator('.card').count(), '卡片')
    b.close()
