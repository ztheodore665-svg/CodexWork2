# 阶段 5 · 评测扩充 + 归因对比 + 可观测看板（W5-1 / W5-2 / W5-3）

> 这一阶段要解决什么：**让"改完 prompt 指标到底变好还是变坏"变成一张表能回答的问题**，
> 并给生成质量补上第二个口径（忠实度），最后把这张表变成一个谁都能打开看的可观测看板。
> 出口条件见第 1 节。
>
> 第一段做了 W5-1（评测扩充）与 W5-2（归因对比）；**第二段（本文 §2.6 / §3.6）收口 W5-3（可观测看板）**，
> 并补修了 5.3 的真实事故（self_check 按意图豁免引用校验）。**阶段 5 至此全部完成。**

设计文档依据：[`旅忆地图 TravelMemory.md`](../../旅忆地图%20TravelMemory.md) §4.5（评测集 schema）、
§10.1（核心硬指标）、第十一章「阶段 5」。

---

## 1. 这一阶段要解决什么 + 出口条件

```
W5-1 评测扩充（口径收口 + judge + 数据集扩容）  ──┐
                                                   ├──► W5-2 归因对比表 ──► W5-3 可观测看板
```

| 块 | 出口条件（验收标准） | 状态 |
| --- | --- | --- |
| W5-1 | 引用率 / 拒答口径收口为 `metrics.py` 纯函数；新增忠实度指标（默认关）；评测集 22 rag + 4 refusal + 1 recommend + 2 plan（阶段 5.2：规划题不再标 rag） | ✅ 已完成 |
| W5-2 | `--compare` 按 prompt_version + embedding_model 分组输出 Markdown 归因表，含环比 ↑/↓ 与 P95；分组逻辑不依赖数据库、可单测 | ✅ 已完成 |
| W5-3 | `make dashboard` 导出单文件静态 HTML：汇总卡片（总用例数 / 平均 Recall / 平均引用率）+ 归因对比表（↑↓）+ 延迟 P50/P95 + 趋势图；聚合与渲染不依赖数据库、可单测；`pytest` 全绿 | ✅ 已完成 |

**阶段 5 最终状态：W5-1 ✅ / W5-2 ✅ / W5-3 ✅（封板）**

---

## 2. 核心原理讲解

### 2.1 为什么"拒答"在三个指标里各有各的算法

先看一组容易搞混的场景。假设用户问"我 2019 年去过巴黎吗"（refusal 用例），系统正确地拒答了：

| 指标 | 记什么 | 为什么 |
| --- | --- | --- |
| `refusal_correct` | **1.0** | 这正是它要度量的：该拒的时候拒了 |
| `citation_rate` | **None（不计入）** | 拒答话术不是事实性回答 |
| `faithfulness` | **None（不送 judge）** | 拒答话术没有事实句，评它没有意义 |

三个"不计入"不是偷懒，而是**每个指标只对自己的失败模式负责**。
最容易犯的错是把 `citation_rate` 在拒答时记 **0.0**：阶段 4 真的踩过这个坑 ——
`SELF_CHECK_FAILED_ANSWER` 兜底话术里有一句"你可以把问题说得更具体一些"，
它不在软句白名单里，于是整段话术被当成"缺少引用的事实句"，引用率算出 0.0。
**拿安全话术的质量惩罚生成质量**，是最冤枉的一种错：系统明明做对了（拒答），
却在一个衡量"回答质量"的指标上背了锅。

那 self_check 失败（该拒但生成质量不过关）怎么记？答案是
`refusal_correct = 0.0` 记一次 —— 因为"该拒的用例没给出可验收的回答"本身就是错误，
记在拒答口径里刚好，**不双标**（同一个失败不在两个指标里各扣一遍）。

`refusal_correct` 还有一个反直觉的设计：**正常回答记 None，不记 1.0**。

```
refusal 用例：拒了 = 1.0，没拒 = 0.0
其他用例：   拒了 = 0.0，没拒 = None   ← 注意不是 1.0
```

为什么？打个比方：消防演习的评分标准是"报警器响的时候喷不喷水"。
昨天没演习、没喷水，这不是功劳，只是**什么都没发生**。
如果给"没喷水"也打 1.0，那一个从来不开机的系统也能拿满分，
指标就失去了分辨力。正常回答的质量由 `citation_rate` 负责，各管各的。

