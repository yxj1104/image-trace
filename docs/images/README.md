# 截图

README 里引用的四张界面截图。

**这些图是用假数据生成的，不是真实工作文件。**
生成脚本在 `tools/demo/` 下，它造了 47 个假的
PSD / JPG / PNG（渐变底 + 中文大字，模拟设计稿），索引到独立库
`D:/作品集/_data`，然后截图。

不用真实索引是因为里面有真实的项目名与客户资料，
README 是公开的，截进去等于泄露。

## 两条硬要求

**1. 目录名不能带 demo / test / sample。**
`02-reverse.png` 的详情面板会把文件的完整路径显示出来。第一版用的
是 `D:\_demo\files\...`，截图一眼就能看出是假数据 —— 而且显得不专业。
现在用 `D:\作品集\2026案例`。

**2. 假文件名必须先跟真实索引比对。**
第一版有一个 `国庆海报` / `欢度国庆`，而用户真实库里有 7 个同名文件
（包括 `泰国欢度国庆.psd`）。名字撞上了，就等于把真实文件名截进 README。
现在换成 `节庆主视觉` / `节日快乐`，`纹理`（真实库 887 条）也换成了
`底纹质感`。

改假数据名字前先跑一遍：

```python
import sqlite3
c = sqlite3.connect('data/index.db')
for w in ['要用的名字']:
    n = c.execute("SELECT COUNT(*) FROM files WHERE path LIKE ?", (f'%{w}%',)).fetchone()[0]
    print(w, n, '可用' if n == 0 else '重合，换一个')
```

| 文件 | 内容 | 用于 |
|---|---|---|
| `01-browse.png` | 浏览全部文件，三栏布局 | 「界面」章节 |
| `02-reverse.png` | 以图搜图结果 + 右侧详情 | 首屏图、「以图搜图」章节 |
| `03-ocr.png` | 搜「火锅」命中图里的字 | 「图上文字」章节 |
| `04-scene.png` | 搜「红色喜庆的海报」 | 「画面内容」章节 |

重新截图：

```bash
python tools/demo/make_files.py                 # 造 47 个假文件
python trace.py --db D:/作品集/_data/index.db index D:/作品集/2026案例
python trace.py --db D:/作品集/_data/index.db pair
python trace.py --db D:/作品集/_data/index.db serve --port 8851

# 另开一个终端
python tools/demo/shot_browse.py                # 浏览页
python tools/demo/shot_reverse.py               # 以图搜图
python tools/demo/shot_modes.py                 # 文字与画面搜索
```

截图后统一压一遍（1440 宽、256 色调色板），否则单张 1.2MB：

```python
from PIL import Image
im = Image.open(f).convert('RGB').quantize(colors=256)
im.save(f, optimize=True)
```

## 截图完必须做的一步

把截好的图喂给 OCR 扫一遍，确认没有敏感词漏出来。这个项目的 OCR 引擎
正好能直接用：

```python
import sys; sys.path.insert(0, '.')
from PIL import Image
from core import ocr

im = Image.open('docs/images/02-reverse.png').convert('RGB')
im = im.resize((im.width * 3, im.height * 3), Image.LANCZOS)
txt = ' '.join(t for t, _s, _b in ocr.recognize(im))
for w in ['客户名', '真实项目名', 'demo', '旅游']:
    if w in txt:
        print('❌ 漏了:', w)
```

放大 3 倍是必要的 —— 详情面板的路径字号只有 12px，不放大识别不出来。
