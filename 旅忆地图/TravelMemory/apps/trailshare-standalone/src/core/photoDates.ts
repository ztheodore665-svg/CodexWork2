/** 用照片的拍摄日期给「天」定日期（W15-1 的收尾修复）。
 *
 * ## 为什么需要它（QA 判 FAIL 的那条）
 *
 * 选照片时 `takenAt` 一路传到了小应用（`StudioEmbed` 会把主应用的
 * `takenAt ?? takenAtLocal` 放进 init），但**没有任何地方消费它去填日期**：
 * `emptyDraft()` 的 `day.date = todayIso()`，导入照片只往地点里追加照片、不动日期。
 * 于是编辑页的日期框永远显示"今天"，生成的包日期也是今天 —— 用户的记忆被改成了
 * 他今天导照片的那一天。
 *
 * ## 为什么直接取前 10 位，而不是 `new Date(...)` 转 UTC
 *
 * 后端的 `takenAt` 可能是 `taken_at_local`（**本地墙钟**）。`new Date('2025-10-04T23:30-05:00')`
 * 在东八区会算成 10-05 —— 差一天。照片日期是**日历日**，不是时刻：字符串切前 10 位
 * 得到的正是拍摄那天的日历日，跨时区也不会漂。
 *
 * ## 不看 `takenAtLocal` 的第二个来源
 *
 * 只认 `takenAt` 一个字段是刻意的：主应用在 `init` 里已经把
 * `takenAt ?? takenAtLocal` 合并到这一个字段上（W15-1 的第一处修复），
 * 两条链路（embed / standalone）都只有这一个入口，这里再判一次只会让规则分叉。
 */

import { isValidDate, type TripDraft } from './buildTrip';

/** `YYYY-MM-DD` 前缀（允许后面跟 `T...` / 空格 / 时区）。 */
const DATE_PREFIX_RE = /^(\d{4}-\d{2}-\d{2})/;

/**
 * 从一张照片的 `takenAt` 里取出**日历日**。
 *
 * @param takenAt ISO 串（`2025-10-04T12:00:00Z`）或只有日期（`2025-10-04`）
 * @returns `YYYY-MM-DD`，或 `null`（缺失 / 形状不对 / 不是真实日期）
 */
export function dateOfTakenAt(takenAt: unknown): string | null {
  if (typeof takenAt !== 'string') return null;
  const matched = DATE_PREFIX_RE.exec(takenAt.trim());
  if (!matched) return null;
  const day = matched[1];
  // 形状对但日期不存在（2025-02-31 / 2025-13-01）一律当没有：宁可显示"今天"，
  // 也不要把一个不存在的日子写进包里（`PlaceMeta.date` 会被拿去排序）
  return isValidDate(day) ? day : null;
}

/**
 * 从一组照片里取**最早**的有效日期。
 *
 * @param photos 任意带可选 `takenAt` 的对象（草稿照片、系统照片都行）
 * @returns `YYYY-MM-DD` 或 `null`（一张有效日期都没有）
 */
export function earliestTakenDate(photos: readonly { takenAt?: string }[]): string | null {
  let earliest: string | null = null;
  for (const photo of photos ?? []) {
    const day = dateOfTakenAt(photo?.takenAt);
    if (day === null) continue;
    // `YYYY-MM-DD` 定长，字典序 == 时间序
    if (earliest === null || day < earliest) earliest = day;
  }
  return earliest;
}

/** 一天里有没有照片（所有地点合计）。 */
function dayHasPhotos(day: TripDraft['days'][number]): boolean {
  return day.places.some((place) => (place.photos?.length ?? 0) > 0);
}

/**
 * 仅当某天还没有照片时，用照片日期回填 `day.date`（不覆盖用户已改过的日期）。
 *
 * ## 只填**第一个**空天
 *
 * 一组照片只对应一个拍摄日（这里取最早那天）。如果把所有空天都填成同一天，
 * 用户会得到两个日期相同的"天" —— 协议允许，但那不是他的意思，还得手工改回来。
 * 所以扫描顺序里第一个没有照片的天被填，其余原样。
 *
 * ## 接入点（重要）
 *
 * 调用方要在**往天里塞照片之前**调它：塞完之后那天就有照片了，
 * "0 张照片才回填"的条件会立刻失效 —— 这正是原 bug 的形态（先塞照片，没人管日期）。
 *
 * @param draft  当前草稿
 * @param photos 这次导入的照片（只读，用来取最早日期）
 * @returns 新草稿（没有任何改动时返回**原对象**）
 */
export function seedDayDates(
  draft: TripDraft,
  photos: readonly { takenAt?: string }[],
): TripDraft {
  if (!draft || !Array.isArray(draft.days) || draft.days.length === 0) return draft;
  const seed = earliestTakenDate(photos);
  if (seed === null) return draft;

  const index = draft.days.findIndex((day) => day && !dayHasPhotos(day));
  if (index < 0) return draft; // 每天都有照片：用户已经在用了，不动
  const target = draft.days[index];
  if (target.date === seed) return draft; // 已经是那天，别制造新对象

  const days = draft.days.slice();
  // 只换 date，id 与其它字段（含 places 数组引用）保持不动：UI 的 key 与选中态都不受影响
  days[index] = { ...target, date: seed };
  return { ...draft, days };
}
