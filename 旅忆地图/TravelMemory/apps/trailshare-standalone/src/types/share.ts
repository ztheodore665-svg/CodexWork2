/** 定向加密信物（TrailShare 协议 `ts-2026.4`）的全部类型。
 *
 * ## 为什么单独一个文件，而不是并进 `types/agent.ts`
 *
 * 信物协议是**跨设备的线格式**：A 机器打包出的 JSON 字节，B 机器必须能原样验签。
 * 一旦类型和对话页类型混在一起，改一个对话字段就可能顺手把 `PlaceMeta` 的形状带偏，
 * 而这种偏差不会在编译期报错，只会在两台机器上表现为"验签随机失败"。
 * 物理隔离是为了让线格式的改动必须显式发生在这个文件里。
 *
 * ## 本文件对规范的唯一扩展
 *
 * `Place.lat/lng` 与 `PlaceMeta.lat/lng/radius` 是 TravelMemory 的 W6-4 扩展：
 * TrailShare 特意"不用地图 API"，而我们要求信物里带**模糊后**的坐标（后端拉普拉斯加噪）。
 * 全部是可选字段 —— 不写就是规范原版，写了收件人也只是多看到一个圆圈，
 * 不会影响 tag/intensity/承诺/验签任何一条语义。
 */

// ---------------------------------------------------------------------------
// 3.1 标签与强度
// ---------------------------------------------------------------------------

import {
  SEMANTIC_TAGS,
  SEMANTIC_TAG_LABELS,
  type SemanticTag,
} from '../generated/semanticTags';

/** 六类标签。类型与常量都由 `src/generated/semanticTags.ts` 派生（阶段 16）。 */
export type Tag = SemanticTag;
export type IntensityBucket = 'low' | 'mid' | 'high';

/** 平票时的稳定排序依据。
 *
 * 这个数组的**顺序本身就是协议的一部分**：`recommend()` 在分数相同时按它取先后，
 * 换个顺序就会让"两个标签打平时推荐哪个"在两端不一致。所以只能追加，不能重排。 */
export const ALL_TAGS: readonly Tag[] = SEMANTIC_TAGS;

export const TAG_LABELS: Record<Tag, string> = SEMANTIC_TAG_LABELS;

export const INTENSITY_STARS: Record<IntensityBucket, string> = {
  low: '★',
  mid: '★★',
  high: '★★★',
};

export const INTENSITY_MEANING: Record<IntensityBucket, string> = {
  low: '一般',
  mid: '喜欢',
  high: '强烈推荐',
};

export const ALL_INTENSITIES: readonly IntensityBucket[] = ['low', 'mid', 'high'];

// ---------------------------------------------------------------------------
// 3.2 旅程模型
// ---------------------------------------------------------------------------

export interface TripPhoto {
  id: string;
  /** 压缩后的 JPEG 字节。用 `ArrayBuffer` 而不是 `Uint8Array`：
   *  IndexedDB 在 Safari 上会把 `Uint8Array` 读回成 `ArrayBuffer`，统一按后者存读最省心。 */
  image: ArrayBuffer;
  caption?: string;
}

export interface Place {
  id: string;
  /** 手写地点名。不用地图 API/地理编码：那会把"精确位置"引回系统里。 */
  name: string;
  /** "上午"/"下午"之类的自由文本，只在密文里出现。 */
  label?: string;
  tag: Tag;
  intensity: IntensityBucket;
  coverIndex: number;
  /** 1–5 张，由 `MAX_PHOTOS_PER_PLACE` 约束。 */
  photos: TripPhoto[];

  // --- TravelMemory 扩展（W6-4 地理模糊） -----------------------------------
  /** 原始 **WGS84** 纬度。缺省 = 这个地点没有坐标，模糊旁路直接跳过（不写密文）。 */
  lat?: number;
  /** 原始 **WGS84** 经度。缺省含义同 `lat`。 */
  lng?: number;
}

export interface Day {
  id: string;
  /** 'YYYY-MM-DD'。允许重复（一天拆成两段写），所以不能拿它当唯一键。 */
  date: string;
  places: Place[];
}

export interface Trip {
  id: string;
  title: string;
  coverPlaceId?: string;
  createdAt: string;
  days: Day[];
}

