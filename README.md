# nowcoder-collect · 牛客比赛代码审查工作台

采集牛客 ACM 比赛提交、执行确定性雷同检测，**辅助人工复核**。相似代码、时间关系或算法分级并不意味着作弊已被证明。

## 启动

需要 Python 3.11+。采集器、检测器和旧版 curses TUI 仍然只依赖标准库；新版终端界面使用 [Textual](https://textual.textualize.io/)。

```bash
python3 -m pip install -r requirements-tui.txt
./run.sh
```

在 NixOS 等不允许全局 pip 安装的环境，可使用虚拟环境：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-tui.txt
./run.sh
```

无 Textual 时仍可运行 `./run.sh --legacy` 进入原 curses 界面；检测 CLI 和 DSH skill 完全保留。

## 新工作区

- **左栏比赛**：同时显示 SQLite 本地比赛与在线最近参加的比赛。无需联网即可读取已采集比赛。顶部可直接输入比赛 ID 打开未参加的比赛。
- **中栏线索**：按参赛者、题号、检测等级搜索，按高优先级和人工复核状态筛选。
- **右栏证据**：同时查看三种相似度、稀有指纹、提交时间线，以及用于检测的两份代表代码、统一差异视图。
- **人工复核**：提供“已核查 / 无需跟进 / 需进一步核实”状态，独立保存在 `~/.dsh/nowcoder/reviews.json`，不会写回原比赛数据或改变检测算法结论。
- **后台任务**：采集和检测作为子进程执行，UI 持续响应；Ctrl+X 可请求取消当前采集/检测，任务失败会在状态栏说明。

快速键：`1/2/3` 切比赛、线索、审查窗格；`j/k` 或方向键移动；`/` 搜索、`f` 循环过滤、`:` 打开命令模式；`a` 离线分析、`c` 采集并分析、`r` 在线刷新；`i` 输入比赛 ID、`l` 登录、`s` 扫码；`x` 标记已核查、`d` 无需跟进、`!` 需进一步核实；`t` 切换主题、`Ctrl+X` 取消后台任务、`?` 帮助、`q` 退出。

界面采用紧凑的 Linux rice 风格：圆角窗格标题和副标题显示筛选结果数；焦点边框与强调色绑定；风险等级按红 / 黄 / 灰提示，复核状态单独着色。底部是单行 lualine 风格状态栏，包含 NORMAL/COMMAND/COLLECT 模式、当前比赛和窗格、主题、时间与采集进度。`?` 弹出完整按键说明，不常驻第二条快捷键栏。

### 主题、Nerd Font 与壁纸联动

默认 `auto`：优先读取 `~/.cache/wal/colors.json`（pywal），其次尝试 `~/.cache/matugen/colors.json`、`~/.config/matugen/colors.json`；都没有就回退到 `ansi-dark`。**matugen 没有固定的 JSON 输出位置**，所以如果你通过 matugen 模板生成 JSON，最可靠的是设置 `NOWCODER_THEME_FILE` 指向该文件。仅解析 JSON 色值，不运行外部脚本或 shell。

运行时每 4 秒检查一次配色文件是否变更（`auto`/ `wallpaper` 模式），更新壁纸后通常无需重启。可用 `:theme auto` 重新读取，或用 `:theme gruvbox` 固定主题（固定主题不会跟随壁纸）。`t` 在内置和已注册的壁纸主题间切换。

在 **Fish Shell** 中使用：

```fish
# 有 pywal 或 matugen JSON 时自动跟随
set -lx NOWCODER_THEME auto
set -lx NOWCODER_THEME_FILE ~/.cache/wal/colors.json

# Nerd Font 可用时启用 powerline glyph；缺字形就不设置
set -lx NOWCODER_NERD_FONT 1
./run.sh
```

无 Nerd Font 默认使用 `▌` 作为分隔符，避免乱码。预置主题也可用 `NOWCODER_THEME=nord ./run.sh`。其他命令包括 `:contest 143786`、`:analyze`、`:collect`。

窄屏为单窗格，使用 `1/2/3` 切换；中等宽度显示两个窗格，当前在审查窗格时显示“线索 + 审查”。

首次登录：按 `l` 打开登录页，按 `s` 扫码并用微信确认；Cookie 存于 `~/.dsh/nowcoder/cookie.txt`。在线最近比赛列表还需要用户 UID，可在账户页面填写（从牛客个人主页地址获取），保存在 `~/.dsh/nowcoder/uid.txt`。请勿提交 Cookie、UID 或私人审查记录。

数据库：`~/.dsh/nowcoder/nowcoder.db`；采集输出：`~/nowcoder-data/<比赛ID>/`。旧数据格式保持兼容。

## 检测与测试

```bash
python3 detector/main.py --contest 143786 --json-out report.json
(cd detector && python3 -m unittest discover -s tests -v)
python3 -m unittest discover -s tests -v
```

确定性检测器使用 L1 原文、L2 去空白、L3 词法归一化、稀有 n-gram、模板排除和提交时间线。详细算法、阈值和限制见 [detector/docs/design.md](detector/docs/design.md)。

## 目录

```text
review_tui.py         Textual 多面板审查工作区
tui_backend.py        本地比赛索引、代码查询、复核记录
tui.py                原版 curses TUI（--legacy）
login.py              微信扫码登录
skill/scripts/        牛客数据采集 CLI
detector/             确定性雷同检测器
browser-extension/    浏览器采集扩展（可选）
tests/                TUI 数据层测试
```

数据隐私：工具只读取牛客页面，不提交修改；代码和审查记录中可能包含他人隐私，请勿上传公共仓库。模型不能仅凭代码相似度直接认定抄袭或 AI 代做。

MIT License。
