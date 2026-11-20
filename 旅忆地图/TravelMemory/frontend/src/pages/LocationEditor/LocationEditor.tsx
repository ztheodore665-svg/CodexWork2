/** 照片位置补全（W12-2b，主应用侧）。
 *
 * ## 它解决什么问题
 *
 * 照片库里总有一批**没有 GPS** 的照片（截图、微信保存的图、被洗过 EXIF 的图）。
 * 这些照片在轨迹上根本不存在 —— 我们绝不伪造坐标。但用户往往**记得**那张照片
 * 是在哪拍的，缺的只是一个"点一下地图"的入口。
 * 行程卡片上的「补标」一次只能补一张，几十张就是一小时，所以这里做批量版。
 *
 * ## 三件事的边界（写这个组件时最容易越界的三处）
 *
 * 1. **不改 `MapCanvas`**。它是主地图的渲染器，吃一份 `RenderPlan`、要管 fitBounds
 *    与图层开关。批量补全要的是"一张干净地图 + 点哪给哪"，硬塞进去得给它造一份
 *    假 plan、再想办法从覆盖物里把点击捞出来。所以这里用 `loadAmap` 自建一个
 *    `AMap.Map`，跟 `MiniMap` 是同一个思路。
 * 2. **不自己读 `import.meta.env`**。Key / 安全密钥由 `MapPage` 以 props 传入 ——
 *    env 的唯一读取点收敛在主应用那一处，审计时只要看那里。
 * 3. **不造 API**。`patchPhotoLocation(photoId, lat, lng)` 已经存在（
 *    `services/api/photos.ts`），后端负责范围校验与 `location_source=user`。
 *
 * ## 坐标系（错一步就差 300~700 米）
 *
 * 主应用的存储口径是 **WGS84**（`PhotoListItem.coordSystem`），高德点击给的是
 * **GCJ-02**。所以写成库之前必须 `gcj02ToWgs84`。反过来，Marker 要显示在高德底图上，
 * 所以用**点击时的原始 GCJ-02** 放 Marker（不做二次转换，避免来回换算的舍入误差）。
 *
 * ## 失败处理
 *
 * 补标是用户明确点击的同步动作：失败必须 toast（destructive）**并且保留该项**，
 * 让用户重试点一次。静默失败会让人以为补上了，而地图上什么都没有。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ImageOff, Loader2, MapPin, X } from 'lucide-react';

import { gcj02ToWgs84 } from '@travelmemory/geo';

import { Button } from '../../components/ui/button';
import { toast } from '../../components/ui/use-toast';
import { DEFAULT_MAP_CENTER, DEFAULT_MAP_ZOOM } from '../../lib/mapView';
import { patchPhotoLocation, type PhotoListItem } from '../../services/api/photos';
import {
  isValidLatLng,
  removePhotoById,
  selectPendingPhotos,
} from '../../services/location-fix/validate';
import { loadAmap, type AmapMap, type AmapNamespace, type AmapOverlay } from '../MapPage/amap';
import { getOrCreateThumbUrl } from '../MapPage/PhotoThumb';
import './LocationEditor.css';

export interface LocationEditorProps {
  /** 是否打开。关闭时组件不渲染任何东西，也**不会**去加载高德脚本。 */
  open: boolean;
  /** 照片库（只有 `hasLocation === false` 的会进待补列表）。 */
  photos: readonly PhotoListItem[];
  /** 高德 JS API key（由 `MapPage` 从环境变量读入后传入）。 */
  jsKey: string;
  /** 高德安全密钥（可选）。 */
  securityCode?: string;
  /** 补标成功后通知上层刷新照片库与地图。 */
  onTripsChanged?: () => void | Promise<void>;
  /** 关闭面板。 */
  onClose: () => void;
}

/** 高德 click 事件里我们真正读到的字段。 */
interface ClickEventLike {
  lnglat?: { lng: number; lat: number };
}

/** 打开时的缩放：没有位置线索，先给个"能看见一个省"的视野，让用户自己拖。 */
const OPEN_ZOOM = DEFAULT_MAP_ZOOM + 1;

