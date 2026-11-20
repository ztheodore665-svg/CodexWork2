/** 轨迹回放的开关。
 *
 * 默认关闭：回放要加载高德底图、跑一个 60fps 的主循环，属于"重"功能；
 * 灰度期用环境变量控制，避免未验收的入口在正式环境里被误触。
 *
 * 打开方式：`frontend/.env.local` 里加 `VITE_TRAIL_REPLAY=1`。
 *
 * **它是编译期常量**（Vite 会把 `import.meta.env.VITE_TRAIL_REPLAY === '1'` 直接内联成
 * `true`/`false`），所以关闭时「行程卡片上的回放入口」这个分支会被 minifier 消掉；
 * 但 `ReplayStage` 仍会被 `MapPage` 静态 import 进产物 —— 只是永远不会被打开。
 * 要让它彻底不进包，得改成 `React.lazy` 动态 import（本期未做，代价是一个 Suspense 边界）。
 */
export const TRAIL_REPLAY_ENABLED = import.meta.env.VITE_TRAIL_REPLAY === '1';
