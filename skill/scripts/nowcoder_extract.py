#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""牛客网 ACM 比赛代码提取器 —— DSH skill `nowcoder-collect` 的配套脚本。

从 ac.nowcoder.com 拉取某场比赛的提交与代码，按学生/题号落盘，并写入 SQLite 数据库。

真实接口（2026-11 实测）：
  最近参加的比赛: GET /acm-heavy/acm/contest/profile/contest-joined-history
                   ?uid={uid}&page=1&pageSize=30&onlyJoinedFilter=true
                   &searchContestName=&onlyRatingFilter=false&contestEndFilter=true
  比赛信息(JSON):  GET /acm/contest/contest-info?id={contestId}
                   -> data.name / data.startTime / data.endTime
  提交列表(JSON):  GET /acm/contest/status-list?token=&id={contestId}&page={n}&pageSize=50
                   -> data.data[]（userName/userId/index 题号/languageName/statusMessage/
                      submissionId/problemId/score/submitTime），basicInfo.pageCount/statusCount 翻页
  代码页(HTML):    GET /acm/contest/view-submission?submissionId={id}
                   -> 代码在 <pre class="prettyprint lang-xxx">…</pre>（HTML 转义）

注意：status-list 会返回「比赛期间 + 赛后补题」的全部提交，本脚本默认按
比赛窗口（startTime <= submitTime <= endTime）过滤，只保留比赛期间提交。
用 --phase 切换：contest(默认,仅比赛期间) / after(仅补题) / all(全部)。

用法:
  python3 nowcoder_extract.py --recent [--uid <数字id>]
  python3 nowcoder_extract.py --contest 143786 [--name "..." ] [--phase contest] [--out ./data]
  python3 nowcoder_extract.py --contest 143786 --phase all      # 含补题
  python3 nowcoder_extract.py --query "select count(*) from submissions where contest_id=143786"  # 查库

