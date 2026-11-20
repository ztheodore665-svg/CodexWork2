# 阶段 0：纯文本 RAG CLI

> **一句话**：先让「问一句关于自己旅行记忆的问题 → 得到带照片证据的回答」这件事，在命令行里跑通。
> 本阶段**不碰照片、不做网页、不做地图**，只把 RAG 这条主链路和它的度量方式立起来。

---

## 0. 怎么读这份文档

- 第 2 节是**原理**，哪怕不写代码也要看懂，后面所有阶段都建立在这几个概念上。
- 第 4~6 节按工作块顺序讲「做了什么 + 为什么 + 代码在哪」，你可以边读边打开对应文件对照。
- 第 8 节是**验证步骤**，建议跟着敲一遍，看到真实输出才算学会。
- 第 9~10 节是易错点和练习，用来确认你真的理解了。

前置知识（不会也没关系，遇到再查）：Python `async/await`、SQLAlchemy 2.0 查询写法、PostgreSQL 基本 SQL、HTTP 接口调用。

---

## 1. 目标与出口条件

| 工作块 | 目标 | 出口条件（做完没做完按这个判定） |
| --- | --- | --- |
| **W0-1 数据底座** | 跑通 PostgreSQL(PostGIS + pgvector)，能写入并检索一条向量 | 两个扩展创建成功；写入并检索返回预期结果 |
| **W0-2 RAG 管道** | CLI 能回答自然语言问题，事实句必带引用 | 示例问题返回带 `[n]` 引用的答案 |
| **W0-3 评测与可观测** | 能度量召回质量，且每次问答都有 trace | 可看 trace 的每一步耗时；评测集出 Recall@5 / MRR |

阶段严格串行：**上一阶段的出口条件没满足，不进入下一阶段**（PROJECT.md §二、[7]）。

---

## 2. 必须先懂的三个概念

### 2.1 Embedding：把一段话变成一串坐标

计算机不认识"乌镇"这两个字，只认识数字。Embedding 模型做的事是：

> 输入一段文字 → 输出一个固定长度的浮点数组（本项目默认 1024 维）→ 语义相近的文字，向量在空间中离得近。

打个比方：给每段话发一张"坐标卡"，意思相近的卡片会落在同一片区域。于是"找相关资料"就变成了"找坐标最近的卡片"。

```
"2025 年秋天去了乌镇和西塘"   →  [0.02, -0.11, 0.37, ... ]  （1024 个数）
"那年秋天逛的江南古镇"         →  [0.03, -0.09, 0.35, ... ]  ← 和上面很近
"昨天写的周报"                →  [-0.21, 0.44, -0.08, ...]  ← 离得很远
```

"近"用什么量？本项目用**余弦相似度**（两个向量的夹角）：值域 -1~1，越大越相似。
pgvector 提供的 `<=>` 算的是**余弦距离 = 1 - 余弦相似度**，所以检索时要自己换算（见 4.6）。

### 2.2 RAG：开卷考试

大模型闭卷答题会"编"。RAG（Retrieval-Augmented Generation）把流程改成开卷：

1. 先把资料库里的相关内容**找出来**（检索）
2. 把找到的原文**贴进提示词**（构建上下文）
3. 让模型**只依据这些原文**作答（生成）

本项目比普通 RAG 更严：模型输出的每一句事实，必须标明来自哪条资料（`[1]`、`[2]`…），而且要能被机器校验。

### 2.3 为什么"引用率 100%"是硬指标

如果回答里出现"你在 2024 年去过巴黎"，而库里根本没有这条记录，用户就**无从判断真假**——这对"个人记忆"类产品是致命的。所以本项目立下规矩：

- 事实性句子**必须**带引用；
- 引用编号**必须**落在本轮提供的证据范围内；
- 证据不足就**明确拒答**，不允许用"可能是""大概"糊过去。

这条规矩不能靠"让模型自觉"，必须在代码里硬校验（见 5.6）。

---

## 3. 阶段 0 全景图

