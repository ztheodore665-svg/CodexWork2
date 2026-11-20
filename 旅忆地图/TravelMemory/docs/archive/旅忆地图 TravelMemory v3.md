# 旅忆地图 TravelMemory（v3 修订版）

> 一句话：以个人照片地图记忆为数据源的旅行记忆 Agent。地图相册是数据底座和可视化，Agent 是核心；区块链与零知识证明是可降级的旁路验证层，不参与主流程。

**第一目标**：学习 Agent / RAG / Memory 开发。
**第二目标**：做出可演示、可答辩、可写简历的完整应用。
**第三目标（v3 新增）**：在不破坏前两个目标的前提下，叠加一个能被导师认可、可写成论文章节的科研增量——**隐私可验证的语义轨迹共享与群体推荐**。

**核心设计**：照片不搬家，只借缩略图和元数据。Agent 基于真实照片/行程数据做问答、复盘、规划。

---

## 零、规模假设与验收标准

### 规模假设（所有选型的量级前提）

| 项 | 假设值 |
| --- | --- |
| 照片数 | 1 万～2 万张（个人 3～5 年旅行） |
| Document 数 | 约 3 万（按 Trip / TripItem / 天 聚合后 chunk） |
| MemoryItem 数 | 单用户 < 2000 |
| 并发 | 单用户，1～2 设备 |
| **可贡献群体模式的用户数（v3 新增）** | **5～30 人（同学/实验室规模）——这是全部科研设计的量级前提** |

**结论**：pgvector 单机完全够用，不需要专用向量库；超过 50 万 Document 再重新评估。
**推论**：阶段 0～2 数据量小，向量检索用**精确扫描**即可，不必上 ANN 索引（见 §5.4）。
**科研推论（重要）**：用户量只有 5～30 人，**任何"联邦学习 + 安全聚合"的经典设定都不成立**（FL 的收敛与隐私收益依赖数十到数百客户端）。因此 v3 把"联邦"重新定位为**本地计算 + 可验证上传 + 逻辑聚合器**，科研卖点放在**"小样本下的语义抽象 + 可验证性"**，而不是"联邦学习本身"（见 §9.10.4）。

### 验收标准 A：工程指标（阶段 6 结束必须达标，不可让步）

| 维度 | 指标 | 目标 |
| --- | --- | --- |
| 导入 | 1000 张照片 导入 + AI 打标 + 建索引 | < 10 分钟 |
| 地图 | 1 万点聚合渲染 | ≥ 60fps，拖动无明显卡顿 |
| 检索 | Recall@5 | ≥ 0.8 |
| 检索 | MRR | ≥ 0.6 |
| 生成 | 事实性回答引用率 | **100%**（缺失即判失败） |
| 生成 | 无证据场景拒答率 | 100%（不得编造行程） |
| 工具 | 工具选择准确率 | ≥ 0.9 |
| 系统 | P95 首字延迟 | < 3s |
| 系统 | 单次问答 LLM 成本 | < ¥0.05 |

> 评测集 `data/eval_cases.jsonl` 第一天就建，20～30 条，每阶段扩充。

### 验收标准 B：科研指标（阶段 7，可选轨，达标即论文素材）

| 维度 | 指标 | 目标 | 拿不到时的降级 |
| --- | --- | --- | --- |
| 语义抽象 | TripItem → 语义标签映射覆盖率 | ≥ 95% | 人工补标 + 标注工具 |
| 语义抽象 | 语义标签体系一致性（双人标注 Kappa） | ≥ 0.7 | 收敛标签字典到 6 类 |
| 隐私-效用 | ε=1.0 下 RAG Recall@5 相对下降 | ≤ 10% | 放宽到 ≤ 20%，写进论文限制 |
| 隐私-效用 | 分享后路线可重识别率（模拟攻击） | ≤ 10% | 提高 k 阈值到 10 再测 |
| 可验证性 | 单点范围证明：生成 / 验证 / proof size | < 2s / < 50ms / < 5KB | 降级为 Merkle 承诺 + 签名（< 10ms） |
| 可验证性 | 断言验证成功率 | ≥ 99% | — |
| 群体推荐 | 留一法 hit@3（N ≥ 8 条行程） | ≥ 0.5 | 报告 hit@5 与随机基线对比 |
| 匿名性 | 对外发布模式的最小支持度 k | ≥ 5 | 若样本不足则只在本地模式库内使用，不上链 |
| 成本 | 单次链上锚定成本 | ≈ 0（L2/测试网/可验证时间戳） | 改为本地哈希链 + 公开时间戳 |

> **这些指标全部是"加分项"不是"通行证"。工程指标 A 未达标，科研指标 B 一律不算数。**

---

## 一、项目定位

一个以个人照片地图记忆为数据源的 AI Agent 应用。

- **数据层**：照片导入 → EXIF/位置/时间 → 地图气泡 → 行程分组 → 路线可视化
- **智能层**：RAG 检索 + 分层 Memory + Tool Use + Planning + Reflection + Evaluation
- **展示层**：地图相册 + Agent 对话页
- **（v3 新增）验证层**：隐私保护分享 + 可验证位置断言 + 群体语义模式库 —— **旁路、异步、可降级**

### 1.1 科研定位（v3 新增，写进 PROJECT.md 与开题）

论文工作标题：

> **Verifiable and Privacy-Preserving Travel Memory Sharing: A Semantic-Aware Geo-Indistinguishability Approach with On-Chain Attestation.**

三个可拆分的研究问题（RQ），按难度递进，**做成任意一条都能写**：

| RQ | 内容 | 难度 | 对应阶段 |
| --- | --- | --- | --- |
| **RQ1** | 语义感知的位置隐私-效用权衡：用 `Place.poi_type` / `ai_tags` / Theme 约束噪声，让"抹掉坐标"不等于"抹掉语义" | 中 | 阶段 7.1 |
| **RQ2** | 小样本下的语义轨迹模式挖掘与序列推荐：语义抽象 + 评分过滤 + k-匿名约束 | 中低 | 阶段 7.2 |
| **RQ3** | 可验证的匿名语义断言：ZKP 电路证明"存在一条路线满足语义/时空约束"而不泄露坐标与身份 | 高 | 阶段 7.3 |
| （延伸）RQ4 | 去中心化多 Agent 语义质量共识（PoCQ 变体） | 极高 | 阶段 7.4，独立成文 |

**建议**：答辩/论文以 RQ1+RQ2 为基座（一定能做完），RQ3 作为核心亮点（做不完有降级路径），RQ4 只写"未来工作"。

### 非目标（明确不做）

- 不做原图云备份
- 不做社交/信息流/关注
- 不做多用户协作（**单用户多设备**模型，`User` / `Device` 表只为设备级 token 存在）
- 不做内容审核
- 不做完整社区
- **（v3 新增）不做代币经济、不做链上身份体系、不做公链主网部署**
- **（v3 新增）不做"具体地点级"的协同过滤** —— 这是与主流推荐系统的显式差异点，也是本项目的立论前提

### 学习目标（写进 PROJECT.md）

- RAG：索引、检索、重排、上下文构建、引用
- Memory：短期会话、情景记忆、语义偏好、程序性记忆
- Tool Use：工具 schema、调用、校验、错误处理、缓存
- Planning：任务拆解、多步执行、状态机
- Reflection：复盘、摘要、写回记忆
- Evaluation：检索指标、答案忠实度、工具选择准确率
- Observability：Trace、Token、延迟、成本、工具日志
- **（v3 新增）Privacy：差分隐私机制、隐私预算、隐私-效用权衡度量**
- **（v3 新增）Verifiability：承诺方案、零知识证明、链上锚定与验证**

---

## 二、核心功能与优先级

### P0（严格串行，做完一个再做下一个）—— 与 v2 完全一致，不许插入科研内容

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
- **（v3 新增）隐私保护式行程分享**：`share_mode` + 精度标签 + 前端按精度渲染
- 行程导出 / 分享卡片
- 流式输出（SSE）

### P2（有精力再说）

- AI 照片预打分
- 偏好迁移报告
- 自己 vs 自己的路线对比
- 程序性记忆（规划模板、工具使用经验）—— **v1 不实现**，仅在 Memory 分层中保留位置
- Capacitor 套壳

### P3（v3 新增：科研扩展轨，独立分支 `feat/research`，与主线解耦）

- 7.1 语义标签体系 + 语义行程序列抽取 + 隐私感知泛化
- 7.2 本地语义模式挖掘 + 评分过滤 + k-匿名发布 + 群体语义推荐工具
- 7.3 可验证位置断言（L0 承诺-揭示 → L1 zkSNARK）+ 链上锚定
- 7.4（可选）多 Agent 语义质量共识 PoCQ 变体

> **P3 的唯一约束**：全部走 feature flag，默认关闭；任一模块失败不得让 P0～P2 的任一指标退化。

---

## 三、架构设计

### 3.1 五层架构（v3 在四层之上加"旁路验证层"）

```
① 客户端层    React + TypeScript + Capacitor（后置）
              地图渲染 / 相册 / Agent UI / 精度标签渲染 / 可验证分享开关

② 接口层      FastAPI Router + Pydantic
              路由 / 参数校验 / 统一响应 / 版本管理

③ 业务服务层  PhotoService / TripService / MapService
              DocumentService / MemoryService
              PrivacyService / SemanticPatternService        ← v3 新增
              FederatedService / BlockchainBridge            ← v3 新增（旁路）
              AgentService（仅接口适配）
              LLMService（统一 LLM 入口）

④ 数据智能层  PostgreSQL + PostGIS + pgvector
              MinIO / LLM / 高德 API / JSONL trace

⑤ 旁路验证层  ← v3 新增，永不阻塞 ①～④
              zk circuit executor（进程外 / 队列）
              chain adapter（L2 / 测试网 / 可验证时间戳 / 本地哈希链）
              所有调用：异步 + 超时熔断 + 失败降级为"未验证"
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

# v3 新增（均在 tools 之下，仍保持单向）
tools/privacy_tool.py          -> services/privacy_service.py
tools/zk_location_tool.py      -> services/blockchain_bridge.py + zk executor（子进程/HTTP）
tools/federated_recommend_tool.py -> services/federated_service.py -> services/semantic_pattern_service.py

普通 services 不调用 tools
tools 只被 agent/nodes 调用
AgentService 只做接口适配，不直接调工具

# v3 新增硬约束
services/blockchain_bridge.py 只能被 tools/* 与 jobs/* 调用，禁止被 agent/nodes 直接调用
services/privacy_service.py   可被 MapService 与 tools 调用（它属于"位置表达"，不是"验证"）
所有旁路模块失败必须抛 BizDegradedException，由调用方降级，不得向上传播
```

**也就是说**：

