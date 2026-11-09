# nowcoder-collect

收集牛客网 ACM 比赛（`ac.nowcoder.com`）的提交代码，用于训练队管理与代码审查（防 AI 代做 / 防抄袭）。提供两种形态：

- **DSH skill**（`skill/`）：给 [DSH](https://github.com/DeepSeekAI/dsh) agent 用的技能，一句话触发「导出 XX 比赛代码 / 看看最近参加的竞赛」。
- **浏览器插件**（`browser-extension/`）：Chrome/Edge Manifest V3 扩展，网页端一键/批量采集（可选，独立可用）。

核心能力：拉取某场比赛**全部提交 + 源码**，按学生/题号落盘，并自动生成**相似度报告**（跨人同题雷同检测）与**嫌疑排名**。

## 目录结构

```
nowcoder-collect/
├── skill/                        # DSH skill（主要）
│   ├── SKILL.md                  # 技能说明 + 接口逆向记录
│   └── scripts/
│       └── nowcoder_extract.py   # 提取脚本（纯标准库，零依赖）
├── browser-extension/            # 浏览器插件（可选）
│   ├── manifest.json
│   ├── background.js
│   ├── content/
│   └── popup/
├── install.sh                    # 一键安装 skill
├── LICENSE
└── README.md
```

## 安装

### DSH skill

```bash
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

## 使用（skill）

```bash
cd <repo>/skill

# 列出最近参加的比赛
python3 scripts/nowcoder_extract.py --recent --uid <你的数字uid>

# 提取某场比赛全部代码
python3 scripts/nowcoder_extract.py --contest 143786 --name "ACM-第一次训练赛" --out ./nowcoder-data
```

输出（`<out>/<contestId>/`）：

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | 全部提交元信息（姓名/题号/语言/结果/得分/时间/URL） |
| `合集.md` | 全部代码合集，含行数与注释占比 |
| `相似度.md` | 跨人同题雷同对（Winnowing 指纹，阈值 50%） |
| `排名.md` | 嫌疑排名（最高跨人相似度 → 雷同对数 → 提交数） |
| `code/` | 单个源码文件 `{姓名}_{题号}_{语言}.{ext}` |

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
| 提交列表 | `/acm/contest/status-list?token=&id={contestId}&page={n}&pageSize=50` | JSON `data.data[]`，`data.basicInfo.pageCount/statusCount` 翻页 |
| 代码页 | `/acm/contest/view-submission?submissionId={id}` | 服务端渲染 HTML，代码在 `<pre class="prettyprint lang-xxx">` |

提交对象字段：`userName`、`userId`、`submissionId`、`index`（题号 A/B/C）、`problemId`、`languageName`、`statusMessage`、`score`、`submitTime`、`fullScore`、`time`、`memory`、`length`。

## 审查说明

- 相似度与注释占比只是**线索**，不是「AI 作弊」的最终判定；结合题目难度、提交时间、代码风格人工判断。
- 相似度只统计「不同人 + 同一题」的跨人雷同（已排除同一人的重复提交）。
- 请遵守牛客网平台规则与队员隐私约定，仅用于队伍训练审查。

## License

MIT
