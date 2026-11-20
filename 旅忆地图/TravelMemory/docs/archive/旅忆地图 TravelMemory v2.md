# 旅忆地图 TravelMemory（v2 修订版）

> 一句话：以个人照片地图记忆为数据源的旅行记忆 Agent。地图相册是数据底座和可视化，Agent 是核心。

**第一目标**：学习 Agent / RAG / Memory 开发。  
**第二目标**：做出可演示、可答辩、可写简历的完整应用。

**核心设计**：照片不搬家，只借缩略图和元数据。Agent 基于真实照片/行程数据做问答、复盘、规划。

---

## 零、规模假设与验收标准

### 规模假设（所有选型的量级前提）

| 项            | 假设值                                    |
| ------------ | -------------------------------------- |
| 照片数          | 1 万～2 万张（个人 3～5 年旅行）                   |
| Document 数   | 约 3 万（按 Trip / TripItem / 天 聚合后 chunk） |
| MemoryItem 数 | 单用户 < 2000                             |
| 并发           | 单用户，1～2 设备                             |

**结论**：pgvector 单机完全够用，不需要专用向量库；超过 50 万 Document 再重新评估。  
**推论**：阶段 0～2 数据量小，向量检索用**精确扫描**即可，不必上 ANN 索引（见 §5.4）。

### 验收标准（带数字，阶段 6 结束时要能达标）

| 维度 | 指标                        | 目标               |
| -- | ------------------------- | ---------------- |
| 导入 | 1000 张照片 导入 + AI 打标 + 建索引 | < 10 分钟          |
| 地图 | 1 万点聚合渲染                  | ≥ 60fps，拖动无明显卡顿  |
| 检索 | Recall@5                  | ≥ 0.8            |
| 检索 | MRR                       | ≥ 0.6            |
| 生成 | 事实性回答引用率                  | **100%**（缺失即判失败） |
| 生成 | 无证据场景拒答率                  | 100%（不得编造行程）     |
| 工具 | 工具选择准确率                   | ≥ 0.9            |
| 系统 | P95 首字延迟                  | < 3s             |
| 系统 | 单次问答 LLM 成本               | < ¥0.05          |

> 评测集 `data/eval_cases.jsonl` 第一天就建，20～30 条，每阶段扩充。

---

## 一、项目定位

一个以个人照片地图记忆为数据源的 AI Agent 应用。

- **数据层**：照片导入 → EXIF/位置/时间 → 地图气泡 → 行程分组 → 路线可视化
- **智能层**：RAG 检索 + 分层 Memory + Tool Use + Planning + Reflection + Evaluation
- **展示层**：地图相册 + Agent 对话页

### 非目标（明确不做）

- 不做原图云备份
- 不做社交/信息流/关注
- 不做多用户协作（**单用户多设备**模型，`User` / `Device` 表只为设备级 token 存在）
- 不做内容审核
- 不做完整社区

### 学习目标（写进 PROJECT.md）

- RAG：索引、检索、重排、上下文构建、引用
- Memory：短期会话、情景记忆、语义偏好、程序性记忆
- Tool Use：工具 schema、调用、校验、错误处理、缓存
- Planning：任务拆解、多步执行、状态机
- Reflection：复盘、摘要、写回记忆
- Evaluation：检索指标、答案忠实度、工具选择准确率
- Observability：Trace、Token、延迟、成本、工具日志

---

## 二、核心功能与优先级

### P0（严格串行，做完一个再做下一个）

1. 纯文本 RAG 管道跑通（rewrite → retrieve → build_context → generate → self_check）
2. **离线批处理管道**：caption / ai_tags 生成（幂等、可重试、有成本上限）
3. 照片元数据接入，结构化过滤 + 向量检索（时间、地点、行程）
4. Query 结构化解析（时间/地点/主题抽取 → 元数据过滤）
5. 工具调用：search_poi、calculate_route
6. Memory：会话摘要 + 偏好抽取 + 写回
7. 评估：20～30 个 QA 对 + 检索命中率
8. 可观测：JSONL + AgentRun/AgentStep/ToolCallLog

### P1（拉开差距）

- 地图相册最小闭环（导入、EXIF、缩略图、气泡、行程分组、路线）
- 导入时快速筛选
- AI 主题打标 + 主题相册视图
- Agent 多步 Planning
- 偏好画像 + 时间衰减 + 冲突处理
- 行程导出 / 分享卡片
- 流式输出（SSE）

### P2（有精力再说）

- AI 照片预打分
- 偏好迁移报告
- 自己 vs 自己的路线对比
- 程序性记忆（规划模板、工具使用经验）—— **v1 不实现**，仅在 Memory 分层中保留位置
- Capacitor 套壳

---

## 三、架构设计

### 3.1 四层架构

```
① 客户端层    React + TypeScript + Capacitor（后置）
              地图渲染 / 相册 / Agent UI

② 接口层      FastAPI Router + Pydantic
              路由 / 参数校验 / 统一响应 / 版本管理

③ 业务服务层  PhotoService / TripService / MapService
              DocumentService / MemoryService
              AgentService（仅接口适配）
              LLMService（统一 LLM 入口）

④ 数据智能层  PostgreSQL + PostGIS + pgvector
              MinIO / LLM / 高德 API / JSONL trace
```

### 3.2 依赖方向（权威定义，全篇以此为准）

```
api/v1/agent.py -> agent/service.py -> agent/pipeline.py（阶段 0～3，AGENT_RUNTIME=pipeline）
api/v1/agent.py -> agent/service.py -> agent/graph.py   （阶段 4+，AGENT_RUNTIME=graph）

agent/pipeline.py -> agent/nodes/pipeline/* -> tools/registry -> tools/* -> services/*
agent/graph.py    -> agent/nodes/graph/*    -> tools/registry -> tools/* -> services/*

agent/rag/indexer.py   -> services/document_service.py
agent/rag/retriever.py -> services/document_service.py   （底层向量/结构化查询）
agent/memory/*         -> services/memory_service.py
jobs/*                 -> services/*  + core/llm.py

普通 services 不调用 tools
tools 只被 agent/nodes 调用
AgentService 只做接口适配，不直接调工具
```

**也就是说**：

- PhotoService 不知道 Agent 存在
- MapService 不知道 Agent 存在
- DocumentService 管文档 CRUD、向量写入、底层向量/结构化查询
- MemoryService 管记忆读写和衰减
- AgentService 只负责把 HTTP 请求转给 pipeline/graph
- pipeline / graph 是 Agent 运行时入口，由 `AGENT_RUNTIME` 配置切换
- nodes 是各步骤节点
- tools 是适配器，调 services
- `nodes/graph/write_memory.py` 调 MemoryService 属于**业务写入**，不违反"节点不自己插表"（该约束仅针对 observability 表）

### 3.3 前后端边界

