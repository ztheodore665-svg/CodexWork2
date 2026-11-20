/** 展示层格式化：日期、里程、时长。一律用 UTC 取值（EXIF 墙钟时间存在 UTC 里）。 */

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function pad2(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

/** 2026.07.12 */
export function formatDateDot(ms: number): string {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}.${pad2(d.getUTCMonth() + 1)}.${pad2(d.getUTCDate())}`;
}

/** 07.12（时间轴刻度用） */
export function formatDayMonth(ms: number): string {
  const d = new Date(ms);
  return `${pad2(d.getUTCMonth() + 1)}.${pad2(d.getUTCDate())}`;
}

/** 14:32 */
export function formatTimeHM(ms: number): string {
  const d = new Date(ms);
  return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
}

/** 周日 */
export function formatWeekday(ms: number): string {
  return WEEKDAYS[new Date(ms).getUTCDay()];
}

/** 千分位（手写，避免 toLocaleString 在不同环境的差异） */
export function groupThousands(value: number): string {
  const rounded = Math.round(Math.abs(value));
  const text = String(rounded);
  let out = '';
  for (let i = 0; i < text.length; i += 1) {
    if (i > 0 && (text.length - i) % 3 === 0) out += ',';
    out += text[i];
  }
  return value < 0 ? `-${out}` : out;
}

/** 1,284 km（不足 1km 显示米） */
export function formatKm(meters: number): string {
  if (!Number.isFinite(meters)) return '0 km';
  if (meters < 1000) return `${Math.round(meters)} m`;
  const km = meters / 1000;
  if (km < 10) return `${km.toFixed(1)} km`;
  return `${groupThousands(km)} km`;
}

/** 纯数字里程（HUD 里字号固定，单位单独排） */
export function formatKmNumber(meters: number): string {
  const km = Math.max(0, meters) / 1000;
  return groupThousands(km);
}

/** 8 小时 20 分 */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '0 分钟';
  const totalMinutes = Math.round(ms / 60000);
  const days = Math.floor(totalMinutes / (60 * 24));
  const hours = Math.floor((totalMinutes - days * 60 * 24) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `${days} 天 ${hours} 小时`;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  return `${minutes} 分钟`;
}

/** 0..1 → "62%" */
export function formatPercent(ratio: number): string {
  const clamped = Math.max(0, Math.min(1, ratio));
  return `${Math.round(clamped * 100)}%`;
}
