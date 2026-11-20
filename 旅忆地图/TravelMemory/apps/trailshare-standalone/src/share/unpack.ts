/** 解包：验签 → 承诺比对 → KEM 解封 → 解密内容（规范 §6.3），以及给 UI 的视图层（§6.4）。
 *
 * ## 为什么有两个接口
 *
 * - `unpack()`：返回 `pkg` 与完整 `report`（含 `errors`），服务算法与测试。
 * - `unpackForViewer()`：**不返回 pkg**，也不给任何能算出"这个包一共有几组"的字段。
 *
 * 合并成一个会让"界面不暴露组数"这条产品红线失效：一旦 UI 拿得到 `pkg`，
 * `pkg.groups.length` 就迟早会出现在某个"还有 N 组没解开"的提示里 ——
 * 而"还有 N 组"本身就是在告诉收件人"有人收到了你没收到的东西"。
 *
 * ## 失败口径
 *
 * - 解不开某个 wrappedKey → 抛 `not-a-recipient`，**不区分**"不是给你的"与"密文坏了"；
 * - 一个 place 解密失败只丢这个 place；整组为空才丢整组；
 * - 一组都没解开 → `此信物不包含给你的内容`；
 * - 结构错误对外统一成 `文件已损坏，或不是 TrailShare 信物`（不透露是哪个字段坏了）。
 */

import { groupPlacesCommitmentValue, stripAlgo } from './crypto/commitment';
import { decryptBytes, importAesKey, NOT_A_RECIPIENT, unwrapKey } from './crypto/encrypt';
import { bodyOf, verifyCanonical } from './crypto/sign';
import { x25519PrivFromSeed } from './crypto/keys';
import type {
  PlaceMeta,
  SafeReport,
  SharePackage,
  UnpackedGroup,
  UnpackedGroupView,
  UnpackedPhoto,
  UnpackedPlace,
  UnpackCredential,
  UnpackView,
  UnpackViewerResult,
  VerificationReport,
} from '../types/share';
import { toArrayBuffer, fromUtf8 } from '../utils/binary';
import {
  assertStructure,
  assertVersion,
  CORRUPT_MESSAGE,
  GENESIS_HASH,
  OldShareVersionError,
  OLD_VERSION_MESSAGE,
} from './schema';

/** 解包选项。`ledgerHashes` 是本地哈希链里已有的 `hash` 列表（用于判链是否连续）。 */
export interface UnpackOptions {
  chainEnabled?: boolean;
  ledgerHashes?: string[];
}

export interface UnpackViewerOptions extends UnpackOptions {
  /** 已经处理过（接受/拒绝）的 groupId —— UI 用它回填 `processed`。 */
  processedGroupIds?: string[];
}

export interface UnpackResult {
  pkg: SharePackage;
  /** 只含**解开了的**组。不代表包里的全部组。 */
  groups: UnpackedGroup[];
  report: VerificationReport;
  canAccept: boolean;
}

/** 一组都没解开时的文案。也是 `unpackForViewer` 的 `not-for-you` 文案。 */
export const NOT_FOR_YOU_MESSAGE = '此信物不包含给你的内容';

