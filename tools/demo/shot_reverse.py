"""开发用：截「以图搜图」结果页。

需要先跑 tools/demo/make_files.py 造数据并启动服务：

    python trace.py --db D:/_demo/data/index.db serve --port 8851

路径按需改 URL / OUT 两行即可。
"""
import os

from playwright.sync_api import sync_playwright

URL = 'http://127.0.0.1:8851/'
OUT = r'D:\figma\agent\image-trace\docs\images'
TARGET = r'D:\_demo\files\产品海报\火锅季套餐.jpg'

os.makedirs(OUT, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width': 1440, 'height': 900},
                    device_scale_factor=2)
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))

    pg.goto(URL, wait_until='domcontentloaded')
    pg.wait_for_timeout(4000)
    pg.evaluate("document.querySelector('#overlay')?.classList.remove('open')")
    pg.mouse.move(1200, 850)
    pg.wait_for_timeout(1000)

    # 用 JS 直接构造一个 File 和 drop 事件，模拟用户拖图。
    # 前端逻辑就是读 dataTransfer.files[0]，这样最接近真实路径。
    import base64
    data = open(TARGET, 'rb').read()
    b64 = base64.b64encode(data).decode()

    pg.evaluate("""async (b64) => {
        const res = await fetch('data:image/jpeg;base64,' + b64);
        const blob = await res.blob();
        const file = new File([blob], '火锅季套餐.jpg', { type: 'image/jpeg' });
        const dt = new DataTransfer();
        dt.items.add(file);
        window.dispatchEvent(new DragEvent('drop', {
            dataTransfer: dt, bubbles: true, cancelable: true
        }));
    }""", b64)

    pg.wait_for_timeout(7000)
    pg.mouse.move(1200, 850)
    pg.wait_for_timeout(800)

    print('结果卡:', pg.locator('.card').count(), '| 标题:', pg.inner_text('#main-title'))
    pg.screenshot(path=os.path.join(OUT, '02-reverse.png'))
    print('已截 02-reverse.png')
    print('错误:', errs if errs else '无')
    b.close()
