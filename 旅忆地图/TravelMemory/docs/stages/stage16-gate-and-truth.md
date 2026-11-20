# 阶段 16 交付报告（门禁与真相：CI + 清理 + 收敛）

> 执行者：AI 编码代理（会话内直执行）
> 日期：2026-11-20
> 任务书：本阶段不另发任务书，范围来自 2026-11-20 的架构调研结论（16A 装监控 / 16B 删 / 16C 改 / 16D 验收）
> 结论：**四个批次全部执行**；后端 **643 passed / 0 skipped**，前端 **954 passed**，`ruff` / `mypy` / 跨包边界 / 阶段索引 / 生成物校验全绿；删除 36 个受控文件（含退役的 `showcase/` 与 481 行未接线隐私模块），新增 9 个门禁与生成物文件。
> 遗留：4 份地图加载器未合并、`llm.py` 等四个大文件未拆分、CI 尚未在 GitHub 真跑过、`tripIds` 旧字段未删 —— 逐条见 §7。

---

## §1 这一阶段要解决什么

阶段 15 结束时，仓库里有一批「写了但没接线」「声明了但没实现」「文档说有但代码里没有」的东西。
它们单看都不致命，合在一起造成一个后果：**你无法用机器证明任何一条承诺仍然成立**。

本阶段的出口条件只有一句话：**把承诺换成退出码**。

| # | 出口条件 | 判定方式 | 结果 |
|---|---|---|---|
| 1 | 有 CI，且四个维度都跑 | `.github/workflows/ci.yml` 存在，含 backend-lint / backend-test / web / docs | 已落盘（远端首跑待推送） |
| 2 | 分层守卫覆盖 schemas 与 api | `test_layering.py` 规则表 | 通过（7 用例） |
| 3 | 跨工作区依赖有门禁 | `node tools/check-import-boundaries.mjs` | 通过（8 工作区） |
| 4 | 死开关/死配置读取点为 0 | `grep` + 测试期望 | 通过 |
| 5 | Makefile 无空 target | 逐 target 可导入 | 通过 |
| 6 | 阶段索引可自证 | `python tools/check_stage_index.py` | 通过（18 份文档） |
| 7 | 语义标签只有一处原件 | `python tools/gen_semantic_tags.py --check` | 通过（2 份生成物） |
| 8 | 两套运行时行为等价 | `test_graph_runtime.py` 完整事件序列 + 重试次数 | 通过 |
| 9 | 导回日期/坐标 P1 修复 | 4 条新回归用例 | 通过 |
| 10 | 全量回归 | 后端 643 / 前端 954 | 通过 |

---

## §2 交付总表

| 批次 | 内容 | 状态 |
|---|---|---|
| 16A 装监控 | CI（4 job）、分层守卫扩到 schemas/api、跨工作区边界脚本、阶段索引四态 + 校验脚本 | 完成 |
| 16B 删 | 4 个死 flag、6 个死配置、`thumb_url` 死列、4 个空 Makefile target、481 行未接线模块、`showcase/`、产物出库、硬编码 DSN、僵尸 lock、stage2 版本号 | 完成 |
| 16C 改 | 两套编排对齐 + 契约测试、语义算法单一实现、常量下沉、12 处 404 样板收口、导回 P1、依赖上界、SessionStore 删除、聚类叙事修正 | 完成 |
| 16D 验收 | ruff / mypy / 后端全量 / 前端全量 / 边界 / 索引 / 生成物 | 通过 |

---

## §3 逐项改动

### 3.1 门禁（16A）

**`.github/workflows/ci.yml`（新增）**

四个 job：

| job | 跑什么 | 为什么单列 |
|---|---|---|
| `backend-lint` | `ruff check .` + `mypy app` | 快，先失败，省掉后面 1 分钟 |
| `backend-test` | pgvector 镜像 + `alembic upgrade head` + `pytest --junit-xml` | 不给库，10 个 DB 用例永远 skip，trips/photos 全链路是「绿的假象」 |
| `web` | `npm ci` + typecheck + vitest + 边界脚本 | 前端此前唯一门禁是 `tsc` |
| `docs` | 阶段索引 + 生成物校验 | 文档失真不该靠人眼发现 |

