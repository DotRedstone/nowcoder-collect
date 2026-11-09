# nowcoder-collect · 牛客代码收集与雷同检测

一个完整的 ACM 训练队工具：**登录 → 爬取比赛全部提交 → 去 LLM 化确定性雷同检测 → 结果/榜单**。用于训练队管理与代码审查（防 AI 代做 / 防抄袭）。

## 核心组件（主体）

| 组件 | 位置 | 作用 |
| --- | --- | --- |
| **爬取器** | `skill/scripts/nowcoder_extract.py` | 登录态抓取比赛全部提交 + 榜单，落 SQLite（多线程，纯标准库） |
| **检测器** | `detector/` | 去 LLM 化确定性雷同检测：L1/L2/L3 归一化 + 稀有 n-gram + 时间线 + 规则分级 |
| **TUI 面板** | `tui.py` | 一体化 curses 面板，一条命令跑完整个流程 |

## 附加项（可选）

| 组件 | 位置 | 作用 |
| --- | --- | --- |
| DSH skill | `skill/` | 给 [DSH](https://github.com/DeepSeekAI/dsh) agent 的技能封装（爬取 + 榜单） |
| 浏览器插件 | `browser-extension/` | Chrome/Edge 网页端采集（独立可用） |

## 快速开始

```bash
git clone https://github.com/DotRedstone/nowcoder-collect.git
cd nowcoder-collect
python3 tui.py            # 一体化面板：登录 → 选比赛 → 爬取 → 分析 → 结果/榜单
python3 tui.py --check    # 非交互：校验登录并列出比赛
```

首次使用在面板里「更新 Cookie」粘贴牛客登录态（`~/.dsh/nowcoder/cookie.txt`）即可。

## 目录结构

```
nowcoder-collect/
├── tui.py                        # ★ TUI 面板（主入口）
├── detector/                     # ★ 去 LLM 化确定性雷同检测器
│   ├── main.py                   #   python main.py --contest 143786
│   ├── config.toml               #   阈值配置
│   ├── detectors/                #   preprocess/normalize/similarity/template/timeline/classify
│   ├── docs/design.md            #   内核设计文档（含参考文献）
│   └── tests/                    #   单元 + 回归测试（22 个）
├── skill/                        # 附加：DSH skill（爬取 + 榜单）
│   ├── SKILL.md
│   └── scripts/nowcoder_extract.py
├── browser-extension/            # 附加：浏览器插件
├── install.sh                    # 附加：一键安装 skill
├── LICENSE
└── README.md
```

## 附加：安装 DSH skill

（可选）把 `skill/` 软链为 DSH skill，供 DSH agent 调用「导出比赛代码 / 查榜单」：```bash
git clone https://github.com/DotRedstone/nowcoder-collect.git
cd nowcoder-collect
./install.sh    # 软链到 $DSH_HOME/skills/nowcoder-collect，skill-filesystem 自动发现
```

`install.sh` 只是 `ln -s <repo>/skill ~/.dsh/skills/nowcoder-collect`，也可手动：

```bash
ln -s "$(pwd)/skill" ~/.dsh/skills/nowcoder-collect
```

### 浏览器插件（可选）

1. 打开 `chrome://extensions/`（Edge 为 `edge://extensions/`）
2. 开启「开发者模式」→「加载已解压的扩展程序」→ 选择 `browser-extension/` 目录

## 爬取器 CLI（skill/scripts/nowcoder_extract.py）

（TUI 内部也是调它，可单独用）

```bash
cd <repo>/skill

# 列出最近参加的比赛
python3 scripts/nowcoder_extract.py --recent --uid <你的数字uid>

# 提取某场比赛「比赛期间」代码（默认，推荐用于作弊审查）
python3 scripts/nowcoder_extract.py --contest 143786 --out ./nowcoder-data

# 含补题的完整数据 / 只取补题
python3 scripts/nowcoder_extract.py --contest 143786 --phase all
python3 scripts/nowcoder_extract.py --contest 143786 --phase after

# 查 SQLite 库
python3 scripts/nowcoder_extract.py --query "select count(*) from submissions where contest_id=143786 and phase='contest'"

# 重建榜单（官方 #rank 赛后常被隐藏，用提交数据按总分重建）
python3 scripts/nowcoder_extract.py --rank 143786
```

> ⚠️ 牛客 `status-list` 会返回「比赛期间 + 赛后补题」的全部提交。默认 `--phase contest` 只留比赛期间（按 `startTime <= submitTime <= endTime` 过滤），避免补题污染「比赛期间作弊」的判断。

输出（`<out>/<contestId>/`）：

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | 提交元信息 + `startTime/endTime/inContest/afterContest` 统计 |
| `合集.md` | 全部代码合集，含行数与注释占比 |
| `相似度.md` | 跨人同题雷同对（Winnowing 指纹，阈值 50%） |
| `排名.md` | 嫌疑排名（最高跨人相似度 → 雷同对数 → 提交数） |
| `code/` | 单个源码文件 `{姓名}_{题号}_{语言}.{ext}` |
| `~/.dsh/nowcoder/nowcoder.db` | SQLite 持久化库 |

## TUI 面板（一体化）

一条命令跑完「登录 → 选比赛 → 爬取 → 分析 → 结果/榜单」：

```bash
python3 tui.py            # 交互式面板（curses，纯标准库）
python3 tui.py --check    # 非交互：校验登录并列出比赛
```

首次使用先在面板里「更新 Cookie」粘贴登录态；之后选比赛即可自动多线程爬取 → 确定性雷同检测 → 浏览结果与榜单。

## 数据库（SQLite）

默认 `~/.dsh/nowcoder/nowcoder.db`（`--db` 可改）。两张表：

```sql
contests(contest_id, name, start_time, end_time, description)
submissions(submission_id, contest_id, student, student_id, problem_index, problem_id,
            problem_title, language, verdict, score, submit_time, phase,
            code, url, loc, comments, comment_ratio, extracted_at)
```

`phase` ∈ `contest`（比赛期间）/ `after`（补题）/ `before`。`--query` 可执行任意只读 SQL，例如按人查提交、按题查 AC 数、导出数据。

## 登录 Cookie

接口需要牛客登录态 Cookie。三个来源，优先级从高到低：

1. `--cookie "..."`（命令行）
2. 环境变量 `NOWCODER_COOKIE`
3. 文件 `~/.dsh/nowcoder/cookie.txt`（建议 `chmod 600`）

获取：登录牛客 → DevTools → Network → 任意 `nowcoder.com` 请求 → Request Headers → 复制整行 `Cookie:`。

> ⚠️ Cookie 等同账号完全控制权。本工具只用 GET 只读、不改账号任何内容；用完建议重登作废旧会话。

## 接口逆向记录（2026-11 实测）

| 用途 | 地址 | 返回 |
| --- | --- | --- |
| 最近参加的比赛 | `/acm-heavy/acm/contest/profile/contest-joined-history?uid={uid}&page=1&pageSize=30&onlyJoinedFilter=true&contestEndFilter=true` | JSON `data.dataList[]` |
| 比赛信息 | `/acm/contest/contest-info?id={contestId}` | JSON `data.name/startTime/endTime/description` |
| 提交列表 | `/acm/contest/status-list?token=&id={contestId}&page={n}&pageSize=50` | JSON `data.data[]`，`data.basicInfo.pageCount/statusCount` 翻页 |
| 代码页 | `/acm/contest/view-submission?submissionId={id}` | 服务端渲染 HTML，代码在 `<pre class="prettyprint lang-xxx">` |

提交对象字段：`userName`、`userId`、`submissionId`、`index`（题号 A/B/C）、`problemId`、`languageName`、`statusMessage`、`score`、`submitTime`、`fullScore`、`time`、`memory`、`length`。

注意：`status-list` 含「比赛期间 + 赛后补题」两类提交，需按比赛窗口（`contest-info` 的 `startTime`/`endTime`）过滤。

## 审查说明

- 相似度与注释占比只是**线索**，不是「AI 作弊」的最终判定；结合题目难度、提交时间、代码风格人工判断。
- 相似度只统计「不同人 + 同一题」的跨人雷同（已排除同一人的重复提交）。
- 请遵守牛客网平台规则与队员隐私约定，仅用于队伍训练审查。

## License

MIT
