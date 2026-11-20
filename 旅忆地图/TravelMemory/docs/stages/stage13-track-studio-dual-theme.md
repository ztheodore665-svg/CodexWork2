# 阶段 13 交付报告（双入口双主题 + 主应用指向 embed + showcase 退役 + 代码审计）

> 执行者：AI 编码代理（团队 `software-stage13`：工程师 寇豆码 / QA 严过关 / 主理人 齐活林）
> 日期：2026-11-14
> 任务书：阶段 13 W13-1 ~ W13-7（对话交付，未单独落盘）
> 结论：**七个工作块全部交付**；`756` 条测试全绿（123 + 108 + 414 + 34 + 21 + 56）；三个 app 构建通过；真浏览器验证「独立=深色零网络 / 嵌入=浅色有图」在两个 app 上都成立。
> 遗留：`apps/` 目录仍未被 git 跟踪（无法用 diff 审计，见 §19）；Playwright/E2E 未引入（沿用 CDP）。

---

## §16 实际交付了什么

### 16.1 七个工作块

| 块 | 完成度 | 交付内容 |
|---|---|---|
| W13-1 Track Studio 双入口 + core | **100%** | `index.html`（深色）/ `embed.html`（浅色）、多页构建、按入口注入 CSP、`core/` 六个模块 |
| W13-2 Track Studio 深色 UI | **100%** | 从 showcase 复制 HUD/照片卡/时间轴/SetupPanel/Landing + 演示数据；降级格网（不加载高德）；变速曲线；WebM 导出 |
| W13-3 Track Studio 浅色 UI | **100%** | 现有 UI 迁入 `theme-light/` + **新增浅色调色板**（原样式其实是深色，见 §18-1）；补位置、桥、导回保留 |
| W13-4 TrailShare 双主题 | **100%** | 深色独立（纯文本、无地图无格网）/ 浅色嵌入（PlaceMap）；`src/share/**` 实现零改动，parity 仍逐字节通过 |
| W13-5 主应用指向 `embed.html` | **100%** | `StudioEmbed.tsx:151` 改路径 + 两条 qa 测试补 `pathname === '/embed.html'` |
| W13-6 showcase 退役 | **100%** | `showcase/README.md` 重写为退役说明；`showcase/src/**` 零改动；34 条测试仍绿 |
| W13-7 代码审计 | **100%** | `docs/stages/stage13-code-audit.md`：极低风险合并 **18 项**、中风险记录 **8 条**、高风险记录 **7 条** |

### 16.2 Track Studio 新结构（43 个文件）

```
apps/track-studio/
├── index.html            data-entry="standalone"（深色，零网络，格网）
├── embed.html            data-entry="embed"（浅色，允许高德）
├── public/_headers       / 与 /index.html 无 amap；/embed.html 放行 script/img
├── vite.config.ts        多页 input + 按 ctx.path 注入 CSP
└── src/
    ├── main.tsx          按 data-entry 动态 import 两个 App；SW 仅 standalone
    ├── csp.ts            buildCsp(kind) / injectCsp(html, kind)
    ├── env.ts
    ├── core/             demoRoute / identity / journeyBuild / photoImport /
    │                     speedCurve / validate / videoRecorder（+ 7 个测试）
    ├── theme-dark/       App / styles.css / data/demoJourneys / map/gridMap /
    │                     ui{Hud, PhotoCards, Timeline, SetupPanel, Landing, DonePanel}
    │                     + App.test / entry.test / source.test / PhotoCards.test / Timeline.test
    └── theme-light/      App / bridge / styles.css / map{baseMap, controller} /
                          pages/LocationEditor / ui{Landing, EditPanel, PlayStage,
                          DonePanel, PrivacyNotice, EmbedGuidance}
```

### 16.3 TrailShare 新结构（38 个文件）

```
apps/trailshare-standalone/src/
├── core/          buildTrip / packFlow / receive / identity / download / journeyExport（+ 4 个测试）
├── theme-dark/    App / styles.css / ui{Landing, EditPanel, PreviewPanel, DonePanel,
│                  PrivacyNotice, ReceivePanel, PlaceList}   ← 无 map
├── theme-light/   App / bridge / styles.css / ui{全部 + PlaceBoard + PlaceMap + PlaceList}
├── main.tsx       按 data-entry 动态 import
├── csp.ts / env.ts / types/share.ts / utils/* / share/**    ← 协议层零改动
└── map/amap.ts / planPlaces.ts                              ← 仅浅色版使用
```

