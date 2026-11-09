"""T2 归一化层：L1 原文 / L2 去空白 / L3 词法归一化（alpha-renaming）。

核心是 L3：把用户标识符按首次出现顺序重命名为 v0/v1/v2...，
数字统一为 NUM，字符串统一为 STR，关键字/内置符号保留。
这样 `list_` 和 `sorted_list` 这种改名掩码会归一化，只剩结构差异。
"""
import re
from typing import List, Tuple

# ---------------------------------------------------------------- 关键字 / 内置符号表
KEYWORDS = {
    "cpp": set("""alignas alignof and and_eq asm auto bitand bitor bool break case catch
        char char8_t char16_t char32_t class compl concept const consteval constexpr
        constinit const_cast continue co_await co_return co_yield decltype default delete
        do double dynamic_cast else enum explicit export extern false float for friend
        goto if inline int long mutable namespace new noexcept not not_eq nullptr operator
        or or_eq private protected public register reinterpret_cast requires return short
        signed sizeof static static_assert static_cast struct switch template this thread_local
        throw true try typedef typeid typename union unsigned using virtual void volatile
        wchar_t while xor xor_eq""".split()),
    "python": set("""False None True and as assert async await break class continue def del
        elif else except finally for from global if import in is lambda nonlocal not or
        pass raise return try while with yield""".split()),
    "java": set("""abstract assert boolean break byte case catch char class const continue
        default do double else enum extends final finally float for goto if implements import
        instanceof int interface long native new package private protected public return short
        static strictfp super switch synchronized this throw throws transient try void volatile
        while true false null""".split()),
}

BUILTINS = {
    "cpp": set("""cout cin cerr endl printf scanf puts gets strlen strcmp strcpy strcat memset
        memcpy sort stable_sort reverse unique lower_bound upper_bound binary_search min max abs
        swap begin end size empty push_back pop_back front back clear insert erase find substr
        string vector map set pair make_pair queue stack priority_queue greater less gcd sqrt pow
        ceil floor fabs max_element min_element accumulate iota next_permutation tie get getline
        to_string stoi stoll int long double char bool auto sizeof typedef nullptr stringstream""".split()),
    "python": set("""print len range sorted reversed list dict set tuple str int float bool input
        open read write split join strip append extend pop remove insert keys values items get
        enumerate zip map filter sum min max abs round any all isinstance type self len range
        defaultdict deque Counter namedtuple""".split()),
    "java": set("""System out print println main length size add get put remove contains equals
        toString parseInt""".split()),
}

# ---------------------------------------------------------------- 词法
_TOKEN_RE = re.compile(
    r"""
    (?P<string>"{3}[\s\S]*?"{3}|'{3}[\s\S]*?'{3}|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
  | (?P<number>\b0[xX][0-9a-fA-F]+\b|\b\d+(?:\.\d*)?(?:[eE][+-]?\d+)?\b)
  | (?P<ident>[^\W\d]\w*)
  | (?P<op><<|>>|<=|>=|==|!=|\+\+|--|&&|\|\||->|::|\+=|-=|\*=|/=|%=|\*\*|//|[+\-*/%<>=!&|^~?:.,;()\[\]{}@])
  | (?P<ws>\s+)
  | (?P<other>.)
    """,
    re.VERBOSE | re.DOTALL,
)


def _strip_comments(code: str, line: Tuple[str, ...], block: Tuple[Tuple[str, str], ...]) -> str:
    """语言感知的注释剥离：先保护字符串，再删注释。"""
    out = []
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        # 字符串（含三引号）
        if c in '"\'':
            triple = code[i:i + 3]
            if triple in ('"""', "'''"):
                j = code.find(triple, i + 3)
                j = (n if j == -1 else j + 3)
                out.append(code[i:j])
                i = j
                continue
            q = c
            j = i + 1
            while j < n:
                if code[j] == '\\':
                    j += 2
                    continue
                if code[j] == q:
                    j += 1
                    break
                j += 1
            out.append(code[i:j])
            i = j
            continue
        # 块注释
        matched = False
        for s, e in block:
            if code[i:i + len(s)] == s:
                j = code.find(e, i + len(s))
                j = (n if j == -1 else j + len(e))
                out.append(' ')
                i = j
                matched = True
                break
        if matched:
            continue
        # 行注释
        matched = False
        for s in line:
            if code[i:i + len(s)] == s:
                j = code.find('\n', i)
                j = (n if j == -1 else j)
                out.append('\n')
                i = j
                matched = True
                break
        if matched:
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def strip_comments(code: str, lang: str) -> str:
    if lang == "python":
        return _strip_comments(code, line=("#",), block=())
    # C/C++：行注释 // 与预处理 #，块注释 /* */
    return _strip_comments(code, line=("//", "#"), block=(("/*", "*/"),))


def tokenize(code: str) -> List[Tuple[str, str]]:
    """词法切分，返回 (kind, text) 列表。kind ∈ string/number/ident/op/other。"""
    tokens = []
    for m in _TOKEN_RE.finditer(code):
        kind = m.lastgroup
        if kind in ("ws",):
            continue
        tokens.append((kind, m.group()))
    return tokens


# ---------------------------------------------------------------- 三口径
def l1(code: str) -> str:
    """L1 原文，一字不差。"""
    return code


def l2(code: str) -> str:
    """L2 去空白。"""
    return re.sub(r"\s+", "", code)


def l3_tokens(code: str, lang: str) -> List[str]:
    """L3 词法归一化：输出 token 序列。"""
    kw = KEYWORDS.get(lang, set())
    bi = BUILTINS.get(lang, set())
    code = strip_comments(code, lang)
    rename: dict = {}
    out: List[str] = []
    for kind, tok in tokenize(code):
        if kind == "string":
            out.append("STR")
        elif kind == "number":
            out.append("NUM")
        elif kind == "ident":
            if tok in kw:
                out.append(tok)
            elif tok in bi:
                out.append(tok)
            else:
                if tok not in rename:
                    rename[tok] = f"v{len(rename)}"
                out.append(rename[tok])
        elif kind == "op":
            out.append(tok)
        else:  # other：罕见字符，保留一个占位，避免丢失结构
            out.append("?")
    return out


def l3_str(code: str, lang: str) -> str:
    """L3 序列的字符串形式（用于展示 / md5）。"""
    return " ".join(l3_tokens(code, lang))
