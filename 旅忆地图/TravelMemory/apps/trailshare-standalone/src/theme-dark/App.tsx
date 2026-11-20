/** 深色版（独立入口）状态机：landing → edit → preview → done，外加 receive。
 *
 * 阶段 13 / W13-4：从 `src/App.tsx` **复制**并裁掉"嵌入"那一半。
 *
 * ## 与浅色版的差异（就是"两套承诺"的具体形状）
 *
 * | 能力 | 浅色（embed） | 深色（standalone，本文件） |
 * | --- | --- | --- |
 * | iframe 桥 / 主应用注入照片 | 有 | **没有**（本文件不 import `./bridge`） |
 * | 「从主应用相册选」 | 有（置灰提示） | **不显示** |
 * | 「导回主应用」 | 有（postMessage） | **不显示** |
 * | 地图（`PlaceMap` / `PlaceBoard`） | 有（高德 / 降级） | **没有**，预览与接收都用 `PlaceList` 纯文本 |
 * | 网络请求 | 允许地图底图 | **零**（CSP 里一个地图域名都没有） |
 *
 * 保留下来的东西一条没少：选照片 → 按天编辑 → 生成（加密 + 签名）→ 下载
 * `.trailshare` / `journey.json`，以及"打开别人的信物"。
 *
 * 数据全部在内存里（`draft` 里的照片字节、生成的 `pkg`），`close()` 一次性清空
 * （D7：清引用，不承诺密码学擦除）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { pickFromSystem, type SystemPhoto } from '@travelmemory/photo-source';
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
import { identitySource, loadIdentity, type IdentitySource, type LocalIdentity } from '../core/identity';
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
  /** 入口种类。深色版只会收到 `'standalone'`（`main.tsx` 按 `data-entry` 分发）；
   *  仍保留这个 prop 是为了两个入口共用同一份 `render(<App entry={entry} />)`。 */
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
/** 根组件：外层 `<div className="theme-dark">` 是样式作用域锚点（`theme-dark/styles.css`）。 */
export default function App(_props: AppProps): JSX.Element {
  return (
    <div className="theme-dark">
      <DarkApp entry="standalone" />
    </div>
  );
}

function DarkApp(_props: AppProps): JSX.Element {
  const [mode, setMode] = useState<Mode>('landing');
  const [draft, setDraft] = useState<TripDraft>(emptyDraft);
  const [identity, setIdentity] = useState<LocalIdentity | null>(null);
  /** 身份来源（§10.2）：UI 据此决定显示"使用主应用身份"还是"临时身份"。 */
  const [source, setSource] = useState<IdentitySource>('local');
  /**
   * 注入失败/缺失时的警告（§10.2 要求明说）。
   * 深色版没有 iframe 桥，也就不存在"主应用未提供身份"这件事，所以这里恒为 null；
   * 保留这个 state 是为了让 `Landing` 的 props 与浅色版同形（那边桥会 set 它）。
   */
  const [identityNote] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [recipientsRaw, setRecipientsRaw] = useState('');
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

  /* ── 没有 iframe 桥 ───────────────────────────────────────────
   * 深色版是独立打开的页面：没有父窗口给它注入照片 / 身份，所以这里不启动桥，
   * 也不存在 `setInjectedPhotos` 这条同步链（`theme-light/App.tsx` 才有）。
   * 身份就是本机 IndexedDB 里的那一把（拿不到时退化成内存身份）。
   */

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
    async (dayIndex: number, placeIndex: number) => {
      setError(null);
      setBusy(true);
      try {
        const picked = await pickFromSystem();
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
    async () => {
      setError(null);
      setBusy(true);
      try {
        const picked: SystemPhoto[] = await pickFromSystem();
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
    // 独立版没有父窗口：永远是本地 `<a download>`，不走 postMessage
    downloadBlob(blob, shareFilename, 'journey.trailshare');
  }, [packed, shareFilename]);

  const buildBundle = useCallback(() => {
    if (!packed) return null;
    const meta = photoMetaFromDrafts(packed.trip, draft);
    return buildJourneyBundle(packed.trip, meta);
  }, [packed, draft]);

  const handleDownloadJourney = useCallback(() => {
    const bundle = buildBundle();
    if (!bundle) return;
    downloadJson(bundle, 'journey.json', 'journey.json');
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

  /** 已接受的地点 → `journey.json` 下载（深色版是独立应用，没有父窗口可发）。 */
  const handleExportAccepted = useCallback(() => {
    if (accepted.length === 0) return;
    const fromName = received && received.ok ? received.from.displayName : '';
    const bundle = acceptedToJourney(accepted, fromName);
    downloadJson(bundle, 'journey.json', 'journey.json');
  }, [accepted, received]);

  const largeHint =
    totalPhotoBytes(draft) > LARGE_PACKAGE_BYTES
      ? `照片合计 ${formatBytes(totalPhotoBytes(draft))}，包会比较大（约 ${formatBytes(totalPhotoBytes(draft) * 1.37)}）。`
      : null;

  /* ── 渲染 ─────────────────────────────────────────────────── */

  /* ── 渲染 ───────────────────────────────────────────────────
   * 没有"embed 入口被误用"的引导页：深色版就是独立入口（`index.html`），
   * 不存在"该在主应用里打开"的情况。
   */

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
          onAddPhotos={(dayIndex, placeIndex) => void pickIntoPlace(dayIndex, placeIndex)}
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
        onGenerate={() => void handleGenerate()}
        onBack={() => setMode('edit')}
      />
    );
  }

  // W12-5：接收别人的信物（`?` 都没有，纯本地文件）
  if (mode === 'receive') {
    return (
      <ReceivePanel
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
        onDownloadShare={handleDownloadShare}
        onDownloadJourney={handleDownloadJourney}
        onClose={handleClose}
      />
    );
  }

  return (
    <Landing
      identity={identity}
      identitySource={source}
      identityNote={identityNote}
      busy={busy}
      error={error}
      onPickSystem={() => void handleLandingPick()}
      onReceiveFile={(file) => void handleReceiveFile(file)}
    />
  );
}
