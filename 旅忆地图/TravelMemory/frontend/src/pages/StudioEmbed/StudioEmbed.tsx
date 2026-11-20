/** 主应用里的 iframe 嵌入层（阶段 11）：把阶段 10 的两个独立小应用"装进"地图相册。
 *
 * ## 为什么用 iframe + postMessage，而不是把它们编译成组件
 *
 * 这是阶段 9 拍板的（见阶段 10 任务书开头"主应用嵌入方式：iframe，不用 Module Federation"）。
 * 两个小应用带自己的 CSP、自己的 Service Worker、自己的构建产物，
 * 任何"打进主应用 bundle"的做法都会让那三条独立承诺（零后端 / 离线 / 可单独部署）失效。
 * 代价就是这一层桥：父窗口要给数据（身份 + 缩略图），子窗口要能交回结果。
 *
 * ## 生命周期：打开时挂载，关闭时**卸载**
 *
 * `{open && <iframe/>}` 而不是 `src='about:blank'`：卸载会连同子文档一起销毁，
 * 小应用里已经载入的照片字节、录好的 WebM、生成的包全部随之释放。
 * 只改 `src` 的话，上一轮的草稿还留在那个 origin 的内存里 —— 下次打开会看到"上次的东西"，
 * 这在分享这类**含私钥**的场景里是不能接受的残留（E7）。
 *
 * ## 关闭必须做的三件事
 *
 * `dispose()` 卸监听、`clearTimeout()` 停握手计时、清 `error` 状态。
 * 少做任何一件，下次打开都会继承上一次的坏状态（错误卡片一直挂着、或者计时器
 * 在第二次打开时才触发）。
 *
 * ## 依赖顺序（effect 只看 `[open, iframeEl, targetOrigin, base, 四个稳定回调]`）
 *
 * `onReady` 里要用最新的 `photos` / `trip` / `onTripsChanged`，但它们每次渲染都可能是新引用。
 * 若把它们放进 effect 依赖，父组件一刷新就会**重建桥**，而子应用只在挂载时发一次 `ready`：
 * 新的监听器等不到第二条 `ready`，界面永远停在"正在握手"。
 * 所以回调一律通过 `latest` ref 读最新 props，effect 只随"要不要连、连谁"变化。
 *
 * `iframeEl` 必须是依赖之一：iframe 在 Radix Portal 里**二次渲染**才出现，
 * 光看 `ref.current` 的被动 effect 会在它出现之前就 flush 掉（QA 用 CDP 证伪过的真实 bug）。
 * 详见组件里 `iframeEl` 的注释。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, X } from 'lucide-react';

import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../../components/ui/dialog';
import { toast } from '../../components/ui/use-toast';
import { importJourneyToMain } from '../../services/journey-import/journeyToTrip';
import { resolveNativeBridge } from '../../services/native';
import { createStudioBridge } from '../../services/studio-bridge/bridge';
import type {
  StudioIdentity,
  StudioInitPayload,
  StudioMode,
  StudioPhoto,
} from '../../services/studio-bridge/types';
import type { PhotoListItem } from '../../services/api/photos';
import type { TripView } from '../../services/api/trips';
import { toBase64 } from '../../utils/base64';
import { THUMB_MAX_EDGE } from '../MapPage/photoImport';
import { loadOrCreateIdentity } from '../SharePage/identity';
import { selectStudioPhotos } from './selectPhotos';
import './StudioEmbed.css';

/** 握手超时。5 秒足够 dev server 冷启动响应，又不至于让"没开小应用"的用户干等。 */
const HANDSHAKE_TIMEOUT_MS = 5000;

/** 小应用的基地址（**不写死在代码里**：留空则回落到 dev 默认端口）。 */
function studioBaseUrl(mode: StudioMode): string {
  const configured: unknown =
    mode === 'track' ? import.meta.env.VITE_STUDIO_TRACK_URL : import.meta.env.VITE_STUDIO_SHARE_URL;
  const raw = typeof configured === 'string' ? configured.trim() : '';
  if (raw.length > 0) return raw.replace(/\/+$/, '');
  // 没有配置时才用默认值：`.env.example` 里给的也是这两个，但留空的人不该看到白屏
  return mode === 'track' ? 'http://localhost:5174' : 'http://localhost:5175';
}

/** 取 origin；非法 URL 返回空串（由调用方显示错误卡片）。 */
function originOf(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.origin : '';
  } catch {
    return '';
  }
}

/** 「小应用未启动」的提示（E6）：把**确切命令**给出来，用户照抄就能解决。 */
function notRunningText(mode: StudioMode): string {
  const command =
    mode === 'track'
      ? 'npm run dev -w @travelmemory/track-studio'
      : 'npm run dev -w @travelmemory/trailshare-standalone';
  return `小应用未启动。请运行 ${command}`;
}

