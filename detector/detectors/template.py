"""T4 模板剔除：高频 n-gram 识别与排除（题目模板免疫）。"""
from collections import defaultdict
from typing import Dict, List, Set, Tuple

from .similarity import ngrams


def build_usage(tokens_by_student: Dict[int, List[str]], k: int) -> Dict[Tuple, Set[int]]:
    usage: Dict[Tuple, Set[int]] = defaultdict(set)
    for sid, tokens in tokens_by_student.items():
        for ng in ngrams(tokens, k):
            usage[ng].add(sid)
    return usage


def template_ngrams(usage: Dict[Tuple, Set[int]], total_students: int, ratio: float = 0.7) -> Set[Tuple]:
    """被 >=ratio 比例学生使用的 n-gram 视为「题目模板」，应从相似度中排除。"""
    if total_students <= 0:
        return set()
    threshold = total_students * ratio
    return {ng for ng, users in usage.items() if len(users) >= threshold}


def common_ratio(tokens: List[str], usage: Dict[Tuple, Set[int]], k: int, rare_threshold: int) -> float:
    """一份代码里「常见 n-gram」占比（越高越像模板/套路）。"""
    gs = ngrams(tokens, k)
    if not gs:
        return 1.0
    common = sum(1 for ng in gs if len(usage.get(ng, set())) > rare_threshold)
    return round(common / len(gs), 4)
