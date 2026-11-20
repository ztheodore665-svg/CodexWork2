/** W15-4：已接受的地点 → `journey.json`（`acceptedToJourney`）。
 *
 * 这一层是纯函数，所以可以钉住"文件长什么样"：分天、id、照片数、缩略图绑定，
 * 以及**绝不能出现原图字节**。
 */

import { describe, expect, it } from 'vitest';

import { JOURNEY_BUNDLE_VERSION } from '@travelmemory/journey-transfer';

import { acceptedToJourney, journeyPhotoId, parseSenderFromTitle, senderTitleOf } from './receivedToJourney';
import type { ReceivedPlace } from './receivedStore';

const NOW = '2026-11-13T00:00:00.000Z';

/** 一小段 JPEG 头 + 空载荷的 data URL（格式与真实缩略图一致，但只有几十字节）。 */
const THUMB = 'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQ==';

function received(overrides: Partial<ReceivedPlace> = {}): ReceivedPlace {
  return {
    id: 's_demo:p1_aaa',
    shareId: 's_demo',
    fromPubkey: 'aa'.repeat(32),
    fromName: '小旅',
    placeName: '西湖',
    tag: 'nature',
    intensity: 'mid',
    date: '2026-11-01',
    photos: [{ thumbnailDataUrl: THUMB }],
    acceptedAt: '2026-11-02T00:00:00.000Z',
    rejected: false,
    ...overrides,
  };
}

describe('acceptedToJourney', () => {
  it('2 个地点跨 2 天 → 2 天，天 id 是 d_1 / d_2，标题带发送方名字 + 第一个地点', () => {
    const bundle = acceptedToJourney(
      [
        received({ id: 's_demo:p1_aaa', placeName: '西湖', date: '2026-11-02' }),
        received({ id: 's_demo:p2_bbb', placeName: '灵隐寺', date: '2026-11-01' }),
      ],
      '小旅',
      NOW,
    );

    expect(bundle.version).toBe(JOURNEY_BUNDLE_VERSION);
    expect(bundle.version).toBe('journey-2026.1');
    // 标题 = `来自 X 的信物 · 第一个地点`（第一个地点 = 按日期排完后的第一处）
    expect(bundle.title).toBe('来自 小旅 的信物 · 灵隐寺');
    expect(bundle.exportedAt).toBe(NOW);
    expect(bundle.days).toHaveLength(2);
    // 日期升序：11-01 在前，即便输入顺序相反
    expect(bundle.days.map((day) => day.date)).toEqual(['2026-11-01', '2026-11-02']);
    expect(bundle.days.map((day) => day.id)).toEqual(['d_1', 'd_2']);
    expect(bundle.days[0].places.map((place) => place.name)).toEqual(['灵隐寺']);
    expect(bundle.days[1].places.map((place) => place.name)).toEqual(['西湖']);
  });

  it('同一天的两个地点合并成一天，照片数与缩略图/takenAt 都正确', () => {
    const bundle = acceptedToJourney(
      [
        received({
          id: 's_demo:p1_aaa',
          date: '2026-11-01',
          photos: [{ thumbnailDataUrl: THUMB }, { thumbnailDataUrl: THUMB, caption: '日落' }],
        }),
        received({ id: 's_demo:p2_bbb', placeName: '灵隐寺', date: '2026-11-01', photos: [] }),
      ],
      '小旅',
      NOW,
    );

    expect(bundle.days).toHaveLength(1);
    const [day] = bundle.days;
    expect(day.places).toHaveLength(2);
    expect(day.places[0].photos).toHaveLength(2);
    expect(day.places[1].photos).toHaveLength(0);

    const [first, second] = day.places[0].photos;
    expect(first.id).toBe(journeyPhotoId('s_demo:p1_aaa', 0));
    expect(first.thumbnailDataUrl.startsWith('data:image/jpeg;base64,')).toBe(true);
    expect(first.takenAt).toBe('2026-11-01T00:00:00Z');
    // 配文从 ReceivedPhoto 带到 JourneyPhoto
    expect(second.caption).toBe('日落');
    // 标签 / 强度保留；`coverIndex` / `lat` 这类主应用字段不进线格式
    expect(day.places[0].tag).toBe('nature');
    expect(day.places[0].intensity).toBe('mid');
    expect('coverIndex' in day.places[0]).toBe(false);
  });

  it('已拒绝的地点不进包（调用方忘了过滤也不会漏进来）', () => {
    const bundle = acceptedToJourney(
      [
        received({ id: 's_demo:p1_aaa', placeName: '西湖' }),
        received({ id: 's_demo:p2_bbb', placeName: '不要的地点', rejected: true }),
      ],
      '小旅',
      NOW,
    );

    expect(bundle.days).toHaveLength(1);
    expect(bundle.days[0].places.map((place) => place.name)).toEqual(['西湖']);
  });

  it('坐标挂在照片上（线格式里地点本身没有 lat/lng）', () => {
    const withCoords = acceptedToJourney(
      [received({ lat: 30.24, lng: 120.15 })],
      '小旅',
      NOW,
    );
    expect(withCoords.days[0].places[0].photos[0].lat).toBeCloseTo(30.24, 6);
    expect(withCoords.days[0].places[0].photos[0].lng).toBeCloseTo(120.15, 6);

    const noCoords = acceptedToJourney([received({ lat: Number.NaN })], '小旅', NOW);
    expect('lat' in noCoords.days[0].places[0].photos[0]).toBe(false);
  });

  it('序列化后不含原图字节：没有 image 字段，只有缩略图 data URL', () => {
    const bundle = acceptedToJourney([received()], '小旅', NOW);
    const json = JSON.stringify(bundle);

    expect(json).not.toContain('"image"');
    expect(json).not.toContain('ArrayBuffer');
    expect(json).toContain('"thumbnailDataUrl":"data:image/jpeg;base64,');
    // 一个地点一张小图 + 元数据，不该是"原图大小"的量级
    expect(json.length).toBeLessThan(2000);
  });

  it('没有已接受地点 → 空 days、标题退化成不带地点后缀，不抛错', () => {
    const bundle = acceptedToJourney([], '小旅', NOW);
    expect(bundle.days).toEqual([]);
    expect(bundle.title).toBe('来自 小旅 的信物');
  });
});

