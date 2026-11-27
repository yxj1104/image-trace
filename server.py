"""本地 HTTP 服务。只用标准库，零额外依赖。"""
import json
import os
import sys
import io
import time
import base64
import urllib.parse
import webbrowser
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import db as dbmod, retrieve
from core import paths as pathmod
from PIL import Image

WEB_DIR = pathmod.web_dir()

_state = {
    'db_path': None,
    'index': None,
    'rows': None,
    'data_dir': None,
    'job': None,
    'fingerprint': None,
    # 右键菜单触发的一次性动作，等前端来取
    'pending': None,
    # 详情面板大图缓存：{(path, mtime, size): bytes}
    # 每次点卡片都重新渲染一遍太浪费，AI 类文件要 2 秒
    'detail_cache': {},
}

DETAIL_MAX = 760          # 详情预览最长边
DETAIL_BIG = 1800         # 看大图时的最长边
_DETAIL_CACHE_LIMIT = 60

# 做画面语义编码的源文件格式，与 core.embedder.SOURCE_EXTS 保持一致
EMBED_EXTS = ('psd', 'ai', 'cdr', 'eps', 'pdf', 'docx', 'doc',
              'ppt', 'pptx', 'svg', 'tif', 'tiff')


def _ensure_index(db_path):
    """按需加载内存索引。库文件变了就重载。"""
    fp = _db_fingerprint(db_path)
    if (_state['index'] is not None and _state['db_path'] == db_path
            and _state['fingerprint'] == fp):
        return _state['index'], _state['rows']
    db = dbmod.DB(db_path)
    rows = db.all_files(with_orb=True)
    db.close()
    _state['rows'] = rows
    _state['index'] = retrieve.Index(rows, with_orb=True)
    _state['db_path'] = db_path
    _state['fingerprint'] = fp
    return _state['index'], rows


def _db_fingerprint(db_path):
    """用 (mtime, 大小, 记录数) 判断索引是否被外部改动过。"""
    try:
        st = os.stat(db_path)
        return (int(st.st_mtime), st.st_size)
    except OSError:
        return None


def _invalidate_index():
    _state['index'] = None
    _state['fingerprint'] = None


def _multi_term_hits(sem, mat, ids, terms, limit=120, top_per_term=60):
    """多个候选词各检索一次，用 RRF（倒数排名融合）合并。

    **不用「取最高分」**：每个词各自按自己的分布截断，每个都能召回
    50~100 条，取并集后变成 300+ 条（实测「火锅店开业海报」390 条），
    等于又退回到不过滤的状态。

    **也不用「向量平均」**：平均会把互斥概念糊在一起（「火锅」+「海报」
    的平均既不火锅也不海报）。

    RRF 是标准的混合排序做法，只看排名不看分数：
        score(d) = Σ 1 / (k + rank_i(d))
    同时命中多个词的图得分累加，自然排到前面；只被一个弱词勉强命中的
    图得分低。k=60 是文献里的常用值。
    """
    if not terms:
        return [], []

    K = 60
    fused = {}         # file_id -> 累计得分
    why = {}           # file_id -> 命中过哪些词
    for t in terms:
        qv = sem.encode_text(t)
        if qv is None:
            continue
        hits = sem.search(qv, mat, ids, limit=top_per_term)
        for rank, (fid, _score) in enumerate(hits):
            fused[fid] = fused.get(fid, 0.0) + 1.0 / (K + rank + 1)
            why.setdefault(fid, []).append(t)

    if not fused:
        return [], []

    ranked = sorted(fused.items(), key=lambda x: -x[1])

    # 用相对头名的比例截断。RRF 分数的量纲取决命中词数，不能用绝对值。
    # 0.35 是实测值：单词查询时它等价于「保留头部」，
    # 多词查询时能砍掉只被一个弱词命中的长尾。
    top = ranked[0][1]
    cutoff = top * 0.35
    out = [(fid, score) for fid, score in ranked if score >= cutoff]
    terms_used = sorted({t for fid, _ in out for t in why.get(fid, [])})
    return out[:limit], terms_used


def _attach_rows(db, hits, via, score_key='score'):
    """把 (file_id, score) 列表补成完整行，保持相似度顺序。

    SQL 的 IN 不保证返回顺序，必须按 hits 重排 —— 否则前端拿到的是
    随机顺序，用户看到的第一条不是最相似的。
    """
    if not hits:
        return []
    idmap = {i: s for i, s in hits}
    qm = ','.join('?' * len(idmap))
    rows = db.conn.execute(
        f"SELECT * FROM files WHERE id IN ({qm})", tuple(idmap)).fetchall()
    by_id = {r['id']: r for r in rows}

    out = []
    for fid, score in hits:
        r = by_id.get(fid)
        if r is None:
            continue
        d = _row_dict(r)
        d[score_key] = round(score, 4)
        d['matched'] = {'via': via, score_key: round(score, 4)}
        out.append(d)
    return out


def _render_image(path, ext, max_side):
    """按格式渲染一张预览图，最长边不超过 max_side。

    每种格式走各自最合适的渲染器，不能图省事全丢给 PIL ——
    PIL 遇到 EPS 会去调 Ghostscript，本机没有，要卡 36 秒才失败。
    """
    from PIL import Image as _Im

    if ext in ('.psd', '.tif', '.tiff', '.bmp', '.gif', '.png'):
        # PSD 里存着拼合后的预览位图，PIL 读的就是它（不碰图层树）。
        # 实测 767MB 的 PSD 只要 444ms。
        im = _Im.open(path)
        im.load()
        im = im.convert('RGB')
        im.thumbnail((max_side, max_side), _Im.LANCZOS)
        return im

    if ext in ('.ai', '.pdf'):
        # AI 文件大多能当 PDF 读，首页就是画布
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(path)
        try:
            page = doc[0]
            w, h = page.get_size()
            if not w or not h:
                return None
            im = page.render(scale=max_side / max(w, h)).to_pil()
        finally:
            doc.close()
        return im.convert('RGB')

    if ext == '.svg':
        import resvg_py
        im = _Im.open(io.BytesIO(bytes(
            resvg_py.svg_to_bytes(svg_path=path, width=max_side))))
        return im.convert('RGB')

    # 其余（CDR / EPS / Office）先试 Windows Shell 缩略图：
    # 系统装的处理器往往能出很大、很清晰的图（CDR 实测 1014×1280），
    # 比自己解 zip 里的 BMP 强得多。Shell 拿不到再退回索引解码器。
    try:
        from extract import shellthumb
        im = shellthumb.get_thumbnail(path, max_side)
        if im is not None:
            im = im.convert('RGB')
            im.thumbnail((max_side, max_side), _Im.LANCZOS)
            return im
    except Exception:
        pass

    from extract import extract as extract_file
    im, err, _kind = extract_file(path, ext.lstrip('.'))
    if im is None:
        return None
    im = im.convert('RGB')
    im.thumbnail((max_side, max_side), _Im.LANCZOS)
    return im


