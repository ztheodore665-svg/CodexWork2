/** 坐标与几何基础的单测。
 *
 * 这批断言由两处历史测试合并而来：
 *  - 主应用 `MapPage/coords.test.ts`：与后端逐位对齐的天安门/洛阳向量
 *  - 演示站 `lib/geo.test.ts`：往返精度、极点、单调性、haversine 对照
 *
 * **合并的意义**：两个应用从此跑同一批断言、同一份实现，
 * 不会再出现「同一个点在一个应用里偏移、在另一个应用里不偏移」。
 */

import { describe, expect, it } from 'vitest';
import { gcj02ToWgs84, outOfChina, wgs84ToGcj02 } from './coords';
import { haversine } from './distance';
import { invMercX, invMercY, mercX, mercY } from './mercator';
import { clamp } from './number';

describe('wgs84ToGcj02', () => {
  it('天安门：与后端 app/utils/coords.py 逐位一致', () => {
    // 后端实测：LngLat(lng=116.403672, lat=39.910634)
    const gcj = wgs84ToGcj02(116.397428, 39.90923);
    expect(gcj.lng).toBe(116.403672);
    expect(gcj.lat).toBe(39.910634);
  });

  it('洛阳（偏移量小一些但同样存在）', () => {
    // 后端实测：LngLat(lng=112.440498, lat=34.662648)
    const gcj = wgs84ToGcj02(112.4342, 34.6636);
    expect(gcj.lng).toBe(112.440498);
    expect(gcj.lat).toBe(34.662648);
  });

  it('境外直通，不加偏移', () => {
    const london = wgs84ToGcj02(-0.1276, 51.5072);
    expect(london.lng).toBe(-0.1276);
    expect(london.lat).toBe(51.5072);
  });

  it('偏移方向为东北、量级合理（天安门）', () => {
    const gcj = wgs84ToGcj02(116.404, 39.915);
    expect(gcj.lng).toBeGreaterThan(116.404);
    expect(gcj.lat).toBeGreaterThan(39.915);
    expect(gcj.lng - 116.404).toBeLessThan(0.02);
    expect(gcj.lat - 39.915).toBeLessThan(0.02);
    expect(gcj.lng - 116.404).toBeGreaterThan(0.001);
    expect(gcj.lat - 39.915).toBeGreaterThan(0.0005);
  });
});

describe('gcj02ToWgs84', () => {
  it('往返收敛：转过去再转回来，误差 < 1e-6', () => {
    const samples: Array<[number, number]> = [
      [116.397428, 39.90923], // 北京
      [121.4737, 31.2304], // 上海
      [113.2644, 23.1291], // 广州
      [87.6168, 43.8256], // 乌鲁木齐
      [126.5349, 45.8038], // 哈尔滨
      [102.7123, 25.0406], // 昆明
      [91.1322, 29.6604], // 拉萨
      [100.5, 36.5333], // 青海湖
    ];
    for (const [lng, lat] of samples) {
      const gcj = wgs84ToGcj02(lng, lat);
      // 先确认真偏了（几百米），再确认能转回来
      expect(Math.abs(gcj.lng - lng)).toBeGreaterThan(1e-4);
      const back = gcj02ToWgs84(gcj.lng, gcj.lat);
      expect(Math.abs(back.lng - lng)).toBeLessThan(1e-6);
      expect(Math.abs(back.lat - lat)).toBeLessThan(1e-6);
    }
  });

  it('境外同样直通', () => {
    const back = gcj02ToWgs84(-0.1276, 51.5072);
    expect(back.lng).toBe(-0.1276);
    expect(back.lat).toBe(51.5072);
  });

  it('极点附近（lat 85 / lng 179）不产生 NaN，且原样返回', () => {
    const nearPole = wgs84ToGcj02(179, 85);
    expect(Number.isFinite(nearPole.lng)).toBe(true);
    expect(Number.isFinite(nearPole.lat)).toBe(true);
    expect(nearPole.lng).toBe(179);
    expect(nearPole.lat).toBe(85);
  });
});

