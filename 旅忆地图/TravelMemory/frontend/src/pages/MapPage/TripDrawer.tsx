/** 「行程与相册」抽屉的内容 —— 两个 Tab：行程列表 / 照片库。
 *
 * ## 为什么拆成两个 Tab 而不是一个长列表
 *
 * 阶段 7 把模型翻过来了：**照片是底层，行程是视图**。于是这个抽屉要同时回答
 * 两个不同的问题 ——「我有哪几趟旅程」和「我库里有哪些照片」。
 * 混在一个列表里，用户想在照片库里勾选加入行程时，得在几十张照片里找出
 * 哪张已经在某趟里；而想删一趟旅程时，又会被照片列表的长度带着滚半天。
 *
 * ## 两个 Tab 的协作
 *
 * ```
 *  行程 Tab                     照片库 Tab
 *  ┌──────────────┐            ┌──────────────┐
 *  │ 新建旅程      │            │ 导入照片      │
 *  │ 洛阳两日  ✎ 🗑 │◄──────────│ ☑ 勾选照片    │
 *  │  (选中高亮)   │  加入当前行程 │ ☐ 加入这趟   │
 *  └──────────────┘            └──────────────┘
 * ```
 *  行程 Tab 里选中哪趟，照片库的「加入当前行程」就往哪趟加。
 *  选中态由 MapPage 持有（地图要高亮那趟的照片），所以这里只上报。
 *
 * ## 为什么导入不再自动聚类
 *
 * 照片是唯一的物理事实，行程由用户手动组建。自动聚类会让用户没有发言权
 * （导入 3 张被切成 2 趟，其中一趟只有 1 张，而他明知那 3 张是同一次出游），
 * 而且重建会冲掉手工整理的结果。所以导入只写 `Photo` 表。
 */

import { type ChangeEvent, useCallback, useMemo, useRef, useState } from 'react';
import {
  CheckCheck,
  ChevronDown,
  ChevronRight,
  FolderOpen,
  ImageDown,
  Layers,
  Loader2,
  MapPin,
  MapPinOff,
  Pencil,
  Play,
  Plus,
  Route,
  Share2,
  Trash2,
} from 'lucide-react';

import type { PhotoListItem } from '../../services/api/photos';
import { importPhotos } from '../../services/api/photos';import type { TripView } from '../../services/api/trips';
import { assignTripPhotos, deleteTrip } from '../../services/api/trips';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Checkbox } from '../../components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../components/ui/dialog';
import { Label } from '../../components/ui/label';
import { ScrollArea } from '../../components/ui/scroll-area';
import { Separator } from '../../components/ui/separator';
import { toast } from '../../components/ui/use-toast';
import { resolveNativeBridge } from '../../services/native';
import {
  IDLE_IMPORT,
  THUMB_MAX_EDGE,
  assignThumbs,
  filterImageFiles,
  reduceImportState,
  type ImportOutcome,
} from './photoImport';
import { CreateTripDialog } from './CreateTripDialog';
import { PhotoLocationDialog } from './PhotoLocationDialog';
import { PhotoThumb } from './PhotoThumb';
import { RenameTripDialog } from './RenameTripDialog';
import { TRAIL_REPLAY_ENABLED } from './replay/replayFlag';
import type { LayerVisibility } from './layers';
import { groupFriendsBySender, splitTrips, type TripScope } from './tripScope';

export type DrawerTab = 'trips' | 'photos';

