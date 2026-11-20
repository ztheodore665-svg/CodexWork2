/** 数值夹取。 */

/**
 * 把 value 夹到 [min, max]。
 *
 * ⚠️ **NaN 必须显式兜住**：`NaN < min` 和 `NaN > max` 都是 false，
 * 不兜的话 NaN 会原样穿过夹取，一路污染到相机与整帧渲染，
 * 表现为画面全黑且无法自愈（只能刷新页面）。
 */
export function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  if (value < min) return min;
  if (value > max) return max;
  return value;
}
