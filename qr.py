# -*- coding: utf-8 -*-
"""极简 QR 码编码器（纯标准库，无第三方依赖）。

支持：byte 模式、纠错级别 L、版本 1-6（最多 136 字节数据，够放登录 URL）。
只做「编码成矩阵」，渲染交给调用方。
"""
import base64

# ---------------------------------------------------------------- GF(256) / Reed-Solomon
EXP = [0] * 512
LOG = [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def _gmul(a, b):
    if a == 0 or b == 0:
        return 0
    return EXP[LOG[a] + LOG[b]]


def _rs_gen(nsym):
    g = [1]
    for i in range(nsym):
        ng = [0] * (len(g) + 1)
        for j in range(len(g)):
            ng[j] ^= _gmul(g[j], EXP[i])
            ng[j + 1] ^= g[j]
        g = ng
    return g


def _rs_encode(data, nsym):
    gen = _rs_gen(nsym)
    msg = data + [0] * nsym
    for i in range(len(data)):
        coef = msg[i]
        if coef:
            for j in range(len(gen)):
                msg[i + j] ^= _gmul(gen[j], coef)
    return data + msg[-nsym:]


# ---------------------------------------------------------------- 容量表（EC 级别 L，版本 1-6）
# (总码字数, 每块纠错码字数, 块数)
CAP = {
    1: (26, 7, 1),
    2: (44, 10, 1),
    3: (70, 15, 1),
    4: (100, 20, 1),
    5: (134, 26, 1),
    6: (172, 18, 2),
}
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}


def _choose_version(data_len):
    for v in sorted(CAP):
        total, ec, blocks = CAP[v]
        data_cw = total - ec * blocks
        if data_len + 2 <= data_cw:  # +2 覆盖 mode(4bit)+count(8bit)+终止符+填充的码字开销
            return v
    raise ValueError("数据过长（>136 字节）")


def _bits_to_cw(bits):
    while len(bits) % 8:
        bits.append(0)
    cw = []
    for i in range(0, len(bits), 8):
        v = 0
        for b in bits[i:i + 8]:
            v = (v << 1) | b
        cw.append(v)
    return cw


def _encode_data(data: bytes, version: int):
    n = len(data)
    bits = [0, 1, 0, 0]  # 0100 = byte 模式
    for i in range(7, -1, -1):  # 字符数（版本 1-9 用 8 位）
        bits.append((n >> i) & 1)
    for b in data:
        for i in range(7, -1, -1):
            bits.append((b >> i) & 1)

    total, ec, blocks = CAP[version]
    cap_bits = (total - ec * blocks) * 8
    # 终止符（最多 4 个 0）
    bits += [0] * min(4, cap_bits - len(bits))
    # 补齐到字节
    while len(bits) % 8:
        bits.append(0)
    # 填充字节 0xEC 0x11
    pad = (0xEC, 0x11)
    pi = 0
    while len(bits) < cap_bits:
        for i in range(7, -1, -1):
            bits.append((pad[pi] >> i) & 1)
        pi ^= 1
    return _bits_to_cw(bits)


def _build_codewords(data: bytes, version: int):
    total, ec_per_block, num_blocks = CAP[version]
    data_cw_total = total - ec_per_block * num_blocks
    cw = _encode_data(data, version)
    # 分块（v1-6 L 级余数都是 0，可均分）
    base = data_cw_total // num_blocks
    blocks = [cw[i * base:(i + 1) * base] for i in range(num_blocks)]
    ec_blocks = [_rs_encode(b, ec_per_block) for b in blocks]
    # 交错
    out = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec_per_block):
        for b, eb in zip(blocks, ec_blocks):
            out.append(eb[len(b) + i])
    return out


