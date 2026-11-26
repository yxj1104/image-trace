'use strict';

/* ============================================================
   溯源 — 前端。所有控件自绘，不使用任何原生控件外观。
   ============================================================ */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const state = {
  stats: null,
  filters: null,
  results: [],
  total: 0,
  selected: null,
  queryImage: null,      // dataURL
  queryFile: null,
  mode: 'browse',        // browse | search
  view: 'grid',
  sort: 'mtime',
  q: '',
  suggestIdx: -1,
  suggestItems: [],
  qTimer: null,
  sel: {
    kind: '',
    ext: new Set(),
    dirs: new Set(),
    color: null,
    colorTol: 40,
    widthMin: null,
    widthMax: null,
    heightMin: null,
    heightMax: null,
    days: '',
  },
  treeOpen: new Set(),
  loading: false,
  roots: [],
  dataDir: '',
  lastIndex: null,
  job: null,          // 索引进度快照
  pollTimer: null,
  searchMode: 'name', // name | semantic —— 搜文件名还是搜画面内容
  semantic: null,     // 语义索引状态快照
  semanticPoll: null,
  semQuery: '',       // 最近一次画面搜索词，用来判断要不要重发请求
  semChannel: '',     // faces | semantic —— 上一次查询走的是哪条通道
  semTerms: [],       // 上一次查询被拆成了哪些词
  semNote: '',        // 后端给的提示（比如「有人脸的匹配太少，已放宽」）
  textStatus: null,   // OCR 索引状态快照
  textPoll: null,
};

/* ============================================================
   自绘 tooltip —— 替代原生 title
   ============================================================ */

const tipEl = $('#tip');
let tipTimer = null;

function bindTip(el, text) {
  if (!el || !text) return;
  // 幂等：同一元素重复绑定只更新文字，不叠加监听器
  if (el._tipText !== undefined) {
    el._tipText = text;
    return;
  }
  el._tipText = text;
  el.addEventListener('mouseenter', () => {
    if (!el._tipText) return;
    clearTimeout(tipTimer);
    tipTimer = setTimeout(() => {
      tipEl.textContent = el._tipText;
      tipEl.classList.add('on');
      const r = el.getBoundingClientRect();
      const t = tipEl.getBoundingClientRect();
      let x = r.left;
      let y = r.bottom + 7;
      if (x + t.width > innerWidth - 8) x = innerWidth - t.width - 8;
      if (x < 8) x = 8;
      if (y + t.height > innerHeight - 8) y = r.top - t.height - 7;
      tipEl.style.left = x + 'px';
      tipEl.style.top = y + 'px';
    }, 380);
  });
  el.addEventListener('mouseleave', () => {
    clearTimeout(tipTimer);
    tipEl.classList.remove('on');
  });
}

/* ============================================================
   自绘下拉选择
   ============================================================ */

function createSelect(host, options, value, onChange) {
  host.innerHTML = '';
  host.classList.add('select');
  const btn = document.createElement('button');
  btn.className = 'select-btn';
  const panel = document.createElement('div');
  panel.className = 'select-panel';

  function label() {
    const o = options.find(x => x.value === value);
    return o ? o.label : options[0].label;
  }

  function render() {
    btn.innerHTML = `<span></span>
      <svg class="caret" width="9" height="9" viewBox="0 0 10 10" fill="none">
        <path d="M2 4l3 3 3-3" stroke="currentColor" stroke-width="1.3"
              stroke-linecap="round" stroke-linejoin="round"/>
      </svg>`;
    btn.firstChild.textContent = label();
    panel.innerHTML = '';
    options.forEach(o => {
      const it = document.createElement('div');
      it.className = 'select-item' + (o.value === value ? ' on' : '');
      it.innerHTML = `<svg class="select-check" width="11" height="11"
          viewBox="0 0 12 12" fill="none">
          <path d="M2 6.2l2.6 2.6L10 3.4" stroke="currentColor"
                stroke-width="1.6" stroke-linecap="round"
                stroke-linejoin="round"/></svg><span></span>`;
      it.lastChild.textContent = o.label;
      it.addEventListener('click', e => {
        e.stopPropagation();
        value = o.value;
        host.classList.remove('open');
        render();
        onChange(o.value);
      });
      panel.appendChild(it);
    });
  }

  btn.addEventListener('click', e => {
    e.stopPropagation();
    const was = host.classList.contains('open');
    closeAllSelects();
    if (!was) host.classList.add('open');
  });

  host.appendChild(btn);
  host.appendChild(panel);
  render();
  return { set(v) { value = v; render(); } };
}

function closeAllSelects() {
  $$('.select.open').forEach(s => s.classList.remove('open'));
}

document.addEventListener('click', closeAllSelects);

/* ============================================================
   自绘滑块（颜色容差、RGB 调节用）
   ============================================================ */

function createSlider(host, opts) {
  const { min = 0, max = 255, value = 0, track = null, onInput } = opts;
  host.className = 'slider';
  const tr = document.createElement('div');
  tr.className = 'slider-track';
  if (track) tr.style.background = track;
  const th = document.createElement('div');
  th.className = 'slider-thumb';
  host.appendChild(tr);
  host.appendChild(th);

  let v = value;
  function paint() {
    const p = (v - min) / (max - min);
    th.style.left = (p * 100) + '%';
  }
  function set(nv, fire = true) {
    v = Math.max(min, Math.min(max, Math.round(nv)));
    paint();
    if (fire && onInput) onInput(v);
  }
  function fromEvent(e) {
    const r = tr.getBoundingClientRect();
    const x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
    set(min + (x / r.width) * (max - min));
  }

  let dragging = false;
  const down = e => {
    dragging = true;
    fromEvent(e);
    e.preventDefault();
  };
  const move = e => { if (dragging) fromEvent(e); };
  const up = () => { dragging = false; };

  host.addEventListener('mousedown', down);
  host.addEventListener('touchstart', down, { passive: false });
  window.addEventListener('mousemove', move);
  window.addEventListener('touchmove', move, { passive: false });
  window.addEventListener('mouseup', up);
  window.addEventListener('touchend', up);

  paint();
  return { get: () => v, set };
}

/* ============================================================
   自绘颜色选择面板
   ============================================================ */

const PRESET_COLORS = [
  '#E24B4A', '#D85A30', '#EF9F27', '#C0A32C', '#639922', '#1D9E75',
  '#2F6E5E', '#378ADD', '#534AB7', '#993556', '#5F5E5A', '#1F1E1C',
];

function hexToRgb(h) {
  h = h.replace('#', '');
  return [parseInt(h.slice(0, 2), 16),
          parseInt(h.slice(2, 4), 16),
          parseInt(h.slice(4, 6), 16)];
}

function rgbToHex(r, g, b) {
  return '#' + [r, g, b].map(x =>
    Math.max(0, Math.min(255, Math.round(x))).toString(16).padStart(2, '0')
  ).join('').toUpperCase();
}

function openColorPicker(anchor, cur, onPick) {
  const ov = $('#overlay');
  const md = $('#modal');
  let rgb = cur ? hexToRgb(cur) : [47, 110, 94];

  md.innerHTML = `
    <div class="modal-head">
      <div class="modal-title">选择颜色</div>
      <div class="modal-desc">按主色调筛选。容差越大匹配范围越宽。</div>
    </div>
    <div class="modal-body">
      <div class="cp-preview" id="cpPrev"></div>
      <div class="cp-sliders">
        <div class="cp-slider-row">
          <span class="cp-slider-label">R</span><div id="slR"></div>
        </div>
        <div class="cp-slider-row">
          <span class="cp-slider-label">G</span><div id="slG"></div>
        </div>
        <div class="cp-slider-row">
          <span class="cp-slider-label">B</span><div id="slB"></div>
        </div>
      </div>
      <div class="chips" id="cpPresets" style="margin-bottom:12px"></div>
      <div class="sect-title" style="margin-bottom:6px">容差</div>
      <div class="cp-slider-row">
        <span class="cp-slider-label">±</span><div id="slTol"></div>
        <span class="mono" id="tolVal" style="width:26px;text-align:right"></span>
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" id="cpNone">清除颜色</button>
      <button class="btn primary" id="cpOk">应用</button>
    </div>`;

  const prev = $('#cpPrev', md);
  const tolVal = $('#tolVal', md);

  function refresh() {
    prev.style.background = rgbToHex(...rgb);
    const hex = rgbToHex(...rgb);
    ['R', 'G', 'B'].forEach((c, i) => {
      const el = $(`#sl${c}`, md);
      if (el) el.querySelector('.slider-track').style.background =
        `linear-gradient(90deg, ${rgbToHex(...rgb.map((x, j) => j === i ? 0 : x))},
         ${rgbToHex(...rgb.map((x, j) => j === i ? 255 : x))})`;
    });
  }

  const slR = createSlider($('#slR', md), {
    min: 0, max: 255, value: rgb[0],
    onInput: v => { rgb[0] = v; refresh(); },
  });
  const slG = createSlider($('#slG', md), {
    min: 0, max: 255, value: rgb[1],
    onInput: v => { rgb[1] = v; refresh(); },
  });
  const slB = createSlider($('#slB', md), {
    min: 0, max: 255, value: rgb[2],
    onInput: v => { rgb[2] = v; refresh(); },
  });
  const slTol = createSlider($('#slTol', md), {
    min: 10, max: 120, value: state.sel.colorTol,
    onInput: v => { tolVal.textContent = v; state.sel.colorTol = v; },
  });
  tolVal.textContent = state.sel.colorTol;

  const presets = $('#cpPresets', md);
  PRESET_COLORS.forEach(hex => {
    const d = document.createElement('div');
    d.className = 'color-dot';
    d.style.background = hex;
    d.addEventListener('click', () => {
      rgb = hexToRgb(hex);
      slR.set(rgb[0]); slG.set(rgb[1]); slB.set(rgb[2]);
      refresh();
    });
    presets.appendChild(d);
  });

  refresh();
  ov.classList.add('open');

  $('#cpNone', md).onclick = () => {
    ov.classList.remove('open');
    onPick(null);
  };
  $('#cpOk', md).onclick = () => {
    ov.classList.remove('open');
    onPick(rgbToHex(...rgb));
  };
  ov.onclick = e => {
    if (e.target === ov) ov.classList.remove('open');
  };
}