| 功能                   | 前端    | 后端    | 理由                     |
| -------------------- | ----- | ----- | ---------------------- |
| 照片选择                 | ✅     |       | 系统能力                   |
| EXIF 解析              | ✅     |       | 原图不上传，隐私（⚠️ iOS 见 §12） |
| 缩略图生成                | ✅     |       | 省流量                    |
| 地图渲染                 | ✅     |       | 高德 JS                  |
| 气泡聚合                 | ✅     |       | Supercluster           |
| 行程分组（规则）             | ✅ 可先做 | ✅ 持久化 |                        |
| 逆地理编码                |       | ✅     | 藏 API Key              |
| Agent 编排             | ✅ UI  | ✅ 逻辑  | LLM 必须在后端              |
| 记忆检索                 |       | ✅     | pgvector + 结构化过滤       |
| caption / ai_tags 生成 |       | ✅     | 需要 VLM，且要幂等重试          |

**原则**：渲染和隐私放前端，计算和智能放后端。

### 3.4 两条核心数据流

**导入照片**

```
用户选照片 → 前端 exifr 读 GPS → Canvas 生成缩略图
→ POST /api/v1/photos → 存 MinIO + PostgreSQL
→ 入队 IngestJob（caption / ai_tags / embedding）
→ 离线批处理消费 → 写回 Photo + Document
→ 地图渲染气泡
（原图从未离开手机）
```

**Agent 问答/规划**

```
用户输入 → POST /api/v1/agent/chat（支持 SSE 流式）
→ agent/service.py 按 AGENT_RUNTIME 转 pipeline / graph
→ rewrite_query → query_parse（时间/地点/主题） → retrieve
→ build_context → generate → self_check
→ 工具调用（search_poi / calculate_route）
→ LLM 生成带引用回答
→ write_memory 写回
→ 统一由 observability.py 记录 trace
→ 返回前端，高亮地图证据
```

### 3.5 Agent 工具链

```
tools/
  query_user_memory → MemoryService / DocumentService
  search_poi        → MapService（高德）
  calculate_route   → MapService（高德）
  get_weather       → 外部 API
```

**原则**：

- LLM 只做决策，地点必须经地图 API 校验，降低幻觉
- 每个工具入参用 Pydantic schema 校验
- 最终回答必须引用照片/行程 ID，方便前端高亮地图

---

## 四、坐标与位置

### 4.1 坐标系

- EXIF GPS 通常是 **WGS84**
- 高德地图和大部分高德 API 使用 **GCJ-02**
- 必须做 WGS84 与 GCJ-02 转换，不用自己实现偏移算法，可调高德转换或成熟库

**统一规则**：

- PostGIS 里统一存 WGS84，类型 `geography(Point, 4326)`（距离计算直接得米）
- API 默认返回 WGS84
- 前端给高德 JS 渲染前转 GCJ-02
- 后端调高德 POI/路径规划前转 GCJ-02
- 逆地理编码结果回来后再统一转回 WGS84 存储

数据模型和 API 里**显式加 `coord_system` 字段**，否则后期偏移问题很难查。

> 注意：高德坐标转换 API 单次最多 40 个点、有每日配额，批量导入要做分批 + 缓存。  
> 坐标转换本身有几十米误差，"空间距离 < M 公里"的聚类阈值不要把 M 设得太小（建议 ≥ 0.5km）。

### 4.2 无位置照片处理（重要）

现实中无 GPS 照片占比可能高达 30%～60%（截图、室内、老照片、社交软件保存图、部分机型）。必须在数据层就支持：

```
Photo.location_source      exif / inferred / user / none
Photo.location_confidence  0~1
```

**处理策略（按序）**：

1. **时间邻近推断**：前后 30 分钟内有 GPS 照片 → 借用其 Place，`location_source='inferred'`，confidence 低
2. **用户手动标注**：地图页支持拖点或搜 POI 指派，`location_source='user'`，confidence=1
3. **归入"未定位"分组**：允许按 Trip 聚合展示，**不强行上图**（避免地图上出现假的聚集点）
4. `location_source='none'` 的照片不参与空间过滤，但**仍参与向量检索**（caption/ai_tags 有语义）

**日期切分用 `taken_at_local`，时间差计算用 `taken_at`**（见 §4.3）。

### 4.3 时间与时区

EXIF `DateTimeOriginal` 通常不带时区，但"同一天""去年秋天"完全依赖本地时间。必须双写：

```
taken_at        timestamptz   按拍摄地本地时间解释后的 UTC 时刻（排序、时间差用）
taken_at_local  timestamp     拍摄地本地墙上时间（按天分组、展示用）
tz_name         text          IANA 时区，如 Asia/Shanghai
tz_source       text          exif_offset / device / user_override
```

EXIF 无时区信息时，用导入时设备时区填充 `tz_name`，并标 `tz_source='device'`。

### 4.4 自动聚类：先规则后算法

规则（阶段 6 先跑通）：

- 同一天（以 `taken_at_local` 为准）
- 相邻照片时间差 < N 小时（N 默认 4，用 `taken_at` 算）
- 空间距离 < M 公里（M 默认 1，用 `geography` 算米）
- 停留点合并（同一 Place 且停留 > 30 分钟合并为一个 TripItem）

跑通规则后，再考虑 DBSCAN + PostGIS 时空聚类。

---

## 五、技术栈

| 模块        | 选型                                               | 备注                          |
| --------- | ------------------------------------------------ | --------------------------- |
| 前端框架      | React + TypeScript + Vite                        | 后置                          |
| 地图        | 高德 JS API                                        | 后置，JS Key 必须配安全密钥和域名白名单     |
| UI        | Tailwind CSS + shadcn/ui                         | 后置                          |
| 后端        | FastAPI + SQLAlchemy 2.0 + Pydantic v2 + Alembic |                             |
| 数据库       | PostgreSQL 16 + PostGIS 3.4 + pgvector           | **需自建镜像**，见 §5.1            |
| ORM 空间扩展  | **GeoAlchemy2**                                  | PostGIS 必需，别漏               |
| 对象存储      | MinIO                                            | 后置，只存缩略图                    |
| Agent 编排  | 阶段 0～3 纯 Python 管道，阶段 4 迁 LangGraph              | `AGENT_RUNTIME` 切换          |
| RAG       | 自己实现核心流程，不用黑盒框架                                  |                             |
| Embedding | **第一天必须选定**                                      | 见 §5.2                      |
| Reranker  | bge-reranker-v2-m3（本地）或 Qwen rerank（API）         | 阶段 3 再加；本地需 GPU/显存，无卡就用 API |
| 向量库       | pgvector                                         | **阶段 0～2 不建 ANN 索引**，见 §5.4 |
| LLM 抽象    | `core/llm.py` 统一封装                               | 重试/超时/成本统计                  |
| 缓存        | 阶段 0 内存 dict + TTL；阶段 5 Redis                    | POI / 路线 / 逆地编结果            |
| 可观测       | 阶段 0：JSONL + 表记录；阶段 5：Langfuse                   |                             |
| LLM       | DeepSeek / Qwen 做对话                              |                             |
| 移动端       | Capacitor                                        | 阶段 6                        |
| 部署        | Docker Compose + Nginx                           | 分阶段                         |
| CI/CD     | GitHub Actions                                   |                             |
| 测试        | pytest + Playwright + 坐标转换 + 聚类 + RAG 评测         |                             |

