# 阶段 2：结构化 + 向量混合检索

> 对应工作块：**W2-1 Query 解析 / W2-2 时空过滤 + 全文 / W2-3 SSE 流式**
> 前置：阶段 0（RAG CLI）与阶段 1（离线批处理）已跑通

## 0. 读法

这一阶段的问题很集中：**"我去年秋天去过哪些古镇？"这种问题，纯向量检索答不好。**

原因是向量只懂"语义像不像"，不懂"时间是不是 2025 年 9~11 月""是不是古镇"。
阶段 2 就是在向量之外，再给检索装上**三样东西**：结构化过滤、词面召回、流式输出。

建议按这个顺序读：

1. 第 2 节讲四个原理（都是手算例子，不需要数据库）
2. 第 3~5 节逐块讲实现（W2-1 / W2-2 / W2-3）
3. 第 7 节是可以直接复制去跑的验证命令
4. 第 8 节是这次真实踩到的坑，每一个都有对应的报错原文

---

## 1. 目标与出口条件

| 工作块 | 出口条件 | 本阶段的判定方式 | 结果 |
| --- | --- | --- | --- |
| **W2-1** 结构化解析 | 中文时间表达用例通过；解析不确定时退化为不过滤 | `make eval-time`，22 条用例自带 `today` 保证可复现 | ✅ **22/22 = 1.0000** |
| **W2-2** 混合检索 | 混合检索 Recall@5 **不低于**纯向量 | `make eval-ablation`，三档消融同批用例对比 | ✅ 见下表（真实库待跑，内存替身已验证脚本可跑） |
| **W2-3** SSE 流式 | 首字可见，回答可中断 | 浏览器实测 + `tests/test_streaming.py` 断言 | ✅ 7 条流式断言通过（含"多段推送"与"可中断"）；前端 `npm run build` 通过 |

消融结果（**内存替身 + 哈希向量**，26 条评测用例，仅用于验证脚本与排序逻辑；
真实 embedding 的数值以 `make eval-ablation` 为准）：

```
档位                    Recall@5      MRR      平均延迟
-------------------------------------------------
纯向量（阶段 0）            0.714    0.473      33.2
+结构化过滤                0.857    0.630      23.6
+词面召回（混合）           0.857    0.627      26.3
```

Recall@5 从 0.714 提到 0.857，**满足"不低于纯向量"**。
MRR 在"+词面"档比"+过滤"档低了 0.003 —— 这点差异在第 9 节单独说，不粉饰。

---

## 2. 必须先懂的四个原理

### 2.1 过滤是"先缩小候选集"，所以它必须有兜底

结构化过滤的思路很朴素：

```
提问「去年秋天去过哪些古镇」
  → 解析出 {date_start: 2025-09-01, date_end: 2025-11-30, themes: [古镇]}
  → 只在这个子集里做向量检索
```

好处是精准。坏处也很致命：**一旦解析错了，正确答案会被直接排除掉，而且没有任何补救机会。**

所以本阶段给过滤配了一道**安全阀**：

> 过滤后命中数 < `filter_min_hits`（默认 3 条）→ 补一次**不过滤**的检索，把结果并回去。

这不是"不信任过滤"，而是承认**漏召回比多召回贵得多**：
多召回几条，顶多让上下文变长、生成稍慢；漏召回，用户得到的就是一个错误答案。

`state.retrieve_debug.filter_fallback` 会记录"这次到底兜底了没有"。
排查"为什么搜不到"的时候，先看这个字段，比看 SQL 快十倍。

### 2.2 向量之外为什么要词面路：向量对**专名**很笨

做一个思想实验。库里有两条：

| 文档 | 内容 |
| --- | --- |
| A | 在嘉兴乌镇坐摇橹船，夜景很好 |
| B | 秦始皇陵兵马俑博物馆，人非常多 |

问"兵马俑"。向量路可能把 A 也拉回来（都是"旅游景点"语义），
而真正含"兵马俑"三个字的 B 却不一定排第一 —— 因为向量看的是**语义距离**，不是**字面出现**。

