# 旅忆地图 TravelMemory

> 以个人照片地图记忆为数据源的旅行记忆 Agent。地图相册是数据底座与可视化，Agent 是核心。

完整设计见 **[旅忆地图 TravelMemory.md](./旅忆地图 TravelMemory.md)**，本文档只讲如何跑起来。
项目约定见 [PROJECT.md](./PROJECT.md)，历史文档见 [docs/archive/](./docs/archive/)。
**阶段复盘与原理讲解见 [docs/stages/](./docs/stages/)**（每完成一个阶段新增一份，当前交付到阶段 16；索引带四态：已验证 / 未验证 / 未开始 / 已关闭）。

---

## 快速上手

```bash
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements.txt   # Windows
cp .env.example .env          # 填 LLM_API_KEY / LLM_BASE_URL
make up                       # 启动 PostgreSQL(PostGIS + pgvector)
make migrate                  # 建表 + 建扩展
make check                    # 环境自检：扩展 / 向量维度 / 脏向量
make seed-demo                # 导入演示语料（幂等）
make cli                      # 阶段 0：纯文本 RAG 问答
```

### 一键启动（Windows）

双击仓库根目录的 `start-dev.bat`，等价于下面四条命令各开一个窗口。
**端口就绪后会自动打开对应页面**（`main` 只开 5173；`apps` 开 5174 / 5175），
最多等 30 秒，超时只提示、不阻塞。

```bat
start-dev.bat          :: 后端 8000 + 主应用 5173 + 轨迹演示 5174 + 分享旅程 5175
start-dev.bat main     :: 只起后端 + 主应用（地图 / 问答）
start-dev.bat apps     :: 只起两个独立小应用（不需要后端）
start-dev.bat check    :: 只体检：虚拟环境 / node_modules / 5432 / 四个端口
```

**为什么是三个前端端口，而不是一个**：`5173` 主应用、`5174` 轨迹演示、`5175` 分享旅程。
端口不同 = **origin 不同**，iframe 里的子应用读不到主应用的 `localStorage`（身份种子、
好友列表都在那里）。合并成一个端口会让「小应用碰不到主应用数据」这条隐私边界直接消失。

**为什么一个端口有两个入口**：`5174` / `5175` 各自构建两个 HTML —
`http://localhost:5174/` 是独立版（CSP 只允许 `'self'`，全程零网络），
`http://localhost:5174/embed.html` 是嵌入版（额外放行高德底图）。
同一份代码、两套安全策略，不是两个版本。

