// 牛客代码收集器 - background service worker
// 职责：状态存储（chrome.storage.local）、导出下载、相似度计算、消息路由。

const STORE_KEY = 'ncc_submissions';
const QUEUE_KEY = 'ncc_queue';
const CONFIG_KEY = 'ncc_config';

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get([STORE_KEY, QUEUE_KEY], (d) => {
    const patch = {};
    if (!Array.isArray(d[STORE_KEY])) patch[STORE_KEY] = [];
    if (!Array.isArray(d[QUEUE_KEY])) patch[QUEUE_KEY] = [];
    if (Object.keys(patch).length) chrome.storage.local.set(patch);
  });
});

function getSubmissions() {
  return new Promise((r) => chrome.storage.local.get([STORE_KEY], (d) => r(Array.isArray(d[STORE_KEY]) ? d[STORE_KEY] : [])));
}
function setSubmissions(list) {
  return new Promise((r) => chrome.storage.local.set({ [STORE_KEY]: list }, () => r(list)));
}
function getQueue() {
  return new Promise((r) => chrome.storage.local.get([QUEUE_KEY], (d) => r(Array.isArray(d[QUEUE_KEY]) ? d[QUEUE_KEY] : [])));
}
function setQueue(q) {
  return new Promise((r) => chrome.storage.local.set({ [QUEUE_KEY]: q }, () => r(q)));
}

