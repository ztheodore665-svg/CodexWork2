/** 单页状态机：landing → edit → play → done（A5：不引 react-router）。
 *
 * 数据全部在内存里（`photos` 是 `SystemPhoto[]`，关掉页面即丢）。
 * 唯一落盘的东西是用户主动点"下载"产生的文件。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { buildJourney, type Journey, type TransportMode } from '@travelmemory/trail';
import {
  createLibrarySource,
  pickFromSystem,
  setInjectedPhotos,
  type SystemPhoto,
} from '@travelmemory/photo-source';

import { initBridge, isEmbedded, parseBridgePhotos, sendToParent } from './bridge';
import { hasAmapKey } from '../env';
import {
  identitySource,
  loadIdentity,
  setInjectedIdentity,
  type IdentitySource,
  type LocalIdentity,
} from '../core/identity';
import { buildJourneyBundle, bundleToJson, downloadBlob } from '../core/journeyBuild';
import { demoPhotos } from '../core/demoRoute';
import { toTrailPhotos } from '../core/photoImport';
import { applyLocation } from '../core/validate';
import { LocationEditor } from './pages/LocationEditor';
import { DonePanel } from './ui/DonePanel';
import { EditPanel } from './ui/EditPanel';
import { EmbedGuidance } from './ui/EmbedGuidance';
import { Landing } from './ui/Landing';
import { PlayStage } from './ui/PlayStage';
import './styles.css';

/** `location` 是 W12-2a 新增的「补全照片位置」页：从编辑页进，补完/跳过回编辑页。 */
type Mode = 'landing' | 'edit' | 'location' | 'play' | 'done';

/** 导出/播放时用的旅程标题。track-studio 不采集标题（那是 TrailShare 的事）。 */
const JOURNEY_TITLE = '我的旅程';

/**
 * 浅色（嵌入版）界面主体。
 *
 * 阶段 13：本文件从 `src/App.tsx` 迁到 `src/theme-light/`，**行为零改动**。
 * 唯一的包装是外层那个 `<div className="theme-light">`：样式表整体以
 * `.theme-light` 为作用域前缀，两个主题互不污染（H9）。
 */
export default function App(): JSX.Element {
  return (
    <div className="theme-light">
      <ThemeLightApp />
    </div>
  );
}

