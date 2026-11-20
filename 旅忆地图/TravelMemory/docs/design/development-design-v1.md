# 开发设计 v1（可直接执行）

> 读者：你们三个人。
> 用法：第 1 节看完全貌；第 5~11 节照着写代码；第 15 节照着排期；第 17 节照着演示。
> 前置：[产品定义 v2](./product-v2-local-first.md)、[产品优化提案](./product-optimization.md)。
> 现有资产：`travelmemory`（阶段 0~7 已完成，359 测试）、`trailshare`（v2026.5，230 测试）。

> **阶段 16 变更（2026-11-20）**：`app/agent/thin.py` 与 `app/privacy/receipt.py` 已删除 ——
> 它们从未挂任何 HTTP 路由，属于「写完但没接线」的代码。本文与
> `product-v2-local-first.md` 中标注「已实现」的这两项，请读作「曾实现，已删除」；
> 设计本身仍可作为阶段 17+ 的参考，但**不要以为它们在运行**。

---

## 1. 一页纸

| 项 | 内容 |
| --- | --- |
| 产品 | 本地优先的旅行记忆 Agent：能答、能证、能分享 |
| 一句话 | 本机能答的本机答，需要措辞的只发几条，需要综合的才上传索引——每答附一张数据小票 |
| 技术主线 | 三档执行（L0/L1/L2）+ 多 Agent 分工（前台/专员/质检）+ 可核对小票 |
| 演示 | 断网也能演：本机问答 → 升级云端 → 导入朋友信物 → 删云端索引 |
| 创新点 | 把"隐私"做成可测档位：覆盖率、准确率、成本、外发字节构成一条曲线 |
| 不做 | 行程规划、年度回顾、区块链、端侧大模型、多人协同编辑 |

---

## 2. 现状盘点：哪些已经能用了

| 能力 | 在哪 | 状态 |
| --- | --- | --- |
| RAG 问答 + 引用 + 拒答 | `app/agent/pipeline.py` | 已实现，359 测试 |
| 混合检索 / 重排 / 记忆 / 评测 / 看板 | `app/agent/*` | 已实现 |
| 数据小票（三档不变量） | `app/privacy/receipt.py` | 曾实现 15 测试；**阶段 16 已删除（未接线）** |
| L1 无状态生成 | `app/agent/thin.py` | 曾实现 8 测试；**阶段 16 已删除（未接线）** |
| 加密信物 / 定向分享 / 承诺 / 签名 | `trailshare` | 已实现，230 测试 |
| 信物推荐 + 反事实忠实度 | `trailshare` E1~E4 | 已实现（E2=0.833） |
| 前端壳 / 地图 / 导入 | `frontend/` | 已实现（阶段 6） |
| 多 Agent 教学原型 | `research/experiments/multi_agent_demo/` | 已实现，可跑 |
| 本机查询引擎（L0） | 无 | **要写** |
| 前台路由 + 专员注册表 | 无 | **要写** |
| 信物导入 React 页 | 无 | **要写** |
| 三档对比实验 | 无 | **要写** |

**结论：不是从零开始，是把已有零件按一条主线装起来。**

---

## 3. 范围

### P0（必须，答辩要看）

1. 小票接进 SSE + 前端档位徽章。
2. TrailShare 打成库，React 里能导入信物、地图打点、回答可引用 `[gift:s_x]`。
3. L0 本机查询（枚举/计数/偏好/求证四类）。
4. 前台路由 + 升级同意卡。
5. L1 接口（已有服务层，补 HTTP）。
6. E-A 三档对比表。

### P1（有时间就做）

7. 服务端专员注册表（summary/planner/fact_check 三套 prompt + 权限）。
8. E-B 小票 A/B 问卷。
9. 设置页"删除云端索引"。

### P2（不做也不影响答辩）

10. 语义审计（反驳员）。
11. 多设备同步 / CRDT。
12. C2PA 导出。

---

## 4. 总体架构

