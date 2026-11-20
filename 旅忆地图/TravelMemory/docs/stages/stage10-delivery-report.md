# 阶段 10 交付报告（Track Studio + TrailShare Standalone）

> 执行者：AI 编码代理（软件开发团队 `software-stage10`：工程师 寇豆码 / QA 严过关 / 主理人 齐活林）
> 日期：2026-11-13
> 任务书：[`stage10-track-studio-share-standalone.md`](./stage10-track-studio-share-standalone.md)
> 结论：**四个新目录全部落地且可运行**；196 个单元测试全绿；两个 app 的 `vite build` 通过；WebM 导出、Service Worker 离线、零外部请求三项均在**真浏览器**（Edge 157 headless + CDP）取得证据。

---

## 0. TL;DR

- `apps/track-studio`（5174）：从系统相册导入 → 编辑交通工具 → 用 `packages/trail` 回放 → 导出 WebM。
- `apps/trailshare-standalone`（5175）：导入照片 + 填旅程 → 打包 `.trailshare`（TrailShare `ts-2026.4`）→ 下载；协议实现是 frontend 的**逐字节复制**，并有**逐字节 parity 测试**。
- `packages/journey-transfer` / `packages/photo-source`：两个零内部依赖的纯 TS 包，供两个 app 共用。
- 零后端承诺：CSP（build 注入）+ 最小 Service Worker（离线壳）+ DevTools 教程三层证据齐备。

---

## 1. 新建了哪些文件（完整路径）

合计 **101 个文件**（不含 `node_modules/`、`dist/`）。

### 1.1 `packages/journey-transfer`（9）

```
packages/journey-transfer/package.json
packages/journey-transfer/tsconfig.json
packages/journey-transfer/vitest.config.ts
packages/journey-transfer/src/types.ts
packages/journey-transfer/src/export.ts
packages/journey-transfer/src/import.ts
packages/journey-transfer/src/index.ts
packages/journey-transfer/src/export.test.ts
packages/journey-transfer/src/import.test.ts
```

### 1.2 `packages/photo-source`（13）

```
packages/photo-source/package.json
packages/photo-source/tsconfig.json
packages/photo-source/vitest.config.ts
packages/photo-source/src/types.ts
packages/photo-source/src/thumbnail.ts
packages/photo-source/src/exif.ts
packages/photo-source/src/system-picker.ts
packages/photo-source/src/iframe-bridge.ts
packages/photo-source/src/index.ts
packages/photo-source/src/thumbnail.test.ts
packages/photo-source/src/exif.test.ts
packages/photo-source/src/system-picker.test.ts
packages/photo-source/src/iframe-bridge.test.ts
```

### 1.3 `apps/track-studio`（34）

```
apps/track-studio/package.json
apps/track-studio/tsconfig.json
apps/track-studio/tsconfig.node.json
apps/track-studio/vite.config.ts
apps/track-studio/index.html
apps/track-studio/public/sw.js
apps/track-studio/public/_headers
apps/track-studio/public/embed-harness.html
apps/track-studio/scripts/browser-verify.mjs
apps/track-studio/src/main.tsx
apps/track-studio/src/App.tsx
apps/track-studio/src/styles.css
apps/track-studio/src/env.ts
apps/track-studio/src/csp.ts
apps/track-studio/src/identity.ts
apps/track-studio/src/bridge.ts
apps/track-studio/src/journeyExport.ts
apps/track-studio/src/map/baseMap.ts
apps/track-studio/src/map/controller.ts
apps/track-studio/src/photo/toTrailPhotos.ts
apps/track-studio/src/photo/demoRoute.ts
apps/track-studio/src/video/recorder.ts
apps/track-studio/src/ui/Landing.tsx
apps/track-studio/src/ui/EditPanel.tsx
apps/track-studio/src/ui/PlayStage.tsx
apps/track-studio/src/ui/DonePanel.tsx
apps/track-studio/src/ui/PrivacyNotice.tsx
apps/track-studio/src/csp.test.ts
apps/track-studio/src/bridge.test.ts
apps/track-studio/src/identity.test.ts
apps/track-studio/src/journeyExport.test.ts
apps/track-studio/src/map/controller.test.ts
apps/track-studio/src/photo/toTrailPhotos.test.ts
apps/track-studio/src/video/recorder.test.ts
```

