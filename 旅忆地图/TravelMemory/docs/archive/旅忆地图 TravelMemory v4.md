# 旅忆地图 TravelMemory v4（精简版）

> **一句话**：以个人照片地图记忆为数据源的旅行记忆 Agent。地图相册是数据底座与可视化，Agent 是核心；区块链与零知识证明是可降级的旁路验证层，不参与主流程。

| | |
| --- | --- |
| 版本 | v4（精简版，基于 v3 收敛范围） |
| 适用读者 | 项目组成员、导师 |
| 维护方式 | 本文档为唯一设计基线，代码与文档不一致时以本文档为准，修改需提 PR 并更新版本号 |

---

## 摘要（一页看懂）

**做什么**：导入手机相册照片，系统按时间+位置自动聚合成行程，在地图上还原你的旅行轨迹；在此之上构建一个 Agent，能用自然语言回答"我去年秋天去过哪些古镇""我在杭州最喜欢哪类地方""帮我规划一个类似的周末"，且**每一句事实性回答都必须能点开对应的照片证据**。

**技术主线**（学习目标，也是工程主体）：RAG 检索 → 结构化查询解析 → 工具调用 → 分层记忆 → 规划与反思 → 离线评测 → 全链路可观测。

**科研增量**（阶段 7，可选）：把上述能力延伸到"分享"场景——**在不泄露坐标的前提下证明自己确实走过某条路线**，并用匿名聚合的语义模式做小样本目的地推荐。区块链在其中只作验证层。

**交付形态**：Web 应用（主）+ Android / iOS 应用（Capacitor 复用同一套前端代码）。

**关键取舍**：砍掉了全部运维型苦力与低价值功能（见附录 A），保留了所有能在面试中展开讲的技术点（Redis 多级缓存、Capacitor 跨端、PostGIS 空间计算、pgvector 检索、ZKP 电路、Docker 编排）。

---

## 一、项目定位与目标

### 1.1 三层次目标

| 层次 | 目标 | 达标判据 |
| --- | --- | --- |
| **工程目标**（必须） | 交付一个完整、可演示、可实际使用的应用 | 第二章验收标准 A 全部达标 |
| **学习目标**（必须） | 系统掌握 Agent / RAG / Memory 的工程实现 | 每个能力点都有独立可测试模块与评测 |
| **科研目标**（可选） | 产出可被导师认可、可写成论文章节的增量 | 第二章验收标准 B 至少完成 RQ1 + RQ2 |

> 三者优先级严格递减：**科研做不完可以整块砍掉，工程不达标则项目不成立。**

### 1.2 核心设计约束（不可协商）

1. **照片不搬家**：原图永不上云，只提取 EXIF 元数据 + 本地生成缩略图。
2. **WGS84 存储，GCJ-02 展示**：所有位置字段显式标注 `coord_system`。
3. **无证据必须拒答**：事实性回答引用率 100%，缺失即判失败。
4. **依赖方向单向**：`agent → nodes → tools → services → models`，循环依赖视为缺陷。
5. **旁路可降级**：所有区块链 / ZKP / 隐私模块异步、可选，任一失败不得影响主流程。

### 1.3 非目标（明确不做）

- 不做原图云备份、不做社交/信息流/关注、不做多用户协作（**单用户多设备**模型）
- 不做内容审核、不做社区
- 不做代币经济、不做链上身份体系、**不部署公链主网**
- 不做"具体地点级"的协同过滤（这是本项目与主流推荐系统的显式差异点，也是立论前提）
- 不做中文分词扩展编译（zhparser / pg_jieba）—— 运维成本远大于收益
- 不做主题相册（Theme 多对多体系）—— 由 `ai_tags` 与语义标签承担

### 1.4 科研定位

论文工作标题：

> **Verifiable and Privacy-Preserving Travel Memory Sharing: A Semantic-Aware Geo-Indistinguishability Approach with On-Chain Attestation.**

| 研究问题 | 内容 | 难度 | 建议 |
| --- | --- | --- | --- |
| **RQ1** | 语义感知的位置隐私-效用权衡：用 POI 类型与语义标签约束噪声，使"抹掉坐标"不等于"抹掉语义" | 中 | 必做 |
| **RQ2** | 小样本下的语义轨迹模式挖掘与序列推荐：语义抽象 + 评分过滤 + k-匿名约束 | 中低 | 必做 |
| **RQ3** | 可验证的匿名语义断言：零知识证明"存在一条路线满足语义/时空约束"而不泄露坐标与身份 | 高 | 尽力，有降级 |
| RQ4 | 多 Agent 语义质量共识（PoCQ 变体） | 极高 | **仅写作未来工作，不实现** |

---

## 二、验收标准

### 2.1 标准 A：工程指标（阶段 6 结束必须达标）

| 维度 | 指标 | 目标 |
| --- | --- | --- |
| 导入 | 1000 张照片：导入 + AI 打标 + 建索引 | < 10 分钟 |
| 地图 | 1 万点聚合渲染 | ≥ 60fps，拖动无明显卡顿 |
| 检索 | Recall@5 / MRR | ≥ 0.8 / ≥ 0.6 |
| 生成 | 事实性回答引用率 | **100%**（缺失即判失败） |
| 生成 | 无证据场景拒答率 | 100% |
| 工具 | 工具选择准确率 | ≥ 0.9 |
| 系统 | P95 首字延迟 | < 3s |
| 系统 | 单次问答 LLM 成本 | < ¥0.05 |
| 缓存 | POI / 路线类工具缓存命中率 | ≥ 60% |
| 移动端 | Android 真机可完成导入→成图→问答闭环 | 通过 |

评测集 `backend/data/eval_cases.jsonl`，20～30 条，**第一天建立**，每阶段扩充。

### 2.2 标准 B：科研指标（阶段 7，可选轨）

| 维度 | 指标 | 目标 | 降级值 |
| --- | --- | --- | --- |
| 语义抽象 | TripItem → 语义标签映射覆盖率 | ≥ 95% | 人工补标 |
| 隐私-效用 | ε = 1.0 下 RAG Recall@5 相对下降 | ≤ 10% | ≤ 20% |
| 隐私-效用 | 分享路线可重识别率（模拟攻击） | ≤ 10% | 提高 k 到 10 再测 |
| 可验证性 | 范围证明：生成 / 验证 / proof 大小 | < 2s / < 50ms / < 5KB | 降级为承诺-签名（< 10ms） |
| 群体推荐 | 留一法 hit@3（N ≥ 8 条行程） | ≥ 0.5 | 报告 hit@5 与基线对比 |
| 匿名性 | 对外发布模式的最小支持度 k | ≥ 5 | 不足则仅本机使用 |
| 成本 | 单次链上锚定成本 | ≈ 0 | 本地哈希链 |

> 标准 B 全部为加分项。**标准 A 未达标时，标准 B 一律不计入成果。**

---

## 三、系统架构

### 3.1 分层

```
┌─ ① 客户端层 ────────────────────────────────────────────┐
│  React + TypeScript + Vite + Capacitor                  │
│  地图渲染 / 相册 / Agent 对话 / 精度标签 / 验证徽章        │
│  Web 与 Android / iOS 共用同一套代码，原生能力插件化        │
└─────────────────────────────────────────────────────────┘
                        ↓ HTTPS / SSE
┌─ ② 接口层 ──────────────────────────────────────────────┐
│  FastAPI Router + Pydantic v2                           │
│  路由 / 参数校验 / 统一响应 {code, message, data} / v1 前缀│
└─────────────────────────────────────────────────────────┘
                        ↓
┌─ ③ 业务服务层 ──────────────────────────────────────────┐
│  Photo / Trip / Map / Document / Memory                 │
│  Privacy / SemanticPattern        ← 科研                │
│  BlockchainBridge                 ← 科研，旁路           │
│  AgentService（仅接口适配）                               │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─ ④ 数据与智能层 ────────────────────────────────────────┐
│  PostgreSQL 16 + PostGIS 3.4 + pgvector                 │
│  Redis（多级缓存 + 幂等锁 + 预算计数）                     │
│  MinIO（缩略图） / LLM API / 高德 API / JSONL trace       │
└─────────────────────────────────────────────────────────┘

┌─ ⑤ 旁路验证层（永不阻塞 ①～④）──────────────────────────┐
│  ZK 电路执行器（进程外 + 超时熔断）                        │
│  链适配器（本地哈希链 / OpenTimestamps / L2 测试网）       │
│  所有调用：异步、失败降级为「未验证」                       │
└─────────────────────────────────────────────────────────┘
```

