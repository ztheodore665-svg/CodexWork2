/** 生成页：时间线预览 + 「生成信物」。
 *
 * 预览必须是**只读**的、和最终包内容一一对应的：用户在这里看到的
 * 「日期 → 地点 + 标签 + 强度 + 照片数」就是收件人会看到的结构
 * （地点名与日期在密文里，标签与强度在明文里 —— 这是协议的决定）。
 *
 * 阶段 13 / W13-4：深色版**没有地图**，所以整页只有纯文本时间线（`PlaceList` 同款结构）；
 * 浅色版（embed）才换成「地图 + 侧栏」（`PlaceBoard`）。两边展示的是同一份草稿 ——
 * 而且都是**过了 `splitOversizedPlaces` 的草稿**：>5 张照片的地点会在生成时被拆成两个，
 * 预览必须提前这么显示，否则"预览 ≠ 产物"。
 */

import type { UnpackedPlace } from '../../types/share';
import { splitOversizedPlaces, validateDraft, type TripDraft } from '../../core/buildTrip';
import { formatBytes } from '../../core/packFlow';
import { PlaceList } from './PlaceList';

export interface PreviewPanelProps {
  draft: TripDraft;
  /** 用户填的收件人数（0 = 只有自己）。 */
  recipientCount: number;
  busy: boolean;
  progressText: string | null;
  error: string | null;
  onGenerate: () => void;
  onBack: () => void;
}

/**
 * 草稿里是否存在"会进包"的坐标（决定要不要显示"精确坐标"提示）。
 *
 * 与 `buildTrip` 的 `firstGeoOf` 同一判据：每个地点取第一张带合法坐标的照片，
 * 有任意一个地点命中就返回 true。
 *
 * @param draft 当前草稿
 */
function hasCoordinates(draft: TripDraft): boolean {
  for (const day of draft.days) {
    for (const place of day.places) {
      for (const photo of place.photos) {
        const { lat, lng } = photo;
        if (typeof lat !== 'number' || !Number.isFinite(lat)) continue;
        if (typeof lng !== 'number' || !Number.isFinite(lng)) continue;
        if (lat < -90 || lat > 90 || lng < -180 || lng > 180) continue;
        return true;
      }
    }
  }
  return false;
}

/**
 * 草稿 → 地图/列表都能吃的 `UnpackedPlace[]`。
 *
 * 坐标来源：`PlaceDraft` 本身没有 lat/lng（协议里坐标只挂在照片上，由后端模糊后
 * 写进 `PlaceMeta`），所以取**该地点第一张带坐标的照片**的位置作为地点的代表点。
 * TODO(歧义): 同一地点多张照片坐标不同时，更严谨的做法是取重心；这里取第一张，
 * 因为"第一张"是用户心里的封面图，与 `coverIndex` 的语义一致。
 */
function draftPlaces(draft: TripDraft): UnpackedPlace[] {
  const out: UnpackedPlace[] = [];
  for (const day of draft.days) {
    for (const place of day.places) {
      const geo = place.photos.find(
        (photo) => typeof photo.lat === 'number' && typeof photo.lng === 'number',
      );
      const item: UnpackedPlace = {
        placeId: place.id,
        name: place.name,
        date: day.date,
        tag: place.tag,
        intensity: place.intensity,
        photos: place.photos.map((photo) => ({
          photoId: photo.id,
          image: photo.bytes,
          ...(photo.caption ? { caption: photo.caption } : {}),
        })),
      };
      if (place.label) item.label = place.label;
      if (geo && typeof geo.lat === 'number' && typeof geo.lng === 'number') {
        item.lat = geo.lat;
        item.lng = geo.lng;
      }
      out.push(item);
    }
  }
  return out;
}

export function PreviewPanel(props: PreviewPanelProps): JSX.Element {
  const { draft, busy, progressText, error } = props;
  // W15-2 收尾：预览必须看**拆分后**的草稿 —— `buildTrip` 会把 >5 张的地点拆成
  // 「西湖（1/2）」「西湖（2/2）」，如果这一页还按原草稿渲染，用户看到 1 个地点 6 张照片、
  // 收件人收到 2 个地点 5+1 张。预览 = 产物的承诺不能破，所以这里用同一个纯函数。
  const preview = splitOversizedPlaces(draft);
  const problem = validateDraft(preview);
  const dayCount = preview.days.length;
  const placeCount = preview.days.reduce((sum, day) => sum + day.places.length, 0);
  const totalPhotos = preview.days.reduce(
    (sum, day) => sum + day.places.reduce((inner, place) => inner + place.photos.length, 0),
    0,
  );

  return (
    <div className="panel">
      <header className="panel-head">
        <div>
          <h1>{draft.title || '未命名旅程'}</h1>
          <p className="lead">
            {dayCount} 天 · {placeCount} 个地点 · {totalPhotos} 张照片 ·{' '}
            {props.recipientCount === 0
              ? '未填收件人（只有你自己能打开）'
              : `收件人 ${props.recipientCount} 位 + 你自己`}
          </p>
        </div>
        <button type="button" className="btn btn-ghost" disabled={busy} onClick={props.onBack}>
          返回编辑
        </button>
      </header>

      {/* 深色版固定走纯文本：`PlaceList` 负责"按天分组 + 标签 + 强度 + 照片数"，
          与下面的 timeline 是同一份数据，只是列表更适合没有地图的版面。 */}
      <PlaceList
        places={draftPlaces(preview)}
        emptyHint="还没有地点：先在编辑页加一个地点并放照片"
      />

      <p className="hint">
        包里的内容：照片原图（AES-GCM 加密）、地点名与日期（加密）、标签与强度（明文，收件人解封前就能看到分类）。
        {props.recipientCount === 0 ? ' 未填收件人，这个包只有你自己能打开。' : ''}
      </p>
      {/* W12-4：坐标现在真的会进包（`buildGeoHints`），必须**可见地**告知 ——
          用户可以接受"带坐标"，但不能接受"以为自己没带"。本版本没有后端地理模糊，
          所以写的是"精确坐标"，而不是"模糊后的位置"。 */}
      {hasCoordinates(preview) ? (
        <p className="hint hint-warn">信物会包含这些地点的精确坐标（本版本未做地理模糊）。</p>
      ) : null}
      <p className="hint">预计包体：约 {formatBytes(estimateBytes(preview))}（加密后会略大）。</p>

      {problem ? <p className="error">{problem}</p> : null}
      {error ? <p className="error">{error}</p> : null}
      {progressText ? <p className="hint hint-strong">{progressText}</p> : null}

      <footer className="panel-foot">
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || problem !== null}
          onClick={props.onGenerate}
        >
          {busy ? '正在生成…' : '生成信物'}
        </button>
      </footer>
    </div>
  );
}

/** 粗估包体：照片字节 × 1.37（base64 膨胀）+ 元数据的固定开销。 */
function estimateBytes(draft: TripDraft): number {
  let photoBytes = 0;
  for (const day of draft.days) {
    for (const place of day.places) {
      for (const photo of place.photos) photoBytes += photo.bytes.byteLength;
    }
  }
  return Math.round(photoBytes * 1.37) + 2048;
}
