/** 深色版补点页（阶段 13 / W13-2：从 `showcase/src/ui/SetupPanel.tsx` 移植并改造）。
 *
 * ## 与 showcase 版本的差异（都是为了"零网络"与"复用 core"）
 *
 * | 项 | showcase | 这里 |
 * |---|---|---|
 * | 底图 | SDK 控制器（可联网）+ 降级格网 | **只有** `map/gridMap` 的自绘格网 |
 * | 照片来源 | 自带 `readExif` / `makeThumb`（独立实现） | `core/photoImport` 的 `SystemPhoto`（读 EXIF + 缩略图都在 `@travelmemory/photo-source` 里） |
 * | 写回坐标 | 直接改 `Photo[]` | `core/validate` 的 `applyLocation`（同一份校验 + 纯函数写回） |
 * | 交通工具编辑 | 有（「每段路怎么走」） | 有（阶段 14 / W14-3a 补回，见下方「每段交通工具」） |
 *
 * 保留 showcase 的两个交互决策（都是实测结论）：
 *  1. 点一次地图可以给**多张**选中的照片写坐标，且带一点点扇形偏移
 *     （`BATCH_FAN_DEG`）—— 否则多张照片落在同一点，轨迹长度是 0，播放时一动不动；
 *  2. 拖动不算点击（`moved` 判定在 `gridMap` 里），所以平移地图不会误写坐标。
 *
 * ## 每段交通工具（阶段 14 / W14-3a 补回）
 *
 * 段由 `transportPlan.ts` 的 `buildTransportSegments` 算出来（纯函数、有单测），
 * 这里只负责渲染与回写：每个相邻的「带坐标」照片对一行 `<select>`，
 * 写回 `transports[出发照片 id]` —— 与 `Photo.transportToNext` 的索引键一致，
 * 所以父组件可以把这个对象**原样**塞进 `toTrailPhotos({ transports })`。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { TRANSPORT_MODES, TRANSPORT_STYLE, type TransportMode } from '@travelmemory/trail';
import type { SystemPhoto } from '@travelmemory/photo-source';

import { placeNameFromFileName } from '../../core/photoImport';
import { isValidLatLng } from '../../core/validate';
import { buildTransportSegments } from '../transportPlan';
import { createGridMap, type GridMapController } from '../map/gridMap';

/**
 * 批量定位时给每张照片一点点扇形偏移（约 45m 递增）。
 * 纯粹为了让轨迹在「多张照片落在同一处」时仍然画得出来，**不代表真实位置** ——
 * 与 showcase 的 `BATCH_FAN_DEG` 同值，改动要两边一起动（视觉一致）。
 */
const BATCH_FAN_DEG = 0.00042;

/** 地图初始视野：有坐标的照片取包围盒，一张都没有时看中国全域。 */
const DEFAULT_VIEW = { center: { lng: 104.2, lat: 35.6 }, zoom: 4.4 };

function isUnlocated(photo: SystemPhoto): boolean {
  return !isValidLatLng(photo.lat, photo.lng);
}

function initialView(photos: readonly SystemPhoto[]): {
  center: { lng: number; lat: number };
  zoom: number;
} {
  const located = photos.filter((photo) => !isUnlocated(photo));
  if (located.length === 0) return DEFAULT_VIEW;

  let minLng = 180;
  let maxLng = -180;
  let minLat = 90;
  let maxLat = -90;
  for (const photo of located) {
    const lng = photo.lng as number;
    const lat = photo.lat as number;
    if (lng < minLng) minLng = lng;
    if (lng > maxLng) maxLng = lng;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }
  const span = Math.max(maxLng - minLng, (maxLat - minLat) * 1.4, 0.02);
  const zoom = Math.min(12, Math.max(3.2, Math.log2(2025 / span) - 0.6));
  return {
    center: { lng: (minLng + maxLng) / 2, lat: (minLat + maxLat) / 2 },
    zoom,
  };
}

/** `takenAt`（ISO UTC）→ `2026-04-18 01:00 UTC`；缺失时给一句人话。 */
function showTakenAt(takenAt: string | undefined): string {
  if (typeof takenAt !== 'string' || takenAt.length === 0) return '无拍摄时间';
  return `${takenAt.replace('T', ' ').slice(0, 16)} UTC`;
}

export interface SetupPanelProps {
  photos: SystemPhoto[];
  /** 每段交通工具：`{ 出发照片 id: 模式 }`。缺省项按自驾。 */
  transports: Record<string, TransportMode>;
  /** 写回一张照片的坐标（WGS84）。父组件用 `core/validate` 的 `applyLocation` 更新状态。 */
  onApplyLocation: (photoId: string, lat: number, lng: number) => void;
  /** 写回「这张照片到下一张」的交通工具。 */
  onTransport: (photoId: string, mode: TransportMode) => void;
  /** 继续从系统相册追加照片。 */
  onAddPhotos: () => void;
  /** 开始旅程（带位置的照片 ≥ 2 时可用）。 */
  onStart: () => void;
  onBack: () => void;
  busy: boolean;
  importing: { done: number; total: number } | null;
}

