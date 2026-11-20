# 阶段复盘文档

每完成一个阶段，就在这个目录里新增一份文档。文档不是"提交记录"，而是**教学材料**：
目标是你读完就能像自己动手写了一遍那样，讲清楚这个阶段为什么这么设计、每个环节的原理是什么。

## 命名与结构约定

文件名规则：`stage<编号>-<主题>.md`；配套的交付报告统一命名为
`stage<编号>-delivery-report.md`，与阶段文档放在同一目录。

## 状态词只有四个

| 状态 | 含义 | 判据 |
| --- | --- | --- |
| `已验证` | 出口条件全部跑过，且结果是可复现的 | 有命令、有输出、有日期 |
| `未验证` | 代码已交付，但至少一条主路径没在真实环境跑过 | 报告里能指出具体哪条 NOT RUN |
| `未开始` | 只有计划，没有可运行产物 | — |
| `已关闭` | 主动决定不做，或已被后续方案取代 | 写清关闭理由与资产去向 |

**为什么不再用「✅ 已完成」**：阶段 6~15 的索引里 15 行是 ✅，但正文累计有 20 多处
NOT RUN / 未实测（真高德底图、真机 MediaRecorder、真实 EXIF 点选、主应用分享入口）。
当「完成」既表示「跑过」又表示「写完代码」，这个词的信息量就是零。
`tools/check_stage_index.py` 会强制每行出现上面四个词之一。

## 阶段总表

