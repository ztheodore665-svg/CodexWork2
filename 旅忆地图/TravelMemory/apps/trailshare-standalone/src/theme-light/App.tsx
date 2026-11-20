/** 单页状态机：landing → edit → preview → done（A5：不引 react-router），
 *  外加 W12-5 的接收页（landing → receive）。
 *
 * 数据全部在内存里（`draft` 里的照片字节、生成的 `pkg`），`close()` 一次性清空
 * （D7：清引用，不承诺密码学擦除）。
 *
 * ## W12-4：两个入口，两种承诺
 *
 * `entry` 由 `main.tsx` 从 HTML 的 `data-entry` 读出后传进来：
 *  - `standalone`（`index.html`）：全程零网络请求，**没有地图**；
 *  - `embed`（`embed.html`）：被主应用 iframe 嵌入，可以用地图底图。
 *
 * `entry === 'embed'` 但没在主应用里打开（不在 iframe / 没有 `?embedded=1`）时，
 * 渲染引导页并给一个**手点**的链接，**绝不自动跳转** —— 自动跳转会让"我在哪"
 * 这个问题永远得不到回答，而且用户可能本来就想单独用。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  createLibrarySource,
  pickFromSystem,
  setInjectedPhotos,
  type SystemPhoto,
} from '@travelmemory/photo-source';

import { initBridge, isEmbedded, parseBridgePhotos, sendToParent } from './bridge';
import {
  emptyDraft,
  todayIso,
  totalPhotoBytes,
  type DayDraft,
  type PhotoDraft,
  type PlaceDraft,
  type TripDraft,
} from '../core/buildTrip';
import type { EntryKind } from '../csp';
import { downloadBlob, downloadJson, sanitizeFilename } from '../core/download';
import {
  identitySource,
  loadIdentity,
  setInjectedIdentity,
  type IdentitySource,
  type LocalIdentity,
} from '../core/identity';
import { buildJourneyBundle, photoMetaFromDrafts } from '../core/journeyExport';
import { formatBytes, packJourney, parseRecipientInput, type PackJourneyResult } from '../core/packFlow';
import { RECEIVE_CORRUPT_MESSAGE, receiveFromFile } from '../core/receive';
import { seedDayDates } from '../core/photoDates';
import { acceptedToJourney } from '../core/receivedToJourney';
import {
  buildReceivedPlace,
  buildRejectedPlace,
  decisionsFor,
  markRejected,
  saveReceived,
  thumbnailsOf,
  type ReceivedPlace,
} from '../core/receivedStore';
import { identityFromSeed } from '../share/crypto/keys';
import type { UnpackedPlace, UnpackViewerResult } from '../types/share';
import { DonePanel } from './ui/DonePanel';
import { EditPanel } from './ui/EditPanel';
import { Landing } from './ui/Landing';
import { PreviewPanel } from './ui/PreviewPanel';
import { ReceivePanel } from './ui/ReceivePanel';
import './styles.css';

type Mode = 'landing' | 'edit' | 'preview' | 'done' | 'receive';

export interface AppProps {
  /** 入口种类（`index.html` = standalone，`embed.html` = embed）。 */
  entry: EntryKind;
}

/** 照片总字节超过这个值时提示"包会比较大"（不阻断）。 */
const LARGE_PACKAGE_BYTES = 8 * 1024 * 1024;

/** 把 `SystemPhoto` 转成草稿照片（去掉不需要的字段，保留缩略图与 EXIF）。 */
function toDraftPhoto(photo: SystemPhoto): PhotoDraft {
  return {
    id: photo.id,
    bytes: photo.bytes,
    name: photo.name,
    thumbnailDataUrl: photo.thumbnailDataUrl,
    ...(photo.takenAt ? { takenAt: photo.takenAt } : {}),
    ...(typeof photo.lat === 'number' ? { lat: photo.lat } : {}),
    ...(typeof photo.lng === 'number' ? { lng: photo.lng } : {}),
  };
}

