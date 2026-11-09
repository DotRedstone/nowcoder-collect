#!/usr/bin/env python3
"""Nowcoder Review Workspace: responsive Textual dashboard for contest reviews."""
from __future__ import annotations

import difflib
import json
import subprocess
import sys
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Select, Static, TabbedContent, TabPane, TextArea

from tui_backend import COOKIE_PATH, UID_PATH, DB_PATH, REVIEW_PATH, ReviewStore, case_key, local_contests, merge_contests, pair_sources, read_secret, write_secret
import login

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "skill/scripts"))
import nowcoder_extract as extract  # noqa: E402

class ReviewApp(App):
    TITLE = "NOWCODER · Review Workspace"
    SUB_TITLE = "比赛代码审查"
    CSS = """
    Screen { layout: vertical; }
    #toolbar { height: 3; background: $panel; padding: 0 1; }
    #toolbar Button { margin-right: 1; min-width: 11; }
    #workspace { height: 1fr; }
    .pane { border: round $primary-background-lighten-2; padding: 0 1; min-width: 19; }
    .pane Label { height: 2; text-style: bold; padding-top: 1; }
    #left { width: 26%; }
    #middle { width: 39%; }
    #right { width: 35%; }
    DataTable { height: 1fr; }
    #search, #level { margin-bottom: 1; }
    #evidence { height: 12; padding: 1; overflow-y: auto; }
    #codes { height: 1fr; }
    TextArea { height: 1fr; }
    #status { height: 3; padding: 0 2; background: $panel; content-align-vertical: middle; }
    #login-panel { display: none; height: 1fr; padding: 1 3; }
    #qr { height: 1fr; overflow-y: auto; }
    #login-message { height: 3; }
    #review-buttons { height: 3; }
    #review-buttons Button { margin-right: 1; }
    """
    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("question_mark", "help", "帮助"),
        Binding("slash", "search", "搜索"),
        Binding("r", "refresh", "刷新"),
        Binding("a", "analyze", "分析"),
        Binding("c", "collect", "采集"),
        Binding("escape", "back", "返回"),
        Binding("ctrl+t", "toggle_login", "登录"),
        Binding("ctrl+x", "cancel_task", "取消任务"),
    ]

    def __init__(self):
        super().__init__()
        self.remote = []
        self.contests = []
        self.contest = None
        self.cases = []
        self.filtered_cases = []
        self.current = None
        self.report = {}
        self.store = ReviewStore()
        self.proc = None
        self.running = False
        self.cancel_requested = False
        self.login_active = False
        self.login_gen = 0
        self.login_visible = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="toolbar"):
            yield Button("↻ 刷新", id="refresh")
            yield Button("↓ 采集", id="collect", variant="primary")
            yield Button("▶ 本地分析", id="analyze")
            yield Button("▤ 榜单", id="rank")
            yield Button("账户 / 扫码", id="login")
        with Horizontal(id="workspace"):
            with Vertical(classes="pane", id="left"):
                yield Label("比赛 / Contests")
                yield DataTable(id="contests", cursor_type="row")
            with Vertical(classes="pane", id="middle"):
                yield Label("线索 / Findings")
                yield Input(placeholder="搜索选手、题号、判定  /", id="search")
                yield Select([("全部线索", "all"), ("高优先级", "high"), ("待复核", "pending"), ("已处理", "reviewed")], value="all", allow_blank=False, id="level")
                yield DataTable(id="findings", cursor_type="row")
            with Vertical(classes="pane", id="right"):
                yield Label("证据 / Evidence")
                yield Static("选择比赛并分析本地数据，或先采集。", id="evidence")
                with TabbedContent(id="codes"):
                    with TabPane("代码 A"):
                        yield TextArea("", id="code-a", read_only=True, show_line_numbers=True)
                    with TabPane("代码 B"):
                        yield TextArea("", id="code-b", read_only=True, show_line_numbers=True)
                    with TabPane("Diff"):
                        yield TextArea("", id="diff", read_only=True, show_line_numbers=True)
                with Horizontal(id="review-buttons"):
                    yield Button("已核查", id="review")
                    yield Button("无需跟进", id="dismiss")
                    yield Button("需进一步核实", id="investigate")
        with Vertical(id="login-panel"):
            yield Label("微信扫码登录 / Login")
            yield Static("点击开始扫码，或使用已有的 Cookie。", id="qr")
            yield Static("", id="login-message")
            with Horizontal():
                yield Button("开始扫码", id="start-login", variant="primary")
                yield Button("关闭", id="close-login")
                yield Button("手动配置提示", id="cookie-help")
        yield Static("准备就绪 · ? 帮助 · Tab 切换焦点", id="status")
        yield Footer()

    def on_mount(self):
        self.query_one("#contests", DataTable).add_columns("比赛", "提交", "代码")
        self.query_one("#findings", DataTable).add_columns("级别", "题", "选手对", "L3", "状态")
        self.load_contests()
        if read_secret(COOKIE_PATH):
            self.action_refresh()
        else:
            self.status("本地数据可离线使用 · 如需远程比赛请登录。")

    def status(self, text):
        self.query_one("#status", Static).update(text)

    def load_contests(self):
        try:
            self.contests = merge_contests(local_contests(), self.remote)
        except Exception as e:
            self.status(f"本地数据读取失败：{e}")
            self.contests = merge_contests([], self.remote)
        table = self.query_one("#contests", DataTable)
        table.clear()
        for c in self.contests:
            table.add_row(f"{c['name'] or '比赛'}  #{c['contest_id']}", str(c.get("submissions", 0)), str(c.get("codes", 0)), key=str(c["contest_id"]))
        if self.contest not in [c["contest_id"] for c in self.contests]:
            self.contest = None
        if self.contest is None and self.contests:
            self.choose(self.contests[0]["contest_id"])

    def choose(self, cid):
        if cid == self.contest:
            return
        self.contest = cid
        self.report = {}
        self.cases = []
        self.current = None
        self.query_one("#findings", DataTable).clear()
        self.query_one("#evidence", Static).update(f"已选择比赛 #{cid}\n按 a 检测数据库 · 按 c 抓取后检测")
        for name in ("#code-a", "#code-b", "#diff"):
            self.query_one(name, TextArea).load_text("")
        self.status(f"比赛 #{cid} · a 本地分析 · c 采集 · r 在线刷新")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted):
        key = event.row_key
        if key is None:
            return
        try:
            idx = int(key.value)
            if event.data_table.id == "contests":
                self.choose(idx)
            elif event.data_table.id == "findings":
                self.preview(self.filtered_cases[idx])
        except (TypeError, ValueError, IndexError):
            return

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "search":
            self.filter_cases()

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "level":
            self.filter_cases()

    def filter_cases(self):
        search = self.query_one("#search", Input).value.strip().casefold()
        level = self.query_one("#level", Select).value
        self.filtered_cases = []
        for case in self.cases:
            state = self.store.status(case_key(self.contest, "contest", case))
            text = f"{' '.join(case['pair'])} {case['problem']} {case['verdict']}".casefold()
            if search and search not in text: continue
            if level == "high" and case["verdict"] not in ("实锤", "中偏强"): continue
            if level == "pending" and state != "待复核": continue
            if level == "reviewed" and state == "待复核": continue
            self.filtered_cases.append(case)
        table = self.query_one("#findings", DataTable)
        table.clear()
        for i, case in enumerate(self.filtered_cases):
            table.add_row(case["verdict"], str(case["problem"]), " ↔ ".join(case["pair"]),
                          f"{case['scores']['L3']:.2f}", self.store.status(case_key(self.contest, "contest", case)), key=str(i))
        if self.filtered_cases:
            self.preview(self.filtered_cases[0])
        else:
            self.current = None
            self.query_one("#evidence", Static).update("当前没有符合筛选条件的线索。")

    def preview(self, case):
        self.current = case
        key = case_key(self.contest, "contest", case)
        scores = case.get("scores", {})
        rare = case.get("rare", {})
        timeline = case.get("timeline") or {}
        self.query_one("#evidence", Static).update("\n".join([
            f"{case['problem']} · {' ↔ '.join(case['pair'])}",
            f"算法等级：{case['verdict']}  ·  人工复核：{self.store.status(key)}",
            f"L1 {scores.get('L1', 0):.2f} · L2 {scores.get('L2', 0):.2f} · L3 {scores.get('L3', 0):.2f}",
            f"稀有指纹：{rare.get('shared', 0)} · 分数 {rare.get('score', 0):.2f}",
            f"AC 时间差：{timeline.get('delta_seconds') if timeline.get('delta_seconds') is not None else '未知'} 秒",
            f"时间先后符合检测规则：{'是' if timeline.get('direction_valid') else '否/未知'}",
            "注意：线索并不等于作弊证据，必须人工核查。",
        ]))
        try:
            a, b = pair_sources(DB_PATH, self.contest, "contest", case)
            ca, cb = a["code"], b["code"]
            self.query_one("#code-a", TextArea).load_text(ca)
            self.query_one("#code-b", TextArea).load_text(cb)
            diff = difflib.unified_diff(ca.splitlines(keepends=True), cb.splitlines(keepends=True),
                                        fromfile=f"{a['student']} #{a['submission_id']}",
                                        tofile=f"{b['student']} #{b['submission_id']}")
            self.query_one("#diff", TextArea).load_text("".join(diff) or "代码文本没有差异")
        except Exception as e:
            self.status(f"代码预览失败：{e}")

    def mark(self, state):
        if not self.current or self.contest is None:
            self.status("请先选择一条线索。")
            return
        try:
            self.store.set(case_key(self.contest, "contest", self.current), status=state)
            self.filter_cases()
            self.status(f"已保存复核状态：{state} · {REVIEW_PATH}")
        except Exception as e:
            self.status(f"保存失败：{e}")

    def on_button_pressed(self, event: Button.Pressed):
        actions = {
            "refresh": self.action_refresh, "collect": self.action_collect, "analyze": self.action_analyze,
            "rank": self.action_rank, "login": self.action_toggle_login,
            "start-login": self.start_login, "close-login": self.action_back,
            "cookie-help": self.cookie_help,
            "review": lambda: self.mark("已核查"), "dismiss": lambda: self.mark("无需跟进"),
            "investigate": lambda: self.mark("需进一步核实"),
        }
        action = actions.get(event.button.id)
        if action: action()

    def action_help(self):
        self.status("Tab 切换面板 · 方向键选中 · / 搜索 · a 分析 · c 采集 · r 刷新 · Ctrl+T 登录 · Ctrl+X 取消 · q 退出")

    def action_search(self):
        if not self.login_visible: self.query_one("#search", Input).focus()

    def action_toggle_login(self):
        self.login_visible = not self.login_visible
        self.query_one("#workspace").display = not self.login_visible
        self.query_one("#toolbar").display = not self.login_visible
        self.query_one("#login-panel").display = self.login_visible

    def action_back(self):
        if self.login_visible: self.action_toggle_login()

    def cookie_help(self):
        self.query_one("#login-message", Static).update(
            f"可手动将 Cookie 保存至 {COOKIE_PATH}（0600 权限），用户 UID 保存至 {UID_PATH}。不要把凭据提交进 Git。")

    def start_login(self):
        if self.login_active:
            self.status("登录仍在进行。")
            return
        self.login_gen += 1
        self.login_active = True
        self.query_one("#qr", Static).update("正在获取登录二维码…")
        self.login_worker(self.login_gen)

    @work(thread=True, group="login", exclusive=True)
    def login_worker(self, gen):
        def qr(s): self.call_from_thread(self.login_update, gen, "#qr", s)
        def status(s): self.call_from_thread(self.login_update, gen, "#login-message", s)
        try:
            cookie = login.login_flow(render_cb=qr, status_cb=status)
            write_secret(COOKIE_PATH, cookie)
            self.call_from_thread(self.login_finish, gen, "扫码成功，Cookie 已保存。")
        except Exception as e:
            self.call_from_thread(self.login_finish, gen, f"扫码失败：{e}")

    def login_update(self, gen, target, message):
        if self.login_gen == gen:
            self.query_one(target, Static).update(message)

    def login_finish(self, gen, message):
        if self.login_gen != gen: return
        self.login_active = False
        self.query_one("#login-message", Static).update(message)
        self.status(message)
        if message.startswith("扫码成功"): self.action_refresh()

    def action_refresh(self):
        if self.running:
            self.status("采集/分析期间不能刷新。")
            return
        if not read_secret(COOKIE_PATH) or not read_secret(UID_PATH):
            self.status(f"在线列表需要 Cookie 和 UID（{UID_PATH}）；本地浏览不受影响。")
            return
        self.status("刷新远程比赛中…")
        self.refresh_worker()

    @work(thread=True, group="refresh", exclusive=True)
    def refresh_worker(self):
        try:
            remote = extract.list_recent(read_secret(COOKIE_PATH), read_secret(UID_PATH))
            self.call_from_thread(self.refresh_done, remote, None)
        except Exception as e:
            self.call_from_thread(self.refresh_done, [], str(e))

    def refresh_done(self, remote, error):
        if error:
            self.status(f"远程列表失败，已保留本地列表：{error}")
            return
        self.remote = remote
        self.load_contests()
        self.status(f"远程比赛 {len(remote)} 场已刷新。")

    def action_collect(self): self.launch(True)
    def action_analyze(self): self.launch(False)

    def launch(self, collect):
        if self.running:
            self.status("已有任务运行，Ctrl+X 取消。")
            return
        if self.contest is None:
            self.status("请先选择比赛。")
            return
        if collect and not read_secret(COOKIE_PATH):
            self.status("采集前需要登录。")
            return
        self.running = True
        self.cancel_requested = False
        self.status(f"比赛 #{self.contest}：{'采集并分析' if collect else '离线分析'}启动中…")
        self.job_worker(self.contest, collect)

    def action_cancel_task(self):
        if not self.running:
            self.status("没有运行中的任务。")
            return
        self.cancel_requested = True
        proc = self.proc
        if proc is not None and proc.poll() is None: proc.terminate()
        self.status("已请求取消任务…")

    @work(thread=True, group="jobs", exclusive=True)
    def job_worker(self, cid, collect):
        try:
            if collect:
                proc = subprocess.Popen(
                    [sys.executable, str(ROOT / "skill/scripts/nowcoder_extract.py"),
                     "--contest", str(cid), "--threads", "16", "--db", str(DB_PATH),
                     "--out", str(Path.home() / "nowcoder-data")],
                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
                self.proc = proc
                for line in proc.stderr:
                    if self.cancel_requested:
                        proc.terminate()
                        break
                    if line.strip(): self.call_from_thread(self.status, f"采集 #{cid} · {line.strip()[:160]}")
                output = proc.stdout.read()
                proc.wait()
                self.proc = None
                if self.cancel_requested: raise InterruptedError("采集已取消")
                if proc.returncode: raise RuntimeError(f"采集失败（退出码 {proc.returncode}）：{output[-200:]}")
                try:
                    if not json.loads(output.strip().splitlines()[-1]).get("ok"):
                        raise RuntimeError("采集未返回成功标记")
                except (ValueError, IndexError) as e:
                    raise RuntimeError(f"采集输出无效：{e}") from e
                self.call_from_thread(self.status, "采集完成，开始检测…")
            if self.cancel_requested: raise InterruptedError("任务已取消")
            proc = subprocess.Popen(
                [sys.executable, str(ROOT / "detector/main.py"), "--contest", str(cid),
                 "--db", str(DB_PATH), "--phase", "contest"],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.proc = proc
            output, errors = proc.communicate()
            self.proc = None
            if self.cancel_requested: raise InterruptedError("任务已取消")
            if proc.returncode: raise RuntimeError(errors.strip()[-500:] or f"检测退出码 {proc.returncode}")
            self.call_from_thread(self.job_done, cid, json.loads(output), None)
        except Exception as e:
            self.call_from_thread(self.job_done, cid, None, str(e))
        finally:
            self.proc = None

    def job_done(self, cid, report, error):
        self.running = False
        self.cancel_requested = False
        if error:
            self.status(f"任务失败或取消：{error}")
            return
        self.load_contests()
        if cid != self.contest:
            self.status(f"比赛 #{cid} 已分析；请重新选中该比赛。")
            return
        self.report = report or {}
        self.cases = self.report.get("results", [])
        self.filter_cases()
        self.status(f"分析完成 · 比较 {self.report.get('meta', {}).get('pairs_compared', 0)} 对 · {len(self.cases)} 条线索")

    def action_rank(self):
        if self.contest is None:
            self.status("请选择比赛。")
            return
        self.status("获取榜单中…")
        self.rank_worker(self.contest)

    @work(thread=True, group="rank", exclusive=True)
    def rank_worker(self, cid):
        try:
            _, ranked = extract.compute_rank(read_secret(COOKIE_PATH), cid, "contest")
            lines = ["排名  选手                 分数      AC  提交"]
            for i, s in enumerate(ranked, 1):
                lines.append(f"{i:>3}   {s['name'][:18]:<18} {s['total']:>8.2f}  {s['ac']:>2}  {s['submissions']:>4}")
            self.call_from_thread(self.rank_done, cid, "\n".join(lines), None)
        except Exception as e:
            self.call_from_thread(self.rank_done, cid, "", str(e))

    def rank_done(self, cid, lines, error):
        if error: self.status(f"榜单读取失败：{error}")
        else:
            self.query_one("#evidence", Static).update(f"比赛 #{cid} · 榜单\n{lines}")
            self.status("榜单已展示在证据栏，选择线索返回审查。")


if __name__ == "__main__":
    ReviewApp().run()
