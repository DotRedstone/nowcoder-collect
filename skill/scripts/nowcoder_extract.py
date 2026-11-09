#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""牛客网 ACM 比赛代码提取器 —— DSH skill `nowcoder-collect` 的配套脚本。

从 ac.nowcoder.com 拉取比赛全部提交与代码，按学生/题号落盘，并写入 SQLite 数据库。

真实接口（2026-11 实测）：
  最近参加的比赛: GET /acm-heavy/acm/contest/profile/contest-joined-history
                   ?uid={uid}&page=1&pageSize=30&onlyJoinedFilter=true&contestEndFilter=true
  比赛信息(JSON):  GET /acm/contest/contest-info?id={contestId}
                   -> data.name / data.startTime / data.endTime
  榜单完整数据:    GET /acm/contest/rank/submit-list?currentContestId={id}&contestList={id}&rankScope=ALL
                   -> data.problemData[]（index 题号/score 满分/title 标题/problemId）
                   -> data.submitDataList[0].signUpUsers[]（uid/name/school/violation）
                   -> data.submitDataList[0].submissions[]（uid/problemId/submissionId/
                      submitTime/status/rightCaseRate/timeConsumption）
                   ※ 这是「比赛期间」的完整提交（#rank 前端就靠它客户端算榜）
  提交记录(JSON):  GET /acm/contest/status-list?token=&id={contestId}&page={n}&pageSize=50
                   ※ 只返回部分（含补题），仅用于补题采集，不作为主源
  代码页(HTML):    GET /acm/contest/view-submission?submissionId={id}
                   -> 代码在 <pre class="prettyprint lang-xxx">；语言/运行状态/得分在 .coder-cont-legend

用法:
  python3 nowcoder_extract.py --recent [--uid <数字id>]
  python3 nowcoder_extract.py --rank 143786                 # 重建榜单（IOI 总分）
  python3 nowcoder_extract.py --contest 143786 [--phase contest|after|all]
  python3 nowcoder_extract.py --query "select ..."

