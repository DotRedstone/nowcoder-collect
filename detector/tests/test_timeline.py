import unittest

from detectors.preprocess import Submission
from detectors.timeline import analyze_timeline, classify_error, is_ac


def mk(sid, verdict, t, pi="A"):
    return Submission(submission_id=sid, contest_id=1, student=f"s{sid}", student_id=sid,
                      problem_index=pi, problem_id=1, problem_title="", language="C++",
                      verdict=verdict, score=0, submit_time=t, phase="contest", code="x")


class TestTimeline(unittest.TestCase):
    def test_is_ac(self):
        self.assertTrue(is_ac("答案正确"))
        self.assertTrue(is_ac("Accepted"))
        self.assertFalse(is_ac("答案错误"))

    def test_failures_before_ac(self):
        a = [mk(1, "答案正确", 1000)]
        b = [mk(2, "答案错误", 1100), mk(3, "答案错误", 1200),
             mk(4, "编译错误", 1300), mk(5, "答案正确", 1400)]
        r = analyze_timeline(a, b)
        self.assertEqual(r["who_first"], "A")       # A 先 AC
        self.assertEqual(r["later_failures_before_ac"], 3)
        self.assertTrue(r["direction_valid"])

    def test_no_direction(self):
        # B 先 AC(300)，但 A 在 200 就提交过（早于 B 的 AC），说明 A 独立在写
        a = [mk(1, "答案错误", 200), mk(2, "答案正确", 500)]
        b = [mk(3, "答案正确", 300)]
        r = analyze_timeline(a, b)
        self.assertEqual(r["who_first"], "B")       # B 先 AC
        self.assertFalse(r["direction_valid"])      # A 早于 B 的 AC 就开始

    def test_classify_error(self):
        self.assertEqual(classify_error("编译错误"), "编译错误")
        self.assertEqual(classify_error("运行超时"), "超时")
        self.assertEqual(classify_error("答案错误"), "答案错误")


if __name__ == "__main__":
    unittest.main()