还有一类用例连"拒答率"都不该进：**recommend / plan**。

阶段 5.2 修过一个真实事故："帮我照着上次那趟再规划一个类似的路线。"
被标成了 `rag`。可规划题不可能从库里"引用"出一个未来行程 ——
self_check 一失败就拒答，这条题于是在 `refusal_correct` 上记了 0.0。
**用 RAG 的尺子量规划的题**：指标被污染，真正的问题（题目标错了）反而被掩盖。

所以 recommend / plan 对两个事实性指标都是 None：

```
rag：            拒了 = 0.0，没拒 = None
recommend/plan： 恒为 None
```

它们的对错看的是"推荐/规划是否合理"，这个口径现阶段没有自动化指标
（数据集 `note` 里写的"只计 hit@3"也要等阶段 7）。没有尺子的时候就**留白** ——
错记一个 0.0 比不记更糟：它会让人以为"拒答率下降了"，从而去改一个本来没问题的模块。

阶段 5.3 补上了这个故事的下半集：**光豁免指标不够，运行时也得豁免**。

5.2 只把统计口径改对了，`SelfCheckNode` 内部仍按事实题的标准检查每一句话 ——
于是 plan 题给出"建议寻找拥有历史遗迹和老城区美食的城市"这样合理的规划建议时，
因为句句没有 `[n]`，照样被判"缺少引用" → 重试 → 拒答。
**指标说"这条不计引用率"，self_check 说"杀了"**，两层认的不是同一套口径。

修法分三块（核心是 `app/utils/intent.py`）：

1. 意图分类走**确定性规则**（关键词命中），不用 LLM —— 评测集标好的 kind 必须与
   运行时判定一致，"这次算下次不算"的豁免比没有豁免更危险
2. `QueryParseNode` 把意图写进 `ParsedQuery.intent`（fact / plan / recommend）。
   两条运行时（pipeline 与 graph）共用同一个节点，改动自动覆盖两者
3. `SelfCheckNode` 对 plan/recommend **跳过逐句引用校验，但保留编号越界校验** ——
   豁免的是"要不要引用"，不是"引用能不能瞎编"

标记词表刻意不收"行程"："哪趟行程是以看石窟和古城为主的？"问的是过去哪一趟，
是事实题；收了"行程"就会误放一条 rag 过关，指标被反向污染。
`tests/test_intent.py::test_classifier_agrees_with_eval_dataset` 钉死
"分类器判定 == 数据集标注"——将来加了没有标记词的 plan 题，测试会直接红，
而不是等真实 API 验证时才发现又被误杀。

### 2.2 LLM-as-a-Judge 为什么默认关闭

忠实度（faithfulness）= 让模型再当一回考官，读一遍证据和回答，
判断每句话有没有证据支撑，只输出 1 或 0。

它回答的是引用率回答不了的问题：

```
证据：[1] 清晨的鼓浪屿很安静。
回答：你在鼓浪屿拍了清晨的海滩[1]。   ← 引用率 1.0，但"海滩"证据里根本没有！
```

引用率只查"标没标 `[n]`"，忠实度查"标了的那句话是不是证据真的说了"。

但默认关（`EVAL_LLM_JUDGE=false`），三个理由：

1. **成本**：一次全量评测会多出与用例等量的 LLM 调用（28 条 → 28 次额外调用），
   而输出只有 **1 bit**；
2. **稳定性**：judge 自己也是模型，也会抽风。它适合"盯一段时间的生成质量趋势"，
   不适合当每次改动的门槛；
3. **口径要严**：judge 说了一句"1，因为证据支持"怎么办？
   `parse_faithfulness()` 只认**单独一个 0 或 1（可带句读尾巴）**，
   其余一律 None。猜一个值比没有值更危险 —— 猜出来的忠实度看起来像个指标，
   但它会把"judge 今天话多"误记成"模型今天爱幻觉"。

### 2.3 归因表与环比箭头

评测的绝对值意义有限（换数据、换模型、甚至模型当天抽风都会动），
有意义的是**同一批题、相邻两次运行之间的差**。所以每次结果都记
`prompt_version + embedding_model`，`--compare` 把它们分组、按首现时间排序，
相邻两组之间画箭头：