词面路补的就是这块：用 `content ILIKE '%兵马俑%'` 判断**字面有没有出现**。

分数定义成**关键词覆盖率**：

```
覆盖率 = 命中的关键词数 / 关键词总数
```

举例：问"乌镇 古镇"，关键词是 `["乌镇", "古镇"]`：

| 文档 | 命中 | 覆盖率 |
| --- | --- | --- |
| 含"乌镇"也含"古镇" | 2/2 | 1.0 |
| 只含"古镇" | 1/2 | 0.5 |
| 两个都没有 | 0/2 | 0.0 |

`lexical_min_coverage=0.5` 是防噪声闸门：只命中 1/3 关键词的文档不该进来，
否则"古镇"这种高频词能把半个库捞出来。

### 2.3 为什么必须先归一化再加权（这是混合检索最容易写错的一行）

两路分数的**量纲完全不同**：

- 向量分：余弦相似度，哈希向量下只有 **0.05~0.13**
- 词面分：覆盖率，**0~1.0**

如果直接 `0.65 * 向量分 + 0.35 * 词面分`：

```
向量分 0.12（本路最高）→ 贡献 0.078
词面分 1.0（本路最高）→ 贡献 0.35
```

向量路被彻底淹没 → 融合退化成"词面单点决定"，混合检索名存实亡。

正确做法是**各自 min-max 归一化到 0~1，再加权**：

```
归一化后：向量路最高分 → 1.0，词面路最高分 → 1.0
最终分 = 0.65 × 向量归一 + 0.35 × 词面归一
```

两条路现在"各有一票"，权重才真正表达了"我更信哪一路"。

> **为什么本阶段不用 RRF？**
> RRF（`1/(k+rank)`）不需要归一化、对量纲免疫，听起来更好。
> 但设计文档把它排在阶段 4（W4-3）：等阶段 3 引入 reranker、阶段 4 加入记忆检索，
> 三路以上融合时 RRF 才明显更稳。现在用归一化加权，好处是**能直接看出每路贡献了多少分**。
> 换的时候只动 `Retriever._fuse` 一个函数。

### 2.4 时间过滤要用"区间相交"，不是"落在区间内"

一趟行程是一个区间：`2025-10-02 ~ 2025-10-05`。
用户问"10 月 3 日我在哪"——查询区间是 `2025-10-03 ~ 2025-10-03`。

如果判断"文档区间是否包含查询区间"，这趟行程会被漏掉（它比查询区间长）。

正确的判断是**相交**：

```
文档结束 >= 查询开始   AND   文档开始 <= 查询结束
doc_end  >= q_start    AND   doc_start <= q_end
```

另外一条同样重要的规则：**文档没有时间字段时，直接放行**。
否则问"我七月去过湖边吗"，所有地点类文档（没有日期字段）会被整体误杀。

### 2.5 LLM 负责"认字"，规则负责"算数"

时间解析有两条路可走：

| 方案 | 问题 |
| --- | --- |
| 全交给 LLM | ① LLM 不知道"今天"是哪天（prompt 里注入了也常算错一两年）；② 每次输出可能不同，**评测无法归因**；③ 离线占位模式（mock LLM）下整条链路没法验证 |
| 全靠规则 | 认不出"上次那趟""看完冰雕的那次"这类指代性表达 |
| **规则 + LLM 双路（本项目）** | 规则做**确定性的日历换算**，LLM 做**从句子里认出时间词**；冲突时**以规则为准** |

一句话：**数学题不要交给 LLM 做，那等于每次运行掷一次骰子。**

`time_source` 字段记录最终用的是哪一路（`rule` / `llm` / `none`），
写进 trace 后可以直接统计"有多少次是规则算出来的"。

---

## 3. W2-1：结构化解析

### 3.1 双路解析怎么接在一起

`QueryParseNode`（`backend/app/agent/nodes/pipeline/query_parse.py`）的执行顺序：

