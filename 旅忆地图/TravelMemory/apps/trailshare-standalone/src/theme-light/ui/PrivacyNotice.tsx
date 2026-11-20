/** 「你的数据不会离开这台设备」——零后端承诺的用户可见部分（§8.2 / §8.5）。
 *
 * ## 两套文案（W12-4 之后必须分开）
 *
 * | 入口 | 承诺 | 文案 |
 * | --- | --- | --- |
 * | standalone（`index.html`） | **全程零网络请求** | 拔掉网线，整条流程照样走完 |
 * | embed（`embed.html`） | **除地图底图外零网络请求** | 除了地图底图，没有其他网络请求 |
 *
 * 二者不能混用：embed 会去高德拉瓦片，说"全程零网络"就是撒谎；而 standalone 说
 * "除了地图底图"会白白削弱自己最硬的证据（它的 CSP 里连地图域名都没有）。
 *
 * 语义不许改写，也不许折叠进"了解更多"（首屏默认展开）。
 */

export interface PrivacyNoticeProps {
  /** 是否在主应用里嵌入（= 可能加载地图底图）。 */
  embedded?: boolean;
}

export function PrivacyNotice({ embedded = false }: PrivacyNoticeProps): JSX.Element {
  /** 网络请求的口径：两个入口只有这一句不同。 */
  const networkLine = embedded ? '除了地图底图，没有其他网络请求' : '全程零网络请求';

  return (
    <section className="privacy" aria-labelledby="privacy-title">
      <h2 id="privacy-title">你的数据不会离开这台设备</h2>
      <ul className="privacy-list">
        <li>照片只在你本机处理，不上传、不备份、不发往任何服务器。</li>
        <li>
          分享包（.trailshare）直接下载到你的设备，由你决定发给谁 —— 我们不代发、不留副本。
        </li>
        <li>
          <strong>你可以自己验证</strong>：拔掉网线试试——
          <strong>{embedded ? '除了地图底图，整条流程照常工作' : '全程零网络请求，整条流程照常工作'}</strong>
          。
        </li>
        <li>
          <strong>你会看到什么</strong>：按 F12 打开开发者工具 → Network 面板 → 操作一遍，
          {embedded
            ? '除了本页的静态文件与地图底图瓦片，没有任何请求发出。'
            : '除了本页的静态文件，没有任何请求发出。'}
        </li>
      </ul>
      {embedded ? (
        <p className="privacy-foot">
          当前是在主应用里打开：照片由主应用通过消息传进来，本页没有访问你的相册。
          生成的文件会交回主应用处理。
        </p>
      ) : null}
      <details className="privacy-how">
        <summary>怎么自己验证</summary>
        <ol>
          <li>按 F12（Mac 按 Cmd+Option+I）打开开发者工具</li>
          <li>切换到 Network 面板</li>
          <li>勾选 “Preserve log”（保留日志）</li>
          <li>操作一遍：选照片 → 填写旅程 → 生成 → 下载</li>
          <li>
            你会看到：
            {embedded
              ? '除了本页的静态文件与地图底图瓦片，没有任何请求发出'
              : '除了本页的静态文件，没有任何请求发出'}
          </li>
        </ol>
      </details>
      <p className="hint">{networkLine}。</p>
    </section>
  );
}