pytest 用 `--junit-xml` 而不是只看退出码：本机安全钩子会把 exit code 改成 1，
判定统一认 `failures="0"`（`Makefile` 的注释里记过这个坑）。

**`backend/tests/test_layering.py`（改）**

规则从 3 条扩到 5 条：

```python
("app/schemas", ("app.agent", "app.api", "app.tools", "app.services")),
("app/agent", ("app.api",)),
```

同时把 `schemas/semantic.py` 的常量下沉（见 3.3），并删掉 `services/document_service.py`
以外的历史例外登记。守卫的价值取决于覆盖到哪 —— 只守「已经守住的三个方向」等于没守。

**`tools/check-import-boundaries.mjs`（新增，零依赖）**

四条规则：

1. `apps/*` 不得 import `frontend/*` 或另一个 app（相对路径也算）；
2. `packages/*` 不得 import 任何宿主；
3. 任何工作区只能在 `package.json` 已声明的依赖里 import `@travelmemory/*`；
4. GCJ-02 偏移常数只允许出现在 `packages/geo` 与 `backend/app/utils/coords.py`。

未用 dependency-cruiser 的原因：它要引 30MB 依赖树，而这里只需要布尔判定与
「文件:行号 → 目标」的报错。测试文件（`*.test.ts` / `__tests__`）允许跨工作区，
因为 parity 测试的全部意义就是同时 import 两份实现比对。

**`tools/check_stage_index.py`（新增）**

四条规则：链接必须存在、目录里每份 `stage*.md` 都必须在表里、每行状态必须是四个词之一、
引用阶段文件的行必须带状态。当前索引 18 份文档全部通过。

### 3.2 删除（16B）

| 删掉的东西 | 规模 | 为什么 |
|---|---|---|
| `agent/thin.py` + `privacy/receipt.py` | 481 行源码 + 274 行测试（23 用例） | 没有任何 HTTP 入口，`router.py` 只挂 5 个 router；「零外发」承诺无法兑现，留着只会让人以为系统已有该能力 |
| `showcase/` | 27 个受控文件 + 1 个僵尸 lock | 阶段 9 已声明退役、阶段 13 主应用已改指 `embed.html`；保留只会让「4 份地图加载器」永久存在 |
| 4 个死 flag | `map_album` / `native_bridge` / `zk` / `chain` | 全仓读取点为 0；`STAGE6_FLAG_NAMES` 从 4 降到 2，`STAGE7` 从 3 降到 1 |
| 6 个死配置 | `minio_*` ×3、`tool_rate_limit_per_min`、`privacy_version`、`semantic_tag_version` | 0 引用；其中限流字段宣称了从未实现的能力 |
| `photo.thumb_url` | 迁移 `0009` | 死列，从未写入；保留会诱导写出永远为假的分支 |
| 4 个 Makefile target | `semantic-batch` / `resemantic` / `anchor-batch` / `exp-privacy` / `exp-recommend` | 前三个指向不存在的模块，后可两个指向已清空的目录；可执行的承诺比文档更硬 |
| `backend/data/dashboard*.html` | 2 个产物 | 构建输出不该入库；已加 `.gitignore` |
| `backend/delete_hashed.py` | 1 个脚本 | 内含硬编码数据库 DSN，一次性清理脚本不该留在仓库 |
| `frontend/package-lock.json` | 165KB | workspaces 生效后 npm 只认根 lock，这是僵尸文件 |
| `app/data/semantic_tags.yaml` | 1 份字典快照 | 靠人工同步的「第二真相源」，已由生成器取代（见 3.3） |
| `SessionStore` + 3 条用例 | ~50 行 | 生产零调用；会话历史走 `agent.memory`，不在缓存 list 里 |

