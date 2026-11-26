"""生成一套演示用的假文件，供 README 截图。

**为什么不在真实索引上截图**：用户的索引里全是真实项目名
（真实的项目名和客户资料），README 是公开的，
截进去等于泄露。所以造一套看起来像设计文件的假数据。

造的形态贴近真实使用场景：
  - 成对的 PSD + JPG（源文件与导出图，部分同名部分不同名）
  - 单张 JPG / PNG
  - 一张带明显中文大字的「海报」用于演示图上文字搜索

输出到 D:\\作品集\\2026案例，索引库在 D:\\作品集\\_data。

**目录名有讲究**：详情面板会把文件的完整路径显示出来，所以路径里
不能出现 demo / test / sample 这类字样。用「作品集」这种真实感的名字。
同样，生成的假文件名也要先跟真实索引比对 —— 「国庆海报」「欢度国庆」
在用户库里都有真实文件，撞上了就等于把真实文件名截进 README。
"""
import os
import random
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from psd_thumb import inject as inject_thumb   # noqa: E402

# 路径里不能出现 demo / test 这类字样 —— 详情面板会把完整路径
# 显示出来，截进 README 里带 demo 的目录名等于告诉所有人这是假数据。
OUT = r'D:\作品集\2026案例'
FONT = r'C:\Windows\Fonts\msyhbd.ttc'

# (目录, 主名, 副标题, 主色, 副色, 图上文字)
ITEMS = [
    ('2026夏季活动', '夏日海滩主视觉', 'SUMMER 2026', (32, 148, 190), (255, 214, 102), '夏日海滩狂欢'),
    ('2026夏季活动', '夏日海滩主视觉-终稿', 'SUMMER 2026', (32, 148, 190), (255, 214, 102), '夏日海滩狂欢'),
    ('2026夏季活动', '海岛露营招募', 'ISLAND CAMP', (44, 120, 96), (250, 240, 210), '海岛露营招募'),
    ('2026夏季活动', '夜游灯光节', 'LIGHT FESTIVAL', (18, 24, 62), (255, 186, 66), '夜游灯光节'),
    ('品牌素材', '品牌主色板', 'BRAND COLOR', (198, 62, 78), (255, 245, 235), '品牌识别系统'),
    ('品牌素材', 'logo横版', 'LOGO', (240, 240, 240), (30, 30, 30), 'IMAGE TRACE'),
    ('品牌素材', 'logo竖版', 'LOGO', (240, 240, 240), (30, 30, 30), 'IMAGE TRACE'),
    ('产品海报', '秋季新品发布', 'AUTUMN NEW', (150, 92, 52), (245, 228, 200), '秋季新品发布'),
    ('产品海报', '火锅季套餐', 'HOTPOT', (186, 42, 32), (255, 220, 150), '火锅季套餐'),
    ('产品海报', '咖啡买一送一', 'COFFEE BOGO', (92, 62, 44), (240, 220, 190), '咖啡买一送一'),
    ('产品海报', '甜品下午茶', 'DESSERT', (238, 160, 180), (255, 250, 240), '甜品下午茶'),
    ('参考图', '版式参考-A', 'LAYOUT REF', (200, 205, 215), (60, 60, 70), '版式参考'),
    ('参考图', '版式参考-B', 'LAYOUT REF', (215, 205, 195), (60, 55, 50), '版式参考'),
    ('参考图', '配色灵感', 'PALETTE', (120, 160, 140), (250, 245, 230), '配色灵感'),
    ('展会物料', '展位效果图', 'BOOTH', (48, 88, 140), (235, 240, 250), '国际会展中心'),
    ('展会物料', '易拉宝-正面', 'ROLLUP', (48, 88, 140), (235, 240, 250), '国际会展中心'),
    ('节日素材', '春节主视觉', 'SPRING FEST', (176, 36, 32), (250, 220, 120), '新春快乐'),
    ('节日素材', '中秋团圆', 'MID AUTUMN', (40, 52, 96), (250, 230, 160), '中秋团圆'),
    ('节日素材', '节庆主视觉', 'FESTIVAL', (200, 40, 40), (255, 235, 180), '节日快乐'),
    ('客户提案', '提案封面-甲', 'PROPOSAL', (70, 70, 80), (240, 240, 245), '品牌升级提案'),
    ('客户提案', '提案封面-乙', 'PROPOSAL', (80, 70, 70), (240, 240, 245), '品牌升级提案'),
    ('客户提案', '提案内页数据', 'DATA PAGE', (245, 245, 248), (60, 60, 70), '增长数据'),
]


