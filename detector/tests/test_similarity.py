import unittest

from detectors.normalize import l3_tokens
from detectors.similarity import build_rare_sets, compute_scores, ngrams, rare_between, ratio


class TestSimilarity(unittest.TestCase):
    def test_identical(self):
        s = compute_scores("int main(){return 0;}", "int main(){return 0;}", "cpp")
        self.assertEqual(s["L1"], 1.0)
        self.assertEqual(s["L2"], 1.0)
        self.assertEqual(s["L3"], 1.0)
        self.assertTrue(s["md5_ws_equal"])

    def test_ratio_range(self):
        self.assertAlmostEqual(ratio("abc", "abc"), 1.0)
        self.assertAlmostEqual(ratio("abc", "xyz"), 0.0)
        self.assertAlmostEqual(ratio([1, 2, 3], [1, 2, 3]), 1.0)

    def test_ngrams(self):
        self.assertEqual(ngrams(["a", "b", "c", "d"], 2),
                         {("a", "b"), ("b", "c"), ("c", "d")})
        self.assertEqual(ngrams(["a", "b"], 3), set())

    def test_rare_between(self):
        tokens = {
            1: l3_tokens("int a = b + c;", "cpp"),
            2: l3_tokens("int a = b + c;", "cpp"),
            3: l3_tokens("int a = b - c;", "cpp"),
        }
        rare_sets, _ = build_rare_sets(tokens, 3, 3)
        r = rare_between(rare_sets[1], rare_sets[2])
        self.assertGreater(r["score"], 0.5)
        self.assertGreater(r["shared"], 0)


if __name__ == "__main__":
    unittest.main()
