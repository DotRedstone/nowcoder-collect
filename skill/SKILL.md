---
name: nowcoder-collect
description: 收集牛客网 ACM 比赛（ac.nowcoder.com）的提交代码。当用户要求拉取/导出/整理牛客比赛或训练赛的队员代码、查看最近参加的比赛、或做代码雷同/疑似 AI 辅助审查时使用。需要用户的登录 Cookie。
---

# 牛客代码收集（nowcoder-collect）

直接从 ac.nowcoder.com 用用户登录态拉取比赛提交与代码，按「学生/题号」落盘 + 写入 SQLite 数据库 + 生成相似度报告与嫌疑排名。

## 何时用

- 「把我最近参加的竞赛」「导出 XX 比赛的代码」「整理训练赛代码」「查查有没有人抄代码」这类请求。
- 用户是 ACM 队伍管理者，要收集团员提交代码做 AI/抄袭审查。

## 关键：比赛期间 vs 赛后补题

牛客 `status-list` 会返回「比赛期间 + 赛后补题」的全部提交。本脚本**默认只采集比赛期间**（`startTime <= submitTime <= endTime`），用 `--phase` 切换：

- `--phase contest`（默认）仅比赛期间
- `--phase after` 仅赛后补题
- `--phase all` 全部

**审查比赛期间是否作弊时，务必用默认 `contest`**，否则补题会污染结论（补题时抄比赛答案 ≠ 比赛期间作弊）。

## 前置：登录 Cookie

接口需要用户浏览器登录态 Cookie（`NOWCODERUID`、`t`、`NOWCODERCLINETID` 等）。三个来源，优先级从高到低：

1. `--cookie "..."`（命令行直接传）
2. 环境变量 `NOWCODER_COOKIE`
3. 文件 `~/.dsh/nowcoder/cookie.txt`（建议 `chmod 600`）

**获取方式**：用户浏览器登录牛客后，DevTools → Network → 任意 `nowcoder.com` 请求 → Request Headers → 复制整行 `Cookie:`。会话会过期，失效后让用户重登再更新 `~/.dsh/nowcoder/cookie.txt`。

## 执行

配套脚本：`scripts/nowcoder_extract.py`（纯标准库，零第三方依赖）。

```bash
# 1) 列出最近参加的比赛（拿到 contestId）
python3 scripts/nowcoder_extract.py --recent --uid <你的数字uid>
# 或在 ~/.dsh/nowcoder/uid.txt 写入 uid 后省略 --uid

# 2) 提取某场比赛「比赛期间」代码（默认）
python3 scripts/nowcoder_extract.py --contest 143786 --out ./nowcoder-data

# 3) 含补题的完整数据
python3 scripts/nowcoder_extract.py --contest 143786 --phase all

# 4) 只取补题
python3 scripts/nowcoder_extract.py --contest 143786 --phase after

# 5) 查 SQLite（默认 ~/.dsh/nowcoder/nowcoder.db）
python3 scripts/nowcoder_extract.py --query "select count(*) from submissions where contest_id=143786 and phase='contest'"
```

输出（`<out>/<contestId>/`）：

- `manifest.json` — 全部提交元信息（含 startTime/endTime/inContest/afterContest 统计）
- `合集.md` — 全部代码合集，含行数与注释占比
- `相似度.md` — 高相似提交对（跨人同题，Winnowing 指纹，阈值 50%）
- `排名.md` — 嫌疑排名（按最高跨人同题相似度 → 雷同对数 → 提交数，含 AC 数与平均注释占比）
- `code/` — 单个源码文件：`{姓名}_{题号}_{语言}.{ext}`
- 另有持久化 SQLite：`~/.dsh/nowcoder/nowcoder.db`

## 数据库（SQLite）

默认路径 `~/.dsh/nowcoder/nowcoder.db`（`--db` 可改）。两张表：

```sql
contests(contest_id, name, start_time, end_time, description)
submissions(submission_id, contest_id, student, student_id, problem_index, problem_id,
            language, verdict, score, submit_time, run_time, memory, length,
            loc, comments, comment_ratio, phase, code, url, extracted_at)
```

`phase` ∈ `contest`（比赛期间）/ `after`（补题）/ `before`。用 `--query` 执行任意只读 SQL：

```bash
# 某人的全部提交
python3 scripts/nowcoder_extract.py --query "select problem_index, verdict, phase, datetime(submit_time/1000,'unixepoch','+8 hours') from submissions where student='张三'"
# 各题 AC 数
python3 scripts/nowcoder_extract.py --query "select problem_index, count(*) from submissions where verdict='答案正确' group by problem_index"
```

## 真实接口（2026-11 实测，改版时据此核对）

| 用途 | 地址 | 返回 |
| --- | --- | --- |
| 最近参加的比赛 | `/acm-heavy/acm/contest/profile/contest-joined-history?uid={uid}&page=1&pageSize=30&onlyJoinedFilter=true&searchContestName=&onlyRatingFilter=false&contestEndFilter=true` | JSON，`data.dataList[]` |
| 比赛信息 | `/acm/contest/contest-info?id={contestId}` | JSON，`data.name`/`startTime`/`endTime`/`description` |
| 提交列表 | `/acm/contest/status-list?token=&id={contestId}&page={n}&pageSize=50` | JSON，`data.data[]`（`userName`/`userId`/`index` 题号/`languageName`/`statusMessage`/`submissionId`/`problemId`/`score`/`submitTime`），`data.basicInfo.pageCount/statusCount` 翻页 |
| 代码页 | `/acm/contest/view-submission?submissionId={id}` | 服务端渲染 HTML，代码在 `<pre class="prettyprint lang-xxx">…</pre>`（HTML 转义） |

- 代码页是服务端渲染，`fetch` 直接拿到 `pre.prettyprint` 的 `textContent`，无需执行 JS。
- 提交列表是纯 JSON，无 `<table>`，别按表格式去解析。
- `status-list` 含补题，用 `--phase` 过滤。
- `uid` 是数字 ID（如 12345678），不是 `NOWCODERUID` cookie 里的 UUID；在个人主页 `/acm/contest/profile/{uid}` 可查。

## 注意

- 只用 GET 只读，不改账号任何内容。
- 相似度与注释占比只是审查线索，不是 AI 作弊的最终判定；结合题目难度、提交时间、代码风格人工判断。
- Cookie 等同账号完全控制权，仅在用户主动提供时使用；用完可建议其重登作废旧会话。