### 1.4 `apps/trailshare-standalone`（45）

```
apps/trailshare-standalone/package.json
apps/trailshare-standalone/tsconfig.json
apps/trailshare-standalone/tsconfig.node.json
apps/trailshare-standalone/vite.config.ts
apps/trailshare-standalone/index.html
apps/trailshare-standalone/public/sw.js
apps/trailshare-standalone/public/_headers
apps/trailshare-standalone/public/embed-harness.html
apps/trailshare-standalone/src/main.tsx
apps/trailshare-standalone/src/App.tsx
apps/trailshare-standalone/src/styles.css
apps/trailshare-standalone/src/csp.ts
apps/trailshare-standalone/src/bridge.ts
apps/trailshare-standalone/src/identity.ts
apps/trailshare-standalone/src/buildTrip.ts
apps/trailshare-standalone/src/packFlow.ts
apps/trailshare-standalone/src/download.ts
apps/trailshare-standalone/src/journeyExport.ts
apps/trailshare-standalone/src/types/share.ts
apps/trailshare-standalone/src/utils/base64.ts
apps/trailshare-standalone/src/utils/binary.ts
apps/trailshare-standalone/src/utils/hash.ts
apps/trailshare-standalone/src/utils/id.ts
apps/trailshare-standalone/src/share/schema.ts
apps/trailshare-standalone/src/share/filter.ts
apps/trailshare-standalone/src/share/pack.ts
apps/trailshare-standalone/src/share/unpack.ts
apps/trailshare-standalone/src/share/prepare.ts
apps/trailshare-standalone/src/share/crypto/keys.ts
apps/trailshare-standalone/src/share/crypto/sign.ts
apps/trailshare-standalone/src/share/crypto/encrypt.ts
apps/trailshare-standalone/src/share/crypto/commitment.ts
apps/trailshare-standalone/src/share/__tests__/parity.test.ts
apps/trailshare-standalone/src/share/__tests__/roundtrip.test.ts
apps/trailshare-standalone/src/share/__tests__/recipients.test.ts
apps/trailshare-standalone/src/share/__tests__/protocol-hardening.test.ts
apps/trailshare-standalone/src/buildTrip.test.ts
apps/trailshare-standalone/src/csp.test.ts
apps/trailshare-standalone/src/bridge.test.ts
apps/trailshare-standalone/src/identity.test.ts
apps/trailshare-standalone/src/ui/Landing.tsx
apps/trailshare-standalone/src/ui/EditPanel.tsx
apps/trailshare-standalone/src/ui/PreviewPanel.tsx
apps/trailshare-standalone/src/ui/DonePanel.tsx
apps/trailshare-standalone/src/ui/PrivacyNotice.tsx
```

### 1.5 文档

```
docs/stages/stage10-track-studio-share-standalone.md   ← 任务书落盘（新建）
docs/stages/stage10-delivery-report.md                 ← 本文件（新建）
```

---

## 2. 修改了哪些现有文件

| 文件 | 改动 | 是否在任务书允许清单内 |
|---|---|---|
| `package.json` | `workspaces` 追加 `apps/track-studio`、`apps/trailshare-standalone` | ✅ 允许（§1.2） |
| `docs/stages/README.md` | 阶段表格加 1 行 stage10 | ✅ 允许（§15.2） |
| `package-lock.json` | `npm install` 自动写入（4 个 workspace 条目 + exifr + @noble 依赖） | ❌ **不在允许清单内，属必要副作用，见 §19.1** |

**`frontend/`、`backend/`、`showcase/`、`packages/trail|geo|amap-loader/`、`docs/` 其余文件：零改动。**

---

## 3. `git diff --stat` 与边界证明

```
$ git diff --stat
 docs/stages/README.md |   1 +
 package-lock.json     | 151 ++++++++++++++++++++++++++++++++++++++++++--------
 package.json          |   4 +-
 3 files changed, 131 insertions(+), 25 deletions(-)

$ git diff package.json
@@ -6,7 +6,9 @@
   "workspaces": [
     "frontend",
     "showcase",
-    "packages/*"
+    "packages/*",
+    "apps/track-studio",
+    "apps/trailshare-standalone"
   ],
```

