/** 本地下载（B16）：`<a download>` + Blob URL，**不用 `window.open`**。
 *
 * 为什么不用 `window.open(url)`：
 * 1. 会被弹窗拦截器拦（用户点的是"下载"不是"打开"）；
 * 2. 新标签页会留下 blob URL 的历史记录；
 * 3. CSP 的 `default-src 'self'` 下它还可能直接失败。
 *
 * 每次下载都自己建 URL、自己 revoke —— 一个 blob URL 会**钉住整块内存**
 * （`journey.json` 与 `.trailshare` 都不小），不 revoke 就是内存泄漏。
 */

/** 文件名里不能有的字符替换掉（各平台规则不同，取最保守的一档）。 */
export function sanitizeFilename(name: string, fallback: string): string {
  const trimmed = (name ?? '').trim();
  const base = trimmed.length > 0 ? trimmed : fallback;
  // 去掉路径分隔符与控制字符，其余保留（中文文件名是合法的）
  // eslint-disable-next-line no-control-regex
  const cleaned = base.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').slice(0, 120);
  return cleaned.length > 0 ? cleaned : fallback;
}

/**
 * 触发浏览器下载。
 *
 * @param blob 文件内容
 * @param filename 保存名（会被 `sanitizeFilename` 清洗）
 * @param fallback 名字被清空时的兜底
 */
export function downloadBlob(blob: Blob, filename: string, fallback = 'download'): void {
  if (typeof document === 'undefined' || typeof URL.createObjectURL !== 'function') return;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = sanitizeFilename(filename, fallback);
  anchor.rel = 'noopener';
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // 立刻 revoke 会让部分浏览器来不及开始读 blob；延后一拍再放
  setTimeout(() => {
    try {
      URL.revokeObjectURL(url);
    } catch {
      /* 已经 revoke 过 */
    }
  }, 1000);
}

/** 把对象写成 JSON 文件下载（两空格缩进：用户可以直接打开看）。 */
export function downloadJson(value: unknown, filename: string, fallback = 'data.json'): void {
  const text = JSON.stringify(value, null, 2);
  downloadBlob(new Blob([text], { type: 'application/json' }), filename, fallback);
}
