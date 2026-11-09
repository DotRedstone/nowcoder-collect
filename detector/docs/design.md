# 牛客代码雷同检测器 · 内核设计文档

> 版本：1.0.0 · 状态：已实现（`detector/`）· 依赖：纯 Python 标准库

本文档描述「分析层」（去 LLM 化的确定性雷同检测）的算法内核、数据结构、判定规则与设计取舍。目标是让**任何读代码的人都能复现结论**：同一份数据库跑出来，结果逐字节一致。

---

## 1. 目标与边界

**目标**：给定一场牛客比赛的全部提交（`nowcoder.db`），输出「哪些人之间、在哪道题上、代码雷同到值得人工介入」的确定性结论。

**明确不做的**：

- 不做「语义等价」判断（`for`↔`while`、递归↔迭代、`a[i]`↔`*(a+i)` 认不出，属 L4 AST 三期）。
- 不证明「谁把代码给了谁」（只能证明「代码像 + 时间对得上」）。
- 不做跨语言比较（C++ 和 Python 写同一算法，相似度≈0）。
- 不调用 LLM 做判定（LLM 只负责把最终 JSON 写成报告文字）。

---

## 2. 总体架构

```
nowcoder.db (submissions)
   │
   ├─[T1] preprocess.py    读库 → 清洗 → 按(题号,语言)分组 → 每人取代表代码
   │
   ├─[T2] normalize.py     每份代码 → L1 原文 / L2 去空白 / L3 词法归一化
   │
   ├─[T3] similarity.py    difflib 三口径 + 稀有 n-gram（MOSS 式）
   │
   ├─[T4] template.py      人群模板（crowd）检测 + 常见 n-gram 占比
   │
   ├─[T5] timeline.py      谁先 AC / 时间差 / 失败序列 / 方向有效性
   │
   ├─[T6] classify.py      确定性阈值组合 → verdict
   │
   └─[T7] main.py          两两比较编排 + 稳定排序 + JSON 输出
```

模块边界：T1–T6 是纯函数（同输入同输出），只有 T7 做 I/O 编排。

---

## 3. 数据模型（T1）

数据来源是 `skill/scripts/nowcoder_extract.py` 产出的 SQLite：

```sql
submissions(
  submission_id, contest_id, student, student_id,
  problem_index,      -- 题号 A/B/C…
  problem_id, problem_title,
  language, verdict, score,
  submit_time,        -- 毫秒时间戳
  phase,              -- contest(比赛期间) / after(补题) / before
  code, url, loc, comments, comment_ratio, extracted_at
)
```

### 3.1 清洗与过滤

- 只取 `phase = 'contest'`（比赛期间），补题不进分析。
- 过滤 `code IS NULL / code = ''` 的空代码。
- `strip_stderr` 兜底剥离可能混入的 stderr（当前 extractor 只存源码，此步目前是 no-op）。

### 3.2 语言分桶

只比较同语言桶内的提交（跨桶不比较）：

| 桶 | 匹配 |
|---|---|
| `cpp` | C、C++、clang、g++、gcc |
| `python` | Python3、PyPy3 |
| `java` | Java |
| `other` | 其余（不参与比较） |

### 3.3 分组与代表代码

- 按 `(problem_index, 语言桶)` 分组 → 同题同语言才两两比较。
- 组内按 `student_id` 分组，取**代表提交**：分数最高者；同分取代码最长者（最完整）。

> 代表代码的选择影响「一人多交」的噪声：同一个学生同一题交了 5 次，只取最好的一份参与两两比较，避免「5×5=25 条 100% 对」的重复计数。

---

## 4. 归一化层（T2，核心）

### 4.1 三口径

对每份代码同时产出三个口径，**并列输出**，绝不混成单一数字：

| 口径 | 变换 | 用途 |
|---|---|---|
| L1 | 原文不动 | 「一字不差」的最保守证据 |
| L2 | 删除全部空白 `\s+` | **判定主口径**，抵消缩进/换行差异 |
| L3 | 词法归一化（见下） | 识别**改名掩码** |

