#!/usr/bin/env python3
"""Nowcoder Review Workspace: responsive Textual dashboard for contest reviews."""
from __future__ import annotations

import difflib
import json
import os
import re
from datetime import datetime
import subprocess
import sys
from pathlib import Path

from rich.text import Text
from textual.screen import ModalScreen
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Input, Static, TabbedContent, TabPane, TextArea

from tui_backend import COOKIE_PATH, UID_PATH, DB_PATH, REVIEW_PATH, ReviewStore, case_key, local_contests, merge_contests, pair_sources, read_secret, write_secret
import login
from rice_theme import load_wallpaper_theme

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "skill/scripts"))
import nowcoder_extract as extract  # noqa: E402

class ReviewApp(App):
    TITLE = "NOWCODER · Review Workspace"
    SUB_TITLE = "比赛代码审查"
    CSS = """
    Screen {
        layout: vertical; background: $background; color: $foreground;
    }
    #titlebar {
        height: 1; padding: 0 2;
        color: $text-muted; background: $background;
    }
    #workspace {
        height: 1fr; width: 100%;
        padding: 0 1; background: $background;
    }
    .pane {
        height: 1fr; min-width: 18;
        border: round $panel; margin: 0 0 0 1;
        padding: 0 1; background: $background;
        border-title-color: $text-muted;
        border-title-style: bold;
        border-subtitle-color: $text-muted;
        border-subtitle-align: right;
    }
    .pane:focus-within {
        border: round $accent;
        border-title-color: $accent;
        border-subtitle-color: $text-accent;
    }
    #left { width: 25%; }
    #middle { width: 38%; }
    #right { width: 37%; }
    DataTable {
        height: 1fr;
        background: $background; color: $foreground;
        scrollbar-size: 1 1;
    }
    DataTable > .datatable--cursor {
        background: $accent 20%;
        color: $foreground;
        text-style: none;
    }
    DataTable > .datatable--hover { background: $accent 9%; }
    #search {
        display: none; height: 1;
        padding: 0 1; border: none;
        background: $surface;
    }
    #evidence {
        height: 11; padding: 1 0 0 1;
        overflow-y: auto; color: $foreground;
    }
    #empty {
        display: none; height: auto; padding: 3 1;
        color: $foreground;
    }
    #filterline { height: 1; color: $text-muted; }
    #codes { height: 1fr; }
    TabbedContent { background: $background; }
    Tabs { height: 2; background: $background; }
    Tab { background: $background; color: $text-muted; padding: 0 1; }
    Tab.-active { background: $background; color: $accent; text-style: bold; }
    TextArea {
        height: 1fr; border: none;
        background: $background; color: $foreground;
        scrollbar-size: 1 1;
    }
    #code-a, #code-b { width: 1fr; }
    #status {
        height: 1; width: 100%;
        background: $surface; color: $foreground;
    }
    #command-row, #id-row { display: none; height: 1; }
    #command, #contest-input {
        height: 1; border: none; padding: 0 1;
        background: $surface;
    }
    #login-panel {
        display: none;
        height: 1fr;
        align: center middle;
        padding: 1 2;
    }
    #login-card {
        width: 90; max-width: 100%; height: 100%;
        border: round $panel;
        border-title-color: $accent;
        padding: 1 2; background: $background;
    }
    #qr {
        height: 1fr; text-align: center;
        overflow-y: auto;
    }
    #login-message {
        height: 2; text-align: center;
        color: $text-muted;
    }
    #uid-input { height: 3; margin: 0 0 1 0; }
    HelpScreen { align: center middle; background: $background 70%; }
    #help-card {
        width: 72; max-width: 95%;
        height: auto; border: round $accent;
        padding: 1 2; background: $surface;
    }
    """
    BINDINGS = [
        Binding("q", "quit", "quit", show=False),
        Binding("question_mark", "help", "help", show=False),
        Binding("colon", "command", "command", show=False),
        Binding("slash", "search", "search", show=False),
        Binding("1", "pane_1", "contests", show=False),
        Binding("2", "pane_2", "findings", show=False),
        Binding("3", "pane_3", "inspector", show=False),
        Binding("f", "filter", "filter", show=False),
        Binding("r", "refresh", "refresh", show=False),
        Binding("a", "analyze", "analyze", show=False),
        Binding("c", "collect", "collect", show=False),
        Binding("g", "rank", "ranking", show=False),
        Binding("i", "open_id", "contest id", show=False),
        Binding("l", "toggle_login", "login", show=False),
        Binding("s", "scan", "scan", show=False),
        Binding("x", "mark_reviewed", "reviewed", show=False),
        Binding("d", "mark_dismissed", "dismissed", show=False),
        Binding("exclamation_mark", "mark_investigate", "investigate", show=False),
        Binding("t", "theme", "theme", show=False),
        Binding("escape", "back", "back", show=False),
        Binding("ctrl+x", "cancel_task", "cancel", show=False),
    ]
    THEMES = ("ansi-dark", "catppuccin-mocha", "nord", "gruvbox", "textual-dark")
    FILTERS = (("all", "ALL"), ("high", "HIGH"), ("pending", "PENDING"), ("reviewed", "REVIEWED"))

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
        self.filter_idx = 0
        self.active_pane = 0
        self._selected_finding = None
        self._status_message = "ready"
        self._progress = ""
        self._spinner_tick = 0
        self._wallpaper_source = ""
        self._nerd_font = os.environ.get("NOWCODER_NERD_FONT", "0") == "1"
        self._help_visible = False

    def compose(self) -> ComposeResult:
        yield Static("nowcoder / review", id="titlebar")
        with Horizontal(id="command-row"):
            yield Input(placeholder=":help / :collect / :analyze / :contest ID / :theme auto", id="command")
        with Horizontal(id="id-row"):
            yield Input(placeholder="contest id ↵", id="contest-input")
        with Horizontal(id="workspace"):
            with Vertical(classes="pane", id="left"):
                yield DataTable(id="contests", cursor_type="row", show_header=False, cell_padding=0)
            with Vertical(classes="pane", id="middle"):
                yield Static("ALL · f 筛选 · / 搜索", id="filterline")
                yield Input(placeholder="/ 搜索选手、题号、结果", id="search")
                yield Static("", id="empty")
                yield DataTable(id="findings", cursor_type="row", show_header=False, cell_padding=0)
            with Vertical(classes="pane", id="right"):
                yield Static("选择一条线索查看代码与时间证据。", id="evidence")
                with TabbedContent(id="codes"):
                    with TabPane("源码 A ↔ B"):
                        with Horizontal():
                            yield TextArea("", id="code-a", read_only=True, show_line_numbers=True)
                            yield TextArea("", id="code-b", read_only=True, show_line_numbers=True)
                    with TabPane("Diff"):
                        yield TextArea("", id="diff", read_only=True, show_line_numbers=True)
        with Vertical(id="login-panel"):
            with VerticalScroll(id="login-card"):
                yield Static("按 s 扫码 · 输入 UID 后回车保存 · Esc 返回", id="login-message")
                yield Static("按 s 开始微信扫码登录。", id="qr")
                yield Input(placeholder="牛客 UID（主页地址中的数字）· Enter 保存", id="uid-input")
        yield Static("", id="status")

    def on_key(self, event):
        if not isinstance(self.focused, DataTable): return
        if event.key == "j":
            self.focused.action_cursor_down()
            event.stop()
        elif event.key == "k":
            self.focused.action_cursor_up()
            event.stop()

    def on_mount(self):
        self.apply_theme(os.environ.get("NOWCODER_THEME") or os.environ.get("TEXTUAL_THEME") or "auto")
        self.query_one("#contests", DataTable).add_columns("比赛", "提交", "代码")
        self.query_one("#findings", DataTable).add_columns("级别", "题", "选手对", "L3", "状态")
        self._update_pane_titles()
        self.load_contests()
        self.apply_layout()
        self.set_interval(1.0, self.update_statusline)
        self.update_statusline()
        self.query_one("#contests", DataTable).focus()
        if read_secret(COOKIE_PATH):
            self.action_refresh()
        else:
            self.status("本地数据可离线使用 · 如需远程比赛请登录。")

    @staticmethod
    def rich_color(value: str | None, fallback: str = "white") -> str:
        """Textual's ansi_* tokens must be converted before Rich parses styles."""
        if not value or value == "ansi_default":
            return fallback
        if value.startswith("ansi_"):
            return value[5:]
        return value

    def apply_theme(self, requested: str) -> None:
        """Auto and wallpaper modes fall back cleanly to the terminal palette."""
        if requested in ("auto", "wallpaper"):
            theme, source = load_wallpaper_theme()
            if theme:
                self.register_theme(theme)
                self._wallpaper_source = source
                requested = theme.name
            else:
                self._wallpaper_source = ""
                requested = "ansi-dark"
        if requested not in self.available_themes:
            requested = "ansi-dark"
        self.theme = requested
        if self.is_mounted:
            self._update_pane_titles()
            if self.cases:
                self.filter_cases()
            self.update_statusline()

    def status(self, message: str) -> None:
        self._status_message = message
        self.update_title()
        self.update_statusline()

    def update_title(self) -> None:
        message = self._status_message or "ready"
        header = Text(" ◈ nowcoder / review", style="bold")
        header.append("  ·  ", style="dim")
        header.append(message[:max(12, self.size.width - 30)], style="dim")
        self.query_one("#titlebar", Static).update(header)

    def _update_pane_titles(self) -> None:
        if not self.is_mounted:
            return
        icons = ("󰒓", "󰀦", "󰆍") if self._nerd_font else ("◇", "◆", "▣")
        panes = [self.query_one(name) for name in ("#left", "#middle", "#right")]
        panes[0].border_title = f"{icons[0]} contests"
        panes[0].border_subtitle = f"{len(self.contests)} 场"
        level = self.FILTERS[self.filter_idx][1].lower()
        panes[1].border_title = f"{icons[1]} findings · {level}"
        index = 0
        if self.filtered_cases and self.current in self.filtered_cases:
            index = self.filtered_cases.index(self.current) + 1
        panes[1].border_subtitle = f"{index}/{len(self.filtered_cases)}"
        panes[2].border_title = f"{icons[2]} inspect"
        panes[2].border_subtitle = (
            f"{self.current.get('problem', '?')} · L3"
            if self.current else "code / diff"
        )
        self.query_one("#login-card").border_title = "󰌆  login / wechat" if self._nerd_font else "◈ login / wechat"

    def _mode(self) -> tuple[str, str]:
        if self.running:
            return ("COLLECT" if getattr(self, "_job_kind", "collect") == "collect" else "ANALYZE", "warning")
        if self.login_visible:
            return ("LOGIN", "secondary")
        if self.query_one("#command-row").display:
            return ("COMMAND", "accent")
        if self.query_one("#id-row").display or self.query_one("#search").display:
            return ("INSERT", "accent")
        return ("NORMAL", "success")

    def update_statusline(self) -> None:
        if not self.is_mounted:
            return
        self._spinner_tick += 1
        mode, semantic = self._mode()
        theme = self.current_theme
        color = self.rich_color(getattr(theme, semantic, None) or theme.accent, "green")
        bg = self.rich_color(theme.background, "black")
        separator = "" if self._nerd_font else "▌"
        label = ("◐◓◑◒"[self._spinner_tick % 4] + " ") if self.running else ""
        left = Text()
        left.append(f" {label}{mode} ", style=f"bold {bg} on {color}")
        left.append(f"{separator} ", style=f"{color}")
        cid = f"#{self.contest}" if self.contest is not None else "no contest"
        active = ("contests", "findings", "inspect")[self.active_pane]
        left.append(f" {cid} › {active}", style="bold")
        right_text = " · ".join(filter(None, [
            self._progress if self.running else "",
            self.theme,
            datetime.now().strftime("%H:%M"),
        ]))
        right = Text(" " + right_text + " ", style="dim")
        width = max(0, self.size.width)
        remaining = width - left.cell_len - right.cell_len
        if remaining < 1:
            right = Text(" " + datetime.now().strftime("%H:%M"), style="dim")
            remaining = width - left.cell_len - right.cell_len
        if remaining < 1:
            # Let Textual clip the line, rather than wrap to a second row.
            right = Text()
            remaining = max(0, width - left.cell_len)
        left.append(" " * remaining)
        left.append_text(right)
        self.query_one("#status", Static).update(left, layout=False)

    def _render_empty(self) -> None:
        empty = self.query_one("#empty", Static)
        findings = self.query_one("#findings", DataTable)
        if self.filtered_cases:
            findings.display = True
            empty.display = False
            return
        findings.display = False
        empty.display = True
        code_count = sum(int(c.get("codes", 0) or 0) for c in self.contests)
        login = "已保存 Cookie" if read_secret(COOKIE_PATH) else "未登录 / 可离线使用"
        logo = Text()
        for line in ("   ▄▄  ▄▄", "  █  ██  █  nowcoder", "   ▀▀  ▀▀   review"):
            logo.append(line + "\n", style=self.rich_color(self.current_theme.accent, "cyan"))
        logo.append("\n  contests  ", style="dim")
        logo.append(f"{len(self.contests)}\n")
        logo.append("  codes     ", style="dim")
        logo.append(f"{code_count}\n")
        logo.append("  session   ", style="dim")
        logo.append(login + "\n\n")
        if not self.contests:
            logo.append("  i 输入比赛 ID / l 登录\n  r 刷新在线比赛列表", style="dim")
        elif not self.cases:
            logo.append("  a 分析本地比赛\n  c 采集并分析", style="dim")
        else:
            logo.append("  没有匹配的线索\n  / 搜索 · f 切换过滤", style="dim")
        empty.update(logo)

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
        self._update_pane_titles()
        self._render_empty()

    def choose(self, cid):
        if cid == self.contest:
            return
        self.contest = cid
        self.report = {}
        self.cases = []
        self.current = None
        self.query_one("#findings", DataTable).clear()
        self.query_one("#evidence", Static).update(f"比赛 #{cid}\n按 a 分析本地数据 · 按 c 采集并分析")
        for name in ("#code-a", "#code-b", "#diff"):
            self.query_one(name, TextArea).load_text("")
        self.update_title()
        self._update_pane_titles()
        self._render_empty()
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
                table = self.query_one("#findings", DataTable)
                first = table.ordered_columns[0].key
                previous = self._selected_finding
                self._selected_finding = idx
                if previous is not None and previous != idx and previous < len(self.filtered_cases):
                    table.update_cell(str(previous), first, self._risk_cell(self.filtered_cases[previous], False))
                if 0 <= idx < len(self.filtered_cases):
                    table.update_cell(str(idx), first, self._risk_cell(self.filtered_cases[idx], True))
                    self.preview(self.filtered_cases[idx])
        except (TypeError, ValueError, IndexError):
            return

    def _risk_cell(self, case: dict, selected: bool = False) -> Text:
        verdict = case.get("verdict", "")
        severity = {
            "实锤": ("HIGH", self.current_theme.error),
            "中偏强": ("MED", self.current_theme.warning),
            "存疑": ("LOW", self.current_theme.foreground),
        }
        label, color = severity.get(verdict, ("—", self.current_theme.foreground))
        color = self.rich_color(color, "white")
        l3 = float(case.get("scores", {}).get("L3", 0) or 0)
        n = max(0, min(4, round(l3 * 4)))
        marker = Text("▎" if selected else " ")
        marker.stylize(self.rich_color(self.current_theme.accent, "cyan"))
        marker.append("● ", style=f"bold {color}" if label != "LOW" else "dim")
        marker.append(label + " ", style=f"{color}" if label != "LOW" else "dim")
        marker.append("▮" * n + "▯" * (4 - n), style=color)
        return marker

    def _review_cell(self, state: str) -> Text:
        style = {
            "已核查": self.rich_color(self.current_theme.success, "green"),
            "需进一步核实": "magenta",
            "无需跟进": "dim",
            "待复核": "dim",
        }.get(state, "dim")
        label = {
            "已核查": "✓ 已核查", "需进一步核实": "◆ 待核实",
            "无需跟进": "— 无需跟进", "待复核": "· 待复核",
        }.get(state, state)
        return Text(label, style=style)

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "search":
            self.filter_cases()

    def filter_cases(self):
        search = self.query_one("#search", Input).value.strip().casefold()
        level = self.FILTERS[self.filter_idx][0]
        self.filtered_cases = []
        for case in self.cases:
            state = self.store.status(case_key(self.contest, "contest", case))
            haystack = f"{' '.join(case['pair'])} {case['problem']} {case['verdict']}".casefold()
            if search and search not in haystack:
                continue
            if level == "high" and case["verdict"] not in ("实锤", "中偏强"):
                continue
            if level == "pending" and state != "待复核":
                continue
            if level == "reviewed" and state == "待复核":
                continue
            self.filtered_cases.append(case)
        table = self.query_one("#findings", DataTable)
        table.clear()
        self._selected_finding = None
        for i, case in enumerate(self.filtered_cases):
            state = self.store.status(case_key(self.contest, "contest", case))
            table.add_row(
                self._risk_cell(case, selected=i == 0),
                str(case["problem"]),
                " ↔ ".join(case["pair"]),
                f"{float(case.get('scores', {}).get('L3', 0)):>4.2f}",
                self._review_cell(state), key=str(i),
            )
        if self.filtered_cases:
            self._selected_finding = 0
            self.preview(self.filtered_cases[0])
        else:
            self.current = None
            self.query_one("#evidence", Static).update(
                "当前筛选条件下没有线索。" if self.cases else "尚未分析比赛 · a 分析 / c 采集")
        self._render_empty()
        self._update_pane_titles()

    def preview(self, case):
        self.current = case
        self._update_pane_titles()
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


    def apply_layout(self):
        width = self.size.width
        panes = [self.query_one("#left"), self.query_one("#middle"), self.query_one("#right")]
        if width < 80:
            for i, pane in enumerate(panes):
                pane.display = i == self.active_pane
                pane.styles.width = "100%"
        elif width < 116:
            if self.active_pane == 2:
                panes[0].display = False
                panes[1].display = True
                panes[2].display = True
                panes[1].styles.width = "45%"
                panes[2].styles.width = "55%"
            else:
                panes[0].display = True
                panes[1].display = True
                panes[2].display = False
                panes[0].styles.width = "38%"
                panes[1].styles.width = "62%"
        else:
            for pane in panes:
                pane.display = True
            panes[0].styles.width = "25%"
            panes[1].styles.width = "38%"
            panes[2].styles.width = "37%"

    def on_resize(self, event):
        self.apply_layout()

    def focus_pane(self, index):
        self.active_pane = index
        self.apply_layout()
        target = ("#contests", "#findings", "#code-a")[index]
        self.query_one(target).focus()

    def action_pane_1(self): self.focus_pane(0)
    def action_pane_2(self): self.focus_pane(1)
    def action_pane_3(self): self.focus_pane(2)

    def action_help(self):
        self.status("1/2/3 panes · j/k move · / search · f filter · : commands · a analyze · c collect · x/d review · l login · i ID · t theme · q quit")

    def action_search(self):
        if not self.login_visible:
            field = self.query_one("#search", Input)
            field.display = True
            field.focus()

    def action_filter(self):
        self.filter_idx = (self.filter_idx + 1) % len(self.FILTERS)
        self.query_one("#filterline", Static).update(
            f" {self.FILTERS[self.filter_idx][1]}  ·  f to filter")
        self.filter_cases()

    def action_command(self):
        if self.login_visible: return
        self.query_one("#command-row").display = True
        field = self.query_one("#command", Input)
        field.value = ""
        field.focus()

    def action_open_id(self):
        if self.login_visible: return
        self.query_one("#id-row").display = True
        field = self.query_one("#contest-input", Input)
        field.value = ""
        field.focus()

    def on_input_submitted(self, event: Input.Submitted):
        name = event.input.id
        if name == "contest-input":
            self.open_contest_id()
            self.query_one("#id-row").display = False
            self.focus_pane(0)
        elif name == "uid-input":
            self.save_uid()
        elif name == "command":
            cmd = event.input.value.strip()
            self.query_one("#command-row").display = False
            self.focus_pane(self.active_pane)
            self.execute_command(cmd)
        elif name == "search":
            self.query_one("#search").display = False
            self.focus_pane(1)

    def execute_command(self, command):
        pieces = command.split()
        if not pieces: return
        op = pieces[0].lower().lstrip(":")
        actions = {
            "help": self.action_help, "refresh": self.action_refresh,
            "collect": self.action_collect, "analyze": self.action_analyze,
            "rank": self.action_rank, "login": self.action_toggle_login,
            "scan": self.action_scan,
        }
        if op in actions:
            actions[op]()
        elif op in ("contest", "id") and len(pieces) == 2:
            self.query_one("#contest-input", Input).value = pieces[1]
            self.open_contest_id()
        elif op == "theme" and len(pieces) == 2:
            if pieces[1] in self.available_themes:
                self.theme = pieces[1]
                self.status(f"theme: {self.theme}")
            else:
                self.status(f"Unknown theme: {pieces[1]}")
        else:
            self.status(f"Unknown command: {command} · :help")

    def action_theme(self):
        themes = [name for name in self.THEMES if name in self.available_themes]
        if not themes: return
        try: index = (themes.index(self.theme) + 1) % len(themes)
        except ValueError: index = 0
        self.theme = themes[index]
        self.status(f"theme: {self.theme}")

    def action_scan(self):
        if not self.login_visible: self.action_toggle_login()
        self.start_login()

    def action_mark_reviewed(self):
        if not self.login_visible: self.mark("已核查")

    def action_mark_dismissed(self):
        if not self.login_visible: self.mark("无需跟进")

    def action_mark_investigate(self):
        if not self.login_visible: self.mark("需进一步核实")

    def action_toggle_login(self):
        self.login_visible = not self.login_visible
        self.query_one("#workspace").display = not self.login_visible
        self.query_one("#login-panel").display = self.login_visible
        if self.login_visible:
            self.query_one("#uid-input", Input).value = read_secret(UID_PATH)
            self.query_one("#uid-input", Input).focus()
        else:
            self.focus_pane(0)

    def action_back(self):
        if self.query_one("#command-row").display:
            self.query_one("#command-row").display = False
            self.focus_pane(self.active_pane)
        elif self.query_one("#id-row").display:
            self.query_one("#id-row").display = False
            self.focus_pane(0)
        elif self.login_visible:
            self.action_toggle_login()
        else:
            self.query_one("#search", Input).display = False
            self.focus_pane(self.active_pane)

    def open_contest_id(self):
        text = self.query_one("#contest-input", Input).value.strip()
        if not text.isdecimal() or int(text) <= 0:
            self.status("Enter a positive integer contest ID")
            return
        cid = int(text)
        if not any(c["contestId"] == cid for c in self.remote):
            self.remote.append({"contestId": cid, "contestName": f"比赛 #{cid}"})
        self.load_contests()
        self.choose(cid)
        self.status(f"Contest #{cid} · c collect · a analyze local data")

    def save_uid(self):
        value = self.query_one("#uid-input", Input).value.strip()
        if not value.isdecimal() or int(value) <= 0:
            self.query_one("#login-message", Static).update("UID must be a positive number")
            return
        try:
            write_secret(UID_PATH, value)
            self.query_one("#login-message", Static).update("UID saved")
            self.action_refresh()
        except Exception as e:
            self.query_one("#login-message", Static).update(f"Unable to save UID: {e}")

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
        self.update_title()
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