def gradient(w, h, c1, c2):
    im = Image.new('RGB', (w, h), c1)
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(1, h - 1)
        col = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
        d.line([(0, y), (w, y)], fill=col)
    return im


def make(w, h, title, sub, c1, c2, big):
    im = gradient(w, h, c1, c2)
    d = ImageDraw.Draw(im)
    # 柔和光斑
    for _ in range(5):
        cx = random.randint(0, w)
        cy = random.randint(0, h)
        r = random.randint(w // 8, w // 3)
        ov = Image.new('RGBA', (r * 2, r * 2), (0, 0, 0, 0))
        ImageDraw.Draw(ov).ellipse(
            [0, 0, r * 2, r * 2], fill=(255, 255, 255, 16))
        im.paste(ov, (cx - r, cy - r), ov)

    dark = sum(c2) < 380
    fg = (250, 250, 250) if dark else (40, 44, 52)

    try:
        f_big = ImageFont.truetype(FONT, int(h * 0.13))
        f_sub = ImageFont.truetype(FONT, int(h * 0.035))
    except Exception:
        f_big = f_sub = ImageFont.load_default()

    # 大字（用于演示图上文字搜索）
    bw = d.textlength(big, font=f_big)
    d.text(((w - bw) / 2, h * 0.40), big, font=f_big, fill=fg)
    sw = d.textlength(sub, font=f_sub)
    d.text(((w - sw) / 2, h * 0.60), sub, font=f_sub, fill=fg)

    # 装饰细线
    d.line([(w * 0.2, h * 0.34), (w * 0.8, h * 0.34)], fill=fg, width=2)
    return im


def main():
    random.seed(20261126)
    os.makedirs(OUT, exist_ok=True)
    from psd_tools import PSDImage

    n = 0
    for folder, stem, sub, c1, c2, big in ITEMS:
        d = os.path.join(OUT, folder)
        os.makedirs(d, exist_ok=True)
        im = make(760, 1000, stem, sub, c1, c2, big)

        # 每项都写 PSD（源文件）。
        # psd_tools 保存时不写 1036 缩略图资源，而真实 Photoshop 一定写，
        # 所以要手工注入 —— 否则本项目提取器读不到（返回 no_thumb）。
        psd_path = os.path.join(d, stem + '.psd')
        PSDImage.frompil(im).save(psd_path)
        inject_thumb(psd_path, im)

        # 三分之二写同名 JPG（模拟「导出图」），其余用不同名，
        # 演示「文件名不一致也能找到源文件」
        if random.random() < 0.66:
            jpg_name = stem + '.jpg'
        else:
            jpg_name = random.choice([
                '微信图片_2026' + str(random.randint(1000, 9999)) + '.jpg',
                '导出-' + str(random.randint(1, 99)) + '.jpg',
                '未标题-' + str(random.randint(1, 30)) + '.jpg',
            ])
        im.save(os.path.join(d, jpg_name), quality=88)
        n += 2

    # 几张独立的 PNG（无源文件）
    for i, (c, label) in enumerate([
            ((210, 120, 150), '插画素材'), ((90, 150, 200), '图标集'),
            ((150, 170, 110), '底纹质感')]):
        d = os.path.join(OUT, '素材库')
        os.makedirs(d, exist_ok=True)
        im = make(600, 600, label, 'ASSET', c, (250, 250, 250), label)
        im.save(os.path.join(d, f'{label}-{i + 1}.png'))
        n += 1

    print(f'生成 {n} 个文件到 {OUT}')
    for root, dirs, files in os.walk(OUT):
        if files:
            rel = os.path.relpath(root, OUT)
            print(f'  {rel}: {len(files)}')


if __name__ == '__main__':
    main()
