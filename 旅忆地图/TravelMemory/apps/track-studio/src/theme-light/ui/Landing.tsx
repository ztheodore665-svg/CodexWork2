/** 首屏（Landing，§7.1.1）。
 *
 * 三件事必须一眼可见：从哪导入照片、地图是否需要配置、数据不会离开本机。
 * "从主应用相册选"在非嵌入模式下**置灰而不是隐藏** —— 隐藏会让用户不知道
 * 还有这条路，置灰 + hover 提示才是可发现且诚实的做法（§9.3）。
 */

import { shortPubkey, type IdentitySource, type LocalIdentity } from '../../core/identity';
import { PrivacyNotice } from './PrivacyNotice';

export interface LandingProps {
  embedded: boolean;
  /** 主应用注入的照片数量（决定"从主应用相册选"是否可用）。 */
  libraryCount: number;
  identity: LocalIdentity | null;
  /** 身份来源（§10.2）：注入 / 本机 / 内存。 */
  identitySource: IdentitySource;
  /** 注入失败或缺失时的警告文案。 */
  identityNote: string | null;
  hasAmapKey: boolean;
  busy: boolean;
  error: string | null;
  onPickSystem: () => void;
  onPickLibrary: () => void;
  onUseDemo: () => void;
}

export function Landing(props: LandingProps): JSX.Element {
  const { embedded, libraryCount, identity, identitySource, identityNote, hasAmapKey, busy, error } =
    props;
  const libraryReady = embedded && libraryCount > 0;
  /**
   * 「从主应用相册选」的文案要分清三种情况（W14 收尾）。
   *
   * 原来只有两分支，把"嵌入了但主应用没传照片"和"根本没嵌入"混成一句
   * 「（未嵌入，不可用）」—— 用户明明是从主应用点进来的，却说没嵌入，
   * 会让人以为链接坏了。`disabled` 的口径不变（仍然 `!libraryReady || busy`），
   * 只把"为什么点不了"说准。
   */
  const libraryTitle = !embedded
    ? '请从主应用进入此功能'
    : libraryCount > 0
      ? '使用主应用注入的照片'
      : '主应用没有可传入的照片';
  const librarySuffix = !embedded
    ? '（未嵌入，不可用）'
    : libraryCount > 0
      ? `（${libraryCount} 张可用）`
      : '（主应用没有可传入的照片）';
  const injected = identitySource === 'injected';

  return (
    <div className="landing">
      <header className="landing-head">
        <div className="brand">TRACK STUDIO</div>
        <h1>把相册里的一天，走成一条会动的地图</h1>
        <p className="lead">
          选几张带位置信息的照片，这里会按拍摄时间把地点连成轨迹并播放出来。
          没有账号、没有服务器，关掉页面数据就没了。
        </p>
      </header>

      <div className="landing-actions">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={props.onPickSystem}>
          {busy ? '正在读取照片…' : '从系统相册导入'}
        </button>

        <button
          type="button"
          className="btn"
          disabled={!libraryReady || busy}
          title={libraryTitle}
          onClick={props.onPickLibrary}
        >
          从主应用相册选
          {librarySuffix}
        </button>

        {/* 阶段 13 / W13-3：这里**不再**渲染「用演示路线体验」。
            "用演示轨迹开始"是深色独立入口（theme-dark/Landing）的入口，
            嵌入版按任务书要求不显示它 —— 两个入口的入口矩阵因此互不重叠。
            `onUseDemo` 仍然保留在 props 里（App 的演示数据链路没删），
            将来若产品要求恢复，加回一个按钮即可，不需要动状态机。
            TODO(歧义): 任务书只写"不显示"，没说要不要顺手删掉 App 里的演示数据链路；
            这里按"最小改动 + 保留能力"处理。 */}
      </div>

      {!hasAmapKey ? (
        <p className="hint">
          底图需要配置地图服务；轨迹与播放不受影响（会自动切换为示意格网）。
        </p>
      ) : null}

      {error ? <p className="error">{error}</p> : null}

      <div className="identity">
        {identity ? (
          <>
            <span className="identity-badge">
              {injected ? '使用主应用身份（仅内存，不落盘）' : '临时身份 · 仅本机有效'}
            </span>
            {/* 注入身份不露公钥：露了会让用户误以为"这是主应用的身份"，
                而它其实只是一次会话内借用的密钥（§10.2 的措辞要求）。 */}
            {injected ? null : (
              <code className="identity-key">
                {identity.pubkey.startsWith('ed25519:') ? 'ed25519:' : ''}
                {shortPubkey(identity.pubkey)}…
              </code>
            )}
            <span className="identity-note">
              {injected
                ? '这个身份由主应用通过消息注入，只存在于本页内存里；本页不会把它写进任何存储。'
                : identity.persistent
                  ? '存在本浏览器的 IndexedDB 里；清掉站点数据就没了，无法找回。'
                  : '当前浏览器不允许本地存储，身份只存在于这个标签页，刷新即变。'}
            </span>
          </>
        ) : (
          <span className="identity-note">正在准备临时身份…</span>
        )}
        {identityNote ? <span className="identity-note identity-warn">{identityNote}</span> : null}
      </div>

      <PrivacyNotice embedded={embedded} />
    </div>
  );
}
