/* 三省六部 · 朝堂信息流 —— 零依赖前端 */
(() => {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  const STATES = ['Pending', 'Taizi', 'Zhongshu', 'Menxia', 'Assigned', 'Doing', 'Review', 'Done'];
  const STATE_CN = {
    Pending: '待理', Taizi: '太子', Zhongshu: '中书', Menxia: '门下',
    Assigned: '派发', Doing: '执行', Review: '覆核', Done: '回奏',
    Blocked: '阻塞', Cancelled: '已废', Next: '候办', PendingConfirm: '候批',
  };
  const PRESETS = [
    { label: '竞品分析', title: '分析 CrewAI、AutoGen、LangGraph 三个多 Agent 框架并出对比报告',
      detail: '重点关注：架构设计、Agent 通信方式、任务编排能力、可观测性、学习曲线。输出结构化对比报告，含推荐场景。' },
    { label: '安全审查', title: '审查并加固登录接口的安全性',
      detail: '现有 FastAPI 登录接口存在弱口令与暴力破解风险，请给出问题清单、加固方案与验证步骤。' },
    { label: '项目周报', title: '生成本周项目进展周报',
      detail: '汇总本周完成事项、进行中事项、风险与下周计划，交一份可直接发群的周报。' },
  ];

  const EV_META = {
    call: { tag: '差遣', cat: 'doc', cls: 'k-call' },
    call_return: { tag: '回话', cat: 'doc', cls: 'k-doc' },
    tool_call: { tag: '用印', cat: 'tool', cls: 'k-tool' },
    tool_result: { tag: '回执', cat: 'tool', cls: 'k-tool' },
    state: { tag: '移文', cat: 'doc', cls: 'k-doc' },
    flow: { tag: '过文', cat: 'doc', cls: 'k-doc' },
    progress: { tag: '动议', cat: 'doc', cls: 'k-doc' },
    todo: { tag: '勾当', cat: 'doc', cls: 'k-doc' },
    done_report: { tag: '交差', cat: 'tool', cls: 'k-artifact' },
    block: { tag: '阻塞', cat: 'deny', cls: 'k-deny' },
    artifact: { tag: '落笔', cat: 'tool', cls: 'k-artifact' },
    audit: { tag: '审计', cat: 'doc', cls: 'k-doc' },
    round: { tag: '回合', cat: 'think', cls: 'k-dim' },
    role_start: { tag: '出班', cat: 'think', cls: 'k-dim' },
    role_end: { tag: '退班', cat: 'think', cls: 'k-dim' },
    thought: { tag: '思虑', cat: 'think', cls: 'k-thought' },
    text: { tag: '陈奏', cat: 'doc', cls: 'k-doc' },
    error: { tag: '惊变', cat: 'deny', cls: 'k-deny' },
    guard: { tag: '戒饬', cat: 'deny', cls: 'k-deny' },
    followup: { tag: '回奏', cat: 'doc', cls: 'k-call' },
    court_start: { tag: '升朝', cat: 'doc', cls: 'k-doc' },
    court_end: { tag: '退朝', cat: 'doc', cls: 'k-doc' },
  };

  const S = {
    roles: [], roleMap: {}, tasks: [], taskId: null, es: null,
    lastSeq: 0, filter: 'all', roleFilter: null, openThought: null,
    calls: 0, tokens: 0, t0: 0, timer: null, running: false,
    exportTask: null, exportDefault: '',
    atBottom: true, pending: 0, deleteTask: null,
  };

  // ── 基础 ─────────────────────────────────────────────

  function roleLabel(id) {
    const r = S.roleMap[id];
    return r ? `${r.emoji} ${r.label}` : (id || '天');
  }

  async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).error || msg; } catch (_) { /* ignore */ }
      throw new Error(msg);
    }
    return res.json();
  }

  function setWebMeta(w) {
    const node = $('#meta-web');
    if (!node) return;
    const on = !!(w && w.available);
    node.textContent = on ? `✓ ${(w.tools || []).length}` : (w && w.enabled ? '连接中…' : '未启用');
    node.classList.toggle('off', !on);
    node.title = on
      ? `可用：${(w.tools || []).join('、')}`
      : ((w && (w.error || '网关未就绪')) || '');
  }

  function setMeta() {
    $('#meta-calls').textContent = String(S.calls);
    $('#meta-tokens').textContent = S.tokens ? (S.tokens / 1000).toFixed(1) + 'k' : '0';
    if (S.running && S.t0) $('#meta-time').textContent = Math.round((Date.now() - S.t0) / 1000) + 's';
  }
  function startClock() {
    stopClock();
    S.running = true;
    S.t0 = Date.now();
    S.timer = setInterval(setMeta, 500);
  }
  function stopClock() {
    S.running = false;
    if (S.timer) { clearInterval(S.timer); S.timer = null; }
    setMeta();
  }
  function nowStr() {
    const d = new Date();
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':');
  }
  function kb(n) {
    if (!n) return '';
    if (n < 1024) return n + 'B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + 'K';
    return (n / 1048576).toFixed(1) + 'M';
  }

  // ── 圣旨 ─────────────────────────────────────────────

  function renderPresets() {
    const box = $('#presets');
    box.innerHTML = '';
    PRESETS.forEach((p) => {
      const b = el('button', 'chip', p.label);
      b.type = 'button';
      b.onclick = () => {
        $('#decree-text').value = p.title + '\n\n' + p.detail;
        $('#decree-text').focus();
      };
      box.appendChild(b);
    });
  }

  async function sendDecree() {
    const raw = $('#decree-text').value.trim();
    if (!raw) { $('#decree-text').focus(); return; }
    const [title, ...rest] = raw.split('\n');
    const btn = $('#btn-send');
    btn.disabled = true;
    try {
      const r = await api('/api/decree', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title.trim(), detail: rest.join('\n').trim() }),
      });
      S.taskId = r.task_id;
      localStorage.setItem('edict.lastTask', S.taskId);
      resetFeed();
      connect(S.taskId);
      refreshTasks();
    } catch (e) {
      pushEvent({ kind: 'error', message: '颁旨失败：' + e.message, at: nowStr() });
    } finally {
      btn.disabled = false;
    }
  }

  // ── 轨道 ─────────────────────────────────────────────

  function renderRail(task) {
    const rail = $('#rail');
    rail.innerHTML = '';
    const cur = task ? task.state : null;
    const curIdx = STATES.indexOf(cur);
    STATES.forEach((st, i) => {
      const n = el('span', 'node');
      n.appendChild(el('span', 'cn', STATE_CN[st] || st));
      n.appendChild(el('span', null, st));
      if (cur === st) n.classList.add('current');
      else if (curIdx >= 0 && i < curIdx) n.classList.add('done');
      rail.appendChild(n);
      if (i < STATES.length - 1) rail.appendChild(el('span', 'arrow', '›'));
    });
    rail.appendChild(el('span', 'arrow', '›'));
    const blocked = el('span', 'node' + (cur === 'Blocked' ? ' current' : ''), '阻塞');
    rail.appendChild(blocked);
    let note = '尚未开朝';
    const rn = task ? (task.round_no || 1) : 1;
    if (task) {
      if (cur === 'Done') note = `已回奏${rn > 1 ? ` · 第${rn}轮` : ''} · ${task.llm_calls} 次调用`;
      else if (task.status === 'running') note = `${STATE_CN[cur] || cur} · ${task.org}`;
      else note = `${task.note || '未回奏'} · ${STATE_CN[cur] || cur}`;
    }
    $('#rail-note').textContent = note;
  }

  // ── 看板 ─────────────────────────────────────────────

  function renderBoard() {
    const board = $('#board');
    board.innerHTML = '';
    const cols = [...STATES, 'Blocked'];
    const byState = {};
    S.tasks.forEach((t) => { (byState[t.state] = byState[t.state] || []).push(t); });

    cols.forEach((st) => {
      const col = el('div', 'col' + (st === 'Done' ? ' terminal' : st === 'Blocked' ? ' blocked' : ''));
      const head = el('div', 'col-head');
      head.appendChild(el('span', 'cn', STATE_CN[st] || st));
      head.appendChild(el('span', null, String((byState[st] || []).length)));
      col.appendChild(head);
      const cards = byState[st] || [];
      if (!cards.length) col.appendChild(el('div', 'col-empty', '—'));
      cards.forEach((t) => col.appendChild(taskCard(t)));
      board.appendChild(col);
    });

    const done = S.tasks.filter((t) => t.state === 'Done').length;
    $('#board-hint').textContent = S.tasks.length ? `${S.tasks.length} 道旨意 · ${done} 道回奏` : '尚无旨意';
  }

  function taskCard(t) {
    const c = el('div', 'card' + (t.id === S.taskId ? ' selected' : ''));
    c.title = t.title;
    c.dataset.task = t.id;
    const idEl = el('div', 'id');
    const m = String(t.id).match(/^(.*-)(\d+)$/);
    if (m) idEl.innerHTML = `${m[1]}<b>${m[2]}</b>`;
    else idEl.textContent = t.id;
    if ((t.round_no || 1) > 1) idEl.appendChild(el('span', 'round', `第${t.round_no}轮`));
    c.appendChild(idEl);
    c.appendChild(el('div', 'title', t.title));
    const who = el('div', 'who');
    who.appendChild(el('span', null, t.org || '—'));
    const doneTodos = (t.todos || []).filter((x) => x.status === 'completed').length;
    const all = (t.todos || []).length;
    who.appendChild(el('span', null, all ? `${doneTodos}/${all}` : ''));
    c.appendChild(who);
    const bar = el('div', 'bar');
    const i = el('i');
    i.style.width = all ? Math.round((doneTodos / all) * 100) + '%' : (t.state === 'Done' ? '100%' : '0%');
    bar.appendChild(i);
    c.appendChild(bar);
    const del = el('button', 'card-del', '×');
    del.type = 'button';
    del.title = '删除这道旨意';
    del.setAttribute('aria-label', `删除 ${t.id}`);
    del.onclick = (e) => { e.stopPropagation(); openDelete(t.id); };
    c.appendChild(del);
    c.onclick = () => { if (t.id !== S.taskId) selectTask(t.id); };
    return c;
  }

  async function selectTask(taskId) {
    if (!taskId) return;
    S.taskId = taskId;
    localStorage.setItem('edict.lastTask', taskId);
    resetFeed();
    connect(taskId);
    await refreshTasks();                // 拉最新列表后统一渲染（选中态/轨道/产出）
  }

  let tasksSeq = 0;
  async function refreshTasks() {
    const seq = (tasksSeq += 1);
    try {
      const r = await api('/api/tasks');
      if (seq !== tasksSeq) return;      // 旧响应晚到：丢弃，别把状态写回去
      S.tasks = (r.tasks || []).sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
      renderBoard();
      const cur = S.tasks.find((t) => t.id === S.taskId);
      renderRail(cur || null);
      refreshArtifacts();
    } catch (e) { /* 静默 */ }
  }

  // ── 产出：按旨意 → 职能（流程层级）分组 ──────────────

  function dirRank(dir) {
    if (dir === '圣旨') return -1;      // 皇上原文，永远排在最前
    const hit = S.roles.find((r) => r.dept_dir === dir);
    if (hit) return hit.rank;
    if (dir === '共议') return 25.5;   // 旧制目录：政令未分职能时的产物
    return 80;
  }
  function dirLabel(dir) {
    if (dir === '圣旨') return '👑 圣旨';
    const hit = S.roles.find((r) => r.dept_dir === dir);
    if (hit) return `${hit.emoji} ${hit.label}`;
    if (dir === '共议') return '🏛 共议（旧制）';
    return '📁 ' + dir;
  }

  function groupFiles(task) {
    const files = (task.files_detail && task.files_detail.length)
      ? task.files_detail
      : (task.files || []).map((p) => ({ path: p, size: 0 }));
    const map = new Map();
    files.forEach((f) => {
      const parts = f.path.split('/');
      const dir = parts.length > 1 ? parts[0] : '（根）';
      if (!map.has(dir)) map.set(dir, []);
      map.get(dir).push({ ...f, name: parts.length > 1 ? parts.slice(1).join('/') : f.path });
    });
    return [...map.entries()]
      .map(([dir, list]) => ({ dir, list: list.sort((a, b) => a.name.localeCompare(b.name, 'zh')), rank: dirRank(dir) }))
      .sort((a, b) => a.rank - b.rank || a.dir.localeCompare(b.dir, 'zh'));
  }

  let artSeq = 0;
  async function refreshArtifacts() {
    const id = S.taskId;
    const seq = (artSeq += 1);
    if (id) {
      const t = S.tasks.find((x) => x.id === id);
      // 有缓存就不重复拉，只有条目数对不上（新落笔/新旨意）才补一次明细
      if (t && (!t.files_detail || t.files_detail.length !== (t.files || []).length)) {
        try {
          const d = await api(`/api/tasks/${encodeURIComponent(id)}`);
          if (seq !== artSeq) return;            // 期间切了旨意，丢弃旧结果
          const cur = S.tasks.find((x) => x.id === id);
          if (cur) {
            cur.files = d.files || cur.files;
            cur.files_detail = d.files_detail || [];
            cur.state = d.state || cur.state;
          }
        } catch (_) { /* 忽略：按已知条目渲染 */ }
      }
    }
    if (seq !== artSeq) return;
    renderArtifacts();
  }

  function renderArtifacts() {
    const box = $('#artifact-list');
    box.innerHTML = '';
    const t = S.tasks.find((x) => x.id === S.taskId);
    const exportable = S.tasks.filter((x) => (x.files || []).length);
    $('#btn-export-all').disabled = !exportable.length;
    $('#btn-export').disabled = !t || !(t.files || []).length;
    const canFollow = !!t && t.status !== 'running' && ['Done', 'Blocked', 'Cancelled'].includes(t.state);
    const fb = $('#btn-followup');
    fb.disabled = !canFollow;
    fb.title = canFollow
      ? `在原旨意上续一轮（第 ${(t.round_no || 1) + 1} 轮）`
      : (t && t.status === 'running' ? '这道旨意还在办，等它回奏后再续' : '先在看板选一道已回奏的旨意');

    if (!t) {
      box.appendChild(el('div', 'art-empty', '请先在「军机处看板」上点一道旨意，这里只显示当前旨意的产出。'));
      $('#artifact-hint').textContent = '未选旨意';
      return;
    }

    const files = (t.files_detail && t.files_detail.length)
      ? t.files_detail
      : (t.files || []).map((p) => ({ path: p, size: 0 }));
    const head = el('div', 'art-task-head');
    head.appendChild(el('span', 'id', t.id));
    head.appendChild(el('span', 'title', t.title));
    head.appendChild(el('span', 'state' + (t.state === 'Done' ? ' done' : ''), STATE_CN[t.state] || t.state));
    if ((t.round_no || 1) > 1) head.appendChild(el('span', 'state', `第${t.round_no}轮`));
    head.appendChild(el('span', 'state', `${files.length} 份`));
    box.appendChild(head);

    if (!files.length) {
      box.appendChild(el('div', 'art-empty', t.status === 'running'
        ? '这道旨意还在办，尚无文书落笔。'
        : '这道旨意没有留下文书。'));
      $('#artifact-hint').textContent = '0 份';
      return;
    }
    $('#artifact-hint').textContent = `${files.length} 份 · 全部在沙箱内`;

    groupFiles(t).forEach((g) => {
      const grp = el('div', 'art-group');
      const gh = el('div', 'art-group-head');
      gh.appendChild(el('span', null, dirLabel(g.dir)));
      gh.appendChild(el('span', 'bar-line'));
      gh.appendChild(el('span', 'n', String(g.list.length)));
      grp.appendChild(gh);

      g.list.forEach((f) => {
        const b = el('button', 'art-file');
        b.type = 'button';
        b.appendChild(el('span', 'name', `${g.dir}/${f.name}`));
        b.appendChild(el('span', 'size', kb(f.size)));
        b.onclick = () => openArtifact(t.id, `${g.dir}/${f.name}`);
        grp.appendChild(b);
      });
      box.appendChild(grp);
    });
  }

  // ── 文书预览（markdown 排版）─────────────────────────

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function inlineMd(s) {
    s = s.replace(/`([^`]+)`/g, (_, a) => `<code>${a}</code>`);
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*\w])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    s = s.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return s;
  }

  function splitRow(line) {
    let t = line.trim();
    if (t.startsWith('|')) t = t.slice(1);
    if (t.endsWith('|')) t = t.slice(0, -1);
    return t.split('|').map((x) => x.trim());
  }

  function buildList(items, base) {
    let html = '';
    let i = 0;
    while (i < items.length) {
      const it = items[i];
      const ordered = it.ordered;
      html += ordered ? '<ol>' : '<ul>';
      while (i < items.length && items[i].ordered === ordered && items[i].indent === base) {
        const cur = items[i];
        i += 1;
        let inner = '';
        if (i < items.length && items[i].indent > base) {
          const sub = [];
          while (i < items.length && items[i].indent > base) { sub.push(items[i]); i += 1; }
          inner = buildList(sub, base + 2);
        }
        html += `<li>${inlineMd(escapeHtml(cur.text))}${inner}</li>`;
      }
      html += ordered ? '</ol>' : '</ul>';
      if (i < items.length && items[i].indent > base) {
        const sub = [];
        while (i < items.length && items[i].indent > base) { sub.push(items[i]); i += 1; }
        html += buildList(sub, base + 2);
      }
    }
    return html;
  }

  function mdToHtml(src) {
    const lines = String(src).replace(/\r\n?/g, '\n').split('\n');
    const out = [];
    let i = 0;
    const blockStart = (l) => /^(#{1,6}\s|```|\s*[-*+]\s+|\s*\d+\.\s+|\s*>|\s*\|)/.test(l) || /^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(l);
    while (i < lines.length) {
      const line = lines[i];
      if (/^```/.test(line)) {
        const buf = [];
        i += 1;
        while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i += 1; }
        i += 1;
        out.push(`<pre><code>${escapeHtml(buf.join('\n'))}</code></pre>`);
        continue;
      }
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        const lv = h[1].length;
        out.push(`<h${lv}>${inlineMd(escapeHtml(h[2]))}</h${lv}>`);
        i += 1;
        continue;
      }
      if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { out.push('<hr>'); i += 1; continue; }
      if (/^\s*\|/.test(line) && i + 1 < lines.length && /^[\s|:-]+$/.test(lines[i + 1]) && lines[i + 1].includes('-')) {
        const head = splitRow(line);
        i += 2;
        const rows = [];
        while (i < lines.length && /^\s*\|/.test(lines[i])) { rows.push(splitRow(lines[i])); i += 1; }
        out.push(`<table><thead><tr>${head.map((x) => `<th>${inlineMd(escapeHtml(x))}</th>`).join('')}</tr></thead><tbody>${
          rows.map((r) => `<tr>${r.map((c) => `<td>${inlineMd(escapeHtml(c))}</td>`).join('')}</tr>`).join('')}</tbody></table>`);
        continue;
      }
      if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
        const items = [];
        while (i < lines.length) {
          const m = lines[i].match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
          if (!m) break;
          items.push({ indent: m[1].replace(/\t/g, '  ').length, ordered: /\d/.test(m[2]), text: m[3] });
          i += 1;
        }
        out.push(buildList(items, items[0] ? items[0].indent : 0));
        continue;
      }
      if (/^\s*>\s?/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^\s*>\s?/, '')); i += 1; }
        out.push(`<blockquote>${mdToHtml(buf.join('\n'))}</blockquote>`);
        continue;
      }
      if (!line.trim()) { i += 1; continue; }
      const buf = [line];
      i += 1;
      while (i < lines.length && lines[i].trim() && !blockStart(lines[i])) { buf.push(lines[i]); i += 1; }
      out.push(`<p>${inlineMd(escapeHtml(buf.join('\n'))).replace(/\n/g, '<br>')}</p>`);
    }
    return out.join('\n');
  }

  async function openArtifact(taskId, path) {
    const title = $('#modal-title');
    const body = $('#modal-body');
    title.textContent = path;
    body.innerHTML = '<p>正在取文书…</p>';
    $('#modal').hidden = false;
    $('#modal-export').onclick = () => { $('#modal').hidden = true; openExport([taskId]); };
    try {
      const r = await api(`/api/artifact/${encodeURIComponent(taskId)}?path=${encodeURIComponent(path)}`);
      title.textContent = `${taskId} · ${r.path} · ${r.size} 字`;
      if (/\.(md|markdown|txt)$/i.test(path)) {
        body.innerHTML = mdToHtml(r.text || '（空）');
      } else {
        body.innerHTML = `<pre><code>${escapeHtml(r.text || '（空）')}</code></pre>`;
      }
      body.scrollTop = 0;
    } catch (e) {
      body.innerHTML = `<p>读不到这份文书：${escapeHtml(e.message)}</p>`;
    }
  }

  // ── 删除旨意 ─────────────────────────────────────────

  async function openDelete(taskId) {
    const t = S.tasks.find((x) => x.id === taskId);
    if (!t) return;
    S.deleteTask = taskId;
    const res = $('#delete-result');
    res.textContent = '';
    res.className = 'export-result';

    let files = (t.files || []).length;
    try {
      const d = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
      files = (d.files || []).length;
    } catch (_) { /* 详情拿不到就用已知条数 */ }

    const stateTxt = `${STATE_CN[t.state] || t.state}${(t.round_no || 1) > 1 ? ` · 第${t.round_no}轮` : ''}`;
    $('#delete-head').textContent = `删除 ${t.id}`;
    $('#delete-tip').innerHTML =
      `《${escapeHtml(t.title)}》<br>状态：${escapeHtml(stateTxt)}　沙箱文书：${files} 份<br>`
      + '删除后：看板卡片、产出清单、总线回放都会消失，<strong>不可恢复</strong>。';
    $('#del-purge').checked = true;
    $('#del-purge-label').textContent = `同时删除沙箱目录（${files} 份文书）`;
    const running = t.status === 'running';
    $('#delete-hint').textContent = running ? '这道旨意还在办，请先「叫停」再删' : '';
    $('#delete-run').disabled = running;
    $('#delete-modal').hidden = false;
  }

  async function runDelete() {
    const id = S.deleteTask;
    if (!id) return;
    const purge = $('#del-purge').checked;
    const btn = $('#delete-run');
    const res = $('#delete-result');
    btn.disabled = true;
    res.className = 'export-result';
    res.textContent = '正在删除…';
    try {
      const r = await api(`/api/tasks/${encodeURIComponent(id)}?purge=${purge ? '1' : '0'}`, { method: 'DELETE' });
      res.className = 'export-result ok';
      res.textContent = r.purged
        ? `已删除 ${id}（沙箱 ${r.files} 份文书一并移除）`
        : `已删除 ${id}（沙箱文件保留在原处）`;
      setTimeout(() => { $('#delete-modal').hidden = true; }, 800);

      if (S.taskId === id) {
        S.taskId = null;
        localStorage.removeItem('edict.lastTask');
        resetFeed();
        renderRail(null);
      }
      await refreshTasks();
      if (!S.taskId && S.tasks.length) {
        await selectTask(S.tasks[0].id);
      } else if (!S.tasks.length) {
        renderArtifacts();
      }
    } catch (e) {
      res.className = 'export-result err';
      res.textContent = '删除失败：' + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  // ── 回奏（在原旨意上续一轮）─────────────────────────

  function renderAttachments(t) {
    const box = $('#followup-files');
    box.innerHTML = '';
    const list = (t.instructions || []).slice().sort((a, b) => (a.round || 0) - (b.round || 0));
    if (!list.length) {
      box.appendChild(el('div', 'attach-empty', '这道旨意没有留下原文记录。'));
      return;
    }
    box.appendChild(el('div', 'attach-title', '原文 · 点开看全文'));
    list.forEach((ins) => {
      const b = el('button', 'attach-item');
      b.type = 'button';
      b.appendChild(el('span', 'r', `第${ins.round || 1}轮`));
      b.appendChild(el('span', 't', `${ins.kind === 'followup' ? '回奏' : '圣旨'}：${ins.title || ''}`));
      b.appendChild(el('span', 'p', ins.md || '未落盘'));
      if (ins.md) {
        b.onclick = () => openArtifact(t.id, ins.md);
      } else {
        b.disabled = true;
        b.title = '这一轮的原文尚未落盘';
      }
      box.appendChild(b);
    });
  }

  async function openFollowup() {
    const t = S.tasks.find((x) => x.id === S.taskId);
    if (!t) return;
    const next = (t.round_no || 1) + 1;
    $('#followup-head').textContent = `回奏 · ${t.id} 第 ${next} 轮`;
    $('#followup-tip').textContent =
      `回奏不是新开旨意：它在「${t.title}」上续第 ${next} 轮，从太子重新走三省六部，`
      + '产出叠加在同一道旨意目录里（文件名带「·第' + next + '轮」后缀，不覆盖上一轮定稿）。';
    $('#followup-title').value = '';
    $('#followup-detail').value = '';
    $('#followup-hint').textContent = `原旨意共 ${(t.files || []).length} 份文书`;
    const res = $('#followup-result');
    res.textContent = '';
    res.className = 'export-result';
    $('#followup-modal').hidden = false;
    renderAttachments(t);
    // 拉一次详情，拿到完整的 instructions（含本轮原文的 md 路径）
    try {
      const d = await api(`/api/tasks/${encodeURIComponent(t.id)}`);
      t.instructions = d.instructions || t.instructions;
      renderAttachments(t);
    } catch (_) { /* 拿不到就按已知的渲染 */ }
    $('#followup-title').focus();
  }

  async function runFollowup() {
    const t = S.tasks.find((x) => x.id === S.taskId);
    if (!t) return;
    const title = $('#followup-title').value.trim();
    const detail = $('#followup-detail').value.trim();
    const res = $('#followup-result');
    if (!title) {
      res.className = 'export-result err';
      res.textContent = '请先写一句本轮圣意（要补强/修改什么）。';
      $('#followup-title').focus();
      return;
    }
    const btn = $('#followup-run');
    btn.disabled = true;
    res.className = 'export-result';
    res.textContent = '正在下旨…';
    try {
      const r = await api(`/api/tasks/${encodeURIComponent(t.id)}/followup`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, detail }),
      });
      res.className = 'export-result ok';
      res.textContent = `已下旨：第 ${r.round} 轮，太子开始分拣…`;
      setTimeout(() => { $('#followup-modal').hidden = true; }, 900);
      await selectTask(t.id);    // 同一条旨意：重连总线（回放含新一轮）并拉最新状态
    } catch (e) {
      res.className = 'export-result err';
      res.textContent = '下旨失败：' + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  // ── 导出 ─────────────────────────────────────────────

  async function openExport(taskIds) {
    S.exportTask = taskIds;
    $('#export-result').textContent = '';
    $('#export-result').className = 'export-result';
    $('#export-tip').textContent = taskIds.length === 1
      ? `把 ${taskIds[0]} 的全部文书复制到下面的目录（会在其中新建同名子目录）。`
      : `把 ${taskIds.length} 道旨意的文书分别复制到下面的目录（每道旨意一个同名子目录）。`;
    if (!S.exportDefault) {
      try { S.exportDefault = (await api('/api/export/default')).dest; } catch (_) { S.exportDefault = ''; }
    }
    const input = $('#export-path');
    if (!input.value) input.value = S.exportDefault;
    $('#export-modal').hidden = false;
    input.focus();
    input.select();
  }

  async function runExport() {
    const dest = $('#export-path').value.trim();
    const res = $('#export-result');
    if (!dest) {
      res.className = 'export-result err';
      res.textContent = '请先填写目标目录（绝对路径）。';
      return;
    }
    const btn = $('#export-run');
    btn.disabled = true;
    res.className = 'export-result';
    res.textContent = '正在导出…';
    try {
      const ids = S.exportTask || [];
      let files = 0;
      let lastDest = dest;
      for (const id of ids) {
        const r = await api(`/api/export/${encodeURIComponent(id)}`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dest }),
        });
        files += r.files;
        lastDest = r.dest;
      }
      S.exportDefault = dest;
      res.className = 'export-result ok';
      res.innerHTML = ids.length === 1
        ? `已导出 ${files} 份文书到：<br><code>${escapeHtml(lastDest)}</code>`
        : `已导出 ${files} 份文书，共 ${ids.length} 个旨意目录：<br><code>${escapeHtml(dest)}</code>`;
      pushEvent({ kind: 'audit', at: nowStr(), role: 'system', label: '内侍', ok: true,
        action: 'export', detail: `导出 ${files} 份 → ${dest}` });
    } catch (e) {
      res.className = 'export-result err';
      res.textContent = '导出失败：' + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  // ── 总线 ─────────────────────────────────────────────

  function resetFeed() {
    S.lastSeq = 0;
    S.openThought = null;
    S.calls = 0;
    S.tokens = 0;
    S.atBottom = true;
    S.pending = 0;
    stopClock();
    $('#bus').innerHTML = '';
    updateToBottom();
  }

  function whoLabel(ev) {
    if (ev.label) {
      const id = findRoleByLabel(ev.label);
      if (id) return roleLabel(id);
      return ev.label;
    }
    if (ev.role && S.roleMap[ev.role]) return roleLabel(ev.role);
    return '内侍';
  }

  // 只认沙箱里真实存在的职能目录打头，避免把「立册于太子/…」里的"立册于"也算进路径
  const MD_DIRS = ['圣旨', '太子', '中书省', '门下省', '尚书省',
    '户部', '礼部', '兵部', '刑部', '工部', '吏部', '共议'];
  const MD_RE = new RegExp(
    `((?:${MD_DIRS.join('|')})(?:/[\\w\\u4e00-\\u9fa5·\\-（）()]+)*\\.md)`, 'g');

  function linkify(text) {
    return escapeHtml(text).replace(MD_RE, (m) => (
      `<a class="md-link" data-path="${m}">${m}</a>`
    ));
  }

  function setText(node, text) {
    if (!text) return;
    node.innerHTML = linkify(text);
    node.querySelectorAll('a.md-link').forEach((a) => {
      a.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        openArtifact(S.taskId, a.dataset.path);
      });
    });
  }

  function findRoleByLabel(label) {
    const r = S.roles.find((x) => x.label === label || x.name === label);
    return r ? r.id : '';
  }

  function pushEvent(ev) {
    const meta = EV_META[ev.kind] || { tag: ev.kind, cat: 'doc', cls: '' };
    const isDeny = ev.kind === 'audit' && !ev.ok;
    const cat = isDeny ? 'deny' : (ev.kind === 'audit' ? 'doc' : meta.cat);

    if (ev.kind === 'thought' && S.openThought && S.openThought.role === ev.role) {
      S.openThought.textEl.textContent += ev.text || '';
      scrollBus();
      return;
    }
    if (ev.kind !== 'thought') S.openThought = null;

    const row = el('div', `ev ${meta.cls}`);
    row.dataset.cat = cat;
    row.dataset.role = ev.role || '';
    row.appendChild(el('div', 't', ev.at || nowStr()));
    const body = el('div', 'body');
    const line1 = el('div', 'line1');
    line1.appendChild(el('span', 'tag', isDeny ? '朱批' : meta.tag));
    line1.appendChild(el('span', 'who', whoLabel(ev)));

    const textEl = el('div', 'text');
    let text = '';
    switch (ev.kind) {
      case 'call':
        line1.appendChild(el('span', 'arrow', '→'));
        line1.appendChild(el('span', 'who', roleLabel(ev.to)));
        text = ev.instruction || ''; textEl.classList.add('dim'); break;
      case 'call_return':
        line1.appendChild(el('span', 'arrow', '⟵ 回话'));
        text = ev.text || ''; textEl.classList.add('dim'); break;
      case 'state':
        text = `${STATE_CN[ev.from] || ev.from} → ${STATE_CN[ev.to] || ev.to}　${ev.remark || ''}`; break;
      case 'flow':
        line1.appendChild(el('span', 'arrow', '→'));
        line1.appendChild(el('span', 'who', ev.to || '—'));
        text = ev.remark || ''; break;
      case 'progress':
        text = ev.text || ''; if (ev.plan) textEl.classList.add('dim'); break;
      case 'todo':
        text = `${ev.title || ''} → ${ev.status || ''}${ev.detail ? '　' + ev.detail : ''}`; break;
      case 'done_report':
        text = ev.summary || ''; break;
      case 'artifact':
        text = `${ev.path}（${ev.size || 0} 字）`; refreshTasksSoon(); break;
      case 'tool_call':
        text = `${ev.name}(${ev.args || ''})`; textEl.classList.add('mono'); break;
      case 'tool_result':
        text = (ev.ok ? '' : '✗ ') + (ev.content || '') + (ev.ms ? `  ·${ev.ms}ms` : '');
        textEl.classList.add('mono'); if (!ev.ok) textEl.classList.add('dim'); break;
      case 'audit':
        text = `${ev.action}${ev.detail ? '：' + ev.detail : ''}`; break;
      case 'round':
        text = `第 ${ev.round} 轮 · 余 ${ev.budget_left}`; textEl.classList.add('dim'); break;
      case 'role_start':
        text = ev.instruction || ''; textEl.classList.add('dim'); break;
      case 'role_end':
        text = ev.text || ''; textEl.classList.add('dim');
        if (ev.tokens) S.tokens += (ev.tokens.prompt || 0) + (ev.tokens.completion || 0);
        break;
      case 'thought':
        text = ev.text || ''; textEl.classList.add('think-live'); break;
      case 'text':
        text = ev.text || ''; break;
      case 'court_start':
        text = `模型 ${ev.model}` + (ev.round > 1 ? ` · 第 ${ev.round} 轮回奏` : '') + ` · 旨意「${ev.title}」`;
        break;
      case 'followup':
        text = `第 ${ev.round} 轮：${ev.title || ''}${ev.detail ? '　' + ev.detail : ''}`;
        break;
      case 'court_end':
        text = `收朝：${STATE_CN[ev.state] || ev.state} · ${ev.llm_calls} 次调用 · ${ev.seconds}s` +
               (ev.note ? ` · ${ev.note}` : '');
        break;
      case 'block':
        text = ev.reason || ''; break;
      case 'error':
      case 'guard':
        text = ev.message || `重复动作提醒（第 ${ev.count} 次）`; break;
      default:
        text = ev.text || ev.message || '';
    }
    if (text) setText(textEl, text);

    body.appendChild(line1);
    if (text) body.appendChild(textEl);
    row.appendChild(body);
    $('#bus').appendChild(row);

    if (ev.kind === 'thought') S.openThought = { role: ev.role, textEl };
    if (ev.kind === 'round') S.calls += 1;
    if (ev.kind === 'court_start') startClock();
    if (ev.kind === 'court_end') { stopClock(); refreshTasks(); }
    if (ev.kind === 'state' || ev.kind === 'artifact') refreshTasksSoon();

    applyFilterTo(row);
    setMeta();
    scrollBus();
  }

  let lastScrollAt = 0;
  let trailTimer = null;
  function scrollBus(force) {
    const bus = $('#bus');
    if (!(force || S.atBottom)) {
      S.pending += 1;            // 用户正在上翻，不打扰，只计数
      updateToBottom();
      return;
    }
    // 直接赋值即可；不要用 rAF —— 后台标签页 rAF 会被冻结，滚动就再也不生效了
    const run = () => {
      bus.scrollTop = bus.scrollHeight;
      S.atBottom = true;
      updateToBottom();
    };
    const now = performance.now();
    if (force || now - lastScrollAt >= 50) {
      lastScrollAt = now;
      run();
      return;
    }
    // 节流窗口内被跳过的那一次，收尾补一帧，避免停在离底部几十像素的地方
    if (trailTimer) return;
    trailTimer = setTimeout(() => {
      trailTimer = null;
      lastScrollAt = performance.now();
      run();
    }, 60);
  }

  function nearBottom(bus) {
    return bus.scrollHeight - bus.scrollTop - bus.clientHeight < 48;
  }

  function updateToBottom() {
    const btn = $('#btn-to-bottom');
    if (!btn) return;
    btn.hidden = S.atBottom;
    $('#to-bottom-n').textContent = S.pending > 0 ? String(S.pending) : '';
  }

  function onBusScroll() {
    const bus = $('#bus');
    const at = nearBottom(bus);
    if (at) S.pending = 0;
    S.atBottom = at;
    updateToBottom();
  }

  let tasksTimer = null;
  function refreshTasksSoon() {
    if (tasksTimer) return;
    tasksTimer = setTimeout(() => { tasksTimer = null; refreshTasks(); }, 700);
  }

  function applyFilterTo(row) {
    const roleHit = !S.roleFilter || row.dataset.role === S.roleFilter;
    const catHit = S.filter === 'all' || row.dataset.cat === S.filter;
    row.style.display = (catHit && roleHit) ? '' : 'none';
  }
  function applyFilter() { $$('#bus .ev').forEach(applyFilterTo); }

  // ── SSE ──────────────────────────────────────────────

  function connect(taskId) {
    if (S.es) { S.es.close(); S.es = null; }
    const es = new EventSource(`/api/stream/${encodeURIComponent(taskId)}`);
    S.es = es;
    es.onmessage = (m) => {
      let ev;
      try { ev = JSON.parse(m.data); } catch (_) { return; }
      if (ev.kind === 'stream_end') {
        es.close(); S.es = null; stopClock(); refreshTasks();
        if (!$('#bus .ev')) {
          pushEvent({ kind: 'audit', at: nowStr(), role: 'system', label: '内侍', ok: true,
            action: 'no_events', detail: '这道旨意没有留下总线记录（仅看板与产出可查）' });
        }
        return;
      }
      const seq = Number(ev.seq || 0);
      if (seq && seq <= S.lastSeq) return;
      if (seq) S.lastSeq = seq;
      pushEvent(ev);
    };
    es.onerror = () => { /* 浏览器自动重连；收朝后由 stream_end 关闭 */ };
  }

  // ── 角色 / 权限 ──────────────────────────────────────

  function renderRoster() {
    const box = $('#roster');
    box.innerHTML = '';
    S.roles.forEach((r) => {
      const c = el('button', 'rcard');
      c.type = 'button';
      c.dataset.role = r.id;
      const top = el('div', 'top');
      top.appendChild(el('span', 'emoji', r.emoji));
      top.appendChild(el('span', 'label', r.label));
      if (r.can_state) top.appendChild(el('span', 'flag state', '可改状态'));
      if (r.web) top.appendChild(el('span', 'flag', '可联网'));
      c.appendChild(top);
      c.appendChild(el('div', 'name', `${r.name} · ${r.layer === 'coordination' ? '协调' : r.layer === 'execution' ? '执行' : '旁观'}`));
      c.appendChild(el('div', 'duty', r.duty));
      c.appendChild(el('div', 'scope', r.allows_label.length ? '可差遣：' + r.allows_label.join('、') : '末级 · 不可差遣'));
      c.onclick = () => {
        S.roleFilter = (S.roleFilter === r.id) ? null : r.id;
        $$('.rcard').forEach((x) => x.classList.toggle('active', x.dataset.role === S.roleFilter));
        applyFilter();
      };
      box.appendChild(c);
    });
  }

  function renderMatrix() {
    const t = $('#matrix');
    t.innerHTML = '';
    const froms = S.roles.filter((r) => r.allows.length);
    const thead = el('thead');
    const hr = el('tr');
    hr.appendChild(el('th', 'rowh', '可差遣 →'));
    S.roles.forEach((r) => hr.appendChild(el('th', null, r.label)));
    thead.appendChild(hr);
    t.appendChild(thead);
    const tb = el('tbody');
    froms.forEach((r) => {
      const tr = el('tr');
      tr.appendChild(el('th', 'rowh', `${r.emoji} ${r.label}`));
      S.roles.forEach((c) => {
        const yes = r.allows.includes(c.id);
        tr.appendChild(el('td', yes ? 'yes' : 'no', yes ? '●' : '·'));
      });
      tb.appendChild(tr);
    });
    t.appendChild(tb);
  }

  // ── 启动 ─────────────────────────────────────────────

  async function boot() {
    renderPresets();
    $('#btn-send').onclick = sendDecree;
    $('#decree-text').addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') sendDecree();
    });
    $('#btn-abort').onclick = async () => {
      if (!S.taskId) return;
      try { await api(`/api/abort/${encodeURIComponent(S.taskId)}`, { method: 'POST' }); } catch (_) { /* ignore */ }
    };
    $('#filters').addEventListener('click', (e) => {
      const b = e.target.closest('.chip');
      if (!b) return;
      S.filter = b.dataset.filter;
      $$('#filters .chip').forEach((x) => x.classList.toggle('active', x === b));
      applyFilter();
    });
    $('#btn-export').onclick = () => { if (S.taskId) openExport([S.taskId]); };
    const busEl = $('#bus');
    busEl.addEventListener('scroll', onBusScroll, { passive: true });
    $('#btn-to-bottom').onclick = () => {
      S.atBottom = true;
      S.pending = 0;
      scrollBus(true);
      updateToBottom();
    };
    $('#delete-close').onclick = () => { $('#delete-modal').hidden = true; };
    $('#delete-modal').addEventListener('click', (e) => {
      if (e.target.id === 'delete-modal') $('#delete-modal').hidden = true;
    });
    $('#delete-run').onclick = runDelete;
    $('#btn-followup').onclick = openFollowup;
    $('#followup-close').onclick = () => { $('#followup-modal').hidden = true; };
    $('#followup-modal').addEventListener('click', (e) => {
      if (e.target.id === 'followup-modal') $('#followup-modal').hidden = true;
    });
    $('#followup-run').onclick = runFollowup;
    $('#followup-title').addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') runFollowup();
    });
    $('#btn-export-all').onclick = () => {
      const ids = S.tasks.filter((t) => (t.files || []).length).map((t) => t.id);
      if (ids.length) openExport(ids);
    };
    $('#modal-close').onclick = () => { $('#modal').hidden = true; };
    $('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') $('#modal').hidden = true; });
    $('#export-close').onclick = () => { $('#export-modal').hidden = true; };
    $('#export-modal').addEventListener('click', (e) => { if (e.target.id === 'export-modal') $('#export-modal').hidden = true; });
    $('#export-reset').onclick = () => { $('#export-path').value = S.exportDefault || ''; };
    $('#export-run').onclick = runExport;
    $('#export-path').addEventListener('keydown', (e) => { if (e.key === 'Enter') runExport(); });
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      $('#modal').hidden = true;
      $('#export-modal').hidden = true;
      $('#followup-modal').hidden = true;
      $('#delete-modal').hidden = true;
    });

    try {
      const [rolesRes, health] = await Promise.all([api('/api/roles'), api('/api/health')]);
      S.roles = rolesRes.roles || [];
      S.roles.forEach((r) => { S.roleMap[r.id] = r; });
      $('#meta-model').textContent = health.model || '—';
      setWebMeta(health.web);
      if (!(health.web && health.web.available) && health.web && health.web.enabled) {
        const timer = setInterval(async () => {
          try {
            const w = await api('/api/web/status');
            setWebMeta(w);
            if (w.available) clearInterval(timer);
          } catch (_) { /* 网关还没起，下次再试 */ }
        }, 5000);
      }
      renderRoster();
      renderMatrix();
      if (!health.key_present) {
        pushEvent({ kind: 'error', at: nowStr(), message: '未检测到 DEEPSEEK_API_KEY —— 请检查项目根目录的 .env' });
      }
    } catch (e) {
      pushEvent({ kind: 'error', at: nowStr(), message: '加载角色失败：' + e.message });
    }

    try { S.exportDefault = (await api('/api/export/default')).dest; } catch (_) { /* ignore */ }

    await refreshTasks();
    const last = localStorage.getItem('edict.lastTask');
    const cur = S.tasks.find((t) => t.id === last) || S.tasks[0];
    if (cur) {
      await selectTask(cur.id);
      if (cur.status === 'running') startClock();
    } else {
      renderRail(null);
      renderArtifacts();
    }
  }

  boot();

  // 仅在 #debug 下暴露内部入口，方便自动化验证总线行为（不影响正常使用）
  if (location.hash === '#debug') {
    window.__hermesBus = { push: pushEvent, state: S };
  }
})();