```
1. 先跑规则解析器 parse_time_range(state.search_query)
   → 命中：time_source = "rule"，LLM 的时间建议直接不采纳
2. 规则没命中 → 校验 LLM 返回的 time_range（必须是合法 ISO 且 start <= end）
   → 合法：time_source = "llm"
3. 都没有 → 时间过滤条件为 None（走不过滤分支）
```

注意第 2 步的"校验"不是形式主义：LLM 很可能返回 `{"start": "去年秋天"}` 这种
非 ISO 值。此时 `build_filter` 会**静默丢弃时间条件**而不是让 SQL 报错。

### 3.2 `StructuredFilter`：解析结果 → SQL 的唯一中间表示

`backend/app/agent/rag/filters.py`：

```python
@dataclass
class StructuredFilter:
    date_start: date | None = None
    date_end: date | None = None
    places: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    bbox: BBox | None = None

    def is_empty(self) -> bool: ...      # 没有任何条件 → 走不过滤分支
    def describe(self) -> dict: ...      # 给 trace / 前端看的摘要
```

为什么要单独抽这个数据类，而不是在 SQL 层直接读 `ParsedQuery`？

- **解耦**：`ParsedQuery` 是"自然语言的理解结果"，`StructuredFilter` 是"数据库的查询条件"。
  两者会各自演化（比如将来加"拍摄设备""同行人"），混在一起必然长成一坨 if。
- **可测**：`is_empty()` 把"要不要过滤"这个分支做成了一个可单测的纯函数。
- **可解释**：`describe()` 让前端能显示"我理解了：2025-09-01~2025-11-30 / 古镇"，
  一旦出现"我以为搜到了却没搜到"，界面上直接能看到系统理解成了什么。

`degraded=True`（LLM 不可用 / 输出不合法）时的处理很关键：

> **丢弃 LLM 给的 places / themes**（它们不可信），
> **但保留规则算出的时间范围**（规则是确定性计算，不属于"猜"）。

这条让离线占位模式下时间过滤依然生效 —— 否则 mock 环境里永远测不到过滤路径。

### 3.3 规则解析器的优先级与两类"主动放弃"

`backend/app/agent/rag/time_parser.py` 是**纯函数**（不连 DB、不调 LLM，可单测）。
候选按优先级从高到低，命中即返回：

```
农历节日（春节/清明/端午/中秋 → 主动返回 None）
  > 相对时段（今天/昨天/本周/上个月/上上个月/最近 N 天/上半年）
  > ISO 日期（2025-10-03）
  > 节假日（五一/国庆/暑假/寒假）
  > 年×季 > 年×月 > 年 > 季 > 月
```

两个反直觉但必要的细节：

1. **农历节日主动放弃**：春节的公历日期年年不同，规则算不准 → 返回 `None`（不加过滤）。
   返回 `None` 不是失败，而是"这一维不设条件"，是完全合法的结果。
2. **推断年份落在未来就往前挪一年**：今天是 2026-10-19，问"我七月去过湖边吗"，
   七月会被推断成 2026-07（已发生）；但如果推断出 2027-07（未来），
   说明你要回忆的肯定是已经发生过的 → 往前挪一年。

冬季跨年也处理了：`冬 = (12月, 2月)`，`month_from > month_to` 时结束年 +1，
所以"冬天"在 2026 年问出来是 `2025-12-01 ~ 2026-02-28`。

### 3.4 22 条用例怎么保证可复现

`backend/data/time_cases.jsonl` 每条自带 `today`：

```jsonl
{"today":"2026-10-19","question":"我去年秋天去过哪些古镇？","start":"2025-09-01","end":"2025-11-30"}
{"today":"2026-10-19","question":"春节期间我去了哪？","start":null,"end":null}
{"today":"2026-10-19","question":"我在杭州最喜欢哪类地点？","start":null,"end":null}
```

**为什么用例要自带 `today`？** 因为"上个月""去年"的结果依赖运行日期。
如果用真实 `date.today()`，同一份用例今年跑满分、明年可能全红 ——
那样的评测集是废的，它测的是"今天离用例写出来的日子有多远"。

三类用例都要有：