function uid() {
  try { if (crypto.randomUUID) return crypto.randomUUID(); } catch (e) {}
  return 'id-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

// ---------- 代码指标 ----------
function computeMetrics(code) {
  const lines = (code || '').split(/\r?\n/);
  let loc = 0, blanks = 0, comments = 0;
  let inBlock = false, blockChar = '';
  for (let l of lines) {
    const t = l.trim();
    if (!t) { blanks++; continue; }
    if (inBlock) {
      comments++;
      if (blockChar === '"""' || blockChar === "'''") {
        if (t.includes(blockChar)) inBlock = false;
      } else if (t.includes('*/')) inBlock = false;
      continue;
    }
    if (t.startsWith('/*')) { comments++; if (!t.includes('*/')) { inBlock = true; blockChar = '/*'; } continue; }
    if (t.startsWith('//') || t.startsWith('#')) { comments++; continue; }
    if (t.startsWith('"""') || t.startsWith("'''")) {
      comments++;
      const q = t.slice(0, 3);
      if (t.length <= 3 || !t.slice(3).includes(q)) { inBlock = true; blockChar = q; }
      continue;
    }
    loc++;
  }
  const total = lines.length;
  const commentRatio = total ? comments / total : 0;
  return { loc, blanks, comments, commentRatio: Math.round(commentRatio * 1000) / 1000 };
}

// ---------- 相似度（Winnowing 指纹 + Jaccard） ----------
function normalizeCode(code) {
  return (code || '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/[^\n]*/g, '')
    .replace(/^[ \t]*#[^\n]*/gm, '')
    .replace(/\s+/g, '');
}
function hashStr(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
  return h >>> 0;
}
function winnow(code, k, w) {
  k = k || 5; w = w || 4;
  const s = normalizeCode(code);
  if (s.length < k + w) return [];
  const hashes = [];
  for (let i = 0; i + k <= s.length; i++) hashes.push(hashStr(s.slice(i, i + k)));
  const fp = new Set();
  for (let i = 0; i + w <= hashes.length; i++) {
    let m = Infinity;
    for (let j = i; j < i + w; j++) if (hashes[j] < m) m = hashes[j];
    fp.add(m);
  }
  return Array.from(fp);
}
function jaccard(a, b) {
  if (!a || !b || !a.length || !b.length) return 0;
  const sa = new Set(a), sb = new Set(b);
  let inter = 0;
  for (const x of sa) if (sb.has(x)) inter++;
  return inter / (sa.size + sb.size - inter);
}

// ---------- 保存 ----------
async function saveSubmission(submission) {
  if (!submission || !submission.code || !submission.code.trim()) return { ok: false, error: '空代码' };
  const list = await getSubmissions();
  const metrics = computeMetrics(submission.code);
  const item = {
    id: uid(),
    code: submission.code,
    contestId: submission.contestId || '',
    contestName: submission.contestName || '',
    problem: submission.problem || '',
    problemIndex: submission.problemIndex || '',
    student: submission.student || '',
    studentId: submission.studentId || '',
    language: submission.language || '',
    verdict: submission.verdict || '',
    score: submission.score || '',
    submissionId: submission.submissionId || '',
    url: submission.url || '',
    capturedAt: submission.capturedAt || new Date().toISOString(),
    metrics
  };
  const dup = list.find((s) =>
    (item.url && s.url === item.url) ||
    (item.submissionId && s.submissionId === item.submissionId && s.student === item.student)
  );
  if (dup) return { ok: true, duplicate: true, id: dup.id, count: list.length };
  list.push(item);
  await setSubmissions(list);
  return { ok: true, duplicate: false, id: item.id, count: list.length };
}

async function getState() {
  const [submissions, queue] = await Promise.all([getSubmissions(), getQueue()]);
  return { ok: true, submissions, queue, stats: buildStats(submissions) };
}
function buildStats(list) {
  const students = new Set(), problems = new Set(), contests = new Set();
  list.forEach((s) => {
    if (s.student) students.add(s.student);
    if (s.problem || s.problemIndex) problems.add(s.problem || s.problemIndex);
    if (s.contestId) contests.add(s.contestId);
  });
  return { total: list.length, students: students.size, problems: problems.size, contests: contests.size };
}

// ---------- 导出 ----------
function extForLang(lang) {
  const l = (lang || '').toLowerCase();
  if (/c\+\+|cpp|g\+\+|clang\s*\+\+/.test(l)) return 'cpp';
  if (/c#|csharp/.test(l)) return 'cs';
  if (/objective-c|objc/.test(l)) return 'm';
  if (/\bc\b|^c$|c11|c99|c17|c23|gcc|clang/.test(l)) return 'c';
  if (/java/.test(l)) return 'java';
  if (/python|pypy/.test(l)) return 'py';
  if (/golang|^go$/.test(l)) return 'go';
  if (/javascript|node/.test(l)) return 'js';
  if (/typescript/.test(l)) return 'ts';
  if (/rust/.test(l)) return 'rs';
  if (/php/.test(l)) return 'php';
  if (/kotlin/.test(l)) return 'kt';
  if (/swift/.test(l)) return 'swift';
  if (/ruby/.test(l)) return 'rb';
  if (/pascal|delphi/.test(l)) return 'pas';
  if (/haskell/.test(l)) return 'hs';
  if (/scala/.test(l)) return 'scala';
  if (/perl/.test(l)) return 'pl';
  if (/ocaml/.test(l)) return 'ml';
  return 'txt';
}
function sanitize(s) {
  return (s == null ? '' : String(s))
    .replace(/[\\/:*?"<>|\r\n\t]+/g, '_')
    .replace(/\s+/g, '_')
    .slice(0, 60)
    .replace(/^_+|_+$/g, '') || 'unknown';
}

function buildFiles(subs, groupBy) {
  const files = [];
  const used = new Map();
  subs.forEach((s, i) => {
    const ext = extForLang(s.language);
    const student = sanitize(s.student);
    const problem = sanitize(s.problem || s.problemIndex);
    const idx = sanitize(s.problemIndex);
    const lang = sanitize(s.language);
    const base = `${student}_${idx || problem}_${lang || 'code'}`;
    let folder = '';
    if (groupBy === 'student') folder = student + '/';
    else if (groupBy === 'problem') folder = problem + '/';
    else if (groupBy === 'contest') folder = sanitize(s.contestId || s.contestName) + '/';
    let fname = `${folder}${base}.${ext}`;
    const n = used.get(fname) || 0;
    used.set(fname, n + 1);
    if (n > 0) fname = `${folder}${base}_${n + 1}.${ext}`;
    files.push({ filename: fname, content: s.code || '' });
  });
  return files;
}

async function downloadFiles(files) {
  if (!files.length) return { ok: false, error: '没有可导出的提交' };
  let done = 0;
  for (const f of files) {
    const blob = new Blob([f.content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    await new Promise((resolve) => {
      chrome.downloads.download({ url, filename: f.filename, saveAs: false, conflictAction: 'uniquify' }, () => {
        setTimeout(() => { try { URL.revokeObjectURL(url); } catch (e) {} resolve(); }, 250);
      });
    });
    done++;
  }
  return { ok: true, downloaded: done };
}

function fenceFor(code) {
  let f = '```';
  while ((code || '').includes(f)) f += '`';
  return f;
}
function buildCombined(subs) {
  const sorted = [...subs].sort((a, b) =>
    (a.student || '').localeCompare(b.student || '', 'zh-CN') ||
    (a.problem || a.problemIndex || '').localeCompare(b.problem || b.problemIndex || '', 'zh-CN')
  );
  const L = [];
  L.push('# 牛客代码收集合集');
  L.push('');
  L.push(`- 导出时间：${new Date().toLocaleString('zh-CN')}`);
  L.push(`- 提交总数：${subs.length}`);
  L.push('');
  for (const s of sorted) {
    const tag = [s.language, s.verdict].filter(Boolean).join(' · ');
    L.push(`## ${s.student || '未知'} — ${s.problem || s.problemIndex || s.submissionId || '?'}${tag ? '（' + tag + '）' : ''}`);
    if (s.url) L.push(`- 来源：${s.url}`);
    const m = s.metrics;
    if (m) L.push(`- 行数 ${m.loc}，注释 ${m.comments}，注释占比 ${Math.round(m.commentRatio * 100)}%`);
    L.push('');
    const f = fenceFor(s.code);
    L.push(f + extForLang(s.language));
    L.push(s.code || '');
    L.push(f);
    L.push('');
  }
  return L.join('\n');
}

function pick(s) {
  return {
    id: s.id,
    student: s.student || s.studentId || '未知',
    problem: s.problem || s.problemIndex || s.submissionId || '?',
    language: s.language,
    url: s.url
  };
}
async function similarity(threshold) {
  const t = threshold || 0.5;
  const subs = await getSubmissions();
  const n = subs.length;
  const fps = subs.map((s) => winnow(s.code));
  const pairs = [];
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const score = jaccard(fps[i], fps[j]);
      if (score >= t) pairs.push({ a: pick(subs[i]), b: pick(subs[j]), score: Math.round(score * 1000) / 1000 });
    }
  }
  pairs.sort((x, y) => y.score - x.score);
  return { ok: true, pairs, total: n };
}

function buildSimilarityReport(pairs, total) {
  const L = [];
  L.push('# 牛客代码相似度报告');
  L.push('');
  L.push(`- 生成时间：${new Date().toLocaleString('zh-CN')}`);
  L.push(`- 参与比较提交：${total}`);
  L.push(`- 高相似对数量：${pairs.length}`);
  L.push('');
  L.push('> 说明：基于 Winnowing 指纹 + Jaccard 相似度。相似度高通常意味着代码高度雷同（可能互相抄袭或使用同一模板），仅作审查线索，不作最终判定。');
  L.push('');
  if (!pairs.length) {
    L.push('未发现明显相似的提交对。');
    return L.join('\n');
  }
  L.push('| 提交 A | 提交 B | 相似度 |');
  L.push('| --- | --- | --- |');
  for (const p of pairs) {
    const a = `${p.a.student}/${p.a.problem}`;
    const b = `${p.b.student}/${p.b.problem}`;
    L.push(`| ${a} | ${b} | ${(p.score * 100).toFixed(1)}% |`);
  }
  return L.join('\n');
}

async function exportFiles(options) {
  const subs = await getSubmissions();
  if (!subs.length) return { ok: false, error: '没有可导出的提交' };
  const files = buildFiles(subs, (options && options.groupBy) || 'student');
  return downloadFiles(files);
}
async function exportCombined() {
  const subs = await getSubmissions();
  if (!subs.length) return { ok: false, error: '没有可导出的提交' };
  const text = buildCombined(subs);
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const filename = `牛客代码合集_${new Date().toISOString().slice(0, 10)}.md`;
  await new Promise((r) => chrome.downloads.download({ url, filename, saveAs: true }, () => setTimeout(() => { URL.revokeObjectURL(url); r(); }, 300)));
  return { ok: true, downloaded: 1 };
}
async function exportSimilarity(pairs, total) {
  const text = buildSimilarityReport(pairs || [], total || 0);
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const filename = `牛客相似度报告_${new Date().toISOString().slice(0, 10)}.md`;
  await new Promise((r) => chrome.downloads.download({ url, filename, saveAs: true }, () => setTimeout(() => { URL.revokeObjectURL(url); r(); }, 300)));
  return { ok: true };
}

async function captureCurrent() {
  const tabs = await new Promise((r) => chrome.tabs.query({ active: true, currentWindow: true }, r));
  if (!tabs.length) return { ok: false, error: '无活动标签页' };
  try {
    const r = await new Promise((res) => chrome.tabs.sendMessage(tabs[0].id, { type: 'doCapture' }, (resp) => res(resp || { ok: false, error: '无响应' })));
    return r;
  } catch (e) {
    return { ok: false, error: '请先打开牛客网的代码页（脚本未注入或页面不对）' };
  }
}

async function diagnoseCurrent() {
  const tabs = await new Promise((r) => chrome.tabs.query({ active: true, currentWindow: true }, r));
  if (!tabs.length) return { ok: false, error: '无活动标签页' };
  try {
    const info = await new Promise((res) => chrome.tabs.sendMessage(tabs[0].id, { type: 'diagnose' }, (resp) => res(resp || null)));
    if (!info) return { ok: false, error: '诊断失败' };
    const text = JSON.stringify(info, null, 2);
    const blob = new Blob([text], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const filename = `牛客诊断_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`;
    await new Promise((r) => chrome.downloads.download({ url, filename, saveAs: true }, () => setTimeout(() => { URL.revokeObjectURL(url); r(); }, 300)));
    return { ok: true, downloaded: 1, pageType: info.pageType };
  } catch (e) {
    return { ok: false, error: '当前页不在牛客网，或脚本未注入' };
  }
}

// ---------- 消息路由 ----------
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      switch (msg && msg.type) {
        case 'saveSubmission': return await saveSubmission(msg.submission);
        case 'getState': return await getState();
        case 'deleteSubmission': {
          let list = await getSubmissions();
          list = list.filter((s) => s.id !== msg.id);
          await setSubmissions(list);
          return { ok: true };
        }
        case 'clearAll': await setSubmissions([]); return { ok: true };
        case 'updateSubmission': {
          let list = await getSubmissions();
          list = list.map((s) => {
            if (s.id === msg.id) {
              const next = { ...s, ...(msg.patch || {}) };
              if (msg.patch && msg.patch.code !== undefined) next.metrics = computeMetrics(next.code);
              return next;
            }
            return s;
          });
          await setSubmissions(list);
          return { ok: true };
        }
        case 'setQueue': await setQueue(msg.items || []); return { ok: true };
        case 'nextQueue': {
          let q = await getQueue();
          const item = q.shift();
          await setQueue(q);
          return { ok: true, item: item || null, remaining: q.length };
        }
        case 'exportFiles': return await exportFiles(msg.options);
        case 'exportCombined': return await exportCombined();
        case 'similarity': return await similarity(msg.threshold);
        case 'exportSimilarity': return await exportSimilarity(msg.pairs, msg.total);
        case 'captureCurrent': return await captureCurrent();
        case 'diagnoseCurrent': return await diagnoseCurrent();
        default: return { ok: false, error: '未知消息类型' };
      }
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    }
  })().then(sendResponse);
  return true;
});