```
                 ┌──────────── 离线：把资料变成可检索的向量 ────────────┐
  data/*.jsonl ─▶│  chunker（切块） → embedder（向量化） → Document 表 │
                 └───────────────────────────────────────────────────┘
                                                              │
                                                       pgvector 检索
                                                              │
  ┌──────────────────── 在线：一次问答的六个环节 ────────────────▼──────┐
  │ ① rewrite     改写问题（补全指代："照着上次那趟" → 具体行程）        │
  │ ② query_parse 抽关键词（辅助向量检索；解析失败就"不过滤"）           │
  │ ③ retrieve    向量检索 + 相似度阈值过滤                             │
  │ ④ build_context 给证据编号 [1][2][3]，拼进上下文                    │
  │ ⑤ generate    带 system 规则生成答案（无证据则直接拒答，不调模型）   │
  │ ⑥ self_check  硬校验引用率；失败 → 重试一次 → 仍失败则拒答           │
  └───────────────────────────────────────────────────────────────────┘
                          │ 每一步都写 trace
                          ▼
        AgentRun / AgentStep / ToolCallLog + backend/data/traces/*.jsonl
```

---

## 4. W0-1 数据底座

### 4.1 为什么是 PostgreSQL + PostGIS + pgvector

| 需求 | 选型理由 |
| --- | --- |
| 存轨迹与时间 | PostGIS 提供地理类型与空间函数 |
| 存向量并做相似检索 | pgvector 提供 `vector` 类型与 `<=>` 距离算子 |
| 中文全文兜底（阶段 2 用） | pg_trgm 支持三元组相似度匹配 |
| 结构化过滤 + 向量检索在同一条 SQL 里 | 关系型数据库的强项，避免"两趟查询再合并"的复杂度 |

代价是要自己装扩展：官方镜像里 `pgvector/pgvector` 没 PostGIS，`postgis/postgis` 没 pgvector，所以 [`backend/docker/db/Dockerfile`](../../backend/docker/db/Dockerfile) 自建了镜像。

### 4.2 Document 表：把"资料"抽象成检索单元

代码：[`backend/app/models/document.py`](../../backend/app/models/document.py)

真实世界里资料有照片、地点、行程三种形态，但**检索只关心"一段可引用的文本 + 一个向量"**，所以统一成 `Document`：

```
Document
 ├─ source_type / source_id / chunk_index   ← 稳定三元组（唯一键）
 ├─ content                                 ← 原文
 ├─ metadata (JSONB)                        ← 时间窗/地点/主题，阶段 2 做过滤用
 ├─ embedding  vector(1024)                 ← 向量
 └─ embedding_model                         ← 记录是哪个模型生成的
```

**为什么唯一键用三元组而不是自增 id？**
自增 id 在重建库之后会变，评测集里写死的"期望命中的那条资料"就失效了。三元组（类型 + 稳定标识 + 第几块）在任何机器上都不变（设计文档 §4.5）。

> 阶段 1 补充：这个键后来加上了 `user_id`（迁移 0003）。原因是两个用户的
> `day/2025-10-03#0` 会撞在同一行上互相覆盖——详见
> [stage1 第 9 节易错点](../stages/stage1-offline-jobs.md)。

顺带一个好处：重复导入同一份资料会**覆盖而不是堆积**，重索引天然幂等、不会重复烧 embedding 的钱。

### 4.3 迁移做了什么

[`backend/alembic/versions/0001_stage0_core.py`](../../backend/alembic/versions/0001_stage0_core.py)

1. 建三个扩展：`postgis` / `vector` / `pg_trgm`
2. 建 8 张表：`user`、`document`、`agent_run`、`agent_step`、`tool_call_log`、`eval_case`、`eval_result`
3. 向量维度从环境变量 `EMBEDDING_DIM` 读取（改维度 = 换模型，必须同时 `make reindex`）

阶段范围内**只建本阶段用到的表**，Photo/Trip/Place 等留给后续阶段——避免阶段之间互相污染。

### 4.4 阶段 0 为什么不建 HNSW 索引

HNSW 是 pgvector 的近似索引，能让百万级检索变快，但它有个坑：

> HNSW 是**先遍历图、后套 WHERE 过滤**。如果先按向量取 40 条，再用 `user_id = ?` 过滤，可能一条都不剩。

所以阶段 0 用**精确扫描**（全表算距离再排序，几十万条内完全够用），先把"正确性"钉死；阶段 3 数据量上来之后再引入索引，并配套"结果不足回退精确扫描"的兜底。

### 4.5 启动自检：把脏向量拦在门外

代码：[`backend/app/db/checks.py`](../../backend/app/db/checks.py)

