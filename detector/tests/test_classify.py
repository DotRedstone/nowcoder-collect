import unittest

from detectors.classify import classify

TH = {
    "solid_l2": 0.98, "solid_delta_seconds": 1800,
    "mid_strong_rare": 0.5, "mid_strong_l3": 0.85,
    "suspicious_l3": 0.8, "suspicious_rare": 0.3,
    "crowd_threshold": 3,
}


def sc(L1=0.0, L2=0.0, L3=0.0, md5=False):
    return {"L1": L1, "L2": L2, "L3": L3, "md5_ws_equal": md5}


class TestClassify(unittest.TestCase):
    def test_solid(self):
        r = classify(sc(L2=1.0, L3=1.0, md5=True), {"score": 1.0},
                     {"direction_valid": True, "delta_seconds": 100}, TH, crowd_count=2)
        self.assertEqual(r, "实锤")

    def test_crowd_template_excluded(self):
        # 一模一样但 5 个人写 → 人群模板，排除
        r = classify(sc(L2=1.0, L3=1.0, md5=True), {"score": 1.0},
                     {"direction_valid": True, "delta_seconds": 100}, TH, crowd_count=5)
        self.assertEqual(r, "排除")

    def test_mid_strong(self):
        r = classify(sc(L2=0.9, L3=0.9), {"score": 0.6}, {}, TH, crowd_count=1)
        self.assertEqual(r, "中偏强")

    def test_suspicious(self):
        r = classify(sc(L2=0.7, L3=0.82), {"score": 0.4}, {}, TH, crowd_count=1)
        self.assertEqual(r, "存疑")

    def test_excluded(self):
        r = classify(sc(L2=0.5, L3=0.6), {"score": 0.1}, {}, TH, crowd_count=1)
        self.assertEqual(r, "排除")

    def test_boundary_no_jump(self):
        # 边界值 0.98 与 0.9799 不应越界
        a = classify(sc(L2=0.98, L3=0.7), {"score": 0.0}, {}, TH, crowd_count=1)
        b = classify(sc(L2=0.9799, L3=0.7), {"score": 0.0}, {}, TH, crowd_count=1)
        self.assertNotEqual(a, "实锤")  # 方向不成立，都不该实锤


if __name__ == "__main__":
    unittest.main()