describe('outOfChina 判定框', () => {
  it('使用标准框（72.004~137.8347 / 0.8293~55.8271），不是收窄版', () => {
    // 这四个点都在「收窄版」(73.66~135.05 / 3.86~53.55) 之外、
    // 但仍在标准框之内 —— 收窄版会误判成境外、不做偏移。
    expect(outOfChina(136.0, 50.0)).toBe(false); // 黑龙江北部
    expect(outOfChina(73.0, 40.0)).toBe(false); // 新疆西部
    expect(outOfChina(110.0, 54.0)).toBe(false); // 内蒙古北部
    expect(outOfChina(110.0, 2.0)).toBe(false); // 南海
    // 框外
    expect(outOfChina(140.0, 40.0)).toBe(true); // 日本以东
    expect(outOfChina(100.0, 60.0)).toBe(true); // 俄罗斯
  });
});

describe('墨卡托', () => {
  it('赤道为 0.5，南北对称且北小南大', () => {
    expect(mercY(0)).toBeCloseTo(0.5, 10);
    expect(mercY(40)).toBeLessThan(0.5);
    expect(mercY(-40)).toBeGreaterThan(0.5);
    expect(mercX(0)).toBeCloseTo(0.5, 10);
    expect(mercX(-180)).toBeCloseTo(0, 10);
    expect(mercX(180)).toBeCloseTo(1, 10);
  });

  it('mercY 严格单调递减', () => {
    const latitudes = [-85, -60, -30, 0, 30, 60, 85];
    for (let i = 1; i < latitudes.length; i += 1) {
      expect(mercY(latitudes[i])).toBeLessThan(mercY(latitudes[i - 1]));
    }
  });

  it('正反变换互为逆运算', () => {
    for (const lng of [-179, -90, 0, 45, 179]) {
      expect(invMercX(mercX(lng))).toBeCloseTo(lng, 10);
    }
    for (const lat of [-80, -30, 0, 30, 80]) {
      expect(invMercY(mercY(lat))).toBeCloseTo(lat, 8);
    }
  });

  it('极点附近不产生 NaN', () => {
    expect(Number.isFinite(mercY(85))).toBe(true);
    expect(Number.isFinite(mercY(-85))).toBe(true);
    expect(Number.isFinite(mercX(179))).toBe(true);
  });
});

describe('haversine', () => {
  it('北京 → 上海 ≈ 1067km ± 20km', () => {
    const km = haversine(39.9042, 116.4074, 31.2304, 121.4737) / 1000;
    expect(km).toBeGreaterThan(1047);
    expect(km).toBeLessThan(1087);
  });

  it('同一点距离为 0，对跖点约为半个地球周长', () => {
    expect(haversine(31.23, 121.47, 31.23, 121.47)).toBe(0);
    const antipodal = haversine(0, 0, 0, 180) / 1000;
    expect(antipodal).toBeGreaterThan(19900);
    expect(antipodal).toBeLessThan(20100);
  });

  it('必须用球面距离而不是墨卡托平面距离：纬度 60° 的 1° 经度只有赤道的一半', () => {
    // 若有人把里程改成墨卡托平面距离，这条会立刻红
    const equator = haversine(0, 0, 0, 1) / 1000;
    const at60 = haversine(60, 0, 60, 1) / 1000;
    expect(equator).toBeGreaterThan(110);
    expect(equator).toBeLessThan(112);
    expect(at60 / equator).toBeGreaterThan(0.49);
    expect(at60 / equator).toBeLessThan(0.51);
  });
});

describe('clamp', () => {
  it('常规夹取', () => {
    expect(clamp(5, 0, 10)).toBe(5);
    expect(clamp(-5, 0, 10)).toBe(0);
    expect(clamp(15, 0, 10)).toBe(10);
  });

  it('NaN 被兜到下限，不会原样穿透', () => {
    // NaN 的两次比较都是 false，不显式兜住就会一路污染到相机
    expect(clamp(Number.NaN, 0, 10)).toBe(0);
    expect(clamp(Number.POSITIVE_INFINITY, 0, 10)).toBe(0);
    expect(clamp(Number.NEGATIVE_INFINITY, 0, 10)).toBe(0);
  });
});
