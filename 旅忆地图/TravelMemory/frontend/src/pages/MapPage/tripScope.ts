/** 行程的「归属范围」判定（W15-5 降级版）。
 *
 * ## 为什么需要这一层
 *
 * 收件人把 TrailShare 信物导回主应用后，导进来的行程和自己建的行程**混在同一个列表里**：
 * 「洛阳两日」旁边躺着「来自 阿岚 的信物 · 西湖」。用户没法一眼看出哪些是自己拍的、
 * 哪些是朋友发来的，删除时也不知道自己删的是谁的东西。
 *
 * ## 为什么靠标题前缀判定（而不是数据库字段）
 *
 * 后端的 `Trip` 只有 `title / source / startedAt / endedAt`，**没有发件人字段**
 * （`fromPubkey` 在 `.trailshare` 协议里有，但导回主应用时没有落库的位置 ——
 * 加列要动后端与迁移，本轮不改）。所以约定：
 *
 *     来自 ${fromName} 的信物 · ${第一个地点}
 *
 * 这串标题由 `apps/trailshare-standalone/src/core/receivedToJourney.ts` 的
 * `senderTitleOf` 生成，正则 `^来自 (.+?) 的信物(?:\s|$)` 由本文件复刻。
 * **两边必须同步改**（这是本轮唯一的跨应用耦合点，已写进 TODO）。
 *
 * TODO(歧义): 后续阶段应给 `Trip` 加 `fromPubkey`（或 `scope`）列，标题只作展示用；
 *  那时本文件的正则可以退役为"兼容旧数据"的降级路径。
 *  （阶段 17 只接通了"朋友的分享"图层，分类仍靠标题前缀 —— 加列要动迁移，未做。）
 */

/** 行程属于谁：自己建的 / 朋友发来的。 */
export type TripScope = 'mine' | 'friends';

/**
 * 标题前缀正则。
 *
 * - 非贪婪 `(.+?)`：名字里带空格（「张 三」）时只取到第一个「 的信物」之前；
 * - `(?:\s|$)`：后面必须是空白（` · 西湖` 那种）或标题结束 ——
 *   否则「来自 阿岚 的信物们」这类标题会被误判。
 */
const SENDER_TITLE_RE = /^来自 (.+?) 的信物(?:\s|$)/;

/**
 * 标题前缀「来自 X 的信物…」→ X；不匹配返回 `null`（= 我自己的旅程）。
 *
 * @param title 任意标题（数据库里的数据按不可信处理）
 * @example senderOfTitle('来自 阿岚 的信物 · 西湖') // '阿岚'
 * @example senderOfTitle('洛阳两日') // null
 */
export function senderOfTitle(title: string): string | null {
  if (typeof title !== 'string') return null;
  const matched = SENDER_TITLE_RE.exec(title.trim());
  if (!matched) return null;
  const sender = matched[1]?.trim() ?? '';
  return sender.length > 0 ? sender : null;
}

/**
 * 按范围拆分行程：无发件人前缀的归「我的」，其余归「朋友的」。
 *
 * 两个数组都保持**原顺序**（列表顺序 = 后端返回顺序 = 用户熟悉的顺序），
 * 也不复制对象（调用方拿到的就是原对象引用）。
 *
 * @param trips 任意带 `title` 的行程数组
 */
export function splitTrips<T extends { title: string }>(
  trips: readonly T[],
): { mine: T[]; friends: T[] } {
  const mine: T[] = [];
  const friends: T[] = [];
  for (const trip of trips ?? []) {
    if (!trip) continue;
    if (senderOfTitle(trip.title) === null) mine.push(trip);
    else friends.push(trip);
  }
  return { mine, friends };
}

/**
 * 朋友的行程按发件人分组。
 *
 * - 组顺序 = 发件人**首次出现**的顺序（不是字典序：用户刚收到的那个应该在上面）；
 * - 组内顺序 = 输入顺序。
 *
 * @param trips 已经过 `splitTrips` 的 `friends`（传全量也能跑，但要把 `mine` 混进来
 *              会出现 `sender: ''` 那种怪组，所以本函数要求调用方先拆）
 */
export function groupFriendsBySender<T extends { title: string }>(
  trips: readonly T[],
): Array<{ sender: string; trips: T[] }> {
  const groups = new Map<string, T[]>();
  for (const trip of trips ?? []) {
    if (!trip) continue;
    const sender = senderOfTitle(trip.title);
    // 没有前缀的不进任何组：分组的语义是"谁发给我的"，无主行程不该出现在这里
    if (sender === null) continue;
    const bucket = groups.get(sender);
    if (bucket) bucket.push(trip);
    else groups.set(sender, [trip]);
  }
  return [...groups.entries()].map(([sender, items]) => ({ sender, trips: items }));
}