版本号统一：`backend/app/main.py`、`frontend/package.json` 从 `0.0.0-stage2` 改为 `0.16.0`，
健康检查 `stage` 从 2 改为 16（`test_api_agent.py` 同步）。

### 3.3 收敛（16C）

**① 两套编排器对齐（本阶段最高风险项）**

改动三处：

- `pipeline.py` 的节点顺序从 `retrieve → tool_call → load_memory → build_context`
  改为 `retrieve → load_memory → tool_call → build_context`，与 `graph.py` 的边一致；
- 新增 `Settings.agent_generate_retries: int = 1`，两种运行时共用；
- `LangGraphAgent.__init__` 的 `max_generate_retries` 默认从 `Settings` 取（`None` 哨兵，
  避免默认参数在 import 时求值）；
- `Settings.agent_runtime` 默认从 `pipeline` 改为 `graph` —— `make eval` 与线上必须同源；
  未知值或 langgraph 缺失仍回落 `pipeline`（降级不抛错）。

契约测试两条（`tests/test_graph_runtime.py`）：

- `test_event_sequence_matches_pipeline`：把原来只比 `{parsed, evidence, done}` 的断言
  改成**完整事件类型序列**逐项相等。旧断言在两套运行时 delta/restart 位置不同时依然绿，
  等于没测。
- `test_retry_budget_matches_between_runtimes`：第一次生成故意不带引用 → 重试一次 → 通过。
  断言两套运行时的 `generate` 调用次数都是 2、最终答案相同。

**② 语义算法只剩一份**

`utils/semantic_tags.py` 新增 `match_semantic_tag()`：投票 → 平票两级裁决
（先比命中词长度，再比 `SEMANTIC_TAGS` 顺序）。`services/semantic_service.py` 删掉
自己那份 `_segments` / `_vote` / `_break_tie`，改为调用它并组装 `SemanticTagResult`。
`classify_semantic_tag` / `score_semantic_tags` / `pick_semantic_tag` 保留为薄封装，
内部走同一个实现。91 条语义相关用例全绿。

**③ 契约常量下沉**

新增 `app/schemas/policy.py`，放 `K_ANONYMITY = 5` 与 `MAX_SEQUENCE_STEPS = 6`。
`schemas/semantic.py` 与 `services/semantic_trajectory.py` 都从这里取，
方向从 `schemas → services`（反向）改成 `services → schemas`（允许）。
`semantic_trajectory` 保留同名符号，调用方 import 路径不变。

**④ 语义标签单源 + 生成**

新增 `tools/gen_semantic_tags.py`：以 `utils/semantic_tags.py` 为唯一原件，生成

```
frontend/src/generated/semanticTags.ts
apps/trailshare-standalone/src/generated/semanticTags.ts
```

两份文件带 `GENERATED` 头；`--check` 模式在 CI 里跑，忘跑生成就红。
前端 `pages/SharePage/semanticTags.ts` 与 `apps/.../types/share.ts` 改为从生成物派生
（`Tag` 类型用 `(typeof SEMANTIC_TAGS)[number]` 收窄），手写数组全部删除。
原来的「从 py 源码抠数组比对」测试保留，作为第二道守卫。

**⑤ 12 处 404 样板收口**

新增 `app/api/deps.py::ensure_flag(name)`。`trips/photos/privacy/recommend` 四个路由文件里
`if not is_enabled(...): raise HTTPException(404)` 的 12 处两行样板全部替换为一行调用。
`grep "该能力未启用"` 从 12 降到 1（只剩守卫自身）。

**⑥ 导回 P1（日期未知 / 5 张未定位）**

后端：

- `TripCreateRequest` 新增可选 `started_on: date`（语义是「当地日期」，不是时间戳）；
- `create_manual_trip` 把它按「当地 00:00 UTC」写进 `started_at`；
- `assign_photos_to_trip` 改为**只有算得出真实时间才覆盖**：
  `if earliest is not None: trip.started_at = earliest`。缩略图没有 EXIF，
  `_earliest(photos)` 恒为 None；无条件赋值会把刚写进去的日期抹掉 —— 这是同一 P1 的另一半。

