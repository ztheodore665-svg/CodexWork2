# 旅忆地图 TravelMemory

> **一句话**：以个人照片地图记忆为数据源的旅行记忆 Agent。地图相册是数据底座与可视化，Agent 是核心。

---

## 一、项目简介

### 1.1 它解决什么问题

手机里存着几千张旅行照片，但它们只是按时间排成一列的缩略图。你很难回答这些很自然的问题：

- 我去年秋天去过哪些古镇？
- 我在杭州最喜欢哪类地方？
- 帮我照着上次那趟，再规划一个类似的周末。

**旅忆地图**做两件事：

1. **把照片还原成旅程**：读取照片的时间与位置，自动聚合成行程，在地图上重建你的轨迹。
2. **让旅程可以被对话**：在此之上构建一个 Agent，用自然语言回答关于你旅行记忆的问题，并且**每一句事实性回答都能点开对应的照片证据**。

### 1.2 三条设计底线

| # | 底线          | 说明                                           |
| - | ----------- | -------------------------------------------- |
| 1 | **照片不搬家**   | 原图永不上云，只取 EXIF 元数据 + 本地生成缩略图                 |
| 2 | **坐标系统一**   | WGS84 存储，GCJ-02 展示，所有位置字段显式标注 `coord_system` |
| 3 | **无证据必须拒答** | 事实性回答引用率 100%，缺失即判失败，不允许编造行程                 |

### 1.3 交付形态

- **Web 应用**（主）
- **Android / iOS 应用**：同一套前端代码通过 Capacitor 构建

### 1.4 能力清单

| 类别      | 能力                                             |
| ------- | ---------------------------------------------- |
| 数据      | 照片导入、EXIF 与 GPS 解析、缩略图生成、去重、无位置照片兜底、时区处理       |
| 地图      | 气泡聚合渲染（万级）、按天/空间自动聚类成行程、路线可视化、行程分组与编辑          |
| 检索      | 向量检索 + 结构化过滤（时间/地点/主题）、中文全文、RRF 融合、Reranker 重排 |
| Agent   | 查询改写、结构化解析、工具调用、多步规划、反思、带引用生成                  |
| Memory  | 短期会话 / 情景记忆 / 语义偏好三层，指数衰减与冲突处理，来源可追溯           |
| 交互      | SSE 流式输出、证据高亮、行程复盘、路线规划                        |
| 工程      | 离线批处理（幂等 + 成本控制）、全链路 trace、离线评测集与版本化归因         |
| 隐私（扩展）  | 差分隐私扰动、语义泛化、精度标签渲染、可验证分享                       |
| 推荐（扩展）  | 匿名语义轨迹模式挖掘、k-匿名发布、小样本目的地推荐                     |
| 可验证（扩展） | 零知识范围证明、承诺锚定、独立验证端                             |

### 1.5 明确不做

- 不做原图云备份、社交 / 信息流 / 关注、多用户协作（**单用户多设备**模型）
- 不做内容审核、不做社区
- 不做代币经济、链上身份体系，**不部署公链主网**
- 不做"具体地点级"的协同过滤 —— 这是本项目与主流推荐系统的显式差异，也是立论前提

---

## 二、系统架构

### 2.1 分层

```
┌─ ① 客户端层 ────────────────────────────────────────────┐
│  React + TypeScript + Vite + Capacitor                  │
│  地图渲染 / 相册 / Agent 对话 / 精度标签 / 验证徽章        │
│  Web 与 Android / iOS 共用同一套代码，原生能力插件化        │
└─────────────────────────────────────────────────────────┘
                        ↓ HTTPS / SSE
┌─ ② 接口层 ──────────────────────────────────────────────┐
│  FastAPI Router + Pydantic v2                           │
│  路由 / 参数校验 / 统一响应 / 版本前缀                     │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─ ③ 业务服务层 ──────────────────────────────────────────┐
│  Photo / Trip / Map / Document / Memory                 │
│  Privacy / SemanticPattern / BlockchainBridge（旁路）     │
│  AgentService（仅接口适配）                               │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─ ④ 数据与智能层 ────────────────────────────────────────┐
│  PostgreSQL 16 + PostGIS + pgvector + pg_trgm           │
│  Redis（多级缓存 / 会话态 / 幂等锁 / 预算计数）             │
│  MinIO（缩略图） / LLM API / 高德 API / JSONL trace       │
└─────────────────────────────────────────────────────────┘

┌─ ⑤ 旁路验证层（永不阻塞 ①～④）──────────────────────────┐
│  ZK 电路执行器（进程外 + 超时熔断）                        │
│  链适配器（本地哈希链 / OpenTimestamps / L2 测试网）       │
│  所有调用异步，失败降级为「未验证」                        │
└─────────────────────────────────────────────────────────┘
```

### 2.2 依赖方向

```
api/v1/agent.py → agent/service.py → agent/pipeline.py    （阶段 0～3）
api/v1/agent.py → agent/service.py → agent/graph.py       （阶段 4+）

agent/pipeline.py → agent/nodes/pipeline/* → tools/registry → tools/* → services/*
agent/graph.py    → agent/nodes/graph/*    → tools/registry → tools/* → services/*

agent/rag/indexer.py   → services/document_service.py
agent/rag/retriever.py → services/document_service.py
agent/memory/*         → services/memory_service.py
jobs/*                 → services/* + core/llm.py + core/cache.py

约束：
  普通 services 不调用 tools；tools 只被 agent/nodes 调用
  AgentService 只做接口适配，不直接调工具
  blockchain_bridge 只能被 tools/* 与 jobs/* 调用
  旁路模块失败必须抛 DegradedException，由调用方降级
```

### 2.3 前后端职责边界

| 功能                         | 前端   | 后端   | 理由                     |
| -------------------------- | ---- | ---- | ---------------------- |
| 照片选择 / EXIF 解析 / 缩略图生成     | ✅    |      | 原图不上传                  |
| 地图渲染 / 气泡聚合                | ✅    |      | 高德 JS + Supercluster   |
| 图层管理 / 地图动画                | ✅    |      | 命令式 API + OverlayUI    |
| 聚合点查询接口                    |      | ✅    | 返回 bbox + zoom 聚合结果   |
| 精度标签渲染 / 验证徽章              | ✅    |      | 由后端 `precision` 字段驱动   |
| 逆地理编码                      |      | ✅    | 隐藏 API Key             |
| Agent 编排                   | ✅ UI | ✅ 逻辑 | LLM 必须在后端              |
| 记忆检索                       |      | ✅    | pgvector + 结构化过滤       |
| caption / ai_tags / 语义标签生成 |      | ✅    | 需要 VLM，且必须幂等可重试        |
| ZKP 证明生成                   | ❌    | ✅    | 电路规模大                  |
| ZKP 验证                     | ✅ 可选 | ✅    | 验证很轻，前端验一次可演示"无需信任服务器" |

**原则**：渲染与隐私放前端，计算与智能放后端，证明放后端、验证可两端。

### 2.4 三条核心数据流

**A. 导入与成图**

```
选照片 → exifr 读 GPS → Canvas 生成缩略图 → POST /photos
→ 存 PostgreSQL + MinIO → 入队 IngestJob（caption / ai_tags）
→ 离线批处理消费 → 写回 Photo → 入队 index → 聚合 Document + embedding
→ 地图渲染气泡 → 规则聚类生成 Trip / TripItem
（原图从未离开设备）
```

**B. Agent 问答与规划**

```
用户输入 → POST /agent/chat（SSE 流式）
→ rewrite → query_parse → retrieve → build_context → generate → self_check
→ 工具调用（search_poi / calculate_route / semantic_recommend）
→ LLM 生成带引用回答 → write_memory 写回
→ observability 统一记录 trace → 返回前端，地图高亮证据
```

**C. 可验证分享（扩展）**

```
用户选择行程 + share_mode + ε
→ PrivacyService.obfuscate_route() → 泛化几何 + precision 标签
→ [可选] ZK 电路生成 proof（进程外，超时熔断）
→ [可选] 异步上链锚定（失败 = 未锚定，不影响分享）
→ 返回 {route_fuzzy, precision, assertion, verify_url}
```

---

## 三、技术选型

### 3.1 选型表

