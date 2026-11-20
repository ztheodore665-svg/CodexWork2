// @vitest-environment jsdom
/** 照片卡片层的组件测试（阶段 13 / W13-2）。
 *
 * showcase 没有给 `PhotoCards` 写组件测试（它有 `styles.test.ts` / `timeline.test.ts`
 * 两个"读源码的声明契约"测试），这里补一份**真跑组件**的：
 *
 *  1. 每张照片都渲染出一个 `.card` 节点，且地名写在了卡片里；
 *  2. `update()` 会把可见卡片摆上屏（`display: block` + `translate3d` + 透明度），
 *     并同步引线锚点（`anchors` 的第 5 个分量置 1）—— 渲染器靠它画引线；
 *  3. 同屏卡片数量不超过 `MAX_CARDS`（5）—— 这是 showcase 里按截图调过的密度上限，
 *     破了就会出现"多张卡片叠在一起、文字互遮"。
 *
 * 说明：`update()` 是"每帧调用"的 hot path，所以它只写 `style.transform` /
 * `style.opacity` / `display`，其余属性一概不碰 —— 断言也只盯这三样。
 */

import { createRef } from 'react';
import { act } from 'react-dom/test-utils';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  ANCHOR_STRIDE,
  analyticProjection,
  buildJourney,
  prepareScene,
  type Journey,
  type Photo,
} from '@travelmemory/trail';

import { PhotoCards, type PhotoCardsHandle } from './PhotoCards';

/** 5 张带坐标的照片（杭州西湖一带，段长几公里，互相都在视口内）。 */
function photosFixture(): Photo[] {
  const points = [
    { lat: 30.2431, lng: 120.1459, name: '西湖 · 断桥' },
    { lat: 30.2408, lng: 120.0985, name: '灵隐寺' },
    { lat: 30.2213, lng: 120.1147, name: '龙井村' },
    { lat: 30.1728, lng: 120.1009, name: '宋城' },
    { lat: 30.1878, lng: 120.144, name: '钱塘江 · 之江路' },
  ];
  const base = Date.UTC(2026, 3, 18, 1, 0, 0);
  return points.map((point, index) => ({
    id: `p${index}`,
    lat: point.lat,
    lng: point.lng,
    takenAt: base + index * 40 * 60 * 1000,
    placeName: point.name,
    locationSource: 'exif',
    thumb: `data:image/png;base64,${'A'.repeat(16)}`,
  }));
}

const JOURNEY: Journey = buildJourney({
  id: 'dark-photo-cards',
  title: '卡片测试',
  subtitle: '5 张',
  photos: photosFixture(),
});

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // React 18 要求显式声明"当前是 act 环境"，否则每个 act 都打一条警告
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

/** 取当前所有可见卡片（`display: none` 的不算）。 */
function visibleCards(): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>('.card')).filter(
    (el) => el.style.display === 'block',
  );
}

describe('PhotoCards', () => {
  it('每张照片一个卡片节点，地名写在卡片里', async () => {
    const ref = createRef<PhotoCardsHandle>();
    await act(async () => {
      root.render(<PhotoCards ref={ref} journey={JOURNEY} />);
    });

    const cards = container.querySelectorAll('.card');
    expect(cards.length).toBe(JOURNEY.photos.length);

    const text = container.textContent ?? '';
    for (const photo of JOURNEY.photos) {
      expect(text).toContain(photo.placeName ?? '');
    }
  });

  it('update() 把可见卡片摆上屏，并同步引线锚点', async () => {
    const ref = createRef<PhotoCardsHandle>();
    await act(async () => {
      root.render(<PhotoCards ref={ref} journey={JOURNEY} />);
    });

    const scene = prepareScene(JOURNEY);
    const start = JOURNEY.photos[0];
    const proj = analyticProjection(
      { lng: start.lng ?? 0, lat: start.lat ?? 0 },
      JOURNEY.suggestedZoom,
      800,
      600,
    );

    await act(async () => {
      ref.current?.update(scene, proj, scene.photoS[0], true, 1000, proj.ox, proj.oy);
    });

    const shown = visibleCards();
    expect(shown.length).toBeGreaterThan(0);
    // 密度上限：同屏不超过 5 张（showcase 的 MAX_CARDS）
    expect(shown.length).toBeLessThanOrEqual(5);

    const first = shown[0];
    expect(first.style.transform).toContain('translate3d');
    expect(Number(first.style.opacity)).toBeGreaterThan(0);

    const anchors = ref.current?.anchors;
    expect(anchors).toBeDefined();
    expect(anchors?.length).toBe(Math.max(1, JOURNEY.photos.length) * ANCHOR_STRIDE);
    // 至少有一张的 visible 分量为 1（渲染器据此画引线）
    let visibleAnchors = 0;
    for (let index = 0; index < JOURNEY.photos.length; index += 1) {
      if (anchors?.[index * ANCHOR_STRIDE + 4] === 1) visibleAnchors += 1;
    }
    expect(visibleAnchors).toBeGreaterThan(0);
  });

  it('还没走到的照片不上屏（进度 0 时不会把所有卡片摊开）', async () => {
    const ref = createRef<PhotoCardsHandle>();
    await act(async () => {
      root.render(<PhotoCards ref={ref} journey={JOURNEY} />);
    });

    const scene = prepareScene(JOURNEY);
    const start = JOURNEY.photos[0];
    const proj = analyticProjection(
      { lng: start.lng ?? 0, lat: start.lat ?? 0 },
      JOURNEY.suggestedZoom,
      800,
      600,
    );

    await act(async () => {
      ref.current?.update(scene, proj, 0, false, 1000, proj.ox, proj.oy);
    });

    // 播放态（paused=false）只看名次窗口 ≤3 的邻居，远端的必须隐藏
    expect(visibleCards().length).toBeLessThan(JOURNEY.photos.length);
  });
});
