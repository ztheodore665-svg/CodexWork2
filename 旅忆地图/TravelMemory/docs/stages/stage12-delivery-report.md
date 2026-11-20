# 阶段 12 交付报告（轨迹变速 + 位置补全 + 高德共享 + TrailShare 地图与收件人侧）

> 执行者：AI 编码代理（团队 `software-stage12`：工程师 寇豆码 / QA 严过关 / 主理人 齐活林）
> 日期：2026-11-13
> 任务书：阶段 12 W12-1 ~ W12-5（对话交付，未单独落盘）
> 结论：**五个工作块全部落地**；`682` 条测试全绿（21 + 56 + 84 + 108 + 413）；两个 app 与主应用构建通过；QA 在真浏览器里验到补位置全流程、独立入口零请求、embed 引导页、无 Key 降级示意。
> **一处未接线**：主应用 `StudioEmbed.tsx` 的 iframe src 仍是 `index.html`，所以主应用里暂时看不到 TrailShare 地图（任务书 A15 明确要求标 TODO，见 §9）。

---

## 1. 新增了哪些文件（完整路径）

### 1.1 Track Studio（W12-1 / W12-2）

```
apps/track-studio/src/playback/speedCurve.ts
apps/track-studio/src/playback/speedCurve.test.ts
apps/track-studio/src/playback/speedCurve.qa.test.ts      ← QA 补：4 条非线性/边界回归
apps/track-studio/src/pages/LocationEditor.tsx
apps/track-studio/src/pages/location/locationFix.ts
apps/track-studio/src/pages/location/locationFix.test.ts
```

### 1.2 TrailShare（W12-3 / W12-4 / W12-5）

```
apps/trailshare-standalone/embed.html
apps/trailshare-standalone/src/env.ts
apps/trailshare-standalone/src/map/amap.ts
apps/trailshare-standalone/src/map/planPlaces.ts
apps/trailshare-standalone/src/map/planPlaces.test.ts
apps/trailshare-standalone/src/receive.ts
apps/trailshare-standalone/src/receive.test.ts
apps/trailshare-standalone/src/packFlow.test.ts
apps/trailshare-standalone/src/ui/PlaceMap.tsx
apps/trailshare-standalone/src/ui/PlaceList.tsx
apps/trailshare-standalone/src/ui/PlaceList.test.tsx
apps/trailshare-standalone/src/ui/PlaceBoard.tsx
apps/trailshare-standalone/src/ui/ReceivePanel.tsx
```

### 1.3 主应用（W12-2b）

```
frontend/src/pages/LocationEditor/LocationEditor.tsx
frontend/src/pages/LocationEditor/LocationEditor.css
frontend/src/services/location-fix/validate.ts
frontend/src/services/location-fix/validate.test.ts
```

### 1.4 文档

```
docs/deploy-amap-key.md
docs/stages/stage12-delivery-report.md      ← 本文件
```

---

## 2. 修改了哪些现有文件（关键 diff）

### 2.1 `apps/track-studio/src/ui/PlayStage.tsx`（W12-1 接线）

```diff
+  const durations = useMemo(() => computeSegmentDurations(journey), [journey]);
+  // effect 挂载时：totalDurationSeconds(durations) * 1000
+  clock.durationMs = Math.max(1000, totalDurationSeconds(durations) * 1000);
...
-      samplePose(journey, clock.q * scene.totalMeters, pose);
+      samplePose(journey, qToDistance(clock.q, journey, durations), pose);
```

进度条、`clock.seek`、1x/2x/4x 倍速**仍基于 q**（一行未动）；导出时长 `clock.durationMs / speed` 自动跟随新总时长。

### 2.2 `apps/track-studio/src/ui/EditPanel.tsx` + `App.tsx`

```diff
+  /** 打开「补全照片位置」页（仅当有缺坐标照片时可用）。 */
+  onFixLocations: () => void;
+  {missing.length > 0 ? (
+    <button className="btn btn-tiny" onClick={onFixLocations}>{missing.length} 张照片缺位置，点击补全</button>
+  ) : null}
...
+type Mode = 'landing' | 'edit' | 'location' | 'play' | 'done';
+  const handleApplyLocation = (photoId, lat, lng) => setPhotos(prev => applyLocation(prev, photoId, lat, lng));
```

### 2.3 `frontend/src/pages/MapPage/MapPage.tsx`（W12-2b 入口，+100 −2）

