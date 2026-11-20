# 阶段 14 交付报告（端到端验证 + 嵌入版浅色播放页 + 深色版补缺口 + 省市轮廓图）

> 执行者：AI 编码代理（团队 `software-stage14`：工程师 寇豆码 / QA 严过关 / 主理人 齐活林）
> 日期：2026-11-15
> 任务书：阶段 14 W14-1 ~ W14-5（对话交付）
> 结论：**五个工作块全部交付**；`812` 条测试全绿（175 + 112 + 414 + 34 + 21 + 56）；三个 app 构建通过；**W14-1 端到端真跑通**（生成了 15,846 B 的 `.trailshare`，独立版能解开）。
> 两个 P1 未修并已给出补丁/决策建议（日期字段、单地点 5 张上限）；`apps/` 基线仍未达成。

---

## §13 实际交付了什么

| 块 | 完成度 | 结论 |
|---|---|---|
| W14-4 M1 JSON 头收敛 | **100%** | `trips.ts` 三处写请求收敛为 `jsonHeaders()`，逐处确认均为 JSON body |
| W14-2 嵌入版播放页全浅色 | **100%** | 自绘浅色轨迹（`#0066cc/#0099ff`）、高德 `light` 样式、浅色控制条；深色版零改动 |
| W14-3 深色版补缺口 | **100%**（真实 EXIF 点击 NOT RUN） | 每段交通工具 + 签名 `journey.json`（Ed25519，20 条新测试） |
| W14-5 省市轮廓图 | **100%**（市级按需 TODO） | 458 KB 省级 GeoJSON、GCJ 平面直接投影、离屏缓存、失败退格网、外部请求 0 |
| W14-1 端到端 | **PASS（真跑）** | 主应用 → TrailShare embed → 生成下载 → 独立版解开；分享包日期字段有 P1 缺陷 |

### 13.1 新增文件

```
apps/track-studio/public/geo/china-provinces.json         458 KB / 35 features / 仅 adcode+name
apps/track-studio/src/theme-dark/map/chinaOutline.ts
apps/track-studio/src/theme-dark/map/chinaOutline.test.ts
apps/track-studio/src/theme-dark/map/gridMap.click.test.ts
apps/track-studio/src/theme-dark/transportPlan.ts
apps/track-studio/src/theme-dark/transportPlan.test.ts
apps/track-studio/src/theme-dark/exportJourney.ts
apps/track-studio/src/theme-dark/exportJourney.test.ts
apps/track-studio/src/theme-light/ui/Landing.qa.test.tsx
apps/trailshare-standalone/src/theme-light/ui/Landing.qa.test.tsx
docs/stages/stage14-e2e-and-light-playstage.md
```

### 13.2 修改文件（关键 diff）

**W14-4** `frontend/src/services/api/trips.ts`

```diff
+function jsonHeaders(): Record<string, string> {
+  return { 'Content-Type': 'application/json' };
+}
-      headers: { 'Content-Type': 'application/json' },
+      headers: jsonHeaders(),
```
三处：`createTrip`（POST）、`renameTrip`（PATCH）、`assignTripPhotos`（POST），全部 `JSON.stringify`，无 multipart。

**W14-2** `theme-light/ui/PlayStage.tsx`

```diff
-      renderer.draw(drawState);
+      drawLightFrame(ctx, { scene, proj, progressS, headX, headY });
```
- 自绘：未走段 `#0066cc`、已走段 `#0099ff`（线宽 +0.8）、光点实心 `#0066cc` + 白描边；按 `segments[k].mode` 画实线/虚线；不画格网/暗角/扫描线。
- `Renderer` 只保留 `resize()` 作 DPR/尺寸管理；深色版仍走内核 `renderer.draw`。

