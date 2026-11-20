/** 已接受的地点 → `journey.json`（W15-4）：**纯函数**，不碰 DOM、不碰存储、不发请求。
 *
 * ## 为什么单独一层（而不是在 App 里拼）
 *
 * "收下的地点怎么变成一条能导回主应用的旅程"是这段功能里最容易被写歪的地方：
 * 分天、天/地点/照片 id 的生成、缩略图与元数据的绑定，任何一处错了都是
 * **静默错误**（文件还是打得开，只是照片错位或丢天）。抽成纯函数后单测能直接钉住。
 *
 * ## 为什么不用 `photoMetaFromDrafts`（W15-2 的教训）
 *
 * 那个函数靠"天→地点→照片"的**结构位置**把草稿缩略图对到 trip 的照片 id 上。
 * 接收侧没有草稿，而且地点是用户逐个挑的（还会跨封信合并），位置关系没有任何意义。
 * 这里改用**显式 id**：照片 id = `${地点 id}:${序号}`，`photoMeta` 的键就是它 ——
 * 无论怎么分组、怎么排序都不会错位。
 *
 * ## 红线
 *
 * - 输出 `JourneyBundle` 里**没有原图字节**：`TripPhoto.image` 一律写
 *   `new ArrayBuffer(0)`，而 `bundleFromTrip` 从不读 `image`（它只读 id 与 caption，
 *   图与元数据来自 `photoMeta`）。这条是"收件人不能靠 journey.json 拿到原图"的保证。
 * - 已拒绝的地点**不进包**（即便调用方忘了过滤，这里也再挡一次）。
 */

import { bundleFromTrip, type JourneyBundle, type JourneyPhotoLike } from '@travelmemory/journey-transfer';

import type { Day, Place, Trip, TripPhoto } from '../types/share';
import { newId } from '../utils/id';
import { UNNAMED_SENDER, type ReceivedPlace } from './receivedStore';

/** 一天的 id 形状：`d_1`、`d_2`…（与 `.trailshare` 的 `d1_xxxxxx` 不同，这里是线格式里的
 *  普通标识，只要在 bundle 内唯一即可）。 */
function dayId(index: number): string {
  return `d_${index + 1}`;
}

/** 照片在 bundle 里的 id：`${地点 id}:${序号}`（也是 `photoMeta` 的键）。 */
export function journeyPhotoId(placeId: string, index: number): string {
  return `${placeId}:${index}`;
}

/** `YYYY-MM-DD` 的合法性判据与 `buildTrip.isValidDate` 同口径，只做本地日期。 */
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** 非法日期的地方统一落到这一天（宁可日期不准，也让用户能看见收下的地点）。 */
function safeDate(date: string): string {
  return DATE_RE.test(date ?? '') ? date : new Date().toISOString().slice(0, 10);
}

/* ── 标题约定（W15-5 的接口，别随手改） ───────────────────────
 *
 * 主应用的 `Trip` **没有** `fromPubkey` 字段（只有 `id/title/coverPlaceId/createdAt/days`），
 * 所以"这趟旅程是哪个发件人给的"只能从**标题前缀**反解：
 *
 *     `来自 ${fromName} 的信物 · ${第一个地点的 placeName}`
 *
 * 两个理由：
 * 1. 前缀 `^来自 (.+?) 的信物` 是 W15-5 分组的唯一依据 —— 改了这条约定，
 *    已经导进主应用的旅程就再也分不到发件人名下；
 * 2. 带上第一个地点名，同一发件人的多趟旅程在列表里才**可区分**
 *    （否则全是「来自 张三 的信物」，用户面对一整列同名条目）。
 *
 * `·` 之后的部分是**给人看的**，解析时忽略（正则只取到 ` 的信物` 为止）。
 */

/** 标题前缀（导出时写、解析时读，两边必须用同一份常量）。 */
export const SENDER_TITLE_PREFIX = '来自 ';
/** 标题里名字的收尾。解析时忽略它之后的一切（`· 地点` 那段）。 */
export const SENDER_TITLE_MARK = ' 的信物';
/** 名字与第一处地点之间的分隔符。 */
export const SENDER_TITLE_SEPARATOR = ' · ';

/** 反解用的正则：非贪婪，名字里带空格也只会取到第一个 ` 的信物` 为止。 */
const SENDER_TITLE_RE = /^来自 (.+?) 的信物/;

/**
 * 生成导回主应用的旅程标题。
 *
 * @param fromName 发送方显示名（空 → `（未署名）`）
 * @param firstPlaceName 第一个地点的名字；空则不带 ` · 地点` 后缀
 * @example senderTitleOf('张三', '西湖') // '来自 张三 的信物 · 西湖'
 */
export function senderTitleOf(fromName: string, firstPlaceName?: string | null): string {
  const name = fromName?.trim() || UNNAMED_SENDER;
  const base = `${SENDER_TITLE_PREFIX}${name}${SENDER_TITLE_MARK}`;
  const place = firstPlaceName?.trim() ?? '';
  return place.length > 0 ? `${base}${SENDER_TITLE_SEPARATOR}${place}` : base;
}

