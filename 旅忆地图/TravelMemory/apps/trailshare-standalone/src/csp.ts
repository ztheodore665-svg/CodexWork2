/** 内容安全策略（CSP）的唯一定义处 —— TrailShare 版（W12-4：**两个入口两套策略**）。
 *
 * ## 为什么必须分两套
 *
 * 这个应用有两个产物入口，承诺完全不同：
 *
 * | 入口 | HTML | 承诺 | CSP |
 * | --- | --- | --- | --- |
 * | standalone | `index.html` | **全程零网络请求** | 一个地图域名都没有 |
 * | embed | `embed.html` | 除地图底图外零网络请求 | 只放行脚本 `webapi.amap.com` 与图片 `*.amap.com` |
 *
 * standalone 的 CSP 是"零网络"最硬的证据：DevTools 的 Network 面板里除了同源静态
 * 资源，一个请求都不该出现（§8.2 / §8.5）。所以**它连地图瓦片的例外都不能有** ——
 * 一旦有人往 standalone 的策略里加了一个 amap 域名，"零网络"就从"可验证的承诺"
 * 退化成"我们的说法"。`csp.test.ts` 里有一条断言专门守着这件事。
 *
 * ## D9：只在 build 注入
 *
 * `vite dev` 用内联脚本 + WebSocket 做 HMR，注入 `script-src 'self'` 会直接打死 HMR。
 * 所以插件写 `apply: 'build'`，dev 完全不注入，产物带 CSP。
 *
 * ## ⚠️ `frame-ancestors` 在 `<meta>` 里无效
 *
 * CSP 规范规定：`<meta http-equiv>` 下发时 `frame-ancestors` / `sandbox` / `report-uri`
 * 会被**静默忽略**。这里仍然写进 meta（语义完整、便于人工审计），
 * 并另外提供 `public/_headers` 用真实 HTTP 头下发同一份策略 —— 那才是它生效的地方。
 */

/** 入口种类。与 `index.html` / `embed.html` 的 `data-entry` 一一对应。 */
export type EntryKind = 'standalone' | 'embed';

/** 主应用域名。**部署时替换**（占位符，见任务书 §8.1）。 */
export const PARENT_ORIGIN = 'https://app.travelmemory.com';

/** `<meta>` 注入判重用的标记。 */
export const CSP_META_MARKER = 'http-equiv="Content-Security-Policy"';

/** 高德 JS API 的脚本域名（**只有 embed 入口**放行）。 */
export const AMAP_SCRIPT_ORIGIN = 'https://webapi.amap.com';
/** 高德瓦片 / 服务域名（**只有 embed 入口**放行）。 */
export const AMAP_ASSET_ORIGIN = 'https://*.amap.com';

/**
 * 拼出指定入口的完整 CSP。
 *
 * 共有部分：
 * - `script-src 'self'`：没有内联脚本（React 产物是同源外链）；
 * - `style-src` 留 `'unsafe-inline'`：React 的内联 `style` 属性需要它；
 * - `img-src` 需要 `data:`（缩略图 / 解出来的照片）与 `blob:`（预览）；
 * - `media-src` 需要 `blob:`；
 * - `frame-ancestors`：只允许主应用与自身嵌套（meta 里无效，见 `_headers`）。
 *
 * `embed` 额外允许：
 * - `script-src` + `webapi.amap.com`（JS API 2.0 的脚本本体）；
 * - `img-src` + `*.amap.com`（地图瓦片是 `<img>`）。
 *
 * **`connect-src` 两个入口都保持 `'self'`**（任务书 §6.4.3）：放行 `*.amap.com`
 * 等于允许向高德发 fetch/XHR，会把"除了地图底图没有其他网络请求"降级成
 * "还能向高德发请求"。这条边界由 `csp.test.ts` 专门盯着，别顺手放宽。
 * 若将来在真机上拿到「底图灰屏 + 控制台 CSP 拦截 connect」的**实际证据**，
 * 按任务书 §6.4.3 上报后再改，不要在本文件私自放宽。
 *
 * @param kind 入口种类；缺省 = `standalone`（最严格的策略是安全默认值）
 */
export function buildCsp(kind: EntryKind = 'standalone'): string {
  if (kind === 'embed') {
    return [
      "default-src 'self'",
      `script-src 'self' ${AMAP_SCRIPT_ORIGIN}`,
      "style-src 'self' 'unsafe-inline'",
      `img-src 'self' blob: data: ${AMAP_ASSET_ORIGIN}`,
      // 只允许同源：高德 SDK 若在运行期用 XHR 拉服务，请求会被 CSP 拦下（见文件头）
      "connect-src 'self'",
      "font-src 'self'",
      "media-src 'self' blob:",
      "worker-src 'self'",
      `frame-ancestors 'self' ${PARENT_ORIGIN}`,
    ].join('; ');
  }

  // standalone：**不放行任何第三方域名**，这是"全程零网络请求"的唯一证据
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "connect-src 'self'",
    "font-src 'self'",
    "media-src 'self' blob:",
    "worker-src 'self'",
    `frame-ancestors 'self' ${PARENT_ORIGIN}`,
  ].join('; ');
}

/**
 * 把 CSP 以 `<meta>` 形式插到 `</head>` 之前（幂等）。
 *
 * @param html 产物的 HTML 文本（`index.html` / `embed.html`）
 * @param kind 该 HTML 属于哪个入口
 * @returns 注入了 CSP meta 的 HTML；已有 CSP 或找不到 `</head>` 时原样返回
 */
export function injectCsp(html: string, kind: EntryKind = 'standalone'): string {
  if (html.includes(CSP_META_MARKER)) return html;
  const headEnd = html.toLowerCase().indexOf('</head>');
  if (headEnd < 0) return html;
  const meta = `<meta http-equiv="Content-Security-Policy" content="${buildCsp(kind)}" />`;
  return `${html.slice(0, headEnd)}    ${meta}\n  ${html.slice(headEnd)}`;
}

/**
 * 从 HTML 内容判断它属于哪个入口。
 *
 * 优先看 `<html data-entry="…">`（产物里我们自己的标记），
 * 否则退回看有没有 `embed.html` 特征（build 时的兜底：万一 vite 把 html 压过一遍）。
 *
 * @param html  HTML 文本
 * @param path  该 HTML 的构建路径（`transformIndexHtml` 的 `ctx.path`）
 */
export function entryOf(html: string, path = ''): EntryKind {
  if (html.includes('data-entry="embed"')) return 'embed';
  if (path.includes('embed.html')) return 'embed';
  return 'standalone';
}
