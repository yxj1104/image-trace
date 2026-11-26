/* ============================================================
   画面内容搜索 / 图中文搜索
   ============================================================

   三种搜索是三条不同的路径：

   - 文件名搜索走 /api/list，SQL 里按名字/路径 LIKE，结果确定
   - 图中文搜索走 /api/text/search，查 OCR 缓存表，命中图里印的字
   - 画面搜索走 /api/semantic/search，用中文 CLIP 把查询词编码成
     512 维向量，跟图库里的向量算余弦相似度

   三者解决三个不同的问题：文件叫什么 / 图上写了什么 / 画面看起来像什么。
*/

/** 切到某个搜索模式。name = 文件名，text = 图中文，semantic = 画面内容 */
function setSearchMode(mode) {
  if (state.searchMode === mode) return;
  state.searchMode = mode;

  $$('#search-mode .seg-item').forEach(el => {
    el.classList.toggle('on', el.dataset.sm === mode);
  });

  const inp = $('#q');
  if (mode === 'semantic') {
    inp.placeholder = '搜画面内容，比如：沙滩、酒店房间、寺庙';
    // 语义搜索不提供文件名补全，那会诱导用户以为在搜文件名
    $('#q-panel').classList.remove('open');
    enterSemanticMode();
  } else if (mode === 'text') {
    inp.placeholder = '搜图上印的字，比如：开业庆典、新品上市';
    $('#q-panel').classList.remove('open');
    enterTextMode();
  } else {
    inp.placeholder = '搜文件名或路径，空格分隔多个词';
    state.semQuery = '';
    if (state.mode === 'semantic' || state.mode === 'text') {
      state.mode = 'browse';
      state.results = [];
    }
    // 切回文件名模式时，把输入框里的词交给文件名搜索。
    // 不接管的话输入框写着「沙滩」、列表却显示全部文件，
    // 用户会以为搜索失效了。
    state.q = inp.value.trim();
  }

  if (inp.value.trim()) {
    if (mode === 'semantic') runSemanticSearch(inp.value.trim());
    else if (mode === 'text') runTextSearch(inp.value.trim());
    else applyFilters();
  }
}

/** 进入画面搜索：先看索引建好了没，没建就提示 */
async function enterSemanticMode() {
  await refreshSemanticStatus();

  if (!state.semantic) {
    toast('无法获取画面索引状态');
    return;
  }
  if (!state.semantic.available) {
    showSemanticSetup(state.semantic);
    return;
  }
  if (!state.semantic.ready) {
    showSemanticSetup(state.semantic);
    return;
  }
  // 已就绪：如果输入框里有词，直接搜
  const q = $('#q').value.trim();
  if (q) runSemanticSearch(q);
}

async function refreshSemanticStatus() {
  try {
    const r = await fetch('/api/semantic/status');
    state.semantic = await r.json();
    return state.semantic;
  } catch (e) {
    return null;
  }
}

/**
 * 画面索引还没建好时的引导页。
 *
 * 这里不做「一键偷偷开始」—— 全量编码 6000 个源文件要占用几小时 CPU，
 * 必须让用户知道要花多久、可以中途停、可以不占满 CPU 再点。
 */
