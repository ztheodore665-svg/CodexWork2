import { describe, expect, it } from 'vitest';
import { buildJourney, samplePose, createPose } from './journey';
import type { Photo } from './types';

function photo(id: string, lat: number | null, lng: number | null, takenAt: number, place: string): Photo {
  return {
    id,
    lat,
    lng,
    takenAt,
    placeName: place,
    locationSource: lat === null ? 'none' : 'exif',
    gradient: 'linear-gradient(135deg,#0f2027,#203a43)',
  };
}

const day1 = Date.UTC(2026, 6, 12, 8, 0, 0);

describe('buildJourney', () => {
  it('2 张照片能生成轨迹且里程 > 0', () => {
    const journey = buildJourney({
      id: 't1',
      title: 't',
      subtitle: 's',
      photos: [photo('a', 36.6171, 101.7782, day1, '西宁'), photo('b', 36.5333, 100.5, day1 + 3600_000, '青海湖')],
    });
    expect(journey.track.length).toBeGreaterThanOrEqual(400);
    expect(journey.totalMeters).toBeGreaterThan(0);
    expect(journey.photos).toHaveLength(2);
    // 青海湖到西宁直线约 120km，轨迹里程应是同一量级
    expect(journey.totalMeters / 1000).toBeGreaterThan(80);
    expect(journey.totalMeters / 1000).toBeLessThan(200);
  });

  it('只有 1 张已定位照片时 track 为空', () => {
    const journey = buildJourney({
      id: 't2',
      title: 't',
      subtitle: 's',
      photos: [photo('a', 36.6171, 101.7782, day1, '西宁'), photo('b', null, null, day1 + 1000, '未知')],
    });
    expect(journey.track).toHaveLength(0);
    expect(journey.totalMeters).toBe(0);
    expect(journey.photos).toHaveLength(1);
  });

  it('按 UTC 日期分组：跨两天 → 2 个 day', () => {
    const journey = buildJourney({
      id: 't3',
      title: 't',
      subtitle: 's',
      photos: [
        photo('a', 31.24, 121.49, day1, '外滩'),
        photo('b', 31.3239, 120.6294, day1 + 8 * 3600_000, '拙政园'),
        photo('c', 30.245, 120.15, day1 + 30 * 3600_000, '西湖'),
      ],
    });
    expect(journey.days).toHaveLength(2);
    expect(journey.days[0].startS).toBe(0);
    expect(journey.days[1].endS).toBeCloseTo(journey.totalMeters, 5);
    expect(journey.days[0].index).toBe(0);
    expect(journey.days[1].index).toBe(1);
  });

  /**
   * 注意（P0 复盘）：这条原先写的是 `suggestedZoom ∈ [7.5, 13]`，**是恒真的** ——
   * 它把实现的 clamp 区间直接当成了验收区间：
   *   · 上界 13 由 `clamp(zoom, 7.5, 13)` 数学保证，永不越界；
   *   · 原夹具（西宁/茶卡/敦煌，站距中位 ≈365km）算出来 ≈7.44，被夹到 **7.5**，
   *     正好等于下界 —— 双端都恒真，对「顶到 clamp」这个失效模式完全免疫。
   * 这里做了两处修正：
   *   1. **换夹具**：本用例的行程跨度要落在区间内部，不能自己就贴着 clamp 边界；
   *   2. **改严格不等号**：把 7.5 / 13 这两个 clamp 端点排除在外。
   * 真正锁机制的是文件末尾那三条（近似重复照片不变性 / 站距单调性 / 0.5 量化）。
   */
  it('suggestedZoom 落在区间内部（不含 clamp 端点）且是 0.5 的整数倍', () => {
    // 上海 → 苏州 → 杭州：跨度约 85km / 128km，中位 ≈107km，算出来约 9.5，
    // 稳稳落在 (7.5, 13) 内部，不会自己撞到 clamp 边界。
    const journey = buildJourney({
      id: 't4',
      title: 't',
      subtitle: 's',
      photos: [
        photo('a', 31.24, 121.49, day1, '外滩'),
        photo('b', 31.3239, 120.6294, day1 + 3600_000, '拙政园'),
        photo('c', 30.245, 120.15, day1 + 7200_000, '西湖'),
      ],
    });
    expect(journey.suggestedZoom).toBeGreaterThan(7.5);
    expect(journey.suggestedZoom).toBeLessThan(13);
    expect(Math.abs(journey.suggestedZoom * 2 - Math.round(journey.suggestedZoom * 2))).toBeLessThan(1e-9);
  });

  it('samplePose 在两端与中间都稳定', () => {
    const journey = buildJourney({
      id: 't5',
      title: 't',
      subtitle: 's',
      photos: [photo('a', 36.6171, 101.7782, day1, '西宁'), photo('b', 36.5333, 100.5, day1 + 3600_000, '青海湖')],
    });
    const pose = createPose();

    samplePose(journey, 0, pose);
    expect(pose.s).toBeCloseTo(0, 6);
    expect(pose.lat).toBeCloseTo(36.6171, 2);

    samplePose(journey, journey.totalMeters, pose);
    expect(pose.lat).toBeCloseTo(36.5333, 2);
    expect(pose.lng).toBeCloseTo(100.5, 2);

    samplePose(journey, journey.totalMeters / 2, pose);
    expect(pose.s).toBeCloseTo(journey.totalMeters / 2, 3);
    expect(Number.isFinite(pose.mx)).toBe(true);
    expect(Number.isFinite(pose.t)).toBe(true);
  });

  it('未定位照片不进轨迹但保留顺序', () => {
    const journey = buildJourney({
      id: 't6',
      title: 't',
      subtitle: 's',
      photos: [
        photo('c', 30.245, 120.15, day1 + 7200_000, '西湖'),
        photo('a', 31.24, 121.49, day1, '外滩'),
        photo('x', null, null, day1 + 1000, '未知'),
        photo('b', 31.3239, 120.6294, day1 + 3600_000, '拙政园'),
      ],
    });
    expect(journey.photos.map((p) => p.id)).toEqual(['a', 'b', 'c']);
  });
});

