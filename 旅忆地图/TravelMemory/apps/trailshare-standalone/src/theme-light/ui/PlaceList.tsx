/** 地点列表（W12-4）：按日期分组渲染地点、标签、强度、配文与照片。
 *
 * ## 为什么"按日期分组"写在导出函数里
 *
 * 分组是**数据决策**（同一天多个地点要合成一段），不是渲染细节：它决定了时间线的
 * 顺序与段落边界。抽成 `groupPlacesByDate` 之后可以直接单测，也方便 `PlaceBoard`
 * 判断"点地图要不要滚到某一项"。
 *
 * ## 照片为什么是 data URL
 *
 * 解包出来的照片是 `ArrayBuffer`（密文解开后的原图字节），**只存在于内存**：
 * 转成 `data:image/jpeg;base64,…` 直接给 `<img>`，用完即弃，不落 IndexedDB、
 * 不发 blob URL（blob URL 会活到页面关闭，等于给照片续了命）。
 *
 * ## 不显示组
 *
 * 这个列表**只认地点**。组 id / 组数一律不出现 —— 那是协议红线（见 `unpack.ts` 文件头）：
 * "还有 N 组没解开"本身就是在告诉收件人"有人收到了你没收到的东西"。
 */

import {
  ALL_INTENSITIES,
  INTENSITY_MEANING,
  INTENSITY_STARS,
  TAG_LABELS,
  type UnpackedPlace,
} from '../../types/share';
import { toBase64 } from '../../utils/base64';

export interface PlaceListProps {
  places: readonly UnpackedPlace[];
  /** 当前选中的地点（地图联动时高亮）。 */
  selectedId?: string | null;
  /** 点一项时回调（不给就是纯展示）。 */
  onSelect?: (placeId: string) => void;
  /** 空列表时的提示。 */
  emptyHint?: string;
}

/** 一天的段落：`date` + 当天地点（保持传入顺序）。 */
export interface PlaceDateGroup {
  date: string;
  places: UnpackedPlace[];
}

/**
 * 按 `date` 分组，日期升序（`YYYY-MM-DD` 可以直接字符串比较），组内保持原顺序。
 *
 * @param places 解包出来的地点
 */
export function groupPlacesByDate(places: readonly UnpackedPlace[]): PlaceDateGroup[] {
  const buckets = new Map<string, UnpackedPlace[]>();
  for (const place of places) {
    const key = typeof place.date === 'string' && place.date.length > 0 ? place.date : '未写日期';
    const bucket = buckets.get(key);
    if (bucket) bucket.push(place);
    else buckets.set(key, [place]);
  }
  return [...buckets.entries()]
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
    .map(([date, items]) => ({ date, places: items }));
}

/**
 * 照片字节 → `data:` URL。
 *
 * @param image 解包出来的 JPEG 字节
 */
export function imageToDataUrl(image: ArrayBuffer): string {
  return `data:image/jpeg;base64,${toBase64(new Uint8Array(image))}`;
}

/** 强度星星；未知值退回"★"（脏数据不该让整行渲染失败）。 */
function starsOf(intensity: string): string {
  return INTENSITY_STARS[intensity as keyof typeof INTENSITY_STARS] ?? '★';
}

/** 强度中文；未知值原样显示。 */
function meaningOf(intensity: string): string {
  return INTENSITY_MEANING[intensity as keyof typeof INTENSITY_MEANING] ?? intensity;
}

/** 标签中文名；未知值原样显示（协议可能扩标签，旧前端不该崩）。 */
function tagLabelOf(tag: string): string {
  return TAG_LABELS[tag as keyof typeof TAG_LABELS] ?? tag;
}

export function PlaceList({
  places,
  selectedId = null,
  onSelect,
  emptyHint = '这个信物里还没有地点',
}: PlaceListProps): JSX.Element {
  const groups = groupPlacesByDate(places);

  if (groups.length === 0) {
    return <p className="hint place-list-empty">{emptyHint}</p>;
  }

  return (
    <div className="place-list">
      {groups.map((group) => (
        <section key={group.date} className="place-date" data-date={group.date}>
          <h3 className="place-date-head">{group.date}</h3>
          <ul className="place-rows">
            {group.places.map((place) => {
              const active = place.placeId === selectedId;
              const clickable = typeof onSelect === 'function';
              return (
                <li
                  key={place.placeId}
                  className={active ? 'place-row is-active' : 'place-row'}
                  data-place-id={place.placeId}
                  onClick={clickable ? () => onSelect?.(place.placeId) : undefined}
                  onKeyDown={
                    clickable
                      ? (event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            onSelect?.(place.placeId);
                          }
                        }
                      : undefined
                  }
                  role={clickable ? 'button' : undefined}
                  tabIndex={clickable ? 0 : undefined}
                  aria-pressed={clickable ? active : undefined}
                >
                  <div className="place-row-head">
                    <span className="place-row-name">{place.name || '（未命名地点）'}</span>
                    <span className="badge">{tagLabelOf(place.tag)}</span>
                    <span className="badge badge-dim">
                      {starsOf(place.intensity)} {meaningOf(place.intensity)}
                    </span>
                    {place.label ? <span className="hint">{place.label}</span> : null}
                    {typeof place.lat === 'number' && typeof place.lng === 'number' ? (
                      <span className="hint place-row-geo">已标注位置</span>
                    ) : null}
                  </div>

                  {place.photos.length > 0 ? (
                    <ul className="place-photos">
                      {place.photos.map((photo) => (
                        <li key={photo.photoId} className="place-photo">
                          <img
                            className="place-photo-img"
                            src={imageToDataUrl(photo.image)}
                            alt={photo.caption ?? ''}
                            loading="lazy"
                          />
                          {photo.caption ? (
                            <p className="place-photo-caption">{photo.caption}</p>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="hint">这里没有照片</p>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

/** 导出给测试/调用方：所有合法强度的中文名（避免各处硬编码）。 */
export const INTENSITY_LABELS = ALL_INTENSITIES.map((value) => ({
  value,
  label: INTENSITY_MEANING[value],
  stars: INTENSITY_STARS[value],
}));
