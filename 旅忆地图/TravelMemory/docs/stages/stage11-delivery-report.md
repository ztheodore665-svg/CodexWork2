# 阶段 11 交付报告（主应用接入）

> 执行者：AI 编码代理（团队 `software-stage11`：工程师 寇豆码 / QA 严过关 / 主理人 齐活林）
> 日期：2026-11-13
> 前置：阶段 10 验收通过（196 测试、WebM/离线/零请求均有真浏览器证据）
> 结论：**两个入口已接进主应用，iframe 握手在真浏览器里跑通**；402 条前端测试全绿；有 1 个真 Bug 在 QA 阶段被拦下并修掉（见 §9）。

---

## 0. TL;DR

- 地图页左下工具栏新增「轨迹演示」；Trip 详情新增「分享这个旅程」。
- 点开 → 全屏 Radix Dialog + iframe（`?embedded=1&parentOrigin=<主应用 origin>`）→ iframe 发 `ready` → 主应用回 `init`（身份 + 缩略图，`ArrayBuffer` transfer）→ 子窗口显示「使用主应用身份（仅内存，不落盘）」。
- 子窗口 `import-to-main` → `journeyToTrip` → 逐张上传缩略图 → `createTrip` → `assignTripPhotos` → 刷新行程列表。
- 真浏览器实测：握手成功、关闭卸载、重复开关 3 轮稳定、小应用未启动时错误卡片含启动命令。

**环境限制（影响验收口径）**：本机**后端未运行**（`:8000` 不可达）、无 `.venv`/fastapi/uvicorn/PostgreSQL/docker。因此"从主应用传真实照片 → 生成 → 导回落库 → 行程列表可见"这条链路**没有端到端跑过**；协议与映射由单测覆盖，浏览器侧只验证到"0 张照片时握手仍成立"。

---

## 1. 新增了哪些文件（完整路径）

### 1.1 `frontend/src/services/studio-bridge/`（3）

```
frontend/src/services/studio-bridge/types.ts       消息与载荷类型（StudioPhoto / StudioInitPayload / StudioChildMessage）
frontend/src/services/studio-bridge/bridge.ts      postMessage 客户端（source + origin 双校验、transfer 去重、dispose 幂等）
frontend/src/services/studio-bridge/bridge.test.ts 15 条
```

### 1.2 `frontend/src/pages/StudioEmbed/`（6）

```
frontend/src/pages/StudioEmbed/StudioEmbed.tsx            全屏 Dialog + iframe 容器 + 握手编排
frontend/src/pages/StudioEmbed/StudioEmbed.css            作用域样式（studio-embed-*）
frontend/src/pages/StudioEmbed/selectPhotos.ts            trip.items → 有序照片子集（纯函数）
frontend/src/pages/StudioEmbed/selectPhotos.test.ts       8 条
frontend/src/pages/StudioEmbed/studioEmbed.qa.test.ts     QA 补：3 条握手接线回归（曾红，现绿）
frontend/src/pages/StudioEmbed/studioEmbed.share.qa.test.ts QA 补：6 条 share 模式（5175 / trips / 顺序 / 四键 / 副本 / 超时文案）
```

### 1.3 `frontend/src/services/journey-import/`（2）

```
frontend/src/services/journey-import/journeyToTrip.ts       JourneyBundle → 导入计划 + 落库编排
frontend/src/services/journey-import/journeyToTrip.test.ts  20 条
```

合计 **11 个新文件**（含 QA 的 2 个测试文件）。

---

## 2. 修改了哪些现有文件（逐处 diff）

### 2.1 `frontend/src/pages/MapPage/MapPage.tsx`（+30 −2）