```
| prompt_version | embedding_model | n | Recall@5 | MRR | 引用率 | ... |
| pv2026.10-stage4.1 | bge-m3@v1 | 28 | 0.857 | 0.778 | 1.000 | ... |
| pv2026.10-stage5.1 | bge-m3@v1 | 28 | 0.857 ↑ | ... |
```

箭头的方向由一个元数据表决定，而不是一堆 if/else：

```python
_METRICS = (
    ("recall_at_k", True),      # True = 越大越好
    ("p95_latency_ms", False),  # False = 越小越好
    ...
)
```

延迟从 200ms 涨到 300ms 是**退步**，箭头朝下；检索分涨了箭头朝上。
↑ 恒为绿、↓ 恒为红，因为方向判完之后颜色就只剩"好不好"一种含义。

不给箭头的情况也和给箭头一样重要：首组没有对比对象、任一侧是 None
（比如上一轮没开 judge，忠实度不可比）、两侧完全相等（延迟持平是常态，
满屏"→"等于没有信息）。

### 2.4 P95 为什么用"最近秩"而不是插值

P95 要回答的是"最慢的 5% 到底有多慢"，用来判断尾延迟有没有失控。
两种算法在样本少时会分叉：

```
3 个样本：100ms, 200ms, 300ms

线性插值：P95 = 第 2.85 个 = 200 + 0.85 × 100 = 285ms  ← 这个延迟从未出现过！
最近秩：  P95 = 第 ceil(0.95×3) = 第 3 个 = 300ms
```

285ms 没有任何一次真实请求对应过，拿它做告警阈值会永远等不到触发
（或者反过来，永远在误报）。最近秩的公式就一行：

```python
rank = max(1, math.ceil(q * len(ordered)))
```

### 2.5 为什么 compare 的逻辑必须不碰数据库

老实现是把分组、均值、`count/avg` 全塞进 SQL（`GROUP BY` + `func.avg`）。
问题是：**聚合口径没法单测**。想验证"None 不参与均值""P95 用最近秩"，
你得先起一个 PostgreSQL，造几行历史数据 —— 于是这些口径长期处于
"看起来对"的状态，直到某天 `--compare` 输出了离谱的数字。

阶段 5 把整套逻辑改成纯函数（`eval/compare.py` 不 import SQLAlchemy），
`test_eval_compare.py` 用 `ResultRecord` 纯数据就能把整张表钉死。
ORM → 纯数据的搬运留在 `runner.py` 的 `print_compare` 里，就几行。
代价是万行以上的历史结果要在 Python 侧聚合 —— 真到那天再把均值搬进 SQL，
接口不用变。

### 2.6 W5-3：看板为什么是「单文件静态 HTML」而不是一个服务

看板的使用场景是**明早开会时随手打开**，不是常驻巡检。所以选型上刻意保守：

| 选项 | 为什么没选 / 选了 |
| --- | --- |
| Grafana + Prometheus | 要常驻服务、要埋点改造、要运维。阶段 5 的产出是"一次评测的结论"，不是实时曲线 |
| FastAPI 新路由 | 打开看板前得先起后端，等于把"看一眼"变成"部署一次" |
| **单文件静态 HTML（选它）** | 双击即开、能邮件转发、能塞进 Git；数据内联成 `window.DASHBOARD_DATA`，只有 Pico.css 与 ECharts 走 CDN |

断网时页面仍然可读：卡片与表格是纯 HTML/内联 CSS，只有图表块会空着 ——
**降级成"没图但有数"，而不是白屏**。

### 2.7 顶部卡片为什么取「最新一组」而不是「全部历史平均」

这是 W5-3 唯一一处真正的口径决策。跨版本求平均是错的：

```
pv2026.10-stage5.1  Recall = 0.80
pv2026.10-stage5.2  Recall = 1.00
────────────────────────────────
"全部历史平均"      = 0.90   ← 这个数既不代表 5.1 也不代表 5.2
```

0.90 会被当成"系统当前的水平"，而系统当前其实是 1.00。
所以卡片取**最新一组**，并在副标题写明这个数属于哪个版本。

分工因此很清楚：**卡片回答"现在怎么样"，归因表回答"比上次好还是坏"**。
卡片上的箭头直接复用 `CompareRow.deltas`（方向已由 `compare._arrows` 按指标语义判完），
不再二次判断 —— 免得出现"表格说退步、卡片说进步"的两套口径。

