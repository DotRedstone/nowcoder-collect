# nowcoder-collect · 牛客代码收集与雷同检测

一个完整的 ACM 训练队工具：**登录 → 爬取比赛全部提交 → 去 LLM 化确定性雷同检测 → 结果汇总**。用于训练队代码审查（防 AI 代做 / 防抄袭）。纯 Python 标准库，零第三方依赖。

## 快速开始

```bash
git clone https://github.com/DotRedstone/nowcoder-collect.git
cd nowcoder-collect
./run.sh            # 一个命令启动：没登录会自动弹二维码扫码登录，然后进面板
```

## 使用流程

整条流程在 TUI 面板里一条龙完成：

1. **登录**：`python3 login.py` 在终端渲染二维码，微信「扫一扫」即登录（Cookie 存 `~/.dsh/nowcoder/cookie.txt`）。二维码优先用 `qrencode` 渲染，没装则自动通过 `nix-shell` 拉取或走在线 QR 服务兜底。也可打开 `python3 tui.py` 后在菜单里选「扫码登录」。
2. **选比赛**：菜单「选择比赛并分析」→ 列出你参加的比赛 → `↑↓` 选一个。
3. **爬取**：自动多线程抓取该场**比赛期间**的全部提交（代码 + 姓名 + 题号 + 语言 + 结果 + 得分 + 时间），写入 SQLite。
4. **分析**：去 LLM 化确定性雷同检测，同题两两比较。
5. **汇总**：按分级浏览雷同对，`Enter` 看详情，`r` 看榜单，`q` 返回。

### 分析结论长什么样

| 等级 | 含义 | 例子 |
|---|---|---|
| **实锤** | 去空白后逐字节一致 + 时间方向成立 | 张三/B ↔ 李四（L2=1.0，Δ=167s） |
| **中偏强** | 稀有 n-gram 高度共享 + 结构高度相似（改名/改写） | 张三/C ↔ 李四（L3=0.93） |
| **存疑** | 结构相似 + 一定稀有指纹，需人工看 | — |
| **排除** | 其余；含「人群模板」（多人写同一份正解） | A 题大家答案一样 |

每个结论都带证据：三口径相似度（L1 原文 / L2 去空白 / L3 词法归一化）、稀有 n-gram 指纹、时间线（谁先 AC / 时间差 / 失败次数）。不是「我觉得像」，是可复现、可审计的数字。

## 目录结构

```
nowcoder-collect/
├── login.py              # 终端扫码登录（微信）
├── tui.py                # 一体化 TUI 面板（主入口）
├── detector/             # 去 LLM 化确定性雷同检测器
│   ├── main.py           #   python main.py --contest 143786
│   ├── config.toml       #   阈值配置（可调）
│   ├── detectors/        #   preprocess/normalize/similarity/template/timeline/classify
│   ├── docs/design.md    #   内核设计文档（算法/公式/参考文献）
│   └── tests/            #   单元 + 回归测试
├── skill/                # DSH skill（给 agent 读，见 skill/SKILL.md）
├── browser-extension/    # 浏览器插件（可选，网页端采集）
├── install.sh
└── LICENSE
```

## 检测器单独用（想直接拿 JSON）

```bash
cd detector
python3 main.py --contest 143786 --all --json-out report.json   # 全部对（含"排除"）
python3 -m unittest discover -s tests -v                        # 22 个测试
```

内核算法（L1/L2/L3 归一化、稀有 n-gram、时间线、分级规则、确定性保证、参考文献）见 [`detector/docs/design.md`](detector/docs/design.md)。

## 给 DSH agent 用（可选）

这是给 DSH agent 装的技能。agent 读 [`skill/SKILL.md`](skill/SKILL.md)（那是 agent 的说明书，含接口逆向记录和 CLI 命令）；人类看本 README 就够。

```bash
./install.sh    # 软链到 $DSH_HOME/skills/nowcoder-collect，skill-filesystem 自动发现
```

## 浏览器插件（可选）

Chrome/Edge：`chrome://extensions` → 开发者模式 → 「加载已解压的扩展程序」→ 选 `browser-extension/` 目录。

## 说明

- 相似度与注释占比只是**线索**，不是「AI 作弊」的最终判定；请结合题目难度、提交时间、代码风格人工判断。
- 工具只读牛客数据，不改账号任何内容。
- 请遵守牛客平台规则与队员隐私约定，仅用于队伍训练审查。

## License

MIT
