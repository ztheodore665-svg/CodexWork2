# 旅忆地图 TravelMemory

> 一句话：以个人照片地图记忆为数据源的旅行记忆 Agent。地图相册是数据底座和可视化，Agent 是核心。

**第一目标**：学习 Agent / RAG / Memory 开发。
**第二目标**：做出可演示、可答辩、可写简历的完整应用。

**核心设计**：照片不搬家，只借缩略图和元数据。Agent 基于真实照片/行程数据做问答、复盘、规划。

---

## 一、项目定位

一个以个人照片地图记忆为数据源的 AI Agent 应用。

- **数据层**：照片导入 → EXIF/位置/时间 → 地图气泡 → 行程分组 → 路线可视化
- **智能层**：RAG 检索 + 分层 Memory + Tool Use + Planning + Reflection + Evaluation
- **展示层**：地图相册 + Agent 对话页

**非目标（明确不做）**：

- 不做原图云备份
- 不做社交/信息流/关注
- 不做多用户协作
- 不做内容审核
- 不做完整社区

**学习目标（写进 PROJECT.md）**：

- RAG：索引、检索、重排、上下文构建、引用
- Memory：短期会话、情景记忆、语义偏好、程序性记忆
- Tool Use：工具 schema、调用、校验、错误处理、缓存
- Planning：任务拆解、多步执行、状态机
- Reflection：复盘、摘要、写回记忆
- Evaluation：检索指标、答案忠实度、工具选择准确率
- Observability：Trace、Token、延迟、成本、工具日志

---

## 二、核心功能（P0 严格串行）

### 第 1 周：纯文本 RAG CLI

旅行笔记/模拟照片描述 → chunk → embedding → pgvector → 带引用问答

不碰地图、不碰 EXIF、不碰 Capacitor。

### 第 2 周：接入照片元数据

caption + ai_tags + 地点 + 时间 + 用户备注 → 文档 → 混合检索

地图相册、EXIF、缩略图、Capacitor 全部后置。

### P0 真正要做的（按顺序）

1. 纯文本 RAG 管道跑通（rewrite → retrieve → build_context → generate → evaluate）
2. 照片元数据接入（caption + ai_tags 生成）
3. 结构化过滤 + 向量检索（时间、地点、行程）
4. 工具调用：search_poi、calculate_route
5. Memory：会话摘要 + 偏好抽取 + 写回
6. 评估：20～30 个 QA 对 + 检索命中率
7. 可观测：JSONL + AgentRun/AgentStep/ToolCallLog

### P1 拉开差距

- 地图相册最小闭环（导入、EXIF、缩略图、气泡、行程分组、路线）
- 导入时快速筛选
- AI 主题打标 + 主题相册视图
- Agent 多步 Planning
- 偏好画像 + 时间衰减 + 冲突处理
- 行程导出 / 分享卡片

### P2 有精力再说

- AI 照片预打分
- 偏好迁移报告
- 自己 vs 自己的路线对比
- Capacitor 套壳

### 自动聚类先规则后算法

- 同一天
- 相邻照片时间差 < N 小时
- 空间距离 < M 公里
- 停留点合并

先跑通规则，再考虑 DBSCAN + PostGIS 复杂时空聚类。

---

## 三、架构设计

### 四层架构

```
① 客户端层    React + TypeScript + Capacitor（后置）
              地图渲染 / 相册 / Agent UI

② 接口层      FastAPI Router + Pydantic
              路由 / 参数校验 / 统一响应 / 版本管理

③ 业务服务层  PhotoService / TripService / MapService
              DocumentService / MemoryService
              AgentService（仅接口适配）

④ 数据智能层  PostgreSQL + PostGIS + pgvector
              MinIO / LLM / 高德 API / JSONL trace
```

### 依赖方向（严格单向）

