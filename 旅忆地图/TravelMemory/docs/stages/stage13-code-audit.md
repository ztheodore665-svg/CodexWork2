# 阶段 13 代码审计清单

> 执行者：工程师 寇豆码（团队 `software-stage13`）
> 范围：`apps/track-studio/src/`、`apps/trailshare-standalone/src/`（跳过 `src/share/**`）、
> `frontend/src/`（跳过 `agent/`、`services/share/`、`services/crypto/`）、
> `packages/journey-transfer/`、`packages/photo-source/`
> 原则：**只合并"极低风险"**；中/高风险只记录，不动代码。每批合并后立刻跑该 workspace 全套测试。

## 扫描手段（为什么候选这么少）

五个 workspace 的 `tsconfig.json` **本来就开着** `noUnusedLocals` / `noUnusedParameters`，
所以 `npx tsc --noEmit --noUnusedLocals --noUnusedParameters -p <tsconfig>` 在全部五个 workspace
上都返回 **0 条** —— 未使用的 import / 局部变量在 CI 阶段就被拦住了，没有留给审计的存量。

于是实际候选来自三类手工扫描（脚本如下，全部只读）：

1. 连续空行：`/\n(?:[ \t]*\n){3,}/` 统计每个文件里"≥3 个空行"的段数；
2. 同文件重复字面常量：只认完整字符串字面量（`'xxx'` / `"xxx"`，无空格，长度 ≥ 6），按出现次数排序，
   再人工排除 `typeof x === 'number'` / `'undefined'` / JSX className 这类"惯用重复"；
3. 同文件重复 import 源：同一个模块被 `import type {...}` 与 `import {...}` 引用了两次；
4. `console.log(` 调试残留：全部 workspace 非测试文件 **0 处**（`console.warn/error` 未动，
   `window.__tmLast` 这类 DEV 探针未动）；
5. 注释错别字：中英文常见错字词表（`recieve/seperate/occured/...`）+ 中文叠字（`的的/了了/。。`）
   全部 workspace **0 处**。

---

## 极低风险（已自动合并）

| # | 位置 | 问题 | 处理 | 结果 |
|---|---|---|---|---|
| 1 | `apps/trailshare-standalone/src/core/buildTrip.ts` | `../types/share` 被 type / value 两行分别 import | 合并为一条带 `type` 修饰符的 import | trailshare 108 绿 |
| 2 | `…/theme-dark/ui/DonePanel.tsx` | `../../core/packFlow` 重复 import 两次 | 合并为 `import { formatBytes, type PackJourneyResult }` | trailshare 108 绿 |
| 3 | `…/theme-dark/ui/EditPanel.tsx` | `../../core/identity` 重复 import 两次（不相邻） | 合并到上方那行，删掉下方那行 | trailshare 108 绿 |
| 4 | `…/theme-dark/ui/Landing.tsx` | 同上 | 合并为 `import { shortPubkey, type IdentitySource, type LocalIdentity }` | trailshare 108 绿 |
| 5 | `…/theme-dark/ui/PlaceList.tsx` | `../../types/share` 重复 import 两次 | 合并为一条（值 + `type UnpackedPlace`） | trailshare 108 绿 |
| 6 | `…/theme-dark/ui/PreviewPanel.tsx` | `../../core/buildTrip` 重复 import 两次 | 合并为 `import { validateDraft, type TripDraft }` | trailshare 108 绿 |
| 7 | `…/theme-light/App.tsx` | `../core/identity` 重复 import 两次 | 合并为一条（5 个符号） | trailshare 108 绿 |
| 8 | `…/theme-light/ui/DonePanel.tsx` | `../../core/packFlow` 重复 import 两次 | 合并 | trailshare 108 绿 |
| 9 | `…/theme-light/ui/EditPanel.tsx` | `../../core/identity` 重复 import 两次（不相邻） | 合并 | trailshare 108 绿 |
| 10 | `…/theme-light/ui/Landing.tsx` | `../../core/identity` 重复 import 两次 | 合并 | trailshare 108 绿 |
| 11 | `…/theme-light/ui/PlaceList.tsx` | `../../types/share` 重复 import 两次 | 合并 | trailshare 108 绿 |
| 12 | `…/theme-light/ui/PreviewPanel.tsx` | `../../types/share` 与 `../../core/buildTrip` 各重复一次 | 两组分别合并 | trailshare 108 绿 |
| 13 | `apps/track-studio/src/theme-light/ui/Landing.tsx` | `../../core/identity` 重复 import 两次 | 合并为一条 | track-studio 116 绿 |
| 14 | `apps/track-studio/src/theme-dark/styles.css` | 文件头注释后有 4 个连续空行（我上一批删 `@tailwind` 留下的痕迹） | 压成 1 个空行 | track-studio 116 绿 |
| 15 | `frontend/src/styles.css` | `.meta-row .refused` 与 `.composer` 之间有 11 个连续空行 | 压成 1 个空行 | frontend 414 绿 |
| 16 | `frontend/src/pages/LocationEditor/LocationEditor.tsx` | `../../services/api/photos` 重复 import 两次 | 合并 | frontend 414 绿 |
| 17 | `frontend/src/pages/MapPage/PhotoLocationDialog.tsx` | 同上 | 合并 | frontend 414 绿 |
| 18 | `frontend/src/services/journey-import/journeyToTrip.test.ts` | `../../services/api/photos` 的 type import 拆成两行 | 合并为一条 type import（**未改任何断言**） | frontend 414 绿 |

合计 **18 项**（trailshare 12、track-studio 2、frontend 4）。全部为"零行为"改动：
要么是把同一模块的两条 import 合成一条，要么是把连续空行压掉。

**合并失败已回滚**：无（18 项全部一次通过）。

---