export function SetupPanel(props: SetupPanelProps): JSX.Element {
  const { photos, busy, importing } = props;
  const [selected, setSelected] = useState<string[]>([]);
  const hostRef = useRef<HTMLDivElement>(null);

  /** 地图点击回调挂在 effect 里注册一次，读不到最新 state，所以用 ref 透传。 */
  const selectedRef = useRef<string[]>(selected);
  const photosRef = useRef<SystemPhoto[]>(photos);
  const applyRef = useRef<(photoId: string, lat: number, lng: number) => void>(() => undefined);
  applyRef.current = props.onApplyLocation;
  selectedRef.current = selected;
  photosRef.current = photos;

  const sorted = useMemo(
    () =>
      [...photos].sort((a, b) => {
        const left = Date.parse(a.takenAt ?? '');
        const right = Date.parse(b.takenAt ?? '');
        const leftMs = Number.isFinite(left) ? left : 0;
        const rightMs = Number.isFinite(right) ? right : 0;
        return leftMs - rightMs;
      }),
    [photos],
  );

  const pendingCount = useMemo(() => photos.filter(isUnlocated).length, [photos]);
  const locatedCount = photos.length - pendingCount;

  /**
   * 每段交通工具（W14-3a）。
   *
   * `photos` 一变就重算：补完坐标后段会变多（一张"待定位"的照片被定位后，
   * 它前后的相邻关系都成立）。`transports` 也要进依赖 —— 否则用户选了"火车"，
   * 这一行会跳回"自驾"（受控 `<select>` 的值来自 `segment.mode`）。
   */
  const segments = useMemo(
    () => buildTransportSegments(photos, props.transports),
    [photos, props.transports],
  );

  /** 点地图：给选中的照片（没有选中时给第一张待定位的）写坐标。 */
  const handleMapClick = useCallback((lng: number, lat: number) => {
    if (!isValidLatLng(lat, lng)) return;
    const current = photosRef.current;
    const ids = selectedRef.current.filter((id) => current.some((photo) => photo.id === id));
    const targets =
      ids.length > 0 ? ids : current.filter(isUnlocated).slice(0, 1).map((photo) => photo.id);
    if (targets.length === 0) return;

    const fan = targets.length > 1;
    targets.forEach((id, index) => {
      const offset = fan ? index * BATCH_FAN_DEG : 0;
      const nextLat = Math.min(85, Math.max(-85, lat + offset * 0.6));
      const nextLng = lng + offset;
      if (!isValidLatLng(nextLat, nextLng)) return;
      applyRef.current(id, nextLat, nextLng);
    });

    // 写回后自动选中"下一张仍缺坐标的"，让连续补点不需要反复点列表
    const remaining = current.filter((photo) => isUnlocated(photo) && !targets.includes(photo.id));
    setSelected(remaining.length > 0 ? [remaining[0].id] : []);
  }, []);

  /* ── 建地图（只建一次；初始视野取挂载那一刻） ───────────────── */
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const inner = document.createElement('div');
    inner.style.cssText = 'position:absolute;inset:0;';
    host.appendChild(inner);

    const view = initialView(photosRef.current);
    let controller: GridMapController | null = createGridMap(inner, {
      center: view.center,
      zoom: view.zoom,
      // 这一页的中心来自照片 EXIF（WGS84），所以平面就是 WGS84：
      // 省界要换算一次，点击回调则原样返回 WGS84（见 `if (options.coordinateSystem)` 的说明）。
      coordinateSystem: 'wgs84',
      interactive: true,
      paintGrid: true,
    });
    controller.onClick((lng, lat) => handleMapClick(lng, lat));

    return () => {
      controller?.destroy();
      controller = null;
      if (inner.parentElement) inner.parentElement.removeChild(inner);
    };
    // 只挂一次：初始视野与容器都属于"挂载期"资源（与浅色版 LocationEditor 同理）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = (photoId: string, multi: boolean): void => {
    setSelected((prev) => {
      if (!multi) return prev[0] === photoId && prev.length === 1 ? [] : [photoId];
      return prev.includes(photoId) ? prev.filter((id) => id !== photoId) : [...prev, photoId];
    });
  };

  const selectedSet = new Set(selected);

  return (
    <div className="setup">
      <div className="setup-side">
        <div className="setup-head">
          <div className="setup-title">
            <button
              type="button"
              className="btn btn-ghost"
              style={{ height: 24, padding: '0 8px' }}
              onClick={props.onBack}
            >
              ← 返回
            </button>
            <span style={{ color: 'var(--muted)', fontSize: 10, letterSpacing: '0.24em' }}>
              SETUP
            </span>
          </div>
          <div className="setup-sub">
            {photos.length === 0
              ? '还没有照片。导入后可以在地图上逐张补地点。'
              : pendingCount > 0
                ? `${pendingCount} 张缺少地点 · 共 ${photos.length} 张`
                : `全部 ${photos.length} 张都有地点`}
          </div>
          <div className="setup-tools">
            <button type="button" className="chip" disabled={busy} onClick={props.onAddPhotos}>
              添加照片
            </button>
            {pendingCount > 0 ? (
              <button
                type="button"
                className="chip chip-warn"
                onClick={() =>
                  setSelected(photos.filter(isUnlocated).map((photo) => photo.id))
                }
              >
                全选待定位 ({pendingCount})
              </button>
            ) : null}
            {selected.length > 0 ? (
              <button type="button" className="chip" onClick={() => setSelected([])}>
                清除选择 ({selected.length})
              </button>
            ) : null}
            {importing ? (
              <span className="chip" style={{ borderColor: 'rgba(34,211,238,0.4)' }}>
                导入中 {importing.done}/{importing.total}
              </span>
            ) : null}
          </div>
        </div>

        <div className="photo-grid">
          {sorted.length === 0 ? (
            <div className="setup-hint" style={{ gridColumn: '1 / -1' }}>
              把照片拖到首页，或者点「添加照片」。
              <br />
              <br />
              会自动读取 EXIF 里的 <b>GPS</b> 与<b>拍摄时间</b>。GPS 被平台抹掉的情况会标成
              「待定位」，选中后在地图上点一下即可 —— 我们**不会**给没有坐标的照片编一个位置。
            </div>
          ) : (
            sorted.map((photo) => {
              const pending = isUnlocated(photo);
              const isSelected = selectedSet.has(photo.id);
              const name = placeNameFromFileName(photo.name);
              return (
                <button
                  type="button"
                  key={photo.id}
                  className={`photo-cell${isSelected ? ' is-selected' : ''}${pending ? ' is-pending' : ''}`}
                  style={{ padding: 0, background: 'none' }}
                  onClick={(event) =>
                    toggle(photo.id, event.metaKey || event.ctrlKey || event.shiftKey)
                  }
                  title={pending ? '点击选中，再在地图上点一下补地点' : name}
                >
                  <span
                    style={{
                      display: 'block',
                      width: '100%',
                      height: '100%',
                      backgroundImage: `url(${photo.thumbnailDataUrl})`,
                      backgroundSize: 'cover',
                      backgroundPosition: 'center',
                    }}
                  />
                  <span className={`photo-badge${pending ? '' : ' ok'}`}>
                    {pending ? '待定位' : 'EXIF'}
                  </span>
                  <span className="photo-cell-meta">{showTakenAt(photo.takenAt)}</span>
                </button>
              );
            })
          )}
        </div>

        {/* 每段交通工具（W14-3a）：只列「相邻且都有坐标」的照片对。
            写回 `transports[fromId]`，播放时内核按 `TRANSPORT_STYLE` 换线型与颜色。 */}
        {segments.length > 0 ? (
          <div className="transport-list">
            <div className="transport-list-head">
              每段交通工具 · {segments.length} 段
            </div>
            <div className="transport-rows">
              {segments.map((segment) => (
                <label className="transport-row" key={segment.key}>
                  <span className="transport-leg">第 {segment.index + 1} 段</span>
                  <span className="transport-ends">
                    {segment.fromName} → {segment.toName}
                  </span>
                  <select
                    className="transport-select"
                    value={segment.mode}
                    aria-label={`${segment.fromName} 到 ${segment.toName} 的交通工具`}
                    onChange={(event) =>
                      props.onTransport(segment.fromId, event.target.value as TransportMode)
                    }
                  >
                    {TRANSPORT_MODES.map((mode) => (
                      <option key={mode} value={mode}>
                        {TRANSPORT_STYLE[mode].label}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
          </div>
        ) : null}

        <div className="setup-foot">
          <div className="setup-hint">
            选中照片 → 在地图上点击即可写入坐标
            {selected.length > 1 ? `（当前 ${selected.length} 张，点一次批量定位）` : ''}。
            <br />
            <b>Ctrl / Shift</b> 多选，<b>拖拽</b>平移，<b>滚轮</b>缩放。
            <br />
            坐标只存在内存里，关掉页面即清空。
          </div>
          <button
            type="button"
            className="btn btn-primary"
            disabled={locatedCount < 2}
            onClick={props.onStart}
            style={{ width: '100%' }}
          >
            开始旅程（{locatedCount} 张已定位）
          </button>
          {locatedCount < 2 ? (
            <div className="setup-hint" style={{ textAlign: 'center' }}>
              至少需要 2 张已定位的照片才能生成轨迹
            </div>
          ) : null}
        </div>
      </div>

      <div className="setup-map">
        <div className="topbar-note">
          底图是本地省界轮廓（可拖拽 / 滚轮缩放 / 点击取点）·
          数据在应用内，全程不联网
        </div>
        <div className="map-canvas-host" ref={hostRef} />
        <div className="map-hint panel">
          {selected.length > 0 ? (
            <span>
              点击地图为 <b>{selected.length}</b> 张照片写入地点
            </span>
          ) : (
            <span>先选中一张待定位的照片</span>
          )}
        </div>
      </div>
    </div>
  );
}