### 5.1 数据库镜像必须自建（PostGIS + pgvector 同时要）

`pgvector/pgvector:pg16` **不含 PostGIS**，`postgis/postgis:16-3.4` **不含 pgvector**。用自建镜像：

```dockerfile
# backend/docker/db/Dockerfile
FROM postgis/postgis:16-3.4
RUN apt-get update \
 && apt-get install -y postgresql-16-pgvector \
 && rm -rf /var/lib/apt/lists/*
```

初始化 SQL（Alembic 首次迁移里执行）：

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- 阶段 2
-- CREATE EXTENSION IF NOT EXISTS zhparser;  -- 阶段 4
```

### 5.2 Embedding 模型（第一天必须选定）

三选一，先用一个，别纠结：

| 方案     | 模型                     | 维度                      | 场景       |
| ------ | ---------------------- | ----------------------- | -------- |
| 国内 API | Qwen text-embedding-v3 | **默认 1024**（可选 768/512） | 有 API，省事 |
| 本地     | bge-m3                 | 1024                    | 想本地跑     |
| OpenAI | text-embedding-3-small | 1536                    | 有海外 API  |

配置写在 `core/config.py`：

```python
EMBEDDING_MODEL: str              # 如 "bge-m3"
EMBEDDING_MODEL_VERSION: str      # 如 "bge-m3@v1"，写进 Document/MemoryItem
EMBEDDING_DIM: int                # 1024
```

**硬约束（务必遵守）**：

- Alembic 迁移用配置生成 vector 维度
- `Document.embedding` 与 `MemoryItem.embedding` 维度必须一致
- **换模型必须走 `make reindex` 全量重算，不能只改配置**
- 检索时带 `WHERE embedding_model = :current` 兜底；启动时校验库内主流值与配置一致，不一致直接报错
- 维度上限：HNSW 索引支持到 2000 维，`text-embedding-3-large`(3072) 不可用于 HNSW

### 5.3 中文全文检索（分阶段）

| 阶段   | 方案                  | 说明                      |
| ---- | ------------------- | ----------------------- |
| 阶段 0 | 纯向量 + 结构化过滤         | 不做全文                    |
| 阶段 2 | pg_trgm + ILIKE     | 临时方案，按字符 trigram 切，效果一般 |
| 阶段 4 | zhparser 或 pg_jieba | Docker 镜像编译扩展           |

阶段 0 不做 RRF，先向量 top-k + 元数据过滤；阶段 4 加 RRF 融合。

### 5.4 向量索引时机（避免召回不足）

pgvector 的 HNSW 是**先图遍历、后过滤**，带 `WHERE` 时返回条数可能远少于 k，甚至 0 条。而本应用的核心检索恰恰是"user_id + 时间范围 + 空间范围 + 向量"，最容易踩坑。

| 阶段         | 做法                                                    |
| ---------- | ----------------------------------------------------- |
| 阶段 0～2（万级） | **不建 ANN 索引**，精确扫描，正确性优先                              |
| 阶段 3+（十万级） | 建 HNSW（`m=16, ef_construction=64`），查询时 `ef_search` 调大 |
| 兜底策略       | 检索层实现两阶段：ANN 结果 < k 时回退精确扫描                           |

### 5.5 关键决策理由

- **后端用 FastAPI**：AI 必须 Python，同进程部署少一层调用
- **pgvector 而非独立向量库**：万级数据够用，少一个 Docker 容器
- **Capacitor 而非 RN/Flutter**：核心业务逻辑尽量复用，不用重写（原生能力和权限仍需平台适配）
- **高德优先**：国内数据准，但坐标转换必须显式处理
- **阶段 0 纯 Python 管道**：先理解 RAG 本质，再学 LangGraph
- **中文全文后置**：PostgreSQL 默认全文对中文支持差
- **Embedding 配置化 + 版本化**：不同模型维度不同，换模型要全量重算
- **可观测先用 JSONL**：Langfuse 多一个服务，阶段 0 不引入
- **自建 PG 镜像**：PostGIS + pgvector 缺一不可，现成镜像不能同时满足

---

## 六、代码结构

文件夹按职责分，不按架构层分。一个层可能对应多个文件夹。

```
travel-memory/
├── frontend/                          React + Capacitor（后置）
│   ├── src/
│   │   ├── pages/
│   │   │   ├── MapPage/               地图页
│   │   │   └── AgentPage/             Agent 页
│   │   ├── components/
│   │   │   ├── ui/                    基础 UI（shadcn）
│   │   │   └── business/              业务组件
│   │   ├── services/
│   │   │   ├── api/
│   │   │   │   ├── client.ts
│   │   │   │   ├── photo.ts
│   │   │   │   ├── trip.ts
│   │   │   │   └── agent.ts           SSE 流式（阶段 2+）
│   │   │   └── native/
│   │   │       ├── photoSource.ts     Web / Native 双实现
│   │   │       └── index.ts
│   │   ├── workers/
│   │   │   └── thumbnail.worker.ts
│   │   ├── utils/
│   │   │   ├── exif.ts
│   │   │   ├── coord.ts
│   │   │   └── format.ts
│   │   ├── store/
│   │   ├── types/
│   │   └── main.tsx
│   ├── capacitor.config.ts
│   └── package.json
│
├── backend/
│   ├── docker/db/Dockerfile           PostGIS + pgvector 自建镜像
│   ├── app/
│   │   ├── main.py                    入口（阶段 1+）
│   │   ├── cli.py                     阶段 0 主入口
│   │   ├── api/
│   │   │   └── v1/
│   │   │       ├── __init__.py
│   │   │       ├── router.py
│   │   │       ├── photos.py
│   │   │       ├── trips.py
│   │   │       └── agent.py           阶段 0 不建
│   │   ├── schemas/
│   │   │   ├── photo.py
│   │   │   ├── trip.py
│   │   │   ├── agent.py
│   │   │   └── common.py              {code, message, data}
│   │   ├── services/                  业务层
│   │   │   ├── photo_service.py
│   │   │   ├── trip_service.py
│   │   │   ├── map_service.py
│   │   │   ├── document_service.py    文档 CRUD、向量写入、底层查询
│   │   │   └── memory_service.py      记忆读写与衰减
│   │   ├── agent/                     Agent 运行时
│   │   │   ├── service.py             仅接口适配 + AGENT_RUNTIME 分派
│   │   │   ├── pipeline.py            纯 Python 管道（阶段 0～3）
│   │   │   ├── graph.py               状态机（阶段 4）
│   │   │   ├── state.py               AgentState
│   │   │   ├── prompts/               ★ prompt 模板 + 版本号
│   │   │   │   ├── rewrite.md
│   │   │   │   ├── query_parse.md
│   │   │   │   ├── generate.md
│   │   │   │   ├── self_check.md
│   │   │   │   └── memory_extract.md
│   │   │   ├── nodes/
│   │   │   │   ├── pipeline/          阶段 0～3 节点
│   │   │   │   │   ├── rewrite_query.py
│   │   │   │   │   ├── query_parse.py     时间/地点/主题抽取
│   │   │   │   │   ├── retrieve.py
│   │   │   │   │   ├── build_context.py
│   │   │   │   │   ├── generate.py
│   │   │   │   │   └── self_check.py      ★ 原 evaluate.py，改名避免混淆
│   │   │   │   └── graph/             阶段 4 节点
│   │   │   │       ├── plan.py
│   │   │   │       ├── call_tool.py
│   │   │   │       ├── reflect.py
│   │   │   │       └── write_memory.py
│   │   │   ├── memory/
│   │   │   │   ├── short_term.py
│   │   │   │   ├── episodic.py
│   │   │   │   ├── semantic.py
│   │   │   │   ├── preference.py
│   │   │   │   └── procedural.py      P2，v1 仅占位
│   │   │   ├── rag/
│   │   │   │   ├── indexer.py         调 DocumentService 写入
│   │   │   │   ├── retriever.py       改写、解析、过滤编排、RRF、rerank
│   │   │   │   ├── query_rewrite.py
│   │   │   │   ├── query_parse.py     结构化解析（时间/空间/主题）
│   │   │   │   ├── reranker.py
│   │   │   │   └── context_builder.py
│   │   │   ├── eval/                  ★ 离线评测，与运行时无关
│   │   │   │   ├── dataset.py
│   │   │   │   ├── metrics.py
│   │   │   │   └── runner.py
│   │   │   └── observability.py       统一写 AgentRun/Step/ToolCallLog
│   │   ├── jobs/                      ★ 离线批处理
│   │   │   ├── base.py                幂等 + 重试 + 成本上限
│   │   │   ├── caption_job.py         VLM 生成 caption / ai_tags
│   │   │   └── index_job.py           文档切分 + embedding 写入
│   │   ├── tools/                     工具适配器
│   │   │   ├── base.py                基类 + Pydantic schema
│   │   │   ├── registry.py            工具注册表
│   │   │   ├── cache.py               TTL 缓存（阶段 0 内存 / 阶段 5 Redis）
│   │   │   ├── memory_tool.py
│   │   │   ├── poi_tool.py
│   │   │   └── route_tool.py
│   │   ├── models/
│   │   │   ├── base.py
│   │   │   ├── photo.py
│   │   │   ├── trip.py
│   │   │   ├── place.py
│   │   │   ├── theme.py
│   │   │   ├── document.py
│   │   │   ├── memory.py              MemoryItem / MemorySource / MemoryEvent
│   │   │   ├── message.py
│   │   │   ├── job.py                 IngestJob
│   │   │   ├── agent_run.py
│   │   │   ├── eval.py
│   │   │   └── tool_call_log.py
│   │   ├── db/
│   │   │   ├── session.py
│   │   │   └── init_db.py
│   │   ├── core/
│   │   │   ├── config.py              含 EMBEDDING_* / AGENT_RUNTIME / 预算
│   │   │   ├── llm.py                 ★ 统一 LLM 入口：重试/超时/成本
│   │   │   ├── security.py
│   │   │   ├── deps.py
│   │   │   └── exceptions.py
│   │   └── utils/
│   │       ├── coord.py
│   │       └── geo.py
│   ├── alembic/
│   │   ├── versions/
│   │   └── env.py
│   ├── tests/
│   │   ├── test_photos.py
│   │   ├── test_trips.py
│   │   ├── test_agent.py
│   │   ├── test_tools.py             ★ 新增
│   │   ├── test_memory.py            ★ 新增
│   │   ├── test_jobs.py              ★ 新增（幂等/重试）
│   │   ├── test_coord.py
│   │   └── test_rag_eval.py
│   ├── data/
│   │   ├── eval_cases.jsonl           评测集，第一天就建
│   │   └── traces/                    JSONL trace
│   ├── requirements.txt
│   └── pyproject.toml
│
├── docker-compose.yml                 完整版（阶段 5+）
├── docker-compose.stage0.yml          只启 PostgreSQL(PostGIS+pgvector)
├── .gitignore
├── .env.stage0.example                ★ 阶段 0 精简
├── .env.example                       完整
├── Makefile
├── PROJECT.md
└── README.md                          含本地启动步骤、分支/提交规范
```


### 文件夹与架构层对应

| 文件夹 | 架构层 | 职责 |
|---|---|---|
| api/v1/ | 接口层 | 定义 URL、收请求、转 Service |
| schemas/ | 接口层 | Pydantic 请求/响应格式 |
| services/ | 业务层 | 业务逻辑（含 DocumentService / MemoryService） |
| agent/ | 业务层 | Agent 运行时（pipeline、graph、nodes、memory、rag、eval） |
| jobs/ | 业务层 | 离线批处理（caption、索引） |
| tools/ | 业务层 | 工具适配器 + 注册表 + 缓存 |
| models/ | 数据层 | SQLAlchemy 表结构 |
| db/ | 数据层 | 数据库连接 |
| core/ | 全局 | 配置、LLM、密钥、鉴权、依赖注入 |
| utils/ | 全局 | 坐标转换、空间计算 |
| data/ | 全局 | 评测集、trace 文件 |
| main.py / cli.py | 全局 | 入口 |

### 职责边界（写死）

| 模块 | 负责 | 不负责 |
|---|---|---|
| DocumentService | 文档 CRUD、向量写入、底层向量/结构化查询 | 查询改写、解析、RRF、rerank |
| rag/retriever.py | 查询改写、结构化解析、过滤编排、RRF、rerank、top-k 组装 | 底层向量写入 |
| rag/indexer.py | 调 DocumentService 写入 | 查询 |
| jobs/* | 幂等调度、重试、成本统计、状态回写 | 业务逻辑（委托 services） |
| core/llm.py | LLM 调用、重试、超时、token/成本统计 | 业务逻辑、prompt 内容 |
| observability.py | 统一写 AgentRun/Step/ToolCallLog | 业务逻辑 |
| nodes/* | 只调 `trace.step(...)`，不自己插 observability 表 | 直接写 observability 表 |
| eval/* | 离线评测，可单独跑 | 不参与运行时 |

### 可扩展性设计

- **工具注册机制**：`tools/registry.py`，新增工具只需继承并注册
- **节点可插拔**：`nodes/pipeline/` 与 `nodes/graph/` 分开
- **Pipeline 与 Graph 并存**：`AGENT_RUNTIME` 环境变量切换，可回滚对比
- **Memory 分层**：short_term / episodic / semantic / preference 独立模块（procedural P2）
- **RAG 管线分段**：indexer / retriever / reranker / context_builder 可单独替换
- **DocumentService / MemoryService**：agent 不直接操作 models
- **Prompt 版本化**：`agent/prompts/*.md` + `AgentRun.prompt_version`，评测结果可归因
- **评测独立**：`agent/eval/` 不依赖 API
- **依赖注入**：`core/deps.py` 统一管理 DB session、当前用户、配置
- **统一响应**：`schemas/common.py` 定义 `{code, message, data}`
- **统一异常**：`core/exceptions.py` 定义业务异常和全局处理器
- **版本化 API**：`/api/v1/` 前缀
- **配置管理**：`core/config.py` 用 Pydantic Settings
- **数据库迁移**：Alembic 管理，不手改表

### 原生能力抽象示例

`services/native/photoSource.ts`

```typescript
export interface PhotoSource {
  pick(): Promise<Photo[]>;
}

class WebPhotoSource implements PhotoSource {
  async pick() { /* input type=file */ }
}