QA 独立复核（`git diff --name-only`）：只有上述三个文件；已跟踪的 `frontend/`、`backend/`、`showcase/`、`packages/*`、`docs/` 零改动。`git status` 里的 `backend/app/agent/thin.py`、`docs/design/*`、`research/` 等未跟踪文件 mtime 早于本阶段开工时间，**不是本次产物**。

---

## 4. 所有测试命令与结果

### 4.1 单元测试（主理人本轮复跑，`npx vitest run`）

| 目标 | 文件 | 测试 | 结果 |
|---|---|---|---|
| `packages/journey-transfer` | 2 | 21 | ✅ 21 passed |
| `packages/photo-source` | 4 | 56 | ✅ 56 passed |
| `apps/track-studio` | 7 | 53 | ✅ 53 passed |
| `apps/trailshare-standalone` | 8 | 66 | ✅ 66 passed |
| **合计** | **21** | **196** | **✅ 0 failed** |

```
=== packages/journey-transfer ===   Test Files  2 passed (2)   Tests  21 passed (21)
=== packages/photo-source ===       Test Files  4 passed (4)   Tests  56 passed (56)
=== apps/track-studio ===           Test Files  7 passed (7)   Tests  53 passed (53)
=== apps/trailshare-standalone ===  Test Files  8 passed (8)   Tests  66 passed (66)
```

### 4.2 全仓回归（根 `npm test`，确认新 workspace 没拖累既有套件）

```
$ npm test            # → exit 0
frontend   350 passed (350)
showcase    34 passed  (34)
+ 四个新目标 196 passed (196)
```

### 4.3 类型检查

四个包/应用 `npm run typecheck` 均 `exit 0`（`tsc --noEmit`，含 `tsconfig.node.json`）。

### 4.4 构建

```
$ npm run build -w @travelmemory/track-studio
✓ 89 modules transformed.  dist/index.html 1.67 kB / assets 232.68 kB + 75.18 kB(exifr 动态块)
✓ built in 2.89s

$ npm run build -w @travelmemory/trailshare-standalone
✓ 91 modules transformed.  dist/index.html 1.62 kB / assets 235.64 kB + 75.18 kB
✓ built in 4.62s
```

### 4.5 CSP 注入时机（D9）

| 场景 | track-studio | trailshare-standalone |
|---|---|---|
| `vite dev` 首页 `Content-Security-Policy` 计数 | **0**（HTML 里有 `/@vite/client`） | **0** |
| `vite preview`（产物）计数 | **1** | **1** |
| dist 里 `amap` 出现次数 | 允许（`script-src`/`img-src`） | **0**（全 dist 含 `_headers` 零命中） |

---

## 5. 零请求 / 离线 / WebM 的真浏览器证据（替代 Playwright）

仓库没有 Playwright（`node_modules/@playwright/test` 缺失，安装浏览器体积大且不稳定），按任务书 **B8 / C3** 跳过 E2E。QA 改用本机 **Edge 157 headless + CDP**（Node 22 自带 `WebSocket`/`fetch`，零依赖脚本 `apps/track-studio/scripts/browser-verify.mjs`）取得等价证据：

| 验收项 | 结果 | 证据 |
|---|---|---|
| **零请求 · track-studio** | ✅ PASS | 页面加载 + 点击「用演示路线体验」后：`total=4`，`externalHttp=0`，`violations=0`；4 条全是同源 `index.html` / `js` / `css`（另有 1 条 `data:` 缩略图，不是请求） |
| **零请求 · trailshare** | ✅ PASS | `total=3`，`externalHttp=0`；仅同源 `index.html` / `js` / `css` |
| **离线 · track-studio** | ✅ PASS | `navigator.serviceWorker.controller===true`，`caches.keys()===['tm-track-studio-v1']`；`Network.emulateNetworkConditions{offline:true}` 后 `Page.reload`，`#root` 文本 404 字符且含「从系统相册导入」 |
| **离线 · trailshare** | ✅ PASS | 同上，cache `tm-trailshare-v1`，离线重载后 `#root` 422 字符且含关键字 |
| **WebM 真录 · track-studio** | ✅ PASS | 页面上下文 `import('/src/video/recorder.ts')` 调 `recordCanvas`：`{size:34264, type:"video/webm;codecs=vp9"}`；`canRecord()/captureStream/vp9` 均可用 |