```
api/v1/agent.py -> agent/service.py -> agent/pipeline.py（阶段 0～3）
api/v1/agent.py -> agent/service.py -> agent/graph.py（阶段 4+）
agent/pipeline.py -> agent/nodes/pipeline/* -> tools/registry -> tools/* -> services/*
agent/graph.py -> agent/nodes/graph/* -> tools/registry -> tools/* -> services/*
agent/rag/indexer.py -> services/document_service.py
agent/memory/* -> services/memory_service.py
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
- pipeline / graph 是 Agent 运行时入口
- nodes 是各步骤节点
- tools 是适配器，调 services

### 前后端边界

| 功能 | 前端 | 后端 | 理由 |
|---|---|---|---|
| 照片选择 | ✅ | | 系统能力 |
| EXIF 解析 | ✅ | | 原图不上传，隐私 |
| 缩略图生成 | ✅ | | 省流量 |
| 地图渲染 | ✅ | | 高德 JS |
| 气泡聚合 | ✅ | | Supercluster |
| 行程分组（规则） | ✅ 可先做 | ✅ 持久化 | |
| 逆地理编码 | | ✅ | 藏 API Key |
| Agent 编排 | ✅ UI | ✅ 逻辑 | LLM 必须在后端 |
| 记忆检索 | | ✅ | pgvector + 结构化过滤 |

**原则**：渲染和隐私放前端，计算和智能放后端。

### 两条核心数据流

**导入照片**

```
用户选照片 → 前端 exifr 读 GPS → Canvas 生成缩略图
→ POST /api/v1/photos → 存 MinIO + PostgreSQL → 地图渲染气泡
（原图从未离开手机）
```

**Agent 问答/规划**

```
用户输入 → POST /api/v1/agent/chat
→ agent/service.py 转 pipeline / graph
→ rewrite_query → retrieve → build_context → generate → evaluate
→ 工具调用（search_poi / calculate_route）
→ LLM 生成带引用回答
→ write_memory 写回
→ 返回前端，高亮地图证据
```

### 阶段 0 的纯 Python 管道（不用 LangGraph）

```
rewrite -> retrieve -> build_context -> generate -> evaluate
```

阶段 4 再迁 LangGraph。先理解 RAG 和 Memory 的本质，再学状态机。

### Agent 工具链

```
agent/nodes/pipeline/call_tool.py（阶段 4）
  query_user_memory → MemoryService / DocumentService
  search_poi → MapService
  calculate_route → MapService
  get_weather → 外部 API