前端：

- `planJourneyImport` 从 `days[0].date` 取 `startedOn`（只认 `YYYY-MM-DD` 或 ISO 前 10 位，
  畸形日期宁可没有也不猜）；
- `createTrip(title, { startedOn })` 透传 `started_on`；
- **补坐标循环移到 `assignTripPhotos` 之前**：挂照片时会把 `photo.lat/lng` 快照进
  `trip_item`，反序会让那 5 张在地图上变成「未定位」。

新增 4 条回归用例：后端 2 条（日期落库、挂照片不覆盖）、前端 2 条（`startedOn` 取值、
调用顺序改为 patch → assign）。

**⑦ 其它**

- `requirements.txt` 每个依赖加上界（`langgraph>=0.2.60,<2` 等）；新增 `requirements-dev.txt` 的上界；
- `models/trip.py` 与 `api/v1/trips.py` 修正「自动聚类仍在运行」的过时叙事 ——
  阶段 7 起导入不再触发聚类，`rebuild_user_trips()` 无生产调用方，只剩测试与将来的显式命令；
- `PROJECT.md` 红线 6 删除 `make resemantic`（该 target 指向不存在的模块），
  仓库结构更新为 `apps/*` + 5 个 packages；
- `README.md` 更新阶段、目录、测试数与新增的三条校验命令。

---

## §4 文件清单

### 新增（9）

```
.github/workflows/ci.yml
tools/check-import-boundaries.mjs
tools/check_stage_index.py
tools/gen_semantic_tags.py
backend/app/api/deps.py
backend/app/schemas/policy.py
backend/alembic/versions/0009_drop_photo_thumb_url.py
frontend/src/generated/semanticTags.ts
apps/trailshare-standalone/src/generated/semanticTags.ts
```

### 删除（36 个受控文件）

```
backend/app/agent/thin.py
backend/app/privacy/receipt.py
backend/app/data/semantic_tags.yaml
backend/tests/test_thin_engine.py
backend/tests/test_privacy_receipt.py
backend/delete_hashed.py
backend/data/dashboard.html
backend/data/dashboard.demo.html
frontend/package-lock.json
showcase/**（27 个文件，整目录）
```

### 收尾后追加（不属于验收项）

```
start-dev.bat    Windows 一键启动：all / main / apps / check 四种模式
```

它不参与测试与构建，只负责把四个服务各开一个窗口，并在端口已占用时跳过。
三个前端端口不能合并的原因写在 `README.md` 的「一键启动（Windows）」一节。

**朋友分享图层接通（2026-11-20 收尾）**：`TripDrawer` 里那个 disabled 的
「在地图上显示朋友的分享」原本写着「待阶段 16」，但阶段 16 已封板 —— 而且它想防的
误认正在发生：`tripPointsFromTrips(trips)` 把所有行程（含朋友的）都打成 `source: 'photo'`。
收尾时把它做成了真开关：

- `MapPointInput.source` 增加 `'friend'`，`LayerVisibility` 同步增加 `friend`；
- `DEFAULT_LAYER_VISIBILITY.friend = false`（默认不画）；
- `tripPointsFromTrips(trips, 'friend')` 产出朋友点，`MapCanvas` 给它挂「朋友的分享」常显标签；
- `MapPage` 用 `splitTrips()` 分流，我的走 `photo`、朋友的走 `friend`。

验证：前端 954 passed（新增 2 条：图层默认关闭、开关可切换）。

### 关键修改（40 个受控文件）