class NativePhotoSource implements PhotoSource {
  async pick() { /* Camera.pickImages() */ }
}

export const photoSource = Capacitor.isNativePlatform()
  ? new NativePhotoSource()
  : new WebPhotoSource();
```

业务代码只调 `photoSource.pick()`，套壳时只换实现类。

---

## 七、数据模型

### 7.1 核心表

```
User
  id, name, created_at

Device / AuthToken
  id, user_id, device_id, token, expires_at
  -- 单用户多设备，不做账号体系

Photo
  id, user_id, platform, asset_id, local_thumb_path, thumb_url
  lat, lng, coord_system
  location_source, location_confidence          -- exif/inferred/user/none
  taken_at, taken_at_local, tz_name, tz_source
  exif_json, caption, ai_tags
  ai_status, ai_model, ai_updated_at            -- pending/running/done/failed
  embedding, embedding_model, hash, deleted_at
  -- dedupe_key = unique(platform, asset_id)

Place
  id, name, lat, lng, address, poi_type, coord_system
  amap_poi_id
  -- unique(amap_poi_id) where not null
  -- 无 POI 时 unique(name, round(lat,4), round(lng,4))

Trip
  id, user_id, title, start_date, end_date, cover_photo_id

TripItem
  id, trip_id, place_id, order, arrive_at, leave_at

