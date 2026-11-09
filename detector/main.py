"""牛客代码雷同检测器 · 主入口（去 LLM 化，确定性管道）。

用法:
  python main.py --contest 143786                        # 默认读 ~/.dsh/nowcoder/nowcoder.db
  python main.py --contest 143786 --db /path/nowcoder.db --all   # 含"排除"对
  python main.py --contest 143786 --json-out report.json

同输入必同输出：无随机、无时间戳、浮点固定精度、结果稳定排序。
"""
import argparse
import json
import os
import sys
import tomllib
from collections import Counter
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from detectors import __version__  # noqa: E402
from detectors.preprocess import (  # noqa: E402
    Submission, group_by_problem_lang, group_by_student, lang_bucket,
    load_submissions, representative,
)
from detectors.normalize import l2, l3_tokens  # noqa: E402
from detectors.similarity import (  # noqa: E402
    build_rare_sets, compute_scores, md5, rare_between,
)
from detectors.template import build_usage, common_ratio  # noqa: E402
from detectors.timeline import analyze_timeline  # noqa: E402
from detectors.classify import VERDICT_ORDER, classify  # noqa: E402


def load_config(path: str) -> Dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def detect(db_path: str, contest_id: int, phase: str, config: Dict, include_excluded: bool) -> Dict:
    cfg_ngram = config["ngram"]
    cfg_th = config["thresholds"]
    k = cfg_ngram["k"]
    rare_threshold = cfg_ngram["rare_threshold"]
    crowd_threshold = config.get("template", {}).get("crowd_threshold", 3)

    subs = load_submissions(db_path, contest_id, phase)
    groups = group_by_problem_lang(subs)

    results: List[Dict] = []
    pairs_compared = 0

    for (problem, lang), group in sorted(groups.items()):
        if lang == "other":
            continue
        by_student = group_by_student(group)
        reps: Dict[int, Submission] = {sid: representative(ss) for sid, ss in by_student.items()}
        tokens_by_student: Dict[int, List[str]] = {
            sid: l3_tokens(rep.code, lang) for sid, rep in reps.items()
        }
        usage = build_usage(tokens_by_student, k)
        rare_sets, _ = build_rare_sets(tokens_by_student, k, rare_threshold)

        # 同一份代码被多少不同学生写（模板/人群代码检测）
        md5_counter = Counter(md5(l2(rep.code)) for rep in reps.values())

        sids = sorted(reps.keys())
        for i in range(len(sids)):
            for j in range(i + 1, len(sids)):
                sa, sb = sids[i], sids[j]
                a, b = reps[sa], reps[sb]
                pairs_compared += 1

                scores = compute_scores(a.code, b.code, lang)
                rare = rare_between(rare_sets.get(sa, set()), rare_sets.get(sb, set()))
                timeline = analyze_timeline(by_student[sa], by_student[sb])
                crowd = md5_counter[md5(l2(a.code))]  # 与 A 相同代码的人数
                tr_a = common_ratio(tokens_by_student[sa], usage, k, rare_threshold)
                tr_b = common_ratio(tokens_by_student[sb], usage, k, rare_threshold)

                verdict = classify(scores, rare, timeline, cfg_th, crowd)

                if verdict == "排除" and not include_excluded:
                    continue

                results.append({
                    "pair": [a.student, b.student],
                    "student_ids": [sa, sb],
                    "problem": problem,
                    "problem_title": a.problem_title or b.problem_title or "",
                    "language": lang,
                    "verdict": verdict,
                    "scores": scores,
                    "rare": rare,
                    "timeline": timeline,
                    "template_ratio": round(max(tr_a, tr_b), 4),
                    "crowd_count": crowd,
                })

    # 确定性排序：severity → rare → L3 → 名字
    def sort_key(r):
        return (
            VERDICT_ORDER.get(r["verdict"], 9),
            -(r["rare"]["score"]),
            -(r["scores"]["L3"]),
            -(r["scores"]["L2"]),
            tuple(r["pair"]),
            r["problem"],
        )

    results.sort(key=sort_key)

    summary = Counter(r["verdict"] for r in results)
    return {
        "meta": {
            "version": __version__,
            "contest_id": contest_id,
            "phase": phase,
            "submissions": len(subs),
            "students": len({s.student_id for s in subs}),
            "pairs_compared": pairs_compared,
            "pairs_reported": len(results),
            "verdict_summary": dict(sorted(summary.items(), key=lambda kv: VERDICT_ORDER.get(kv[0], 9))),
        },
        "results": results,
    }


def main():
    ap = argparse.ArgumentParser(description="牛客代码雷同检测器（去 LLM 化）")
    ap.add_argument("--contest", type=int, required=True, help="比赛 ID")
    ap.add_argument("--db", default=os.path.expanduser("~/.dsh/nowcoder/nowcoder.db"))
    ap.add_argument("--phase", default="contest", choices=["contest", "after", "all"])
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml"))
    ap.add_argument("--all", action="store_true", help="也输出 verdict=排除 的对")
    ap.add_argument("--json-out", help="写 JSON 到文件（默认打印到 stdout）")
    args = ap.parse_args()

    config = load_config(args.config)
    report = detect(args.db, args.contest, args.phase, config, args.all)

    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"已写入 {args.json_out}（{report['meta']['pairs_reported']} 条）", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
