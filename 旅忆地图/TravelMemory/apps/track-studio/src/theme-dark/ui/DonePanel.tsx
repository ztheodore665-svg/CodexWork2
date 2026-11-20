/** 深色版完成页（阶段 13 / W13-2 要求 5）。
 *
 * showcase 原来没有"导出视频"这条路，所以也没有完成页；深色版按任务书补上：
 * 录完一段 WebM 之后给 `<video>` 预览 + 一个下载按钮。
 *
 * 出口只有本地动作：`<a download>` 直接把 Blob 落盘（B16），**没有任何上传**。
 * 「重新播放」与「关闭」都不保留任何数据：`videoBlob` 由上层 state 持有，
 * 关闭时被丢掉（与"零后端、关页即清空"的承诺一致）。
 *
 * 刻意不出现：「导回主应用」—— 深色版是独立打开的页面，没有父窗口可回传（要求 6）。
 *
 * 阶段 14 / W14-3b：补上「导出轨迹 JSON（已签名）」。这个按钮**只落盘**，
 * 同样不上传 —— 与视频下载并列，都是本地动作。
 */

export interface DonePanelProps {
  /** 录到的视频 objectURL；不可录制时为 null。 */
  videoUrl: string | null;
  /** 视频体积（MB），用于给用户一个"这文件多大"的预期。 */
  videoSizeMb: number | null;
  /** 签名后的 `journey.json` 文本；`null` = 这次没有可导出的轨迹（按钮置灰）。 */
  journeyJson: string | null;
  /** 签名公钥（`ed25519:…`），用于在完成页显示"这份文件是谁签的"。 */
  signerPubkey: string | null;
  onDownload: () => void;
  /** 下载已签名的 `journey.json`（`<a download>` 落盘，不上传）。 */
  onExportJson: () => void;
  onReplay: () => void;
  onClose: () => void;
}

export function DonePanel({
  videoUrl,
  videoSizeMb,
  journeyJson,
  signerPubkey,
  onDownload,
  onExportJson,
  onReplay,
  onClose,
}: DonePanelProps): JSX.Element {
  return (
    <div className="modal-mask">
      <div className="done-modal fade-in">
        <div className="modal-title">导出完成</div>
        <div className="modal-text">
          {videoUrl
            ? `视频已经录好${videoSizeMb !== null ? `（约 ${videoSizeMb.toFixed(1)}MB）` : ''}，点下面的按钮保存到你的设备。`
            : '这个浏览器没有产出视频。可以用系统录屏功能录制播放过程。'}
        </div>

        {videoUrl ? (
          <video className="done-video" src={videoUrl} controls playsInline preload="metadata" />
        ) : (
          <div className="done-empty">
            <div>视频录制不可用。</div>
            <div style={{ marginTop: 6, fontSize: 11 }}>
              桌面 Chrome / Edge / Firefox 支持导出 WebM；移动端请用系统录屏。
            </div>
          </div>
        )}

        <div className="done-actions">
          <button type="button" className="btn btn-primary" disabled={!videoUrl} onClick={onDownload}>
            下载视频
          </button>
          <button
            type="button"
            className="btn"
            disabled={journeyJson === null}
            onClick={onExportJson}
            title="导出签名后的 journey.json（只写到本机，不上传）"
          >
            导出轨迹 JSON（已签名）
          </button>
          <button type="button" className="btn" onClick={onReplay}>
            重新播放
          </button>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            关闭（清空内存）
          </button>
        </div>

        {journeyJson !== null ? (
          <div className="done-sign-note">
            轨迹 JSON 用本机身份签名
            {signerPubkey ? `（${signerPubkey}）` : ''}，导入方可以验签确认这份文件没被改过。
          </div>
        ) : (
          <div className="done-sign-note">这次没有可导出的轨迹数据（至少要 2 张带位置的照片）。</div>
        )}
      </div>
    </div>
  );
}