# ---------------------------------------------------------------- 掩码与格式信息
def _mask_fn(mask, r, c):
    if mask == 0:
        return (r + c) % 2 == 0
    if mask == 1:
        return r % 2 == 0
    if mask == 2:
        return c % 3 == 0
    if mask == 3:
        return (r + c) % 3 == 0
    if mask == 4:
        return (r // 2 + c // 3) % 2 == 0
    if mask == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if mask == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    if mask == 7:
        return ((r + c) % 2 + (r * c) % 3) % 2 == 0
    return False


def _format_info(ecl_bits, mask):
    """15 位格式信息（含 BCH + 掩码 0x5412）。EC L = 01。"""
    data = (ecl_bits << 3) | mask
    rem = data << 10
    gen = 0x537
    for i in range(14, 9, -1):
        if rem & (1 << i):
            rem ^= gen << (i - 10)
    return ((data << 10) | rem) ^ 0x5412


# ---------------------------------------------------------------- 矩阵构建
def make_matrix(data: str, mask: int = 0):
    """返回 (size, matrix)。matrix[r][c] = True(黑) / False(白)。"""
    data = data.encode("utf-8")
    version = _choose_version(len(data))
    codewords = _build_codewords(data, version)
    size = 17 + 4 * version
    m = [[None] * size for _ in range(size)]

    def set_finder(r, c):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = r + dr, c + dc
                if 0 <= rr < size and 0 <= cc < size:
                    m[rr][cc] = (0 <= dr <= 6 and 0 <= dc <= 6 and
                                 (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4)))

    set_finder(0, 0)
    set_finder(0, size - 7)
    set_finder(size - 7, 0)

    # 定时图案
    for i in range(8, size - 8):
        if m[6][i] is None:
            m[6][i] = (i % 2 == 0)
        if m[i][6] is None:
            m[i][6] = (i % 2 == 0)

    # 对齐图案（v2+）
    for r in ALIGN[version]:
        for c in ALIGN[version]:
            if m[r][c] is not None:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[r + dr][c + dc] = (max(abs(dr), abs(dc)) != 1)

    # 预留格式信息位置
    for i in range(9):
        if m[i][8] is None:
            m[i][8] = False
        if m[8][i] is None:
            m[8][i] = False
    for i in range(8):
        if m[8][size - 1 - i] is None:
            m[8][size - 1 - i] = False
        if m[size - 1 - i][8] is None:
            m[size - 1 - i][8] = False
    # 暗模块
    m[4 * version + 9][8] = True

    # 数据位放置（zigzag，应用掩码）
    bits = [((cw >> b) & 1) for cw in codewords for b in range(7, -1, -1)]
    idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rng = range(size - 1, -1, -1) if upward else range(size)
        for r in rng:
            for c in (col, col - 1):
                if m[r][c] is None:
                    bit = False
                    if idx < len(bits):
                        bit = bool(bits[idx])
                        idx += 1
                    if mask:
                        bit = bit ^ _mask_fn(mask, r, c)
                    m[r][c] = bit
        upward = not upward
        col -= 2

    # 格式信息（EC L = 01），两份，坐标按 QR 规范
    fi = _format_info(0b01, mask)
    copy1 = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
             (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    copy2 = [(size - 1, 8), (size - 2, 8), (size - 3, 8), (size - 4, 8),
             (size - 5, 8), (size - 6, 8), (size - 7, 8), (8, size - 8),
             (8, size - 7), (8, size - 6), (8, size - 5), (8, size - 4),
             (8, size - 3), (8, size - 2), (8, size - 1)]
    for i, (r, c) in enumerate(copy1 + copy2):
        m[r][c] = bool((fi >> i) & 1)
    return size, m


def matrix_to_str(size, m):
    """把矩阵转成 ██/空格 文本，方便终端渲染（2 字符宽 1 行 = 接近正方形）。"""
    lines = []
    for r in range(size):
        line = "".join("██" if m[r][c] else "  " for c in range(size))
        lines.append(line)
    return "\n".join(lines)


def extract_wechat_url(ticket: str) -> str:
    """从微信 ticket 里解出登录 URL。ticket 是 base64，URL 内嵌其中。"""
    raw = base64.b64decode(ticket + "=" * (-len(ticket) % 4), altchars=b"-_")
    text = raw.decode("latin-1")
    i = text.find("http")
    if i >= 0:
        return text[i:].split("\x00")[0].strip()
    # 兜底：正则找 http 串
    import re
    m = re.search(r"https?://[A-Za-z0-9_./?=&%\-]+", text)
    return m.group(0) if m else ""
