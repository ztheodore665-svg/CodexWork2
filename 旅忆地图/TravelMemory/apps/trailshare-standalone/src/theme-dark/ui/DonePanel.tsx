/** 完成页：下载 `.trailshare` / 自检报告 / 关闭。
 *
 * 阶段 13 / W13-4：深色版删掉「导回主应用」——独立入口没有父窗口可回传
 * （浅色版保留这一条，见 `theme-light/ui/DonePanel.tsx`）。
 *
 * 自检报告不是装饰：发送方**无法**验证收件人那边能不能解开，所以"我自己用这把种子
 * 解开了、签名与承诺都对"是他唯一能拿到的证据。自检失败时下载按钮必须禁用 ——
 * 否则用户会把一个连自己都打不开的文件发给别人。
 */

import { formatBytes, type PackJourneyResult } from '../../core/packFlow';

export interface DonePanelProps {
  packed: PackJourneyResult;
  /** `.trailshare` 的文件名（已清洗）。 */
  filename: string;
  sizeBytes: number;
  onDownloadShare: () => void;
  onDownloadJourney: () => void;
  onClose: () => void;
}

export function DonePanel(props: DonePanelProps): JSX.Element {
  const { packed, filename, sizeBytes } = props;
  const { report } = packed;

  return (
    <div className="panel">
      <header className="panel-head">
        <div>
          <h1>信物已生成</h1>
          <p className="lead">
            {filename} · {formatBytes(sizeBytes)} · {report.places} 个地点 / {report.photos} 张照片
          </p>
        </div>
      </header>

      <section className={report.ok ? 'report report-ok' : 'report report-bad'}>
        <h2>自检（用你自己的种子解开）</h2>
        <ul>
          <li>{report.signatureOk ? '✅ 签名通过' : '❌ 签名不一致'}</li>
          <li>
            {report.commitmentOk === true
              ? '✅ 承诺一致（包内容与承诺值吻合）'
              : report.commitmentOk === false
                ? '❌ 承诺不一致（包内容被改动）'
                : '• 承诺无法判定'}
          </li>
          <li>{report.ok ? '✅ 能用你的身份解开' : `❌ ${report.reason ?? '解不开'}`}</li>
          <li className="hint">{report.chainNote}</li>
        </ul>
      </section>

      <p className="hint">
        {packed.onlyMe
          ? '未填收件人：这个包只有你自己能打开（收件人拿到也解不开）。'
          : `收件人 ${packed.recipients.length - 1} 位 + 你自己，共 ${packed.pkg.groups[0].wrappedKeys.length} 份密钥。`}
      </p>

      <div className="done-actions">
        <button
          type="button"
          className="btn btn-primary"
          disabled={!report.ok}
          onClick={props.onDownloadShare}
        >
          下载 .trailshare
        </button>
        <button type="button" className="btn" disabled={!report.ok} onClick={props.onDownloadJourney}>
          下载 journey.json（给主应用，只有缩略图）
        </button>
        <button type="button" className="btn btn-ghost" onClick={props.onClose}>
          关闭（清空内存）
        </button>
      </div>

      <p className="hint">
        「关闭」会丢掉这次导入的全部照片、草稿与已生成的包。本页不写 IndexedDB（只有身份种子在里面）、
        不上传，刷新即空。
      </p>
    </div>
  );
}