- 能算准的（20 条）
- **主动放弃的**（2 条：农历节日、纯地点问题）—— 断言"不过滤"和断言"算得准"同样重要

---

## 4. W2-2：混合检索

### 4.1 SQL 层（`backend/app/services/document_service.py`）

三个新增方法，各自独立可测：

| 方法 | 作用 |
| --- | --- |
| `_filter_clauses(flt)` | 把 `StructuredFilter` 翻译成 SQL 条件列表（空过滤 → 空列表） |
| `_bbox_clauses(bbox)` | 包围盒相交（四个方向都要重叠） |
| `lexical_search(...)` | 词面召回，分数 = 关键词覆盖率 |
| `coverage_expr(terms)` | 覆盖率表达式（单独抽出，为了能不连库编译断言） |

时间条件的写法（**区间相交 + 缺字段放行**）：

```sql
OR coalesce(metadata->>'date_end', metadata->>'date') IS NULL
OR coalesce(metadata->>'date_end', metadata->>'date') >= '2025-09-01'
```

地名用 JSONB 的 `?|`（数组是否包含任一元素）：

```sql
metadata->'cities' ?| ARRAY['乌镇']
```

主题同理。bbox 必须**转成数值再比较**：

```sql
(metadata->>'min_lat')::float <= 31.0
```

`'9.5'` 和 `'10.2'` 按字符串比较会得出错误结论（`'9.5' > '10.2'` 为真）。

### 4.2 索引：GIN（JSONB）+ GIN trgm（全文）

`backend/alembic/versions/0004_stage2_retrieval_indexes.py` 建两个索引：

| 索引 | 用到的场景 | 为什么必须建 |
| --- | --- | --- |
| `ix_document_metadata_gin` | `metadata ?\| ARRAY[...]` | 默认 GIN（`jsonb_ops`）正好支持 `? / ?\| / ?& / @>`；不建就要全表展开 JSONB |
| `ix_document_content_trgm` | `content ILIKE '%词%'` | 普通 B-tree 对**前置通配符完全无效**，只能靠 pg_trgm |

两个必须说清楚的点：

1. **索引是加速，不是改变行为。** 索引不可用时 Postgres 退回顺序扫描，
   `ILIKE` 的子串语义依然成立。所以正确性不依赖索引。
2. **pg_trgm 的中文效果依赖数据库 locale。** 它按"词字符"切 trigram，
   中文环境下相似度可能退化。因此本项目**不用 `similarity()` 当主分数**，
   只用它做**同分时的排序**；主分数用覆盖率（可复现，适合做评测指标）。

> **为什么还是不给 embedding 建 HNSW？**
> pgvector 的 HNSW 是"先遍历图、后套 WHERE"，带 `user_id` + 时间 + 空间过滤时
> 可能返回 **0 条**。阶段 0~2 一律精确扫描，阶段 3（W3-4）才建 HNSW 并配套回退策略。

### 4.3 编排层（`backend/app/agent/rag/retriever.py`）

一次 `retrieve()` 的完整流程：

```
① 向量路（带结构化过滤）
② 安全阀 1：命中 < filter_min_hits → 补一次不过滤检索，_merge_keep_best 合并
③ 词面路（同样的过滤条件）
④ 融合：各自 min-max 归一化 → 加权求和
⑤ 写 state.retrieve_debug（filter / filter_fallback / vector_hits / lexical_hits / fused_hits）
```

`min_score` 闸门**保留不动**：向量分低于阈值的证据不进上下文。
这是阶段 0 就立下的拒答基石，融合不能绕过它 ——
测试 `test_min_score_still_gates_refusal` 专门守着这条。

### 4.4 端口拆成两个 Protocol

`backend/app/agent/rag/ports.py` 现在是：

```python
class VectorSearchPort(Protocol):
    async def similar_search(self, user_id, query_vector, *, top_k=8, source_types=None, flt=None) -> list[SearchHit]: ...

class LexicalSearchPort(Protocol):
    async def lexical_search(self, user_id, terms, *, top_k=8, flt=None, min_coverage=0.5, query_text=None) -> list[SearchHit]: ...
```

