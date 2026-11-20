/** `embed.html` 被**直接打开**时的整页引导（任务书 §6.4）。
 *
 * ## 为什么需要一整个页面而不是一句 tooltip
 *
 * `embed.html` 是给主应用 iframe 用的**嵌入版**：它的照片来自父窗口 `postMessage`
 * 注入、它的身份也由父窗口给，产物（视频 / journey.json）默认交回主应用。
 * 用户直接在浏览器里敲开这个地址时，这些输入全都不存在 —— 只有一个
 * "从主应用相册选（未嵌入，不可用）"的灰按钮，等于让人对着一扇锁着的门。
 *
 * 所以这里给整页引导，说清两件事：
 *  1. 这个页面需要在主应用内使用（它是 iframe 视图，不是独立应用）；
 *  2. 想单独用的话，走 `index.html`（深色独立版）。
 *
 * ## 铁律：**绝不自动跳转**
 *
 * 不能调用"替换当前地址"这类浏览器导航 API（`replace` / `assign` / 直接改地址栏的 href）。
 * 自动跳转会让"我在哪 / 为什么变了"永远得不到回答，而且用户可能本来就想看看这个地址是什么。
 * 跳转必须是**用户点的链接**。这条由 `EmbedGuidance.test.tsx` 的源码断言守着
 * （组件里不许出现任何导航 API）。
 *
 * ## 样式
 *
 * 复用已有的 `.panel` / `.panel-head` / `.lead` / `.hint` / `.btn`，不新增 CSS：
 * 这些规则都在 `.theme-light` 作用域内（外层容器由 `App` 提供）。
 */

export function EmbedGuidance(): JSX.Element {
  return (
    <div className="panel">
      <header className="panel-head">
        <div>
          <h1>这个页面需要在主应用内使用。</h1>
          <p className="lead">
            <code>embed.html</code> 是给主应用 iframe 用的嵌入视图：照片与身份都由主应用
            通过消息传进来，导出结果也交回主应用处理。它不需要你直接访问。
          </p>
          <p className="hint">
            如果你想要的是<strong>独立使用</strong>（自己选照片、补位置、播放、导出视频），
            用下面这个链接。
          </p>
        </div>
      </header>

      <p>
        <a className="btn btn-primary" href="./index.html">
          独立打开轨迹演示 →
        </a>
      </p>
    </div>
  );
}
