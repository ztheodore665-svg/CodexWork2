/**
 * 阻尼跟随相机。
 *
 * 用指数衰减系数 k = 1 - exp(-dt / tau) 而不是固定系数（如 0.1）：
 * 固定系数下帧率一变手感就变，指数形式与帧率无关。
 * 匀速前进时相机稳定地略微落后于光点，光点自然浮在中心偏前——这就是"活"的手感。
 */

/**
 * 默认阻尼时间常数（秒）。滞后量 ≈ `DEFAULT_TAU × 屏幕速度`。
 * 倍速播放时由调用方按 `DEFAULT_TAU / max(1, speed)` 缩放，避免 4x 下光点被甩出中心。
 */
export const DEFAULT_TAU = 0.22;

export class Camera {
  /** WGS84 */
  lng = 0;
  lat = 0;
  initialized = false;

  reset(lng: number, lat: number): void {
    this.lng = lng;
    this.lat = lat;
    this.initialized = true;
  }

  /** 跟随目标点。dtMs 为上一帧到这一帧的毫秒数。 */
  follow(targetLng: number, targetLat: number, dtMs: number, tau = DEFAULT_TAU): void {
    if (!this.initialized) {
      this.reset(targetLng, targetLat);
      return;
    }
    const dt = Math.min(Math.max(dtMs, 0), 100) / 1000;
    const k = 1 - Math.exp(-dt / tau);
    this.lng += (targetLng - this.lng) * k;
    this.lat += (targetLat - this.lat) * k;
  }
}