换 embedding 模型后忘记重建索引，是这类项目最常见的"隐形事故"：检索照样返回结果，但质量已经崩了，而且很难察觉。所以启动时强制检查三件事：

1. 三个扩展是否都在；
2. `document.embedding` 的实际维度是否等于 `EMBEDDING_DIM`；
3. 库内占多数的 `embedding_model` 是否等于当前配置（不一致 → 提示 `make reindex`）。

### 4.6 检索服务：一行 SQL 的关键点

代码：[`backend/app/services/document_service.py`](../../backend/app/services/document_service.py)

```python
distance = Document.embedding.cosine_distance(list(query_vector))
stmt = (
    select(Document, distance.label("distance"))
    .where(Document.user_id == user_id, Document.embedding.is_not(None))
    .order_by(distance)
    .limit(top_k)
)
# 之后：score = 1 - distance
```

要点：
- `<=>` 是**距离**不是相似度，必须 `1 - distance` 换算回来；
- `user_id` 过滤**必须显式写**，这是为多用户留的口子，也防止 A 用户看到 B 的资料；
- 写入用 `ON CONFLICT DO UPDATE`，冲突目标是三元组，实现幂等重索引。

---

## 5. W0-2 RAG 管道

代码：[`backend/app/agent/pipeline.py`](../../backend/app/agent/pipeline.py)、[`nodes/pipeline/`](../../backend/app/agent/nodes/pipeline/)

### 5.1 ① rewrite：先让人话变成"可检索的话"

用户说话是给人听的：「照着上次那趟再来一个」——代词、省略一大堆，直接丢给向量检索几乎必然召回失败。
这一步让模型补全指代、展开省略（"去过哪些古镇" → "去过的古镇、古村落、水乡景区"），**只输出改写后的一句话**。

失败兜底：模型输出过长/异常时，退回原始问题（`clean_llm_text` 里判 >300 字符即视为异常）。

### 5.2 ② query_parse：抽关键词（顺手为阶段 2 留字段）

让模型输出 JSON：`keywords / places / themes / time_range`。

阶段 0 只用到 `keywords`，把它拼到检索串后面——短问题的向量太稀疏，补几个核心词能明显提升召回。
阶段 2（W2-1）会真正用 `time_range / places` 做 SQL 结构化过滤。

**重要原则：解析失败一律退化为"不过滤"**（`ParsedQuery.degraded=True`）。宁可多召回让上层处理，也不能因为猜错时间而漏召回。

### 5.3 ③ retrieve：检索 + 阈值过滤

```python
hits = await self.searcher.similar_search(state.user_id, vector, top_k=...)
return [hit for hit in hits if hit.score >= self.config.min_score]
```

`RETRIEVAL_MIN_SCORE`（默认 0.30）是"宁可拒答也不硬凑引用"的执行者：证据太不相干就不进上下文。

### 5.4 ④ build_context：编号从这里诞生

代码：[`backend/app/agent/rag/context_builder.py`](../../backend/app/agent/rag/context_builder.py)

```
[1] 来源=trip/t1#0（开始=2025-10-02 地点=乌镇、西塘） 相似度=0.612
2025 年秋天，10 月 2 日到 10 月 5 日，我去了一趟江南的水乡古镇……
```

两个设计点：
- **编号必须从 1 连续编号，且顺序与模型看到的顺序一致**。编号错位是最难查的 bug（模型引用 [3]，但 [3] 其实在第 2 条）。
- **字符预算**（默认 4000）超了就丢弃尾部证据，并把 `dropped/truncated` 写进 trace，方便事后发现"证据被吃掉导致的拒答"。

上下文的**顺序**也是有讲究的（设计文档 §7.4 固定顺序）：系统指令 → 用户偏好 → 行程上下文 → **检索证据** → 群体先验 → 对话历史/问题。阶段 0 只有证据和问题两块，其余位置留给后续阶段插入，顺序不变。

### 5.5 ⑤ generate：要么带证据生成，要么直接拒答

代码：[`backend/app/agent/nodes/pipeline/generate.py`](../../backend/app/agent/nodes/pipeline/generate.py)

- **没有证据 → 直接拒答，不调 LLM**。省一次钱，也避免模型"自由发挥"。
- **有证据 → 按 [`system.md`](../../backend/app/agent/prompts/system.md) 的规则生成**：每句事实带 `[n]`，编号只能取自本轮证据，不允许用常识冒充个人经历。