const MODE_TITLE: Record<StudioMode, string> = {
  track: '轨迹演示',
  share: '分享这个旅程',
};

export interface StudioEmbedProps {
  open: boolean;
  onClose: () => void;
  mode: StudioMode;
  /** `share` 模式必传：要分享的行程。 */
  trip?: TripView;
  /** `track` 模式传主应用照片库；`share` 模式会自动过滤成该行程的照片。 */
  photos: readonly PhotoListItem[];
  /** 导回成功后让上层刷新（会重拉行程列表并重画地图）。 */
  onTripsChanged?: () => void | Promise<void>;
}

/**
 * 全屏嵌入一个独立小应用。
 *
 * 父 → 子：`init`（身份 + 缩略图 + 行程）。子 → 父：`import-to-main` / `download` / `error`。
 */
export function StudioEmbed({
  open,
  onClose,
  mode,
  trip,
  photos,
  onTripsChanged,
}: StudioEmbedProps) {
  /**
   * iframe 元素本身放进 state（callback ref），**"它出现了"才是建桥的信号**。
   *
   * 为什么不能用 `useRef` + 被动 effect（QA 在真浏览器里证伪过的根因）：
   * iframe 挂在 Radix `DialogPortal` 里，Portal 首帧 `mounted=false` 返回 null，
   * 靠 `useLayoutEffect` 二次渲染才吐出子节点；而**被动 effect 在 iframe 出现之前就 flush 了**，
   * 那时 `ref.current` 是 null → effect 提前 return，依赖数组里又没有任何
   * "iframe 已挂载"的信号 → 它再也不会跑。后果：桥永远不建、`ready` 没人接、
   * 握手文案永远停在"正在握手"、超时卡片永不出现（都是同一个 bug）。
   * 换成 `setIframeEl` 后由 React 在挂载/卸载时调用它，触发一次重渲染，
   * effect 以元素为依赖 → 时机必然在 iframe 存在之后。
   */
  const [iframeEl, setIframeEl] = useState<HTMLIFrameElement | null>(null);
  /** 最新 props 的快照：给一次性创建的桥回调读取（见文件头"依赖顺序"）。 */
  const latest = useRef({ mode, trip, photos, onTripsChanged, onClose });
  latest.current = { mode, trip, photos, onTripsChanged, onClose };
  /** iframe 是否触发过 `load`。**只用于超时文案**：区分"服务器没起"与"起来了但不回应"。 */
  const loadedRef = useRef(false);
  /** 当前握手计时器的清理函数：计时器活在 effect 内，桥回调（onReady/onError）需要停它。 */
  const clearTimerRef = useRef<() => void>(() => undefined);

  const [ready, setReady] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [photoCount, setPhotoCount] = useState(0);
  const [missingCount, setMissingCount] = useState(0);
  const [busyText, setBusyText] = useState<string | null>(null);

  const base = studioBaseUrl(mode).replace(/\/+$/, '');
  const targetOrigin = originOf(base);
  // `parentOrigin` 让小应用知道该把 ready / import-to-main 发给谁（它是另一个 origin，
  // 主应用 dev 的 localhost:5173 不在它内置的白名单里）。见阶段 11 §2.3。
  const parentOrigin = typeof window === 'undefined' ? '' : window.location.origin;
  // 阶段 13 / W13-5：小应用现在有两个 HTML 入口 —— `index.html` 是深色独立版
  // （全程零网络、没有地图），`embed.html` 才是给 iframe 用的浅色地图版。
  // 主应用必须显式指向 `embed.html`，否则嵌进来的是一份"没有地图的独立版"。
  // `base` 先去掉尾部斜杠，避免拼出 `//embed.html`（部分静态托管会把它当协议相对 URL）。
  const src = `${base}/embed.html?embedded=1&parentOrigin=${encodeURIComponent(parentOrigin)}`;

  /* 每次打开都从零开始：上一次的错误卡片 / 计数 / load 标记不该留在新会话里。 */
  useEffect(() => {
    if (!open) return;
    setReady(false);
    setErrorText(null);
    setPhotoCount(0);
    setMissingCount(0);
    setBusyText(null);
    loadedRef.current = false;
  }, [open, mode]);

  /** 收到 `ready` 时组装注入载荷：身份（两个键名都发）+ 缩略图 + 行程。 */
  const buildInitPayload = useCallback(async (): Promise<StudioInitPayload> => {
    const current = latest.current;
    const identity = loadOrCreateIdentity();
    const studioIdentity: StudioIdentity = {
      privHex: identity.privHex,
      pubkey: identity.pubkey,
      // 阶段 11 任务书 §6.1 的键名，与上面同值（两个 app 认的是上面那对）
      ed25519Priv: identity.privHex,
      ed25519Pub: identity.pubkey,
    };

    const picked = selectStudioPhotos(current.mode, current.trip, current.photos);
    const native = resolveNativeBridge();
    const studioPhotos: StudioPhoto[] = [];
    let missing = 0;

    for (const photo of picked) {
      let bytes: ArrayBuffer | null = null;
      try {
        // 唯一路径：本机缩略图缓存（原图在后端资产里，主应用本机只有这张）
        bytes = await native.thumbs.get(photo.id, THUMB_MAX_EDGE);
      } catch {
        bytes = null;
      }
      // 0 字节的 buffer 也当"没有"：小应用那边只会把它当坏图丢掉，白白占一次 transfer
      if (!bytes || bytes.byteLength === 0) {
        missing += 1;
        continue;
      }

      // **先复制一份再交出去**：`thumbs.get()` 在内存实现（隐私模式 / IndexedDB 不可用）
      // 下返回的是缓存里的**同一个** ArrayBuffer 实例，而 transfer 会把它 detach ——
      // 结果是照片库列表里的缩略图立刻变成 0 字节，且没有任何报错。
      // 一张缩略图十几 KB，复制一遍远便宜于这种幽灵 bug。
      const owned = bytes.slice(0);

      // 小应用用 `name` 推断"地点名"（`placeNameFromFileName`），所以给个可读的名字
      const label = photo.caption?.trim() || photo.takenAtLocal?.trim() || photo.id;
      const payloadPhoto: StudioPhoto = {
        id: photo.id,
        bytes: owned,
        name: `${label}.jpg`,
        thumbnailDataUrl: `data:image/jpeg;base64,${toBase64(new Uint8Array(owned))}`,
      };
      const takenAt = photo.takenAt ?? photo.takenAtLocal;
      if (takenAt) payloadPhoto.takenAt = takenAt;
      if (typeof photo.lat === 'number') payloadPhoto.lat = photo.lat;
      if (typeof photo.lng === 'number') payloadPhoto.lng = photo.lng;
      studioPhotos.push(payloadPhoto);
    }

    setPhotoCount(studioPhotos.length);
    setMissingCount(missing);

    return {
      identity: studioIdentity,
      photos: studioPhotos,
      // share 模式把这一趟传过去。阶段 10 的小应用**目前不读**它（见类型注释与报告），
      // 预填表单要等下一阶段改 app；这里不假装它生效。
      trips: current.mode === 'share' && current.trip ? [current.trip] : [],
    };
  }, []);

  /** 小应用要求把 journey 导回主应用。 */
  const handleImportToMain = useCallback(async (journey: unknown): Promise<void> => {
    setBusyText('正在导入旅程…');
    try {
      const stats = await importJourneyToMain(journey);
      // 统计进控制台而不是 toast：toast 文案是验收口径（见任务书 §2.3）
      console.info('[studio] 导回结果', stats);
      await latest.current.onTripsChanged?.();
      toast({ title: '已保存', description: '导入的旅程已加入行程列表' });
      latest.current.onClose();
    } catch (err) {
      toast({
        variant: 'destructive',
        title: '导入失败',
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusyText(null);
    }
  }, []);

  /** 小应用要求下载一个文件（Blob 从 iframe 结构化复制过来）。 */
  const handleDownload = useCallback((blob: Blob, filename: string): void => {
    if (typeof document === 'undefined') return;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename.length > 0 ? filename : 'download';
    anchor.rel = 'noopener';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    // 立刻 revoke 会让部分浏览器来不及开始下载，延后一拍（与阶段 10 的 downloadBlob 一致）
    setTimeout(() => {
      try {
        URL.revokeObjectURL(url);
      } catch {
        /* 已经 revoke 过 */
      }
    }, 1000);
  }, []);

  /**
   * 收到 `ready`：停超时、标记就绪、返回注入载荷。
   *
   * **必须是 `useCallback` 且依赖稳定**：它被传进只创建一次的桥，
   * 若每次 render 都是新引用且进了建桥 effect 的依赖数组，父组件刷新就会重建桥，
   * 而子应用只在挂载时发一次 `ready` —— 新的监听器等不到第二条，界面卡在"正在握手"。
   */
  const handleReady = useCallback(async (): Promise<StudioInitPayload> => {
    clearTimerRef.current();
    setReady(true);
    return buildInitPayload();
  }, [buildInitPayload]);

  /** 桥自己报的错（子应用的 `{type:'error'}` 或发送失败）。 */
  const handleBridgeError = useCallback((message: string): void => {
    clearTimerRef.current();
    setErrorText(message);
  }, []);

  /* 桥：**iframe 真实存在之后**才建（`iframeEl` 是依赖信号），关闭时拆。 */
  useEffect(() => {
    if (!open || !iframeEl) return;
    if (targetOrigin.length === 0) {
      setErrorText(`小应用地址配置有误（需要 http/https）：「${base}」`);
      return;
    }

    const timer = window.setTimeout(() => {
      // 两种失败都给用户能动手的话：是"服务器没起"还是"起来了但不回应"。
      // 两条都必须含「握手超时」——用户先认识到"等了 5 秒没握上"这件事；
      // 两条也都必须带启动命令 —— `onLoad` 判断不了服务是否真的在跑
      // （浏览器对"连接被拒绝"也发 load 事件，错误页照样算 loaded），
      // 所以"已加载"分支同样要给命令，否则用户看到的是一个无从下手的地址。
      const command = notRunningText(latest.current.mode);
      setErrorText(
        loadedRef.current
          ? `握手超时：小应用页面已加载，但 5 秒内没有收到它的就绪信号（ready）。请确认地址可访问：${base}。${command}`
          : `握手超时：5 秒内小应用没有任何响应。${command}`,
      );
    }, HANDSHAKE_TIMEOUT_MS);
    clearTimerRef.current = (): void => {
      window.clearTimeout(timer);
    };

    const studioBridge = createStudioBridge({
      iframe: iframeEl,
      targetOrigin,
      handlers: {
        onReady: handleReady,
        onImportToMain: handleImportToMain,
        onDownload: handleDownload,
        onError: handleBridgeError,
      },
    });

    return () => {
      clearTimerRef.current();
      clearTimerRef.current = (): void => undefined;
      studioBridge.dispose();
    };
    // 依赖里**只有** open / iframeEl（元素身份）/ 两个由 mode 推导的字符串 / 四个
    // `useCallback` 稳定回调。photos 刷新、toast、onTripsChanged 换引用都不会重建桥，
    // 所以"重复 render 不会重复建桥、不会重复发 init"；打开/关闭时 iframeEl 变 null，
    // cleanup 里 `dispose()` 卸监听（幂等），下次打开是全新一轮 ready → init。
  }, [
    open,
    iframeEl,
    targetOrigin,
    base,
    handleReady,
    handleImportToMain,
    handleDownload,
    handleBridgeError,
  ]);

  const noPhotoHint = ready && photoCount === 0;
  const subtitle = errorText
    ? '小应用未就绪'
    : !ready
      ? '正在与小应用握手…'
      : `已传入 ${photoCount} 张缩略图${
          missingCount > 0 ? `（${missingCount} 张本机无缩略图已跳过）` : ''
        }`;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      {/* 全屏：`max-w-none` + `translate-0` + `rounded-none` 把 DialogContent 的默认样式压平。
          `studio-embed-content` 给 StudioEmbed.css 定位那个自带的关闭按钮用。 */}
      <DialogContent
        className="studio-embed-content left-0 top-0 h-[100dvh] w-screen max-w-none translate-x-0 translate-y-0 gap-0 rounded-none border-0 p-0"
        aria-describedby="studio-embed-desc"
      >
        <div className="studio-embed-root">
          <header className="studio-embed-bar">
            <div className="studio-embed-heading">
              <DialogTitle className="studio-embed-title">{MODE_TITLE[mode]}</DialogTitle>
              <DialogDescription id="studio-embed-desc" className="studio-embed-subtitle">
                {subtitle}
              </DialogDescription>
            </div>
            <button
              type="button"
              className="studio-embed-close"
              onClick={onClose}
              aria-label="关闭小应用"
            >
              <X className="h-4 w-4" />
            </button>
          </header>

          {busyText && (
            <div className="studio-embed-notice" role="status">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {busyText}
            </div>
          )}

          {noPhotoHint && !errorText && (
            <div className="studio-embed-notice" role="status">
              暂无可传入的照片：主应用只保存缩略图，若刚换过浏览器需要重新导入照片
            </div>
          )}

          {errorText && (
            <div className="studio-embed-error" role="alert">
              <p className="studio-embed-error-text">{errorText}</p>
              <p className="studio-embed-error-hint">
                小应用需要在另一个端口单独启动；关掉这个面板后可以重新打开重试。
              </p>
            </div>
          )}

          {!ready && !errorText && (
            <div className="studio-embed-notice" role="status">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              正在启动小应用…（首次加载需要几秒）
            </div>
          )}

          <div className="studio-embed-body">
            {/* 关键：`open` 为 false 时**不渲染** iframe —— 子文档被整体销毁（E7）。
                `ref={setIframeEl}` 是 callback ref：React 在挂载时传元素、卸载时传 null，
                这就是建桥 effect 需要的"iframe 已存在"信号。 */}
            {open ? (
              <iframe
                ref={setIframeEl}
                title={MODE_TITLE[mode]}
                src={src}
                allow="camera; fullscreen"
                className="studio-embed-frame"
                onLoad={() => {
                  // 只服务于超时文案：能区分"页面根本没起来"与"起来了但不回应"
                  loadedRef.current = true;
                }}
              />
            ) : null}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