def _row_dict(r, matched=None):
    d = {
        'id': r['id'],
        'path': r['path'],
        'name': r['name'],
        'stem': r['stem'],
        'ext': r['ext'],
        'kind': r['kind'],
        'size': r['size'],
        'mtime': r['mtime'],
        'width': r['width'],
        'height': r['height'],
        'aspect': r['aspect'],
        'has_thumb': bool(r['thumb_path']),
        'error': r['extract_error'],
        'group_id': r['group_id'],
        'palette': json.loads(r['palette']) if r['palette'] else [],
    }
    if matched:
        d.update(matched)
    return d


class Handler(BaseHTTPRequestHandler):
    db_path = None

    def log_message(self, fmt, *a):
        pass  # 静音

    # ---------- 工具 ----------

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    # ---------- 路由 ----------

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ('/', '/index.html'):
            return self._serve_file(os.path.join(WEB_DIR, 'index.html'),
                                    'text/html; charset=utf-8')
        if path.startswith('/static/'):
            rel = path[len('/static/'):]
            safe = os.path.normpath(rel).replace('\\', '/')
            if safe.startswith('..'):
                return self._send(403, 'forbidden', 'text/plain')
            full = os.path.join(WEB_DIR, safe)
            ctype = {
                '.js': 'application/javascript; charset=utf-8',
                '.css': 'text/css; charset=utf-8',
                '.svg': 'image/svg+xml',
                '.png': 'image/png',
                '.woff2': 'font/woff2',
                '.ico': 'image/x-icon',
            }.get(os.path.splitext(full)[1], 'application/octet-stream')
            return self._serve_file(full, ctype)

        if path == '/api/stats':
            return self._api_stats()

        if path == '/api/thumb':
            return self._api_thumb(qs)

        if path == '/api/preview':
            return self._api_preview(qs)

        if path == '/api/file':
            return self._api_file(qs)

        if path == '/api/browse':
            return self._api_browse(qs)

        if path == '/api/filters':
            return self._api_filters()

        if path == '/api/list':
            return self._api_list(qs)

        if path == '/api/suggest':
            return self._api_suggest(qs)

        if path == '/api/roots':
            return self._api_roots()

        if path == '/api/index/status':
            return self._api_index_status()

        if path == '/api/semantic/status':
            return self._api_semantic_status()

        if path == '/api/semantic/search':
            return self._api_semantic_search(qs)

        if path == '/api/text/status':
            return self._api_text_status()

        if path == '/api/text/search':
            return self._api_text_search(qs)

        if path == '/api/shellmenu':
            return self._api_shellmenu_status()

        if path == '/api/pending':
            # 不「取一次就删」：右键会额外打开一个新标签页，
            # 旧标签页也在轮询，谁先拿到就消费掉对另一方不公平。
            # 改成带自增序号，前端记住自己处理过的最后一个序号。
            p = _state.get('pending')
            return self._json({'pending': p})

        return self._send(404, 'not found', 'text/plain')

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/api/search':
            return self._api_search()
        if parsed.path == '/api/open':
            return self._api_open()
        if parsed.path == '/api/rebuild-groups':
            return self._api_rebuild_groups()
        if parsed.path == '/api/index/start':
            return self._api_index_start()
        if parsed.path == '/api/index/cancel':
            return self._api_index_cancel()
        if parsed.path == '/api/semantic/start':
            return self._api_semantic_start()
        if parsed.path == '/api/semantic/cancel':
            return self._api_semantic_cancel()
        if parsed.path == '/api/text/start':
            return self._api_text_start()
        if parsed.path == '/api/text/cancel':
            return self._api_text_cancel()
        if parsed.path == '/api/roots':
            return self._api_roots_set()
        if parsed.path == '/api/shellmenu':
            return self._api_shellmenu()
        if parsed.path == '/api/shell-action':
            return self._api_shell_action()
        return self._send(404, 'not found', 'text/plain')

    def _serve_file(self, full, ctype):
        if not os.path.isfile(full):
            return self._send(404, 'not found', 'text/plain')
        try:
            with open(full, 'rb') as f:
                data = f.read()
        except OSError:
            return self._send(500, 'read error', 'text/plain')
        self._send(200, data, ctype)

    # ---------- API ----------

    def _api_stats(self):
        db = dbmod.DB(self.db_path)
        s = db.stats()
        roots = [r['path'] for r in
                 db.conn.execute("SELECT path FROM roots").fetchall()]
        last = db.get_meta('last_index')
        db.close()
        self._json({
            'total': s['total'],
            'by_ext': [{'ext': e, 'count': c} for e, c in s['by_ext']],
            'by_kind': [{'kind': k, 'count': c} for k, c in s['by_kind']],
            'errors': [{'error': e, 'count': c} for e, c in s['errors']],
            'roots': roots,
            'last_index': int(last) if last else None,
        })

    def _api_filters(self):
        """筛选器需要的聚合数据：格式计数 + 目录树 + 时间范围 + 颜色。"""
        db = dbmod.DB(self.db_path)
        rows = db.conn.execute(
            "SELECT ext, kind, path, size, mtime, aspect, width, height "
            "FROM files").fetchall()
        db.close()

        by_ext = {}
        by_kind = {}
        tree = {}
        mtimes = []
        for r in rows:
            by_ext[r['ext']] = by_ext.get(r['ext'], 0) + 1
            by_kind[r['kind']] = by_kind.get(r['kind'], 0) + 1
            if r['mtime']:
                mtimes.append(r['mtime'])
            p = r['path'].replace('/', '\\')
            parts = p.split('\\')
            if len(parts) >= 2:
                drive = parts[0]
                l1 = parts[1] if len(parts) > 2 else ''
                l2 = parts[2] if len(parts) > 3 else ''
                node = tree.setdefault(drive, {'count': 0, 'children': {}})
                node['count'] += 1
                if l1:
                    c1 = node['children'].setdefault(
                        l1, {'count': 0, 'children': {}})
                    c1['count'] += 1
                    if l2:
                        c2 = c1['children'].setdefault(l2, {'count': 0})
                        c2['count'] += 1

        tree_out = []
        for drive in sorted(tree):
            node = tree[drive]
            children = []
            for name, c in sorted(node['children'].items(),
                                  key=lambda x: -x[1]['count'])[:40]:
                sub = [{'name': n, 'count': cc['count']}
                       for n, cc in sorted(c['children'].items(),
                                           key=lambda x: -x[1]['count'])[:40]]
                children.append({'name': name, 'count': c['count'],
                                 'children': sub})
            tree_out.append({'name': drive, 'count': node['count'],
                             'children': children})

        self._json({
            'by_ext': sorted(({'ext': k, 'count': v}
                              for k, v in by_ext.items()),
                             key=lambda x: -x['count']),
            'by_kind': sorted(({'kind': k, 'count': v}
                               for k, v in by_kind.items()),
                              key=lambda x: -x['count']),
            'tree': tree_out,
            'mtime_min': min(mtimes) if mtimes else None,
            'mtime_max': max(mtimes) if mtimes else None,
        })

    def _api_list(self, qs):
        """浏览模式：按筛选条件列出文件（不搜图）。"""
        limit = min(int(qs.get('limit', ['120'])[0]), 1000)
        offset = int(qs.get('offset', ['0'])[0])
        exts = qs.get('ext', [''])[0]
        kinds = qs.get('kind', [''])[0]
        dirs = qs.get('dir', [''])[0]
        sort = qs.get('sort', ['mtime'])[0]
        q = qs.get('q', [''])[0].strip()

        where, params = _build_where(exts, kinds, dirs, q)
        db = dbmod.DB(self.db_path)

        if q:
            # 有搜索词时按相关性排序，并且必须在 SQL 里排 ——
            # 在 Python 里对已 LIMIT 的结果排序，只能重排第一页，
            # 第二页开始就乱了。
            #
            # 三级排序：
            #   1. 文件名命中优先于仅路径命中 —— INSTR 不命中返回 0，
            #      所以用「INSTR = 0」当排序键，1 表示只匹配路径，排后面
            #   2. 名字里的命中位置越靠前越好
            #   3. 名字越短越精确
            order = ("(INSTR(LOWER(name), LOWER(?)) = 0) ASC, "
                     "CASE WHEN INSTR(LOWER(name), LOWER(?)) > 0 "
                     "THEN INSTR(LOWER(name), LOWER(?)) ELSE 999999 END ASC, "
                     "LENGTH(name) ASC, mtime DESC")
            order_params = [q, q, q]
        else:
            order = {'mtime': 'mtime DESC', 'mtime_asc': 'mtime ASC',
                     'size': 'size DESC', 'name': 'name ASC'}.get(
                sort, 'mtime DESC')
            order_params = []

        sql = ("SELECT id,path,name,stem,ext,kind,size,mtime,width,height,"
               "aspect,palette,thumb_path,extract_error,group_id "
               f"FROM files {where} ORDER BY {order} LIMIT ? OFFSET ?")
        rows = db.conn.execute(sql, params + order_params
                               + [limit, offset]).fetchall()
        total = db.conn.execute(
            f"SELECT COUNT(*) c FROM files {where}", params).fetchone()['c']
        db.close()

        items = [_row_dict(r) for r in rows]
        if q:
            for it in items:
                it['score'] = _name_score(it['name'], q)

        self._json({
            'total': total,
            'query': q,
            'items': items,
        })

    def _api_suggest(self, qs):
        """搜索建议：从文件名的词片段里找匹配，用于搜索框下拉。"""
        q = qs.get('q', [''])[0].strip()
        if not q or len(q) > 60:
            return self._json({'items': []})
        db = dbmod.DB(self.db_path)
        like = '%' + q.replace('%', '').replace('_', '') + '%'
        rows = db.conn.execute(
            "SELECT name, COUNT(*) c FROM files WHERE name LIKE ? "
            "GROUP BY name ORDER BY c DESC, LENGTH(name) ASC LIMIT 40",
            (like,)).fetchall()
        # 目录名也作为建议来源（用户常按项目名找）
        dirs = db.conn.execute(
            "SELECT path FROM files WHERE path LIKE ? LIMIT 400",
            (like,)).fetchall()
        db.close()

        seen = set()
        out = []
        for r in rows:
            base = r['name']
            if base not in seen:
                seen.add(base)
                out.append({'text': base, 'kind': 'file', 'count': r['c']})
        for r in dirs:
            p = r['path']
            parent = os.path.basename(os.path.dirname(p))
            if parent and q in parent and parent not in seen:
                seen.add(parent)
                out.append({'text': parent, 'kind': 'dir', 'count': 0})
        self._json({'items': out[:12]})


    def _api_search(self):
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b''
        qim = None
        payload = {}
        ctype = self.headers.get('Content-Type', '')

        if 'application/json' in ctype:
            try:
                payload = json.loads(raw.decode('utf-8'))
            except Exception:
                return self._json({'error': 'bad json'}, 400)

            # 两种用法：传 base64 图片，或传本地路径（右键菜单用）
            local = payload.get('path')
            b64 = payload.get('image')
            if local:
                if not os.path.isfile(local):
                    return self._json({'error': '文件不存在', 'path': local}, 404)
                try:
                    qim = Image.open(local)
                    qim.load()
                    qim = qim.convert('RGB')
                except Exception as e:
                    return self._json({'error': f'decode: {e}'}, 400)
            elif b64:
                if ',' in b64:
                    b64 = b64.split(',', 1)[1]
                try:
                    qim = Image.open(io.BytesIO(base64.b64decode(b64)))
                    qim.load()
                    qim = qim.convert('RGB')
                except Exception as e:
                    return self._json({'error': f'decode: {e}'}, 400)
        else:
            try:
                qim = Image.open(io.BytesIO(raw))
                qim.load()
                qim = qim.convert('RGB')
            except Exception:
                return self._json({'error': 'bad image'}, 400)

        if qim is None:
            return self._json({'error': 'no image'}, 400)

        idx, rows = _ensure_index(self.db_path)
        limit = int(payload.get('limit', 60))
        results = retrieve.search(idx, qim, limit=limit)
        results = retrieve.expand_groups(idx, results)

        out = []
        for r in results:
            matched = {
                'phash_dist': r['phash_dist'],
                'orb': round(r['orb'], 4) if r['orb'] is not None else None,
                'rotation': r['rotation'],
                'weak': r['weak'],
                'via': r.get('via', 'visual'),
            }
            out.append(_row_dict(r['row'], matched))

        self._json({'results': out, 'total': len(out),
                    'library': len(rows)})

    def _api_thumb(self, qs):
        fid = qs.get('id', [''])[0]
        if not fid.isdigit():
            return self._send(400, 'bad id', 'text/plain')
        db = dbmod.DB(self.db_path)
        r = db.conn.execute(
            "SELECT thumb_path FROM files WHERE id=?", (int(fid),)).fetchone()
        db.close()
        if not r or not r['thumb_path']:
            return self._serve_placeholder()
        # db_path 可能在任意位置，缩略图永远在库文件同级的 thumbs/
        data_dir = os.path.dirname(os.path.abspath(self.db_path))
        full = os.path.join(data_dir, 'thumbs', r['thumb_path'])
        # 库里的 thumb_path 只说明「索引时生成过」，不代表文件还在 ——
        # thumbs/ 被清掉后（换机器、手工删缓存）库里仍有记录。
        # 直接交给 _serve_file 会返回 404，前端就是一片破图，
        # 所以这里必须显式判断，缺图时退到占位图。
        if not os.path.isfile(full):
            return self._serve_placeholder()
        return self._serve_file(full, 'image/jpeg')

    def _api_preview(self, qs):
        """按文件路径返回预览图，供右键搜索时的拖图框显示缩略图。

        右键触发时前端拿到的是一个本地路径，浏览器不能直接读本地文件，
        所以由服务端渲染一张小图回传。优先用现成的缩略图缓存，
        没有再从原图重新生成。
        """
        p = qs.get('path', [''])[0]
        if not p or not os.path.isfile(p):
            return self._serve_placeholder()

        size = 96
        # 1) 库里已有缩略图，直接给
        db = dbmod.DB(self.db_path)
        row = db.conn.execute(
            "SELECT thumb_path FROM files WHERE path=?", (p,)).fetchone()
        db.close()
        if row and row['thumb_path']:
            data_dir = os.path.dirname(os.path.abspath(self.db_path))
            full = os.path.join(data_dir, 'thumbs', row['thumb_path'])
            if os.path.isfile(full):
                return self._serve_file(full, 'image/jpeg')

        # 2) 现场提取一次（右键的文件可能还没入库）
        try:
            from extract import extract as extract_file
            ext = os.path.splitext(p)[1].lstrip('.').lower()
            im, err, _kind = extract_file(p, ext)
            if im is not None:
                im = im.convert('RGB')
                im.thumbnail((size, size))
                buf = io.BytesIO()
                im.save(buf, 'JPEG', quality=88)
                return self._send(200, buf.getvalue(), 'image/jpeg')
        except Exception:
            pass

        return self._serve_placeholder()

    def _serve_placeholder(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160">'
               '<rect width="160" height="160" fill="#EDEBE6"/>'
               '<path d="M60 96l14-18 12 14 10-8 16 22H60z" fill="#C9C7C0"/>'
               '<circle cx="72" cy="62" r="7" fill="#C9C7C0"/></svg>')
        self._send(200, svg, 'image/svg+xml')

    def _api_file(self, qs):
        """详情面板的大图预览。

        位图直接给原图。PSD / AI / CDR / Office 这类浏览器显示不了的，
        按「画质优先」的顺序回退：

          1. 用各自的渲染器出 760px 长边的图（PSD 走 PIL 的合并图层，
             AI/PDF 走 pdfium，SVG 走 resvg）—— 这是能看出内容的那一档
          2. 索引时生成的缩略图（PSD 内嵌的只有 45×160，很糊但总能出）
          3. 灰图标占位

        **不能一上来就给缩略图**：这个面板是用来确认「这是不是我要的那张」
        的，给个 45×160 的小条或者灰图标，等于这条信息没了。

        渲染结果按 (路径, mtime, 大小) 缓存，同一张图反复点只算一次。
        """
        fid = qs.get('id', [''])[0]
        if not fid.isdigit():
            return self._send(400, 'bad id', 'text/plain')
        # big=1 是「看大图」模式：长边渲染到 1800px，竖版海报按宽度
        # 铺开后能看清字
        big = qs.get('big', ['0'])[0] == '1'
        db = dbmod.DB(self.db_path)
        r = db.conn.execute(
            "SELECT path, thumb_path FROM files WHERE id=?",
            (int(fid),)).fetchone()
        db.close()
        if not r:
            return self._serve_placeholder()

        path = r['path']
        ext = os.path.splitext(path)[1].lower()
        ctype = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                 '.png': 'image/png', '.webp': 'image/webp',
                 '.gif': 'image/gif', '.bmp': 'image/bmp'}.get(ext)
        if ctype and os.path.isfile(path):
            # 大图模式下位图也走缩放进 JPEG 的路径：
            # 原图可能 6000px / 20MB，直接给浏览器会卡好几秒，
            # 而 1800px 足够看清任何文字。
            if big:
                data = self._render_detail(path, ext, big=True)
                if data:
                    return self._send(200, data, 'image/jpeg')
            return self._serve_file(path, ctype)

        # Office 文档直接用索引时的缩略图。
        # 现渲染要走 Word/WPS 的 COM 导出 PDF，实测 8.4 秒 ——
        # 点一下等八秒，比看张 240×338 的小图难受得多。
        from extract import OFFICE_EXTS
        if ext.lstrip('.') in OFFICE_EXTS:
            tp = r['thumb_path']
            if tp:
                data_dir = os.path.dirname(os.path.abspath(self.db_path))
                full = os.path.join(data_dir, 'thumbs', tp)
                if os.path.isfile(full):
                    return self._serve_file(full, 'image/jpeg')

        data = self._render_detail(path, ext, big)
        if data:
            return self._send(200, data, 'image/jpeg')

        # 渲染不出来：退回索引时存的缩略图
        tp = r['thumb_path']
        if tp:
            data_dir = os.path.dirname(os.path.abspath(self.db_path))
            full = os.path.join(data_dir, 'thumbs', tp)
            if os.path.isfile(full):
                return self._serve_file(full, 'image/jpeg')

        return self._serve_placeholder()

    def _render_detail(self, path, ext, big=False):
        """把任意支持的格式渲染成 JPEG 字节。失败返回 None。

        big=True 用于「看大图」：长边给到 1800px，竖版海报按宽度铺开
        后能看清字。默认 760 是详情面板的尺寸，别动 —— 那个面板
        只有 344px 宽，渲染更大只是浪费 CPU。
        """
        if not os.path.isfile(path):
            return None
        try:
            st = os.stat(path)
            key = (path, int(st.st_mtime), st.st_size, big)
        except OSError:
            return None

        cache = _state['detail_cache']
        if key in cache:
            return cache[key]

        im = None
        try:
            im = _render_image(path, ext, DETAIL_BIG if big else DETAIL_MAX)
        except Exception:
            im = None

        if im is None:
            return None

        buf = io.BytesIO()
        im.convert('RGB').save(buf, 'JPEG', quality=90)
        data = buf.getvalue()

        if len(cache) >= _DETAIL_CACHE_LIMIT:
            cache.clear()          # 简单粗暴，够用
        cache[key] = data
        return data

    def _api_open(self):
        """在资源管理器中选中文件。"""
        length = int(self.headers.get('Content-Length') or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            return self._json({'error': 'bad json'}, 400)
        fid = payload.get('id')
        if not isinstance(fid, int):
            return self._json({'error': 'bad id'}, 400)
        db = dbmod.DB(self.db_path)
        r = db.conn.execute("SELECT path FROM files WHERE id=?",
                            (fid,)).fetchone()
        db.close()
        if not r:
            return self._json({'error': 'not found'}, 404)
        p = r['path']
        if not os.path.exists(p):
            return self._json({'error': 'file missing', 'path': p}, 404)
        try:
            if os.name == 'nt':
                import subprocess
                subprocess.Popen(['explorer', '/select,', os.path.normpath(p)])
            else:
                import subprocess
                subprocess.Popen(['xdg-open', os.path.dirname(p)])
        except Exception as e:
            return self._json({'error': str(e)}, 500)
        self._json({'ok': True, 'path': p})

    def _api_browse(self, qs):
        target = qs.get('path', [''])[0]
        if not target:
            drives = []
            if os.name == 'nt':
                import string
                for d in string.ascii_uppercase:
                    p = f'{d}:\\'
                    if os.path.isdir(p):
                        drives.append({'name': f'{d}:', 'path': p,
                                       'dir': True})
            return self._json({'path': '', 'items': drives})

        if not os.path.isdir(target):
            return self._json({'error': 'not a dir'}, 400)
        items = []
        try:
            with os.scandir(target) as it:
                for e in it:
                    if e.name.startswith('$') or e.name.startswith('.'):
                        continue
                    try:
                        is_dir = e.is_dir()
                    except OSError:
                        continue
                    if is_dir:
                        items.append({'name': e.name,
                                      'path': os.path.join(target, e.name),
                                      'dir': True})
        except PermissionError:
            return self._json({'error': 'permission'}, 403)
        items.sort(key=lambda x: x['name'].lower())
        parent = os.path.dirname(target.rstrip('\\/'))
        self._json({'path': target, 'parent': parent, 'items': items})

    # ---------- 索引管理 ----------

    def _api_roots(self):
        db = dbmod.DB(self.db_path)
        roots = [r['path'] for r in
                 db.conn.execute("SELECT path FROM roots ORDER BY path")]
        last = db.get_meta('last_index')
        total = db.stats()['total']
        # 没有 roots 记录时，从现有路径推断盘符，避免首次打开是空的
        if not roots and total:
            seen = set()
            for r in db.conn.execute("SELECT path FROM files LIMIT 5000"):
                drv = os.path.splitdrive(r['path'])[0]
                if drv:
                    seen.add(drv + os.sep)
            roots = sorted(seen)
        db.close()
        self._json({
            'roots': roots,
            'last_index': int(last) if last else None,
            'total': total,
            'data_dir': pathmod.data_dir(),
        })

    def _api_roots_set(self):
        length = int(self.headers.get('Content-Length') or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            return self._json({'error': 'bad json'}, 400)
        roots = payload.get('roots')
        if not isinstance(roots, list):
            return self._json({'error': 'roots must be a list'}, 400)
        roots = [str(r).strip() for r in roots if str(r).strip()]
        bad = [r for r in roots if not os.path.isdir(r)]
        if bad:
            return self._json({'error': '目录不存在', 'paths': bad}, 400)
        # 规范化：前端传来的可能带正斜杠或重复分隔符
        norm = []
        for r in roots:
            p = os.path.normpath(os.path.abspath(r))
            if p.endswith(':'):
                p += os.sep
            if p not in norm:
                norm.append(p)
        # 去掉被已有根目录覆盖的子目录。
        # E:\ 和 E:\素材\某项目 同时存在时，后者是多余的 ——
        # 扫 E:\ 已经包含它，留着只会让删除判定出现两个重叠范围。
        roots = _drop_covered(norm)
        db = dbmod.DB(self.db_path)
        db.conn.execute("DELETE FROM roots")
        for r in roots:
            db.add_root(r)
        db.close()
        self._json({'ok': True, 'roots': roots})

    def _api_index_status(self):
        job = _state.get('job')
        if job is None:
            db = dbmod.DB(self.db_path)
            total = db.stats()['total']
            last = db.get_meta('last_index')
            db.close()
            return self._json({
                'state': 'idle', 'running': False, 'total': total,
                'currently_indexed': total,
                'last_index': int(last) if last else None,
                'message': '', 'percent': 0,
            })
        snap = job.snapshot()
        snap['currently_indexed'] = _state.get('rows') and len(_state['rows']) or 0
        self._json(snap)

    def _api_index_start(self):
        from core.indexer import IndexJob
        job = _state.get('job')
        if job is not None and job.snapshot()['running']:
            return self._json({'error': '已在索引中', **job.snapshot()}, 409)

        # 优先用请求里传来的 roots，否则用库里记录的
        length = int(self.headers.get('Content-Length') or 0)
        roots = []
        if length:
            try:
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
                roots = payload.get('roots') or []
            except Exception:
                pass
        if not roots:
            db = dbmod.DB(self.db_path)
            roots = [r['path'] for r in
                     db.conn.execute("SELECT path FROM roots")]
            db.close()
        if not roots:
            return self._json({'error': '未设置索引目录'}, 400)

        job = IndexJob(self.db_path, roots)
        _state['job'] = job
        _state['index'] = None        # 索引会变，强制重载
        job.start()
        self._json({'ok': True, **job.snapshot()})

    def _api_index_cancel(self):
        job = _state.get('job')
        if job is None:
            return self._json({'error': '没有正在运行的作业'}, 400)
        job.cancel()
        self._json({'ok': True, **job.snapshot()})

    # ---------- 画面语义搜索 ----------

    def _api_semantic_status(self):
        """模型与向量库状态，前端用来决定显示「开始建立」还是「搜索框」。"""
        from core import faces
        from core import semantic as sem
        db = dbmod.DB(self.db_path)
        try:
            files = db.conn.execute(
                "SELECT COUNT(*) c FROM files").fetchone()['c']
            src_qm = ','.join('?' * len(EMBED_EXTS))
            src = db.conn.execute(
                f"SELECT COUNT(*) c FROM files WHERE ext IN ({src_qm})",
                EMBED_EXTS).fetchone()['c']
            done = db.count_embeddings(sem.MODEL_NAME)
            faced = db.count_faces(faces.MODEL_NAME)

            # 剩余时间按格式加权估算。实测单张成本差异极大：
            #   PSD/JPG/PDF/AI  约 0.28s（渲染 + 一次推理；小图 0.19s，
            #                   但 1GB 以上的 PSD 要 1s，均值被拉到 0.28）
            #   Word/DOCX/DOC   2~3s（要走 COM 导出 PDF 再渲染）
            # 只按总数量乘一个常数会算错三倍，用户看到「40 分钟」
            # 实际跑两小时会很不满。
            #
            # 用一条 GROUP BY 而不是逐格式 COUNT：这个接口被前端每 1.5 秒
            # 轮询一次，12 条带 NOT IN 子查询的 COUNT 会把 SQLite 拖慢，
            # 跟索引作业抢锁（实测吞吐从 216/分掉到 72/分）。
            cost_ms = {'psd': 280, 'jpg': 280, 'jpeg': 280, 'png': 280,
                       'webp': 280, 'docx': 2800, 'doc': 2200,
                       'pdf': 280, 'ai': 280, 'cdr': 280, 'eps': 280,
                       'svg': 280, 'tif': 280, 'tiff': 280,
                       'ppt': 280, 'pptx': 280}
            qm2 = ','.join('?' * len(EMBED_EXTS))
            remaining_cost = 0
            for r in db.conn.execute(
                    f"SELECT f.ext AS e, COUNT(*) AS c FROM files f "
                    f"WHERE f.ext IN ({qm2}) AND NOT EXISTS ("
                    f"  SELECT 1 FROM embeddings em WHERE em.file_id=f.id "
                    f"  AND em.model=?) GROUP BY f.ext",
                    (*EMBED_EXTS, sem.MODEL_NAME)):
                remaining_cost += r['c'] * cost_ms.get(r['e'], 190)
        finally:
            db.close()

        job = _state.get('embed_job')
        snap = job.snapshot() if job is not None else {
            'state': 'idle', 'running': False, 'done': 0, 'total': 0,
            'failed': 0, 'message': '', 'eta': None, 'error': None,
        }
        self._json({
            'available': sem.available(),
            'load_error': sem.load_error(),
            'model': sem.MODEL_NAME,
            'embedded': done,
            'total_files': files,
            'source_files': src,
            'ready': done > 0,
            # 人脸数据的独立进度。旧版向量没有这一项，
            # 前端据此提示「重新建立可补齐人物搜索」
            'faces_available': faces.available(),
            'faces_done': faced,
            'remaining_cost_ms': remaining_cost,
            'job': snap,
        })

    def _api_semantic_start(self):
        from core.embedder import EmbedJob
        job = _state.get('embed_job')
        if job is not None and job.snapshot()['running']:
            return self._json({'error': '已在建立中', **job.snapshot()}, 409)

        # 前端可以调 threads / delay 控制「温和程度」，默认 4 线程 + 每批歇 40ms
        threads, delay = 4, 0.04
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            try:
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
                threads = int(payload.get('threads') or threads)
                delay = float(payload.get('delay', delay))
            except Exception:
                pass

        job = EmbedJob(self.db_path, threads=threads, delay=delay)
        _state['embed_job'] = job
        job.start()
        self._json({'ok': True, **job.snapshot()})

    def _api_semantic_cancel(self):
        job = _state.get('embed_job')
        if job is None:
            return self._json({'error': '没有正在运行的作业'}, 400)
        job.cancel()
        self._json({'ok': True, **job.snapshot()})

    # ---------- 图里的文字（OCR） ----------

    def _api_text_status(self):
        """文字索引状态 + 可选的索引范围（按磁盘、按格式）。"""
        from core import ocr
        from core.ocrindex import COST_MS, OCR_EXTS

        db = dbmod.DB(self.db_path)
        try:
            done = db.count_texts(ocr.MODEL_NAME)
            total_all = db.conn.execute(
                "SELECT COUNT(*) c FROM files").fetchone()['c']

            # 一条 SQL 聚合出「盘 × 格式」的待办量。
            # 不能用「读全表再按 id 查是否已识别」的写法 —— 这个接口
            # 被前端每 1.5 秒轮询，18698 行读进内存再做集合运算会把
            # 索引作业拖慢（之前语义状态接口就是这样掉速的）。
            qm = ','.join('?' * len(OCR_EXTS))
            rows = db.conn.execute(
                f"SELECT substr(f.path,1,2) AS drv, f.ext AS ext, "
                f"COUNT(*) AS total, "
                f"SUM(CASE WHEN EXISTS (SELECT 1 FROM texts t "
                f"  WHERE t.file_id = f.id AND t.model = ?) THEN 1 ELSE 0 END) "
                f"  AS done "
                f"FROM files f WHERE f.ext IN ({qm}) "
                f"GROUP BY drv, ext",
                (ocr.MODEL_NAME, *OCR_EXTS)).fetchall()
        finally:
            db.close()

        slices = []
        for r in rows:
            left = r['total'] - (r['done'] or 0)
            slices.append({
                'drive': r['drv'], 'ext': r['ext'],
                'total': r['total'], 'done': r['done'] or 0, 'left': left,
                'cost_ms': left * COST_MS.get(r['ext'], 1500),
            })
        slices.sort(key=lambda x: (x['drive'], -x['total']))

        job = _state.get('ocr_job')
        snap = job.snapshot() if job is not None else {
            'state': 'idle', 'running': False, 'done': 0, 'total': 0,
            'failed': 0, 'message': '', 'eta': None, 'error': None,
        }

        self._json({
            'available': ocr.available(),
            'load_error': ocr.load_error(),
            'model': ocr.MODEL_NAME,
            'done': done,
            'total_files': total_all,
            'ready': done > 0,
            'slices': slices,
            'job': snap,
        })

    def _api_text_start(self):
        from core.ocrindex import OcrJob
        job = _state.get('ocr_job')
        if job is not None and job.snapshot()['running']:
            return self._json({'error': '已在建立中', **job.snapshot()}, 409)

        drives, exts, delay = [], [], 0.05
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            try:
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
                drives = payload.get('drives') or []
                exts = payload.get('exts') or []
                delay = float(payload.get('delay', delay))
            except Exception:
                pass

        job = OcrJob(self.db_path, drives=drives, exts=exts, delay=delay)
        _state['ocr_job'] = job
        job.start()
        self._json({'ok': True, **job.snapshot()})

    def _api_text_cancel(self):
        job = _state.get('ocr_job')
        if job is None:
            return self._json({'error': '没有正在运行的作业'}, 400)
        job.cancel()
        self._json({'ok': True, **job.snapshot()})

    def _api_text_search(self, qs):
        """按图里的文字搜图。GET /api/text/search?q=开业庆典

        多个词（空格分隔）是「与」关系。返回的每张图带 hit_texts
        字段（命中的文字段列表），前端用来显示片段和高亮。
        """
        from core import ocr

        q = (qs.get('q', [''])[0] or '').strip()
        limit = min(int(qs.get('limit', ['100'])[0] or 100), 300)
        if not q:
            return self._json({'error': '缺少查询词'}, 400)

        terms = [t for t in q.split() if t.strip()]

        db = dbmod.DB(self.db_path)
        try:
            rows = db.search_texts(terms, limit=limit * 3)
            if not rows:
                return self._json({'results': [], 'total': 0, 'query': q,
                                   'terms': terms})

            # 按文件聚合：一个文件可能有多段命中，全部带上给前端做高亮
            by_file = {}
            for r in rows:
                fid = r['fid']
                e = by_file.setdefault(fid, {'hits': [], 'n': r['n_hit'],
                                             'best': r['score'] or 0})
                if len(e['hits']) < 6:
                    e['hits'].append({'text': r['content'],
                                      'score': r['score'] or 0,
                                      'box': r['box']})

            fids = list(by_file)[:limit]
            qm = ','.join('?' * len(fids))
            meta = {r['id']: r for r in db.conn.execute(
                f"SELECT * FROM files WHERE id IN ({qm})", fids).fetchall()}

            out = []
            for fid in fids:
                r = meta.get(fid)
                if r is None:
                    continue
                d = _row_dict(r)
                e = by_file[fid]
                d['hit_texts'] = e['hits']
                d['hit_count'] = e['n']
                d['matched'] = {'via': 'text', 'count': e['n'],
                                'best': round(e['best'], 3)}
                out.append(d)
        finally:
            db.close()

        self._json({
            'results': out, 'total': len(out), 'query': q,
            'terms': terms, 'channel': 'text',
        })

    def _api_semantic_search(self, qs):
        """按画面内容搜图。GET /api/semantic/search?q=沙滩&limit=60

        查询词不限固定词表 —— 用中文 CLIP 编码，任何中英文都能量化。
        但实测长句效果差（多个概念平均成糊向量），所以先拆词
        （core/query.py），每个候选词各检索一次、合并排序。

        人物词的处理分两种：
          - 只搜人物（「人物」「模特」）：走人脸通道，按脸数排
          - 混合查询（「沙滩上的情侣」）：CLIP 场景 ∩ 有人脸
        """
        from core import faces, query as qmod
        from core import semantic as sem

        q = (qs.get('q', [''])[0] or '').strip()
        limit = min(int(qs.get('limit', ['60'])[0] or 60), 200)
        if not q:
            return self._json({'error': '缺少查询词'}, 400)

        plan = qmod.parse(q)

        db = dbmod.DB(self.db_path)
        try:
            mat, ids = db.load_embeddings(sem.MODEL_NAME)
            if mat is None:
                return self._json({
                    'error': '还没有建立画面索引',
                    'results': [], 'total': 0,
                }, 409)

            if plan['strict']:
                return self._search_by_faces(db, q, limit, plan)

            face_ids = (db.ids_with_faces(faces.MODEL_NAME, 1)
                        if plan['person'] else None)

            hits, terms_used = _multi_term_hits(sem, mat, ids, plan['terms'],
                                                limit=limit * 2)

            if face_ids is not None:
                # 混合查询：场景相似度 + 必须检出人脸。
                # 交集可能很少（实测「海边的女孩」只剩 5 条），
                # 如果少于 3 条就放开人脸限制，宁可多给几条也别空着。
                inter = [(i, s) for i, s in hits if i in face_ids]
                if len(inter) >= 3:
                    hits = inter
                    plan['note'] = '已限定画面里必须有人'
                else:
                    plan['note'] = '有人脸的匹配太少，已放宽限制'
            elif plan['person']:
                plan['note'] = ''

            out = _attach_rows(db, hits[:limit], 'semantic')
        finally:
            db.close()

        self._json({
            'results': out,
            'total': len(out),
            'query': q,
            'channel': 'faces' if plan['strict'] else 'semantic',
            'terms': terms_used,
            'note': plan.get('note', ''),
            'person': plan['person'],
        })

    def _search_by_faces(self, db, q, limit, plan=None):
        """按「画面里有人」搜。结果按人脸数从多到少排。

        排序依据不是相似度而是脸数：用户搜「人物」是想找主体是人物的图，
        一张 3 个模特的封面比一张角落里有个路人的风景图更符合意图。
        """
        from core import faces
        with_face = db.ids_with_faces(faces.MODEL_NAME, min_faces=1)
        if not with_face:
            return self._json({
                'results': [], 'total': 0, 'query': q, 'channel': 'faces',
                'message': '还没有人脸数据，重新建立画面索引即可补齐',
            })

        # 按脸数降序，脸数相同的按修改时间新的在前
        rows = db.conn.execute(
            "SELECT f.*, c.faces AS _faces FROM files f "
            "JOIN face_counts c ON c.file_id = f.id "
            "WHERE c.model=? AND c.faces > 0 "
            "ORDER BY c.faces DESC, f.mtime DESC LIMIT ?",
            (faces.MODEL_NAME, limit)).fetchall()

        out = []
        for r in rows:
            d = _row_dict(r)
            d['faces'] = r['_faces']
            d['matched'] = {'via': 'faces', 'faces': r['_faces']}
            out.append(d)
        self._json({'results': out, 'total': len(out), 'query': q,
                    'channel': 'faces'})


    def _api_shell_action(self):
        """右键菜单触发后，把动作记录下来供前端取用。

        请求体：
            {"action": "search", "path": "C:\\图.jpg", "result": {...}}
            {"action": "root",   "path": "E:\\"}
        前端轮询 /api/pending 拿到后决定怎么展示。
        """
        length = int(self.headers.get('Content-Length') or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            return self._json({'error': 'bad json'}, 400)
        # 带时间戳，前端靠它判断「这条我处理过了没有」
        payload['_seq'] = time.time()
        _state['pending'] = payload
        self._json({'ok': True, 'seq': payload['_seq']})

    def _api_shellmenu_status(self):
        from tools import shellmenu
        self._json(shellmenu.status())

    def _api_shellmenu(self):
        """安装 / 卸载右键菜单。请求体 {"action": "install"|"uninstall"}"""
        from tools import shellmenu
        length = int(self.headers.get('Content-Length') or 0)
        action = 'install'
        payload = {}
        if length:
            try:
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
                action = payload.get('action', 'install')
            except Exception:
                pass

        if not shellmenu.is_windows():
            return self._json({'error': '仅支持 Windows'}, 400)

        if action == 'install':
            ok, fail = shellmenu.install(
                include_file=payload.get('files', True),
                include_dir=payload.get('dir', True),
                include_drive=payload.get('drive', True),
            )
            self._json({'ok': True, 'installed': ok, 'failed': fail,
                        'status': shellmenu.status()})
        elif action == 'repair':
            # 和 install 做同一件事，但语义不同：install 是「从没装过，
            # 现在装」；repair 是「装了但指向的目标没了，重写一遍」。
            # 分开是为了让界面能给出准确的提示文案。
            ok, fail = shellmenu.repair()
            self._json({'ok': True, 'repaired': ok, 'failed': fail,
                        'status': shellmenu.status()})
        elif action == 'uninstall':
            n = shellmenu.uninstall()
            self._json({'ok': True, 'removed': n,
                        'status': shellmenu.status()})
        else:
            self._json({'error': '未知操作'}, 400)

    def _api_rebuild_groups(self):
        from core import pairing
        db = dbmod.DB(self.db_path)
        rows = db.all_files()
        db.clear_groups()
        by_name = pairing.pair_by_name(rows)
        by_hash = pairing.pair_by_hash(rows)
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        reasons = {}
        for (a, b), rs in list(by_name.items()) + list(by_hash.items()):
            union(a, b)
            reasons.setdefault(find(a), []).extend(rs)

        clusters = {}
        for node in list(parent.keys()):
            clusters.setdefault(find(node), []).append(node)
        by_id = {r['id']: r for r in rows}
        gid = 1
        made = 0
        for root, members in clusters.items():
            if len(members) < 2:
                continue
            srcs = [m for m in members if by_id[m]['kind'] == 'source'] or members
            exps = [m for m in members if by_id[m]['kind'] == 'export']
            rs = []
            for m in members:
                for rr in reasons.get(find(m), []):
                    if rr not in rs:
                        rs.append(rr)
            conf = 0.95 if any('同目录同主名' in r for r in rs) else 0.7
            db.set_group(gid, exps[0] if exps else members[0], srcs[0],
                         conf, rs, members)
            gid += 1
            made += 1
        db.commit()
        db.close()
        _state['index'] = None  # 强制重载
        self._json({'groups': made})


def _build_where(exts, kinds, dirs, q=''):
    clauses = []
    params = []
    if exts:
        exts = [e for e in exts.split(',') if e]
        if exts:
            clauses.append('ext IN (%s)' % ','.join('?' * len(exts)))
            params.extend(exts)
    if kinds:
        kinds = [k for k in kinds.split(',') if k]
        if kinds:
            clauses.append('kind IN (%s)' % ','.join('?' * len(kinds)))
            params.extend(kinds)
    if dirs:
        parts = [d for d in dirs.split('\n') if d.strip()]
        ors = []
        for d in parts:
            ors.append('path LIKE ?')
            params.append(d.strip().replace('/', '\\') + '%')
        if ors:
            clauses.append('(' + ' OR '.join(ors) + ')')
    if q:
        # 文件名 / 路径 双通道匹配；按空白拆词后要求全部命中（AND）
        for tok in q.split():
            tok = tok.replace('%', '').replace('_', '')
            if not tok:
                continue
            clauses.append('(name LIKE ? OR path LIKE ?)')
            params.extend(['%' + tok + '%', '%' + tok + '%'])
    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    return where, params


def _drop_covered(roots):
    """去掉被其他根目录包含的子目录。

    E:\\ 和 E:\\素材\\某项目 同时存在时，后者没有意义 ——
    扫 E:\\ 已经覆盖它。留着会让「删除判定」出现两个重叠范围，
    还会让扫描重复。
    """
    norm = []
    for r in roots:
        p = os.path.normcase(os.path.normpath(r))
        if not p.endswith(os.sep):
            p += os.sep
        norm.append((p, r))

    out = []
    for p, orig in norm:
        covered = False
        for q, _ in norm:
            if p == q:
                continue
            if p.startswith(q):
                covered = True
                break
        if not covered:
            out.append(orig)
    return out


def _name_score(name, q):
    """文件名搜索排序分。越高越靠前。

    - 完全等于查询：1000
    - 开头等于查询：800
    - 查询是完整词（前后是分隔符）：600
    - 包含查询：300 - 位置惩罚
    - 命中越长占比越高越靠前
    """
    n = name.lower()
    ql = q.lower().strip()
    if not ql:
        return 0
    stem = os.path.splitext(n)[0]
    if stem == ql:
        return 1000
    if stem.startswith(ql):
        return 800 + len(ql) / max(1, len(stem)) * 100
    # 按扩展名搜（如 ".psd"）
    if ql.startswith('.') and n.endswith(ql):
        return 700
    idx = n.find(ql)
    if idx < 0:
        return 0
    pos = 1 - min(idx, 20) / 40           # 越靠前越高，最多扣 0.5
    cover = len(ql) / max(1, len(stem))
    return 200 + pos * 100 + cover * 100


def run(db_path, port=8848, open_browser=True):
    Handler.db_path = db_path
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    db = dbmod.DB(db_path)          # 不存在会自动建空库（首次使用时界面引导建索引）
    n = db.stats()['total']
    db.close()
    print(f'索引 {n} 条记录')
    print(f'数据目录 {pathmod.data_dir()}')
    print(f'服务地址 http://127.0.0.1:{port}', flush=True)

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(
            f'http://127.0.0.1:{port}')).start()

    # 端口被占时自动往后找，避免「双击没反应」
    srv = None
    last_err = None
    for p in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', p), Handler)
            port = p
            break
        except OSError as e:
            last_err = e
            continue
    if srv is None:
        print(f'无法监听端口: {last_err}', flush=True)
        return 1

    if port != 8848:
        print(f'端口 8848 被占用，改用 {port}', flush=True)
        if open_browser:
            threading.Timer(0.6, lambda: webbrowser.open(
                f'http://127.0.0.1:{port}')).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n停止服务')
    finally:
        srv.server_close()
    return 0