生成后还会做一件事：把答案里出现的 `[n]` **反查回文档引用**（`_cited_refs`），前端将来就能实现"点 [1] → 地图飞到那张照片"。

### 5.6 ⑥ self_check：引用率的硬校验

代码：[`backend/app/agent/nodes/pipeline/self_check.py`](../../backend/app/agent/nodes/pipeline/self_check.py)、[`backend/app/utils/citation.py`](../../backend/app/utils/citation.py)

规则（确定性代码，不靠模型自觉）：

| 情况 | 判定 |
| --- | --- |
| 事实句里没有 `[n]` | ❌ 违规 |
| 引用编号超出本轮证据条数 | ❌ 违规（臆造引用） |
| 拒答答案里出现 `[n]` | ❌ 违规（自相矛盾） |
| 礼貌/引导句（"抱歉…""你可以…"） | ✅ 不要求引用（白名单，宁可少放行） |

管道的处理：**违规 → 重试生成一次 → 仍违规 → 拒答**。这样引用率 100% 才是真的被保证，而不是"差不多"。

### 5.7 分块：为什么要 overlap

代码：[`backend/app/agent/rag/chunker.py`](../../backend/app/agent/rag/chunker.py)

超过 800 token 的文档按句子切开，相邻块之间**保留 10% 重叠**。
原因：中文没有空格，硬切会把地名、日期从中间劈开，"我问的那句话"和"库里那句话"就对不上了。按句子切 + 重叠，能让跨块的语义不断裂。

### 5.8 Prompt 版本化：为什么必须改版本号

Prompt 放在 [`app/agent/prompts/*.md`](../../backend/app/agent/prompts/)，加载器里维护 `PROMPT_VERSION`（当前 `pv2026.10-stage0.1`）。

改 prompt 而不改版本号，评测结果里记录的版本就与实际不符，**指标变好变坏无法归因**。所以规矩是：动任何一个 `.md`，都要同步递增版本号（PROJECT.md §五）。

### 5.9 离线占位模式：没有密钥也能跑通

`LLM_PROVIDER` / `EMBEDDING_PROVIDER` 设为 `auto` 时：配了 Key 走真实服务，没配就降级为

- `mock` LLM：本地规则生成回答（把前 3 条证据首句拼成带引用的答案）；
- `hashing` embedding：本地哈希向量（字 + 相邻二字 + 英文词 → blake2b 映射 → L2 归一化）。

**它的边界要记牢**：只用于验证工程链路是否通畅，**检索质量不能拿它下结论**（它只认字面重合，理解不了"去年秋天"这类表达）。评测请用真实 embedding。

---

## 6. W0-3 评测与可观测

### 6.1 三个指标，手算一遍就懂

代码：[`backend/app/agent/eval/metrics.py`](../../backend/app/agent/eval/metrics.py)

以评测题「我去年秋天去过哪些古镇？」为例，期望命中 `trip/t1#0`：

假设检索返回前 5 条：`p10, t5, t1, p11, t3`

- **Recall@5** = 前 5 条命中的期望条数 / 期望总条数 = 1 / 1 = **1.0**
- **MRR** = 1 / 第一条命中的排名 = 1 / 3 = **0.33**（越靠前越好）
- **引用率** = 答案里"带引用的事实句 / 全部事实句"，目标 **1.0**
- **拒答正确** = 该拒答的题目是否正确拒答（1.0 / 0.0）

有个容易忽略的设计：**默认评测模式把 min_score 设成 0**。
因为它测的是"排序质量本身"；相似度阈值属于线上策略，在 `--full`（走真实阈值）里体现。两者分开，指标才不会互相干扰。

### 6.2 可观测：三步一 trace

代码：[`backend/app/agent/observability.py`](../../backend/app/agent/observability.py)

节点里**只允许**这样写（PROJECT.md §二、[5]）：

```python
async with trace.step("retrieve", {"search_text": text}) as step:
    hits = await self.retriever.retrieve(state)
    step.output = {"count": len(hits), ...}
```