TripItemPhoto
  id, trip_item_id, photo_id

Theme
  id, name, category

PhotoTheme
  id, photo_id, theme_id, confidence, source

IngestJob                                        -- 离线批处理，幂等
  id, photo_id, job_type, ai_model
  status, attempts, last_error, cost_cents, created_at
  -- unique(photo_id, job_type, ai_model)

ChatSession
  id, user_id, title, created_at

Message
  id, session_id, role, content, tool_calls JSONB
  token_usage, latency_ms, created_at

MemoryItem                                       -- 统一记忆存储（含偏好）
  id, user_id, type, key, value, confidence
  last_seen_at, decay_rate, expires_at
  embedding, embedding_model

MemorySource                                     -- 替代数组，支持 FK/级联
  id, memory_id, source_type, source_id
  -- index(memory_id) / index(source_type, source_id)

MemoryEvent
  id, memory_id, event_type, old_value, new_value, reason, created_at

Document
  id, user_id, source_type, source_id, chunk_index
  content, metadata JSONB
  token_count, char_len
  embedding, embedding_model

AgentRun
  id, user_id, session_id, query, status
  latency_ms, token_usage, prompt_version, created_at

AgentStep
  id, run_id, step_type, input, output, tool_name, latency_ms, created_at

ToolCallLog
  id, message_id, run_id, tool_name, input, output
  latency_ms, cache_hit, created_at

EvalCase
  id, user_id, question
  expected_evidence JSONB, expected_answer

EvalResult
  id, eval_case_id, run_id
  recall_at_k, mrr, ndcg, citation_rate
  faithfulness, latency_ms, prompt_version
