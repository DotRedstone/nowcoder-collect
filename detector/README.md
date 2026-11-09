# 牛客代码雷同检测器（去 LLM 化）

把「LLM 看代码拍脑袋判读」拆成**确定性算法管道 + LLM 只负责写报告文字**。

- 输入：`nowcoder.db` 的比赛提交数据（由 `skill/scripts/nowcoder_extract.py` 产出）
- 输出：固定 schema 的 JSON 结论
- 保证：**同输入必同输出**（无随机、无时间戳、浮点定精度、结果稳定排序）
- 依赖：**纯标准库**（difflib / hashlib / sqlite3 / tomllib / json），零第三方

> 📄 完整内核设计（算法、公式、阈值、确定性保证、参考文献）见 [`docs/design.md`](docs/design.md)

## 架构

```
nowcoder.db
   │ [1] preprocess   读库 → 清洗 → 按(题号,语言)分组
   │ [2] normalize    L1 原文 / L2 去空白 / L3 词法归一化(alpha-renaming)
   │ [3] similarity   difflib 三口径 + 稀有 n-gram(MOSS 式)
   │ [4] template     高频 n-gram 排除 + 人群模板(crowd)检测
   │ [5] timeline     谁先谁后 / 失败序列 / 方向有效性
   │ [6] classify     确定性阈值 → verdict(实锤/中偏强/存疑/排除)
   └ [7] main.py      一条命令跑完 → JSON
```

## 使用

```bash
cd detector

# 跑 143786（默认读 ~/.dsh/nowcoder/nowcoder.db，只输出非"排除"对）
python3 main.py --contest 143786

# 含全部对 + 写文件
python3 main.py --contest 143786 --all --json-out report.json

# 查结果
python3 -c "import json;d=json.load(open('report.json'));print([r for r in d['results'] if r['verdict']=='实锤'])"
```

## 三口径相似度（并列输出，单一数字没法辩护）

| 口径 | 含义 | 用途 |
| --- | --- | --- |
| L1 | 原文逐字符 | 一字不差的实锤 |
| L2 | 去空白 | **判定主口径**，抵消缩进差异 |
| L3 | 词法归一化（用户标识符重命名 v0/v1/…、数字→NUM、字符串→STR、关键字/内置保留） | 识别**改名掩码**（`list_`→`sorted_list` 这种） |

## 稀有 n-gram（MOSS 式）

对 L3 序列按窗口 k=5 切 n-gram，统计每个 n-gram 被多少**不同学生**使用；
「稀有 n-gram」= 使用人数 ≤ 3。对每一对（不同人、同题）：

```
shared_rare = |rare(A) ∩ rare(B)|
rare_score  = shared_rare / min(|rare(A)|, |rare(B)|)
```

天然免疫模板：`#include<bits/stdc++.h>`、`while(n>9)` 这种被几十人用的 n-gram 直接掉出稀有集。

## 人群模板（crowd）检测

同一份代码（L2 的 MD5 相同）被 **≥3 个不同学生**写过 → 判为「套路/人群模板」，排除（不是抄袭）。
这解决了「简单题大家答案一模一样」的误报。

## 分级规则（阈值在 config.toml）

```
实锤   = (L2 >= 0.98 或 md5_ws_equal) 且 方向成立 且 AC时间差 < 1800s
中偏强 = rare_score >= 0.5 且 L3 >= 0.85
存疑   = L3 >= 0.8 且 rare_score >= 0.3
排除   = 其余（含人群模板）
```

## JSON schema

```json
{
  "meta": {"version", "contest_id", "phase", "submissions", "students",
           "pairs_compared", "pairs_reported", "verdict_summary"},
  "results": [
    {
      "pair": ["A", "B"], "student_ids": [uid_a, uid_b],
      "problem": "B", "problem_title": "小乐乐查找数字", "language": "cpp",
      "verdict": "实锤",
      "scores": {"L1": 0.96, "L2": 1.0, "L3": 1.0, "md5_ws_equal": true},
      "rare": {"shared": 12, "score": 1.0, "samples": ["v0 = v1 ( v2 ) ..."]},
      "timeline": {"who_first", "delta_seconds", "first_submit_delta",
                   "later_failures_before_ac", "direction_valid", "error_types"},
      "template_ratio": 0.1, "crowd_count": 2
    }
  ]
}
```

## 测试

```bash
cd detector
python3 -m unittest discover -s tests -v   # 单元测试（合成数据）
# test_regression.py 会读本地 DB 做回归（无 DB 时自动跳过）
```

回归基准（143786 真实数据）：

- 张三/B ↔ 李四 → `实锤`，`md5_ws_equal=true`
- 张三/C ↔ 李四 → `中偏强`（L2≈0.96，非逐字节一致）
- 结果两次运行逐字节一致

## 已知限制

- **语义等价识别不到**：`for`↔`while`、递归↔迭代、`a[i]`↔`*(a+i)`，L3 词法层认不出（需 AST，三期）。
- **接触渠道无法证明**：只能证明「代码像 + 时间对得上」，证明不了谁给了谁，需人工问。
- **单人独立撞车**：正解写法两个人独立写出概率不低，算法给低分不给 0。
- **不处理跨语言**：C++ 与 Python 写同一算法，相似度≈0。
- **题目模板判断依赖数据量**：人少时「≥3 人同一份代码」的阈值不可靠（`config.toml` 可调）。
- **错误类型目前只到 verdict 粒度**：没有 stderr，无法做「括号错配/漏行」级分类（需 extractor 额外存 stderr）。
