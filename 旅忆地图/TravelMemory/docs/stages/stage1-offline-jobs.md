# 阶段 1：离线批处理 + 照片元数据

> **一句话**：照片本身没有文本，而检索只能检索文本——所以必须先"造出文本"，
> 并且**造的过程要能重跑、要能断点续跑、要能卡住预算**。这三件事就是 W1-1 / W1-2 / W1-3。

---

## 0. 读法

第 2 节是这一阶段最值钱的部分（"批处理"的思维方式），第 3~5 节按工作块讲实现，
第 8 节给你能直接复制执行的命令与**我这边实测的输出**，第 11 节记录了一个真实观察：
文档变多之后指标反而略降——这正是阶段 2、3 存在的理由。

---

## 1. 目标与出口条件

| 块 | 内容 | 依赖 | 出口条件 |
| --- | --- | --- | --- |
| **W1-1** | `jobs/` 框架：幂等、重试、成本上限、状态回写 | 阶段 0 | 同一任务重复执行不重复烧 token |
| **W1-2** | `caption_job`：生成 caption + ai_tags 并写回 Photo | W1-1 | 照片跑完，失败可重试，成本不超上限 |
| **W1-3** | `index_job`：按天聚合 → chunk → embedding → 写 Document | W1-1 | `make reindex` 可全量重建 |

**本阶段不碰**：地图、EXIF 解析、Capacitor（分别是 W6-1 / W6-3）。

---

## 2. 必须先懂的三个原理

### 2.1 照片是二进制：检索系统只认文本

阶段 0 建好的向量检索，输入必须是"一段文字"。而一张 JPEG 里能被直接用的只有 EXIF
（时间、GPS、相机型号）——**"这张照片拍的是龙门石窟的卢舍那大佛"这种信息，不写出来就永远不存在**。

所以阶段 1 的第一件事是让模型把照片"翻译"成两类文本：

| 产物 | 形态 | 用途 |
| --- | --- | --- |
| `caption` | 一句自然语言描述 | → 向量化，让语义检索生效 |
| `ai_tags` | 结构化标签数组（古镇 / 博物馆 / 夜景…） | → 阶段 2 结构化过滤、阶段 7 语义模式挖掘 |

### 2.2 为什么必须离线批处理，不能在线跑

在线问答时调模型：一次调用、秒级返回、失败了重试一次无所谓。
给 1000 张照片生成 caption：

- **时间**：不可能让用户等几百次请求
- **成本**：一次跑批可能花掉几十元，**失败重跑不能重花一遍**
- **稳定性**：中途网络抖一下、服务限流一次，不能全盘重来

所以批处理框架要解决三件事，这也是 W1-1 的全部内容：

```
1. 幂等    同一件事做过了就别再做      → unique(photo_id, job_type, ai_model)
2. 可重试  只有失败的才需要重做        → status=failed 且 attempts < 上限
3. 成本闸  一次跑批最多花多少          → 累计 cost_cents 超过上限就停
```

### 2.3 为什么要按"天"聚合，而不是一张照片一条文档

一张照片信息量太小："10-03 09:12 乌镇，一张船的照片"。
单条文档会带来两个问题：

1. **粒度太碎**：问"我去年秋天去过哪些古镇"，命中的是某一张船的照片，
   而不是"那趟江南古镇之行"，回答会缺失上下文；
2. **成本与噪声**：文档数 ×N → embedding 成本 ×N，且大量近似重复的文本会挤占 top-k 名额。

按天聚合后，一条文档 = 日期 + 城市 + 地点 + 当天每张照片的 caption，
语义完整、数量可控，正好对应"一天一段记忆"这个自然单位。
（设计文档写的是「按 Trip / 天聚合」：Trip 表由阶段 6 规则聚类产生，
所以阶段 1 只做"天"，阶段 6 再加一个 TripGrouper，Document 结构不用改。）

---

## 3. W1-1：jobs 框架

代码：[`backend/app/jobs/base.py`](../../backend/app/jobs/base.py)
表：[`backend/app/models/jobs.py`](../../backend/app/models/jobs.py)

