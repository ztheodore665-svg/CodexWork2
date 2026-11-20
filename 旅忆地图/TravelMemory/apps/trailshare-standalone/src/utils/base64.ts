/** base64 编解码（信物里所有密文 / IV / 盐的传输形式）。
 *
 * ## 为什么不用 `btoa` / `atob`
 *
 * `btoa` 只接受 Latin-1 字符串：直接丢二进制进去，大于 0x7F 的字节要么抛
 * `InvalidCharacterError`，要么（在某些运行时里）被静默截断成 `charCodeAt & 0xFF`
 * 之后的怪值。为了避开它，常见的写法是先 `String.fromCharCode(...bytes)`，
 * 但一张几百 KB 的照片会把上十万个参数压进调用栈 → `RangeError`。
 *
 * 这里直接按 RFC 4648 的字母表手写一个纯函数版本：**不依赖任何 DOM API**，
 * 因此在 Node（vitest）与浏览器里行为完全一致 —— 这是"测试通过 = 线上通过"的前提。
 */

const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

const LOOKUP: Int16Array = (() => {
  // -1 表示非法字符（含 '=' 之外的空白）。用 Int16Array 而非对象，解码热路径更快。
  const table = new Int16Array(256).fill(-1);
  for (let i = 0; i < ALPHABET.length; i += 1) {
    table[ALPHABET.charCodeAt(i)] = i;
  }
  return table;
})();

/** 字节 → base64（标准字母表，带 `=` 填充）。 */
export function toBase64(bytes: Uint8Array): string {
  let out = '';
  let i = 0;
  for (; i + 2 < bytes.length; i += 3) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
    out +=
      ALPHABET[(n >> 18) & 63] +
      ALPHABET[(n >> 12) & 63] +
      ALPHABET[(n >> 6) & 63] +
      ALPHABET[n & 63];
  }
  const rest = bytes.length - i;
  if (rest === 1) {
    const n = bytes[i] << 16;
    out += ALPHABET[(n >> 18) & 63] + ALPHABET[(n >> 12) & 63] + '==';
  } else if (rest === 2) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8);
    out +=
      ALPHABET[(n >> 18) & 63] +
      ALPHABET[(n >> 12) & 63] +
      ALPHABET[(n >> 6) & 63] +
      '=';
  }
  return out;
}

/** base64 → 字节。容忍换行与空格（复制粘贴进来的文本里很常见），
 * 但拒绝其它非法字符：密文被截断比"解密失败"更危险，因为它可能静默解出半张照片。 */
export function fromBase64(text: string): Uint8Array {
  const clean = text.replace(/[\s]/g, '');
  const padding = clean.endsWith('==') ? 2 : clean.endsWith('=') ? 1 : 0;
  const body = clean.length - padding;
  if (body % 4 === 1) {
    throw new Error('base64 长度非法');
  }
  const outLength = Math.floor((body * 3) / 4);
  const out = new Uint8Array(outLength);
  let acc = 0;
  let bits = 0;
  let o = 0;
  for (let i = 0; i < body; i += 1) {
    const code = clean.charCodeAt(i);
    const value = code < 256 ? LOOKUP[code] : -1;
    if (value < 0) {
      throw new Error(`base64 含非法字符：${clean[i]}`);
    }
    acc = (acc << 6) | value;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out[o] = (acc >> bits) & 0xff;
      o += 1;
    }
  }
  return out;
}