```
┌──────────────────────── 浏览器（Vault） ────────────────────────┐
│  React 壳                                                        │
│   ├─ 地图 / 记忆库 / 信物 / 设置                                  │
│   ├─ 前台 Router（规则分类，先于任何网络请求）                     │
│   ├─ L0 查询引擎（时间解析 + 过滤 + 聚合 + 模板作答）              │
│   └─ IndexedDB：day_docs / photos / gifts / receipts / settings   │
└───────────────┬───────────────────────────────┬──────────────────┘
                │ L1：选中的 K 条日文档           │ 信物文件（点对点）
                ▼                               ▼
┌──────────────────────── 服务端（Engine） ────────┐   ┌── 朋友 ──┐
│  FastAPI                                        │   │ TrailShare│
│   ├─ /agent/chat            （L2，现有）         │   │ 加密信物  │
│   ├─ /agent/answer-with-context（L1，无状态）    │   └──────────┘
│   ├─ Orchestrator：选专员 → 生成 → 质检 → 小票    │
│   └─ Postgres：派生索引 + index_consent + trace   │
└─────────────────────────────────────────────────┘
```

**三档的边界**：L0 不出设备；L1 只发选中的 K 条、不留存；L2 索引在云端、TTL。
**多 Agent 的边界**：前台在客户端（避免分类泄露），专员与质检在服务端（需要模型与工具）。

---

## 5. 多 Agent 设计（可落地版）

### 5.1 角色表

| 角色 | 在哪 | 职责 | 权限 | 失败处理 |
| --- | --- | --- | --- | --- |
| 前台 Router | 客户端 | 分类、选档、选专员、弹升级卡 | 只读本机设置 | 分类失败 → L2 |
| 时间线专员 | 客户端 | L0 枚举/计数/偏好/求证 | 只读本机索引 | 无结果 → 建议放宽条件 |
| 总结专员 | 服务端 | L1 归纳与措辞 | 只读请求里的 K 条 | 引用不过 → 重写 1 次 → 拒答 |
| 规划专员 | 服务端 | L2 建议类 | 检索 + 生成，不要求逐句引用 | 同上，但豁免引用 |
| 求证专员 | 两端 | 判断"是否去过/有没有" | 本机优先，云端兜底 | 无证据 → 主动拒答 |
| 质检员 | 服务端 | 结构校验 + 引用越界 | 只读草稿与证据 | 打回 1 次，再不过拒答 |

**判定"这算不算多 Agent"的标准**：角色之间是否有**不同的信息集、不同的工具权限、不同的验收标准**。三者有其一才算，只有 prompt 措辞不同不算。

### 5.2 服务端注册表

```python
# app/agent/specialists/registry.py
@dataclass(frozen=True)
class Specialist:
    name: str                 # timeline / summary / planner / fact_check
    intents: tuple[str, ...]  # 与 utils/intent.py 的词表对齐
    prompt_name: str          # prompts/<name>_v1.md
    requires_citation: bool
    retrieval_profile: str    # none / narrow / balanced
    allowed_tools: tuple[str, ...]
    max_output_tokens: int

SPECIALISTS = (
    Specialist("summary", ("fact",), "summary_v1", True, "narrow", (), 600),
    Specialist("planner", ("plan",), "planner_v1", False, "balanced", ("route", "poi"), 900),
    Specialist("fact_check", ("fact",), "fact_check_v1", False, "narrow", (), 300),
)
```

### 5.3 编排状态机

```
route → select_specialist → (retrieve?) → generate → audit
                                                   ├─ pass → receipt → done
                                                   └─ fail → repair(1) → audit
                                                                  ├─ pass → done
                                                                  └─ fail → refuse → done
```

**停止条件（缺一不可）**：`max_steps=4`、`max_repair=1`、`budget_ms=2500`、`max_output_tokens` 按专员。超限 → 返回当前最优 + `degraded_reason`，不抛错。

### 5.4 与现有 pipeline 的接法（最小改动）

不改 `AgentPipeline` 的链路顺序，只加三个状态字段：

```python
# app/agent/state.py
specialist: str = "summary"
tier: str = "L2"
receipt: object | None = None
```

