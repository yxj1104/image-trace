# 截图

README 里引用的四张界面截图。

**这些图是用假数据生成的，不是真实工作文件。**
生成脚本在 `tools/demo/` 下，它造了 47 个假的
PSD / JPG / PNG（渐变底 + 中文大字，模拟设计稿），索引到独立库
`D:/_demo/data`，然后截图。

不用真实索引是因为里面有客户项目名（「示例项目」这类），
README 是公开的，截进去等于泄露。

| 文件 | 内容 | 用于 |
|---|---|---|
| `01-browse.png` | 浏览全部文件，三栏布局 | 「界面」章节 |
| `02-reverse.png` | 以图搜图结果 + 右侧详情 | 首屏图、「以图搜图」章节 |
| `03-ocr.png` | 搜「火锅」命中图里的字 | 「图上文字」章节 |
| `04-scene.png` | 搜「红色喜庆的海报」 | 「画面内容」章节 |

重新截图：

```bash
python tools/demo/make_files.py                 # 造 47 个假文件
python trace.py --db D:/_demo/data/index.db index D:/_demo/files
python trace.py --db D:/_demo/data/index.db pair
python trace.py --db D:/_demo/data/index.db serve --port 8851

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
