# PROJECT.md —— 项目约定

设计基线：[旅忆地图 TravelMemory.md](./旅忆地图 TravelMemory.md)
上手指南：[README.md](./README.md)

---

## 一、学习目标（Checklist）

- [ ] **RAG**：索引、检索、重排、上下文构建、引用
- [ ] **Memory**：短期会话、情景记忆、语义偏好；衰减与冲突处理
- [ ] **Tool Use**：Pydantic schema、超时、重试、缓存、限流、调用日志
- [ ] **Planning**：任务拆解、多步执行、状态机（LangGraph）
- [ ] **Reflection**：复盘、摘要、写回记忆
- [ ] **Evaluation**：检索指标、引用率、拒答率、工具选择准确率
- [ ] **Observability**：Run / Step / ToolCall 全链路 trace
- [ ] （可选）**Privacy**：差分隐私机制、隐私-效用权衡度量
- [ ] （可选）**Verifiability**：承诺方案、零知识证明、链上锚定与验证

---

## 二、不可协商的约定

| # | 约定 | 违反后果 |
| --- | --- | --- |
| 1 | 照片不搬家，原图永不上云 | 项目立论失效 |
| 2 | WGS84 存储，GCJ-02 展示，字段显式带 `coord_system` | 后期偏移问题极难排查 |
| 3 | 事实性回答引用率 100%，无证据必须拒答 | 核心硬指标失效 |
| 4 | 依赖方向单向：`agent → nodes → tools → services → models` | 视为缺陷，CI 拦截 |
| 5 | 可观测统一由 `observability.py` 写，节点只调 `trace.step(...)` | trace 口径不一致，无法归因 |
| 6 | 换 embedding 模型 → `make reindex` | 产生脏向量 |
| 7 | 阶段严格串行，上一阶段出口条件未满足不得进入下一阶段 | 范围蔓延 |
| 8 | 扩展模块走 feature flag，默认全关；失败必须降级而非抛错 | 主线被拖垮 |
| 9 | 分享信物的解密私钥（X25519）永远只在用户设备本地生成，绝不上传服务端；服务端只收孤立坐标点、只回加噪点 | 隐私保证形同虚设 |
| 10 | 坐标转换**只能**从 `@travelmemory/geo` 取，业务代码不许自带偏移常数 | 同一张照片在不同页面落到不同位置 |

> 第 10 条的由来：这套转换一度在仓库里存在三份，其中一份把「境外判定框」抄窄了，
> 导致边远地区的照片在主应用与展示站里位置不同。合并后 `packages/geo` 是前端唯一入口，
> 判定框与后端 `app/utils/coords.py` 保持一致。

---

## 二·五、仓库结构

JS 侧是 npm workspaces（在仓库根 `npm install` 一次装完）：

```
frontend/        主应用（React + TS + Capacitor）
apps/track-studio/         轨迹演示（独立小应用，可单独打开，也可被 iframe 嵌入）
apps/trailshare-standalone/ 分享旅程（同上，全程零网络请求）
packages/geo/         坐标与地图基础（主应用与小应用共用）
packages/amap-loader/ 高德 JS API 2.0 加载原语（主应用与小应用共用）
packages/trail/       轨迹回放内核（样条/相机/时钟/投影/渲染器，不含 React）
packages/journey-transfer/ 旅程包的导入/导出线格式
packages/photo-source/     照片来源抽象（系统相册 / 文件选择器）
backend/              Python 后端
```

**依赖方向**：`宿主（frontend / apps/*） → packages/trail → packages/geo`，单向不回头；
`宿主 → packages/amap-loader` 是另一条并列箭头，**不经过回放内核**（主应用的地图相册
不是回放，也要加载地图）。内核里出现 React 就说明抽错了层——那是宿主的事。

阶段 16 起，这条方向由 `tools/check-import-boundaries.mjs` 在 CI 里检查：
小应用不得 import 主应用、共享包不得 import 任何宿主、跨包 import 必须在 package.json 里声明。
`showcase/` 已在阶段 16 物理删除（阶段 9 起已退役，阶段 13 主应用改指 `embed.html`）。

**共享代码的判定标准**：两个及以上工作区都要用，且没有应用专属的错误文案 / UI 语义，
才允许进 `packages/`。只被一个工作区用的抽象不进包——那是提前抽象。

两条判据的正反例，都别再争论第二遍：

- **该抽（`amap-loader`）**：去重、超时、script 复用、安全密钥配置是纯机制，两个宿主都要。
  而「失败是抛给 UI 还是静默降级」「超时 15s 还是 8s」「要不要冷却重试」是策略，
  **留在各自宿主的薄适配层**，调用点一行没改。合并前两份实现已经漂移出真 bug
  （安全密钥字段一个写成 `securityCode`、一个写成官方的 `securityJsCode`），见 stage9 §6.1 坑 8。
- **不该抽（`packages/share`）**：分享协议层目前只有 frontend 一个消费者，
  隔壁 `trailshare` 明确不改动。抽了就是提前抽象，还多一份要同步的协议。

---

## 三、分支策略

```
main            始终可交付状态，对应阶段 0~6
feat/research   阶段 7 扩展方向，独立分支，可整块放弃
```

**扩展代码合入 `main` 的前提**：`make test-degradation` 通过（断言所有 flag 关闭时主流程指标不变）。

---

## 四、工作块推进方式

每个阶段拆成若干工作块（见设计文档第十章）。推进原则：

1. **先定契约，再并行**：每个阶段中标记为"共同依赖"的块（通常是数据契约或框架块）先由一人完成，之后其余块可交给不同的人并行推进。
2. **单人开发**：按块号顺序完成，不要跨阶段并行。
3. **出口条件优先于完成度**：一个块只有满足出口条件才算完成，不要为了赶下一个块而留下未验证的代码。

---

## 五、提交规范

```
<type>(<scope>): <subject>

type: feat | fix | refactor | docs | test | chore
scope: agent | rag | memory | tools | api | photo | trip | map | jobs | frontend | infra
```

**每次改动 prompt 必须同时更新 `agent/prompts/` 文件与 `prompt_version`**，否则评测无法归因。