/* ============================================================
   筛选面板渲染
   ============================================================ */

function renderSide() {
  const f = state.filters;
  const host = $('#side-scroll');
  host.innerHTML = '';

  const kinds = [
    { v: '', l: '全部' },
    { v: 'source', l: '源文件' },
    { v: 'export', l: '导出图' },
  ];

  const g1 = group('类型');
  kinds.forEach(k => {
    const cnt = f ? (f.by_kind.find(x => x.kind === k.v)?.count ??
      f.by_kind.reduce((s, x) => s + x.count, 0)) : 0;
    g1.body.appendChild(radioRow(
      k.l, state.sel.kind === k.v, k.v ? cnt : null,
      () => { state.sel.kind = k.v; renderSide(); applyFilters(); }));
  });
  host.appendChild(g1.el);

  const g2 = group('格式');
  const chips = document.createElement('div');
  chips.className = 'chips';
  (f ? f.by_ext : []).forEach(x => {
    // 白名单决定筛选器里显示哪些格式。加新格式时这里必须同步，
    // 否则后端能索引、界面筛不出来（Office 就这样漏过一次）
    if (!['psd', 'ai', 'cdr', 'pdf', 'png', 'jpg', 'jpeg', 'webp',
          'svg', 'eps', 'tif', 'gif',
          'doc', 'docx', 'docm', 'ppt', 'pptx', 'pptm',
          'xls', 'xlsx', 'xlsm'].includes(x.ext)) return;
    const c = document.createElement('div');
    c.className = 'chip' + (state.sel.ext.has(x.ext) ? ' on' : '');
    c.innerHTML = `<span>${x.ext.toUpperCase()}</span>
                   <span class="chip-count">${x.count}</span>`;
    c.onclick = () => {
      state.sel.ext.has(x.ext) ? state.sel.ext.delete(x.ext)
                               : state.sel.ext.add(x.ext);
      renderSide(); applyFilters();
    };
    chips.appendChild(c);
  });
  g2.body.appendChild(chips);
  host.appendChild(g2.el);

  const g3 = group('目录');
  if (f && f.tree.length) {
    f.tree.forEach(drive => {
      const node = treeNode(drive.name, drive.count, 0, 'drive', drive.name);
      g3.body.appendChild(node);
      if (state.treeOpen.has(drive.name)) {
        drive.children.forEach(c => {
          const key = drive.name + '|' + c.name;
          g3.body.appendChild(
            treeNode(c.name, c.count, 1, 'dir', key, drive.name));
          if (state.treeOpen.has(key)) {
            (c.children || []).forEach(cc => {
              const k2 = key + '|' + cc.name;
              g3.body.appendChild(
                treeNode(cc.name, cc.count, 2, 'leaf', k2));
            });
          }
        });
      }
    });
  }
  host.appendChild(g3.el);

  const g4 = group('时间');
  const dayOpts = [
    { v: '', l: '不限' }, { v: '7', l: '近 7 天' },
    { v: '30', l: '近 30 天' }, { v: '365', l: '近一年' },
  ];
  const dl = document.createElement('div');
  dl.className = 'chips';
  dayOpts.forEach(o => {
    const c = document.createElement('div');
    c.className = 'chip' + (state.sel.days === o.v ? ' on' : '');
    c.textContent = o.l;
    c.onclick = () => {
      state.sel.days = o.v; renderSide(); applyFilters();
    };
    dl.appendChild(c);
  });
  g4.body.appendChild(dl);
  host.appendChild(g4.el);

  const g5 = group('尺寸');
  const wrap = document.createElement('div');
  wrap.style.display = 'flex';
  wrap.style.flexDirection = 'column';
  wrap.style.gap = '7px';
  wrap.innerHTML = `
    <div class="range-row">
      <div class="stepper" data-k="widthMin">
        <button class="stepper-btn" data-d="-1">
          <svg width="7" height="7" viewBox="0 0 8 8"><path d="M1 4h6"
            stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        </button>
        <input type="number" placeholder="宽" value="${state.sel.widthMin ?? ''}">
        <button class="stepper-btn" data-d="1">
          <svg width="7" height="7" viewBox="0 0 8 8"><path d="M4 1v6M1 4h6"
            stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        </button>
      </div>
      <span class="range-sep">–</span>
      <div class="stepper" data-k="widthMax">
        <button class="stepper-btn" data-d="-1">
          <svg width="7" height="7" viewBox="0 0 8 8"><path d="M1 4h6"
            stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        </button>
        <input type="number" placeholder="宽" value="${state.sel.widthMax ?? ''}">
        <button class="stepper-btn" data-d="1">
          <svg width="7" height="7" viewBox="0 0 8 8"><path d="M4 1v6M1 4h6"
            stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        </button>
      </div>
    </div>
    <div class="range-row">
      <div class="stepper" data-k="heightMin">
        <button class="stepper-btn" data-d="-1">
          <svg width="7" height="7" viewBox="0 0 8 8"><path d="M1 4h6"
            stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        </button>
        <input type="number" placeholder="高" value="${state.sel.heightMin ?? ''}">
        <button class="stepper-btn" data-d="1">
          <svg width="7" height="7" viewBox="0 0 8 8"><path d="M4 1v6M1 4h6"
            stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        </button>
      </div>
      <span class="range-sep">–</span>
      <div class="stepper" data-k="heightMax">
        <button class="stepper-btn" data-d="-1">
          <svg width="7" height="7" viewBox="0 0 8 8"><path d="M1 4h6"
            stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        </button>
        <input type="number" placeholder="高" value="${state.sel.heightMax ?? ''}">
        <button class="stepper-btn" data-d="1">
          <svg width="7" height="7" viewBox="0 0 8 8"><path d="M4 1v6M1 4h6"
            stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
        </button>
      </div>
    </div>
    <div class="chips" style="margin-top:2px">
      ${[['竖版', 1, 1.2], ['横版', 1.2, 99], ['方图', .92, 1.08]]
        .map(([n, a, b]) => `<div class="chip" data-ar="${a},${b}">${n}</div>`)
        .join('')}
    </div>`;

  $$('.stepper', wrap).forEach(st => {
    const k = st.dataset.k;
    const inp = st.querySelector('input');
    const step = k.startsWith('width') ? 50 : 50;
    st.querySelectorAll('.stepper-btn').forEach(b => {
      b.onclick = () => {
        const cur = parseInt(inp.value) || 0;
        const nv = Math.max(0, cur + parseInt(b.dataset.d) * step);
        inp.value = nv || '';
        state.sel[k] = nv || null;
        applyFilters();
      };
    });
    inp.addEventListener('input', () => {
      const v = parseInt(inp.value);
      state.sel[k] = isNaN(v) ? null : v;
    });
    inp.addEventListener('change', applyFilters);
  });
  $$('[data-ar]', wrap).forEach(b => {
    b.onclick = () => {
      const [lo, hi] = b.dataset.ar.split(',').map(Number);
      if (lo < 1.1 && hi > 1.1) {
        state.sel.widthMin = null; state.sel.widthMax = null;
      }
      state.sel.aspectLo = lo; state.sel.aspectHi = hi;
      applyFilters();
      $$('[data-ar]', wrap).forEach(x => x.classList.remove('on'));
      b.classList.add('on');
    };
  });
  g5.body.appendChild(wrap);
  host.appendChild(g5.el);

  const g6 = group('颜色');
  const btn = document.createElement('button');
  btn.className = 'color-picker-btn';
  const sw = document.createElement('span');
  sw.className = 'cp-swatch';
  sw.style.background = state.sel.color || 'transparent';
  if (!state.sel.color) sw.style.boxShadow =
    'inset 0 0 0 1px var(--line-input)';
  btn.appendChild(sw);
  const lbl = document.createElement('span');
  lbl.textContent = state.sel.color || '不限颜色';
  btn.appendChild(lbl);
  btn.onclick = () => openColorPicker(btn, state.sel.color, hex => {
    state.sel.color = hex;
    renderSide();
    applyFilters();
  });
  g6.body.appendChild(btn);
  host.appendChild(g6.el);

  const g7 = group('显示');
  const sw2 = document.createElement('div');
  sw2.className = 'switch' + (state.sel.hideNoThumb ? ' on' : '');
  sw2.innerHTML = `<div class="switch-track"><div class="switch-knob"></div></div>
                   <span class="switch-label">隐藏无预览的文件</span>`;
  sw2.onclick = () => {
    state.sel.hideNoThumb = !state.sel.hideNoThumb;
    renderSide(); applyFilters();
  };
  g7.body.appendChild(sw2);
  host.appendChild(g7.el);
}