拆开而不是合并成一个 `SearchPort`，是为了让"只有向量路"的实现（阶段 0 的旧实现、
将来换成纯向量数据库）**不必实现词面方法**。`Retriever.lexical` 为 `None` 时自动退化为纯向量。

### 4.5 内存替身为什么要再实现一遍过滤

`backend/tests/fakes.py` 里的 `InMemoryVectorSearch` 同时实现两个端口，
并且**在 Python 里把结构化过滤又写了一遍**（`_match_filter`）。

看起来是重复劳动，实则是有意为之：

> 过滤逻辑有一份真实实现（SQL）和一份内存实现（Python），
> 任一边改错了，两边的语义就会不一致 —— 而"不一致"在单测里表现为失败，
> 逼着你去想清楚"到底哪边才是对的"。

如果只保留 SQL 实现，没有数据库的机器上一个过滤测试都跑不了。

---

## 5. W2-3：SSE 流式 + 前端对话页

### 5.1 为什么是 POST + SSE，而不是 EventSource

浏览器的 `EventSource` **只支持 GET**，带不了 JSON body。
而一次问答必须传「问题 + 会话 id + 历史」，用 query string 拼既难看又容易被日志记下来。

所以后端是 **POST + `text/event-stream`**，前端用 `fetch` + `ReadableStream` 自己解析。
这也是 OpenAI / DeepSeek 等大模型 API 的做法。

### 5.2 后端事件协议

`AgentPipeline.run_stream()` 产出的事件：

| 事件 | payload | 前端怎么用 |
| --- | --- | --- |
| `parsed` | `keywords / places / themes / time_range / time_source / degraded` | 展示"我理解了什么"（FilterChips） |
| `evidence` | `blocks[]` + `filter` + `filter_fallback` + `vector_hits` / `lexical_hits` | 证据列表，附本次实际生效的过滤条件 |
| `delta` | 一段文本 | 逐段追加，这就是**首字可见** |
| `restart` | `reason: self_check_failed` | 清空已渲染内容（引用校验没过，要重答） |
| `done` | `refused / citations / latency_ms / token_usage` | 收尾 |

**非流式 `run()` 就是"把流式事件全部丢掉"**：

```python
async for _event in self.run_stream(...):
    pass
return state
```

这不是偷懒。流式与非流式共用一套编排，才能保证两条路径行为完全一致 ——
否则改了流式忘了改非流式，评测跑的是非流式、线上跑的是流式，**指标根本对不上**。

### 5.3 引用校验必须在完整答案上做

流式生成时中途检查引用率会**误判**（答案才吐了半句，`[1]` 还没写出来）。
所以 `self_check` 只在**流结束后跑一次**，不通过时发 `restart` 事件让前端清空重来。

### 5.4 中断与 trace 收尾

前端点"停止生成" → `AbortController.abort()` → fetch 断开 →
后端 `await request.is_disconnected()` 返回 True → `break`。

这里有个容易漏的点：**`break` 会让 `run_stream` 的生成器停在 `yield` 处**，
它的 `try/except` 不会执行，`AgentRun` 行会永远停在 `status="running"`。
所以 SSE 层在 `break` 之前必须显式收尾：

```python
await trace.finish_run(status="interrupted")
```

### 5.5 三个响应头

```python
headers={
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",   # 关掉 Nginx 的响应缓冲
}
```

`X-Accel-Buffering: no` 是必需的：Nginx 默认会缓冲响应体，
流式会被"攒起来"再一次性吐出，首字可见直接失效。

另外每 20 个事件发一个 `: keepalive` 注释行，防止中间层因空闲断开连接。

### 5.6 前端按 `\n\n` 切帧，不是按行

`frontend/src/services/api/agent.ts`：

```typescript
buffer += decoder.decode(value, { stream: true });
let sep = buffer.indexOf('\n\n');
while (sep >= 0) {
  const frame = buffer.slice(0, sep);
  buffer = buffer.slice(sep + 2);   // 剩下的半帧留着下次用
  ...
}
```