### 3.1 幂等键是整个阶段的地基

```sql
UNIQUE (photo_id, job_type, ai_model)
```

这三个字段合起来回答"这件事做过了没有"：

- `photo_id` — 对谁做
- `job_type` — 做什么（caption / index）
- `ai_model` — 用哪个模型版本做

**为什么模型版本要进幂等键？**
换模型时，我们希望的是"新模型重跑一遍"，而不是"覆盖掉旧结果"——
两者同时存在才能对比效果，也符合"三个版本化"的要求。

### 3.2 一次跑批的状态机

```
         ┌──────────── 还没有任务行 ────────────┐
photo ──▶│ 建行：status=pending, attempts=0     │
         └─────────────────────────────────────┘
                        │ 认领（claim）
                        ▼
               status=running, attempts+1
                        │
             ┌──────────┴──────────┐
             ▼                     ▼
         执行成功                执行失败
      status=done             status=failed
    cost_cents += 花费        last_error=原因
                          （attempts 未超上限 → 下次跑批自动重试）

已 done 的行不会被再挑出来，除非 --force（force 把它重置回 pending）。
```

### 3.3 三个实现细节

**(1) 认领用 `FOR UPDATE SKIP LOCKED`**

```python
.select(IngestJob).where(...).with_for_update(skip_locked=True)
```

多个 worker 同时跑批时，被别人锁住的行直接跳过，不会重复消费。
单进程时这个语法没有副作用，多进程时它就是"不重复烧钱"的关键。

**(2) 单条失败不能中断整批**

```python
try:
    cost = await self.process_one(job.photo_id)
except Exception as exc:
    self._mark_failed(job, str(exc))     # 记 failed，继续下一条
```

跑批跑到第 800 张时因为一张图坏了而整体崩溃，是不能接受的。

**(3) 成本闸放在循环里**

```python
if budget > 0 and report.cost_cents >= budget:
    report.stopped_by_budget = True
    break                                # 剩下的下次跑批继续
```

### 3.4 子类只写业务

`BaseJob` 子类只要实现：`candidate_photo_ids()`（挑谁）+ `process_one()`（做什么）。
建行、认领、重试、成本闸、状态回写全部由基类完成——
所以 W1-2 和 W1-3 各自只有 60~90 行。

---

## 4. W1-2：caption_job

代码：[`app/jobs/caption_job.py`](../../backend/app/jobs/caption_job.py)、[`app/core/vision.py`](../../backend/app/core/vision.py)

### 4.1 视觉模型也要抽象一层

和阶段 0 的 LLM / Embedding 同样的套路：

| provider | 行为 |
| --- | --- |
| `openai` | OpenAI 兼容多模态接口，缩略图以 base64 data URI 内联发送 |
| `mock` | 本地规则生成（用时间 + 地点线索拼描述，关键词表映射标签） |

`auto` = 配了 Key 走真实 VLM，没配走 mock。

**为什么阶段 1 需要 mock**：导入链路（W6-1）还没做，库里没有真实图片字节，
真跑 VLM 无从谈起；有了 mock，"批处理 → 写回 → 入索引"整条链路可以先验证。
再次强调：**mock 的输出不能用于效果评估**。

### 4.2 写回 Photo 的哪些字段

```
caption        ← 模型生成的一句话
ai_tags        ← 标签数组
ai_status      ← pending → done（下一环节靠它挑候选）
ai_model       ← 模型版本（便于追溯"这段话是谁写的"）
ai_updated_at  ← 时间
```

`ai_status` 是**连接两个任务的接口**：index_job 只挑 `ai_status = done` 的照片，
所以没有 caption 的照片不会被索引——避免出现"空文档"。

---

## 5. W1-3：index_job

代码：[`app/jobs/index_job.py`](../../backend/app/jobs/index_job.py)、[`app/services/day_digest.py`](../../backend/app/services/day_digest.py)

### 5.1 一天一条文档

