// popup.js - 管理面板
let state = { submissions: [], stats: {}, queue: [] };

const $ = (id) => document.getElementById(id);

function send(msg) {
  return new Promise((res) => {
    chrome.runtime.sendMessage(msg, (r) => res(r || { ok: false }));
  });
}

function toast(text) {
  let el = $('toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.className = 'toast show';
  clearTimeout(el.__t);
  el.__t = setTimeout(() => el.className = 'toast', 1600);
}

function fmtDate(iso) {
  try { return new Date(iso).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
  catch (e) { return ''; }
}
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function filtered() {
  const q = $('search').value.trim().toLowerCase();
  let list = state.submissions.slice().reverse();
  if (q) {
    list = list.filter((s) =>
      [s.student, s.problem, s.problemIndex, s.language, s.verdict, s.contestId, s.submissionId]
        .filter(Boolean).join(' ').toLowerCase().includes(q)
    );
  }
  return list;
}

async function refresh() {
  const r = await send({ type: 'getState' });
  if (r && r.ok) state = r;
  render();
}

function render() {
  $('stats').textContent = `提交 ${state.stats.total} · 学生 ${state.stats.students} · 题目 ${state.stats.problems} · 比赛 ${state.stats.contests}`;
  const list = filtered();
  const box = $('list');
  box.innerHTML = '';
  $('empty').classList.toggle('hidden', list.length > 0);
  for (const s of list) box.appendChild(renderItem(s));
}

function renderItem(s) {
  const div = document.createElement('div');
  div.className = 'item';
  const head = document.createElement('div');
  head.className = 'item-head';
  head.innerHTML =
    `<div class="item-title"><b>${esc(s.student || '未知')}</b> <span class="muted">${esc(s.problem || s.problemIndex || s.submissionId || '?')}</span></div>` +
    `<div class="item-meta">` +
      `<span class="tag">${esc(s.language || '?')}</span>` +
      `<span class="tag ${/accept|通过|正确/i.test(s.verdict || '') ? 'ok' : ''}">${esc(s.verdict || '未判')}</span>` +
      `<span class="muted">${fmtDate(s.capturedAt)}</span>` +
    `</div>`;
  head.addEventListener('click', () => {
    div.classList.toggle('open');
    if (div.classList.contains('open')) renderDetail(div, s);
  });
  div.appendChild(head);
  const detail = document.createElement('div');
  detail.className = 'detail';
  div.appendChild(detail);
  return div;
}

function renderDetail(div, s) {
  const detail = div.querySelector('.detail');
  if (detail.dataset.rendered) return;
  detail.dataset.rendered = '1';
  const m = s.metrics || {};
  detail.innerHTML =
    `<div class="detail-meta">` +
      `<label>姓名 <input data-k="student" value="${esc(s.student)}"></label>` +
      `<label>题目 <input data-k="problem" value="${esc(s.problem || '')}"></label>` +
      `<label>语言 <input data-k="language" value="${esc(s.language)}"></label>` +
      `<label>结果 <input data-k="verdict" value="${esc(s.verdict)}"></label>` +
      `<span class="muted">${m.loc || 0} 行 · 注释 ${m.comments || 0}（${Math.round((m.commentRatio || 0) * 100)}%）</span>` +
      `<div class="detail-actions">` +
        `<button class="btn small" data-act="save">保存修改</button>` +
        `<button class="btn small danger" data-act="del">删除</button>` +
        `<button class="btn small" data-act="copy">复制代码</button>` +
      `</div>` +
    `</div>` +
    `<textarea class="code" spellcheck="false">${esc(s.code || '')}</textarea>`;

  detail.querySelector('[data-act="save"]').addEventListener('click', async () => {
    const patch = {};
    detail.querySelectorAll('input[data-k]').forEach((inp) => { patch[inp.dataset.k] = inp.value; });
    patch.code = detail.querySelector('textarea.code').value;
    await send({ type: 'updateSubmission', id: s.id, patch });
    toast('已保存');
    refresh();
  });
  detail.querySelector('[data-act="del"]').addEventListener('click', async () => {
    if (!confirm('删除这条提交？')) return;
    await send({ type: 'deleteSubmission', id: s.id });
    refresh();
  });
  detail.querySelector('[data-act="copy"]').addEventListener('click', async () => {
    const code = detail.querySelector('textarea.code').value;
    try { await navigator.clipboard.writeText(code); toast('已复制'); }
    catch (e) { detail.querySelector('textarea.code').select(); document.execCommand('copy'); toast('已复制'); }
  });
}

async function showSimilarity() {
  const r = await send({ type: 'similarity', threshold: 0.5 });
  if (!r || !r.ok) return;
  const panel = $('similarityPanel');
  panel.classList.remove('hidden');
  const pairs = r.pairs || [];
  let html = `<div class="sim-title">相似度报告（${pairs.length} 对）</div>`;
  if (!pairs.length) {
    html += `<div class="muted">未发现相似度 ≥50% 的提交对。</div>`;
  } else {
    html += pairs.slice(0, 30).map((p) =>
      `<div class="sim-row">${esc(p.a.student)}/${esc(p.a.problem)} ↔ ${esc(p.b.student)}/${esc(p.b.problem)}：<b>${(p.score * 100).toFixed(1)}%</b></div>`
    ).join('');
    if (pairs.length > 30) html += `<div class="muted">… 仅显示前 30 对</div>`;
  }
  html += `<button class="btn small" data-act="export" style="margin-top:8px">导出完整报告</button>`;
  panel.innerHTML = html;
  panel.querySelector('[data-act="export"]').addEventListener('click', async () => {
    await send({ type: 'exportSimilarity', pairs, total: r.total });
    toast('报告已导出');
  });
}

$('captureBtn').addEventListener('click', async () => {
  const r = await send({ type: 'captureCurrent' });
  if (r && r.ok) { toast('已采集'); refresh(); }
  else toast('采集失败：' + ((r && r.error) || '请打开牛客网代码页'));
});
$('exportFilesBtn').addEventListener('click', async () => {
  const r = await send({ type: 'exportFiles', options: { groupBy: $('groupBy').value } });
  if (r && r.ok) toast(`已导出 ${r.downloaded} 个文件`);
  else toast('导出失败：' + ((r && r.error) || ''));
});
$('exportCombinedBtn').addEventListener('click', async () => {
  const r = await send({ type: 'exportCombined' });
  if (r && r.ok) toast('合集已导出');
  else toast('导出失败：' + ((r && r.error) || ''));
});
$('similarityBtn').addEventListener('click', showSimilarity);
$('diagnoseBtn').addEventListener('click', async () => {
  const r = await send({ type: 'diagnoseCurrent' });
  if (r && r.ok) toast(`诊断已导出（页面类型：${r.pageType}）`);
  else toast('诊断失败：' + ((r && r.error) || '请先打开牛客网页面'));
});
$('clearBtn').addEventListener('click', async () => {
  if (!confirm('清空全部采集的代码？')) return;
  await send({ type: 'clearAll' });
  refresh();
  toast('已清空');
});
$('search').addEventListener('input', render);

refresh();
