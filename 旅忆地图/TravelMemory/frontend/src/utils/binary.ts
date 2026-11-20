/** 字节 ↔ 字符串的底层互转。
 *
 * ## 为什么单独抽一层
 *
 * 签名、承诺、KEM、base64 四处都要做「字节 → hex」「文本 → UTF-8 字节」，
 * 而且**必须逐位一致**：只要有一处用了 `toUpperCase` 而另一处用小写，
 * 或者一处多 encode 了一次 UTF-8，两端算出的字节就不同，表现为验签"随机"失败。
 * 抽成一层之后，这类不一致只能发生在这一处，排查范围从整个仓库缩到 40 行。
 *
 * ## 为什么 hex 统一小写
 *
 * 规范里的所有 hex（种子、公钥、签名、承诺）都是小写。改成大写会让
 * 「复制公钥给好友」这种手工环节出现两个不同字符串指向同一把钥匙。
 */

const HEX_ALPHABET = '0123456789abcdef';

/** 字节 → 小写 hex。 */
export function bytesToHex(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; i += 1) {
    out += HEX_ALPHABET[bytes[i] >> 4] + HEX_ALPHABET[bytes[i] & 15];
  }
  return out;
}

/** hex → 字节。不接受奇数长度与非法字符：
 * 静默吞掉一个非法字符会让公钥少一个字节，后面变成"验签失败"这种极难归因的错误。 */
export function hexToBytes(hex: string): Uint8Array {
  const clean = hex.startsWith('0x') ? hex.slice(2) : hex;
  if (clean.length % 2 !== 0) {
    throw new Error(`hex 长度必须是偶数，收到 ${clean.length}`);
  }
  const out = new Uint8Array(clean.length / 2);
  for (let i = 0; i < out.length; i += 1) {
    const byte = Number.parseInt(clean.slice(i * 2, i * 2 + 2), 16);
    if (Number.isNaN(byte)) {
      throw new Error(`hex 含非法字符：${clean.slice(i * 2, i * 2 + 2)}`);
    }
    out[i] = byte;
  }
  return out;
}

/** 顺序拼接若干字节数组（KEM 的 `ephPub || recipientPub` 就靠它）。 */
export function concatBytes(...arrays: Uint8Array[]): Uint8Array {
  let total = 0;
  for (const a of arrays) total += a.length;
  const out = new Uint8Array(total);
  let offset = 0;
  for (const a of arrays) {
    out.set(a, offset);
    offset += a.length;
  }
  return out;
}

const encoder = new TextEncoder();

/** 文本 → UTF-8 字节。承诺与签名的输入统一走这里，避免"隐式按平台编码"。 */
export function utf8(text: string): Uint8Array {
  return encoder.encode(text);
}

const decoder = new TextDecoder('utf-8', { fatal: false });

/** UTF-8 字节 → 文本（解出 `PlaceMeta` 时用）。 */
export function fromUtf8(bytes: Uint8Array): string {
  return decoder.decode(bytes);
}

/** `Uint8Array` → 独立 `ArrayBuffer`。
 *
 * 必须显式切片：`subarray()` 出来的视图带 byteOffset，直接把 `.buffer` 交给
 * `crypto.subtle` 或 IndexedDB 会把**整段底层内存**写进去，得到长度错误的密文。 */
export function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  // Uint8Array 的底层可能是 SharedArrayBuffer（TS 5.6 起 `buffer` 是联合类型），
  // 而 WebCrypto / IndexedDB 只收 ArrayBuffer，所以显式拷一份普通 ArrayBuffer。
  const out = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(out).set(bytes);
  return out;
}

/** `ArrayBuffer` → `Uint8Array`（打包前把照片字节喂给 `crypto.subtle`）。 */
export function toBytes(buffer: ArrayBuffer): Uint8Array {
  return new Uint8Array(buffer);
}
