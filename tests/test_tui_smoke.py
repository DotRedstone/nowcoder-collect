"""Headless Screen-stack and review flow tests (no network or private data)."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_tui import ReviewApp, Page
from review_domain import PeopleStore

class ScreenTests(unittest.TestCase):
    def test_home_to_contest_to_people_to_code_and_back(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                db = Path(directory) / "fake.db"
                with patch("review_tui.local_contests", return_value=[{
                    "contest_id": 5, "name": "校内周赛",
                    "submissions": 1, "codes": 1}]), patch(
                    "review_tui.read_secret", return_value=""
                ), patch("review_tui.all_submissions", return_value=[]), patch(
                    "review_tui.people_for_contest", return_value=[
                    {"uid": 11, "name": "张三", "ac": 1, "total": 100, "staff": False}
                ]), patch("review_tui.person_submissions", return_value=[
                    {"submission_id": 101, "contest_id": 5, "student_id": 11,
                     "student": "张三", "problem_index": "A", "problem_title": "求和",
                     "phase": "contest", "submit_time": 1000, "score": 100,
                     "verdict": "答案正确", "language": "C++", "code": "int main(){}",
                     "loc": 1}
                ]), patch("review_tui.ReviewApp.db", return_value=db):
                    app = ReviewApp()
                    async with app.run_test(size=(105, 32)) as pilot:
                        await pilot.pause()
                        self.assertEqual(app.screen.kind, "home")
                        self.assertEqual(len(app.screen.rows), 5)
                        app.action_enter()
                        await pilot.pause()
                        self.assertEqual(app.screen.kind, "contests")
                        app.action_enter()
                        await pilot.pause()
                        self.assertEqual(app.screen.kind, "contest")
                        app.screen.query_one("#list").move_cursor(row=1)
                        await pilot.pause()
                        app.action_enter()
                        await pilot.pause()
                        self.assertEqual(app.screen.kind, "people")
                        app.action_enter()
                        await pilot.pause()
                        self.assertEqual(app.screen.kind, "person")
                        app.action_enter()
                        await pilot.pause()
                        self.assertEqual(app.screen.kind, "code")
                        self.assertIn("int main()", app.screen.query_one("#code").text)
                        app.action_back()
                        await pilot.pause()
                        self.assertEqual(app.screen.kind, "person")
        asyncio.run(run())

    def test_settings_and_user_mark_roundtrip(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                with patch("review_tui.PeopleStore", return_value=PeopleStore(Path(directory) / "people.json")), patch(
                    "review_tui.ReviewStore", return_value=__import__("tui_backend").ReviewStore(Path(directory) / "reviews.json")
                ):
                    app = ReviewApp()
                    async with app.run_test(size=(100, 30)) as pilot:
                        await pilot.pause()
                        app.push_page("mark", "人员标记", cid=5, targets=[11])
                        await pilot.pause()
                        self.assertEqual(app.screen.kind, "mark")
                        app.action_enter()
                        await pilot.pause()
                        page = app.screen
                        self.assertTrue(page.query_one("#input-row").display)
                        app.submit_input(page, "value", "已手动复核")
                        await pilot.pause()
                        self.assertEqual(app.people.get(5, 11)["mark"], "normal")
        asyncio.run(run())


    def test_settings_categories_and_ranking_screen_mount(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                with patch("review_tui.read_secret", return_value=""), patch(
                    "review_tui.local_contests", return_value=[]
                ), patch("review_tui.local_rank", return_value=[
                    {"rank": 1, "uid": 1, "name": "甲", "ac": 2, "total": 150},
                    {"rank": 2, "uid": 2, "name": "乙", "ac": 1, "total": 100},
                ]), patch("review_tui.PeopleStore", return_value=PeopleStore(Path(directory) / "people.json")):
                    app = ReviewApp()
                    async with app.run_test(size=(110, 34)) as pilot:
                        await pilot.pause()
                        app.push_page("settings", "设置")
                        await pilot.pause()
                        self.assertEqual(app.screen.kind, "settings")
                        self.assertGreater(app.screen.query_one("#list").row_count, 5)
                        app.push_page("settings_section", "外观", section="外观")
                        await pilot.pause()
                        self.assertEqual(app.screen.kind, "settings_section")
                        app.action_option_next()
                        await pilot.pause()
                        app.action_back()
                        await pilot.pause()
                        self.assertEqual(app.screen.kind, "confirm")
                        app.action_enter()
                        await pilot.pause()
                        self.assertEqual(app.screen.kind, "settings")
                        app.push_page("ranking", "排行榜", cid=5)
                        await pilot.pause()
                        self.assertEqual(app.screen.kind, "ranking")
                        self.assertEqual(app.screen.query_one("#list").row_count, 7)
                        self.assertIn("本地推算", app.screen.query_one("#frame").border_title)
        asyncio.run(run())

    def test_evidence_source_and_tab(self):
        async def run():
            case = {
                "pair": ["甲", "乙"], "student_ids": [1, 2], "problem": "B",
                "language": "cpp", "verdict": "存疑",
                "scores": {"L1": 0.8, "L2": 0.85, "L3": 0.91, "md5_ws_equal": False},
                "rare": {"score": 0.42, "shared": 2, "samples": ["for int"]},
                "timeline": {"first_submit_delta": 5, "later_failures_before_ac": 1, "error_types": ["WA"]},
                "template_ratio": 0.1, "crowd_count": 1,
            }
            with patch("review_tui.read_secret", return_value=""), patch(
                "review_tui.local_contests", return_value=[]
            ), patch("review_tui.pair_sources", return_value=(
                {"student": "甲", "submission_id": 101, "code": "int a=1;\n"},
                {"student": "乙", "submission_id": 102, "code": "int a=2;\n"},
            )):
                app = ReviewApp()
                async with app.run_test(size=(125, 38)) as pilot:
                    await pilot.pause()
                    app.push_page("evidence", "证据", cid=5, case=case)
                    await pilot.pause()
                    self.assertTrue(app.screen.query_one("#source-pair").display)
                    self.assertIn("int a=1", app.screen.query_one("#source-a").text)
                    app.action_tab_key()
                    await pilot.pause()
                    self.assertEqual(app.screen.query_one("#source-pair").active, "tab-diff")
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
