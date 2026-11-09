"""Exercise mounting the Textual dashboard without credentials or a network."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_tui import ReviewApp


class SmokeTests(unittest.TestCase):
    def test_app_mounts_offline(self):
        async def run():
            with patch("review_tui.read_secret", return_value=""), patch("review_tui.local_contests", return_value=[]):
                app = ReviewApp()
                async with app.run_test(size=(140, 42)) as pilot:
                    await pilot.pause()
                    self.assertIsNone(app.contest)
                    self.assertEqual(app.query_one("#contests").row_count, 0)
                    self.assertIsNotNone(app.query_one("#evidence"))
                    app.action_help()
                    await pilot.pause()
                    self.assertIn("Tab", str(app.query_one("#status").render()))
        asyncio.run(run())

    def test_review_navigation_with_mock_data(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                with patch("review_tui.read_secret", return_value=""), patch(
                    "review_tui.local_contests", return_value=[
                        {"contest_id": 123, "name": "训练赛", "submissions": 2, "codes": 2}
                    ]
                ), patch("review_tui.ReviewStore", return_value=__import__("tui_backend").ReviewStore(Path(tmp) / "reviews.json")):
                    app = ReviewApp()
                    async with app.run_test(size=(140, 42)) as pilot:
                        await pilot.pause()
                        self.assertEqual(app.contest, 123)
                        self.assertEqual(app.query_one("#contests").row_count, 1)
                        app.action_toggle_login()
                        await pilot.pause()
                        self.assertTrue(app.login_visible)
                        app.action_back()
                        self.assertFalse(app.login_visible)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
