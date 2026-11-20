// @vitest-environment jsdom
/** 地点列表（W12-4）的渲染测试。
 *
 * 用 jsdom 而不是 node：这一层唯一的职责就是"把数据摆成人能读的样子"，
 * 分组、标签中文名、照片 data URL 全部只能在渲染结果上断言。
 *
 * 四条：
 *  ① 按日期分组，日期升序；
 *  ② 标签 / 强度显示中文（协议里的英文值不许漏到界面上）；
 *  ③ 照片是 `data:image/jpeg;base64,` 前缀（内存里转，不落盘、不发请求）；
 *  ④ 没有照片的地点不崩（占位文案而不是空 `<ul>` 或抛错）。
 */

import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import type { IntensityBucket, Tag, UnpackedPlace } from '../../types/share';
import { PlaceList, groupPlacesByDate, imageToDataUrl } from './PlaceList';

// `globals` 没开，testing-library 的自动 cleanup 不会生效：不手动清，上一个用例的
// DOM 会留在 document 里，后面的 `getByText` 就会"找到多个元素"。
afterEach(cleanup);

function bytes(values: number[]): ArrayBuffer {
  const out = new Uint8Array(values.length);
  out.set(values);
  return out.buffer;
}

function place(
  placeId: string,
  overrides: Partial<UnpackedPlace> & { date?: string } = {},
): UnpackedPlace {
  return {
    placeId,
    name: `地点 ${placeId}`,
    date: '2026-11-01',
    tag: 'heritage' as Tag,
    intensity: 'high' as IntensityBucket,
    photos: [],
    ...overrides,
  };
}

describe('groupPlacesByDate', () => {
  it('按日期分组并升序，组内保持原顺序', () => {
    const groups = groupPlacesByDate([
      place('b', { date: '2026-11-02' }),
      place('a', { date: '2026-11-01' }),
      place('c', { date: '2026-11-02' }),
    ]);

    expect(groups.map((group) => group.date)).toEqual(['2026-11-01', '2026-11-02']);
    expect(groups[1].places.map((item) => item.placeId)).toEqual(['b', 'c']);
  });

  it('空数组返回空分组', () => {
    expect(groupPlacesByDate([])).toEqual([]);
  });
});

describe('imageToDataUrl', () => {
  it('产出 data:image/jpeg;base64 前缀（浏览器里直接给 <img>）', () => {
    const url = imageToDataUrl(bytes([0xff, 0xd8, 0xff]));
    expect(url.startsWith('data:image/jpeg;base64,')).toBe(true);
    // 0xff 0xd8 0xff → "/9j/" （base64 前三字节）
    expect(url.endsWith('/9j/')).toBe(true);
  });
});

describe('PlaceList 渲染', () => {
  it('日期分组、标签中文、强度星级都出现在界面上', () => {
    render(
      <PlaceList
        places={[
          place('p1', {
            name: '拙政园',
            date: '2026-11-01',
            tag: 'heritage' as Tag,
            intensity: 'high' as IntensityBucket,
            label: '上午',
          }),
          place('p2', {
            name: '平江路',
            date: '2026-11-02',
            tag: 'food' as Tag,
            intensity: 'mid' as IntensityBucket,
          }),
        ]}
      />,
    );

    expect(screen.getByText('2026-11-01')).toBeTruthy();
    expect(screen.getByText('2026-11-02')).toBeTruthy();
    expect(screen.getByText('拙政园')).toBeTruthy();
    // 标签中文名（不是 heritage / food）
    expect(screen.getByText('人文历史')).toBeTruthy();
    expect(screen.getByText('美食探索')).toBeTruthy();
    // 强度：星星 + 中文
    expect(screen.getByText(/★★★ 强烈推荐/)).toBeTruthy();
    expect(screen.getByText(/★★ 喜欢/)).toBeTruthy();
    expect(screen.getByText('上午')).toBeTruthy();
  });

  it('照片渲染成 data URL 的 <img>，配文跟着出来', () => {
    const { container } = render(
      <PlaceList
        places={[
          place('p1', {
            photos: [
              { photoId: 'ph1', image: bytes([0xff, 0xd8, 0x01]), caption: '雨后的廊桥' },
              { photoId: 'ph2', image: bytes([0xff, 0xd8, 0x02]) },
            ],
          }),
        ]}
      />,
    );

    // 直接查 `<img>`：没有配文的图 `alt=""` 会被 a11y 当成 presentation，
    // `getAllByRole('img')` 数不到它。
    const images = container.querySelectorAll('img.place-photo-img');
    expect(images).toHaveLength(2);
    for (const image of Array.from(images)) {
      expect(image.getAttribute('src')?.startsWith('data:image/jpeg;base64,')).toBe(true);
    }
    expect(screen.getByText('雨后的廊桥')).toBeTruthy();
  });

  it('没有照片的地点显示占位，不崩', () => {
    render(<PlaceList places={[place('p1', { name: '空地点' })]} />);
    expect(screen.getByText('空地点')).toBeTruthy();
    expect(screen.getByText('这里没有照片')).toBeTruthy();
  });

  it('空列表显示提示文案', () => {
    render(<PlaceList places={[]} emptyHint="这个信物里还没有地点" />);
    expect(screen.getByText('这个信物里还没有地点')).toBeTruthy();
  });

  it('选中项带 is-active 类（地图联动高亮靠它）', () => {
    const { container } = render(
      <PlaceList places={[place('p1'), place('p2')]} selectedId="p2" onSelect={() => undefined} />,
    );
    const active = container.querySelector('.place-row.is-active');
    expect(active?.getAttribute('data-place-id')).toBe('p2');
  });
});