```

**原则**：

- LLM 只做决策，地点必须经地图 API 校验，降低幻觉
- 每个工具入参用 Pydantic schema 校验
- 最终回答必须引用照片/行程 ID，方便前端高亮地图

---

## 四、坐标系统

- EXIF GPS 通常是 **WGS84**
- 高德地图和大部分高德 API 使用 **GCJ-02**
- 必须做 WGS84 与 GCJ-02 转换，不用自己实现偏移算法，可调高德转换或成熟库

**统一规则**：

- PostGIS 里统一存 WGS84
- API 默认返回 WGS84
- 前端给高德 JS 渲染前转 GCJ-02
- 后端调高德 POI/路径规划前转 GCJ-02
- 逆地理编码结果回来后再统一转回 WGS84 存储

数据模型和 API 里显式加 `coord_system` 字段，否则后期偏移问题很难查。

---

## 五、技术栈

| 模块 | 选型 | 备注 |
|---|---|---|
| 前端框架 | React + TypeScript + Vite | 后置 |
| 地图 | 高德 JS API | 后置，JS Key 必须配安全密钥和域名白名单 |
| UI | Tailwind CSS + shadcn/ui | 后置 |
| 后端 | FastAPI + SQLAlchemy 2.0 + Pydantic v2 + Alembic | |
| 数据库 | PostgreSQL + PostGIS + pgvector | |
| 对象存储 | MinIO | 后置，只存缩略图 |
| Agent 编排 | 阶段 0～3 纯 Python 管道，阶段 4 迁 LangGraph | |
| RAG | 自己实现核心流程，不用黑盒框架 | |
| Embedding | **第一天必须选定** | 见下方 |
| Reranker | bge-reranker-v2-m3 之类 | 阶段 3 再加 |
| 向量库 | pgvector，加 HNSW 索引 | |
| 可观测 | 阶段 0：JSONL + 表记录；阶段 5：Langfuse | |
| LLM | DeepSeek / Qwen 做对话 | |
| 移动端 | Capacitor | 阶段 6 |
| 部署 | Docker Compose + Nginx | 分阶段 |
| CI/CD | GitHub Actions | |
| 测试 | pytest + Playwright + 坐标转换 + 聚类 + RAG 评测 | |

### Embedding 模型（第一天必须选定）

三选一，先用一个，别纠结：

| 方案 | 模型 | 维度 | 场景 |
|---|---|---|---|
| 国内 API | Qwen text-embedding-v3 | 按文档配置 | 有 API，省事 |
| 本地 | bge-m3 | 1024 | 想本地跑 |
| OpenAI | text-embedding-3-small | 1536 | 有海外 API |

配置写在 `core/config.py`：

```python
EMBEDDING_MODEL: str
EMBEDDING_DIM: int
```

Alembic 迁移用配置生成 vector 维度。

### 中文全文检索（分阶段）

| 阶段 | 方案 | 说明 |
|---|---|---|
| 阶段 0 | 纯向量 + 结构化过滤 | 不做全文 |
| 阶段 2 | pg_trgm + ILIKE | 临时方案，按字符 trigram 切，效果一般 |
| 阶段 4 | zhparser 或 pg_jieba | Docker 镜像编译扩展 |

阶段 0 不做 RRF，先向量 top-k + 元数据过滤。

### 关键决策理由

- **后端用 FastAPI**：AI 必须 Python，同进程部署少一层调用
- **pgvector 而非独立向量库**：万级数据够用，少一个 Docker 容器
- **Capacitor 而非 RN/Flutter**：核心业务逻辑尽量复用，不用重写
- **高德优先**：国内数据准，但坐标转换必须显式处理
- **阶段 0 纯 Python 管道**：先理解 RAG 本质，再学 LangGraph
- **中文全文后置**：PostgreSQL 默认全文对中文支持差，先向量 + 结构化过滤
- **Embedding 配置化**：不同模型维度不同，Alembic 迁移用配置生成 vector 维度
- **可观测先用 JSONL**：Langfuse 多一个服务，阶段 0 不引入

---

## 六、代码结构（规范性与可扩展性）

### 目录约定

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
│   │   │   │   └── agent.ts
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
├── backend/                           FastAPI
│   ├── app/
│   │   ├── main.py                    入口
│   │   ├── cli.py                     阶段 0 主入口
│   │   ├── api/
│   │   │   └── v1/
│   │   │       ├── __init__.py
│   │   │       ├── router.py
│   │   │       ├── photos.py
│   │   │       ├── trips.py
│   │   │       └── agent.py           阶段 0 可建但不用
│   │   ├── schemas/
│   │   │   ├── photo.py
│   │   │   ├── trip.py
│   │   │   ├── agent.py
│   │   │   └── common.py
│   │   ├── services/                  业务层
│   │   │   ├── photo_service.py
│   │   │   ├── trip_service.py
│   │   │   ├── map_service.py
│   │   │   ├── document_service.py    文档 CRUD、向量写入、底层查询
│   │   │   └── memory_service.py      记忆读写与衰减
│   │   ├── agent/                     Agent 运行时
│   │   │   ├── service.py             仅接口适配
│   │   │   ├── pipeline.py            纯 Python 管道（阶段 0～3）
│   │   │   ├── graph.py               状态机（阶段 4）
│   │   │   ├── state.py               AgentState
│   │   │   ├── nodes/
│   │   │   │   ├── pipeline/          阶段 0～3 节点
│   │   │   │   │   ├── rewrite_query.py
│   │   │   │   │   ├── retrieve.py
│   │   │   │   │   ├── build_context.py
│   │   │   │   │   ├── generate.py
│   │   │   │   │   └── evaluate.py
│   │   │   │   └── graph/             阶段 4 节点
│   │   │   │       ├── plan.py
│   │   │   │       ├── call_tool.py
│   │   │   │       ├── reflect.py
│   │   │   │       └── write_memory.py
│   │   │   ├── memory/
│   │   │   │   ├── short_term.py
│   │   │   │   ├── episodic.py
│   │   │   │   ├── semantic.py
│   │   │   │   └── preference.py
│   │   │   ├── rag/
│   │   │   │   ├── indexer.py         调 DocumentService 写入
│   │   │   │   ├── retriever.py       查询改写、过滤编排、RRF、rerank
│   │   │   │   ├── reranker.py
│   │   │   │   ├── query_rewrite.py
│   │   │   │   └── context_builder.py
│   │   │   ├── eval/
│   │   │   │   ├── dataset.py
│   │   │   │   ├── metrics.py
│   │   │   │   └── runner.py
│   │   │   └── observability.py       统一写 AgentRun/Step/ToolCallLog
│   │   ├── tools/                     工具适配器
│   │   │   ├── base.py                基类
│   │   │   ├── registry.py            工具注册表
│   │   │   ├── memory_tool.py
│   │   │   ├── poi_tool.py
│   │   │   └── route_tool.py
│   │   ├── models/
│   │   │   ├── base.py
│   │   │   ├── photo.py
│   │   │   ├── trip.py
│   │   │   ├── theme.py
│   │   │   ├── document.py
│   │   │   ├── memory.py
│   │   │   ├── message.py
│   │   │   ├── agent_run.py
│   │   │   ├── eval.py
│   │   │   └── tool_call_log.py
│   │   ├── db/
│   │   │   ├── session.py
│   │   │   └── init_db.py
│   │   ├── core/
│   │   │   ├── config.py              含 EMBEDDING_MODEL / EMBEDDING_DIM
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
│   │   ├── test_coord.py
│   │   └── test_rag_eval.py
│   ├── data/
│   │   ├── eval_cases.jsonl           评测集，第一天就建
│   │   └── traces/                    JSONL trace
│   ├── requirements.txt
│   └── pyproject.toml
│
├── docker-compose.yml                 分阶段
├── docker-compose.stage0.yml          只启 PostgreSQL + pgvector
├── .gitignore
├── .env.example
├── Makefile
├── PROJECT.md
└── README.md
```

