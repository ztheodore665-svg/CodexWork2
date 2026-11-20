/** 主应用 ↔ 小应用（iframe）的消息契约（阶段 11）。
 *
 * ## 为什么不直接 import `apps/*` 的类型
 *
 * `apps/track-studio` 与 `apps/trailshare-standalone` 是**独立的 workspace**，
 * 主应用（`frontend`）一行都不该依赖它们：两个 app 可以单独发布、单独升级，
 * 也可以被删掉而主应用照常构建。所以这里按阶段 10 任务书 §9.2 的**线格式**
 * 重新声明一份结构（字段名逐字对齐），让"结构兼容"代替"包依赖"。
 *
 * 代价是改协议要同时动两处；收益是 `frontend` 的构建不依赖 `apps/` 是否存在 ——
 * 这条边界比省下这几十行类型更重要。
 *
 * ## 字段为什么长这样
 *
 * - `StudioPhoto` 与阶段 10 的 `SystemPhoto`（`@travelmemory/photo-source`）字段一致：
 *   小应用那边的 `parseBridgePhotos()` 就按 `id` / `bytes` / `name` /
 *   `thumbnailDataUrl` / `takenAt` / `lat` / `lng` 逐个校验，多一个少一个都会降级。
 * - `StudioIdentity` **同时**带 `privHex` / `pubkey` 与 `ed25519Priv` / `ed25519Pub`：
 *   阶段 10 已落地的小应用只认前者（见 `apps/*\/src/identity.ts` 的
 *   `setInjectedIdentity`），而阶段 11 任务书 §6.1 写的是后者。
 *   两个键名都发，注入在两种实现下都能生效；多两个字符串的代价可以忽略。
 */

/** 嵌入哪一个小应用。 */
export type StudioMode = 'track' | 'share';

/**
 * 传给 iframe 的一张照片。
 *
 * `bytes` 是**缩略图**字节（不是原图）：主应用只在本机缓存缩略图
 * （`resolveNativeBridge().thumbs`），原图在后端资产里，见任务书 §2.3。
 */
export interface StudioPhoto {
  id: string;
  /** 缩略图 JPEG 字节。会被 `postMessage` 以 transfer 方式**移交**（移交后本地失效）。 */
  bytes: ArrayBuffer;
  /** 文件名。小应用会用它推断"地点名"，所以这里给一个可读的名字。 */
  name: string;
  takenAt?: string;
  lat?: number;
  lng?: number;
  /** 同一张缩略图的 `data:image/jpeg;base64,` 形式，给小应用直接当 `<img src>`。 */
  thumbnailDataUrl: string;
}

/**
 * 注入给小应用的身份。
 *
 * **只关注键名，不关心敏感度**：这里装的确实是主应用的 Ed25519 种子。
 * 主应用的种子本来就在本机 localStorage（`pages/SharePage/identity.ts`），
 * 而小应用运行在**另一个 origin**、只把它放在模块内存里、不落盘、也不回发
 * （阶段 10 的 `setInjectedIdentity` 与 `sendToParent` 的值检查兜底，
 * 见 `docs/dev-report-stage10.md` §7 / §8.7）。这条链路是"传一次"而不是"复制一份"。
 */
export interface StudioIdentity {
  /** 阶段 10 小应用认这个键。 */
  privHex: string;
  pubkey: string;
  /** 阶段 11 任务书 §6.1 的键名，与上面同值。 */
  ed25519Priv: string;
  ed25519Pub: string;
}

/** 父 → 子 `init` 消息的载荷。 */
export interface StudioInitPayload {
  identity?: StudioIdentity;
  photos?: StudioPhoto[];
  /**
   * 要分享的行程（share 模式传 `[trip]`）。
   *
   * 阶段 10 的两个小应用目前**不消费**这个字段（`BridgeInitPayload` 里只声明了
   * `trips?: unknown` 就再没读过），所以这里如实传过去、如实不指望它生效 ——
   * 预填表单是下一阶段的事。见交付报告"已知降级项"。
   */
  trips?: unknown[];
}

/** 子 → 父的消息（阶段 10 §9.2）。 */
export type StudioChildMessage =
  | { type: 'ready' }
  | { type: 'import-to-main'; journey: unknown }
  | { type: 'download'; blob: Blob; filename: string }
  | { type: 'error'; message: string };