同理，"总用例数"显示的是**最新一组的 n**（= 一次评测的题目数），
历史累计行数降级为副标题；否则"跑了几轮"会被混进"有多少道题"。

### 2.8 P50 与 P95 为什么必须一起出现

- **只看 P95**：一次 3 秒的冷启动会把整组"看起来很慢"，于是去优化一个一次性的抖动；
- **只看 P50**：尾延迟从 200ms 涨到 2s 完全看不见，而那才是用户实际抱怨的东西。

两者都沿用 `compare.percentile` 的**最近秩**口径（不插值），
所以看板上的 P95 与 `--compare` 终端表里的 P95 是**同一个数** ——
同一个出口不允许给出两个答案。看板还额外给出 `P95 / P50` 倍率，
倍率突然变大 = 尾延迟在拖，即使 P50 没动。

### 2.9 `--demo` 不是"演示功能"，是出口条件的一部分

`make dashboard` 的出口条件是"看板能出"，而 CI / 没起 Docker 的机器上没有 PostgreSQL。
没有 `--demo`，这条出口条件就长期处于**未验证**状态 ——
和 `--ablation --offline`（阶段 4 W4-3）是同一个道理。

`--demo` 只喂合成的 `ResultRecord`，走的是与真实路径**完全相同**的
`build_dashboard_data` + `render_dashboard_html`，因此它验证的是"看板对不对"，
而不是"演示数据好不好看"。页面标题会被强制改成
「（DEMO · 合成样例数据，非真实指标）」—— **看板是用来做决策的**，
一张看起来像真的、数字却是编的看板，比没有看板更危险。

---

## 3. 逐块讲解

### 3.1 W5-1 指标口径收口（`app/agent/eval/metrics.py`）

阶段 4 的口径是内联在 `runner.py::run_full_eval` 里的两行 if/else，
阶段 5 收口成三个纯函数，各自管一类"要不要计入"：

| 函数 | 管什么 |
| --- | --- |
| `refusal_correct(kind, refused)` | 拒答口径（见 2.1） |
| `citation_rate_for(kind, answer, refused=)` | 引用率口径：拒答 / recommend / plan 记 None，其余真算（豁免名单与 self_check 共用，见 `app/utils/intent.py`） |
| `parse_faithfulness(raw)` | judge 输出的严格解析 |

`UNSCORED_CITATION_KINDS = frozenset({"recommend", "plan"})` 显式列出
"不计引用率"的 kind。推荐类（群体先验）和规划类答案允许没有逐句引用 ——
强求"第二天去西塘[3]"是没有意义的，规划的质量看合理不合理，不看引用。

`EvalRow` 新增 `faithfulness`（judge 分）与 `refuse_reason`
（`no_evidence` / `self_check_failed`，报告里显示拒答原因，不入库）。
`aggregate()` 里 faithfulness 参与按 kind 的均值，None 被 `_mean` 跳过。

### 3.2 judge 接线（`app/agent/eval/runner.py`）

`judge_faithfulness(pipeline, state)`：把 `state.blocks` 拼成证据文本，
用 `render("eval_faithfulness", ...)` 渲染 prompt（`{{var}}` 占位，版本化管理），
复用 `pipeline.llm` / `pipeline.settings`，`temperature=0.0`（评判要确定性）、
`max_tokens=8`（只够输出一个数字，逼模型别解释）。

调用失败按 None 计并告警 —— judge 是附加指标，不该带崩整场评测
（`tests/test_eval_metrics.py` 里有"fake chat 抛异常 → None 且不崩"的用例）。

开关在 `run_full_eval` 里把关：`state.refused or not settings.eval_llm_judge`
→ 不调用，直接 None。

### 3.3 归因对比（`app/agent/eval/compare.py`，新文件）

纯 dataclass + 纯函数，**不 import SQLAlchemy**：

- `ResultRecord` —— runner 从 ORM 搬过来的一条扁平结果；
- `percentile(values, q)` —— 最近秩（见 2.4）；
- `build_compare_rows(records)` —— 分组、排序、均值、P95、箭头；
- `render_compare_markdown(rows, color=)` —— Markdown 表格，
  `color=True` 时箭头套 ANSI 色（`print_compare` 按 `sys.stdout.isatty()` 决定）。