`AgentPipeline.run_stream` 开头调用 `Orchestrator.select(state.parsed.intent)`，
把 `specialist.prompt_name` 传给 `GenerateNode`（`GenerateNode` 现在用固定 prompt，加一个可选参数即可）。
**这样 359 个测试不需要重写，只需要新增用例。**

---

## 6. 数据设计

### 6.1 本机 IndexedDB

库名 `travelmemory`，版本 1：

| store | keyPath | 索引 | 内容 |
| --- | --- | --- | --- |
| `day_docs` | `id` | `by_date` / `by_trip` / `by_city` | `{id, trip_id, date, city, place, text, photo_ids[], source}` |
| `photos` | `id` | `by_day` | `{id, thumb: Blob, taken_at, lat, lng, caption}` |
| `gifts` | `id` | `by_from` | `{id: shareId+groupId, share_id, from_name, from_pubkey, places[], accepted_at}` |
| `gift_places` | `id` | `by_tag` | `{id, gift_id, name, tag, intensity, date, evidence}` |
| `receipts` | `id` | `by_ts` | 小票 JSON（原样存，便于"数据账本"页） |
| `settings` | `key` | — | `{cloud_enabled, ttl_hours, persist_requested}` |
| `sync_state` | `trip_id` | — | `{synced_at, doc_count, bytes}` |

**配额策略（来自 2026 年的浏览器实测口径）**：

- Chrome/Firefox 可用约磁盘的 50%；Safari 每源默认约 50MB，且 7 天不访问可能被清。
- 启动时调 `navigator.storage.persist()`，并在设置页显示 `navigator.storage.estimate()`。
- **只存缩略图**（320px / q0.7）与日文档文本，不存原图。
- `QuotaExceededError` 处理：先删最久未访问行程的 `photos`，再删 `day_docs`，最后提示导出。
- **不做双向同步、不做 CRDT**：本机是只读镜像 + 用户显式重新同步。课程项目没有多端并发编辑，上 CRDT 是纯负债。

### 6.2 服务端新增

只加一张表，别动现有表：

```sql
CREATE TABLE index_consent (
  user_id      UUID NOT NULL,
  trip_id      TEXT NOT NULL,
  enabled_at   TIMESTAMPTZ NOT NULL,
  ttl_hours    INT NOT NULL DEFAULT 24,
  deleted_at   TIMESTAMPTZ,
  PRIMARY KEY (user_id, trip_id)
);
```

### 6.3 契约变更

| 现有 | 变更 | 兼容性 |
| --- | --- | --- |
| `DocumentRef.source_type` | 新增 `day` | 只增枚举值，旧数据不受影响 |
| `MemorySourceRef.source_type` | 新增 `friend_share` | 同上 |
| `ChatMeta` | 新增 `tier` / `specialist` | 前端可选读取 |
| SSE | 新增 `receipt` 事件（在 `done` 前） | 旧前端忽略未知事件即可 |
| `PipelineState` | 新增 `tier` / `specialist` / `receipt` | dataclass 默认值，测试不破 |

---

## 7. 接口设计

### 7.1 L1：`POST /api/v1/agent/answer-with-context`

```json
{
  "query": "这趟和上次那趟像在哪？",
  "session_id": "web-ab12cd34",
  "context": [
    {"doc_id": "d_2024-10-02_wuzhen", "date": "2024-10-02", "place": "乌镇",
     "text": "10-02 乌镇：西栅夜景，临水木屋，江南水乡古镇。"}
  ]
}
```

响应 SSE：`meta → evidence → delta* → receipt → done`。

服务层已实现（`ThinAnswerService`）。HTTP 层只做三件事：校验、调用、转发事件。
**不写库、不建 session、不落 trace 表。**

### 7.2 同步镜像：`POST /api/v1/vault/sync`

请求 `{trip_id}`，响应 `{docs: [...], receipt: {...}}`。
这是唯一一次把日文档下发到本机；之后 L0 查询不再联网。响应带一张"同步小票"（docs 数、bytes）。

### 7.3 删除云端索引：`DELETE /api/v1/vault/index/{trip_id}`

