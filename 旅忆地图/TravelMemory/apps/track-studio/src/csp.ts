/** 内容安全策略（CSP）的唯一定义处 —— 现在按**入口**给两套策略。
 *
 * ## 为什么是纯函数 + 单独文件
 *
 * CSP 是"零后端承诺"的第一层证据（§8.1）：产物里的 `connect-src 'self'` 让浏览器
 * 自己拒绝一切外部请求 —— 用户不需要相信我们的文案，看 Network 面板即可。
 * 一个字符串拼错（少个分号、多了个域名）就会让这条证据失效，而且**浏览器不会报错**，
 * 只是静默放宽。所以它必须能被单测钉住。
 *
 * ## 阶段 13 / W13-1：两个入口，两套策略
 *
 * 应用现在有两个 HTML 入口，它们的**地图策略根本不同**：
 *
 * | 入口 | 文件 | 底图 | script/img 要不要放行高德 |
 * |---|---|---|---|
 * | `standalone` | `index.html` | 自绘降级格网（深色版，零网络） | **完全不放行** |
 * | `embed` | `embed.html` | 高德 JS API（浅色版，允许联网底图） | 放行 `webapi.amap.com` / `*.amap.com` |
 *
 * 这是本次改动最重要的一条：**独立入口一行高德域名都不许出现**。
 * 之前所有入口共用一份带高德的 CSP，等于把"独立版可以联网"这个口子一直开着；
 * 现在由入口决定，且由 `csp.test.ts` + 构建后的 grep 双向钉住。
 *
 * ## D9：只在 build 注入
 *
 * `vite dev` 下 Vite 用内联脚本 + WebSocket 做 HMR，注入 CSP 会把 HMR 打死
 * （`script-src 'self'` 拒绝内联、`connect-src 'self'` 拒绝 HMR 的 ws）。
 * 所以插件写 `apply: 'build'`，dev 完全不注入，产物带 CSP。
 *
 * ## ⚠️ `frame-ancestors` 在 `<meta>` 里无效
 *
 * CSP 规范明确规定：通过 `<meta http-equiv>` 下发时，`frame-ancestors` /
 * `report-uri` / `sandbox` 会被**忽略**（不是报错，是静默忽略）。也就是说
 * meta 版的 CSP 挡不住"被别人 iframe 嵌套"这一条。
 *
 * 处理：① meta 里照写（无害，且语义上把完整策略留在了产物里，便于人工审计）；
 * ② 同时提供 `public/_headers`，把同一份策略作为**真实 HTTP 头**下发，
 * 那才是 `frame-ancestors` 生效的地方。两个入口各一段。
 */

/** 入口种类。`standalone` = 深色独立版（零网络）；`embed` = 浅色嵌入版（允许高德底图）。 */
export type EntryKind = 'standalone' | 'embed';

/** 高德 JS API 的脚本源。**只有 `embed` 入口**会放行。 */
const AMAP_SCRIPT_ORIGIN = 'https://webapi.amap.com';
/** 高德瓦片 / 图标的图片源（`*.amap.com` 覆盖 webst01~04、restapi 等）。 */
const AMAP_IMAGE_ORIGIN = 'https://*.amap.com';
/** 主应用域名。**部署时替换**（见任务书 §8.1 与"部署前待替换的占位符"表）。 */
export const PARENT_ORIGIN = 'https://app.travelmemory.com';

/** `<meta>` 注入时用来判重，避免同一个 HTML 里插两份 CSP。 */
export const CSP_META_MARKER = 'http-equiv="Content-Security-Policy"';

/**
 * 拼出某个入口的完整 CSP。
 *
 * 两个入口**逐字相同**的指令：
 * - `default-src 'self'`：默认只允许同源；
 * - `style-src` 留 `'unsafe-inline'`：过渡/定位需要我们动态写 `style` 属性（React 内联样式），
 *   而 `style` 属性受 `style-src-attr`/`'unsafe-inline'` 管；这是有意的、最小的放宽；
 * - `connect-src 'self'` 是零后端的核心：fetch/XHR/WebSocket 一律只允许同源；
 * - `media-src` 允许 `blob:`（视频预览是 blob URL）；
 * - `frame-ancestors` 只允许自身与主应用 —— 注意 meta 里无效，见文件头。
 *
 * 差异只有两处：`embed` 额外放行高德的脚本源与图片源；`standalone` 一个都不放。
 *
 * @param kind 入口种类。缺省 `'standalone'`（**fail-closed**：忘了传就按最严的来）
 */
export function buildCsp(kind: EntryKind = 'standalone'): string {
  const scriptSrc =
    kind === 'embed' ? `script-src 'self' ${AMAP_SCRIPT_ORIGIN}` : "script-src 'self'";
  const imgSrc =
    kind === 'embed'
      ? `img-src 'self' blob: data: ${AMAP_IMAGE_ORIGIN}`
      : "img-src 'self' blob: data:";
  return [
    "default-src 'self'",
    scriptSrc,
    "style-src 'self' 'unsafe-inline'",
    imgSrc,
    "connect-src 'self'",
    "font-src 'self'",
    "media-src 'self' blob:",
    "worker-src 'self'",
    `frame-ancestors 'self' ${PARENT_ORIGIN}`,
  ].join('; ');
}

/**
 * 把某个入口的 CSP 以 `<meta>` 形式插到 `</head>` 之前。
 *
 * 幂等：已经有一份 CSP meta 时原样返回（重复插入会让浏览器按"多份策略取交集"
 * 处理，多一条无效指令就可能让整条策略失效，很难查）。
 *
 * @param html 产物的 HTML 文本
 * @param kind 入口种类（决定放不放行高德）
 * @returns 注入了 CSP meta 的 HTML；找不到 `</head>` 时原样返回（无法安全插入）
 */
export function injectCsp(html: string, kind: EntryKind = 'standalone'): string {
  if (html.includes(CSP_META_MARKER)) return html;
  const headEnd = html.toLowerCase().indexOf('</head>');
  if (headEnd < 0) return html;
  const meta = `<meta http-equiv="Content-Security-Policy" content="${buildCsp(kind)}" />`;
  return `${html.slice(0, headEnd)}    ${meta}\n  ${html.slice(headEnd)}`;
}