落盘两条路：数据库三张表（`AgentRun` 整体耗时/成本、`AgentStep` 每个环节、`ToolCallLog` 每次工具调用与缓存命中）+ JSONL 文件（`backend/data/traces/YYYY-MM-DD.jsonl`，便于 grep 和离线分析）。

**任何写入失败只告警不影响主流程**——trace 是旁路设施，不能因为记日志把问答搞挂。

### 6.3 归因对比

`EvalResult` 冗余记录了 `prompt_version` + `embedding_model`，于是 `make eval-compare` 能直接输出对比表：

```
prompt_version          embedding_model          n   Recall      MRR      引用率   延迟ms
pv2026.10-stage0.1      bge-m3@v1               26    0.83    0.71      1.000   1450
```

改了 prompt 或换了模型，跑两次再对比，就知道到底变好还是变坏。

### 6.4 评测集为什么用三元组

[`backend/data/eval_cases.jsonl`](../../backend/data/eval_cases.jsonl)（26 条：21 条 rag + 4 条必须拒答 + 1 条群体先验）里写的是：

```json
{"kind":"rag","question":"我去年秋天去过哪些古镇？",
 "expected_evidence":[{"source_type":"trip","source_id":"t1","chunk_index":0}]}
```

同 4.2 的道理：重建库后依然有效。`kind` 决定统计口径——`recommend` 类的群体先验**不计入引用率**（设计文档 §9.11）。

---

## 7. 文件清单

| 文件 | 作用 | 对应知识点 |
| --- | --- | --- |
| `backend/requirements.txt` 等 | 依赖清单 | 环境隔离 |
| `app/core/config.py` | 唯一的环境变量入口 | 配置集中化 |
| `app/core/llm.py` / `embedding.py` | LLM / 向量调用（重试、超时、成本） | 外部 API 封装、auto 降级 |
| `app/core/exceptions.py` | 异常体系（`DegradedException` 用于旁路降级） | 失败降级 |
| `app/db/session.py` / `checks.py` | 异步会话 / 启动自检 | 连接池、脏数据防线 |
| `app/models/*.py` | 8 张表的 ORM | 数据建模 |
| `alembic/versions/0001_*.py` | 初始迁移（三扩展 + 8 表） | 迁移幂等 |
| `app/schemas/document.py` | 层间契约（`DocumentRef` / `SearchHit`） | 契约与实现分离 |
| `app/services/document_service.py` | 文档写入 + 向量检索 | pgvector 用法 |
| `app/agent/rag/chunker.py` | 分块 | 长文本处理 |
| `app/agent/rag/indexer.py` | 切块 → 向量化 → 落库 | 离线索引 |
| `app/agent/rag/retriever.py` | 检索编排（阈值、top-k） | 召回策略 |
| `app/agent/rag/context_builder.py` | 编号与上下文预算 | 提示词工程 |
| `app/agent/nodes/pipeline/*.py` | 六个环节 | 管道式 Agent |
| `app/agent/prompts/*.md` | 版本化提示词 | 归因前提 |
| `app/agent/observability.py` | Run/Step/ToolCall + JSONL | 可观测性 |
| `app/agent/eval/*` | 评测集、指标、执行器 | 量化评估 |
| `app/cli.py` | 问答入口 + 环境自检 | 最小可用入口 |
| `data/demo_corpus.jsonl` 等 | 27 条演示语料 | 无真实数据时的替身 |

---

## 8. 跑起来验证

```bash
make up            # 启动 PostGIS + pgvector 容器（首次会自建镜像，稍慢）
make migrate       # 建扩展 + 建表
make check         # 期望：✓ 数据库已连通 / ✓ 扩展与向量维度检查通过
make seed-demo     # 导入 27 条演示语料（幂等，可重复执行）
make cli           # 交互式问答
```

**判定是否真的跑通**：

| 输入 | 期望 |
| --- | --- |
| 「我去年秋天去过哪些古镇？」 | 带 `[1][2]` 引用的回答，下方列出证据来源与相似度 |
| 「我在南极看过企鹅吗？」 | 明确拒答，不编造 |
| `make test` | 通过（需真实库的 2 条用例在无 Docker 时自动跳过） |
| `make eval` | 输出每条用例的 Recall@5 / MRR 与按 kind 汇总 |
| `python -m app.agent.eval.runner --full` | 引用率 1.00、拒答正确 1.00 |

常见问题排查：