Cookie 来源优先级: --cookie > NOWCODER_COOKIE > ~/.dsh/nowcoder/cookie.txt
"""
import argparse
import datetime
import hashlib
import html as htmlmod
import json
import os
import re
import sqlite3
import sys
import urllib.request

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


# ---------------------------------------------------------------- 提取
def fetch_submissions(cookie, contest_id):
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


def fetch_problem_data(cookie, contest_id):
    """榜单接口的 problemData（题号 + 满分）与 basicInfo（比赛窗口等）。"""
    d = get_json(f"{BASE}/acm/contest/real-time-rank-data?token=&id={contest_id}&page=1", cookie)
    data = d.get("data") or {}
    return data.get("problemData") or [], data.get("basicInfo") or {}


def compute_rank(cookie, contest_id, phase):
    """用提交数据重建榜单：每人在每题取最高分，按总分降序。"""
    problems, basic = fetch_problem_data(cookie, contest_id)
    start, end = basic.get("contestBeginTime"), basic.get("contestEndTime")
    subs = fetch_submissions(cookie, contest_id)
    sel = [s for s in subs if phase == "all" or phase_of(s.get("submitTime"), start, end) == phase]
    students = {}
    for s in sel:
        sid = s.get("userId")
        stu = students.setdefault(sid, {"name": s.get("userName") or "", "best": {}, "submissions": 0, "ac": 0})
        stu["submissions"] += 1
        idx = s.get("index") or ""
        sc = s.get("score") or 0
        if idx:
            stu["best"][idx] = max(stu["best"].get(idx, 0), sc)
        if s.get("statusMessage") in ("答案正确", "Accepted", "通过"):
            stu["ac"] += 1
    for stu in students.values():
        stu["total"] = round(sum(stu["best"].values()), 2)
    ranked = sorted(students.values(), key=lambda s: (-s["total"], -s["ac"], s["name"]))
    return problems, ranked


def rank_table_md(problems, ranked, phase_label):
    phase_txt = {"contest": "比赛期间", "after": "补题", "all": "全部"}.get(phase_label, phase_label)
    names = [p["name"] for p in problems]
    L = ["# 比赛榜单（重建）", ""]
    L.append(f"- 排名范围：{phase_txt}")
    L.append(f"- 参与人数：{len(ranked)}")
    L.append("- 规则：IOI 计分，每人在每题取最高分求和，按总分降序")
    L.append("")
    L.append("| 排名 | 姓名 | 总分 | AC | " + " | ".join(names) + " | 提交 |")
    L.append("| --- | --- | --- | --- | " + " | ".join("---" for _ in names) + " | --- |")
    for i, stu in enumerate(ranked, 1):
        cells = [str(i), stu["name"], f"{stu['total']:.1f}", str(stu["ac"])]
        for nm in names:
            v = stu["best"].get(nm)
            cells.append(f"{v:.1f}" if v is not None else "—")
        cells.append(str(stu["submissions"]))
        L.append("| " + " | ".join(cells) + " |")
    return "\n".join(L)


def fetch_code(cookie, submission_id):
    try:
        page = get(f"{BASE}/acm/contest/view-submission?submissionId={submission_id}", cookie)
        m = re.search(r'<pre[^>]*class="prettyprint[^"]*"[^>]*>([\s\S]*?)</pre>', page)
        if m:
            return htmlmod.unescape(m.group(1)).strip()
    except Exception:
        pass
    return ""


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


def phase_of(submit_time, start, end):
    if submit_time is None:
        return "unknown"
    if start and submit_time < start:
        return "before"
    if end and submit_time > end:
        return "after"
    return "contest"


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
    pairs = []
    for i in range(len(fps)):
        for j in range(i + 1, len(fps)):
            a, fa = fps[i]
            b, fb = fps[j]
            # 只比较「不同人 + 同一题」：跨人同题才是抄袭线索，重交/不同题无意义
            if a["studentId"] == b["studentId"]:
                continue
            if a["problemIndex"] != b["problemIndex"]:
                continue
            sc = _jaccard(fa, fb)
            if sc >= 0.5:
                pairs.append((a, b, sc))
    pairs.sort(key=lambda x: -x[2])
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
        language      TEXT,
        verdict       TEXT,
        score         REAL,
        submit_time   INTEGER,
        run_time      INTEGER,
        memory        INTEGER,
        length        INTEGER,
        loc           INTEGER,
        comments      INTEGER,
        comment_ratio REAL,
        phase         TEXT,
        code          TEXT,
        url           TEXT,
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


def upsert_submission(con, it, contest_id, phase):
    con.execute("""INSERT OR REPLACE INTO submissions
        (submission_id, contest_id, student, student_id, problem_index, problem_id,
         language, verdict, score, submit_time, run_time, memory, length,
         loc, comments, comment_ratio, phase, code, url, extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (it["submissionId"], contest_id, it["student"], it["studentId"],
                 it["problemIndex"], it["problemId"], it["language"], it["verdict"],
                 it["score"], it["submitTime"], it["time"], it["memory"], it["length"],
                 it["metrics"]["loc"], it["metrics"]["comments"], it["metrics"]["ratio"],
                 phase, it["code"], it["url"], it["extractedAt"]))