```diff
+import { LocationEditor } from '../LocationEditor/LocationEditor';
+import { MapPin } from 'lucide-react';
+  const missingLocationCount = photos.filter((p) => !p.hasLocation).length;
+  const openLocationEditor = () => {
+    if (missingLocationCount === 0) { toast({ title: '所有照片都有位置' }); return; }
+    setTripDrawerOpen(false); setLocationOpen(true);
+  };
+        <Button onClick={openLocationEditor}><MapPin/>补全照片位置<Badge>{missingLocationCount}</Badge></Button>
+      <LocationEditor open={locationOpen} photos={photos} jsKey={AMAP_JS_KEY} securityCode={AMAP_SECURITY_CODE}
+        onClose={() => setLocationOpen(false)} onChanged={handleTripsChanged} />
```

### 2.4 `apps/trailshare-standalone`（W12-3/4/5）

| 文件 | 改动 |
|---|---|
| `index.html` | `<html data-entry="standalone">`；CSP 由插件按入口注入 |
| `embed.html` | **新增**：`data-entry="embed"`，标题「信物地图 · TrailShare」 |
| `vite.config.ts` | 多页 `rollupOptions.input`（`main` / `embed`）；`transformIndexHtml` 按 `ctx.path` 选 CSP；测试 include 加 `*.test.tsx` |
| `src/csp.ts` | `buildCsp(kind)` / `injectCsp(html, kind)`；embed 才放行 `script-src webapi.amap.com` + `img-src *.amap.com`；**`connect-src` 两个入口都逐字 `'self'`** |
| `src/main.tsx` | 从 `document.documentElement.dataset.entry` 读入口 → `<App entry={entry} />`；SW 仅 standalone 注册 |
| `src/App.tsx` | `embedded = entry==='embed' && inIframe && isEmbedded()`；`mode:'receive'`；`misusedEmbed` 引导页；持有 `received/receiveBusy/...` |
| `src/ui/Landing.tsx` | 「打开别人的信物」按钮 + `.receive-drop` 拖拽区 + 隐藏 file input |
| `src/ui/PreviewPanel.tsx` | `embedded ? <PlaceBoard/> : 原时间线`；有坐标时显示「信物会包含这些地点的精确坐标（本版本未做地理模糊）。」 |
| `src/ui/PrivacyNotice.tsx` | embed 文案「除了地图底图，没有其他网络请求」；standalone 保持「全程零网络请求」 |
| `public/_headers` | 三块：`/`、`/index.html`（无 amap）、`/embed.html`（script/img 放行 amap、`connect-src 'self'`） |
| `package.json` | +`@travelmemory/amap-loader`、`@travelmemory/geo`；devDeps +`@types/node`、`@testing-library/react|dom` |
| `src/buildTrip.ts` | +`firstGeoOf` / `buildGeoHints`，代表点写 `Place.lat/lng` |
| `src/packFlow.ts` | `groups[0]` 增加 `geoHints: buildGeoHints(trip)`（**这是本轮最关键的一处修复**，见 §7） |

### 2.5 根 `.env.example`

```diff
+# 高德 Web 端 JS Key（Track Studio / TrailShare embed 用；前端构建期注入）
+VITE_AMAP_JS_KEY=
+VITE_AMAP_SECURITY_CODE=
```

---

## 3. `git diff --stat`

```
 .env.example                              |   4 +
 docs/stages/README.md                     |   5 +-
 frontend/.env.example                     |   6 +
 frontend/src/pages/MapPage/MapPage.tsx    | 100 ++++++-
 frontend/src/pages/MapPage/TripDrawer.tsx |  20 ++
 package-lock.json                         | 443 ++++++++++++++++++++----------
 package.json                              |   4 +-
 7 files changed, 432 insertions(+), 150 deletions(-)
```

说明：
- `frontend/.env.example`、`TripDrawer.tsx`、`docs/stages/README.md`、`package.json`、`package-lock.json` 的部分改动是**阶段 10/11 遗留**（本轮未回退）。
- 本轮 tracked 新增只有 `MapPage.tsx` 与根 `.env.example`；其余全在未跟踪目录里。
- **`apps/`、`packages/journey-transfer`、`packages/photo-source` 至今整体未被 git 跟踪**，所以 `git diff` 看不到这两个 app 的改动。QA 已用 mtime 清单核对本轮文件范围，但也明确指出：没有基线就无法证明"没删断言/没改期望值"。建议阶段 13 先补一次基线提交（见 §9）。

---

## 4. 五个工作块完成度

