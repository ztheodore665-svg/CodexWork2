/** SHA-256（走 WebCrypto `crypto.subtle`）。
 *
 * ## 为什么这里用 `crypto.subtle`，而 `commitment.ts` 用的是 @noble/hashes
 *
 * 两条路径算出的摘要完全相同，选哪条取决于**分层约束**：
 *
 * - `crypto/*` 层内部允许用 WebCrypto（它已经在跟 `crypto.subtle` 打交道做 AES-GCM 了）；
 *   放在 `utils/hash.ts` 里，KEM 的 `salt = sha256(ephPub || recipientPub)` 就能
 *   和 AES 共用同一个"能用 WebCrypto 就别另引一份实现"的前提。
 * - `commitment.ts` 被规范标成**纯函数**（禁 `crypto.subtle`），因为承诺要能在
 *   没有 WebCrypto 的环境（实验脚本、纯 Node）里复算。那里用同步的 noble 版本。
 *
 * 两者混用不会出问题：摘要值是确定的，只要不把 hex 大小写写歪。
 */

import { bytesToHex, toArrayBuffer, utf8 } from './binary';

/** 取 WebCrypto 的 subtle。Node 18+ 与浏览器都有 `globalThis.crypto`；
 * `file://` 下它是 undefined —— 那时整个信物流程本来也跑不起来，给出可读报错即可。 */
function subtle(): SubtleCrypto {
  const c: Crypto | undefined = globalThis.crypto;
  if (!c || !c.subtle) {
    throw new Error('当前环境没有 WebCrypto（crypto.subtle），请通过 http(s):// 打开页面');
  }
  return c.subtle;
}

/** 字节 → 32 字节摘要。 */
export async function sha256Bytes(data: Uint8Array): Promise<Uint8Array> {
  const digest = await subtle().digest('SHA-256', toArrayBuffer(data));
  return new Uint8Array(digest);
}

/** 字节 → 小写 hex 摘要。 */
export async function sha256Hex(data: Uint8Array): Promise<string> {
  return bytesToHex(await sha256Bytes(data));
}

/** UTF-8 文本 → 小写 hex 摘要（承诺与哈希链都是对字符串做的）。 */
export async function sha256Text(text: string): Promise<string> {
  return sha256Hex(utf8(text));
}
