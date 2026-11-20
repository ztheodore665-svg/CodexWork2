/** WGS84 ↔ GCJ-02 坐标转换（纯函数，无 IO，可单测）。
 *
 * ## 这里为什么是「唯一真相」
 *
 * 这套转换原本在仓库里存在三份：后端 `app/utils/coords.py`（权威）、
 * 主应用 `MapPage/coords.ts`、演示站 `demo/lib/geo.ts`。前两份是逐行同构移植，
 * 第三份抄的时候把「境外判定框」抄窄了（73.66\~135.05 / 3.86\~53.55，应为
 * 72.004\~137.8347 / 0.8293\~55.8271）—— 于是黑龙江东部、漠河一带的照片
 * 在两个应用里会落到不同位置。
 *
 * 合并后本模块是前端唯一的转换入口，判定框取标准值（与后端一致）。
 * 业务代码不许自己加偏移常数。
 *
 * ## 红线（PROJECT.md §二、[2]）
 *
 * **存储一律 WGS84，展示一律 GCJ-02。**
 */

/** 经纬度对。**字段顺序刻意是 (lng, lat)**：与 GeoJSON / 高德 API 一致。 */
export interface LngLat {
  lng: number;
  lat: number;
}

/** 克拉索夫斯基椭球参数（GCJ-02 使用的基准）。与后端保持一致。 */
const A = 6378245.0;
const EE = 0.00669342162296594323;
const PI = Math.PI;

/** 境外不做偏移（GCJ-02 只在中国境内有偏移）。
 *
 *  判定框取业界标准值，与后端 `app/utils/coords.py` 一致。
 *  **不要收窄它**：收窄会把「境内在框外」的点误判成境外，表现为边远地区坐标不偏移。
 */
export function outOfChina(lng: number, lat: number): boolean {
  return !(lng >= 72.004 && lng <= 137.8347 && lat >= 0.8293 && lat <= 55.8271);
}

function transformLat(x: number, y: number): number {
  let ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
  ret += ((20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0) / 3.0;
  ret += ((20.0 * Math.sin(y * PI) + 40.0 * Math.sin(y / 3.0 * PI)) * 2.0) / 3.0;
  ret += ((160.0 * Math.sin(y / 12.0 * PI) + 320.0 * Math.sin(y * PI / 30.0)) * 2.0) / 3.0;
  return ret;
}

function transformLng(x: number, y: number): number {
  let ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
  ret += ((20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0) / 3.0;
  ret += ((20.0 * Math.sin(x * PI) + 40.0 * Math.sin(x / 3.0 * PI)) * 2.0) / 3.0;
  ret += ((150.0 * Math.sin(x / 12.0 * PI) + 300.0 * Math.sin(x / 30.0 * PI)) * 2.0) / 3.0;
  return ret;
}

/** WGS84 → GCJ-02（存储坐标 → 地图展示坐标）。 */
export function wgs84ToGcj02(lng: number, lat: number): LngLat {
  if (outOfChina(lng, lat)) {
    return { lng, lat };
  }
  const dLat = transformLat(lng - 105.0, lat - 35.0);
  const dLng = transformLng(lng - 105.0, lat - 35.0);
  const radLat = (lat / 180.0) * PI;
  const sinLat = Math.sin(radLat);
  const magic = 1 - EE * sinLat * sinLat;
  const sqrtMagic = Math.sqrt(magic);

  // 与后端 `app/utils/coords.py` 的两个分母逐位对齐，改一个字符两边就会对不上
  const latOffset = (dLat * 180.0) / (((A * (1 - EE)) / (magic * sqrtMagic)) * PI);
  const lngOffset = (dLng * 180.0) / (((A / sqrtMagic) * Math.cos(radLat)) * PI);
  return { lng: round6(lng + lngOffset), lat: round6(lat + latOffset) };
}

/** GCJ-02 → WGS84（地图 API 返回的坐标 → 入库坐标）。
 *
 *  不动点迭代。实测 3 次已收敛到 1e-9 量级，再被 `round6` 收到 6 位小数，
 *  最终误差 ≤ 5e-7 度（约 5 厘米），远细于任何展示需求。
 */
export function gcj02ToWgs84(lng: number, lat: number, iterations = 3): LngLat {
  if (outOfChina(lng, lat)) {
    return { lng, lat };
  }
  let curLng = lng;
  let curLat = lat;
  for (let i = 0; i < Math.max(1, iterations); i += 1) {
    const guessed = wgs84ToGcj02(curLng, curLat);
    curLng += lng - guessed.lng;
    curLat += lat - guessed.lat;
  }
  return { lng: round6(curLng), lat: round6(curLat) };
}

/** 与后端 `round(x, 6)` 对齐：6 位小数约 0.1 米。 */
function round6(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}
