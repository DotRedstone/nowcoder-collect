"""Tests for the offline-first review workflow. No Textual dependency required."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tui_backend import ReviewStore, case_key, local_contests, merge_contests, pair_sources


class ReviewTests(unittest.TestCase):
    def test_review_status_survives_restart_and_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviews.json"
            store = ReviewStore(path)
            store.set("case", status="已核查", note="比对过两份代码")
            next_store = ReviewStore(path)
            self.assertEqual(next_store.status("case"), "已核查")
            self.assertEqual(next_store.note("case"), "比对过两份代码")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_status_never_changes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ReviewStore(Path(tmp) / "r.json")
            with self.assertRaises(ValueError):
                store.set("case", status="认定作弊")
            self.assertFalse(store.path.exists())

    def test_case_key_is_stable_on_order_and_verdict(self):
        result = {"student_ids": [9, 4], "problem": "B", "verdict": "存疑"}
        other = {"student_ids": [4, 9], "problem": "B", "verdict": "实锤"}
        self.assertEqual(case_key(123, "contest", result), case_key(123, "contest", other))

    def test_merge_keeps_offline_and_remote(self):
        rows = merge_contests(
            [{"contest_id": 3, "name": "本地", "submissions": 10, "codes": 8}],
            [{"contestId": 4, "contestName": "远程"}, {"contestId": 3, "contestName": "另一名称"}],
        )
        self.assertEqual([r["contest_id"] for r in rows], [4, 3])
        self.assertEqual(rows[1]["name"], "本地")
        self.assertEqual(rows[1]["codes"], 8)

    def test_local_contests_and_representative_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.db"
            with sqlite3.connect(path) as con:
                con.executescript("""
                CREATE TABLE contests (contest_id INTEGER, name TEXT, start_time INTEGER);
                CREATE TABLE submissions (
                    submission_id INTEGER, contest_id INTEGER, student_id INTEGER,
                    student TEXT, problem_index TEXT, phase TEXT, code TEXT,
                    language TEXT, score REAL, url TEXT
                );
                INSERT INTO contests VALUES (123, '训练赛', 10);
                INSERT INTO submissions VALUES
                  (1,123,4,'A','B','contest','short','C++',100,'one'),
                  (2,123,4,'A','B','contest','longer source','C++',100,'two'),
                  (3,123,9,'B','B','contest','print(1)','Python',100,'three'),
                  (4,123,9,'B','B','contest','result = 7','C++',90,'four');
                """)
            contests = local_contests(path)
            self.assertEqual(contests[0]["submissions"], 4)
            result = {"student_ids": [4, 9], "problem": "B", "language": "cpp"}
            a, b = pair_sources(path, 123, "contest", result)
            self.assertEqual((a["submission_id"], b["submission_id"]), (2, 4))


if __name__ == "__main__":
    unittest.main()
