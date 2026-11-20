/** 首屏（Landing）—— 深色版（阶段 13 / W13-4：从 `theme-light/ui/Landing` 复制后裁剪）。
 *
 * 必须一眼看到：从哪导入照片、当前身份是临时的、数据不出本机。
 * **不显示「从主应用相册选」**：深色版是独立打开的页面，没有父窗口给它注入照片，
 * 摆一个永远点不动的按钮只会让人以为"功能坏了"（浅色版则必须置灰保留，见那边文件头）。
 *
 * ## W12-5：这一页现在有两个方向
 *
 * - **发**：选照片 → 编辑 → 生成（原来那条路）；
 * - **收**：打开别人的 `.trailshare`（新增）。
 *
 * 收件人入口放在同一屏而不是藏进菜单：信物是"发"和"收"两半，只做一个方向的产品
 * 等于只做了一半。拖拽区与文件选择框**并存** —— 拖拽在桌面端最快，文件框在移动端
 * 是唯一可行的方式。
 */

import { useRef, useState } from 'react';

import { shortPubkey, type IdentitySource, type LocalIdentity } from '../../core/identity';
import { PrivacyNotice } from './PrivacyNotice';

export interface LandingProps {
  identity: LocalIdentity | null;
  /** 身份来源（§10.2）：注入 / 本机 / 内存。 */
  identitySource: IdentitySource;
  /** 注入失败或缺失时的警告文案。 */
  identityNote: string | null;
  busy: boolean;
  error: string | null;
  onPickSystem: () => void;
  /** W12-5：用户选了一个 `.trailshare` 文件（发件箱方向）。 */
  onReceiveFile: (file: File) => void;
}

/** 只认 `.trailshare`（以及裸 JSON，方便用户从聊天工具里改名/直传的文件）。 */
function looksLikeShareFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return name.endsWith('.trailshare') || name.endsWith('.json');
}

export function Landing(props: LandingProps): JSX.Element {
  const { identity, identitySource, identityNote, busy, error } = props;
  // 深色版拿不到注入身份（没有桥），`injected` 恒为 false；保留判断是为了与浅色版同形。
  const injected = identitySource === 'injected';
  const inputRef = useRef<HTMLInputElement | null>(null);
  /** 拖拽悬停态：只影响视觉，不影响逻辑。 */
  const [dragOver, setDragOver] = useState(false);
  /** 用户拖了一个不像信物的东西进来时给一句提示（不 alert）。 */
  const [dropNote, setDropNote] = useState<string | null>(null);

  const takeFile = (file: File | undefined | null): void => {
    if (!file) return;
    if (!looksLikeShareFile(file)) {
      setDropNote('只支持 .trailshare 文件（或它的 JSON 内容）');
      return;
    }
    setDropNote(null);
    props.onReceiveFile(file);
  };

  return (
    <div className="landing">
      <header className="landing-head">
        <div className="brand">TRAILSHARE</div>
        <h1>把一段旅程封成一枚信物，只给指定的人看</h1>
        <p className="lead">
          选几张照片、写下日期与地点，这里会生成一个 <code>.trailshare</code> 文件：
          内容加密，只有你填的收件人（和你自己）能打开。没有账号、没有服务器，
          关掉页面数据就没了。
        </p>
      </header>

      <div className="landing-actions">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={props.onPickSystem}>
          {busy ? '正在读取照片…' : '从系统相册导入'}
        </button>
      </div>

      {error ? <p className="error">{error}</p> : null}

      {/* W12-5：收件人方向。拖拽区与文件选择框并存：桌面拖、移动端点。 */}
      <section className="receive-entry" aria-labelledby="receive-entry-title">
        <h2 id="receive-entry-title">收到别人的信物？</h2>
        <div
          className={dragOver ? 'receive-drop is-over' : 'receive-drop'}
          onDragOver={(event) => {
            event.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragOver(false);
            takeFile(event.dataTransfer?.files?.[0] ?? null);
          }}
        >
          <p>把 <code>.trailshare</code> 文件拖到这里，或</p>
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
          >
            打开别人的信物
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".trailshare,application/json"
            className="visually-hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              // 同一个文件选两次也要重新触发（不清空 value 就不会再触发 change）
              event.target.value = '';
              takeFile(file ?? null);
            }}
          />
        </div>
        {dropNote ? <p className="hint hint-warn">{dropNote}</p> : null}
      </section>

      <div className="identity">
        {identity ? (
          <>
            <span className="identity-badge">
              {injected ? '使用主应用身份（仅内存，不落盘）' : '临时身份 · 仅本机有效'}
            </span>
            {/* 注入身份不露公钥：露了会让收件人误以为是主应用长期身份。 */}
            {injected ? null : <code className="identity-key">ed25519:{shortPubkey(identity.pubkey)}…</code>}
            <span className="identity-note">
              {injected
                ? '这个身份由主应用通过消息注入，只存在于本页内存里；本页不会把它写进任何存储。'
                : identity.persistent
                  ? '存在本浏览器的 IndexedDB 里；清掉站点数据就没了，无法找回。收件人要靠它认出这个包是你发的。'
                  : '当前浏览器不允许本地存储，身份只存在于这个标签页，刷新即变。'}
            </span>
          </>
        ) : (
          <span className="identity-note">正在准备临时身份…</span>
        )}
        {identityNote ? <span className="identity-note identity-warn">{identityNote}</span> : null}
      </div>

      <PrivacyNotice />
    </div>
  );
}
