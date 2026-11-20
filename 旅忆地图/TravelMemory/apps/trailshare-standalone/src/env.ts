/** 环境变量的**唯一**读取点（W12-4）。
 *
 * 为什么单独一个文件：这个应用的卖点是"零后端、可审计"，而"有没有偷偷读某个 env /
 * 埋点 key"是审计时最该一眼看清的事。全仓（本 app 内）只有这里碰 `import.meta.env`，
 * 想加新变量必须先在这个文件里露脸。
 *
 * 已知例外：`@travelmemory/amap-loader` 是既有共享包，它自己不读 env（只接收参数），
 * 所以地图 key 仍然只从这里往外流。
 *
 * 安全前提：Web 端 JS Key 是**公开值**，会被打进产物，靠域名白名单 + 安全密钥保护，
 * 不当机密（见 `docs/deploy-amap-key.md`）。
 */

function readEnv(name: 'VITE_AMAP_JS_KEY' | 'VITE_AMAP_SECURITY_CODE'): string {
  const raw: unknown = (import.meta.env as Record<string, unknown>)[name];
  return typeof raw === 'string' ? raw.trim() : '';
}

/** 高德 JS API Key。**绝不硬编码**：没配置就返回空串，地图走降级示意。 */
export function readAmapKey(): string {
  return readEnv('VITE_AMAP_JS_KEY');
}

/** 高德安全密钥（官方字段名 `securityJsCode`，由 loader 在插脚本前注入）。 */
export function readAmapSecurityCode(): string {
  return readEnv('VITE_AMAP_SECURITY_CODE');
}

/**
 * 是否配置了可用的地图 Key。
 *
 * 占位串判断与 Track Studio / 主应用保持一致：空串、含「请」「your」、长度 < 8
 * 都视为未配置。**未配置时地图一个请求都不会发**（这条路径有测试与文档守着）。
 */
export function hasAmapKey(): boolean {
  const key = readAmapKey();
  if (key.length === 0) return false;
  if (key.includes('请') || key.includes('your')) return false;
  return key.length >= 8;
}