```diff
-import { BadgeCheck, ChevronLeft, Route, ShieldCheck, Sparkles } from 'lucide-react';
+import { BadgeCheck, ChevronLeft, Clapperboard, Route, ShieldCheck, Sparkles } from 'lucide-react';
+import type { StudioMode } from '../../services/studio-bridge/types';
+import { StudioEmbed } from '../StudioEmbed/StudioEmbed';
+  const [studio, setStudio] = useState<{ mode: StudioMode; trip?: TripView } | null>(null);
         </Button>
+        <Button … onClick={() => setStudio({ mode: 'track' })}>
+          <Clapperboard className="h-4 w-4" />
+          轨迹演示
+        </Button>
             onReplayTrip={setReplayTrip}
+            onShareTrip={(trip) => setStudio({ mode: 'share', trip })}
           />
+      <StudioEmbed
+        open={studio !== null}
+        mode={studio?.mode ?? 'track'}
+        trip={studio?.trip}
+        photos={photos}
+        onTripsChanged={handleTripsChanged}
+        onClose={() => setStudio(null)}
+      />
```

> 入口放在**左下角工具栏**（「验证信物」下方），与「生成信物/验证信物」并列；任务书 §2 写的是"顶部工具栏"，但该页顶部只有品牌条，既有入口都在左下角，按"样式跟现有按钮一致"放在这里。

### 2.2 `frontend/src/pages/MapPage/TripDrawer.tsx`（+20）

```diff
+  Share2,
+  /** 分享这条行程（阶段 11：嵌入 TrailShare 独立小应用）。可选，不传 = 不显示按钮。 */
+  onShareTrip?: (trip: TripView) => void;
+  onShareTrip,
+                            {onShareTrip ? (
+                              <Button aria-label={`分享行程 ${trip.title}`} onClick={() => onShareTrip(trip)}>
+                                <Share2 className="h-3.5 w-3.5" />
+                              </Button>
+                            ) : null}
```

> 与既有 `onReplayTrip?` 同款"可选 prop"约定；不传时旧行为完全不变（现有抽屉测试无需改）。

### 2.3 `frontend/.env.example`（+6）

```diff
+# 阶段 11：主应用 iframe 嵌入的两个小应用地址（dev 默认值见 StudioEmbed.tsx）。
+VITE_STUDIO_TRACK_URL=http://localhost:5174
+VITE_STUDIO_SHARE_URL=http://localhost:5175
```

### 2.4 `apps/track-studio/src/bridge.ts`、`apps/trailshare-standalone/src/bridge.ts`（各 +40，**经用户批准的唯一越界改动**）

```diff
+function readParentOverride(): string | null {          // 只认 http/https，只取 origin
+  const raw = new URLSearchParams(location.search).get('parentOrigin');
+  … return parsed.origin;
+}
+const PARENT_OVERRIDE = readParentOverride();
 export function allowedOrigins() { … [PARENT_ORIGIN, ...PARENT_OVERRIDE, location.origin] }
 export function targetOrigin() { if (PARENT_OVERRIDE) return PARENT_OVERRIDE; … 原逻辑 }
```

> 为什么必须改：阶段 10 的小应用把父窗口 origin 写死为 `https://app.travelmemory.com`，且 loopback 下把 `ready` 发往**自身** origin。主应用 dev 在 5173、小应用在 5174/5175，跨源导致主应用永远收不到 `ready`。该改动**向后兼容**（不传参时行为与阶段 10 完全一致），生产部署仍走占位域名替换。两个 app 的 `bridge.test.ts` 各 +3 条（合法覆盖、非法值忽略、targetOrigin 覆盖）。

---

## 3. `git diff --stat`

```
 docs/stages/README.md                     |   1 +
 frontend/.env.example                     |   6 ++
 frontend/src/pages/MapPage/MapPage.tsx    |  30 +++++-
 frontend/src/pages/MapPage/TripDrawer.tsx |  20 ++++
 package-lock.json                         | 151 +++++++++++++++++++++++-----
 package.json                              |   4 +-
 6 files changed, 186 insertions(+), 26 deletions(-)
```

- `docs/stages/README.md` / `package.json` / `package-lock.json` 是**阶段 10 遗留**（本轮未动）。
- 本轮新增改动：`.env.example`、`MapPage.tsx`、`TripDrawer.tsx`；新目录 3 个（未跟踪）。
- `frontend/src/services/share/`、`services/crypto/`、`agent/`、`backend/`、`showcase/`、`packages/` **零改动**（QA 用 mtime + `git status` 双查）。
- `apps/` 本轮只有 4 个文件被改：两个 `bridge.ts` + 两个 `bridge.test.ts`。