/* ══════════════════════════════════════════════════════════════
 * QA 补充用例：退化输入 / 乱序 / samplePose 边界
 * ══════════════════════════════════════════════════════════════ */

describe('buildJourney 退化输入', () => {
  it('两张照片坐标完全相同：不崩、不产生 NaN，里程为 0', () => {
    const same = { lat: 30.245, lng: 120.15 };
    const journey = buildJourney({
      id: 'dup',
      title: 't',
      subtitle: 's',
      photos: [
        photo('a', same.lat, same.lng, day1, '西湖'),
        photo('b', same.lat, same.lng, day1 + 60_000, '西湖'),
      ],
    });

    expect(journey.photos).toHaveLength(2);
    expect(journey.track.length).toBeGreaterThan(0);
    expect(Number.isFinite(journey.totalMeters)).toBe(true);
    expect(journey.totalMeters).toBeLessThan(1);
    expect(journey.totalMeters).toBeGreaterThanOrEqual(0);

    for (const sample of journey.track) {
      expect(Number.isFinite(sample.mx)).toBe(true);
      expect(Number.isFinite(sample.my)).toBe(true);
      expect(Number.isFinite(sample.s)).toBe(true);
      expect(Number.isFinite(sample.t)).toBe(true);
    }

    const pose = createPose();
    for (const s of [0, journey.totalMeters, journey.totalMeters / 2]) {
      samplePose(journey, s, pose);
      expect(Number.isFinite(pose.lat)).toBe(true);
      expect(Number.isFinite(pose.lng)).toBe(true);
      expect(Number.isFinite(pose.mx)).toBe(true);
      expect(Number.isFinite(pose.my)).toBe(true);
      expect(Number.isFinite(pose.t)).toBe(true);
    }
  });

  it('照片时间乱序输入：内部按 takenAt 升序，轨迹跟着时间走', () => {
    const journey = buildJourney({
      id: 'shuffle',
      title: 't',
      subtitle: 's',
      photos: [
        photo('c', 30.245, 120.15, day1 + 7200_000, '西湖'),
        photo('a', 31.24, 121.49, day1, '外滩'),
        photo('b', 31.3239, 120.6294, day1 + 3600_000, '拙政园'),
      ],
    });

    expect(journey.photos.map((p) => p.id)).toEqual(['a', 'b', 'c']);
    for (let i = 1; i < journey.photos.length; i += 1) {
      expect(journey.photos[i].takenAt).toBeGreaterThanOrEqual(journey.photos[i - 1].takenAt);
    }
    for (let i = 1; i < journey.track.length; i += 1) {
      expect(journey.track[i].t).toBeGreaterThanOrEqual(journey.track[i - 1].t);
      expect(journey.track[i].s).toBeGreaterThanOrEqual(journey.track[i - 1].s);
    }
    expect(journey.track[0].t).toBe(day1);
    expect(journey.track[journey.track.length - 1].t).toBe(day1 + 7200_000);
  });

  it('只有 2 张照片也能生成足够密的采样点（>= 20）', () => {
    const journey = buildJourney({
      id: 'two',
      title: 't',
      subtitle: 's',
      photos: [
        photo('a', 36.6171, 101.7782, day1, '西宁'),
        photo('b', 36.5333, 100.5, day1 + 3600_000, '青海湖'),
      ],
    });
    expect(journey.track.length).toBeGreaterThanOrEqual(20);
    expect(journey.days).toHaveLength(1);
  });

  it('里程用真实经纬度：高纬度两点的里程不因墨卡托拉伸而虚高', () => {
    // 纬度 60° 上经度相差 1° 的实际距离约 cos(60°) ≈ 0.5 倍赤道值
    const equator = buildJourney({
      id: 'eq',
      title: 't',
      subtitle: 's',
      photos: [photo('a', 0, 0, day1, ''), photo('b', 0, 1, day1 + 60_000, '')],
    });
    const highLat = buildJourney({
      id: 'hi',
      title: 't',
      subtitle: 's',
      photos: [photo('a', 60, 0, day1, ''), photo('b', 60, 1, day1 + 60_000, '')],
    });
    expect(equator.totalMeters / 1000).toBeCloseTo(111.2, 0);
    expect(highLat.totalMeters / 1000).toBeCloseTo(55.6, 0);
    // 墨卡托平面距离会是同一个值，这里必须明显更小
    expect(highLat.totalMeters).toBeLessThan(equator.totalMeters * 0.6);
  });
});