function showSemanticSetup(st) {
  const grid = $('#grid');
  const empty = $('#empty');
  grid.style.display = 'none';
  empty.style.display = '';

  if (!st || !st.available) {
    // 模型缺失有三种可能：下了 lite 版、源码运行时忘了放模型、
    // 文件损坏。文案要覆盖这三种，并给出可执行的下一步 ——
    // 开源版不能写「联系维护者」，用户联系不到人。
    empty.innerHTML = `
      <div class="empty-icon">!</div>
      <div class="empty-title">画面搜索不可用</div>
      <div class="empty-desc small">
        缺少模型文件 <code>models/cn_clip.onnx</code>（182MB）。<br>
        如果你下的是 lite 版，这个功能本来就不包含；<br>
        如果是完整版，模型可能没解压完整。<br>
        源码运行的话，见 <code>models/README.md</code> 获取模型。
      </div>`;
    return;
  }

  const job = st.job || {};
  const running = job.running;
  const pct = job.total ? Math.round(job.done / job.total * 100) : 0;
  const eta = job.eta ? fmtDuration(job.eta) : '';

  if (running) {
    empty.innerHTML = `
      <div class="empty-icon spin"></div>
      <div class="empty-title">正在建立画面索引</div>
      <div class="empty-desc">${job.done} / ${job.total}
        ${eta ? `　剩余约 ${eta}` : ''}</div>
      <div class="sem-bar"><div class="sem-bar-fill" style="width:${pct}%"></div></div>
      <div class="empty-desc small">${job.message || ''}</div>
      <div class="btn-row">
        <button class="btn ghost" id="semStop">暂停</button>
      </div>`;
    $('#semStop').onclick = async () => {
      await fetch('/api/semantic/cancel', { method: 'POST' });
      toast('已暂停，下次可以接着做');
      refreshSemanticStatus();
      showSemanticSetup(state.semantic);
    };
    startSemanticPoll();
    return;
  }

  const remain = st.source_files - st.embedded;
  if (remain <= 0) {
    empty.innerHTML = `
      <div class="empty-icon">✓</div>
      <div class="empty-title">画面索引已就绪</div>
      <div class="empty-desc">直接在上方输入画面内容即可搜索</div>`;
    return;
  }

  // 预估时间按格式加权：PSD/JPG 各约 0.19s，Word 文档走 COM 要 2~3s。
  // 用统一的单张耗时会差三倍，所以让后端算好毫秒数传过来。
  const perMs = st.remaining_cost_ms || (remain * 190);
  const mins = Math.max(1, Math.round(perMs / 60000));
  empty.innerHTML = `
    <div class="empty-icon">◎</div>
    <div class="empty-title">需要先建立画面索引</div>
    <div class="empty-desc">
      给 ${st.source_files} 个源文件（PSD / AI / PDF / Word…）各生成一份
      「画面指纹」，之后就能按画面内容搜索。<br>
      已建立 ${st.embedded} 个，还剩 ${remain} 个，
      预计需要 ${mins > 60 ? (mins / 60).toFixed(1) + ' 小时' : mins + ' 分钟'}。
    </div>
    <div class="empty-desc small">
      建立过程只占用约一半 CPU，你可以继续用别的软件。
      中途暂停或关闭程序都没关系，下次会从断点继续。
    </div>
    <div class="btn-row">
      <button class="btn primary" id="semStart">开始建立</button>
    </div>`;
  $('#semStart').onclick = async () => {
    const r = await fetch('/api/semantic/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // 4 线程 + 每批歇 30ms：实测约占一半 CPU，用 PS 时不明显卡
      body: JSON.stringify({ threads: 4, delay: 0.03 }),
    });
    const j = await r.json();
    if (j.error) {
      toast(j.error);
      return;
    }
    await refreshSemanticStatus();
    showSemanticSetup(state.semantic);
  };
}

function fmtDuration(sec) {
  if (!sec || sec < 0) return '';
  if (sec < 60) return `${Math.round(sec)} 秒`;
  if (sec < 3600) return `${Math.round(sec / 60)} 分钟`;
  return `${(sec / 3600).toFixed(1)} 小时`;
}

function startSemanticPoll() {
  if (state.semanticPoll) return;
  state.semanticPoll = setInterval(async () => {
    if (state.searchMode !== 'semantic') return;
    const st = await refreshSemanticStatus();
    if (!st) return;
    const running = st.job && st.job.running;
    if (state.mode !== 'semantic') {
      showSemanticSetup(st);
    }
    if (!running) {
      clearInterval(state.semanticPoll);
      state.semanticPoll = null;
      if (state.mode !== 'semantic' && st.ready) {
        showSemanticSetup(st);
      }
    }
  }, 1500);
}

/** 执行画面搜索 */
async function runSemanticSearch(q) {
  q = (q || '').trim();
  if (!q) {
    state.mode = 'browse';
    state.results = [];
    applyFilters();
    return;
  }
  state.semQuery = q;
  state.mode = 'semantic';
  state.loading = true;

  const grid = $('#grid');
  const empty = $('#empty');
  empty.style.display = 'none';
  grid.style.display = '';
  grid.innerHTML = '<div class="sem-loading">正在按画面内容检索…</div>';

  try {
    const r = await fetch('/api/semantic/search?q=' + encodeURIComponent(q) +
                          '&limit=120');
    const j = await r.json();
    if (j.error) {
      state.mode = 'browse';
      await refreshSemanticStatus();
      showSemanticSetup(state.semantic);
      if (j.error !== '还没有建立画面索引') toast(j.error);
      return;
    }
    state.results = j.results || [];
    state.total = state.results.length;
    state.semChannel = j.channel || 'semantic';
    state.semTerms = j.terms || [];
    state.semNote = j.note || '';
    renderResults();
    updateMainTitle();
    renderQueryHint();
  } catch (e) {
    toast('搜索失败');
    state.mode = 'browse';
  } finally {
    state.loading = false;
  }
}

