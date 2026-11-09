"""Editable user detector thresholds; the bundled detector/config.toml stays immutable."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path
from review_domain import DETECTOR_PATH, _write_atomic

BUNDLED = Path(__file__).resolve().parent / "detector" / "config.toml"
LIMITS = {
    "ngram.k": (int, 2, 20, 1),
    "ngram.rare_threshold": (int, 1, 50, 1),
    "thresholds.solid_l2": (float, 0, 1, 0.01),
    "thresholds.solid_delta_seconds": (int, 1, 86400, 60),
    "thresholds.mid_strong_rare": (float, 0, 1, 0.05),
    "thresholds.mid_strong_l3": (float, 0, 1, 0.05),
    "thresholds.suspicious_l3": (float, 0, 1, 0.05),
    "thresholds.suspicious_rare": (float, 0, 1, 0.05),
    "template.global_ratio": (float, 0, 1, 0.05),
    "template.crowd_threshold": (int, 2, 100, 1),
}

def defaults() -> dict:
    with BUNDLED.open("rb") as f:
        return tomllib.load(f)

def flatten(config: dict) -> dict:
    return {key: config[key.split(".")[0]][key.split(".")[1]] for key in LIMITS}

def unflatten(items: dict) -> dict:
    cfg = defaults()
    for key, value in items.items():
        category, param = key.split(".")
        cfg[category][param] = value
    return cfg

def check(key: str, value) -> int | float:
    if key not in LIMITS:
        raise ValueError("未支持的阈值")
    t, lo, hi, _ = LIMITS[key]
    if type(value) not in (int, float):
        raise ValueError("必须是数值")
    val = t(value)
    if t is int and float(value) != float(val):
        raise ValueError("整数项不允许小数")
    if not lo <= val <= hi:
        raise ValueError(f"{key} 超出范围 [{lo}, {hi}]")
    return val

def load(path: Path = DETECTOR_PATH) -> dict:
    cfg = defaults()
    if path.exists():
        with path.open("rb") as f:
            custom = tomllib.load(f)
        for group in ("ngram", "thresholds", "template"):
            for name, value in custom.get(group, {}).items():
                key = f"{group}.{name}"
                if key in LIMITS:
                    cfg[group][name] = check(key, value)
    return flatten(cfg)

def save(items: dict, path: Path = DETECTOR_PATH) -> None:
    checked = {key: check(key, value) for key, value in items.items()}
    config = unflatten(checked)
    lines = [f"version = {json.dumps(config.get('version', '1.0.0'))}"]
    for group in ("ngram", "thresholds", "template"):
        lines += ["", f"[{group}]"]
        for name, value in config[group].items():
            lines.append(f"{name} = {value}")
    _write_atomic(path, "\n".join(lines) + "\n")
