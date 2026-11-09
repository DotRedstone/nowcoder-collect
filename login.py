#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""牛客网终端扫码登录（纯标准库，无第三方依赖）。

流程：
  1. GET /oauth2/login/wechat_qr_code        → ticket + imageUrl（微信二维码，120s 过期）
  2. 从 ticket 里 base64 解出微信登录 URL
  3. 用 qrserver.com 把 URL 生成标准二维码 PNG → 本地解码（stdlib）→ 终端渲染
  4. 轮询 /oauth2/login/wechat_mp_status?ticket=…   → 未扫描/已扫描/确认
  5. 确认后 Set-Cookie 携带 t 令牌 → 收集 → 写 ~/.dsh/nowcoder/cookie.txt

用法:
  python3 login.py            # 终端显示二维码，微信扫码登录
"""
import base64
import http.cookiejar
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zlib

BASE = "https://www.nowcoder.com"
QR_URL = BASE + "/oauth2/login/wechat_qr_code"
STATUS_URL = BASE + "/oauth2/login/wechat_mp_status"
COOKIE_FILE = os.path.expanduser("~/.dsh/nowcoder/cookie.txt")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": BASE + "/login"}


# ---------------------------------------------------------------- 从 ticket 解出登录 URL
def extract_wechat_url(ticket: str) -> str:
    """ticket 是 base64，微信登录 URL 内嵌其中（http://weixin.qq.com/q/...）。"""
    raw = base64.b64decode(ticket + "=" * (-len(ticket) % 4), altchars=b"-_")
    text = raw.decode("latin-1")
    m = re.search(r"https?://[A-Za-z0-9_./?=&%\-]+", text)
    return m.group(0) if m else ""


# ---------------------------------------------------------------- PNG 解码（纯标准库）
def decode_png(data: bytes):
    """解码 PNG 到 (width, height, grayscale_pixels)。支持位深 1/2/4/8，灰度/真彩/调色板/alpha。"""
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
    row_bytes = (width * channels * bit_depth + 7) // 8
    bpp = max(1, (channels * bit_depth) // 8)

    # 反滤波（None/Sub/Up/Average/Paeth），字节级
    out = bytearray()
    prev = bytearray(row_bytes)
    p = 0
    for _ in range(height):
        ft = raw[p]
        p += 1
        line = bytearray(raw[p:p + row_bytes])
        p += row_bytes
        if ft == 1:  # Sub
            for i in range(bpp, row_bytes):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:  # Up
            for i in range(row_bytes):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:  # Average
            for i in range(row_bytes):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:  # Paeth
            for i in range(row_bytes):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out += line
        prev = line

    # 转灰度像素
    pixels = []
    n = width * height
    if bit_depth == 8:
        for i in range(0, len(out), channels):
            if len(pixels) >= n:
                break
            pixels.append(_gray8(out, i, color_type, palette))
    else:
        per_byte = 8 // bit_depth
        mask = (1 << bit_depth) - 1
        maxv = mask
        for byte in out:
            for k in range(per_byte):
                if len(pixels) >= n:
                    break
                idx = (byte >> (8 - bit_depth * (k + 1))) & mask
                if color_type == 3 and palette:
                    r, g, b = palette[idx * 3: idx * 3 + 3]
                    pixels.append((r * 299 + g * 587 + b * 114) // 1000)
                else:
                    pixels.append(idx * 255 // maxv)
    return width, height, pixels


def _gray8(out, i, color_type, palette):
    if color_type == 0:
        return out[i]
    if color_type in (2, 6):
        r, g, b = out[i], out[i + 1], out[i + 2]
        return (r * 299 + g * 587 + b * 114) // 1000
    if color_type == 3:
        idx = out[i]
        r, g, b = palette[idx * 3: idx * 3 + 3]
        return (r * 299 + g * 587 + b * 114) // 1000
    return out[i]  # 4: 灰度+alpha


# ---------------------------------------------------------------- 终端渲染
def render_qr(pixels, width, height, target_cells=40) -> str:
    """把灰度位图缩到终端，用 ██/空格 渲染成可扫的二维码。"""
    scale = max(1, width // target_cells)
    lines = ["  " * (target_cells + 2)]
    for y in range(0, height, scale):
        row = "  "
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


# ---------------------------------------------------------------- 登录流程
def fetch_qr():
    req = urllib.request.Request(QR_URL, headers=HEADERS)
    d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8"))
    return d["data"]["ticket"], d["data"]["imageUrl"], d["data"]["expireSecond"]


def generate_qr_text(url: str) -> str:
    """生成终端二维码文本。优先级：qrencode > nix-shell qrencode > QR 服务器本地解码。"""
    # 1. qrencode 直接可用（标准终端二维码工具）
    if shutil.which("qrencode"):
        r = subprocess.run(["qrencode", "-t", "UTF8", "-s", "1", "-m", "2", "-o", "-", url],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    # 2. NixOS：通过 nix-shell 拉 qrencode
    if shutil.which("nix-shell"):
        try:
            r = subprocess.run(
                ["nix-shell", "-p", "qrencode", "--run",
                 "qrencode -t UTF8 -s 1 -m 2 -o - " + shlex.quote(url)],
                capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        except Exception:
            pass
    # 3. 兜底：QR 服务器生成标准二维码 PNG → 本地解码渲染
    api = "https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=" + urllib.parse.quote(url, safe="")
    data = urllib.request.urlopen(urllib.request.Request(api, headers={"User-Agent": UA}), timeout=20).read()
    w, h, px = decode_png(data)
    return render_qr(px, w, h)


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


def login_flow(render_cb=None, status_cb=None) -> str:
    """扫码登录，成功返回 cookie 字符串；超时抛 TimeoutError。"""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    ticket, image_url, expire = fetch_qr()
    if render_cb:
        try:
            url = extract_wechat_url(ticket)
            render_cb("请用微信「扫一扫」扫描下方二维码登录：\n\n" + generate_qr_text(url) + "\n")
        except Exception as e:
            render_cb(f"二维码生成失败，请用微信扫一扫打开：\n{image_url}\n({e})")

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
        cookie = jar_to_cookie_string(jar)
        if code == 0 or "成功" in msg or "t=" in cookie:
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


if __name__ == "__main__":
    main()