function group(title) {
  const el = document.createElement('div');
  el.className = 'fgroup';
  el.innerHTML = `<div class="fgroup-head">
      <span class="fgroup-title">${title}</span>
    </div>`;
  const body = document.createElement('div');
  el.appendChild(body);
  return { el, body };
}

function radioRow(label, on, count, onClick) {
  const r = document.createElement('div');
  r.className = 'radio-row' + (on ? ' on' : '');
  r.innerHTML = `<span class="radio-dot"></span>
                 <span class="radio-label"></span>
                 ${count != null ? `<span class="radio-count">${count}</span>` : ''}`;
  r.querySelector('.radio-label').textContent = label;
  r.onclick = onClick;
  return r;
}

function treeNode(name, count, depth, kind, key, parentKey) {
  const el = document.createElement('div');
  const isOpen = state.treeOpen.has(key);
  const hasChild = kind === 'drive';
  const on = state.sel.dirs.has(key);
  el.className = 'tree-node' + (on ? ' on' : '');
  el.style.paddingLeft = (4 + depth * 11) + 'px';
  el.innerHTML = `
    <span class="tree-twist ${hasChild ? (isOpen ? 'open' : '') :
      (kind === 'leaf' ? 'leaf' : '')}">
      ${hasChild || kind === 'dir' ? `<svg width="7" height="7"
        viewBox="0 0 8 8"><path d="M2.5 1.5L5.5 4l-3 2.5" fill="none"
        stroke="currentColor" stroke-width="1.3" stroke-linecap="round"
        stroke-linejoin="round"/></svg>` : ''}
    </span>
    <span class="tree-name"></span>
    <span class="tree-count">${count}</span>`;
  el.querySelector('.tree-name').textContent = name;
  bindTip(el, key.replace(/\|/g, '\\'));

  // 三角箭头单独响应展开/折叠，点名字才是筛选
  // （不能用 e.offsetX 判断区域：offsetX 相对的是实际命中的子元素，
  //   点在文字上时 offsetX 会重新从文字左边缘算起，永远大于阈值）
  const tw = el.querySelector('.tree-twist');
  if (!tw.classList.contains('leaf')) {
    tw.style.cursor = 'pointer';
    tw.addEventListener('click', e => {
      e.stopPropagation();
      state.treeOpen.has(key) ? state.treeOpen.delete(key)
                              : state.treeOpen.add(key);
      renderSide();
    });
  }

  el.onclick = () => {
    state.sel.dirs.has(key) ? state.sel.dirs.delete(key)
                            : state.sel.dirs.add(key);
    // 选中盘符/目录时顺手展开，省一次点击
    if (!state.treeOpen.has(key)) state.treeOpen.add(key);
    renderSide();
    applyFilters();
  };
  return el;
}

/* ============================================================
   结果渲染
   ============================================================ */

function fmtSize(n) {
  if (!n) return '';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB';
  return (n / 1024 / 1024).toFixed(1) + ' MB';
}