### 3.4 落库与迁移

`EvalResult.faithfulness`（迁移 0007，可空 Float）。**默认 None 而不是 0**：
judge 默认关闭意味着大量行"未评"，存 0 会把"没评过"混进"评为幻觉"，
忠实度均值直接失真。None 的语义是"未评"，聚合时跳过。

`PROMPT_VERSION` 随 prompt 改动递增：5.1（新增 judge prompt）→ **5.2**（system.md
禁注脚规则）→ **5.3**（system.md 禁"有证据却说无法回答"类废话）。
PROJECT.md §五：改 prompt 必须同步版本，否则归因表会把两次不同的行为混成一组，
环比箭头也就失去意义。

### 3.5 评测集扩容（`backend/data/eval_cases.jsonl`）

26 → 29 行：新增 1 条 rag（厦门清晨，验证新增 trip t9 可召回）+ 1 条 plan
（江南古镇两日规划，验证规划类不计引用率、只要求生成合理）。
阶段 5.2 再修正：把"帮我照着上次那趟再规划一个类似的路线。"从 rag **改判为 plan**
（规划题引用不出未来行程，标 rag 会被 self_check 误杀，详见 §2.1），
并补 1 条 rag（"去年四月我在杭州做了什么？"→ t2）保住被改走那条的检索覆盖。
最终构成：**22 rag + 4 refusal + 1 recommend + 2 plan**，
`test_eval_metrics.py::test_dataset_contract` 钉死这个构成，
并专门断言"含'规划/推荐/该去哪'的问题不许是 rag"。

### 3.6 W5-3 可观测看板（`app/agent/eval/dashboard.py` + `app/scripts/export_dashboard.py`）

依旧是「纯函数 + 脏活分离」的两层结构，与 `compare.py` / `runner.py` 完全一致：

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 纯函数 | `app/agent/eval/dashboard.py` | `build_dashboard_data()` 聚合（复用 `build_compare_rows`，额外算 P50）、`render_dashboard_html()` 渲染。**不 import SQLAlchemy、不碰文件系统** |
| 脏活 | `app/scripts/export_dashboard.py` | 读 `eval_result` → 搬成 `ResultRecord` → 调上面两个函数 → 写 HTML |

聚合**复用** `build_compare_rows` 而不是另写一份分组逻辑：看板与终端表格
不能有两套聚合口径，两套口径迟早给出两个答案。
看板只多算一个 P50 —— 为此 `CompareRow` 新增 `p50_latency_ms` 字段
（Markdown 表不渲染它，所以 `--compare` 的输出一字未变）。

渲染用标准库 `string.Template`（**不引 jinja2**：这份模板只需要替换 5 个占位符，
为一个 200 行的脚本加一个运行时依赖不划算）。
模板里刻意不出现 `${}`（那是 JS 模板字面量，会和 `string.Template` 撞车），
JS 全部用字符串拼接。

**逃逸是硬要求**，因为分组名来自数据库 = 不可信输入：

- HTML 文本 → `html.escape`；
- 内嵌 JSON → `json.dumps(...).replace("<", "\\u003c")`。
  少了这一步，`</script>` 出现在 `prompt_version` 里就能截断整个脚本块。

`tests/test_eval_dashboard.py` 里 `test_untrusted_group_name_is_escaped` 钉死这两条。

ECharts 数据里的 `None` 原样落成 `null`：ECharts 会自动断线，
于是「上一轮没开 judge，忠实度不可比」显示成**缺口**，
而不是被插值成一条假的平滑曲线 —— 和 §2.3「不可比就不给箭头」是同一个约定。

`Makefile` 新增两个目标：

```makefile
dashboard:        # 读库导出 → backend/data/dashboard.html
dashboard-demo:   # 合成样例 → backend/data/dashboard.demo.html（页面标题注明 DEMO）
```

---

## 4. 文件清单

