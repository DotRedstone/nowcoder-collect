#!/usr/bin/env python3
"""Nowcoder: a local-first, one-level-at-a-time review TUI.

The UI uses Textual Screens, not a permanently visible three-pane dashboard.
No detector algorithms or detector JSON fields are modified.
"""
from __future__ import annotations

import copy
import difflib
import json
from importlib.util import find_spec
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Input, Static, TabbedContent, TabPane, TextArea

import login
from rice_theme import load_wallpaper_theme
from tui_backend import COOKIE_PATH, UID_PATH, ReviewStore, case_key, local_contests, merge_contests, pair_sources, read_secret, write_secret
from review_domain import (
    CONFIG_DIR, DB_PATH, DETECTOR_PATH, EXPORT_DIR, FILTERS_PATH, MARKS, MARK_TEXT,
    PEOPLE_PATH, REPORTS_DIR, SETTINGS_PATH, STAFF_PATH, DEFAULT_FILTERS, DEFAULT_SETTINGS,
    PeopleStore, all_submissions, contest_matches, detector_db, export_ranking, final_rank,
    hidden_uids, is_ac, load_filters, load_report, load_settings, load_staff, local_rank,
    people_for_contest, person_submissions, problem_submissions, problems_for_contest,
    rank_source_available,
    representative_for, representatives_by_problem, report_key, save_filters, save_report,
    save_settings, save_staff, validate_filters, validate_setting, SCHEMA, RANK_POLICY,
)
from review_settings import LIMITS, load as load_thresholds, save as save_thresholds

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
import nowcoder_extract as extractor  # noqa: E402

THEMES = ("auto", "ansi-dark", "catppuccin-mocha", "nord", "gruvbox", "textual-dark")
CATEGORIES = ("账户", "比赛筛选", "人员名单", "采集", "检测阈值", "外观", "快捷键", "关于")
SETTINGS_GROUPS = {
    "采集": ("threads", "collect_after", "auto_analyze", "db_path", "out_dir"),
    "外观": ("theme", "nerd_font", "density", "language", "key_hints"),
}
SETTING_NAMES = {
    "threads": "并发线程", "collect_after": "同时采集补题", "auto_analyze": "采集后检测",
    "db_path": "SQLite 路径", "out_dir": "输出目录", "theme": "主题",
    "nerd_font": "Nerd Font 图标", "density": "行距", "language": "界面语言",
    "key_hints": "显示按键提示",
}
KEY_HELP = {
    "全局": "j/k/↑↓ 移动 · Enter/l 进入 · Esc/h 返回 · g/G 顶/底 · / 搜索 · : 命令 · ? 帮助 · q 退出",
    "比赛": "Enter 打开 · c 采集并分析 · a 本地分析 · r 在线刷新 · F 规则外也显示",
    "线索": "Enter 证据 · f 过滤 · x 已核查 · d 无需跟进 · ! 需核实",
    "证据": "m 人员标记 · 1/2 看选手 · x/d/! 处理线索 · n/p 上/下一条",
    "代码": "n/p 列表上一/下一份 · 方括号键 此人此题的历史 · m 给人打标",
    "设置": "h/l/←→ 切换选项 · 空格 开关 · a 新增 · e 编辑 · d 删除 · Ctrl+S 保存",
    "榜单": "←→ 修改剔除/标记策略 · e 导出 Markdown/CSV/HTML",
}

class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "关闭", show=False),
                Binding("question_mark", "close", "关闭", show=False)]
    CSS = """
    HelpScreen { align: center middle; background: $background 70%; }
    #help { width: 86; max-width: 95%; height: auto; padding: 1 2; border: round $accent; background: $surface; }
    """
    def __init__(self, name: str):
        self.section = name
        super().__init__()

    def compose(self) -> ComposeResult:
        lines = ["nowcoder › 帮助", "", KEY_HELP["全局"]]
        for key, value in KEY_HELP.items():
            if key not in ("全局", self.section):
                continue
            if key != "全局":
                lines.extend(["", f"【{key}】", value])
        yield Static("\n".join(lines) + "\n\nEsc 返回", id="help")

    def action_close(self) -> None:
        self.dismiss()


class Page(Screen):
    """A self-contained navigable layer. Only the current layer receives focus."""
    CSS = """
    Page { layout: vertical; background: $background; color: $foreground; }
    #crumb { height: 1; padding: 0 2; color: $text-muted; }
    #frame {
        height: 1fr; padding: 0 1;
        margin: 0 1; border: round $panel; background: $background;
        border-title-color: $accent; border-subtitle-color: $text-muted;
    }
    #frame:focus-within { border: round $accent; }
    #overview { height: auto; max-height: 13; padding: 1 1; color: $foreground; }
    #list { height: 1fr; background: $background; color: $foreground; scrollbar-size: 1 1; }
    DataTable > .datatable--cursor { background: $accent 20%; text-style: none; }
    #source-pair, #single-code { display: none; height: 1fr; }
    #source-a, #source-b { height: 1fr; width: 1fr; background: $background; border: none; }
    #source-a, #source-b, #code { scrollbar-size: 1 1; }
    #code { height: 1fr; border: none; background: $background; }
    #search-row, #input-row, #command-row { display: none; height: 3; margin: 0 1; }
    Input { height: 3; background: $surface; }
    #qr { display: none; height: 1fr; text-align: center; padding: 1; overflow-y: auto; }
    #status { height: 1; background: $surface; color: $foreground; }
    """
    def __init__(self, kind: str, context: dict | None = None):
        self.kind = kind
        self.context = context or {}
        self.rows: list[dict] = []
        self.cursor = 0
        self.search = ""
        self.pending_input = ""
        self.qr_text = ""
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("nowcoder", id="crumb")
        with Vertical(id="frame"):
            yield Static("", id="overview")
            yield DataTable(id="list", cursor_type="row", cell_padding=0)
            with TabbedContent(id="source-pair"):
                with TabPane("源码 A ↔ B", id="tab-pair"):
                    with Horizontal():
                        yield TextArea("", id="source-a", read_only=True, show_line_numbers=True)
                        yield TextArea("", id="source-b", read_only=True, show_line_numbers=True)
                with TabPane("Diff", id="tab-diff"):
                    yield TextArea("", id="source-diff", read_only=True, show_line_numbers=True)
            yield TextArea("", id="code", read_only=True, show_line_numbers=True)
            yield Static("", id="qr")
        with Horizontal(id="search-row"):
            yield Input(placeholder="/ 搜索当前列表", id="search")
        with Horizontal(id="input-row"):
            yield Input(placeholder="输入并按 Enter 确认", id="value")
        with Horizontal(id="command-row"):
            yield Input(placeholder=":help / :contest 123 / :theme auto", id="command")
        yield Static("", id="status")

    def on_mount(self) -> None:
        self.app.render_page(self)

    def on_screen_resume(self) -> None:
        self.app.render_page(self)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "list":
            self.cursor = event.cursor_row
            self.app.update_statusline()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "list":
            self.app.action_enter()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.app.submit_input(self, event.input.id, event.value)

    def selected(self) -> dict | None:
        return self.rows[self.cursor] if 0 <= self.cursor < len(self.rows) else None

    def show_rows(self, columns: tuple[str, ...], rows: list[tuple[dict, tuple]]) -> None:
        table = self.query_one("#list", DataTable)
        table.cell_padding = 0 if self.app.settings.get("density", "紧凑") == "紧凑" else 1
        self.rows = [item for item, _ in rows]
        table.clear(columns=True)
        table.add_columns(*columns)
        for i, (_, cells) in enumerate(rows):
            table.add_row(*cells, key=str(i))
        self.cursor = min(self.cursor, max(0, len(self.rows) - 1))
        if self.rows:
            table.move_cursor(row=self.cursor)
        table.display = True
        self.query_one("#source-pair").display = False
        self.query_one("#code").display = False
        self.query_one("#qr").display = False

    @staticmethod
    def _code_syntax(widget: TextArea, raw_language: str) -> None:
        """Best-effort syntax coloring with optional textual[syntax] parsers."""
        if find_spec("tree_sitter") is None or find_spec("tree_sitter_languages") is None:
            return
        value = (raw_language or "").casefold()
        language = None
        if "python" in value or "pypy" in value: language = "python"
        elif "c++" in value or "cpp" in value or "g++" in value: language = "cpp"
        elif value in ("c", "gcc", "c11", "c99"): language = "c"
        elif "java" in value and "script" not in value: language = "java"
        elif "javascript" in value: language = "javascript"
        elif "rust" in value: language = "rust"
        try:
            widget.language = language
        except (ValueError, LookupError, RuntimeError):
            widget.language = None

    def show_source(self, left: str, right: str, diff: str,
                    language_a: str = "", language_b: str = "") -> None:
        self.query_one("#list").display = False
        self.query_one("#source-pair").display = True
        self.query_one("#code").display = False
        a, b = self.query_one("#source-a", TextArea), self.query_one("#source-b", TextArea)
        self._code_syntax(a, language_a)
        self._code_syntax(b, language_b)
        a.load_text(left)
        b.load_text(right)
        self.query_one("#source-diff", TextArea).load_text(diff)

    def show_code(self, source: str, raw_language: str = "") -> None:
        self.query_one("#list").display = False
        self.query_one("#source-pair").display = False
        widget = self.query_one("#code", TextArea)
        widget.display = True
        self._code_syntax(widget, raw_language)
        widget.load_text(source)

    def ask(self, action: str, placeholder: str, value: str = "") -> None:
        self.pending_input = action
        row = self.query_one("#input-row")
        row.display = True
        field = self.query_one("#value", Input)
        field.placeholder = placeholder
        field.value = str(value)
        field.focus()