| 模块        | 选型                                                       | 说明                        |
| --------- | -------------------------------------------------------- | ------------------------- |
| 前端        | React + TypeScript + Vite + Tailwind + shadcn/ui         |                           |
| 跨端        | **Capacitor**                                            | 一套代码出 Web / Android / iOS |
| 地图        | 高德 JS API（Web）/ 高德 Web 服务 API（后端）                        | JS Key 配安全密钥 + 域名白名单      |
| 聚合渲染      | Supercluster + Canvas / WebGL                            | 万级点不用 DOM Marker          |
| 后端        | FastAPI + SQLAlchemy 2.0 + Pydantic v2 + Alembic         |                           |
| 数据库       | PostgreSQL 16 + **PostGIS** + **pgvector** + **pg_trgm** | 需自建镜像                     |
| 空间 ORM    | GeoAlchemy2                                              | PostGIS 必需                |
| 缓存        | **Redis 7**                                              | 四级用途，见 3.2                |
| 对象存储      | MinIO                                                    | 仅存缩略图                     |
| Agent 编排  | 阶段 0～3 纯 Python 管道；阶段 4 起 LangGraph                      | `AGENT_RUNTIME` 可切换       |
| RAG       | 自行实现，不引入黑盒框架                                             |                           |
| Embedding | Qwen text-embedding-v3（默认 1024 维）/ bge-m3                |                           |
| Reranker  | bge-reranker-v2-m3（本地需 GPU）或 Qwen rerank（API）            | 阶段 3                      |
| LLM       | DeepSeek / Qwen                                          | 统一封装于 `core/llm.py`       |
| 可观测       | JSONL + 数据库表；可选 Langfuse 自托管                             |                           |
| 部署        | Docker Compose（单文件 + profiles）+ Nginx                    |                           |
| 测试        | pytest + Playwright + RAG 评测                             |                           |

### 3.2 Redis 的四个用途

| 用途               | 结构                                     | TTL                      | 解决什么                     |
| ---------------- | -------------------------------------- | ------------------------ | ------------------------ |
| ① 外部 API 结果缓存    | String（JSON）                           | POI 7d / 路线 1d / 逆地编 30d | 高德 API 有配额，重复查询直接烧钱      |
| ② Embedding 结果缓存 | String，key = `sha256(content + model)` | 永久（LRU）                  | 重跑索引时不重复付费               |
| ③ 短期会话记忆 + 流式状态  | List（滑动窗口）+ Hash                       | 24h                      | 会话上下文不必落库                |
| ④ 幂等锁 + 预算计数     | SETNX + INCR/EXPIRE                    | 任务级 / 每日                 | 批处理不重复烧 token；LLM 日预算硬上限 |

> 缓存只用于外部调用与会话态，**不缓存用户数据本体**，从根上避免缓存与数据库的一致性问题。

### 3.3 跨端实现

```
frontend/src/services/native/
  photoSource.ts      interface PhotoSource { pick(): Promise<Photo[]> }
                      ├─ WebPhotoSource     → <input type="file">
                      └─ NativePhotoSource  → Camera / MediaStore / PHPicker
  thumbnailStore.ts   缩略图持久化（Filesystem API，存相对路径）
  permissions.ts      相册读写 / 定位权限申请
```

真实需要处理的差异：

- iOS 取图可能丢失 EXIF GPS，需要 PHAsset 回退方案，**真机优先验证**
- 缩略图必须落沙盒持久目录（`Directory.Documents`），存相对路径（绝对路径重装后失效）
- Web 端 `URL.createObjectURL` 与原生 `webPath` 生命周期不同，需统一抽象
- 权限拒绝、相册大规模导入（> 5000 张）的分批与进度

### 3.4 数据库镜像

`pgvector/pgvector:pg16` 不含 PostGIS，`postgis/postgis:16-3.4` 不含 pgvector，**必须自建**：

```dockerfile
FROM postgis/postgis:16-3.4
RUN apt-get update && apt-get install -y postgresql-16-pgvector && rm -rf /var/lib/apt/lists/*
```

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

### 3.5 关键决策理由

| 决策                        | 理由                                                                                                         |
| ------------------------- | ---------------------------------------------------------------------------------------------------------- |
| FastAPI                   | AI 栈必须是 Python，同进程部署少一层调用                                                                                  |
| pgvector 而非独立向量库          | 万级数据足够，少一个服务组件                                                                                             |
| **阶段 0～2 不建 HNSW 索引**     | pgvector 的 HNSW 是"先图遍历、后过滤"，带 `WHERE user_id + 时间 + 空间` 时可能返回 0 条；先用精确扫描保证正确性，阶段 3+ 建 HNSW 并配套"结果不足回退精确扫描" |
| Capacitor 而非 RN / Flutter | 已有前端代码可复用；但原生能力与权限必须平台适配                                                                                   |
| 阶段 0 不用 LangGraph         | 先理解 RAG 与 Memory 本质，再引入状态机                                                                                 |
| pg_trgm 一档到底              | 编译 PostgreSQL 分词扩展是纯运维成本，收益不成正比                                                                            |
| 单文件 Compose + profiles    | 多套 compose 文件是维护负担                                                                                         |

---

## 四、数据模型

### 4.1 核心表

```
User
  id, name, created_at                       -- 单用户部署，不做账号体系

Photo
  id, user_id, platform, asset_id, local_thumb_path, thumb_url
  lat, lng, coord_system
  location_source, location_confidence       -- exif / inferred / user / none
  taken_at, taken_at_local, tz_name, tz_source
  exif_json, caption, ai_tags
  ai_status, ai_model, ai_updated_at         -- pending / running / done / failed
  embedding, embedding_model, hash, deleted_at
  -- unique(platform, asset_id) 去重

Place
  id, name, lat, lng, address, poi_type, coord_system, amap_poi_id

Trip
  id, user_id, title, start_date, end_date, cover_photo_id
  share_mode, privacy_version                -- 扩展
  semantic_sequence JSONB, semantic_version, avg_score      -- 扩展

TripItem
  id, trip_id, place_id, order, arrive_at, leave_at
  semantic_tag, semantic_intensity, score    -- 扩展

TripItemPhoto
  id, trip_item_id, photo_id

ChatSession
  id, user_id, title, created_at

Message
  id, session_id, role, content, tool_calls JSONB
  token_usage, latency_ms, created_at

Document
  id, user_id, source_type, source_id, chunk_index
  content, metadata JSONB, token_count, char_len
  embedding, embedding_model
  -- unique(source_type, source_id, chunk_index)

MemoryItem
  id, user_id, type, key, value, confidence
  last_seen_at, decay_rate, expires_at
  embedding, embedding_model, chain_commitment

MemorySource
  id, memory_id, source_type, source_id      -- 关联表而非数组，支持外键与反查

IngestJob
  id, photo_id, job_type, ai_model
  status, attempts, last_error, cost_cents, created_at
  -- unique(photo_id, job_type, ai_model) 保证幂等

AgentRun
  id, user_id, session_id, query, status
  latency_ms, token_usage, prompt_version

AgentStep
  id, run_id, step_type, input, output, tool_name, latency_ms, created_at

ToolCallLog
  id, message_id, run_id, tool_name, input, output
  latency_ms, cache_hit, created_at

EvalCase
  id, user_id, question, kind                 -- rag / memory / recommend / refusal
  expected_evidence JSONB, expected_answer

EvalResult
  id, eval_case_id, run_id
  recall_at_k, mrr, citation_rate
  latency_ms, prompt_version
```

### 4.2 扩展表（可整块移除，不影响主流程）

```
PrivacyAssertion
  id, user_id, trip_id, share_mode, mechanism, epsilon
  public_inputs JSONB          -- region_bbox / time_window / semantic_digest / avg_score
  proof BYTEA, proof_scheme    -- none / merkle_sig / groth16 / halo2
  circuit_version, commitment
  prev_hash                    -- 本地哈希链
  chain_tx_hash, status, expires_at, created_at

SemanticPattern
  id, pattern JSONB, pattern_version
  support, source_count, avg_score, score_sum, score_n
  confidence, published        -- support >= k 才发布
  last_updated

SemanticContribution
  id, user_id, round_id, pattern_version
  encrypted_update BYTEA, noise_scale, proof BYTEA
  aggregated, created_at
  -- unique(user_id, round_id, pattern_version)  防重复贡献
```

### 4.3 设计原则

- 原图只存 `platform + asset_id`，展示时通过系统 API 重新取；缩略图必须落设备持久目录
- 删除级联：删 Photo → 删 MinIO 缩略图 → 删 Document 向量 → 删行程关联 → 删 MemorySource 引用 → 删 PrivacyAssertion
- Embedding 维度不写死，从配置读；**换模型必须 `make reindex`**
- `Document.embedding` 与 `MemoryItem.embedding` 维度必须一致
- Document 是检索单元，Photo / Trip 是数据源；MemoryItem 是分层记忆的统一存储单元
- **原始坐标永不因分享而修改**：扰动只发生在读取出口
- **链上只写 32 字节哈希**，不含任何坐标、语义、身份

