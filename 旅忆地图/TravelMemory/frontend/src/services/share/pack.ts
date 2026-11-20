/** 打包：`Trip + 可见性 + 收件人` → `SharePackage`（规范 §6.1）。
 *
 * ## 顺序不能改的三件事
 *
 * 1. **先过滤再加密**（`applyVisibility` 在第一步）。反过来会留下"存在但不可见"的痕迹 ——
 *    见 `filter.ts` 的模块 docstring。
 * 2. **承诺用"包内实际写入的 places"**。用过滤前的列表会让收件人复算出一个不同的值，
 *    表现为"承诺对不上"，而这种错会被误读成"内容被篡改了"。
 * 3. **签名的是 `SignedBody`（不含 `from.sig`）**。签完了再补上 sig，顺序反了就是死循环。
 *
 * ## 时间
 *
 * `createdAt` 与链记录的 `ts` 都来自显式传入的 `now`（缺省才取当下）。
 * 打包结果会被写进哈希链与溯源边，时间是判重的一部分 —— 让它由调用方决定，
 * 实验与回放才有可复现性。
 */

import { groupPlacesCommitmentValue, stripAlgo, type CommitmentGroupPart } from '../crypto/commitment';
import { encryptBytes, importAesKey, wrapKeyForRecipients } from '../crypto/encrypt';
import { signCanonical } from '../crypto/sign';
import type {
  ChainRecord,
  EncryptedPhoto,
  EncryptedPlace,
  FilterResult,
  Identity,
  PlaceGeoHint,
  PlaceMeta,
  ShareGroup,
  SharePackage,
  SignedBody,
  Trip,
  VisibilityState,
} from '../../types/share';
import { toBase64 } from '../../utils/base64';
import { toBytes, utf8 } from '../../utils/binary';
import { sha256Hex } from '../../utils/hash';
import { newId, randomBytes } from '../../utils/id';
import { applyVisibility } from './filter';
import { GENESIS_HASH, MAX_GROUPS, MAX_PHOTOS_PER_PLACE, MIN_PHOTOS_PER_PLACE, SHARE_VERSION } from './schema';

/** 一组的输入。`geoHints` 是 TravelMemory 扩展（见 `prepare.ts`）。 */
export interface PackGroupInput {
  trip: Trip;
  visibility: VisibilityState;
  /** 收件人的 **Ed25519** 公钥（bare hex 或 `ed25519:` 前缀）。 */
  recipientPubkeys: string[];
  /** placeId → 模糊后的坐标。由 `prepare.ts` 注入；没有就不写进 `PlaceMeta`。 */
  geoHints?: Record<string, PlaceGeoHint>;
}

export interface PackInput {
  groups: PackGroupInput[];
  senderIdentity: Identity;
  displayName?: string;
  prevChain?: ChainRecord | null;
  /** 显式关链：链关闭时 `seq=0`、`prevHash=GENESIS`、`chainRecord=null`。 */
  chainEnabled?: boolean;
  now?: Date;
  shareId?: string;
}

export interface PackResult {
  pkg: SharePackage;
  visible: FilterResult[];
  chainRecord: ChainRecord | null;
}

/** 盐的长度（16 字节）。参与承诺，让"同一批内容打两次包"得到不同的承诺值。 */
const SALT_BYTES = 16;

/** 组密钥长度（AES-256）。 */
const KEY_BYTES = 32;