/**
 * 在结果区上方显示「这次是怎么搜的」。
 *
 * 为什么需要：用户输入长句时，后端会拆成多个词分别检索。
 * 如果不告诉他，搜索结果的构成就是不透明的 —— 尤其在结果不理想时，
 * 他没法判断是该换词还是库里真没有。
 */
function renderQueryHint() {
  const host = $('#query-hint');
  if (!host) return;
  const terms = (state.semTerms || []).filter(Boolean);
  const needNote = !!state.semNote;

  if (state.mode !== 'semantic' || (!terms.length && !needNote)) {
    host.style.display = 'none';
    host.innerHTML = '';
    return;
  }

  // 只有一个词、且和输入一致时不必解释拆词，但有 note 时仍要显示
  const onlyOne = terms.length <= 1 &&
    terms[0] === (state.semQuery || '').toLowerCase();
  if (onlyOne && !needNote) {
    host.style.display = 'none';
    host.innerHTML = '';
    return;
  }

  host.style.display = '';
  host.innerHTML = '';

  if (!onlyOne && terms.length) {
    const label = document.createElement('span');
    label.className = 'qh-label';
    label.textContent = '拆成这些词检索：';
    host.appendChild(label);

    terms.slice(0, 6).forEach(t => {
      const chip = document.createElement('span');
      chip.className = 'qh-chip';
      chip.textContent = t;
      host.appendChild(chip);
    });
  }

  if (state.semNote) {
    const note = document.createElement('span');
    note.className = 'qh-note';
    note.textContent = (onlyOne ? '' : '· ') + state.semNote;
    host.appendChild(note);
  }
}

/* ============================================================
   图中文搜索（OCR 缓存）
   ============================================================ */

async function refreshTextStatus() {
  try {
    const r = await fetch('/api/text/status');
    state.textStatus = await r.json();
    syncOcrEntry(state.textStatus);
    return state.textStatus;
  } catch (e) {
    return null;
  }
}

/**
 * 顶栏的「文字索引」入口按钮。
 *
 * **这是一个补救设计。** 原来的逻辑是：进入「图中文」模式时，只有
 * `ready === false`（一帧都没识别）才显示范围选择页。一旦识别了哪怕
 * 一个文件，ready 就变成 true，于是直接进搜索态 —— 范围选择页
 * 再也进不去了，用户没法继续补剩下的 18000 多个文件。
 *
 * 把「首次建索引」和「继续补索引」当成两回事是错的。索引永远是
 * 部分的（用户按范围一批批跑），所以入口必须是常驻的。
 */
function syncOcrEntry(st) {
  const btn = $('#btn-ocr');
  if (!btn) return;
  if (!st || !st.available) {
    btn.style.display = 'none';
    return;
  }
  btn.style.display = '';

  const left = (st.slices || []).reduce((a, s) => a + s.left, 0);
  const label = $('#btn-ocr-text');
  const dot = $('#btn-ocr-dot');
  const job = st.job || {};

  if (job.running) {
    label.textContent = `识别中 ${job.done}/${job.total}`;
    dot.style.display = '';
    btn.classList.add('busy');
  } else if (left > 0) {
    // 显示「已识别 / 总数」，让人一眼知道索引是部分的
    label.textContent = `文字索引 ${st.done}/${st.total_files}`;
    dot.style.display = '';
    btn.classList.remove('busy');
  } else {
    label.textContent = '文字索引';
    dot.style.display = 'none';
    btn.classList.remove('busy');
  }
}

