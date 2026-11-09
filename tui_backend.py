"""Data access and review persistence for the Nowcoder TUI (stdlib only)."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

DB_PATH = Path.home() / ".dsh/nowcoder/nowcoder.db"
REVIEW_PATH = Path.home() / ".dsh/nowcoder/reviews.json"
COOKIE_PATH = Path.home() / ".dsh/nowcoder/cookie.txt"
UID_PATH = Path.home() / ".dsh/nowcoder/uid.txt"
STATES = ("待复核", "已核查", "无需跟进", "需进一步核实")


def read_secret(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def write_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(value.strip())
    os.chmod(path, 0o600)


def local_contests(db: Path = DB_PATH) -> list[dict[str, Any]]:
    if not db.is_file():
        return []
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute("""
                SELECT c.contest_id, c.name, COUNT(s.submission_id) AS submissions,
                       SUM(CASE WHEN s.code IS NOT NULL AND s.code != '' THEN 1 ELSE 0 END) AS codes
                  FROM contests c
                  LEFT JOIN submissions s ON s.contest_id = c.contest_id
                 GROUP BY c.contest_id, c.name
                 ORDER BY c.start_time DESC, c.contest_id DESC
            """).fetchall()
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                return []
            raise
    return [dict(row) for row in rows]


def merge_contests(local: list[dict], remote: list[dict]) -> list[dict]:
    """Keep local contests accessible even while logged out or offline."""
    merged = {int(c["contest_id"]): dict(c) for c in local}
    for c in remote:
        cid = int(c["contestId"])
        entry = merged.setdefault(cid, {
            "contest_id": cid, "name": "", "submissions": 0, "codes": 0,
        })
        entry["name"] = entry["name"] or c.get("contestName") or f"比赛 #{cid}"
    return sorted(merged.values(), key=lambda c: c["contest_id"], reverse=True)


def case_key(contest_id: int, phase: str, result: dict) -> str:
    """Use stable IDs, not names or risk classifications (which may change)."""
    ids = sorted(int(i) for i in result["student_ids"])
    return f"{int(contest_id)}:{phase}:{result['problem']}:{ids[0]}:{ids[1]}"


class ReviewStore:
    def __init__(self, path: Path = REVIEW_PATH):
        self.path = path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.data = raw if isinstance(raw, dict) else {}
        except FileNotFoundError:
            self.data = {}
        except json.JSONDecodeError as e:
            raise ValueError(f"复核记录损坏，请先备份并修复 {path}: {e}") from e

    def status(self, key: str) -> str:
        value = self.data.get(key, {})
        return value.get("status", STATES[0]) if isinstance(value, dict) else STATES[0]

    def note(self, key: str) -> str:
        value = self.data.get(key, {})
        return value.get("note", "") if isinstance(value, dict) else ""

    def set(self, key: str, *, status: str | None = None, note: str | None = None) -> None:
        if status is not None and status not in STATES:
            raise ValueError("invalid review status")
        record = dict(self.data.get(key) or {})
        if status is not None:
            record["status"] = status
        if note is not None:
            record["note"] = note[:4000]
        self.data[key] = record
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".reviews-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(self.data, out, ensure_ascii=False, indent=2, sort_keys=True)
                out.write("\n")
                out.flush()
                os.fsync(out.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def pair_sources(db: Path, contest_id: int, phase: str, result: dict) -> tuple[dict, dict]:
    """Pick the same highest-scoring / longest representatives as the detector."""
    from detector.detectors.preprocess import lang_bucket

    ids = [int(s) for s in result["student_ids"]]
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT student_id, student, submission_id, code, language, score, url
              FROM submissions
             WHERE contest_id = ? AND phase = ? AND problem_index = ?
               AND student_id IN (?, ?) AND code IS NOT NULL AND code != ''
             ORDER BY submission_id
        """, (contest_id, phase, result["problem"], *ids)).fetchall()
    selected = []
    for uid in ids:
        candidates = [
            dict(row) for row in rows
            if int(row["student_id"]) == uid
            and lang_bucket(row["language"]) == result["language"]
            and (row["code"] or "").strip()
        ]
        if not candidates:
            selected.append({"student": str(uid), "code": "", "submission_id": None, "url": ""})
            continue
        selected.append(max(candidates, key=lambda row: (float(row["score"] or 0), len(row["code"]))))
    return selected[0], selected[1]