### 16.4 关键行为

| 场景 | 结果 |
|---|---|
| `5174/` | 深色 `rgb(4,7,13)`、外部请求 0、`.landing-grid` 格纹、演示播放 HUD 4%→10% / 13km→53km、WebM 真录 **275,641 B**（vp9） |
| `5174/embed.html` 直接开 | 「这个页面需要在主应用内使用。」+ `./index.html`，2s 不跳转 |
| `5174/embed-harness.html` | 握手 ready→init；「使用主应用身份（仅内存，不落盘）」；「从主应用相册选（5 张可用）」 |
| `5175/` | 深色 `rgb(4,7,13)`、外部请求 0、**无 canvas / 无地图 / 无格网** |
| `5175/embed.html` | 浅色 `rgb(247,249,252)`、`.place-map` 存在、无 Key 时「未配置地图服务，仅显示散布示意」 |
| 主应用 5173 → 轨迹演示 | iframe `src=.../embed.html?embedded=1&parentOrigin=…5173`；子窗口浅色 `rgb(247,249,252)` + 注入身份；父窗口「已传入 0 张缩略图」，无超时 |
| 主应用 5173 → 分享旅程 | **NOT RUN**：后端不可达 → 无行程 → 入口不渲染 |

---

## §17 修改了哪些现有文件

### 17.1 `frontend/src/pages/StudioEmbed/StudioEmbed.tsx`（W13-5）

```diff
-  const src = `${base}?embedded=1&parentOrigin=${encodeURIComponent(parentOrigin)}`;
+  const trimmed = base.replace(/\/+$/, '');          // 防 http://host//embed.html
+  const src = `${trimmed}/embed.html?embedded=1&parentOrigin=${encodeURIComponent(parentOrigin)}`;
```

两条 qa 测试各加一条断言（未删原有断言）：

```ts
expect(new URL(src).pathname).toBe('/embed.html');
```

### 17.2 两个 app 的入口/构建文件

| 文件 | 改动 |
|---|---|
| `apps/track-studio/index.html` | `data-entry="standalone"`、补 `html,body,#root{height:100%}` |
| `apps/track-studio/embed.html` | **新增**：浅色骨架（`theme-color #f7f9fc`、`color-scheme light`） |
| `apps/track-studio/vite.config.ts` | 多页 `input`、CSP 按 `ctx.path` 选 kind、test include 加 `*.test.tsx` |
| `apps/track-studio/src/csp.ts` | `buildCsp(kind)` / `injectCsp(html, kind)`；standalone 无 amap |
| `apps/track-studio/src/main.tsx` | 按 `data-entry` 动态 import；SW 仅 standalone |
| `apps/track-studio/public/_headers` | 三分段（`/`、`/index.html`、`/embed.html`） |
| `apps/track-studio/public/embed-harness.html` | iframe → `./embed.html?embedded=1`；身份 payload `seed` → `privHex` |
| `apps/trailshare-standalone/index.html` | `data-entry="standalone"`；骨架保持深色 |
| `apps/trailshare-standalone/embed.html` | 骨架改浅色（`#f7f9fc`、`color-scheme light`） |
| `apps/trailshare-standalone/src/main.tsx` | 按 `data-entry` 动态 import；SW 仅 standalone |
| `apps/trailshare-standalone/public/embed-harness.html` | iframe → `./embed.html?embedded=1` |

### 17.3 `showcase/README.md`

重写为退役说明（178 行变更，正文替换、非追加）：阶段 13 退役、UI 能力已合并到 `apps/track-studio` 深色入口、代码保留不再维护、仍可启动但优先用 Track Studio。
`showcase/src/**` **零改动**（QA 用 `git status` 复核），`showcase/package.json` 未动。

### 17.4 W13-7 极低风险（18 项）

分布在 trailshare 12 项、track-studio 2 项、frontend 3 项、frontend 测试 1 项。类型只有三类：**同一模块的重复 import 合并**（15 项）、**连续空行压缩**（2 项，`theme-dark/styles.css` 与 `frontend/src/styles.css`）、**同文件重复 type import 合并**（1 项）。
中风险 8 条（如 `services/api/trips.ts` 的 JSON 头重复、`TripDrawer` 长 Tailwind 串）、高风险 7 条（如 track 两套 UI 主循环重复、`share/**` ↔ `frontend/services/share/**` 双实现）**只记录未动**。