| 文件 | 作用 | 对应知识点 |
| --- | --- | --- |
| `app/agent/eval/metrics.py` | 指标口径收口：`refusal_correct` / `citation_rate_for` / `parse_faithfulness` | 指标设计、口径分层 |
| `app/utils/intent.py`（新，5.3） | 意图分类 + 豁免名单（`UNSCORED_INTENTS`），self_check 与 eval 共用 | 分层红线（nodes 不能 import agent.eval）、确定性规则 |
| `app/agent/state.py` + `nodes/pipeline/query_parse.py` | `ParsedQuery.intent` 字段与规则分类填写（降级时也填） | 双运行时共享节点 |
| `app/agent/nodes/pipeline/self_check.py` | plan/recommend 豁免逐句引用（编号越界照查） | 红线与豁免的边界 |
| `app/agent/prompts/system.md` | 铁律 6/7：禁注脚与免责声明、禁"有证据却说无法回答" | prompt 工程 |
| `app/agent/eval/compare.py`（新） | 归因对比纯函数：分组 / P95 / 箭头 / Markdown 渲染 | 分位数、环比、可测性 |
| `app/agent/eval/runner.py` | judge 接线（plan/recommend 不送）、口径换纯函数、`--compare` 重写 | 评测执行、LLM-as-a-Judge |
| `app/agent/prompts/eval_faithfulness.md`（新） | judge prompt（`{{var}}` 占位，版本化） | prompt 管理 |
| `app/agent/prompts/loader.py` | `PROMPT_VERSION` → `pv2026.10-stage5.3` | 版本归因 |
| `app/core/config.py` | `eval_llm_judge` 开关（默认关） | feature flag、成本控制 |
| `app/models/eval.py` + `alembic/versions/0007_*.py` | `faithfulness` 列 + 迁移 | schema 演进、可空列语义 |
| `data/eval_cases.jsonl` | 26 → 29 行（+1 rag +1 plan；1 条 rag 改判 plan） | 评测集构成 |
| `tests/test_eval_metrics.py`（新） | 口径 + judge 接线 + 数据集契约（替身，不烧 token） | Mock、回归防护 |
| `tests/test_intent.py`（新，5.3） | 分类器行为（含"行程"反例）+ 与数据集标注一致 | 启发式的兜底测试 |
| `tests/test_self_check.py` | 意图豁免：跳过引用校验但越界照查、fact 不受影响 | 豁免边界回归 |
| `tests/test_eval_compare.py`（新） | 分组 / P95 / 箭头 / 排序 / 渲染全纯数据 | 不依赖 DB 的单测 |
| `app/agent/eval/dashboard.py`（新，W5-3） | 看板纯函数层：聚合 + HTML 渲染（Pico.css / ECharts CDN，数据内联） | 可观测性、静态产物 |
| `app/scripts/export_dashboard.py`（新，W5-3） | `make dashboard`：读 `eval_result` → 写 `data/dashboard.html`；`--demo` 合成样例 | 脚本入口、离线可验证 |
| `app/agent/eval/compare.py` | 新增 `CompareRow.p50_latency_ms`（Markdown 表不受影响） | 口径复用，避免两套聚合 |
| `Makefile` | `dashboard` / `dashboard-demo` 两个目标 | 出口条件入口 |
| `tests/test_eval_dashboard.py`（新） | 聚合口径 / 渲染契约 / 内嵌 JSON 可解析 / XSS 逃逸 | 不依赖 DB 的单测 |
| `backend/data/dashboard.html` | 真实数据看板产物（`make dashboard` 覆盖写） | 交付物 |
| `.env.example` | `EVAL_LLM_JUDGE=false` 示例条目 | 配置即文档 |

---

## 5. 怎么跑起来验证

```bash
cd backend

# ① 离线口径测试（不连库、不烧 token）——第一道门
python -m pytest tests/test_eval_metrics.py tests/test_eval_compare.py \
                 tests/test_eval_dashboard.py tests/test_intent.py tests/test_self_check.py -q
# 期望：60 passed（口径 12 + 归因 9 + 看板 14 + 意图分类 13 + self_check 豁免 12）
#                  注：口径与意图分类文件里有 parametrize，用例数 ≠ 函数数

# ② 小样本全量（含 judge 演示；mock 下 judge 也会走替身链路）
EVAL_LLM_JUDGE=true python -m app.agent.eval.runner --full --limit 5
# 期望：报告每行带「忠实度=1.00」，拒答行带「（拒答：no_evidence）」

# ③ 归因对比（需要历史 EvalResult；至少跑两次不同 PROMPT_VERSION 才有箭头）
python -m app.agent.eval.runner --compare
# 期望：Markdown 表格 + 环比 ↑/↓；空库输出「暂无历史评测结果，先跑一次：make eval」

# ③′ 可观测看板（W5-3 出口条件）
cd .. && make dashboard           # → backend/data/dashboard.html
make dashboard-demo               # 无数据库时验证看板本身 → data/dashboard.demo.html
# 期望：✓ 看板已生成；双击 HTML 能看到 汇总卡片 / 归因表（↑↓）/ 延迟 P50-P95 / 两张 ECharts
# 无库且不加 --demo 时：打印「数据库不可达…」并 exit 1，而不是抛一屏连接栈

# ④ 质量门禁
python -m ruff format app tests && python -m ruff check . && python -m mypy app && python -m pytest -q
```