---

## 4. 主应用有没有 CSP？

**没有。** `frontend/index.html` 无 `<meta http-equiv="Content-Security-Policy">`，`frontend/vite.config.ts` 无 CSP 插件，`frontend/public/` 无 `_headers`。因此**不需要**改 `frame-src`，本阶段对 CSP 零改动。

（生产侧要注意：主应用若不配 CSP，iframe 不受限；若将来加，必须把两个小应用的 origin 写进 `frame-src`。）

---

## 5. 照片从哪里取的

唯一来源是**本机缩略图缓存**（后端只存元数据，原图永不上云）：

```ts
// frontend/src/pages/StudioEmbed/StudioEmbed.tsx
const bytes = await resolveNativeBridge().thumbs.get(photo.id, THUMB_MAX_EDGE); // THUMB_MAX_EDGE=256，从 photoImport 导入
if (!bytes || bytes.byteLength === 0) { skipped += 1; continue; }
const owned = bytes.slice(0);                     // 必须复制：transfer 会 detach 缓存里的同一个 buffer
const thumbnailDataUrl = 'data:image/jpeg;base64,' + toBase64(new Uint8Array(owned));
// → { id, bytes: owned, name, takenAt, lat, lng, thumbnailDataUrl }
```

- `track` 模式：照片库全部；`share` 模式：只取 `trip.items[].photoId`，**保持行程顺序**（`selectPhotos.ts`，纯函数 + 8 条测试）。
- 取不到的计入 `skipped`，顶栏显示「已传入 N 张缩略图（M 张本机无缩略图已跳过）」；一张都没有时显示「暂无可传入的照片：主应用只保存缩略图，若刚换过浏览器需要重新导入照片」。
- 传 `ArrayBuffer` 时用 `transfer` 列表（按对象身份去重），避免复制开销。

---

## 6. 身份从哪里取的

```ts
// frontend/src/pages/StudioEmbed/StudioEmbed.tsx
const identity = loadOrCreateIdentity();          // frontend/src/pages/SharePage/identity.ts
return {
  identity: {
    privHex: identity.privHex,                    // 阶段 10 小应用读这个
    pubkey: identity.pubkey,
    ed25519Priv: identity.privHex,                // 阶段 11 任务书 §6.1 的形状
    ed25519Pub: identity.pubkey,
  },
  photos,
  trips: mode === 'share' && trip ? [trip] : undefined,
};
```

- 种子仍只存在 `SharePage/identity.ts` 的 localStorage（既有实现，本轮未改）；**没有**新增任何 localStorage/IndexedDB 写入（QA grep 确认）。
- 小应用侧收到后只在模块内存使用，不落盘、不回传（阶段 10 的 `setInjectedIdentity` + `sendToParent` 兜底）。

---

## 7. 导回落库的字段映射表（**有损，逐条说明**）

`journeyToTrip` 做两件事：`planJourneyImport()`（纯函数，校验 + 展开）与 `importJourneyToMain()`（网络编排）。

| JourneyBundle 字段 | 主应用落点 | 结果 |
|---|---|---|
| `version` | 校验必须 `journey-2026.1` | ✅ 不符直接抛中文错误 |
| `title` | `createTrip(title)` | ✅ 保留（空则「未命名旅程」） |
| `days[].date` | 无字段可写 | ❌ **丢失**（后端没有"改拍摄时间"接口） |
| `places[].name` / `label` | 无字段可写 | ❌ **丢失** |
| `places[].tag` / `intensity` | 无字段可写 | ❌ **丢失** |
| `photos[].thumbnailDataUrl` | `File` → `importPhotos` → photo id | ✅ 写入照片库（**缩略图，不是原图**） |
| `photos[].takenAt` | 无接口 | ❌ **丢失** |
| `photos[].lat/lng` | `patchPhotoLocation(id, lat, lng)` | ✅ best-effort（失败只计数，不影响整体） |
| `photos[].caption` | 无接口 | ❌ **丢失** |
| `exportedAt` | — | 忽略 |