### 17.5 一处必须披露的越界

`apps/trailshare-standalone/src/share/__tests__/recipients.test.ts` 改了 **2 行 import**（`../../packFlow` → `../../core/packFlow`、`../../buildTrip` → `../../core/buildTrip`），因为这两个模块按目标树搬进了 `core/`。**协议实现（schema/pack/unpack/crypto/filter/prepare）零改动**，parity 测试仍逐字节通过（21 条 share 子集全绿）。若要求 `share/**` 绝对零改动，可改成在 `src/` 留两个 re-export 薄壳，但会多出目标树里没有的文件——本轮按"最小改动"选择改测试 import，并在此披露。

### 17.6 `git diff --stat`

```
 .env.example                                       |   4 +
 docs/stages/README.md                              |   6 +-
 frontend/.env.example                              |   6 +
 frontend/src/pages/MapPage/MapPage.tsx             | 100 ++++-
 frontend/src/pages/MapPage/PhotoLocationDialog.tsx |   3 +-
 frontend/src/pages/MapPage/TripDrawer.tsx          |  20 +
 frontend/src/styles.css                            |  10 -
 package-lock.json                                  | 443 ++++++++++++++-------
 package.json                                       |   4 +-
 showcase/README.md                                 | 178 ++++-----
 10 files changed, 504 insertions(+), 270 deletions(-)
```

> `MapPage.tsx` / `TripDrawer.tsx` / `.env.example` / `package.json` / `package-lock.json` 是阶段 10–12 遗留；本轮 tracked 改动是 `StudioEmbed`（未跟踪目录内）、`PhotoLocationDialog.tsx`（W13-7 import 合并）、`frontend/styles.css`（空行）、`showcase/README.md`（退役）。

---

## §18 踩过的坑

1. **`theme-light` 名不副实（P0，QA 拦下）**：Track Studio 原 UI 本身就是深色（`--bg:#04070d`），"浅色"只是设计文档里对嵌入版的称呼。第一版照搬后 `embed.html` 实测 `rgb(4,7,13)`，双主题等于没做。修法：在 `.theme-light` 作用域覆盖 11 个变量 + 逐条覆盖 10 处硬编码深色（97 行补丁），`embed.html` 骨架同步改浅。**例外**：播放页 `.stage` 保留暗场（压在 `amap://styles/dark` 底图 + 发光轨迹上，刷浅会丢层次），已在 CSS 注释里写明。
2. **harness 身份键错（P1，stage 10 遗留）**：`embed-harness.html` 发 `{ seed: TEST_SEED }`，而 `core/identity.ts` 只认 `privHex`，所以本地永远验不到"使用主应用身份"。QA 用对照实验证伪（换 `privHex` 立刻生效、换回 `seed` 立刻退回临时身份），应用本身是对的。已改 `privHex`。
3. **harness 入口错位**：双入口后 `/` 是深色独立版（没有桥），harness 仍指 `/?embedded=1`，会加载错主题。两个 app 的 harness 都改成 `./embed.html?embedded=1`。
4. **Track 的 embed 直接访问缺整页引导**（我派单时漏写的验收项）：只有一个按钮 tooltip。补 `EmbedGuidance.tsx` + 3 条测试（含"源码里没有 location 跳转"的断言）。
5. **W13-7 候选极少**：五个 workspace 的 tsconfig 早已开 `noUnusedLocals/noUnusedParameters`，`tsc` 全绿、`console.log` 残留 0，所以 18 项几乎全是"重复 import 合并 + 空行"。这不是没扫，是存量本来就干净。
6. **`apps/` 未被 git 跟踪**：QA 无法用 diff 证明"协议层/断言零改动"，只能用 mtime 佐证（share 实现停在 11-13 00:28，本轮只有 recipients.test.ts 变动）。
7. **深浅两套 UI 的样式同源 90%**：机械加作用域前缀能过，但后续任何视觉改动都要改两处——已作为高风险项记入 audit，没有在本轮合并。

---

## §19 未完成项与差异清单

### 19.1 未完成 / 需后续处理