### 文件夹与架构层对应

| 文件夹 | 架构层 | 职责 |
|---|---|---|
| api/v1/ | 接口层 | 定义 URL、收请求、转 Service |
| schemas/ | 接口层 | Pydantic 请求/响应格式 |
| services/ | 业务层 | 业务逻辑（含 DocumentService / MemoryService） |
| agent/ | 业务层 | Agent 运行时（pipeline、graph、nodes、memory、rag、eval） |
| tools/ | 业务层 | 工具适配器 + 注册表 |
| models/ | 数据层 | SQLAlchemy 表结构 |
| db/ | 数据层 | 数据库连接 |
| core/ | 全局 | 配置、密钥、鉴权、依赖注入 |
| utils/ | 全局 | 坐标转换、空间计算 |
| data/ | 全局 | 评测集、trace 文件 |
| main.py / cli.py | 全局 | 入口 |

### 职责边界（写死）

| 模块 | 负责 | 不负责 |
|---|---|---|
| DocumentService | 文档 CRUD、向量写入、底层向量/结构化查询 | 查询改写、RRF、rerank |
| rag/retriever.py | 查询改写、元数据过滤编排、RRF、rerank、top-k 组装 | 底层向量写入 |
| rag/indexer.py | 调 DocumentService 写入 | 查询 |
| observability.py | 统一写 AgentRun/Step/ToolCallLog | 业务逻辑 |
| nodes/* | 只调 trace.step(...)，不自己插表 | 直接写 observability 表 |

### 调用规则

```
api/v1/agent.py -> agent/service.py -> agent/pipeline.py（阶段 0～3）
api/v1/agent.py -> agent/service.py -> agent/graph.py（阶段 4+）
agent/pipeline.py -> agent/nodes/pipeline/* -> tools/registry -> tools/* -> services/*
agent/graph.py -> agent/nodes/graph/* -> tools/registry -> tools/* -> services/*
agent/rag/indexer.py -> services/document_service.py
agent/memory/* -> services/memory_service.py
普通 services 不调 tools
tools 只被 agent/nodes 调用
AgentService 只做接口适配，不直接调工具
schemas 和 models 分开：前者是 API 格式，后者是数据库结构
```

### 可扩展性设计

- **工具注册机制**：`tools/registry.py` 定义注册表，新增工具只需继承并注册
- **节点可插拔**：`agent/nodes/pipeline/` 和 `agent/nodes/graph/` 分开，各自独立
- **Pipeline 与 Graph 并存**：阶段 0～3 用 pipeline，阶段 4 平滑迁 graph
- **Memory 分层**：short_term / episodic / semantic / preference 独立模块
- **RAG 管线分段**：indexer / retriever / reranker / context_builder 可单独替换
- **DocumentService / MemoryService**：agent 不直接操作 models，保持依赖干净
- **评测独立**：`agent/eval/` 不依赖 API，可单独跑
- **依赖注入**：`core/deps.py` 统一管理 DB session、当前用户、配置
- **统一响应**：`schemas/common.py` 定义 `{code, message, data}`
- **统一异常**：`core/exceptions.py` 定义业务异常和全局处理器
- **版本化 API**：`/api/v1/` 前缀
- **配置管理**：`core/config.py` 用 Pydantic Settings
- **Embedding 配置化**：`EMBEDDING_MODEL` / `EMBEDDING_DIM`，Alembic 迁移用配置生成 vector 维度
- **数据库迁移**：Alembic 管理，不手改表
- **测试目录**：与 app 结构对应，覆盖核心逻辑和 RAG 评测

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

### 核心表

```
User
  id, name, created_at

Device / AuthToken
  id, user_id, device_id, token, expires_at

Photo
  id, user_id, platform, asset_id, local_thumb_path, thumb_url
  lat, lng, coord_system, taken_at, taken_at_tz
  exif_json, caption, ai_tags, embedding, hash, deleted_at

Place
  id, name, lat, lng, address, poi_type, coord_system

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

ChatSession
  id, user_id, title, created_at

Message
  id, session_id, role, content, tool_calls JSONB
  token_usage, latency_ms, created_at

Preference
  id, user_id, tag, weight, last_seen_at, decay_rate, source

Document
  id, user_id, source_type, source_id, chunk_index
  content, metadata JSONB, embedding

MemoryItem
  id, user_id, type, key, value, confidence
  source_photo_ids, source_trip_ids
  last_seen_at, decay_rate, expires_at, embedding

MemoryEvent
  id, memory_id, event_type, old_value, new_value, reason, created_at

AgentRun
  id, user_id, session_id, query, status, latency_ms, token_usage, created_at

AgentStep
  id, run_id, step_type, input, output, tool_name, latency_ms, created_at

ToolCallLog
  id, message_id, run_id, tool_name, input, output, latency_ms, created_at

EvalCase
  id, user_id, question
  expected_photo_ids, expected_trip_ids
  expected_doc_keys, expected_answer

EvalResult
  id, eval_case_id, run_id, recall_at_k, mrr, faithfulness, latency_ms
```

### 设计原则

- 原图只存 `asset_id + platform`，展示时通过系统 API 重新取
- 缩略图本地必须有一份，否则离线、重装、换设备后地图就是空壳
- 云端 MinIO 只存缩略图，且要允许用户关闭云同步
- 删除逻辑要级联：删 Photo → 删 MinIO 缩略图 → 删向量 → 删行程关联 → 删 Memory 引用
- theme 不用数组，用多对多带 `confidence` 和 `source`
- embedding 维度不要写死，从 config 读，Alembic 迁移用配置生成
- **MemoryItem.embedding 和 Document.embedding 维度必须一致**，都从 `EMBEDDING_DIM` 读
- 加 hash 去重，避免重复导入
- 加 `deleted_at` 软删除
- Document 是 RAG 的检索单元，Photo/Trip 是数据源
- MemoryItem 是分层记忆的存储单元，MemoryEvent 记录变更历史

### 评测集稳定标识

**不要用 Document.id 做评测标识**，Document 重建后 id 会变。

用 `source_type + source_id + chunk_index` 作为稳定标识：

```jsonl
{"question":"我去年秋天去过哪些古镇？","expected_trip_ids":["t1"],"expected_doc_keys":[{"source_type":"trip","source_id":"t1","chunk_index":0}]}
{"question":"我在杭州最喜欢哪类地点？","expected_doc_keys":[{"source_type":"place","source_id":"p12","chunk_index":0}]}
{"question":"帮我规划一天类似我上次的路线。","expected_trip_ids":["t2"]}
```

---

## 八、Agent 设计（核心）

### 阶段 0～3：纯 Python 管道

`pipeline.py`：

```
rewrite -> retrieve -> build_context -> generate -> evaluate
```

每步是独立函数，方便单独测试和替换。先理解 RAG 和 Memory 的本质。

### 阶段 4：迁 LangGraph

`graph.py` 定义状态机，节点包括：

```
rewrite_query → retrieve → plan → call_tool → generate → reflect → write_memory
```

`AgentState` 包含：

```
query, rewritten_query, retrieved_docs, tool_results
plan, answer, citations, memory_updates, token_usage
```

### RAG 管线

**离线索引**：

```
照片元数据 + AI caption + ai_tags + 地点名 + 时间 + 用户备注
→ 按 Trip / TripItem / 天 聚合成文档
→ chunk
→ embedding
→ 存 pgvector + 元数据
```

**在线检索（阶段 0）**：

```
用户问题
→ 查询改写
→ 元数据过滤（user_id / trip_id / 时间 / 空间）
→ 向量 top-k
→ 上下文构建
→ LLM 生成带引用回答
```

**在线检索（阶段 2+）**：

```
加入 PostgreSQL 全文（pg_trgm + ILIKE）
→ RRF 融合
→ Reranker 重排
→ 上下文压缩
```

**关键点**：照片本身没有文本。必须先生成 `caption` / `ai_tags`，否则向量检索效果很差。阶段 0 至少要对地点、时间、用户备注、行程标题做结构化检索，再逐步加向量。

### Memory 分层

- **短期记忆**：当前会话消息、当前行程上下文
- **情景记忆**：某次旅行、某个 Trip、某组照片的事件摘要
- **语义记忆**：用户偏好、事实、长期兴趣
- **程序性记忆**：常用路线、规划模板、工具使用经验

**写回策略（不是每轮都写）**：

- 每 N 轮生成会话摘要
- 用户明确表达偏好时抽取
- 行程结束后生成复盘并写回情景记忆
- 新偏好覆盖旧偏好时记录冲突
- 低权重记忆按时间衰减或用户删除

### 工具校验

- 每个工具入参用 Pydantic schema 校验
- `query_user_memory` 返回结构化证据：`photo_ids`、`trip_ids`、`snippets`、`scores`
- Agent 最终回答必须引用照片/行程 ID，方便前端高亮地图
- `search_poi` 返回高德 POI ID，不要只返回自然语言
- `calculate_route` 用高德路径规划，PostGIS 主要负责距离、附近、聚类
- 加超时、重试、缓存、限流
- 加 ToolCallLog，方便调试幻觉

### 评估与可观测

**评测集第一天就建**，`data/eval_cases.jsonl`。

阶段 0 先测检索命中，不测生成忠实度。

**指标**：

- 检索：Recall@k、MRR、nDCG
- 生成：引用准确率、忠实度、有用性
- 工具：工具选择准确率、参数准确率
- 系统：延迟、Token、成本

**可观测**：

- 阶段 0：AgentRun / AgentStep / ToolCallLog + `traces/*.jsonl`
- 阶段 5：Langfuse 自托管
- **统一由 `agent/observability.py` 写**，节点里只调 `trace.step(...)`，不要每个节点自己插表

---

## 九、开发路线（严格串行）

### 阶段 0：纯文本 RAG CLI（第 1 周）

- 旅行笔记 → chunk → embedding → pgvector → 带引用问答
- 纯 Python 管道：rewrite -> retrieve -> build_context -> generate -> evaluate
- 建评测集 20～30 条，先测检索命中
- 可观测用 JSONL + 表记录
- **第一天选定 embedding 模型**
- **主入口：`python -m app.cli`**

### 阶段 1：接入照片元数据（第 2 周）

- caption + ai_tags + 地点 + 时间 + 用户备注 → Document → 混合检索
- 结构化过滤 + 向量 top-k
- 不碰地图、不碰 EXIF、不碰 Capacitor

### 阶段 2：结构化 + 向量混合检索

- 时间、空间、行程过滤 + 向量检索
- 中文全文先用 pg_trgm + ILIKE
- 暂时不做 RRF

### 阶段 3：工具调用 + Reranker

- search_poi、calculate_route，Pydantic schema、超时、重试、缓存
- 加 bge-reranker-v2-m3 重排

### 阶段 4：Memory + LangGraph

- 会话摘要、偏好抽取、情景记忆、写回与衰减
- Pipeline 迁 LangGraph
- 中文全文升级 zhparser 或 pg_jieba
- 加入 RRF 融合

### 阶段 5：评估与可观测

- 评测集扩充，加生成忠实度
- Langfuse 自托管，指标看板

### 阶段 6：地图相册 + Capacitor

- 导入、EXIF、缩略图、气泡、行程分组、路线
- 原生权限、asset_id、沙盒缩略图

**Capacitor 100% 代码复用**改成：核心业务逻辑尽量复用，原生能力和权限需要平台适配。

---

## 十、执行文件（缺失补齐）

### `.gitignore`

```
__pycache__/
*.pyc
.env
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

### `.env.example`

```
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/travel_memory
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIM=1024
LLM_API_KEY=
LLM_BASE_URL=
AMAP_API_KEY=
MINIO_ENDPOINT=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
```

### `Makefile`

```makefile
.PHONY: install dev up down migrate test eval cli

install:
	cd backend && pip install -r requirements.txt

dev:
	cd backend && uvicorn app.main:app --reload

up:
	docker compose -f docker-compose.stage0.yml up -d

down:
	docker compose -f docker-compose.stage0.yml down

migrate:
	cd backend && alembic upgrade head

test:
	cd backend && pytest

eval:
	cd backend && python -m app.agent.eval.runner

cli:
	cd backend && python -m app.cli
```

### Docker Compose 分阶段

**`docker-compose.stage0.yml`**：只启 PostgreSQL + pgvector

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
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

**`docker-compose.yml`**：完整版（阶段 5+ 再加 MinIO、Nginx、Langfuse）

---

## 十一、文档补充

1. 非目标：不做什么
2. 验收标准：1000 张照片导入多久、地图是否卡、Agent 是否必须引用证据、RAG 召回率
3. ER 图：Photo / Trip / TripItem / Theme / Document / MemoryItem / Message 关系
4. 关键时序图：导入照片、Agent 规划、Memory 写回
5. API 示例：请求/响应 JSON
6. 风险清单

---

## 十二、风险清单

- **隐私**：缩略图、GPS、时间、人脸都可能敏感。需隐私政策、授权说明、可删除、可导出、可关闭上传、MinIO 预签名 URL、传输加密
- **坐标偏移**：WGS84 存储，GCJ-02 展示，显式 `coord_system` 字段
- **URI 失效**：Web File 临时，Android MediaStore 可能变，iOS 用 PHAsset localIdentifier，Capacitor webPath 临时
- **高德 Key**：JS Key 前端可见，必须配安全密钥和域名白名单
- **LLM 幻觉**：工具校验、证据链、ToolCallLog
- **成本**：LLM 调用、地图 API 配额、MinIO 存储
- **冷启动**：没照片时支持手动添加想去清单
- **RAG 效果差**：照片无文本，必须先生成 caption / ai_tags，再向量检索
- **中文全文检索**：PostgreSQL 默认支持差，阶段 0 不做，阶段 2 用 pg_trgm，阶段 4 升级 zhparser/pg_jieba
- **Memory 膨胀**：写回策略要克制，加衰减和过期
- **评估缺失**：没有评测集就无法验证 RAG 和 Memory 是否有效
- **Embedding 维度写死**：不同模型维度不同，必须配置化
- **LangGraph 过早引入**：阶段 0～3 用纯 Python 管道，阶段 4 再迁
- **评测集不稳定**：用 `source_type + source_id + chunk_index` 做标识，不用 Document.id

---

## 十三、如果只改三件事

1. **P0 严格串行**：第 1 周只做纯文本 RAG CLI，第 2 周再接照片元数据，地图相册全部后置。
2. **新增 DocumentService / MemoryService**，agent 不直接操作 models，依赖方向保持干净。
3. **评估集第一天就建**，可观测先用 JSONL，Langfuse 后置。

---

## 十四、一句话总结

- Agent 是主线，地图相册是数据源和可视化。
- P0 严格串行：先纯文本 RAG，再照片元数据，地图最后。
- 照片不搬家，只借元数据和缩略图。
- WGS84 存储，GCJ-02 展示。
- RAG 必须有 caption/ai_tags，否则检索无效。
- 中文全文后置：阶段 0 向量 + 结构化过滤，阶段 2 pg_trgm，阶段 4 zhparser。
- Embedding 维度配置化，不写死。
- Memory 分层，写回克制，衰减和冲突要设计。
- 依赖方向不循环：agent -> nodes -> tools -> services -> models。
- DocumentService / MemoryService 隔离 agent 和 models。
- 评估集第一天就建，用 `source_type + source_id + chunk_index` 做稳定标识。
- 可观测统一由 `observability.py` 写，节点只调 `trace.step(...)`。
- 阶段 0～3 纯 Python 管道，阶段 4 再迁 LangGraph。
- Docker Compose 分阶段，阶段 0 只启 PostgreSQL + pgvector。
- CLI 先跑通，`python -m app.cli` 是阶段 0 主入口。
- 网页先跑通，Capacitor 套壳最后上。