"""T3 相似度引擎：三口径 difflib + 稀有 n-gram（MOSS 式）。"""
import difflib
import hashlib
from collections import defaultdict
from typing import Dict, List, Set, Tuple

from .normalize import l1, l2, l3_tokens


def ratio(a, b) -> float:
    """difflib.SequenceMatcher 相似度，接受 str 或 list。"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8", "replace")).hexdigest()


def ngrams(tokens: List[str], k: int) -> Set[Tuple[str, ...]]:
    if len(tokens) < k:
        return set()
    return {tuple(tokens[i:i + k]) for i in range(len(tokens) - k + 1)}


def compute_scores(code_a: str, code_b: str, lang: str) -> Dict:
    ta = l3_tokens(code_a, lang)
    tb = l3_tokens(code_b, lang)
    return {
        "L1": round(ratio(l1(code_a), l1(code_b)), 4),
        "L2": round(ratio(l2(code_a), l2(code_b)), 4),
        "L3": round(ratio(ta, tb), 4),
        "md5_ws_equal": md5(l2(code_a)) == md5(l2(code_b)),
    }


def build_rare_sets(tokens_by_student: Dict[int, List[str]], k: int, rare_threshold: int):
    """返回 (每学生的稀有 n-gram 集合, 全库稀有 n-gram 集合)。

    usage[ngram] = 使用该 n-gram 的不同学生数。
    稀有 = 使用学生数 <= rare_threshold。
    """
    usage: Dict[Tuple, Set[int]] = defaultdict(set)
    for sid, tokens in tokens_by_student.items():
        for ng in ngrams(tokens, k):
            usage[ng].add(sid)
    rare_global = {ng for ng, users in usage.items() if len(users) <= rare_threshold}
    rare_sets: Dict[int, Set[Tuple]] = {}
    for sid, tokens in tokens_by_student.items():
        rare_sets[sid] = {ng for ng in ngrams(tokens, k) if ng in rare_global}
    return rare_sets, usage


def rare_between(rare_a: Set, rare_b: Set) -> Dict:
    if not rare_a or not rare_b:
        return {"shared": 0, "score": 0.0, "samples": []}
    shared = rare_a & rare_b
    score = len(shared) / min(len(rare_a), len(rare_b))
    samples = [" ".join(ng) for ng in sorted(shared)[:8]]
    return {"shared": len(shared), "score": round(score, 4), "samples": samples}