class ReviewApp(App):
    TITLE = "牛客 · 审核工作台"
    CSS = "Screen { background: $background; }"
    BINDINGS = [
        Binding("q", "quit_safe", "退出", show=False),
        Binding("escape", "back", "返回", show=False),
        Binding("h", "back_or_adjust", "返回或调整", show=False),
        Binding("l", "enter_or_adjust", "进入或调整", show=False),
        # DataTable.RowSelected handles Enter; Input.Submitted handles forms.
        Binding("j", "down", "下移", show=False),
        Binding("k", "up", "上移", show=False),
        Binding("g", "group_or_top", "分组或顶部", show=False),
        Binding("shift+g", "bottom", "底部", show=False),
        Binding("slash", "search", "搜索", show=False),
        Binding("colon", "command", "命令", show=False),
        Binding("question_mark", "help", "帮助", show=False),
        Binding("f", "filter", "筛选", show=False),
        Binding("shift+f", "toggle_all", "全部", show=False),
        Binding("a", "analyze", "分析", show=False),
        Binding("c", "collect", "采集", show=False),
        Binding("r", "refresh", "刷新", show=False),
        Binding("m", "mark_person", "人员标记", show=False),
        Binding("x", "review_ok", "已核查", show=False),
        Binding("d", "review_dismiss", "无需跟进", show=False),
        Binding("exclamation_mark", "review_followup", "需核实", show=False),
        Binding("n", "next", "下一个", show=False),
        Binding("p", "previous", "上一个", show=False),
        Binding("left_square_bracket", "history_previous", "上一提交", show=False),
        Binding("right_square_bracket", "history_next", "下一提交", show=False),
        Binding("left", "option_previous", "上一选项", show=False),
        Binding("right", "option_next", "下一选项", show=False),
        Binding("space", "toggle", "切换", show=False),
        Binding("tab", "tab_key", "切换 Diff", show=False),
        Binding("e", "edit", "编辑", show=False),
        Binding("s", "scan", "扫码", show=False),
        Binding("ctrl+s", "save", "保存", show=False),
        Binding("ctrl+x", "cancel_task", "取消任务", show=False),
        Binding("t", "theme", "主题", show=False),
        Binding("1", "evidence_a", "选手 A", show=False),
        Binding("2", "evidence_b", "选手 B", show=False),
    ]
    def __init__(self) -> None:
        super().__init__()
        self.remote: list[dict] = []
        self.manual_ids: set[int] = set()
        self.reports: dict[int, dict] = {}
        self.trail = ["首页"]
        self._status = "本地数据就绪"
        self._task_mode = ""
        self._task_progress = ""
        self._task_contest = None
        self.proc: subprocess.Popen | None = None
        self.running = False
        self.cancel_requested = False
        self.spinner_tick = 0
        self.wallpaper_source = ""
        self.wallpaper_signature = None
        self.show_all_contests = False
        self.finding_filter = 0
        self.finding_group = 0
        self.people_filter = 0
        self.nerd_font = False
        self.login_running = False
        self.settings_dirty = False
        self.login_generation = 0
        self.error_at_start = ""
        self._people_readonly = False
        self._reviews_readonly = False
        self._load_user_data()

    def _load_user_data(self):
        try:
            self.settings = load_settings()
        except (OSError, ValueError) as e:
            self.settings = dict(DEFAULT_SETTINGS)
            self.error_at_start = str(e)
        try:
            self.filters = load_filters()
        except (OSError, ValueError) as e:
            self.filters = copy.deepcopy(DEFAULT_FILTERS)
            self.error_at_start += f" 筛选配置错误：{e}"
        try:
            self.staff = load_staff()
        except (OSError, ValueError) as e:
            self.staff = []
            self.error_at_start += f" 管理人员名单错误：{e}"
        try:
            self.people = PeopleStore()
        except (OSError, ValueError) as e:
            self.people = object.__new__(PeopleStore)
            self.people.path = PEOPLE_PATH
            self.people.data = {}
            self._people_readonly = True
            self.error_at_start += f" 人员标记损坏：{e}（只读，先修复原文件）"
        try:
            self.reviews = ReviewStore()
        except (OSError, ValueError) as e:
            self.reviews = object.__new__(ReviewStore)
            self.reviews.path = CONFIG_DIR / "reviews.json"
            self.reviews.data = {}
            self._reviews_readonly = True
            self.error_at_start += f" 线索记录损坏：{e}（只读，先修复原文件）"
        try:
            self.thresholds = load_thresholds()
        except (OSError, ValueError) as e:
            self.thresholds = load_thresholds(ROOT / "detector" / "config.toml")
            self.error_at_start += f" 阈值错误：{e}"
        self.settings_draft = copy.deepcopy(self.settings)
        self.filters_draft = copy.deepcopy(self.filters)
        self.staff_draft = copy.deepcopy(self.staff)
        self.thresholds_draft = copy.deepcopy(self.thresholds)
        self.nerd_font = bool(self.settings.get("nerd_font"))

    def on_mount(self) -> None:
        self.apply_theme(self.settings["theme"])
        self.push_screen(Page("home"))
        self.set_interval(1.0, self.update_statusline)
        self.set_interval(4.0, self.poll_wallpaper)
        if self.error_at_start:
            self.set_status(self.error_at_start)

    def apply_theme(self, choice: str) -> None:
        if choice in ("auto", "wallpaper"):
            theme, path = load_wallpaper_theme()
            if theme:
                self.register_theme(theme)
                self.wallpaper_source = path
                self.wallpaper_signature = self._wallpaper_stat(path)
                choice = "wallpaper"
            else:
                self.wallpaper_source = ""
                self.wallpaper_signature = None
                choice = "ansi-dark"
        else:
            self.wallpaper_source = ""
            self.wallpaper_signature = None
        if choice not in self.available_themes:
            choice = "ansi-dark"
        self.theme = choice

    @staticmethod
    def _wallpaper_stat(path: str) -> tuple[int, int] | None:
        try:
            stat = Path(path).stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def poll_wallpaper(self) -> None:
        if self.settings.get("theme") not in ("auto", "wallpaper"):
            return
        fresh, source = load_wallpaper_theme()
        if not fresh:
            return
        signature = self._wallpaper_stat(source)
        if source == self.wallpaper_source and signature == self.wallpaper_signature:
            return
        self.register_theme(fresh)
        self.wallpaper_source = source
        self.wallpaper_signature = signature
        if self.theme == "wallpaper":
            self.theme = "ansi-dark"
        self.theme = "wallpaper"
        self.set_status("壁纸主题已同步")
        page = self.current_page()
        if page:
            self.render_page(page)

    def current_page(self) -> Page | None:
        screen = self.screen
        return screen if isinstance(screen, Page) else None

    def push_page(self, kind: str, label: str, **context) -> None:
        self.trail.append(label)
        self.push_screen(Page(kind, context))

    def pop_page(self) -> None:
        if len(self.trail) > 1:
            self.trail.pop()
            self.pop_screen()

    def set_status(self, message: str) -> None:
        self._status = str(message)
        self.update_statusline()

    @staticmethod
    def _rich_color(value: str | None, fallback: str = "white") -> str:
        if not value or value == "ansi_default":
            return fallback
        return value[5:] if value.startswith("ansi_") else value

    def update_statusline(self) -> None:
        page = self.current_page()
        if not page:
            return
        self.spinner_tick += 1
        theme = self.current_theme
        mode = self._task_mode if self.running else ("COMMAND" if page.query_one("#command-row").display else
            "INSERT" if page.query_one("#input-row").display or page.query_one("#search-row").display else "NORMAL")
        color = self._rich_color(theme.warning if self.running else theme.success, "green")
        bg = self._rich_color(theme.background, "black")
        bar = Text()
        spinner = "◐◓◑◒"[self.spinner_tick % 4] + " " if self.running else ""
        bar.append(f" {spinner}{mode} ", style=f"bold {bg} on {color}")
        bar.append(" " if self.nerd_font else "▌ ", style=color)
        where = f"#{page.context.get('cid', '—')} › {self.trail[-1]}"
        bar.append(where[:max(16, self.size.width - 42)], style="bold")
        right = ""
        if self.running:
            label = self._task_progress
            stage = re.search(r"阶段\s+(\d+)/(\d+)", label)
            if stage:
                done, count = map(int, stage.groups())
                width = 10
                progress = min(width, max(0, round(done / max(count, 1) * width)))
                label = "━" * progress + "─" * (width - progress) + f" 阶段 {done}/{count}"
            right = f"#{self._task_contest} {label}  "
        right += f"{self.theme} · {datetime.now().strftime('%H:%M')}"
        gap = max(1, self.size.width - bar.cell_len - Text(right).cell_len - 2)
        bar.append(" " * gap)
        bar.append(right, style="dim")
        page.query_one("#status", Static).update(bar)
        hints = {
            "home": "Enter 进入   / 搜索   ? 帮助   q 退出",
            "contests": "Enter 打开   F 全部   c 采集   / 搜索",
            "contest": "Enter 进入   c 采集   a 分析   h 返回",
            "findings": "Enter 证据   f 筛选   / 搜索   h 返回",
            "evidence": "m 标记人员   1/2 选手   x 复核   h 返回",
            "people": "Enter 选手   m 标记   f 筛选   h 返回",
            "person": "Enter 源码   m 标记   h 返回",
            "problems": "Enter 题目   / 搜索   h 返回",
            "problem": "Enter 代码   h 返回",
            "code": "n/p 切人   方括号键 历史版本   m 标记",
            "ranking": "←→ 策略   e 导出   h 返回",
            "settings": "Enter 设置分类   h 返回",
            "settings_section": "←→ 切换   Ctrl+S 保存   h 返回",
            "account": "s 扫码   Enter 选择   h 返回",
        }
        if self.settings.get("key_hints", True):
            page.query_one("#crumb", Static).update(" › ".join(("nowcoder", *self.trail)) +
                "   ·   " + hints.get(page.kind, "Enter 选择 · h 返回"))
        else:
            page.query_one("#crumb", Static).update(" › ".join(("nowcoder", *self.trail)))

    def _contests(self) -> list[dict]:
        try:
            rows = merge_contests(local_contests(self.db()), self.remote)
            return [row for row in rows if contest_matches(row, self.filters)]
        except (OSError, ValueError, re.error) as error:
            self._status = f"比赛筛选失败：{error}"
            return []

    def db(self) -> Path:
        return Path(self.settings.get("db_path", str(DB_PATH))).expanduser()

    def staff_ids(self) -> set[int]:
        return hidden_uids(self.staff)

    def report_id(self) -> str:
        return report_key(self.staff_ids(), self.thresholds)

    def report_for(self, cid: int) -> dict:
        if cid not in self.reports:
            try:
                self.reports[cid] = load_report(cid, self.report_id()) or {}
            except (OSError, ValueError):
                self.reports[cid] = {}
        return self.reports[cid]

    def _paint(self, label: str, color: str | None = None) -> Text:
        text = Text("▎ ", style=self._rich_color(self.current_theme.accent, "cyan"))
        text.append(label, style=color or "")
        return text

    def _risk(self, verdict: str, l3: float = 0) -> Text:
        col = {"实锤": "red", "中偏强": "yellow", "存疑": "grey70", "排除": "dim"}.get(verdict, "dim")
        amount = max(0, min(5, round(l3 * 5)))
        return self._paint("● " + verdict + " " + "▮" * amount + "▯" * (5 - amount), col)

    def _mark(self, uid: int, cid: int) -> Text:
        if uid in self.staff_ids():
            return Text("管理", style="dim")
        mark = self.people.get(cid, uid).get("mark", "unreviewed")
        return Text(MARK_TEXT.get(mark, "未看"), style={
            "normal": "green", "suspect": "yellow", "cheater": "red"
        }.get(mark, "dim"))

    def _show(self, page: Page, title: str, summary: str, columns: tuple[str, ...], entries: list[tuple[dict, tuple]]) -> None:
        page.query_one("#frame").border_title = title
        page.query_one("#frame").border_subtitle = f"{len(entries)} 项"
        page.query_one("#overview", Static).update(summary)
        page.show_rows(columns, entries)
        self.update_statusline()
        page.query_one("#list", DataTable).focus()

    def render_page(self, page: Page) -> None:
        """Keep recoverable local database/config errors inside the active screen."""
        try:
            self._render_page(page)
        except (sqlite3.Error, OSError, ValueError, KeyError) as error:
            self._status = f"本地数据读取失败：{error}"
            page.query_one("#frame").border_title = "读取失败"
            page.query_one("#overview", Static).update(Text(
                f"无法读取当前页面数据：{error}\n原数据库未被修改。按 h 返回上一层，检查数据库/配置后重试。"))
            page.show_rows(("错误",), [])
            self.update_statusline()

    def _render_page(self, page: Page) -> None:
        kind, ctx = page.kind, page.context
        cid = ctx.get("cid")
        if kind == "home":
            filtered = self._contests()
            logo = Text("     ◈ nowcoder / review\n\n", style=self._rich_color(self.current_theme.accent))
            logo.append("本校训练赛代码审核\n", style="bold")
            logo.append(f"本地比赛：{len(local_contests(self.db()))}   规则命中：{len(filtered)}   ")
            logo.append("Cookie：已保存\n\n" if read_secret(COOKIE_PATH) else "Cookie：未登录\n\n")
            entries = [
                ({"action": "contests"}, (self._paint("比赛列表"), "查看与审核已采集比赛")),
                ({"action": "id"}, (self._paint("打开比赛 ID"), "支持不在已参加列表内的比赛")),
                ({"action": "pending"}, (self._paint("待复核"), "跨比赛线索与存疑人员")),
                ({"action": "account"}, (self._paint("登录 / 账户"), "微信扫码和 UID")),
                ({"action": "settings"}, (self._paint("设置"), "比赛规则、管理者、阈值与外观")),
            ]
            self._show(page, "首页", logo, ("入口", "说明"), entries)
        elif kind == "contests":
            all_rows = merge_contests(local_contests(self.db()), self.remote)
            entries = []
            for contest in all_rows:
                allowed = contest_matches(contest, self.filters)
                manual = int(contest["contest_id"]) in self.manual_ids
                if not (self.show_all_contests or allowed or manual):
                    continue
                if page.search and page.search.casefold() not in (
                    str(contest.get("name", "")) + str(contest["contest_id"])).casefold():
                    continue
                label = "规则外" if not allowed else ("本地" if contest.get("codes") else "未采集")
                label += " · 手动" if manual else ""
                timestamp = contest.get("start_time") or 0
                try:
                    timestamp = float(timestamp)
                    date = datetime.fromtimestamp(timestamp / (1000 if timestamp > 1e11 else 1)).strftime("%m-%d") if timestamp else "—"
                except (OverflowError, ValueError, TypeError, OSError):
                    date = "—"
                report = self.report_for(int(contest["contest_id"]))
                clues = report.get("results", [])
                summary = Counter(c.get("verdict", "") for c in clues)
                reviewed = sum(self.reviews.status(case_key(int(contest["contest_id"]), "contest", case)) != "待复核" for case in clues)
                progress = f"{reviewed}/{len(clues)}" if report.get("meta") else "—"
                label += " · 已分析" if report.get("meta") else ""
                entries.append(({"cid": contest["contest_id"], "name": contest.get("name", "")}, (
                    self._paint(contest.get("name") or f"比赛 #{contest['contest_id']}"),
                    str(contest["contest_id"]), date, str(contest.get("submissions", 0)),
                    label, f"{summary['实锤']}/{summary['中偏强']}/{summary['存疑']}" if clues else "—",
                    progress)))
            count = sum(contest_matches(c, self.filters) for c in all_rows)
            self._show(page, f"比赛列表 · 筛选 {count}/{len(all_rows)}",
                "F：仅显示符合规则 / 全部（手动打开的比赛始终保留）\n"
                "线索数量 H/M/L 分别对应实锤/中偏强/存疑。在线列表仅代表当前账户加入的比赛。",
                ("比赛", "ID", "日期", "提交", "状态", "H/M/L", "复核"), entries)
        elif kind == "contest":
            subs = all_submissions(cid, self.db())
            report = self.report_for(cid)
            counts = Counter(r.get("verdict") for r in report.get("results", []))
            active_people = len({int(s.get("student_id") or 0) for s in subs if int(s.get("student_id") or 0) not in self.staff_ids()})
            uids = {int(sub.get("student_id") or 0) for sub in subs} - self.staff_ids()
            uids.discard(0)
            people_counts = Counter(self.people.get(cid, uid).get("mark", "unreviewed") for uid in uids)
            summary = (f"参赛者 {active_people}   提交 {len(subs)}   检测比较 {report.get('meta', {}).get('pairs_compared', '未分析')} 对\n"
                       f"线索：实锤 {counts['实锤']} · 中偏强 {counts['中偏强']} · 存疑 {counts['存疑']}\n"
                       f"人工：正常 {people_counts['normal']} · 存疑 {people_counts['suspect']} · "
                       f"作弊者 {people_counts['cheater']} · 未看 {people_counts['unreviewed']}\n"
                       f"管理人员已从检测输入排除：{len(self.staff_ids())} 人\n")
            entries = [(dict(action=action), (self._paint(name), detail)) for name, action, detail in (
                ("线索", "findings", "逐条核验证据"),
                ("选手", "people", "所有参赛者与所有提交历史"),
                ("题目", "problems", "按题跨人浏览代码"),
                ("最终排行榜", "ranking", "标记策略、预览与导出"),
                ("采集并检测", "collect", "后台采集，不阻塞浏览"),
            )]
            self._show(page, f"比赛 #{cid} · 总览", summary, ("页面", "说明"), entries)
        elif kind == "findings":
            report = self.report_for(cid)
            entries = []
            raw_cases = list(report.get("results", []))
            if self.finding_group:
                raw_cases.sort(key=lambda case: (case.get("problem", ""), case.get("verdict", "")))
            for case in raw_cases:
                if self.staff_ids().intersection(int(uid) for uid in case.get("student_ids", [])):
                    continue
                state = self.reviews.status(case_key(cid, "contest", case))
                if self.finding_filter == 1 and case.get("verdict") not in ("实锤", "中偏强"):
                    continue
                if self.finding_filter == 2 and state != "待复核":
                    continue
                if self.finding_filter == 3 and state == "待复核":
                    continue
                if page.search and page.search.casefold() not in (
                    " ".join(case.get("pair", [])) + case.get("problem", "")).casefold():
                    continue
                entries.append(({"case": case}, (
                    self._risk(case["verdict"], case.get("scores", {}).get("L3", 0)),
                    str(case["problem"]), " ↔ ".join(case["pair"]),
                    f"{case['scores'].get('L2', 0):.2f}/{case['scores'].get('L3', 0):.2f}",
                    state)))
            level = ("全部", "高优先级", "未处理", "已处理")[self.finding_filter]
            group_name = "按题" if self.finding_group else "按等级"
            self._show(page, f"雷同线索 · {level} · {group_name}",
                "相似度只是审查线索，不能据此直接认定作弊。\nf 切换过滤，Enter 查看证据，x/d/! 记录处理状态。",
                ("等级", "题", "选手对", "L2/L3", "复核"), entries)
        elif kind == "evidence":
            case = ctx["case"]
            page.query_one("#frame").border_title = f"证据 · {case['problem']} · {' ↔ '.join(case['pair'])}"
            page.query_one("#frame").border_subtitle = "Tab 切 Diff · 1/2 看人"
            scores = case.get("scores", {})
            rare = case.get("rare", {})
            timeline = case.get("timeline") or {}
            summary = (
                f"{case['verdict']} · 人工复核 {self.reviews.status(case_key(cid, 'contest', case))}\n"
                f"L1 {scores.get('L1', 0):.3f}   L2 {scores.get('L2', 0):.3f}   L3 {scores.get('L3', 0):.3f}\n"
                f"MD5 去空白相同：{('是' if scores.get('md5_ws_equal') else '否')} · 稀有指纹 {rare.get('shared', 0)} / {rare.get('score', 0):.3f}\n"
            f"样例：{'、'.join(rare.get('samples', [])[:3])[:110] or '暂无'}\n"
            f"模板比例 {case.get('template_ratio', 0):.3f}"
                f"   Crowd {case.get('crowd_count', 0)}\n"
                f"AC 时间差 {timeline.get('delta_seconds', '未知')} 秒   首提交差 {timeline.get('first_submit_delta', '未知')} 秒\n"
            f"先 AC：{timeline.get('who_first') or '未知'}   方向成立 {timeline.get('direction_valid', '未知')}\n"
            f"后交者 AC 前失败 {timeline.get('later_failures_before_ac', 0)} 次 · "
            f"{'、'.join(timeline.get('error_types', [])) or '—'}\n"
                "x 已核查 · d 无需跟进 · ! 需核实 · m 给人标记"
            )
            page.query_one("#overview", Static).update(summary)
            try:
                a, b = pair_sources(self.db(), cid, "contest", case)
                diff = "".join(difflib.unified_diff(
                    a["code"].splitlines(True), b["code"].splitlines(True),
                    fromfile=f"{a['student']} #{a.get('submission_id')}",
                    tofile=f"{b['student']} #{b.get('submission_id')}"))
                page.show_source(a.get("code", ""), b.get("code", ""), diff or "文本完全相同", a.get("language", ""), b.get("language", ""))
            except (OSError, ValueError, KeyError) as e:
                page.show_source("", "", "")
                self.set_status(f"证据源码不可用：{e}")
        elif kind in ("people", "person", "problems", "problem", "ranking", "pending"):
            self.render_lists(page)
        elif kind == "code":
            self.render_code(page)
        elif kind == "mark":
            choices = [(uid, MARK_TEXT[mark], mark) for uid in ctx["targets"] for mark in ("normal", "suspect", "cheater", "unreviewed")]
            if len(ctx["targets"]) == 2:
                choices += [(0, MARK_TEXT[mark], mark) for mark in ("normal", "suspect", "cheater", "unreviewed")]
            entries = [({"uid": uid, "mark": mark}, (
                self._paint("双方" if uid == 0 else f"UID {uid}"), title)) for uid, title, mark in choices]
            self._show(page, "给人标记", "人工定性会进入最终榜单，请核实证据后操作。\nEnter 选择标记，下一步可输入备注。",
                       ("对象", "标记"), entries)
        elif kind == "account":
            entries = [({"action": action}, (self._paint(title), detail)) for title, action, detail in (
                ("微信扫码 / 重新扫码", "scan", "不显示或输出 Cookie"),
                ("设置用户 UID", "uid", "从牛客主页获取数字 UID"),
                ("退出登录", "logout", "删除本地 Cookie 文件"),
            )]
            self._show(page, "登录 / 账户",
                f"Cookie：{'已保存' if read_secret(COOKIE_PATH) else '未登录'}\n"
                f"UID：{read_secret(UID_PATH) or '未设置'}\n"
                "按 s 扫码登录；二维码只在本机终端展示。",
                ("操作", "说明"), entries)
        elif kind == "settings":
            entries = [({"section": label}, (self._paint(label), "Enter 查看 / 修改")) for label in CATEGORIES]
            self._show(page, "设置", "设置只写入 ~/.dsh/nowcoder，不修改仓库默认配置。\n修改后 Ctrl+S 保存，Esc 离开未保存项会确认。", ("分类", "操作"), entries)
        elif kind == "settings_section":
            self.render_settings(page)
        elif kind == "confirm":
            self._show(page, "确认离开", "设置存在未保存修改：请选择保存、放弃或继续编辑。",
                       ("操作",), [({"action": x}, (self._paint(x),)) for x in ("保存并离开", "放弃修改", "继续编辑")])
        self.update_statusline()

    def render_lists(self, page: Page) -> None:
        kind, ctx, cid = page.kind, page.context, page.context.get("cid")
        if kind == "people":
            items = people_for_contest(cid, self.db(), self.staff_ids())
            entries = []
            for person in items:
                mark = self.people.get(cid, person["uid"]).get("mark", "unreviewed")
                if self.people_filter == 1 and mark != "unreviewed": continue
                if self.people_filter == 2 and mark != "suspect": continue
                if self.people_filter == 3 and mark != "cheater": continue
                if self.people_filter == 4 and not any(person["uid"] in x.get("student_ids", []) for x in self.report_for(cid).get("results", [])): continue
                if page.search and page.search.casefold() not in (person["name"] + str(person["uid"])).casefold(): continue
                entries.append(({"uid": person["uid"], "name": person["name"]}, (
                    str(person.get("original_rank", "—")),
                    self._paint(person["name"], "dim" if person["staff"] else ""),
                    str(person["uid"]), str(person["ac"]), f"{person['total']:.2f}",
                    self._mark(person["uid"], cid))))
            self._show(page, "选手 · " + ("全部", "未看", "存疑", "作弊者", "有线索")[self.people_filter],
                "含所有已采集参赛者；管理人员灰显排在末尾。Enter 看全部提交，m 给人打标。",
                ("原榜", "选手", "UID", "AC", "得分", "标记"), entries)
        elif kind == "person":
            uid = ctx["uid"]
            subs = person_submissions(cid, uid, self.db())
            by_problem = {}
            for sub in subs:
                by_problem.setdefault((sub["problem_index"], sub["phase"]), []).append(sub)
            representatives = {rep["submission_id"] for group in by_problem.values() if (rep := representative_for(group))}
            entries = []
            for index, sub in enumerate(subs):
                if page.search and page.search.casefold() not in (str(sub.get("problem_index")) + str(sub.get("verdict"))).casefold(): continue
                dt = datetime.fromtimestamp((sub.get("submit_time") or 0)/1000).strftime("%m-%d %H:%M") if sub.get("submit_time") else "—"
                entries.append(({"index": index, "submission": sub}, (
                    self._paint("★ " if sub["submission_id"] in representatives else "  "),
                    str(sub.get("problem_index") or "?"), str(sub.get("verdict") or "—"),
                    f"{float(sub.get('score') or 0):.2f}", dt, str(sub.get("language") or ""),
                    str(sub.get("loc") or len((sub.get("code") or "").splitlines())))))
            case_count = sum(uid in r.get("student_ids", []) for r in self.report_for(cid).get("results", []))
            self._show(page, f"选手 · {ctx['name']}",
                f"UID {uid} · 标记 {self.people.get(cid, uid).get('mark', 'unreviewed')} · 相关线索 {case_count}\n"
                f"备注：{self.people.get(cid, uid).get('note', '—')}\n"
                "★ 检测代表提交 · 所有 WA/CE/AC 历史版本均可查看，按 m 标记此人。",
                ("代表", "题", "结果", "得分", "提交时间", "语言", "行数"), entries)
        elif kind == "problems":
            items = problems_for_contest(cid, self.db())
            entries = [({"problem": item["problem"]}, (
                self._paint(item["problem"]), item["title"], str(item["ac"]), str(item["submissions"])))
                for item in items if not page.search or page.search.casefold() in (item["problem"] + item["title"]).casefold()]
            self._show(page, "题目", "Enter 打开题目，按人浏览代表代码，再用 方括号键 查看同一人的历史提交。",
                       ("题号", "题目", "AC 人数", "提交"), entries)
        elif kind == "problem":
            problem = ctx["problem"]
            all_rows = problem_submissions(cid, problem, self.db())
            reps = representatives_by_problem(all_rows)
            sort = ctx.setdefault("sort", 0)
            sort_names = ("UID", "AC 时间", "姓名", "线索数量")
            if sort == 1:
                first_ac = {}
                for sub in all_rows:
                    if is_ac(sub.get("verdict")):
                        uid = int(sub["student_id"])
                        first_ac[uid] = min(first_ac.get(uid, float("inf")), sub.get("submit_time") or float("inf"))
                reps.sort(key=lambda sub: (first_ac.get(int(sub["student_id"]), float("inf")), sub.get("student") or ""))
            elif sort == 2:
                reps.sort(key=lambda sub: (sub.get("student") or "", int(sub["student_id"])))
            elif sort == 3:
                suspicious = Counter(uid for row in self.report_for(cid).get("results", [])
                                     for uid in row.get("student_ids", []) if row.get("problem") == problem)
                reps.sort(key=lambda sub: (-suspicious[int(sub["student_id"])], sub.get("student") or ""))
            entries = [({"index": i, "submission": sub}, (
                self._paint(sub.get("student") or str(sub["student_id"])),
                str(sub["student_id"]), str(sub.get("verdict") or ""), f"{float(sub.get('score') or 0):.2f}",
                self._mark(int(sub["student_id"]), cid))) for i, sub in enumerate(reps)]
            ctx["sequence"] = reps
            self._show(page, f"{problem} 题 · 代表提交 · {sort_names[sort]}",
                       f"共 {len(all_rows)} 次提交，{len(reps)} 位选手。按 g 切换排序。\n"
                       "Enter 浏览代码；之后 n/p 同题换人，方括号切该人的历史版本。",
                       ("选手", "UID", "结果", "得分", "标记"), entries)
        elif kind == "ranking":
            rank = local_rank(cid, self.db())
            opts = ctx.setdefault("options", {
                "cheater": self.settings["rank_cheater"], "suspect": self.settings["rank_suspect"],
                "staff": self.settings["rank_staff"], "format": self.settings["rank_format"],
            })
            rows = final_rank(rank, self.people, cid, self.staff_ids(),
                              cheater=opts["cheater"], suspect=opts["suspect"], staff_policy=opts["staff"])
            ctx["rank_rows"] = rows
            entries = []
            for key, title in (("cheater", "作弊者"), ("suspect", "存疑者"), ("staff", "管理者"), ("format", "导出格式")):
                entries.append(({"option": key}, (self._paint(title), f"‹ {opts[key]} ›", "")))
            entries.append(({"action": "export"}, (self._paint("导出最终排行榜"), "按 e 导出", "")))
            for row in rows:
                entries.append(({"rank": row}, (str(row["rank"]), row["name"],
                    f"UID {row['uid']} · AC {row['ac']} · {row['total']:.2f} · {row['mark']}")))
            unanswered = sum(self.reviews.status(case_key(cid, "contest", r)) == "待复核"
                for r in self.report_for(cid).get("results", []))
            unreviewed = sum(not self.people.get(cid, p["uid"]).get("mark") or
                self.people.get(cid, p["uid"]).get("mark") == "unreviewed" for p in rank if p["uid"] not in self.staff_ids())
            official_source = rank_source_available(cid, self.db())
            self._show(page, "最终排行榜 · " + ("官方同源快照" if official_source else "本地推算"),
                f"⚠ 未处理线索 {unanswered} 条，未审人员 {unreviewed} 人；不阻止导出。\n"
                + ("成绩使用采集时保存的官方 submit-list 源数据；仍须人工复核。\n"
                   if official_source else
                   "⚠ 缺少官方报名零提交者与满分元数据，当前是本地推算，可能与官方榜不同。\n")
                + "在前四个选项上按 ←/→ 调整；按 e 导出。",
                ("名次/选项", "姓名/设置", "得分/说明"), entries)
        elif kind == "pending":
            entries = []
            for filename in sorted(REPORTS_DIR.glob("*.json")) if REPORTS_DIR.is_dir() else []:
                try:
                    cid_i = int(filename.stem)
                    raw = json.loads(filename.read_text(encoding="utf-8"))
                    for case in raw.get("report", {}).get("results", []):
                        if self.staff_ids().intersection(int(uid) for uid in case.get("student_ids", [])):
                            continue
                        if self.reviews.status(case_key(cid_i, "contest", case)) == "待复核":
                            entries.append(({"cid": cid_i, "case": case}, (
                                self._risk(case["verdict"], case.get("scores", {}).get("L3", 0)),
                                f"#{cid_i}", " ↔ ".join(case["pair"]))))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            for key, value in self.people.data.items():
                if isinstance(value, dict) and value.get("mark") == "suspect":
                    entries.append(({"person_key": key}, (self._paint("存疑人员", "yellow"), key, value.get("note", "")[:36])))
            self._show(page, "跨比赛待复核",
                       "读取本机已有的检测报告缓存与手动人员标记。\n没有分析过的比赛不会出现在这里。",
                       ("类型", "比赛", "对象"), entries)
        else:
            self.set_status("暂不支持的页面")

    def render_code(self, page: Page) -> None:
        seq = page.context.get("sequence", [])
        index = page.context.get("index", 0)
        if not 0 <= index < len(seq):
            self.set_status("没有可查看的提交")
            return
        sub = page.context.get("history_selected") or seq[index]
        page.context["submission"] = sub
        cid = page.context["cid"]
        page.query_one("#frame").border_title = f"{sub.get('student', '')} · {sub.get('problem_index', '?')} · #{sub['submission_id']}"
        page.query_one("#frame").border_subtitle = f"{index+1}/{len(seq)} · n/p · 历史版本"
        ms = sub.get("submit_time")
        dt = datetime.fromtimestamp(ms/1000).strftime("%Y-%m-%d %H:%M:%S") if ms else "未知"
        page.query_one("#overview", Static).update(
            f"UID {sub.get('student_id')} · {sub.get('verdict', '未知')} · {sub.get('language', '')}"
            f" · 得分 {sub.get('score', 0)} · {dt}\n"
            f"标记：{self.people.get(cid, int(sub.get('student_id') or 0)).get('mark', 'unreviewed')}"
            "   n/p 前后代码 · 方括号切换历史 · m 给人标记")
        page.show_code(sub.get("code") or "（本次提交无本地源码）", sub.get("language") or "")
        self.update_statusline()

    def render_settings(self, page: Page) -> None:
        section = page.context["section"]
        entries = []
        if section in SETTINGS_GROUPS:
            for key in SETTINGS_GROUPS[section]:
                value = self.settings_draft[key]
                pretty = f"[{'✓' if value else ' '}]" if isinstance(value, bool) else f"‹ {value} ›"
                entries.append(({"setting": key}, (self._paint(SETTING_NAMES[key]), pretty, "●" if value != self.settings[key] else "")))
        elif section == "检测阈值":
            for key, value in self.thresholds_draft.items():
                entries.append(({"threshold": key}, (self._paint(key), f"‹ {value} ›",
                    "●" if value != self.thresholds[key] else "")))
        elif section == "比赛筛选":
            entries += [({"filter_option": "match_field"}, (self._paint("匹配字段"), f"‹ {self.filters_draft['match_field']} ›", "")),
                        ({"filter_option": "case_sensitive"}, (self._paint("大小写敏感"), f"[{'✓' if self.filters_draft['case_sensitive'] else ' '}]", ""))]
            for group, title in (("whitelist", "白名单"), ("blacklist", "黑名单")):
                for index, rule in enumerate(self.filters_draft[group]):
                    entries.append(({"filter_group": group, "filter_index": index},
                                    (self._paint(f"{title} #{index+1}"), rule["pattern"],
                                     "[✓]" if rule.get("enabled", True) else "[ ]")))
            try:
                validate_filters(self.filters_draft)
                contests = merge_contests(local_contests(self.db()), self.remote)
                match = [c.get("name") or str(c["contest_id"]) for c in contests if contest_matches(c, self.filters_draft)]
                excluded = [c.get("name") or str(c["contest_id"]) for c in contests if not contest_matches(c, self.filters_draft)]
                hint = (f"实时预览：命中 {len(match)}/{len(contests)} 场\n"
                        f"命中：{'、'.join(match[:4]) or '—'}\n"
                        f"排除：{'、'.join(excluded[:4]) or '—'}")
            except re.error as e:
                hint = f"正则错误：{e}"
            except ValueError as e:
                hint = f"配置错误：{e}"
        elif section == "人员名单":
            for i, row in enumerate(self.staff_draft):
                entries.append(({"staff_index": i}, (self._paint(str(row["uid"])),
                    row.get("name", ""), "[✓] 管理" if row.get("enabled", True) else "[ ] 已禁用")))
        elif section == "账户":
            entries = [({"goto": "account"}, (self._paint("登录 / 账户"), "管理扫码和 UID", ""))]
        elif section == "快捷键":
            entries = [({"noop": 1}, (self._paint(key), text[:80], "")) for key, text in KEY_HELP.items()]
        elif section == "关于":
            entries = [({"noop": 1}, (self._paint(label), value, "")) for label, value in (
                ("版本", "分层工作流 v2"), ("数据库", str(self.db())),
                ("用户配置", str(SETTINGS_PATH)), ("人员标记", str(PEOPLE_PATH)),
                ("检测器", "算法与输出 schema 保持原样"))]
        if section != "比赛筛选":
            hint = "←/→ 切换 · Enter 编辑 · Ctrl+S 保存 · Esc 返回"
        if section == "人员名单":
            hint = "a 新增 UID · e 编辑 · d 删除 · 空格启用/停用 · Ctrl+S 保存"
        if section == "比赛筛选":
            hint += "\na 新增正则（格式：白 正则 或 黑 正则） · e 编辑 · d 删除 · 空格切换 · Ctrl+S 保存"
        if section in ("采集", "外观", "检测阈值", "比赛筛选", "人员名单"):
            entries.append(({"action": "defaults"}, (self._paint("恢复本类默认设置"), "Enter 确认修改（Ctrl+S 保存）", "")))
        self._show(page, section + (" · 未保存" if self.settings_dirty else ""),
                   hint, ("设置", "值", "状态"), entries)

    def action_enter(self) -> None:
        page = self.current_page()
        if not page: return
        item = page.selected()
        if not item: return
        kind, ctx = page.kind, page.context
        cid = ctx.get("cid")
        if kind == "home":
            action = item["action"]
            if action == "id": page.ask("contest_id", "比赛数字 ID")
            else:
                name = {"contests": "比赛列表", "pending": "待复核", "account": "账户", "settings": "设置"}[action]
                self.push_page(action, name)
        elif kind == "contests":
            self.push_page("contest", str(item.get("name") or item["cid"]), cid=item["cid"])
        elif kind == "contest":
            action = item["action"]
            if action == "collect":
                self.launch(cid, True)
            else:
                self.push_page(action, {"findings": "线索", "people": "选手", "problems": "题目", "ranking": "排行榜"}[action], cid=cid)
        elif kind == "findings":
            self.push_page("evidence", item["case"]["problem"] + " 证据", cid=cid, case=item["case"])
        elif kind == "people":
            self.push_page("person", item["name"], cid=cid, uid=item["uid"], name=item["name"])
        elif kind == "person":
            subs = person_submissions(cid, ctx["uid"], self.db())
            self.push_page("code", "源码", cid=cid, sequence=subs, index=item["index"])
        elif kind == "problems":
            self.push_page("problem", item["problem"], cid=cid, problem=item["problem"])
        elif kind == "problem":
            self.push_page("code", "源码", cid=cid, sequence=ctx["sequence"], index=item["index"])
        elif kind == "pending":
            if "case" in item:
                self.push_page("evidence", "待复核证据", cid=item["cid"], case=item["case"])
            elif "person_key" in item:
                ci, uid = map(int, item["person_key"].split(":"))
                people = people_for_contest(ci, self.db())
                name = next((r["name"] for r in people if r["uid"] == uid), str(uid))
                self.push_page("person", name, cid=ci, uid=uid, name=name)
        elif kind == "ranking":
            if "option" in item:
                self.adjust_option(1)
            elif item.get("action") == "export":
                self.action_export()
        elif kind == "account":
            if item["action"] == "scan": self.action_scan()
            elif item["action"] == "uid": page.ask("uid", "牛客 UID", read_secret(UID_PATH))
            elif item["action"] == "logout":
                try:
                    COOKIE_PATH.unlink(missing_ok=True)
                    self.set_status("已退出登录，本地 Cookie 已删除")
                    self.render_page(page)
                except OSError as e: self.set_status(f"删除 Cookie 失败：{e}")
        elif kind == "settings":
            self.push_page("settings_section", item["section"], section=item["section"])
        elif kind == "settings_section":
            if item.get("goto"):
                self.push_page("account", "账户")
            elif item.get("action") == "defaults":
                self.reset_category(ctx["section"])
                self.render_page(page)
            else:
                self.edit_setting(item, 1, click=True)
        elif kind == "mark":
            targets = ctx["targets"] if item["uid"] == 0 else [item["uid"]]
            ctx["chosen"] = (targets, item["mark"])
            page.ask("mark_note", "标记备注（可留空）", "")
        elif kind == "confirm":
            action = item["action"]
            if action == "保存并离开":
                if self.save_all(): self.pop_page(); self.pop_page()
            elif action == "放弃修改":
                self.reset_drafts()
                self.pop_page(); self.pop_page()
            else:
                self.pop_page()

    def action_back_or_adjust(self):
        page = self.current_page()
        if page and page.kind == "settings_section":
            self.adjust_option(-1)
        else:
            self.action_back()

    def action_enter_or_adjust(self):
        page = self.current_page()
        if page and page.kind == "settings_section":
            self.adjust_option(1)
        else:
            self.action_enter()

    def action_group_or_top(self):
        page = self.current_page()
        if page and page.kind == "findings":
            self.finding_group = 1 - self.finding_group
            self.render_page(page)
        elif page and page.kind == "problem":
            page.context["sort"] = (page.context.get("sort", 0) + 1) % 4
            self.render_page(page)
        else:
            self.action_top()

    def action_tab_key(self):
        page = self.current_page()
        if page and page.kind == "evidence":
            tabs = page.query_one("#source-pair", TabbedContent)
            tabs.active = "tab-diff" if tabs.active == "tab-pair" else "tab-pair"
        else:
            self.focus_next()

    def action_back(self) -> None:
        page = self.current_page()
        if not page:
            return
        for name in ("#input-row", "#search-row", "#command-row"):
            if page.query_one(name).display:
                page.query_one(name).display = False
                page.query_one("#list", DataTable).focus()
                self.update_statusline()
                return
        if self.settings_dirty and page.kind in ("settings", "settings_section"):
            self.push_page("confirm", "确认离开")
            return
        self.pop_page()

    def action_quit_safe(self) -> None:
        if self.running:
            self.set_status("任务运行中。Ctrl+X 请求取消，任务结束后再退出。")
            return
        self.exit()

    def action_down(self):
        page = self.current_page()
        if page and not isinstance(page.focused, Input):
            table = page.query_one("#list", DataTable)
            if table.display: table.action_cursor_down()

    def action_up(self):
        page = self.current_page()
        if page and not isinstance(page.focused, Input):
            table = page.query_one("#list", DataTable)
            if table.display: table.action_cursor_up()

    def action_top(self):
        page = self.current_page()
        if page and page.rows:
            page.query_one("#list", DataTable).move_cursor(row=0)

    def action_bottom(self):
        page = self.current_page()
        if page and page.rows:
            page.query_one("#list", DataTable).move_cursor(row=len(page.rows)-1)

    def action_search(self):
        page = self.current_page()
        if page and page.query_one("#list").display:
            page.query_one("#search-row").display = True
            field = page.query_one("#search", Input)
            field.value = page.search
            field.focus()
            self.update_statusline()

    def action_command(self):
        page = self.current_page()
        if page:
            page.query_one("#command-row").display = True
            page.query_one("#command", Input).focus()
            self.update_statusline()

    def submit_input(self, page: Page, field: str, value: str) -> None:
        if field == "search":
            page.search = value.strip()
            page.query_one("#search-row").display = False
            self.render_page(page)
        elif field == "command":
            page.query_one("#command-row").display = False
            self.command(value.strip())
        elif field == "value":
            page.query_one("#input-row").display = False
            self.handle_input(page, page.pending_input, value.strip())
        self.update_statusline()

    def command(self, value):
        parts = value.lstrip(":").split()
        if not parts: return
        op = parts[0].lower()
        if op in ("help", "?"): self.action_help()
        elif op == "contest" and len(parts) == 2: self.open_contest(parts[1])
        elif op == "theme" and len(parts) == 2:
            self.apply_theme(parts[1])
            self.set_status(f"主题：{self.theme}")
            page = self.current_page()
            if page: self.render_page(page)
        elif op == "collect": self.action_collect()
        elif op == "analyze": self.action_analyze()
        elif op == "refresh": self.action_refresh()
        else: self.set_status(f"未知命令：{value}")

    def open_contest(self, value: str) -> None:
        if not value.isdecimal() or int(value) <= 0:
            self.set_status("比赛 ID 必须是正整数")
            return
        cid = int(value)
        self.manual_ids.add(cid)
        self.remote.append({"contestId": cid, "contestName": f"比赛 #{cid}"})
        self.push_page("contest", f"#{cid}", cid=cid)

    def handle_input(self, page: Page, action: str, value: str):
        try:
            if action == "contest_id":
                self.open_contest(value)
            elif action == "uid":
                if not value.isdecimal() or int(value) <= 0:
                    raise ValueError("UID 必须是正整数")
                write_secret(UID_PATH, value)
                self.set_status("UID 已保存")
                self.render_page(page)
            elif action == "mark_note":
                targets, mark = page.context["chosen"]
                if self._people_readonly:
                    raise ValueError("人员标记文件损坏，必须修复原文件才能写入")
                for uid in targets:
                    self.people.mark(page.context["cid"], uid, mark, value)
                self.pop_page()
                active = self.current_page()
                if active: self.render_page(active)
                self.set_status(f"人员标记已保存：{MARK_TEXT[mark]} · {len(targets)} 人")
            elif action == "filter_add":
                group, pattern = value.split(" ", 1)
                bucket = {"白": "whitelist", "黑": "blacklist"}.get(group)
                if not bucket: raise ValueError("格式：白 正则 或 黑 正则")
                new = copy.deepcopy(self.filters_draft)
                new[bucket].append({"pattern": pattern, "enabled": True})
                validate_filters(new)
                self.filters_draft = new
                self.settings_dirty = True
                self.render_page(page)
            elif action == "filter_edit":
                item = page.context["edit_item"]
                new = copy.deepcopy(self.filters_draft)
                new[item["filter_group"]][item["filter_index"]]["pattern"] = value
                validate_filters(new)
                self.filters_draft = new
                self.settings_dirty = True
                self.render_page(page)
            elif action == "staff_add":
                parts = value.split(maxsplit=1)
                uid = int(parts[0])
                if uid <= 0 or any(p["uid"] == uid for p in self.staff_draft):
                    raise ValueError("UID 无效或重复")
                self.staff_draft.append({"uid": uid, "name": parts[1] if len(parts) > 1 else "", "enabled": True})
                self.settings_dirty = True
                self.render_page(page)
            elif action == "staff_edit":
                item = page.context["edit_item"]
                self.staff_draft[item["staff_index"]]["name"] = value
                self.settings_dirty = True
                self.render_page(page)
            elif action == "setting_edit":
                key = page.context["edit_item"]["setting"]
                kind = SCHEMA[key][0]
                val = int(value) if kind == "int" else value
                validate_setting(key, val)
                self.settings_draft[key] = val
                self.settings_dirty = True
                self.render_page(page)
            elif action == "threshold_edit":
                key = page.context["edit_item"]["threshold"]
                t = LIMITS[key][0]
                number = t(value)
                from review_settings import check
                self.thresholds_draft[key] = check(key, number)
                self.settings_dirty = True
                self.render_page(page)
        except (OSError, ValueError, IndexError, KeyError, re.error) as e:
            self.set_status(f"输入无效：{e}")

    def action_filter(self):
        page = self.current_page()
        if not page: return
        if page.kind == "findings": self.finding_filter = (self.finding_filter + 1) % 4
        elif page.kind == "people": self.people_filter = (self.people_filter + 1) % 5
        else: return
        self.render_page(page)

    def action_toggle_all(self):
        page = self.current_page()
        if page and page.kind == "contests":
            self.show_all_contests = not self.show_all_contests
            self.render_page(page)

    def action_review_ok(self): self.review_case("已核查")
    def action_review_dismiss(self):
        page = self.current_page()
        if page and page.kind == "settings_section":
            self.action_delete()
        else:
            self.review_case("无需跟进")
    def action_review_followup(self): self.review_case("需进一步核实")

    def review_case(self, status: str) -> None:
        page = self.current_page()
        if not page: return
        if page.kind == "evidence":
            case = page.context["case"]
        elif page.kind == "findings" and page.selected():
            case = page.selected()["case"]
        else:
            return
        cid = page.context["cid"]
        if self._reviews_readonly:
            self.set_status("线索复核记录损坏，必须先修复原文件")
            return
        self.reviews.set(case_key(cid, "contest", case), status=status)
        self.set_status(f"复核状态已保存：{status}")
        if page.kind == "evidence":
            if status != "需进一步核实":
                self.navigate_finding(1, only_pending=True)
            else:
                self.render_page(page)
        else:
            self.render_page(page)

    def action_evidence_a(self):
        self.person_from_evidence(0)

    def action_evidence_b(self):
        self.person_from_evidence(1)

    def person_from_evidence(self, index: int):
        page = self.current_page()
        if not page or page.kind != "evidence": return
        case = page.context["case"]
        uid = int(case["student_ids"][index])
        name = case["pair"][index]
        self.push_page("person", name, cid=page.context["cid"], uid=uid, name=name)

    def action_mark_person(self):
        page = self.current_page()
        if not page: return
        ctx = page.context
        if page.kind == "evidence":
            targets = list(map(int, ctx["case"]["student_ids"]))
        elif page.kind == "person":
            targets = [ctx["uid"]]
        elif page.kind == "people" and page.selected():
            targets = [page.selected()["uid"]]
        elif page.kind == "code":
            targets = [int(ctx["submission"]["student_id"])]
        else:
            return
        self.push_page("mark", "人员标记", cid=ctx["cid"], targets=targets)

    def action_next(self):
        page = self.current_page()
        if page and page.kind == "evidence": self.navigate_finding(1)
        elif page and page.kind == "code": self.move_code(1)

    def action_previous(self):
        page = self.current_page()
        if page and page.kind == "evidence": self.navigate_finding(-1)
        elif page and page.kind == "code": self.move_code(-1)

    def navigate_finding(self, delta, only_pending=False):
        page = self.current_page()
        if not page: return
        cases = self.report_for(page.context["cid"]).get("results", [])
        if not cases: return
        try: index = next(i for i, item in enumerate(cases) if item is page.context["case"])
        except StopIteration:
            try: index = cases.index(page.context["case"])
            except ValueError: return
        for step in range(1, len(cases) + 1):
            nxt = cases[(index + delta * step) % len(cases)]
            if self.staff_ids().intersection(nxt.get("student_ids", [])): continue
            if only_pending and self.reviews.status(case_key(page.context["cid"], "contest", nxt)) != "待复核": continue
            page.context["case"] = nxt
            self.render_page(page)
            return
        self.set_status("没有其他待处理线索")

    def move_code(self, delta):
        page = self.current_page()
        if page and page.kind == "code":
            seq = page.context["sequence"]
            if seq:
                page.context["index"] = (page.context["index"] + delta) % len(seq)
                page.context.pop("history_selected", None)
                self.render_page(page)

    def action_history_previous(self): self.move_history(-1)
    def action_history_next(self): self.move_history(1)

    def move_history(self, delta):
        page = self.current_page()
        if not page or page.kind != "code": return
        sub = page.context["submission"]
        history = [s for s in person_submissions(page.context["cid"], int(sub["student_id"]), self.db())
                   if s["problem_index"] == sub["problem_index"]]
        if not history: return
        position = next((i for i, row in enumerate(history) if row["submission_id"] == sub["submission_id"]), 0)
        page.context["history_selected"] = history[(position + delta) % len(history)]
        self.render_page(page)

    def action_option_previous(self): self.adjust_option(-1)
    def action_option_next(self): self.adjust_option(1)
    def action_toggle(self):
        page = self.current_page()
        if page and page.kind == "settings_section": self.edit_setting(page.selected(), 1)

    def adjust_option(self, amount):
        page = self.current_page()
        if not page: return
        if page.kind == "ranking":
            item = page.selected()
            if not item or "option" not in item: return
            key = item["option"]
            opts = RANK_POLICY[key]
            current = page.context["options"][key]
            page.context["options"][key] = opts[(opts.index(current) + amount) % len(opts)]
            self.render_page(page)
        elif page.kind == "settings_section":
            self.edit_setting(page.selected(), amount)

    def action_edit(self):
        page = self.current_page()
        if page and page.kind == "settings_section": self.edit_setting(page.selected(), 0, click=True)
        elif page and page.kind == "ranking": self.action_export()

    def edit_setting(self, item, direction=1, click=False):
        page = self.current_page()
        if not page or not item: return
        section = page.context["section"]
        if "setting" in item:
            key = item["setting"]
            kind = SCHEMA[key][0]
            current = self.settings_draft[key]
            if kind == "bool": self.settings_draft[key] = not current
            elif kind == "enum":
                if key == "theme": opts = THEMES
                else: opts = SCHEMA[key][1:]
                self.settings_draft[key] = opts[(opts.index(current) + (direction or 1)) % len(opts)]
                if key == "theme": self.apply_theme(self.settings_draft[key])
            elif kind == "int" and not click:
                self.settings_draft[key] = max(SCHEMA[key][1], min(SCHEMA[key][2], current + direction))
            else:
                page.context["edit_item"] = item
                page.ask("setting_edit", SETTING_NAMES[key], str(current))
                return
        elif "threshold" in item:
            key = item["threshold"]
            t, lo, hi, step = LIMITS[key]
            if click:
                page.context["edit_item"] = item
                page.ask("threshold_edit", key, str(self.thresholds_draft[key]))
                return
            self.thresholds_draft[key] = t(max(lo, min(hi, round(self.thresholds_draft[key] + step * direction, 4))))
        elif "filter_option" in item:
            key = item["filter_option"]
            if key == "case_sensitive": self.filters_draft[key] = not self.filters_draft[key]
            else: self.filters_draft[key] = ("比赛名+ID" if self.filters_draft[key] == "比赛名" else "比赛名")
        elif "filter_group" in item:
            if click:
                page.context["edit_item"] = item
                page.ask("filter_edit", "正则表达式", self.filters_draft[item["filter_group"]][item["filter_index"]]["pattern"])
                return
            rule = self.filters_draft[item["filter_group"]][item["filter_index"]]
            rule["enabled"] = not rule.get("enabled", True)
        elif "staff_index" in item:
            if click:
                page.context["edit_item"] = item
                page.ask("staff_edit", "管理者备注昵称", self.staff_draft[item["staff_index"]]["name"])
                return
            rule = self.staff_draft[item["staff_index"]]
            rule["enabled"] = not rule.get("enabled", True)
        else: return
        self.settings_dirty = True
        self.render_page(page)

    def action_add(self):
        page = self.current_page()
        if page and page.kind == "settings_section":
            if page.context["section"] == "比赛筛选": page.ask("filter_add", "白 正则表达式 / 黑 正则表达式")
            elif page.context["section"] == "人员名单": page.ask("staff_add", "UID 可选昵称")

    def action_delete(self):
        page = self.current_page()
        if page and page.kind == "settings_section":
            item = page.selected()
            if not item: return
            if "filter_group" in item:
                del self.filters_draft[item["filter_group"]][item["filter_index"]]
            elif "staff_index" in item:
                del self.staff_draft[item["staff_index"]]
            else: return
            self.settings_dirty = True
            self.render_page(page)
        elif page and page.kind == "findings":
            self.review_case("无需跟进")

    def save_all(self):
        if self.running:
            self.set_status("后台任务运行中，暂不允许变更并保存检测配置；请先完成或取消任务。")
            return False
        try:
            validate_filters(self.filters_draft)
            # No change to bundled detector/config.toml.
            save_settings(self.settings_draft)
            save_filters(self.filters_draft)
            save_staff(self.staff_draft)
            save_thresholds(self.thresholds_draft)
            staff_changed = self.staff != self.staff_draft or self.thresholds != self.thresholds_draft
            self.settings = copy.deepcopy(self.settings_draft)
            self.filters = copy.deepcopy(self.filters_draft)
            self.staff = copy.deepcopy(self.staff_draft)
            self.thresholds = copy.deepcopy(self.thresholds_draft)
            self.nerd_font = bool(self.settings["nerd_font"])
            self.settings_dirty = False
            if staff_changed: self.reports.clear()
            self.set_status("设置已保存。人员名单/检测阈值变更需重新分析比赛。")
            return True
        except (OSError, ValueError, re.error) as e:
            self.set_status(f"保存失败：{e}")
            return False

    def reset_category(self, section: str) -> None:
        from review_settings import flatten, defaults
        if section in SETTINGS_GROUPS:
            for key in SETTINGS_GROUPS[section]:
                self.settings_draft[key] = copy.deepcopy(DEFAULT_SETTINGS[key])
        elif section == "比赛筛选":
            self.filters_draft = copy.deepcopy(DEFAULT_FILTERS)
        elif section == "人员名单":
            self.staff_draft = []
        elif section == "检测阈值":
            self.thresholds_draft = flatten(defaults())
        self.settings_dirty = True
        self.set_status(f"{section} 已恢复默认（尚未保存）")

    def reset_drafts(self):
        self.settings_draft = copy.deepcopy(self.settings)
        self.filters_draft = copy.deepcopy(self.filters)
        self.staff_draft = copy.deepcopy(self.staff)
        self.thresholds_draft = copy.deepcopy(self.thresholds)
        self.settings_dirty = False

    def action_save(self):
        page = self.current_page()
        if page and page.kind in ("settings", "settings_section"):
            if self.save_all(): self.render_page(page)

    def action_export(self):
        page = self.current_page()
        if not page or page.kind != "ranking": return
        opts = page.context["options"]
        try:
            destination = export_ranking(page.context["cid"], page.context["rank_rows"], opts["format"],
                                         Path(self.settings["out_dir"]).expanduser())
            self.set_status(f"榜单已导出：{destination}")
        except (OSError, ValueError) as e:
            self.set_status(f"导出失败：{e}")

    def action_help(self):
        page = self.current_page()
        if page:
            section = "榜单" if page.kind == "ranking" else "设置" if page.kind.startswith("settings") else (
                "线索" if page.kind == "findings" else "证据" if page.kind == "evidence" else
                "代码" if page.kind == "code" else "比赛")
            self.push_screen(HelpScreen(section))

    def action_theme(self):
        index = THEMES.index(self.settings["theme"]) if self.settings["theme"] in THEMES else 0
        value = THEMES[(index + 1) % len(THEMES)]
        self.apply_theme(value)
        self.settings["theme"] = value
        self.settings_draft["theme"] = value
        self.set_status("主题预览：" + value)
        page = self.current_page()
        if page: self.render_page(page)

    def action_analyze(self):
        page = self.current_page()
        if page and page.kind == "settings_section":
            self.action_add()
        elif page and page.context.get("cid"):
            self.launch(page.context["cid"], False)

    def action_collect(self):
        page = self.current_page()
        if page and page.context.get("cid"):
            self.launch(page.context["cid"], True)

    def action_refresh(self):
        if self.running:
            self.set_status("采集或检测任务进行中")
            return
        cookie, uid = read_secret(COOKIE_PATH), read_secret(UID_PATH)
        if not cookie or not uid:
            self.set_status("在线刷新需要 Cookie 和 UID；本地比赛无需登录")
            return
        self.set_status("正在刷新已加入比赛列表…")
        self.refresh_worker(cookie, uid)

    @work(thread=True, group="refresh", exclusive=True)
    def refresh_worker(self, cookie: str, uid: str):
        try:
            result = extractor.list_recent(cookie, uid)
            self.call_from_thread(self.refresh_done, result, None)
        except Exception as e:
            self.call_from_thread(self.refresh_done, [], str(e))

    def refresh_done(self, rows, error):
        if error: self.set_status("刷新失败（离线比赛仍可浏览）：" + error)
        else:
            self.remote = [x for x in rows if x.get("contestId")]
            self.set_status(f"已更新 {len(self.remote)} 场已加入比赛")
            page = self.current_page()
            if page and page.kind in ("home", "contests"): self.render_page(page)

    def action_scan(self):
        page = self.current_page()
        if not page or page.kind != "account" or self.login_running: return
        self.login_running = True
        self.login_generation += 1
        qr = page.query_one("#qr", Static)
        qr.display = True
        qr.update("正在获取二维码…")
        self.scan_worker(self.login_generation)

    @work(thread=True, group="login", exclusive=True)
    def scan_worker(self, generation: int):
        def show_qr(text: str): self.call_from_thread(self.scan_update, generation, text)
        def show_status(msg: str): self.call_from_thread(self.set_status, msg)
        try:
            cookie = login.login_flow(render_cb=show_qr, status_cb=show_status)
            write_secret(COOKIE_PATH, cookie)
            self.call_from_thread(self.scan_done, generation, "登录成功")
        except Exception as e:
            self.call_from_thread(self.scan_done, generation, "登录失败：" + str(e))

    def scan_update(self, generation, data):
        if self.login_generation != generation: return
        page = self.current_page()
        if page and page.kind == "account":
            page.query_one("#qr", Static).update(data)

    def scan_done(self, generation, message):
        if generation != self.login_generation: return
        self.login_running = False
        self.set_status(message)
        page = self.current_page()
        if page and page.kind == "account": self.render_page(page)

    def launch(self, cid: int, collect: bool):
        if self.running:
            self.set_status("任务已在运行 · Ctrl+X 取消")
            return
        if collect and not read_secret(COOKIE_PATH):
            self.set_status("采集需要登录")
            return
        if not collect and not self.db().exists():
            self.set_status("本地尚无数据库，请先采集")
            return
        self.running = True
        self.cancel_requested = False
        self._task_mode = "COLLECT" if collect else "ANALYZE"
        self._task_contest = cid
        self._task_progress = "启动中"
        self.set_status(f"比赛 #{cid} 已提交后台任务，仍可继续浏览")
        self.job_worker(cid, collect, self.staff_ids(), self.db(), self.settings.copy())

    def action_cancel_task(self):
        if not self.running: return
        self.cancel_requested = True
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        self.set_status("已请求取消后台任务")

    def _run_process(self, args: list[str], progress=False) -> str:
        process = subprocess.Popen(args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.proc = process
        if progress:
            # Extractor prints progress to stderr; stdout is a small JSON summary.
            for line in process.stderr:
                if self.cancel_requested:
                    process.terminate()
                    break
                match = re.search(r"\[(\d+)/(\d+)\]", line)
                if match:
                    done, total = map(int, match.groups())
                    if total:
                        self.call_from_thread(self.progress, f"阶段 {done}/{total}")
            stdout = process.stdout.read()
            process.wait()
            stderr = ""
        else:
            stdout, stderr = process.communicate()
        self.proc = None
        if self.cancel_requested:
            raise InterruptedError("任务已取消")
        if process.returncode:
            raise RuntimeError((stderr or stdout)[-400:] or f"子进程退出码 {process.returncode}")
        return stdout

    def progress(self, label: str):
        self._task_progress = label
        self.update_statusline()

    @work(thread=True, group="jobs", exclusive=True)
    def job_worker(self, cid: int, collect: bool, excluded: set[int], db: Path, settings: dict):
        try:
            if collect:
                stdout = self._run_process([
                    sys.executable, str(ROOT / "skill" / "scripts" / "nowcoder_extract.py"),
                    "--contest", str(cid), "--phase", "all" if settings["collect_after"] else "contest",
                    "--threads", str(settings["threads"]), "--db", str(db),
                    "--out", str(Path(settings["out_dir"]).expanduser())
                ], progress=True)
                data = json.loads(stdout)
                if not data.get("ok"):
                    raise RuntimeError("采集器未返回成功状态")
                if not settings["auto_analyze"]:
                    self.call_from_thread(self.job_done, cid, None, None)
                    return
            self.call_from_thread(self.progress, "确定性检测中")
            with detector_db(db, excluded) as working_db:
                output = self._run_process([
                    sys.executable, str(ROOT / "detector" / "main.py"),
                    "--contest", str(cid), "--phase", "contest", "--db", str(working_db),
                    "--config", str(DETECTOR_PATH if DETECTOR_PATH.is_file() else ROOT / "detector" / "config.toml")
                ])
            self.call_from_thread(self.job_done, cid, json.loads(output), None)
        except Exception as e:
            self.call_from_thread(self.job_done, cid, None, str(e))

    def job_done(self, cid: int, report: dict | None, error: str | None):
        self.running = False
        self.proc = None
        self._task_progress = ""
        self._task_mode = ""
        self._task_contest = None
        self.cancel_requested = False
        if error:
            self.set_status(f"任务未完成：{error}")
            return
        if report:
            self.reports[cid] = report
            try:
                save_report(cid, report, self.report_id())
            except OSError as e:
                self.set_status(f"检测成功但缓存保存失败：{e}")
                return
        page = self.current_page()
        if page and page.context.get("cid") == cid:
            self.render_page(page)
        self.set_status(f"比赛 #{cid} {'检测完成' if report else '采集完成'} · 可继续人工复核")


if __name__ == "__main__":
    ReviewApp().run()
