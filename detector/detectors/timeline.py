"""T5 时间线引擎：谁先谁后 / 失败序列 / 时间差（确定性）。"""
from typing import Dict, List, Optional

from .preprocess import Submission

AC_VERDICTS = ("答案正确", "Accepted", "通过")


def is_ac(verdict: str) -> bool:
    return any(v in (verdict or "") for v in AC_VERDICTS)


def classify_error(verdict: str) -> str:
    v = verdict or ""
    if "编译" in v or "Compile" in v:
        return "编译错误"
    if "超时" in v or "Time" in v or "TLE" in v:
        return "超时"
    if "内存" in v or "Memory" in v:
        return "内存超限"
    if "答案" in v or "Wrong" in v or "WA" in v:
        return "答案错误"
    if "段错误" in v or "Seg" in v:
        return "段错误"
    if "格式" in v or "Presentation" in v:
        return "格式错误"
    if "输出" in v:
        return "输出超限"
    return v or "未知"


def analyze_timeline(subs_a: List[Submission], subs_b: List[Submission]) -> Optional[Dict]:
    """对两个学生的同题提交做时间线分析。"""
    if not subs_a or not subs_b:
        return None
    a = sorted(subs_a, key=lambda s: s.submit_time)
    b = sorted(subs_b, key=lambda s: s.submit_time)

    first_a, first_b = a[0].submit_time, b[0].submit_time
    first_submit_delta = abs(first_a - first_b) / 1000.0

    ac_a = min((s.submit_time for s in a if is_ac(s.verdict)), default=None)
    ac_b = min((s.submit_time for s in b if is_ac(s.verdict)), default=None)

    who_first = None
    delta_seconds = None
    if ac_a is not None and ac_b is not None:
        who_first = "A" if ac_a <= ac_b else "B"
        delta_seconds = abs(ac_a - ac_b) / 1000.0
    elif ac_a is not None:
        who_first = "A"
    elif ac_b is not None:
        who_first = "B"

    # 方向有效性：后交方在对方 AC 之后才开始提交
    direction_valid = False
    if who_first == "A" and ac_a is not None and first_b >= ac_a:
        direction_valid = True
    elif who_first == "B" and ac_b is not None and first_a >= ac_b:
        direction_valid = True

    # 后交方 AC 前的失败次数与错误类型
    later = b if who_first == "A" else a
    later_ac = ac_b if who_first == "A" else ac_a
    failures = 0
    error_types: List[str] = []
    for s in later:
        if s.submit_time < (later_ac if later_ac is not None else float("inf")) and not is_ac(s.verdict):
            failures += 1
            error_types.append(classify_error(s.verdict))

    return {
        "who_first": who_first,
        "delta_seconds": round(delta_seconds, 1) if delta_seconds is not None else None,
        "first_submit_delta": round(first_submit_delta, 1),
        "later_failures_before_ac": failures,
        "direction_valid": direction_valid,
        "error_types": error_types[:10],
    }