Cookie 来源优先级: --cookie > NOWCODER_COOKIE > ~/.dsh/nowcoder/cookie.txt
"""
import argparse
import datetime
import html as htmlmod
import json
import os
import re
import sqlite3
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "https://ac.nowcoder.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


# ---------------------------------------------------------------- cookie / uid
def load_cookie(cli_cookie):
    if cli_cookie:
        return cli_cookie.strip()
    if os.environ.get("NOWCODER_COOKIE"):
        return os.environ["NOWCODER_COOKIE"].strip()
    p = os.path.expanduser("~/.dsh/nowcoder/cookie.txt")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    sys.exit("未找到 Cookie：用 --cookie 传入，或设 NOWCODER_COOKIE，或写入 ~/.dsh/nowcoder/cookie.txt")


def _read_uid_file():
    p = os.path.expanduser("~/.dsh/nowcoder/uid.txt")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    return ""


def get(url, cookie):
    req = urllib.request.Request(url, headers={"Cookie": cookie, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def get_json(url, cookie):
    raw = get(url, cookie).lstrip("\ufeff")
    return json.loads(raw)


def time_str(ms):
    try:
        return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ms)


# ---------------------------------------------------------------- 最近比赛
def list_recent(cookie, uid):
    url = (BASE + "/acm-heavy/acm/contest/profile/contest-joined-history"
           f"?uid={uid}&page=1&pageSize=30&onlyJoinedFilter=true"
           "&searchContestName=&onlyRatingFilter=false&contestEndFilter=true")
    d = get_json(url, cookie)
    data = d.get("data") or {}
    arr = data.get("dataList") or data.get("data") or []
    return [{"contestId": it.get("contestId"), "contestName": it.get("contestName"),
             "startTime": it.get("startTime"), "endTime": it.get("endTime")} for it in arr]


# ---------------------------------------------------------------- 比赛信息
def fetch_contest_info(cookie, contest_id):
    url = f"{BASE}/acm/contest/contest-info?id={contest_id}"
    d = get_json(url, cookie)
    data = d.get("data") or {}
    return {
        "contestId": data.get("sourceId") or contest_id,
        "name": data.get("name") or "",
        "startTime": data.get("startTime"),
        "endTime": data.get("endTime"),
        "description": data.get("description") or "",
    }


# ---------------------------------------------------------------- 榜单完整数据
def fetch_submit_list(cookie, contest_id):
    """榜单真实数据源：报名用户 + 全部比赛期间提交。"""
    url = f"{BASE}/acm/contest/rank/submit-list?currentContestId={contest_id}&contestList={contest_id}&rankScope=ALL"
    d = get_json(url, cookie)
    data = d.get("data") or {}
    problems = data.get("problemData") or []       # {score(满分), index, problemId, title}
    sdl = (data.get("submitDataList") or [{}])[0]
    signup = {u["uid"]: u for u in (sdl.get("signUpUsers") or [])}
    submissions = sdl.get("submissions") or []
    basic = sdl.get("basicInfo") or {}
    return problems, signup, submissions, basic


# ---------------------------------------------------------------- 提交记录（补题用）
def fetch_status_list(cookie, contest_id):
    subs = []
    first = get_json(f"{BASE}/acm/contest/status-list?token=&id={contest_id}&page=1&pageSize=50", cookie)
    data = first.get("data") or {}
    bi = data.get("basicInfo") or {}
    page_count = bi.get("pageCount") or 1
    subs.extend(data.get("data") or [])
    for p in range(2, page_count + 1):
        r = get_json(f"{BASE}/acm/contest/status-list?token=&id={contest_id}&page={p}&pageSize=50", cookie)
        subs.extend((r.get("data") or {}).get("data") or [])
    return subs


# ---------------------------------------------------------------- 代码页解析
def extract_code_page(html):
    """从 view-submission HTML 提取 code/language/verdict/score。"""
    code = ""
    m = re.search(r'<pre[^>]*class="prettyprint[^"]*"[^>]*>([\s\S]*?)</pre>', html)
    if m:
        code = htmlmod.unescape(m.group(1)).strip()
    m = re.search(r'<div class="coder-cont-legend">([\s\S]*?)</div>', html)
    legend = m.group(1) if m else ""

    def pick(pattern):
        mm = re.search(pattern, legend)
        return mm.group(1).strip() if mm else ""

    lang = pick(r'语言[:：]\s*([^<>\n]+)')
    verdict = pick(r'运行状态[:：]\s*<[^>]*>\s*([^<>\n]+)')
    score = pick(r'得分[:：]\s*<[^>]*>\s*([\d.]+)')
    return code, lang, verdict, score


def fetch_code_full(cookie, submission_id):
    try:
        page = get(f"{BASE}/acm/contest/view-submission?submissionId={submission_id}", cookie)
        return extract_code_page(page)
    except Exception:
        return "", "", "", ""


def ext_for_lang(lang):
    l = (lang or "").lower()
    if re.search(r"c\+\+|cpp|g\+\+|clang", l):
        return "cpp"
    if re.search(r"c#|csharp", l):
        return "cs"
    if re.search(r"\bc\b|^c$|c11|c99|c17|c23|gcc", l):
        return "c"
    if "java" in l:
        return "java"
    if "python" in l or "pypy" in l:
        return "py"
    if "go" in l:
        return "go"
    if "javascript" in l or "node" in l:
        return "js"
    if "typescript" in l:
        return "ts"
    if "rust" in l:
        return "rs"
    if "php" in l:
        return "php"
    if "kotlin" in l:
        return "kt"
    if "swift" in l:
        return "swift"
    if "ruby" in l:
        return "rb"
    if "pascal" in l:
        return "pas"
    if "haskell" in l:
        return "hs"
    if "scala" in l:
        return "scala"
    if "perl" in l:
        return "pl"
    return "txt"


def sanitize(s):
    return (re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(s or "")).replace(" ", "_")[:60] or "unknown")


def code_metrics(code):
    lines = (code or "").split("\n")
    loc = comments = blanks = 0
    in_block = False
    for ln in lines:
        t = ln.strip()
        if not t:
            blanks += 1
            continue
        if in_block:
            comments += 1
            if "*/" in t:
                in_block = False
            continue
        if t.startswith("/*"):
            comments += 1
            if "*/" not in t:
                in_block = True
            continue
        if t.startswith("//") or t.startswith("#"):
            comments += 1
            continue
        loc += 1
    ratio = comments / len(lines) if lines else 0
    return {"loc": loc, "comments": comments, "blanks": blanks, "ratio": round(ratio, 3)}


# ---------------------------------------------------------------- 相似度
def _norm(code):
    return re.sub(r"\s+", "",
                  re.sub(r"/\*[\s\S]*?\*/", "",
                         re.sub(r"//[^\n]*", "",
                                re.sub(r"^[ \t]*#[^\n]*", "", code or "", flags=re.M))))


def _hash(s):
    h = 5381
    for ch in s:
        h = ((h << 5) + h + ord(ch)) & 0xFFFFFFFF
    return h


def _winnow(code, k=5, w=4):
    s = _norm(code)
    if len(s) < k + w:
        return []
    hs = [_hash(s[i:i + k]) for i in range(len(s) - k + 1)]
    fp = set()
    for i in range(len(hs) - w + 1):
        fp.add(min(hs[i:i + w]))
    return list(fp)


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    return inter / (len(sa) + len(sb) - inter)


def build_similarity(items):
    fps = [(it, _winnow(it["code"])) for it in items if it["code"]]
    best = {}  # (min_uid, max_uid, problem) -> (a, b, max_sim)，同一对人同题只报一次
    for i in range(len(fps)):
        for j in range(i + 1, len(fps)):
            a, fa = fps[i]
            b, fb = fps[j]
            if a["studentId"] == b["studentId"]:
                continue
            if a["problemIndex"] != b["problemIndex"]:
                continue
            sc = _jaccard(fa, fb)
            if sc < 0.5:
                continue
            key = (min(a["studentId"], b["studentId"]), max(a["studentId"], b["studentId"]), a["problemIndex"])
            if key not in best or sc > best[key][2]:
                best[key] = (a, b, sc)
    pairs = sorted(best.values(), key=lambda x: -x[2])
    return pairs


def build_ranking(items, pairs):
    students = {}
    for it in items:
        sid = it["studentId"]
        s = students.setdefault(sid, {
            "name": it["student"], "submissions": 0, "ac": 0,
            "comment_sum": 0.0, "code_count": 0, "max_sim": 0.0,
            "sim_pairs": 0, "partners": set(),
        })
        s["submissions"] += 1
        if it["verdict"] in ("答案正确", "Accepted", "通过"):
            s["ac"] += 1
        if it["code"]:
            s["comment_sum"] += (it["metrics"] or {}).get("ratio", 0.0)
            s["code_count"] += 1
    for a, b, sc in pairs:
        for sid, other in ((a["studentId"], b["student"]), (b["studentId"], a["student"])):
            s = students[sid]
            s["sim_pairs"] += 1
            s["max_sim"] = max(s["max_sim"], sc)
            s["partners"].add(other)
    return sorted(students.values(),
                  key=lambda s: (-s["max_sim"], -s["sim_pairs"], -s["submissions"]))


def fence(code):
    f = "```"
    while f in (code or ""):
        f += "`"
    return f


# ---------------------------------------------------------------- SQLite
def init_db(db_path):
    con = sqlite3.connect(db_path)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS contests (
        contest_id  INTEGER PRIMARY KEY,
        name        TEXT,
        start_time  INTEGER,
        end_time    INTEGER,
        description TEXT
    );
    CREATE TABLE IF NOT EXISTS submissions (
        submission_id INTEGER PRIMARY KEY,
        contest_id    INTEGER NOT NULL,
        student       TEXT,
        student_id    INTEGER,
        problem_index TEXT,
        problem_id    INTEGER,
        problem_title TEXT,
        language      TEXT,
        verdict       TEXT,
        score         REAL,
        submit_time   INTEGER,
        phase         TEXT,
        code          TEXT,
        url           TEXT,
        loc           INTEGER,
        comments      INTEGER,
        comment_ratio REAL,
        extracted_at  TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_sub_contest ON submissions(contest_id);
    CREATE INDEX IF NOT EXISTS idx_sub_student ON submissions(contest_id, student);
    """)
    con.commit()
    return con