- PhotoService 不知道 Agent 存在
- MapService 不知道 Agent 存在；但**知道 PrivacyService 存在**（分享路由必须按精度裁剪）
- DocumentService 管文档 CRUD、向量写入、底层向量/结构化查询
- MemoryService 管记忆读写和衰减
- AgentService 只负责把 HTTP 请求转给 pipeline/graph
- pipeline / graph 是 Agent 运行时入口，由 `AGENT_RUNTIME` 配置切换
- nodes 是各步骤节点
- tools 是适配器，调 services
- `nodes/graph/write_memory.py` 调 MemoryService 属于**业务写入**，不违反"节点不自己插表"（该约束仅针对 observability 表）
- **（v3）Agent 的 RAG 管线、Memory 衰减、工具主链路不依赖任何链上状态；链只提供"事后可验证"，不提供"运行时必需"**

### 3.3 前后端边界

| 功能 | 前端 | 后端 | 理由 |
| --- | --- | --- | --- |
| 照片选择 | ✅ | | 系统能力 |
| EXIF 解析 | ✅ | | 原图不上传，隐私（⚠️ iOS 见 §12） |
| 缩略图生成 | ✅ | | 省流量 |
| 地图渲染 | ✅ | | 高德 JS |
| 气泡聚合 | ✅ | | Supercluster |
| 行程分组（规则） | ✅ 可先做 | ✅ 持久化 | |
| 逆地理编码 | | ✅ | 藏 API Key |
| Agent 编排 | ✅ UI | ✅ 逻辑 | LLM 必须在后端 |
| 记忆检索 | | ✅ | pgvector + 结构化过滤 |
| caption / ai_tags 生成 | | ✅ | 需要 VLM，且要幂等重试 |
| **（v3）语义标签抽取** | | ✅ | 需要 LLM + 标签字典版本化 |
| **（v3）坐标扰动（LDP）** | ✅ 可选 | ✅ 默认 | 前端加噪可做到服务器不可见原始值，但会让服务端检索退化；默认放后端，前端加噪作为进阶开关 |
| **（v3）proof 生成** | ❌ | ✅ | 电路规模大，绝不放前端 |
| **（v3）proof 验证** | ✅ 可选 | ✅ | 验证很轻，前端验一次能演示"无需信任服务器" |
| **（v3）精度标签渲染** | ✅ | | 圆形/多边形/城市中心/隐藏，由后端给 `precision` 字段驱动 |

**原则**：渲染和隐私放前端，计算和智能放后端，**证明放后端、验证可两端**。

### 3.4 三条核心数据流（v3 在两条之上加"可验证分享流"）

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
→ 工具调用（search_poi / calculate_route / [v3] semantic_recommend）
→ LLM 生成带引用回答
→ write_memory 写回
→ 统一由 observability.py 记录 trace
→ 返回前端，高亮地图证据
```

**（v3 新增）可验证分享 + 群体推荐（旁路）**

```
用户点"生成可验证分享"
→ MapService.get_shared_route(trip_id, share_mode, epsilon)
   → PrivacyService.obfuscate()  → 泛化后的路线 + precision 标签
→ [可选] zk_location_tool.prove(secret=原始坐标序列, public=语义摘要+区域+时间窗)
   → 进程外电路执行 → proof + public_inputs
   → [可选] blockchain_bridge.anchor(commitment) → tx_hash（异步，失败=未锚定，不报错）
→ 返回 {route_fuzzy, precision, assertion, tx_hash, verify_url}

用户问"去完洛阳该去哪"
→ Agent plan → semantic_recommend 工具
   → SemanticPatternService.next_semantic(current_tags, user_pref)
   → 返回"人文历史 → 自然风光"这类**语义建议**（带 support / avg_score）
   → ★ 该结果只能进"群体先验"区，不得进"证据区"（见 §9.11）
```

### 3.5 Agent 工具链

```
tools/
  query_user_memory → MemoryService / DocumentService
  search_poi        → MapService（高德）
  calculate_route   → MapService（高德）
  get_weather       → 外部 API
  ---- v3 新增（全部 feature-flag 控制）----
  share_route        → PrivacyService + MapService          （P1）
  zk_location        → BlockchainBridge + zk executor        （P3/RQ3）
  semantic_recommend → SemanticPatternService / FederatedService（P3/RQ2）
```

**原则**：

- LLM 只做决策，地点必须经地图 API 校验，降低幻觉
- 每个工具入参用 Pydantic schema 校验
- 最终回答必须引用照片/行程 ID，方便前端高亮地图
- **（v3）隐私与验证类工具的输出必须自带"可信度标签"**（`verified` / `unverified` / `degraded`），UI 与 prompt 都要显式区分

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
> **（v3）隐私扰动与坐标转换必须分两步、顺序固定**：先转 GCJ-02 展示坐标，再在展示坐标系上做扰动，否则扰动半径会被坐标系偏移污染，论文里说不清。

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

> **（v3）科研相关**：`location_source != 'exif'` 的坐标**不得作为 ZKP 的秘密输入来源**（它本来就是猜的，证明它"确实到过"在语义上说不通）。电路只接受 `location_source in ('exif','user')` 的点。

### 4.3 时间与时区

EXIF `DateTimeOriginal` 通常不带时区，但"同一天""去年秋天"完全依赖本地时间。必须双写：

```
taken_at        timestamptz   按拍摄地本地时间解释后的 UTC 时刻（排序、时间差用）
taken_at_local  timestamp     拍摄地本地墙上时间（按天分组、展示用）
tz_name         text          IANA 时区，如 Asia/Shanghai
tz_source       text          exif_offset / device / user_override
```

EXIF 无时区信息时，用导入时设备时区填充 `tz_name`，并标 `tz_source='device'`。

> **（v3）时间窗是 ZKP 的公开输入之一**，必须先把 `taken_at` 归一化到 UTC 整数秒再进电路，否则跨时区行程的时间约束会证明失败。

### 4.4 自动聚类：先规则后算法

规则（阶段 6 先跑通）：

- 同一天（以 `taken_at_local` 为准）
- 相邻照片时间差 < N 小时（N 默认 4，用 `taken_at` 算）
- 空间距离 < M 公里（M 默认 1，用 `geography` 算米）
- 停留点合并（同一 Place 且停留 > 30 分钟合并为一个 TripItem）

跑通规则后，再考虑 DBSCAN + PostGIS 时空聚类。

### 4.5 隐私保护式位置分享（v3 新增，P1 主功能）

**核心主张**：分享的是**路线的语义与形状**，不是坐标。

#### 4.5.1 四种 share_mode

| share_mode | 含义 | 技术实现 | 前端渲染 |
| --- | --- | --- | --- |
| `private` | 不分享 | — | — |
| `fuzzy` | 模糊路线 | K-匿名 + 空间模糊（`ST_Buffer` / `ST_Envelope` / 网格吸附） | 圆形区域 / 多边形区域 |
| `route_only` | 只给语义序列 | 语义抽象后丢弃几何 | 城市中心点 + 语义时序条 |
| `verifiable` | 语义序列 + 可验证断言 | `route_only` + ZKP + 链上锚定 | 语义条 + "已验证"徽章 + 验证链接 |

**推荐落地顺序**：`fuzzy`（K-匿名/空间模糊）→ `fuzzy`（地理不可区分性）→ `route_only` → `verifiable`。
**理由**：前两个是纯后端几何计算，一天能做完且立刻可演示；后两个依赖语义体系与电路，属于科研轨。

#### 4.5.2 四种隐私机制

1. **地理不可区分性（Geo-Indistinguishability）**：平面拉普拉斯机制，隐私预算 ε（km⁻¹），有数学保证。
   默认 ε = 1.0（在市区尺度约等于 1km 量级扰动），用户可在 UI 上滑杆调节。
2. **K-匿名与空间模糊**：PostGIS `ST_Buffer` / `ST_Envelope`，用区域代替精确坐标；网格吸附到 0.01° / 0.05° / 0.1° 档位。
3. **轨迹泛化与路径混淆**：起终点模糊、插入诱饵路径、速度平滑。
4. **本地差分隐私（LDP）**：前端加噪，服务器看不到原始坐标（进阶开关，会让服务端空间检索退化，需要"服务端只做语义检索"配合）。

#### 4.5.3 PrivacyService 接口（写死）

```python
class PrivacyService:
    def obfuscate_location(
        self, lat: float, lng: float, mode: str, epsilon: float | None = None
    ) -> ObfuscatedPoint: ...

    def obfuscate_route(
        self, trip_id: str, share_mode: str, epsilon: float = 1.0
    ) -> SharedRoute: ...   # 返回 geometry + precision 标签 + 语义序列

    def semantic_generalize(self, trip_id: str) -> list[SemanticStep]: ...