describe('samplePose 边界', () => {
  const journey = buildJourney({
    id: 'bound',
    title: 't',
    subtitle: 's',
    photos: [
      photo('a', 36.6171, 101.7782, day1, '西宁'),
      photo('b', 36.5333, 100.5, day1 + 3600_000, '青海湖'),
      photo('c', 36.7, 99.0833, day1 + 7200_000, '茶卡'),
    ],
  });

  const assertFinitePose = (s: number): void => {
    const pose = createPose();
    expect(() => samplePose(journey, s, pose)).not.toThrow();
    for (const key of ['s', 't', 'lng', 'lat', 'mx', 'my', 'index'] as const) {
      expect(Number.isFinite(pose[key])).toBe(true);
    }
  };

  it('s = 0', () => assertFinitePose(0));
  it('s = totalMeters', () => assertFinitePose(journey.totalMeters));
  it('s 为负数（超界）', () => assertFinitePose(-1000));
  it('s 大于总长（超界）', () => assertFinitePose(journey.totalMeters * 10));

  /**
   * NaN 必须被 clamp 兜住（src/lib/geo.ts clamp 首行 `!Number.isFinite(value) → min`）。
   * 否则 target = NaN → 二分退化 → ratio = NaN → pose 全 NaN，
   * 再经 camera.follow 把相机永久污染成 NaN（画面黑掉且无法自愈）。
   */
  it('s 为 NaN 时被夹到起点，不把 NaN 带出去', () => assertFinitePose(Number.NaN));

  it('超界的 s 被夹到端点', () => {
    const pose = createPose();
    samplePose(journey, -1000, pose);
    expect(pose.s).toBe(0);
    expect(pose.index).toBe(0);

    samplePose(journey, journey.totalMeters * 10, pose);
    expect(pose.s).toBeCloseTo(journey.totalMeters, 6);
  });

  it('空轨迹（无照片）返回有限姿态', () => {
    const empty = buildJourney({ id: 'e', title: 't', subtitle: 's', photos: [] });
    expect(empty.track).toHaveLength(0);
    const pose = createPose();
    expect(() => samplePose(empty, 0, pose)).not.toThrow();
    expect(Number.isFinite(pose.mx)).toBe(true);
    expect(Number.isFinite(pose.my)).toBe(true);
  });
});