describe('标题约定与反解（W15-5 的接口）', () => {
  it('senderTitleOf：带第一个地点时是「来自 X 的信物 · 地点」', () => {
    expect(senderTitleOf('张三', '西湖')).toBe('来自 张三 的信物 · 西湖');
    // 没地点名 / 空地点名 → 不带后缀（不能留一个孤零零的 ` · `）
    expect(senderTitleOf('张三')).toBe('来自 张三 的信物');
    expect(senderTitleOf('张三', '   ')).toBe('来自 张三 的信物');
    // 没名字 → 兜底文案
    expect(senderTitleOf('  ', '西湖')).toBe('来自 （未署名） 的信物 · 西湖');
  });

  it('parseSenderFromTitle：正常标题取出名字，忽略 ` · 地点` 后缀', () => {
    expect(parseSenderFromTitle('来自 张三 的信物 · 西湖')).toBe('张三');
    // 旧格式（没有地点后缀）也要能解析：已经导进主应用的历史数据不能丢
    expect(parseSenderFromTitle('来自 张三 的信物')).toBe('张三');
  });

  it('parseSenderFromTitle：名字里带空格 / 点号也原样取出', () => {
    expect(parseSenderFromTitle('来自 张 三 的信物 · 灵隐寺')).toBe('张 三');
    expect(parseSenderFromTitle('来自 A. B. Chen 的信物 · 西湖')).toBe('A. B. Chen');
    expect(parseSenderFromTitle('  来自 小旅 的信物 · 西湖  ')).toBe('小旅');
  });

  it('parseSenderFromTitle：不是信物标题 / 脏输入一律 null', () => {
    expect(parseSenderFromTitle('杭州两日')).toBeNull();
    expect(parseSenderFromTitle('来自 的信物')).toBeNull();
    expect(parseSenderFromTitle('来自  的信物 · 西湖')).toBeNull();
    expect(parseSenderFromTitle('')).toBeNull();
    expect(parseSenderFromTitle(null)).toBeNull();
    expect(parseSenderFromTitle(undefined)).toBeNull();
    expect(parseSenderFromTitle(42)).toBeNull();
  });

  it('导出 → 反解 是闭环（含名字带空格）', () => {
    const bundle = acceptedToJourney([received()], '张 三', NOW);
    expect(parseSenderFromTitle(bundle.title)).toBe('张 三');
  });
});