### 3.2 依赖方向（权威定义）

```
api/v1/agent.py → agent/service.py → agent/pipeline.py   （阶段 0～3，AGENT_RUNTIME=pipeline）
api/v1/agent.py → agent/service.py → agent/graph.py      （阶段 4+，  AGENT_RUNTIME=graph）

agent/pipeline.py → agent/nodes/pipeline/* → tools/registry → tools/* → services/*
agent/graph.py    → agent/nodes/graph/*    → tools/registry → tools/* → services/*

agent/rag/indexer.py    → services/document_service.py
agent/rag/retriever.py  → services/document_service.py
agent/memory/*          → services/memory_service.py
jobs/*                  → services/*  +  core/llm.py  +  core/cache.py

# 科研模块（仍在 tools 之下，保持单向）
tools/privacy_tool.py            → services/privacy_service.py
tools/semantic_recommend_tool.py → services/semantic_pattern_service.py
tools/zk_location_tool.py        → services/blockchain_bridge.py → zk/executor.py

约束：
  普通 services 不调用 tools
  tools 只被 agent/nodes 调用
  AgentService 只做接口适配，不直接调工具
  blockchain_bridge 只能被 tools/* 与 jobs/* 调用，禁止被 agent/nodes 直接调用
  旁路模块失败必须抛 DegradedException，由调用方降级，不得向上传播
```

### 3.3 前后端职责边界

| 功能 | 前端 | 后端 | 理由 |
| --- | --- | --- | --- |
| 照片选择 / EXIF 解析 / 缩略图生成 | ✅ | | 原图不上传 |
| 地图渲染 / 气泡聚合 | ✅ | | 高德 JS + Supercluster |
| 行程分组（规则） | ✅ 可先做 | ✅ 持久化 | |
| 精度标签渲染 / 验证徽章 | ✅ | | 由后端 `precision` 字段驱动 |
| 逆地理编码 | | ✅ | 隐藏 API Key |
| Agent 编排 | ✅ UI | ✅ 逻辑 | LLM 必须在后端 |
| 记忆检索 | | ✅ | pgvector + 结构化过滤 |
| caption / ai_tags / 语义标签生成 | | ✅ | 需要 VLM，且必须幂等可重试 |
| ZKP 证明生成 | ❌ | ✅ | 电路规模大 |
| ZKP 验证 | ✅ 可选 | ✅ | 验证很轻，前端验一次可演示"无需信任服务器" |

**原则**：渲染与隐私放前端，计算与智能放后端，证明放后端、验证可两端。

### 3.4 三条核心数据流

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
→ 工具调用（search_poi / calculate_route / [科研] semantic_recommend）
→ LLM 生成带引用回答 → write_memory 写回
→ observability.py 统一记录 trace → 返回前端，地图高亮证据
```

**C. 可验证分享（科研，旁路）**

```
用户选择行程 + share_mode + ε
→ PrivacyService.obfuscate_route() → 泛化几何 + precision 标签
→ [可选] zk 电路生成 proof（进程外，30s 超时）
→ [可选] 异步上链锚定（失败 = 未锚定，不影响分享）
→ 返回 {route_fuzzy, precision, assertion, verify_url}
```

---

## 四、技术选型

### 4.1 选型表

| 模块 | 选型 | 说明 |
| --- | --- | --- |
| 前端 | React + TypeScript + Vite + Tailwind + shadcn/ui | |
| 跨端 | **Capacitor 7** | 一套代码出 Web / Android / iOS |
| 地图 | 高德 JS API（Web）/ 高德 Web 服务 API（后端） | JS Key 配安全密钥 + 域名白名单 |
| 后端 | FastAPI + SQLAlchemy 2.0 + Pydantic v2 + Alembic | |
| 数据库 | PostgreSQL 16 + **PostGIS 3.4** + **pgvector** + **pg_trgm** | 需自建镜像 |
| 空间 ORM | GeoAlchemy2 | PostGIS 必需 |
| 缓存 | **Redis 7** | 见 4.2，四级用途 |
| 对象存储 | MinIO | 仅存缩略图；本地部署时可换本地目录 |
| Agent 编排 | 阶段 0～3 纯 Python 管道；阶段 4 起 LangGraph | `AGENT_RUNTIME` 切换，可回滚对比 |
| RAG | 自行实现，不引入黑盒框架 | |
| Embedding | Qwen text-embedding-v3（默认 1024 维）/ bge-m3 | 第一天选定 |
| Reranker | bge-reranker-v2-m3（本地，需 GPU）或 Qwen rerank（API） | 阶段 3 |
| LLM | DeepSeek / Qwen | 统一封装于 `core/llm.py` |
| 可观测 | JSONL + 数据库表（阶段 0 起）；Langfuse 自托管（阶段 5 可选） | |
| 部署 | Docker Compose（单文件 + profiles）+ Nginx | |
| 测试 | pytest + Playwright + RAG 评测 | |

### 4.2 Redis 的四个真实用途（不是挂名）

| 用途 | 数据结构 | TTL | 解决什么问题 |
| --- | --- | --- | --- |
| ① 外部 API 结果缓存 | String（JSON） | POI 7d / 路线 1d / 逆地编 30d | 高德 API 有配额，重复查询直接烧钱 |
| ② Embedding 结果缓存 | String，key = `sha256(content + model)` | 永久（LRU 淘汰） | 重跑索引 / 重导入时不重复付费 |
| ③ 短期会话记忆 + 流式状态 | List（滑动窗口）+ Hash | 24h | 会话上下文不必落库，重启不丢当前轮 |
| ④ 幂等锁 + 预算计数 | String（SETNX）+ INCR/EXPIRE | 任务级 / 每日 | IngestJob 不重复烧 token；LLM 日预算硬上限原子控制 |

> 面试可展开的点：缓存键设计与穿透防护（空值缓存）、TTL 分级策略、原子计数做限流、缓存与 DB 的一致性边界（**缓存只用于外部调用与会话态，不缓存用户数据本体，避免一致性问题**）。

### 4.3 Capacitor 跨端的实现方式

```
frontend/src/services/native/
  photoSource.ts    interface PhotoSource { pick(): Promise<Photo[]> }
                    ├─ WebPhotoSource     → <input type="file">
                    └─ NativePhotoSource  → Camera / MediaStore / PHPicker
  thumbnailStore.ts  缩略图持久化（Filesystem API，存相对路径）
  permissions.ts     相册读写 / 定位权限申请

export const photoSource = Capacitor.isNativePlatform()
  ? new NativePhotoSource() : new WebPhotoSource();
