/** 信物的版本、上限与结构校验。
 *
 * ## 为什么结构校验要"严到字段级"
 *
 * `unpack()` 的输入是一个**来历不明的 JSON**（拖进来的文件、别人转发的字符串）。
 * 若只做"能 parse 就往下走"，后面每一步都会拿到 shape 不确定的对象：
 * `place.tag` 是个数组、`photos` 是 undefined、`encryptedCaption` 有而 `ivCaption` 没有
 * —— 每一种都会在某个深处抛出一个与真实原因无关的错误。
 * 所以：一次校验，全部拒绝，统一文案。
 *
 * ## 为什么旧版本直接拒绝而不是兼容
 *
 * v2026.3 及更早的 group 只有一个 tag（没有逐地点的 place 列表）。
 * 硬转会把"多地点的一天"压扁成一条记录，语义已经变了 —— 与其静默产出错误数据，
 * 不如明确告诉用户"请让发送方重新生成"。
 *
 * ## `.trailshare` 文件格式的当前定义
 *
 * **UTF-8 JSON 文本**，即 `JSON.stringify(pkg)`（`JSON.parse` 即可读回）。
 * 选它是为了调试与离线跑通：人可以直接打开看字段，测试也不需要构造二进制容器。
 * 真实项目里会换成带魔数与长度前缀的二进制容器，但**本协议层始终保持 JSON 可序列化**，
 * 因为签名算的是 `canonicalJson` 的字节 —— 换成容器只是换外层包装，不动线格式。
 */

import type { IntensityBucket, SharePackage, ShareVersion, Tag } from '../types/share';
import { ALL_INTENSITIES, ALL_TAGS } from '../types/share';

/** 当前协议版本。`ts` = TrailShare，`2026.4` = 第 4 次修订。 */
export const SHARE_VERSION: ShareVersion = 'ts-2026.4';

/** 一个包最多 3 组。上限来自"每组可见性都要人工勾一遍"的产品判断：
 *  超过 3 组用户已经分不清哪组给了谁，隐私承诺也就失去意义。 */
export const MAX_GROUPS = 3;

/** 每个地点最多 5 张照片（压缩后体积与"地点卡"可读性的折中）。 */
export const MAX_PHOTOS_PER_PLACE = 5;

/** 每个地点至少 1 张照片：0 张会出现没有照片的空地点卡（规范 §13.10）。 */
export const MIN_PHOTOS_PER_PLACE = 1;

/** 创世哈希：64 个 `0`。哈希链的第一条记录指向它，表示"链从这里开始"。 */
export const GENESIS_HASH = '0'.repeat(64);

/** `.trailshare` 文件的当前编码（见模块 docstring）。 */
export const SHARE_FILE_ENCODING = 'utf-8-json';

/** 文件扩展名。 */
export const SHARE_FILE_EXTENSION = '.trailshare';

/** 旧版本拒绝的固定文案。测试按字面比对，改一个字就是 breaking change。 */
export const OLD_VERSION_MESSAGE = '此信物为旧版本，请让发送方重新生成';

/** 结构错误的统一文案。**不含组数 / groupId**，因为它会直接显示给收件人。 */
export const CORRUPT_MESSAGE = '文件已损坏，或不是 TrailShare 信物';

/** 未来版本的提示。 */
export const UPGRADE_MESSAGE = '请升级本应用';

/** 版本号里的次版本号：`'ts-2026.4'` → `4`。解析不出来返回 `NaN`。 */
export function versionMinor(version: string): number {
  const matched = /^ts-(\d+)\.(\d+)$/.exec((version ?? '').trim());
  if (!matched) return Number.NaN;
  return Number.parseInt(matched[2], 10);
}

/** 旧版本异常。`unpack()` 抛它，`unpackForViewer()` 据此给出 `kind: 'old-version'`。 */
export class OldShareVersionError extends Error {
  constructor() {
    super(OLD_VERSION_MESSAGE);
    this.name = 'OldShareVersionError';
  }
}

/** 版本闸门：
 *  - 次版本 < 4 → `OldShareVersionError`（固定文案）
 *  - 解析不出 / 更高 → 结构错误（更高时提示升级）
 */
export function assertVersion(version: unknown): void {
  if (typeof version !== 'string') {
    throw new Error(CORRUPT_MESSAGE);
  }
  const minor = versionMinor(version);
  if (Number.isNaN(minor)) {
    throw new Error(CORRUPT_MESSAGE);
  }
  if (minor < 4) {
    throw new OldShareVersionError();
  }
  if (minor > 4) {
    throw new Error(UPGRADE_MESSAGE);
  }
}

