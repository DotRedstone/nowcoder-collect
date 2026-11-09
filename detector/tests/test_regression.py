"""回归测试：对本地数据库跑真实比赛，断言关键不变量（无 DB 时自动跳过）。

不在测试里写死队员姓名（隐私），只断言泛化结论。
"""
import json
import os
import unittest

from main import detect, load_config

DB = os.path.expanduser("~/.dsh/nowcoder/nowcoder.db")
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.toml")


@unittest.skipUnless(os.path.exists(DB), "本地数据库不存在，跳过回归")
class TestRegression(unittest.TestCase):
    def test_143786_deterministic_and_has_solid(self):
        cfg = load_config(CONFIG)
        r1 = detect(DB, 143786, "contest", cfg, False)
        r2 = detect(DB, 143786, "contest", cfg, False)

        # 可复现：两次输出逐字节一致
        self.assertEqual(
            json.dumps(r1, sort_keys=True, ensure_ascii=False),
            json.dumps(r2, sort_keys=True, ensure_ascii=False),
        )

        # 至少一条"实锤"
        self.assertTrue(any(x["verdict"] == "实锤" for x in r1["results"]))

        # 实锤对必须 md5_ws_equal（去空白后逐字节一致）
        for x in r1["results"]:
            if x["verdict"] == "实锤":
                self.assertTrue(x["scores"]["md5_ws_equal"])

        # 报告的非"排除"对都应有完整字段
        for x in r1["results"]:
            for k in ("pair", "problem", "verdict", "scores", "rare", "timeline", "crowd_count"):
                self.assertIn(k, x)


if __name__ == "__main__":
    unittest.main()