export interface VisibilityState {
  /** `${tripId}::${groupId}` —— 一组一份屏蔽状态，组与组互不影响。 */
  id: string;
  tripId: string;
  groupId: string;
  hiddenDayIds: string[];
  hiddenPlaceIds: string[];
  hiddenPhotoIds: string[];
  hiddenCaptionPhotoIds: string[];
}

// ---------------------------------------------------------------------------
// 3.3 分享包
// ---------------------------------------------------------------------------

export type ShareVersion = 'ts-2026.4';

export interface EncryptedPhoto {
  photoId: string;
  /** base64(AES-GCM 密文 + tag) */
  encrypted: string;
  /** base64，12 字节随机 IV。每张照片一个，绝不复用。 */
  iv: string;
  /** 有配文且配文未被屏蔽时才出现；与 `ivCaption` 必须成对。 */
  encryptedCaption?: string;
  ivCaption?: string;
}

export interface EncryptedPlace {
  placeId: string;
  /** 明文：承诺要覆盖它，收件人也要在解封前就能看到分类。 */
  tag: Tag;
  intensity: IntensityBucket;
  ivMeta: string;
  /** 密文 `PlaceMeta`。地点名/日期/label 全在这里。 */
  encryptedMeta: string;
  photos: EncryptedPhoto[];
}

export interface WrappedKey {
  /** 收件人 Ed25519 公钥的 bare hex（不带 `ed25519:` 前缀）。 */
  recipientPubkey: string;
  /** 本次 KEM 的临时 X25519 公钥 bare hex。每收件人一条，用完即弃。 */
  ephemeralPubkey: string;
  encryptedKey: string;
  iv: string;
}

export interface ShareGroup {
  /** `g{i}_{6 位}`，如 `g1_ab12cd`。 */
  groupId: string;
  payload: {
    cipher: 'aes-gcm';
    /** base64(16 随机字节)。参与承诺，让"同一批内容打两次包"得到不同的承诺值。 */
    salt: string;
    places: EncryptedPlace[];
  };
  wrappedKeys: WrappedKey[];
}

export interface SharePackage {
  version: ShareVersion;
  shareId: string;
  from: { displayName: string; pubkey: string; sig: string };
  groups: ShareGroup[];
  commitment: { hash: string; algo: 'sha256' };
  chain: { prevHash: string; seq: number; anchor: null };
  createdAt: string;
}

/** 被签名的那一半：`from` 里没有 `sig`。
 *
 * 签名值不可能把自己签进去（自引用），所以 `body = pkg 去掉 from.sig`。
 * 解包侧必须用同一个 `bodyOf()` 复算，手写一遍漏掉 `sig` 就会验签全失败。 */
export interface SignedBody {
  version: ShareVersion;
  shareId: string;
  from: { displayName: string; pubkey: string };
  groups: ShareGroup[];
  commitment: { hash: string; algo: 'sha256' };
  chain: { prevHash: string; seq: number; anchor: null };
  createdAt: string;
}

/** 地点元数据，**只存在于密文里**（`EncryptedPlace.encryptedMeta` 解出来的对象）。
 *
 * `lat/lng/radius` 是 TravelMemory 扩展：后端拉普拉斯模糊后的坐标 + 承诺半径。
 * 可选 —— 没有坐标的 place 解出来就没有这三个键，收件人侧据此决定画不画圈。 */
export interface PlaceMeta {
  name: string;
  label?: string;
  /** 'YYYY-MM-DD'；解包时用它给时间线排序，所以格式必须严格。 */
  date: string;
  lat?: number;
  lng?: number;
  /** 承诺半径（米），**不是本次实际偏移量**。 */
  radius?: number;
}

// ---------------------------------------------------------------------------
// 身份与凭证
// ---------------------------------------------------------------------------

/** 一份种子派生出的两套密钥。
 *
 * Ed25519 用来签名（可验证"是 A 发的"），X25519 用来做 KEM（只有指定的人能解封）。
 * 两套密钥**同一份种子**，所以用户只需要备份一个 32 字节的种子。 */
export interface Identity {
  /** 32 字节种子的 hex（64 个字符），**不是**私钥哈希。noble 内部会自己 clamp。 */
  privHex: string;
  /** Ed25519 公钥 bare hex（64 字符）。 */
  pubHex: string;
  /** `ed25519:` + `pubHex`，对外交换用的形式。 */
  pubkey: string;
  x25519Priv: Uint8Array;
  x25519Pub: Uint8Array;
}