软删除 `index_consent` + 删除该 trip 的 `document` 行；返回删除条数。
前端删完后档位徽章从橙变灰，并提示"下次提问需要重新上传"。

### 7.4 SSE 事件顺序

| 事件 | 载荷 | 前端动作 |
| --- | --- | --- |
| `meta` | prompt_version / tier / specialist | 显示档位徽章 |
| `parsed` | 时间/地点/意图 | 显示"我理解了什么" |
| `evidence` | `[n]` 与来源 | 渲染证据条 |
| `delta` | 文本片段 | 流式渲染 |
| `receipt` | 小票 JSON | 渲染小票卡（可展开） |
| `done` | 延迟 / token / 是否拒答 | 收尾 |

---

## 8. 前端设计

### 8.1 路由

```
/            地图首页
/ask         问答
/gift        信物（生成 / 导入）
/library     记忆库
/settings    设置
```

### 8.2 六个页面

| 页面 | 必须有的元素 | 验收 |
| --- | --- | --- |
| 地图 | 旅程卡、图钉、档位总览（本机 N 个地点 / 云端索引 M 条） | 断网能看 |
| 问答 | 输入框、回答卡、档位徽章、小票卡 | L0 秒答；L1 有升级卡 |
| 信物 | 生成（选天/地点/收件人）、导入、预览对方视角 | 导入后地图出现图钉 |
| 记忆库 | 本机/朋友两种图标、评星、删除 | 删除后引用失效 |
| 设置 | 只存本机 / 云端加速、存储用量、删除索引 | 切换后徽章变化 |
| 数据账本 | 历史小票列表、按档位筛选 | 每条能展开 |

### 8.3 关键组件

```ts
// components/AnswerCard.tsx
<AnswerCard
  answer={answer}
  evidence={evidence}          // [{index, thumb, date, place, source}]
  tier={tier}                  // L0 / L1 / L2
  receipt={receipt}
  onExpandReceipt={...}
/>
```

`EscalationCard` 必须显示：为什么本机答不了、将要发什么（条数 + 日期范围）、留存多久、三个按钮（仅这次 / 总是允许 / 取消）。

### 8.4 状态管理

不引 Redux/Zustand。`AppContext` + `useReducer` 足够，状态只有五块：`vault`、`session`、`ui`、`settings`、`receipts`。

### 8.5 TrailShare 集成

1. 在 `trailshare` 加 `src/lib.ts`，导出：`ensureKeyPair`、`pack`、`unpackForViewer`、`unpackWithFallback`、`listMemories`、`putMemory`、`listEdges`、`putEdge`、`types`。
2. 加 `vite.lib.config.ts`，`build.lib = {entry:'src/lib.ts', formats:['es'], fileName:'trailshare'}`。
3. 主项目 `package.json` 加 `"trailshare": "file:../trailshare"`；或把 `dist-lib` 拷进 `src/lib/`。
4. React 里 `import { unpackForViewer } from 'trailshare'`，解出的组写入 `gifts` / `gift_places`。
5. **同一个 origin 才能共享 IndexedDB**：开发时用 Vite 代理把 `/vault` 指到 trailshare 的 build，或直接把库 import 进 React（推荐后者，单页单库）。

---

## 9. 本机查询引擎（L0）

### 9.1 支持的四类问题

| 类型 | 例子 | 输出 |
| --- | --- | --- |
| 枚举 | 去年秋天去了哪些古镇 | 地点列表 + 每个地点 1 张照片 + `[n]` |
| 计数 | 一共去过几个城市 | 数字 + 去重依据 |
| 偏好 | 我更喜欢人文还是自然 | 标签直方图 + 前三名 |
| 求证 | 我真的去过龙门石窟吗 | 是/否 + 日期 + 照片；没有则明确说没有 |

### 9.2 时间解析（规则表）

| 表达 | 解析 |
| --- | --- |
| 去年 / 今年 / 前年 | 日历年 |
| 去年秋天 | 去年 9~11 月 |
| 上个月 | 上月 1 日 ~ 月末 |
| 2024 年 | 全年 |
| 2024-10 | 当月 |
| 五一 / 国庆 | 固定区间（可配置） |