`theme-light/styles.css`：删掉给 `.stage` 重新声明深色 token 的整段补丁；`.stage` 改 `background: var(--bg)`；控制条 `color:#16202e / border:1px #e0e6ed / background:rgba(255,255,255,.94)`；`stage-scrim`、`stage-title`、`stage-note` 三处写死深色一并改浅。
`theme-light/map/baseMap.ts`：`DARK_STYLE` → `LIGHT_STYLE='amap://styles/light'`；降级自绘底色改 `#f7f9fc…#e4ecf4`，格网/十字/标注改浅色。
`theme-light/map/controller.ts`：`initMap` 加可选 `paintFallback`（默认 false，既有测试不变）。

**W14-3** `theme-dark/App.tsx` / `SetupPanel.tsx` / `DonePanel.tsx`

- `transports` state + `transportMap` → `toTrailPhotos(photos,{transports})`；
- `SetupPanel` 新增 `.transport-list`：每段「第 N 段 / 起→止 / select（自驾/火车/飞机/步行）」；
- 完成页新增「导出轨迹 JSON（已签名）」：`{version:'journey-json-1', bundle, signerPubkey, signature}`，`stableStringify` 签名，`<a download>` 落盘，不上传。

`core/identity.ts` **仅新增**：

```ts
export function signWithSeed(seedHex: string, message: Uint8Array): string;
```
（`@noble/curves/ed25519`；已有函数一行未改。）

**W14-5** `theme-dark/map/gridMap.ts` / `theme-dark/App.tsx`

```diff
+  outline?: boolean;              // 默认 true
+  onOutlineChange?: (ready: boolean) => void;
+  isOutlineReady(): boolean;
-  drawState.fallback = started;
+  drawState.fallback = started && !outlineReadyRef.current;
```
- 底图 canvas 条件改为 `paintGrid || outline`（播放页 `paintGrid:false` 也要这张画布）；
- 轮廓就绪 → 画省界并停画格网；失败 → 原格网逻辑逐字不变；
- `GridMapOptions.coordinateSystem` 改为**必填**，控制器如实回传；点击按平面换算（GCJ 平面 → WGS84，WGS84 平面恒等）。

**文案修复（P3）**：两个 `theme-light/ui/Landing.tsx` 的「未嵌入，不可用」改三分支（未嵌入 / 已嵌入但 0 张 / 有照片）。

### 13.3 `git diff --stat`

```
 .env.example                                       |   4 +
 docs/stages/README.md                              |  10 +-
 frontend/.env.example                              |   6 +
 frontend/src/pages/MapPage/MapPage.tsx             | 100 ++++-
 frontend/src/pages/MapPage/PhotoLocationDialog.tsx |   3 +-
 frontend/src/pages/MapPage/TripDrawer.tsx          |  20 +
 frontend/src/services/api/trips.ts                 |  23 +-
 frontend/src/styles.css                            |  10 -
 package-lock.json                                  | 443 ++++++++++++++-------
 package.json                                       |   4 +-
 showcase/README.md                                 | 178 ++++-----
 11 files changed, 527 insertions(+), 274 deletions(-)
```

> `trips.ts` 是本轮唯一 tracked 的前端实现改动；其余是阶段 10–13 遗留。`apps/**` 全部未跟踪。

---

## §14 修改了哪些现有文件（补充说明）

| 文件 | 阶段 | 改动 |
|---|---|---|
| `frontend/src/services/api/trips.ts` | W14-4 | `jsonHeaders()` + 三处调用 |
| `theme-light/ui/PlayStage.tsx` | W14-2 | 自绘浅色轨迹；内核渲染器只留 resize |
| `theme-light/styles.css` | W14-2 | 删除 `.stage` 深色 token 重声明 + 3 处写死深色改浅 |
| `theme-light/map/baseMap.ts` | W14-2 | 高德 `light` 样式 + 降级格网浅色 |
| `theme-light/map/controller.ts` | W14-2 | `paintFallback` 可选参数 |
| `theme-dark/App.tsx` | W14-3/5 | transports、签名导出、`fallback` 依赖轮廓就绪、`coordinateSystem:'gcj02'` |
| `theme-dark/ui/SetupPanel.tsx` | W14-3/5 | 交通工具 UI；声明 `coordinateSystem:'wgs84'` |
| `theme-dark/ui/DonePanel.tsx` | W14-3 | 「导出轨迹 JSON（已签名）」按钮 |
| `theme-dark/map/gridMap.ts` | W14-5 | 轮廓加载/缓存/绘制；点击换算；`coordinateSystem` 必填 |
| `theme-dark/styles.css` | W14-3 | `.transport-list/-row/-select` |
| `core/identity.ts` | W14-3 | 仅新增 `signWithSeed` |
| `theme-light/ui/Landing.tsx` ×2 | P3 | 照片按钮文案三分支 |
| `theme-dark/entry.test.ts` | W14-5 | 同步 `coordinateSystem` 契约断言 |

