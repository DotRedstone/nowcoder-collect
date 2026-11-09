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

快速键：`Tab` 切焦点、`↑↓` 移动、`/` 搜索、`a` 本地分析、`c` 采集后分析、`r` 在线刷新、`Ctrl+T` 登录、`Ctrl+X` 取消任务、`?` 帮助、`q` 退出。界面按钮同样支持点击。

首次登录：点击“账户 / 扫码” → “开始扫码”并用微信确认；Cookie 存于 `~/.dsh/nowcoder/cookie.txt`。在线最近比赛列表还需要用户 UID，可在账户页面填写（从牛客个人主页地址获取），保存在 `~/.dsh/nowcoder/uid.txt`。请勿提交 Cookie、UID 或私人审查记录。

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