### 4.4 三个版本化

| 版本项 | 变更时必须执行 |
| --- | --- |
| `EMBEDDING_MODEL_VERSION` | `make reindex` |
| `SEMANTIC_TAG_VERSION` | `make resemantic` + 重跑实验 |
| `PRIVACY_VERSION` | 重跑隐私-效用曲线 |

启动时校验库内主流值与配置一致，不一致直接报错退出，避免脏向量。

### 4.5 评测集稳定标识

自增 id 在重建后会变化，**统一使用 `source_type + source_id + chunk_index`**：

```jsonl
{"kind":"rag","question":"我去年秋天去过哪些古镇？","expected_evidence":[{"source_type":"trip","source_id":"t1","chunk_index":0}]}
{"kind":"refusal","question":"我 2019 年去过巴黎吗？","expected_answer":"拒答（无证据）"}
{"kind":"recommend","question":"去完洛阳该去哪？","expected_answer":"自然风光类","note":"不计入 citation_rate，只计 hit@3"}
```

---

## 五、代码结构

```
travel-memory/
├── README.md / PROJECT.md / Makefile / docker-compose.yml / .env.example
│
├── frontend/
│   ├── src/
│   │   ├── pages/{MapPage, AgentPage, SharePage}
│   │   ├── components/
│   │   │   ├── ui/                           # shadcn 基础组件
│   │   │   └── business/                     # LayerManager / ClusterLayer /
│   │   │                                     # TripRouteLayer / EvidenceLayer /
│   │   │                                     # PrecisionBadge / VerificationBadge
│   │   ├── services/
│   │   │   ├── api/                          # client / photo / trip / agent / share
│   │   │   └── native/                       # photoSource / thumbnailStore / permissions
│   │   ├── workers/
│   │   │   ├── thumbnail.worker.ts           # 缩略图生成
│   │   │   └── cluster.worker.ts             # 照片点聚类（放 Worker，不阻塞主线程）
│   │   ├── utils/{exif, coord, format}.ts
│   │   └── store/ types/ main.tsx
│   └── capacitor.config.ts
│
├── backend/
│   ├── docker/db/Dockerfile                  # PostGIS + pgvector
│   ├── app/
│   │   ├── main.py  cli.py
│   │   ├── api/v1/                           # router / photos / trips / agent / share / recommend
│   │   ├── schemas/                          # 接口契约（与 models 严格分离）
│   │   ├── services/                         # photo / trip / map / document / memory
│   │   │                                     # + privacy / semantic_pattern / blockchain_bridge
│   │   ├── agent/
│   │   │   ├── service.py  pipeline.py  graph.py  state.py
│   │   │   ├── prompts/*.md                  # 版本化 prompt
│   │   │   ├── nodes/pipeline/               # rewrite / query_parse / retrieve /
│   │   │   │                                 # build_context / generate / self_check
│   │   │   ├── nodes/graph/                  # plan / call_tool / reflect / write_memory
│   │   │   ├── memory/                       # short_term / episodic / semantic / preference
│   │   │   ├── rag/                          # indexer / retriever / query_parse /
│   │   │   │                                 # reranker / context_builder
│   │   │   ├── eval/                         # dataset / metrics / runner
│   │   │   └── observability.py              # 统一写 Run / Step / ToolCall
│   │   ├── jobs/                             # base / caption_job / index_job
│   │   │                                     # + semantic_job / anchor_job
│   │   ├── tools/                            # base / registry / cache /
│   │   │                                     # memory_tool / poi_tool / route_tool
│   │   │                                     # + privacy_tool / zk_location_tool / semantic_recommend_tool
│   │   ├── privacy/                          # laplace / gridsnap / metrics（纯函数，无 IO）
│   │   ├── zk/                               # executor / verify / circuit（进程外）
│   │   ├── models/ db/ utils/
│   │   ├── core/                             # config / llm / cache(Redis) / deps /
│   │   │                                     # security / exceptions / feature_flags
│   │   └── data/semantic_tags.yaml           # 语义标签字典（版本化）
│   ├── alembic/  tests/  data/{eval_cases.jsonl, traces/}
│
├── research/{experiments, results}           # 不参与部署
├── deploy/{nginx.conf, docker-compose.prod.yml}
└── docs/                                     # 历史版本文档
```

