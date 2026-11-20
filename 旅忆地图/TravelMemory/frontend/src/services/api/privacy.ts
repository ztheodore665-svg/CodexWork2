/** 地理模糊的客户端（后端 `POST /api/v1/privacy/obfuscate`）。
 *
 * ## 为什么模糊要在服务端做
 *
 * 前端自己加噪也能"看起来一样"，但那样做等于把**真实坐标与拉普拉斯参数**都留在
 * 客户端，任何人打开控制台就能拿到原始点，隐私保证形同虚设。让服务端只收一个
 * 孤立点、只吐加噪点，服务端自己也无从还原轨迹 —— 这是本项目唯一能有效阻止
 * "轨迹关联"的构造。
 *
 * ## 为什么失败要抛出而不是返回原坐标
 *
 * 返回原坐标 = 静默地退化成"分享精确位置"，这是最坏的一种降级：
 * 用户以为开了模糊，实际发出去的是真实坐标，且没有任何提示。
 * 所以这里照 `agent.ts` 的写法把 HTTP 错误抛成 `Error`，由 `prepare.ts` 决定降级策略。
 */

import type { ObfuscatedPoint, ObfuscationMode } from '../../types/share';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';

/** 后端 `epsilon` 的默认取值（1.0）：与 `app/schemas/privacy.py` 的 `Field(default=1.0)` 对齐。
 *  ε 越大噪声越小（越接近原坐标）；`b = radius / ε` 是拉普拉斯尺度。 */
export const DEFAULT_EPSILON = 1.0;

/** 默认模糊粒度（≈500 米），与后端 `ObfuscateRequest.mode` 的默认值一致。 */
export const DEFAULT_MODE: ObfuscationMode = 'approx';

/** 把一个 WGS84 坐标交给后端加噪，拿回加噪点 + 承诺半径。
 *
 * 注意：后端对同一输入每次返回**不同**的点（独立抽样），重放校准不出真实位置。
 * 开关未启用时后端返回 404（不是 403）—— 未启用的能力对外表现为"不存在"。
 */
export async function obfuscateLocation(
  lat: number,
  lng: number,
  mode: ObfuscationMode = DEFAULT_MODE,
  epsilon: number = DEFAULT_EPSILON,
): Promise<ObfuscatedPoint> {
  const response = await fetch(`${API_BASE}/api/v1/privacy/obfuscate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lat, lng, mode, epsilon }),
  });

  if (!response.ok) {
    throw new Error(`地理模糊失败：HTTP ${response.status}`);
  }

  return (await response.json()) as ObfuscatedPoint;
}