### 4.2 L3 词法归一化

分四步：

**① 注释剥离（语言感知）**

- Python：剥 `# …`（行注释）。
- C/C++：剥 `// …`、`/* … */`（块注释）、`# …`（预处理行）。
- 剥离器先**保护字符串字面量**（含三引号 `"""…"""`、`'''…'''` 和转义 `\`），再删注释，避免把字符串里的 `//` 或 `#` 误删。

**② 词法切分**

正则把代码切成 token，类别：`string` / `number` / `ident` / `op` / `other`。

- 字符串：`"…"` `'…'` 及三引号，识别转义。
- 数字：`0x…` 十六进制、整数、小数、科学计数法 `1e9`。
- 标识符：`[^\W\d]\w*`（Unicode 字母开头，含中文/下划线）。
- 运算符：多字符优先（`<<` `>>` `<=` `==` `++` `&&` `->` `::` `+=` `**` `//` 等）。

**③ 分类归一**

| token 类别 | 处理 |
|---|---|
| 关键字 | 保留原样（各语言 keyword 表） |
| 内置符号 | 保留原样（`cout` `printf` `sort` `len` `range` `append`… 白名单） |
| 数字字面量 | 统一 `NUM` |
| 字符串字面量 | 统一 `STR` |
| **用户标识符** | **按首次出现顺序重命名 `v0 v1 v2 …`** |
| 罕见字符 | 占位 `?`（保留结构位，不丢失） |

**④ alpha-renaming 的效果**

```
list_ = sorted_list[::-1]   →  v0 = v1 [ : : - NUM ]
x = y[::-1]                 →  v0 = v1 [ : : - NUM ]   ← 完全一致
```

`list_` 和 `x` 都变 `v0`，`sorted_list` 和 `y` 都变 `v1`——**改名掩盖不了结构**。这是编译器里的 α-重命名（alpha-renaming / alpha-conversion）。

---

## 5. 相似度引擎（T3）

### 5.1 基础相似度（difflib）

```python
difflib.SequenceMatcher(None, a, b).ratio()
```

- 对 **L1（字符串）**、**L2（字符串）**、**L3（token 列表）** 各算一个 ratio。
- L3 在 token 序列上做，是**结构级**比较（不是字符级）。
- 底层是 Ratcliff–Obershelp「格式塔」模式匹配（见参考文献）。

```python
scores = {
  "L1": ratio(L1_a, L1_b),
  "L2": ratio(L2_a, L2_b),
  "L3": ratio(token_a, token_b),   # token 序列
  "md5_ws_equal": md5(L2_a) == md5(L2_b),   # 去空白后是否逐字节一致
}
```

### 5.2 稀有 n-gram（MOSS 式指纹）

比字符相似度更硬的证据，直接复刻 MOSS 的 winnowing 思想（这里用**固定窗口 n-gram + 稀有度**简化版）：

1. 每份代码取 L3 token 序列，窗口 `k=5` 切成 n-gram 集合。
2. 统计每个 n-gram 被多少**不同学生**使用 → `usage_count`。
3. **稀有 n-gram** = `usage_count ≤ 3`（阈值 `config.toml` 可调）。
4. 对每一对（不同人、同题）：

```
rare(A) = { A 的 n-gram 中稀有的那些 }
rare(B) = { B 的 n-gram 中稀有的那些 }
shared   = rare(A) ∩ rare(B)
rare_score = |shared| / min(|rare(A)|, |rare(B)|)
```

**为什么免疫模板**：`#include<bits/stdc++.h>`、`while(n>9)` 这类片段被几十人用，`usage_count` 巨大 → 掉出稀有集 → 不计入雷同证据。真正的「个人写法」（奇怪的变量名组合、非常规的运算顺序）才会稀有、才会被当作指纹。

### 5.3 为什么是三个口径并列

单一数字没法辩护。举例（真实数据）：

- 张三/B ↔ 李四：L2=1.00（逐字节一致）→ 实锤。
- 张三/C ↔ 李四：L2=0.96、L3=0.93（改了变量名，但结构一样）→ 中偏强。