解析失败 → 不猜，返回 `null`，升级到 L1。

### 9.3 执行

```ts
function answerLocally(q: string, docs: DayDoc[], now: Date): LocalAnswer | null {
  const parsed = parse(q, docs, now);      // 时间 + 地点 + 标签 + 意图
  if (!parsed) return null;                // 解析不了 → 交给上层升级
  const hits = filterDocs(docs, parsed);   // 纯函数，可单测
  if (parsed.kind === 'exists' && hits.length === 0) {
    return refuseWithSuggestion(parsed);   // 求证失败：明确说没有
  }
  return render(parsed, hits);             // 模板 + 编号
}
```

### 9.4 失败与升级

- 解析不了 → 升级。
- 枚举为 0 结果 → 先建议放宽时间/地点，不直接升级（避免"没查到"被误当成"需要云"）。
- 求证为 0 结果 → 拒答，不升级（没有证据就是没有）。

---

## 10. 后端设计

### 10.1 文件落点

```
backend/app/
  agent/
    orchestrator.py          # 前台：选专员、预算、停止条件（新增）
    specialists/
      __init__.py
      registry.py            # Specialist 定义与注册表（新增）
      prompts.py             # 专员 prompt 加载与版本（新增）
    thin.py                  # L1（阶段 16 已删除，未接线）
  privacy/receipt.py         # 小票（阶段 16 已删除，未接线）
  api/v1/
    agent.py                 # 加 /answer-with-context（改）
    vault.py                 # sync / delete（新增）
  schemas/
    agent.py                 # ChatMeta 加 tier/specialist（改）
    vault.py                 # 同步与删除契约（新增）
```

### 10.2 与现有节点复用

| 专员 | 复用 | 新增 |
| --- | --- | --- |
| summary | `BuildContextNode` + `GenerateNode` + `SelfCheckNode` | `prompts/summary_v1.md` |
| planner | `ToolCallNode` + `GenerateNode` | `prompts/planner_v1.md` + 豁免引用规则 |
| fact_check | `RetrieveNode` + 拒答模板 | `prompts/fact_check_v1.md` |

### 10.3 质检

v1 只用现有 `collect_violations`（结构 + 越界 + 拒答不带引用）。
**不要**在课程周期内上语义审计；把它写成"下一步"，并在报告里给出设计（已有 `stage8` 文档）。

---

## 11. 数据流

### L0

```
输入 → Router(L0) → 本机过滤 → 模板作答 → 本地质检（引用编号）→ 小票(L0, 0 条) → 渲染
```

### L1

```
输入 → Router(L1) → 选证据(K 条) → 同意卡 → POST /answer-with-context
     → 服务端生成 → 质检 → receipt(L1, K 条, 不留存) → 渲染
```

### 信物

```
trailshare 生成 .trailshare → 主应用导入 → unpackForViewer（本机解密）
→ 写 gifts / gift_places → 地图打点 → L0 回答可引用 [gift:s_x]
```

---

## 12. 安全与隐私

| 威胁 | 措施 | 不宣称 |
| --- | --- | --- |
| 原图外泄 | 只传文本；`photos_sent` 结构恒 0 | 不宣称"设备被攻破也安全" |
| 服务端留存 | L1 无状态；L2 TTL + 删除接口 | 不宣称"端到端加密" |
| 信物转发 | 定向 KEM；界面不报组数 | 不宣称"防截图" |
| 日志泄露 | 小票不含正文；trace 不存 query 原文（可配置） | 不宣称"零日志" |
| 误升级 | 同意卡必须显示条数与范围 | 不宣称"用户永远不会误点" |

**红线**：信物明文、私钥、原图，三者任何一个出现在 L1/L2 的请求体里，都算实现缺陷。

---

## 13. 测试计划

| 层 | 用例 | 现状 |
| --- | --- | --- |
| 小票不变量 | 15 | 已通过 |
| L1 服务 | 8 | 已通过 |
| 路由 | 20（四类意图 + 越界 + 模糊） | 待写 |
| 时间解析 | 20 | 待写 |
| L0 查询 | 20（每类 5） | 待写 |
| 信物导入 | 6（正常/非收件人/旧版本/篡改/重复/空） | 待写 |
| SSE 顺序 | 3（receipt 在 done 前） | 待写 |
| 降级 | 3（云端关/超时/预算耗尽） | 待写 |
| 手工 | 断网全流程 | 待做 |

