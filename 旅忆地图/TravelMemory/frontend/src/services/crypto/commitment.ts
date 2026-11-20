/** 组织承诺（v2026.4）：对"可见 place 的 (placeId, tag, intensity) 有序列表 + 每组盐"做 SHA256。
 *
 * ## 为什么承诺要存在
 *
 * 信物里 tag / intensity 是**明文**（收件人要在解封前就能看到分类），
 * 于是发送方可以事后抵赖："我发的是'休闲'不是'美食'"。承诺把这份明文列表钉死：
 * 任何人都能用公开字段复算哈希，改动一个 tag 就会对不上 —— 不需要任何密钥。
 *
 * ## 为什么这里用 `@noble/hashes` 而不是 `utils/hash.ts`（WebCrypto）
 *
 * 规范 §2.3 把 `crypto/commitment.ts` 划成**纯函数**：禁止 IndexedDB、
 * `crypto.subtle`、`Date.now()`、网络。原因是承诺必须能在没有 WebCrypto 的环境里
 * （实验脚本、纯 Node 回放）复算，且同步求值更好测。noble 的 `sha256` 是纯计算，
 * 与 `crypto.subtle.digest('SHA-256')` 结果逐位相同，所以与 `utils/hash.ts` 混用安全。
 *
 * ## 覆盖什么 / 不覆盖什么
 *
 * 覆盖：可见 place 的 `(placeId, tag, intensity)` 有序列表 + 每组盐。
 * 不覆盖：日期、地点名、label、配文、照片本身 —— 它们都在密文里，
 * 一旦进承诺就等于把明文摘要公开，等于泄露内容（规范 E1 实验正是量这件事）。
 */

import { sha256 } from '@noble/hashes/sha256';
import type { IntensityBucket, Tag } from '../../types/share';
import { bytesToHex, utf8 } from '../../utils/binary';

/** 字段分隔符。选 `||` 而不是 `|`：单竖线在日期/名字里都有可能出现，
 * 会造成 `a|b||c` 与 `a||b|c` 这类碰撞（拼接类承诺的经典坑）。 */
const SEP = '||';

/** 承诺值的算法前缀。写进 `commitment.hash`，便于将来换算法时区分。 */
export const HASH_PREFIX = 'sha256:';

/** 输入的一部分：一组的 groupId、盐，以及**包内实际写入的** places。 */
export interface CommitmentGroupPart {
  groupId: string;
  salt: string;
  places: Array<{ placeId: string; tag: Tag; intensity: IntensityBucket }>;
}

/** 剥掉 `sha256:` 前缀。
 *
 * 哈希链比较 `prevHash` 前必须先剥：读写路径一个带前缀一个不带，
 * 会让"链断了"这种结论出现在根本没坏的数据上（规范 §13.5）。 */
export function stripAlgo(hash: string): string {
  const value = (hash ?? '').trim();
  return value.startsWith(HASH_PREFIX) ? value.slice(HASH_PREFIX.length) : value;
}

/** 单组的承诺片段：`groupId||salt||placeId||tag||intensity||…`
 *
 * places **先按 placeId 排序**：打包顺序取决于用户勾选顺序，
 * 不排序的话"同一批内容勾两次"会得到两个承诺值，承诺就失去"可复算"的意义。 */
export function groupPart(group: CommitmentGroupPart): string {
  const sorted = [...group.places].sort((a, b) => (a.placeId < b.placeId ? -1 : a.placeId > b.placeId ? 1 : 0));
  const tail = sorted.flatMap((p) => [p.placeId, p.tag, p.intensity]);
  return [group.groupId, group.salt, ...tail].join(SEP);
}

/** 整个包的承诺值，形如 `sha256:<64hex>`。
 *
 * 组之间按 `groupId` 排序，理由同上：组内顺序不稳定，组间顺序也不该影响承诺。 */
export function groupPlacesCommitmentValue(parts: CommitmentGroupPart[]): string {
  const sorted = [...parts].sort((a, b) => (a.groupId < b.groupId ? -1 : a.groupId > b.groupId ? 1 : 0));
  const input = sorted.map(groupPart).join(SEP);
  return HASH_PREFIX + bytesToHex(sha256(utf8(input)));
}
