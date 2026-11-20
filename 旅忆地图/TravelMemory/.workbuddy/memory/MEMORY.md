# TravelMemory 项目长期约定

## 环境

- 虚拟环境在 `F:/project/travelmemory/.venv`，用 `.venv/Scripts/python.exe` 跑一切
  （`python -m pytest` 而不是 `pytest`：Windows 下 `python -m` 才把 cwd 加进 sys.path）
- ~~本机 **没有可用的 PostgreSQL**（5432 不通）~~ **2026-10-23 复核：库是在线的**
  （`eval_result` 有 60 行 3 组），`make dashboard` / `--compare` 可直接跑真实数据。
  仍保留离线路径 `--ablation --offline` 与 `--demo` 以备无库环境。
  涉及真实库的用例会自动 skip；需要验证检索类出口条件时用
  `python -m app.agent.eval.runner --ablation --offline`
- lint：`python -m ruff check .` + `python -m mypy app`（`make lint`）

## 不可协商的红线（PROJECT.md §二）

1. 原图永不上云；坐标 WGS84 存储 / GCJ-02 展示，字段带 `coord_system`
2. 事实性回答**引用率 100%，无证据必须拒答**
3. 依赖方向单向：`agent → nodes → tools → services → models`
   **推论**：被 agent 与 services 共用的东西（契约、纯函数）必须放 `app/schemas/` 或
   `app/utils/`，**不能放 `app/agent/` 下**，否则 services 反向依赖
4. 只有 `core/config.py` 与 `core/feature_flags.py` 能读环境变量
5. 阶段严格串行；扩展模块走 feature flag 且默认全关
6. 改 prompt 必须同步改 `PROMPT_VERSION`（`app/agent/prompts/loader.py`）
7. 分享信物的解密私钥只在用户设备本地生成，绝不上传服务端（阶段 6 新增）

## 阶段进度

| 阶段 | 状态 | 迁移 |
| --- | --- | --- |
| 0 纯文本 RAG CLI | ✅ | 0001 |
| 1 离线批处理 | ✅ | 0002 / 0003 |
| 2 结构化 + 混合检索 | ✅ | 0004 |
| 3 工具 + Reranker + 缓存 | ✅ | 0005 |
| 4 Memory + LangGraph | ✅ | **0006**（`memory_item` / `memory_source`） |
| 5 评估与可观测 | ✅ 已封板 | 0007（`faithfulness`） |
| 6 地图相册 + 跨端 + 隐私分享 | ✅ 已交付（真机验证待人工） | — |
| 7 语义抽象 + 群体推荐 + 可验证断言 | ✅ 已收口（提交 `b970dad`） | — |
| 8 多 Agent 引用审计 | ⛔ 已关闭（提案，非产品路径） | — |
| 9 轨迹回放 | ✅ 已交付 | — |
| 10–15 双入口 / 双主题 / 分享链路 | ✅ 已交付（多项待人工验证） | — |
| 16 门禁与真相 | ✅ 本机验证（CI 首跑待推送） | **0009**（删 `photo.thumb_url`） |

## 阶段 16 要点（门禁与真相）

- 守卫：`tests/test_layering.py` 覆盖 tools/services/models/**schemas**/**agent**；
  `schemas` 不得 import `services`（常量放 `app/schemas/policy.py`）
- 前端边界：`tools/check-import-boundaries.mjs` —— apps 不 import frontend、packages 不 import 宿主、
  跨包 import 必须在 package.json 声明、GCJ-02 常数只能在 `packages/geo` 与 `utils/coords.py`
- 语义标签：唯一源仍是 `app/utils/semantic_tags.py`，跑 `tools/gen_semantic_tags.py` 生成两份 TS；
  `make guards` / CI 用 `--check` 抓漏跑。`semantic_tags.yaml` 已删
- 运行时：`AGENT_RUNTIME` 默认 **graph**；重试次数唯一来源 `Settings.agent_generate_retries`；
  两套编排的完整事件序列与重试次数由 `test_graph_runtime.py` 钉死
- 导回：`POST /trips` 接受 `started_on`（当地日期）；`assign_photos_to_trip` 不用 None 覆盖已知日期；
  前端补坐标必须在 `assignTripPhotos` **之前**
- 文档：`docs/stages/README.md` 四态（已验证 / 未验证 / 未开始 / 已关闭），
  `tools/check_stage_index.py` 校验；`docs/` 的「完成」不再等于「验证过」
- 已删除：`showcase/`、`agent/thin.py` + `privacy/receipt.py`（481 行，从未接线）、
  `SessionStore`、4 个死 flag、6 个死配置

## 阶段 7 要点（W7-1 ~ W7-3）

- 文档：`docs/stages/stage7-research.md`。**不宣称联邦学习** —— 隐私收益来自
  语义抽象 + 序列截断 + k-匿名门禁三重叠加，不是参与方数量
- W7-1：`app/utils/semantic_tags.py` 是六类词表**唯一真相源**（前后端共用，顺序即契约）。
  词表两类词：**单字负责泛化、整名负责纠偏**，靠 `_break_tie()` 的平票修正
  （命中词更长者胜出）生效 —— 所以整名必须比它要压过的单字更长