> 未配置 API Key 时会自动降级为**离线占位模式**（本地规则作答 + 本地哈希向量），
> 此时只能验证链路是否通畅，回答质量不代表真实效果（且需把 `RETRIEVAL_MIN_SCORE` 调低，见下文）。
>
> Windows 用户请先读 [Windows 环境注意事项](#windows-环境注意事项实测于-windows-11-家庭版--powershell)
> （没有 `make` 的替代命令、镜像方案、alembic 编码坑）。

**验证清单（每次改动后都该跑；阶段 16 起多数已进 CI）**：

| 步骤 | 命令 | 期待结果 |
| --- | --- | --- |
| 扩展与表 | `make check` | 三个扩展齐备，`document` 表有数据 |
| 有证据问答 | `make cli` 后问「我去年秋天去过哪些古镇？」 | 带 `[n]` 引用的回答，下方列出证据与来源 |
| 无证据拒答 | 问「我在南极看过企鹅吗？」 | 明确拒答，不编造 |
| 检索指标 | `make eval` | 输出 Recall@5 / MRR |
| 生成指标 | `python -m app.agent.eval.runner --full` | 引用率 1.00、拒答正确 1.00 |
| 归因对比 | `make eval-compare` | 按 prompt_version / embedding_model 输出对比表 |
| 单元测试 | `make test` + `npm run test` | 后端 643 / 前端 954 全绿（CI 另起 Postgres，不跳过 DB 用例） |

**阶段 1 已在阶段 0 之上跑通**（照片 → caption → 日文档），验证命令：

```bash
make seed-photos                      # 合成演示照片（仅元数据，无图片文件）
cd backend && python -m app.jobs.caption_job --limit 200
cd backend && python -m app.jobs.index_job --limit 500
cd backend && python -m app.jobs.caption_job --limit 200   # ★ 第二次应显示"执行 0 条"
```

原理讲解见 [docs/stages/stage1-offline-jobs.md](./docs/stages/stage1-offline-jobs.md)。

**阶段 2 已在阶段 1 之上跑通**（中文时间解析 + 结构化过滤 + 混合检索 + SSE 流式问答页）：

```bash
make eval-time             # W2-1：中文时间表达解析准确率（不需要数据库）
make migrate               # 建阶段 2 的 GIN / pg_trgm 索引
make index-batch           # 重建日文档，写入 bbox（min/max 经纬度）
make eval-ablation         # W2-2 出口条件：混合检索 Recall@5 不低于纯向量

make web-install           # 首次：装前端依赖
make dev & make web-dev    # 后端 8000 + 前端 5173，浏览器打开 http://localhost:5173
```

原理讲解见 [docs/stages/stage2-structured-retrieval.md](./docs/stages/stage2-structured-retrieval.md)。

**阶段 3 已在真实库上验收通过**（工具调用 + Reranker + 缓存）：

```bash
docker compose --profile stage3 up -d   # 阶段 3 起需要 Redis
make migrate                            # 0005：HNSW 索引
make eval-tools                         # W3-1 出口：工具选择准确率 ≥ 0.9（离线可跑）
make eval-ablation                      # W3-4 出口： Recall@5 不下降
make cache-stats                        # W3-3 出口：POI/路线缓存命中率 ≥ 0.6
```

真实数值（k=5，26 条用例）：纯向量 Recall@5 **0.619** → +结构化过滤 **0.809** → +词面混合 **0.762**；
工具选择准确率 **22/22 = 1.0000**；`pytest` **204 passed**。

原理讲解见 [docs/stages/stage3-tools-rerank.md](./docs/stages/stage3-tools-rerank.md)。

**阶段 4 已完成**（三层记忆 + LangGraph + 融合策略对比）：

```bash
make migrate                            # 0006：memory_item / memory_source
AGENT_RUNTIME=graph make dev            # 切到图运行时（默认 pipeline，可回滚）
python -m app.agent.eval.runner --ablation   # 四档消融 + 出口条件自动判定
```

真实大模型下的消融（qwen3.7-plus + text-embedding-flash，k=5，26 条）：

| 档位 | Recall@5 | MRR |
| --- | --- | --- |
| 纯向量 | 0.833 | 0.718 |
| +结构化过滤 | 0.857 | 0.722 |
| **+词面召回（weighted，默认）** | **0.857** | **0.778** |
| +RRF 融合 | 0.833 | 0.679 |

**结论：真实语义向量下 RRF 不敌 weighted，MRR 掉 0.1** —— 词面覆盖率只有 1.0/0.5/0.0 三档，
RRF 的"只看排名"会放大这种离散噪声。默认保持 `RETRIEVAL_FUSION=weighted`；
RRF 已实现加权 + 分档排名，留待三路（+记忆）时再用数据决定是否切换。

原理讲解见 [docs/stages/stage4-memory-graph.md](./docs/stages/stage4-memory-graph.md)。

**阶段 5 已完成**（W5-1 评测扩充 + W5-2 归因对比 + W5-3 可观测看板）：

```bash
python -m app.agent.eval.runner --full --limit 5          # 小样本全量（EVAL_LLM_JUDGE=true 时附忠实度）
python -m app.agent.eval.runner --compare                 # prompt_version + embedding_model 归因对比表（含环比 ↑/↓）
make dashboard                                            # W5-3：导出单文件静态看板 → backend/data/dashboard.html
make dashboard-demo                                       # 无数据库时验证看板本身（页面标题注明 DEMO）
```

W5-3 看板：汇总卡片（总用例数 / 平均 Recall / 平均引用率 / 忠实度 / P50-P95 延迟）+
归因对比表（↑↓ 环比）+ 两张 ECharts（质量趋势、P50/P95 延迟分布）。
单文件静态 HTML，双击即开；聚合与渲染是纯函数（`app/agent/eval/dashboard.py`），
不连库也能单测。卡片取**最新一组**而不是跨版本平均 ——
一个数如果不对应任何一次真实运行，就不该出现在看板上。

评测集 29 条（22 rag + 4 refusal + 1 recommend + 2 plan；阶段 5.2 已把规划题从 rag 改判）；
拒答口径收口为 `metrics.py` 纯函数（拒答不双扣：引用率/忠实度记 None，拒答正确性单独判；
recommend/plan 两个事实性指标都不计入）；`SelfCheckNode` 对 plan/recommend 同样豁免逐句引用
校验（阶段 5.3，规则意图分类 `app/utils/intent.py`，编号越界照查）；
`--compare` 聚合改为 Python 纯函数（P95 最近秩），看板复用同一套口径（额外算 P50），`pytest` **359 passed**
（本机 PostgreSQL 在线，10 个 db 用例实跑通过；无库机器上为 335 passed / 10 skipped，
跳过项均为需要真实 PostgreSQL 的用例；测试进程由 conftest 的 autouse fixture 强制
`LLM_PROVIDER=mock` + `EMBEDDING_PROVIDER=hashing`，不碰真实模型）。

原理讲解见 [docs/stages/stage5-eval-observability.md](./docs/stages/stage5-eval-observability.md)。

按阶段启用更多服务：

```bash
docker compose --profile stage3 up -d                    # + Redis（阶段 3 起）
docker compose --profile stage3 --profile stage6 up -d   # + MinIO（阶段 6 起）
```

---

## 三条不可协商的约定

1. **照片不搬家**：原图永不上云，只取 EXIF 元数据 + 本地生成缩略图。
2. **WGS84 存储，GCJ-02 展示**：所有位置字段显式标注 `coord_system`。
3. **无证据必须拒答**：事实性回答引用率 100%，缺失即判失败。

---

## 开发顺序

阶段严格串行：**上一阶段的出口条件未满足，不进入下一阶段**。
每个阶段拆成若干**工作块**，块之间依赖独立即可并行推进；单人开发按块号顺序完成。
详细的块内容与出口条件见 [设计文档第十一章](./旅忆地图 TravelMemory.md)；前端地图（图层栈、动画、性能红线）见 [第六章](./旅忆地图 TravelMemory.md)。

| 阶段 | 目标 | 工作块 |
| --- | --- | --- |
| **0** | 纯文本 RAG CLI | W0-1 数据底座 / W0-2 RAG 管道 / W0-3 评测与可观测 |
| **1** | 离线批处理 + 照片元数据 | W1-1 jobs 框架 / W1-2 caption+ai_tags / W1-3 索引任务 |
| **2** | 结构化 + 向量混合检索 | W2-1 Query 解析 / W2-2 时空过滤+全文 / W2-3 SSE 流式 |
| **3** | 工具调用 + Reranker + 缓存 | W3-1 tools 框架 / W3-2 地图工具 / W3-3 Redis / W3-4 Reranker+HNSW |
| **4** | Memory + LangGraph | W4-1 三层记忆 / W4-2 LangGraph 迁移 / W4-3 RRF+压缩 |
| **5** | 评估与可观测 | W5-1 评测扩充 / W5-2 归因对比 ✅ 第一段已完成；W5-3 看板待下一段 |
| **6** | 地图相册 + 跨端 | W6-1 导入链路 / W6-2 地图与行程 / W6-3 Capacitor / W6-4 隐私分享 / W6-5 降级测试 |
| **7** | 扩展方向（可选，独立分支） | W7-1 语义与隐私 / W7-2 模式挖掘与推荐 / W7-3 可验证断言 |

**阶段 7 可整块放弃**，不影响阶段 0～6 的交付。

---

## 常用命令

```bash
make test               # 全量测试
make test-degradation   # ★ 断言扩展开关全关时主流程不受影响
make eval               # 跑评测集
make eval-time          # 中文时间表达解析准确率（不需要数据库）
make eval-ablation      # 三档消融：纯向量 / +过滤 / +词面
make eval-compare       # 按 prompt_version / embedding_model 输出对比表
make dashboard          # 导出可观测看板（单文件静态 HTML）
make reindex            # 换 embedding 模型后必须执行
make resemantic         # 换语义标签字典后必须执行（阶段 7）
make lint               # ruff + mypy
```

---

## Windows 环境注意事项（实测于 Windows 11 家庭版 + PowerShell）

### 1. 没有 `make` 怎么办

Windows PowerShell 默认不带 `make`。三选一，推荐第一种：

```powershell
winget install GnuWin32.Make     # 装完重开终端即可用 make
# 或者：用 Git Bash / WSL 执行 make
# 或者：直接照下表手动执行等价命令（所有命令都在项目根目录执行）
```

| Makefile 目标 | 等价的原始命令（PowerShell） |
| --- | --- |
| `make up` | `docker compose up -d --build` |
| `make migrate` | `cd backend; alembic upgrade head; cd ..` |
| `make check` | `cd backend; python -m app.cli --check; cd ..` |
| `make seed-demo` | `cd backend; python -m app.scripts.seed_demo; cd ..` |
| `make cli` | `cd backend; python -m app.cli; cd ..` |
| `make test` | `cd backend; python -m pytest; cd ..`（**必须带 `python -m`**，否则报 `No module named 'app'`） |
| `make eval-tools` | `cd backend; python -m app.agent.eval.runner --tools; cd ..` |
| `make cache-stats` | `cd backend; python -m app.scripts.cache_stats; cd ..` |
| `make eval` | `cd backend; python -m app.agent.eval.runner; cd ..` |
| `make eval-time` | `cd backend; python -m app.agent.eval.runner --time; cd ..` |
| `make eval-ablation` | `cd backend; python -m app.agent.eval.runner --ablation; cd ..` |
| `make eval-compare` | `cd backend; python -m app.agent.eval.runner --compare; cd ..` |
| `make dashboard` | `cd backend; python -m app.scripts.export_dashboard; cd ..` |
| `make dashboard-demo` | `cd backend; python -m app.scripts.export_dashboard --demo --out data/dashboard.demo.html; cd ..` |
| `make web-dev` | `cd frontend; npm run dev; cd ..` |

> 容器起来后建议先等 10~15 秒再跑迁移，等 healthcheck 变 `healthy`。

### 2. Docker 镜像与加速器

- 本项目**必须**用自建镜像（`docker-compose.yml` 里是 `build: ./backend/docker/db`），
  基础镜像为 `pgvector/pgvector:pg16` + apt 装 `postgresql-16-postgis-3`。
  原因见 [`backend/docker/db/Dockerfile`](./backend/docker/db/Dockerfile) 里的注释：
  原方案 `postgis/postgis:16-3.4`（Debian 11）的 PostgreSQL 官方源已 404，装不上 pgvector。
- **所有 YAML 必须存为「UTF-8 无 BOM」**，带 BOM 时 Docker 报 `invalid trailing UTF-8 octet`。
- 加速器不稳定时可多备几个，在 Docker Desktop → Docker Engine 配置里替换 `registry-mirrors`：
  `https://docker.1ms.run`、`https://docker.m.daocloud.io`、`https://hub.rat.dev` 等（会偶发 403/限流）。

### 3. Alembic 的编码坑（已在本仓库修复）

Alembic 用 `encoding="locale"` 读 `alembic.ini`，中文 Windows 的 locale 是 GBK，
文件里只要有 UTF-8 中文就会报 `UnicodeDecodeError: 'gbk' codec ...`。

**仓库里的 `backend/alembic.ini` 已改为纯 ASCII 注释**，不要再往里面加中文；
若你本地已经被转成了 GBK，直接把该文件覆盖回 UTF-8 版本即可。

### 4. 离线占位模式（没配 API Key）必须调阈值

哈希向量的余弦相似度普遍只有 0.05~0.13，沿用真实模型的 `RETRIEVAL_MIN_SCORE=0.30`
会把证据全过滤掉，表现为**所有问题都拒答**。离线模式请在 `.env` 里改成：

```
EMBEDDING_PROVIDER=hashing
EMBEDDING_MODEL=hashing
EMBEDDING_MODEL_VERSION=hashing-1024@v1
RETRIEVAL_MIN_SCORE=0.05
```

（CLI 启动时会检测到这种组合并主动提醒。）

### 5. 退出 CLI

输入 `exit`（或 `quit` / `q`）退出。`Ctrl+C` 也能退，只是会多出 `CancelledError`
之类的 asyncio 噪音——已在代码中捕获，不影响功能。

### 6. 本地 MinIO 走 HTTP，不开 HTTPS

本地开发用 **HTTP**（`http://localhost:9000`，Console 是 `http://localhost:9001`）；
> 阶段 16 删除了 `minio_*` 三个空配置字段（代码里从未读取）。本节保留作部署备忘，
> 但当前产品链路**不走对象存储**：缩略图路径存在 `photo.local_thumb_path`，原图不落服务端。

**生产部署时再启用 HTTPS**，届时需把正式证书挂载到容器的 `~/.minio/certs`。

两个容易踩的点：

- `docker-compose.yml` 的 MinIO 服务**不要**挂载证书目录、`MINIO_SERVER_URL` 保持 `http://`，
  端口是 `9000:9000` 与 `9001:9001`（不是 HTTPS 端口）。
- `.env` 里的 `MINIO_ENDPOINT` **必须显式写 `http://`**：不写协议时 `boto3` / `minio-py`
  默认走 HTTPS，而本地是自签名证书，会直接报 SSL 验证失败。

浏览器打开 `http://localhost:9001` 应当**直接进登录页**——如果弹出"您的连接不是私密连接"，
说明某处仍指向 `https://`，按上面两条排查，不要靠手动点"高级 → 继续"绕过去。

---

## 目录速查

```
frontend/   主应用：React + TS + Capacitor（Web / Android / iOS 同一套代码）
apps/
  track-studio/          轨迹演示：可单独打开（零后端），也可被主应用 iframe 嵌入
  trailshare-standalone/ 分享旅程：独立版全程零网络请求（CSP 只放行自身）
packages/
  amap-loader/ 高德 JS API 2.0 加载原语：去重、超时、script 复用、安全密钥
            —— 主应用与小应用共用；失败策略（抛错 / 降级）仍各自保留
  geo/      坐标与地图基础：WGS84↔GCJ-02、Web 墨卡托、球面距离（纯函数，零依赖）
            —— 前端**唯一**的坐标转换入口，主应用与小应用共用
  trail/    轨迹回放内核：样条、里程、路段、相机、Canvas 渲染（纯逻辑，不含 React）
  journey-transfer/ 旅程包的导入/导出线格式（与后端 `journeyToTrip` 对应）
  photo-source/     照片来源抽象（系统相册 / 文件选择器），小应用共用
backend/app/
  api/ 接口层        schemas/ 接口契约      services/ 业务层
  agent/ Agent 运行时（pipeline / graph / rag / memory / eval / prompts）
  jobs/  离线批处理   tools/ 工具适配器
  privacy/ 隐私机制（纯函数）：拉普拉斯地理模糊
  models/ 表结构      core/ 配置 / LLM / Redis 缓存 / feature flags
research/   实验脚本与结果（不参与部署）
deploy/     Nginx 与生产 Compose
tools/      真机验收工具：帧率曲线 / 回放与卡片复验 / CPU profile / 地图三处出图
            （都自包含，会自己拉起预览 + 无头浏览器；不参与构建与部署，别和 backend/app/tools 混了）
docs/
  stages/   阶段复盘与原理讲解（stage0~16）
  images/   验收截图
  design/   设计稿与提案      archive/ 历史文档
```

**JS 侧的依赖安装只需在仓库根跑一次**（npm workspaces）：

```bash
npm install            # 同时装好 frontend / apps/* / packages/*
npm run typecheck      # 全部工作区的类型检查
npm run test           # 全部工作区的测试（当前 954 条）
npm run check:boundaries   # 跨工作区依赖边界（阶段 16 新增，CI 也跑）
python tools/check_stage_index.py       # 阶段索引一致性
python tools/gen_semantic_tags.py --check  # 语义标签生成物是否最新
```

后端仍是 Python，走 `make` 与 `requirements.txt`，与 JS 工作区互不影响。

依赖方向严格单向：`agent → nodes → tools → services → models`。