/** 解包凭证：只需要自己的种子，其余全部现算。 */
export interface UnpackCredential {
  privHex: string;
  pubHex?: string;
}

// ---------------------------------------------------------------------------
// 过滤中间结果
// ---------------------------------------------------------------------------

export interface FilterPhoto {
  photo: TripPhoto;
  /** 配文是否随照片一起打包（`false` 时照片打包、配文不打包）。 */
  includeCaption: boolean;
}

export interface FilterPlace {
  place: Place;
  /** 从所属 Day 上拷下来的日期，打包时写进 `PlaceMeta.date`。 */
  date: string;
  photos: FilterPhoto[];
}

export interface FilterResult {
  tripId: string;
  places: FilterPlace[];
}

// ---------------------------------------------------------------------------
// 解包结果
// ---------------------------------------------------------------------------

export interface UnpackedPhoto {
  photoId: string;
  image: ArrayBuffer;
  caption?: string;
}

export interface UnpackedPlace {
  placeId: string;
  name: string;
  label?: string;
  date: string;
  tag: Tag;
  intensity: IntensityBucket;
  photos: UnpackedPhoto[];
  lat?: number;
  lng?: number;
  radius?: number;
}

/** 一个"我成功解开了"的组。组数不等于包里的组数（解不开的组不会出现在这里）。 */
export interface UnpackedGroup {
  groupId: string;
  places: UnpackedPlace[];
}

// ---------------------------------------------------------------------------
// 3.5 验证报告
// ---------------------------------------------------------------------------

export interface VerificationReport {
  structureOk: boolean;
  signatureOk: boolean;
  /** `null` = 无法比对（缺字段等）。 */
  commitmentOk: boolean | null;
  /** `null` = 链关闭或无法判定。 */
  chainOk: boolean | null;
  chainNote: string;
  /** 只给算法/测试看：`errors` 里可能出现 groupId，绝不能进 UI。 */
  errors: string[];
}

/** 给 UI 的报告：砍掉 `errors`，因为它可能含 groupId / 组数线索。 */
export type SafeReport = Omit<VerificationReport, 'errors'>;

// ---------------------------------------------------------------------------
// 6.4 视图层
// ---------------------------------------------------------------------------

export interface UnpackedGroupView {
  groupId: string;
  places: UnpackedPlace[];
  fromName: string;
  fromPubkey: string;
  shareId: string;
  /** 由 UI 查本地溯源边后回填（同一文件重复导入时显示"已接受/已拒绝"）。 */
  processed: boolean;
}

export interface UnpackView {
  ok: true;
  /** 只含**解开了的**组。刻意不提供 `pkg` / `totalGroups` / `groupCount`，
   *  UI 因此无从得知"这个包一共有几组"。 */
  groups: UnpackedGroupView[];
  from: { displayName: string; pubkey: string };
  shareId: string;
  commitment: string;
  signature: string;
  chainSeq: number;
  chainPrevHash: string;
  chainOk: boolean | null;
  chainNote: string;
  report: SafeReport;
  canAccept: boolean;
}

export type UnpackFailureKind = 'old-version' | 'not-for-you' | 'invalid';

export interface UnpackFailure {
  ok: false;
  kind: UnpackFailureKind;
  /** 不含组数、不含 groupId —— 结构错误统一成一句人话。 */
  reason: string;
}

export type UnpackViewerResult = UnpackView | UnpackFailure;

// ---------------------------------------------------------------------------
// 5.6 本地哈希链
// ---------------------------------------------------------------------------

export type ChainEvent = 'share_created' | 'share_accepted' | 'share_rejected';

export interface ChainRecord {
  seq: number;
  hash: string;
  prevHash: string;
  event: ChainEvent;
  refId: string;
  ts: string;
}

// ---------------------------------------------------------------------------
// 地理模糊（TravelMemory 扩展）
// ---------------------------------------------------------------------------

export type ObfuscationMode = 'exact' | 'approx' | 'city';

/** 后端 `POST /api/v1/privacy/obfuscate` 的响应（WGS84）。 */
export interface ObfuscatedPoint {
  lat: number;
  lng: number;
  /** 承诺半径（米），不是本次实际偏移量。 */
  radius: number;
  mode: string;
  epsilon: number;
  coord_system: string;
}

/** 打进 `PlaceMeta` 的那一小份坐标（pack 只认这三个键）。 */
export interface PlaceGeoHint {
  lat: number;
  lng: number;
  radius: number;
}