| 块 | 完成度 | 说明 |
|---|---|---|
| W12-1 轨迹变速 | **100%** | `speedCurve.ts` + PlayStage 接线 + 12 条单测 + QA 4 条回归；演示路线总时长 42s → **4s** |
| W12-2 照片补位置 | **100%（两侧）** | Track Studio 全流程（真浏览器 3 张 → 3 次点选 → 自动返回）；主应用复用**已存在的** `patchPhotoLocation`，无 API 缺口 |
| W12-3 高德 Key 防护 | **100%** | Key/安全密钥已在两个 app 就位；`docs/deploy-amap-key.md` 四节齐全；loader 注入早于插脚本 |
| W12-4 TrailShare 加地图 | **90%** | `embed.html` + 双 CSP + PlaceMap/PlaceBoard + 无 Key 降级都完成；**主应用 StudioEmbed 仍指向 index.html**（A15 TODO），地图在主应用内暂时不可达 |
| W12-5 收件人侧 | **100%（展示）** | 文件选择/拖拽 → `unpackForViewer` → 文本列表（standalone）/地图+侧栏（embed）；三种失败文案齐全；**不含**接受/拒绝落库（任务书非目标） |

---

## 5. 所有测试命令与结果

```
packages/journey-transfer    2 files /  21 passed
packages/photo-source        4 files /  56 passed
apps/track-studio           10 files /  84 passed     ← 阶段 11 的 53 → 80（批次 A）→ 84（QA 4 条）
apps/trailshare-standalone  12 files / 108 passed     ← 阶段 11 的 66 → 100 → 108（packFlow 7 + connect-src 1）
frontend                    37 files / 413 passed     ← 阶段 11 的 402 → 413（validate 11）
────────────────────────────────────────────────
合计                                   682 passed，0 failed
```

构建：

```
track-studio        ✓ 92 modules，index 241.69 kB
trailshare          ✓ 108 modules，main 254.25 kB + amap chunk 1.39 kB
frontend            ✓ 1771 modules，index 456.20 kB
```

CSP 产物（逐字）：

```
dist/index.html   script-src 'self'; … connect-src 'self'; …     ← 无任何 amap
dist/embed.html   script-src 'self' https://webapi.amap.com; … connect-src 'self'; …
dist/assets/main-*.js   webapi 出现 0 次（高德适配层只在 amap-*.js 动态 chunk）
```

---

## 6. 手动验收清单（逐条）

| # | 项 | 状态 | 证据 |
|---|---|---|---|
| 1 | W12-1 播放"近快远慢"、总时长比旧版短 | ✅ | 演示 4 段 = 4.561/2.669/5.554/4.466 km → 各 1s、总 4s（原 42s）；非线性由 4 条 QA 用例钉住（`q=0.5` 偏离线性 >5% 总长） |
| 2 | W12-2 Track Studio 补位置全流程 | ✅ | 真浏览器：3 张无 GPS → 补位置页 3 项 + 地图 984×631 → 点 1 下剩 2 项、「已选：35.86100, 104.21285」→ 第 3 下自动回编辑页、「3 张带位置信息」 |
| 3 | W12-2 主应用补位置页 | ✅（无照片环境） | 后端不可达、照片 0 张 → 点按钮出现 toast「所有照片都有位置」，不打开空面板；**有照片时的保存未端到端验证**（`patchPhotoLocation` 单测级覆盖） |
| 4 | W12-3 配 Key 真地图 / 不配降级 | ⚠️ **仅无 Key 侧** | 无 Key：Track Studio 降级格网、TrailShare embed「未配置地图服务」+SVG 462×418；**本机无可用 Key，真地图未验证** |
| 5 | W12-4 两入口 CSP 正确、独立零请求、嵌入有地图 | ✅ / ⚠️ | 独立入口：外部请求 0、amap 0；embed：CSP 正确、无 Key 时降级示意；**有 Key 的 embed 地图未验证** |
| 6 | W12-5 打开别人的包能展示 | ✅（文本+失败路径） | bad →「文件已损坏，或不是 TrailShare 信物」；old →「此信物为旧版本，请让发送方重新生成」；ok 路径由 5 条真实 pack→unpack 单测覆盖；**嵌入态地图+侧栏未用真实包走通** |

---

## 7. 本轮被 QA 拦下的两个问题（值得记录）

1. **P3：零时长段破坏 `q=0` 不变量**。`speedCurve.ts` 里 `duration > 0 ? … : 1`，当分档参数把某段时长配成 0 时，`qToDistance(0)` 直接跳到该段末尾（实测 480.703m 而非 0）。QA 补的回归用例（`q=0` 必须为 0）红着交付，修成 `: 0` 后 84 条全绿。
2. **功能缺口：`.trailshare` 里没有坐标**。`packFlow.ts` 调 `pack()` 时没传 `geoHints`，而协议只从 `geoHints` 写 `meta.lat/lng`。后果是收件人侧 `UnpackedPlace` 无坐标 → W12-4 地图一个点都画不出来。修法：`buildGeoHints(trip)` 取每个地点第一张合法坐标照片、`radius: 0`（精确点），并在生成页明确提示「信物会包含这些地点的精确坐标（本版本未做地理模糊）」；`packFlow.test.ts` 用**真实 pack → unpack 回读**断言 `lat/lng` 与 `radius === 0`，另有"全无坐标 → 三个字段 undefined"的反向用例。