```

`mode` 取值：`none / geo_indistinguishability / k_anonymity / grid_snap`。

**返回值必须带 `precision` 标签**，这是前端渲染与论文度量的共同契约：

```json
{
  "geometry": {...},
  "precision": {"level": "circle", "radius_m": 1200, "epsilon": 1.0, "mechanism": "geo_indistinguishability"},
  "semantic_sequence": ["人文历史(高)", "人文历史(中)", "自然风光(极高)", "人文历史(高)"],
  "privacy_version": "privacy-2026.1"
}
```

#### 4.5.4 与现有模型的整合

- `Photo` **永远保留原始坐标**（数据库中的原始值不因分享而改变，扰动只发生在**读取出口**）—— 这是"隐私不破坏主功能"的关键
- `Place` 作为已泛化的标签层（POI 本身是公开信息，不构成隐私泄露）
- `Trip` 新增 `share_mode`、`privacy_version`
- `MapService` 新增 `get_shared_route(trip_id, share_mode, epsilon)`
- 前端按 `precision.level` 渲染：精确点 / 圆形区域 / 多边形区域 / 城市中心 / 隐藏

#### 4.5.5 语义感知的扰动（RQ1 的形式化）

朴素拉普拉斯会把"龙门石窟"扰动到"洛阳机场"，语义全毁。本项目用**语义感知**约束噪声：

```
效用损失 U(p, p') = 1 - sim(semantic(p), semantic(p'))
噪声采样的接受域：{ p' | U(p, p') <= U_max }  ∪  常规拉普拉斯分布
```

即：**优先在"语义等价类内部"采样噪声点**（同类 POI、`poi_type` 相同、`ai_tags` 重叠度高），语义等价类为空时退回标准机制。

- 语义等价类由 `Place.poi_type` + `Theme` + `ai_tags` 离线预计算，存 `SemanticEquivalenceClass`
- `U_max` 默认 0.3，写进 config
- **这是 RQ1 的核心贡献点，也是"语义感知 Geo-Indistinguishability"这个名字的来处**
- 度量：同一 ε 下，语义感知机制 vs 朴素机制的 "语义保真率" 与 "RAG Recall@5 保留率" 对比曲线

### 4.6 威胁模型（v3 新增，答辩必问题）

**必须先写清楚防谁，否则"隐私保护"无从评估。**

| 对手 | 能力 | 能看到什么 | 我们的防御 |
| --- | --- | --- | --- |
| **A1 好奇的服务端**（honest-but-curious） | 持有数据库全部明文 | 原始坐标、时间、照片语义 | 前端 LDP 加噪（可选）；或明示"服务端可见，但我们不分享原始坐标" |
| **A2 分享接收者** | 拿到分享链接 | 只有泛化几何 + 语义序列 + proof | ε 扰动 + k-匿名 + 语义抽象 |
| **A3 链上观察者** | 读全部链上数据 | 只有 32 字节承诺哈希 + 时间戳 + 聚合计数 | 链上不写坐标、不写语义原文、不写身份 |
| **A4 恶意贡献者** | 注册多个身份提交伪造模式 | 全局模式库 | ZKP 证明"行程真实存在" + 每用户对同一模式仅贡献一次 + 信誉权重 |
| **A5 重识别攻击者** | 掌握目标的大致行踪（如朋友圈） | 分享出来的语义序列 | k-匿名约束（support ≥ 5）+ 语义序列长度截断（默认 ≤ 6 步） |

**明确不防御**：
- 拥有物理设备访问权限的攻击者
- 全球被动监听者（流量分析）—— 只做传输加密，不做匿名网络
- 用户自己主动公开分享原始照片

> **论文里必须写**：语义序列本身具有重识别风险（"龙门石窟+洛邑古城+老君山+洛阳博物馆"这个四元组几乎唯一确定一个人）。因此**模式库对外发布前必须满足 k-匿名**：`support < k` 的模式只在本机使用，不允许上链或对外推荐。这是本项目与朴素"语义分享"方案的关键区别，务必写进 RQ2。

---

## 五、技术栈

| 模块 | 选型 | 备注 |
| --- | --- | --- |
| 前端框架 | React + TypeScript + Vite | 后置 |
| 地图 | 高德 JS API | 后置，JS Key 必须配安全密钥和域名白名单 |
| UI | Tailwind CSS + shadcn/ui | 后置 |
| 后端 | FastAPI + SQLAlchemy 2.0 + Pydantic v2 + Alembic | |
| 数据库 | PostgreSQL 16 + PostGIS 3.4 + pgvector | **需自建镜像**，见 §5.1 |
| ORM 空间扩展 | **GeoAlchemy2** | PostGIS 必需，别漏 |
| 对象存储 | MinIO | 后置，只存缩略图 |
| Agent 编排 | 阶段 0～3 纯 Python 管道，阶段 4 迁 LangGraph | `AGENT_RUNTIME` 切换 |
| RAG | 自己实现核心流程，不用黑盒框架 | |
| Embedding | **第一天必须选定** | 见 §5.2 |
| Reranker | bge-reranker-v2-m3（本地）或 Qwen rerank（API） | 阶段 3 再加；本地需 GPU/显存，无卡就用 API |
| 向量库 | pgvector | **阶段 0～2 不建 ANN 索引**，见 §5.4 |
| LLM 抽象 | `core/llm.py` 统一封装 | 重试/超时/成本统计 |
| 缓存 | 阶段 0 内存 dict + TTL；阶段 5 Redis | POI / 路线 / 逆地编结果 |
| 可观测 | 阶段 0：JSONL + 表记录；阶段 5：Langfuse | |
| LLM | DeepSeek / Qwen 做对话 | |
| 移动端 | Capacitor | 阶段 6 |
| 部署 | Docker Compose + Nginx | 分阶段 |
| CI/CD | GitHub Actions | |
| 测试 | pytest + Playwright + 坐标转换 + 聚类 + RAG 评测 | |
| **（v3）语义标签** | **自维护字典 `semantic_tags.yaml` + 版本号** | 见 §5.6，**不依赖任何外部服务** |
| **（v3）DP 机制** | 自实现平面拉普拉斯（约 80 行） | 不引第三方 DP 库，论文里好说清 |
| **（v3）ZKP - L0 降级** | Merkle 承诺 + Ed25519 签名 + 公开时间戳 | **默认路径**，一天能做完 |
| **（v3）ZKP - L1 目标** | gnark（Go，Groth16）或 Halo2（Rust） | **可选**，进程外调用，见 §5.7 |
| **（v3）链** | 本地哈希链（默认）/ OpenTimestamps（免费）/ L2 测试网（Sepolia、Base Sepolia） | **主网与代币一律不做** |
| **（v3）聚合通信** | 复用现有 FastAPI + 队列，不上 MQ | 5～30 人规模不需要 |

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

| 方案 | 模型 | 维度 | 场景 |
| --- | --- | --- | --- |
| 国内 API | Qwen text-embedding-v3 | **默认 1024**（可选 768/512） | 有 API，省事 |
| 本地 | bge-m3 | 1024 | 想本地跑 |
| OpenAI | text-embedding-3-small | 1536 | 有海外 API |

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

| 阶段 | 方案 | 说明 |
| --- | --- | --- |
| 阶段 0 | 纯向量 + 结构化过滤 | 不做全文 |
| 阶段 2 | pg_trgm + ILIKE | 临时方案，按字符 trigram 切，效果一般 |
| 阶段 4 | zhparser 或 pg_jieba | Docker 镜像编译扩展 |

阶段 0 不做 RRF，先向量 top-k + 元数据过滤；阶段 4 加 RRF 融合。

### 5.4 向量索引时机（避免召回不足）

pgvector 的 HNSW 是**先图遍历、后过滤**，带 `WHERE` 时返回条数可能远少于 k，甚至 0 条。而本应用的核心检索恰恰是"user_id + 时间范围 + 空间范围 + 向量"，最容易踩坑。

| 阶段 | 做法 |
| --- | --- |
| 阶段 0～2（万级） | **不建 ANN 索引**，精确扫描，正确性优先 |
| 阶段 3+（十万级） | 建 HNSW（`m=16, ef_construction=64`），查询时 `ef_search` 调大 |
| 兜底策略 | 检索层实现两阶段：ANN 结果 < k 时回退精确扫描 |

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

### 5.6 语义标签体系（v3 新增，必须版本化）

**这是整个科研轨的地基，地基漂移则模式库、电路、评测全部作废。**

```yaml
# backend/app/data/semantic_tags.yaml
version: "sem-2026.1"
tags:
  - id: heritage      # 人文历史
    zh: 人文历史
    poi_types: [风景名胜:风景名胜, 文化传媒, 文物古迹, 宗教场所]
    theme_hints: [古镇, 古城, 博物馆, 石窟, 寺庙, 遗址]
  - id: nature        # 自然风光
    zh: 自然风光
    poi_types: [自然风光, 公园广场, 山川, 湖泊]
    theme_hints: [山, 湖, 森林, 草原, 海边]
  - id: watertown     # 水乡古镇
    zh: 水乡古镇
    theme_hints: [水乡, 古镇, 古村, 园林]
  - id: metropolis    # 大都市
    zh: 都市风光
    poi_types: [商务楼宇, 购物中心, 地标]
    theme_hints: [外滩, 陆家嘴, CBD, 商圈, 天际线]
  - id: leisure       # 休闲度假
    zh: 休闲度假
  - id: food          # 美食探索
    zh: 美食探索
    poi_types: [餐饮服务, 美食街]
fallback: unknown
```

**硬约束**：

- 标签字典版本号写进 `Trip.semantic_version`、`SemanticPattern.pattern_version`
- **换字典必须跑 `make resemantic` 全量重算**，与 `make reindex` 同级别
- 抽取顺序（成本从低到高，命中即停）：`Place.poi_type` 映射 → `Theme` 映射 → `ai_tags` 关键词 → LLM 兜底（只在以上都失败时调，且必须幂等缓存）
- 每条语义步带强度：`高 / 中 / 低`（由停留时长 + 照片数 + 用户评分合成）

### 5.7 ZKP 技术路线与降级（v3 新增）

**原则：永远有一条不需要 ZK 也能演示的路。**

| 级别 | 方案 | 能提供什么 | 工作量 | 何时用 |
| --- | --- | --- | --- | --- |
| **L0（默认）** | Merkle 承诺 + Ed25519 签名 + 公开时间戳 | 完整性 + 存在性 + 时间戳，**不提供零知识** | 1 天 | 阶段 7.3 起步，演示必备 |
| **L1** | 外包 gnark/Halo2 电路，Python 通过子进程/HTTP 调用 | 真正的零知识范围证明 | 2～3 周 | RQ3 核心 |
| **L2** | 电路内联 + proof 聚合 | 多行程批量验证 | 不做 | 未来工作 |

**L1 电路的 MVP 定义（务必先做最小版本）**：

```
公开输入：
  region_bbox     (min_lat, min_lng, max_lat, max_lng)
  time_window     (start_ts, end_ts)
  semantic_digest (语义序列的哈希)
  avg_score_x100  (平均评分 ×100)
秘密输入：
  points[]  (lat, lng, ts) × n
  scores[]  × n
约束：
  1. 所有 point 落在 region_bbox 内
  2. 所有 ts 落在 time_window 内
  3. semantic_digest == HASH(semantic_tags(points))
  4. avg(scores) >= 阈值
  5. n <= 32（电路规模上限）