**结论要说清楚**：导回主应用后得到的是「一个有标题、有缩略图、有坐标的行程」，**地点名/标签/强度/配文/拍摄时间都不在后端模型里**，无法还原。顺序由 `assignTripPhotos(tripId, ids)` 的 `ord` 保留（传参顺序即展示顺序）。

写库顺序：逐张 `importPhotos([file])`（一次一张，避开后端按 hash 去重导致的返回下标错位）→ 全部失败则抛「没有可导入的照片」且**不建空行程** → `createTrip` → `assignTripPhotos` → 逐张补坐标 → 返回统计。任意一步抛错：toast destructive，对话框保留，不清空用户输入。

---

## 8. 所有测试命令与结果

```
$ cd apps/track-studio && npm test
  Test Files  7 passed (7)    Tests  56 passed (56)      ← 53 + bridge 覆盖 parentOrigin 3 条

$ cd ../trailshare-standalone && npm test
  Test Files  8 passed (8)    Tests  69 passed (69)      ← 66 + 3

$ cd ../../frontend && npm run typecheck
  exit 0

$ npm test
  Test Files  36 passed (36)  Tests  402 passed (402)    ← 基线 350 + 43（bridge/selectPhotos/journeyToTrip）+ 6（QA share）+ 3（QA 握手）

$ npm run build -w travel-memory-frontend
  ✓ 1768 modules transformed.  dist 450.31 kB (gzip 152.97 kB)  ✓ built
```

阶段 10 的两个共享包未改动，测试数保持 21 / 56。全仓相关总数：**402 + 56 + 69 + 21 + 56 = 604**。

---

## 9. 手动验收清单（逐条给真实状态）

| # | 项 | 状态 | 证据 / 原因 |
|---|---|---|---|
| 1 | 主应用跑起来（5173） | ✅ | dev server 起停正常，首页渲染 |
| 2 | 两个小应用跑起来（5174/5175） | ✅ | 均 200，首屏渲染 |
| 3 | 地图页有「轨迹演示」按钮 | ✅ | DOM 文本命中（左下工具栏，非顶部） |
| 4 | 点开 → iframe 加载 → 「从主应用相册选」可用 | ⚠️ **部分** | 握手成功、子窗口显示「使用主应用身份（仅内存，不落盘）」；但按钮仍 disabled —— 因为主应用照片库 **0 张**（后端未启动），按 §9.3「photos 非空后可用」这是正确行为。**有照片时的可用态未验证** |
| 5 | 选照片 → 生成 → 播放 → 导出 WebM | ❌ **NOT RUN** | 无照片可传（后端不可达）；WebM 能力本身在阶段 10 已真浏览器验证（34KB vp9） |
| 6 | 导回 → 提示「已保存」→ 行程列表可见 | ❌ **NOT RUN** | 后端不可达；`importJourneyToMain` 仅用注入替身做单测（调用顺序/参数/失败路径） |
| 7 | 关闭 → 重开 → iframe 重新加载 | ✅ | 连开连关 3 轮：每轮「已传入 0 张」、关闭后 studioFrames=0 / dialog=0 |
| 8 | Trip 详情有「分享这个旅程」 | ❌ **NOT RUN** | 无后端 → 无行程 → 按钮不渲染（`onShareTrip` 只在有 trip 的卡片里渲染） |
| 9 | TrailShare 预填这个 Trip | ❌ **做不到** | 阶段 10 的 app 收到 `trips` 但不消费（`BridgeInitPayload.trips?: unknown` 之后从未读取）。要预填必须改 `apps/*/src/App.tsx`，本轮边界不允许 |
| 10 | 生成 `.trailshare` → 下载 | ❌ **NOT RUN（嵌入态）** | 嵌入态需要行程才能进入；独立态在阶段 10 只验证了构建与打包单测，未做浏览器下载 E2E |
| 11 | 关掉小应用 → 5s 后错误卡片 | ✅ | 原文：「握手超时：小应用页面已加载，但 5 秒内没有收到它的就绪信号（ready）。请确认地址可访问：http://localhost:5174。小应用未启动。请运行 npm run dev -w @travelmemory/track-studio」 |
| 12 | `git diff --stat` 只改允许文件 | ✅ | 见 §3 |