/** 打包。抛错的两处：组数不在 1–3、某一组没有任何可见内容。 */
export async function pack(input: PackInput): Promise<PackResult> {
  if (!input.groups || input.groups.length < 1 || input.groups.length > MAX_GROUPS) {
    throw new Error(`一次只能打包 1–${MAX_GROUPS} 组，收到 ${input.groups?.length ?? 0} 组`);
  }
  if (!input.senderIdentity?.privHex) {
    throw new Error('缺少发送方身份');
  }

  const now = input.now ?? new Date();
  const shareId = input.shareId ?? newId('s', 8);
  const identity = input.senderIdentity;

  const groups: ShareGroup[] = [];
  const visible: FilterResult[] = [];
  const commitmentParts: CommitmentGroupPart[] = [];

  for (let i = 0; i < input.groups.length; i += 1) {
    const groupInput = input.groups[i];
    const filtered = applyVisibility(groupInput.trip, groupInput.visibility);

    if (filtered.places.length === 0) {
      throw new Error(`第 ${i + 1} 组没有任何可见内容`);
    }
    for (const entry of filtered.places) {
      assertPhotosInRange(entry.place.id, entry.photos.length);
    }

    const salt = toBase64(randomBytes(SALT_BYTES));
    const keyBytes = randomBytes(KEY_BYTES);
    const aes = await importAesKey(keyBytes);

    const encryptedPlaces: EncryptedPlace[] = [];
    for (const entry of filtered.places) {
      const place = entry.place;
      const meta: PlaceMeta = {
        name: place.name,
        label: place.label,
        date: entry.date,
      };
      const hint = groupInput.geoHints?.[place.id];
      if (hint) {
        // TravelMemory 扩展：只在拿到后端模糊结果时才写，否则退化成规范原版
        meta.lat = hint.lat;
        meta.lng = hint.lng;
        meta.radius = hint.radius;
      }

      // meta 与每张照片各自独立 IV（规范 §5.3）
      const metaSealed = await encryptBytes(aes, utf8(JSON.stringify(meta)));

      const photos: EncryptedPhoto[] = [];
      for (const item of entry.photos) {
        const bytes = toBytes(item.photo.image);
        if (bytes.byteLength === 0) {
          throw new Error(`照片 ${item.photo.id} 的字节为空`);
        }
        const sealed = await encryptBytes(aes, bytes);
        const photo: EncryptedPhoto = {
          photoId: item.photo.id,
          encrypted: sealed.dataB64,
          iv: sealed.ivB64,
        };
        if (item.includeCaption && item.photo.caption) {
          const captionSealed = await encryptBytes(aes, utf8(item.photo.caption));
          photo.encryptedCaption = captionSealed.dataB64;
          photo.ivCaption = captionSealed.ivB64;
        }
        photos.push(photo);
      }

      encryptedPlaces.push({
        placeId: place.id,
        tag: place.tag,
        intensity: place.intensity,
        ivMeta: metaSealed.ivB64,
        encryptedMeta: metaSealed.dataB64,
        photos,
      });
    }

    const wrappedKeys = await wrapKeyForRecipients(
      keyBytes,
      groupInput.recipientPubkeys ?? [],
      identity.x25519Priv,
    );
    const groupId = newId(`g${i + 1}`, 6);

    groups.push({
      groupId,
      payload: { cipher: 'aes-gcm', salt, places: encryptedPlaces },
      wrappedKeys,
    });

    // 承诺必须用**包内实际写入的** places：过滤后可能有地点被丢掉
    commitmentParts.push({
      groupId,
      salt,
      places: encryptedPlaces.map((p) => ({
        placeId: p.placeId,
        tag: p.tag,
        intensity: p.intensity,
      })),
    });

    visible.push(filtered);
  }

  const commitment = { hash: groupPlacesCommitmentValue(commitmentParts), algo: 'sha256' as const };

  const chainEnabled = input.chainEnabled !== false;
  const seq = chainEnabled ? (input.prevChain ? input.prevChain.seq + 1 : 0) : 0;
  const prevHash = chainEnabled && input.prevChain ? input.prevChain.hash : GENESIS_HASH;

  const body: SignedBody = {
    version: SHARE_VERSION,
    shareId,
    from: { displayName: input.displayName ?? '', pubkey: identity.pubkey },
    groups,
    commitment,
    chain: { prevHash, seq, anchor: null },
    createdAt: now.toISOString(),
  };

  const sig = signCanonical(body, identity.privHex);
  const pkg: SharePackage = { ...body, from: { ...body.from, sig } };

  const chainRecord: ChainRecord | null = chainEnabled
    ? {
        seq,
        hash: `sha256:${await sha256Hex(utf8(`${shareId}||${sig}||${stripAlgo(prevHash)}||${seq}`))}`,
        prevHash,
        event: 'share_created',
        refId: shareId,
        ts: now.toISOString(),
      }
    : null;

  return { pkg, visible, chainRecord };
}

function assertPhotosInRange(placeId: string, count: number): void {
  if (count < MIN_PHOTOS_PER_PLACE || count > MAX_PHOTOS_PER_PLACE) {
    throw new Error(`地点 ${placeId} 的可见照片数为 ${count}，必须在 ${MIN_PHOTOS_PER_PLACE}–${MAX_PHOTOS_PER_PLACE} 之间`);
  }
}