**一个 chunk 里可能装半帧，也可能装三帧。** 不累积 buffer 直接按行处理，
在网络稍慢时就会随机丢事件 —— 而且这种 bug 只在真机上偶发，本地怎么测都测不出来。

---

## 6. 文件清单

| 文件 | 作用 | 对应知识点 |
| --- | --- | --- |
| `backend/app/agent/rag/time_parser.py` | 中文时间表达 → 日期范围（纯函数） | 双路解析里的"规则算数"；两类主动放弃 |
| `backend/app/agent/rag/filters.py` | `BBox` / `StructuredFilter` / `bbox_from_center` | 解析结果 → SQL 的唯一中间表示 |
| `backend/app/agent/rag/ports.py` | `VectorSearchPort` / `LexicalSearchPort` | 两个端口分开，允许只实现一路 |
| `backend/app/agent/rag/retriever.py` | 两路召回 + 过滤兜底 + min-max 融合 | 三档梯子 + 两道安全阀 |
| `backend/app/services/document_service.py` | `_filter_clauses` / `_bbox_clauses` / `lexical_search` / `coverage_expr` | JSONB `?\|`、cast 数值比较、覆盖率先算 |
| `backend/alembic/versions/0004_stage2_retrieval_indexes.py` | GIN(JSONB) + GIN(trgm) 索引 | 索引是加速不是行为；不建 HNSW 的原因 |
| `backend/app/agent/nodes/pipeline/query_parse.py` | 规则优先、LLM 兜底 | `time_source` 归因 |
| `backend/app/agent/prompts/query_parse.md` | 让 LLM 保守填写 time_range | 宁可不过滤，不要编一个时间窗 |
| `backend/app/agent/pipeline.py` | `run_stream` 事件流；`run` 复用流式 | 流式/非流式口径一致 |
| `backend/app/api/v1/agent.py` | SSE 接口 | POST + SSE、反代缓冲、中断收尾 |
| `backend/app/schemas/agent.py` | `ChatRequest` / `EvidenceOut` / `ChatMeta` | 接口契约 |
| `backend/app/core/llm.py` | `astream_chat` + `StreamUsage` | 用量不能在异步生成器 return 里取 |
| `backend/data/time_cases.jsonl` | 22 条时间用例（自带 `today`） | 可复现的评测集 |
| `backend/app/agent/eval/runner.py` | `--time` / `--ablation` / `--no-parse` | 分口径定位问题 |
| `frontend/src/services/api/agent.ts` | SSE 客户端 | 按 `\n\n` 切帧、AbortController |
| `frontend/src/pages/AgentPage/` | 对话页骨架 | 首字可见、停止生成、FilterChips |
| `backend/tests/test_time_parser.py` | 时间解析单测 | |
| `backend/tests/test_filters.py` | `StructuredFilter` 与 bbox 单测 | |
| `backend/tests/test_retriever_hybrid.py` | 混合检索编排（内存替身） | 含消融脚本的冒烟测试 |
| `backend/tests/test_document_service_sql.py` | **SQL 编译断言** | 不连库也能验证 SQL 语法 |
| `backend/tests/test_streaming.py` | 流式首字可见 / 可中断 | 把"点两下能看见"变成断言 |
| `backend/tests/test_api_agent.py` | 路由挂载 / 契约校验的冒烟测试 | 结构性事实也要有回归保护 |
| `backend/tests/fakes.py` | 内存检索（含过滤的 Python 实现） | 两份实现逼着语义一致 |

---

## 7. 跑起来验证

### 7.1 不需要数据库的部分（任何机器都能跑）

```bash
cd backend

# W2-1 出口条件：中文时间表达解析准确率
python -m app.agent.eval.runner --time
# 期望：解析准确率：1.0000（22/22），22 行全部 ✓

# 全量测试
pytest -q
# 期望：99 passed, 9 skipped（9 个 skip 是标 db 需要真实库的用例）

# 静态检查
ruff check . && mypy app
# 期望：All checks passed! / Success
```