| 文件 | 对应阶段 | 状态 | 说明 |
| --- | --- | --- | --- |
| [stage0-rag-cli.md](./stage0-rag-cli.md) | 阶段 0：纯文本 RAG CLI（W0-1 ~ W0-3） | 已验证 | Recall@5≈0.71；拒答 4/4；含 Windows 实战记录 |
| [stage1-offline-jobs.md](./stage1-offline-jobs.md) | 阶段 1：离线批处理 + 照片元数据（W1-1 ~ W1-3） | 已验证 | 129 张照片 → 43 条日文档；重跑执行 0 条 |
| [stage2-structured-retrieval.md](./stage2-structured-retrieval.md) | 阶段 2：结构化 + 向量混合检索（W2-1 ~ W2-3） | 未验证 | 时间解析 22/22、消融 0.714→0.857；真实库 `eval-ablation` 未跑 |
| [stage3-tools-rerank.md](./stage3-tools-rerank.md) | 阶段 3：工具调用 + Reranker + 缓存（W3-1 ~ W3-4） | 已验证 | 真实库：工具 22/22；0.619→0.809→0.762；HNSW 迁移 0005 |
| [stage4-memory-graph.md](./stage4-memory-graph.md) | 阶段 4：Memory + LangGraph（W4-1 ~ W4-3） | 已验证 | 真实库消融 weighted 0.857/0.778 最优；迁移 0006 |
| [stage4-end-of-night-report.md](./stage4-end-of-night-report.md) | 阶段 4 收尾记录 | 已验证 | 当夜决策留痕，非独立阶段 |
| [stage5-eval-observability.md](./stage5-eval-observability.md) | 阶段 5：评估与可观测（W5-1 ~ W5-3） | 已验证 | W5-3 看板已交付（旧索引写「待下一段」有误）；`make dashboard` 可用 |
| [stage6-map-album.md](./stage6-map-album.md) | 阶段 6：地图相册 + 跨端 + 隐私分享（W6-1 ~ W6-5） | 未验证 | 447 passed / 11 skipped；真机双窗口与高德渲染待人工 |
| [stage7-research.md](./stage7-research.md) | 阶段 7：语义抽象 + 群体推荐 + 可验证断言（W7-1 ~ W7-3） | 已验证 | 收口提交 `b970dad`；k-匿名门禁 `K_ANONYMITY=5` |
| [stage8-multi-agent-audit.md](../design/stage8-multi-agent-audit.md) | 阶段 8：多 Agent 引用审计（提案） | 已关闭 | 未接入产品路径；原型资产保留在 `research/experiments/multi_agent_audit/` |
| [stage9-trail-replay.md](./stage9-trail-replay.md) | 阶段 9：轨迹回放（阶段 6 延伸，非原路线图阶段） | 已验证 | 423 条测试全绿；10 秒采样 ≈60fps；地图加载器已合二为一 |
| [stage10-track-studio-share-standalone.md](./stage10-track-studio-share-standalone.md) | 阶段 10：Track Studio + TrailShare Standalone（任务书） | 未验证 | 两个独立小应用成型；Playwright 真实文件链路未跑 |
| [stage10-delivery-report.md](./stage10-delivery-report.md) | 阶段 10 交付报告 | 未验证 | 移动端与 iOS Safari 分支未实测；大包性能无进度条 |
| [stage11-delivery-report.md](./stage11-delivery-report.md) | 阶段 11：主应用接入（iframe + postMessage + 导回） | 未验证 | 任务书为对话交付未落盘；端到端未跑 |
| [stage12-delivery-report.md](./stage12-delivery-report.md) | 阶段 12：变速 + 位置补全 + 高德 + 收件人侧 | 未验证 | 本机无高德 Key，两条地图路径只验了降级 |
| [stage13-track-studio-dual-theme.md](./stage13-track-studio-dual-theme.md) | 阶段 13：双入口双主题 + 主应用指向 embed + showcase 退役 | 未验证 | 756 条测试全绿；真机 MediaRecorder 未验 |
| [stage13-code-audit.md](./stage13-code-audit.md) | 阶段 13 代码审计清单 | 已验证 | 18 条极低 / 8 条中 / 7 条高，归档可查 |
| [stage14-e2e-and-light-playstage.md](./stage14-e2e-and-light-playstage.md) | 阶段 14：端到端验证 + 浅色播放页 + 省界轮廓 | 未验证 | W14-3 真实 EXIF 点选、W14-5 市级边界 NOT RUN |
| [stage15-p1-fix-receive-library.md](./stage15-p1-fix-receive-library.md) | 阶段 15：P1 修复 + 技术卫生 + 接收端 + 记忆库分类 | 未验证 | 886/984 条测试全绿；导回日期/坐标 P1 留到阶段 16 |
| [stage16-gate-and-truth.md](./stage16-gate-and-truth.md) | 阶段 16：门禁与真相（CI + 清理 + 收敛） | 未验证 | 643/954 全绿；ruff/mypy/边界/索引/生成物全绿；Actions 首跑待推送；地图加载器合并留到阶段 17 |

## 每份文档固定包含

1. **这一阶段要解决什么** + 出口条件（做完没做完，用出口条件判定）
2. **核心原理讲解**（用比喻和手算例子，不讲黑话）
3. **逐块讲解**：每个工作块做了什么、为什么这么做、代码在哪
4. **文件清单**：文件 → 作用 → 对应知识点
5. **怎么跑起来验证**：命令 + 期望结果 + 常见报错排查
6. **踩过的坑 / 易错点**
7. **动手练习**（可选）：改一个小参数，看指标怎么变

## 与其他文档的关系

- 设计文档 [`旅忆地图 TravelMemory.md`](../../旅忆地图%20TravelMemory.md)：规格说明书，讲"要做什么"
- 约定 [`PROJECT.md`](../../PROJECT.md)：不可协商的红线
- 上手 [`README.md`](../../README.md)：怎么跑起来
- **本目录**：讲"为什么这么做、原理是什么"，是给学习用的

## 自动校验

```bash
python tools/check_stage_index.py
```

四条规则：链接必须存在、目录里每份 `stage*.md` 都必须在表里、每行状态必须是四个词之一、
引用阶段文件的行必须带状态。CI 的 `docs` job 会跑它。
