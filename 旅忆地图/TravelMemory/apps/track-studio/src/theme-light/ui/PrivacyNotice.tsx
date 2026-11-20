/** 「你的数据不会离开这台设备」——零后端承诺的用户可见部分（§8.2 / §8.5）。
 *
 * 这段文案是产品承诺，不是营销文案：每一条都能被用户当场验证。
 * 所以**不许改写语义**，也不许把它折叠进"了解更多"里 ——
 * 首屏默认展开，`<details>` 只用来放"怎么自己验证"的操作步骤。
 */

export interface PrivacyNoticeProps {
  /** 嵌入模式下补一句"照片由主应用提供"，避免用户以为小应用读了他的相册。 */
  embedded?: boolean;
}

export function PrivacyNotice({ embedded = false }: PrivacyNoticeProps): JSX.Element {
  return (
    <section className="privacy" aria-labelledby="privacy-title">
      <h2 id="privacy-title">你的数据不会离开这台设备</h2>
      <ul className="privacy-list">
        <li>照片只在你本机处理，不上传、不备份、不发往任何服务器。</li>
        <li>视频与 journey.json 直接下载到你的设备，由你决定发给谁。</li>
        <li>
          <strong>你可以自己验证</strong>：拔掉网线试试——除了地图底图，其他功能照常工作。
        </li>
        <li>
          <strong>你会看到什么</strong>：按 F12 打开开发者工具 → Network 面板 → 操作一遍，
          除了地图瓦片，没有任何请求发出。
        </li>
      </ul>
      {embedded ? (
        <p className="privacy-foot">
          当前是在主应用里打开：照片由主应用通过消息传进来，本页没有访问你的相册。
        </p>
      ) : null}
      <details className="privacy-how">
        <summary>怎么自己验证</summary>
        <ol>
          <li>按 F12（Mac 按 Cmd+Option+I）打开开发者工具</li>
          <li>切换到 Network 面板</li>
          <li>勾选 “Preserve log”（保留日志）</li>
          <li>操作一遍：选照片 → 生成 → 导出</li>
          <li>
            你会看到：只有 <code>webapi.amap.com</code> 和 <code>*.amap.com</code> 的请求
          </li>
        </ol>
      </details>
    </section>
  );
}