/* ══════════════════════════════════════════════════════════════
 * suggestedZoom 的机制级回归（数据集无关）
 *
 * 背景：`journey.test.ts` 里原有那条 `suggestedZoom ∈ [7.5, 13]` 的断言
 * 是**恒真**的 —— 它把实现的 clamp 区间直接当成了验收区间：
 *   · 上界 13 由 `clamp()` 保证，永不越界；
 *   · 下界 7.5 也恰好命中（那组西宁/茶卡/敦煌算出来 ≈7.44，被夹到 7.5）。
 * 所以那条断言对「zoom 顶到 clamp 上界」这个真实失效模式完全免疫，
 * 曾经 92 条全绿而画面全废，就是它放过 13 的直接证据。
 *
 * 下面三条锁定的是**机制**而不是输出区间，因此：
 *   · 不随演示数据的合法变更而假红；
 *   · 硬编码 `return 9` 这类作弊过不了；
 *   · 旧实现（站点去重阈值 50m）会直接红。
 * ══════════════════════════════════════════════════════════════ */

/** 以米为单位沿给定方位角偏移（球面近似，测试用足够） */
function offsetMeters(
  lat: number,
  lng: number,
  bearingDeg: number,
  meters: number,
): { lat: number; lng: number } {
  const rad = (bearingDeg * Math.PI) / 180;
  const dLat = (meters * Math.cos(rad)) / 111320;
  const dLng = (meters * Math.sin(rad)) / (111320 * Math.cos((lat * Math.PI) / 180));
  return { lat: lat + dLat, lng: lng + dLng };
}

function zoomOf(photos: Photo[]): number {
  return buildJourney({ id: 'z', title: 't', subtitle: 's', photos }).suggestedZoom;
}

