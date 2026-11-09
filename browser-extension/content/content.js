// content.js - 页面集成：浮动按钮、快捷键、基于 API 的批量采集
(function () {
  if (window.__NCC_CONTENT_LOADED__) return;
  window.__NCC_CONTENT_LOADED__ = true;

  const EX = window.__NCC_EXTRACTOR__;
  const isTop = window === window.top;

  function send(msg) {
    return new Promise((res) => {
      try {
        chrome.runtime.sendMessage(msg, (r) => res(r || { ok: false }));
      } catch (e) {
        res({ ok: false, error: String(e) });
      }
    });
  }

  function lineCount(c) { return (c || '').split('\n').length; }

  function toast(text, kind) {
    let el = document.getElementById('__ncc_toast');
    if (!el) {
      el = document.createElement('div');
      el.id = '__ncc_toast';
      el.style.cssText = 'position:fixed;left:16px;bottom:16px;z-index:2147483647;padding:10px 14px;border-radius:8px;font:13px/1.4 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;color:#fff;box-shadow:0 6px 18px rgba(0,0,0,.25);opacity:1;transition:opacity .3s;pointer-events:none;';
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.style.background = kind === 'ok' ? '#16a34a' : '#dc2626';
    el.style.opacity = '1';
    clearTimeout(el.__t);
    el.__t = setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 400); }, 3200);
  }

  async function capture(preferSelection) {
    let code = preferSelection ? (window.getSelection() ? window.getSelection().toString() : '') : '';
    if (!code || !code.trim()) code = EX.extractCode();
    if (!code || !code.trim()) code = window.getSelection() ? window.getSelection().toString() : '';
    if (!code || !code.trim()) { toast('未检测到代码，请先选中代码或打开代码页', 'error'); return { ok: false }; }
    const meta = EX.extractMeta();
    const r = await send({ type: 'saveSubmission', submission: Object.assign({}, meta, { code }) });
    if (r && r.ok) {
      const who = [meta.student, meta.problem || meta.problemIndex].filter(Boolean).join(' ');
      toast(`${r.duplicate ? '已存在：' : '已保存：'}${who || '代码'}（${lineCount(code)} 行）`, 'ok');
    } else {
      toast('保存失败：' + ((r && r.error) || '未知'), 'error');
    }
    return r;
  }

  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && (e.key === 'S' || e.key === 's')) {
      e.preventDefault();
      capture(false);
    }
  });

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg && msg.type === 'doCapture') {
      (async () => { sendResponse(await capture(false)); })();
      return true;
    }
    if (msg && msg.type === 'diagnose') {
      sendResponse(EX.diagnose());
      return false;
    }
    if (msg && msg.type === 'batchCollect') {
      (async () => { sendResponse(await batchCollect()); })();
      return true;
    }
  });

  function injectFab() {
    if (!isTop || document.getElementById('__ncc_fab')) return;
    const fab = document.createElement('div');
    fab.id = '__ncc_fab';
    fab.textContent = '📥 收集代码';
    fab.title = '保存当前页面的代码（Ctrl+Shift+S）';
    fab.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483646;background:#2563eb;color:#fff;padding:10px 16px;border-radius:999px;font:14px/1 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;cursor:pointer;box-shadow:0 6px 18px rgba(0,0,0,.3);user-select:none;';
    fab.addEventListener('click', () => {
      const sel = window.getSelection() ? window.getSelection().toString() : '';
      capture(sel && sel.trim().length > 0);
    });
    document.body.appendChild(fab);
  }

  function injectBatchBtn() {
    if (!isTop || document.getElementById('__ncc_batch')) return;
    const btn = document.createElement('div');
    btn.id = '__ncc_batch';
    btn.textContent = '📚 批量收集本场比赛全部提交';
    btn.style.cssText = 'position:fixed;right:16px;bottom:64px;z-index:2147483646;background:#7c3aed;color:#fff;padding:10px 16px;border-radius:999px;font:14px/1 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;cursor:pointer;box-shadow:0 6px 18px rgba(0,0,0,.3);user-select:none;';
    btn.addEventListener('click', () => batchCollect());
    document.body.appendChild(btn);
  }

  // ---------- 批量采集（基于牛客真实 API） ----------
  async function fetchJSON(url) {
    const resp = await fetch(url, { credentials: 'include' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return await resp.json();
  }

  async function fetchCodeOf(submissionId) {
    try {
      const resp = await fetch(`/acm/contest/view-submission?submissionId=${submissionId}`, { credentials: 'include' });
      if (!resp.ok) return '';
      const html = await resp.text();
      const doc = new DOMParser().parseFromString(html, 'text/html');
      return EX.extractCodeFromDoc(doc) || '';
    } catch (e) { return ''; }
  }

  async function batchCollect() {
    const cid = EX.extractContestId();
    if (!cid) { toast('未识别到比赛 ID', 'error'); return { ok: false, error: 'no contest id' }; }

    // 1) 拉取全部提交列表（分页）
    toast('正在获取提交列表…', 'ok');
    let subs = [];
    try {
      const first = await fetchJSON(`/acm/contest/status-list?token=&id=${cid}&page=1&pageSize=50`);
      const bi = (first && first.data && first.data.basicInfo) || {};
      const pageCount = bi.pageCount || 1;
      const total = bi.statusCount || 0;
      subs = ((first.data && first.data.data) || []).slice();
      for (let p = 2; p <= pageCount; p++) {
        const r = await fetchJSON(`/acm/contest/status-list?token=&id=${cid}&page=${p}&pageSize=50`);
        subs = subs.concat((r.data && r.data.data) || []);
      }
      toast(`共 ${subs.length} 条提交，开始采集代码…`, 'ok');
    } catch (e) {
      toast('获取提交列表失败：' + (e && e.message), 'error');
      return { ok: false, error: String(e && e.message) };
    }

    // 2) 逐条抓代码页（服务端渲染，直接取 pre.prettyprint）
    let ok = 0, fail = 0;
    const fails = [];
    for (let i = 0; i < subs.length; i++) {
      const it = subs[i];
      const code = await fetchCodeOf(it.submissionId);
      const sub = {
        student: it.userName || '',
        studentId: String(it.userId || ''),
        problemIndex: it.index || '',
        problem: it.index || '',
        language: it.languageName || it.languageCategoryName || '',
        verdict: it.statusMessage || '',
        score: String(it.score != null ? it.score : ''),
        submissionId: String(it.submissionId || ''),
        contestId: cid,
        code: code || '',
        url: `https://ac.nowcoder.com/acm/contest/view-submission?submissionId=${it.submissionId}`,
        capturedAt: new Date().toISOString()
      };
      if (code && code.trim()) {
        const r = await send({ type: 'saveSubmission', submission: sub });
        if (r && r.ok) ok++; else { fail++; fails.push(sub.url); }
      } else { fail++; fails.push(sub.url); }
      if (i % 5 === 0 || i === subs.length - 1) {
        toast(`采集 ${i + 1}/${subs.length}（成功 ${ok} / 失败 ${fail}）`, 'ok');
      }
    }
    toast(`批量完成：成功 ${ok}，失败 ${fail}`, ok ? 'ok' : 'error');
    if (fails.length) console.warn('[牛客代码收集器] 未采集到的提交：', fails);
    return { ok: ok > 0, okCount: ok, failCount: fail };
  }

  function init() {
    if (!document.body) { setTimeout(init, 200); return; }
    if (!isTop) return;
    const type = EX.detectPageType();
    if (type === 'code') injectFab();
    else if (type === 'contest' || type === 'status') injectBatchBtn();
    else injectFab();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