常见报错：

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `✗ 数据库不可达` | 没起 Docker | 本段只验证口径用 ①；要跑 `--full` / `--compare` / `make dashboard` 先 `make up && make migrate && make seed-demo`；只想验证看板本身用 `make dashboard-demo` |
| 看板打开后没有图 | 离线，CDN 取不到 ECharts | 预期降级：卡片与表格照常可读，只有图表块空着。要离线看图就把 `echarts.min.js` 下载到本地并改模板里的 `src` |
| 忠实度全是 None | `EVAL_LLM_JUDGE` 没开，或该条被拒答 | 开开关再跑；拒答行本就不评 |
| judge 告警「忠实度评判失败」 | LLM 调用异常 | 不阻塞评测；查日志里的具体错误 |
| `--compare` 没有箭头 | 只有一组历史结果，或两侧指标相等 / 一侧 None | 改一次 prompt（升 PROMPT_VERSION）再跑一次 |

---

## 6. 踩过的坑 / 易错点

1. **self_check 兜底话术的引用率陷阱**（阶段 4 遗留，本段用回归用例钉死）：
   `SELF_CHECK_FAILED_ANSWER` 里有"你可以把问题说得更具体一些"这种非软句，
   直接算 `citation_rate()` 得 0.0。解法不是改话术，而是拒答时记 None ——
   口径上"拒答不参与引用率"，`test_eval_metrics.py::test_citation_rate_for_self_check_fallback_is_none`
   防复发。
2. **judge 严格解析**：模型爱带解释（"1，因为…"）、爱输出多个数字（"1 0"）。
   `parse_faithfulness` 用 `fullmatch` 只认单独一个 0/1 + 可选句读，
   其余 None。宁可少一次有效样本，不要一个猜出来的指标。
3. **judge 失败不带崩评测**：`judge_faithfulness` 的调用包在 try 里，
   异常 → 告警 + None。评测跑两个小时，最后因为一次 judge 超时整个作废，最憋屈。
4. **`Settings.model_construct()` 造测试配置**：测试里要"只改一个开关"的 Settings，
   直接 `Settings()` 会去读 .env 与真实环境；`model_construct(eval_llm_judge=True)`
   绕过环境读取，其余字段保持默认，确定性最好。
5. **P95 的 None 传播**：任一侧为 None 就不给箭头。老版 SQL 版对比表把
   "没开 judge"的组和"开了 judge"的组直接摆在一起，忠实度一列一个数一个 `-`，
   没有箭头反而看不懂 —— 现在的约定是"不可比就不比"。
6. **模型的"画蛇添足"会被 self_check 误杀**（阶段 5.2 真实事故）：问"去年秋天去过
   哪些古镇"，模型生成了一句"注：证据显示年份为 2025 年……"—— 这句注脚没有 `[n]`，
   self_check 判"缺少引用"，最终整段答案被拒答（用户实测）。
   解法在 **prompt 层**：`system.md` 铁律第 6 条禁止注脚/免责声明/补充说明，
   并明确"相对时间直接按证据里的年份陈述，不要解释换算过程"；
   同时把 `PROMPT_VERSION` 升到 `pv2026.10-stage5.2`（归因表靠它区分两次行为）。
   **没有**去软句白名单里加"注"—— 那等于给"不带引用的事实句"开闸，
   100% 引用率红线会被这句例外蛀空。prompt 管行为，self_check 管红线，各管各的。