```

**先只做约束 1 和 2**（单点/多点的范围证明，电路很小、100 行以内能搞定），跑通验证链路后再逐条加。**不要一上来就写完整电路**，那是阶段 7 崩掉的最常见原因。

**工程约束**：

- 电路执行一律**进程外**，带 30s 超时；超时/失败 → 标记 `status='failed'`，分享内容降级为 `route_only`，**不阻塞、不报错**
- `proof` 与 `public_inputs` 落库，`chain_tx_hash` 可空
- 验证端（前端或第三方）只需 `proof + public_inputs + vk`，不依赖我们的数据库 —— 这才是"可验证"的意义

### 5.8 区块链与"联邦"的定位修正（v3 新增，重要）

**区块链的定位：从"存储层"改为"验证层"。**

- 链上**只写**：`commitment`（32 字节哈希）、`timestamp`、`circuit_version`、聚合计数（整数）
- 链上**绝不写**：坐标、语义原文、用户标识、行程标题
- 主网与代币一律不做；默认用**本地哈希链**（自维护 append-only 表 + 每个承诺串联上一个哈希），演示效果与链一致，成本为零；需要"公开可验证"时改用 OpenTimestamps（免费、比特币锚定）或 L2 测试网

**"联邦学习"的定位修正**：

只有 5～30 个用户，**经典联邦学习（FedAvg + 安全聚合）既不收敛也不提供有意义的隐私保证**，硬套会被答辩老师一眼看穿。v3 的做法：

- 保留"数据不出域"的核心思想：**本地抽取语义序列 → 本地计算模式统计 → 只上传扰动后的统计量/加密更新**
- 但把术语从"联邦学习"改为 **"隐私保护的分布式语义聚合（Privacy-Preserving Distributed Semantic Aggregation）"**
- 聚合器是**逻辑角色**（本项目里就是服务端一个函数），不宣称去中心化
- 真正的去中心化（PoCQ / 多 Agent 共识）放在 RQ4，明确标注为"未来工作或独立论文"

**这个修正让项目从"套壳联邦学习"变成"诚实的小样本分布式聚合方案"，反而更站得住。**

---

## 六、代码结构

文件夹按职责分，不按架构层分。一个层可能对应多个文件夹。

```
travel-memory/
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── MapPage/
│   │   │   └── AgentPage/
│   │   ├── components/
│   │   │   ├── ui/
│   │   │   └── business/
│   │   │       ├── PrecisionBadge.tsx          ★ v3 精度标签
│   │   │       ├── SharedRouteLayer.tsx        ★ v3 泛化路线渲染
│   │   │       ├── VerificationBadge.tsx       ★ v3 已验证徽章 + 验证链接
│   │   │       └── SemanticSequenceBar.tsx     ★ v3 语义时序条
│   │   ├── services/
│   │   │   ├── api/
│   │   │   │   ├── client.ts
│   │   │   │   ├── photo.ts
│   │   │   │   ├── trip.ts
│   │   │   │   ├── agent.ts
│   │   │   │   ├── share.ts                   ★ v3
│   │   │   │   └── recommend.ts               ★ v3
│   │   │   └── native/
│   │   │       ├── photoSource.ts
│   │   │       └── index.ts
│   │   ├── workers/thumbnail.worker.ts
│   │   ├── utils/{exif.ts, coord.ts, format.ts}
│   │   ├── store/ types/ main.tsx
│   ├── capacitor.config.ts
│   └── package.json
│
├── backend/
│   ├── docker/db/Dockerfile
│   ├── app/
│   │   ├── main.py
│   │   ├── cli.py
│   │   ├── api/v1/
│   │   │   ├── router.py photos.py trips.py agent.py
│   │   │   ├── share.py                        ★ v3 分享与断言接口
│   │   │   └── recommend.py                    ★ v3 群体语义推荐接口
│   │   ├── schemas/
│   │   │   ├── photo.py trip.py agent.py common.py
│   │   │   ├── privacy.py                      ★ v3 ObfuscatedPoint / SharedRoute
│   │   │   ├── assertion.py                    ★ v3 PrivacyAssertion / VerifyRequest
│   │   │   └── semantic.py                     ★ v3 SemanticStep / SemanticPattern
│   │   ├── services/
│   │   │   ├── photo_service.py
│   │   │   ├── trip_service.py
│   │   │   ├── map_service.py                  + get_shared_route()
│   │   │   ├── document_service.py
│   │   │   ├── memory_service.py
│   │   │   ├── privacy_service.py              ★ v3 坐标模糊 / 语义泛化 / 精度标签
│   │   │   ├── semantic_pattern_service.py     ★ v3 语义抽取 / 模式挖掘 / k-匿名发布
│   │   │   ├── federated_service.py            ★ v3 本地更新 / 扰动 / 聚合
│   │   │   └── blockchain_bridge.py            ★ v3 锚定 / 验证 / 事件索引（旁路）
│   │   ├── agent/
│   │   │   ├── service.py pipeline.py graph.py state.py
│   │   │   ├── prompts/
│   │   │   │   ├── rewrite.md query_parse.md generate.md self_check.md
│   │   │   │   ├── memory_extract.md
│   │   │   │   └── semantic_extract.md         ★ v3 语义标签抽取（含字典版本注入）
│   │   │   ├── nodes/
│   │   │   │   ├── pipeline/
│   │   │   │   │   ├── rewrite_query.py query_parse.py retrieve.py
│   │   │   │   │   ├── build_context.py generate.py self_check.py
│   │   │   │   └── graph/
│   │   │   │       ├── plan.py call_tool.py reflect.py write_memory.py
│   │   │   │       ├── privacy_assert.py       ★ v3 可验证分享节点（P3）
│   │   │   │       └── semantic_pattern.py     ★ v3 语义模式推荐节点（P3）
│   │   │   ├── memory/{short_term,episodic,semantic,preference,procedural}.py
│   │   │   ├── rag/{indexer,retriever,query_rewrite,query_parse,reranker,context_builder}.py
│   │   │   ├── eval/{dataset,metrics,runner}.py
│   │   │   └── observability.py
│   │   ├── jobs/
│   │   │   ├── base.py
│   │   │   ├── caption_job.py
│   │   │   ├── index_job.py
│   │   │   ├── semantic_job.py                 ★ v3 语义序列抽取（幂等）
│   │   │   └── anchor_job.py                   ★ v3 异步链上锚定（旁路，失败重试不阻塞）
│   │   ├── tools/
│   │   │   ├── base.py registry.py cache.py
│   │   │   ├── memory_tool.py poi_tool.py route_tool.py
│   │   │   ├── privacy_tool.py                 ★ v3
│   │   │   ├── zk_location_tool.py             ★ v3（进程外电路）
│   │   │   └── federated_recommend_tool.py     ★ v3
│   │   ├── privacy/                            ★ v3 隐私机制（纯函数，可单测）
│   │   │   ├── laplace.py                      平面拉普拉斯
│   │   │   ├── kanonymity.py
│   │   │   ├── gridsnap.py
│   │   │   └── metrics.py                      重识别率 / 语义保真率 / 效用损失
│   │   ├── zk/                                 ★ v3（可选，进程外）
│   │   │   ├── circuit/                        gnark 或 Halo2 工程
│   │   │   ├── executor.py                     子进程/HTTP 调用 + 超时熔断
│   │   │   └── verify.py                       轻量验证（可在前端复用逻辑）
│   │   ├── models/
│   │   │   ├── base.py photo.py trip.py place.py theme.py
│   │   │   ├── document.py memory.py message.py job.py
│   │   │   ├── agent_run.py eval.py tool_call_log.py
│   │   │   ├── privacy.py                      ★ v3 PrivacyAssertion / SemanticEquivalenceClass
│   │   │   ├── semantic.py                     ★ v3 SemanticPattern / SemanticTrip
│   │   │   └── federated.py                    ★ v3 FederatedUpdate / ChainAnchor
│   │   ├── db/{session.py, init_db.py}
│   │   ├── core/
│   │   │   ├── config.py                       + PRIVACY_* / ZK_* / CHAIN_* / SEMANTIC_*
│   │   │   ├── llm.py security.py deps.py exceptions.py
│   │   │   └── feature_flags.py                ★ v3 统一开关（默认全关）
│   │   ├── utils/{coord.py, geo.py}
│   │   └── data/
│   │       └── semantic_tags.yaml              ★ v3 语义标签字典（版本化）
│   ├── alembic/
│   ├── tests/
│   │   ├── test_photos.py test_trips.py test_agent.py
│   │   ├── test_tools.py test_memory.py test_jobs.py
│   │   ├── test_coord.py test_rag_eval.py
│   │   ├── test_privacy.py                     ★ v3 机制正确性 + 效用损失
│   │   ├── test_semantic.py                    ★ v3 抽取准确率 + 模式挖掘
│   │   ├── test_zk.py                          ★ v3 证明/验证往返（可 skip）
│   │   └── test_degradation.py                 ★ v3 ★ 最重要：旁路全关时主流程不受影响
│   ├── data/{eval_cases.jsonl, traces/}
│   ├── requirements.txt
│   └── pyproject.toml
│
├── research/                                   ★ v3 科研产物，与工程代码分离
│   ├── experiments/                            实验脚本（隐私-效用曲线、留一法）
│   ├── notebooks/                              结果绘图
│   └── results/                                CSV + 图（论文素材）
│
├── docker-compose.yml
├── docker-compose.stage0.yml
├── .gitignore
├── .env.stage0.example
├── .env.example
├── Makefile
├── PROJECT.md
└── README.md
```

### 文件夹与架构层对应

| 文件夹 | 架构层 | 职责 |
| --- | --- | --- |
| api/v1/ | 接口层 | 定义 URL、收请求、转 Service |
| schemas/ | 接口层 | Pydantic 请求/响应格式 |
| services/ | 业务层 | 业务逻辑（含 DocumentService / MemoryService / PrivacyService / SemanticPatternService） |
| agent/ | 业务层 | Agent 运行时（pipeline、graph、nodes、memory、rag、eval） |
| jobs/ | 业务层 | 离线批处理 |
| tools/ | 业务层 | 工具适配器 + 注册表 + 缓存 |
| **privacy/（v3）** | 业务层 | 隐私机制纯函数，无 IO，便于单测与论文复现 |
| **zk/（v3）** | **旁路验证层** | 电路执行器，进程外，失败可降级 |
| models/ | 数据层 | SQLAlchemy 表结构 |
| db/ | 数据层 | 数据库连接 |
| core/ | 全局 | 配置、LLM、密钥、鉴权、依赖注入、**feature flags** |
| utils/ | 全局 | 坐标转换、空间计算 |
| data/ | 全局 | 评测集、trace 文件、语义标签字典 |
| **research/（v3）** | 独立 | 实验与结果，不参与部署 |
| main.py / cli.py | 全局 | 入口 |

### 职责边界（写死）

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| DocumentService | 文档 CRUD、向量写入、底层向量/结构化查询 | 查询改写、解析、RRF、rerank |
| rag/retriever.py | 查询改写、结构化解析、过滤编排、RRF、rerank、top-k 组装 | 底层向量写入 |
| rag/indexer.py | 调 DocumentService 写入 | 查询 |
| jobs/* | 幂等调度、重试、成本统计、状态回写 | 业务逻辑（委托 services） |
| core/llm.py | LLM 调用、重试、超时、token/成本统计 | 业务逻辑、prompt 内容 |
| observability.py | 统一写 AgentRun/Step/ToolCallLog | 业务逻辑 |
| nodes/* | 只调 `trace.step(...)`，不自己插 observability 表 | 直接写 observability 表 |
| eval/* | 离线评测，可单独跑 | 不参与运行时 |
| **privacy/*（v3）** | 纯数学机制（拉普拉斯、k-匿名、网格吸附），无 IO | 数据库读写、HTTP |
| **services/privacy_service.py（v3）** | 编排 privacy/* 与 PostGIS，产出带 `precision` 的结果 | 电路、链 |
| **services/blockchain_bridge.py（v3）** | 承诺、锚定、验证、事件索引；**失败抛 degraded 不抛 fatal** | 业务逻辑、主流程任何必需环节 |
| **services/semantic_pattern_service.py（v3）** | 语义抽取编排、模式挖掘、k-匿名门禁、推荐查询 | 电路 |
| **zk/executor.py（v3）** | 进程外执行电路、超时熔断、返回 proof | 数据库、业务判断 |

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
- **（v3）Feature Flag 总开关**：`core/feature_flags.py`，`PRIVACY_ENABLED` / `SEMANTIC_ENABLED` / `ZK_ENABLED` / `CHAIN_ENABLED` / `FEDERATED_ENABLED`，**默认全部 false**
- **（v3）版本化三兄弟**：`EMBEDDING_MODEL_VERSION` / `SEMANTIC_TAG_VERSION` / `PRIVACY_VERSION`，任一变更都要重算并写进 `AgentRun` 与 `EvalResult`
- **（v3）降级契约**：所有旁路模块统一返回 `DegradedResult(ok=False, reason=..., fallback=...)`，`tests/test_degradation.py` 断言"全关状态下 P0 指标不变"

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

### 7.1 核心表（原有）

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
  share_mode                        ★ v3  private/fuzzy/route_only/verifiable
  privacy_version                   ★ v3  如 "privacy-2026.1"
  semantic_sequence JSONB           ★ v3  ["heritage:high","heritage:mid","nature:very_high",...]
  semantic_version                  ★ v3  如 "sem-2026.1"
  avg_score                         ★ v3  0~5，用户自评或由停留/照片数推断

TripItem
  id, trip_id, place_id, order, arrive_at, leave_at
  semantic_tag                      ★ v3  heritage/nature/watertown/metropolis/leisure/food
  semantic_intensity                ★ v3  high/mid/low
  score                             ★ v3  0~5

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
  chain_commitment                  ★ v3  可空，Merkle 承诺哈希（旁路写入）

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
  privacy_assertion_id              ★ v3  可空
  semantic_version                  ★ v3  可空

AgentStep
  id, run_id, step_type, input, output, tool_name, latency_ms, created_at

ToolCallLog
  id, message_id, run_id, tool_name, input, output
  latency_ms, cache_hit, created_at

EvalCase
  id, user_id, question
  expected_evidence JSONB, expected_answer
  kind                              ★ v3  rag / memory / recommend / refusal

EvalResult
  id, eval_case_id, run_id
  recall_at_k, mrr, ndcg, citation_rate
  faithfulness, latency_ms, prompt_version
  semantic_version                  ★ v3  可空
```