describe('suggestedZoom 机制回归（P0：曾经恒等于 13）', () => {
  /**
   * (a) 近似重复照片不变性 —— 直接锁根因，也是本次 P0 的机制。
   *
   * 同一景点的两张照片相隔约 1.3km（真实相机同景点连拍的抖动量级）。
   * 若去重阈值小于这个尺度，一个景点会被算成两个站点，站距中位值从
   * ~240km 塌到 ~1.3km，zoom 直接被顶到 clamp 上界 13。
   *
   * 判据是「加重复照片前后 zoom 不变」，**不是**「zoom 落在某个区间」——
   * 旧实现（50m 阈值）下：单张版 ≈8.0、重复版 =13，差值 5.0，必红。
   * 注意：这条不能用「base 与 jittered 的 Δ」来写，因为旧实现下两者都是 13，
   * Δ=0 会假绿；必须用「一张 vs 两张」的对照组。
   */
  it('同一景点的两张近似重复照片（相距 1.3km）不得把 suggestedZoom 抬高', () => {
    const A = { lat: 36.6171, lng: 101.7782 }; // 西宁
    const B = { lat: 36.7, lng: 99.0833 }; // 茶卡，距 A 约 241km

    const single = zoomOf([
      photo('a1', A.lat, A.lng, day1, '西宁'),
      photo('b1', B.lat, B.lng, day1 + 3600_000, '茶卡'),
    ]);

    // 八个方位各测一次，避免「只在一个方向上恰好过关」
    for (const bearing of [0, 45, 90, 135, 180, 225, 270, 315]) {
      const dupA = offsetMeters(A.lat, A.lng, bearing, 1300);
      const dupB = offsetMeters(B.lat, B.lng, (bearing + 180) % 360, 1300);
      const paired = zoomOf([
        photo('a1', A.lat, A.lng, day1, '西宁'),
        photo('a2', dupA.lat, dupA.lng, day1 + 60_000, '西宁'),
        photo('b1', B.lat, B.lng, day1 + 3600_000, '茶卡'),
        photo('b2', dupB.lat, dupB.lng, day1 + 3660_000, '茶卡'),
      ]);

      expect(
        Math.abs(paired - single),
        `bearing ${bearing}°：重复照片让 suggestedZoom 从 ${single} 跳到 ${paired}（去重阈值小于同景点抖动尺度）`,
      ).toBeLessThanOrEqual(0.5);
    }
  });

  /**
   * (b) 站距单调性 —— 专治硬编码与公式反向。
   * 行程跨度越大，需要的视距越远，zoom 就必须越小。
   * 硬编码 `return 9` 会让两者相等，直接红。
   */
  it('站距越大 zoom 越小（单调性，防硬编码）', () => {
    const origin = { lat: 36.6, lng: 101.0 };
    const near = offsetMeters(origin.lat, origin.lng, 0, 20_000); // 20km
    const far = offsetMeters(origin.lat, origin.lng, 0, 200_000); // 200km

    const zNear = zoomOf([
      photo('n1', origin.lat, origin.lng, day1, '起点'),
      photo('n2', near.lat, near.lng, day1 + 3600_000, '终点'),
    ]);
    const zFar = zoomOf([
      photo('f1', origin.lat, origin.lng, day1, '起点'),
      photo('f2', far.lat, far.lng, day1 + 3600_000, '终点'),
    ]);

    expect(zNear).toBeGreaterThan(zFar);
    // 再锁一层量级关系：跨度放大 10 倍，zoom 应下降约 log2(10)≈3.3 档
    expect(zNear - zFar).toBeGreaterThan(2);
    expect(zNear - zFar).toBeLessThan(5);
  });

  /**
   * (c) 0.5 量化 —— `computeSuggestedZoom` 末尾做了 `Math.round(x*2)/2`，
   * 这条锁住量化不被误删（否则时间轴刻度与网格间距会取到非整数档）。
   *
   * ⚠️ 预期噪声：5km / 500km 这两档跨度极端，`clamp` 后分别撞到 13 / 7.5，
   * 会触发 `journey.ts` 里的 DEV `console.warn('[journey] suggestedZoom 撞到 clamp 边界')`。
   * 这是**预期行为**（极端跨度撞边界是合法的），不是测试失败，看 CI 日志时不要误判。
   */
  it('suggestedZoom 必须落在 0.5 的整数倍上且有限', () => {
    const cases: number[] = [5_000, 50_000, 500_000];
    for (const meters of cases) {
      const end = offsetMeters(36.6, 101.0, 30, meters);
      const zoom = zoomOf([
        photo('p1', 36.6, 101.0, day1, '起点'),
        photo('p2', end.lat, end.lng, day1 + 3600_000, '终点'),
      ]);
      expect(Number.isFinite(zoom)).toBe(true);
      expect(Math.abs(zoom * 2 - Math.round(zoom * 2)), `${meters}m 的 zoom=${zoom} 不是 0.5 的整数倍`).toBeLessThan(1e-9);
    }
  });
});