let placeSeq = 0;
function newPlaceId(): string {
  placeSeq += 1;
  return `pl_${placeSeq}_${Math.random().toString(16).slice(2, 8)}`;
}

let daySeq = 0;
function newDayId(): string {
  daySeq += 1;
  return `day_${daySeq}_${Math.random().toString(16).slice(2, 8)}`;
}

/**
 * 浅色（嵌入版）界面。
 *
 * 阶段 13 / W13-4：本文件从 `src/App.tsx` 迁到 `src/theme-light/`，**行为零改动**
 * （embed 分支、地图看板、iframe 桥、导回主应用全部保留）。
 * 唯一新增的是外层 `<div className="theme-light">`：样式表整体以 `.theme-light`
 * 为作用域，与深色（`theme-dark`）互不污染。
 */
export default function App({ entry }: AppProps): JSX.Element {
  return (
    <div className="theme-light">
      <ThemeLightApp entry={entry} />
    </div>
  );
}

function ThemeLightApp({ entry }: AppProps): JSX.Element {
  const [mode, setMode] = useState<Mode>('landing');
  const [draft, setDraft] = useState<TripDraft>(emptyDraft);
  const [identity, setIdentity] = useState<LocalIdentity | null>(null);
  /** 身份来源（§10.2）：UI 据此决定显示"使用主应用身份"还是"临时身份"。 */
  const [source, setSource] = useState<IdentitySource>('local');
  /** 注入失败/缺失时的警告（§10.2 要求明说）。 */
  const [identityNote, setIdentityNote] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [recipientsRaw, setRecipientsRaw] = useState('');
  const [libraryPhotos, setLibraryPhotos] = useState<SystemPhoto[]>([]);
  const [busy, setBusy] = useState(false);
  const [progressText, setProgressText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [packed, setPacked] = useState<PackJourneyResult | null>(null);
  const [shareSize, setShareSize] = useState(0);
  /** W12-5：接收页的解包结果（`null` = 还没选文件）。 */
  const [received, setReceived] = useState<UnpackViewerResult | null>(null);
  const [receiveBusy, setReceiveBusy] = useState(false);
  const [receiveFileName, setReceiveFileName] = useState<string | null>(null);
  const [receiveError, setReceiveError] = useState<string | null>(null);
  /** W15-4：每个地点的决定（键 = `placeId`）。 */
  const [decisions, setDecisions] = useState<Record<string, 'accepted' | 'rejected'>>({});
  /** W15-4：已接受的地点（内存里的一份，导出 journey 用它，不重新读库）。 */
  const [accepted, setAccepted] = useState<ReceivedPlace[]>([]);
  /** W15-4：写本地存储失败时的提示（不动 `receiveError`，那个是"读文件"的）。 */
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const lastPickedRef = useRef<SystemPhoto[]>([]);

  /**
   * 是否真的在主应用里被嵌入（F8）。
   *
   * 三个条件缺一不可：
   *  1. 入口是 `embed.html`（`index.html` 永远按独立应用对待）；
   *  2. 确实在 iframe 里（`window.parent !== window`）；
   *  3. URL 带 `?embedded=1`（主应用注入的约定，`bridge.isEmbedded()`）。
   *
   * 单独打开 `embed.html` 时只满足第 1 条 —— 这时给引导页，不猜、不自动跳转。
   */
  const inIframe = typeof window !== 'undefined' && window.parent !== window;
  const embedded = entry === 'embed' && inIframe && isEmbedded();
  /** embed 入口被用错了地方：渲染引导页（**不自动跳转**到 index.html）。 */
  const misusedEmbed = entry === 'embed' && !embedded;

  /* ── 身份 ─────────────────────────────────────────────────── */
  useEffect(() => {
    let alive = true;
    void loadIdentity().then((value) => {
      if (!alive) return;
      setIdentity(value);
      setSource(identitySource());
    });
    return () => {
      alive = false;
    };
  }, []);

  /* ── iframe 桥 ─────────────────────────────────────────────── */
  useEffect(() => {
    // 桥只在 `?embedded=1` 时才真正工作（`initBridge` 内部会再判一次）。
    // embed 入口的合法性不在这里决定 —— 那是上面 `embedded` 三个条件的事。
    return initBridge({
      onInit: (payload) => {
        setLibraryPhotos(parseBridgePhotos(payload.photos));

        // §10.2：优先用主应用注入的身份。注入身份只在内存（不碰 IndexedDB），
        // 也不会被回发给父窗口（`bridge.sendToParent` 有值检查兜底）。
        // 打包时 `identityFromSeed(identity.seedHex)` 用的就是这把注入种子。
        const accepted = payload.identity ? setInjectedIdentity(payload.identity) : false;
        setSource(identitySource());
        if (!accepted) {
          setIdentityNote('主应用未提供身份，将使用临时身份');
          return;
        }
        setIdentityNote(null);
        void loadIdentity().then((value) => {
          setIdentity(value);
          setSource(identitySource());
        });
      },
      onPhotosUpdate: (next) => setLibraryPhotos(next),
    });
  }, []);

  useEffect(() => {
    setInjectedPhotos(libraryPhotos);
  }, [libraryPhotos]);

  /* ── 草稿编辑 ─────────────────────────────────────────────── */

  const updateDraft = useCallback((updater: (current: TripDraft) => TripDraft) => {
    setDraft((current) => updater(current));
  }, []);

  const addPhotosTo = useCallback(
    (dayIndex: number, placeIndex: number, photos: SystemPhoto[]) => {
      if (photos.length === 0) return;
      updateDraft((current) => ({
        ...current,
        days: current.days.map((day, di) =>
          di !== dayIndex
            ? day
            : {
                ...day,
                places: day.places.map((place, pi) =>
                  pi !== placeIndex
                    ? place
                    : { ...place, photos: [...place.photos, ...photos.map(toDraftPhoto)] },
                ),
              },
        ),
      }));
    },
    [updateDraft],
  );

  const pickIntoPlace = useCallback(
    async (dayIndex: number, placeIndex: number, from: 'system' | 'library') => {
      setError(null);
      setBusy(true);
      try {
        let picked: SystemPhoto[] = [];
        if (from === 'system') {
          picked = await pickFromSystem();
        } else {
          const source = createLibrarySource();
          if (!source.available()) {
            setError('主应用还没有把照片传过来。请先回到主应用再进入。');
            return;
          }
          picked = await source.pick();
        }
        if (picked.length === 0) return;
        lastPickedRef.current = picked;
        addPhotosTo(dayIndex, placeIndex, picked);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : '读取照片失败，请重试。');
      } finally {
        setBusy(false);
      }
    },
    [addPhotosTo],
  );

  /** 首屏的"导入"：把照片放进第一天的第一个地点（初始草稿就有一个空地点）。 */
  const handleLandingPick = useCallback(
    async (from: 'system' | 'library') => {
      setError(null);
      setBusy(true);
      try {
        let picked: SystemPhoto[] = [];
        if (from === 'system') {
          picked = await pickFromSystem();
        } else {
          const source = createLibrarySource();
          if (!source.available()) {
            setError('主应用还没有把照片传过来。请先回到主应用再进入。');
            return;
          }
          picked = await source.pick();
        }
        if (picked.length === 0) return;
        lastPickedRef.current = picked;
        setDraft((current) => {
          // W15-1 收尾：**先**用照片日期给空天定日期，**再**塞照片。
          // 顺序反了就失效 —— 塞完之后那天已经有照片，"0 张照片才回填"永远不会命中，
          // 日期框会一直显示 `emptyDraft()` 的今天（QA 判 FAIL 的那条）。
          const base = seedDayDates(current.days.length > 0 ? current : emptyDraft(), picked);
          const days = base.days.length > 0 ? base.days : emptyDraft().days;
          const firstDay = days[0];
          const places =
            firstDay.places.length > 0
              ? firstDay.places
              : [
                  {
                    id: newPlaceId(),
                    name: '',
                    tag: 'nature' as const,
                    intensity: 'mid' as const,
                    photos: [],
                  },
                ];
          const firstPlace = places[0];
          const nextDays: DayDraft[] = [
            {
              ...firstDay,
              places: [
                { ...firstPlace, photos: [...firstPlace.photos, ...picked.map(toDraftPhoto)] },
                ...places.slice(1),
              ],
            },
            ...days.slice(1),
          ];
          return { ...base, days: nextDays };
        });
        setMode('edit');
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : '读取照片失败，请重试。');
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const handleTitleChange = useCallback(
    (value: string) => updateDraft((current) => ({ ...current, title: value })),
    [updateDraft],
  );

  const handleAddDay = useCallback(() => {
    updateDraft((current) => {
      const base = current.days.length > 0 ? current.days[current.days.length - 1].date : todayIso();
      const next = new Date(`${base}T00:00:00.000Z`);
      next.setUTCDate(next.getUTCDate() + 1);
      return {
        ...current,
        days: [
          ...current.days,
          {
            id: newDayId(),
            date: next.toISOString().slice(0, 10),
            places: [
              { id: newPlaceId(), name: '', tag: 'nature', intensity: 'mid', photos: [] },
            ],
          },
        ],
      };
    });
  }, [updateDraft]);

  const handleRemoveDay = useCallback(
    (dayIndex: number) => {
      updateDraft((current) => ({
        ...current,
        days: current.days.filter((_day, index) => index !== dayIndex),
      }));
    },
    [updateDraft],
  );

  const handleDateChange = useCallback(
    (dayIndex: number, value: string) => {
      updateDraft((current) => ({
        ...current,
        days: current.days.map((day, index) =>
          index === dayIndex ? { ...day, date: value } : day,
        ),
      }));
    },
    [updateDraft],
  );

  const handleAddPlace = useCallback(
    (dayIndex: number) => {
      updateDraft((current) => ({
        ...current,
        days: current.days.map((day, index) =>
          index !== dayIndex
            ? day
            : {
                ...day,
                places: [
                  ...day.places,
                  { id: newPlaceId(), name: '', tag: 'nature', intensity: 'mid', photos: [] },
                ],
              },
        ),
      }));
    },
    [updateDraft],
  );

  const handleRemovePlace = useCallback(
    (dayIndex: number, placeIndex: number) => {
      updateDraft((current) => ({
        ...current,
        days: current.days.map((day, index) =>
          index !== dayIndex
            ? day
            : { ...day, places: day.places.filter((_place, pi) => pi !== placeIndex) },
        ),
      }));
    },
    [updateDraft],
  );

  const handlePlaceField = useCallback(
    (
      dayIndex: number,
      placeIndex: number,
      patch: Partial<Pick<PlaceDraft, 'name' | 'label' | 'tag' | 'intensity'>>,
    ) => {
      updateDraft((current) => ({
        ...current,
        days: current.days.map((day, index) =>
          index !== dayIndex
            ? day
            : {
                ...day,
                places: day.places.map((place, pi) =>
                  pi === placeIndex ? { ...place, ...patch } : place,
                ),
              },
        ),
      }));
    },
    [updateDraft],
  );

  const handleRemovePhoto = useCallback(
    (dayIndex: number, placeIndex: number, photoId: string) => {
      updateDraft((current) => ({
        ...current,
        days: current.days.map((day, index) =>
          index !== dayIndex
            ? day
            : {
                ...day,
                places: day.places.map((place, pi) =>
                  pi === placeIndex
                    ? { ...place, photos: place.photos.filter((photo) => photo.id !== photoId) }
                    : place,
                ),
              },
        ),
      }));
    },
    [updateDraft],
  );

  const handleCaptionChange = useCallback(
    (dayIndex: number, placeIndex: number, photoId: string, value: string) => {
      updateDraft((current) => ({
        ...current,
        days: current.days.map((day, index) =>
          index !== dayIndex
            ? day
            : {
                ...day,
                places: day.places.map((place, pi) =>
                  pi === placeIndex
                    ? {
                        ...place,
                        photos: place.photos.map((photo) =>
                          photo.id === photoId ? { ...photo, caption: value } : photo,
                        ),
                      }
                    : place,
                ),
              },
        ),
      }));
    },
    [updateDraft],
  );

  /* ── 生成 ─────────────────────────────────────────────────── */

  const recipientCount = useMemo(() => {
    try {
      return parseRecipientInput(recipientsRaw).length;
    } catch {
      return 0;
    }
  }, [recipientsRaw]);

  const handleGenerate = useCallback(async () => {
    if (!identity) {
      setError('身份还没准备好，请稍等一秒再试。');
      return;
    }
    setError(null);
    setBusy(true);
    setProgressText('正在加密照片并签名…');
    try {
      const result = await packJourney({
        draft,
        // 协议要完整的 Identity（签名 + X25519）。种子是唯一真源，
        // 两套密钥都由 `identityFromSeed` 现算，不额外存储。
        myIdentity: identityFromSeed(identity.seedHex),
        displayName,
        recipientsRaw,
      });
      setPacked(result);
      setShareSize(JSON.stringify(result.pkg).length);
      setProgressText(null);
      setMode('done');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '生成失败，请重试。');
    } finally {
      setBusy(false);
      setProgressText(null);
    }
  }, [draft, identity, displayName, recipientsRaw]);

  /* ── 下载 / 导回 ───────────────────────────────────────────── */

  const shareFilename = useMemo(
    () => `${sanitizeFilename(draft.title, 'journey')}.trailshare`,
    [draft.title],
  );

  const handleDownloadShare = useCallback(() => {
    if (!packed) return;
    const blob = new Blob([JSON.stringify(packed.pkg)], { type: 'application/json' });
    if (embedded) {
      sendToParent({ type: 'download', blob, filename: shareFilename });
      return;
    }
    downloadBlob(blob, shareFilename, 'journey.trailshare');
  }, [packed, embedded, shareFilename]);

  const buildBundle = useCallback(() => {
    if (!packed) return null;
    const meta = photoMetaFromDrafts(packed.trip, draft);
    return buildJourneyBundle(packed.trip, meta);
  }, [packed, draft]);

  const handleDownloadJourney = useCallback(() => {
    const bundle = buildBundle();
    if (!bundle) return;
    if (embedded) {
      sendToParent({ type: 'import-to-main', journey: bundle });
      return;
    }
    downloadJson(bundle, 'journey.json', 'journey.json');
  }, [buildBundle, embedded]);

  const handleImportToMain = useCallback(() => {
    const bundle = buildBundle();
    if (!bundle) return;
    sendToParent({ type: 'import-to-main', journey: bundle });
  }, [buildBundle]);

  const handleClose = useCallback(() => {
    // D7：清引用（不承诺密码学擦除）。照片字节、草稿、已生成的包一起丢掉。
    setDraft(emptyDraft());
    setPacked(null);
    setShareSize(0);
    setRecipientsRaw('');
    setDisplayName('');
    setError(null);
    setProgressText(null);
    lastPickedRef.current = [];
    setMode('landing');
  }, []);

  /* ── 接收别人的信物（W12-5） ────────────────────────────────── */

  /**
   * 读一个 `.trailshare` 文件并用**当前身份**解开。
   *
   * 为什么先 `await loadIdentity()`：用户可能在身份还没读出来时就点了文件
   * （IndexedDB 慢启动）。这里拿不到种子就无法解 KEM，只能等。
   *
   * 解包本身的失败（不是给我的 / 版本旧 / 文件坏）由 `receiveFromFile` 转成
   * `UnpackViewerResult`，**不抛异常**；`catch` 只兜"读取阶段"的意外。
   */
  const handleReceiveFile = useCallback(
    async (file: File) => {
      setReceiveFileName(file.name);
      setReceiveBusy(true);
      setReceiveError(null);
      // 换一封信 = 换一份决定（上一封的"已接受"不能带过来，否则会导出别人的地点）
      setDecisions({});
      setAccepted([]);
      setDecisionError(null);
      setMode('receive');
      try {
        const current = identity ?? (await loadIdentity());
        setIdentity(current);
        const result = await receiveFromFile(file, current.seedHex);
        setReceived(result);
        // 同一封信重复打开时回填"已接受 / 已拒绝"（读库失败最多看起来没处理过，不阻塞阅读）
        if (result.ok) {
          const hydrated = await decisionsFor(result);
          setDecisions(hydrated.decisions);
          setAccepted(hydrated.accepted);
        }
      } catch (caught) {
        // 走到这里只可能是读取/身份阶段的意外：按"文件损坏"处理，让用户能重选
        setReceived({ ok: false, kind: 'invalid', reason: RECEIVE_CORRUPT_MESSAGE });
        setReceiveError(caught instanceof Error ? caught.message : null);
      } finally {
        setReceiveBusy(false);
      }
    },
    [identity],
  );

  const handleLeaveReceive = useCallback(() => {
    // 解出来的照片字节只活在这里：离开接收页就丢掉引用（D7）
    setReceived(null);
    setReceiveFileName(null);
    setReceiveError(null);
    // 决定已经落盘（IndexedDB），内存里的这份只是导出用，离开即丢
    setDecisions({});
    setAccepted([]);
    setDecisionError(null);
    setMode('landing');
  }, []);

  /* ── 接受 / 拒绝一个地点（W15-4） ───────────────────────────── */

  /**
   * 收下一个地点：先把缩略图抽出来落盘，再更新内存态。
   *
   * 顺序很重要 —— 先写库再改 UI：写失败时列表不会假装"已接受"，
   * 用户看到的是错误提示 + 还能重试的按钮。
   */
  const handleAcceptPlace = useCallback(
    async (place: UnpackedPlace) => {
      if (!received || !received.ok) return;
      setDecisionError(null);
      try {
        const record = buildReceivedPlace(received, place, thumbnailsOf(place));
        await saveReceived(record);
        // 同一地点再接受一次（重复点/重复导入）不该在导出里出现两份
        setAccepted((prev) => [...prev.filter((item) => item.id !== record.id), record]);
        setDecisions((prev) => ({ ...prev, [place.placeId]: 'accepted' }));
      } catch (caught) {
        setDecisionError(caught instanceof Error ? caught.message : '未知原因');
      }
    },
    [received],
  );

  /** 拒绝一个地点：写**完整**记录（发件人/地点名/缩略图都留），将来按发件人分组不会拿到空名。 */
  const handleRejectPlace = useCallback(
    async (place: UnpackedPlace) => {
      if (!received || !received.ok) return;
      setDecisionError(null);
      const id = `${received.shareId}:${place.placeId}`;
      try {
        await saveReceived(buildRejectedPlace(received, place, thumbnailsOf(place)));
      } catch (caught) {
        // 兜底：完整记录写不进去时，至少留下"这个 id 被拒绝过"这条事实
        try {
          await markRejected(id);
        } catch {
          setDecisionError(caught instanceof Error ? caught.message : '未知原因');
          return;
        }
      }
      setAccepted((prev) => prev.filter((item) => item.id !== id));
      setDecisions((prev) => ({ ...prev, [place.placeId]: 'rejected' }));
    },
    [received],
  );

  /** 已接受的地点 → journey（embed 发回主应用；standalone 下载文件）。 */
  const handleExportAccepted = useCallback(() => {
    if (accepted.length === 0) return;
    const fromName = received && received.ok ? received.from.displayName : '';
    const bundle = acceptedToJourney(accepted, fromName);
    if (embedded) {
      sendToParent({ type: 'import-to-main', journey: bundle });
      return;
    }
    downloadJson(bundle, 'journey.json', 'journey.json');
  }, [accepted, embedded, received]);

  const largeHint =
    totalPhotoBytes(draft) > LARGE_PACKAGE_BYTES
      ? `照片合计 ${formatBytes(totalPhotoBytes(draft))}，包会比较大（约 ${formatBytes(totalPhotoBytes(draft) * 1.37)}）。`
      : null;

  /* ── 渲染 ─────────────────────────────────────────────────── */

  // W12-4 / F8：embed 入口被单独打开（不在 iframe，或没有 ?embedded=1）。
  // 只给引导，**绝不自动跳转** —— 用户可能就想单独用，也可能只是想看看这个地址是什么。
  if (misusedEmbed) {
    return (
      <div className="panel entry-guard">
        <header className="panel-head">
          <div>
            <h1>这个页面需要在主应用内使用。</h1>
            <p className="lead">
              <code>embed.html</code> 是给主应用 iframe 用的地图视图：它会在你打开主应用时
              由主应用带参数载入，不需要你直接访问。
            </p>
            <p className="hint">
              如果你想要的是<strong>独立使用</strong>（自己选照片、生成信物、打开别人的信物），
              用下面这个链接。
            </p>
          </div>
        </header>
        <p>
          <a className="btn btn-primary" href="./index.html">
            独立打开 TrailShare →
          </a>
        </p>
      </div>
    );
  }

  if (mode === 'edit') {
    return (
      <>
        <EditPanel
          draft={draft}
          identity={identity}
          displayName={displayName}
          recipientsRaw={recipientsRaw}
          busy={busy}
          error={error ?? largeHint}
          onTitleChange={handleTitleChange}
          onDisplayNameChange={setDisplayName}
          onRecipientsChange={setRecipientsRaw}
          onAddDay={handleAddDay}
          onRemoveDay={handleRemoveDay}
          onDateChange={handleDateChange}
          onAddPlace={handleAddPlace}
          onRemovePlace={handleRemovePlace}
          onPlaceField={handlePlaceField}
          onAddPhotos={(dayIndex, placeIndex, from) =>
            void pickIntoPlace(dayIndex, placeIndex, from)
          }
          onRemovePhoto={handleRemovePhoto}
          onCaptionChange={handleCaptionChange}
          onPreview={() => {
            setError(null);
            setMode('preview');
          }}
          onBack={handleClose}
        />
      </>
    );
  }

  if (mode === 'preview') {
    return (
      <PreviewPanel
        draft={draft}
        recipientCount={recipientCount}
        busy={busy}
        progressText={progressText}
        error={error}
        embedded={embedded}
        onGenerate={() => void handleGenerate()}
        onBack={() => setMode('edit')}
      />
    );
  }

  // W12-5：接收别人的信物（`?` 都没有，纯本地文件）
  if (mode === 'receive') {
    return (
      <ReceivePanel
        embedded={embedded}
        result={received}
        busy={receiveBusy}
        fileName={receiveFileName}
        error={receiveError}
        decisions={decisions}
        decisionError={decisionError}
        onFile={(file) => void handleReceiveFile(file)}
        onAccept={(place) => void handleAcceptPlace(place)}
        onReject={(place) => void handleRejectPlace(place)}
        onExport={handleExportAccepted}
        onBack={handleLeaveReceive}
      />
    );
  }

  if (mode === 'done' && packed) {
    return (
      <DonePanel
        packed={packed}
        filename={shareFilename}
        sizeBytes={shareSize}
        embedded={embedded}
        onDownloadShare={handleDownloadShare}
        onDownloadJourney={handleDownloadJourney}
        onImportToMain={handleImportToMain}
        onClose={handleClose}
      />
    );
  }

  return (
    <Landing
      embedded={embedded}
      libraryCount={libraryPhotos.length}
      identity={identity}
      identitySource={source}
      identityNote={identityNote}
      busy={busy}
      error={error}
      onPickSystem={() => void handleLandingPick('system')}
      onPickLibrary={() => void handleLandingPick('library')}
      onReceiveFile={(file) => void handleReceiveFile(file)}
    />
  );
}