复现命令（脚本已入库，`--task` 三选一）：

```bash
# 1) 起服务
npm run dev -w @travelmemory/track-studio          # 5174（WebM 用 dev）
npm run preview -w @travelmemory/track-studio      # 5174（离线/零请求用产物）
npm run preview -w @travelmemory/trailshare-standalone   # 5175

# 2) 起无头浏览器
"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
  --headless=new --disable-gpu --no-sandbox \
  --remote-debugging-port=9222 --user-data-dir=/tmp/tm-cdp about:blank

# 3) 跑验证
node apps/track-studio/scripts/browser-verify.mjs --task=webm     --url=http://localhost:5174/
node apps/track-studio/scripts/browser-verify.mjs --task=offline  --url=http://localhost:5174/
node apps/track-studio/scripts/browser-verify.mjs --task=network  --url=http://localhost:5174/ --clickDemo
node apps/track-studio/scripts/browser-verify.mjs --task=network  --url=http://localhost:5175/
```

**口径说明**：本轮本机**未配置高德 Key**，所以 track-studio 全程走降级格网，`amap.com` 白名单分支未被实际触发（`externalHttp=0` 比验收要求更严）。配置 Key 后瓦片请求会出现，属设计内允许项。

---

## 6. 协议对照测试：测试向量怎么生成的

**结论：没有用"捞 frontend 期望值"的三级逃生，做到了逐字节 parity。**

1. `pack()` 的全部随机性来自 `crypto.getRandomValues`（16B salt、32B 组密钥、X25519 临时私钥、`newId` 的随机字节），而 Ed25519 签名是确定性的 → 替换随机源即可完全复现。
2. 测试用 `Object.defineProperty(globalThis.crypto, 'getRandomValues', ...)` 把随机源换成确定序列：`bytes[i] = (counter++ * 37 + 11) & 0xff`，每次打包前 `counter = 0`。
3. 固定向量：种子 `'11'.repeat(32)`；收件人公钥由 `publicKeyFromSeed('22'.repeat(32))` **现算**（不硬编码猜测）；照片字节为固定小数组；`now = 2026-11-13T00:00:00.000Z`；`shareId = 's_test0001'`；固定 `displayName`。
4. 断言：
   - `JSON.stringify(copy.pkg) === JSON.stringify(original.pkg)`（**通过**）；
   - `from.sig` 相等、`commitment.hash` 相等、`version==='ts-2026.4'`、`chain.seq===0`、`prevHash==='0'.repeat(64)`；
   - **交叉解包**：原版解复制版、复制版解原版，都 `signatureOk===true`、`commitmentOk===true`，地点名/日期/配文/照片字节逐字节相同；
   - **防伪阳性**：恢复随机源后连打两次包，`salt` 必须不同（证明"相同"来自确定性桩，而不是随机根本没工作）。
5. 复制保真度（`diff` 结论）：14 个文件里 **9 个零差异**（`types/share.ts`、4 个 utils、4 个 crypto），5 个只有 import 行不同（schema 4 行、filter 2 行、pack 16 行、unpack 12 行、prepare 2 行）。`showcase/src/map/baseMap.ts` → `apps/track-studio/src/map/baseMap.ts` **零差异**。

---

## 7. 所有 `// TODO(歧义)` 注释（共 6 条）

