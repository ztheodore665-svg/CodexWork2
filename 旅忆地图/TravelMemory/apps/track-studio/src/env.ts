/** 环境变量的**唯一**读取点。
 *
 * 为什么单独一个文件：CSP 承诺"零后端"，而"有没有偷偷读某个 env / 埋点 key"
 * 是审计时最该一眼看清的事。全仓只有这里碰 `import.meta.env`，
 * 想加新变量必须先在这个文件里露脸。
 *
 * `@travelmemory/amap-loader` 与从 showcase 复制来的 `map/baseMap.ts` 会自己读
 * `VITE_AMAP_JS_KEY`（它们是既有代码，按红线不许改），这两处是已知例外。
 */

function readEnv(name: 'VITE_AMAP_JS_KEY' | 'VITE_AMAP_SECURITY_CODE'): string {
  const raw: unknown = (import.meta.env as Record<string, unknown>)[name];
  return typeof raw === 'string' ? raw.trim() : '';
}

/** 高德 JS API Key。**绝不硬编码**：没配置就返回空串，走降级格网。 */
export function readAmapKey(): string {
  return readEnv('VITE_AMAP_JS_KEY');
}

/** 高德安全密钥（可选，与 Key 配套时由 loader 注入 `window._AMapSecurityConfig`）。 */
export function readAmapSecurityCode(): string {
  return readEnv('VITE_AMAP_SECURITY_CODE');
}

/** 是否配置了可用的地图 Key（占位串判断与 `baseMap.ts` 的 `looksUnset` 保持一致）。 */
export function hasAmapKey(): boolean {
  const key = readAmapKey();
  if (key.length === 0) return false;
  if (key.includes('请') || key.includes('your') || key.length < 8) return false;
  return true;
}

/** 开发模式（Vite `DEV`）。 */
export function isDev(): boolean {
  return import.meta.env.DEV === true;
}

/** 生产构建（Vite `PROD`）。Service Worker 只在生产注册（§8.4）。 */
export function isProd(): boolean {
  return import.meta.env.PROD === true;
}