def upsert_contest(con, info):
    con.execute("""INSERT OR REPLACE INTO contests
        (contest_id, name, start_time, end_time, description) VALUES (?,?,?,?,?)""",
                (info["contestId"], info.get("name", ""), info.get("startTime"),
                 info.get("endTime"), info.get("description", "")))


def upsert_submission(con, it, contest_id):
    con.execute("""INSERT OR REPLACE INTO submissions
        (submission_id, contest_id, student, student_id, problem_index, problem_id,
         problem_title, language, verdict, score, submit_time, phase, code, url,
         loc, comments, comment_ratio, extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (it["submissionId"], contest_id, it["student"], it["studentId"],
                 it["problemIndex"], it["problemId"], it["problem"],
                 it["language"], it["verdict"], it["score"], it["submitTime"],
                 it["phase"], it["code"], it["url"],
                 it["metrics"]["loc"], it["metrics"]["comments"], it["metrics"]["ratio"],
                 it["extractedAt"]))


# ---------------------------------------------------------------- 榜单重建
def compute_rank(cookie, contest_id, phase):
    problems, signup, submissions, basic = fetch_submit_list(cookie, contest_id)
    full = {p["problemId"]: p["score"] for p in problems}
    order = [p["index"] for p in problems]
    best = {uid: {} for uid in signup}       # 全部报名用户（含 0 提交）
    cnt = {uid: 0 for uid in signup}
    for s in submissions:
        uid = s["uid"]
        pid = s["problemId"]
        if s["status"] == 5:                 # 答案正确 -> 满分
            sc = full.get(pid, 0)
        else:                                # 部分分 -> rightCaseRate * 满分
            sc = round(s.get("rightCaseRate", 0) * full.get(pid, 0), 2)
        best.setdefault(uid, {})
        best[uid][pid] = max(best[uid].get(pid, 0), sc)
        cnt[uid] = cnt.get(uid, 0) + 1
    ranked = []
    for uid, b in best.items():
        total = round(sum(b.values()), 2)
        ac = sum(1 for pid, sc in b.items() if sc >= full.get(pid, 0) - 1e-6)
        u = signup.get(uid, {})
        by_index = {next((p["index"] for p in problems if p["problemId"] == pid), str(pid)): sc
                    for pid, sc in b.items()}
        ranked.append({"name": u.get("name") or str(uid), "total": total, "ac": ac,
                       "submissions": cnt.get(uid, 0), "best": by_index,
                       "violation": u.get("violation", False)})
    ranked.sort(key=lambda s: (-s["total"], -s["ac"], s["name"]))
    prob_for_table = [{"name": p["index"], "score": p["score"], "title": p.get("title", "")} for p in problems]
    return prob_for_table, ranked


def rank_table_md(problems, ranked, phase_label):
    phase_txt = {"contest": "比赛期间", "after": "补题", "all": "全部"}.get(phase_label, phase_label)
    names = [p["name"] for p in problems]
    L = ["# 比赛榜单（官方同源重建）", ""]
    L.append(f"- 排名范围：{phase_txt}")
    L.append(f"- 人数：{len(ranked)}")
    L.append("- 规则：IOI 计分，每题取最高分（AC 得满分，否则 rightCaseRate×满分），按总分降序")
    L.append("")
    # 题号 + 满分 + 标题提示
    L.append("题号满分：" + "　".join(f"{p['name']}={p['score']}" for p in problems))
    L.append("")
    L.append("| 排名 | 姓名 | 总分 | AC | " + " | ".join(names) + " | 提交 |")
    L.append("| --- | --- | --- | --- | " + " | ".join("---" for _ in names) + " | --- |")
    for i, stu in enumerate(ranked, 1):
        cells = [str(i), stu["name"] + ("⚠️" if stu.get("violation") else ""), f"{stu['total']:.2f}", str(stu["ac"])]
        for nm in names:
            v = stu["best"].get(nm)
            cells.append(f"{v:.2f}" if v is not None else "—")
        cells.append(str(stu["submissions"]))
        L.append("| " + " | ".join(cells) + " |")
    return "\n".join(L)


# ---------------------------------------------------------------- 主流程
def extract(cookie, contest_id, out_dir, phase, db_path, threads):
    print(f"[1/5] 拉取比赛信息与榜单数据 contest={contest_id} …", file=sys.stderr)
    info = fetch_contest_info(cookie, contest_id)
    name = info.get("name") or str(contest_id)
    start, end = info.get("startTime"), info.get("endTime")
    problems, signup, rank_subs, rank_basic = fetch_submit_list(cookie, contest_id)
    prob_by_id = {p["problemId"]: p for p in problems}
    print(f"      比赛: {name}  窗口 {time_str(start)} ~ {time_str(end)}", file=sys.stderr)
    print(f"      榜单提交(比赛期间): {len(rank_subs)} 条，报名 {len(signup)} 人", file=sys.stderr)

    # 补题（status-list 过滤 > endTime）
    after_subs = []
    if phase in ("after", "all"):
        status_list = fetch_status_list(cookie, contest_id)
        after_subs = [s for s in status_list if end and s.get("submitTime") and s["submitTime"] > end]
        print(f"      补题提交: {len(after_subs)} 条", file=sys.stderr)

    # 统一提交清单
    raw = []  # {submissionId, student, studentId, problemIndex, problem, submitTime, phase}
    seen = set()
    def add(submissionId, student, studentId, pidx, title, submitTime, ph):
        if not submissionId or submissionId in seen:
            return
        seen.add(submissionId)
        raw.append({"submissionId": submissionId, "student": student, "studentId": studentId,
                    "problemIndex": pidx, "problem": title, "submitTime": submitTime, "phase": ph})

    if phase in ("contest", "all"):
        for s in rank_subs:
            p = prob_by_id.get(s["problemId"], {})
            u = signup.get(s["uid"], {})
            add(s["submissionId"], u.get("name", ""), s["uid"],
                p.get("index", ""), p.get("title", ""), s["submitTime"], "contest")
    for s in after_subs:
        add(s.get("submissionId"), s.get("userName", ""), s.get("userId"),
            s.get("index", ""), "", s.get("submitTime"), "after")

    n_contest = len([r for r in raw if r["phase"] == "contest"])
    n_after = len([r for r in raw if r["phase"] == "after"])
    print(f"[2/5] 本阶段采集 {len(raw)} 条（比赛期间 {n_contest}，补题 {n_after}）", file=sys.stderr)

    print(f"[3/5] 抓取代码（并发 {threads} 线程）…", file=sys.stderr)

    def build_item(r):
        code, lang, verdict, score = fetch_code_full(cookie, r["submissionId"])
        it = {
            "submissionId": r["submissionId"],
            "student": r["student"],
            "studentId": r["studentId"],
            "problemIndex": r["problemIndex"],
            "problem": r["problem"],
            "problemId": next((p["problemId"] for p in problems if p["index"] == r["problemIndex"]), None),
            "language": lang,
            "verdict": verdict,
            "score": score,
            "submitTime": r["submitTime"],
            "phase": r["phase"],
            "code": code,
            "url": f"{BASE}/acm/contest/view-submission?submissionId={r['submissionId']}",
            "extractedAt": time_str(int(datetime.datetime.now().timestamp() * 1000)),
        }
        it["metrics"] = code_metrics(code)
        return it

    items = [None] * len(raw)
    with ThreadPoolExecutor(max_workers=threads) as pool:
        fut_map = {pool.submit(build_item, r): i for i, r in enumerate(raw)}
        done = 0
        for fut in as_completed(fut_map):
            items[fut_map[fut]] = fut.result()
            done += 1
            if done % 20 == 0 or done == len(raw):
                print(f"      {done}/{len(raw)}", file=sys.stderr)

    ok = sum(1 for it in items if it["code"])
    print(f"[4/5] 写数据库 + 导出 …", file=sys.stderr)

    db_path = db_path or os.path.expanduser("~/.dsh/nowcoder/nowcoder.db")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    con = init_db(db_path)
    upsert_contest(con, info)
    for it in items:
        upsert_submission(con, it, contest_id)
    con.commit()
    con.close()

    base = os.path.join(out_dir, str(contest_id))
    code_dir = os.path.join(base, "code")
    os.makedirs(code_dir, exist_ok=True)

    manifest = {
        "contestId": contest_id, "contestName": name,
        "startTime": start, "endTime": end, "phase": phase,
        "signUpUsers": len(signup),
        "inContest": n_contest, "afterContest": n_after,
        "selected": len(items), "extracted": ok,
        "extractedAt": time_str(int(datetime.datetime.now().timestamp() * 1000)),
        "submissions": [{k: v for k, v in it.items() if k != "code"} for it in items],
    }
    with open(os.path.join(base, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    used = {}
    for it in items:
        if not it["code"]:
            continue
        ext = ext_for_lang(it["language"])
        name_f = f"{sanitize(it['student'])}_{sanitize(it['problemIndex'])}_{sanitize(it['language'])}.{ext}"
        n = used.get(name_f, 0)
        used[name_f] = n + 1
        if n:
            name_f = f"{sanitize(it['student'])}_{sanitize(it['problemIndex'])}_{sanitize(it['language'])}_{n + 1}.{ext}"
        with open(os.path.join(code_dir, name_f), "w", encoding="utf-8") as f:
            f.write(it["code"])

    md = ["# 牛客代码收集", ""]
    md.append(f"- 比赛：{name}（{contest_id}）")
    md.append(f"- 窗口：{time_str(start)} ~ {time_str(end)}" if start and end else "")
    md.append(f"- 采集范围：{'全部' if phase == 'all' else ('比赛期间' if phase == 'contest' else '赛后补题')}")
    md.append(f"- 提交：{len(items)} 条（比赛期间 {n_contest}，补题 {n_after}）")
    md.append("")
    for it in sorted(items, key=lambda x: (x["student"], x["problemIndex"])):
        title = f" {it['problem']}" if it.get("problem") else ""
        tag = " · ".join(filter(None, [it["language"], it["verdict"]]))
        md.append(f"## {it['student'] or '未知'} — {it['problemIndex'] or it['submissionId']}{title}"
                  + (f"（{tag}）" if tag else ""))
        if it["url"]:
            md.append(f"- 来源：{it['url']}")
        m = it["metrics"]
        md.append(f"- {m['loc']} 行 · 注释 {m['comments']} · 注释占比 {int(m['ratio'] * 100)}%")
        md.append("")
        f = fence(it["code"])
        md.append(f + ext_for_lang(it["language"]))
        md.append(it["code"] or "")
        md.append(f)
        md.append("")
    with open(os.path.join(base, "合集.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    pairs = build_similarity(items)
    sim = ["# 相似度报告", ""]
    sim.append(f"- 参与比较：{sum(1 for it in items if it['code'])} 份代码")
    sim.append(f"- 采集范围：{'全部' if phase == 'all' else ('比赛期间' if phase == 'contest' else '赛后补题')}")
    sim.append(f"- 阈值：50%")
    sim.append(f"- 高相似对：{len(pairs)}")
    sim.append("")
    sim.append("> 仅统计「不同人、同一题」的跨人雷同对（已排除同一人的重复提交）。基于 Winnowing 指纹 + Jaccard，仅作抄袭线索，不作最终判定。")
    sim.append("")
    if not pairs:
        sim.append("未发现明显相似对。")
    else:
        sim.append("| 提交 A | 提交 B | 相似度 |")
        sim.append("| --- | --- | --- |")
        for a, b, sc in pairs:
            sim.append(f"| {a['student']}/{a['problemIndex']} | {b['student']}/{b['problemIndex']} "
                       f"| {sc * 100:.1f}% |")
    with open(os.path.join(base, "相似度.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(sim))

    ranked = build_ranking(items, pairs)
    rank = ["# 嫌疑排名", ""]
    rank.append("- 按「最高跨人同题相似度 → 雷同对数 → 提交数」降序；平均注释占比仅作辅助参考。")
    rank.append("- 嫌疑：高 = 存在 ≥70% 跨人同题相似对；中 = 存在 50%–70%；— = 未发现跨人雷同。")
    rank.append("")
    rank.append("| 排名 | 姓名 | 提交 | AC | 最高跨人相似 | 雷同对数 | 平均注释% | 嫌疑 |")
    rank.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, s in enumerate(ranked, 1):
        level = "高" if s["max_sim"] >= 0.7 else ("中" if s["max_sim"] >= 0.5 else "—")
        avg_c = round(s["comment_sum"] / s["code_count"] * 100) if s["code_count"] else 0
        partners = "、".join(sorted(s["partners"])) or ""
        rank.append(f"| {i} | {s['name']} | {s['submissions']} | {s['ac']} "
                    f"| {s['max_sim'] * 100:.1f}% | {s['sim_pairs']} | {avg_c}% | {level} |")
        if partners:
            rank.append(f"| | ↳ 关联: {partners} | | | | | | |")
    with open(os.path.join(base, "排名.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(rank))

    # 保存官方同源的榜单计算输入（含零提交报名者），只用于本机离线审核。
    # 该缓存不含 Cookie、源码或敏感请求头；文件权限由 review_domain 控制。
    try:
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[2]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from review_domain import save_rank_snapshot
        save_rank_snapshot(int(contest_id),
                           Path(db_path or "~/.dsh/nowcoder/nowcoder.db").expanduser(),
                           problems, signup, rank_subs)
    except (OSError, ValueError, ImportError) as exc:
        print(f"      榜单离线缓存失败（采集结果不受影响）：{exc}", file=sys.stderr)

    print(f"[5/5] 完成 → {base}  |  DB: {db_path}", file=sys.stderr)
    print(json.dumps({"ok": True, "contestId": contest_id, "name": name,
                      "signUpUsers": len(signup), "inContest": n_contest, "afterContest": n_after,
                      "selected": len(items), "extracted": ok, "dir": base,
                      "db": db_path, "similarityPairs": len(pairs)}, ensure_ascii=False))
    return 0


def query_db(db_path, sql):
    db_path = db_path or os.path.expanduser("~/.dsh/nowcoder/nowcoder.db")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(sql).fetchall()
    con.close()
    print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description="牛客网 ACM 代码提取器")
    ap.add_argument("--recent", action="store_true", help="列出最近参加的比赛")
    ap.add_argument("--contest", help="比赛 ID")
    ap.add_argument("--rank", help="重建某场比赛的榜单（IOI 总分排名）")
    ap.add_argument("--uid", default="", help="牛客用户数字 ID（--recent 用，缺省读 NOWCODER_UID 或 ~/.dsh/nowcoder/uid.txt）")
    ap.add_argument("--phase", default="contest", choices=["contest", "after", "all"],
                    help="采集范围：contest=仅比赛期间(默认) after=仅补题 all=全部")
    ap.add_argument("--out", default="./nowcoder-data", help="输出根目录")
    ap.add_argument("--db", default="", help="SQLite 路径（缺省 ~/.dsh/nowcoder/nowcoder.db）")
    ap.add_argument("--threads", type=int, default=24, help="并发抓取线程数（默认 24）")
    ap.add_argument("--query", help="对 SQLite 执行只读 SQL 后退出")
    ap.add_argument("--cookie", help="登录 Cookie 字符串")
    args = ap.parse_args()

    cookie = load_cookie(args.cookie)

    if args.query:
        query_db(args.db, args.query)
        return 0

    if args.rank:
        problems, ranked = compute_rank(cookie, args.rank, args.phase)
        print(rank_table_md(problems, ranked, args.phase))
        return 0

    if args.recent:
        uid = args.uid or os.environ.get("NOWCODER_UID") or _read_uid_file()
        if not uid:
            sys.exit("--recent 需要 uid：用 --uid 传入，或设 NOWCODER_UID，或写入 ~/.dsh/nowcoder/uid.txt")
        contests = list_recent(cookie, uid)
        print(json.dumps({"ok": True, "contests": contests}, ensure_ascii=False, indent=2))
        return 0

    if not args.contest:
        ap.print_help()
        return 2

    return extract(cookie, args.contest, args.out, args.phase, args.db, args.threads)


if __name__ == "__main__":
    sys.exit(main())