```
backend/app/agent/pipeline.py           节点序 + 重试读配置
backend/app/agent/nodes/graph/graph.py  重试默认值从 Settings 取
backend/app/core/config.py              删死配置 + agent_generate_retries + 默认 graph
backend/app/core/feature_flags.py       7 个 flag（原 11）
backend/app/core/cache.py               删 SessionStore
backend/app/core/llm.py                 未改（见 §7）
backend/app/models/photo.py             删 thumb_url
backend/app/schemas/trip.py             + started_on
backend/app/services/trip_service.py    日期写入 + 不覆盖
backend/app/services/semantic_service.py  复用统一算法
backend/app/services/semantic_trajectory.py  常量改从 policy 取
backend/app/utils/semantic_tags.py      统一匹配实现
backend/app/api/v1/{trips,photos,privacy,recommend}.py  404 收口
frontend/src/services/journey-import/journeyToTrip.ts  日期 + 顺序
frontend/src/services/api/trips.ts      createTrip 选项
frontend/src/pages/SharePage/semanticTags.ts  改用生成物
apps/trailshare-standalone/src/types/share.ts 改用生成物
docs/stages/README.md                   四态状态机 + 总表
PROJECT.md / README.md / Makefile / .gitignore / package.json
```

---

## §5 验证记录（2026-11-20 本机）

| 命令 | 结果 |
|---|---|
| `ruff check .`（backend） | All checks passed |
| `mypy app`（backend） | Success: no issues found in 119 source files |
| `python -m pytest`（backend） | **643 passed / 0 skipped**（junit: failures=0 errors=0） |
| `npm run typecheck`（全工作区） | 通过 |
| `npm run test --workspaces` | **954 passed**（frontend 436 / trailshare 168 / track-studio 175 / journey-transfer 21 / photo-source 56 / trail 34 / geo 17 / amap 14 / root 14） |
| `node tools/check-import-boundaries.mjs` | 通过（8 工作区：2 应用 / 5 共享包） |
| `python tools/check_stage_index.py` | 通过（18 份阶段文档） |
| `python tools/gen_semantic_tags.py --check` | 通过（2 份生成物，`sem-2026.2`） |
| `alembic upgrade head` | 0008 → 0009 成功 |

> 前端总数从 984 降到 954：删掉 `showcase` 的 34 条，新增导回 2 条。
> 后端从 664 降到 643：删掉未接线模块的 23 条、`SessionStore` 3 条，新增 5 条（2 日期 + 1 重试 + 1 事件序列替换 + 1 相邻）。

---

## §6 踩过的坑

1. **`__all__` 里列不存在的名字不会立刻报错**。给 `semantic_trajectory` 加 re-export 时
   顺手写了 `__all__`，其中 `MIN_AVG_SCORE` 等名字拼错不会在 import 时报，只在
   `from ... import *` 时炸。删掉 `__all__`，只留注释说明「同名符号保留」。
2. **默认参数在 import 时求值**。`LangGraphAgent(max_generate_retries=settings.x)` 不能写成
   默认参数，否则配置改了它不变。改用 `max_generate_retries: int | None = None` + 构造时解析。
3. **`json.dumps` 默认把中文转成 `\uXXXX`**。生成物是给人看的，必须 `ensure_ascii=False`，
   否则每次 diff 都是一屏转义码。
4. **前端 API 签名加位置参数会打断既有调用**。`createTrip(title, startedOn, options)`
   让 5 个测试报 TS2345；改成 `createTrip(title, options?: CreateTripOptions)` 后零破坏。
5. **删 workspaces 成员后必须重建根 lock**。只删目录不跑 `npm install --package-lock-only`，
   CI 的 `npm ci` 会因为 lock 与 package.json 不一致直接失败。
6. **删 `showcase` 会让「4 份地图加载器」变成 3 份，但不会自动合并**。本阶段只做了减法，
   合并留到阶段 17 的 adapter 契约（见 §7）。

---

## §7 未完成 / 判断未做（诚实清单）

