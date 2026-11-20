/** 地图相册页 —— 产品化之后的**全屏地图壳**。
 *
 * ## 结构：地图是唯一的底层，其余全部叠在它上面
 *
 * 阶段 6 之前这里是"顶部三个 Tab + 一块 70vh 的地图"，地图被关在一张卡片里。
 * 旅忆地图的产品本质是**先看地图，再谈别的**，所以现在：
 *
 * ```
 * ┌─────────────────────────────────────────────┐
 * │  [旅忆地图 · 隐私模糊]          （左上品牌）  │
 * │                                             │
 * │           全屏高德地图（唯一底层）             │
 * │                                    ┌────┐  │
 * │                                    │行程 │ ← 右缘半圆 Tab
 * │                                    └────┘  │
 * │  [生成信物]                         (AI)   │
 * │  [验证信物]                                │
 * │  （左下工具栏）                    （右下 FAB）│
 * └─────────────────────────────────────────────┘
 * ```
 *
 * ## 三个浮层互斥
 *
 * 行程抽屉、AI 抽屉、信物对话框**同时只开一个**。不是为了"设计简洁"：
 * 可视区域就那么大，两个抽屉各占 400px 就把地图挤成一条缝，
 * 而用户此刻正需要看着地图判断"这个行程落在哪"。
 *
 * ## 为什么 fitBounds 用计数器而不是 ref 命令
 *
 * 见 `MapCanvas.tsx` 的 `fitSignal` 注释：父组件 `await` 完刷新行程时
 * React 还没重渲染，那时调 `fitAll()` 读到的是**上一批**覆盖物 ——
 * 飞向旧位置，且没有任何报错。计数器走同一次 commit，不会错。
 *
 * （`flyTo` 不受这个影响：它只改地图中心的缩放与中心点，不依赖覆盖物列表，
 *  所以点证据定位时直接调句柄是安全的。）
 */

