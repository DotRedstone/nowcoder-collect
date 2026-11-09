"""T1 数据预处理：从 SQLite 读比赛提交，清洗后交给后续层。

纯标准库。输入：nowcoder.db 的 submissions 表；输出：清洗后的提交列表。
"""
import sqlite3
import re
from dataclasses import dataclass
from typing import List, Dict, Tuple


@dataclass
class Submission:
    submission_id: int
    contest_id: int
    student: str
    student_id: int
    problem_index: str
    problem_id: int
    problem_title: str
    language: str
    verdict: str
    score: float
    submit_time: int       # 毫秒时间戳
    phase: str
    code: str
    url: str = ""
    loc: int = 0
    comments: int = 0
    comment_ratio: float = 0.0


def lang_bucket(lang: str) -> str:
    """语言分桶：C/C++ 归 cpp，Python/PyPy 归 python，其余归 other。"""
    l = (lang or "").lower()
    if "python" in l or "pypy" in l:
        return "python"
    if "java" in l:
        return "java"
    if "c" in l:  # C, C++, clang, gcc, g++ 都含 'c'
        return "cpp"
    return "other"


def strip_stderr(code: str) -> str:
    """剥离可能混入的 stderr/编译错误信息（当前 extractor 已只存源码，此处为兜底）。"""
    if not code:
        return code
    # 若源码开头被塞了编译错误文本（常见于把 stderr 拼进 code），这里不做激进处理，
    # 仅去掉两端空白；详细剥离留给后续 stderr 独立字段。
    return code.strip("\ufeff \t\r\n")


def load_submissions(db_path: str, contest_id: int, phase: str = "contest") -> List[Submission]:
    """读数据库，返回清洗后的提交列表（已过滤空代码、非目标 phase）。"""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM submissions WHERE contest_id=? AND phase=? AND code IS NOT NULL AND code != ''",
        (contest_id, phase),
    ).fetchall()
    con.close()

    subs = []
    for r in rows:
        d = dict(r)
        d["code"] = strip_stderr(d["code"] or "")
        if not d["code"]:
            continue
        # 只保留需要的字段，避免未知列
        subs.append(Submission(
            submission_id=d.get("submission_id"),
            contest_id=d.get("contest_id"),
            student=d.get("student") or "",
            student_id=d.get("student_id") or 0,
            problem_index=d.get("problem_index") or "",
            problem_id=d.get("problem_id") or 0,
            problem_title=d.get("problem_title") or "",
            language=d.get("language") or "",
            verdict=d.get("verdict") or "",
            score=d.get("score") or 0.0,
            submit_time=d.get("submit_time") or 0,
            phase=d.get("phase") or phase,
            code=d["code"],
            url=d.get("url") or "",
            loc=d.get("loc") or 0,
            comments=d.get("comments") or 0,
            comment_ratio=d.get("comment_ratio") or 0.0,
        ))
    return subs


def group_by_problem_lang(subs: List[Submission]) -> Dict[Tuple[str, str], List[Submission]]:
    """按 (题号, 语言桶) 分组，跨题/跨语言不比较。"""
    groups: Dict[Tuple[str, str], List[Submission]] = {}
    for s in subs:
        key = (s.problem_index, lang_bucket(s.language))
        groups.setdefault(key, []).append(s)
    return groups


def group_by_student(subs: List[Submission]) -> Dict[int, List[Submission]]:
    """按学生分组，用于时间线分析（每人该题的全部提交）。"""
    by_student: Dict[int, List[Submission]] = {}
    for s in subs:
        by_student.setdefault(s.student_id, []).append(s)
    return by_student


def representative(subs: List[Submission]) -> Submission:
    """取代表提交：最高分；同分取最长代码（最完整）。"""
    best = None
    for s in subs:
        sc = s.score if isinstance(s.score, (int, float)) else 0.0
        if best is None or sc > (best.score or 0) or (
            sc == (best.score or 0) and len(s.code) > len(best.code)
        ):
            best = s
    return best
