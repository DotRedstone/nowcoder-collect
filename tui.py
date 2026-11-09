#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""牛客代码收集 + 雷同检测 一体化 TUI 面板（纯标准库 curses）。

流程：登录(Cookie) → 选比赛 → 爬取(多线程) → 分析(确定性检测) → 结果浏览/榜单。

用法:
  python3 tui.py                 # 交互式面板
  python3 tui.py --check         # 非交互：只校验登录并列出比赛（测试用）
"""
import curses
import json
import os
import re
import subprocess
import sys
import threading

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO, "skill", "scripts"))
sys.path.insert(0, os.path.join(REPO, "detector"))

import nowcoder_extract as ex          # noqa: E402
import login                           # noqa: E402
from main import detect as run_detect  # noqa: E402
from main import load_config           # noqa: E402

COOKIE_FILE = os.path.expanduser("~/.dsh/nowcoder/cookie.txt")
UID_FILE = os.path.expanduser("~/.dsh/nowcoder/uid.txt")
DB_FILE = os.path.expanduser("~/.dsh/nowcoder/nowcoder.db")
OUT_DIR = os.path.expanduser("~/nowcoder-data")
CONFIG = os.path.join(REPO, "detector", "config.toml")
THREADS = 32

VERDICT_COLOR = {"实锤": 1, "中偏强": 2, "存疑": 3, "排除": 4}


# ---------------------------------------------------------------- 工作流（可独立测试）
def read_cookie() -> str:
    return open(COOKIE_FILE, encoding="utf-8").read().strip() if os.path.exists(COOKIE_FILE) else ""


def read_uid() -> str:
    return open(UID_FILE, encoding="utf-8").read().strip() if os.path.exists(UID_FILE) else ""


def save_cookie(c: str) -> None:
    os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(c.strip())
    os.chmod(COOKIE_FILE, 0o600)


def list_contests(cookie: str, uid: str):
    """返回最近比赛列表；登录失效返回 None。"""
    try:
        return ex.list_recent(cookie, uid)
    except Exception:
        return None


def run_extract(contest_id, status: dict):
    """后台线程：跑提取，实时更新 status（text/step/cur/total/summary）。"""
    status.update({"text": "正在启动…", "step": 1, "total_steps": 5, "cur": 0, "total": 0})
    proc = subprocess.Popen(
        [sys.executable, os.path.join(REPO, "skill", "scripts", "nowcoder_extract.py"),
         "--contest", str(contest_id), "--threads", str(THREADS),
         "--out", OUT_DIR, "--db", DB_FILE],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    for line in proc.stderr:
        line = line.strip()
        if not line:
            continue
        status["text"] = line
        m = re.search(r"\[(\d+)/(\d+)\]", line)
        if m:
            status["step"] = int(m.group(1))
            status["total_steps"] = int(m.group(2))
        m = re.search(r"(\d+)/(\d+)", line)
        if m:
            status["cur"] = int(m.group(1))
            status["total"] = int(m.group(2))
    out = proc.stdout.read()
    proc.wait()
    try:
        status["summary"] = json.loads(out.strip().splitlines()[-1])
    except Exception:
        status["summary"] = None


def run_analyze(contest_id, status: dict):
    """后台线程：跑确定性检测，结果写入 status['report']。"""
    status["text"] = "正在分析（两两比较相似度）…"
    cfg = load_config(CONFIG)
    status["report"] = run_detect(DB_FILE, contest_id, "contest", cfg, False)


def build_rank(contest_id, status: dict):
    try:
        problems, ranked = ex.compute_rank(ex.load_cookie(""), contest_id, "contest")
        status["rank"] = ex.rank_table_md(problems, ranked, "contest")
    except Exception as e:
        status["rank"] = f"榜单获取失败：{e}"


# ---------------------------------------------------------------- TUI
class Tui:
    def __init__(self, stdscr):
        self.scr = stdscr
        self.scr.keypad(True)
        self.scr.timeout(100)  # getch 超时 100ms，让进度条/状态能实时刷新，不阻塞
        try:
            curses.curs_set(0)
        except curses.error:
            pass  # 部分终端不支持隐藏光标
        # 颜色初始化：start_color 先行；-1 默认背景需 use_default_colors；
        # 无颜色终端（COLORS=0）/ use_default_colors 失败时整体降级，绝不崩
        try:
            if curses.has_colors():
                curses.start_color()
                bg = curses.COLOR_BLACK
                try:
                    curses.use_default_colors()
                    bg = -1
                except Exception:
                    bg = curses.COLOR_BLACK
                curses.init_pair(1, curses.COLOR_RED, bg)
                curses.init_pair(2, curses.COLOR_YELLOW, bg)
                curses.init_pair(3, curses.COLOR_CYAN, bg)
                curses.init_pair(4, curses.COLOR_WHITE, bg)
                curses.init_pair(5, curses.COLOR_GREEN, bg)
                curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_WHITE)
        except Exception:
            pass  # 任何颜色异常都降级为无颜色，不影响功能

        self.cookie = read_cookie()
        self.uid = read_uid()
        self.contests = []
        self.menu_idx = 0
        self.pick_idx = 0
        self.results = []
        self.res_idx = 0
        self.res_scroll = 0
        self.detail_scroll = 0
        self.rank_scroll = 0
        self.report = None
        self.rank_md = None
        self.status = {}
        self.state = "menu"
        self.msg = ""
        self.contest_id = None
        self.contest_name = ""
        self.login_qr = ""
        self.login_status = ""
        self.login_done = False

    # ---- 工具 ----
    def h(self):
        return self.scr.getmaxyx()[0]

    def w(self):
        return self.scr.getmaxyx()[1]

    def title(self, text):
        self.scr.addstr(0, 0, " " + text + " ", curses.color_pair(6) | curses.A_BOLD)
        self.scr.addstr(0, len(text) + 2, " " * max(0, self.w() - len(text) - 2), curses.color_pair(6))

    def footer(self, keys):
        s = "  " + "   ".join(keys)
        if self.h() > 0:
            self.scr.addstr(self.h() - 1, 0, s[: self.w() - 1], curses.color_pair(6))
            self.scr.addstr(self.h() - 1, len(s), " " * max(0, self.w() - len(s) - 1), curses.color_pair(6))

    def box(self, y, x, h, w):
        """画一个圆角边框。"""
        if w < 4 or h < 3:
            return
        self.scr.addstr(y, x, "╭" + "─" * (w - 2) + "╮")
        for i in range(1, h - 1):
            if y + i >= self.h():
                break
            self.scr.addstr(y + i, x, "│")
            self.scr.addstr(y + i, x + w - 1, "│")
        if y + h - 1 < self.h():
            self.scr.addstr(y + h - 1, x, "╰" + "─" * (w - 2) + "╯")

    def progress_bar(self, y, x, w, frac, label=""):
        """画进度条：[████░░░░] 50%"""
        frac = max(0.0, min(1.0, frac))
        pct = f"{int(frac * 100):3d}%"
        bar_w = max(6, w - len(label) - len(pct) - 6)
        filled = int(bar_w * frac)
        bar = "█" * filled + "░" * (bar_w - filled)
        self.scr.addstr(y, x, f"{label} [{bar}] {pct}"[: w])

    def input_text(self, prompt):
        self.scr.clear()
        self.scr.addstr(2, 2, prompt)
        self.scr.addstr(4, 2, "→ ")
        curses.echo()
        try:
            s = self.scr.getstr(4, 4, 4000).decode("utf-8", "replace")
        finally:
            curses.noecho()
        return s.strip()

    # ---- 登录校验 ----
    def ensure_login(self):
        if not self.cookie:
            self.msg = "未找到 Cookie，请在菜单里选「更新 Cookie」粘贴"
            return False
        if not self.uid:
            # 尝试从 cookie 里猜不到 uid，需用户提供；先跳过，list_recent 会失败
            self.msg = "未找到 uid（~/.dsh/nowcoder/uid.txt）"
        return True

    # ---- 各屏幕 ----
    def draw_menu(self):
        self.scr.clear()
        self.title(" 牛客代码收集 · 雷同检测 ")
        login = "✓ 已登录" if self.cookie else "✗ 未登录"
        self.scr.addstr(2, 2, f"登录状态: {login}", curses.color_pair(5 if self.cookie else 2) | curses.A_BOLD)
        self.scr.addstr(3, 2, f"数据库:    {DB_FILE}", curses.A_DIM)

        items = [
            ("选择比赛并分析", "登录 → 爬取 → 检测"),
            ("分析已爬取的比赛", "直接用数据库"),
            ("查看榜单", "重建 IOI 总分排名"),
            ("扫码登录（微信）", "终端二维码登录"),
            ("手动粘贴 Cookie", "从浏览器复制"),
            ("退出", ""),
        ]
        by, bx = 5, 2
        bh = len(items) + 3
        bw = min(self.w() - 4, 62)
        self.box(by, bx, bh, bw)
        for i, (name, desc) in enumerate(items):
            y = by + 2 + i
            attr = curses.A_REVERSE if i == self.menu_idx else curses.A_NORMAL
            line = f"  {i + 1}. {name}"
            if desc:
                line = line.ljust(bw - 4 - len(desc)) + desc
            self.scr.addstr(y, bx + 1, line[: bw - 2], attr)
        if self.msg:
            self.scr.addstr(self.h() - 2, 2, self.msg, curses.color_pair(2))
        self.footer(["↑↓/数字 选择", "Enter 确认", "q 退出"])

    def draw_pick(self):
        self.scr.clear()
        self.title("选择比赛")
        self.scr.addstr(2, 2, "我参加的比赛（最近）：", curses.A_BOLD)
        if not self.contests:
            self.scr.addstr(4, 2, "（空 / 登录失效，请回菜单更新 Cookie）", curses.color_pair(2))
        else:
            for i, c in enumerate(self.contests):
                y = 4 + i
                if y >= self.h() - 2:
                    break
                attr = curses.A_REVERSE if i == self.pick_idx else curses.A_NORMAL
                name = c.get("contestName") or "?"
                cid = c.get("contestId")
                self.scr.addstr(y, 2, f"  {name}  (#{cid})", attr)
        self.footer(["↑↓ 选择", "Enter 确认", "Esc 返回"])

    def draw_running(self):
        self.scr.clear()
        self.title(f" 正在处理 · {self.contest_name} (#{self.contest_id}) ")
        steps = ["拉取比赛信息", "拉取提交列表", "抓取代码（多线程）", "写库 + 导出报告", "完成"]
        step = self.status.get("step", 1)
        cur = self.status.get("cur", 0)
        total = self.status.get("total", 0)
        txt = self.status.get("text", "")
        w = self.w()

        y = 2
        for i, s in enumerate(steps, 1):
            if i < step:
                self.scr.addstr(y, 3, f"  ✓  {s}", curses.color_pair(5))
                y += 1
            elif i == step:
                self.scr.addstr(y, 3, f"  ▶  {s}", curses.color_pair(3) | curses.A_BOLD)
                y += 1
                # 阶段内进度条：缩进挂在当前 ▶ 这一步下面，明确给出 X/Y + 百分比
                if total > 0:
                    frac = min(1.0, cur / total)
                    bar_w = max(10, min(40, w - 28))
                    filled = int(bar_w * frac)
                    bar = "█" * filled + "░" * (bar_w - filled)
                    self.scr.addstr(y, 6, f"└─ [{bar}]  {cur}/{total}  ({int(frac * 100)}%)", curses.color_pair(3))
                else:
                    self.scr.addstr(y, 6, f"└─ {txt[: w - 8]}", curses.color_pair(3))
                y += 1
            else:
                self.scr.addstr(y, 3, f"  ○  {s}", curses.color_pair(4))
                y += 1

        self.scr.addstr(y + 1, 3, f"  {txt[: w - 6]}", curses.A_DIM)
        self.footer(["请稍候… 多线程爬取约 1 分钟"])

    def draw_results(self):
        self.scr.clear()
        self.title(f" 雷同检测结果 · {self.contest_name} (#{self.contest_id}) ")
        m = (self.report or {}).get("meta", {})
        vs = m.get("verdict_summary", {})
        parts = []
        for name, col in (("实锤", 1), ("中偏强", 2), ("存疑", 3), ("排除", 4)):
            parts.append((name, vs.get(name, 0), col))
        self.scr.addstr(2, 2, f"比较 {m.get('pairs_compared', 0)} 对 → ", curses.A_BOLD)
        x = len(f"比较 {m.get('pairs_compared', 0)} 对 → ") + 2
        for name, n, col in parts:
            s = f" {name} {n} "
            self.scr.addstr(2, x, s, curses.color_pair(col) | curses.A_BOLD)
            x += len(s)
        self.scr.addstr(3, 2, "─" * (self.w() - 4))

        h = self.h()
        start = self.res_scroll
        if not self.results:
            self.scr.addstr(4, 2, "  （未发现跨人同题雷同，队伍很干净 ✓）", curses.color_pair(5))
        for i in range(start, min(len(self.results), start + h - 6)):
            r = self.results[i]
            y = 4 + i - start
            attr = curses.A_REVERSE if i == self.res_idx else curses.A_NORMAL
            color = curses.color_pair(VERDICT_COLOR.get(r["verdict"], 4))
            sc, ra, tv = r["scores"], r["rare"], r["timeline"]
            left = f"{r['verdict']}  {r['pair'][0]}/{r['problem']} ↔ {r['pair'][1]}"
            right = f"L2={sc['L2']:.2f} L3={sc['L3']:.2f} rare={ra['score']:.2f} Δ={tv.get('delta_seconds') or '—'}s"
            line = ("  " + left.ljust(40) + right)[: self.w() - 2]
            self.scr.addstr(y, 2, line, attr | color)
        self.footer(["↑↓ 滚动", "Enter 详情", "r 榜单", "q 返回"])

    def draw_detail(self):
        r = self.results[self.res_idx]
        self.scr.clear()
        self.title("详情")
        text = json.dumps(r, ensure_ascii=False, indent=2)
        lines = text.splitlines()
        h = self.h()
        for i in range(self.detail_scroll, min(len(lines), self.detail_scroll + h - 2)):
            self.scr.addstr(i - self.detail_scroll, 0, lines[i][: self.w() - 1])
        self.footer(["↑↓ 滚动", "Esc 返回"])

    def draw_rank(self):
        self.scr.clear()
        self.title(f"榜单 · {self.contest_name}")
        lines = (self.rank_md or "（暂无）").splitlines()
        h = self.h()
        for i in range(self.rank_scroll, min(len(lines), self.rank_scroll + h - 2)):
            self.scr.addstr(i - self.rank_scroll, 0, lines[i][: self.w() - 1])
        self.footer(["↑↓ 滚动", "Esc 返回"])

    # ---- 主循环 ----
    def run(self):
        # 一个命令启动：没有 Cookie 就直接进入扫码登录，登录完自动回到主菜单
        if not self.cookie:
            self.start_login()
        while True:
            if self.state == "menu":
                self.draw_menu()
            elif self.state == "pick":
                self.draw_pick()
            elif self.state == "running":
                self.draw_running()
            elif self.state == "results":
                self.draw_results()
            elif self.state == "detail":
                self.draw_detail()
            elif self.state == "rank":
                self.draw_rank()
            elif self.state == "login":
                self.draw_login()
            self.scr.refresh()
            key = self.scr.getch()
            if key != -1:
                self.handle(key)
            if self.state == "quit":
                break

    def handle(self, key):
        if self.state == "menu":
            if key in (curses.KEY_UP, ord("k")):
                self.menu_idx = (self.menu_idx - 1) % 6
            elif key in (curses.KEY_DOWN, ord("j")):
                self.menu_idx = (self.menu_idx + 1) % 6
            elif ord("1") <= key <= ord("6"):
                self.menu_idx = key - ord("1")
                self.menu_action()
            elif key in (curses.KEY_ENTER, 10, 13):
                self.menu_action()
            elif key in (ord("q"), 27):
                self.state = "quit"
        elif self.state == "pick":
            if key in (curses.KEY_UP, ord("k")):
                self.pick_idx = max(0, self.pick_idx - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                self.pick_idx = min(len(self.contests) - 1, self.pick_idx + 1)
            elif key in (curses.KEY_ENTER, 10, 13):
                c = self.contests[self.pick_idx]
                self.contest_id = c["contestId"]
                self.contest_name = c.get("contestName", "")
                self.start_workflow()
            elif key == 27:
                self.state = "menu"
        elif self.state == "results":
            if key in (curses.KEY_UP, ord("k")):
                if self.res_idx > 0:
                    self.res_idx -= 1
                    if self.res_idx < self.res_scroll:
                        self.res_scroll = self.res_idx
            elif key in (curses.KEY_DOWN, ord("j")):
                if self.res_idx < len(self.results) - 1:
                    self.res_idx += 1
                    if self.res_idx >= self.res_scroll + self.h() - 8:
                        self.res_scroll += 1
            elif key in (curses.KEY_ENTER, 10, 13):
                if self.results:
                    self.state = "detail"
                    self.detail_scroll = 0
            elif key == ord("r"):
                self.status = {}
                threading.Thread(target=build_rank, args=(self.contest_id, self.status), daemon=True).start()
                self.state = "rank"
            elif key in (ord("q"), 27):
                self.state = "menu"
        elif self.state == "detail":
            if key in (curses.KEY_UP, ord("k")):
                self.detail_scroll = max(0, self.detail_scroll - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                self.detail_scroll += 1
            elif key in (ord("q"), 27):
                self.state = "results"
        elif self.state == "rank":
            if key in (curses.KEY_UP, ord("k")):
                self.rank_scroll = max(0, self.rank_scroll - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                self.rank_scroll += 1
            elif key in (ord("q"), 27):
                self.state = "results"
        elif self.state == "login":
            if self.login_done:
                self.state = "menu"
            elif key in (ord("q"), 27):
                self.state = "menu"

    def menu_action(self):
        if self.menu_idx == 0:       # 选择比赛并分析
            if not self.ensure_login():
                return
            self.contests = list_contests(self.cookie, self.uid) or []
            if not self.contests:
                self.msg = "拉取比赛列表失败（Cookie 可能失效），请更新 Cookie"
                return
            self.pick_idx = 0
            self.state = "pick"
        elif self.menu_idx == 1:     # 分析已爬取的比赛
            if not self.contests:
                self.contests = list_contests(self.cookie, self.uid) or []
            self.pick_idx = 0
            self.state = "pick"
        elif self.menu_idx == 2:     # 榜单
            self.msg = "请先选一个比赛再查看榜单"
            if self.contest_id is None:
                return
            self.status = {}
            threading.Thread(target=build_rank, args=(self.contest_id, self.status), daemon=True).start()
            self.state = "rank"
        elif self.menu_idx == 3:     # 扫码登录
            self.start_login()
        elif self.menu_idx == 4:     # 手动粘贴 Cookie
            c = self.input_text("粘贴牛客登录 Cookie（DevTools→Network→请求头→Cookie 整行）")
            if c:
                save_cookie(c)
                self.cookie = c
                self.msg = "Cookie 已保存"
        elif self.menu_idx == 5:
            self.state = "quit"

    def start_workflow(self):
        """登录→爬取→分析，后台跑，跑完切到结果页。"""
        self.state = "running"
        self.status = {"text": "启动中…", "summary": None, "report": None}

        def work():
            run_extract(self.contest_id, self.status)
            if self.status.get("summary") is None or not self.status["summary"].get("ok"):
                self.status["text"] = "爬取失败，请检查 Cookie"
                self.state = "menu"
                return
            run_analyze(self.contest_id, self.status)
            self.report = self.status.get("report")
            self.results = (self.report or {}).get("results", [])
            self.res_idx = 0
            self.res_scroll = 0
            self.state = "results"

        threading.Thread(target=work, daemon=True).start()

    def start_login(self):
        """微信扫码登录：后台跑，渲染二维码 + 轮询。"""
        self.state = "login"
        self.login_qr = "获取二维码中…"
        self.login_status = ""
        self.login_done = False

        def render(t):
            self.login_qr = t

        def status(m):
            self.login_status = m

        def work():
            try:
                cookie = login.login_flow(render_cb=render, status_cb=status)
                login.save_cookie(cookie)
                self.cookie = cookie
                self.login_status = "登录成功 ✓"
                self.msg = "登录成功"
            except Exception as e:
                self.login_status = f"登录失败：{e}"
                self.msg = f"登录失败：{e}"
            self.login_done = True

        threading.Thread(target=work, daemon=True).start()

    def draw_login(self):
        self.scr.clear()
        self.title("扫码登录（微信）")
        lines = (self.login_qr or "").splitlines()
        h = self.h()
        for i, ln in enumerate(lines[: h - 3]):
            self.scr.addstr(i, 0, ln[: self.w() - 1])
        self.scr.addstr(h - 2, 0, ("状态：" + (self.login_status or ""))[: self.w() - 1])
        self.footer(["q 取消返回"])


def main(stdscr):
    Tui(stdscr).run()


if __name__ == "__main__":
    if "--check" in sys.argv:
        # 非交互：校验登录 + 列出比赛
        cookie = read_cookie()
        uid = read_uid()
        if not cookie:
            print("✗ 无 Cookie")
            sys.exit(1)
        contests = list_contests(cookie, uid)
        if contests is None:
            print("✗ 登录失效")
            sys.exit(1)
        print(f"✓ 登录有效，{len(contests)} 场比赛：")
        for c in contests:
            print(f"  #{c['contestId']}  {c['contestName']}")
        sys.exit(0)
    curses.wrapper(main)