import { useCallback, useMemo, useRef, useState } from 'react';
import {
  BadgeCheck,
  ChevronLeft,
  Clapperboard,
  MapPin,
  Route,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';

import type { PhotoListItem } from '../../services/api/photos';
import type { TripView } from '../../services/api/trips';
import type { StudioMode } from '../../services/studio-bridge/types';
import type { UnpackedPlace } from '../../types/share';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '../../components/ui/sheet';
import { toast } from '../../components/ui/use-toast';
import { findPointForEvidence } from '../../lib/mapView';
import { ChatPanel } from '../AgentPage/ChatPanel';
import { LocationEditor } from '../LocationEditor/LocationEditor';
import { StudioEmbed } from '../StudioEmbed/StudioEmbed';
import { MapCanvas, type MapHandle } from './MapCanvas';
import { ShareDialog, type ShareSection } from './ShareDialog';
import { TripDrawer } from './TripDrawer';
import { ReplayStage } from './replay/ReplayStage';
import {
  DEFAULT_LAYER_VISIBILITY,
  planMapRender,
  sharePointsFromPlaces,
  toggleLayer,
  type LayerVisibility,
} from './layers';
import { tripPointsFromTrips } from './tripPoints';
import { splitTrips } from './tripScope';

export interface MapPageProps {
  /** 信物解包结果里的全部地点（已过验签）。坐标是 WGS84（模糊后）。 */
  sharePlaces?: readonly UnpackedPlace[];
  /** 后端行程列表。每个 item 里带坐标的会进「我的照片」图层。 */
  trips?: readonly TripView[];
  /** 照片库（阶段 7：照片是底层数据，行程只是视图）。 */
  photos?: readonly PhotoListItem[];
  /** 导入/新建/分配/删除成功后通知上层刷新数据。 */
  onTripsChanged?: () => void | Promise<void>;
  /** 信物解出的地点 → 上地图画模糊圈（由 App 持有状态）。 */
  onViewOnMap?: (places: UnpackedPlace[]) => void;
}

/** 高德 JS API key。前端 key 会被打进产物，靠 referer 白名单保护，不当机密。 */
const AMAP_JS_KEY = import.meta.env.VITE_AMAP_JS_KEY ?? '';
const AMAP_SECURITY_CODE = import.meta.env.VITE_AMAP_SECURITY_CODE ?? '';

export function MapPage({
  sharePlaces = [],
  trips = [],
  photos = [],
  onTripsChanged,
  onViewOnMap,
}: MapPageProps) {
  const [visibility, setVisibility] = useState<LayerVisibility>(DEFAULT_LAYER_VISIBILITY);
  const [tripDrawerOpen, setTripDrawerOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [shareSection, setShareSection] = useState<ShareSection | null>(null);
  const [selectedTripId, setSelectedTripId] = useState<string | null>(null);
  const [highlightId, setHighlightId] = useState('');
  const [fitSignal, setFitSignal] = useState(0);
  /** 正在回放的行程。null = 没开回放层。 */
  const [replayTrip, setReplayTrip] = useState<TripView | null>(null);
  /** 正在嵌入的小应用（阶段 11）。null = 没开。`mode` 决定嵌哪一个。 */
  const [studio, setStudio] = useState<{ mode: StudioMode; trip?: TripView } | null>(null);
  /** 批量补全照片位置面板（W12-2b）。 */
  const [locationEditorOpen, setLocationEditorOpen] = useState(false);
  const mapRef = useRef<MapHandle | null>(null);

  // 三个数据源拼成一份点列表：图层开关与渲染计划都只认 `MapPointInput`，
  // 所以"合流"发生在这一层，layers.ts 不需要知道行程是什么。
  //
  // 阶段 17：朋友的行程单独打 `friend` 标签，默认由 `DEFAULT_LAYER_VISIBILITY`
  // 关掉 —— 它不是"更多点"，而是"别人的点"，混进 photo 层会被当成自己去过的地方。
  const points = useMemo(() => {
    const { mine, friends } = splitTrips(trips);
    return [
      ...sharePointsFromPlaces(sharePlaces),
      ...tripPointsFromTrips(mine, 'photo'),
      ...tripPointsFromTrips(friends, 'friend'),
    ];
  }, [sharePlaces, trips]);
  // 可见点数 / 已隐藏点数都来自纯逻辑层，UI 不自己数一遍
  const plan = useMemo(() => planMapRender(points, visibility), [points, visibility]);
  const shareCount = useMemo(() => points.filter((p) => p.source === 'share').length, [points]);
  // 照片总数直接数照片库，不再从行程反推 —— 阶段 7 之后照片可以不属于任何行程
  const photoTotal = photos.length;

  /** 导入/新建完成：先让上层刷新数据，再请求地图飞过去。
   *  顺序不能反 —— 飞行的依据是新数据算出来的覆盖物。 */
  const handleTripsChanged = useCallback(async () => {
    await onTripsChanged?.();
    setFitSignal((n) => n + 1);
  }, [onTripsChanged]);

  /** 待补位置的照片数（W12-2b）。用后端的 `hasLocation`，**不拿 lat 判空**。 */
  const missingLocationCount = useMemo(
    () => photos.filter((photo) => !photo.hasLocation).length,
    [photos],
  );

  /**
   * 打开「补全照片位置」面板。
   *
   * 一张都不缺时不打开，只回一句 toast：打开一个空面板会让人以为功能坏了。
   * 打开时顺手关掉另外两个抽屉（它们与地图抢同一块可视区域）。
   */
  const openLocationEditor = useCallback(() => {
    if (missingLocationCount === 0) {
      toast({
        variant: 'success',
        title: '所有照片都有位置',
        description: '照片库里的照片都带坐标，不需要补全。',
      });
      return;
    }
    setTripDrawerOpen(false);
    setChatOpen(false);
    setShareSection(null);
    setLocationEditorOpen(true);
  }, [missingLocationCount]);

  /** 点 AI 回答里的引用 `[n]` → 飞到对应证据并高亮。 */
  const handleLocate = useCallback(
    (ref: string) => {
      const point = findPointForEvidence(points, ref);
      // 证据里没有坐标通路（见 lib/mapView.ts 的说明），必须说清楚
      // 而不是默默什么都不做 —— 后者会被当成"按钮坏了"。
      if (!point || typeof point.lat !== 'number' || typeof point.lng !== 'number') {
        toast({
          variant: 'destructive',
          title: '无法定位到地图',
          description: '这条引用没有对应的坐标信息，只能查看文字内容。',
        });
        return;
      }
      setSelectedTripId(null);
      setHighlightId(point.id);
      setChatOpen(false);
      // flyTo 只改地图中心与缩放，不依赖覆盖物列表，所以直接调句柄是安全的
      mapRef.current?.flyTo(point.lng, point.lat);
    },
    [points],
  );

  /** 打开信物对话框，并关掉另外两个浮层（三者互斥）。 */
  const openShare = useCallback((section: ShareSection) => {
    setTripDrawerOpen(false);
    setChatOpen(false);
    setShareSection(section);
  }, []);

  return (
    <div className="relative h-[100dvh] w-full overflow-hidden bg-background">
      {/* 唯一底层：全屏地图。
          回放打开时**卸载**它，而不是隐藏：高德实例只要还活着就会持续更新图层、
          占主线程，`visibility:hidden` 只是不显示、并不会停掉这些工作（实测隐藏更慢）。
          代价：关闭回放后主地图会重新初始化，视野回到默认中心。 */}
      <div className="absolute inset-0">
        {replayTrip === null ? (
          <MapCanvas
            ref={mapRef}
            points={points}
            jsKey={AMAP_JS_KEY}
            securityCode={AMAP_SECURITY_CODE}
            highlightId={highlightId}
            fitSignal={fitSignal}
          />
        ) : null}
      </div>

      {/* 左上角品牌条。pointer-events-none 让这块不挡住地图拖动，
          Badge 自己再打开事件（它不需要响应拖拽）。 */}
      <div className="pointer-events-none absolute left-4 top-4 z-10 flex items-center gap-2 rounded-xl border bg-background/85 px-3 py-2 shadow-sm backdrop-blur">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Route className="h-4 w-4" />
        </span>
        <div className="leading-tight">
          <div className="text-sm font-semibold tracking-tight">旅忆地图</div>
          <div className="text-[11px] text-muted-foreground">本地解签 · 坐标不出设备</div>
        </div>
        <Badge variant="secure" className="pointer-events-auto">
          <ShieldCheck className="h-3 w-3" />
          隐私模糊
        </Badge>
      </div>

      {/* 左下角工具栏：信物分享的两个入口 */}
      <div className="absolute bottom-6 left-4 z-10 flex flex-col gap-2">
        <Button
          variant="outline"
          size="sm"
          className="justify-start border-primary/30 bg-background/90 shadow-md backdrop-blur hover:bg-background"
          onClick={() => openShare('generate')}
        >
          <BadgeCheck className="h-4 w-4 text-primary" />
          生成信物
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="justify-start bg-background/90 shadow-md backdrop-blur hover:bg-background"
          onClick={() => openShare('verify')}
        >
          <ShieldCheck className="h-4 w-4" />
          验证信物
        </Button>
        {/* 阶段 11：把独立小应用（Track Studio）以全屏 iframe 嵌进来。
            与上面两个按钮不同，这一个不碰浮层互斥 —— 它自己就是全屏模态。 */}
        <Button
          variant="outline"
          size="sm"
          className="justify-start bg-background/90 shadow-md backdrop-blur hover:bg-background"
          onClick={() => setStudio({ mode: 'track' })}
        >
          <Clapperboard className="h-4 w-4" />
          轨迹演示
        </Button>
        {/* W12-2b：批量补全照片位置。缺坐标照片多时，逐张点「补标」太慢；
            这里一次点地图就把一张补上，补完自动从列表里消失。 */}
        <Button
          variant="outline"
          size="sm"
          className="justify-start bg-background/90 shadow-md backdrop-blur hover:bg-background"
          onClick={openLocationEditor}
          title={
            missingLocationCount > 0
              ? `还有 ${missingLocationCount} 张照片没有位置`
              : '所有照片都有位置'
          }
        >
          <MapPin className="h-4 w-4" />
          补全照片位置
          {missingLocationCount > 0 && (
            <Badge variant="muted" className="ml-1">
              {missingLocationCount}
            </Badge>
          )}
        </Button>
        <p className="max-w-[200px] pl-1 text-[11px] leading-relaxed text-muted-foreground">
          模糊圈半径是<strong>承诺半径</strong>，不是本次实际偏移量 —— 真实位置只在圈内某处。
        </p>
      </div>

      {/* 右缘半圆形 Tab：抽屉触发器。文字竖排，收起时只占 ~28px。 */}
      <button
        type="button"
        onClick={() => {
          setChatOpen(false);
          setTripDrawerOpen(true);
        }}
        aria-label="打开行程与相册抽屉"
        className="absolute right-0 top-1/2 z-10 flex -translate-y-1/2 items-center gap-1 rounded-l-full border border-r-0 bg-background/90 py-5 pl-2 pr-1.5 shadow-lg backdrop-blur transition-all duration-200 hover:bg-background hover:pr-2.5"
      >
        <ChevronLeft className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="text-xs font-medium [writing-mode:vertical-rl]">行程与相册</span>
      </button>

      {/* 右下角 AI FAB */}
      <button
        type="button"
        onClick={() => {
          setTripDrawerOpen(false);
          setChatOpen(true);
        }}
        aria-label="打开旅忆助手"
        className="absolute bottom-6 right-4 z-10 flex h-14 w-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-xl transition-all duration-200 hover:scale-105 hover:shadow-2xl active:scale-95"
      >
        <Sparkles className="h-6 w-6" />
      </button>

      {/* 行程与相册抽屉 */}
      <Sheet open={tripDrawerOpen} onOpenChange={setTripDrawerOpen}>
        <SheetContent side="right" className="flex w-[92vw] flex-col gap-0 p-0 sm:max-w-[400px]">
          <SheetHeader className="sr-only">
            <SheetTitle>行程与相册</SheetTitle>
            <SheetDescription>行程列表、照片导入与图层开关</SheetDescription>
          </SheetHeader>
          <TripDrawer
            trips={trips}
            photos={photos}
            visibility={visibility}
            onToggleLayer={(layer) => setVisibility((v) => toggleLayer(v, layer))}
            shareCount={shareCount}
            onTripsChanged={handleTripsChanged}
            selectedTripId={selectedTripId}
            onSelectTrip={setSelectedTripId}
            jsKey={AMAP_JS_KEY}
            securityCode={AMAP_SECURITY_CODE}
            onReplayTrip={setReplayTrip}
            onShareTrip={(trip) => setStudio({ mode: 'share', trip })}
          />
        </SheetContent>
      </Sheet>

      {/* AI 助手抽屉。与行程抽屉互斥：见文件头说明。 */}
      <Sheet open={chatOpen} onOpenChange={setChatOpen}>
        <SheetContent side="right" className="flex w-[92vw] flex-col gap-0 p-0 sm:max-w-[440px]">
          <SheetHeader className="sr-only">
            <SheetTitle>旅忆助手</SheetTitle>
            <SheetDescription>关于你旅行记忆的问答，回答带引用</SheetDescription>
          </SheetHeader>
          <ChatPanel onLocate={handleLocate} />
        </SheetContent>
      </Sheet>

      {/* 信物分享对话框 */}
      <ShareDialog
        open={shareSection !== null}
        onOpenChange={(open) => {
          if (!open) setShareSection(null);
        }}
        trips={trips}
        section={shareSection ?? undefined}
        onViewOnMap={(places) => {
          setShareSection(null);
          onViewOnMap?.(places);
        }}
      />

      {/* 轨迹回放层：全屏模态，盖住整页。开关在 replayFlag（VITE_TRAIL_REPLAY）。 */}
      <ReplayStage
        trip={replayTrip}
        photos={photos}
        open={replayTrip !== null}
        onOpenChange={(next) => {
          if (!next) setReplayTrip(null);
        }}
      />

      {/* 底部常驻计数：让用户随时知道地图上有多少点。
          放在底部中间而不是角落，是为了不和左右两个浮层抢位置。 */}
      <div className="pointer-events-none absolute bottom-6 left-1/2 z-0 -translate-x-1/2">
        <div className="flex items-center gap-2 rounded-full border bg-background/85 px-3 py-1.5 text-xs text-muted-foreground shadow-sm backdrop-blur">
          <Route className="h-3 w-3" />
          <span>
            可见 <span className="font-semibold text-foreground">{plan.items.length}</span> 个点
          </span>
          <span className="text-border">|</span>
          <span>照片 {photoTotal}</span>
          {plan.hidden > 0 && <Badge variant="muted">已隐藏 {plan.hidden}</Badge>}
        </div>
      </div>

      {/* W12-2b：批量补全照片位置。Key / 安全密钥**从这一层传下去**（组件自己不读
          import.meta.env）；补标成功后走 handleTripsChanged 刷新照片库与地图。 */}
      <LocationEditor
        open={locationEditorOpen}
        photos={photos}
        jsKey={AMAP_JS_KEY}
        securityCode={AMAP_SECURITY_CODE}
        onTripsChanged={handleTripsChanged}
        onClose={() => setLocationEditorOpen(false)}
      />

      {/* 阶段 11：独立小应用的全屏嵌入层。打开时挂载 iframe，关闭时卸载（含子文档）。
          `mode` 决定嵌 track-studio（5174）还是 trailshare-standalone（5175）；
          `trip` 只有 share 模式才有值。 */}
      <StudioEmbed
        open={studio !== null}
        mode={studio?.mode ?? 'track'}
        trip={studio?.trip}
        photos={photos}
        onTripsChanged={handleTripsChanged}
        onClose={() => setStudio(null)}
      />
    </div>
  );
}