| 现象 | 原因与处理 |
| --- | --- |
| `make check` 说数据库不可达 | Docker 没启动，或 5432 端口被占用 |
| 提示"缺少扩展" | 没执行 `make migrate`（扩展在首次迁移里创建） |
| 提示"向量维度不一致" | 改了 `EMBEDDING_DIM` 却没重建，需新迁移 + `make reindex` |
| 提示"库内主流向量来自…" | 换过 embedding 模型，执行 `make reindex` |
| 一直拒答 | 先确认 `make seed-demo` 跑过；再看 `RETRIEVAL_MIN_SCORE` 是否设太高 |
| 回答没有引用 | 看 trace 里 `self_check` 的 `violations` 字段 |

---

## 9. 易错点清单

1. **HNSW 带过滤可能返回 0 条**——先精确扫描，别急着上索引（4.4）。
2. **换 embedding 模型必须 `make reindex`**，否则库里新旧向量混在一起，检索看起来正常、质量已崩。
3. **改 prompt 必须同步改 `PROMPT_VERSION`**，否则无法归因。
4. **阈值不是越高越好**：太高会频繁拒答，太低会"硬凑引用"。用 `make eval` 调，而不是拍脑袋。
5. **编号顺序必须与上下文一致**，否则引用全错位且难以察觉。
6. **哈希向量 / mock 回答不能用于效果结论**，只能证明"链路通了"。
7. **分块别按字符硬切**，中文会把地名日期劈开。
8. **trace 写入失败不能让主流程失败**——它只是旁路设施。
9. **`user_id` 过滤必须显式带上**，哪怕当前只有一个人用。
10. **阶段范围别蔓延**：阶段 0 不要提前建 Photo/Trip 表，也不要写 HTTP 业务接口。
11. **Windows 无 `make`**：手动执行等价命令，或 `winget install GnuWin32.Make`（见 10.1）。
12. **`alembic.ini` 在中文 Windows 上会被 GBK 解码**：文件里别放中文注释（仓库已改为纯 ASCII）。
13. **YAML 必须 UTF-8 无 BOM**：带 BOM 时 Docker 报 `invalid trailing UTF-8 octet`。
14. **占位模式要把 `RETRIEVAL_MIN_SCORE` 降到 0.05**：否则一路拒答，会误以为管道坏了。

---

## 10. Windows 实战记录（真实跑通过程）

> 这一节记录 2026-10 在 Windows 11 家庭版 + PowerShell + Docker Desktop(WSL2) 上的真实执行过程，
> 以及因此对本阶段代码/文档做的**永久性修改**。你已经跑过的同学可以对照复盘。

### 10.1 环境差异与应对

| 文档假设 | Windows 实际情况 | 应对 |
| --- | --- | --- |
| 用 `make` 跑命令 | PowerShell 没有 `make` | 手动执行等价命令，或 `winget install GnuWin32.Make`（README 有对照表） |
| Docker Desktop 需要 Ubuntu 发行版 | 只需要 WSL 2 内核 | `wsl --install --no-distribution`（约 100MB，不用下 1GB 的 Ubuntu） |
| `docker pull` 正常 | 加速器偶发 `denied` / 403 | 多备几个 registry-mirror 轮换 |
| Alembic 读 ini 用 UTF-8 | 中文 Windows locale 是 GBK → 解码失败 | 把 `alembic.ini` 改成**纯 ASCII**（比转 GBK 更彻底，跨机器都不会再踩） |

### 10.2 数据库镜像方案被替换了（原理要懂）

原方案 `FROM postgis/postgis:16-3.4` + apt 装 pgvector，**实测不可行**：

- 该镜像基于 Debian 11(bullseye)，其 PostgreSQL 官方源 `apt.postgresql.org` 已 404，归档源同样 404；
- 源里没有 `postgresql-server-dev-16`，也没有 `postgresql-16-pgvector`；
- 想从源码编译 pgvector，镜像内缺 `/usr/include/postgresql/16/server/` 头文件，编译失败。

**改后的方案**（已写入仓库）：

```dockerfile
FROM pgvector/pgvector:pg16          # 基于 Debian 12(bookworm)，自带 pgvector
RUN apt-get update && apt-get install -y postgresql-16-postgis-3
```

