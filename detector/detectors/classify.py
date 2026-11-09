"""T6 分级器：确定性阈值组合 → verdict（纯规则，阈值从配置读）。"""
from typing import Dict

VERDICT_ORDER = {"实锤": 0, "中偏强": 1, "存疑": 2, "排除": 3}


def classify(scores: Dict, rare: Dict, timeline: Dict, thresholds: Dict, crowd_count: int = 0) -> str:
    """根据三口径相似度 + 稀有度 + 时间线给出确定性别级。

    同输入必同输出：只依赖传入数字，无随机、无模型。
    """
    l2 = scores.get("L2", 0.0)
    l3 = scores.get("L3", 0.0)
    md5_ws = scores.get("md5_ws_equal", False)
    rs = rare.get("score", 0.0)
    tv = timeline or {}

    # 人群模板：同一份代码被很多人（>=crowd_threshold）写过 → 是套路，排除
    if md5_ws and crowd_count >= thresholds.get("crowd_threshold", 3):
        return "排除"

    # 实锤：去空白后几乎一致 + 方向成立 + 时间窗内
    if md5_ws or l2 >= thresholds["solid_l2"]:
        if (tv.get("direction_valid")
                and tv.get("delta_seconds") is not None
                and tv["delta_seconds"] < thresholds["solid_delta_seconds"]):
            return "实锤"
        # 代码几乎一致但方向/时间不成立：仍属高度可疑，但不够"实锤"
        if md5_ws:
            return "中偏强"

    # 中偏强：稀有 n-gram 高度共享 + L3 结构高度相似
    if rs >= thresholds["mid_strong_rare"] and l3 >= thresholds["mid_strong_l3"]:
        return "中偏强"

    # 存疑：L3 较高 + 稀有 n-gram 一定共享
    if l3 >= thresholds["suspicious_l3"] and rs >= thresholds["suspicious_rare"]:
        return "存疑"

    return "排除"