/**
 * 从旅程标题反解发送方显示名（W15-5 分组用）。
 *
 * 只认 `来自 X 的信物 …` 这个前缀；其它标题（用户自己建的旅程、旧数据）一律 `null`，
 * 调用方据此把它们放进"我自己的旅程"。
 *
 * @param title 任意标题（接受 `unknown`：主应用侧拿到的是数据库里的不可信数据）
 * @returns 发件人名，或 `null`（不是信物标题 / 名字为空）
 * @example parseSenderFromTitle('来自 张 三 的信物 · 西湖') // '张 三'
 * @example parseSenderFromTitle('杭州两日') // null
 */
export function parseSenderFromTitle(title: unknown): string | null {
  if (typeof title !== 'string') return null;
  const matched = SENDER_TITLE_RE.exec(title.trim());
  if (!matched) return null;
  const name = matched[1]?.trim() ?? '';
  return name.length > 0 ? name : null;
}

/**
 * 已接受的地点 → `JourneyBundle`。
 *
 * - 按 `date` 分天（同一天合并成一天，日期升序；同一天内保持调用方给的顺序）；
 * - 地点 id 直接用 `ReceivedPlace.id`（`shareId:placeId`，跨封信也不会撞）；
 * - 照片 id = `${地点 id}:${序号}`，`photoMeta` 用它绑定缩略图与坐标；
 * - `takenAt` 统一写 `${date}T00:00:00Z`：地点只有日期没有时刻，编一个时刻比留空更危险
 *   （主应用会按它排序，假时刻会让同一天里的地点顺序看起来"有依据"）；
 * - 标题是 `来自 ${fromName} 的信物 · ${第一个地点名}`（见 `SENDER_TITLE_*`），
 *   W15-5 靠它反解发件人。
 *
 * @param places   已接受的地点（`rejected: true` 的会被过滤掉）
 * @param fromName 发送方显示名（标题用它）
 * @param now      导出时间（测试用），缺省 = 当前时刻
 */
export function acceptedToJourney(
  places: readonly ReceivedPlace[],
  fromName: string,
  now: string = new Date().toISOString(),
): JourneyBundle {
  const accepted = (places ?? []).filter((place) => place && place.rejected !== true);

  // 日期升序、同一天保持原顺序：先按日期稳定排序，再由下面的循环合并成天。
  const ordered = [...accepted].sort((a, b) => {
    const left = safeDate(a.date);
    const right = safeDate(b.date);
    if (left === right) return 0;
    return left < right ? -1 : 1;
  });

  const photoMeta = new Map<string, JourneyPhotoLike>();
  const days: Day[] = [];

  for (const received of ordered) {
    const date = safeDate(received.date);
    let day = days[days.length - 1];
    if (!day || day.date !== date) {
      day = { id: dayId(days.length), date, places: [] } satisfies Day;
      days.push(day);
    }

    const photos: TripPhoto[] = received.photos.map((photo, index) => {
      const id = journeyPhotoId(received.id, index);
      const meta: JourneyPhotoLike = {
        thumbnailDataUrl: photo.thumbnailDataUrl,
        takenAt: `${date}T00:00:00Z`,
      };
      if (typeof received.lat === 'number' && Number.isFinite(received.lat)) meta.lat = received.lat;
      if (typeof received.lng === 'number' && Number.isFinite(received.lng)) meta.lng = received.lng;
      photoMeta.set(id, meta);

      const tripPhoto: TripPhoto = {
        id,
        // bundle 不读 image（见文件头）：写空 buffer 而不是把原图塞进来。
        image: new ArrayBuffer(0),
      };
      const caption = photo.caption?.trim();
      if (caption && caption.length > 0) tripPhoto.caption = caption;
      return tripPhoto;
    });

    const place: Place = {
      id: received.id,
      name: received.placeName,
      tag: received.tag,
      // 收件人没有"选封面"这一步：第一张就是封面。
      intensity: received.intensity,
      coverIndex: 0,
      photos,
    };
    const label = received.label?.trim();
    if (label && label.length > 0) place.label = label;
    if (typeof received.lat === 'number' && Number.isFinite(received.lat)) place.lat = received.lat;
    if (typeof received.lng === 'number' && Number.isFinite(received.lng)) place.lng = received.lng;

    day.places.push(place);
  }

  const sender = fromName?.trim() || UNNAMED_SENDER;
  const firstPlace = days[0]?.places[0];
  const trip: Trip = {
    id: newId('t', 8),
    // 标题约定见 `SENDER_TITLE_*`：主应用没有 fromPubkey，W15-5 只能靠这个前缀分组。
    title: senderTitleOf(sender, firstPlace?.name),
    createdAt: now,
    days,
    ...(firstPlace ? { coverPlaceId: firstPlace.id } : {}),
  };

  return bundleFromTrip(trip, photoMeta, { exportedAt: now });
}
