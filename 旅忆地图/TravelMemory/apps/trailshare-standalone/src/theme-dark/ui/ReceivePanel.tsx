/** 接收页（W12-5 + W15-4）：打开别人发来的 `.trailshare`，逐个地点决定「收下 / 拒绝」。
 *
 * ## 这一页要回答的四个问题
 *
 * 1. **这是谁发的** —— 显示名 + 短公钥（收件人靠它判断"认不认识"）；
 * 2. **这个包有没有被改过** —— 验签 / 承诺 / 链三态，一项一行，不折叠；
 * 3. **里面是什么** —— 纯列表（`PlaceList`）。深色版没有地图，所以这一页永远不出现看板；
 *    浅色版（embed）才用「地图 + 侧栏」的 `PlaceBoard`；
 * 4. **我要收哪些**（W15-4）—— 地点清单逐条「接受 / 拒绝」，收下的地点下载成
 *    `journey.json`（深色版是独立应用，没有父窗口可发）。
 *
 * ## 三种失败必须说人话（不许合并成一句"打不开"）
 *
 * | kind | 用户该做什么 |
 * | --- | --- |
 * | `not-for-you` | 让发送方把「我」加进收件人，重新发一次 |
 * | `old-version` | 让发送方用新版重新生成 |
 * | `invalid` | 文件传坏了 / 根本不是信物 —— 重新要一份 |
 *
 * 文案本身来自协议层（`unpackForViewer`），这里**不再改写**：同一句话在 CLI、
 * 测试和 UI 里必须是同一串字符，否则排查时对不上。
 *
 * ## 红线
 *
 * 不显示组数、不显示 groupId、不显示"还有 N 组没解开"。
 * 也不显示密文/密钥的任何片段 —— 收件人只需要知道"能不能看、是谁发的"。
 * 决定（接受/拒绝）只写本机 IndexedDB，**这一页不发任何网络请求**。
 */

import { useRef } from 'react';

import { shortPubkey } from '../../core/identity';
import { TAG_LABELS, type UnpackedPlace, type UnpackViewerResult } from '../../types/share';
import { PlaceList } from './PlaceList';
import { placeCountOfResult, placesOfResult } from '../../core/receive';

/** 一个地点上"用户做过的决定"。没有键 = 还没决定。 */
export type PlaceDecision = 'accepted' | 'rejected';

export interface ReceivePanelProps {
  /**
   * 是否在主应用里嵌入。深色版永远是独立应用（本文件不 import `./bridge`），
   * 这里只是与浅色版保持同一套 props 形状；导出按钮文案仍按 `embedded` 走。
   */
  embedded?: boolean;
  /** 解包结果；`null` = 还没选文件。 */
  result: UnpackViewerResult | null;
  /** 读文件/解包中的忙碌态。 */
  busy: boolean;
  /** 刚选的文件名（只用于显示）。 */
  fileName: string | null;
  /** 读取/身份阶段的意外（解包失败走 `result`，不走这里）。 */
  error?: string | null;
  /** 每个 `placeId` 的决定（`Record<placeId, 'accepted'|'rejected'>`）。 */
  decisions?: Record<string, PlaceDecision>;
  /** 写本地存储失败时的提示（读文件失败走 `error`）。 */
  decisionError?: string | null;
  /** 选了一个文件（父组件负责读取与解包）。 */
  onFile: (file: File) => void;
  /** 收下一个地点（父组件负责落盘）。 */
  onAccept: (place: UnpackedPlace) => void;
  /** 拒绝一个地点（父组件负责落盘）。 */
  onReject: (place: UnpackedPlace) => void;
  /** 导出已接受的地点为 journey（深色版 = 下载）。 */
  onExport: () => void;
  /** 回到首屏。 */
  onBack: () => void;
}