## 中风险（已记录，未动）

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| M1 | `frontend/src/services/api/trips.ts` | `'Content-Type'` 与 `'application/json'` 各出现 3 次 | 收敛成 `jsonHeaders()` 一个 helper。涉及请求构造，属结构性改动，且要逐处确认没有一处用的是别的 content-type |
| M2 | `frontend/src/pages/MapPage/TripDrawer.tsx` | 长 Tailwind 串 `h-7 w-7 p-0 text-muted-foreground hover:text-foreground` ×3、`h-3.5 w-3.5` ×6 | 抽成模块内常量能减重复，但 Tailwind 串抽常量会牺牲"看 className 就知道长什么样"，且该文件正被别的阶段改动中，先别动 |
| M3 | 各 `EditPanel` / `DonePanel` 里的 `className="btn btn-tiny"` 一类重复 | 同一文件内重复 3~5 次 | 属 JSX 属性字面量，抽常量收益低于可读性损失；只在样式体系重构时顺手做 |
| M4 | `apps/track-studio/src/core/identity.ts`、`apps/trailshare-standalone/src/core/identity.ts` 的 `bytesToHex` / `hexToBytes` | 包内自用、包外无调用方（`utils/binary.ts` 里另有同实现） | 属"疑似未使用的导出"，规则明确不算极低风险（可能是对外契约 / 测试 fixture）。真要收敛应把编解码统一到 `utils/binary`，需跨文件评估 |
| M5 | `apps/trailshare-standalone/src/core/identity.ts` vs `apps/track-studio/src/core/identity.ts` | 两份近乎相同的身份模块（种子 → 公钥 → IndexedDB） | 抽到 `packages/` 是长期正解，但**跨包移动代码本批次禁止**，且要重新做两个 app 的身份迁移测试 |
| M6 | `packages/photo-source/src/system-picker.ts` | `typeof x === 'undefined'` 出现 7 次 | 惯用写法（Node/browser 双端守卫），抽常量反而更难读 |
| M7 | `frontend/src/services/native/permissions.ts` | `'prompt'` / `'granted'` / `'denied'` / `'unsupported'` 多次出现 | 已有字面量联合类型约束，抽 const 只是把类型再抄一遍 |
| M8 | `apps/track-studio/src/theme-dark/ui/SetupPanel.tsx` 的 `"setup-hint"`、`"photo-cell"` 等 class 串 | 同一文件内重复 | 同 M3，等样式体系重构时统一 |

---

## 高风险（已记录，未动）

| # | 位置 | 问题 | 为什么不动 |
|---|---|---|---|
| H1 | `apps/track-studio/src/theme-dark/{App.tsx,ui/*}` ↔ `theme-light/{ui/PlayStage.tsx,ui/*,styles.css}` | 整套 UI + 主循环 + 样式表是**复制关系**（PlayStage 441 行 ↔ theme-dark App 里的 PlayStage 约 300 行，styles.css 90% 同源） | 合并要抽"播放舞台"共享层并引入主题上下文；主循环里每一条（25Hz 下发、rAF 计数、揭幕时序）都是踩坑踩出来的，任何重构都必须真机逐帧回归。这是**架构级改动**，不是代码整理 |
| H2 | `apps/trailshare-standalone/src/share/**` ↔ `frontend/src/services/share/**` | 协议**双实现**（parity 测试逐字节比对，双方必须一致） | 合并会让零后端小应用被迫打包主应用的网络层，直接违反"独立部署"承诺；本批次也明令禁止改 `src/share/**` |
| H3 | `apps/trailshare-standalone/src/theme-light/ui/{EditPanel,PreviewPanel,ReceivePanel,DonePanel,Landing}.tsx` ↔ `theme-dark/ui/` 同名文件 | 3~5 组"深/浅两份 UI"，差异只有 1~2 个分支（embedded / 地图 / 导回主应用） | 合并需要把"主题"提升为运行时上下文（Context 或 props 注入），属于架构改动；现在两份共 1751 行，合并省下的重复不抵一次回归风险 |
| H4 | `frontend/src/pages/MapPage/replay/{PhotoCards,Hud,Timeline}` ↔ `apps/track-studio/src/theme-dark/ui/` 同名文件 | 主应用那份已经独立演进（多出 replay 子目录与自己的卡片策略） | 跨应用对齐需要产品先定"两处视觉是否要一致"，且主应用那份有 413→414 条测试兜着的既有行为 |
| H5 | `apps/trailshare-standalone/src/share/schema.ts` / `frontend/src/services/share/schema.ts` | `'string'` 字面量各出现 20 次 | 全是手写 schema 里的 `typeof x === 'string'` 分支，抽常量会破坏"一眼看出校验形状"的可读性 |
| H6 | `apps/trailshare-standalone/src/theme-*/ui/*` 里"重复类型定义"（如两处 `IntensityBucket` 的用法） | 类型层重复 | 规则明确排除"重复类型定义"，且类型合并会牵动 `types/share.ts` 契约 |
| H7 | 各 workspace 的"看起来像 dead code"的分支（如深色版 `Landing` 里 `identitySource === 'injected'` 恒假） | 行为判断置信度低 | 规则明确"任何涉及行为判断的看起来像 dead code 都不算极低风险"；这批分支是为两个主题保持同形，删掉会让下次合并 diff 变大 |

---

## 结论

- 五个 workspace 的"未使用符号"存量已经是 **0**（`noUnusedLocals` 一直开着），
  本次审计能安全自动化的只有 **重复 import 源（17 条）+ 连续空行（2 处）**。
- 真正值得做的整理都在中/高风险表里，其中 **H1 / H3（两份 UI）** 与 **H2（协议双实现）**
  是阶段 14 的候选，需要单独立项 + 真机回归，不适合在一个"只合极低风险"的批次里动。