这就是"选型的时效性"：镜像基座的发行版一旦 EOL，依赖它的构建方案就会失效。
排查这类问题的方法也很典型——**进容器里 `cat /etc/os-release` + `apt-cache search`，先确认发行版和源，再谈装包**。

### 10.3 实际执行结果与指标

```
docker compose build --no-cache && docker compose up -d
→ 容器 travelmemory-db-1 状态 Up (healthy)，三个扩展创建成功

（backend 目录）alembic upgrade head
→ INFO  [alembic.runtime.migration] Running upgrade  -> 0001, 阶段 0 初始 schema

python -m app.scripts.seed_demo
→ 导入完成：27 条记录 → 27 个 chunk

python -m app.cli             → 出现交互提示符，事实回答带 [1][2]，下方列出证据
python -m app.agent.eval.runner
→ rag: Recall@5 = 0.7143，MRR = 0.4833（21 条）
→ refusal: 4 / 4 全部正确拒答
→ recommend: 1 条（按设计不计入引用率）
```

失败的 6 条全部是**语义理解类**问题（"杭州最喜欢哪类地点""去年夏天去了哪儿""上海看了什么夜景"）。
这不是 bug，而是离线占位模式的已知边界：哈希向量只认字面重合，理解不了"去年夏天 = 2025 年 7~8 月"这类映射。
换成真实 embedding（bge-m3 / text-embedding-v3）后这类题目会明显改善——**这也正好说明了为什么要先建评测集**：
没有数字，你就分不清"管道坏了"和"模型能力不够"。

### 10.4 阶段 0 出口条件达成情况

| 出口条件 | 状态 | 证据 |
| --- | --- | --- |
| 数据库连通、三扩展创建 | ✅ | `CREATE EXTENSION` × 3 成功，容器 healthy |
| 迁移成功、8 张表建好 | ✅ | `Running upgrade -> 0001` |
| 语料入索引 | ✅ | 27 条记录 → 27 个 chunk |
| CLI 能问答 | ✅ | 出现交互提示符并能返回答案 |
| 事实回答带引用 | ✅ | 答案含 `[1][2]`，并列出证据与相似度 |
| 无证据明确拒答 | ✅ | 拒答类 4/4 正确 |
| 评测能出 Recall@5 / MRR | ✅ | 0.7143 / 0.4833 |
| trace 可回溯 | ✅ | 每次问答在 `backend/data/traces/*.jsonl` 留下完整 step |

**结论：阶段 0 出口条件全部达成**，可以进入阶段 1。

### 10.5 因此永久修改了什么

1. `backend/docker/db/Dockerfile` → 换 `pgvector/pgvector:pg16` + `postgresql-16-postgis-3`，并写清原因。
2. `backend/alembic.ini` → 改为纯 ASCII，从根上消除 GBK 解码问题。
3. `docker-compose.yml` → 保持 `build: ./backend/docker/db`，加 BOM 警告注释。
4. `app/db/checks.py` → 向量模型不一致时，额外提示"离线模式该改哪三个变量"。
5. `app/cli.py` → 占位模式下若阈值过高会主动提醒；`Ctrl+C` 退出不再抛 `CancelledError` 噪音。
6. `.env.example` / `README.md` / 本文档 → 补充离线模式配置、Windows 无 make 的替代命令表。

---

## 11. 动手练习（验证你是否真的理解）

1. 把 `RETRIEVAL_MIN_SCORE` 从 0.30 改成 0.60，重跑 `make eval`，观察拒答变多、Recall 如何变化。
2. 在 `data/demo_corpus.jsonl` 里加一条自己的"假行程"，重新 `make seed-demo`，问一个只有它能回答的问题，看引用是否指向它。
3. 把 `system.md` 里"必须用 [n]"那句删掉（记得同时改 `PROMPT_VERSION`），再跑 `--full`，看引用率掉到多少。
4. 打开 `backend/data/traces/当天.jsonl`，数一次问答产生了哪些 `step`，各自耗时多少毫秒——找出最慢的环节。

---

## 12. 下一阶段

阶段 1：离线批处理 + 照片元数据（W1-1 jobs 框架 / W1-2 caption + ai_tags / W1-3 索引任务）。
核心变化：索引不再是手跑脚本，而是**可断点续跑、幂等、带任务状态**的批处理；并开始真正接触照片的 EXIF 与缩略图（仍然遵守"原图永不上云"）。
