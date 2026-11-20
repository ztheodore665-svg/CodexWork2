/** 数据融合层：坐标模糊 → 打进 PlaceMeta。
 *
 * 这里**注入假的 obfuscate 函数**，不发真实网络请求：
 * 一是夜间 CI 没有后端，二是"打包是否成功"不该依赖一段网络往返，
 * 三是真机验证本来就在 README 的人工清单里，不该由单测试图代替。
 */

import { describe, expect, it } from 'vitest';

import { prepareShare, type ObfuscateFn } from './prepare';
import { unpack } from './unpack';
import type { PackInput } from './pack';
import { identityA, identityB, makeTrip, makeVisibility, NOW, SEED_B } from './fixtures';
import type { ObfuscatedPoint, ObfuscationMode, Trip } from '../../types/share';

/** 给三个地点分别设置：有坐标 / 无坐标 / 有坐标。 */
function tripWithCoords(): Trip {
  const trip = makeTrip();
  trip.days[0].places[0].lat = 35.0036; // 清水寺
  trip.days[0].places[0].lng = 135.7788;
  // p_2（锦市场）刻意不设坐标
  trip.days[1].places[0].lat = 35.017; // 岚山竹林
  trip.days[1].places[0].lng = 135.6716;
  return trip;
}

/** 假的模糊实现：把点往东北挪 0.01 度，承诺半径 500 米。 */
function fakeObfuscate(calls: Array<{ lat: number; lng: number }> = []): ObfuscateFn {
  return async (lat: number, lng: number, mode: ObfuscationMode, epsilon: number) => {
    calls.push({ lat, lng });
    const point: ObfuscatedPoint = {
      lat: lat + 0.01,
      lng: lng + 0.01,
      radius: 500,
      mode,
      epsilon,
      coord_system: 'WGS84',
    };
    return point;
  };
}

function inputFor(trip: Trip): PackInput {
  return {
    groups: [{ trip, visibility: makeVisibility(), recipientPubkeys: [identityB.pubkey] }],
    senderIdentity: identityA,
    displayName: 'A',
    now: NOW,
    shareId: 's_feedface',
    chainEnabled: false,
  };
}

describe('prepareShare：坐标写进密文', () => {
  it('有坐标的 place 被写入 lat/lng/radius', async () => {
    const calls: Array<{ lat: number; lng: number }> = [];
    const result = await prepareShare(inputFor(tripWithCoords()), { obfuscate: fakeObfuscate(calls) });

    expect(result.warnings).toEqual([]);
    const unpacked = await unpack(result.pkg, { privHex: SEED_B });
    const places = unpacked.groups[0].places;

    expect(places[0].lat).toBeCloseTo(35.0136, 5);
    expect(places[0].lng).toBeCloseTo(135.7888, 5);
    expect(places[0].radius).toBe(500);
    expect(places[2].lat).toBeCloseTo(35.027, 5);
    expect(places[2].radius).toBe(500);
  });

  it('无坐标的 place 不带这些字段（退化成规范原版 PlaceMeta）', async () => {
    const result = await prepareShare(inputFor(tripWithCoords()), { obfuscate: fakeObfuscate() });
    const unpacked = await unpack(result.pkg, { privHex: SEED_B });
    const p2 = unpacked.groups[0].places.find((p) => p.placeId === 'p_2');

    expect(p2?.lat).toBeUndefined();
    expect(p2?.lng).toBeUndefined();
    expect(p2?.radius).toBeUndefined();
    expect(p2?.name).toBe('锦市场'); // 其余字段照旧
  });

  it('只对可见的 place 调模糊：被屏蔽的地点不会出现在调用里', async () => {
    const calls: Array<{ lat: number; lng: number }> = [];
    const input = inputFor(tripWithCoords());
    input.groups[0].visibility = makeVisibility({ hiddenDayIds: ['d_2'] });

    await prepareShare(input, { obfuscate: fakeObfuscate(calls) });

    // 只应收到 p_1 一次调用（p_3 在 d_2，已被屏蔽；p_2 无坐标）
    expect(calls.length).toBe(1);
    expect(calls[0].lat).toBeCloseTo(35.0036, 5);
  });

  it('mode / epsilon 透传给后端', async () => {
    let seen: { mode: ObfuscationMode; epsilon: number } | null = null;
    const obfuscate: ObfuscateFn = async (_lat, _lng, mode, epsilon) => {
      seen = { mode, epsilon };
      return { lat: 1, lng: 2, radius: 5000, mode, epsilon, coord_system: 'WGS84' };
    };
    const result = await prepareShare(inputFor(tripWithCoords()), { obfuscate, mode: 'city', epsilon: 0.5 });
    expect(seen).toEqual({ mode: 'city', epsilon: 0.5 });

    const unpacked = await unpack(result.pkg, { privHex: SEED_B });
    expect(unpacked.groups[0].places[0].radius).toBe(5000);
  });
});

describe('prepareShare：失败必须降级（PROJECT.md §二、[8]）', () => {
  it('obfuscate 抛错时仍能打包，并返回 warnings', async () => {
    const failing: ObfuscateFn = async () => {
      throw new Error('地理模糊失败：HTTP 404');
    };
    const result = await prepareShare(inputFor(tripWithCoords()), { obfuscate: failing });

    // 打包没被拖垮
    expect(result.pkg.groups[0].payload.places.length).toBe(3);
    expect(result.warnings.length).toBe(2); // 两个有坐标的地点各一条
    expect(result.warnings[0]).toContain('清水寺');
    expect(result.warnings[0]).toContain('不写入坐标');

    // 但坐标一个都没写进去 —— 绝不退化成"发原坐标"
    const unpacked = await unpack(result.pkg, { privHex: SEED_B });
    for (const place of unpacked.groups[0].places) {
      expect(place.lat).toBeUndefined();
      expect(place.lng).toBeUndefined();
      expect(place.radius).toBeUndefined();
    }
  });

  it('部分失败：一个地点的坐标进包，另一个降级', async () => {
    const partial: ObfuscateFn = async (lat, lng, mode, epsilon) => {
      if (Math.abs(lat - 35.0036) < 1e-6) throw new Error('timeout');
      return { lat: lat + 0.01, lng: lng + 0.01, radius: 500, mode, epsilon, coord_system: 'WGS84' };
    };
    const result = await prepareShare(inputFor(tripWithCoords()), { obfuscate: partial });

    expect(result.warnings.length).toBe(1);
    const unpacked = await unpack(result.pkg, { privHex: SEED_B });
    expect(unpacked.groups[0].places[0].lat).toBeUndefined();
    expect(unpacked.groups[0].places[2].lat).toBeCloseTo(35.027, 5);
  });

  it('不注入 obfuscate = 旁路未启用：不发请求、无 warning、无坐标', async () => {
    const result = await prepareShare(inputFor(tripWithCoords()));
    expect(result.warnings).toEqual([]);
    const unpacked = await unpack(result.pkg, { privHex: SEED_B });
    expect(unpacked.groups[0].places[0].lat).toBeUndefined();
  });
});
