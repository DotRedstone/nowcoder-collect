#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""牛客网终端扫码登录（纯标准库，无第三方依赖）。

流程：
  1. GET /oauth2/login/wechat_qr_code        → ticket + imageUrl（微信二维码，120s 过期）
  2. 下载二维码 PNG → 解码 → 终端块字符渲染
  3. 轮询 /oauth2/login/wechat_mp_status?ticket=…   → 未扫描/已扫描/确认
  4. 确认后 Set-Cookie 携带 t 令牌 → 收集 → 写 ~/.dsh/nowcoder/cookie.txt

用法:
  python3 login.py            # 终端显示二维码，微信扫码登录
"""
import http.cookiejar
import json
import os
import struct
import sys
import time
import urllib.request
import zlib

import qr  # 极简 QR 编码器（同目录）

BASE = "https://www.nowcoder.com"
QR_URL = BASE + "/oauth2/login/wechat_qr_code"
STATUS_URL = BASE + "/oauth2/login/wechat_mp_status"
COOKIE_FILE = os.path.expanduser("~/.dsh/nowcoder/cookie.txt")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": BASE + "/login"}


# ---------------------------------------------------------------- PNG 解码
def decode_png(data: bytes):
    """解码 PNG，返回 (width, height, grayscale_pixels)。

    支持灰度/真彩/调色板/带 alpha，8-bit 位深、非隔行（二维码图片的常见形态）。
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 文件")
    pos, idat = 8, b""
    width = height = bit_depth = color_type = 0
    palette = b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"PLTE":
            palette = chunk
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length

    raw = zlib.decompress(idat)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    stride = width * channels
    bpp = channels

    # 反滤波（None/Sub/Up/Average/Paeth）
    out = bytearray()
    prev = bytearray(stride)
    p = 0
    for _ in range(height):
        ft = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ft == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:  # Average
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out += line
        prev = line

    # 转灰度
    pixels = []
    n = len(out)
    for i in range(0, n, channels):
        if color_type == 0:
            g = out[i]
        elif color_type == 2:
            r, gg, b = out[i], out[i + 1], out[i + 2]
            g = (r * 299 + gg * 587 + b * 114) // 1000
        elif color_type == 3:
            idx = out[i]
            r, gg, b = palette[idx * 3: idx * 3 + 3]
            g = (r * 299 + gg * 587 + b * 114) // 1000
        elif color_type == 6:
            r, gg, b = out[i], out[i + 1], out[i + 2]
            g = (r * 299 + gg * 587 + b * 114) // 1000
        else:  # 4: 灰度+alpha
            g = out[i]
        pixels.append(g)
    return width, height, pixels


# ---------------------------------------------------------------- 终端渲染
def render_qr(pixels, width, height, target_cells=40) -> str:
    """把灰度位图缩到终端，用 ██/空格 渲染成可扫的二维码。"""
    scale = max(1, width // target_cells)
    # 加一圈静区
    lines = ["  " * (target_cells + 2)]
    for y in range(0, height, scale):
        row = "  "  # 左侧静区
        for x in range(0, width, scale):
            total, cnt = 0, 0
            for yy in range(y, min(y + scale, height)):
                base = yy * width
                for xx in range(x, min(x + scale, width)):
                    total += pixels[base + xx]
                    cnt += 1
            row += "██" if (total / cnt) < 128 else "  "
        lines.append(row + "  ")
    lines.append("  " * (target_cells + 2))
    return "\n".join(lines)


def fetch_qr():
    req = urllib.request.Request(QR_URL, headers=HEADERS)
    d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8"))
    return d["data"]["ticket"], d["data"]["imageUrl"], d["data"]["expireSecond"]


def fetch_qr_pixels(image_url):
    req = urllib.request.Request(image_url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=20).read()


def jar_to_cookie_string(jar) -> str:
    parts = []
    for c in jar:
        if "nowcoder" in (c.domain or ""):
            parts.append(f"{c.name}={c.value}")
    return "; ".join(parts)


def save_cookie(s: str):
    os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(s)
    os.chmod(COOKIE_FILE, 0o600)


# ---------------------------------------------------------------- 登录流程
def login_flow(render_cb=None, status_cb=None) -> str:
    """扫码登录，成功返回 cookie 字符串；超时抛 TimeoutError。

    render_cb(text): 展示二维码（如打印 / 写进 curses）
    status_cb(msg):  更新状态提示
    """
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    ticket, image_url, expire = fetch_qr()
    if render_cb:
        try:
            url = qr.extract_wechat_url(ticket)
            size, m = qr.make_matrix(url, mask=0)
            render_cb("请用微信「扫一扫」扫描下方二维码登录：\n\n"
                      + qr.matrix_to_str(size, m) + "\n")
        except Exception as e:
            render_cb(f"二维码渲染失败，请用微信扫一扫打开此链接：\n{image_url}\n({e})")

    deadline = time.time() + expire
    while time.time() < deadline:
        if status_cb:
            status_cb("等待微信扫码确认…")
        time.sleep(3)
        req = urllib.request.Request(f"{STATUS_URL}?ticket={ticket}", headers=HEADERS)
        try:
            resp = opener.open(req, timeout=20)
            body = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if status_cb:
                status_cb(f"轮询失败：{e}")
            continue
        code = body.get("code", 1)
        msg = body.get("msg", "")
        if status_cb:
            status_cb(f"状态：{msg}")
        # 检查是否已拿到令牌 cookie
        cookie = jar_to_cookie_string(jar)
        if code == 0 or "成功" in msg or "t=" in cookie:
            # 再等一拍确保 t 令牌落定
            time.sleep(1)
            req = urllib.request.Request(f"{STATUS_URL}?ticket={ticket}", headers=HEADERS)
            try:
                opener.open(req, timeout=20)
            except Exception:
                pass
            cookie = jar_to_cookie_string(jar)
            if "t=" in cookie or "NOWCODERCLOUDTOKEN" in cookie:
                return cookie
    raise TimeoutError(f"扫码登录超时（{expire} 秒未确认）")


def main():
    print("获取登录二维码…\n")
    try:
        cookie = login_flow(
            render_cb=lambda t: print(t + "\n"),
            status_cb=lambda m: print(f"\r{m}", end="", flush=True),
        )
    except TimeoutError as e:
        print(f"\n✗ {e}")
        sys.exit(1)
    save_cookie(cookie)
    print(f"\n✓ 登录成功，Cookie 已保存到 {COOKIE_FILE}")
    # 校验一下
    print("校验中…")
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tui.py"), "--check"],
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)


if __name__ == "__main__":
    main()