```

**必须真实处理（不是套壳就完事）**：

- iOS 取图可能丢失 EXIF GPS → 需要 PHAsset 回退方案，**真机第一天验证**
- 缩略图必须落沙盒持久目录（`Directory.Documents`），存相对路径（绝对路径在重装后失效）
- Web 端 `URL.createObjectURL` 与原生 `webPath` 生命周期不同，需统一抽象
- 权限拒绝、相册大规模导入（>5000 张）的分批与进度

> 面试可展开的点：一套 TypeScript 业务代码跨三端；原生能力通过接口抽象实现可替换；Web 与 Native 生命周期差异的处理。

### 4.4 数据库镜像（PostGIS + pgvector 必须同时具备）

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

### 4.5 关键决策理由

| 决策 | 理由 |
| --- | --- |
| FastAPI | AI 栈必须是 Python，同进程部署少一层调用 |
| pgvector 而非独立向量库 | 万级数据足够，少一个服务组件 |
| **不建 HNSW 索引（阶段 0～2）** | pgvector 的 HNSW 是"先图遍历、后过滤"，带 `WHERE user_id + 时间 + 空间` 时可能返回 0 条；阶段 0～2 用精确扫描保证正确性，阶段 3+ 建 HNSW 并实现"结果不足回退精确扫描" |
| Capacitor 而非 RN / Flutter | 已有前端代码可复用；但**原生能力与权限必须平台适配，不存在 100% 复用** |
| 阶段 0 不用 LangGraph | 先理解 RAG 与 Memory 本质，再引入状态机 |
| pg_trgm 一档到底，不编译 zhparser | 编译 PostgreSQL 扩展是纯运维成本，中文分词收益不足以抵消 |
| 单文件 Compose + profiles | 三套 compose 文件是维护负担；用 `COMPOSE_PROFILES` 区分阶段 |

---

## 五、数据模型

### 5.1 核心表（14 张）

```
User
  id, name, created_at
  -- 单用户部署；不做账号体系

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
  -- unique(amap_poi_id)；无 POI 时 unique(name, round(lat,4), round(lng,4))

Trip
  id, user_id, title, start_date, end_date, cover_photo_id
  share_mode, privacy_version                -- 科研
  semantic_sequence JSONB, semantic_version, avg_score   -- 科研

TripItem
  id, trip_id, place_id, order, arrive_at, leave_at
  semantic_tag, semantic_intensity, score    -- 科研

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
  embedding, embedding_model
  chain_commitment                            -- 科研，可空

MemorySource
  id, memory_id, source_type, source_id
  -- 用关联表而非数组，支持外键与级联；index(source_type, source_id) 反查

IngestJob
  id, photo_id, job_type, ai_model
  status, attempts, last_error, cost_cents, created_at
  -- unique(photo_id, job_type, ai_model) 保证幂等

AgentRun
  id, user_id, session_id, query, status
  latency_ms, token_usage, prompt_version
  privacy_assertion_id, semantic_version      -- 科研，可空

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
  latency_ms, prompt_version, semantic_version
```

### 5.2 科研扩展表（3 张，可整块删除）

```
PrivacyAssertion
  id, user_id, trip_id, share_mode, mechanism, epsilon
  public_inputs JSONB        -- region_bbox / time_window / semantic_digest / avg_score
  proof BYTEA, proof_scheme  -- none / merkle_sig / groth16 / halo2
  circuit_version, commitment
  prev_hash                  -- 本地哈希链，串联上一断言
  chain_tx_hash, status      -- pending / proved / anchored / failed
  expires_at, created_at

SemanticPattern
  id, pattern JSONB, pattern_version
  support, source_count, avg_score, score_sum, score_n
  confidence, published      -- support >= k 才 published
  last_updated
  -- unique(pattern_hash, pattern_version)

SemanticContribution
  id, user_id, round_id, pattern_version
  encrypted_update BYTEA, noise_scale, proof BYTEA
  aggregated, created_at
  -- unique(user_id, round_id, pattern_version)  防重复贡献