---

## 14. 实验设计

### E-A 三档对比（主实验）

29 条题逐条标"最低可行档位"，跑三档：

| 指标 | L0 | L1 | L2 |
| --- | --- | --- | --- |
| 可回答条数 | | | |
| 事实正确率 | | | |
| 引用率 | | | |
| P50 / P95 | | | |
| 单次成本 | | | |
| 外发条数 | 0 | | |
| 外发字节 | 0 | | |

**通过线**：L0 覆盖 ≥ 40% 且外发为 0；L1 外发 ≤ L2 的 50%，准确率差距 ≤ 10 个点。达不到就如实写"阶梯不成立"。

### E-D 路由准确率

人工标 29 条的"应走档位"，与 Router 输出比对，目标 ≥ 0.85。混淆矩阵放报告。

### E-B 小票 A/B

20 人，两组，三个 Likert。只报方向，不报显著性。

### E-C 信物忠实度

复用 TrailShare E2 的 0.833，展示"点开引用 → 反事实检验"。

---

## 15. 排期（12 天，3 人）

| 天 | A（前端/Vault） | B（后端/Agent） | C（评测/报告） |
| --- | --- | --- | --- |
| D1 | 契约评审：小票字段、SSE 顺序、IndexedDB schema | 契约评审 + `index_consent` 迁移 | 29 题标"最低档位" |
| D2 | 小票卡 + 档位徽章 | `receipt` 事件接入 pipeline | 搭 E-A 跑批脚本 |
| D3 | TrailShare lib 构建 | `/answer-with-context` HTTP | 写报告第 3~4 节 |
| D4 | 信物导入页 + 地图打点 | `/vault/sync` + 删除接口 | E-C 复现 |
| D5 | L0 时间解析 + 单测 | 专员注册表 + summary prompt | 路由标注复核 |
| D6 | L0 查询 + 回答卡 | planner / fact_check prompt | E-D 跑批 |
| D7 | 升级同意卡 + 设置页 | 质检打回 + 降级 | 报告第 5~6 节 |
| D8 | 数据账本页 | 端到端联调 | E-A 首轮 |
| D9 | 修 UI 缺陷 | 修接口缺陷 | E-B 问卷 |
| D10 | 演示脚本彩排 | 性能与配额测试 | E-A 定稿 |
| D11 | 录屏备份 | 文档与注释 | 报告定稿 |
| D12 | 全体彩排 | 全体彩排 | 答辩 Q&A |

**依赖**：D1 契约不冻结，D2 之后全部返工。宁可 D1 多花半天。

---

## 16. 验收（DoD）

```bash
# 后端
cd backend && python -m pytest -q            # 全绿（含新增）
python -m ruff check app tests               # 全绿

# 前端
cd frontend && npm test && npm run build     # 全绿

# 手工
1. 断网打开 App → 地图有 3 趟旅程
2. 问"去年秋天去了哪些古镇" → L0 秒答，小票 0 条
3. 问"总结这趟风格" → 升级卡 → 允许 → L1，小票 K 条 / 不留存
4. 导入信物 → 地图出现图钉 → 回答引用 [gift:s_x]
5. 设置删除云端索引 → 橙徽章变灰
```

---

## 17. 演示脚本（5 分钟）

| 时间 | 动作 | 台词 |
| --- | --- | --- |
| 0:00 | 飞行模式，打开 App | "现在没有网。地图上是我自己的 3 趟旅程。" |
| 0:40 | 问"去年秋天去了哪些古镇" | "本机答的，0 条上传。这张小票写着原图 0 张。" |
| 1:30 | 问"总结这趟的风格" | "本机写不出这种话，它问我能不能发 4 条日文档到云端——只发这 4 条，不留存。" |
| 2:30 | 允许，出答案 | "蓝色徽章 = 薄引擎。点开小票能看到发了哪几条、多少字节。" |
| 3:20 | 导入朋友信物 | "这是朋友寄来的加密信物，服务端从没见过。导入后地图多了一个图钉。" |
| 4:00 | 问"西塘是谁推荐的" | "它说'从 A 那里学来的'，还能做反事实检验：拿掉这条，推荐会不会变。" |
| 4:40 | 设置里删除云端索引 | "现在这台设备只剩本机。橙色徽章变灰。" |

