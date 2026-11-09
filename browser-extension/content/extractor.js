// extractor.js - 从牛客网页面提取代码与元信息
// 已按真实页面结构精修：pre.prettyprint / a.long-user-name / status-list JSON 字段
(function () {
  const LANGS = /\b(C#|C\+\+\d*|Java|Python3?|PyPy3?|Go|Golang|JavaScript|TypeScript|Rust|PHP|Kotlin|Swift|Ruby|Objective-C|Perl|Haskell|Scala|Pascal|C\d*)(?![A-Za-z])/i;
  const VERDICTS = /(Accepted|答案正确|通过|Wrong Answer|答案错误|Runtime Error|运行错误|Time Limit Exceeded|时间超限|超时|Memory Limit Exceeded|内存超限|Compile Error|编译错误|Presentation Error|格式错误|Output Limit Exceeded|输出超限|System Error|系统错误|段错误|浮点错误)/i;
  const CODE_HINT = /\b(int|double|char|void|bool|long|short|float|public|class|struct|def|import|from|package|include|using|namespace|return|for|while|if|else|switch|case|typedef|const|static|template|let|var|function|fn|print|printf|scanf|cin|cout|auto|unsigned|signed)\b|#\s*include|[{};=<>]|\b(long\s+long)\b/;

  function looksLikeCode(s) {
    if (!s || s.trim().length < 3) return false;
    return CODE_HINT.test(s) || /[{};]/.test(s) || /^\s*(#\s*include|import\s|def\s|public\s+class)/m.test(s);
  }

  function normalizeLang(s) {
    if (!s) return '';
    const t = s.trim();
    if (/^c\+\+\d*$/i.test(t)) return t;
    if (/c\+\+/i.test(t)) return 'C++';
    if (/^c#$/i.test(t) || /csharp/i.test(t)) return 'C#';
    if (/objective-c|objc/i.test(t)) return 'Objective-C';
    if (/^c\d*$/i.test(t) || /\bc\b/i.test(t)) return 'C';
    if (/java/i.test(t)) return 'Java';
    if (/pypy/i.test(t)) return 'PyPy3';
    if (/python/i.test(t)) return 'Python3';
    if (/golang|^go$/i.test(t)) return 'Go';
    if (/javascript|node/i.test(t)) return 'JavaScript';
    if (/typescript/i.test(t)) return 'TypeScript';
    if (/rust/i.test(t)) return 'Rust';
    if (/php/i.test(t)) return 'PHP';
    if (/kotlin/i.test(t)) return 'Kotlin';
    if (/swift/i.test(t)) return 'Swift';
    if (/ruby/i.test(t)) return 'Ruby';
    if (/pascal|delphi/i.test(t)) return 'Pascal';
    if (/haskell/i.test(t)) return 'Haskell';
    if (/scala/i.test(t)) return 'Scala';
    if (/perl/i.test(t)) return 'Perl';
    return t;
  }

  function absolutize(href) {
    try { return new URL(href, location.href).href; } catch (e) { return href; }
  }

  function reconstructLines(container) {
    if (!container) return '';
    for (const cls of ['.cm-line', '.ace_line', '.CodeMirror-line', '.cl']) {
      const els = container.querySelectorAll(cls);
      if (els && els.length) return Array.from(els).map((e) => e.textContent || '').join('\n');
    }
    return container.textContent || '';
  }

  // 只依赖 DOM 的策略（用于 fetch 到的静态 HTML，牛客代码页是服务端渲染）
  function extractCodeFromDoc(doc) {
    const d = doc || document;
    // 1) 牛客精确：pre.prettyprint（代码已 HTML 转义，textContent 自动还原）
    const pp = d.querySelector('pre.prettyprint');
    if (pp) {
      const v = pp.textContent || '';
      if (v && v.trim().length > 10) return v;
    }
    // 2) textarea
    const tas = d.querySelectorAll('textarea');
    for (const ta of tas) {
      const v = ta.value || ta.textContent || '';
      if (v && v.trim().length > 20 && looksLikeCode(v)) return v;
    }
    // 3) 任意 pre
    let best = '';
    d.querySelectorAll('pre').forEach((p) => { const v = p.textContent || ''; if (looksLikeCode(v) && v.length > best.length) best = v; });
    if (best) return best;
    // 4) code
    d.querySelectorAll('code').forEach((c) => { const v = c.textContent || ''; if (looksLikeCode(v) && v.length > best.length) best = v; });
    if (best) return best;
    // 5) 编辑器行容器
    const cont = d.querySelector('.cm-content') || d.querySelector('.cm-editor') ||
      d.querySelector('.ace_layer.ace_text-layer') || d.querySelector('.ace_editor') ||
      d.querySelector('.CodeMirror-lines') || d.querySelector('.CodeMirror-code');
    const rec = reconstructLines(cont);
    if (rec && looksLikeCode(rec)) return rec;
    return genericScan(d);
  }

  function extractCodeLive() {
    // 1) 牛客精确容器
    const pp = document.querySelector('pre.prettyprint');
    if (pp) {
      const v = pp.textContent || '';
      if (v && v.trim().length > 10) return v;
    }
    // 2) Monaco
    try {
      if (window.monaco && window.monaco.editor && window.monaco.editor.getModels) {
        const models = window.monaco.editor.getModels();
        for (const m of models) {
          let v = '';
          try { v = m.getValue ? m.getValue() : ''; } catch (e) {}
          if (v && v.trim() && looksLikeCode(v)) return v;
        }
      }
    } catch (e) {}
    // 3) Ace
    try {
      if (window.ace) {
        const els = document.querySelectorAll('.ace_editor');
        for (const el of els) {
          let v = '';
          try { const ed = window.ace.edit(el); if (ed) v = ed.getValue(); } catch (e) {}
          if (v && v.trim() && looksLikeCode(v)) return v;
        }
      }
    } catch (e) {}
    // 4) CodeMirror
    const cmEls = document.querySelectorAll('.CodeMirror');
    for (const el of cmEls) {
      let v = '';
      try { if (el.CodeMirror) v = el.CodeMirror.getValue(); } catch (e) {}
      if (!v) v = reconstructLines(el);
      if (v && v.trim() && looksLikeCode(v)) return v;
    }
    return extractCodeFromDoc(document);
  }

  function genericScan(d) {
    const d2 = d || document;
    let best = { v: '', score: 0 };
    d2.querySelectorAll('div, pre, section, article, td').forEach((el) => {
      if (el.children && el.children.length > 25) return;
      let isMono = false;
      try { isMono = /mono|consolas|menlo|courier|monospace/i.test(getComputedStyle(el).fontFamily); } catch (e) {}
      const t = ((el.innerText != null ? el.innerText : el.textContent) || '').trim();
      if (t.length < 30) return;
      const lines = t.split('\n').length;
      const codeish = looksLikeCode(t);
      const score = (isMono ? 2 : 0) + (codeish ? 3 : 0) + Math.min(lines / 8, 3) + Math.min(t.length / 400, 3);
      if (score > best.score) best = { v: t, score };
    });
    return best.score >= 3 ? best.v : '';
  }

  function extractCode() { return extractCodeLive(); }

  // ---------- 元信息 ----------
  function findLabelValue(doc, label) {
    const d = doc || document;
    const els = d.querySelectorAll('th, td, label, span, div, dt, dd, li');
    for (const el of els) {
      const t = (el.textContent || '').trim();
      if (!t) continue;
      if (t === label || (t.startsWith(label) && t.length <= label.length + 2)) {
        const n = el.nextElementSibling;
        if (n) { const v = (n.textContent || '').trim(); if (v && v !== label) return v; }
        const row = el.closest('tr');
        if (row) {
          const cells = Array.from(row.querySelectorAll('td, th'));
          const i = cells.indexOf(el);
          if (i >= 0 && cells[i + 1]) return (cells[i + 1].textContent || '').trim();
        }
        const par = el.parentElement;
        if (par) {
          const v = (par.textContent || '').replace(t, '').trim();
          if (v && v !== label) return v;
        }
      }
    }
    return '';
  }

  function extractContestId(url) {
    const u = url || location.href;
    let m = u.match(/\/acm\/contest\/(\d+)/);
    if (m) return m[1];
    m = u.match(/[?&]id=(\d+)/);
    return m ? m[1] : '';
  }

  function extractStudent(doc) {
    const d = doc || document;
    // 1) 牛客精确：a.long-user-name
    const a = d.querySelector('a.long-user-name');
    if (a) {
      const student = (a.textContent || '').trim();
      const uid = ((a.getAttribute('href') || '').match(/profile\/(\d+)/) || [])[1] || '';
      if (student) return { student, studentId: uid };
    }
    // 2) 通用 profile 链接
    const links = d.querySelectorAll('a[href*="profile"], a[href*="user"], a[href*="coder"]');
    for (const l of links) {
      const t = (l.textContent || '').trim();
      if (t && t.length <= 40 && !/^\d+$/.test(t) && !/profile|user|个人|主页/i.test(t)) {
        const id = ((l.getAttribute('href') || '').match(/(\d+)\s*$/) || [])[1] || '';
        return { student: t, studentId: id };
      }
    }
    const labels = ['提交人', '用户名', '昵称', '作者', '提交者', 'user', 'nickname', 'coder'];
    for (const lb of labels) {
      const v = findLabelValue(d, lb);
      if (v) return { student: v, studentId: '' };
    }
    const av = d.querySelector('.avatar img, .avatar, img[class*="avatar"], [class*="avatar"] img');
    if (av) {
      const t = (av.getAttribute('title') || av.getAttribute('alt') || '').trim();
      if (t) return { student: t, studentId: '' };
    }
    return { student: '', studentId: '' };
  }

  function extractLanguage(doc) {
    const d = doc || document;
    // 1) 牛客精确：legend 里的 span「语言：xxx」
    const spans = d.querySelectorAll('.coder-cont-legend span');
    for (const s of spans) {
      const t = (s.textContent || '').trim();
      if (t.startsWith('语言')) return t.replace(/^语言[:：]\s*/, '').trim();
    }
    // 2) 通用 class 匹配
    const els = d.querySelectorAll('[class*="lang"], [class*="language"], .lang, .language, [class*="type"]');
    for (const el of els) {
      const t = (el.textContent || '').trim();
      const m = t.match(LANGS);
      if (m) return m[1];
    }
    const v = findLabelValue(d, '语言') || findLabelValue(d, 'language');
    if (v) { const m = v.match(LANGS); if (m) return m[1]; }
    return '';
  }

  function extractVerdict(doc) {
    const d = doc || document;
    // 1) 牛客精确：legend 文本「运行状态：xxx」（非贪婪，遇到「得分」停止）
    const legend = d.querySelector('.coder-cont-legend');
    if (legend) {
      const m = (legend.textContent || '').match(/运行状态[:：]\s*([\s\S]*?)(?=得分[:：]|$)/);
      if (m && m[1].trim()) return m[1].trim();
    }
    // 2) 通用标签
    const v = findLabelValue(d, '结果') || findLabelValue(d, '状态') || findLabelValue(d, '运行结果') || findLabelValue(d, 'result');
    if (v) return v;
    const m2 = (d.body ? d.body.textContent : '').match(VERDICTS);
    return m2 ? m2[1] : '';
  }

  function extractScore(doc) {
    const d = doc || document;
    const m = ((d.querySelector('.coder-cont-legend') || {}).textContent || '').match(/得分[:：]\s*([\d.]+)/);
    return m ? m[1] : '';
  }

  function extractProblem(doc, title) {
    const d = doc || document;
    const t = title || (d.title || '');
    const cands = [];
    let m = t.match(/^(.*?)\s*[-—_]\s*(牛客|Nowcoder|NowCoder|nowcoder|牛客网)/i);
    if (m && m[1].trim()) cands.push(m[1].trim());
    d.querySelectorAll('a').forEach((a) => {
      const href = a.getAttribute('href') || '';
      if (/question|problem|acm\/contest\/\d+\/[A-Za-z0-9]/.test(href)) {
        const txt = (a.textContent || '').trim();
        if (txt && txt.length <= 100 && !/题目|提交|讨论/i.test(txt)) cands.push(txt);
      }
    });
    d.querySelectorAll('[class*="problem"], [class*="question"], [class*="title"]').forEach((el) => {
      const txt = (el.textContent || '').trim();
      if (txt && txt.length <= 100 && !/牛客|比赛|排名|提交|状态/i.test(txt)) cands.push(txt);
    });
    return cands.find((c) => c.length > 0 && c.length <= 80) || '';
  }

  function extractMeta(doc) {
    const d = doc || document;
    const url = (d.location && d.location.href) || location.href;
    const meta = {
      url, contestId: '', contestName: '', problem: '', problemIndex: '',
      student: '', studentId: '', language: '', verdict: '', score: '',
      submissionId: '', capturedAt: new Date().toISOString()
    };
    let m;
    if ((m = url.match(/\/contest\/(\d+)/i))) meta.contestId = m[1];
    if ((m = url.match(/[?&]id=(\d+)/i))) meta.contestId = meta.contestId || m[1];
    if ((m = url.match(/submissionId[=\/](\d+)/i))) meta.submissionId = m[1];
    if ((m = url.match(/problemId[=\/](\d+)/i))) meta.problem = m[1];
    if ((m = url.match(/\/contest\/\d+\/([A-Za-z]\d*)/))) meta.problemIndex = m[1];
    meta.contestName = (d.title || '').trim();
    const s = extractStudent(d); meta.student = s.student; meta.studentId = s.studentId;
    meta.language = extractLanguage(d);
    meta.verdict = extractVerdict(d);
    meta.score = extractScore(d);
    const p = extractProblem(d, d.title); if (p) meta.problem = meta.problem || p;
    return meta;
  }

  function detectPageType() {
    const url = location.href;
    if (/view-submission/.test(url)) return 'code';
    const code = extractCodeLive();
    if (code && code.trim().length > 10) return 'code';
    if (/status-list|status|submissions|record|submit/i.test(url)) return 'status';
    if (extractContestId(url)) return 'contest';
    const tables = document.querySelectorAll('table');
    if (tables.length && /结果|状态|运行|语言|通过|正确|错误/i.test((document.body && document.body.textContent) || '')) return 'status';
    return 'other';
  }

  // 表格式提交列表（旧版兜底；新版用 status-list JSON API）
  function extractSubmissionList() {
    const list = [];
    const seen = new Set();
    document.querySelectorAll('table tr').forEach((tr) => {
      const cells = Array.from(tr.querySelectorAll('td, th'));
      if (cells.length < 3) return;
      let codeUrl = '';
      tr.querySelectorAll('a').forEach((a) => {
        const href = a.getAttribute('href') || '';
        if (!codeUrl && /submission|code|status/i.test(href) && !/profile|user/i.test(href)) codeUrl = absolutize(href);
      });
      if (!codeUrl) return;
      const texts = cells.map((c) => (c.textContent || '').trim()).filter(Boolean);
      const joined = texts.join(' | ');
      const langM = joined.match(LANGS);
      const verM = joined.match(VERDICTS);
      const idM = codeUrl.match(/submission(?:Id)?[=\/](\d+)/i);
      const id = idM ? idM[1] : '';
      const key = id || codeUrl;
      if (seen.has(key)) return;
      seen.add(key);
      let student = '';
      tr.querySelectorAll('a').forEach((a) => {
        const href = a.getAttribute('href') || '';
        const t = (a.textContent || '').trim();
        if (!student && /profile|user/i.test(href) && t && t.length <= 40) student = t;
      });
      let problem = '';
      tr.querySelectorAll('a').forEach((a) => {
        const href = a.getAttribute('href') || '';
        const t = (a.textContent || '').trim();
        if (!problem && /question|problem/i.test(href) && t && t.length <= 100) problem = t;
      });
      list.push({
        submissionId: id,
        codeUrl,
        student,
        problem,
        language: langM ? langM[1] : '',
        verdict: verM ? verM[1] : '',
        raw: joined.slice(0, 300)
      });
    });
    return list;
  }

  // 导出当前页面结构诊断信息
  function diagnose() {
    const info = {
      url: location.href,
      title: document.title,
      host: location.host,
      pageType: detectPageType(),
      contestId: extractContestId(),
      extracted: { code: (extractCodeLive() || '').slice(0, 500), meta: extractMeta() },
      editors: {
        hasMonaco: !!(window.monaco && window.monaco.editor),
        hasAce: !!window.ace,
        hasCodeMirror: !!(window.CodeMirror || document.querySelector('.CodeMirror'))
      },
      candidates: [],
      tables: [],
      submissionList: extractSubmissionList().slice(0, 5)
    };
    const sels = ['pre', 'code', 'textarea', '.cm-editor', '.cm-content', '.CodeMirror',
      '.ace_editor', '.ace_text-layer', '[class*="code"]', '[class*="Code"]',
      '[class*="editor"]', '[class*="Editor"]', '[class*="source"]', '[class*="Source"]'];
    const seen = new Set();
    sels.forEach((sel) => {
      document.querySelectorAll(sel).forEach((el) => {
        const cls = el.className && el.className.toString ? el.className.toString() : '';
        const key = el.tagName + ':' + cls + ':' + (el.id || '');
        if (seen.has(key)) return;
        seen.add(key);
        const text = (el.tagName === 'TEXTAREA' && el.value != null) ? el.value : ((el.innerText != null ? el.innerText : el.textContent) || '');
        if (text && text.trim().length >= 20) {
          info.candidates.push({ selector: sel, tag: el.tagName, id: el.id || '', cls, textLen: text.length, preview: text.trim().slice(0, 300), html: el.outerHTML.slice(0, 1500) });
        }
      });
    });
    document.querySelectorAll('table').forEach((t, ti) => {
      if (ti > 3) return;
      const allRows = t.querySelectorAll('tr');
      info.tables.push({
        index: ti,
        rowCount: allRows.length,
        rows: Array.from(allRows).slice(0, 6).map((tr) => ({
          cells: Array.from(tr.querySelectorAll('td,th')).map((c) => (c.textContent || '').trim().slice(0, 80)),
          links: Array.from(tr.querySelectorAll('a')).map((a) => ({ href: a.getAttribute('href') || '', text: (a.textContent || '').trim().slice(0, 40) })).slice(0, 8)
        }))
      });
    });
    return info;
  }

  window.__NCC_EXTRACTOR__ = {
    extractCode,
    extractCodeFromDoc,
    extractMeta,
    detectPageType,
    extractSubmissionList,
    extractContestId,
    diagnose,
    normalizeLang,
    looksLikeCode
  };
})();
