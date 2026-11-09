#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""牛客网 ACM 比赛代码提取器 —— DSH skill `nowcoder-collect` 的配套脚本。

从 ac.nowcoder.com 拉取某场比赛的全部提交 + 代码，按学生/题号落盘。

真实接口（2026-11 实测）：
  最近参加的比赛: GET /acm-heavy/acm/contest/profile/contest-joined-history
                   ?uid={uid}&page=1&pageSize=30&onlyJoinedFilter=true
                   &searchContestName=&onlyRatingFilter=false&contestEndFilter=true
  提交列表(JSON):  GET /acm/contest/status-list?token=&id={contestId}&page={n}&pageSize=50
                   -> data.data[] 每条含 userName/userId/index(题号)/languageName/
                      statusMessage/submissionId/problemId/score/submitTime
                      data.basicInfo.pageCount/statusCount 用于翻页
  代码页(HTML):    GET /acm/contest/view-submission?submissionId={id}
                   -> 代码在 <pre class="prettyprint lang-xxx">…</pre>（HTML 转义）

用法:
  python3 nowcoder_extract.py --recent [--uid 12345678]
  python3 nowcoder_extract.py --contest 143786 [--out ./out] [--cookie "..."]

Cookie 来源优先级: --cookie > 环境变量 NOWCODER_COOKIE > ~/.dsh/nowcoder/cookie.txt
"""
import argparse
import hashlib
import html as htmlmod
import json
import os
import re
import sys
import urllib.request

BASE = "https://ac.nowcoder.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# ---------------------------------------------------------------- cookie
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


# ---------------------------------------------------------------- 最近比赛
def list_recent(cookie, uid):
    url = (BASE + "/acm-heavy/acm/contest/profile/contest-joined-history"
           f"?uid={uid}&page=1&pageSize=30&onlyJoinedFilter=true"
           "&searchContestName=&onlyRatingFilter=false&contestEndFilter=true")
    d = get_json(url, cookie)
    data = d.get("data") or {}
    arr = data.get("dataList") or data.get("data") or []
    out = []
    for it in arr:
        out.append({
            "contestId": it.get("contestId"),
            "contestName": it.get("contestName"),
            "startTime": it.get("startTime"),
            "endTime": it.get("endTime"),
        })
    return out


def fmt_ms(ms):
    try:
        return time_str(int(ms))
    except Exception:
        return str(ms)


def time_str(ms):
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 提取
def fetch_submissions(cookie, contest_id):
    subs = []
    first = get_json(
        f"{BASE}/acm/contest/status-list?token=&id={contest_id}&page=1&pageSize=50", cookie)
    data = first.get("data") or {}
    bi = data.get("basicInfo") or {}
    page_count = bi.get("pageCount") or 1
    subs.extend(data.get("data") or [])
    for p in range(2, page_count + 1):
        r = get_json(
            f"{BASE}/acm/contest/status-list?token=&id={contest_id}&page={p}&pageSize=50", cookie)
        subs.extend((r.get("data") or {}).get("data") or [])
    return subs


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
    return (re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(s or "")).replace(" ", "_")[:60]
            or "unknown")


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


# 相似度（Winnowing 指纹 + Jaccard）
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
    ranked = sorted(students.values(),
                    key=lambda s: (-s["max_sim"], -s["sim_pairs"], -s["submissions"]))
    return ranked


def fence(code):
    f = "```"
    while f in (code or ""):
        f += "`"
    return f


# ---------------------------------------------------------------- 主流程
def extract(cookie, contest_id, out_dir, contest_name=""):
    print(f"[1/4] 拉取提交列表 contest={contest_id} …", file=sys.stderr)
    subs = fetch_submissions(cookie, contest_id)
    if not subs:
        print("该比赛没有提交记录（或比赛 ID 不对 / 未参赛）", file=sys.stderr)
        return 1
    print(f"      共 {len(subs)} 条提交", file=sys.stderr)

    print(f"[2/4] 抓取代码 …", file=sys.stderr)
    items = []
    for idx, s in enumerate(subs):
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
            "code": code,
            "url": f"{BASE}/acm/contest/view-submission?submissionId={s.get('submissionId')}",
        }
        it["metrics"] = code_metrics(code)
        items.append(it)
        if (idx + 1) % 10 == 0 or idx == len(subs) - 1:
            print(f"      {idx + 1}/{len(subs)}", file=sys.stderr)

    ok = sum(1 for it in items if it["code"])
    print(f"[3/4] 成功 {ok}/{len(items)}", file=sys.stderr)

    base = os.path.join(out_dir, str(contest_id))
    code_dir = os.path.join(base, "code")
    os.makedirs(code_dir, exist_ok=True)

    # manifest
    manifest = {
        "contestId": contest_id,
        "contestName": contest_name,
        "total": len(items),
        "extractedAt": time_str(int(__import__("time").time() * 1000)),
        "submissions": [
            {k: v for k, v in it.items() if k != "code"}
            for it in items
        ],
    }
    with open(os.path.join(base, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 单个源码文件
    used = {}
    for it in items:
        if not it["code"]:
            continue
        ext = ext_for_lang(it["language"])
        name = f"{sanitize(it['student'])}_{sanitize(it['problemIndex'])}_{sanitize(it['language'])}.{ext}"
        n = used.get(name, 0)
        used[name] = n + 1
        if n:
            name = f"{sanitize(it['student'])}_{sanitize(it['problemIndex'])}_{sanitize(it['language'])}_{n + 1}.{ext}"
        with open(os.path.join(code_dir, name), "w", encoding="utf-8") as f:
            f.write(it["code"])

    # 合集 markdown
    md = ["# 牛客代码收集", ""]
    md.append(f"- 比赛：{contest_name or contest_id}")
    md.append(f"- 提交总数：{len(items)}")
    md.append(f"- 提取时间：{manifest['extractedAt']}")
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

    # 相似度报告
    pairs = build_similarity(items)
    sim = ["# 相似度报告", ""]
    sim.append(f"- 参与比较：{sum(1 for it in items if it['code'])} 份代码")
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

    # 嫌疑排名（跨人雷同 + 提交统计）
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

    print(f"[4/4] 完成 → {base}", file=sys.stderr)
    print(json.dumps({"ok": True, "contestId": contest_id, "total": len(items),
                      "extracted": ok, "dir": base, "similarityPairs": len(pairs)},
                     ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description="牛客网 ACM 代码提取器")
    ap.add_argument("--recent", action="store_true", help="列出最近参加的比赛")
    ap.add_argument("--contest", help="比赛 ID")
    ap.add_argument("--uid", default="", help="牛客用户数字 ID（--recent 用，缺省读 NOWCODER_UID 或 ~/.dsh/nowcoder/uid.txt）")
    ap.add_argument("--name", default="", help="比赛名称（写入 manifest）")
    ap.add_argument("--out", default="./nowcoder-data", help="输出根目录")
    ap.add_argument("--cookie", help="登录 Cookie 字符串")
    args = ap.parse_args()

    cookie = load_cookie(args.cookie)

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

    return extract(cookie, args.contest, args.out, args.name)


if __name__ == "__main__":
    sys.exit(main())
