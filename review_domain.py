"""Local-first review data, settings, staff exclusion and final ranking.

All data is stored under ~/.dsh/nowcoder. This module uses the standard library
only; the detector's algorithm and JSON output are not changed.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import tempfile
import tomllib
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from tui_backend import DB_PATH, REVIEW_PATH, case_key

CONFIG_DIR = Path.home() / ".dsh" / "nowcoder"
SETTINGS_PATH = CONFIG_DIR / "settings.toml"
FILTERS_PATH = CONFIG_DIR / "filters.toml"
STAFF_PATH = CONFIG_DIR / "staff.toml"
DETECTOR_PATH = CONFIG_DIR / "detector.toml"
PEOPLE_PATH = CONFIG_DIR / "people.json"
REPORTS_DIR = CONFIG_DIR / "reports"
EXPORT_DIR = Path.home() / "nowcoder-data"
MARKS = ("unreviewed", "normal", "suspect", "cheater")
MARK_TEXT = {"unreviewed": "未看", "normal": "正常", "suspect": "存疑", "cheater": "作弊者"}
RANK_POLICY = {
    "cheater": ("剔除", "标记"),
    "suspect": ("照常", "标记", "剔除"),
    "staff": ("剔除", "标记"),
    "format": ("markdown", "csv", "html"),
}
DEFAULT_SETTINGS = {
    "threads": 16, "collect_after": False, "auto_analyze": True,
    "db_path": str(DB_PATH), "out_dir": str(EXPORT_DIR),
    "theme": "auto", "nerd_font": False, "density": "紧凑",
    "language": "中文", "key_hints": True,
    "rank_cheater": "剔除", "rank_suspect": "照常", "rank_staff": "剔除",
    "rank_format": "markdown",
}
SCHEMA = {
    "threads": ("int", 1, 32),
    "collect_after": ("bool",), "auto_analyze": ("bool",),
    "db_path": ("str",), "out_dir": ("str",), "theme": ("str",),
    "nerd_font": ("bool",), "density": ("enum", "紧凑", "宽松"),
    "language": ("enum", "中文", "English"), "key_hints": ("bool",),
    "rank_cheater": ("enum", *RANK_POLICY["cheater"]),
    "rank_suspect": ("enum", *RANK_POLICY["suspect"]),
    "rank_staff": ("enum", *RANK_POLICY["staff"]),
    "rank_format": ("enum", *RANK_POLICY["format"]),
}
DEFAULT_FILTERS = {"match_field": "比赛名", "case_sensitive": False, "whitelist": [], "blacklist": []}

def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    if not isinstance(raw, dict):
        raise ValueError(f"无效配置文件：{path}")
    return raw

def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False)

def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def load_settings(path: Path = SETTINGS_PATH) -> dict:
    settings = dict(DEFAULT_SETTINGS)
    for key, value in _read_toml(path).items():
        if key in SCHEMA:
            validate_setting(key, value)
            settings[key] = value
    return settings

def validate_setting(key: str, value: Any) -> None:
    if key not in SCHEMA:
        raise ValueError(f"未知设置：{key}")
    spec = SCHEMA[key]
    if spec[0] == "bool" and not isinstance(value, bool):
        raise ValueError(f"{key} 必须为开关")
    if spec[0] == "int" and (type(value) is not int or not spec[1] <= value <= spec[2]):
        raise ValueError(f"{key} 必须介于 {spec[1]}..{spec[2]}")
    if spec[0] == "str" and (not isinstance(value, str) or len(value) > 4096):
        raise ValueError(f"{key} 必须为字符串")
    if spec[0] == "enum" and value not in spec[1:]:
        raise ValueError(f"{key} 不在可选值中")

def save_settings(settings: dict, path: Path = SETTINGS_PATH) -> None:
    for key, value in settings.items():
        validate_setting(key, value)
    lines = ["# 牛客审查工作台 · 用户设置"]
    lines += [f"{key} = {_toml_scalar(settings.get(key, DEFAULT_SETTINGS[key]))}" for key in DEFAULT_SETTINGS]
    _write_atomic(path, "\n".join(lines) + "\n")

def validate_filters(config: dict) -> None:
    if config.get("match_field", "比赛名") not in ("比赛名", "比赛名+ID"):
        raise ValueError("未知比赛匹配字段")
    if not isinstance(config.get("case_sensitive", False), bool):
        raise ValueError("大小写敏感必须为开关")
    for group in ("whitelist", "blacklist"):
        rules = config.get(group, [])
        if not isinstance(rules, list):
            raise ValueError(f"{group} 必须为规则列表")
        for r in rules:
            if not isinstance(r, dict) or not isinstance(r.get("pattern"), str) or not isinstance(r.get("enabled", True), bool):
                raise ValueError("规则结构不合法")
            re.compile(r["pattern"], 0 if config.get("case_sensitive", False) else re.IGNORECASE)

def load_filters(path: Path = FILTERS_PATH) -> dict:
    data = dict(DEFAULT_FILTERS)
    data.update(_read_toml(path))
    validate_filters(data)
    return data

def save_filters(config: dict, path: Path = FILTERS_PATH) -> None:
    validate_filters(config)
    lines = ["# 黑名单优先；白名单为空表示显示全部",
             f"match_field = {_toml_scalar(config.get('match_field', '比赛名'))}",
             f"case_sensitive = {_toml_scalar(config.get('case_sensitive', False))}"]
    for group in ("whitelist", "blacklist"):
        for rule in config.get(group, []):
            lines += ["", f"[[{group}]]",
                      f"pattern = {_toml_scalar(rule['pattern'])}",
                      f"enabled = {_toml_scalar(rule.get('enabled', True))}"]
    _write_atomic(path, "\n".join(lines) + "\n")

def contest_matches(contest: dict, config: dict) -> bool:
    flags = 0 if config.get("case_sensitive") else re.IGNORECASE
    candidate = str(contest.get("name") or "")
    if config.get("match_field") == "比赛名+ID":
        candidate += " " + str(contest.get("contest_id", ""))
    def match(group: str) -> bool:
        return any(bool(re.search(rule["pattern"], candidate, flags)) for rule in config.get(group, [])
                   if rule.get("enabled", True) and rule.get("pattern"))
    # Empty means no enabled whitelist patterns (not a zero-match whitelist).
    whitelist = any(rule.get("enabled", True) and rule.get("pattern") for rule in config.get("whitelist", []))
    return not match("blacklist") and (not whitelist or match("whitelist"))

def load_staff(path: Path = STAFF_PATH) -> list[dict]:
    raw = _read_toml(path)
    people = raw.get("people", [])
    if not isinstance(people, list):
        raise ValueError("管理人员名单格式错误")
    result = []
    for row in people:
        uid = int(row["uid"])
        if uid <= 0 or not isinstance(row.get("enabled", True), bool):
            raise ValueError("人员 UID 或启用状态无效")
        if uid in [x["uid"] for x in result]:
            raise ValueError("人员 UID 重复")
        result.append({"uid": uid, "name": str(row.get("name", "")), "enabled": row.get("enabled", True)})
    return result

def save_staff(people: list[dict], path: Path = STAFF_PATH) -> None:
    # Validate before mutating a persistent file.
    if len({int(row["uid"]) for row in people}) != len(people):
        raise ValueError("人员 UID 重复")
    lines = ["# 仅用于排除管理者代码比较和默认榜单"]
    for row in people:
        if int(row["uid"]) <= 0:
            raise ValueError("UID 必须为正整数")
        lines += ["", "[[people]]", f"uid = {int(row['uid'])}",
                  f"name = {_toml_scalar(row.get('name', ''))}",
                  f"enabled = {_toml_scalar(bool(row.get('enabled', True)))}"]
    _write_atomic(path, "\n".join(lines) + "\n")

def hidden_uids(staff: list[dict]) -> set[int]:
    return {int(row["uid"]) for row in staff if row.get("enabled", True)}

class PeopleStore:
    """Persist manual human labels per contest, never auto-label from similarity."""
    def __init__(self, path: Path = PEOPLE_PATH):
        self.path = path
        if path.is_file():
            obj = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(obj, dict):
                raise ValueError("人员标记文件格式错误")
            self.data = obj
        else:
            self.data = {}

    @staticmethod
    def key(contest_id: int, uid: int) -> str:
        return f"{int(contest_id)}:{int(uid)}"

    def get(self, contest_id: int, uid: int) -> dict:
        row = self.data.get(self.key(contest_id, uid), {})
        return row if isinstance(row, dict) else {}

    def mark(self, contest_id: int, uid: int, mark: str, note: str | None = None) -> None:
        if mark not in MARKS:
            raise ValueError("无效人员标记")
        key = self.key(contest_id, uid)
        record = dict(self.data.get(key) or {})
        record["mark"] = mark
        if note is not None:
            record["note"] = note[:4000]
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.data[key] = record
        _write_atomic(self.path, json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

def _rows(db: Path, sql: str, args: tuple = ()) -> list[dict]:
    if not db.exists():
        return []
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        return [dict(x) for x in con.execute(sql, args).fetchall()]

def all_submissions(contest_id: int, db: Path = DB_PATH, *, phase: str = "contest") -> list[dict]:
    return _rows(db, """SELECT * FROM submissions
        WHERE contest_id=? AND phase=? ORDER BY submit_time ASC, submission_id ASC""",
                 (int(contest_id), phase))

def people_for_contest(contest_id: int, db: Path = DB_PATH, staff: set[int] | None = None) -> list[dict]:
    subs = all_submissions(contest_id, db)
    staff = staff or set()
    users: dict[int, dict] = {}
    for s in subs:
        uid = int(s.get("student_id") or 0)
        if uid <= 0:
            continue
        person = users.setdefault(uid, {"uid": uid, "name": s.get("student") or str(uid),
                                          "submissions": 0, "ac_problems": set(), "best": {}})
        person["submissions"] += 1
        if s.get("student"):
            person["name"] = s["student"]
        if is_ac(s.get("verdict", "")):
            person["ac_problems"].add(s.get("problem_index"))
        key = s.get("problem_index") or ""
        person["best"][key] = max(float(s.get("score") or 0), person["best"].get(key, 0))
    result = []
    for person in users.values():
        person["ac"] = len(person.pop("ac_problems"))
        person["total"] = round(sum(person.pop("best").values()), 2)
        person["staff"] = person["uid"] in staff
        result.append(person)
    return sorted(result, key=lambda p: (p["staff"], -p["total"], -p["ac"], p["name"]))

def problems_for_contest(contest_id: int, db: Path = DB_PATH) -> list[dict]:
    subs = all_submissions(contest_id, db)
    items: dict[str, dict] = {}
    for s in subs:
        key = str(s.get("problem_index") or "?")
        row = items.setdefault(key, {"problem": key, "title": s.get("problem_title") or "",
                                     "ac_ids": set(), "submissions": 0})
        if not row["title"]:
            row["title"] = s.get("problem_title") or ""
        row["submissions"] += 1
        if is_ac(s.get("verdict", "")):
            row["ac_ids"].add(int(s.get("student_id") or 0))
    return [{**row, "ac": len(row.pop("ac_ids"))} for _, row in sorted(items.items())]

def person_submissions(contest_id: int, uid: int, db: Path = DB_PATH) -> list[dict]:
    return _rows(db, """SELECT * FROM submissions
        WHERE contest_id=? AND student_id=?
        ORDER BY problem_index, submit_time ASC, submission_id ASC""", (int(contest_id), int(uid)))

def problem_submissions(contest_id: int, problem: str, db: Path = DB_PATH) -> list[dict]:
    return _rows(db, """SELECT * FROM submissions
        WHERE contest_id=? AND phase='contest' AND problem_index=?
        ORDER BY student_id, submit_time ASC, submission_id ASC""", (int(contest_id), problem))

def is_ac(verdict: str | None) -> bool:
    value = (verdict or "").lower()
    return value in ("ac", "accepted", "答案正确") or "accepted" in value or "答案正确" in value

def representative_for(subs: list[dict]) -> dict | None:
    candidates = [s for s in subs if (s.get("code") or "").strip()]
    return max(candidates, key=lambda s: (float(s.get("score") or 0), len(s.get("code") or "")),
               default=None)

def representatives_by_problem(rows: list[dict]) -> list[dict]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[int(row.get("student_id") or 0)].append(row)
    return [rep for key in sorted(grouped) if (rep := representative_for(grouped[key]))]

@contextmanager
def detector_db(db: Path, ignored_uids: set[int]) -> Iterator[Path]:
    """Copy then filter SQLite input; original database and detector remain untouched."""
    if not ignored_uids:
        yield db
        return
    with tempfile.TemporaryDirectory(prefix="nowcoder-detector-") as directory:
        filtered = Path(directory) / "filtered.sqlite3"
        with sqlite3.connect(db) as source, sqlite3.connect(filtered) as target:
            source.backup(target)
            values = sorted(int(x) for x in ignored_uids)
            for start in range(0, len(values), 900):
                batch = values[start:start + 900]
                target.execute(
                    "DELETE FROM submissions WHERE student_id IN (" + ",".join("?" * len(batch)) + ")",
                    batch)
            target.commit()
        yield filtered

def report_key(staff: set[int], config: dict | None = None) -> str:
    payload = {"staff": sorted(staff), "config": config or {}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

def save_report(contest_id: int, report: dict, key: str, directory: Path = REPORTS_DIR) -> None:
    _write_atomic(directory / f"{contest_id}.json", json.dumps(
        {"input_key": key, "report": report}, ensure_ascii=False, indent=2) + "\n")

def load_report(contest_id: int, key: str, directory: Path = REPORTS_DIR) -> dict | None:
    path = directory / f"{contest_id}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("input_key") != key:
        return None
    return data.get("report")

RANK_SNAPSHOT_DIR = CONFIG_DIR / "ranking-source"

def save_rank_snapshot(contest_id: int, db: Path, problems: list[dict],
                       signup: dict, submissions: list[dict],
                       directory: Path | None = None) -> Path:
    """Save official rank-source values, including zero-submit signups.

    This is not an official certification; the source is Nowcoder's submit-list
    response. Store only aggregated scores and UID/name, not credentials.
    """
    full = {int(p["problemId"]): float(p.get("score") or 0) for p in problems}
    entries: dict[int, dict] = {
        int(uid): {"uid": int(uid), "name": u.get("name") or str(uid),
                   "total": 0.0, "ac": 0, "submissions": 0, "best": {}}
        for uid, u in signup.items()}
    for raw in submissions:
        uid, pid = int(raw["uid"]), int(raw["problemId"])
        row = entries.setdefault(uid, {
            "uid": uid, "name": str(uid), "total": 0.0,
            "ac": 0, "submissions": 0, "best": {},
        })
        row["submissions"] += 1
        score = (full.get(pid, 0) if raw.get("status") == 5 else
                 round(float(raw.get("rightCaseRate") or 0) * full.get(pid, 0), 2))
        best = row["best"]
        best[str(pid)] = max(float(best.get(str(pid), 0)), score)
    records = []
    for row in entries.values():
        row["total"] = round(sum(row["best"].values()), 2)
        row["ac"] = sum(float(score) >= full.get(int(pid), 0) - 1e-6
                        for pid, score in row["best"].items())
        del row["best"]
        records.append(row)
    records.sort(key=lambda row: (-row["total"], -row["ac"], row["name"]))
    directory = directory if directory is not None else RANK_SNAPSHOT_DIR
    target = directory / f"{contest_id}.json"
    payload = {"contest_id": int(contest_id), "db": str(db.expanduser().resolve()),
               "rows": records, "source": "nowcoder-submit-list"}
    _write_atomic(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return target

def rank_source_available(contest_id: int, db: Path = DB_PATH,
                          directory: Path | None = None) -> bool:
    directory = directory if directory is not None else RANK_SNAPSHOT_DIR
    path = directory / f"{contest_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("db") == str(db.expanduser().resolve()) and data.get("contest_id") == int(contest_id)
    except (OSError, ValueError):
        return False

def local_rank(contest_id: int, db: Path = DB_PATH) -> list[dict]:
    """IOI max(score per problem), same order as compute_rank.

    The local DB only holds users with submissions. Full-score metadata and
    zero-submission signups are unavailable, so expose this as a provisional
    offline ranking rather than claiming equivalence to the official list.
    """
    if rank_source_available(contest_id, db):
        try:
            raw = json.loads((RANK_SNAPSHOT_DIR / f"{contest_id}.json").read_text(encoding="utf-8"))
            return [{**row, "rank": i} for i, row in enumerate(raw["rows"], 1)]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    people = people_for_contest(contest_id, db)
    return [{**p, "rank": i} for i, p in enumerate(
        sorted(people, key=lambda p: (-p["total"], -p["ac"], p["name"])), 1)]

def final_rank(rows: list[dict], people: PeopleStore, contest_id: int,
               staff: set[int], *, cheater: str = "剔除", suspect: str = "照常",
               staff_policy: str = "剔除") -> list[dict]:
    if cheater not in RANK_POLICY["cheater"] or suspect not in RANK_POLICY["suspect"] or staff_policy not in RANK_POLICY["staff"]:
        raise ValueError("排行榜策略不合法")
    result = []
    rank = 0
    for row in sorted(rows, key=lambda r: (-float(r.get("total") or 0),
                                         -int(r.get("ac") or 0), str(r.get("name") or ""))):
        entry = dict(row)
        uid = int(row["uid"])
        mark = people.get(contest_id, uid).get("mark", "unreviewed")
        hidden = uid in staff
        policy = staff_policy if hidden else (cheater if mark == "cheater" else suspect if mark == "suspect" else "照常")
        if policy == "剔除":
            continue
        if policy != "标记":
            rank += 1
        entry["rank"] = rank if policy != "标记" else "—"
        entry["mark"] = "管理" if hidden else MARK_TEXT.get(mark, "未看")
        entry["policy"] = policy
        result.append(entry)
    return result

def export_ranking(contest_id: int, rows: list[dict], fmt: str,
                   directory: Path = EXPORT_DIR) -> Path:
    if fmt not in RANK_POLICY["format"]:
        raise ValueError("不支持的导出格式")
    suffix = {"markdown": "md", "csv": "csv", "html": "html"}[fmt]
    target = directory / str(int(contest_id)) / ("ranking." + suffix)
    fields = ("rank", "name", "uid", "ac", "total", "mark")
    table = [[str(row.get(key, "")) for key in fields] for row in rows]
    if fmt == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(("名次", "昵称", "UID", "AC", "得分", "标记"))
        writer.writerows(table)
        data = stream.getvalue()
    elif fmt == "markdown":
        def md(s: str) -> str:
            return str(s).replace("|", "\\|").replace("\n", " ")
        lines = [f"# 比赛 #{contest_id} 最终榜单（人工审核）", "",
                 "> 依据本地可用提交重建。仅供内部复核；缺少官方报名用户和题目满分时，排名可能与官方榜不同。", "",
                 "| 名次 | 昵称 | UID | AC | 得分 | 标记 |",
                 "|---:|---|---:|---:|---:|---|"]
        lines += ["| " + " | ".join(map(md, row)) + " |" for row in table]
        data = "\n".join(lines) + "\n"
    else:
        cells = "\n".join("<tr>" + "".join("<td>" + html.escape(x) + "</td>" for x in row) + "</tr>" for row in table)
        data = (
            '<!doctype html><html lang="zh"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>牛客内部训练赛榜单</title><style>'
            'body{background:#12141a;color:#e8e8ee;font:16px system-ui;max-width:960px;margin:auto;padding:2rem}'
            'table{width:100%;border-collapse:collapse}td,th{padding:.7rem;border-bottom:1px solid #353942;text-align:left}'
            'th{color:#a3b9d1}tr:hover{background:#20242b}</style>'
            f'<h1>比赛 #{contest_id} 最终榜单</h1>'
            '<p>本地推算成绩，仅供内部人工复核。</p><table><thead><tr>'
            '<th>名次</th><th>昵称</th><th>UID</th><th>AC</th><th>得分</th><th>标记</th>'
            '</tr></thead><tbody>' + cells + '</tbody></table></html>')
    _write_atomic(target, data)
    return target