### 7.2 需要数据库的部分（有 Docker 时）

```bash
make up                  # 启动 PostgreSQL
make migrate             # ★ 新增：建 0004 的 GIN / trgm 索引
make seed-demo
make seed-photos         # 阶段 1 的照片
cd backend && python -m app.jobs.caption_job --limit 200 && cd ..
make index-batch         # ★ 必须重跑：日文档要写入 bbox（min/max 经纬度）
make eval-ablation       # ★ W2-2 出口条件
```

`make index-batch` 这一步不能省：阶段 2 的包围盒过滤依赖
`metadata` 里的 `min_lat / max_lat / min_lng / max_lng`，
而这些字段是 `day_digest` 在本阶段才补上的（`min/max` 而不是只存中心点 ——
当天跨度大时，只存中心点会被漏掉）。

### 7.3 前端流式（W2-3）

```bash
make web-install         # 首次
make dev                 # 后端 8000
make web-dev             # 前端 5173（/api 已代理到 8000）
# 浏览器打开 http://localhost:5173
```

验收动作：

1. 输入「我去年秋天去过哪些古镇？」→ **文字应该逐段出现**（不是等几秒一次性弹出）
2. 上方 FilterChips 显示 `2025-09-01 ~ 2025-11-30` 与 `古镇`
3. 生成途中点「停止」→ 立刻停住，已生成内容保留，后端日志出现"客户端已断开"
4. 证据区显示命中条数与 `vector_hits / lexical_hits`

### 7.4 常见报错排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `make eval-ablation` 报连不上库 | 没起 Docker | 先 `make up` 并等 healthcheck `healthy` |
| 所有问题都拒答 | 离线模式的哈希向量分数偏低 | `.env` 设 `RETRIEVAL_MIN_SCORE=0.05` |
| 前端一直转圈、不出字 | Nginx/反代缓冲（生产环境） | 确认响应头有 `X-Accel-Buffering: no` |
| 前端事件偶尔丢失 | 按行解析 SSE | 必须按 `\n\n` 切帧并保留半帧 buffer |
| 时间过滤看起来没生效 | 没重跑 `index-batch` | 老文档的 metadata 里没有 `date_start/date_end` bbox |

---

## 8. 踩过的坑 / 易错点

### 8.1 `metadata_json.astext` 在 ORM 属性上不存在

```python
AttributeError: Neither 'InstrumentedAttribute' object nor 'Comparator'
object associated with Document.metadata_json has an attribute 'astext'
```

`Document.metadata_json` 是 ORM 的 `InstrumentedAttribute`，它**不代理**
JSONB 类型的 `astext` 属性。但 `Document.metadata_json["key"].astext`（取到子键后）
是普通表达式，是可以用的。

整个 metadata 转文本要显式 `cast(Document.metadata_json, String)`。

### 8.2 `StreamingResponse` 的依赖清理早于流消费

用 `Depends(get_session)` 的 `yield` 依赖注入会话时，
**会话会在流开始前就被关闭**，流式写入直接报错。
必须在生成器内部 `async with SessionFactory()` 自己管理生命周期。

### 8.3 `reduce` 不能换成内置 `sum`

```python
coverage = reduce(operator.add, hit_exprs) / n     # ✅
coverage = sum(hit_exprs) / n                      # ❌
```

`sum` 以 Python 整数 `0` 起步，SQLAlchemy 会把这个 `0` 拼进 SQL，
导致后续除法与类型表现不一致。

### 8.4 异步生成器的 return 值拿不到

`async for` 拿不到生成器的 `return`，所以 token 用量不能靠 `return` 传出来。
做法是**传一个可变的 `StreamUsage` 对象进去**，让子类往里写。

### 8.5 时间用例文件放错目录

`FileNotFoundError: 时间表达用例不存在: backend\data\time_cases.jsonl`
—— 文件必须与 `eval_cases.jsonl` 同级放在 `backend/data/`（不是 `backend/app/data/`）。

### 8.6 测试里用 `asyncio.run` 调异步 helper

