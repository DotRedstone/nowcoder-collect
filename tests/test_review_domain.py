"""Offline domain regression tests for the second-stage TUI."""
import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from review_domain import (
    PeopleStore, contest_matches, detector_db, export_ranking, final_rank,
    hidden_uids, load_filters, load_staff, local_rank, people_for_contest,
    person_submissions, problem_submissions, problems_for_contest,
    representative_for, save_filters, save_staff, validate_filters,
)

class DomainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "contest.db"
        with sqlite3.connect(self.db) as db:
            db.executescript("""
            CREATE TABLE submissions (
              submission_id INTEGER PRIMARY KEY,contest_id INTEGER,
              student TEXT,student_id INTEGER,problem_index TEXT,
              problem_title TEXT,phase TEXT,score REAL,verdict TEXT,
              code TEXT,language TEXT,submit_time INTEGER,loc INTEGER
            );
            INSERT INTO submissions VALUES
              (101,5,'张三',11,'A','求和','contest',100,'答案正确','int main(){}','C++',1000,1),
              (102,5,'张三',11,'A','求和','contest',0,'编译错误','bad','C++',500,1),
              (103,5,'李四',22,'A','求和','contest',100,'答案正确','ok','C++',2000,1),
              (104,5,'管理员',99,'A','求和','contest',100,'答案正确','staff','C++',3000,1),
              (105,5,'张三',11,'B','数字','contest',50,'部分正确','long code','C++',4000,1),
              (106,5,'李四',22,'B','数字','contest',20,'部分正确','partial','C++',5000,1);
            """)

    def test_filter_precedence_and_roundtrip(self):
        config = {
            "match_field": "比赛名", "case_sensitive": False,
            "whitelist": [{"pattern": "周赛", "enabled": True}],
            "blacklist": [{"pattern": "外校", "enabled": True}],
        }
        path = self.root / "filters.toml"
        save_filters(config, path)
        self.assertEqual(load_filters(path), config)
        self.assertTrue(contest_matches({"name": "校内周赛", "contest_id": 10}, config))
        self.assertFalse(contest_matches({"name": "外校周赛", "contest_id": 11}, config))
        config["whitelist"] = []
        self.assertTrue(contest_matches({"name": "校内模拟赛"}, config))
        with self.assertRaises(Exception):
            save_filters({**config, "blacklist": [{"pattern": "[", "enabled": True}]}, path)
        self.assertEqual(load_filters(path)["blacklist"][0]["pattern"], "外校")

    def test_staff_is_absent_from_filtered_db_but_original_untouched(self):
        path = self.root / "staff.toml"
        save_staff([{"uid": 99, "name": "管理员", "enabled": True},
                    {"uid": 77, "name": "观察员", "enabled": False}], path)
        exclude = hidden_uids(load_staff(path))
        self.assertEqual(exclude, {99})
        with detector_db(self.db, exclude) as copy:
            self.assertNotEqual(copy, self.db)
            with sqlite3.connect(copy) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM submissions WHERE student_id=99").fetchone()[0], 0)
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM submissions WHERE student_id=99").fetchone()[0], 1)

    def test_all_code_histories_and_people(self):
        person = person_submissions(5, 11, self.db)
        self.assertEqual([r["submission_id"] for r in person if r["problem_index"] == "A"], [102, 101])
        self.assertEqual(len(problem_submissions(5, "A", self.db)), 4)
        self.assertEqual(representative_for(person)["submission_id"], 101)
        people = people_for_contest(5, self.db, {99})
        self.assertEqual(len(people), 3)
        self.assertTrue(people[-1]["staff"])
        problems = problems_for_contest(5, self.db)
        self.assertEqual(problems[0]["ac"], 3)

    def test_marks_survive_restart_and_rank_policy(self):
        path = self.root / "people.json"
        store = PeopleStore(path)
        store.mark(5, 22, "cheater", "核查过")
        self.assertEqual(PeopleStore(path).get(5, 22)["note"], "核查过")
        rank = local_rank(5, self.db)
        self.assertEqual(rank[0]["name"], "张三")
        result = final_rank(rank, store, 5, {99}, cheater="剔除")
        self.assertEqual([r["uid"] for r in result], [11])
        self.assertEqual(result[0]["rank"], 1)
        marked = final_rank(rank, store, 5, {99}, cheater="标记")
        self.assertEqual(marked[1]["rank"], "—")
        self.assertEqual(marked[1]["mark"], "作弊者")
        self.assertEqual(marked[0]["rank"], 1)

    def test_export_all_formats_escape_html_and_csv(self):
        rows = [{"rank": 1, "name": "<张|三>", "uid": 11, "ac": 1, "total": 150, "mark": "正常"}]
        for fmt in ("markdown", "csv", "html"):
            path = export_ranking(5, rows, fmt, self.root)
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        html_text = (self.root / "5" / "ranking.html").read_text()
        self.assertIn("&lt;张|三&gt;", html_text)
        md_text = (self.root / "5" / "ranking.md").read_text()
        self.assertIn("<张\\|三>", md_text)
        with (self.root / "5" / "ranking.csv").open() as f:
            data = list(csv.reader(f))
        self.assertEqual(data[1][1], "<张|三>")


if __name__ == "__main__":
    unittest.main()
