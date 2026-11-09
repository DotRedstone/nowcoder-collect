import unittest

from detectors.normalize import l1, l2, l3_tokens, l3_str, strip_comments


class TestNormalize(unittest.TestCase):
    def test_l2_strips_whitespace(self):
        self.assertEqual(l2("a  b\n\tc"), "abc")
        self.assertEqual(l2("int main(){ }"), "intmain(){}")

    def test_l1_is_raw(self):
        self.assertEqual(l1("a  b"), "a  b")

    def test_l3_keeps_keyword_renames_ident(self):
        # int 是关键字保留；x/y/z 是用户标识符 → v0/v1/v2
        toks = l3_tokens("int x = y; x = z;", "cpp")
        self.assertEqual(toks[0], "int")   # 关键字
        self.assertEqual(toks[1], "v0")    # x
        self.assertEqual(toks[3], "v1")    # y
        self.assertEqual(toks[5], "v0")    # x 再次出现
        self.assertNotIn("x", toks)
        self.assertNotIn("y", toks)

    def test_l3_alpha_renaming_equivalence(self):
        # 改名掩码：list_ 和 x 都被重命名，结构应相同
        a = l3_tokens("list_ = sorted_list[::-1]", "python")
        b = l3_tokens("x = y[::-1]", "python")
        self.assertEqual(a, b)

    def test_number_string_normalized(self):
        toks = l3_tokens("for i in range(200000): s = 'abc'", "python")
        self.assertIn("NUM", toks)
        self.assertIn("STR", toks)

    def test_comment_stripped(self):
        self.assertNotIn("#", l3_str("# comment\nx = 1", "python"))
        self.assertNotIn("//", l3_str("// comment\nint x;", "cpp"))
        self.assertNotIn("/*", l3_str("/* c */ int x;", "cpp"))

    def test_empty_code(self):
        self.assertEqual(l2(""), "")
        self.assertEqual(l3_tokens("", "cpp"), [])


if __name__ == "__main__":
    unittest.main()