/** 三种失败的"下一步建议"。文案与 `reason` 互补，不重复。 */
const FAILURE_HINTS: Record<string, string> = {
  'not-for-you': '这个信物是加密给指定收件人的。如果是发给你的，请让发送方把「你」加进收件人后重新生成。',
  'old-version': '旧版本的包无法在本应用中打开，请让发送方用当前版本重新生成。',
  invalid: '可能是在传输中损坏了，或者这个文件根本不是 TrailShare 信物。请让发送方重新发一份。',
};

/** 三态徽章的文案。`null` 表示"无法判定"，不能写成"失败"。 */
function stateLabel(ok: boolean | null): string {
  if (ok === true) return '通过';
  if (ok === false) return '未通过';
  return '无法判定';
}

function stateClass(ok: boolean | null): string {
  if (ok === true) return 'badge badge-ok';
  if (ok === false) return 'badge badge-bad';
  return 'badge badge-dim';
}

/** 标签中文名（协议里是英文值，界面上不许漏出去）。 */
function tagLabel(tag: string): string {
  return TAG_LABELS[tag as keyof typeof TAG_LABELS] ?? tag;
}

export function ReceivePanel({
  embedded = false,
  result,
  busy,
  fileName,
  error = null,
  decisions = {},
  decisionError = null,
  onFile,
  onAccept,
  onReject,
  onExport,
  onBack,
}: ReceivePanelProps): JSX.Element {
  const inputRef = useRef<HTMLInputElement | null>(null);

  const pickAnother = (): void => {
    inputRef.current?.click();
  };

  const fileInput = (
    <input
      ref={inputRef}
      type="file"
      accept=".trailshare,application/json"
      className="visually-hidden"
      onChange={(event) => {
        const file = event.target.files?.[0];
        // 同一个文件连续选两次也要触发（清空 value，否则 onChange 不再触发）
        event.target.value = '';
        if (file) onFile(file);
      }}
    />
  );

  /* ── 还没选文件：只给一个入口 ─────────────────────────────── */
  if (result === null) {
    return (
      <div className="panel receive">
        <header className="panel-head">
          <div>
            <h1>打开别人的信物</h1>
            <p className="lead">
              选择一个 <code>.trailshare</code> 文件。它会在你的浏览器里、用你的身份解开，
              不会上传到任何服务器。
            </p>
          </div>
          <button type="button" className="btn btn-ghost" onClick={onBack}>
            返回
          </button>
        </header>

        <div className="receive-drop" onDragOver={(event) => event.preventDefault()}>
          <p>把 .trailshare 文件拖到这里，或</p>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={pickAnother}>
            {busy ? '正在解开…' : '选择文件'}
          </button>
          {fileName ? <p className="hint">当前文件：{fileName}</p> : null}
          {fileInput}
        </div>
      </div>
    );
  }

  /* ── 失败 ─────────────────────────────────────────────────── */
  if (!result.ok) {
    return (
      <div className="panel receive">
        <header className="panel-head">
          <div>
            <h1>打不开这个信物</h1>
            <p className="lead">{result.reason}</p>
            {fileName ? <p className="hint">文件：{fileName}</p> : null}
          </div>
          <button type="button" className="btn btn-ghost" onClick={onBack}>
            返回
          </button>
        </header>

        <p className="hint hint-warn">{FAILURE_HINTS[result.kind] ?? FAILURE_HINTS.invalid}</p>
        {error ? <p className="error">读取时出错：{error}</p> : null}

        <footer className="panel-foot">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={pickAnother}>
            {busy ? '正在读取…' : '重新选择文件'}
          </button>
        </footer>
        {fileInput}
      </div>
    );
  }

  /* ── 成功 ─────────────────────────────────────────────────── */
  const places = placesOfResult(result);
  const fromName = result.from.displayName || '（未署名）';
  const warn = result.report.signatureOk === false || result.report.commitmentOk === false;
  const acceptedCount = places.filter((place) => decisions[place.placeId] === 'accepted').length;
  // 校验没过不给"接受"：收下的地点会被导进主应用，签不过的内容不该进门。
  const canDecide = result.canAccept;
  const exportLabel = embedded ? '导回主应用' : '下载 journey.json';

  return (
    <div className="panel receive receive-ok">
      <header className="panel-head">
        <div>
          <h1>来自 {fromName} 的信物</h1>
          <p className="lead">
            <code>ed25519:{shortPubkey(result.from.pubkey)}…</code>
            <span className="hint"> · {placeCountOfResult(result)} 个地点</span>
          </p>
        </div>
        <button type="button" className="btn btn-ghost" onClick={onBack}>
          返回
        </button>
      </header>

      <ul className="receive-checks">
        <li>
          <span className="receive-check-label">签名</span>
          <span className={stateClass(result.report.signatureOk)}>
            {stateLabel(result.report.signatureOk)}
          </span>
        </li>
        <li>
          <span className="receive-check-label">承诺值</span>
          <span className={stateClass(result.report.commitmentOk)}>
            {stateLabel(result.report.commitmentOk)}
          </span>
        </li>
        <li>
          <span className="receive-check-label">本地哈希链</span>
          <span className={stateClass(result.report.chainOk)}>{stateLabel(result.report.chainOk)}</span>
          <span className="hint">{result.report.chainNote}</span>
        </li>
      </ul>

      {warn ? (
        <p className="error">
          这个信物的签名或承诺值校验未通过 —— 内容可能被改动过。请让发送方当面确认后再看。
        </p>
      ) : null}
      {!result.canAccept ? (
        <p className="hint hint-warn">校验未全部通过，本页只做展示，不提供「收下」操作。</p>
      ) : null}

      {/* W15-4：逐个地点决定收下 / 拒绝（决定只写本机，不发网络请求） */}
      <section className="receive-decisions" aria-label="地点清单">
        <header className="receive-decisions-head">
          <h2>地点清单</h2>
          <p className="hint">
            已接受 {acceptedCount} 个地点（共 {places.length} 个）
          </p>
        </header>

        {places.length === 0 ? (
          <p className="hint">这个信物里还没有地点</p>
        ) : (
          <ul className="receive-decision-list">
            {places.map((place) => {
              const decision = decisions[place.placeId];
              const rowClass = [
                'receive-decision-row',
                decision === 'accepted' ? 'is-accepted' : '',
                decision === 'rejected' ? 'is-rejected' : '',
              ]
                .filter((part) => part.length > 0)
                .join(' ');
              return (
                <li key={place.placeId} className={rowClass}>
                  <span className="receive-decision-date">{place.date}</span>
                  <span className="receive-decision-name">{place.name}</span>
                  <span className="receive-decision-tag">{tagLabel(place.tag)}</span>
                  {decision === 'accepted' ? (
                    <span className="badge badge-ok">已接受</span>
                  ) : decision === 'rejected' ? (
                    <span className="badge badge-dim">已拒绝</span>
                  ) : (
                    <span className="receive-decision-buttons">
                      <button
                        type="button"
                        className="btn btn-primary"
                        disabled={!canDecide || busy}
                        onClick={() => onAccept(place)}
                      >
                        接受这个地点
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        disabled={!canDecide || busy}
                        onClick={() => onReject(place)}
                      >
                        拒绝
                      </button>
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}

        {decisionError ? <p className="error">保存决定时出错：{decisionError}</p> : null}

        <div className="receive-actions">
          <button
            type="button"
            className="btn btn-primary"
            disabled={acceptedCount === 0}
            onClick={onExport}
          >
            {exportLabel}
          </button>
          {acceptedCount === 0 ? (
            <span className="hint">
              先接受至少一个地点，才能{embedded ? '导回主应用' : '下载 journey.json'}
            </span>
          ) : null}
        </div>
      </section>

      <PlaceList places={places} emptyHint="这个信物里还没有地点" />

      {fileInput}
    </div>
  );
}