- W7-1：两个"序列"不同 —— `semantic_sequence_for_trip()` **不合并**相邻重复（展示用），
  `extract_sequences()` **必须合并**（挖掘用 `support`）。拿错方向模式库会碎
- W7-2：`POST /api/v1/recommend/semantic`，`K_ANONYMITY=5`。门禁**逐模式**判定，
  不是全局样本量。`support` 绝不出网（`SemanticRecommendation` 结构上就不带它）
- W7-2 接口语义：未达标 → **200 + `tag=None`**；开关关 → **404**；
  非法标签静默丢弃，全丢光 → **422**
- W7-3：颜色是密码学语义 —— 绿=证实 / 红=证伪 / 灰=未证实。
  **链未锚定是灰色不是红色**（没拿到证伪证据就不能涂红）
- 改词表必须同步升 `SEMANTIC_TAG_VERSION`，然后跑 `python tools/gen_semantic_tags.py`
  重新生成两份前端常量（当前 `sem-2026.2`）。阶段 16 起**不再有** `core/config.py::semantic_tag_version`
  与 `app/data/semantic_tags.yaml` —— 那两处是已删除的第二、第三真相源
- `POST /api/v1/trips` 建的是 `source=manual` 的空行程；**重建只删 cluster 的行**，
  用户输入不能被派生数据的重建冲掉

## 阶段 6 要点（W6-1 ~ W6-5）

- W6-4 = **后端拉普拉斯地理模糊**（`app/privacy/`，孤立点、独立抽样、`radius` 是承诺半径、
  开关关返回 404）+ **前端 TrailShare 信物移植**（`services/crypto|share/`，协议 ts-2026.4）
- 阶段 6 四个 flag：`photo_import` / `map_album` / `native_bridge` / `privacy`（默认全关）
- 信物私钥红线落点：`pages/SharePage/identity.ts`（localStorage 种子，无网络调用）
- 地图渲染规则：`radius > 0` → Circle 半透明圆不画 Marker；WGS84→GCJ-02 前端
  `pages/MapPage/coords.ts` 与后端同算法（天安门向量测试锁死）
- 前端测试在 `src/**/*.test.ts`（co-located，node 环境）；`npm test` 141 passed
- 高德 JS key 在 `frontend/.env.local`（值来自根 `.env` 的 `AMAP_JS_KEY`，被 gitignore）

## 阶段 5 看板（W5-3）

- `make dashboard` → `backend/data/dashboard.html`（单文件静态 HTML，双击即开）
- `make dashboard-demo` → `data/dashboard.demo.html`（无库时验证看板本身，标题标注 DEMO）
- 纯函数在 `app/agent/eval/dashboard.py`，DB 脏活在 `app/scripts/export_dashboard.py`
- 卡片取**最新一组**不是跨版本平均；P50/P95 同现；与 `--compare` 同源复用聚合

## 运行时

- `AGENT_RUNTIME=pipeline`（默认，阶段 0~3）/ `graph`（阶段 4 LangGraph），
  统一入口 `app/agent/runtime.py::build_agent()`；两种运行时对外接口一致
- 检索融合：`RETRIEVAL_FUSION=weighted`（默认）/ `rrf`
- 记忆总开关 `MEMORY_ENABLED=true`（主干能力，不是 feature flag）

## 已知坑

- **pytest exit code 会被安全钩子置 1**（末尾 `[safe-delete]...` 那行不是失败）。
  判定是否通过**只认 `--junit-xml` 里的 `failures="0"`**
- 前端测试（`src/**/*.test.ts`）：
  - `fireEvent.change(input, { target: { files } })` **静默失效**（`input.files` 只读无 setter），
    必须 `Object.defineProperty(input, 'files', { value: [file], configurable: true })`
  - **jsdom 的 `File` 没有 `.text()`**（浏览器有），不补就只能测到"读不出"这条路径
  - `waitFor` 要等**结果出现**，不要等"loading 消失"——后者在异步开始前就已成立
- 先 `import app.services` 曾触发循环导入，已用 PEP 562 惰性导出修掉；
  新增 `app/services/*` 时留意不要再引回 `app.agent.rag` 的 eager import
- `SELF_CHECK_FAILED_ANSWER` 含非软句 → `citation_rate()` 判 0.0（self_check 不受影响），
  阶段 5 做聚合归因时需处理
- 历史对话进上下文前必须剥掉 `[n]`（`context_builder._fit_history`），
  否则上一轮编号会造成本轮"引用越界"误拒答

## 版本基线（2026-11-19）

- `apps/` 与 `packages/journey-transfer|photo-source` 已于 **d5b19b8** 纳入 git
  （226 files，+42,023），随后 **5056bb8** 更正阶段 15 报告；`main` 已推 origin，`ahead=0`。
- 此后「零改动」声明必须用 `git diff` 证明，不再接受 mtime 自证。
- 本机 Git 曾报 `schannel: CRYPT_E_NO_REVOCATION_CHECK`，已在仓库本地设
  `http.schannelCheckRevoke=false` 才推得动；换机器/网络时可能需要同样设置。
- 阶段 10–15 的交付报告都在 `docs/stages/`，索引见 `docs/stages/README.md`。
