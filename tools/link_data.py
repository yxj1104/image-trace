"""给打包产物建一个指向项目 data/ 的目录联接，避免重复占用数百 MB。

用途：开发时反复打包调试，不想每次跑一遍索引。建个联接让
dist/溯源/data 指向项目根下已有的 data/，两边共用同一份索引库。

**这个脚本只在开发时用，不参与打包产物的分发。** 用户拿到 zip 解压后
data/ 是空的，首次运行会自己创建。

以前这里写死了绝对路径，换机器就失效；改成按本文件的位置推算
项目根，任何路径下都能跑。
"""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, 'data')


def link_path():
    """打包产物里的 data 路径。

    dist/ 下可能有多个产物目录（换了名字），取 exe 所在的那个。
    找不到就以「溯源」为默认名。
    """
    dist = os.path.join(ROOT, 'dist')
    if os.path.isdir(dist):
        for name in os.listdir(dist):
            p = os.path.join(dist, name)
            if os.path.isdir(p) and os.path.isfile(os.path.join(p, name + '.exe')):
                return os.path.join(p, 'data')
    return os.path.join(dist, '溯源', 'data')


def main():
    link = link_path()
    if not os.path.isdir(TARGET):
        print('项目 data/ 不存在，先跑一次索引:', TARGET)
        return 1
    if os.path.exists(link):
        print('已存在:', link)
        return 0
    os.makedirs(os.path.dirname(link), exist_ok=True)
    # mklink 是 cmd 内置命令，必须经 cmd 调用；
    # 路径可能含中文，整条命令交给 shell 最省事
    cmd = f'mklink /J "{link}" "{TARGET}"'
    p = subprocess.run(cmd, shell=True, capture_output=True)
    out = (p.stdout + p.stderr).decode('gbk', 'ignore').strip()
    print(out)
    ok = os.path.isdir(link)
    print('联接可用:', ok)
    if ok:
        db = os.path.join(link, 'index.db')
        if os.path.isfile(db):
            print('索引存在: True', f'{os.path.getsize(db) // 1024 // 1024} MB')
        else:
            print('索引存在: False')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
