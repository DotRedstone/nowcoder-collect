"""Headless Textual tests for rice layout, offline use and keyboard navigation."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_tui import ReviewApp
from tui_backend import ReviewStore


class SmokeTests(unittest.TestCase):
    def test_app_mounts_offline_with_help_overlay_and_statusline(self):
        async def run():
            with patch("review_tui.read_secret", return_value=""), patch(
                "review_tui.local_contests", return_value=[]
            ):
                app = ReviewApp()
                async with app.run_test(size=(140, 42)) as pilot:
                    await pilot.pause()
                    self.assertIsNone(app.contest)
                    self.assertEqual(app.query_one("#contests").row_count, 0)
                    self.assertTrue(app.query_one("#empty").display)
                    self.assertIn("contests", app.query_one("#left").border_title)
                    self.assertIn("NORMAL", str(app.query_one("#status").render()))
                    self.assertTrue(app.query_one("#status").styles.height.value == 1)
                    app.action_help()
                    await pilot.pause()
                    self.assertEqual(app.screen.__class__.__name__, "HelpScreen")
                    self.assertIsNotNone(app.screen.query_one("#help-card"))
                    app.screen.dismiss()
                    await pilot.pause()
        asyncio.run(run())

    def test_contest_workflow_focus_filter_and_theme(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                with patch("review_tui.read_secret", return_value=""), patch(
                    "review_tui.local_contests", return_value=[
                        {"contest_id": 123, "name": "训练赛", "submissions": 2, "codes": 2}
                    ]
                ), patch("review_tui.ReviewStore", return_value=ReviewStore(Path(tmp) / "reviews.json")):
                    app = ReviewApp()
                    async with app.run_test(size=(140, 42)) as pilot:
                        await pilot.pause()
                        self.assertEqual(app.contest, 123)
                        self.assertEqual(app.query_one("#contests").row_count, 1)
                        self.assertIn("1 场", app.query_one("#left").border_subtitle)
                        app.focus_pane(1)
                        self.assertEqual(app.active_pane, 1)
                        app.action_filter()
                        self.assertEqual(app.filter_idx, 1)
                        self.assertIn("high", app.query_one("#middle").border_title)
                        app.execute_command("contest 456")
                        self.assertEqual(app.contest, 456)
                        app.execute_command("theme ansi-dark")
                        self.assertEqual(app.theme, "ansi-dark")
                        app.action_toggle_login()
                        self.assertTrue(app.login_visible)
                        app.action_back()
                        self.assertFalse(app.login_visible)
        asyncio.run(run())

    def test_risk_cells_and_narrow_panes(self):
        async def run():
            with patch("review_tui.read_secret", return_value=""), patch(
                "review_tui.local_contests", return_value=[
                    {"contest_id": 123, "name": "测试", "submissions": 2, "codes": 2}
                ]
            ):
                app = ReviewApp()
                async with app.run_test(size=(72, 26)) as pilot:
                    await pilot.pause()
                    app.focus_pane(1)
                    self.assertTrue(app.query_one("#middle").display)
                    self.assertFalse(app.query_one("#left").display)
                    result = {
                        "pair": ["张三", "李四"], "student_ids": [1, 2],
                        "problem": "B", "language": "cpp", "verdict": "中偏强",
                        "scores": {"L1": 0.7, "L2": 0.8, "L3": 0.95},
                        "rare": {"shared": 4, "score": 0.75},
                        "timeline": {},
                    }
                    app.cases = [result]
                    app.filter_idx = 0
                    app.filter_cases()
                    await pilot.pause()
                    self.assertEqual(app.query_one("#findings").row_count, 1)
                    self.assertIn("MED", app._risk_cell(result, True).plain)
                    self.assertIn("▎", app._risk_cell(result, True).plain)
                    self.assertIn("1/1", app.query_one("#middle").border_subtitle)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