```

### 7.2 索引设计

| 表 | 索引 | 类型 | 何时建 |
|---|---|---|---|
| Photo | `(user_id, taken_at)` | B-tree | 阶段 1 |
| Photo | `(user_id, location_source)` | B-tree | 阶段 6 |
| Photo | `geom` | GIST | 阶段 6（空间过滤/聚类） |
| Photo | `hash` / `unique(platform, asset_id)` | B-tree unique | 阶段 1（去重） |
| Document | `(user_id, source_type)` | B-tree | 阶段 0 |
| Document | `(user_id, embedding_model)` | B-tree | 阶段 0 |
| Document | `metadata` | GIN (jsonb_path_ops) | 阶段 2 |
| Document | `content` | GIN (gin_trgm_ops) | 阶段 2（pg_trgm） |
| Document | `embedding` | **HNSW** | **阶段 3+**（见 §5.4） |
| MemoryItem | `(user_id, type, last_seen_at)` | B-tree | 阶段 4 |
| MemoryItem | `embedding` | HNSW | 阶段 4+ |
| MemorySource | `(source_type, source_id)` | B-tree | 阶段 4（反查） |
| IngestJob | `(status, created_at)` | B-tree | 阶段 1 |
| IngestJob | `unique(photo_id, job_type, ai_model)` | unique | 阶段 1（幂等） |

### 7.3 设计原则

- 原图只存 `asset_id + platform`，展示时通过系统 API 重新取
- 缩略图本地必须有一份（Capacitor 用 Filesystem API 存 Documents 目录，存相对路径，绝对路径会失效），否则离线、重装、换设备后地图就是空壳
- 云端 MinIO 只存缩略图，且要允许用户关闭云同步
- 删除逻辑要级联：删 Photo → 删 MinIO 缩略图 → 删向量 → 删行程关联 → 删 MemorySource 引用
- theme 不用数组，用多对多带 `confidence` 和 `source`
- embedding 维度不写死，从 config 读；**换模型必须 reindex**
- `MemoryItem.embedding` 和 `Document.embedding` 维度必须一致
- 加 hash 去重，避免重复导入
- 加 `deleted_at` 软删除
- Document 是 RAG 的检索单元，Photo/Trip 是数据源
- MemoryItem 是分层记忆的统一存储单元（**不再单设 Preference 表，避免两套衰减逻辑**），MemoryEvent 记录变更历史

### 7.4 评测集稳定标识

**不要用自增 id 做评测标识**（Document 重建后 id 会变，photo/trip 同理）。统一用 `source_type + source_id + chunk_index`：

```jsonl
{"question":"我去年秋天去过哪些古镇？","expected_evidence":[{"source_type":"trip","source_id":"t1","chunk_index":0}],"expected_answer":"..."}
{"question":"我在杭州最喜欢哪类地点？","expected_evidence":[{"source_type":"place","source_id":"p12","chunk_index":0}]}
{"question":"帮我规划一天类似我上次的路线。","expected_evidence":[{"source_type":"trip","source_id":"t2","chunk_index":0}]}
```

`source_type` 取值：`photo` / `trip` / `trip_item` / `place` / `note`。

---

## 八、离线批处理管道（阶段 1 必须建立）

caption / ai_tags 是 RAG 的前提（照片本身没有文本），但原图不上传、缩略图与 MinIO 在阶段 6。**因此批处理管道必须与阶段编号解耦，在阶段 1 就建好**，阶段 1 可先用本地样例缩略图跑通。

### 8.1 流程

```
照片/缩略图就绪
→ 入队 IngestJob(job_type='caption', ai_model=...)
→ caption_job 消费（限流 + 重试 + 成本上限）
→ VLM 生成 caption + ai_tags → 写回 Photo（ai_status='done'）
→ 入队 IngestJob(job_type='index')
→ index_job：按 Trip/TripItem/天 聚合 → chunk → embedding → 写 Document
```

### 8.2 硬约束

- **幂等**：`unique(photo_id, job_type, ai_model)`，同一任务不重复烧 token
- **重试**：`attempts` 上限 3，失败记 `last_error`，不阻塞其他任务
- **成本**：`MAX_CAPTION_PER_RUN`（如 200）+ `LLM_DAILY_BUDGET`，超限直接停
- **状态可见**：`Photo.ai_status` 让前端能展示"处理中/失败"
- **可重跑**：`make reindex` 清空 Document 向量后全量重建（换 embedding 模型时必须执行）
- 阶段 0～5 用 CLI + APScheduler/RQ 即可，**不要过早引入 Celery + MQ**

---

## 九、Agent 设计（核心）

### 9.1 阶段 0～3：纯 Python 管道

```
rewrite -> query_parse -> retrieve -> build_context -> generate -> self_check
```

每步是独立函数，方便单独测试和替换。先理解 RAG 和 Memory 的本质。

> `self_check` 是**运行时自检**（引用是否完整、是否无证据瞎编）；`agent/eval/` 是**离线评测**。两者不要混用同一个名字。

### 9.2 阶段 4：迁 LangGraph

`graph.py` 定义状态机，节点包括：

```
rewrite_query → query_parse → retrieve → plan → call_tool → generate → self_check → reflect → write_memory
```

`AgentState` 包含：

```
query, rewritten_query, parsed_filters, retrieved_docs, memory_hits
tool_results, plan, answer, citations, memory_updates, token_usage
```

### 9.3 RAG 管线

**离线索引**：

```
照片元数据 + AI caption + ai_tags + 地点名 + 时间 + 用户备注
→ 按 Trip / TripItem / 天 聚合成文档
→ chunk（单文档 > 800 token 滑窗切分，overlap 10%）
→ embedding
→ 存 pgvector + 元数据（带 embedding_model）
```

**在线检索（阶段 0）**：

```
用户问题
→ 查询改写
→ 元数据过滤（user_id / embedding_model）
→ 向量 top-k（精确扫描）
→ 上下文构建
→ LLM 生成带引用回答
→ self_check（引用完整 + 拒答判定）
```

**在线检索（阶段 2+）**：

```
+ Query 结构化解析（时间/地点/主题 → 过滤条件）
+ 时间、空间、行程过滤
+ PostgreSQL 全文（pg_trgm + ILIKE）
+ RRF 融合（阶段 4）
+ Reranker 重排（阶段 3）
+ 上下文压缩
```

**关键点**：照片本身没有文本。必须先生成 `caption` / `ai_tags`，否则向量检索效果很差。阶段 0 至少要对地点、时间、用户备注、行程标题做结构化检索，再逐步加向量。

### 9.4 Query 结构化解析（本项目的辨识度所在）

用户问"我去年秋天去过哪些古镇"，"去年秋天"必须转成时间范围，"古镇"要转成主题/POI 类型。这是元数据过滤能否生效的关键。

```
LLM 抽取 → {
  "time_range": {"start": "2025-09-01", "end": "2025-11-30"},
  "places": ["乌镇", "西塘"],
  "themes": ["古镇"],
  "trip_refs": []
}
→ 转成 SQL 过滤条件 + 向量查询
```

实现要点：

- 阶段 2 引入，独立节点 `query_parse.py`，可单独评测（解析准确率作为一项指标）
- 解析失败或不确定时**退化为不过滤**（宁可多召回，不要漏召回）
- 时间解析要用"当前日期"做锚点注入 prompt（LLM 不知道今天是哪天）
- 中文时间表达（"前年""上个夏天""五一前后"）单独做测试用例

### 9.5 上下文融合与拒答策略

**融合顺序（固定模板）**：

```
[系统指令]
[用户偏好 / 语义记忆]     ← query_user_memory 结果
[当前行程上下文]          ← 短期记忆
[检索证据]                ← Document top-k（带编号 [1][2][3]）
[对话历史]
[用户问题]
```

规则：

- memory 结果放在证据之前，作为"背景"；两者冲突时以**检索到的原始证据**为准
- 每个证据块带稳定标识（`source_type + source_id + chunk_index`），prompt 里用 `[1]` 编号，要求 LLM 引用编号
- token 预算：证据总长超过预算时按 rerank 分数截断，并在回答里说明"还找到 N 条相关记录"

**拒答策略（必须实现）**：

| 情况 | 行为 |
|---|---|
| 检索结果为空 | 明确说"你的照片里没有找到相关记录"，并给替代动作（"要不要搜一下附近的 XX？"） |
| 最高 rerank 分数 < 阈值 | 同上，或回答时显式加"不确定"限定 |
| `self_check` 判定引用缺失 | 降级为拒答，不允许输出无引用事实 |
| 涉及未来/实时信息（天气、门票） | 必须走工具，禁止用记忆编造 |

**引用率 100%** 是硬指标（见 §0 验收标准），`self_check` 节点校验，失败即重生成一次，仍失败则拒答。

### 9.6 Memory 分层

- **短期记忆**：当前会话消息、当前行程上下文
- **情景记忆**：某次旅行、某个 Trip、某组照片的事件摘要
- **语义记忆**：用户偏好、事实、长期兴趣
- **程序性记忆**：常用路线、规划模板、工具使用经验（**P2，v1 不实现**）

**衰减公式（写死，别留空）**：

```
weight(t) = w0 * exp(-λ * Δt_days)
λ = ln(2) / half_life_days
默认 half_life_days = 30（偏好类可设 90）
weight < 0.05 或 now > expires_at → 归档（不物理删除）
```

**写回策略（不是每轮都写）**：

- 每 **8 轮**生成一次会话摘要（N 默认值）
- 用户明确表达偏好时抽取（"我不喜欢爬山"）
- 行程结束后生成复盘并写回情景记忆
- 新偏好覆盖旧偏好时：写入新值 + 记 MemoryEvent + **置信度低于 0.6 时向用户确认**（"你之前说过喜欢古镇，这次是说不喜欢了吗？"）
- 低权重记忆按时间衰减或用户删除

### 9.7 工具校验

- 每个工具入参用 Pydantic schema 校验
- `query_user_memory` 返回结构化证据：`photo_ids`、`trip_ids`、`snippets`、`scores`
- Agent 最终回答必须引用照片/行程 ID，方便前端高亮地图
- `search_poi` 返回高德 POI ID，不要只返回自然语言
- `calculate_route` 用高德路径规划，PostGIS 主要负责距离、附近、聚类
- 加超时、重试、缓存（POI/路线/逆地编 TTL 缓存）、限流
- 加 ToolCallLog（含 `cache_hit`），方便调试幻觉


### 9.8 评估与可观测

**评测集第一天就建**，`data/eval_cases.jsonl`。

阶段 0 先测检索命中，不测生成忠实度。

**指标**：

- 检索：Recall@k、MRR、nDCG
- 解析：时间/地点抽取准确率
- 生成：引用准确率（目标 100%）、忠实度、有用性
- 工具：工具选择准确率、参数准确率
- 系统：延迟、Token、成本

**评测可归因**：`EvalResult` 记录 `prompt_version` + `embedding_model`，换 prompt / 换模型时输出**对比表**（`make eval --compare`），否则改完不知道是变好还是变坏。

**可观测**：

- 阶段 0：AgentRun / AgentStep / ToolCallLog + `traces/*.jsonl`
- 阶段 5：Langfuse 自托管
- **统一由 `agent/observability.py` 写**，节点里只调 `trace.step(...)`，不要每个节点自己插表

---

## 十、开发路线（严格串行，阶段号为准）

| 阶段 | 时间 | 目标 |
|---|---|---|
| 阶段 0 | 第 1 周 | 纯文本 RAG CLI |
| 阶段 1 | 第 2 周 | 离线批处理 + 照片元数据接入 |
| 阶段 2 | 第 3 周 | 结构化 + 向量混合检索 |
| 阶段 3 | 第 4 周 | 工具调用 + Reranker |
| 阶段 4 | 第 5～6 周 | Memory + LangGraph |
| 阶段 5 | 第 7 周 | 评估与可观测 |
| 阶段 6 | 第 8～10 周 | 地图相册 + Capacitor |

### 阶段 0：纯文本 RAG CLI（第 1 周）

- 旅行笔记 → chunk → embedding → pgvector → 带引用问答
- 纯 Python 管道：rewrite → query_parse → retrieve → build_context → generate → self_check
- 建评测集 20～30 条，先测检索命中
- 可观测用 JSONL + 表记录
- **第一天选定 embedding 模型**（写入 `EMBEDDING_MODEL_VERSION`）
- **第一天验证 DB 镜像能同时 `CREATE EXTENSION postgis` 和 `vector`**
- **主入口：`python -m app.cli`**；本阶段不建 `api/`

### 阶段 1：离线批处理 + 接入照片元数据（第 2 周）

- 建 `jobs/` 框架：IngestJob 幂等、重试、成本上限
- `caption_job.py` 生成 caption + ai_tags（可用本地样例缩略图先跑通）
- `index_job.py` 建 Document 与向量
- 结构化过滤 + 向量 top-k
- 不碰地图、不碰 EXIF、不碰 Capacitor

### 阶段 2：结构化 + 向量混合检索（第 3 周）

- Query 结构化解析（时间/地点/主题）
- 时间、空间、行程过滤 + 向量检索
- 中文全文先用 pg_trgm + ILIKE
- SSE 流式输出
- 暂时不做 RRF

### 阶段 3：工具调用 + Reranker（第 4 周）

- search_poi、calculate_route，Pydantic schema、超时、重试、缓存
- 加 bge-reranker-v2-m3（无 GPU 时用 Qwen rerank API）重排
- 建 HNSW 索引（数据量上来后）+ ANN 结果不足时的精确扫描回退

### 阶段 4：Memory + LangGraph（第 5～6 周）

- 会话摘要、偏好抽取、情景记忆、写回与衰减
- Pipeline 迁 LangGraph（`AGENT_RUNTIME` 可切换对比）
- 中文全文升级 zhparser 或 pg_jieba
- 加入 RRF 融合

### 阶段 5：评估与可观测（第 7 周）

- 评测集扩充，加生成忠实度、引用率、解析准确率
- 评测结果对比表（按 prompt_version / embedding_model）
- Langfuse 自托管，指标看板

### 阶段 6：地图相册 + Capacitor（第 8～10 周）

- 导入、EXIF、缩略图、气泡、行程分组、路线
- 无位置照片处理（§4.2）
- 原生权限、asset_id、沙盒缩略图
- **真机验证 iOS EXIF GPS 是否可读（第一天就要做，见 §12）**

> 注：Capacitor 做不到 100% 代码复用。核心业务逻辑尽量复用，**原生能力和权限需要平台适配**。

---

## 十一、执行文件

### `.gitignore`

```
__pycache__/
*.pyc
.env
.env.stage0
.venv/
node_modules/
dist/
build/
*.log
data/traces/*.jsonl
!data/traces/.gitkeep
.DS_Store
.idea/
.vscode/
```

### `.env.stage0.example`（阶段 0 只需要这些）

```dotenv
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/travel_memory
EMBEDDING_MODEL=bge-m3
EMBEDDING_MODEL_VERSION=bge-m3@v1
EMBEDDING_DIM=1024
LLM_API_KEY=
LLM_BASE_URL=
AGENT_RUNTIME=pipeline
LLM_DAILY_BUDGET_CENTS=2000
```

### `.env.example`（完整版，阶段 3+ 才需要后半部分）

```dotenv
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/travel_memory
EMBEDDING_MODEL=bge-m3
EMBEDDING_MODEL_VERSION=bge-m3@v1
EMBEDDING_DIM=1024
LLM_API_KEY=
LLM_BASE_URL=
AGENT_RUNTIME=pipeline
LLM_DAILY_BUDGET_CENTS=2000

# 阶段 3+
AMAP_API_KEY=
AMAP_JS_KEY=
RERANK_MODEL=bge-reranker-v2-m3
REDIS_URL=redis://localhost:6379/0

# 阶段 6+
MINIO_ENDPOINT=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
```

### `Makefile`

```makefile
.PHONY: install dev up down migrate test eval cli caption-batch index-batch reindex db-shell lint

install:
	cd backend && pip install -r requirements.txt

dev:
	cd backend && uvicorn app.main:app --reload

up:
	docker compose -f docker-compose.stage0.yml up -d --build

down:
	docker compose -f docker-compose.stage0.yml down

db-shell:
	docker compose -f docker-compose.stage0.yml exec db psql -U user -d travel_memory

migrate:
	cd backend && alembic upgrade head

test:
	cd backend && pytest

eval:
	cd backend && python -m app.agent.eval.runner

eval-compare:                 # 对比不同 prompt_version / embedding_model
	cd backend && python -m app.agent.eval.runner --compare

cli:
	cd backend && python -m app.cli

caption-batch:                # 幂等，--limit 控制单次成本
	cd backend && python -m app.jobs.caption_job --limit 200

index-batch:
	cd backend && python -m app.jobs.index_job --limit 500

reindex:                      # 换 embedding 模型后必须执行
	cd backend && python -m app.jobs.index_job --force

lint:
	cd backend && ruff check . && mypy app
```

### Docker Compose 分阶段

**`docker-compose.stage0.yml`**：只启 PostgreSQL（PostGIS + pgvector 自建镜像）

```yaml
services:
  db:
    build: ./backend/docker/db
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: pass
      POSTGRES_DB: travel_memory
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

**`docker-compose.yml`**：完整版（阶段 5+ 再加 MinIO、Redis、Nginx、Langfuse）

---

## 十二、风险清单

| 风险 | 对策 |
|---|---|
| **隐私**：缩略图、GPS、时间、人脸都可能敏感 | 隐私政策、授权说明、可删除、可导出、可关闭上传、MinIO 预签名 URL、传输加密 |
| **坐标偏移**：WGS84 存储，GCJ-02 展示 | 显式 `coord_system` 字段；高德转换 API 有 40 点/次与配额限制，要分批缓存 |
| **PostGIS + pgvector 镜像** | 自建 Dockerfile（§5.1），第一天验证两个 extension 都能建 |
| **iOS EXIF 丢失**：Capacitor 取图可能拿不到 GPS | **真机第一天验证**；备选 PHAsset + `requestContentEditingInput` 或社区插件；否则"EXIF 解析放前端"这条原则要改 |
| **URI 失效**：Web File 临时，Android MediaStore 可能变，iOS 用 PHAsset localIdentifier，Capacitor webPath 临时 | 只存 `platform + asset_id`，展示时重新取；缩略图存 Filesystem 相对路径 |
| **无 GPS 照片占比高** | `location_source` + 时间邻近推断 + 手动标注 + "未定位"分组（§4.2） |
| **时区错误导致按天分组错** | 双写 `taken_at` / `taken_at_local` + `tz_name`（§4.3） |
| **高德 Key 泄露** | JS Key 前端可见，必须配安全密钥和域名白名单；Web 调用走后端代理 |
| **LLM 幻觉** | 工具校验、证据链、引用率硬指标、无证据拒答、ToolCallLog |
| **成本失控** | `LLM_DAILY_BUDGET` 硬上限、批处理 `--limit`、缓存、幂等去重 |
| **换 embedding 模型产生脏向量** | `embedding_model` 字段 + 启动校验 + `make reindex` |
| **HNSW + 过滤召回不足** | 阶段 0～2 精确扫描；阶段 3+ 加 HNSW 且实现回退（§5.4） |
| **RAG 效果差**：照片无文本 | 必须先生成 caption / ai_tags，再向量检索；批处理管道阶段 1 就建 |
| **中文全文检索** | 阶段 0 不做，阶段 2 pg_trgm，阶段 4 zhparser/pg_jieba |
| **Memory 膨胀** | 写回克制（8 轮一次）、衰减公式、过期归档、冲突确认 |
| **评测集不稳定** | 统一用 `source_type + source_id + chunk_index` 做标识 |
| **评测无法归因** | prompt 版本化 + `EvalResult` 记录版本 + `--compare` 对比表 |
| **LangGraph 过早引入** | 阶段 0～3 纯 Python 管道，阶段 4 再迁，用 `AGENT_RUNTIME` 可回滚 |
| **冷启动**：没照片时无内容 | 支持手动添加"想去清单" |
| **Embedding 维度写死** | 配置化 + 版本化，Alembic 用配置生成 vector 维度 |

---

## 十三、演示剧本（3 分钟，服务第二目标）

| 时间 | 动作 | 展示的技术点 |
|---|---|---|
| 0:00–0:40 | 导入 1000 张照片 → 地图自动成图、按天聚合成行程 | EXIF、时区、坐标转换、规则聚类、无位置兜底 |
| 0:40–1:20 | 问"我去年秋天去过哪些古镇？" | Query 结构化解析（时间+主题）→ 混合检索 → 带引用回答 → **地图上高亮证据照片** |
| 1:20–1:50 | 追问"那帮我规划一个类似的周末路线" | Memory 命中偏好 → Planning → search_poi / calculate_route → 工具调用 trace |
| 1:50–2:20 | 说"我不太喜欢人多的地方" → 再问一次 | 偏好抽取 → 写回 → 二次问答结果变化（**证明 Memory 生效**） |
| 2:20–2:50 | 打开 trace 面板 | Token、延迟、工具调用链、引用来源 |
| 2:50–3:00 | 打开评测报告 | Recall@5、引用率 100%、拒答样例 |

**答辩/简历可用的量化点**：Recall@5 ≥ 0.8、引用率 100%、1000 张导入 < 10 分钟、单次问答成本 < ¥0.05。

---

## 十四、一句话总结（v2）

1. **Agent 是主线，地图相册是数据源和可视化；P0 严格串行，地图最后。**
2. **照片不搬家**，只借元数据和缩略图；**WGS84 存储，GCJ-02 展示**。
3. **RAG 必须有 caption / ai_tags**，批处理管道阶段 1 就建，不能拖到阶段 6。
4. **Embedding 配置化 + 版本化**，换模型必须 `make reindex`。
5. **无证据必须拒答，引用率 100% 是硬指标。**
6. **评测集第一天就建**，prompt 版本化，评测结果要能对比归因。
7. 依赖方向不循环：`agent -> nodes -> tools -> services -> models`，可观测统一由 `observability.py` 写。

---

## 附：v2 相对 v1 的主要改动

| 类别 | 改动 |
|---|---|
| 修正 | DB 镜像自建（PostGIS + pgvector）、补 GeoAlchemy2 |
| 修正 | HNSW 索引时机推迟到阶段 3+，阶段 0～2 精确扫描 |
| 补齐 | caption / ai_tags 批处理链路（IngestJob、幂等、成本上限） |
| 补齐 | 时区模型（taken_at + taken_at_local + tz_name） |
| 补齐 | 无位置照片处理策略 |
| 补齐 | Query 结构化解析、拒答与引用策略 |
| 补齐 | Memory 衰减公式、冲突消解、N 默认值 |
| 补齐 | 索引设计、LLM 抽象层、prompt 版本化、SSE 流式、缓存与预算 |
| 结构 | `Preference` 并入 `MemoryItem`；数组引用改 `MemorySource` 关联表 |
| 结构 | pipeline 的 `evaluate` 改名 `self_check`，与离线 `eval/` 区分 |
| 结构 | 新增 §0 规模与验收、§8 批处理、§13 演示剧本 |
| 文档 | 统一"阶段"编号（周次仅作补充）、去重、补总工期、压缩总结 |