function fmtDate(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-` +
         `${String(d.getDate()).padStart(2, '0')}`;
}

/** 带时分，只在悬停提示里用 —— 卡片上放不下。 */
function fmtDateFull(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return fmtDate(ts) + ' ' +
    `${String(d.getHours()).padStart(2, '0')}:` +
    `${String(d.getMinutes()).padStart(2, '0')}`;
}

/**
 * 卡片上用的日期，统一 `YYYY-MM-DD`。
 *
 * 试过「同年只显示月-日、跨年才补年份」的紧凑写法，省宽度但视觉上不统一：
 * 一列卡片里混着 `2025-01` 和 `01-14`，长度不同，右对齐后左边界参差，
 * 反而不便于扫视对比。日期的用途就是比新旧，格式一致比省几个像素重要。
 */
function fmtDateShort(ts) {
  return fmtDate(ts);
}

const KIND_LABEL = { source: '源文件', export: '导出图', other: '其他' };

/**
 * 卡片副标题：左边说明文字，右边日期。
 *
 * 日期单独占一块并靠右，不加分隔符、不跟前面的文字挤成一行 ——
 * 一列卡片横向对齐后，扫一眼就能看出哪个是新的，不用点开详情。
 * 左半边仍然省略号截断，日期永远完整显示。
 */
function setCardSub(el, text, mtime) {
  if (!el) return;
  el.textContent = '';
  const left = document.createElement('span');
  left.className = 'card-sub-t';
  left.textContent = text || '';
  el.appendChild(left);

  if (mtime) {
    const d = document.createElement('span');
    d.className = 'card-sub-d';
    d.textContent = fmtDateShort(mtime);
    el.appendChild(d);
    bindTip(d, fmtDateFull(mtime));
  }
  if (text) bindTip(left, text);
}

/**
 * 命中文字列表。只在图中文搜索结果里出现。
 *
 * 把命中的词用 <mark> 高亮出来 —— 一张海报命中三段文字，用户需要
 * 一眼看出是哪几个字匹配上了，而不是逐段比对。
 */
function buildHitList(it) {
  const hits = (it && it.hit_texts) || [];
  if (!hits.length) return null;

  const sect = document.createElement('div');
  sect.className = 'sect';

  const title = document.createElement('div');
  title.className = 'sect-title';
  title.textContent = `图中文字（命中 ${it.hit_count || hits.length} 段）`;
  sect.appendChild(title);

  const terms = (state.semQuery || '').split(/\s+/).filter(Boolean);

  hits.forEach((h, i) => {
    const row = document.createElement('div');
    row.className = 'hit-text';
    row.dataset.idx = String(i);
    // 序号和图上框的序号对应。只有前 4 段在图上画了框，
    // 序号也只给这 4 段加，避免误导（第 5 段没有框）。
    if (i < 4) {
      const no = document.createElement('span');
      no.className = 'hit-no';
      no.textContent = String(i + 1);
      row.appendChild(no);
    }
    const text = h.text || '';
    if (terms.length) {
      // 用 split 保留分隔符的方式拼接，避免 innerHTML 注入
      const re = new RegExp('(' + terms.map(escapeRe).join('|') + ')', 'gi');
      text.split(re).forEach(part => {
        if (!part) return;
        if (terms.some(t => t.toLowerCase() === part.toLowerCase())) {
          const m = document.createElement('mark');
          m.textContent = part;
          row.appendChild(m);
        } else {
          row.appendChild(document.createTextNode(part));
        }
      });
    } else {
      row.textContent = text;
    }
    sect.appendChild(row);
  });

  return sect;
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function parentDir(path) {
  if (!path) return '';
  const parts = path.replace(/\//g, '\\').split('\\').filter(Boolean);
  if (parts.length < 2) return path;
  return parts[parts.length - 2] || '';
}

/**
 * 在预览图上叠出命中文字的框。
 *
 * 坐标是 OCR 时归一化的 0~1 相对值（见 core/ocr.py）。用百分比定位，
 * 容器随图片自适应，不依赖图片的实际像素尺寸。
 *
 * 只画前 4 个框：一张海报命中十几段文字时全画出来会把画面盖住。
 *
 * **每个框带序号**，和下方文字列表的序号对应。这是必需的 ——
 * 竖版长海报在 344px 面板里只能显示 121px 宽，一行字高不到 6px，
 * 框再准也看不出框的是哪一段。序号是唯一能把「框」和「文字」
 * 对上的线索。
 */
/**
 * 全屏看大图，并把命中文字的框叠上去。
 *
 * 详情面板只有 344px 宽，竖版海报在里面缩成 121px，一行字的框不到 5px 高，
 * 再准也看不清框的是哪几个字。大图模式是唯一能真正验证「OCR 定位对不对」
 * 的地方 —— 用户也常用它确认「这张图是不是我要的」。
 */
function openBigImage(it) {
  const ov = $('#overlay');
  const md = $('#modal');
  md.classList.add('big');
  md.innerHTML = `
    <div class="big-head">
      <span class="big-name"></span>
      <button class="btn ghost sm" id="bigClose">关闭</button>
    </div>
    <div class="big-body">
      <div class="preview-wrap" id="bigWrap">
        <img src="/api/file?id=${it.id}&big=1" alt="">
        <div class="hit-boxes" id="bigBoxes"></div>
      </div>
    </div>`;
  md.querySelector('.big-name').textContent = it.name;

  const hits = (it.hit_texts || []).filter(h => h.box);
  const host = $('#bigBoxes');
  hits.forEach((h, i) => {
    let box = h.box;
    if (typeof box === 'string') {
      try { box = JSON.parse(box); } catch (e) { return; }
    }
    if (!Array.isArray(box) || box.length < 4) return;
    const xs = box.map(p => p[0]);
    const ys = box.map(p => p[1]);
    const el = document.createElement('div');
    el.className = 'hit-box' + (i === 0 ? ' first' : '');
    el.style.left = (Math.min(...xs) * 100) + '%';
    el.style.top = (Math.min(...ys) * 100) + '%';
    el.style.width = Math.max(0.6, (Math.max(...xs) - Math.min(...xs)) * 100) + '%';
    el.style.height = Math.max(0.4, (Math.max(...ys) - Math.min(...ys)) * 100) + '%';
    bindTip(el, (i + 1) + '. ' + (h.text || ''));
    const num = document.createElement('span');
    num.className = 'hit-num';
    num.textContent = String(i + 1);
    el.appendChild(num);
    host.appendChild(el);
  });

  ov.classList.add('open');
  $('#bigClose').onclick = () => {
    ov.classList.remove('open');
    md.classList.remove('big', 'tall');
  };
  // 点遮罩关闭（点图片本身不关）
  ov.onclick = e => {
    if (e.target === ov) {
      ov.classList.remove('open');
      md.classList.remove('big', 'tall');
    }
  };

  // 竖版长海报（1181×4098 这种）如果按高度装进视口，宽度只剩 200 多像素，
  // 等于没放大。改成按宽度铺开、纵向滚动 —— 这才是看长图的正常方式。
  const img = md.querySelector('#bigWrap img');
  img.onload = () => {
    if (!img.naturalWidth || !img.naturalHeight) return;
    const ratio = img.naturalWidth / img.naturalHeight;
    if (ratio < 0.8) {
      md.classList.add('tall');
      md.querySelector('.big-body').classList.add('scroll');
    }
  };
}

function drawHitBoxes(it) {  const host = $('#hitBoxes');
  if (!host) return;
  host.innerHTML = '';

  const hits = (it && it.hit_texts) || [];
  if (!hits.length) {
    host.style.display = 'none';
    return;
  }
  host.style.display = '';

  hits.slice(0, 4).forEach((h, i) => {
    let box = h.box;
    if (typeof box === 'string') {
      try { box = JSON.parse(box); } catch (e) { box = null; }
    }
    if (!Array.isArray(box) || box.length < 4) return;

    const xs = box.map(p => p[0]);
    const ys = box.map(p => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const y0 = Math.min(...ys), y1 = Math.max(...ys);

    const el = document.createElement('div');
    el.className = 'hit-box' + (i === 0 ? ' first' : '');
    el.dataset.idx = String(i);
    el.style.left = (x0 * 100) + '%';
    el.style.top = (y0 * 100) + '%';
    el.style.width = Math.max(6, (x1 - x0) * 100) + '%';
    el.style.height = Math.max(4, (y1 - y0) * 100) + '%';
    bindTip(el, (i + 1) + '. ' + (h.text || ''));

    const num = document.createElement('span');
    num.className = 'hit-num';
    num.textContent = String(i + 1);
    el.appendChild(num);

    host.appendChild(el);
  });

  // 列表项悬停时把对应的框提亮，反向也能认出是哪一段
  const rows = document.querySelectorAll('.hit-text[data-idx]');
  rows.forEach(r => {
    r.onmouseenter = () => {
      const b = host.querySelector(`.hit-box[data-idx="${r.dataset.idx}"]`);
      if (b) b.classList.add('on');
    };
    r.onmouseleave = () => {
      const b = host.querySelector(`.hit-box[data-idx="${r.dataset.idx}"]`);
      if (b) b.classList.remove('on');
    };
  });
}

function renderResults() {
  const grid = $('#grid');
  const empty = $('#empty');
  // 统一在这里过一遍筛选，图像搜索结果也能被左侧筛选器收窄
  const items = state.results.filter(passLocal);

  grid.className = 'grid' + (state.view === 'list' ? ' list-mode' : '');
  grid.innerHTML = '';

  $('#main-count').textContent = items.length ?
    `${items.length}${state.total > items.length ?
      ' / ' + state.total : ''} 个` : '';

  if (!items.length) {
    grid.style.display = 'none';
    empty.style.display = 'flex';
    let t = '这里还没有内容';
    let d = '把图片拖到窗口任意位置，或按 Ctrl+V 粘贴，即可找出它的源文件。';
    if (state.mode === 'search') {
      t = '没有找到匹配的文件';
      d = '换一张更完整、更接近原图的图试试，或放宽左侧的筛选条件。';
    } else if (state.mode === 'semantic') {
      t = `画面里没有找到「${state.semQuery}」`;
      d = '换个说法试试（比如「海边」而不是「沙滩」），或者这个词的画面库本身就没有。';
    } else if (state.mode === 'text') {
      t = `没有图印着「${state.semQuery}」`;
      d = 'OCR 只认得清印刷体和标题字，手写体、艺术字、极小的字会漏。也可能是这个范围还没建立文字索引。';
    } else if (state.q) {
      t = `没有找到包含「${state.q}」的文件`;
      d = '试试少输入几个字，或改用空格分隔的关键词；也可以只搜项目名。';
    }
    empty.innerHTML = `<div class="empty-title"></div>
      <div class="empty-desc"></div>`;
    empty.querySelector('.empty-title').textContent = t;
    empty.querySelector('.empty-desc').textContent = d;
    return;
  }

  grid.style.display = '';
  empty.style.display = 'none';

  const frag = document.createDocumentFragment();
  items.forEach((it, i) => {
    const c = document.createElement('div');
    c.className = 'card' + (state.selected === it.id ? ' on' : '');
    c.dataset.id = it.id;

    // 缩略图接口在没有缓存时自己返回占位图，不需要前端判断
    const thumb = `<img src="/api/thumb?id=${it.id}" loading="lazy" alt="">`;

    let score = '';
    if (it.via === 'paired') {
      score = `<span class="card-score paired">配对</span>`;
    } else if (it.orb != null) {
      const pct = Math.round(it.orb * 100);
      score = `<span class="card-score${it.weak ? ' weak' : ''}">${pct}%</span>`;
    }

    const badge = it.kind === 'source'
      ? `<span class="card-badge src">源文件</span>`
      : '';

    const err = it.error
      ? `<span class="card-err">!</span>` : '';

    c.innerHTML = `
      <div class="card-thumb">${thumb}${badge}${score}${err}</div>
      <div class="card-meta">
        <div class="card-name"></div>
        <div class="card-sub"></div>
      </div>`;

    const nm = c.querySelector('.card-name');
    nm.textContent = it.name;
    bindTip(nm, it.name);

    const sub = c.querySelector('.card-sub');
    if (state.mode === 'search') {
      const parts = [KIND_LABEL[it.kind] || ''];
      if (it.width && it.height) parts.push(`${it.width}×${it.height}`);
      setCardSub(sub, parts.join(' · '), it.mtime);
    } else if (state.mode === 'semantic') {
      if (state.semChannel === 'faces') {
        // 人脸通道：把检出几个脸显示出来，用户能判断「主图人物」还是
        // 「角落有个路人」
        const parts = [it.faces ? `${it.faces} 张人脸` : '含人物'];
        if (it.width && it.height) parts.push(`${it.width}×${it.height}`);
        setCardSub(sub, parts.join(' · '), it.mtime);
      } else {
        const parts = [KIND_LABEL[it.kind] || ''];
        if (it.width && it.height) parts.push(`${it.width}×${it.height}`);
        setCardSub(sub, parts.join(' · '), it.mtime);
      }
    } else if (state.mode === 'text') {
      // 图上命中了几段文字、命中的是哪一段 —— 这是文字搜索唯一有意义的
      // 附加信息。搜「开业庆典」命中 5 段的图显然比命中 1 段的更相关。
      const hits = it.hit_texts || [];
      const first = hits[0] && hits[0].text ? hits[0].text : '';
      const short = first.length > 18 ? first.slice(0, 18) + '…' : first;
      const n = it.hit_count || hits.length;
      const parts = [];
      if (n > 1) parts.push(`命中 ${n} 段`);
      if (short) parts.push(short);
      setCardSub(sub, parts.join(' · ') || '命中文字', it.mtime);
    } else if (state.q) {
      // 文本搜索时同名文件很多，副标题显示所在目录，否则分不清是哪一个。
      // 日期靠右固定，扫一眼就能比出哪个是新的版本。
      setCardSub(sub, parentDir(it.path), it.mtime);
    } else {
      setCardSub(sub, fmtSize(it.size) + ' · ' + (it.ext || '').toUpperCase(),
                 it.mtime);
    }

    c.onclick = () => selectItem(it.id);
    frag.appendChild(c);
  });
  grid.appendChild(frag);
}

/* ============================================================
   详情面板
   ============================================================ */

async function selectItem(id) {
  state.selected = id;
  $$('.card').forEach(c => c.classList.toggle('on',
    Number(c.dataset.id) === id));

  const it = state.results.find(x => x.id === id);
  if (!it) return;

  const detail = $('#detail');
  detail.classList.add('open');
  const host = $('#detail-scroll');
  host.innerHTML = '<div class="skeleton" style="height:420px"></div>';

  // 大图要现渲染（PSD 合并图层、AI 转 PDF 渲染），几百毫秒到几秒。
  // 骨架屏尺寸跟真实预览一致，避免图出来时面板跳动。
  const src = it.error ? `/api/thumb?id=${it.id}` : `/api/file?id=${it.id}`;

  let reasonHtml = '';
  if (it.via === 'paired') {
    reasonHtml = `<div class="reason">文件系统配对：同目录同主名或指纹一致</div>`;
  } else if (it.phash_dist != null) {
    reasonHtml += `<div class="reason">缩略图指纹距离 ${it.phash_dist} / 64</div>`;
    if (it.orb != null) {
      reasonHtml += `<div class="reason${it.weak ? ' weak' : ''}">
        ORB 特征匹配 ${(it.orb * 100).toFixed(1)}%
        ${it.weak ? '（弱匹配）' : ''}</div>`;
    }
    if (it.rotation) {
      reasonHtml += `<div class="reason">查询图旋转 ${it.rotation}° 后匹配</div>`;
    }
  }

  host.innerHTML = `
    <div class="detail-preview"><div class="preview-wrap">
      <img src="${src}" alt="">
      <div class="hit-boxes" id="hitBoxes"></div>
    </div></div>
    <div class="detail-name"></div>
    <div class="detail-path"></div>
    <div class="sect">
      <div class="kv"><span class="kv-k">类型</span>
        <span class="kv-v">${KIND_LABEL[it.kind] || it.kind} · ${(it.ext || '').toUpperCase()}</span></div>
      ${it.width ? `<div class="kv"><span class="kv-k">尺寸</span>
        <span class="kv-v">${it.width} × ${it.height}</span></div>` : ''}
      ${it.size ? `<div class="kv"><span class="kv-k">大小</span>
        <span class="kv-v">${fmtSize(it.size)}</span></div>` : ''}
      ${it.mtime ? `<div class="kv"><span class="kv-k">修改时间</span>
        <span class="kv-v">${fmtDate(it.mtime)}</span></div>` : ''}
    </div>
    ${reasonHtml ? `<div class="sect"><div class="sect-title">匹配依据</div>
      ${reasonHtml}</div>` : ''}
    ${it.error ? `<div class="sect"><div class="sect-title">提取异常</div>
      <div class="kv"><span class="kv-v" style="color:var(--warn)">
      ${it.error}</span></div></div>` : ''}
    <div class="btn-row">
      <button class="btn primary" id="btnOpen">打开所在位置</button>
      <button class="btn ghost" id="btnCopy">复制路径</button>
      ${(it.hit_texts && it.hit_texts.length)
        ? '<button class="btn ghost" id="btnBig">看大图</button>' : ''}
    </div>
    <div class="sect" id="groupSect" style="display:none"></div>`;

  host.querySelector('.detail-name').textContent = it.name;
  host.querySelector('.detail-path').textContent = it.path;
  bindTip(host.querySelector('.detail-path'), it.path);

  const bigBtn = $('#btnBig');
  if (bigBtn) bigBtn.onclick = () => openBigImage(it);

  drawHitBoxes(it);

  // 命中文字列表插到「匹配依据」之前。
  // 光在图上画框不够用：竖版长海报在 344px 面板里只能显示 121px 宽，
  // 文字都缩成几像素，框再准也看不清。列表补上「命中了哪些文字」，
  // 两条信息互补。
  const hitList = buildHitList(it);
  const matchSect = host.querySelector('.sect');
  if (hitList && matchSect) {
    matchSect.parentNode.insertBefore(hitList, matchSect);
  }

  $('#btnOpen').onclick = async () => {
    try {
      const r = await fetch('/api/open', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: it.id }),
      });
      const j = await r.json();
      if (!j.ok) toast(j.error === 'file missing' ?
        '文件已不在原位置' : '打开失败：' + j.error);
    } catch (e) {
      toast('打开失败');
    }
  };

  $('#btnCopy').onclick = async () => {
    try {
      await navigator.clipboard.writeText(it.path);
      toast('路径已复制');
    } catch (e) {
      toast('复制失败');
    }
  };

  if (it.group_id) loadGroup(it);
}

function loadGroup(it) {
  // 直接用当前结果里的同组文件，不再发请求
  const sibs = state.results.filter(x => x.group_id === it.group_id &&
                                          x.id !== it.id);
  if (!sibs.length) return;

  const sect = $('#groupSect');
  if (!sect) return;
  sect.style.display = '';
  sect.innerHTML = `<div class="sect-title">同一画面的其他文件
    (${sibs.length})</div>`;

  sibs.forEach(s => {
    const el = document.createElement('div');
    el.className = 'group-item';
    el.innerHTML = `
      <img src="/api/thumb?id=${s.id}" alt="">
      <div class="group-item-info">
        <div class="group-item-name"></div>
        <div class="group-item-tag">${KIND_LABEL[s.kind] || s.kind} · ${
          s.width ? s.width + '×' + s.height : ''}</div>
      </div>`;
    el.querySelector('.group-item-name').textContent = s.name;
    bindTip(el.querySelector('.group-item-name'), s.path);
    el.onclick = () => selectItem(s.id);
    sect.appendChild(el);
  });
}

/* ============================================================
   toast（自绘）
   ============================================================ */

let toastTimer = null;
function toast(msg) {
  let el = $('#toast-el');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast-el';
    el.style.cssText = `position:fixed;bottom:26px;left:50%;
      transform:translateX(-50%);background:var(--text);color:#fff;
      padding:8px 14px;border-radius:var(--r-md);font-size:var(--fs-sm);
      z-index:600;opacity:0;transition:opacity .18s;pointer-events:none`;
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.opacity = '1';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.style.opacity = '0'; }, 1800);
}

/* ============================================================
   数据加载
   ============================================================ */

async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    state.stats = await r.json();
    if (!state.job || !state.job.running) {
      $('#status-text').textContent =
        `已索引 ${state.stats.total.toLocaleString()} 个文件`;
    }
  } catch (e) {
    $('#status-text').textContent = '索引读取失败';
    $('#status-dot').classList.add('idle');
  }
}

async function loadRoots() {
  try {
    const r = await fetch('/api/roots');
    const j = await r.json();
    state.roots = j.roots || [];
    state.dataDir = j.data_dir || '';
    state.lastIndex = j.last_index || null;
    return j;
  } catch (e) {
    return null;
  }
}

async function loadFilters() {
  try {
    const r = await fetch('/api/filters');
    state.filters = await r.json();
    renderSide();
  } catch (e) { /* 忽略 */ }
}

function buildQuery(extra = {}) {
  const p = new URLSearchParams();
  const s = state.sel;
  if (s.ext.size) p.set('ext', Array.from(s.ext).join(','));
  if (s.kind) p.set('kind', s.kind);
  if (s.dirs.size) {
    p.set('dir', Array.from(s.dirs).map(d =>
      d.split('|').join('\\')).join('\n'));
  }
  if (state.q) p.set('q', state.q);
  p.set('sort', state.sort);
  Object.entries(extra).forEach(([k, v]) => p.set(k, v));
  return p.toString();
}

async function applyFilters() {
  if (state.mode === 'search' || state.mode === 'semantic' ||
      state.mode === 'text') {
    // 这三种模式的结果集已固定（图搜图 / 画面语义 / 图中文字），
    // 只在前端按筛选条件过一遍，不重新请求
    renderResults();
    return;
  }
  state.loading = true;
  try {
    const r = await fetch('/api/list?' + buildQuery({ limit: 300 }));
    const j = await r.json();
    state.results = j.items.filter(passLocal);
    state.total = j.total;
    renderResults();
    updateMainTitle();
  } catch (e) {
    toast('加载失败');
  } finally {
    state.loading = false;
  }
}

function updateMainTitle() {
  const el = $('#main-title');
  if (state.mode === 'search') {
    el.textContent = '以图搜图结果';
    return;
  }
  if (state.mode === 'semantic') {
    // 人物类查询走的是人脸检测通道，说「画面含」不准确 ——
    // 它筛的是「检测到人脸」，不是「画面语义接近」
    el.textContent = state.semChannel === 'faces'
      ? `有人物的画面`
      : `画面含「${state.semQuery}」`;
    return;
  }
  if (state.mode === 'text') {
    el.textContent = `图上印着「${state.semQuery}」`;
    return;
  }
  if (state.q) {
    el.textContent = `搜索「${state.q}」`;
  } else {
    el.textContent = '全部文件';
  }
}

function passLocal(it) {
  const s = state.sel;
  if (s.hideNoThumb && !it.has_thumb) return false;
  if (s.widthMin && (it.width || 0) < s.widthMin) return false;
  if (s.widthMax && (it.width || 0) > s.widthMax) return false;
  if (s.heightMin && (it.height || 0) < s.heightMin) return false;
  if (s.heightMax && (it.height || 0) > s.heightMax) return false;
  if (s.aspectLo && s.aspectHi) {
    const a = it.aspect || 0;
    if (a < s.aspectLo || a > s.aspectHi) return false;
  }
  if (s.days) {
    const cut = Date.now() / 1000 - Number(s.days) * 86400;
    if ((it.mtime || 0) < cut) return false;
  }
  if (s.color) {
    const c = hexToRgb(s.color);
    const tol = s.colorTol;
    const pal = it.palette || [];
    if (!pal.length) return false;
    const ok = pal.some(p => Math.abs(p[0] - c[0]) <= tol &&
                             Math.abs(p[1] - c[1]) <= tol &&
                             Math.abs(p[2] - c[2]) <= tol);
    if (!ok) return false;
  }
  return true;
}

/* ============================================================
   搜索
   ============================================================ */

function setDzPreview(file, stateText) {
  const dz = $('#dz');
  const thumb = $('#dz-thumb');
  const name = $('#dz-name');
  const st = $('#dz-state');

  dz.classList.add('has-image');
  dz.classList.remove('busy');

  if (file) {
    if (thumb.dataset.url) URL.revokeObjectURL(thumb.dataset.url);
    const url = URL.createObjectURL(file);
    thumb.dataset.url = url;
    thumb.src = url;
    name.textContent = file.name || '剪贴板图片';
    bindTip(name, file.name || '剪贴板图片');
  }
  st.classList.remove('err');
  st.textContent = stateText || '待搜索';
}

function setDzPreviewByPath(path, stateText) {
  // 右键触发时拿到的是本地路径，浏览器读不了本地文件，
  // 所以缩略图由服务端渲染后回传，这里只负责显示。
  const dz = $('#dz');
  const thumb = $('#dz-thumb');
  const name = $('#dz-name');
  const st = $('#dz-state');

  dz.classList.add('has-image');
  dz.classList.remove('busy');

  if (thumb.dataset.url) {
    URL.revokeObjectURL(thumb.dataset.url);
    delete thumb.dataset.url;
  }
  const fname = (path || '').split(/[\\/]/).pop() || '图片';
  thumb.src = '/api/preview?path=' + encodeURIComponent(path);
  name.textContent = fname;
  bindTip(name, path || fname);

  st.classList.remove('err');
  st.textContent = stateText || '待搜索';
}

function clearDzPreview() {
  const dz = $('#dz');
  const thumb = $('#dz-thumb');
  dz.classList.remove('has-image', 'busy');
  if (thumb.dataset.url) {
    URL.revokeObjectURL(thumb.dataset.url);
    delete thumb.dataset.url;
  }
  thumb.removeAttribute('src');
  $('#dz-name').textContent = '';
  $('#dz-state').textContent = '';
}

async function doSearch(file) {
  if (!file) return;
  const dot = $('#status-dot');
  dot.classList.remove('idle');
  dot.classList.add('busy');
  $('#status-text').textContent = '正在比对…';

  const dz = $('#dz');
  setDzPreview(file, '比对中…');
  dz.classList.add('busy');

  try {
    const r = await fetch('/api/search', { method: 'POST', body: file });
    const j = await r.json();
    if (j.error) {
      toast('搜索失败：' + j.error);
      return;
    }
    state.mode = 'search';
    state.results = j.results || [];
    state.total = j.total || 0;
    // 图像搜索与文本搜索互斥，清掉文本框
    if (state.q) {
      state.q = '';
      $('#q').value = '';
      $('#q-clear').style.display = 'none';
    }
    updateMainTitle();
    renderResults();

    const srcs = state.results.filter(x => x.kind === 'source' && !x.weak);
    const paired = state.results.filter(x => x.via === 'paired');
    if (state.results.length) {
      const top = state.results[0];
      selectItem(top.id);
      if (srcs.length) {
        $('#status-text').textContent =
          `找到 ${srcs.length} 个源文件 · 共 ${state.results.length} 个相关文件`;
      } else if (paired.length) {
        $('#status-text').textContent =
          `找到 ${paired.length} 个配对文件 · 共 ${state.results.length} 个`;
      } else {
        $('#status-text').textContent =
          `找到 ${state.results.length} 个相关文件，无源文件`;
      }
    } else {
      $('#status-text').textContent = '没有找到匹配的文件';
    }
  } catch (e) {
    toast('搜索失败');
    $('#status-text').textContent = '搜索失败';
    const st = $('#dz-state');
    st.textContent = '搜索失败';
    st.classList.add('err');
  } finally {
    dot.classList.remove('busy');
    dz.classList.remove('busy');
    const st = $('#dz-state');
    if (!st.classList.contains('err')) {
      st.textContent = state.total
        ? `已搜到 ${state.total} 个` : '没有匹配';
    }
    bindTip(dz, '点右侧 × 取消，或再拖一张图替换');
  }
}

function resetSearch() {
  state.mode = 'browse';
  state.queryFile = null;
  updateMainTitle();
  clearDzPreview();
  bindTip($('#dz'),
    '把图拖到这里，或直接拖到窗口任意位置；也可 Ctrl+V 粘贴');
  $('#detail').classList.remove('open');
  state.selected = null;
  loadStats();
  applyFilters();
}

/* ============================================================
   事件绑定
   ============================================================ */

function bindEvents() {
  // 顶栏拖放区：点击打开文件
  const dz = $('#dz');
  let fileInput = null;

  dz.onclick = e => {
    // 有预览时点框等于「换一张」，点 × 是取消，别混淆
    if (e.target.closest('#dz-clear')) return;
    if (!fileInput) {
      fileInput = document.createElement('input');
      fileInput.type = 'file';
      fileInput.accept = 'image/*';
      fileInput.style.display = 'none';
      document.body.appendChild(fileInput);
      fileInput.onchange = () => {
        if (fileInput.files[0]) doSearch(fileInput.files[0]);
        fileInput.value = '';
      };
    }
    fileInput.click();
  };

  // 全窗口拖放
  const mask = $('#dropmask');
  let dragDepth = 0;

  window.addEventListener('dragenter', e => {
    e.preventDefault();
    dragDepth++;
    if (e.dataTransfer?.types?.includes('Files')) mask.classList.add('on');
  });
  window.addEventListener('dragover', e => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });
  window.addEventListener('dragleave', e => {
    dragDepth--;
    if (dragDepth <= 0) { dragDepth = 0; mask.classList.remove('on'); }
  });
  window.addEventListener('drop', e => {
    e.preventDefault();
    dragDepth = 0;
    mask.classList.remove('on');
    const f = e.dataTransfer?.files?.[0];
    if (f && f.type.startsWith('image/')) doSearch(f);
    else if (f) toast('请拖入图片文件');
  });

  // 粘贴
  window.addEventListener('paste', e => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const it of items) {
      if (it.type.startsWith('image/')) {
        const f = it.getAsFile();
        if (f) { doSearch(f); e.preventDefault(); }
        return;
      }
    }
  });

  // 视图切换
  $$('#view-seg .seg-item').forEach(el => {
    el.onclick = () => {
      $$('#view-seg .seg-item').forEach(x => x.classList.remove('on'));
      el.classList.add('on');
      state.view = el.dataset.view;
      renderResults();
    };
  });

  // 排序
  createSelect($('#sort-select'), [
    { value: 'mtime', label: '最近修改' },
    { value: 'mtime_asc', label: '最早修改' },
    { value: 'size', label: '文件大小' },
    { value: 'name', label: '文件名称' },
  ], state.sort, v => {
    state.sort = v;
    applyFilters();
  });

  // 键盘
  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      closeAllSelects();
      $('#overlay').classList.remove('open');
      if (state.mode === 'search' && e.shiftKey) resetSearch();
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
      e.preventDefault();
      dz.click();
    }
  });

  bindSearch();

  // 搜索模式切换：文件名 / 画面内容
  $$('#search-mode .seg-item').forEach(el => {
    el.onclick = () => setSearchMode(el.dataset.sm);
  });
  bindTip($('#search-mode'), '按文件名搜，还是按画面内容搜');

  $('#btn-update').onclick = onUpdateClick;
  $('#btn-settings').onclick = openSettings;

  // 文字索引入口的绑定放在 semantic.js 末尾（那里定义了
  // refreshTextStatus / showTextSetup）。app.js 先加载、先 boot，
  // 在这里调用会报 refreshTextStatus is not defined（实测踩到）。

  // 顶栏两个按钮的说明用自绘 tooltip，不用原生 title
  bindTip($('#btn-update'), '扫描目录，把新增和修改过的文件补进索引');
  bindTip($('#btn-settings'), '设置索引目录');
  bindTip($('#dz-clear'), '取消搜索');
  bindTip(dz, '把图拖到这里，或直接拖到窗口任意位置；也可 Ctrl+V 粘贴');

  $('#dz-clear').onclick = e => {
    e.stopPropagation();
    resetSearch();
    toast('已取消搜索');
  };
}

/* ============================================================
   文件名搜索
   ============================================================ */

function bindSearch() {
  const box = $('#searchbox');
  const inp = $('#q');
  const clear = $('#q-clear');
  const panel = $('#q-panel');

  function syncClear() {
    clear.style.display = inp.value ? 'grid' : 'none';
  }

  function closePanel() {
    panel.classList.remove('open');
    state.suggestIdx = -1;
    state.suggestItems = [];
  }

  function commit(value, immediate) {
    const v = (value !== undefined ? value : inp.value).trim();
    if (immediate === undefined) immediate = true;
    state.q = v;
    closePanel();
    if (immediate) applyFilters();
  }

  // 输入：防抖后更新结果，同时拉建议
  inp.addEventListener('input', () => {
    syncClear();
    const v = inp.value.trim();
    clearTimeout(state.qTimer);
    if (!v) {
      state.q = '';
      state.semQuery = '';
      closePanel();
      if (state.mode === 'semantic' || state.mode === 'text') {
        state.mode = 'browse';
      }
      applyFilters();
      return;
    }
    state.qTimer = setTimeout(() => {
      if (state.searchMode === 'semantic') {
        // 画面搜索：不走文件名补全，直接把词发给语义接口
        runSemanticSearch(v);
        return;
      }
      if (state.searchMode === 'text') {
        // 图中文搜索：同样不补全（补的是文件名，反而误导）
        runTextSearch(v);
        return;
      }
      state.q = v;
      if (state.mode === 'search') {
        // 从图像搜索结果切回文本搜索
        state.mode = 'browse';
        $('#detail').classList.remove('open');
      }
      applyFilters();
      loadSuggest(v);
    }, 260);
  });

  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      // 图中文 / 画面模式下回车必须走对应的搜索通道。
      // 原来这里无条件落到 commit()（文件名搜索），所以在「图中文」
      // 模式里输完词按回车，会跳成文件名搜索 —— 用户看到的是「搜到了
      // 但没高亮、标题也变了」的错乱状态。防抖跑完再回车才正常，
      // 这个差别太隐蔽。
      if (state.searchMode === 'text' || state.searchMode === 'semantic') {
        clearTimeout(state.qTimer);
        const v = inp.value.trim();
        if (state.searchMode === 'text') runTextSearch(v);
        else runSemanticSearch(v);
        closePanel();
        return;
      }
      if (state.suggestIdx >= 0 && state.suggestItems[state.suggestIdx]) {
        const it = state.suggestItems[state.suggestIdx];
        inp.value = it.text;
        syncClear();
        commit(it.text);
      } else {
        clearTimeout(state.qTimer);
        commit();
      }
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!state.suggestItems.length) return;
      e.preventDefault();
      const n = state.suggestItems.length;
      let i = state.suggestIdx + (e.key === 'ArrowDown' ? 1 : -1);
      if (i < 0) i = n - 1;
      if (i >= n) i = 0;
      state.suggestIdx = i;
      renderSuggest();
    } else if (e.key === 'Escape') {
      if (panel.classList.contains('open')) {
        closePanel();
      } else {
        inp.value = '';
        syncClear();
        commit('', true);
      }
    }
  });

  inp.addEventListener('focus', () => {
    if (state.q && state.suggestItems.length) panel.classList.add('open');
  });

  clear.onclick = () => {
    inp.value = '';
    syncClear();
    commit('', true);
    inp.focus();
  };

  document.addEventListener('click', e => {
    if (!box.contains(e.target)) closePanel();
  });

  function renderSuggest() {
    if (!state.suggestItems.length) {
      panel.classList.remove('open');
      return;
    }
    panel.innerHTML = '';
    const ql = inp.value.trim().toLowerCase();
    state.suggestItems.forEach((it, i) => {
      const el = document.createElement('div');
      el.className = 'sb-item' + (i === state.suggestIdx ? ' on' : '');
      const idx = it.text.toLowerCase().indexOf(ql);
      const t = document.createElement('span');
      t.className = 'sb-item-text';
      if (idx >= 0 && ql) {
        t.innerHTML = '';
        t.appendChild(document.createTextNode(it.text.slice(0, idx)));
        const h = document.createElement('span');
        h.className = 'sb-hit';
        h.textContent = it.text.slice(idx, idx + ql.length);
        t.appendChild(h);
        t.appendChild(document.createTextNode(it.text.slice(idx + ql.length)));
      } else {
        t.textContent = it.text;
      }
      const k = document.createElement('span');
      k.className = 'sb-item-kind';
      k.textContent = it.kind === 'dir' ? '目录' : '文件';
      el.appendChild(k);
      el.appendChild(t);
      if (it.count > 1) {
        const c = document.createElement('span');
        c.className = 'sb-item-count';
        c.textContent = it.count;
        el.appendChild(c);
      }
      el.addEventListener('mousedown', ev => {
        ev.preventDefault();
        inp.value = it.text;
        syncClear();
        commit(it.text);
      });
      panel.appendChild(el);
    });
    panel.classList.add('open');
  }

  async function loadSuggest(q) {
    try {
      const r = await fetch('/api/suggest?q=' + encodeURIComponent(q));
      const j = await r.json();
      state.suggestItems = j.items || [];
      state.suggestIdx = -1;
      renderSuggest();
    } catch (e) { /* 忽略 */ }
  }

  // 全局快捷键：Ctrl+K / Cmd+K 聚焦搜索框
  window.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      inp.focus();
      inp.select();
    }
  });
}

/* ============================================================
   索引管理
   ============================================================ */

async function onUpdateClick() {
  if (state.job && state.job.running) {
    // 正在跑：变为取消
    await fetch('/api/index/cancel', { method: 'POST' });
    return;
  }
  if (!state.roots.length) {
    openSettings();
    return;
  }
  await startIndex(state.roots);
}

async function startIndex(roots) {
  try {
    const r = await fetch('/api/index/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ roots }),
    });
    const j = await r.json();
    if (j.error) { toast(j.error); return; }
    setJob(j);
    startPolling();
  } catch (e) {
    toast('启动索引失败');
  }
}

function setJob(j) {
  state.job = j;
  const bar = $('#idxbar');
  const fill = $('#idxbar-fill');
  const btn = $('#btn-update');
  const txt = $('#btn-update-text');
  const dot = $('#status-dot');

  if (!j || j.state === 'idle') {
    bar.classList.remove('on');
    fill.style.width = '0';
    dot.classList.remove('busy');
    dot.classList.toggle('idle', !state.stats || !state.stats.total);
    txt.textContent = '更新索引';
    return;
  }

  if (j.running) {
    bar.classList.add('on');
    fill.style.width = (j.percent || 0) + '%';
    dot.classList.remove('idle');
    dot.classList.add('busy');
    txt.textContent = '取消';
    const pct = j.percent || 0;
    $('#status-text').textContent =
      j.total ? `索引中 ${j.done}/${j.total}（${pct}%）`
              : (j.message || '索引中…');
    bindTip(btn, j.message || '点击取消索引');
  } else {
    bar.classList.remove('on');
    dot.classList.remove('busy');
    dot.classList.toggle('idle', !state.stats || !state.stats.total);
    txt.textContent = '更新索引';
    if (j.state === 'done') {
      $('#status-text').textContent = j.message || '索引已更新';
      bindTip(btn, '扫描目录，把新增和修改过的文件补进索引');
    } else if (j.state === 'error') {
      $('#status-text').textContent = '索引失败：' + (j.error || '');
    }
  }
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/index/status');
      const j = await r.json();
      const wasRunning = state.job && state.job.running;
      setJob(j);
      if (wasRunning && !j.running) {
        stopPolling();
        // 索引结束后刷新数据和界面
        await loadStats();
        await loadFilters();
        if (state.mode === 'browse') await applyFilters();
        if (j.state === 'done') toast(j.message || '索引已更新');
      }
    } catch (e) {
      stopPolling();
    }
  }, 800);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

/* ---- 设置弹层 ---- */

function openSettings() {
  const ov = $('#overlay');
  const md = $('#modal');

  md.innerHTML = `
    <div class="modal-head">
      <div class="modal-title">索引设置</div>
      <div class="modal-desc">选择要建立索引的目录。加进来后，这些目录里
        新增和修改过的文件都会在下次更新时自动补进索引。</div>
    </div>
    <div class="modal-body">
      <div class="sect-title">索引目录</div>
      <div id="setDirs"></div>
      <div class="btn-row" style="margin-top:9px">
        <button class="btn ghost sm" id="addDir">添加目录</button>
      </div>
      <div class="sect" style="margin-top:16px">
        <div class="sect-title">系统集成</div>
        <div id="setShell"></div>
      </div>
      <div class="sect" style="margin-top:16px">
        <div class="sect-title">数据位置</div>
        <div class="kv"><span class="kv-k">索引文件</span>
          <span class="kv-v mono" id="setData"></span></div>
        <div class="kv"><span class="kv-k">当前记录</span>
          <span class="kv-v" id="setTotal"></span></div>
        <div class="kv"><span class="kv-k">上次更新</span>
          <span class="kv-v" id="setLast"></span></div>
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" id="setCancel">取消</button>
      <button class="btn ghost" id="setSave">只保存</button>
      <button class="btn primary" id="setSaveRun">保存并更新索引</button>
    </div>`;

  let dirs = state.roots.slice();

  function renderDirs() {
    const host = $('#setDirs', md);
    host.innerHTML = '';
    if (!dirs.length) {
      host.innerHTML = `<div class="dir-path empty">还没有添加目录</div>`;
    }
    const list = document.createElement('div');
    list.className = 'dir-list';
    dirs.forEach((d, i) => {
      const el = document.createElement('div');
      el.className = 'dir-item';
      el.innerHTML = `<svg width="11" height="11" viewBox="0 0 14 14"
          fill="none"><path d="M1.5 4.5v6A1 1 0 002.5 11.5h9a1 1 0 001-1V5a1 1 0 00-1-1H7L6 2.5H2.5a1 1 0 00-1 1z"
          stroke="currentColor" stroke-width="1.1"/></svg>
        <span class="dir-item-name"></span>
        <button class="dir-item-del"><svg width="8" height="8"
          viewBox="0 0 10 10"><path d="M1.5 1.5L8.5 8.5M8.5 1.5L1.5 8.5"
          stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg></button>`;
      el.querySelector('.dir-item-name').textContent = d;
      bindTip(el.querySelector('.dir-item-name'), d);
      el.querySelector('.dir-item-del').onclick = ev => {
        ev.stopPropagation();
        dirs.splice(i, 1);
        renderDirs();
      };
      list.appendChild(el);
    });
    host.appendChild(list);
  }

  renderDirs();
  renderShellSection(md);
  $('#setData', md).textContent = state.dataDir || '（未知）';
  $('#setTotal', md).textContent =
    state.stats ? state.stats.total.toLocaleString() + ' 个文件' : '-';
  $('#setLast', md).textContent = state.lastIndex
    ? new Date(state.lastIndex * 1000).toLocaleString('zh-CN') : '从未';

  $('#addDir', md).onclick = () => {
    openDirBrowser('', picked => {
      if (picked && !dirs.includes(picked)) {
        dirs.push(picked);
        renderDirs();
      }
    });
  };

  const close = () => ov.classList.remove('open');
  $('#setCancel', md).onclick = close;
  $('#setSave', md).onclick = async () => {
    if (!dirs.length) { toast('至少需要一个目录'); return; }
    const ok = await saveRoots(dirs);
    if (ok) { close(); toast('已保存'); }
  };
  $('#setSaveRun', md).onclick = async () => {
    if (!dirs.length) { toast('至少需要一个目录'); return; }
    const ok = await saveRoots(dirs);
    if (!ok) return;
    close();
    await startIndex(dirs);
  };

  ov.classList.add('open');
  ov.onclick = e => { if (e.target === ov) close(); };
}

async function renderShellSection(md) {
  const host = $('#setShell', md);
  if (!host) return;
  host.innerHTML = '<div class="kv"><span class="kv-v t3">读取中…</span></div>';

  let st;
  try {
    st = await (await fetch('/api/shellmenu')).json();
  } catch (e) {
    host.innerHTML = '<div class="kv"><span class="kv-v t3">读取失败</span></div>';
    return;
  }

  if (!st.supported) {
    host.innerHTML = '<div class="kv"><span class="kv-v t3">仅 Windows 支持</span></div>';
    return;
  }

  host.innerHTML = `
    <div class="switch ${st.enabled ? 'on' : ''}" id="shellToggle">
      <div class="switch-track"><div class="switch-knob"></div></div>
      <span class="switch-label">在资源管理器右键菜单中显示</span>
    </div>
    <div class="kv" style="margin-top:6px">
      <span class="kv-v t3" id="shellHint"></span>
    </div>`;

  const hint = $('#shellHint', md);
  // 每次调用都从当前 st 重算，避免开关后仍显示旧数字
  const hintText = () => {
    if (!st.enabled) {
      return '开启后可在文件、文件夹、磁盘上右键直接调用。不需要管理员权限。';
    }
    const n = (st.files || []).length;
    return `已注册 ${n} 种图片格式 + 文件夹 + 磁盘。` +
      '右键任意 PSD/JPG 可直接搜源文件。';
  };
  hint.textContent = hintText();

  const toggle = $('#shellToggle', md);
  toggle.onclick = async () => {
    const turnOn = !st.enabled;
    toggle.style.pointerEvents = 'none';
    try {
      const r = await fetch('/api/shellmenu', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: turnOn ? 'install' : 'uninstall' }),
      });
      const j = await r.json();
      if (j.error) { toast(j.error); return; }
      st = j.status;
      toggle.classList.toggle('on', st.enabled);
      hint.textContent = hintText();
      toast(turnOn ? '已注册到右键菜单' : '已从右键菜单移除');
    } catch (e) {
      toast('操作失败');
    } finally {
      toggle.style.pointerEvents = '';
    }
  };
}

async function saveRoots(roots) {
  try {
    const r = await fetch('/api/roots', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ roots }),
    });
    const j = await r.json();
    if (j.error) { toast(j.error); return false; }
    state.roots = j.roots || roots;
    return true;
  } catch (e) {
    toast('保存失败');
    return false;
  }
}

/* ---- 自绘目录浏览 ---- */

function openDirBrowser(startPath, onPick) {
  const ov = $('#overlay');
  const md = $('#modal');
  let cur = startPath || '';

  md.innerHTML = `
    <div class="modal-head">
      <div class="modal-title">选择目录</div>
      <div class="modal-desc">进入目标目录，然后点「选定此目录」。</div>
    </div>
    <div class="modal-body">
      <div class="browser-head">
        <button class="btn sm ghost" id="brUp">上一级</button>
        <span class="browser-path" id="brPath"></span>
      </div>
      <div class="browser-list scroll" id="brList"></div>
    </div>
    <div class="modal-foot">
      <button class="btn ghost" id="brCancel">取消</button>
      <button class="btn primary" id="brPick">选定此目录</button>
    </div>`;

  async function nav(p) {
    try {
      const r = await fetch('/api/browse?path=' + encodeURIComponent(p));
      const j = await r.json();
      if (j.error) { toast(j.error === 'permission' ? '没有权限访问' : j.error); return; }
      cur = j.path || '';
      $('#brPath', md).textContent = cur || '此电脑';
      bindTip($('#brPath', md), cur || '此电脑');
      const list = $('#brList', md);
      list.innerHTML = '';
      if (!j.items.length) {
        list.innerHTML = `<div class="browser-empty">这个目录里没有子目录</div>`;
      }
      j.items.forEach(it => {
        const el = document.createElement('div');
        el.className = 'browser-item';
        el.innerHTML = `<svg width="12" height="12" viewBox="0 0 14 14"
            fill="none"><path d="M1.5 4.5v6A1 1 0 002.5 11.5h9a1 1 0 001-1V5a1 1 0 00-1-1H7L6 2.5H2.5a1 1 0 00-1 1z"
            stroke="currentColor" stroke-width="1.1"/></svg><span></span>`;
        el.querySelector('span').textContent = it.name;
        el.onclick = () => nav(it.path);
        list.appendChild(el);
      });
      $('#brUp', md).disabled = !cur;
    } catch (e) {
      toast('读取目录失败');
    }
  }

  $('#brUp', md).onclick = async () => {
    // 从当前路径推上一级
    const norm = cur.replace(/[\\/]+$/, '');
    const idx = Math.max(norm.lastIndexOf('\\'), norm.lastIndexOf('/'));
    if (idx <= 2) { nav(''); return; }     // D:\x → D:\
    nav(norm.slice(0, idx));
  };
  $('#brCancel', md).onclick = () => ov.classList.remove('open');
  $('#brPick', md).onclick = () => {
    if (!cur) { toast('请先进入一个目录'); return; }
    ov.classList.remove('open');
    onPick(cur);
  };
  ov.classList.add('open');
  ov.onclick = e => { if (e.target === ov) ov.classList.remove('open'); };
  nav(cur);
}

/* ============================================================
   启动
   ============================================================ */

let lastShellSeq = 0;

async function pollShellAction() {
  try {
    const r = await fetch('/api/pending');
    const j = await r.json();
    const p = j.pending;
    if (!p) return;
    // 服务端保留最近一条不删（右键会额外开新标签页，两边都在轮询）。
    // 用序号去重：自己处理过的不再重复处理。
    if (!p._seq || p._seq <= lastShellSeq) return;
    lastShellSeq = p._seq;

    if (p.action === 'search') {
      if (p.error) {
        toast('右键搜索失败：' + p.error);
        return;
      }
      // 服务端已经搜完了，前端只需要拉结果并展示
      await loadStats();
      const name = (p.path || '').split(/[\\/]/).pop();
      toast(p.total ? `已搜到 ${p.total} 个相关文件` : `没有找到「${name}」的相关文件`);
      // 服务端只回传了 total/top，这里再拉一次完整结果
      try {
        const resp = await fetch('/api/search', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: p.path, limit: 60 }),
        });
        const d = await resp.json();
        if (d.results) {
          state.mode = 'search';
          state.results = d.results;
          state.total = d.total || 0;
          updateMainTitle();
          renderResults();
          // 顶栏要能看出「这张图是哪来的」。右键传的是本地路径，
          // 不能当 File 用，走单独的渲染路径
          setDzPreviewByPath(
            p.path,
            state.total ? `已搜到 ${state.total} 个` : '没有匹配');
          if (state.results.length) selectItem(state.results[0].id);
        }
      } catch (e) { /* 忽略 */ }
    } else if (p.action === 'root') {
      toast(p.added ? '已加入索引范围，正在建立索引'
                    : '该目录已在索引范围内，正在更新索引');
      await loadRoots();
      await loadStats();
      startPolling();
    }
  } catch (e) { /* 忽略 */ }
}

async function boot() {
  bindEvents();
  await loadStats();
  const r = await loadRoots();
  await loadFilters();
  await applyFilters();

  // 恢复正在跑的索引作业（刷新页面不丢进度）
  try {
    const st = await (await fetch('/api/index/status')).json();
    if (st.state !== 'idle') {
      setJob(st);
      if (st.running) startPolling();
    }
  } catch (e) { /* 忽略 */ }

  // 空库：直接弹引导
  if (!state.stats || !state.stats.total) {
    openSettings();
  }

  // 右键菜单触发的动作：进来立刻取一次，之后每 2 秒看一眼
  await pollShellAction();
  setInterval(pollShellAction, 2000);

  // 窗口被最小化时，浏览器内核会节流甚至暂停定时器。右键把窗口叫回前台后，
  // 如果只靠 setInterval，可能要等好几秒才反应过来。回到前台立刻补一次。
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) pollShellAction();
  });
  window.addEventListener('focus', pollShellAction);
}

boot();
