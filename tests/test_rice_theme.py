"""Tests for optional wallpaper themes (no user's files or actual login required)."""
import json
import tempfile
import unittest
from pathlib import Path

from rice_theme import load_wallpaper_theme, theme_from_dict


class WallpaperThemeTests(unittest.TestCase):
    def test_pywal(self):
        raw = {
            "special": {"background": "#112233", "foreground": "#eeeeee"},
            "colors": {"color0": "#101010", "color1": "#aa2222", "color2": "#22aa22",
                       "color3": "#cccc22", "color4": "#2244ff", "color5": "#aa22aa",
                       "color6": "#22aaaa", "color8": "#333333"},
        }
        theme = theme_from_dict(raw)
        self.assertEqual(theme.background, "#112233")
        self.assertEqual(theme.error, "#aa2222")
        self.assertEqual(theme.accent, "#aa22aa")
        self.assertEqual(theme.success, "#22aa22")

    def test_matugen_dark(self):
        raw = {"colors": {"dark": {
            "background": "#222222", "on_background": "#eeeeee",
            "surface": "#292929", "primary": "#abcdef", "secondary": "#f1f1f1",
            "tertiary": "#ddaaee", "error": "#ff9999"}}}
        theme = theme_from_dict(raw)
        self.assertEqual(theme.primary, "#abcdef")
        self.assertEqual(theme.accent, "#ddaaee")
        self.assertEqual(theme.foreground, "#eeeeee")

    def test_matugen_new_nested_color_format(self):
        raw = {"colors": {
            "background": {"dark": {"color": "#202020"}},
            "on_background": {"default": {"color": "#f0f0f0"}},
            "primary": {"default": {"color": "#aabbcc"}},
        }}
        theme = theme_from_dict(raw)
        self.assertEqual(theme.primary, "#aabbcc")

    def test_reject_invalid_colors_and_bad_json(self):
        raw = {"colors": {"dark": {
            "background": "red; width: 900", "on_background": "#ffffff",
            "primary": "#ff99aa"}}}
        self.assertIsNone(theme_from_dict(raw))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "colors.json"
            path.write_text("{broken")
            theme, source = load_wallpaper_theme([path])
            self.assertIsNone(theme)
            self.assertEqual(source, "")

    def test_json_file_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "colors.json"
            path.write_text(json.dumps({"colors": {"dark": {
                "background": "#101010", "on_background": "#fefefe",
                "primary": "#0099aa"}}}))
            theme, source = load_wallpaper_theme([path])
            self.assertEqual(theme.name, "wallpaper")
            self.assertEqual(source, str(path))


if __name__ == "__main__":
    unittest.main()