### 7.2 新增表（v3）

```
PrivacyAssertion                                 -- 可验证位置断言
  id, user_id, trip_id
  share_mode                        -- fuzzy / route_only / verifiable
  mechanism                         -- laplace / kanonymity / gridsnap
  epsilon                           -- 隐私预算，可空
  public_inputs JSONB               -- region_bbox / time_window / semantic_digest / avg_score
  proof BYTEA                       -- 可空（L0 时为空）
  proof_scheme                      -- 'none' / 'merkle_sig' / 'groth16' / 'halo2'
  circuit_version                   -- 如 "route-v1"
  commitment                        -- 32 字节哈希，对所有 scheme 都存在
  chain_tx_hash                     -- 可空（未锚定 = 未上链，仍可离线验证）
  status                            -- pending / proved / anchored / failed / expired
  expires_at                        -- 分享链接有效期
  created_at

SemanticEquivalenceClass                         -- 语义等价类（支撑语义感知扰动）
  id, semantic_tag, version
  members JSONB                     -- [{place_id, name, lat, lng, poi_type}]
  updated_at
  -- unique(semantic_tag, version)

SemanticPattern                                  -- 群体语义模式库
  id, pattern JSONB                 -- ["heritage:high","nature:high"]
  pattern_version                   -- 如 "sem-2026.1"
  support                           -- 支持该模式的行程数（k-匿名门禁依据）
  source_count                      -- 贡献用户数（去重）
  avg_score                         -- 平均评分
  score_sum, score_n                -- 聚合用，避免浮点漂移
  confidence                        -- 由 support / avg_score 合成
  published                         -- bool，support >= k 才为 true
  last_updated
  -- unique(pattern_hash, pattern_version)

FederatedUpdate                                  -- 本地更新上传（小样本聚合）
  id, user_id, round_id
  model_version, pattern_version
  encrypted_update BYTEA            -- 扰动后的统计量/加密更新
  proof BYTEA                       -- 可空，ZKP 证明更新来源真实
  noise_scale                       -- LDP 扰动尺度
  aggregated                        -- bool
  created_at
  -- unique(user_id, round_id, pattern_version)   防重复贡献

ChainAnchor                                      -- 本地哈希链（默认）/ 外部链记录
  id, seq, prev_hash, commitment
  external                          -- 'local' / 'ots' / 'sepolia' / 'base-sepolia'
  tx_hash                           -- 可空
  created_at
  -- unique(seq)
```

### 7.3 索引设计

| 表 | 索引 | 类型 | 何时建 |
| --- | --- | --- | --- |
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
| **Trip（v3）** | `(user_id, share_mode)` | B-tree | 阶段 7 |
| **Trip（v3）** | `semantic_sequence` | GIN (jsonb_path_ops) | 阶段 7 |
| **PrivacyAssertion（v3）** | `(user_id, trip_id, status)` | B-tree | 阶段 7 |
| **PrivacyAssertion（v3）** | `commitment` | B-tree unique | 阶段 7 |
| **SemanticPattern（v3）** | `(pattern_version, published, avg_score)` | B-tree | 阶段 7 |
| **FederatedUpdate（v3）** | `(round_id, aggregated)` | B-tree | 阶段 7 |
| **ChainAnchor（v3）** | `unique(seq)` / `commitment` | unique / B-tree | 阶段 7 |

### 7.4 设计原则

- 原图只存 `asset_id + platform`，展示时通过系统 API 重新取
- 缩略图本地必须有一份（Capacitor 用 Filesystem API 存 Documents 目录，存相对路径，绝对路径会失效），否则离线、重装、换设备后地图就是空壳
- 云端 MinIO 只存缩略图，且要允许用户关闭云同步
- 删除逻辑要级联：删 Photo → 删 MinIO 缩略图 → 删向量 → 删行程关联 → 删 MemorySource 引用 → **删 PrivacyAssertion（撤回分享必须物理删除 proof 与 commitment 关联，但链上哈希不可逆，需在 UI 明示"链上承诺无法删除"）**
- theme 不用数组，用多对多带 `confidence` 和 `source`
- embedding 维度不写死，从 config 读；**换模型必须 reindex**
- `MemoryItem.embedding` 和 `Document.embedding` 维度必须一致
- 加 hash 去重，避免重复导入
- 加 `deleted_at` 软删除
- Document 是 RAG 的检索单元，Photo/Trip 是数据源
- MemoryItem 是分层记忆的统一存储单元（**不再单设 Preference 表，避免两套衰减逻辑**），MemoryEvent 记录变更历史
- **（v3）原始坐标永不因分享而修改**：扰动只发生在读取出口（`get_shared_route`），数据库里的 `TripItem`/`Photo` 坐标始终是真值
- **（v3）链上只写哈希**：`commitment` 是 32 字节，链上观察者得不到任何坐标、语义、身份
- **（v3）每张 v3 新增表都要能整表删除而不影响主流程** —— 这是 `test_degradation.py` 的断言之一

### 7.5 评测集稳定标识

**不要用自增 id 做评测标识**（Document 重建后 id 会变，photo/trip 同理）。统一用 `source_type + source_id + chunk_index`：

```jsonl
{"question":"我去年秋天去过哪些古镇？","expected_evidence":[{"source_type":"trip","source_id":"t1","chunk_index":0}],"expected_answer":"..."}
{"question":"我在杭州最喜欢哪类地点？","expected_evidence":[{"source_type":"place","source_id":"p12","chunk_index":0}]}
{"question":"帮我规划一天类似我上次的路线。","expected_evidence":[{"source_type":"trip","source_id":"t2","chunk_index":0}]}
```

`source_type` 取值：`photo` / `trip` / `trip_item` / `place` / `note`。

**（v3）新增两类评测样例**：

```jsonl
{"kind":"refusal","question":"我 2019 年去过巴黎吗？","expected_answer":"拒答（无证据）"}
{"kind":"recommend","question":"去完洛阳该去哪？","expected_answer":"自然风光类（如老君山/嵩山方向）","note":"★ 群体先验，不计入 citation_rate，只计 hit@3"}
```

> **关键**：`kind='recommend'` 的样例**不参与"引用率 100%"的统计**，否则会把统计性建议误判为事实性幻觉。见 §9.11。

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
→ [v3] 入队 IngestJob(job_type='semantic')
→ semantic_job：TripItem → 语义标签 + 强度 → 写 TripItem / Trip.semantic_sequence
```

### 8.2 硬约束

- **幂等**：`unique(photo_id, job_type, ai_model)`，同一任务不重复烧 token
- **重试**：`attempts` 上限 3，失败记 `last_error`，不阻塞其他任务
- **成本**：`MAX_CAPTION_PER_RUN`（如 200）+ `LLM_DAILY_BUDGET`，超限直接停
- **状态可见**：`Photo.ai_status` 让前端能展示"处理中/失败"
- **可重跑**：`make reindex` 清空 Document 向量后全量重建（换 embedding 模型时必须执行）
- 阶段 0～5 用 CLI + APScheduler/RQ 即可，**不要过早引入 Celery + MQ**
- **（v3）`make resemantic`**：换语义标签字典后全量重算 `TripItem.semantic_tag` 与 `Trip.semantic_sequence`，与 `reindex` 同级别
- **（v3）`semantic_job` 的 LLM 兜底必须带缓存**：`unique(trip_item_id, semantic_version)`，否则重跑会烧钱

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

**（v3）`AgentState` 新增字段**（仅在 feature flag 打开时填充，关闭时为 `None`）：

```
semantic_context      -- 当前行程的语义序列
group_prior           -- 群体语义先验（★ 与 evidence 严格分离，见 §9.11）
privacy_mode          -- 当前会话的分享精度设置
assertion_ref         -- 本轮产生的断言 id（可空）
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
- **（v3）新增"意图分类"**：`personal_memory`（查自己的记忆，走 RAG）vs `group_recommendation`（问"去哪"，走语义模式库）。分类错会把群体先验当个人证据，是引用率指标的最大风险源，必须单独评测

### 9.5 上下文融合与拒答策略

**融合顺序（固定模板）**：

```
[系统指令]
[用户偏好 / 语义记忆]         ← query_user_memory 结果
[当前行程上下文]              ← 短期记忆
[检索证据]                    ← Document top-k（带编号 [1][2][3]）
[群体语义先验]                ← ★ v3，独立区块，明确标注"来自 N 位匿名用户的统计规律，非你的记录"
[对话历史]
[用户问题]
```

规则：

- memory 结果放在证据之前，作为"背景"；两者冲突时以**检索到的原始证据**为准
- 每个证据块带稳定标识（`source_type + source_id + chunk_index`），prompt 里用 `[1]` 编号，要求 LLM 引用编号
- token 预算：证据总长超过预算时按 rerank 分数截断，并在回答里说明"还找到 N 条相关记录"
- **（v3）群体先验区块必须带固定免责前缀**，且 prompt 明确要求"该区块内容不得作为事实性陈述，不得附加引用编号"

**拒答策略（必须实现）**：

| 情况 | 行为 |
| --- | --- |
| 检索结果为空 | 明确说"你的照片里没有找到相关记录"，并给替代动作（"要不要搜一下附近的 XX？"） |
| 最高 rerank 分数 < 阈值 | 同上，或回答时显式加"不确定"限定 |
| `self_check` 判定引用缺失 | 降级为拒答，不允许输出无引用事实 |
| 涉及未来/实时信息（天气、门票） | 必须走工具，禁止用记忆编造 |
| **（v3）问题被判为 `group_recommendation` 但模式库无匹配** | 明确说"暂时没有足够的匿名模式支持这个建议"，并退回个人记忆 + 地图工具 |

**引用率 100%** 是硬指标（见 §0 验收标准），`self_check` 节点校验，失败即重生成一次，仍失败则拒答。

> **（v3）`self_check` 必须升级一条规则**：若回答中出现引用编号指向群体先验区块 → 判失败。这条规则保护了"引用率 100%"这个硬指标不被新功能污染。

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
- **（v3）`chain_commitment` 只写哈希不写内容**：Memory 的链上锚定是"存在性证明"，不是"内容备份"；用户删除记忆后链上哈希仍在，必须在隐私政策里写明