/** `YYYY-MM-DD`：`PlaceMeta.date` 会被拿去做时间线排序，格式必须严格。 */
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** 解包主流程。按 §6.3 的顺序：版本 → 结构 → 验签 → 承诺 → 链 → 逐组解封。 */
export async function unpack(
  input: unknown,
  cred: UnpackCredential,
  opts: UnpackOptions = {},
): Promise<UnpackResult> {
  // 1. 版本闸门：必须在结构校验之前，否则旧包会先以"结构错误"被拒，
  //    用户看到的是"文件损坏"而不是"版本过旧"，无从补救。
  assertVersion(isRecord(input) ? input.version : undefined);

  // 2. 结构校验
  const pkg = assertStructure(input);
  const errors: string[] = [];

  // 3. 验签：用 SignedBody（不含 from.sig）复算
  const signatureOk = verifyCanonical(bodyOf(pkg), pkg.from.sig, pkg.from.pubkey);
  if (!signatureOk) errors.push('签名校验未通过');

  // 4. 承诺：只用公开字段复算，不需要任何密钥
  const expected = groupPlacesCommitmentValue(
    pkg.groups.map((g) => ({
      groupId: g.groupId,
      salt: g.payload.salt,
      places: g.payload.places.map((p) => ({
        placeId: p.placeId,
        tag: p.tag,
        intensity: p.intensity,
      })),
    })),
  );
  const commitmentOk: boolean | null = pkg.commitment?.hash ? expected === pkg.commitment.hash : null;
  if (commitmentOk === false) errors.push('承诺值与包内容不一致');

  // 5. 链三态
  const { chainOk, chainNote } = evaluateChain(pkg, opts);
  if (chainOk === false) errors.push('本地哈希链不连续');

  // 6. 逐组解封
  const myX25519Priv = x25519PrivFromSeed(cred.privHex);
  const groups: UnpackedGroup[] = [];

  for (const group of pkg.groups) {
    let groupKey: Uint8Array | null = null;
    for (const wrapped of group.wrappedKeys ?? []) {
      try {
        groupKey = await unwrapKey(wrapped, myX25519Priv);
        break;
      } catch {
        // 换下一条 wrappedKey 再试；全失败 = 这一组不是给我的
      }
    }
    if (!groupKey) {
      errors.push(`组 ${group.groupId} 没有给你的密钥`);
      continue; // 该组消失：不进结果，也不进 UI
    }

    const aes = await importAesKey(groupKey);
    const places: UnpackedPlace[] = [];

    for (const ep of group.payload.places ?? []) {
      try {
        const meta = parseMeta(await decryptBytes(aes, ep.ivMeta, ep.encryptedMeta));
        const photos: UnpackedPhoto[] = [];
        for (const ph of ep.photos ?? []) {
          const bytes = await decryptBytes(aes, ph.iv, ph.encrypted);
          const photo: UnpackedPhoto = { photoId: ph.photoId, image: toArrayBuffer(bytes) };
          if (ph.encryptedCaption && ph.ivCaption) {
            try {
              photo.caption = fromUtf8(await decryptBytes(aes, ph.ivCaption, ph.encryptedCaption));
            } catch {
              // 配文单独坏了：丢配文，保留照片 —— "有图无字"可接受，"无图有字"没意义
            }
          }
          photos.push(photo);
        }
        const place: UnpackedPlace = {
          placeId: ep.placeId,
          name: meta.name,
          date: meta.date,
          tag: ep.tag,
          intensity: ep.intensity,
          photos,
        };
        if (meta.label !== undefined) place.label = meta.label;
        if (meta.lat !== undefined) place.lat = meta.lat;
        if (meta.lng !== undefined) place.lng = meta.lng;
        if (meta.radius !== undefined) place.radius = meta.radius;
        places.push(place);
      } catch {
        // 一个 place 解密失败只丢这个 place（规范 §6.3 第 6 条）
        errors.push(`组 ${group.groupId} 的地点解密失败，已跳过`);
      }
    }

    if (places.length === 0) continue; // 整组为空则丢该组
    // 组内按 date 升序稳定排序（Array#sort 自 ES2019 起保证稳定）
    places.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    groups.push({ groupId: group.groupId, places });
  }

  // 7. 一组都没解开
  if (groups.length === 0) {
    throw new Error(NOT_FOR_YOU_MESSAGE);
  }

  const report: VerificationReport = {
    structureOk: true,
    signatureOk,
    commitmentOk,
    chainOk,
    chainNote,
    errors,
  };

  // 8. 接受门槛：结构 + 签名都必须过，承诺"不能明确为假"
  const canAccept = report.structureOk && report.signatureOk && commitmentOk !== false;

  return { pkg, groups, report, canAccept };
}

/**
 * 给 UI 的解包入口：**不返回 pkg**，失败也不抛（转成 `UnpackFailure`）。
 *
 * 返回值里刻意没有 `pkg` / `totalGroups` / `groupCount`，
 * 因此 UI 无法得知"这个包一共有几组" —— 这是产品红线，不是实现偷懒。
 */