```
2023-07-10 的行程记录（青海、甘肃、青海湖、茶卡盐湖、张掖丹霞），当天共 3 张照片：
- 09:12 于青海、甘肃青海湖：这张照片拍摄于 2023-07-10 09:12，地点在青海湖……
- 14:30 于青海、甘肃茶卡盐湖：……
```

元数据带上后续过滤需要的字段：`date / city / places / themes / photo_count / photo_ids`。
阶段 2 做"时空过滤"时直接读这些字段，不必改表。

### 5.2 同一天只重建一次

一次跑批里，同一天的 3 张照片都会成为候选，但日文档只能重建一次，否则 embedding 白烧 3 遍：

```python
if cache_key in self._built_days:
    return 0.0          # 这一天本轮已经重建过
```

### 5.3 `make reindex` 就是 `--force`

换 embedding 模型后必须全量重建：候选忽略"已完成"状态，从头再跑一遍。
因为 Document 的唯一键是 `(source_type, source_id, chunk_index)`，
重建是**覆盖**而不是新增，不会产生重复文档。

---

## 6. 本阶段新增的数据表

| 表 | 关键字段 | 设计点 |
| --- | --- | --- |
| **Photo** | platform, asset_id, lat/lng, coord_system, taken_at, taken_at_local, caption, ai_tags, ai_status, ai_model, hash, deleted_at | `unique(platform, asset_id)` 去重；原图不入库只存标识；`coord_system` 显式标注；`taken_at_local` 用于按拍摄地分天 |
| **IngestJob** | photo_id, job_type, ai_model, status, attempts, last_error, cost_cents, finished_at | `unique(photo_id, job_type, ai_model)` = 幂等键；`attempts` 控制重试；`cost_cents` 支撑成本闸 |

---

## 7. 文件清单

| 文件 | 作用 | 对应知识点 |
| --- | --- | --- |
| `app/jobs/base.py` | 框架：幂等键 / 认领 / 重试 / 成本闸 / 状态回写 + 统一 CLI | 批处理框架 |
| `app/jobs/caption_job.py` | W1-2：照片 → caption + ai_tags | 多模态调用 |
| `app/jobs/index_job.py` | W1-3：按天聚合 → chunk → embedding | 聚合粒度设计 |
| `app/core/vision.py` | 视觉模型封装（openai / mock） | provider 抽象 |
| `app/services/day_digest.py` | 一天的照片 → 一段文本 + 元数据 | 文档构建 |
| `app/models/photo.py` / `jobs.py` | 两张新表 | 数据建模 |
| `alembic/versions/0002_*.py` | 建 Photo + IngestJob | 迁移 |
| `app/scripts/seed_photos.py` | 合成演示照片（仅元数据） | 无真实数据时的替身 |
| `data/demo_places.jsonl` | 补上经纬度 | 为阶段 6 地图留数据 |

---

## 8. 跑起来验证

```bash
cd backend
alembic upgrade head                 # 建 Photo + IngestJob
python -m app.scripts.seed_photos    # 合成演示照片
python -m app.jobs.caption_job --limit 200
python -m app.jobs.index_job --limit 500
```

**我这边实测的输出**（129 张演示照片，离线 mock 模式）：

```
本次合成 129 张演示照片（platform=demo，仅元数据，无图片文件）

# 第一次跑（限 500，实际把剩下的 69 条跑完）
caption(mock-vision@v1)：执行 69 条，成功 69，失败 0，跳过 0，成本 0.000 分，用时 900ms
# ★ 立刻再跑一次 —— W1-1 的出口条件
caption(mock-vision@v1)：执行 0 条，成功 0，失败 0，跳过 0，成本 0.000 分，用时 88ms
# --force（换模型场景）
caption(mock-vision@v1)：执行 129 条，成功 129，失败 0，跳过 0，成本 0.000 分，用时 972ms

index(hashing-1024@v1)：执行 129 条，成功 129，失败 0，跳过 0，成本 0.000 分，用时 1769ms
index(hashing-1024@v1)：执行 0 条，成功 0，失败 0，跳过 0，成本 0.000 分，用时 77ms   ← 幂等

日文档数：43  |  全部文档数：70（27 条演示语料 + 43 条日文档）
```

