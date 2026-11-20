/** 照片位置补全页（W12-2a，Track Studio 侧）。
 *
 * ## 交互
 *
 * ```
 * ┌──────────────────────────────────────────────────────────┐
 * │ 补全照片位置 · 还剩 N 张          [全部跳过]              │
 * ├───────────────┬──────────────────────────────────────────┤
 * │ 缩略图 + 文件名 │                                          │
 * │ （点一张选中）  │            点地图选点（高德 / 降级格网）    │
 * │  …（30%）      │                     …（70%）              │
 * └───────────────┴──────────────────────────────────────────┘
 * ```
 *
 * 点一张照片 → 它成为「待选点」；在地图上点一下 → 写回这张照片（**内存态**，
 * 关掉页面即丢，与本应用"零后端、不落盘"的承诺一致）→ 自动选中下一张；
 * 全部补完自动回编辑页。
 *
 * ## 坐标系（这里最容易出错）
 *
 * 高德返回 **GCJ-02**，而本应用内部（以及 `SystemPhoto` 存储口径）一律 **WGS84**。
 * 所以：
 *  - `controller.coordinateSystem === 'gcj02'` 时，点击结果先 `gcj02ToWgs84` 再写回；
 *  - 降级格网是自己画的，本来就是 WGS84，直接写回。
 *
 * 不做这一步的后果不是报错，而是**每个补标点都偏 300~700 米**（城区量级），
 * 而轨迹看起来"像那么回事"，没人会发现。
 *
 * ## 视觉：小圆点用点击位置的百分比定位
 *
 * 不要求 marker 跟随地图平移（那样每个 zoomchange 都要重投影），
 * 所以圆点就是容器里一个绝对定位的 `<span>`，位置取点击处相对容器的百分比。
 * 为此在容器上挂了 **capture 阶段**的 click 监听：高德内部若 `stopPropagation`，
 * 冒泡监听会收不到，capture 一定能收到。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { SystemPhoto } from '@travelmemory/photo-source';
import { gcj02ToWgs84 } from '@travelmemory/geo';

import { createBaseMap, type CoordinateSystem, type MapController } from '../map/baseMap';
import { placeNameFromFileName } from '../../core/photoImport';
import {
  initialViewOf,
  isValidLatLng,
  nextPendingId,
  toLocationFixItems,
} from '../../core/validate';

export interface LocationEditorProps {
  /** 全部照片（含已定位的：它们提供初始视野参考）。 */
  photos: SystemPhoto[];
  /** 写回一张照片的坐标（WGS84）。父组件用 `applyLocation` 更新状态。 */
  onApply: (photoId: string, lat: number, lng: number) => void;
  /** 返回编辑页。「全部跳过」与「全部补完」走同一条出口。 */
  onExit: () => void;
}

/** 小圆点的位置（容器百分比，0~1）。 */
interface DotPosition {
  x: number;
  y: number;
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  if (value <= 0) return 0;
  if (value >= 1) return 1;
  return value;
}