### 9.7 工具校验

- 每个工具入参用 Pydantic schema 校验
- `query_user_memory` 返回结构化证据：`photo_ids`、`trip_ids`、`snippets`、`scores`
- Agent 最终回答必须引用照片/行程 ID，方便前端高亮地图
- `search_poi` 返回高德 POI ID，不要只返回自然语言
- `calculate_route` 用高德路径规划，PostGIS 主要负责距离、附近、聚类
- 加超时、重试、缓存（POI/路线/逆地编 TTL 缓存）、限流
- 加 ToolCallLog（含 `cache_hit`），方便调试幻觉
- **（v3）旁路工具必须声明 `degradable=True`**，注册表中标记；执行失败时写入 `status='degraded'` 的 ToolCallLog，Agent 继续往下走，不得重试超过 1 次

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

### 9.9 可验证位置断言与 ZKP（v3 新增，RQ3）

**要证明的命题**：

> "存在一条我真实走过的路线，它的所有途经点都落在公开的地理区域内，时间落在该行程的时间窗内，其语义序列的哈希等于公开摘要，且平均评分 ≥ 阈值。"
> **但不暴露任何具体坐标、具体地点名、具体时间、我的身份。**

#### 9.9.1 断言生成的完整链路

```
1. 用户选择 trip + share_mode='verifiable' + ε
2. PrivacyService.obfuscate_route() → 泛化几何 + precision 标签
3. 组装 public_inputs:
     region_bbox       = 行程包围盒外扩 5km（公开）
     time_window       = [start_date, end_date]（公开，精度到天）
     semantic_digest   = HASH(semantic_sequence)（公开）
     avg_score_x100    = round(avg_score * 100)（公开）
4. 组装 secret inputs:
     points[] = TripItem 坐标序列（仅 location_source in ('exif','user')）
     scores[] = TripItem.score
5. zk/executor.py 进程外执行电路（30s 超时）
   → 成功：proof + public_inputs → PrivacyAssertion(status='proved')
   → 失败/超时：降级为 L0（Merkle 承诺 + 签名），status='proved', proof_scheme='merkle_sig'
6. anchor_job 异步上链（本地哈希链 / OTS / L2）
   → 成功：status='anchored', chain_tx_hash=...
   → 失败：保持 'proved'，UI 显示"已可验证，未上链"
7. 返回分享链接 + 验证页 URL
```

#### 9.9.2 验证端（无需信任我们）

```
验证方拿到 {proof, public_inputs, vk, commitment}
→ 本地/前端 verify.py 校验
→ 通过 → 显示"该断言由符合声明的行程生成，未泄露任何坐标"
→ 不需要访问我们的数据库，不需要我们的服务器在线
```

这一步是"可验证"的全部意义所在，也是演示里最有冲击力的一幕。

#### 9.9.3 降级矩阵（写死，任何一环失败都不阻塞）

| 环节 | 失败时 | UI 表现 | 分享是否可用 |
| --- | --- | --- | --- |
| 语义抽取 | 语义序列为空 | 隐藏语义条 | ✅ 可用（fuzzy） |
| 电路执行超时 | 用 L0 承诺-签名 | "已承诺，未生成零知识证明" | ✅ 可用 |
| 上链失败 | 仅本地 | "已可验证，尚未上链" | ✅ 可用 |
| 验证服务不可用 | — | "暂无法在线验证" | ✅ 可用（离线可验） |

#### 9.9.4 Agent 节点

`agent/nodes/graph/privacy_assert.py`（P3，flag 控制）：

```
输入：trip_id, share_mode, epsilon
输出：{shared_route, assertion_id, verify_url, degraded_reason?}
写入：PrivacyAssertion（经 tools/privacy_tool.py，不直接插表）
```