| # | 位置 | 问题 | 当前选择 |
|---|---|---|---|
| 1 | `packages/journey-transfer/src/export.ts:31` | §11.2 未说"非法 tag"该丢地点还是丢 tag | 保留地点，`tag→leisure` / `intensity→mid`（丢地点会连带丢照片） |
| 2 | `packages/photo-source/src/types.ts:65` | §12 未定义 `PickOptions` | 最小实现 `{ limit?, maxEdge? }` |
| 3 | `apps/track-studio/src/bridge.ts:69` | 无法区分"同源 harness"与"被本地主应用嵌入"（都是 loopback） | 按 loopback 用自身 origin 作 targetOrigin，**绝不使用 `'*'`** |
| 4 | `apps/track-studio/src/journeyExport.ts:15` | 编辑页不采集标签/强度 | 统一 `leisure` / `mid`，留给主应用再改 |
| 5 | `apps/track-studio/src/photo/toTrailPhotos.ts:62` | 没有 `takenAt` 的照片怎么排序 | 伪时间戳 `min(已知) + 序号×60s`，只为排序稳定 |
| 6 | `apps/trailshare-standalone/src/bridge.ts:54` | 同 #3 | 同 #3 |

> 说明：工程师报告中还有一条 `apps/*/src/App.tsx` 的"注入身份 vs §10.3"歧义，已在最后一轮补 §10.2 时消除（改为"注入身份只在内存用、不落盘、不回传"），现存 TODO 为上面 6 条。

---

## 8. 已知未完成项（诚实清单）

1. **Playwright E2E 未执行**（B8/C3 允许）。已用 CDP 真浏览器替代，覆盖零请求、离线、WebM 三项；但**没有**覆盖"真实文件选择器选 5 张带 GPS 的照片 → 完整 UI 流程 → 下载文件"这条链路。
2. **移动端未验证**：`isMobileUA` 分支（引导系统录屏）、iOS Safari 的 `MediaRecorder` 缺失路径、真机触控布局，全部未实测。
3. **Track Studio 播放层被简化**：没有 showcase 的 HUD / 照片卡片 / 时间轴刻度，只有轨迹 + 光点 + 控制器。任务书 §7.1.1 只要求"轨迹渲染 + 播放控制"，但体验比 showcase 素。
4. **TrailShare 没有"打开别人发来的包"的收件人侧 UI**：`unpack()` 存在于代码并用于自检，界面只做"制作信物"（§7.2 范围）。
5. **大包性能**：8MB+ 照片集在 `pack()` 里逐张 AES-GCM，主线程会阻塞数百毫秒；有"正在加密…"文案，**无进度条、无 Worker**。
6. **导出视频无取消按钮**，导出时长 = `42s / 倍速`（1x 约 42 秒，8Mbps 约 40MB）。
7. **身份注入不落盘是设计选择**：注入身份刷新即丢（因为它本来只在内存），UI 只显示来源标签、不显示公钥。
8. **`@noble/hashes` 有两份**：`@noble/curves@2.4.0` 依赖 v2.4，而协议代码 import 的是 v1 子路径（`/sha256`、`/hkdf`），所以 app 内嵌了 `@noble/hashes@1.8.0`。删掉这条依赖会让协议代码找不到模块。
9. **`frame-ancestors` 在 `<meta>` 中会被浏览器忽略**（CSP 规范），真实生效依赖 `public/_headers` 下发的 HTTP 头；部署平台若不用 `_headers`（Netlify 风格），需要自行在 nginx/CDN 配同一条头。

---

## 9. 怎么跑起来

```bash
# 依赖（仓库根跑一次）
npm install

# Track Studio
npm run dev -w @travelmemory/track-studio           # http://localhost:5174
npm run build -w @travelmemory/track-studio && npm run preview -w @travelmemory/track-studio

# TrailShare Standalone
npm run dev -w @travelmemory/trailshare-standalone  # http://localhost:5175
npm run build -w @travelmemory/trailshare-standalone && npm run preview -w @travelmemory/trailshare-standalone

# 测试
npm test -w @travelmemory/journey-transfer
npm test -w @travelmemory/photo-source
npm test -w @travelmemory/track-studio
npm test -w @travelmemory/trailshare-standalone

# 嵌入模式（同源模拟父窗口）
open http://localhost:5174/embed-harness.html
open http://localhost:5175/embed-harness.html
```

可选：根 `.env` 里的 `AMAP_JS_KEY` 需在 `apps/track-studio/.env.local` 里写成 `VITE_AMAP_JS_KEY=...` 才会启用高德底图（当前走降级格网）。