function ThemeLightApp(): JSX.Element {
  const [mode, setMode] = useState<Mode>('landing');
  const [photos, setPhotos] = useState<SystemPhoto[]>([]);
  const [transports, setTransports] = useState<Record<string, TransportMode>>({});
  const [demoMode, setDemoMode] = useState(false);
  const [embedded, setEmbedded] = useState(false);
  /** 主应用通过 postMessage 注入的照片。 */
  const [libraryPhotos, setLibraryPhotos] = useState<SystemPhoto[]>([]);
  const [identity, setIdentity] = useState<LocalIdentity | null>(null);
  /** 身份来源（§10.2）：UI 据此决定显示"使用主应用身份"还是"临时身份"。 */
  const [source, setSource] = useState<IdentitySource>('local');
  /** 注入失败/缺失时的警告（§10.2 要求明说）。 */
  const [identityNote, setIdentityNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [videoBlob, setVideoBlob] = useState<Blob | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);

  /* ── 身份（§10.1） ─────────────────────────────────────────── */
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

  /* ── iframe 桥（§9） ───────────────────────────────────────── */
  useEffect(() => {
    setEmbedded(isEmbedded());
    return initBridge({
      onInit: (payload) => {
        const incoming = parseBridgePhotos(payload.photos);
        setLibraryPhotos(incoming);

        // §10.2：优先用主应用注入的身份；没有/invalid 时退回临时身份并警告。
        // 注入身份只在内存（`setInjectedIdentity` 不碰 IndexedDB），
        // 且绝不回发给父窗口（`bridge.sendToParent` 有值检查兜底）。
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

  // photo-source 的 library 来源与这里的 state 保持同步
  useEffect(() => {
    setInjectedPhotos(libraryPhotos);
  }, [libraryPhotos]);

  /* ── 视频 Blob → URL ──────────────────────────────────────── */
  useEffect(() => {
    if (!videoBlob) {
      setVideoUrl(null);
      return;
    }
    const url = URL.createObjectURL(videoBlob);
    setVideoUrl(url);
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [videoBlob]);

  /* ── 轨迹构建 ─────────────────────────────────────────────── */
  const transportMap = useMemo(
    () => new Map<string, TransportMode>(Object.entries(transports)),
    [transports],
  );

  const trailPhotos = useMemo(
    () => toTrailPhotos(photos, { transports: transportMap }),
    [photos, transportMap],
  );

  const locatedCount = useMemo(
    () => trailPhotos.filter((photo) => photo.lat !== null && photo.lng !== null).length,
    [trailPhotos],
  );

  const journey = useMemo<Journey | null>(() => {
    if (mode !== 'play' && mode !== 'done') return null;
    if (trailPhotos.length === 0) return null;
    return buildJourney({
      id: 'track-studio',
      title: JOURNEY_TITLE,
      subtitle: `${trailPhotos.length} 张照片 · ${locatedCount} 个定位点`,
      photos: trailPhotos,
    });
  }, [mode, trailPhotos, locatedCount]);

  /* ── 照片导入 ─────────────────────────────────────────────── */

  const handlePickSystem = useCallback(async () => {
    setError(null);
    setBusy(true);
    try {
      const picked = await pickFromSystem();
      if (picked.length === 0) return;
      setPhotos((prev) => [...prev, ...picked]);
      setDemoMode(false);
      setMode('edit');
    } catch {
      setError('读取照片失败。请重试，或换一个浏览器。');
    } finally {
      setBusy(false);
    }
  }, []);

  const handlePickLibrary = useCallback(async () => {
    setError(null);
    try {
      const source = createLibrarySource();
      if (!source.available()) {
        setError('主应用还没有把照片传过来。请先回到主应用再进入。');
        return;
      }
      const picked = await source.pick();
      if (picked.length === 0) return;
      setPhotos(picked);
      setDemoMode(false);
      setMode('edit');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '读取主应用相册失败。');
    }
  }, []);

  const handleUseDemo = useCallback(() => {
    setError(null);
    setPhotos(demoPhotos());
    setTransports({});
    setDemoMode(true);
    setMode('edit');
  }, []);

  const handleRemove = useCallback((photoId: string) => {
    setPhotos((prev) => prev.filter((photo) => photo.id !== photoId));
  }, []);

  const handleTransport = useCallback((photoId: string, next: TransportMode) => {
    setTransports((prev) => ({ ...prev, [photoId]: next }));
  }, []);

  /* ── 页面流转 ─────────────────────────────────────────────── */

  const handleOpenLocationEditor = useCallback(() => {
    setMode('location');
  }, []);

  /**
   * W12-2a：把地图点选结果写回一张照片。
   *
   * 只动内存态（`photos` 是 `SystemPhoto[]`，关掉页面即丢），不碰任何存储 ——
   * 这与本应用「零后端、不落盘」的承诺一致。写回逻辑本身在
   * `pages/location/locationFix.ts` 的纯函数里，可单测。
   */
  const handleApplyLocation = useCallback((photoId: string, lat: number, lng: number) => {
    setPhotos((prev) => applyLocation(prev, photoId, lat, lng));
  }, []);

  const handleGenerate = useCallback(() => {
    setMode('play');
  }, []);

  const handleBackToEdit = useCallback(() => {
    setMode('edit');
  }, []);

  const handleExported = useCallback((blob: Blob) => {
    setVideoBlob(blob);
    setMode('done');
  }, []);

  const handleDownloadVideo = useCallback(() => {
    if (!videoBlob) return;
    downloadBlob(videoBlob, 'track-studio.webm');
  }, [videoBlob]);

  const handleDownloadJourney = useCallback(() => {
    const bundle = buildJourneyBundle({ title: JOURNEY_TITLE, photos });
    downloadBlob(new Blob([bundleToJson(bundle)], { type: 'application/json' }), 'journey.json');
  }, [photos]);

  const handleImportToMain = useCallback(() => {
    const bundle = buildJourneyBundle({ title: JOURNEY_TITLE, photos });
    sendToParent({ type: 'import-to-main', journey: bundle });
  }, [photos]);

  const handleClose = useCallback(() => {
    setPhotos([]);
    setTransports({});
    setDemoMode(false);
    setVideoBlob(null);
    setMode('landing');
  }, []);

  /* ── 渲染 ─────────────────────────────────────────────────── */

  /**
   * 任务书 §6.4：`embed.html` 被**直接打开**时给整页引导（不自动跳转）。
   *
   * 判定口径刻意只多一条 `entry === 'embed' && !inIframe`，**不动**上面那个
   * `embedded`（它来自 `isEmbedded()`，即 `?embedded=1`）：
   *
   *  - `entry` 读 HTML 上的 `data-entry`（`index.html`=standalone / `embed.html`=embed）；
   *  - `inIframe` 是 `window.parent !== window`。
   *
   * 为什么不把 `embedded` 也改成"entry + inIframe + ?embedded=1"三合一：
   * `public/embed-harness.html`（阶段 9 的桥调试页）是**同源 iframe 指向 `/?embedded=1`**，
   * 入口是 standalone。一旦把 entry 绑进 `embedded`，这个 harness 里的
   * "从主应用相册选 / 导回主应用"会全部变灰 —— 那会让 QA 失去验证桥的唯一本地手段。
   * 所以：`embedded`（能力开关）保持原样；本块只新增"入口用错了地方"的引导。
   *
   * 覆盖的两种情况都来自任务书原文：
   *  ① `embed.html` 直接打开（不在 iframe 里，也不带 `?embedded=1`）；
   *  ② `embed.html?embedded=1` 但不在 iframe 里（参数是死的，父窗口不存在）。
   * 正常嵌入路径（主应用 iframe + `?embedded=1`）`inIframe === true`，直接跳过本块，
   * 行为与改动前逐字相同。
   */
  const entry =
    typeof document === 'undefined' ? 'standalone' : document.documentElement.dataset.entry;
  const inIframe = typeof window !== 'undefined' && window.parent !== window;
  if (entry === 'embed' && !inIframe) {
    return <EmbedGuidance />;
  }

  if (mode === 'edit') {
    return (
      <EditPanel
        photos={photos}
        transports={transports}
        demoMode={demoMode}
        busy={busy}
        onRemove={handleRemove}
        onTransport={handleTransport}
        onFixLocations={handleOpenLocationEditor}
        onGenerate={handleGenerate}
        onBack={handleClose}
      />
    );
  }

  // W12-2a：补全照片位置。补完（items 变空）或点「全部跳过」都回到编辑页。
  if (mode === 'location') {
    return (
      <LocationEditor photos={photos} onApply={handleApplyLocation} onExit={handleBackToEdit} />
    );
  }

  if (mode === 'play' && journey && journey.track.length > 0) {
    return <PlayStage journey={journey} onBack={handleBackToEdit} onExported={handleExported} />;
  }

  if (mode === 'done') {
    return (
      <DonePanel
        videoUrl={videoUrl}
        videoSizeMb={videoBlob ? videoBlob.size / (1024 * 1024) : null}
        embedded={embedded}
        onDownloadVideo={handleDownloadVideo}
        onDownloadJourney={handleDownloadJourney}
        onImportToMain={handleImportToMain}
        onClose={handleClose}
      />
    );
  }

  if (mode === 'play') {
    // 走到这里只可能是"带位置的照片不足 2 张"，buildJourney 没有轨迹
    return (
      <div className="panel">
        <header className="panel-head">
          <div>
            <h1>还差一点</h1>
            <p className="lead">
              这条轨迹至少需要 2 张带位置信息的照片。回去多选几张，或用「演示路线体验」看看效果。
            </p>
          </div>
        </header>
        <div className="panel-foot">
          <button type="button" className="btn btn-primary" onClick={handleBackToEdit}>
            返回调整
          </button>
        </div>
      </div>
    );
  }

  return (
    <Landing
      embedded={embedded}
      libraryCount={libraryPhotos.length}
      identity={identity}
      identitySource={source}
      identityNote={identityNote}
      hasAmapKey={hasAmapKey()}
      busy={busy}
      error={error}
      onPickSystem={() => void handlePickSystem()}
      onPickLibrary={() => void handlePickLibrary()}
      onUseDemo={handleUseDemo}
    />
  );
}