export function LocationEditor({ photos, onApply, onExit }: LocationEditorProps): JSX.Element {
  /** 待补列表：只有缺坐标的照片。 */
  const items = useMemo(() => toLocationFixItems(photos), [photos]);

  const mapHostRef = useRef<HTMLDivElement>(null);
  /** 挂载那一刻的初始视野（`photos` 之后每次写回都会变，不能每帧重算）。 */
  const initialViewRef = useRef(initialViewOf(photos));

  const [selectedId, setSelectedId] = useState<string | null>(null);
  /** 最近一次点选的坐标（WGS84），只用于底部读数。 */
  const [picked, setPicked] = useState<{ lat: number; lng: number } | null>(null);
  const [dot, setDot] = useState<DotPosition | null>(null);
  const [coordinateSystem, setCoordinateSystem] = useState<CoordinateSystem>('wgs84');
  const [mapError, setMapError] = useState<string | null>(null);

  // 地图的点击回调在挂载时注册一次，读不到最新的 state，所以用 ref 透传。
  const selectedRef = useRef<string | null>(null);
  const pickRef = useRef<(lat: number, lng: number) => void>(() => undefined);
  /** 防止「已返回编辑页」被重复触发（items 变空是稳态，不是一次性事件）。 */
  const exitedRef = useRef(false);

  const select = useCallback((nextId: string | null) => {
    selectedRef.current = nextId;
    setSelectedId(nextId);
  }, []);

  /** 点地图：写回当前选中照片。 */
  const handlePick = useCallback(
    (lat: number, lng: number) => {
      if (!isValidLatLng(lat, lng)) return;
      const target = selectedRef.current;
      setPicked({ lat, lng });
      if (target === null) return; // 没选照片时点地图只更新读数，不写任何照片
      onApply(target, lat, lng);
    },
    [onApply],
  );

  useEffect(() => {
    pickRef.current = handlePick;
  }, [handlePick]);

  /** 自动前进：写回后挑下一张仍缺坐标的照片。 */
  useEffect(() => {
    const next = nextPendingId(photos, selectedRef.current);
    if (next === selectedRef.current) return;
    select(next);
  }, [photos, select]);

  /** 全部补完 → 自动回编辑页。 */
  useEffect(() => {
    if (items.length > 0) {
      exitedRef.current = false;
      return;
    }
    if (exitedRef.current) return;
    exitedRef.current = true;
    onExit();
  }, [items.length, onExit]);

  /** 建地图（只建一次）。 */
  useEffect(() => {
    const host = mapHostRef.current;
    if (!host) return;

    let disposed = false;
    let controller: MapController | null = null;
    const view = initialViewRef.current;

    // 这里刻意用 `createBaseMap` 而不是 `initMap`：补全页需要**能拖能点**的底图，
    // 且降级时要自己画格网（`paintFallback: true`），否则没有 Key 的用户看不到任何
    // 可参照的东西，也没法点。播放页那条路径的 `paintFallback: false` 是因为
    // 格网由渲染器统一画，两层都画会摩尔纹。
    void createBaseMap(host, {
      center: view.center,
      zoom: view.zoom,
      interactive: true,
      paintFallback: true,
    }).then((ctrl) => {
      if (disposed) {
        ctrl.destroy();
        return;
      }
      controller = ctrl;
      setCoordinateSystem(ctrl.coordinateSystem);
      ctrl.onClick((lng, lat) => {
        // 高德给 GCJ-02，存储要 WGS84；降级底图本来就是 WGS84，直接写
        const wgs = ctrl.coordinateSystem === 'gcj02' ? gcj02ToWgs84(lng, lat) : { lng, lat };
        pickRef.current(wgs.lat, wgs.lng);
      });
    }).catch(() => {
      // `createBaseMap` 的契约是"永不 reject"，这里只是兜住将来改动引入的意外抛错：
      // 页面其余部分（列表、「全部跳过」）必须继续可用，不能因为地图挂了就整页白屏。
      if (!disposed) setMapError('底图初始化异常，地图可能无法取点。');
    });

    /** 记录小圆点的百分比位置。capture 阶段监听，不怕地图内部 stopPropagation。 */
    const recordDot = (event: MouseEvent): void => {
      const rect = host.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      setDot({
        x: clamp01((event.clientX - rect.left) / rect.width),
        y: clamp01((event.clientY - rect.top) / rect.height),
      });
    };
    host.addEventListener('click', recordDot, true);

    return () => {
      disposed = true;
      host.removeEventListener('click', recordDot, true);
      controller?.destroy();
      controller = null;
    };
    // 只挂一次：初始视野、布局容器和控制器实例都属于"挂载期"资源。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const activeItem = items.find((item) => item.photoId === selectedId) ?? null;
  const locatedCount = photos.length - items.length;

  return (
    <div className="location-editor">
      <header className="location-editor-head">
        <div>
          <h1>补全照片位置</h1>
          <p className="lead">
            还剩 <strong>{items.length}</strong> 张照片没有位置（已有 {locatedCount} 张带位置）。
            先在左边点一张照片，再在地图上点它所在的位置。
          </p>
          <p className="hint">
            补的坐标只存在内存里，关掉页面即清空，不会上传到任何服务器。
          </p>
        </div>
        <button type="button" className="btn btn-ghost" onClick={onExit}>
          全部跳过
        </button>
      </header>

      <div className="location-editor-body">
        <ol className="location-editor-list" aria-label="缺位置的照片">
          {items.map((item) => {
            const isActive = item.photoId === selectedId;
            const hasPicked = typeof item.lat === 'number' && typeof item.lng === 'number';
            return (
              <li key={item.photoId}>
                <button
                  type="button"
                  className={isActive ? 'location-editor-item is-active' : 'location-editor-item'}
                  onClick={() => select(item.photoId)}
                  aria-pressed={isActive}
                >
                  {item.thumbnailDataUrl ? (
                    <img
                      className="location-editor-thumb"
                      src={item.thumbnailDataUrl}
                      alt=""
                      width={48}
                      height={48}
                    />
                  ) : (
                    <span className="location-editor-thumb" aria-hidden="true" />
                  )}
                  <span className="location-editor-meta">
                    <span className="location-editor-name">{placeNameFromFileName(item.name)}</span>
                    <span className="location-editor-coords">
                      {hasPicked
                        ? `${(item.lat as number).toFixed(5)}, ${(item.lng as number).toFixed(5)}`
                        : '待选点'}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ol>

        <div className="location-editor-map-wrap">
          <div className="location-editor-map" ref={mapHostRef} data-testid="location-editor-map" />
          {dot ? (
            <span
              className="location-editor-dot"
              style={{ left: `${dot.x * 100}%`, top: `${dot.y * 100}%` }}
              aria-hidden="true"
            />
          ) : null}
          <p className="location-editor-readout">
            {picked
              ? `已选：${picked.lat.toFixed(5)}, ${picked.lng.toFixed(5)}（WGS84）`
              : activeItem
                ? `正在为「${placeNameFromFileName(activeItem.name)}」选点`
                : '点地图选择位置'}
          </p>
          {coordinateSystem === 'gcj02' ? (
            <p className="location-editor-note">高德底图 · 已把 GCJ-02 转回 WGS84 存储</p>
          ) : null}
          {mapError ? <p className="location-editor-note location-editor-note-error">{mapError}</p> : null}
        </div>
      </div>
    </div>
  );
}