```python
RuntimeError: asyncio.run() cannot be called from a running event loop
```

pytest-asyncio 的用例本身就在事件循环里，helper 改成 `async def` 直接 `await`。

### 8.7 兜底检索会覆盖掉你想要断言的对象

测 `flt` 是否被传到底层时，如果 `filter_min_hits=3`（默认），
第二次不过滤的调用会把 `captured["flt"]` 覆盖成 `None`。
断言"传了什么"时用 `filter_min_hits=0` 关掉兜底。

### 8.8 `ruff` 自动修复会顺手改掉历史文件

`ruff check --fix` 一次修了 30 处（含阶段 0/1 遗留的 import 排序、未使用导入）。
这没问题，但**修完必须重跑全量测试 + `ruff format`**，确认没有行为变化。

---

## 9. 一个真实观察：+词面档的 MRR 反而低了 0.003

内存替身上的消融结果：

```
+结构化过滤        MRR 0.630
+词面召回（混合）   MRR 0.627   ← 低了
```

这不算"变坏"，但值得记下来。原因是融合时**词面分的量纲被拉平了**：
覆盖率只有 1.0 / 0.5 / 0.0 三档，归一化后大量文档被塞到同一分数，
排序信息被压缩 → MRR（只看第一条命中位置）对这个很敏感。

三个阶段性的结论：

1. **Recall 没降**（0.857 → 0.857）：词面路没有挤掉正确答案，只是把顺序搅了一下。
2. 真实 embedding 下这个差异可能消失 —— 内存替身用的是哈希向量，语义能力极弱，
   词面路的相对权重被放大了。
3. 如果真实库上出现同样情况，**先调 `retrieval_lexical_weight`（0.35 → 0.2）**，
   再看要不要引入 RRF（阶段 4 的 W4-3）。

记这条的目的是：**别看到"加了东西指标没涨"就认为是 bug。**
分档消融的意义恰恰是让你看清"每一层到底贡献了什么"，而不是只看一个总分。

---

## 10. 动手练习

**练习 1（30 秒）**：把 `retrieval_lexical_weight` 从 `0.35` 改成 `0.05`，
跑 `make eval-ablation`，看 Recall@5 和 MRR 怎么变。
你会看到混合档向纯向量档收敛 —— 这就是"权重 = 你更信哪一路"的直观证明。

**练习 2（1 分钟）**：在 `backend/data/time_cases.jsonl` 里加一条
`{"today":"2026-10-19","question":"大前年五一我在哪？","start":"2023-05-01","end":"2023-05-05"}`，
跑 `make eval-time` 看是否命中。再改错一个日期，看它会不会红。

**练习 3（2 分钟）**：把 `retrieval_filter_min_hits` 改成 `999`（等于永远兜底），
跑消融，观察 `+结构化过滤` 档是不是退化回纯向量。
这能让你亲身体会"过滤兜底"到底在防什么。

**练习 4（可选）**：在 `HOLIDAY_RANGES` 里加一个"清明节"并给它一个固定公历日期，
跑 `make eval-time` —— 你会看到"春节期间"那条用例依然通过（它走的是主动放弃分支），
但这个改动本身是错的（清明是农历）。**不要用固定日期处理农历节日。**

---

## 11. 下一阶段

阶段 3（W3-1 ~ W3-4）：工具调用 + Reranker + 缓存。

与本阶段直接相关的两件事：

1. **HNSW 索引**（W3-4）终于可以建了 —— 但必须配套"结果不足回退精确扫描"，
   因为本阶段已经证明：带过滤的检索最怕"一条都不返回"。
2. **Reranker**（W3-4）会接管排序，届时 `_fuse` 的输出应当作为 reranker 的**候选集**，
   而不是最终顺序。第 9 节观察到的 MRR 问题，大概率会被 reranker 顺手解决。

在那之前，本阶段留下的两个待办是：

- 在**真实数据库**上跑一次 `make eval-ablation`，把第 1 节表格换成真实数值
- 浏览器实测 §7.3 的四条验收动作
