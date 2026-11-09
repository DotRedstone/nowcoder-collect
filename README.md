# nowcoder-collect · 牛客训练赛审查工作台

**用途：** 辅助训练赛管理员采集本校内部比赛的代码、分析相似线索、阅读**全部选手的全部提交历史**、进行人工复核，并导出最终成绩榜。算法结论只是一条线索，不自动给任何人判定作弊。

## 启动（Python 3.11+）

采集器、检测器和旧版 curses 继续仅用 Python 标准库；新版界面采用 Textual。

\`\`\`fish
git fetch origin
git switch feat/hierarchical-review-workflow

python3 -m venv .venv
source .venv/bin/activate.fish
python -m pip install -r requirements-tui.txt
./run.sh
\`\`\`

如果分支已存在，用 \`git pull --ff-only origin feat/hierarchical-review-workflow\` 更新。Bash 的激活脚本是 \`.venv/bin/activate\`，Fish 要使用 \`.venv/bin/activate.fish\`。NixOS 建议通过 Nix 提供 Python 的虚拟环境，避免在系统 Python 中全局安装。

旧版依然可使用 \`./run.sh --legacy\`，不会覆盖原本的 \`tui.py\`。

## 完整审核流程

\`\`\`text
首页
 ├── 比赛列表 ──▶ 比赛总览
 │                ├── 线索 ──▶ 证据 / 两份代码 / Diff ──▶ 人员标记
 │                ├── 选手 ──▶ 选手详情 ──▶ 任意提交代码 ──▶ 历史版本
 │                ├── 题目 ──▶ 题目详情 ──▶ 不同选手代表代码 / 历史版本
 │                └── 最终排行榜 ──▶ 剔除 / 标记策略 ──▶ Markdown / CSV / HTML
 ├── 打开比赛 ID
 ├── 跨比赛待复核
 ├── 登录 / 账户
 └── 设置
\`\`\`

每层只有一个主焦点；顶部面包屑标示当前位置，Enter 进入、Esc/h 返回，不再常驻三栏。底部保留单行模式、当前对象、主题、时间和任务进度，\`?\` 打开上下文按键帮助。

**筛选比赛不等于判断管理者创建的比赛。**在线列表只读取该账户“已加入的比赛”，再与本地比赛合并，并用正则白/黑名单筛出需要审查的本校比赛。白名单空表示显示全部；黑名单优先。\`F\` 可以临时显示不命中的比赛，手动输入 ID 的比赛始终可打开。

**管理者隔离是检测输入级别的。**设置中按 UID 配置隐藏人员；分析时复制 SQLite 到短期临时文件并过滤这些人的提交，再调用原检测器。原数据库与原算法完全不动，检测缓存按名单/阈值组合区分；修改名单后需要重新分析。这些人员在选手页灰显、默认榜单中剔除，但管理员仍可查阅他们的代码。

**所有源码来自本地 SQLite。**选手页按人查看所有 WA/CE/AC 的提交，按题可翻各人的代表代码。代码页 \`n/p\` 走进入代码页时的名单，\`[\` / \`]\` 只走当前人当前题的历史版本，无需再访问牛客网页。

**人和线索是两种不同的审核记录。**线索可以标记“已核查 / 无需跟进 / 需进一步核实”，人员独立标为“正常 / 存疑 / 作弊者 / 未看”，并可附审核备注。任何自动相似度都不会自动将选手标为作弊者。

## 排行榜计算说明

采集器现在会额外保存一份**官方 submit-list 源数据的本地汇总快照**（含报名而零提交者和各题满分）。因此，采集成功的比赛可依据与现有 \`compute_rank\` 一致的 IOI 计分口径离线恢复排行榜：每题取最高分，按总分、AC、昵称排序；快照里带 UID，可以应用人工标记。

旧数据库如果没有这份快照，榜单退回 SQLite 提交记录推算：不包含零提交报名者，也无法可靠恢复题目原始满分；页面会明确标成“本地推算”，不能当成完整官方榜。

排行榜可分别对作弊者设置“剔除 / 标记”、存疑者设置“照常 / 标记 / 剔除”、管理者设置“剔除 / 标记”，\`e\` 导出当前策略下结果到 \`~/nowcoder-data/<比赛ID>/ranking.md|csv|html\`（或者设置的输出目录）。剔除的人不参与名次顺延；标记的人显示但不占名次。还有未处理线索/未核查人员时提示警告，不强制阻止导出。

## 快捷键

| 键 | 功能 |
|---|---|
| Enter / l | 进入当前选择 |
| Esc / h | 返回上一层 |
| j / k、↑ / ↓ | 移动 |
| g / G | 顶部 / 底部 |
| / | 当前层搜索 |
| f | 循环线索/选手筛选；比赛列表用 F 显示全部 |
| a / c | 分析本地比赛 / 采集并分析 |
| r | 刷新在线比赛列表 |
| m | 选择对象并给人手动标记，可附备注 |
| x / d / ! | 已核查 / 无需跟进 / 需进一步核实（线索） |
| 1 / 2 | 证据页进入 A / B 选手 |
| n / p | 下一份 / 上一份代码或线索 |
| [ / ] | 当前人同题历史代码版本 |
| e | 导出榜单，或编辑设置条目 |
| ← / → | 榜单策略、数值与枚举切换 |
| Ctrl+S | 保存设置 |
| Ctrl+X | 取消后台任务 |
| : | 命令模式（支持 \`:contest 123\`、\`:theme auto\`、\`:collect\`、\`:analyze\`） |
| ? | 当前层帮助 |
| q | 退出；任务运行时先取消任务 |

## 用户数据与配置

用户私有配置统一存储在 \`~/.dsh/nowcoder/\`，文件默认权限为 \`0600\`：

| 文件 | 内容 |
|---|---|
| \`cookie.txt\`、\`uid.txt\` | 登录态与当前用户 UID（不写入仓库） |
| \`nowcoder.db\` | 比赛及全部已采集提交代码 |
| \`settings.toml\` | 采集线程、路径、外观、榜单默认策略 |
| \`filters.toml\` | 比赛白名单 / 黑名单正则和匹配选项 |
| \`staff.toml\` | 管理人员 UID 与启用状态 |
| \`detector.toml\` | 用户检测阈值覆盖，绝不改 \`detector/config.toml\` |
| \`reviews.json\` | 线索审核状态 |
| \`people.json\` | \`比赛ID:UID\` 形式的人员人工标记、备注、时间 |
| \`reports/\` | 最近一次与当前管理名单/阈值相匹配的检测结果缓存 |
| \`ranking-source/\` | 采集时保存的官方同源榜单汇总，不包含 Cookie |

比赛筛选中的非法正则不会被保存；用户对设置的修改先作为草稿，\`Ctrl+S\` 保存，或在某分类内“恢复本类默认设置”。登录二维码只在终端显示，Cookie 值不在界面展示。

## 主题

保留 \`ansi-dark\`、Catppuccin、Nord、Gruvbox 等。支持自动读取 pywal 或 matugen JSON（\`rice_theme.py\`），可通过 \`NOWCODER_THEME_FILE\` 指向自定义生成路径。Nerd Font 可选：

\`\`\`fish
set -lx NOWCODER_THEME auto
set -lx NOWCODER_THEME_FILE ~/.cache/wal/colors.json
set -lx NOWCODER_NERD_FONT 1
./run.sh
\`\`\`

若不存在该配色文件会回退到 ANSI 配色。原有检测算法、输出 schema 和旧 curses 界面均保留。

### 可选代码语法高亮

默认仅安装纯 Python 依赖；代码页面支持在检测到 Tree-sitter 解析器时自动启用 C/C++、Python、Java 等语言高亮，缺失时仍可查看、复制和滚动完整源码。需要高亮时额外安装：

```fish
python -m pip install -r requirements-tui-syntax.txt
```

该可选方案使用含原生扩展的语法解析包；NixOS 环境若安装失败可继续使用无高亮的默认界面。题目详情页按 `g` 循环排序：UID / 首次 AC 时间 / 昵称 / 对应题目线索数。

## 测试

\`\`\`fish
python -m compileall -q review_tui.py review_domain.py review_settings.py tui_backend.py
python -m unittest discover -s tests -v
pushd detector
python -m unittest discover -s tests -v
popd
\`\`\`

目前这是基于 \`refactor/review-workspace\` 的 Draft PR，需要继续做无登录、真实本地 SQLite、真实扫码及长任务终端验收。**不要把检测等级或手动标记当成无需进一步程序即可处分学生的结论。**