---

## 8. 所有 `// TODO(歧义)` 注释

| # | 位置 | 问题 | 当前选择 |
|---|---|---|---|
| 1 | `PreviewPanel` | 地点本身没有坐标（坐标挂在照片上） | 取该地点第一张带坐标照片作代表点（未取重心） |
| 2 | `PlaceMap` | 全部地点都无坐标时的视野 | 兜底北京 `[116.397, 39.909]`、zoom 5 |
| 3 | `ReceivePanel` | `canAccept=false` 时是否给「收下/拒绝」 | 只读展示，不写本地哈希链（本轮非目标） |
| 4 | `StudioEmbed.tsx` | 主应用 iframe 仍指向 `index.html` | 标 TODO（任务书 A15），**需要一次一行改动**才能让主应用看到 embed 地图 |

---

## 9. W12-5 地图散布是否交付 / StudioEmbed src 是否改

- **W12-5 地图散布：已交付**。`PlaceMap` 有 Key 走高德原生 Circle/Marker，无 Key 走 SVG 散布示意；`PlaceBoard` 在 embed 模式渲染「地图 + 侧栏」，点击地图圆点 `scrollIntoView` 到侧栏对应项。
- **`StudioEmbed.tsx` 的 iframe src：未改**（任务书 A15 明确「若 §1.2 未列，标 TODO」）。当前主应用 iframe 是 `index.html?embedded=1&parentOrigin=…`，因此：
  - 主应用里 TrailShare **仍是无地图的独立版**；
  - `embed.html` 的入口判定、双 CSP、地图能力**都已就绪**，把 src 换成 `embed.html?embedded=1&parentOrigin=…` 即可生效，不需要再改 TrailShare 一行。
  - 这是本轮**唯一的功能性未接线项**。

---

## 10. 已知未完成项（诚实清单）

1. **主应用看不到 TrailShare 地图**（A15，见 §9）。
2. **TrailShare 编辑页没有地图**：只有生成页/接收页在 embed 模式用地图；编辑页保持表单。
3. **有 Key 的真地图未验证**：本机无可用 Key，两条地图路径都只验了"无 Key 降级"。
4. **演示路线看不出三档变速**：5 个点全落在 2–50km 的中段（各 1s），"近快远慢"只有单测能证明。建议后续换一组含 <2km 与 ≥50km 的演示点，或加第二个"跨城"预设（本轮按"不改演示数据"处理）。
5. **坐标是精确点（radius=0）**：独立版没有后端地理模糊，收件人看到的是精确位置（生成页已明确提示）。
6. **接收页只读**：没有接受/拒绝、不写本地哈希链、不能回存到主应用。
7. **高德路径无键盘可达**：Marker/Circle 只能鼠标点；SVG 降级路径支持 Enter/空格。
8. **`apps/` 与两个新包未被 git 跟踪**：无法用 `git diff` 证明"只改了这些行"，也无法回滚到阶段 10 基线。建议先补一次基线提交，再进阶段 13。
9. **报告路径**：任务书 §9.3 写 `docs/dev-report-stage12.md`；按上一轮"阶段文档统一收进 `docs/stages/`"的约定，本文件放在 `docs/stages/stage12-delivery-report.md`，README 已加索引。

---

## 11. 与任务书的差异（简要）

| # | 差异 | 原因 | 影响 |
|---|---|---|---|
| D-1 | 报告放 `docs/stages/` 而非 `docs/dev-report-stage12.md` | 沿用上一轮用户要求的目录约定 | 仅路径不同 |
| D-2 | embed CSP 的 `connect-src` 保持 `'self'`（工程师一度放宽为 `'self' https://*.amap.com`，被打回） | §6.4.3 明确只放行 script/img；放宽会让"除底图外零请求"从"只能同源"退化 | 若真机出现底图灰屏 + CSP 拦 connect，需带证据上报后再放宽 |
| D-3 | 独立版打包时写入精确坐标（radius=0） | 无后端模糊；不写坐标则收件人地图全空 | 生成页已明示；将来接后端模糊时改传模糊后的 hint |
| D-4 | `_headers` 拆三块（`/`、`/index.html`、`/embed.html`） | 静态托管对 `/` 与 `/index.html` 分别匹配 | 比任务书的两块更精确 |
| D-5 | TrailShare 新增 `@travelmemory/amap-loader`、`@travelmemory/geo`、`@types/node`、`@testing-library/*` | 地图与 jsdom 组件测试需要（§1.6 允许 app 级依赖） | 只动该 app 的 package.json，根依赖树未动 |