如果只看一个「相似度」，这两个会混在一起。三口径 + md5 才能说清「到底是一字不差，还是改了名」。

---

## 6. 模板剔除（T4）

### 6.1 人群模板（crowd）

```
同一份代码（L2 的 MD5 相同）被 >= 3 个不同学生写过  →  人群模板，排除
```

解决「简单题大家答案一模一样」的误报：A 题（各位数字之和）几十人写同一份三行正解，如果只比相似度会全员实锤；有了 crowd 检测，这种直接判「套路」排除。

### 6.2 常见 n-gram 占比

```
common_ratio = 代码里「usage_count > 稀有阈值」的 n-gram 数 / 总 n-gram 数
```

作为 `template_ratio` 字段输出，供人工判断「这份代码到底多套路」。

---

## 7. 时间线引擎（T5）

对每一对候选，取两人该题的**全部提交**（不是代表代码），按时间排序：

```
who_first                 = 谁先 AC（按第一次 AC 时间）
delta_seconds             = 两人 AC 时间差（秒）
first_submit_delta        = 两人首次提交时间差
direction_valid           = 后 AC 方的首次提交时间 >= 先 AC 方的 AC 时间
later_failures_before_ac  = 后交方 AC 之前的失败次数
error_types               = 失败提交的 verdict 分类
```

**方向有效性**是「谁抄谁」的时间证据：

- 若 B 在 A 已经 AC 之后**才开始第一次提交**，则「A → B」方向成立。
- 若 B 在 A AC 之前就已经在提交了，则无法主张「B 抄 A」——两人可能独立在写。

**失败类型**（当前只到 verdict 粒度，无 stderr，无法做「括号错配/漏行」级分类）：

| verdict | 分类 | 抄袭指示性 |
|---|---|---|
| 编译错误 | 编译错误 | 中（可能是照着抄漏了语法） |
| 答案错误 | 答案错误 | 中 |
| 运行超时 | 超时 | 弱 |
| 段错误/内存/格式/输出 | 各自类别 | 弱 |

> 计划后续让 extractor 额外存 stderr，才能做「共享同一处错误」这种最硬的证据（两个人都错在同一个地方，比写得一样还可疑）。

---

## 8. 分级器（T6）

纯规则，阈值全部在 `config.toml`，不写死在代码里：

```toml
[thresholds]
solid_l2 = 0.98             # 实锤：L2 达到该值
solid_delta_seconds = 1800  # 实锤：AC 时间差上限（秒）
mid_strong_rare = 0.5
mid_strong_l3 = 0.85
suspicious_l3 = 0.8
suspicious_rare = 0.3

[template]
crowd_threshold = 3        # 人群模板：同一份代码被 >=3 人写

[ngram]
k = 5                      # n-gram 窗口
rare_threshold = 3         # 稀有：被 <=3 人用
```

判定顺序（`classify.py`）：

```python
# 1. 人群模板：md5一致 且 crowd >= 3          → 排除
# 2. 实锤：  (md5一致 或 L2>=0.98) 且 direction_valid 且 Δ<1800s
#    └ 若 md5一致 但方向/时间不成立            → 中偏强
# 3. 中偏强：rare_score >= 0.5 且 L3 >= 0.85
# 4. 存疑：  L3 >= 0.8 且 rare_score >= 0.3
# 5. 排除
```

verdict 等级：`实锤 > 中偏强 > 存疑 > 排除`。

---

## 9. 确定性保证（可复现）

- **无随机数**：全程不用 `random`。
- **无墙钟时间戳**：输出里不含 `datetime.now()`，只有数据派生的值。
- **浮点定精度**：所有分数 `round(x, 4)`。
- **稳定排序**：结果按 `(severity, -rare, -L3, -L2, 名字, 题号)` 排序，键全确定。
- **JSON 稳定序列化**：`json.dumps(sort_keys=True, ensure_ascii=False)`。

验收：同一份库跑三次，输出**逐字节一致**（已由 `tests/test_regression.py` 断言）。

---

## 10. JSON 输出 schema