async function enterTextMode() {
  const st = await refreshTextStatus();
  if (!st) { toast('无法获取文字索引状态'); return; }
  if (!st.available) {
    showTextSetup({
      available: false,
      note: '缺少 OCR 组件 rapidocr_onnxruntime',
    });
    return;
  }
  // 输入框里有词就直接搜（有索引时），否则进范围管理页。
  // 不能只按 ready 判断 —— 索引永远是部分的，用户随时需要回去
  // 加范围、看进度、暂停。
  const q = $('#q').value.trim();
  if (q && st.ready) {
    runTextSearch(q);
    return;
  }
  showTextSetup(st);
}

/**
 * 文字索引未建立时的引导页。
 *
 * 用户明确要求「不要全局索引，按类型按磁盘来」—— 所以这里列出
 * 每个「盘 × 格式」分片，让用户勾选要处理的范围。
 */
function showTextSetup(st) {
  const grid = $('#grid');
  const empty = $('#empty');
  grid.style.display = 'none';
  empty.style.display = '';

  // 标题区跟着改。原来只改了列表区，标题还写着「全部文件 300/18720」，
  // 用户会以为范围页是弹在文件列表上的浮层，不知道自己在哪个界面。
  const title = $('#main-title');
  const count = $('#main-count');
  if (title) title.textContent = '图中文索引';
  if (count) count.textContent = '';

  if (!st || !st.available) {
    empty.innerHTML = `
      <div class="empty-icon">!</div>
      <div class="empty-title">图中文搜索不可用</div>
      <div class="empty-desc">${st && st.note ? st.note : 'OCR 组件未安装'}</div>`;
    return;
  }

  const job = st.job || {};
  if (job.running) {
    const pct = job.total ? Math.round(job.done / job.total * 100) : 0;
    const eta = job.eta ? fmtDuration(job.eta) : '';
    empty.innerHTML = `
      <div class="empty-icon spin"></div>
      <div class="empty-title">正在识别图里的文字</div>
      <div class="empty-desc">${job.done} / ${job.total}
        ${eta ? `　剩余约 ${eta}` : ''}</div>
      <div class="sem-bar"><div class="sem-bar-fill" style="width:${pct}%"></div></div>
      <div class="empty-desc small">${job.message || ''}</div>
      <div class="btn-row"><button class="btn ghost" id="ocrStop">暂停</button></div>`;
    $('#ocrStop').onclick = async () => {
      await fetch('/api/text/cancel', { method: 'POST' });
      toast('已暂停，下次可以接着做');
      const s2 = await refreshTextStatus();
      showTextSetup(s2);
    };
    startTextPoll();
    return;
  }

  // 分片列表：只显示还有待办的分片，按盘分组
  const slices = (st.slices || []).filter(s => s.left > 0);
  const drives = [...new Set(slices.map(s => s.drive))];
  const totalLeft = slices.reduce((a, s) => a + s.left, 0);
  const totalCost = slices.reduce((a, s) => a + s.cost_ms, 0);

  if (!slices.length) {
    empty.innerHTML = `
      <div class="empty-icon">✓</div>
      <div class="empty-title">文字索引已就绪</div>
      <div class="empty-desc">直接在上方输入图上印的字即可搜索</div>`;
    return;
  }

  const rows = drives.map(d => {
    const ds = slices.filter(s => s.drive === d);
    const left = ds.reduce((a, s) => a + s.left, 0);
    const cost = ds.reduce((a, s) => a + s.cost_ms, 0);
    return `<div class="ocr-drive">
      <div class="ocr-drive-head">
        <span class="ocr-drive-name">${d} 盘</span>
        <span class="ocr-drive-meta">${left} 个文件 · 约 ${fmtMinutes(cost)}</span>
      </div>
      <div class="ocr-chips">${
        ds.map(s => `<span class="ocr-chip" data-drive="${d}" data-ext="${s.ext}">
          ${s.ext.toUpperCase()} <em>${s.left}</em></span>`).join('')
      }</div>
    </div>`;
  }).join('');

  empty.innerHTML = `
    <div class="empty-icon">◎</div>
    <div class="empty-title">选择要识别文字的范围</div>
    <div class="empty-desc small">
      识别一遍后把文字存进索引，以后搜索是直接查缓存（毫秒级），
      不用再跑 OCR。共 ${totalLeft} 个待处理，全做完约 ${fmtMinutes(totalCost)}。
    </div>
    <div class="ocr-drives">${rows}</div>
    <div class="empty-desc small">
      点格式标签切换选中。默认只选源文件（PSD/AI/PDF/Word），
      导出图多是源文件的副本，文字内容重复。
    </div>
    <div class="btn-row">
      <button class="btn primary" id="ocrStart">开始识别</button>
    </div>`;

  // 默认选中源文件的格式
  const SRCEXT = ['psd', 'ai', 'pdf', 'docx', 'doc', 'ppt', 'pptx', 'cdr', 'eps'];
  const picked = new Set();
  slices.forEach(s => {
    if (SRCEXT.includes(s.ext)) picked.add(s.drive + '|' + s.ext);
  });
  if (!picked.size) slices.slice(0, 3).forEach(s => picked.add(s.drive + '|' + s.ext));

  function syncChips() {
    $$('.ocr-chip').forEach(el => {
      const k = el.dataset.drive + '|' + el.dataset.ext;
      el.classList.toggle('on', picked.has(k));
    });
  }
  $$('.ocr-chip').forEach(el => {
    el.onclick = () => {
      const k = el.dataset.drive + '|' + el.dataset.ext;
      if (picked.has(k)) picked.delete(k); else picked.add(k);
      syncChips();
    };
  });
  syncChips();

  $('#ocrStart').onclick = async () => {
    const sel = [...picked].map(k => k.split('|'));
    if (!sel.length) { toast('先选一个范围'); return; }
    const payload = {
      drives: [...new Set(sel.map(x => x[0]))],
      exts: [...new Set(sel.map(x => x[1]))],
      delay: 0.03,
    };
    const r = await fetch('/api/text/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const j = await r.json();
    if (j.error) { toast(j.error); return; }
    const s2 = await refreshTextStatus();
    showTextSetup(s2);
  };
}

function fmtMinutes(ms) {
  const m = Math.round(ms / 60000);
  if (m < 1) return '不到 1 分钟';
  if (m < 60) return `${m} 分钟`;
  return `${(m / 60).toFixed(1)} 小时`;
}

function startTextPoll() {
  if (state.textPoll) return;
  state.textPoll = setInterval(async () => {
    if (state.searchMode !== 'text') return;
    const st = await refreshTextStatus();
    if (!st) return;
    const running = st.job && st.job.running;
    if (state.mode !== 'text') showTextSetup(st);
    if (!running) {
      clearInterval(state.textPoll);
      state.textPoll = null;
      if (state.mode !== 'text' && st.ready) showTextSetup(st);
    }
  }, 1500);
}

/** 执行图中文搜索 */
async function runTextSearch(q) {
  q = (q || '').trim();
  if (!q) {
    state.mode = 'browse';
    state.results = [];
    applyFilters();
    return;
  }
  state.semQuery = q;
  state.mode = 'text';
  state.loading = true;

  const grid = $('#grid');
  const empty = $('#empty');
  empty.style.display = 'none';
  grid.style.display = '';
  grid.innerHTML = '<div class="sem-loading">正在查图中的文字…</div>';

  try {
    const r = await fetch('/api/text/search?q=' + encodeURIComponent(q) +
                          '&limit=150');
    const j = await r.json();
    if (j.error) {
      state.mode = 'browse';
      const st = await refreshTextStatus();
      showTextSetup(st);
      if (j.error !== '还没有建立画面索引') toast(j.error);
      return;
    }
    state.results = j.results || [];
    state.total = state.results.length;
    state.semTerms = j.terms || [];
    state.semNote = '';
    state.semChannel = 'text';
    renderResults();
    updateMainTitle();
    renderQueryHint();
  } catch (e) {
    toast('搜索失败');
    state.mode = 'browse';
  } finally {
    state.loading = false;
  }
}

/* ---- 顶栏文字索引入口的绑定 ----
   app.js 先加载，它的 boot() 里拿不到这里的函数（脚本是顺序执行的，
   boot 在 semantic.js 解析之前就跑完了），所以在文件末尾自己绑。 */
(function bindOcrEntry() {
  function wire() {
    const btn = $('#btn-ocr');
    if (!btn) return;
    btn.onclick = async () => {
      if (state.searchMode !== 'text') {
        setSearchMode('text');
      } else {
        const st = await refreshTextStatus();
        showTextSetup(st);
      }
    };
    bindTip(btn, '识别图里的文字，之后可以按图上的字搜图');
    refreshTextStatus();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire);
  } else {
    wire();
  }
})();