/** 结构校验：通过返回窄好类型的 `SharePackage`，不通过抛出 `CORRUPT_MESSAGE`。 */
export function assertStructure(input: unknown): SharePackage {
  if (!isRecord(input)) throw new Error(CORRUPT_MESSAGE);

  assertVersion(input.version);

  if (typeof input.shareId !== 'string' || !input.shareId) throw new Error(CORRUPT_MESSAGE);

  const from = input.from;
  if (!isRecord(from)) throw new Error(CORRUPT_MESSAGE);
  if (typeof from.displayName !== 'string') throw new Error(CORRUPT_MESSAGE);
  if (!isHex64(from.pubkey)) throw new Error(CORRUPT_MESSAGE);
  if (typeof from.sig !== 'string' || !from.sig) throw new Error(CORRUPT_MESSAGE);

  const groups = input.groups;
  if (!Array.isArray(groups) || groups.length < 1 || groups.length > MAX_GROUPS) {
    throw new Error(CORRUPT_MESSAGE);
  }

  for (const group of groups) {
    if (!isRecord(group)) throw new Error(CORRUPT_MESSAGE);
    if (typeof group.groupId !== 'string' || !group.groupId) throw new Error(CORRUPT_MESSAGE);

    const payload = group.payload;
    if (!isRecord(payload)) throw new Error(CORRUPT_MESSAGE);
    if (payload.cipher !== 'aes-gcm') throw new Error(CORRUPT_MESSAGE);
    if (typeof payload.salt !== 'string' || !payload.salt) throw new Error(CORRUPT_MESSAGE);

    const places = payload.places;
    if (!Array.isArray(places) || places.length < 1) throw new Error(CORRUPT_MESSAGE);
    for (const place of places) {
      if (!isRecord(place)) throw new Error(CORRUPT_MESSAGE);
      if (typeof place.placeId !== 'string' || !place.placeId) throw new Error(CORRUPT_MESSAGE);
      if (!isTag(place.tag)) throw new Error(CORRUPT_MESSAGE);
      if (!isIntensity(place.intensity)) throw new Error(CORRUPT_MESSAGE);
      if (typeof place.ivMeta !== 'string' || !place.ivMeta) throw new Error(CORRUPT_MESSAGE);
      if (typeof place.encryptedMeta !== 'string' || !place.encryptedMeta) throw new Error(CORRUPT_MESSAGE);

      const photos = place.photos;
      if (!Array.isArray(photos)) throw new Error(CORRUPT_MESSAGE);
      if (photos.length < MIN_PHOTOS_PER_PLACE || photos.length > MAX_PHOTOS_PER_PLACE) {
        throw new Error(CORRUPT_MESSAGE);
      }
      for (const photo of photos) {
        assertPhoto(photo);
      }
    }

    const wrapped = group.wrappedKeys;
    if (!Array.isArray(wrapped) || wrapped.length < 1) throw new Error(CORRUPT_MESSAGE);
  }

  const commitment = input.commitment;
  if (!isRecord(commitment)) throw new Error(CORRUPT_MESSAGE);
  if (typeof commitment.hash !== 'string' || !commitment.hash) throw new Error(CORRUPT_MESSAGE);
  if (commitment.algo !== 'sha256') throw new Error(CORRUPT_MESSAGE);

  const chain = input.chain;
  if (!isRecord(chain)) throw new Error(CORRUPT_MESSAGE);
  if (typeof chain.prevHash !== 'string' || !chain.prevHash) throw new Error(CORRUPT_MESSAGE);
  if (typeof chain.seq !== 'number' || !Number.isInteger(chain.seq) || chain.seq < 0) {
    throw new Error(CORRUPT_MESSAGE);
  }
  if (chain.anchor !== null) throw new Error(CORRUPT_MESSAGE);

  if (typeof input.createdAt !== 'string' || !input.createdAt) throw new Error(CORRUPT_MESSAGE);

  return input as unknown as SharePackage;
}

function assertPhoto(photo: unknown): void {
  if (!isRecord(photo)) throw new Error(CORRUPT_MESSAGE);
  if (typeof photo.photoId !== 'string' || !photo.photoId) throw new Error(CORRUPT_MESSAGE);
  if (typeof photo.encrypted !== 'string' || !photo.encrypted) throw new Error(CORRUPT_MESSAGE);
  if (typeof photo.iv !== 'string' || !photo.iv) throw new Error(CORRUPT_MESSAGE);
  // 配文的两字段必须成对：只有一个会让解包侧拿着 IV 解空气（或反过来）
  const hasCaption = typeof photo.encryptedCaption === 'string';
  const hasIvCaption = typeof photo.ivCaption === 'string';
  if (hasCaption !== hasIvCaption) throw new Error(CORRUPT_MESSAGE);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** 公钥的两种合法写法都接受：bare hex 或 `ed25519:` + bare hex。 */
function isHex64(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  const bare = value.startsWith('ed25519:') ? value.slice(8) : value;
  return /^[0-9a-fA-F]{64}$/.test(bare);
}

function isTag(value: unknown): value is Tag {
  return typeof value === 'string' && (ALL_TAGS as readonly string[]).includes(value);
}

function isIntensity(value: unknown): value is IntensityBucket {
  return typeof value === 'string' && (ALL_INTENSITIES as readonly string[]).includes(value);
}