export async function unpackForViewer(
  input: unknown,
  cred: UnpackCredential,
  opts: UnpackViewerOptions = {},
): Promise<UnpackViewerResult> {
  let result: UnpackResult;
  try {
    result = await unpack(input, cred, opts);
  } catch (error) {
    if (error instanceof OldShareVersionError) {
      return { ok: false, kind: 'old-version', reason: OLD_VERSION_MESSAGE };
    }
    if (
      error instanceof Error &&
      (error.message === NOT_A_RECIPIENT || error.message === NOT_FOR_YOU_MESSAGE)
    ) {
      return { ok: false, kind: 'not-for-you', reason: NOT_FOR_YOU_MESSAGE };
    }
    // 结构错误统一文案：不透露是哪个字段坏了，也不透露组数
    return { ok: false, kind: 'invalid', reason: CORRUPT_MESSAGE };
  }

  const processedIds = new Set(opts.processedGroupIds ?? []);
  const safeReport: SafeReport = {
    structureOk: result.report.structureOk,
    signatureOk: result.report.signatureOk,
    commitmentOk: result.report.commitmentOk,
    chainOk: result.report.chainOk,
    chainNote: result.report.chainNote,
  };

  const viewGroups: UnpackedGroupView[] = result.groups.map((g) => ({
    groupId: g.groupId,
    places: g.places,
    fromName: result.pkg.from.displayName,
    fromPubkey: result.pkg.from.pubkey,
    shareId: result.pkg.shareId,
    processed: processedIds.has(g.groupId),
  }));

  const view: UnpackView = {
    ok: true,
    groups: viewGroups,
    from: { displayName: result.pkg.from.displayName, pubkey: result.pkg.from.pubkey },
    shareId: result.pkg.shareId,
    commitment: result.pkg.commitment.hash,
    signature: result.pkg.from.sig,
    chainSeq: result.pkg.chain.seq,
    chainPrevHash: result.pkg.chain.prevHash,
    chainOk: result.report.chainOk,
    chainNote: result.report.chainNote,
    report: safeReport,
    canAccept: result.canAccept,
  };
  return view;
}

/** 链三态：关闭 → null；seq=0 且 prevHash=GENESIS → true；命中本地账本 → true；否则 null。 */
function evaluateChain(
  pkg: SharePackage,
  opts: UnpackOptions,
): { chainOk: boolean | null; chainNote: string } {
  if (opts.chainEnabled === false) {
    return { chainOk: null, chainNote: '未启用本地哈希链，本信物不参与链式校验' };
  }
  const prev = stripAlgo(pkg.chain?.prevHash ?? '');
  if (pkg.chain?.seq === 0 && prev === GENESIS_HASH) {
    return { chainOk: true, chainNote: '链首记录（seq=0 且 prevHash 指向创世哈希），链路完整' };
  }
  const ledger = opts.ledgerHashes ?? [];
  if (ledger.some((h) => stripAlgo(h) === prev)) {
    return {
      chainOk: true,
      chainNote: `prevHash 命中本地账本中的一条记录（seq=${pkg.chain?.seq}），链路连续`,
    };
  }
  return {
    chainOk: null,
    chainNote: 'prevHash 未命中本地账本（跨设备接收属预期现象），无法判定链路',
  };
}

/** `PlaceMeta` 的形状校验。日期必须是 `YYYY-MM-DD`：它会被拿去排序与分组，
 *  一个 `2026-1-5` 会让时间线整体错位，而这种错在 UI 上只是"顺序有点怪"。 */
function parseMeta(bytes: Uint8Array): PlaceMeta {
  const meta = JSON.parse(fromUtf8(bytes)) as PlaceMeta;
  if (!meta || typeof meta.name !== 'string' || typeof meta.date !== 'string') {
    throw new Error('PlaceMeta 缺少 name/date');
  }
  if (!DATE_RE.test(meta.date)) {
    throw new Error(`PlaceMeta.date 必须是 YYYY-MM-DD，收到 ${meta.date}`);
  }
  return meta;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