### 5.1 职责边界

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| DocumentService | 文档 CRUD、向量写入、底层向量/结构化查询 | 查询改写、RRF、rerank |
| rag/retriever.py | 改写、解析、过滤编排、RRF、rerank、top-k 组装 | 底层向量写入 |
| rag/indexer.py | 调 DocumentService 写入 | 查询 |
| jobs/* | 幂等调度、重试、成本统计、状态回写 | 业务逻辑 |
| core/llm.py | LLM 调用、重试、超时、token 与成本统计 | 业务逻辑、prompt 内容 |
| core/cache.py | Redis 读写、TTL 策略、幂等锁、预算计数 | 业务判断 |
| observability.py | 统一写 AgentRun / AgentStep / ToolCallLog | 业务逻辑 |
| nodes/* | 只调 `trace.step(...)` | 直接写可观测表 |
| privacy/* | 纯数学机制，无 IO，可单测 | 数据库、HTTP |
| zk/executor.py | 进程外执行电路、超时熔断、返回 proof | 数据库、业务判断 |
| blockchain_bridge.py | 承诺、锚定、验证；失败降级而非抛错 | 主流程任何必需环节 |

---

## 六、前端地图设计

### 6.1 地图定位

- **地图是数据底座与主界面**，不是附属页面 —— 进应用第一眼看到的就是地图。
- **桌面端**：地图全屏，右侧是 Agent 抽屉，可从边缘拉出或收起。
- **移动端**：地图全屏，底部是上拉 Agent 面板，支持半屏 / 全屏两档。
- **联动**：点击 Agent 回答中的引用 `[1]` → 地图 `flyTo` 到对应照片并高亮，形成"文字 ↔ 地图"的双向定位。

前端地图的核心是**轨迹地图**：照片点 → 聚合 → 行程路线 → 证据高亮。所有图层围绕这条主线组织。

### 6.2 图层栈

```
MapPage
└── BaseMapLayer                        高德底图，永远最下
      · 可缩放 / 平移 / 旋转
      · 只持有 zoom / center / bearing，不持有业务状态
│
└── DataLayers（按 zIndex 由低到高叠加）
      ├── ClusterLayer      照片气泡聚合，低 zoom 显示数字（如 "128"）
      ├── TripRouteLayer    按天 / 按 Trip 的路线折线
      ├── TripItemLayer     行程站点标记（编号 + 地点名）
      ├── PhotoLayer        高 zoom 显示缩略图，替代聚合气泡
      ├── EvidenceLayer     证据高亮 + 脉冲扩散
      └── PrivacyLayer      分享时的模糊圆 / 多边形区域
│
└── OverlayUI（React DOM 覆盖层，浮在 Canvas 之上）
      ├── Timeline          时间轴（拖动过滤时间范围）
      ├── LayerPanel        图层开关面板
      ├── Legend            图例
      └── AgentDrawer       右侧抽屉 / 底部上拉面板
```

**原则**

- 底图不参与业务状态，只负责 `zoom` / `center` / `bearing`，业务数据一律走 DataLayers。
- 每个图层有独立四要素：**可见性、zIndex、数据源、动画状态**，互不共享。
- React 只管理 `OverlayUI` 和图层开关；地图内部一律用命令式 API 操作，**避免 React 重渲染整张地图**。
- 图层管理器统一接口：`register / setVisible / updateData / animateIn / animateOut`，新增图层只需实现这五个方法并注册。

### 6.3 动画优先级

**P0 必做（阶段 6 · W6-2）**

| 场景            | 动画                | 作用          |
| ------------- | ----------------- | ----------- |
| 进入地图          | 底图立即显示，照片点分批淡入    | 首屏快，不白屏     |
| 缩放聚合          | 气泡分裂 / 合并，数字变化    | 万级点可读       |
| 点击气泡          | flyTo + 展开照片网格    | 宏观到微观       |
| 点击证据 `[1]`    | 地图飞过去 + 脉冲圆       | Agent 与地图联动 |
| 行程详情          | 路线逐段绘制            | 讲清轨迹        |
| 时间轴过滤         | 点按时间淡入淡出          | 时间旅行        |

**P1 加分**

- 时间轴自动播放
- 路线逐段生长
- 图层切换淡入淡出
- 证据高亮时其他图层降透明度
- 分享页 ε 滑杆拖动时，模糊圆半径平滑过渡

**P2 可选（不进主线）**

- 轨迹回放
- 粒子扩散
- 隐私圆呼吸
- 3D 倾斜视角

### 6.4 首屏策略

- **地图立即渲染，不等待照片数据** —— 底图出来即可交互，照片是后续叠加的。
- 照片点异步加载：先拿聚合点，高 zoom 再拉原始点。
- **空状态**：没有照片时，地图上直接放"导入照片"引导，而不是空白页。
- 状态机：`idle | loading | exploring | playing | evidence | sharing`。
- **动画是状态转移的视觉反馈，不是独立装饰** —— 每个动画都对应一次状态变化，不做无意义的动效。

### 6.5 实现要点

- 高德 JS API **只初始化一次**，禁用不需要的控件（比例尺、鹰眼、3D 控件等）。
- 聚合用 **Supercluster + Canvas / WebGL**，不用 DOM Marker。
- 1 万点必须 Canvas / WebGL，聚类放 `cluster.worker.ts`。
- 缩略图**高 zoom 才加载**，懒加载。
- 动画只用 `CSS transform / opacity` 或 Canvas 插值，**避免触发 layout**。
- 相机动画用 `flyTo / fitBounds / easeTo`，时长 300–800ms。
- 移动端降级：减少粒子、降低动画帧率，支持 `prefers-reduced-motion`。
- 接口 `GET /photos?bbox=&zoom=&time_range=` 返回聚合点，**避免前端全量拉取**。

### 6.6 性能红线

- 1 万点 ≥ 60fps（沿用阶段 6 出口条件）。
- 拖动 / 缩放时**不要每帧重新聚类**。
- **不要用 DOM Marker 渲染万级点**。
- 动画时长 ≤ 1s。
- 频繁的相机动画要节流。
- Android 真机优先验证。
- 提供 `prefers-reduced-motion` 开关。

---

## 七、Agent 设计

### 7.1 管线

**阶段 0～3（纯 Python）**

```
rewrite → query_parse → retrieve → build_context → generate → self_check
```

**阶段 4+（LangGraph）**

```
rewrite_query → query_parse → retrieve → plan → call_tool → generate → self_check → reflect → write_memory
```

`AGENT_RUNTIME` 环境变量切换，可回滚对比两种实现的评测结果。

### 7.2 离线索引

```
照片元数据 + AI caption + ai_tags + 地点名 + 时间 + 用户备注
→ 按 Trip / TripItem / 天 聚合为文档 → chunk（> 800 token 滑窗，overlap 10%）
→ embedding（经 Redis 缓存去重）→ 写 Document
```

**照片本身没有文本，必须先生成 caption / ai_tags，否则向量检索无效。** 批处理管道在阶段 1 建立，不能拖到最后。

### 7.3 Query 结构化解析

用户问"我去年秋天去过哪些古镇"，"去年秋天"必须转成时间范围，"古镇"转成主题/POI 类型。这是元数据过滤能否生效的关键，也是纯向量方案做不到的地方。

```json
{"time_range": {"start": "2025-09-01", "end": "2025-11-30"},
 "places": ["乌镇", "西塘"], "themes": ["古镇"]}
```

- 独立节点 `query_parse.py`，单独评测解析准确率
- 解析不确定时**退化为不过滤**（宁可多召回，不要漏召回）
- 时间解析需把"当前日期"注入 prompt（模型不知道今天是哪天）
- 中文时间表达（"前年""上个夏天""五一前后"）单独建测试用例
- 扩展阶段增加**意图分类**：`personal_memory` vs `group_recommendation`

### 7.4 上下文融合与拒答

**固定模板顺序**

```
[系统指令]
[用户偏好 / 语义记忆]     ← query_user_memory
[当前行程上下文]          ← 短期记忆
[检索证据]                ← Document top-k，编号 [1][2][3]
[群体语义先验]            ← 扩展：独立区块，标注"来自匿名统计，非你的记录"
[对话历史] / [用户问题]
```

**拒答策略**

| 情况 | 行为 |
| --- | --- |
| 检索为空 | "你的照片里没有找到相关记录"，并给替代动作 |
| 最高 rerank 分数 < 阈值 | 加"不确定"限定，或拒答 |
| self_check 判定引用缺失 | 重试一次，仍失败则拒答 |
| 涉及未来 / 实时信息 | 必须走工具，禁止用记忆编造 |
| 群体模式库无匹配 | "暂无足够匿名模式支撑该建议"，退回个人记忆 + 地图工具 |

引用率 100% 由 `self_check` 节点硬校验。

### 7.5 Memory 分层

| 层 | 内容 |
| --- | --- |
| 短期 | 当前会话消息、当前行程上下文（Redis 滑动窗口） |
| 情景 | 某次旅行 / 某个 Trip 的事件摘要 |
| 语义 | 用户偏好、长期兴趣 |

**衰减公式**

```
weight(t) = w0 · exp(-λ · Δt_days)，λ = ln2 / half_life_days
默认 half_life_days = 30（偏好类 90）
weight < 0.05 或已过期 → 归档（不物理删除）
```

**写回策略（克制）**

- 每 8 轮生成一次会话摘要
- 用户明确表达偏好时抽取
- 行程结束后生成复盘并写回情景记忆
- 新偏好覆盖旧偏好时通过 MemorySource 保留来源；置信度 < 0.6 时向用户确认

### 7.6 工具

| 工具 | 依赖 |
| --- | --- |
| `query_user_memory` | MemoryService / DocumentService，返回 `photo_ids` / `trip_ids` / `snippets` / `scores` |
| `search_poi` | MapService，必须返回高德 POI ID |
| `calculate_route` | MapService，路径规划走高德；PostGIS 负责距离、附近、聚类 |
| `share_route`（扩展） | PrivacyService |
| `semantic_recommend`（扩展） | SemanticPatternService |
| `zk_location`（扩展） | BlockchainBridge，进程外电路 |

统一要求：Pydantic schema 校验入参；超时、重试、缓存、限流；写 ToolCallLog（含 `cache_hit`）。
旁路工具标记 `degradable=True`：失败写 `status='degraded'` 日志后继续，不重试超过 1 次。

### 7.7 评估与可观测

- 评测集第一时间建立，阶段 0 只测检索命中，后续增加生成忠实度
- 指标：Recall@5 / MRR / 解析准确率 / 引用率 / 拒答率 / 工具选择准确率 / 延迟 / Token / 成本
- **评测必须可归因**：`EvalResult` 记录 `prompt_version` + `embedding_model`，`make eval-compare` 输出对比表
- 可观测：JSONL + 表记录，可选接 Langfuse
- **统一由 `observability.py` 写**，节点只调 `trace.step(...)`

---

## 八、隐私保护与可验证分享

> **阶段 6 落地形态：前端定向加密信物 + 后端地理模糊。**
> 分享出口由两半拼成：坐标在**服务端**做拉普拉斯加噪（每次只见一个孤立点），
> 内容在**用户设备上**做定向加密（Ed25519 签名 + X25519 KEM + AES-GCM），
> 服务端全程看不到明文、也拿不到任何私钥。阶段 7 的语义感知扰动与 ZK 保留为科研轨。

### 8.1 威胁模型

| 对手 | 能看到 | 防御 |
| --- | --- | --- |
| A1 好奇的服务端 | 数据库全部明文 | 明示"服务端可见原始坐标"，隐私保证作用于**分享出口** |
| A2 分享接收者 | 模糊圈 + 加密信物 | ε 扰动（后端拉普拉斯）+ 定向加密（非收件人一组都解不开） |
| A3 链上观察者 | 32 字节承诺 + 时间戳 | 链上不写坐标、语义、身份 |
| A4 恶意贡献者 | 全局模式库 | 证明行程真实性 + 每用户每模式仅一次贡献 |
| A5 重识别攻击者 | 分享的语义序列 | k-匿名门禁 + 序列长度截断（≤ 6 步） |

**明确不防御**：物理设备访问者、全球被动监听者（流量分析）、用户主动公开的照片。

> 语义序列本身有重识别风险——"龙门石窟 + 洛邑古城 + 老君山 + 洛阳博物馆"这个四元组几乎唯一确定一个人。因此模式库对外发布前必须满足 k-匿名，这是本方案与朴素"语义分享"的关键区别。

### 8.2 分享出口：两半拼起来

```
用户点「生成信物」
   │
   ├─ 前端对每个带坐标的地点：POST /api/v1/privacy/obfuscate（后端拉普拉斯加噪）
   │      └─ 失败 → 该地点不写坐标（绝不退化成"发原坐标"），打规范原版包
   │
   └─ 前端 pack()：过滤（屏蔽=不打包）→ AES-GCM 加密 → X25519 KEM 包钥匙
         → 组织承诺 → Ed25519 签名 → 导出 .trailshare（JSON 文件，带外传递）
```

| 半 | 位置 | 算法 | 为什么放在这一侧 |
| --- | --- | --- | --- |
| 地理模糊 | 后端 `app/privacy/` | 拉普拉斯机制（geo-indistinguishability） | 前端自己加噪等于把真实坐标和 ε 留在控制台里；服务端每次只见一个孤立点，无从还原轨迹 |
| 定向加密 | 前端 `src/services/crypto`+`share/` | Ed25519 签名 / X25519 KEM / AES-GCM / SHA256 承诺 | 私钥必须只在用户设备（红线 [9]）；零后端，文件传递即可完成分享 |

**分界线**：服务端永远不接触照片、配文、地点名，也永远不接触私钥；它只做"一个点进、一个加噪点出"的纯函数。

### 8.3 后端地理模糊（`app/privacy/`，纯函数 + 薄 API）

```python
obfuscate_location(lat, lng, mode, epsilon) -> {lat, lng, radius, mode, epsilon, coord_system}
```

| mode | 承诺半径 | 用途 |
| --- | --- | --- |
| `exact` | 0 | 不模糊（调试 / 自己看） |
| `approx` | ≈500 m | 默认：街区尺度 |
| `city` | ≈5 km | 城市尺度 |

- 输入输出一律 **WGS84**，响应显式带 `coord_system` 字段（红线 [2]）。
- `radius` 是**承诺半径，不是本次实际偏移量**：每次请求独立抽样，同一坐标调用两次得到两个点，重放校准不出真实位置。
- 开关 `privacy` 关闭时返回 **404 而不是 403**：未启用的能力对外表现为"不存在"（红线 [8]）。
- 坐标越界 / 类型错误由 pydantic 统一翻 422；契约之外的错误翻 400，不吐 500 堆栈。

### 8.4 前端定向加密信物（TrailShare 协议 `ts-2026.4` 移植）

**一份种子，两套密钥**：32 字节种子同时派生 Ed25519（签名，证明"是 A 发的"）与
X25519（KEM，只有指定收件人能解封）。X25519 公钥由 Ed25519 公钥按 RFC 7748 标准转换
派生，对方只给一个公钥就够。

| 组件 | 算法 | 守住的属性 |
| --- | --- | --- |
| 组密钥 | 每组随机 32 字节 | 对称加密快，一组一密钥 |
| 内容加密 | AES-GCM，每张照片独立 12 字节 IV | 改一字节即解密失败（完整性） |
| 钥匙封装 | X25519 ECDH + HKDF-SHA256，临时密钥用完即弃 | 不用为每人存长期密钥；盐 = `sha256(ephPub‖recipientPub)` 防跨人复用 |
| 组织承诺 | SHA256(可见 place 的 `(placeId, tag, intensity)` 有序列表 + 每组盐) | 不持钥的人也能验证"没变、没换" |
| 签名 | Ed25519 over canonical JSON | 来源不可抵赖；中间人改一组即双失败 |

**四条必须知道的边界**（移植时如实保留，详见 `docs/stages/stage6-map-album.md`）：

1. **屏蔽 = 不打包**。被取消勾选的 Day/Place/Photo 完全不在包里，接收方看到的是连续时间线；但文件大小仍会泄露"这次带的内容不多"。
2. **`tag`/`intensity` 是明文**。承诺必须能被不持钥的人验证，这是可验证性的代价。
3. **界面从不报总数**。`unpackForViewer` 不返回 `pkg`/组数，UI 因此无从显示"共 N 组"。
4. **传播边界是文件级，不是内容级**。授权用户解密后截图/重加密，协议防不住——它保证"信只能被收件人拆开"，不保证"拆开之后"。

### 8.5 融合打包与地图渲染

`services/share/prepare.ts` 是 TrailShare 规范与 TravelMemory 的唯一差异点：
`pack()` 保持规范原样（不知道坐标、不发请求），模糊旁路被压缩在这一个文件里——
每个可见地点单独 try/catch，失败只记 warning 并**不写坐标**，打包照常成功。

接收端解出的 `PlaceMeta` 带 `lat/lng/radius`（WGS84，模糊后）。MapPage 渲染规则：

| 条件 | 渲染 |
| --- | --- |
| `radius > 0` | 高德 `Circle` 半透明圆（填充 18% 不透明度），**不画 Marker** |
| `radius` 缺省 / 0 | `Marker` |
| 无坐标 | 不上地图 |

坐标在渲染前经 `wgs84ToGcj02`（与后端 `app/utils/coords.py` 同算法，单测锁死同一组天安门向量）转换——存储 WGS84、展示 GCJ-02，红线 [2] 在前端的落点。

### 8.6 语义感知扰动与可验证断言（阶段 7 科研轨，未实现）

**语义感知扰动**：朴素拉普拉斯会把"龙门石窟"扰动到"洛阳机场"，语义全毁。计划用**语义等价类**约束噪声：

```
效用损失 U(p, p') = 1 - sim(semantic(p), semantic(p'))
噪声接受域：{ p' | U(p, p') ≤ U_max }，U_max 默认 0.3
```

即优先在语义等价类内部采样（同类 POI、`ai_tags` 重叠度高），等价类为空时退回标准机制。

**度量**：同一 ε 下，语义感知机制与朴素机制的"语义保真率"及"Recall@5 保留率"对比曲线。

**要证明的命题**

> 存在一条我真实走过的路线，其所有途经点落在公开地理区域内、时间落在行程时间窗内、语义序列哈希等于公开摘要、平均评分 ≥ 阈值；**但不暴露任何具体坐标、地点名、时间或身份。**

**三级降级**

| 级别 | 方案 | 用途 |
| --- | --- | --- |
| **L0** | Merkle 承诺 + Ed25519 签名 + 本地哈希链 | 默认，保证任何时刻都可演示 |
| **L1** | gnark（Groth16）或 Halo2 电路，进程外调用 | 完整零知识证明 |
| L2 | proof 聚合 | 不作为目标 |

**降级矩阵**

| 环节失败 | 表现 | 分享是否可用 |
| --- | --- | --- |
| 语义抽取为空 | 隐藏语义条 | ✅ |
| 电路超时 / 失败 | 降级 L0 | ✅ "已承诺，未生成零知识证明" |
| 上链失败 | 仅本地 | ✅ "已可验证，尚未上链" |
| 验证服务不可用 | — | ✅ 离线可验 |

**验证端无需信任服务方**：只需 `proof + public_inputs + vk`，可在前端或独立验证页完成，不访问数据库、不要求服务在线。这是"可验证"的核心意义。

**链上策略**：只写 32 字节承诺哈希；默认本地哈希链（`prev_hash` 串联），需要公开可验证时用 OpenTimestamps（免费）或 L2 测试网。不自建节点、不碰主网。

---

## 九、群体语义轨迹推荐

### 9.1 思路

从"基于具体地点"升级为"基于语义模式"。放弃具体坐标/景点的协同过滤，转向**基于语义轨迹的序列模式挖掘**。

```
洛阳：      龙门石窟(人文·高) → 洛邑古城(人文·中) → 老君山(自然·极高) → 洛阳博物馆(人文·高)
苏州—上海：  拙政园(水乡·高) → 平江路(水乡·中) → 外滩(都市·高) → 陆家嘴(都市·高)

抽象为：heritage:high → heritage:mid → nature:very_high → heritage:high
        watertown:high → watertown:mid → metropolis:high → metropolis:high

发现的模式：高评分连续同类体验，然后切换到不同类别的高评分体验
```

**推荐链路**：输入"洛阳" → 解析为 `heritage` → 查询"heritage 之后最常出现且评分最高的语义标签" → 推荐 `nature` → 落到具体 POI 时**必须走 `search_poi` 经地图 API 校验**（防幻觉）。

### 9.2 模式挖掘与发布

```
本地抽取 semantic_sequence → 评分过滤（avg_score ≥ 4.0）
→ 序列截断（≤ 6 步，降低重识别风险）
→ 上传 {pattern_hash, support_delta=1, score_sum, score_n}（加噪）
→ 聚合更新 SemanticPattern
→ 发布门禁：support ≥ k（默认 5）才置 published = true
→ 推荐时只查 published 模式
```

**评分防刷**

- 评分只能来自本地已验证行程，有 ZKP 断言的优先加权
- `unique(user_id, round_id, pattern_version)` —— 同一用户对同一模式仅贡献一次
- `avg_score` 随时间衰减
- 前端展示 confidence，不展示精确 support（避免泄露参与人数）

### 9.3 关于术语的诚实表述

参与者规模有限（数十人量级），**不宣称联邦学习**。准确表述是：

> 各客户端在本地完成语义抽取与模式统计，仅上传经扰动的聚合统计量（不含坐标、地点或标识），服务端作为逻辑聚合器汇总为全局语义模式库。隐私收益来自**语义抽象 + 扰动 + k-匿名门禁**三重机制的叠加，而非参与方数量。

### 9.4 实验设计（留一法）

```
对 N 条行程（N ≥ 8）：
  for i in 1..N：用除 i 外的 N-1 条构建模式库，预测第 i 条的第 k+1 步
  记录 hit@3 / hit@5 / MRR
基线：随机推荐 / 流行度推荐 / 个人记忆最近邻
```

该实验不需要真实多用户，用若干条真实或构造的行程即可运行，是小样本设定下可行的评估方式。

### 9.5 群体先验与个人证据的隔离

| | 个人证据 | 群体先验 |
| --- | --- | --- |
| 来源 | 自己的 Photo / Trip / Memory | 匿名聚合的 SemanticPattern |
| 能否引用 | ✅ 必须引用，计入 citation_rate | ❌ 禁止引用，不计入 citation_rate |
| prompt 位置 | `[检索证据]`，带 `[1][2][3]` 编号 | `[群体语义先验]`，无编号 |
| 语气 | "你在 2024-10-03 去过龙门石窟 [1]" | "类似行程的人通常接着去自然风光类景点（基于匿名模式，置信度 0.7）—— 这是建议，不是你的记录" |
| self_check | 缺失引用 → 拒答 | 出现引用编号 → 判失败 |
| 写回 Memory | 正常写回 | 不写回（避免"别人喜欢"污染"我喜欢"） |

**为什么必须隔离**：引用率 100% 是核心硬指标。一旦群体统计被当作个人记忆引用，整套评测体系失效。

**实现**：`context_builder.py` 用两个不同数据结构（`EvidenceBlock` / `PriorBlock`），`self_check.py` 显式校验，`EvalCase.kind` 区分统计口径。

---

## 十、验收标准

### 10.1 工程指标（主流程，必须达标）

| 维度 | 指标 | 目标 |
| --- | --- | --- |
| 导入 | 1000 张照片：导入 + AI 打标 + 建索引 | < 10 分钟 |
| 地图 | 1 万点聚合渲染 | ≥ 60fps，拖动无明显卡顿 |
| 地图 | 首屏地图可交互时间 | < 1s |
| 检索 | Recall@5 / MRR | ≥ 0.8 / ≥ 0.6 |
| 生成 | 事实性回答引用率 | **100%**（缺失即判失败） |
| 生成 | 无证据场景拒答率 | 100% |
| 工具 | 工具选择准确率 | ≥ 0.9 |
| 系统 | P95 首字延迟 | < 3s |
| 系统 | 单次问答 LLM 成本 | < ¥0.05 |
| 缓存 | POI / 路线类工具缓存命中率 | ≥ 60% |
| 移动端 | Android 真机可完成导入→成图→问答闭环 | 通过 |

### 10.2 扩展指标（可选）

| 维度 | 指标 | 目标 | 降级值 |
| --- | --- | --- | --- |
| 语义抽象 | TripItem → 语义标签映射覆盖率 | ≥ 95% | 人工补标 |
| 隐私-效用 | ε = 1.0 下 Recall@5 相对下降 | ≤ 10% | ≤ 20% |
| 隐私-效用 | 分享路线可重识别率（模拟攻击） | ≤ 10% | 提高 k 到 10 再测 |
| 可验证性 | 范围证明：生成 / 验证 / proof 大小 | < 2s / < 50ms / < 5KB | 承诺-签名（< 10ms） |
| 群体推荐 | 留一法 hit@3（N ≥ 8 条行程） | ≥ 0.5 | 报告 hit@5 与基线对比 |
| 匿名性 | 对外发布模式的最小支持度 k | ≥ 5 | 不足则仅本机使用 |
| 成本 | 单次链上锚定成本 | ≈ 0 | 本地哈希链 |

> 扩展指标全部为加分项。**工程指标未达标时，扩展指标不计入成果。**

---

## 十一、开发顺序

阶段严格串行：**上一阶段的出口条件未满足，不进入下一阶段**。
每个阶段拆成若干**工作块**，块与块之间若依赖独立，可交给不同的人并行推进；单人开发则按块号顺序完成。

### 阶段 0 · 纯文本 RAG CLI

**目标**：不碰地图、不碰照片、不碰前端，先把 RAG 管道跑通。

| 块 | 内容 | 依赖 | 出口条件 |
| --- | --- | --- | --- |
| **W0-1** | 数据底座：PostGIS+pgvector 镜像、Alembic 建表与扩展、Document 表、embedding 写入与精确检索 | — | 两个扩展创建成功；写入并检索返回预期结果 |
| **W0-2** | RAG 管道：`rewrite → query_parse → retrieve → build_context → generate → self_check` | W0-1 | CLI 可带引用问答；无证据时拒答 |
| **W0-3** | 评测与可观测：`eval_cases.jsonl` 20～30 条、metrics、runner、JSONL trace、Run/Step 表 | W0-1 | `make eval` 输出 Recall@5；trace 可回溯 |

**推进方式**：单人按 W0-1 → W0-2 → W0-3。多人则**先由一人完成 W0-1 定好数据契约**，W0-2 与 W0-3 即可并行。
**本阶段入口**：`python -m app.cli`，不建 HTTP 接口。

---

### 阶段 1 · 离线批处理 + 照片元数据

**目标**：照片没有文本，必须先造出 caption / ai_tags，否则向量检索无效。

| 块 | 内容 | 依赖 | 出口条件 |
| --- | --- | --- | --- |
| **W1-1** | `jobs/` 框架：IngestJob 幂等、重试、成本上限、状态回写 | 阶段 0 | 同一任务重复执行不重复烧 token |
| **W1-2** | `caption_job`：生成 caption + ai_tags 并写回 Photo | W1-1 | 1000 张跑完，失败可重试，成本不超上限 |
| **W1-3** | `index_job`：按 Trip/天聚合 → chunk → embedding → 写 Document | W1-1 | `make reindex` 可全量重建 |

**推进方式**：单人顺序完成。多人则由一人先完成 W1-1 定义任务契约，W1-2 与 W1-3 并行。
**本阶段不碰**：地图、EXIF、Capacitor。

---

### 阶段 2 · 结构化 + 向量混合检索

**目标**：让"去年秋天""古镇"这类表达真正生效。

| 块 | 内容 | 依赖 | 出口条件 |
| --- | --- | --- | --- |
| **W2-1** | Query 结构化解析：时间 / 地点 / 主题抽取 → 过滤条件 | 阶段 1 | 中文时间表达用例通过；解析失败时退化为不过滤 |
| **W2-2** | 时间 / 空间 / 行程过滤 + pg_trgm 中文全文 | 阶段 1 | 混合检索 Recall@5 不低于纯向量 |
| **W2-3** | SSE 流式输出 + 前端对话页骨架 | 阶段 1 | 首字可见，回答可中断 |

**推进方式**：三块互不阻塞，可完全并行；单人建议 W2-1 → W2-2 → W2-3。
**本阶段暂不做** RRF 融合。

---

### 阶段 3 · 工具调用 + Reranker + 缓存

**目标**：让 Agent 能动手查地图，而不是只靠记忆。

| 块 | 内容 | 依赖 | 出口条件 |
| --- | --- | --- | --- |
| **W3-1** | `tools/` 框架：base、registry、Pydantic schema、超时、重试、日志 | 阶段 2 | 工具选择准确率 ≥ 0.9 |
| **W3-2** | `search_poi` / `calculate_route` + 高德接入 + 坐标转换 | W3-1 | 返回高德 POI ID；转换有缓存与分批 |
| **W3-3** | Redis 四级用途落地（API 缓存 / Embedding 缓存 / 会话态 / 幂等锁与预算计数） | 阶段 2 | 缓存命中率 ≥ 60%；预算计数原子生效 |
| **W3-4** | Reranker 接入 + HNSW 索引与"结果不足回退精确扫描" | W3-1 | 建索引后 Recall@5 不下降 |

**推进方式**：W3-1 是共同依赖，先完成；之后 W3-2 / W3-3 / W3-4 三块可并行。单人按块号顺序。

---

### 阶段 4 · Memory + LangGraph

**目标**：让 Agent 记住你，而不是每次从头认识你。

| 块 | 内容 | 依赖 | 出口条件 |
| --- | --- | --- | --- |
| **W4-1** | Memory 三层（短期 / 情景 / 语义）+ 衰减公式 + 写回策略 | 阶段 3 | 说出偏好后，二次问答结果发生变化 |
| **W4-2** | Pipeline 迁 LangGraph，`AGENT_RUNTIME` 可切换 | 阶段 3 | 两种运行时评测结果可对比 |
| **W4-3** | RRF 融合 + 上下文压缩 | W4-2 | 融合后 Recall@5 提升 |

**推进方式**：W4-1 与 W4-2 可并行（前者是数据与策略，后者是编排改造）；W4-3 需等 W4-2。单人按块号顺序。

---

### 阶段 5 · 评估与可观测

**目标**：能证明"改完之后是变好还是变坏"。

| 块 | 内容 | 依赖 | 出口条件 |
| --- | --- | --- | --- |
| **W5-1** | 评测集扩充：生成忠实度、引用率、拒答率、解析准确率 | 阶段 4 | 全部工程指标可一键跑出 |
| **W5-2** | `make eval-compare`：按 prompt_version / embedding_model 输出归因对比表 | W5-1 | 换 prompt 后能看到指标差异 |
| **W5-3** | 可观测看板（可选接 Langfuse） | W5-1 | Trace / Token / 延迟 / 成本可视化 |

**推进方式**：W5-1 先完成，W5-2 与 W5-3 可并行。单人按块号顺序。

---

### 阶段 6 · 地图相册 + 跨端

**目标**：把此前所有的能力接到真实照片与真实地图上，并落到移动端。

| 块 | 内容 | 依赖 | 出口条件 |
| --- | --- | --- | --- |
| **W6-1** | 导入链路：EXIF 解析、缩略图生成、去重、无位置照片兜底、时区双写 | 阶段 5 | 1000 张导入 + 打标 + 索引 < 10 分钟 |
| **W6-2** | 地图与行程：图层栈（BaseMap / Cluster / TripRoute / TripItem / Photo / Evidence / Privacy）、气泡聚合、规则聚类成 Trip / TripItem、路线可视化、行程编辑、动画 P0 | W6-1 | 1 万点渲染 ≥ 60fps |
| **W6-3** | Capacitor 跨端：原生能力抽象、沙盒缩略图、权限、真机验证 | W6-2（Web 版） | Android 真机跑通导入→成图→问答 |
| **W6-4** | 隐私分享：后端拉普拉斯地理模糊（`privacy/` + API）+ 前端定向加密信物（TrailShare 协议移植：Ed25519 / X25519 KEM / AES-GCM / 组织承诺） | W6-2 | 信物只有指定收件人解得开；模糊圈在地图正确渲染；开关全关时前五阶段指标不变 |
| **W6-5** | `tests/test_degradation.py`：断言扩展开关全关时主流程指标不变 | W6-4 | 开关全关，前五阶段指标不退化 |

**推进方式**：W6-1 → W6-2 是主干，必须先串行完成。之后 W6-3 / W6-4 可并行，W6-5 在两者之后收口。单人按块号顺序。

---

### 阶段 7 · 扩展方向（可选，独立分支）

**前提**：阶段 0～6 已交付且工程指标达标。**任一工作块未完成可整块放弃，不影响主线。**

| 块 | 内容 | 依赖 | 出口条件 |
| --- | --- | --- | --- |
| **W7-1** | 语义层与隐私机制：`semantic_tags.yaml`、语义抽取、`privacy/` 纯函数、语义感知扰动 | 阶段 6 | 隐私-效用曲线出图 |
| **W7-2** | 模式挖掘与推荐：语义序列落库、频繁子序列挖掘、k-匿名门禁、`semantic_recommend` 工具、先验隔离机制 | W7-1 | 留一法 hit@3 达标；Agent 能回答"去完洛阳该去哪" |
| **W7-3** | 可验证断言：先做 L0 承诺-签名（保证可演示），再动电路（先只做范围 + 时间约束），最后接验证页与徽章 | W7-1 | 独立验证页无需服务端即可验证通过 |

**推进方式**：W7-1 → W7-2 / W7-3。建议 W7-2 优先于 W7-3：前者难度可控且一定能出结果，后者风险最高。

**止损规则**：

- W7-1 未完成 → 放弃整个阶段 7，交付纯工程项目
- W7-2 未完成 → 只保留隐私部分的工作
- W7-3 只完成 L0 → 可验证部分以"承诺方案 + 电路设计"呈现，完整 zkSNARK 列为后续工作
- **任何情况下，工程指标不允许退化**

---

## 十二、运行与环境

### 12.1 单文件 Compose + profiles

```yaml
services:
  db:      { build: ./backend/docker/db }                  # 始终启用
  redis:   { image: redis:7-alpine,  profiles: [stage3] }   # 阶段 3+
  minio:   { image: minio/minio,     profiles: [stage6] }   # 阶段 6+
  nginx:   { image: nginx:alpine,    profiles: [prod]   }   # 部署
```

```bash
docker compose up -d                                            # 阶段 0~2
docker compose --profile stage3 up -d                           # 阶段 3~5
docker compose --profile stage3 --profile stage6 up -d          # 阶段 6+
```

### 12.2 环境变量

```dotenv
# 基础
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/travel_memory
EMBEDDING_MODEL=bge-m3
EMBEDDING_MODEL_VERSION=bge-m3@v1
EMBEDDING_DIM=1024
LLM_API_KEY=  LLM_BASE_URL=
AGENT_RUNTIME=pipeline                  # pipeline | graph
LLM_DAILY_BUDGET_CENTS=2000

# 阶段 3+
REDIS_URL=redis://localhost:6379/0
AMAP_API_KEY=  AMAP_JS_KEY=
RERANK_MODEL=bge-reranker-v2-m3

# 阶段 6+
MINIO_ENDPOINT=  MINIO_ACCESS_KEY=  MINIO_SECRET_KEY=

# 扩展（默认全部关闭）
PRIVACY_ENABLED=false   SEMANTIC_ENABLED=false
ZK_ENABLED=false        CHAIN_ENABLED=false

PRIVACY_VERSION=privacy-2026.1
PRIVACY_DEFAULT_EPSILON=1.0
PRIVACY_SEMANTIC_UMAX=0.3
PRIVACY_K_ANONYMITY=5

SEMANTIC_TAG_VERSION=sem-2026.1
SEMANTIC_MAX_SEQ_LEN=6
SEMANTIC_MIN_SCORE=4.0

ZK_SCHEME=none              # none | merkle_sig | groth16 | halo2
ZK_CIRCUIT_VERSION=route-v1
ZK_TIMEOUT_SEC=30
CHAIN_BACKEND=local         # local | ots | sepolia
```

### 12.3 常用命令

```bash
make up / down / migrate / dev / cli
make test                  # 全量测试
make test-degradation      # ★ 断言扩展开关全关时主流程不受影响
make eval / eval-compare   # 评测与版本归因对比
make caption-batch / index-batch   # 幂等批处理（--limit 控制单次成本）
make reindex               # 换 embedding 模型后必须执行
make resemantic            # 换语义标签字典后必须执行（阶段 7）
make exp-privacy / exp-recommend   # 扩展实验
```

### 12.4 语义标签字典

```yaml
version: "sem-2026.1"
tags:
  - {id: heritage,  zh: 人文历史, poi_types: [文物古迹, 宗教场所], hints: [古镇, 古城, 博物馆, 石窟]}
  - {id: nature,    zh: 自然风光, poi_types: [自然风光, 山川, 湖泊], hints: [山, 湖, 森林, 草原]}
  - {id: watertown, zh: 水乡古镇, hints: [水乡, 古镇, 古村, 园林]}
  - {id: metropolis,zh: 都市风光, poi_types: [商务楼宇, 购物中心], hints: [外滩, CBD, 商圈]}
  - {id: leisure,   zh: 休闲度假}
  - {id: food,      zh: 美食探索, poi_types: [餐饮服务]}
fallback: unknown
```

抽取顺序（成本从低到高，命中即停）：`Place.poi_type` 映射 → `ai_tags` 关键词 → LLM 兜底（幂等缓存）。

---

## 十三、风险与对策

### 13.1 工程

| 风险 | 对策 |
| --- | --- |
| 隐私（缩略图、GPS、人脸敏感） | 隐私政策、授权说明、可删除 / 导出 / 关闭上传、MinIO 预签名 URL、传输加密 |
| 坐标偏移 | 显式 `coord_system`；高德转换 API 有 40 点/次与配额限制，分批 + 缓存 |
| PostGIS + pgvector 镜像 | 自建 Dockerfile，第一时间验证两个扩展都能建 |
| iOS 取图丢失 EXIF GPS | 真机优先验证；备选 PHAsset 回退或社区插件 |
| URI 失效 | 只存 `platform + asset_id`，展示时重新取；缩略图存相对路径 |
| 无 GPS 照片占比高（可达 30–60%） | `location_source` + 时间邻近推断 + 手动标注 + "未定位"分组；且不得作为证明的秘密输入 |
| 时区错误导致按天分组错 | 双写 `taken_at` / `taken_at_local` + `tz_name`；进电路前归一化 UTC 整数秒 |
| 高德 Key 泄露 | JS Key 配安全密钥与域名白名单；Web 调用走后端代理 |
| 地图卡顿（万级点） | Canvas / WebGL 渲染 + Worker 聚类；拖动缩放不逐帧重聚；不用 DOM Marker |
| 首屏白屏 | 底图先渲染不等数据；照片点异步叠加；空状态直接给导入引导 |
| LLM 幻觉 | 工具校验、证据链、引用率硬指标、无证据拒答、ToolCallLog |
| 成本失控 | 日预算硬上限（Redis 原子计数）、批处理 `--limit`、缓存、幂等去重 |
| 换 embedding 模型产生脏向量 | `embedding_model` 字段 + 启动校验 + `make reindex` |
| HNSW + 过滤召回不足 | 阶段 0～2 精确扫描；阶段 3+ 建 HNSW 并实现回退 |
| RAG 效果差（照片无文本） | 必须先生成 caption / ai_tags；批处理阶段 1 就建 |
| Memory 膨胀 | 写回克制（8 轮一次）、衰减公式、过期归档 |
| 评测集不稳定 | 统一用 `source_type + source_id + chunk_index` |
| 评测无法归因 | prompt 版本化 + `EvalResult` 记版本 + `--compare` |
| LangGraph 过早引入 | 阶段 0～3 纯 Python 管道，阶段 4 再迁，可回滚 |

### 13.2 扩展方向

| 风险 | 对策 |
| --- | --- |
| 扩展挤占主线 | 独立分支推进；`test_degradation.py` 保护主线；明确的止损规则 |
| 电路做不出来 | 三级降级；先只做范围 + 时间约束；L0 保证永远可演示 |
| 群体先验污染引用率硬指标 | 先验与证据隔离 + self_check 校验 + `EvalCase.kind` 区分口径 |
| 电路规模爆炸 | 秘密输入点数 ≤ 32；序列 ≤ 6；评分用定点整数；哈希用 Poseidon / MiMC 而非 SHA256 |
| 语义标签体系漂移 | 版本化 yaml + `make resemantic`；变更必须重跑实验 |
| 语义序列重识别 | k-匿名门禁 + 长度截断 + 强度分档粗化 |
| 评分可被刷 | 唯一贡献约束 + 已验证加权 + 时间衰减 + 展示 confidence |
| 样本量小 | 不宣称联邦学习；留一法 + 三基线；结论适用边界写入说明 |
| 链上数据不可删除 | 链上只写 32 字节哈希（非个人信息）；隐私政策明示不可删除 |
| 验证依赖服务方 | 验证端只需 proof + vk，提供独立离线验证页 |
| 旁路引入循环依赖 | `blockchain_bridge` 仅允许被 tools / jobs 调用；CI 加 import-linter 检查 |

---

## 十四、演示说明

### 14.1 三分钟版

| 环节 | 动作 | 展示点 |
| --- | --- | --- |
| 1 | 导入 1000 张照片 → 自动成图、按天聚合成行程 | EXIF、时区、坐标转换、规则聚类、无位置兜底 |
| 2 | 缩放地图看气泡聚合 → 点开某天看照片网格 | 图层栈、聚合分裂、flyTo、缩略图懒加载 |
| 3 | 问"我去年秋天去过哪些古镇？" | 结构化解析 → 混合检索 → 带引用回答 |
| 4 | 点回答里的 `[1]` | **地图飞过去 + 脉冲高亮，Agent 与地图联动** |
| 5 | 说"我不太喜欢人多的地方" → 再问一次 | 偏好抽取 → 写回 → **二次回答结果变化，证明 Memory 生效** |
| 6 | 打开 trace 面板与评测报告 | Token、延迟、工具链、引用来源、Recall@5、引用率 100% |

### 14.2 扩展版（含隐私与推荐）

| 环节 | 动作 | 展示点 |
| --- | --- | --- |
| 1 | 三分钟版压缩 | 证明是真能用的 Agent |
| 2 | 分享页拖动 ε 滑杆 | ε 从 5.0 到 0.5，模糊圆半径平滑变大，语义标签基本不变（语义感知 vs 朴素） |
| 3 | 生成可验证分享 | proof → 锚定 → **打开独立验证页，不登录、不访问数据库即验证通过** |
| 4 | 问"去完洛阳该去哪？" | 命中 `heritage → nature` → 显示"基于匿名模式，置信度 0.72，非你的个人记录" |
| 5 | 打开实验报告 | 隐私-效用曲线、留一法 hit@3 对比、proof 耗时 |
| 6 | 收尾 | **"Agent 主线不依赖链；把链和 ZK 全部关掉，前面的功能一个不少。"** |

### 14.3 术语表

| 术语 | 含义 |
| --- | --- |
| 地理不可区分性 | 一种差分隐私机制，保证"从哪个真实位置出发"难以被区分，强度由 ε 控制 |
| 承诺（Commitment） | 对数据的哈希绑定，可事后验证数据未被篡改，但无法从哈希反推数据 |
| 零知识证明（ZKP） | 证明"我知道某个满足条件的秘密"而不泄露秘密本身 |
| 公开输入 / 秘密输入 | 电路里对外暴露的参数 / 只有证明者知道、绝不外泄的参数 |
| k-匿名 | 一条模式至少被 k 个来源支持才允许发布，防止通过稀有组合反推个人 |
| 验证层 | 区块链在本项目中的角色——只验证承诺的存在与时序，不存储业务数据 |
| 语义轨迹 | 把具体地点序列抽象成"人文 / 自然 / 水乡 / 都市"等类别序列 |

### 14.4 常见问题

| 问题 | 回答要点 |
| --- | --- |
| 区块链在这里有什么用？不用行不行？ | 单用户场景下不用也能跑。链解决的是**跨用户的信任问题**——别人凭什么相信你分享的路线是真的。Agent 主线不依赖链，可一键关闭。 |
| 这属于联邦学习吗？ | 不属于。参与者规模有限，不满足联邦学习的前提。做的是本地计算 + 可验证上传 + 逻辑聚合，隐私收益来自语义抽象与 k-匿名，而非参与方数量。 |
| 语义抽象不就是把地点换了个名字？ | 区别在于**泛化能力与可验证性**：一条洛阳行程的语义模式能迁移到西安；且抽象后的序列仍可用 ZKP 证明来源于真实行程，这是纯文本标签做不到的。 |
| 上链的数据能删吗？ | 链上只有 32 字节哈希，不含个人信息；哈希不可逆且不可删，这一点在隐私政策中明示。照片、坐标、语义全部可删。 |
| 用户量只有几十个，结论可信吗？ | 这是明确的适用边界。用留一法 + 三个基线做评估，结论适用于小规模熟人 / 实验室场景，规模化列为后续工作。 |
| 为什么不用现成的 RAG 框架？ | 项目目标是掌握 RAG 与 Agent 的工程实现；且引用率 100%、拒答策略、证据与先验隔离等要求需要深度定制。 |