> **注意**：节点本身不直连链，也不直连电路，**一律通过 tools/**，保持依赖单向。

### 9.10 群体语义轨迹推荐（v3 新增，RQ2）

**核心思路**：从"基于具体地点"升级为"基于语义模式"的群体推荐。放弃具体坐标/景点的协同过滤，转向**基于语义轨迹的序列模式挖掘**。

#### 9.10.1 语义抽象示例

```
洛阳：  龙门石窟(人文·高) → 洛邑古城(人文·中) → 老君山(自然·极高) → 洛阳博物馆(人文·高)
苏州-上海：拙政园(水乡·高) → 平江路(水乡·中) → 外滩(都市·高) → 陆家嘴(都市·高)

抽象为：
  heritage:high → heritage:mid → nature:very_high → heritage:high
  watertown:high → watertown:mid → metropolis:high → metropolis:high

发现的模式：
  "高评分连续同类体验，然后切换到不同类别的高评分体验"
```

**推荐逻辑**：用户输入"洛阳" → 解析为 `heritage` → 查询"heritage 之后最常出现且评分最高的语义标签" → 推荐 `nature` → 再落到具体 POI 时走 `search_poi`（**这一步才回到具体地点，且必须调地图 API 校验，防幻觉**）。

**评分机制的作用**：作为模式质量门槛，只有平均评分 > 4.0 的序列进入模式库。

#### 9.10.2 三段式路线（严格递进，不许跳步）

| | 前期：本地语义挖掘 | 中期：+ ZKP 可验证 | 后期：多 Agent 共识 |
| --- | --- | --- | --- |
| 核心贡献 | 小样本下的语义轨迹推荐 | 可验证的匿名语义模式 | 去中心化的语义质量共识 |
| 解决"样本少" | 语义抽象 + 评分过滤 + k-匿名 | ZKP 验证真实性，防止刷量 | 多 Agent 交叉验证 |
| 科研深度 | 低（工程为主） | 中高（明确技术难点） | 高（前沿） |
| 工程复杂度 | 低 | 中（需开发电路） | 高（需多 Agent + 合约） |
| 建议启动 | 阶段 7.1 | 阶段 7.2→7.3 | 阶段 7.4（可选，多半只写未来工作） |
| 论文定位 | 动机实验 / 系统概述 | 核心贡献章节 | 扩展章节或独立论文 |

#### 9.10.3 模式挖掘与发布

```
本地：TripItem → semantic_sequence（semantic_job）
     → 评分过滤（avg_score >= 4.0）
     → 序列化为 pattern（长度截断到 ≤ 6 步，降低重识别风险）
上传：只传 {pattern_hash, support_delta=1, score_sum, score_n}（扰动后）
聚合：SemanticPatternService.aggregate()
     → support / avg_score / confidence 更新
发布门禁：★ support >= k（默认 5）才置 published=true
推荐：next_semantic(current_tags, user_pref) → 只查 published 的模式
       若本地有未发布模式，可在本机使用但不对外（UI 标注"仅本机"）
```

**评分防操纵（必须做，否则整条链路可刷）**：

- 评分只能来自**本地已验证行程**（有 ZKP 断言的优先加权）
- 每个 `(user_id, round_id, pattern_version)` 唯一约束，同一用户对同一模式只贡献一次
- 聚合时对 `avg_score` 做时间衰减，老模式权重下降
- `confidence = f(support, avg_score, recency)`，前端展示 confidence 而非原始 support（避免泄露精确人数）

#### 9.10.4 "联邦"的现实性修正（务必按此表述）

见 §5.8。核心：**只有 5～30 个用户时，不要宣称在做联邦学习。**

正确表述：

> 本项目采用**隐私保护的分布式语义聚合**：各客户端在本地完成语义抽取与模式统计，仅上传经 LDP 扰动的聚合统计量（不含任何坐标、地点或标识），服务端作为逻辑聚合器汇总为全局语义模式库。由于参与者规模有限（5～30），本工作不宣称联邦学习的收敛性或去中心化保证，其隐私收益来自**语义抽象 + 扰动 + k-匿名门禁**三重机制的叠加。

**这样写既诚实，又能把"小样本"从劣势转成研究问题本身。**

#### 9.10.5 实验设计（留一法，小样本也能出图）

```
对 N 条行程（N >= 8）：
  for i in 1..N:
     用除 i 外的 N-1 条构建模式库
     用 i 的前 k 步语义序列预测第 k+1 步
     记录 hit@3 / hit@5 / MRR
  基线：随机推荐、流行度推荐、纯个人记忆最近邻
  输出：柱状图 + 表格（research/results/）
```

这个实验**不需要真实多用户**，用自己 + 同学的公开行程描述或手工构造的 8～20 条行程就能跑，是"小样本"设定下最诚实也最可行的评估方式。

### 9.11 群体先验与个人证据的隔离（v3 新增，硬约束）

**这是 v3 最重要的一条工程纪律。**

| | 个人证据（Evidence） | 群体先验（Group Prior） |
| --- | --- | --- |
| 来源 | 用户自己的 Photo / Trip / Memory | 匿名聚合的 SemanticPattern |
| 能否引用 | ✅ 必须引用，计入 citation_rate 100% | ❌ **禁止引用**，不计入 citation_rate |
| prompt 位置 | `[检索证据]` 区块，带 `[1][2][3]` 编号 | `[群体语义先验]` 区块，无编号 |
| 表述语气 | "你在 2024-10-03 去过龙门石窟 [1]" | "类似行程的人通常会接着去自然风光类景点（基于 N 条匿名模式，置信度 0.7）—— **这是建议，不是你的记录**" |
| self_check | 缺失引用 → 拒答 | 出现引用编号 → 判失败 |
| 落入 Memory | 情景/语义记忆照常写回 | **不写回 Memory**（避免"别人喜欢"污染"我喜欢"） |

**为什么必须隔离**：引用率 100% 是本项目的核心硬指标。一旦群体统计被当成"我的记忆"引用，整个评测体系就崩了，而且这种错误极难在答辩时解释。

**实现方式**：`build_context.py` 里两个区块用不同的数据结构（`EvidenceBlock` / `PriorBlock`），`self_check.py` 里做显式校验，`EvalCase.kind` 区分统计口径。

---

## 十、开发路线（严格串行，阶段号为准）

| 阶段 | 时间 | 目标 | 科研内容 |
| --- | --- | --- | --- |
| 阶段 0 | 第 1 周 | 纯文本 RAG CLI | — |
| 阶段 1 | 第 2 周 | 离线批处理 + 照片元数据接入 | — |
| 阶段 2 | 第 3 周 | 结构化 + 向量混合检索 | — |
| 阶段 3 | 第 4 周 | 工具调用 + Reranker | — |
| 阶段 4 | 第 5～6 周 | Memory + LangGraph | — |
| 阶段 5 | 第 7 周 | 评估与可观测 | — |
| 阶段 6 | 第 8～10 周 | 地图相册 + Capacitor | **P1 隐私分享（fuzzy）** |
| **阶段 7（新增，可选）** | 第 11～14 周 | **科研扩展轨，独立分支 `feat/research`** | RQ1→RQ2→RQ3→(RQ4) |

> **（v3 重要决策）**：把科研内容从阶段 5/6 里**剥离出来，单列为阶段 7**。
> 原方案把 ZKP 塞进阶段 5、PoCQ 塞进阶段 6，会让本就紧张的 8～10 周主线直接崩盘，且一旦科研卡住会连带拖垮工程交付。
> **阶段 7 是"主线交付之后"的增量，做不完不影响毕业设计/答辩的工程部分。**

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
- **（v3）P1 隐私分享**：`share_mode=fuzzy` + `PrivacyService` + 前端精度标签渲染（约 2 天，不含 ZKP）
- **（v3）`tests/test_degradation.py` 必须在此阶段建立**：断言所有科研 flag 关闭时，P0～P2 全部指标不变

> 注：Capacitor 做不到 100% 代码复用。核心业务逻辑尽量复用，**原生能力和权限需要平台适配**。

### 阶段 7：科研扩展轨（第 11～14 周，可选，独立分支）

**前提**：阶段 0～6 已交付且验收标准 A 达标。**任一 7.x 未做完，直接砍掉，不影响主线。**

#### 7.1 语义层与隐私机制（约 1 周，RQ1）

- `semantic_tags.yaml` v1 + `SemanticPatternService` + `semantic_job`
- `privacy/laplace.py` / `kanonymity.py` / `gridsnap.py` + 单测
- `SemanticEquivalenceClass` 构建，语义感知扰动
- `PrivacyService.obfuscate_route()` + `MapService.get_shared_route()`
- **产出**：隐私-效用曲线（ε vs RAG Recall@5 保留率、语义保真率）、语义感知 vs 朴素机制对比图
- **交付物**：前端精度标签 + 分享页

#### 7.2 语义模式挖掘与推荐（约 1 周，RQ2）

- `Trip.semantic_sequence` / `TripItem.semantic_tag` 落库
- 模式挖掘（PrefixSpan 或自实现频繁子序列，序列短，自实现更简单可控）
- 评分过滤 + k-匿名发布门禁
- `semantic_recommend` 工具 + `semantic_pattern` 图节点
- **§9.11 隔离机制落地**（`EvidenceBlock` / `PriorBlock` + self_check 校验）
- **产出**：留一法 hit@3 实验（§9.10.5）+ 与三个基线的对比表
- **交付物**：Agent 里"去完洛阳该去哪"的真实回答

#### 7.3 可验证断言（约 1.5 周，RQ3）

- 先做 **L0**：Merkle 承诺 + Ed25519 签名 + 本地哈希链（1 天，保证有东西可演示）
- 再做 **L1**：gnark 或 Halo2 电路，**先只做约束 1、2（范围 + 时间）**，跑通后加 3、4
- `zk/executor.py` 进程外 + 30s 超时熔断 + 降级矩阵（§9.9.3）
- `blockchain_bridge.py`：本地哈希链（默认）/ OpenTimestamps / L2 测试网
- `PrivacyAssertion` + `privacy_assert` 图节点 + 前端验证徽章 + 独立验证页
- **产出**：proof 生成/验证耗时、proof size、验证成功率表；端到端演示
- **风险最高，做不完就停在 L0，论文写"承诺-签名方案 + 电路设计（已完成约束建模，实现进行中）"**

#### 7.4 多 Agent 语义共识（可选，多半只写未来工作）

- PoCQ 变体：多 Agent 提议语义模式 → 其他 Agent 用本地数据验证 → 语义共识 → 信誉累积
- 结合 ERC-8004 身份与信誉（仅做设计，不接主网）
- **只在 7.1～7.3 全部完成且时间充裕时启动**；否则写成"未来工作"章节，给出设计要点即可

### 阶段 7 的"止损线"（写进 README）

- 7.1 做不完 → 砍掉整个阶段 7，交付纯工程项目（仍然是一个完整的 Agent 应用）
- 7.2 做不完 → 论文只写 RQ1
- 7.3 只做完 L0 → 论文写 RQ1+RQ2，可验证性部分写"承诺方案与电路设计，完整 zkSNARK 实现作为未来工作"
- **任何情况下，阶段 0～6 的验收标准 A 不允许退化**

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
research/results/*.png
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

### `.env.example`（完整版，含 v3 新增段）

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

# ---------- v3：科研扩展轨（全部默认关闭）----------
PRIVACY_ENABLED=false            # 隐私保护分享
SEMANTIC_ENABLED=false           # 语义标签抽取与模式库
ZK_ENABLED=false                 # 零知识证明（需先完成 L0）
CHAIN_ENABLED=false              # 链上锚定
FEDERATED_ENABLED=false          # 分布式语义聚合

# 隐私
PRIVACY_VERSION=privacy-2026.1
PRIVACY_DEFAULT_EPSILON=1.0      # km^-1
PRIVACY_DEFAULT_MECHANISM=geo_indistinguishability
PRIVACY_SEMANTIC_UMAX=0.3        # 语义感知扰动的效用损失上限
PRIVACY_K_ANONYMITY=5            # 模式发布最小支持度

# 语义
SEMANTIC_TAG_VERSION=sem-2026.1
SEMANTIC_MAX_SEQ_LEN=6           # 语义序列截断长度，降重识别风险
SEMANTIC_MIN_SCORE=4.0           # 进入模式库的评分门槛

# ZK / 链
ZK_SCHEME=none                   # none / merkle_sig / groth16 / halo2
ZK_CIRCUIT_VERSION=route-v1
ZK_EXECUTOR=subprocess           # subprocess / http
ZK_TIMEOUT_SEC=30
CHAIN_BACKEND=local              # local / ots / sepolia / base-sepolia
CHAIN_RPC_URL=
CHAIN_CONTRACT_ADDR=
CHAIN_PRIVATE_KEY=               # 仅测试网，绝不填主网
```

### `Makefile`

```makefile
.PHONY: install dev up down migrate test eval eval-compare cli \
        caption-batch index-batch reindex \
        semantic-batch resemantic anchor-batch \
        exp-privacy exp-recommend lint

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

# ★ v3 最重要的一条：断言旁路全关时主流程不受影响
test-degradation:
	cd backend && pytest tests/test_degradation.py -v

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

# ---------- v3 ----------
semantic-batch:               # 语义标签抽取（幂等）
	cd backend && python -m app.jobs.semantic_job --limit 500

resemantic:                   # 换语义标签字典后必须执行
	cd backend && python -m app.jobs.semantic_job --force

anchor-batch:                 # 异步链上锚定，失败不阻塞
	cd backend && python -m app.jobs.anchor_job

exp-privacy:                  # 隐私-效用曲线实验
	cd research && python experiments/privacy_utility_curve.py

exp-recommend:                # 留一法语义推荐实验
	cd research && python experiments/leave_one_out.py

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

> **（v3）链相关服务一律不进 docker-compose**。默认走本地哈希链（就是一张表），需要外部链时用 OpenTimestamps（一次 HTTP 调用）。**不自建节点，不跑 Geth。**

---

## 十二、风险清单

| 风险 | 对策 |
| --- | --- |
| **隐私**：缩略图、GPS、时间、人脸都可能敏感 | 隐私政策、授权说明、可删除、可导出、可关闭上传、MinIO 预签名 URL、传输加密 |
| **坐标偏移**：WGS84 存储，GCJ-02 展示 | 显式 `coord_system` 字段；高德转换 API 有 40 点/次与配额限制，要分批缓存 |
| **PostGIS + pgvector 镜像** | 自建 Dockerfile（§5.1），第一天验证两个 extension 都能建 |
| **iOS EXIF 丢失**：Capacitor 取图可能拿不到 GPS | **真机第一天验证**；备选 PHAsset + `requestContentEditingInput` 或社区插件；否则"EXIF 解析放前端"这条原则要改 |
| **URI 失效**：Web File 临时，Android MediaStore 可能变，iOS 用 PHAsset localIdentifier，Capacitor webPath 临时 | 只存 `platform + asset_id`，展示时重新取；缩略图存 Filesystem 相对路径 |
| **无 GPS 照片占比高** | `location_source` + 时间邻近推断 + 手动标注 + "未定位"分组（§4.2）；**且不得作为 ZKP 秘密输入** |
| **时区错误导致按天分组错** | 双写 `taken_at` / `taken_at_local` + `tz_name`（§4.3）；**进电路前归一化到 UTC 整数秒** |
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

### v3 新增风险

| 风险 | 严重度 | 对策 |
| --- | --- | --- |
| **科研挤爆主线工期** | 🔴 最高 | 科研全部剥离到**阶段 7 独立分支**；设止损线（§十.7）；`test_degradation.py` 保护主线 |
| **ZKP 电路做不出来** | 🔴 高 | 三级降级 L0→L1→L2（§5.7）；**先只做范围+时间约束**；L0 承诺-签名方案保证永远有东西可演示 |
| **电路规模爆炸**（多点序列 + 哈希 + 评分） | 🟠 中高 | 秘密输入点数上限 32；序列长度截断 ≤ 6；评分用定点整数（×100）；哈希用电路友好哈希（Poseidon/MiMC，不要 SHA256） |
| **语义标签体系漂移** | 🟠 中高 | `semantic_tags.yaml` 版本化 + `make resemantic`；字典变更必须重跑全部实验 |
| **语义序列的重识别**（"龙门石窟+老君山+洛博"几乎唯一） | 🟠 中高 | k-匿名门禁（support ≥ 5）+ 序列长度截断 + 强度分档粗化；**必须写进论文威胁模型** |
| **群体先验污染引用率硬指标** | 🔴 高 | §9.11 隔离机制：`EvidenceBlock` / `PriorBlock` 分离 + self_check 校验 + `EvalCase.kind` 区分统计口径 |
| **评分可被刷** | 🟠 中 | 每用户每模式仅贡献一次（唯一约束）+ 优先加权已验证行程 + 时间衰减 + 展示 confidence 不展示 support |
| **样本量太少（5～30 人）导致实验不可信** | 🟠 中高 | **不宣称联邦学习**（§5.8、§9.10.4）；用留一法 + 三个基线对比；明确写"样本规模限制"为论文 limitation |
| **链上 Gas 成本与合规** | 🟡 中 | 默认本地哈希链；外部链用 OpenTimestamps（免费）或 L2 测试网；**主网与代币一律不做** |
| **链上数据不可删除 vs 用户删除权（GDPR/个保法）** | 🟠 中高 | 链上**只写 32 字节哈希**，不构成个人信息；UI 与隐私政策明示"承诺哈希不可删除，但不含任何可还原信息" |
| **术语被导师/评委质疑（"这也叫联邦学习？""链在这里有什么用？"）** | 🟠 中高 | 主动修正表述（§5.8）；答辩时先讲"链是验证层不是存储层"，再讲"我们的隐私收益来自语义抽象而非参与方数量" |
| **证明/验证链路依赖我们的服务器** | 🟡 中 | 验证端只需 `proof + public_inputs + vk`，前端可独立验证；提供离线验证页 |
| **扰动破坏主流程检索** | 🟡 中 | 扰动只发生在**读取出口**，数据库原始坐标不变（§7.4） |
| **旁路模块引入循环依赖** | 🟡 中 | `blockchain_bridge` 只能被 `tools/*` 与 `jobs/*` 调用；CI 加依赖方向静态检查（import-linter） |
| **PoCQ / 多 Agent 共识过度设计** | 🟡 中 | 归入 7.4 且默认不启动；写"未来工作"即可，不要为它写代码 |

---

## 十三、演示剧本

### 剧本 A：3 分钟工程版（答辩/简历用，不依赖任何科研模块）

| 时间 | 动作 | 展示的技术点 |
| --- | --- | --- |
| 0:00–0:40 | 导入 1000 张照片 → 地图自动成图、按天聚合成行程 | EXIF、时区、坐标转换、规则聚类、无位置兜底 |
| 0:40–1:20 | 问"我去年秋天去过哪些古镇？" | Query 结构化解析（时间+主题）→ 混合检索 → 带引用回答 → **地图上高亮证据照片** |
| 1:20–1:50 | 追问"那帮我规划一个类似的周末路线" | Memory 命中偏好 → Planning → search_poi / calculate_route → 工具调用 trace |
| 1:50–2:20 | 说"我不太喜欢人多的地方" → 再问一次 | 偏好抽取 → 写回 → 二次问答结果变化（**证明 Memory 生效**） |
| 2:20–2:50 | 打开 trace 面板 | Token、延迟、工具调用链、引用来源 |
| 2:50–3:00 | 打开评测报告 | Recall@5、引用率 100%、拒答样例 |

**答辩/简历可用的量化点**：Recall@5 ≥ 0.8、引用率 100%、1000 张导入 < 10 分钟、单次问答成本 < ¥0.05。

### 剧本 B：5 分钟科研版（导师/开题用，需阶段 7.1～7.3）

| 时间 | 动作 | 展示的科研点 |
| --- | --- | --- |
| 0:00–1:30 | **剧本 A 压缩版**：一次问答 + 地图高亮证据 | 证明这是一个真能用的 Agent，不是玩具 |
| 1:30–2:10 | 打开行程分享页，拖动 ε 滑杆 | 隐私-效用权衡：ε 从 5.0 拖到 0.5，地图上圆半径变大，语义标签**基本不变**（**语义感知扰动 vs 朴素扰动的对比**，RQ1） |
| 2:10–2:50 | 点"生成可验证分享" → 等待 → 出现"已验证"徽章 | proof 生成 → 链上锚定 → **打开独立验证页，在不登录、不访问我们数据库的情况下验证通过**（RQ3） |
| 2:50–3:40 | 回到对话，问"去完洛阳该去哪？" | Agent 命中 `heritage → nature` 模式 → 给出建议 → **UI 显示"基于 N 条匿名模式，置信度 0.72，非你的个人记录"**（RQ2 + §9.11 隔离） |
| 3:40–4:20 | 打开实验报告 | 隐私-效用曲线图、留一法 hit@3 对比表、proof 生成/验证耗时表 |
| 4:20–5:00 | 一句话收尾 | "Agent 主线完全不依赖链；把链和 ZK 全部关掉，前面 90 秒的功能一个不少。" |

> **最后这句话是整套设计的核心卖点**：工程完整性 + 科研增量 + 明确的可降级边界。

---

## 十四、一句话总结（v3）

**工程主线（不可动摇）**

1. **Agent 是主线，地图相册是数据源和可视化；P0 严格串行，地图最后。**
2. **照片不搬家**，只借元数据和缩略图；**WGS84 存储，GCJ-02 展示**。
3. **RAG 必须有 caption / ai_tags**，批处理管道阶段 1 就建，不能拖到阶段 6。
4. **Embedding 配置化 + 版本化**，换模型必须 `make reindex`。
5. **无证据必须拒答，引用率 100% 是硬指标。**
6. **评测集第一天就建**，prompt 版本化，评测结果要能对比归因。
7. 依赖方向不循环：`agent -> nodes -> tools -> services -> models`，可观测统一由 `observability.py` 写。

**科研扩展（可降级、可砍掉）**

8. **区块链是验证层，不是存储层**；链上只写 32 字节承诺哈希，绝不写坐标、语义、身份。
9. **隐私保护分享分享的是"可验证的语义断言"，不是原始坐标**；原始坐标永不因分享而修改，扰动只发生在读取出口。
10. **小样本推荐靠"语义抽象 + 评分过滤 + k-匿名门禁"**，不是具体地点的协同过滤。
11. **群体先验与个人证据严格隔离**：前者不可引用、不计入 citation_rate、不写回 Memory。
12. **用户只有 5～30 人时，不宣称联邦学习**，改用"隐私保护的分布式语义聚合"，把小样本本身写成研究问题。
13. **科研内容全部剥离到阶段 7 独立分支 + feature flag**；任一环节做不完直接砍掉，阶段 0～6 的验收标准 A 不允许退化。
14. **永远保留不需要 ZK 的降级路径**（L0 承诺-签名），保证任何时刻都有可演示的"可验证分享"。
15. **语义标签字典、Embedding 模型、隐私机制三者都要版本化**，任一变更都要重算并重跑实验。

---

## 附：v3 相对 v2 的主要改动

### 结构改动

| 类别 | 改动 |
| --- | --- |
| **新增** | §0 验收标准 B（科研指标，9 项，含降级值） |
| **新增** | §1.1 科研定位：论文标题 + RQ1~RQ4 分级 |
| **新增** | §4.5 隐私保护式位置分享（share_mode / 四种机制 / PrivacyService 接口 / 语义感知扰动） |
| **新增** | §4.6 威胁模型（A1~A5 五类对手 + 明确不防御什么） |
| **新增** | §5.6 语义标签体系（版本化 yaml + `make resemantic`） |
| **新增** | §5.7 ZKP 技术路线与降级（L0/L1/L2 三级 + 电路 MVP 定义） |
| **新增** | §5.8 区块链与"联邦"的定位修正 |
| **新增** | §9.9 可验证位置断言与 ZKP（完整链路 + 验证端 + 降级矩阵） |
| **新增** | §9.10 群体语义轨迹推荐（抽象示例 + 三段式 + 防刷分 + 留一法实验） |
| **新增** | §9.11 群体先验与个人证据的隔离（v3 最重要的工程纪律） |
| **新增** | §10 阶段 7 科研扩展轨（7.1~7.4 + 明确的止损线） |
| **新增** | §13 剧本 B（5 分钟科研版） |
| **新增** | 数据模型：`PrivacyAssertion` / `SemanticEquivalenceClass` / `SemanticPattern` / `FederatedUpdate` / `ChainAnchor` |
| **新增** | 字段：`Trip.share_mode|semantic_sequence|semantic_version|avg_score`、`TripItem.semantic_tag|semantic_intensity|score`、`MemoryItem.chain_commitment`、`AgentRun.privacy_assertion_id|semantic_version`、`EvalCase.kind` |
| **新增** | 目录：`services/privacy_service.py`、`services/semantic_pattern_service.py`、`services/federated_service.py`、`services/blockchain_bridge.py`、`privacy/`、`zk/`、`jobs/semantic_job.py`、`jobs/anchor_job.py`、`research/` |
| **新增** | 测试：`test_privacy.py` / `test_semantic.py` / `test_zk.py` / **`test_degradation.py`（最关键）** |
| **新增** | 风险清单：15 条 v3 新增风险，其中 4 条标为 🔴 高严重度 |

### 对讨论稿的关键修正（我自己的建议，供参考）

| # | 原讨论稿提法 | v3 的修正 | 理由 |
| --- | --- | --- | --- |
| 1 | 科研内容塞进阶段 5（ZKP）与阶段 6（PoCQ） | **剥离为独立的阶段 7**，在阶段 0～6 交付之后 | 8～10 周主线本就紧张，ZKP 极易卡死并拖垮工程交付；独立分支后做不完可整块砍掉 |
| 2 | "联邦学习" | 改为**隐私保护的分布式语义聚合**，明确不宣称 FL 的收敛性与去中心化保证 | 5～30 个客户端下 FedAvg 不成立，硬套会被评委一眼看穿；诚实表述反而更站得住 |
| 3 | "分享语义模式" | 增加 **k-匿名门禁**（support ≥ 5 才对外发布）+ **序列长度截断** | "龙门石窟+洛邑古城+老君山+洛博"几乎唯一确定一个人，纯语义分享不等于匿名 |
| 4 | 未提威胁模型 | 补 §4.6 五类对手 + 明确不防御清单 | 答辩必问"你防谁"，没有威胁模型的隐私保护无法评估 |
| 5 | 未区分证据来源 | 新增 §9.11 **群体先验与个人证据隔离** | 群体统计若被当成个人记忆引用，会直接击穿"引用率 100%"这个核心硬指标 |
| 6 | 直接上 zkSNARK | 三级降级 **L0 承诺-签名（默认）→ L1 电路（先只做范围+时间约束）→ L2** | 保证任何时刻都有可演示的"可验证分享"；电路从最小约束做起才不会崩 |
| 7 | 未提语义标签版本化 | 新增 `semantic_tags.yaml` + `SEMANTIC_TAG_VERSION` + `make resemantic` | 标签字典漂移会让模式库、电路、实验全部作废，是隐形的时间黑洞 |
| 8 | 评分机制只说"做门槛" | 补 **防刷分三重约束**（唯一贡献 + 已验证加权 + 时间衰减） | 评分是公开输入，不防刷则整条 ZKP 链路失去意义 |
| 9 | 未给小样本实验方案 | 补 **留一法 + 三个基线**（随机/流行度/个人最近邻） | 5～30 人也能出可信图表，把"样本少"从劣势转为研究问题 |
| 10 | 未提链上删除权冲突 | 明确**链上只写 32 字节哈希**，并在隐私政策明示不可删除 | 直接上链存语义/坐标会与个人信息删除权冲突，且不可逆 |
| 11 | PoCQ 列入阶段 6 | 归入 **7.4，默认不启动，多半只写"未来工作"** | 多 Agent 共识 + 合约的工程复杂度最高，收益最低，是典型的过度设计 |
| 12 | 未给科研止损线 | §十.7 给出 4 条明确止损规则 | 科研轨必须有"砍掉也不影响毕业"的边界，否则会变成无底洞 |

---

## 附：给 AI / 给自己的执行提醒（实施时逐条勾选）

- [ ] 阶段 0 第一天：选定 embedding 模型 + 验证 PostGIS/pgvector 双扩展 + 建评测集
- [ ] 阶段 0～6：**一行链相关代码都不写**（feature flag 全部 false）
- [ ] 阶段 6 结束前：`tests/test_degradation.py` 必须存在且通过
- [ ] 阶段 7 开始前：切 `feat/research` 分支，主线保持 `main` 可交付状态
- [ ] 阶段 7.3：**先做 L0**（1 天），跑通演示后再动电路
- [ ] 阶段 7.3 电路：先只做约束 1、2，跑通后再加 3、4
- [ ] 任何时候：改 embedding / 语义字典 / 隐私机制 → 重算 + 重跑实验 + 更新版本号
- [ ] 论文写作：先写威胁模型，再写机制，最后写实验；limitation 章节必须包含"样本规模"与"链上不可逆性"