---

## 18. 风险与回滚

| 风险 | 概率 | 影响 | 缓解 |
| --- | --- | --- | --- |
| 契约反复改 | 高 | 高 | D1 冻结；改动必须走一次评审 |
| TrailShare lib 构建踩坑 | 中 | 中 | 退路：`iframe` 嵌独立页面 + `postMessage`，不改协议 |
| Safari IndexedDB 被清 | 中 | 中 | 请求持久化 + 导出备份 + 报告写明 |
| L0 解析覆盖不足 | 高 | 中 | 只承诺四类；解析不了就升级，不硬猜 |
| 范围蔓延 | 高 | 高 | P2 全部砍；每天站会只问"今天能不能演示" |
| 演示现场网络差 | 中 | 高 | L0 与信物全离线；L1/L2 备录屏 |

**回滚**：所有新功能挂 feature flag（`tier_l1`、`gift_import`、`local_engine`），默认关；关闭后行为回到阶段 7。

---

## 19. 答辩问答

| 问题 | 答 |
| --- | --- |
| 这不就是 RAG 加开关？ | 开关只是表象。三档差的是检索位置与外发量，而且每档都有可核对的小票和不变量。 |
| 多 Agent 在哪？ | 前台、四个专员、质检员；每个专员的工具权限与验收标准不同。没有权限差异的多角色不算多 Agent。 |
| 小票能伪造吗？ | 能。它是自报 + 不变量校验，不是密码学证明；报告里明确写了这条边界。 |
| 为什么不全本地？ | 端侧模型撑不住中文长文本；而且很多问题不需要模型，规则答得更准更快。 |
| 两个项目怎么算一个系统？ | 共用标签、来源模型与证据编号；信物是 L0 的一种证据源，同一张回答卡能同时引用照片和朋友推荐。 |
| 创新点？ | 把隐私做成可测档位：同一问题在三档下的覆盖率、准确率、成本、外发量构成一条 Pareto 曲线。 |

---

## 20. 已知局限（报告照抄）

1. 29 条样本太小，只能说明方向。
2. L0 规则覆盖有限，长尾会掉到 L1/L2。
3. 小票是自报，没有远程 attestation。
4. 私钥在 IndexedDB，未接硬件密钥。
5. 信物的 tag/intensity 是明文，能看出组数与类型。
6. 没有真实用户实验，E-B 只有 20 人。
7. L1 的"无状态"靠服务端自律，没有可验证执行环境。
8. 云端索引一开，服务端确实拥有文字版旅行史；删除是接口承诺。
9. Safari 可能清理本地存储，未做跨设备恢复。
10. 多 Agent 的"专员"目前是 prompt + 权限配置，不是独立服务；这是刻意的取舍，不是没做完。

---

## 附：参考与依据

- Split Brain / 客户端检索 + 无状态服务端：2026 年工程实践（浏览器持有记忆，服务端只做推理）。
- P3（arXiv 2026）：客户端检索 + 服务端草稿 + 客户端校正，保留 90.3%~95.7% 效果、额外泄露可忽略。
- FD-RAG：本地记忆优先、低置信才升级到大模型，与本设计的 L0→L2 同构。
- IndexedDB 配额：Chrome/Firefox 约磁盘 50%，Safari 每源约 50MB 且 7 天可能清理；需 `navigator.storage.persist()`。
- Booking.com 2025：89% 想用 AI 规划，仅 12% 放心独立决策。
- Gartner 2025-06：2027 年底前 40% agentic 项目被取消。
- ALCE 2024：GPT-4 约 50% 陈述未被引用段落完全支持。
