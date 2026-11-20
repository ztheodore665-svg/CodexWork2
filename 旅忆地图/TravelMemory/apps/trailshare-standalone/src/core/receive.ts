/** 收件人侧的接线逻辑（W12-5）：`.trailshare` 文件文本 → 给 UI 的结果。
 *
 * ## 为什么单独一个文件（而不是全写在 React 里）
 *
 * "读文件 → JSON.parse → 用我的种子解包"这三步的失败口径是**产品承诺**的一部分：
 *  - 文件不是 JSON / 不是对象 → 一句人话，不透露是哪个字段坏了；
 *  - 版本过旧 → 提示让发送方重新生成（**不是**"文件损坏"，否则用户无从补救）；
 *  - 不是给我的 → 只说"不包含给你的内容"，不说组数、不说 groupId。
 *
 * 三条都在 `unpackForViewer` 里定死了（见 `share/unpack.ts` 文件头），这里只做
 * "文件 → 对象"这一层，并把它抽成纯函数，好在 Node 里直接测（不用 jsdom、不用点按钮）。
 *
 * ## 红线
 *
 * 返回给 UI 的结果里**没有** `pkg`、没有组数、没有 groupId（`unpackForViewer` 已经砍掉）。
 * 这个文件也不要"顺手"把它们加回来。
 */

import { unpackForViewer } from '../share/unpack';
import type { UnpackedPlace, UnpackViewerResult } from '../types/share';

/** 解析失败 / 不是对象的统一文案（与 `schema.ts` 的 `CORRUPT_MESSAGE` 一致）。 */
export const RECEIVE_CORRUPT_MESSAGE = '文件已损坏，或不是 TrailShare 信物';

/** 读文件本身失败（权限/编码/文件被删）时的文案，与"内容坏了"区分开。 */
export const RECEIVE_READ_FAILED_MESSAGE = '文件读取失败，请重新选择文件';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * 把信物文件的**文本内容**解成给 UI 的结果。
 *
 * @param text    文件内容（`.trailshare` 就是 JSON）
 * @param privHex 我的种子（`identity.seedHex`）
 */
export async function receiveFromText(text: string, privHex: string): Promise<UnpackViewerResult> {
  if (typeof text !== 'string' || text.trim().length === 0) {
    return { ok: false, kind: 'invalid', reason: RECEIVE_CORRUPT_MESSAGE };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ok: false, kind: 'invalid', reason: RECEIVE_CORRUPT_MESSAGE };
  }
  if (!isRecord(parsed)) {
    return { ok: false, kind: 'invalid', reason: RECEIVE_CORRUPT_MESSAGE };
  }

  return unpackForViewer(parsed, { privHex });
}

/**
 * 从 `File` 读取并解包。
 *
 * 读文件失败（不属于"信物内容"问题）单独给一句文案：用户此时该做的是"重新选文件"，
 * 而不是"去找发送方重发"。
 *
 * @param file    `<input type="file">` / 拖拽拿到的文件
 * @param privHex 我的种子
 */
export async function receiveFromFile(file: File, privHex: string): Promise<UnpackViewerResult> {
  let text: string;
  try {
    text = await file.text();
  } catch {
    return { ok: false, kind: 'invalid', reason: RECEIVE_READ_FAILED_MESSAGE };
  }
  return receiveFromText(text, privHex);
}

/**
 * 结果里的全部地点（按组顺序拼接，同 `placeId` 只保留第一次出现的）。
 *
 * 为什么需要它：`UnpackView` 里地点是**按组**组织的，而 UI 只关心"一串地点"。
 * 去重是因为同一封信可能被重复解开（多组指向同一批 place id 是不合法的，
 * 但真出现时列表里不该出现两个一模一样的卡片）。
 *
 * @param result 解包结果
 */
export function placesOfResult(result: UnpackViewerResult): UnpackedPlace[] {
  if (!result.ok) return [];
  const seen = new Set<string>();
  const out: UnpackedPlace[] = [];
  for (const group of result.groups) {
    for (const place of group.places) {
      if (seen.has(place.placeId)) continue;
      seen.add(place.placeId);
      out.push(place);
    }
  }
  return out;
}

/**
 * 结果里的地点总数（给"共 N 个地点"这类计数用）。
 *
 * 注意：这是**地点**数，不是组数。组数属于协议红线，任何情况下都不进 UI。
 *
 * @param result 解包结果
 */
export function placeCountOfResult(result: UnpackViewerResult): number {
  return placesOfResult(result).length;
}