（成本是 0 分，因为当前用的是本地 mock 视觉模型与本地哈希向量；
换成真实模型后同样的命令会显示真实花费。）

单元测试：`pytest` → **49 passed**（比阶段 0 多了 12 条，全部跑在真实数据库上）：
幂等、失败重试、成本闸、force 重跑、caption 写回、日文档聚合、reindex 不产生重复。

---

## 9. 易错点

1. **幂等键一定要包含模型版本**，否则换模型时旧结果会被悄悄覆盖，没法对比。
2. **单条失败必须吞掉**，否则一张坏照片会让整批前功尽弃。
3. **候选查询要用 `taken_at_local` 而不是 `taken_at`**：一天的定义以拍摄地为准。
4. **同一天只重建一次**：否则这一天的 embedding 被重复烧 N 遍。
5. **测试里不要改 `EMBEDDING_DIM`**：真实库里的列是迁移时按当时的维度建的，
   改成 256 会直接 `DataError`，而且因为是同一个事务，后续语句会连锁失败、
   报"current transaction is aborted"，很难一眼看出根因。
6. **测试要用独立用户**：日文档按 `user_id + 日期` 聚合，共用演示用户会把演示照片算进来。
7. `--force` 是"全量重跑"的意思，**不是"忽略失败"**——失败的行本来就每次都会被重试。
8. 换 embedding 模型后必须 `make reindex`，否则库里新旧向量混在一起（同阶段 0 的规矩）。
9. **唯一键必须覆盖"隔离维度"**（本阶段真实踩到）：`Document` 原来的唯一键是
   `(source_type, source_id, chunk_index)`，两个用户同一天的日文档 `day/2025-10-03#0`
   会撞在同一行上，后写的覆盖先写的，而且 `user_id` 不在更新字段里，
   被覆盖那一行就"变成"了别人的文档。修法是把 `user_id` 加进唯一键（迁移 0003）。
   教训：**只要查询按某个维度过滤，唯一键就该包含这个维度**。

---

## 10. 动手练习

1. 跑两次 `caption_job`，观察第二次"执行 0 条"——然后手动把某一行 `ingest_job.status`
   改成 `failed` 再跑一次，看它是不是**只有那一条**被重跑。
2. 把 `--limit 5` 跑一遍，看报告里的 `processed` 是不是 5、剩下的下次还能续上。
3. 改 `JOB_MAX_COST_CENTS` 成一个很小的数（比如在你真实调用下相当于只能跑 2 条），
   看 `stopped_by_budget` 是否变 True。
4. 手动给一张照片改 `caption`，重跑 `index_job --force`，看日文档正文是否跟着变。

---

## 11. 一个真实观察：文档变多之后，指标反而略降

加入 43 条日文档后，同一批评测：

| | 加入前 | 加入后 |
| --- | --- | --- |
| Recall@5 | 0.750 | 0.750 |
| MRR | 0.453 | 0.432 |

原因很直白：**候选变多了，噪声也变多了**。"我去年秋天去过哪些古镇？"
现在要和 43 条日文档竞争 top-k，某些日文档只因为字面重合就挤进了前排。

这不是 bug，而是"只有向量召回"的天然上限，也正好解释了后面两个阶段为什么必须做：

- **阶段 2**：先用时间 / 地点 / 主题过滤掉绝大部分不相关文档，再向量检索
- **阶段 3**：对召回结果做重排（Reranker），把真正相关的顶上来

记住这条链路的因果，后面学过滤和重排时就不会觉得是"锦上添花"。

---

## 12. 下一阶段

阶段 2：结构化 + 向量混合检索（W2-1 Query 结构化解析 / W2-2 时空过滤 + pg_trgm 全文 / W2-3 SSE 流式）。
核心变化：让"去年秋天""古镇"这类表达**先用条件过滤、再向量召回**，
把上面观察到的 MRR 损失补回来。