export function LocationEditor({
  open,
  photos,
  jsKey,
  securityCode = '',
  onTripsChanged,
  onClose,
}: LocationEditorProps): JSX.Element | null {
  /** 待补列表（本地副本：补标成功后立刻从这里摘掉，不等下一次刷新）。 */
  const [pending, setPending] = useState<PhotoListItem[]>([]);
  /** 当前选中的照片 id。 */
  const [activeId, setActiveId] = useState<string | null>(null);
  /** 已成功补标的 id：防止父组件的 `photos` 还没刷新时把该项又塞回来。 */
  const fixedRef = useRef<Set<string>>(new Set());
  /** 正在保存的照片 id（列表里显示 spinner）。 */
  const [savingId, setSavingId] = useState<string | null>(null);
  /** photoId → objectURL 缩略图。 */
  const [thumbs, setThumbs] = useState<Record<string, string>>({});

  const [amap, setAmap] = useState<AmapNamespace | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  /** 最近一次点选的 WGS84 坐标（只用于读数展示）。 */
  const [picked, setPicked] = useState<{ lat: number; lng: number } | null>(null);

  const hostRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<AmapMap | null>(null);
  const markerRef = useRef<AmapOverlay | null>(null);

  // 地图的 click 回调在挂载时注册一次，读不到最新的 state，用 ref 透传。
  const activeIdRef = useRef<string | null>(null);
  const saveRef = useRef<(photoId: string, lat: number, lng: number) => void>(() => undefined);

  const missingCount = useMemo(() => selectPendingPhotos(photos).length, [photos]);

  /* ── 打开时初始化待补列表 ─────────────────────────────────── */
  useEffect(() => {
    if (!open) {
      fixedRef.current = new Set();
      setPending([]);
      setActiveId(null);
      setPicked(null);
      setSavingId(null);
      return;
    }
    setPending(selectPendingPhotos(photos).filter((photo) => !fixedRef.current.has(photo.id)));
  }, [open, photos]);

  /* ── 自动选中：没有选中项或选中的那张已被补完时，选第一张 ──── */
  useEffect(() => {
    if (!open) return;
    if (pending.length === 0) {
      setActiveId(null);
      return;
    }
    setActiveId((previous) =>
      previous !== null && pending.some((photo) => photo.id === previous)
        ? previous
        : pending[0].id,
    );
  }, [open, pending]);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  /* ── 缩略图：走共享缓存，取不到就显示占位 ───────────────────── */
  useEffect(() => {
    if (!open || pending.length === 0) return;
    let cancelled = false;
    for (const photo of pending) {
      void getOrCreateThumbUrl(photo.id).then((url) => {
        if (cancelled || !url) return;
        setThumbs((previous) => (previous[photo.id] === url ? previous : { ...previous, [photo.id]: url }));
      });
    }
    return () => {
      cancelled = true;
    };
  }, [open, pending]);

  /* ── 保存 ─────────────────────────────────────────────────── */
  const saveLocation = useCallback(
    (photoId: string, lat: number, lng: number) => {
      if (!isValidLatLng(lat, lng)) {
        toast({
          variant: 'destructive',
          title: '坐标不合法',
          description: '纬度需在 -90 ~ 90、经度需在 -180 ~ 180。请重新在地图上点一次。',
        });
        return;
      }
      setSavingId(photoId);
      void (async () => {
        try {
          await patchPhotoLocation(photoId, lat, lng);
          fixedRef.current.add(photoId);
          setPending((previous) => removePhotoById(previous, photoId));
          setActiveId((previous) => (previous === photoId ? null : previous));
          toast({
            variant: 'success',
            title: '已补标坐标',
            description: `${lat.toFixed(5)}, ${lng.toFixed(5)}（WGS84）`,
          });
          await onTripsChanged?.();
        } catch (error) {
          // 失败时**不**从待补列表摘除：用户改一下就能重试
          toast({
            variant: 'destructive',
            title: '补标失败',
            description: error instanceof Error ? error.message : String(error),
          });
        } finally {
          setSavingId(null);
        }
      })();
    },
    [onTripsChanged],
  );

  useEffect(() => {
    saveRef.current = saveLocation;
  }, [saveLocation]);

  /* ── 加载高德（只在打开时） ────────────────────────────────── */
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setMapError(null);
    loadAmap(jsKey, securityCode).then(
      (namespace) => {
        if (!cancelled) setAmap(namespace);
      },
      (error: unknown) => {
        if (cancelled) return;
        // 加载失败不是致命错误：面板其余部分（列表、重试、关闭）必须继续可用
        setMapError(error instanceof Error ? error.message : String(error));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [open, jsKey, securityCode]);

  /* ── 建地图 + 绑点击（打开且脚本就绪时一次） ────────────────── */
  useEffect(() => {
    if (!open || !amap || !hostRef.current || mapRef.current) return;

    const host = hostRef.current;
    const map = new amap.Map(host, {
      viewMode: '2D',
      zoom: OPEN_ZOOM,
      center: [DEFAULT_MAP_CENTER[0], DEFAULT_MAP_CENTER[1]],
    });
    mapRef.current = map;

    const handler = (event: unknown): void => {
      const lnglat = (event as ClickEventLike).lnglat;
      if (!lnglat) return;
      if (typeof lnglat.lng !== 'number' || typeof lnglat.lat !== 'number') return;

      // 高德给 GCJ-02 → 存储要 WGS84（这一步错了点就偏 300~700 米）
      const wgs = gcj02ToWgs84(lnglat.lng, lnglat.lat);
      setPicked({ lat: wgs.lat, lng: wgs.lng });

      // Marker 用**原始 GCJ-02**：它要显示在高德底图上，再转一次只会引入舍入误差
      try {
        markerRef.current?.setMap(null);
        markerRef.current = new amap.Marker({ position: [lnglat.lng, lnglat.lat] });
        markerRef.current.setMap(map);
      } catch {
        // Marker 构造失败不影响补标（读数文本仍然准确）—— 只是少一个标记
        markerRef.current = null;
      }

      const target = activeIdRef.current;
      if (target === null) {
        toast({ variant: 'default', title: '先在左侧选一张照片', description: '选好后再点地图。' });
        return;
      }
      saveRef.current(target, wgs.lat, wgs.lng);
    };

    map.on('click', handler);
    return () => {
      map.off('click', handler);
      markerRef.current?.setMap(null);
      markerRef.current = null;
      map.destroy();
      mapRef.current = null;
    };
  }, [open, amap]);

  /* ── 关闭时清掉地图状态 ───────────────────────────────────── */
  useEffect(() => {
    if (open) return;
    setAmap(null);
    setMapError(null);
  }, [open]);

  if (!open) return null;

  const activePhoto = pending.find((photo) => photo.id === activeId) ?? null;

  return (
    <div className="location-editor-backdrop" role="dialog" aria-modal="true" aria-label="补全照片位置">
      <div className="location-editor">
        <header className="location-editor-head">
          <div>
            <h2 className="location-editor-title">
              <MapPin className="h-4 w-4" aria-hidden="true" />
              补全照片位置
            </h2>
            <p className="location-editor-subtitle">
              还剩 <strong>{pending.length}</strong> 张照片没有位置。先点左边的照片，再点地图上它所在的地方。
              {missingCount > pending.length ? `（已补 ${missingCount - pending.length} 张）` : ''}
            </p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="关闭补全照片位置">
            <X className="h-4 w-4" />
            关闭
          </Button>
        </header>

        <div className="location-editor-body">
          <ul className="location-editor-list" aria-label="缺位置的照片">
            {pending.map((photo) => {
              const isActive = photo.id === activeId;
              const isSaving = photo.id === savingId;
              const thumb = thumbs[photo.id];
              return (
                <li key={photo.id}>
                  <button
                    type="button"
                    className={
                      isActive ? 'location-editor-item is-active' : 'location-editor-item'
                    }
                    onClick={() => setActiveId(photo.id)}
                    aria-pressed={isActive}
                  >
                    {thumb ? (
                      <img className="location-editor-thumb" src={thumb} alt="" />
                    ) : (
                      <span className="location-editor-thumb location-editor-thumb-empty">
                        <ImageOff className="h-4 w-4" aria-hidden="true" />
                      </span>
                    )}
                    <span className="location-editor-meta">
                      <span className="location-editor-name">{photo.caption ?? photo.id}</span>
                      <span className="location-editor-hint">
                        {isSaving ? (
                          <>
                            <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                            保存中…
                          </>
                        ) : photo.takenAtLocal ? (
                          photo.takenAtLocal.replace('T', ' ').slice(0, 16)
                        ) : (
                          '无拍摄时间'
                        )}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>

          <div className="location-editor-map-wrap">
            {/* 地图容器常驻：加载失败时它是个空盒子，由下面的提示卡盖住 */}
            <div className="location-editor-map" ref={hostRef} data-testid="location-editor-map" />

            {mapError ? (
              <div className="location-editor-map-error" role="status">
                <MapPin className="h-5 w-5" aria-hidden="true" />
                <p>地图不可用：{mapError}</p>
                <p className="location-editor-map-error-hint">
                  请检查 `VITE_AMAP_JS_KEY` 与域名白名单（见 `docs/deploy-amap-key.md`）。
                  也可以先用行程抽屉里的「补标坐标」逐张手动填。
                </p>
              </div>
            ) : null}

            {!amap && !mapError ? (
              <div className="location-editor-map-loading" role="status">
                <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
                正在加载地图…
              </div>
            ) : null}

            <p className="location-editor-readout">
              {picked
                ? `已选：${picked.lat.toFixed(5)}, ${picked.lng.toFixed(5)}（WGS84）`
                : activePhoto
                  ? `正在为「${activePhoto.caption ?? activePhoto.id}」选点`
                  : '点地图选择位置'}
            </p>
          </div>
        </div>

        {pending.length === 0 ? (
          <div className="location-editor-empty" role="status">
            所有照片都有位置了。
            <Button variant="ghost" size="sm" onClick={onClose}>
              返回地图
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