# ---------------------------------------------------------------- 主流程
def extract(cookie, contest_id, out_dir, phase, db_path):
    print(f"[1/5] 拉取比赛信息 contest={contest_id} …", file=sys.stderr)
    info = fetch_contest_info(cookie, contest_id)
    name = info.get("name") or str(contest_id)
    start, end = info.get("startTime"), info.get("endTime")
    if start and end:
        print(f"      比赛: {name}  窗口 {time_str(start)} ~ {time_str(end)}", file=sys.stderr)

    print(f"[2/5] 拉取提交列表 …", file=sys.stderr)
    subs = fetch_submissions(cookie, contest_id)
    if not subs:
        print("该比赛没有提交记录（或比赛 ID 不对 / 未参赛）", file=sys.stderr)
        return 1

    # 按比赛窗口分阶段 + 过滤
    phased = []
    for s in subs:
        st = s.get("submitTime")
        phased.append((s, phase_of(st, start, end)))
    keep_phase = phase
    sel = [s for s, ph in phased if (keep_phase == "all" or ph == keep_phase)]
    n_contest = sum(1 for _, ph in phased if ph == "contest")
    n_after = sum(1 for _, ph in phased if ph == "after")
    print(f"      共 {len(subs)} 条：比赛期间 {n_contest}，赛后补题 {n_after}"
          f"，本阶段采集 {len(sel)}（--phase {phase}）", file=sys.stderr)

    print(f"[3/5] 抓取代码（{len(sel)} 条）…", file=sys.stderr)
    items = []
    for idx, (s, ph) in enumerate(phased):
        if keep_phase != "all" and ph != keep_phase:
            continue
        code = fetch_code(cookie, s.get("submissionId"))
        it = {
            "submissionId": s.get("submissionId"),
            "student": s.get("userName") or "",
            "studentId": s.get("userId"),
            "problemIndex": s.get("index") or "",
            "problemId": s.get("problemId"),
            "language": s.get("languageName") or s.get("languageCategoryName") or "",
            "verdict": s.get("statusMessage") or "",
            "score": s.get("score"),
            "submitTime": s.get("submitTime"),
            "time": s.get("time"),
            "memory": s.get("memory"),
            "length": s.get("length"),
            "phase": ph,
            "code": code,
            "url": f"{BASE}/acm/contest/view-submission?submissionId={s.get('submissionId')}",
            "extractedAt": time_str(int(datetime.datetime.now().timestamp() * 1000)),
        }
        it["metrics"] = code_metrics(code)
        items.append(it)
        if (idx + 1) % 10 == 0:
            print(f"      {idx + 1}/{len(subs)}", file=sys.stderr)

    ok = sum(1 for it in items if it["code"])
    print(f"[4/5] 写数据库 + 导出 …", file=sys.stderr)

    # SQLite
    db_path = db_path or os.path.expanduser("~/.dsh/nowcoder/nowcoder.db")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    con = init_db(db_path)
    upsert_contest(con, info)
    for it in items:
        upsert_submission(con, it, contest_id, it["phase"])
    con.commit()
    con.close()

    # 导出文件
    base = os.path.join(out_dir, str(contest_id))
    code_dir = os.path.join(base, "code")
    os.makedirs(code_dir, exist_ok=True)

    manifest = {
        "contestId": contest_id,
        "contestName": name,
        "startTime": start,
        "endTime": end,
        "phase": phase,
        "totalRaw": len(subs),
        "inContest": n_contest,
        "afterContest": n_after,
        "selected": len(items),
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
    md.append(f"- 提交：{len(items)} 条（原始 {len(subs)} 条，比赛期间 {n_contest}，赛后 {n_after}）")
    md.append("")
    for it in sorted(items, key=lambda x: (x["student"], x["problemIndex"])):
        tag = " · ".join(filter(None, [it["language"], it["verdict"]]))
        md.append(f"## {it['student'] or '未知'} — {it['problemIndex'] or it['submissionId']}"
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

    print(f"[5/5] 完成 → {base}  |  DB: {db_path}", file=sys.stderr)
    print(json.dumps({"ok": True, "contestId": contest_id, "name": name,
                      "totalRaw": len(subs), "inContest": n_contest, "afterContest": n_after,
                      "selected": len(items), "extracted": ok, "dir": base,
                      "db": db_path, "similarityPairs": len(pairs)},
                     ensure_ascii=False))
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

    return extract(cookie, args.contest, args.out, args.phase, args.db)


if __name__ == "__main__":
    sys.exit(main())