export interface TripDrawerProps {
  trips: readonly TripView[];
  photos: readonly PhotoListItem[];
  visibility: LayerVisibility;
  onToggleLayer: (layer: keyof LayerVisibility) => void;
  /** 「分享地点」图层的点数（信物解出来的）。 */
  shareCount: number;
  /** 导入/新建/分配/删除成功后通知上层：刷新数据 + 让地图重画。 */
  onTripsChanged: () => void | Promise<void>;
  /** 当前选中的行程 id（地图上高亮）。null = 没有选中。 */
  selectedTripId: string | null;
  onSelectTrip: (id: string | null) => void;
  /** 高德 JS API key（传给补标对话框的内嵌地图）。 */
  jsKey: string;
  securityCode?: string;
  /**
   * 回放这条行程。**可选**：不传 = 不显示回放入口，
   * 这样现有的抽屉测试与其它调用点都不用改。
   */
  onReplayTrip?: (trip: TripView) => void;
  /**
   * 分享这条行程（阶段 11：嵌入 TrailShare 独立小应用）。
   * **可选**，与 `onReplayTrip` 同款约定：不传 = 不显示按钮。
   */
  onShareTrip?: (trip: TripView) => void;
}

export function TripDrawer({
  trips,
  photos,
  visibility,
  onToggleLayer,
  shareCount,
  onTripsChanged,
  selectedTripId,
  onSelectTrip,
  jsKey,
  securityCode = '',
  onReplayTrip,
  onShareTrip,
}: TripDrawerProps) {
  const [tab, setTab] = useState<DrawerTab>('trips');
  /** W15-5：行程的范围——自己建的 / 朋友发来的。默认「我的」（老行为不变）。 */
  const [scope, setScope] = useState<TripScope>('mine');
  /** 展开中的发件人文件夹（默认全收起：朋友多了不该一屏全是名字）。 */
  const [expandedSenders, setExpandedSenders] = useState<ReadonlySet<string>>(new Set());
  const [tripDialogOpen, setTripDialogOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<TripView | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<TripView | null>(null);
  const [locateTarget, setLocateTarget] = useState<PhotoListItem | null>(null);
  const [outcome, setOutcome] = useState<ImportOutcome>(IDLE_IMPORT);
  /** 照片库里勾选的照片 id（多选加入行程用）。 */
  const [checked, setChecked] = useState<ReadonlySet<string>>(new Set());
  const [assigning, setAssigning] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const uploading = outcome.status === 'uploading';
  const selectedTrip = useMemo(
    () => trips.find((trip) => trip.id === selectedTripId) ?? null,
    [trips, selectedTripId],
  );
  const unlocatedCount = photos.filter((photo) => !photo.hasLocation).length;

  /* ── W15-5：按发件人分类（纯函数，见 `tripScope.ts`） ───────────
   *
   * `mine` 用于「我的」列表，`friendGroups` 用于「朋友的」列表。
   * 两个都 `useMemo`：分组结果决定渲染树，每帧重算会让折叠状态跟着抖。
   */
  const { mine, friends } = useMemo(() => splitTrips(trips), [trips]);
  const friendGroups = useMemo(() => groupFriendsBySender(friends), [friends]);

  const toggleSender = useCallback((sender: string) => {
    setExpandedSenders((prev) => {
      const next = new Set(prev);
      if (next.has(sender)) next.delete(sender);
      else next.add(sender);
      return next;
    });
  }, []);

  /**
   * 一行行程。
   *
   * `friend = true` 时（朋友的行程）**砍掉"分享 / 重命名 / 删除"**：
   *  - 分享：把别人发给你的东西再发给第三个人，不是这个功能的语义；
   *  - 重命名：标题前缀是发件人的唯一线索，改名会让它从分组里掉出去；
   *  - 删除：这不是你的行程（要删应该由发件人那边处理）。
   * 只保留回放 —— 看得见、能看，就够。
   */
  const renderTripRow = (trip: TripView, friend: boolean) => {
    const unlocated = trip.items.filter((item) => typeof item.lat !== 'number').length;
    const active = trip.id === selectedTripId;
    return (
      <li key={trip.id}>
        <div
          className={`rounded-lg border px-3 py-2 transition-all duration-150 ${
            active
              ? 'border-primary bg-primary/5 shadow-sm ring-1 ring-primary/30'
              : 'hover:bg-accent/60 hover:shadow-sm'
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              className="flex-1 truncate text-left text-sm font-medium"
              onClick={() => onSelectTrip(active ? null : trip.id)}
              aria-pressed={active}
            >
              {trip.title}
            </button>
            <div className="flex shrink-0 items-center gap-1">
              {TRAIL_REPLAY_ENABLED && onReplayTrip ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
                  aria-label={`回放行程 ${trip.title}`}
                  onClick={() => onReplayTrip(trip)}
                >
                  <Play className="h-3.5 w-3.5" />
                </Button>
              ) : null}
              {/* 阶段 11：把这一趟交给 TrailShare 小应用打包成信物。
                  只在调用方传了 `onShareTrip` 时出现（不传 = 旧行为不变）。
                  朋友的行程不给分享入口（见函数注释）。 */}
              {!friend && onShareTrip ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
                  aria-label={`分享行程 ${trip.title}`}
                  onClick={() => onShareTrip(trip)}
                >
                  <Share2 className="h-3.5 w-3.5" />
                </Button>
              ) : null}
              {!friend && (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
                    aria-label={`重命名行程 ${trip.title}`}
                    onClick={() => setRenameTarget(trip)}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                    aria-label={`删除行程 ${trip.title}`}
                    onClick={() => setDeleteTarget(trip)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </>
              )}
              <Badge variant={trip.source === 'manual' ? 'outline' : 'muted'}>
                {trip.source === 'manual' ? '手工' : '聚类'}
              </Badge>
            </div>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>{formatRange(trip.startedAt, trip.endedAt)}</span>
            <span className="inline-flex items-center gap-1">
              <MapPin className="h-3 w-3" />
              {trip.items.length} 个地点
            </span>
            {unlocated > 0 && (
              <span className="inline-flex items-center gap-1 text-amber-700">
                <MapPinOff className="h-3 w-3" />
                {unlocated} 个未定位
              </span>
            )}
          </div>
          {/* 朋友的行程多一行小字：同一发件人的标题往往只差「 · 地点」，
              只靠标题分不清哪趟是哪趟 */}
          {friend && (
            <div className="mt-1 text-[11px] text-muted-foreground">
              {dayOf(trip.startedAt) ?? '日期未知'} · {trip.itemCount} 张照片
            </div>
          )}
        </div>
      </li>
    );
  };

  const pickFiles = useCallback(() => inputRef.current?.click(), []);

  /** 导入：只写 Photo 表，不再触发聚类（阶段 7 的架构调整）。 */
  const runImport = useCallback(
    async (files: File[]) => {
      setOutcome((prev) => reduceImportState(prev, { type: 'start' }));
      try {
        const bridge = resolveNativeBridge();

        // 缩略图与指纹都允许单张失败：一张图解码不了不该让整批导入挂掉，
        // 后端那边本来就有一个 `failed` 计数器负责这类文件。
        const assets = bridge.photos.adoptFiles?.(files) ?? [];
        const thumbs = await Promise.all(
          assets.map((asset) =>
            bridge.photos.readThumb(asset.id, THUMB_MAX_EDGE).then(
              (bytes) => bytes,
              () => null,
            ),
          ),
        );
        const hashes = await Promise.all(files.map(sha256Hex));

        const result = await importPhotos(files);
        // 拿 reduce 的返回值去弹 toast，而不是读 `outcome`：后者这一轮 render 还没生效
        const done = reduceImportState(IDLE_IMPORT, { type: 'success', result });
        setOutcome(done);

        // 缩略图按**后端 photo id** 存，照片库与信物打包都按这个 id 取图
        await Promise.all(
          assignThumbs(result.photos, hashes, thumbs).map((entry) =>
            bridge.thumbs.put(entry.photoId, THUMB_MAX_EDGE, entry.bytes),
          ),
        );

        toast({
          variant: 'success',
          title: '导入完成',
          description: `成功导入 ${result.imported} 张照片到照片库`,
        });
        await onTripsChanged();
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setOutcome((prev) => reduceImportState(prev, { type: 'error', message }));
        toast({ variant: 'destructive', title: '导入失败', description: message });
      }
    },
    [onTripsChanged],
  );

  const handleChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const files = filterImageFiles(Array.from(event.target.files ?? []));
      // 必须清空：否则连着选同一个文件不会触发第二次 change（浏览器经典坑）
      event.target.value = '';

      if (files.length === 0) {
        toast({ variant: 'destructive', title: '导入失败', description: '请选择图片文件' });
        return;
      }
      void runImport(files);
    },
    [runImport],
  );

  const toggleChecked = useCallback((photoId: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(photoId)) next.delete(photoId);
      else next.add(photoId);
      return next;
    });
  }, []);

  /** 把勾选的照片设成当前行程的快照（全量提交，不是增量 add）。 */
  const assignChecked = useCallback(async () => {
    if (!selectedTrip || checked.size === 0) return;
    setAssigning(true);
    try {
      // 全量：原有条目在前，新勾选的按勾选顺序追加（后端按下标存 ord）
      const existing = selectedTrip.items.map((item) => item.photoId);
      const merged = [...existing, ...[...checked].filter((id) => !existing.includes(id))];
      const updated = await assignTripPhotos(selectedTrip.id, merged);
      toast({
        variant: 'success',
        title: '已加入行程',
        description: `「${updated.title}」现在有 ${updated.itemCount} 张照片`,
      });
      setChecked(new Set());
      await onTripsChanged();
    } catch (err) {
      toast({
        variant: 'destructive',
        title: '加入失败',
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setAssigning(false);
    }
  }, [checked, onTripsChanged, selectedTrip]);

  const confirmDelete = useCallback(async () => {
    if (!deleteTarget) return;
    try {
      await deleteTrip(deleteTarget.id);
      toast({
        variant: 'success',
        title: '行程已删除',
        description: `「${deleteTarget.title}」已移除，照片仍保留在照片库里`,
      });
      if (selectedTripId === deleteTarget.id) onSelectTrip(null);
      setDeleteTarget(null);
      await onTripsChanged();
    } catch (err) {
      toast({
        variant: 'destructive',
        title: '删除失败',
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }, [deleteTarget, onSelectTrip, onTripsChanged, selectedTripId]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* 头部 + Tab 切换 */}
      <div className="border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Route className="h-4 w-4" />
          </span>
          <h2 className="text-sm font-semibold tracking-tight">行程与相册</h2>
          <Badge variant="muted">
            {trips.length} 趟 · {photos.length} 张
          </Badge>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-1 border-b px-2 py-2" role="tablist">
        {(
          [
            { key: 'trips' as const, label: '行程列表', icon: Route },
            { key: 'photos' as const, label: '照片库', icon: ImageDown },
          ] satisfies Array<{ key: DrawerTab; label: string; icon: typeof Route }>
        ).map((item) => (
          <button
            key={item.key}
            role="tab"
            aria-selected={tab === item.key}
            onClick={() => setTab(item.key)}
            className={`flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              tab === item.key
                ? 'bg-primary/10 text-primary'
                : 'text-muted-foreground hover:bg-accent hover:text-foreground'
            }`}
          >
            <item.icon className="h-3.5 w-3.5" />
            {item.label}
          </button>
        ))}
      </div>

      {/* ==================== 行程 Tab ==================== */}
      {tab === 'trips' && (
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-3 px-4 py-3">
            <Button size="sm" className="w-full justify-start" onClick={() => setTripDialogOpen(true)}>
              <Plus className="h-4 w-4" />
              新建旅程
            </Button>

            {/* W15-5：范围切换（分段控件）。用 aria-pressed 而不是 role="tab" ——
                这个抽屉已经有一个 tablist（行程/照片库），再套一层 tab 会让
                屏幕阅读器把两组互不相关的切换当成同一组。 */}
            <div
              className="grid grid-cols-2 gap-1 rounded-lg border p-1"
              role="group"
              aria-label="行程范围"
            >
              {(
                [
                  { key: 'mine' as const, label: '我的', count: mine.length },
                  { key: 'friends' as const, label: '朋友的', count: friends.length },
                ] satisfies Array<{ key: TripScope; label: string; count: number }>
              ).map((item) => (
                <button
                  key={item.key}
                  type="button"
                  aria-pressed={scope === item.key}
                  onClick={() => setScope(item.key)}
                  className={`flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                    scope === item.key
                      ? 'bg-primary/10 text-primary'
                      : 'text-muted-foreground hover:bg-accent hover:text-foreground'
                  }`}
                >
                  {item.label}
                  {item.count > 0 && (
                    <span className="text-[11px] text-muted-foreground">{item.count}</span>
                  )}
                </button>
              ))}
            </div>

            <p className="text-xs leading-relaxed text-muted-foreground">
              旅程是<strong>空的视图</strong>：建好后到「照片库」勾选照片加入。删旅程不会删照片。
            </p>

            {selectedTrip && (
              <div className="rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-xs leading-relaxed">
                当前选中「<strong>{selectedTrip.title}</strong>」，共 {selectedTrip.itemCount} 张照片。
                去照片库勾选即可加入。
              </div>
            )}

            <Separator />

            {scope === 'mine' ? (
              mine.length === 0 ? (
                <p className="rounded-lg border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">
                  还没有旅程。点「新建旅程」开始。
                </p>
              ) : (
                <ul className="space-y-2">{mine.map((trip) => renderTripRow(trip, false))}</ul>
              )
            ) : friends.length === 0 ? (
              <p className="rounded-lg border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">
                还没有朋友分享的旅程。让对方在 TrailShare 里点「接受并导回」。
              </p>
            ) : (
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground">
                  已收到 {friendGroups.length} 位朋友分享的 {friends.length} 趟旅程
                </p>
                {friendGroups.map((group) => {
                  const expanded = expandedSenders.has(group.sender);
                  return (
                    <div key={group.sender} className="rounded-lg border">
                      <button
                        type="button"
                        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium transition-colors hover:bg-accent/60"
                        aria-expanded={expanded}
                        onClick={() => toggleSender(group.sender)}
                      >
                        {expanded ? (
                          <ChevronDown className="h-3.5 w-3.5 shrink-0" />
                        ) : (
                          <ChevronRight className="h-3.5 w-3.5 shrink-0" />
                        )}
                        <FolderOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="flex-1 truncate">来自 {group.sender}</span>
                        <span className="shrink-0 text-[11px] text-muted-foreground">
                          {group.trips.length} 趟
                        </span>
                      </button>
                      {expanded && (
                        <ul className="space-y-2 border-t px-2 py-2">
                          {group.trips.map((trip) => renderTripRow(trip, true))}
                        </ul>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* W15-5 / 阶段 17：朋友的分享图层。
                默认关闭（`DEFAULT_LAYER_VISIBILITY.friend = false`）——
                朋友的点画上来容易被误认成自己去过的地方，必须由用户显式打开。
                打开后这些点带 `source: 'friend'`，渲染时挂「朋友的分享」标签。 */}
            <div className="mt-3 flex items-start gap-2 rounded-lg border px-3 py-2">
              <Checkbox
                id="layer-friend-share"
                checked={visibility.friend}
                onCheckedChange={() => onToggleLayer('friend')}
                aria-label="在地图上显示朋友的分享"
              />
              <Label
                htmlFor="layer-friend-share"
                className="flex flex-1 cursor-pointer flex-col gap-0.5 text-xs font-normal"
              >
                <span>在地图上显示朋友的分享</span>
                <span className="text-[11px] text-muted-foreground">
                  默认关闭；打开后朋友的点会标上「朋友的分享」，不会和你的照片混在一起
                </span>
              </Label>
            </div>
          </div>
        </ScrollArea>
      )}

      {/* ==================== 照片库 Tab ==================== */}
      {tab === 'photos' && (
        <>
          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-3 px-4 py-3">
              <Button
                variant="outline"
                size="sm"
                className="w-full justify-start"
                disabled={uploading}
                onClick={pickFiles}
              >
                {uploading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ImageDown className="h-4 w-4" />
                )}
                {uploading ? '上传中…' : '导入照片'}
              </Button>

              {outcome.status === 'error' && (
                <p className="rounded-lg border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-xs leading-relaxed text-destructive">
                  {outcome.message}
                </p>
              )}
              {outcome.status === 'done' && (
                <p className="rounded-lg border border-emerald-300 bg-emerald-50 px-2 py-1.5 text-xs leading-relaxed text-emerald-900">
                  {outcome.message}
                </p>
              )}

              {/* 隐藏的文件选择器：挂在文档里但不占布局 */}
              <input
                ref={inputRef}
                type="file"
                multiple
                accept="image/*"
                className="hidden"
                onChange={handleChange}
              />

              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  {photos.length === 0
                    ? '还没有照片'
                    : `共 ${photos.length} 张${unlocatedCount > 0 ? `，${unlocatedCount} 张未定位` : ''}`}
                </p>
                {checked.size > 0 && (
                  <span className="text-xs font-medium text-primary">已选 {checked.size} 张</span>
                )}
              </div>

              {photos.length === 0 ? (
                <p className="rounded-lg border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">
                  导入照片后，它们会出现在这里。没有坐标的照片可以手动补标。
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {photos.map((photo) => {
                    const isChecked = checked.has(photo.id);
                    return (
                      <li
                        key={photo.id}
                        className={`flex items-center gap-2 rounded-lg border px-2 py-1.5 transition-colors ${
                          isChecked ? 'border-primary bg-primary/5' : 'hover:bg-accent/60'
                        }`}
                      >
                        <Checkbox
                          id={`photo-${photo.id}`}
                          checked={isChecked}
                          onCheckedChange={() => toggleChecked(photo.id)}
                          aria-label={`选择照片 ${photo.takenAtLocal ?? photo.id}`}
                        />
                        <PhotoThumb
                          photoId={photo.id}
                          className="h-10 w-10 shrink-0 rounded-md object-cover"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-xs font-medium">
                            {photo.takenAtLocal ?? '拍摄时间未知'}
                          </div>
                          {photo.hasLocation ? (
                            <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
                              <MapPin className="h-3 w-3" />
                              {photo.lat?.toFixed(4)}, {photo.lng?.toFixed(4)}
                              <span className="text-border">·</span>
                              {photo.locationSource ?? '未知来源'}
                            </div>
                          ) : (
                            <button
                              type="button"
                              onClick={() => setLocateTarget(photo)}
                              className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-800 transition-colors hover:bg-amber-200"
                            >
                              <MapPinOff className="h-3 w-3" />
                              未定位 · 点此补标
                            </button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </ScrollArea>

          {/* 底部常驻操作条：勾选了照片才有意义 */}
          <div className="border-t px-4 py-3">
            {selectedTrip ? (
              <Button
                size="sm"
                className="w-full"
                disabled={checked.size === 0 || assigning}
                onClick={() => void assignChecked()}
              >
                {assigning ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <CheckCheck className="h-4 w-4" />
                )}
                {assigning
                  ? '加入中…'
                  : `加入「${selectedTrip.title}」（已选 ${checked.size} 张）`}
              </Button>
            ) : (
              <p className="text-center text-xs text-muted-foreground">
                先在「行程列表」选中一趟旅程，才能把勾选的照片加进去
              </p>
            )}
          </div>
        </>
      )}

      {/* 图层开关：行程 Tab 才显示（照片库 Tab 底部已经有操作条了） */}
      {tab === 'trips' && (
        <div className="border-t px-4 py-3">
          <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <Layers className="h-3.5 w-3.5" />
            图层
          </div>
          <div className="mt-2 flex items-center gap-3">
            <Checkbox
              id="layer-share"
              checked={visibility.share}
              onCheckedChange={() => onToggleLayer('share')}
            />
            <Label
              htmlFor="layer-share"
              className="flex flex-1 cursor-pointer items-center justify-between text-xs font-normal"
            >
              <span>分享地点</span>
              <Badge variant="secondary">{shareCount}</Badge>
            </Label>
          </div>
          <div className="mt-2 flex items-center gap-3">
            <Checkbox
              id="layer-photo"
              checked={visibility.photo}
              onCheckedChange={() => onToggleLayer('photo')}
            />
            <Label
              htmlFor="layer-photo"
              className="flex flex-1 cursor-pointer items-center justify-between text-xs font-normal"
            >
              <span>我的照片</span>
              <Badge variant={photos.length > 0 ? 'secondary' : 'muted'}>
                {photos.length > 0 ? photos.length : '暂无'}
              </Badge>
            </Label>
          </div>
        </div>
      )}

      <CreateTripDialog
        open={tripDialogOpen}
        onOpenChange={setTripDialogOpen}
        onCreated={() => void onTripsChanged()}
      />

      {renameTarget && (
        <RenameTripDialog
          open
          onOpenChange={(open) => {
            if (!open) setRenameTarget(null);
          }}
          tripId={renameTarget.id}
          currentTitle={renameTarget.title}
          onRenamed={() => void onTripsChanged()}
        />
      )}

      {locateTarget && (
        <PhotoLocationDialog
          open
          onOpenChange={(open) => {
            if (!open) setLocateTarget(null);
          }}
          photo={locateTarget}
          jsKey={jsKey}
          securityCode={securityCode}
          onSaved={() => void onTripsChanged()}
        />
      )}

      {/* 删除二次确认。**照片不会丢**这件事必须写在确认框里 ——
          用户点删除时心里想的是"我把这趟删了"，担心的正是照片跟着没。 */}
      <Dialog open={deleteTarget !== null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>删除旅程</DialogTitle>
            <DialogDescription>
              确定删除「{deleteTarget?.title}」？这只会移除旅程本身，
              <strong>照片一条不少</strong>，仍留在照片库里。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleteTarget(null)}>
              取消
            </Button>
            <Button variant="destructive" onClick={() => void confirmDelete()}>
              <Trash2 className="h-4 w-4" />
              删除旅程
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** 行程时间范围的人话形式。两端都缺时说「时间未知」而不是空串。 */
export function formatRange(startedAt: string | null, endedAt: string | null): string {
  const start = dayOf(startedAt);
  const end = dayOf(endedAt);
  if (!start && !end) return '时间未知';
  if (start && end) return start === end ? start : `${start} ~ ${end}`;
  return start ?? end ?? '时间未知';
}

/** 取 ISO 串的日期部分（YYYY-MM-DD）。非法或不是真实日期时返回 null。
 *
 *  为什么不能只查形状：`2026-13-99` 完全匹配 `\d{4}-\d{2}-\d{2}`，
 *  但它不是任何一天。所以形状对了之后还要过一次 `new Date`，并**反向比对**
 *  是否被滚动过（`2026-02-31` 会被 JS 静默滚成 `2026-03-03`，只查 isNaN 抓不到它）。
 */
export function dayOf(iso: string | null): string | null {
  if (!iso || iso.length < 10) return null;
  const day = iso.slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const parsed = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toISOString().slice(0, 10) === day ? day : null;
}

/** 一份文件的内容指纹（sha256 hex）。
 *
 *  用来把「按文件下标排好的缩略图」精确对上后端返回的照片 ——
 *  后端 `photos` 不含重复与失败的那些，下标会整体错位。见 `assignThumbs`。
 *  `crypto.subtle` 只在**安全上下文**（https / localhost）存在，局域网 http 下是
 *  `undefined`；此时返回 null，由 `assignThumbs` 退回下标配对。
 */
async function sha256Hex(file: File): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return null;
  try {
    const digest = await subtle.digest('SHA-256', await file.arrayBuffer());
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, '0'))
      .join('');
  } catch {
    return null;
  }
}