```json
{
  "meta": {
    "version": "1.0.0", "contest_id": 143786, "phase": "contest",
    "submissions": 597, "students": 41,
    "pairs_compared": 3100, "pairs_reported": 20,
    "verdict_summary": {"实锤": 1, "中偏强": 11, "存疑": 8}
  },
  "results": [
    {
      "pair": ["张三", "李四"],
      "student_ids": [uid_a, uid_b],
      "problem": "B", "problem_title": "小乐乐查找数字", "language": "cpp",
      "verdict": "实锤",
      "scores": {"L1": 0.96, "L2": 1.0, "L3": 1.0, "md5_ws_equal": true},
      "rare": {"shared": 12, "score": 1.0, "samples": ["v0 = v1 ( v2 ) …"]},
      "timeline": {"who_first": "B", "delta_seconds": 167.0,
                   "first_submit_delta": 17.0, "later_failures_before_ac": 9,
                   "direction_valid": true, "error_types": ["答案错误", "超时"]},
      "template_ratio": 0.1, "crowd_count": 2
    }
  ]
}
```

`timeline.who_first` 的 `"A"`/`"B"` 对应 `pair` 数组的索引 0 / 1。

---

## 11. 已知限制

| 限制 | 原因 | 缓解 |
|---|---|---|
| 语义等价识别不到 | L3 是词法层，不是语法层 | 三期上 AST（`ast`/`clang -ast-dump`） |
| 证明不了「谁给了谁」 | 算法只见代码+时间 | 人工问话 |
| 单人独立撞车 | 正解写法高度收敛 | 算法给低分不给 0，靠 rare 区分 |
| 不处理跨语言 | C++ 与 Python 语法不同 | 语言分桶隔离 |
| 人群模板阈值依赖数据量 | 人少时「≥3 人同代码」不可靠 | `crowd_threshold` 可调 |
| 错误类型粒度粗 | 无 stderr | extractor 补存 stderr 后升级 |

---

## 12. 参考文献

本项目用到的方法均有公开学术/工程出处：

1. **Winnowing 指纹（稀有 n-gram 的思想来源）**
   Schleimer, S., Wilkerson, D. S., Aiken, A. *Winnowing: Local Algorithms for Document Fingerprinting.* In Proceedings of the 2003 ACM SIGMOD International Conference on Management of Data (SIGMOD '03), pp. 76–85, 2003.

2. **MOSS 抄袭检测系统（整体方法论的标杆）**
   Aiken, A. *MOSS: A System for Detecting Software Similarity.* Stanford University. https://theory.stanford.edu/~aiken/moss/

3. **difflib / 格式塔模式匹配（L1/L2/L3 相似度的底层）**
   Ratcliff, J. W., Metzener, D. E. *Pattern Matching: The Gestalt Approach.* Dr. Dobb's Journal, July 1988.（Python `difflib.SequenceMatcher` 的实现基础）

4. **文档相似 / shingling（n-gram 相似）**
   Broder, A. Z. *On the Resemblance and Containment of Documents.* In Proceedings of the Compression and Complexity of Sequences (SEQUENCES '97), 1997.

5. **Jaccard 相似系数（rare_score 的度量）**
   Jaccard, P. *Étude comparative de la distribution florale dans une portion des Alpes et des Jura.* Bulletin de la Société Vaudoise des Sciences Naturelles, 37: 547–579, 1901.

6. **词法分析（tokenizer）**
   Aho, A. V., Lam, M. S., Sethi, R., Ullman, J. D. *Compilers: Principles, Techniques, and Tools* (2nd ed.). Addison-Wesley, 2006.（「龙书」，第 3 章词法分析）

7. **α-重命名 / α-转换（L3 用户标识符重命名的理论依据）**
   Pierce, B. C. *Types and Programming Languages.* MIT Press, 2002.（λ 演算的 α-conversion）

> 说明：本项目是这些思想的**工程简化实现**，并非逐行复刻 MOSS（MOSS 用的是真正的 winnowing 滑窗指纹；这里用固定窗口 n-gram + 稀有度阈值做了等价简化）。
