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
                with patch("review_tui.ReviewApp._load_user_data") as load:
                    def init():
                        from review_domain import DEFAULT_SETTINGS, DEFAULT_FILTERS
                        from review_settings import load as thresholds_load
                        import copy
                        app.settings = copy.deepcopy(DEFAULT_SETTINGS)
                        app.filters = copy.deepcopy(DEFAULT_FILTERS)
                        app.staff = []
                        app.people = PeopleStore(Path(directory) / "people.json")
                        app.reviews = __import__("tui_backend").ReviewStore(Path(directory) / "reviews.json")
                        app.thresholds = thresholds_load()
                        app.settings_draft = copy.deepcopy(app.settings)
                        app.filters_draft = copy.deepcopy(app.filters)
                        app.staff_draft = []
                        app.thresholds_draft = copy.deepcopy(app.thresholds)
                        app.nerd_font = False
                    # Set explicit mock on app instance instead of replacing constructor.
                    load.side_effect = init
                    app = ReviewApp()
                    load.side_effect = None
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


if __name__ == "__main__":
    unittest.main()
