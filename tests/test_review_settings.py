"""User threshold config and settings regression tests."""
import tempfile
import unittest
from pathlib import Path
from review_domain import load_settings, save_settings, DEFAULT_SETTINGS, SCHEMA, validate_setting
from review_settings import check, load as load_thresholds, save as save_thresholds, defaults, flatten

class SettingsTests(unittest.TestCase):
    def test_settings_are_typed_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = dict(DEFAULT_SETTINGS)
            settings["threads"] = 7
            settings["key_hints"] = False
            path = Path(tmp) / "settings.toml"
            save_settings(settings, path)
            self.assertEqual(load_settings(path)["threads"], 7)
            self.assertFalse(load_settings(path)["key_hints"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                validate_setting("threads", 33)
            with self.assertRaises(ValueError):
                validate_setting("collect_after", "no")

    def test_threshold_config_independent_of_bundled_default(self):
        before = flatten(defaults())
        with tempfile.TemporaryDirectory() as tmp:
            custom = dict(before)
            custom["thresholds.solid_l2"] = 0.9
            path = Path(tmp) / "detector.toml"
            save_thresholds(custom, path)
            self.assertEqual(load_thresholds(path)["thresholds.solid_l2"], 0.9)
            self.assertEqual(flatten(defaults()), before)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                check("ngram.k", -1)

if __name__ == "__main__":
    unittest.main()