---

## §15 踩过的坑

1. **坐标系方向反了（最重要）**：任务书假设"DataV GCJ → 先转 WGS → 再 mercator"，但本管线 `prepareScene` 是 `wgs84ToGcj02` 后 `mercX`（`renderer.ts:83-84`），投影中心也是 `startGcj`（`App.tsx:188`）。按任务书字面做，省界相对轨迹偏 **37.9px @Z13**。最终改为 **DataV GCJ 直接投影**，实测轨迹与省界像素最近邻 **0.00px**。真正需要转换的是"点地图补位置"：GCJ 平面反算出的坐标必须 `gcj02ToWgs84` 才能入库。
2. **浅色轨迹不能靠 CSS**：`packages/trail` 的 Renderer 把青色写死在 canvas 里，任务书的 `.track-line` 选择器在实现里不存在。CSS filter 得不到精确 `#0066cc`，最终在 `PlayStage.tsx` 自绘（不碰内核），像素实测两段颜色都在场。
3. **`journey.json` 导出原本不存在**：任务书说"没接身份"，实际深色版只有 WebM。按最小实现新增完整导出 + 签名 + 验签测试。
4. **`make check` 用错解释器**：Makefile 里是 `python`（系统 3.13，无 sqlalchemy），必须用 `.venv/Scripts/python.exe -m app.cli --check`。自检只剩一个**既有**告警：库里向量来自两个模型（hashing-1024 与 qwen3-embedding），需要 `make reindex`，与本轮无关。
5. **W14-1 的 500 其实不是代码问题**：Postgres 没起。`make up` 后 `/health` 200、`POST /trips` 201、照片导入 200（517 B 假 JPEG 返回 `failed:1`，不是 500）。
6. **QA 环境注水（已披露）**：headless profile 没有主应用缩略图库与 TrailShare 身份，QA 按应用自身格式往 IndexedDB 写了 6 张缩略图和主应用种子，才跑通"自己打的包自己开"。打包/加密/签名/解包全部是真代码，但这一步必须写清楚，不能当成"零准备真跑"。
7. **`vite preview` 的正确入口**：依赖被 hoist 到根，`apps/*/node_modules/.bin/vite` 不存在，要用根 `node_modules/.bin/vite`。

---

## §16 未完成项与差异清单

### 16.1 P1：分享包日期丢失（未修，文件只读）

- 现象：解包显示 `2026-11-15`（导入当天），照片实拍是 `2025-10-04`。
- 根因：后端 `taken_at` 全为 null，真值在 `taken_at_local`；`frontend/src/pages/StudioEmbed/StudioEmbed.tsx:217` 只发 `if (photo.takenAt)`。
- 补丁（**未应用**，该文件在 §1.1 标为只读）：
```diff
- if (photo.takenAt) payloadPhoto.takenAt = photo.takenAt;
+ const takenAt = photo.takenAt ?? photo.takenAtLocal;
+ if (takenAt) payloadPhoto.takenAt = takenAt;
```
- 影响：分享包时间线错一天/错月；地点、照片、签名不受影响。

### 16.2 P1：同一地点 >5 张照片会卡住生成（未修）

- 现象：6 张照片全在同一地点 → 「第 1 天第 1 个地点需要 1–5 张照片，现在有 6 张」。
- 位置：`apps/trailshare-standalone/src/core/buildTrip.ts:130`（每地点上限 5）。
- 建议（需产品决策）：① 超限自动拆成多个地点；② 放宽到 9 张；③ 至少把错误文案改成"删除多余照片或拆分地点"。