---

## 10. 所有 `// TODO(歧义)` 注释

| # | 位置 | 问题 | 当前选择 |
|---|---|---|---|
| 1 | `journeyToTrip.ts:211` | `days[].date` / `photos[].takenAt` 无处安放 | 不写；顺序只靠 `assignTripPhotos` 的 `ord` |
| 2 | `journeyToTrip.ts:215` | `caption` 没有写接口 | 计划里保留字段，不提交 |
| 3 | `apps/track-studio/src/bridge.ts:112` | 无 `?parentOrigin` 时无法区分 harness 与本地主应用 | 保持阶段 10 原逻辑；主应用显式传参 |
| 4 | `apps/trailshare-standalone/src/bridge.ts:97` | 同上 | 同上 |

---

## 11. 已知未完成项（诚实清单）

1. **端到端未验证（环境）**：后端、数据库、docker 全不可用，所以"传真实照片 → 生成 → 导出 → 导回落库 → 列表可见"整条链没有跑过。代码层由单测覆盖，浏览器层只验证到"0 张照片时握手成立"。
2. **share 模式预填做不到**（§9 第 9 条）：需要改阶段 10 的 `App.tsx`，超出本轮批准范围。
3. **导回是有损的**（§7）：地点名/标签/强度/配文/拍摄时间都不进后端模型。
4. **`apps/track-studio/src/ui/Landing.tsx:49`**：已嵌入但照片 0 张时，按钮文案是「从主应用相册选（未嵌入，不可用）」——"未嵌入"的措辞与实情不符（实为"已嵌入、无照片"）。属阶段 10 代码，本轮未改。
5. **DialogContent 自带关闭按钮**：`components/ui/dialog.tsx` 不在允许清单，用 `StudioEmbed.css` 的选择器把默认 X 隐藏；若 Tailwind 升级改类名，会变成两个 X 叠在一起（功能不受影响）。
6. **无 Playwright E2E**：沿用阶段 10 的 CDP 方案（headless Edge + 零依赖脚本），覆盖入口/iframe/握手/关闭/错误卡片/重复开关；真实文件选择器与下载未覆盖。
7. **照片字节未做大包压测**：一次传几百张时的 transfer 与 postMessage 体积未测。

---

## 12. 本阶段被 QA 拦下的真 Bug（值得记录）

**症状**：真浏览器里点开「轨迹演示」后，父窗口永远停在"正在与小应用握手…"，子窗口始终是「临时身份」+「从主应用相册选（未嵌入，不可用）」，连 5s 超时卡片都不出现。

**根因**：`StudioEmbed.tsx` 的建桥 effect 读 `iframeRef.current`，而 iframe 在 Radix `DialogPortal` 里首帧为 null、靠 `useLayoutEffect` 二次渲染才挂载；effect 在挂载前就 flush，`null` 直接 `return`，且依赖数组里没有"iframe 已挂载"的信号 —— 桥永远不会创建，超时定时器也在同一个提前 return 之后。

**为什么单测没发现**：`bridge.ts` 自己的 15 条测试都直接构造 bridge，不经过组件渲染；只有"渲染 + 派发 ready"的接线测试才能碰到这一环。

**修法**：`iframeRef`（useRef）→ callback ref + `useState<HTMLIFrameElement|null>`，建桥 effect 依赖 `iframeEl`；handler 放 `latest` ref 避免每次 render 重建桥。QA 的 3 条回归用例（其中 2 条当时是红的）现在全绿。

**另一处文案缺陷**：小应用未启动时 `iframe.onLoad` 也会触发（浏览器错误页），导致"页面已加载"分支吞掉了启动命令；已改为两条分支都拼 `notRunningText(mode)`，真浏览器复验通过。