7. **规划题被标成 rag**（阶段 5.2 真实事故，设计分析见 §2.1）：题目标错比指标算错
   更致命 —— 指标会把"标错的题"记成"系统的错"，把人引向错误的方向。
   `test_dataset_contract` 现在专门断言"含'规划/推荐/该去哪'的问题不许是 rag"，
   且 `refusal_correct` 对 recommend/plan 恒为 None（`test_refusal_correct_unscored_kinds_always_none` 钉死）。
8. **看板数字与终端表格必须同源**（W5-3 设计约束）：看板的分组、均值、P95、箭头
   全部复用 `build_compare_rows`，**没有**另写一份聚合。曾经最容易犯的错就是
   "看板里顺手再算一遍"，于是终端说 P95=12210、看板说 P95=11800，
   两个出口两个答案，谁都不知道该信谁。看板只多算一个 P50，
   为此把 `p50_latency_ms` 加进 `CompareRow`（Markdown 表不渲染它，`--compare` 输出不变）。
9. **跨版本平均是错的**（W5-3 口径决策，见 §2.7）：看板顶部卡片取**最新一组**而不是
   全部历史平均 —— 0.80 与 1.00 平均出来的 0.90 既不代表旧版也不代表新版，
   却会被当成"系统当前的水平"。判据很简单：**一个数如果不对应任何一次真实运行，
   它就不该出现在看板上**。
10. **内嵌 JSON 必须转义 `<`**（W5-3）：`json.dumps` 只保证 JSON 合法，
    不保证它待在 `<script>` 里安全。`prompt_version` 若含 `</script>` 会直接截断脚本块，
    页面从此 JS 全废且不报错。所以 `_chart_payload` 额外把 `& < >` 转成 `\u00xx`。
    这不是"防黑客"，是防**脏数据把看板弄成一个静默损坏的页面**。
11. **豁免必须两层一起改**（阶段 5.3 真实事故）：5.2 只把指标口径改成
   "plan/recommend 不计引用率"，`SelfCheckNode` 却仍按事实题检查 ——
   指标说不杀、运行时照杀，plan 题继续被误杀。**改口径时要问一句"还有谁在执行这个口径"**：
   这次是 self_check，下次可能是 API 层的某个校验。豁免名单因此抽到
   `app/utils/intent.py` 由两层共用（nodes 不能 import agent.eval，分层红线，
   `tests/test_layering.py` 守着这条）。

---

## 7. 动手练习

1. **看 judge 生效**：`EVAL_LLM_JUDGE=true` 跑一次 `--full --limit 5`，
   对比报告的忠实度列与引用率列。然后手动改 `eval_faithfulness.md` 的第一行指令
   （比如把"只输出 1 或 0"改成"只输出是或否"），**记得同步升 `PROMPT_VERSION`**，
   再跑一次 —— 观察 judge 输出解析失败（全 None）与归因表里的版本变化。
2. **看箭头**：跑两次 `--full`（第一次后手动改 `PROMPT_VERSION` 字符串再跑第二次），
   然后 `--compare`。如果两次指标完全一样，把 `retrieval_vector_weight` 从 0.65 调到 0.5
   再跑一次，看 Recall 的箭头朝哪边。
3. **验证最近秩**：在 `tests/test_eval_compare.py` 里加一条
   `percentile([1.0] * 10 + [100.0], 0.95)` 的断言（先手算：11 个样本，
   rank = ceil(0.95 × 11) = 11 → 100.0），再跑测试确认。
4. **把看板弄坏一次（W5-3）**：`make dashboard-demo` 生成后，
   在 `dashboard.py::_CARD_METRICS` 里把 `p95_latency_ms` 的小数位从 `0` 改成 `3`，
   重新生成，看延迟卡片变成 `12210.000` —— 体会一下"卡片元数据表驱动"改一处就全局生效。
   然后把某个分组名改成 `</script><script>alert(1)</script>`（直接改 `--demo` 的
   `demo_records()` 即可），重新生成并在浏览器里看源码：
   `test_untrusted_group_name_is_escaped` 守的就是这个口子。
5. **验证"最新一组"口径（W5-3）**：往 `--demo` 里加第三组 `stage5.4`（Recall 故意设很低），
   重新生成看板，确认顶部卡片跟着变成 5.4 的数 —— 而不是三组平均。