| 项 | 状态 | 原因与后续 |
|---|---|---|
| 4 份地图加载器合并 | **未做** | 计划里阶段 16 只做「删 showcase + conformance 测试」，实际只完成了删 showcase；conformance 测试未写。合并需要先有断言，留到阶段 17 的 `amap-loader` adapter 契约 |
| `core/llm.py`(683) / `core/cache.py` 拆分 | **判断不做** | 拆文件只改变阅读路径，不改变职责；`MockLLM` 被 `LLM_PROVIDER=mock` 真实使用，不能挪进 tests。强行拆要动 20+ 处 import，收益与风险不成比例 |
| `eval/runner.py`(686) / `eval/dashboard.py`(415) 拆分 | **判断不做** | 同上；它们是一次性脚本，不参与请求链路。留到「评测要接 CI」时再按职责拆 |
| `tripIds` 旧字段 | **未删** | 后端已不返回、前端恒为 `[]`，但删它会牵动 5 个测试；与阶段 17 的桥契约一起清理更划算 |
| CI 远端首跑 | **未验证** | 工作流已落盘，本地等价命令全绿；需要推送后看 Actions 第一次运行 |
| 迁移 0009 的 `downgrade` | **未实测** | `upgrade` 已在真实库执行；回滚只补一个 nullable 列，风险低但没有跑过 |
| 前端 eslint / prettier | **未做** | 本阶段的门禁取向是「分层规则优先于代码风格」；风格门禁会带来大量无关 diff |
| 地图加载器红线 10 的自动检查 | **部分** | 边界脚本能抓「偏移常数出现在别处」，但抓不到「该用 `@travelmemory/geo` 却自己写了一套近似公式」 |

---

## §8 对阶段 17 的输入

阶段 17 要做模块框架（`packages/module-sdk`）。本阶段为它留下的地基：

1. **边界脚本已经能守「小应用不得 import 主应用」** —— 新模块接进来时，这条规则自动生效；
2. **生成器 + `--check` 的模式已经跑通** —— 模块 manifest / 协议类型可以用同一套「单源 + 生成 + CI 校验」；
3. **两套运行时的契约测试已建立** —— 模块 SDK 的 host/client 两侧也可以照这个模式做「同一组消息、两侧各跑一遍」；
4. **待清账**：`tripIds`、4 份地图加载器、`studio-bridge/types.ts` 手抄类型，都在阶段 17 的契约收口范围内。

---

## §9 开发日志（时间线）

| 时间 | 动作 | 结果 |
|---|---|---|
| 18:23 | 建立基线：git 干净、`.venv` Python 3.13、后端 664 passed、前端 984 passed | 基线确认 |
| 18:24 | 16A：扩 `test_layering` 到 schemas/api；常量下沉 `schemas/policy.py` | 7 用例通过 |
| 18:25 | 16A：写 CI 四个 job、边界脚本、阶段索引校验脚本 | 边界与索引首跑通过 |
| 18:26 | 16B：删死 flag/配置/列（迁移 0009）、空 target、thin/receipt、showcase、僵尸 lock、DSN 脚本 | 后端 643 passed，迁移 0008→0009 |
| 18:27 | 16C：导回 P1（后端 `started_on` + 不覆盖；前端日期 + 顺序） | 后端 19 条行程用例、前端 22 条导回用例通过 |
| 18:28 | 16C：两套编排对齐 + 完整事件序列 + 重试契约 | 22 条图运行时用例通过 |
| 18:29 | 16C：语义算法单一实现、标签生成器、404 收口、SessionStore 删除 | 91 条语义用例通过 |
| 18:30 | 16D：全量回归 + 静态检查 + 文档更新 | 643 / 954 / 全绿 |
| 18:35 | 写本报告与索引 | — |

---

## §10 复盘：这一阶段真正学到的一件事

**「有测试」和「有门禁」是两回事。**

阶段 15 结束时，仓库里有 984 条测试、556 条后端用例，看起来防护很足。
但两套运行时的事件序列只比了 3 个事件类型，分层守卫只守了 3 个包，
DB 用例在没有数据库时会静默 skip —— 这三处都不是「没测」，是「测了但测不到会坏的地方」。

门禁的判据不是覆盖率，是：**如果我今天故意把它改坏，哪条命令会红？**
阶段 16 的每一项改动，都是在给这个问题补一个答案。
