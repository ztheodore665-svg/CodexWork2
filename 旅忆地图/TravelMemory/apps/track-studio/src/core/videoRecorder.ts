/** 视频导出（§7.1.4）。
 *
 * ## 为什么用 MediaRecorder 而不是 ffmpeg.wasm
 *
 * ffmpeg.wasm 体积 30MB+、首帧要等几秒、编码格式还不可控（H.264 授权问题）。
 * `MediaRecorder` 直接录 canvas 的 `captureStream`，桌面 Chrome/Edge/Firefox
 * 都能出 WebM，代价只是"格式不保证 MP4" —— 任务书明确接受（D5）。
 *
 * ## 移动端不试
 *
 * iOS Safari 上 `MediaRecorder` 要么不存在、要么录出来是黑屏。与其让用户等着
 * 失败，不如立刻告诉他"用系统录屏"（§7.1.4 的移动端检测）。`isMobileUA` 就是这条闸。
 *
 * ## 本模块的边界
 *
 * 它只负责"录"，不负责任"怎么播"。播完一遍的逻辑由宿主（`PlayStage`）通过
 * `opts.play()` 注入 —— 因为只有宿主知道时钟、相机和渲染器怎么联动。
 * 这样 recorder 可以在 node 里被单测（没有 MediaRecorder → 返回 null）。
 */

/** 移动端 UA 判定。iPadOS 13+ 的 UA 伪装成 Mac，所以 `iPad` 之外再兜一层触屏判断。 */
export function isMobileUA(ua?: string): boolean {
  const value = ua ?? (typeof navigator === 'undefined' ? '' : navigator.userAgent);
  if (/Android|iPhone|iPad|iPod/i.test(value)) return true;
  // iPadOS 13+ 的 Safari 报告 "Macintosh"，但它是触屏
  if (/Macintosh/i.test(value) && typeof navigator !== 'undefined' && navigator.maxTouchPoints > 1) {
    return true;
  }
  return false;
}

/** 录制的候选 MIME。vp9 最省体积，vp8 兼容性最好，`video/webm` 是最后兜底。 */
const MIME_CANDIDATES = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'] as const;

/** 录制的目标码率（8Mbps：深色轨迹 + 细线，再低会出现明显色带）。 */
const VIDEO_BITS_PER_SECOND = 8_000_000;

/** 进度上报间隔（毫秒）。 */
const PROGRESS_INTERVAL_MS = 100;

/** 选出当前浏览器支持的 MIME；都不支持返回 `null`。 */
export function pickMimeType(): string | null {
  if (typeof window === 'undefined' || !('MediaRecorder' in window)) return null;
  for (const candidate of MIME_CANDIDATES) {
    try {
      if (MediaRecorder.isTypeSupported(candidate)) return candidate;
    } catch {
      // 某些实现会在未知 MIME 上抛错，继续试下一个
    }
  }
  return null;
}

/**
 * 当前环境能否录制视频。
 *
 * 三个条件缺一不可：有 `MediaRecorder`、canvas 支持 `captureStream`、不是移动端。
 * 任一不满足时 UI 应显示"请用系统录屏"，而不是给一个点了没反应的按钮（C5）。
 */
export function canRecord(): boolean {
  if (typeof window === 'undefined') return false;
  if (!('MediaRecorder' in window)) return false;
  const proto =
    typeof HTMLCanvasElement === 'undefined' ? undefined : HTMLCanvasElement.prototype;
  const capture = (proto as { captureStream?: unknown } | undefined)?.captureStream;
  if (typeof capture !== 'function') return false;
  if (isMobileUA()) return false;
  return pickMimeType() !== null;
}

export interface RecordOptions {
  /** 播放进度回调，0..1。 */
  onProgress?: (progress: number) => void;
  /** 宿主负责"从头完整播一遍"。resolve 即视为播放结束（抛错也照样收尾）。 */
  play: () => Promise<void>;
  /** 用户中途取消。取消时返回 `null`（已录到的内容丢弃）。 */
  isCancelled?: () => boolean;
}

/**
 * 录一段画布视频。
 *
 * @param canvas 正在渲染的可见画布（B3：复用播放页画布，不新开离屏）
 * @param durationMs 预期总时长（用于进度百分比；实际时长由 `play()` 决定）
 * @param options 播放函数与进度回调
 * @returns WebM Blob；不可录、被取消、或一帧都没录到时返回 `null`
 */
export async function recordCanvas(
  canvas: HTMLCanvasElement,
  durationMs: number,
  options: RecordOptions,
): Promise<Blob | null> {
  if (!canRecord()) return null;
  const mimeType = pickMimeType();
  if (!mimeType) return null;

  const capture = (
    canvas as HTMLCanvasElement & { captureStream?: (fps?: number) => MediaStream }
  ).captureStream;
  if (typeof capture !== 'function') return null;

  let stream: MediaStream;
  try {
    stream = capture.call(canvas, 60);
  } catch {
    return null;
  }

  let recorder: MediaRecorder;
  try {
    recorder = new MediaRecorder(stream, {
      mimeType,
      videoBitsPerSecond: VIDEO_BITS_PER_SECOND,
    });
  } catch {
    stopStream(stream);
    return null;
  }

  const chunks: Blob[] = [];
  recorder.ondataavailable = (event: BlobEvent): void => {
    if (event.data && event.data.size > 0) chunks.push(event.data);
  };

  // onstop 与"兜底 resolve"共用同一个 resolve：promise 只会 settle 一次，
  // 所以即使 stop() 抛错 / recorder 已是 inactive，下面的 finally 也能收尾，
  // 不会把调用方永久挂在 await 上。
  let settle: ((blob: Blob) => void) | null = null;
  const finished = new Promise<Blob>((resolve) => {
    settle = resolve;
  });
  const collect = (): void => {
    settle?.(new Blob(chunks, { type: mimeType }));
  };
  recorder.onstop = collect;

  const total = Number.isFinite(durationMs) && durationMs > 0 ? durationMs : 1;
  const startedAt = Date.now();
  const timer = setInterval(() => {
    const progress = Math.min(1, (Date.now() - startedAt) / total);
    options.onProgress?.(progress);
  }, PROGRESS_INTERVAL_MS);

  try {
    recorder.start(100);
    await options.play();
  } catch {
    // 播放中途出错：仍然把已经录到的内容收尾，别让用户白等一场
  } finally {
    clearInterval(timer);
    try {
      if (recorder.state !== 'inactive') recorder.stop();
    } catch {
      // stop 在已停止的 recorder 上会抛，忽略
    }
    collect();
  }

  const blob = await finished;
  stopStream(stream);
  options.onProgress?.(1);

  if (options.isCancelled?.()) return null;
  return blob.size > 0 ? blob : null;
}

/** 关掉 captureStream 产生的轨道，否则画布会一直被这个 stream 引用。 */
function stopStream(stream: MediaStream): void {
  try {
    for (const track of stream.getTracks()) track.stop();
  } catch {
    // 忽略：某些环境没有 getTracks
  }
}