```

### 5.3 索引

| 表 | 索引 | 类型 | 阶段 |
| --- | --- | --- | --- |
| Photo | `(user_id, taken_at)` / `(user_id, location_source)` / `geom`(GIST) / `hash`(unique) | B-tree / GIST | 1 / 6 / 6 / 1 |
| Document | `(user_id, source_type)` / `(user_id, embedding_model)` / `metadata`(GIN) / `content`(GIN trgm) / `embedding`(**HNSW**) | B-tree / GIN / HNSW | 0 / 0 / 2 / 2 / **3+** |
| MemoryItem | `(user_id, type, last_seen_at)` / `embedding`(HNSW) | B-tree / HNSW | 4 |
| MemorySource | `(source_type, source_id)` | B-tree | 4 |
| IngestJob | `(status, created_at)` / `unique(photo_id, job_type, ai_model)` | B-tree / unique | 1 |
| Trip | `semantic_sequence`(GIN) / `(user_id, share_mode)` | GIN / B-tree | 科研 |
| PrivacyAssertion | `(user_id, trip_id, status)` / `commitment`(unique) | B-tree / unique | 科研 |
| SemanticPattern | `(pattern_version, published, avg_score)` | B-tree | 科研 |

### 5.4 设计原则

- 原图只存 `platform + asset_id`，展示时通过系统 API 重新取；缩略图必须落设备持久目录
- 删除级联：删 Photo → 删 MinIO 缩略图 → 删 Document 向量 → 删行程关联 → 删 MemorySource 引用 → 删 PrivacyAssertion
- Embedding 维度不写死，从配置读；**换模型必须 `make reindex` 全量重算**
- `Document.embedding` 与 `MemoryItem.embedding` 维度必须一致
- Document 是 RAG 的检索单元，Photo / Trip 是数据源；MemoryItem 是分层记忆的统一存储单元（**不再单设 Preference 表，避免两套衰减逻辑**）
- **原始坐标永不因分享而修改**：扰动只发生在读取出口
- **链上只写 32 字节哈希**，不含任何坐标、语义、身份

### 5.5 评测集稳定标识

自增 id 在重建后会变化，**统一使用 `source_type + source_id + chunk_index`**：

```jsonl
{"kind":"rag","question":"我去年秋天去过哪些古镇？","expected_evidence":[{"source_type":"trip","source_id":"t1","chunk_index":0}]}
{"kind":"refusal","question":"我 2019 年去过巴黎吗？","expected_answer":"拒答（无证据）"}
{"kind":"recommend","question":"去完洛阳该去哪？","expected_answer":"自然风光类","note":"群体先验，不计入 citation_rate"}
```

> `kind = recommend` 的样例**不参与引用率统计**，否则会把统计性建议误判为事实性幻觉。

---

## 六、代码仓库结构

```
travel-memory/
├── README.md                        快速上手（组员第一眼看这个）
├── PROJECT.md                       项目约定与分工
├── Makefile                         全部命令入口
├── docker-compose.yml               单文件 + profiles
├── .env.example
│
├── frontend/
│   ├── src/
│   │   ├── pages/{MapPage,AgentPage,SharePage}
│   │   ├── components/
│   │   │   ├── ui/                  shadcn 基础组件
│   │   │   └── business/            EvidenceCard / PrecisionBadge / VerificationBadge
│   │   ├── services/
│   │   │   ├── api/                 client / photo / trip / agent / share
│   │   │   └── native/              photoSource / thumbnailStore / permissions
│   │   ├── workers/thumbnail.worker.ts
│   │   ├── utils/{exif,coord,format}.ts
│   │   ├── store/ types/ main.tsx
│   ├── capacitor.config.ts
│   └── package.json
│
├── backend/
│   ├── docker/db/Dockerfile         PostGIS + pgvector 自建镜像
│   ├── app/
│   │   ├── main.py  cli.py
│   │   ├── api/v1/                  router / photos / trips / agent / share / recommend
│   │   ├── schemas/                 接口契约（与 models 严格分离）
│   │   ├── services/
│   │   │   ├── photo_service.py  trip_service.py  map_service.py
│   │   │   ├── document_service.py  memory_service.py
│   │   │   ├── privacy_service.py            ← 科研
│   │   │   ├── semantic_pattern_service.py   ← 科研
│   │   │   └── blockchain_bridge.py          ← 科研，旁路
│   │   ├── agent/
│   │   │   ├── service.py  pipeline.py  graph.py  state.py
│   │   │   ├── prompts/*.md         版本化 prompt（rewrite/query_parse/generate/self_check/memory_extract）
│   │   │   ├── nodes/pipeline/      rewrite_query / query_parse / retrieve /
│   │   │   │                        build_context / generate / self_check
│   │   │   ├── nodes/graph/         plan / call_tool / reflect / write_memory
│   │   │   │                        [科研] privacy_assert / semantic_pattern
│   │   │   ├── memory/              short_term / episodic / semantic / preference
│   │   │   ├── rag/                 indexer / retriever / query_parse /
│   │   │   │                        reranker / context_builder
│   │   │   ├── eval/                dataset / metrics / runner
│   │   │   └── observability.py     统一写 AgentRun / AgentStep / ToolCallLog
│   │   ├── jobs/                    base / caption_job / index_job /
│   │   │                            [科研] semantic_job / anchor_job
│   │   ├── tools/                   base / registry / cache /
│   │   │                            memory_tool / poi_tool / route_tool
│   │   │                            [科研] privacy_tool / zk_location_tool / semantic_recommend_tool
│   │   ├── privacy/                 laplace.py / gridsnap.py / metrics.py  ← 纯函数，无 IO
│   │   ├── zk/                      executor.py / verify.py / circuit/     ← 进程外
│   │   ├── models/                  SQLAlchemy 表
│   │   ├── db/                      session / init_db
│   │   ├── core/                    config / llm / cache(Redis) / deps /
│   │   │                            security / exceptions / feature_flags
│   │   ├── utils/                   coord / geo
│   │   └── data/semantic_tags.yaml  语义标签字典（版本化）
│   ├── alembic/
│   ├── tests/
│   │   ├── test_photos.py  test_trips.py  test_agent.py
│   │   ├── test_tools.py  test_memory.py  test_jobs.py
│   │   ├── test_coord.py  test_rag_eval.py
│   │   ├── [科研] test_privacy.py  test_semantic.py  test_zk.py
│   │   └── test_degradation.py      ★ 断言科研开关全关时主流程不受影响
│   └── data/{eval_cases.jsonl, traces/}
│
├── research/                        科研产物，不参与部署
│   ├── experiments/                 privacy_utility_curve.py / leave_one_out.py
│   └── results/
│
└── deploy/
    ├── nginx.conf
    └── docker-compose.prod.yml
```

### 6.1 职责边界（写死）

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| DocumentService | 文档 CRUD、向量写入、底层向量/结构化查询 | 查询改写、RRF、rerank |
| rag/retriever.py | 改写、解析、过滤编排、RRF、rerank、top-k 组装 | 底层向量写入 |
| rag/indexer.py | 调 DocumentService 写入 | 查询 |
| jobs/* | 幂等调度、重试、成本统计、状态回写 | 业务逻辑（委托 services） |
| core/llm.py | LLM 调用、重试、超时、token 与成本统计 | 业务逻辑、prompt 内容 |
| core/cache.py | Redis 读写、TTL 策略、幂等锁、预算计数 | 业务判断 |
| observability.py | 统一写 AgentRun / AgentStep / ToolCallLog | 业务逻辑 |
| nodes/* | 只调 `trace.step(...)` | 直接写可观测表 |
| privacy/* | 纯数学机制（拉普拉斯、网格吸附），无 IO，可单测 | 数据库、HTTP |
| zk/executor.py | 进程外执行电路、超时熔断、返回 proof | 数据库、业务判断 |
| blockchain_bridge.py | 承诺、锚定、验证；失败抛 degraded 而非 fatal | 主流程任何必需环节 |

### 6.2 三个版本化（任一变更都必须重算 + 重跑实验）

```
EMBEDDING_MODEL_VERSION   → make reindex
SEMANTIC_TAG_VERSION      → make resemantic
PRIVACY_VERSION           → 重跑 privacy_utility_curve
```

启动时校验库内主流值与配置一致，不一致直接报错退出，避免产生脏向量。

---

## 七、Agent 设计（核心技术）

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
→ 按 Trip / TripItem / 天 聚合为文档 → chunk（>800 token 滑窗，overlap 10%）
→ embedding（经 Redis 缓存去重）→ 写 Document
```

**照片本身没有文本，必须先生成 caption / ai_tags，否则向量检索无效。** 批处理管道在阶段 1 建立，不能拖到阶段 6。

### 7.3 Query 结构化解析（项目辨识度所在）

用户问"我去年秋天去过哪些古镇"，"去年秋天"必须转成时间范围，"古镇"转成主题/POI 类型。这是元数据过滤能否生效的关键，也是纯向量方案做不到的地方。

```json
{"time_range": {"start": "2025-09-01", "end": "2025-11-30"},
 "places": ["乌镇", "西塘"], "themes": ["古镇"]}
```

- 独立节点 `query_parse.py`，单独评测解析准确率
- 解析不确定时**退化为不过滤**（宁可多召回，不要漏召回）
- 时间解析需把"当前日期"注入 prompt（模型不知道今天是哪天）
- 中文时间表达（"前年""上个夏天""五一前后"）单独建测试用例
- **[科研] 增加意图分类**：`personal_memory` vs `group_recommendation`；分错会把群体先验当个人证据，是引用率指标的最大风险源

### 7.4 上下文融合与拒答

**固定模板顺序**

```
[系统指令]
[用户偏好 / 语义记忆]     ← query_user_memory
[当前行程上下文]          ← 短期记忆
[检索证据]                ← Document top-k，编号 [1][2][3]
[群体语义先验]            ← 科研：独立区块，明确标注"来自匿名统计，非你的记录"
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
| 短期 | 当前会话消息、当前行程上下文（Redis，滑动窗口） |
| 情景 | 某次旅行 / 某个 Trip 的事件摘要 |
| 语义 | 用户偏好、长期兴趣 |

**衰减公式（固定，不留空）**

```
weight(t) = w0 · exp(-λ · Δt_days)，λ = ln2 / half_life_days
默认 half_life_days = 30（偏好类 90）
weight < 0.05 或已过期 → 归档（不物理删除）
```

**写回策略（克制）**

- 每 8 轮生成一次会话摘要
- 用户明确表达偏好时抽取
- 行程结束后生成复盘并写回情景记忆
- 新偏好覆盖旧偏好时记 MemorySource 来源；置信度 < 0.6 时向用户确认
- **[科研] `chain_commitment` 只写哈希不写内容**；用户删除记忆后链上哈希仍在，必须在隐私政策中说明

### 7.6 工具

| 工具 | 依赖 | 说明 |
| --- | --- | --- |
| `query_user_memory` | MemoryService / DocumentService | 返回 `photo_ids` / `trip_ids` / `snippets` / `scores` |
| `search_poi` | MapService | 必须返回高德 POI ID，不能只返回自然语言 |
| `calculate_route` | MapService | 路径规划走高德；PostGIS 负责距离、附近、聚类 |
| `[科研] share_route` | PrivacyService | 泛化 + 精度标签 |
| `[科研] semantic_recommend` | SemanticPatternService | 只返回语义建议 |
| `[科研] zk_location` | BlockchainBridge | 进程外电路 |

**统一要求**：Pydantic schema 校验入参；超时、重试、缓存、限流；写 ToolCallLog（含 `cache_hit`）。
**旁路工具必须标记 `degradable=True`**：失败写 `status='degraded'` 日志后继续，不重试超过 1 次。

### 7.7 评估与可观测

- 评测集第一天建立，阶段 0 只测检索命中，阶段 5 增加生成忠实度
- 指标：Recall@5 / MRR / 解析准确率 / 引用率 / 拒答率 / 工具选择准确率 / 延迟 / Token / 成本
- **评测必须可归因**：`EvalResult` 记录 `prompt_version` + `embedding_model`，`make eval-compare` 输出对比表，否则改完不知道变好还是变坏
- 可观测：阶段 0 起 JSONL + 表记录；阶段 5 可选接 Langfuse 自托管
- **统一由 `observability.py` 写**，节点只调 `trace.step(...)`

---

## 八、隐私保护与可验证分享（科研 RQ1 / RQ3）

### 8.1 威胁模型（答辩必问"你防谁"）

| 对手 | 能看到 | 防御 |
| --- | --- | --- |
| A1 好奇的服务端 | 数据库全部明文 | 明示"服务端可见原始坐标"，隐私保证作用于**分享出口**而非服务端 |
| A2 分享接收者 | 泛化几何 + 语义序列 + proof | ε 扰动 + k-匿名 + 语义抽象 |
| A3 链上观察者 | 32 字节承诺 + 时间戳 | 链上不写坐标、语义、身份 |
| A4 恶意贡献者 | 全局模式库 | 证明行程真实性 + 每用户每模式仅一次贡献 + 信誉权重 |
| A5 重识别攻击者 | 分享的语义序列 | k-匿名门禁 + 序列长度截断（≤ 6 步） |

**明确不防御**：物理设备访问者、全球被动监听者（流量分析）、用户主动公开的照片。

> **必须写进论文**：语义序列本身有重识别风险——"龙门石窟 + 洛邑古城 + 老君山 + 洛阳博物馆"这个四元组几乎唯一确定一个人。因此模式库对外发布前必须满足 k-匿名。这是本项目与朴素"语义分享"方案的关键区别。

### 8.2 分享模式

| share_mode | 含义 | 前端渲染 |
| --- | --- | --- |
| `private` | 不分享 | — |
| `fuzzy` | 模糊路线 | 圆形 / 多边形区域 |
| `route_only` | 只给语义序列 | 城市中心点 + 语义时序条 |
| `verifiable` | 语义序列 + 可验证断言 | 语义条 + "已验证"徽章 + 验证链接 |

**落地顺序**：`fuzzy`（几何模糊）→ `fuzzy`（拉普拉斯）→ `route_only` → `verifiable`。前两步是纯几何计算，一天可完成且立即能演示。

### 8.3 隐私机制

`app/privacy/` 为纯函数模块，无 IO，便于单测与论文复现：

```python
class PrivacyService:
    def obfuscate_location(lat, lng, mode, epsilon=None) -> ObfuscatedPoint
    def obfuscate_route(trip_id, share_mode, epsilon=1.0) -> SharedRoute
    def semantic_generalize(trip_id) -> list[SemanticStep]
```

`mode` 取值：`none / geo_indistinguishability / grid_snap`。
默认 ε = 1.0（市区尺度约 1km 量级扰动），UI 可滑杆调节。

**返回值必带 `precision` 标签**，这是前端渲染与论文度量的共同契约：

```json
{"geometry": {...},
 "precision": {"level": "circle", "radius_m": 1200, "epsilon": 1.0,
               "mechanism": "geo_indistinguishability"},
 "semantic_sequence": ["人文历史(高)", "人文历史(中)", "自然风光(极高)"],
 "privacy_version": "privacy-2026.1"}
```

### 8.4 语义感知扰动（RQ1 的形式化）

朴素拉普拉斯会把"龙门石窟"扰动到"洛阳机场"，语义全毁。本项目用**语义等价类**约束噪声：

```
效用损失 U(p, p') = 1 - sim(semantic(p), semantic(p'))
噪声接受域：{ p' | U(p, p') ≤ U_max }，U_max 默认 0.3
```

即优先在**语义等价类内部**采样噪声点（同类 POI、`poi_type` 相同、`ai_tags` 重叠度高），等价类为空时退回标准机制。等价类由 `Place.poi_type` + `ai_tags` 运行时计算，无需物化表。

**度量**：同一 ε 下，语义感知机制 vs 朴素机制的"语义保真率"与"Recall@5 保留率"对比曲线。

### 8.5 可验证断言（RQ3）

**要证明的命题**：

> 存在一条我真实走过的路线，其所有途经点落在公开地理区域内、时间落在该行程时间窗内、语义序列哈希等于公开摘要、平均评分 ≥ 阈值；**但不暴露任何具体坐标、地点名、时间或身份。**

**电路 MVP（先做最小版本）**

```
公开输入：region_bbox / time_window / semantic_digest / avg_score_x100
秘密输入：points[]（仅 location_source ∈ {exif, user}）/ scores[]
约束：① 点在包围盒内 ② 时间在窗内 ③ semantic_digest 匹配 ④ 平均分 ≥ 阈值 ⑤ n ≤ 32
```

**先只实现约束 ①②**，跑通验证链路后再加 ③④。直接写完整电路是这条路线最常见的失败原因。

**三级降级（永远有东西可演示）**

| 级别 | 方案 | 工作量 | 用途 |
| --- | --- | --- | --- |
| **L0** | Merkle 承诺 + Ed25519 签名 + 本地哈希链 | 1 天 | **默认，保证可演示** |
| **L1** | gnark（Groth16）或 Halo2 电路，进程外调用 | 2～3 周 | RQ3 核心 |
| L2 | proof 聚合 | — | **不做** |

**降级矩阵**

| 环节失败 | 表现 | 分享是否可用 |
| --- | --- | --- |
| 语义抽取为空 | 隐藏语义条 | ✅（fuzzy） |
| 电路超时 / 失败 | 降级 L0 | ✅ "已承诺，未生成零知识证明" |
| 上链失败 | 仅本地 | ✅ "已可验证，尚未上链" |
| 验证服务不可用 | — | ✅ 离线可验 |

**验证端无需信任我们**：验证方只需 `proof + public_inputs + vk`，可在前端或独立验证页完成，不访问我们的数据库、不要求我们的服务在线。这是"可验证"的全部意义，也是演示中最有说服力的一幕。

**链上策略**：只写 32 字节承诺哈希；默认本地哈希链（`prev_hash` 串联），需要公开可验证时用 OpenTimestamps（免费）或 L2 测试网。**不自建节点、不跑 Geth、不碰主网。**

---

## 九、群体语义轨迹推荐（科研 RQ2）

### 9.1 思路

从"基于具体地点"升级为"基于语义模式"。放弃具体坐标/景点的协同过滤，转向**基于语义轨迹的序列模式挖掘**。

```
洛阳：      龙门石窟(人文·高) → 洛邑古城(人文·中) → 老君山(自然·极高) → 洛阳博物馆(人文·高)
苏州—上海：  拙政园(水乡·高) → 平江路(水乡·中) → 外滩(都市·高) → 陆家嘴(都市·高)

抽象为：heritage:high → heritage:mid → nature:very_high → heritage:high
        watertown:high → watertown:mid → metropolis:high → metropolis:high

发现的模式：高评分连续同类体验，然后切换到不同类别的高评分体验
```

**推荐链路**：用户输入"洛阳" → 解析为 `heritage` → 查询"heritage 之后最常出现且评分最高的语义标签" → 推荐 `nature` → 落到具体 POI 时**必须走 `search_poi` 经地图 API 校验**（防幻觉）。

### 9.2 模式挖掘与发布

```
本地抽取 semantic_sequence → 评分过滤（avg_score ≥ 4.0）
→ 序列截断（≤ 6 步，降低重识别风险）
→ 上传 {pattern_hash, support_delta=1, score_sum, score_n}（加噪）
→ 聚合更新 SemanticPattern
→ 发布门禁：support ≥ k（默认 5）才置 published = true
→ 推荐时只查 published 模式
```

**评分防刷（必须做，否则整条链路可刷）**

- 评分只能来自本地已验证行程，有 ZKP 断言的优先加权
- `unique(user_id, round_id, pattern_version)` —— 同一用户对同一模式仅贡献一次
- `avg_score` 随时间衰减，老模式权重下降
- 前端展示 confidence，不展示精确 support（避免泄露参与人数）

### 9.3 术语修正：不宣称联邦学习

参与者只有 5～30 人，**经典联邦学习（FedAvg + 安全聚合）既不收敛也不提供有意义的隐私保证**。正确表述：

> 本项目采用**隐私保护的分布式语义聚合**：各客户端在本地完成语义抽取与模式统计，仅上传经扰动的聚合统计量（不含坐标、地点或标识），服务端作为逻辑聚合器汇总为全局语义模式库。由于参与者规模有限，本工作**不宣称**联邦学习的收敛性或去中心化保证；隐私收益来自**语义抽象 + 扰动 + k-匿名门禁**三重机制的叠加。

这样既诚实，又把"小样本"从劣势转为研究问题本身。

### 9.4 实验设计（留一法，小样本也能出图）

```
对 N 条行程（N ≥ 8）：
  for i in 1..N：用除 i 外的 N-1 条构建模式库，预测第 i 条的第 k+1 步
  记录 hit@3 / hit@5 / MRR
基线：随机推荐 / 流行度推荐 / 个人记忆最近邻
输出：research/results/ 下的对比表与图
```

该实验不需要真实多用户，用本人与同学的公开行程或手工构造的 8～20 条行程即可运行，是小样本设定下最诚实也最可行的评估方式。

### 9.5 群体先验与个人证据的隔离（硬约束）

| | 个人证据 | 群体先验 |
| --- | --- | --- |
| 来源 | 自己的 Photo / Trip / Memory | 匿名聚合的 SemanticPattern |
| 能否引用 | ✅ 必须引用，计入 citation_rate | ❌ **禁止引用**，不计入 citation_rate |
| prompt 位置 | `[检索证据]`，带 `[1][2][3]` 编号 | `[群体语义先验]`，无编号 |
| 语气 | "你在 2024-10-03 去过龙门石窟 [1]" | "类似行程的人通常接着去自然风光类景点（基于匿名模式，置信度 0.7）—— 这是建议，不是你的记录" |
| self_check | 缺失引用 → 拒答 | 出现引用编号 → 判失败 |
| 写回 Memory | 正常写回 | **不写回**（避免"别人喜欢"污染"我喜欢"） |

**为什么必须隔离**：引用率 100% 是本项目核心硬指标。一旦群体统计被当作个人记忆引用，整套评测体系失效，且这种错误在答辩时极难解释。

**实现**：`context_builder.py` 中用两个不同数据结构（`EvidenceBlock` / `PriorBlock`），`self_check.py` 显式校验，`EvalCase.kind` 区分统计口径。

---

## 十、开发路线与分工

### 10.1 阶段总览

| 阶段 | 周次 | 目标 | 科研 |
| --- | --- | --- | --- |
| 0 | W1 | 纯文本 RAG CLI | — |
| 1 | W2 | 离线批处理 + 照片元数据接入 | — |
| 2 | W3 | 结构化 + 向量混合检索 | — |
| 3 | W4 | 工具调用 + Reranker + Redis | — |
| 4 | W5–W6 | Memory + LangGraph | — |
| 5 | W7 | 评估与可观测 | — |
| 6 | W8–W10 | 地图相册 + Capacitor | `share_mode=fuzzy` |
| **7（可选）** | W11–W14 | **科研轨，独立分支 `feat/research`** | RQ1 → RQ2 → RQ3 |

> **阶段 7 独立成轨的原因**：把 ZKP 挤进阶段 5、共识机制挤进阶段 6，会让 10 周主线直接崩盘。独立分支后，科研做不完可整块砍掉，工程交付不受影响。

### 10.2 各阶段要点

**阶段 0（W1）纯文本 RAG CLI**
- 旅行笔记 → chunk → embedding → pgvector → 带引用问答
- 管道：rewrite → query_parse → retrieve → build_context → generate → self_check
- 第一天：选定 embedding 模型；验证 PostGIS + pgvector 双扩展；建评测集 20～30 条
- 入口 `python -m app.cli`，本阶段不建 HTTP 接口

**阶段 1（W2）离线批处理 + 照片元数据**
- `jobs/` 框架：IngestJob 幂等、重试、成本上限
- `caption_job` 生成 caption + ai_tags；`index_job` 建 Document 与向量
- 不碰地图、不碰 EXIF、不碰 Capacitor

**阶段 2（W3）混合检索**
- Query 结构化解析（时间 / 地点 / 主题）；时间、空间、行程过滤
- pg_trgm 中文全文；SSE 流式输出；暂不做 RRF

**阶段 3（W4）工具 + Reranker + Redis**
- `search_poi` / `calculate_route`：Pydantic schema、超时、重试、Redis 缓存
- bge-reranker-v2-m3（无 GPU 时用 Qwen rerank API）
- 建 HNSW 索引，实现"ANN 结果不足回退精确扫描"
- Redis 四级用途全部落地

**阶段 4（W5–W6）Memory + LangGraph**
- 会话摘要、偏好抽取、情景记忆、写回与衰减
- Pipeline 迁 LangGraph（`AGENT_RUNTIME` 可切换对比）；加入 RRF 融合

**阶段 5（W7）评估与可观测**
- 评测集扩充：生成忠实度、引用率、解析准确率
- `make eval-compare` 归因对比表；可选接 Langfuse

**阶段 6（W8–W10）地图相册 + Capacitor**
- 导入、EXIF、缩略图、气泡、行程分组、路线；无位置照片兜底
- **真机第一天验证 iOS EXIF GPS 是否可读**
- 缩略图落沙盒持久目录、权限申请、大规模导入分批
- 隐私分享 `fuzzy` 模式（约 2 天，不含 ZKP）
- **建立 `tests/test_degradation.py`：断言科研开关全关时主流程指标不变**

**阶段 7（W11–W14）科研轨**
- **7.1 语义层与隐私机制（~1 周）**：`semantic_tags.yaml` + 抽取 + 拉普拉斯/网格吸附 + 语义感知扰动 + 隐私-效用曲线
- **7.2 模式挖掘与推荐（~1 周）**：语义序列落库 + 频繁子序列挖掘 + k-匿名门禁 + `semantic_recommend` 工具 + 9.5 隔离机制 + 留一法实验
- **7.3 可验证断言（~1.5 周）**：先做 L0（1 天，保证可演示）→ 再动电路，先只做范围 + 时间约束 → 验证页 + 徽章
- 7.4 多 Agent 共识：**仅写作未来工作，不实现**

### 10.3 止损线（写进 README）

- 7.1 未完成 → 砍掉整个阶段 7，交付纯工程项目（仍是完整 Agent 应用）
- 7.2 未完成 → 论文只写 RQ1
- 7.3 只完成 L0 → 论文写 RQ1 + RQ2，可验证部分写"承诺方案与电路设计，完整 zkSNARK 实现列为未来工作"
- **任何情况下，标准 A 不允许退化**

### 10.4 建议分工（3 人）

| 角色 | 负责 | 阶段 |
| --- | --- | --- |
| A（Agent / RAG） | pipeline、rag、eval、observability、prompt | 0–5，全程 |
| B（后端 / 数据） | models、services、jobs、Redis、PostGIS、Docker | 1–6，全程 |
| C（前端 / 跨端） | React、地图、Capacitor、原生抽象 | 2–6 |
| 科研部分 | A 主导（语义与 prompt），B 支撑（隐私服务、链桥） | 7 |

单人开发则严格按阶段串行，**不允许并行推进两个阶段**。

---

## 十一、环境与运行

### 11.1 单文件 Compose + profiles

```yaml
# docker-compose.yml
services:
  db:                      # 始终启用
    build: ./backend/docker/db
    environment: { POSTGRES_USER: user, POSTGRES_PASSWORD: pass, POSTGRES_DB: travel_memory }
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]

  redis:                   # 阶段 3+
    image: redis:7-alpine
    profiles: ["stage3"]
    ports: ["6379:6379"]

  minio:                   # 阶段 6+
    image: minio/minio
    profiles: ["stage6"]
    command: server /data --console-address ":9001"
    ports: ["9000:9000", "9001:9001"]

  nginx:                   # 部署
    image: nginx:alpine
    profiles: ["prod"]
    ports: ["80:80"]

volumes: { pgdata: }
```

启动：`docker compose --profile stage3 up -d`

### 11.2 环境变量

```dotenv
# 基础（阶段 0）
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/travel_memory
EMBEDDING_MODEL=bge-m3
EMBEDDING_MODEL_VERSION=bge-m3@v1
EMBEDDING_DIM=1024
LLM_API_KEY=
LLM_BASE_URL=
AGENT_RUNTIME=pipeline
LLM_DAILY_BUDGET_CENTS=2000

# 阶段 3+
REDIS_URL=redis://localhost:6379/0
AMAP_API_KEY=
AMAP_JS_KEY=
RERANK_MODEL=bge-reranker-v2-m3

# 阶段 6+
MINIO_ENDPOINT=  MINIO_ACCESS_KEY=  MINIO_SECRET_KEY=

# 科研（全部默认关闭）
PRIVACY_ENABLED=false
SEMANTIC_ENABLED=false
ZK_ENABLED=false
CHAIN_ENABLED=false

PRIVACY_VERSION=privacy-2026.1
PRIVACY_DEFAULT_EPSILON=1.0
PRIVACY_SEMANTIC_UMAX=0.3
PRIVACY_K_ANONYMITY=5

SEMANTIC_TAG_VERSION=sem-2026.1
SEMANTIC_MAX_SEQ_LEN=6
SEMANTIC_MIN_SCORE=4.0

ZK_SCHEME=none              # none / merkle_sig / groth16 / halo2
ZK_CIRCUIT_VERSION=route-v1
ZK_TIMEOUT_SEC=30
CHAIN_BACKEND=local         # local / ots / sepolia
```

### 11.3 Makefile

```makefile
install dev up down db-shell migrate test lint
  eval            # 跑评测
  eval-compare    # 按 prompt_version / embedding_model 输出对比表
  cli             # 阶段 0 主入口
  caption-batch index-batch reindex
  [科研] semantic-batch resemantic anchor-batch exp-privacy exp-recommend
  test-degradation  # ★ 断言旁路全关时主流程不受影响
```

### 11.4 语义标签字典（版本化）

```yaml
# backend/app/data/semantic_tags.yaml
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
**换字典必须 `make resemantic` 全量重算并重跑全部实验。**

---

## 十二、风险与对策

### 12.1 工程风险

| 风险 | 对策 |
| --- | --- |
| 隐私（缩略图、GPS、人脸敏感） | 隐私政策、授权说明、可删除 / 导出 / 关闭上传、MinIO 预签名 URL、传输加密 |
| 坐标偏移 | 显式 `coord_system`；高德转换 API 有 40 点/次与配额限制，分批 + 缓存 |
| PostGIS + pgvector 镜像 | 自建 Dockerfile，第一天验证两个扩展都能建 |
| **iOS EXIF 丢失** | **真机第一天验证**；备选 PHAsset 回退或社区插件；否则"EXIF 解析放前端"原则需调整 |
| URI 失效 | 只存 `platform + asset_id`，展示时重新取；缩略图存相对路径 |
| 无 GPS 照片占比高（可达 30–60%） | `location_source` + 时间邻近推断 + 手动标注 + "未定位"分组；**且不得作为 ZKP 秘密输入** |
| 时区错误导致按天分组错 | 双写 `taken_at` / `taken_at_local` + `tz_name`；进电路前归一化 UTC 整数秒 |
| 高德 Key 泄露 | JS Key 配安全密钥与域名白名单；Web 调用走后端代理 |
| LLM 幻觉 | 工具校验、证据链、引用率硬指标、无证据拒答、ToolCallLog |
| 成本失控 | 日预算硬上限（Redis 原子计数）、批处理 `--limit`、缓存、幂等去重 |
| 换 embedding 模型产生脏向量 | `embedding_model` 字段 + 启动校验 + `make reindex` |
| HNSW + 过滤召回不足 | 阶段 0–2 精确扫描；阶段 3+ 建 HNSW 并实现回退 |
| RAG 效果差（照片无文本） | 必须先生成 caption / ai_tags；批处理阶段 1 就建 |
| Memory 膨胀 | 写回克制（8 轮一次）、衰减公式、过期归档 |
| 评测集不稳定 | 统一用 `source_type + source_id + chunk_index` |
| 评测无法归因 | prompt 版本化 + `EvalResult` 记版本 + `--compare` |
| LangGraph 过早引入 | 阶段 0–3 纯 Python 管道，阶段 4 再迁，可回滚 |

### 12.2 科研风险

| 风险 | 严重度 | 对策 |
| --- | --- | --- |
| **科研挤爆主线工期** | 🔴 | 剥离到阶段 7 独立分支；止损线；`test_degradation.py` 保护主线 |
| **ZKP 电路做不出来** | 🔴 | 三级降级 L0 → L1；先只做范围 + 时间约束；L0 保证永远可演示 |
| **群体先验污染引用率硬指标** | 🔴 | 9.5 隔离机制 + self_check 校验 + `EvalCase.kind` 区分口径 |
| 电路规模爆炸 | 🟠 | 秘密输入点数 ≤ 32；序列 ≤ 6；评分用定点整数；哈希用 Poseidon / MiMC 而非 SHA256 |
| 语义标签体系漂移 | 🟠 | 版本化 yaml + `make resemantic`；变更必须重跑实验 |
| 语义序列重识别 | 🟠 | k-匿名门禁 + 长度截断 + 强度分档粗化；写入威胁模型 |
| 评分可被刷 | 🟠 | 唯一贡献约束 + 已验证加权 + 时间衰减 + 展示 confidence |
| 样本量 5～30 人，实验可信度 | 🟠 | 不宣称联邦学习；留一法 + 三基线；明确写 limitation |
| 链上数据不可删除 vs 删除权 | 🟠 | 链上只写 32 字节哈希（非个人信息）；隐私政策明示不可删除 |
| 术语被质疑（"这也叫联邦学习？"） | 🟠 | 主动修正表述；答辩先讲"链是验证层不是存储层" |
| 验证依赖我们的服务器 | 🟡 | 验证端只需 proof + vk，提供独立离线验证页 |
| 旁路引入循环依赖 | 🟡 | `blockchain_bridge` 仅允许被 tools / jobs 调用；CI 加 import-linter 检查 |

---

## 十三、演示与答辩

### 13.1 剧本 A：3 分钟工程版（答辩 / 面试）

| 时间 | 动作 | 展示点 |
| --- | --- | --- |
| 0:00–0:40 | 导入 1000 张照片 → 自动成图、按天聚合成行程 | EXIF、时区、坐标转换、规则聚类、无位置兜底 |
| 0:40–1:20 | 问"我去年秋天去过哪些古镇？" | Query 结构化解析 → 混合检索 → 带引用回答 → **地图高亮证据照片** |
| 1:20–1:50 | 追问"帮我规划一个类似的周末路线" | Memory 命中偏好 → Planning → search_poi / calculate_route → 工具 trace |
| 1:50–2:20 | 说"我不太喜欢人多的地方" → 再问一次 | 偏好抽取 → 写回 → **二次回答结果变化，证明 Memory 生效** |
| 2:20–2:50 | 打开 trace 面板 | Token、延迟、工具链、引用来源、缓存命中 |
| 2:50–3:00 | 打开评测报告 | Recall@5、引用率 100%、拒答样例 |

**可量化点**：Recall@5 ≥ 0.8、引用率 100%、1000 张导入 < 10 分钟、单次问答 < ¥0.05、缓存命中率 ≥ 60%。

### 13.2 剧本 B：5 分钟科研版（导师 / 开题）

| 时间 | 动作 | 展示点 |
| --- | --- | --- |
| 0:00–1:30 | 剧本 A 压缩版：一次问答 + 地图高亮 | 证明是真能用的 Agent，不是玩具 |
| 1:30–2:10 | 分享页拖动 ε 滑杆 | ε 从 5.0 到 0.5，圆半径变大，语义标签基本不变（语义感知 vs 朴素，RQ1） |
| 2:10–2:50 | 生成可验证分享 → "已验证"徽章 | proof → 锚定 → **打开独立验证页，不登录、不访问我们数据库即验证通过**（RQ3） |
| 2:50–3:40 | 问"去完洛阳该去哪？" | 命中 `heritage → nature` → UI 显示"基于匿名模式，置信度 0.72，非你的个人记录"（RQ2 + 9.5） |
| 3:40–4:20 | 打开实验报告 | 隐私-效用曲线、留一法 hit@3 对比、proof 耗时表 |
| 4:20–5:00 | 收尾 | **"Agent 主线完全不依赖链；把链和 ZK 全部关掉，前 90 秒的功能一个不少。"** |

### 13.3 术语表（给非区块链背景的读者）

| 术语 | 本文中的含义 |
| --- | --- |
| 地理不可区分性 | 一种差分隐私机制，保证"从哪个真实位置出发"难以被区分，强度由 ε 控制 |
| 承诺（Commitment） | 对数据的哈希绑定，可事后验证数据未被篡改，但无法从哈希反推数据 |
| 零知识证明（ZKP） | 证明"我知道某个满足条件的秘密"而不泄露秘密本身 |
| 公开输入 / 秘密输入 | 电路里对外暴露的参数 / 只有证明者知道、绝不外泄的参数 |
| k-匿名 | 一条模式至少被 k 个来源支持才允许发布，防止通过稀有组合反推个人 |
| 验证层 | 区块链在本项目中的角色——只验证承诺的存在与时序，不存储任何业务数据 |
| 语义轨迹 | 把具体地点序列抽象成"人文 / 自然 / 水乡 / 都市"等类别序列 |

### 13.4 预判问答

| 可能的问题 | 回答要点 |
| --- | --- |
| "区块链在这里到底有什么用？不用行不行？" | 诚实回答：单用户场景下不用也能跑。链解决的是**跨用户的信任问题**——别人凭什么相信你分享的路线是真的。Agent 主线不依赖链，可一键关闭。 |
| "这也叫联邦学习？" | 不叫。参与者 5～30 人，不满足联邦学习的前提。我们做的是本地计算 + 可验证上传 + 逻辑聚合，隐私收益来自语义抽象与 k-匿名，不来自参与方数量。 |
| "语义抽象不就等于把地点换了个名字？" | 区别在于**泛化能力与可验证性**：语义抽象让 1 条洛阳行程能迁移到西安；且抽象后的序列仍可用 ZKP 证明其来源于真实行程，这是纯文本标签做不到的。 |
| "上链的数据能删吗？" | 链上只有 32 字节哈希，不含个人信息；哈希不可逆且不可删，这一点在隐私政策中明示。用户的照片、坐标、语义全部可删。 |
| "用户只有几十个，结论可信吗？" | 这是本文明确的 limitation。我们用留一法 + 三个基线做评估，并声明结论适用于小规模熟人/实验室场景，规模化是后续工作。 |
| "为什么不用现成的 RAG 框架？" | 项目目标是掌握 RAG 与 Agent 的工程实现，黑盒框架达不到学习目标；且引用率 100%、拒答策略、上下文隔离等要求需要深度定制。 |

---

## 附录 A：v3 → v4 精简清单

### A.1 砍掉的部分（及理由）

| 砍掉项 | 理由 |
| --- | --- |
| zhparser / pg_jieba 中文分词扩展 | 编译 PostgreSQL 扩展是纯运维成本，pg_trgm 已满足需求，收益不成正比 |
| Theme / PhotoTheme / 主题相册 | 多对多体系 + 独立 UI，功能价值可由 `ai_tags` 与语义标签完全承担 |
| 程序性记忆（procedural） | 本就列在 P2 未实现，删掉减少概念负担 |
| PoCQ / 多 Agent 共识（RQ4） | 工程复杂度最高、收益最低，仅保留"未来工作"一句，不写代码 |
| LDP 前端加噪、诱饵路径、速度平滑 | 三种机制中收益最低的变形，保留拉普拉斯 + 网格吸附已足够支撑 RQ1 |
| L2 proof 聚合 | 与目标无关 |
| `Device` / `AuthToken` 表 | 单用户部署，不需要设备级 token 体系 |
| `MemoryEvent` 表 | 变更历史对最终功能无可见价值，可解释性由 `MemorySource` 承担 |
| `SemanticEquivalenceClass` 表 | 运行时从 `Place.poi_type` 计算即可，无需物化 |
| `ChainAnchor` 表 | 合并为 `PrivacyAssertion.prev_hash`，少一张表 |
| `FederatedUpdate` → `SemanticContribution` | 更名以匹配"不宣称联邦学习"的表述 |
| 三套 docker-compose 文件 | 合并为单文件 + profiles，降低维护负担 |
| nDCG 指标 | 相关性排序收益低，保留 Recall@5 / MRR 即可 |

### A.2 明确保留的部分（应要求）

| 保留项 | 说明 |
| --- | --- |
| **Capacitor 跨端** | 项目最终交付形态为 Web + Android / iOS，是重要工程点 |
| **Redis** | 四级真实用途（API 缓存 / Embedding 缓存 / 会话态 / 幂等锁与预算计数），不是挂名 |
| **MinIO** | 对象存储 + 预签名 URL，构成完整的存储分层 |
| **Nginx** | 反向代理 + 静态资源，部署链路完整 |
| **Langfuse** | 阶段 5 可选，可观测性是简历加分项，但默认 JSONL |
| **ZKP / 区块链** | 科研轨核心，导师方向所在 |
| **隐私-效用权衡实验** | RQ1 的核心产出 |

### A.3 精简效果

| 维度 | v3 | v4 |
| --- | --- | --- |
| 数据表 | 24 张 | **17 张**（核心 14 + 科研 3） |
| 分享机制 | 4 种 + 3 种变形 | **2 种**（拉普拉斯 / 网格吸附） |
| Compose 文件 | 3 个 | **1 个 + profiles** |
| 研究问题 | RQ1–RQ4（含实现级 RQ4） | **RQ1–RQ3**，RQ4 仅写作未来工作 |
| Memory 分层 | 4 层（含未实现的程序性） | **3 层** |
| 文档篇幅 | 约 950 行 | **约 620 行** |

---

## 附录 B：可写入简历的技术点

| 技术点 | 一句话表述 |
| --- | --- |
| RAG | 自建 RAG 管线（查询改写 / 结构化解析 / 混合检索 / RRF 融合 / Rerank / 上下文构建），Recall@5 ≥ 0.8，引用率 100%，实现无证据拒答 |
| Agent | LangGraph 状态机编排，工具注册与 Pydantic schema 校验，自实现 self_check 保证回答忠实度 |
| Memory | 三层记忆（短期/情景/语义），指数衰减与冲突处理，来源可追溯设计 |
| 向量检索 | PostgreSQL + pgvector，理解 HNSW 与过滤的交互并设计回退策略 |
| 空间数据 | PostGIS 时空聚类、geography 距离计算、WGS84/GCJ-02 坐标系统一处理 |
| 缓存 | Redis 四级用途：外部 API 缓存、Embedding 去重缓存、会话态、幂等锁与预算原子计数 |
| 跨端 | 一套 React/TS 代码通过 Capacitor 构建 Web / Android / iOS，原生能力接口化抽象 |
| 工程化 | FastAPI 分层架构（严格单向依赖）、Alembic 迁移、批处理幂等与成本控制、Docker Compose 多服务编排 |
| 可观测与评测 | 全链路 trace（Run/Step/ToolCall）、离线评测集与版本化归因对比 |
| 隐私计算 | 差分隐私（平面拉普拉斯）实现与隐私-效用权衡实验 |
| 零知识证明 | 设计并实现旅行路线范围证明电路，含三级降级方案 |
| 区块链 | 以链为验证层：承诺锚定、轻量验证端、链上只写哈希的隐私边界设计 |
