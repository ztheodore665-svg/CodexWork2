/** 打包流程：草稿 → Trip → `pack()` → **自检** `unpack()`。
 *
 * ## 为什么要自检
 *
 * 生成 `.trailshare` 的用户是发送方，他无法验证"这个包到底能不能被解开"——
 * 一旦密钥派生/包装路径写错（比如 `toMontgomerySecret` 传错一层），症状是
 * **收件人解不开**，而发送方拿到的是一个"看起来成功"的文件。所以打包完立刻用
 * 自己的种子解一遍：能解开、签名通过、承诺一致，才把下载按钮放出来。
 *
 * ## D10 收件人规则（本文件最容易做错的地方）
 *
 * - 用户填了公钥 → `收件人 + 自己`（自己也要能回头看）；
 * - 用户没填 → **只有自己**，并让 UI 明确写"这个包只有你自己能打开"。
 *
 * 两种情况下 `wrappedKeys.length` 都等于去重后的收件人数 —— 这是有单测钉住的不变量。
 */

import type { Identity, SharePackage, Trip } from '../types/share';
import { buildGeoHints, buildTrip, type TripDraft } from './buildTrip';
import { emptyVisibility } from '../share/filter';
import { pack } from '../share/pack';
import { unpack } from '../share/unpack';
import { normalizePubkey } from '../share/crypto/keys';

/** 组的固定 id：本应用只发一组（协议允许 1–3 组）。 */
export const SELF_GROUP_ID = 'g1_self';

/** 公钥的 bare hex 形状。`ed25519:` 前缀由 `normalizePubkey` 剥掉。 */
const BARE_HEX_RE = /^[0-9a-f]{64}$/i;

/**
 * 解析用户输入的公钥（支持逗号 / 空格 / 换行分隔多个）。
 *
 * @param raw 输入框原文
 * @returns bare hex 公钥数组（可能为空）
 * @throws `Error` 中文错误：某个公钥格式不对
 */
export function parseRecipientInput(raw: string): string[] {
  const parts = (raw ?? '')
    .split(/[,，\s]+/)
    .map((part) => part.trim())
    .filter((part) => part.length > 0);

  return parts.map((part) => {
    const bare = normalizePubkey(part);
    // 只有 bare hex（大写也接受，协议内部一律小写存储）
    if (!BARE_HEX_RE.test(bare)) {
      throw new Error(`收件人公钥格式不对：${part}（应为 64 位十六进制，可带 ed25519: 前缀）`);
    }
    return bare.toLowerCase();
  });
}

/**
 * 应用 D10 规则：算出最终收件人（**永远包含自己**）。
 *
 * @param raw 用户输入的公钥文本（可为空）
 * @param myPubkey 自己的公钥（bare hex 或带前缀）
 * @returns 去重后的收件人公钥（bare hex），顺序：先用户填的，再自己
 */
export function resolveRecipients(raw: string, myPubkey: string): string[] {
  const mine = normalizePubkey(myPubkey).toLowerCase();
  const listed = parseRecipientInput(raw);
  const out: string[] = [];
  for (const pubkey of [...listed, mine]) {
    if (!out.includes(pubkey)) out.push(pubkey);
  }
  return out;
}

export interface PackJourneyInput {
  draft: TripDraft;
  myIdentity: Identity;
  /** 昵称；空则用 '我'。 */
  displayName?: string;
  /** 收件人输入框原文（可为空 → D10 的"只有自己"）。 */
  recipientsRaw: string;
  /** 覆盖时间（测试用）。 */
  now?: Date;
  /** 覆盖 shareId（测试用）。 */
  shareId?: string;
}

/** 自检结果，直接给完成页展示用。 */
export interface SelfCheckReport {
  ok: boolean;
  signatureOk: boolean;
  commitmentOk: boolean | null;
  chainOk: boolean | null;
  chainNote: string;
  groups: number;
  places: number;
  photos: number;
  /** 失败时的人话原因。 */
  reason?: string;
}

export interface PackJourneyResult {
  pkg: SharePackage;
  trip: Trip;
  /** 去重后的收件人（bare hex）。长度 === `pkg.groups[0].wrappedKeys.length`。 */
  recipients: string[];
  /** 是否只有自己（UI 必须据此显示 D10 提示）。 */
  onlyMe: boolean;
  report: SelfCheckReport;
}

/**
 * 打包并自检。
 *
 * @throws `Error` 中文错误：草稿校验失败 / 公钥非法 / 打包失败
 */
export async function packJourney(input: PackJourneyInput): Promise<PackJourneyResult> {
  const trip = buildTrip(input.draft);
  const recipients = resolveRecipients(input.recipientsRaw, input.myIdentity.pubkey);
  const displayName = (input.displayName ?? '').trim() || '我';

  const result = await pack({
    groups: [
      {
        trip,
        // 不屏蔽任何内容，但仍然走 applyVisibility 那条路径：
        // "过滤"是打包流程的一部分，跳过一次就会在将来加屏蔽功能时漏掉。
        visibility: emptyVisibility(trip.id, SELF_GROUP_ID),
        recipientPubkeys: recipients,
        // W12-4 功能缺口修复：`pack()` **只**通过 geoHints 把 lat/lng/radius 写进密文，
        // 不传的话收件人侧 `UnpackedPlace.lat/lng` 全是 undefined，地图一个点都画不出来。
        // `radius: 0` = 精确点（本版本没有后端地理模糊），收件人那边画 Marker 而不是圈。
        geoHints: buildGeoHints(trip),
      },
    ],
    senderIdentity: input.myIdentity,
    displayName,
    ...(input.now ? { now: input.now } : {}),
    ...(input.shareId ? { shareId: input.shareId } : {}),
  });

  const placeCount = result.pkg.groups[0]?.payload.places.length ?? 0;
  const photoCount =
    result.pkg.groups[0]?.payload.places.reduce((sum, place) => sum + place.photos.length, 0) ?? 0;

  let report: SelfCheckReport = {
    ok: false,
    signatureOk: false,
    commitmentOk: null,
    chainOk: null,
    chainNote: '',
    groups: 0,
    places: placeCount,
    photos: photoCount,
  };

  try {
    const selfCheck = await unpack(result.pkg, { privHex: input.myIdentity.privHex });
    const signatureOk = selfCheck.report.signatureOk;
    const commitmentOk = selfCheck.report.commitmentOk;
    const ok = signatureOk && commitmentOk !== false;
    report = {
      ok,
      signatureOk,
      commitmentOk,
      chainOk: selfCheck.report.chainOk,
      chainNote: selfCheck.report.chainNote,
      groups: selfCheck.groups.length,
      places: selfCheck.groups[0]?.places.length ?? 0,
      photos: selfCheck.groups[0]?.places.reduce((sum, place) => sum + place.photos.length, 0) ?? 0,
      ...(ok ? {} : { reason: '自检未通过：这个包连你自己都解不开或签名不一致，请不要发送' }),
    };
  } catch (error) {
    report = {
      ...report,
      reason: error instanceof Error ? `自检失败：${error.message}` : '自检失败',
    };
  }

  return {
    pkg: result.pkg,
    trip,
    recipients,
    onlyMe: recipients.length === 1 && recipients[0] === normalizePubkey(input.myIdentity.pubkey).toLowerCase(),
    report,
  };
}

/** 包体积（字节）→ 人类可读。 */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 KB';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