### 16.3 已知降级 / NOT RUN

1. **W14-3 真实 EXIF 照片点击验证 NOT RUN**：环境无带 GPS 的 JPEG；用 2 张无 GPS 照片走"点地图补坐标"验了 DOM 行为。
2. **W14-5 市级边界未做**：`chinaOutline.ts` 文件头有 `TODO(歧义): 市级边界按缩放按需加载（阶段15+）`。
3. **W14-2 高德 `light` 样式未真机验证**：本机无 Key，只验了降级浅色格网；不可用时可退 `normal`（常量处已注明）。
4. **`amap://styles/light` 的下限**：无 Key 时播放页用自绘浅色格网，轨迹仍是自绘色，不受影响。
5. **首次轮廓构建 239ms 在主线程**（揭幕遮罩期内）；稳态 2.2–5.6ms。未挪到 idle 回调。
6. **`apps/` 基线未达成**：`git ls-files apps` = **0**。本轮所有"零改动"只能靠 mtime 自证，**无法用 git diff 审计**；不要现在 `git add`（会拍到中间状态）。
7. **QA 端到端前的 IDB 注水**：见 §15-6。

### 16.4 数据来源与版权（W14-5）

- 来源：阿里云 DataV.GeoAtlas `areas_v3/bound/100000_full.json`（基于高德开放平台行政区划数据）。
- 处理：仅保留 `adcode/name/geometry`，坐标 4 位小数、去连续重复点；35 个省级 feature 全保留，458 KB。
- 坐标系：**GCJ-02**（北京 `center=[116.405285,39.904989]`，与 GCJ 参考差 0.0022°、与 WGS 参考差 0.0146°）；渲染时**不转换**，直接进 GCJ 墨卡托平面。
- 版权：该数据仅供学习/研究使用；**商用前需自行向高德/阿里云确认授权**。仓库内保留出处注释。

---

## §9 提交前检查清单（逐条）

1. **新增文件**：见 §13.1（10 个代码/数据 + 本报告）。
2. **修改文件 + diff**：见 §13.2 / §14。
3. **`git diff --stat`**：见 §13.3。
4. **完成度**：W14-4 100%、W14-2 100%、W14-3 100%（真实 EXIF NOT RUN）、W14-5 100%（市级 TODO）、W14-1 PASS（真跑）。
5. **测试**：
```
track-studio 175 / 22 files；trailshare 112 / 13；frontend 414 / 37
showcase 34 / 4；journey-transfer 21 / 2；photo-source 56 / 4
合计 812 passed，0 failed；根 typecheck 0 error；三构建 exit 0
```
6. **W14-1 状态**：**跑通**。`.trailshare` = `阶段14 E2E 分享.trailshare`，15,846 B；独立版解出「来自 我 的信物 · 1 个地点」，签名/承诺/本地哈希链均通过。环境注水见 §15-6；日期缺陷见 §16.1。
7. **W14-2 截图**：`F:/temp/qa14-light.png`（浅色，轨迹清晰、无暗角）、`F:/temp/qa14-dark.png`（深色未变）。像素：`#0066cc` 698 px、`#0099ff` 755 px（工程师）/ 659 / 1053（QA，不同进度与分辨率）。
8. **W14-3 签名验证**：同身份 `verify=true`；改 bundle / version / signature 三种均 `false`；文件内搜不到种子；`signerPubkey` 72 字符、`signature` 128 hex。
9. **W14-4 逐处确认**：`createTrip` / `renameTrip` / `assignTripPhotos` 全部 `JSON.stringify`，无 multipart。
10. **W14-5 数据**：见 §16.4（458 KB / 35 省 / GCJ-02 / 版权提示）。
11. **TODO**：市级边界（`chinaOutline.ts` 文件头）；浅色轨迹色值为播放页常量、与暗场内核分叉（阶段 15 若内核支持调色板再收敛）。
12. **已知未完成**：见 §16.1–16.3。
