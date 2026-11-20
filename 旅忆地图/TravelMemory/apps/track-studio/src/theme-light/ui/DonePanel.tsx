/** 完成页（Done，§7.1.1）：预览视频、下载、导回主应用。
 *
 * 两条出口都是**本地动作**：`<a download>` 直接落盘（B16），
 * 或者 postMessage 交给父窗口 —— 没有任何上传。
 */

export interface DonePanelProps {
  /** 录到的视频；不可录制时为 null。 */
  videoUrl: string | null;
  videoSizeMb: number | null;
  embedded: boolean;
  onDownloadVideo: () => void;
  onDownloadJourney: () => void;
  onImportToMain: () => void;
  onClose: () => void;
}

export function DonePanel(props: DonePanelProps): JSX.Element {
  const { videoUrl, videoSizeMb, embedded } = props;

  return (
    <div className="panel">
      <header className="panel-head">
        <div>
          <h1>完成</h1>
          <p className="lead">
            {videoUrl
              ? `视频已经录好${videoSizeMb !== null ? `（约 ${videoSizeMb.toFixed(1)}MB）` : ''}，点下面的按钮保存到你的设备。`
              : '这个浏览器没有产出视频。可以用系统录屏功能录制播放过程，或直接下载 journey.json。'}
          </p>
        </div>
      </header>

      {videoUrl ? (
        <video className="done-video" src={videoUrl} controls playsInline preload="metadata" />
      ) : (
        <div className="done-empty">
          <p>视频录制不可用。</p>
          <p className="hint">桌面 Chrome / Edge / Firefox 支持导出 WebM；移动端请用系统录屏。</p>
        </div>
      )}

      <div className="done-actions">
        <button
          type="button"
          className="btn btn-primary"
          disabled={!videoUrl}
          onClick={props.onDownloadVideo}
        >
          下载视频
        </button>
        <button type="button" className="btn" onClick={props.onDownloadJourney}>
          下载 journey.json（只有缩略图，没有原图）
        </button>
        <button
          type="button"
          className="btn"
          disabled={!embedded}
          title={embedded ? '把旅程交回主应用' : '请从主应用进入此功能'}
          onClick={props.onImportToMain}
        >
          导回主应用
        </button>
        <button type="button" className="btn btn-ghost" onClick={props.onClose}>
          关闭（清空内存）
        </button>
      </div>

      <p className="hint">
        「关闭」会丢掉这次导入的全部照片与视频。这个页面不写 IndexedDB、不上传，刷新即空。
      </p>
    </div>
  );
}