1. **`apps/`、`packages/journey-transfer`、`packages/photo-source` 仍未纳入 git**。本轮所有"零改动"只能靠 mtime 自证。建议先做一次基线提交（`git add apps packages/journey-transfer packages/photo-source`），否则阶段 14 起仍然无法审计 diff。
2. **主应用"分享这个旅程"未端到端验证**：后端不可达 → 无行程 → 入口不渲染。W13-5 的 share 分支只验到"代码路径与单测"，真浏览器未走。
3. **真机 MediaRecorder 未验**：CDP 里 `recordCanvas` 真录出 275KB vp9，但移动端/真机编码器差异未覆盖。
4. **Track 深色版 `SetupPanel` 未移植"每段交通工具"**：全部按默认自驾（`core/photoImport` 的 `transports` 参数已支持恢复）。
5. **Track 深色版 `journey.json` 导出未接本机身份**：`buildJourneyBundle` 本身不签名，独立版也没有"导回主应用"。
6. **TrailShare 深色版 Landing 保留 `identitySource === 'injected'` 判断**（不可达，为与浅色版同形）；`identityNote` 恒为 null。
7. **浅色版播放页 `.stage` 仍是暗场**（设计选择，见 §18-1）；若产品要求"整页浅色"，改一个 CSS 块即可。
8. **showcase 保留**：代码、测试、启动脚本都在，只标记退役。

### 19.2 与任务书的差异

| # | 差异 | 原因 | 影响 |
|---|---|---|---|
| D-1 | `share/__tests__/recipients.test.ts` 改 2 行 import | 模块搬进 `core/` 的连带；协议实现零改动 | parity 仍绿；已在 §17.5 披露 |
| D-2 | Track Studio 浅色是**新增调色板**，不是"现有代码原样迁入" | 现有 UI 实为深色，与 H2"嵌入=浅色"冲突 | 视觉变化；播放页保留暗场 |
| D-3 | 两个 harness 的 iframe src 从 `/?embedded=1` 改为 `./embed.html?embedded=1` | 双入口后 `/` 是深色版，harness 会加载错主题 | 仅影响本地开发工具 |
| D-4 | W13-7 合并 18 项、记录 15 条 | 存量已无 `noUnusedLocals` 违规 | 无行为变化 |
| D-5 | `showcase/README.md` 是整篇重写而非追加 | 原文是使用说明，退役后需要新的首屏说明 | 历史细节仍可在 git 历史里查 |

---

## §14 提交前检查清单（逐条回答）

1. **新增文件**：见 §16.2 / §16.3 的目录树（Track 43 个、TrailShare 38 个、frontend 未新增、文档 2 个：本文件与 `stage13-code-audit.md`）。
2. **修改的现有文件**：见 §17.1–17.6（含 diff 片段）。
3. **`git diff --stat`**：见 §17.6。
4. **七个工作块完成度**：全部 100%，见 §16.1。
5. **测试命令与结果**：
```
track-studio    17 files / 123 passed
trailshare      12 files / 108 passed
frontend        37 files / 414 passed
showcase         4 files /  34 passed
journey-transfer 2 files /  21 passed
photo-source     4 files /  56 passed
────────────────────────────────
合计                             756 passed，0 failed
根 typecheck: exit 0；三个 app build: exit 0
```
6. **手动验收**：见 §16.4；其中"主应用分享旅程"NOT RUN（后端不可达）。
7. **Track Studio 独立版零网络**：CDP `Network.enable` + reload → 外部 http(s) 请求 **0**、amap **0**；`dist/index.html` CSP 无任何地图域名。
8. **TrailShare 独立版零网络**：同上，外部请求 **0**；页面无 canvas / `.place-map` / "未配置地图服务"。
9. **`grep -r "amap" theme-dark`**：两个 app 均 **0**（排除 TODO 注释）。
10. **`src/share/` 与 `showcase/src/`**：实现零改动；唯一例外是 `share/__tests__/recipients.test.ts` 的 2 行 import（§17.5）。
11. **主应用两个 iframe src**：均为 `${base}/embed.html?embedded=1&parentOrigin=…`；track 分支真浏览器已验证，share 分支未端到端（无行程）。
12. **audit 三档清单**：`docs/stages/stage13-code-audit.md`，18 / 8 / 7，含扫描手段与结论。
13. **合并统计**：18 项极低风险（每批合并后跑对应 workspace 全套，无回滚）；中风险 8、高风险 7。
14. **`// TODO(歧义)`**：本轮新增 3 条（Track 深色 SetupPanel 交通工具、深色 journey.json 身份、浅色播放页暗场例外说明）；另有既往 4 条未动。
15. **已知未完成项**：见 §19.1（8 条）。
