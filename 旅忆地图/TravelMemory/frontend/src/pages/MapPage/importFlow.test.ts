// @vitest-environment jsdom
/** 「照片库」整条链路的集成测试（阶段 7：照片是底层、行程是视图）。
 *
 * `photoImport.test.ts` 覆盖的是脱离 DOM 的纯状态机；这一份覆盖的是**那段必须碰 DOM
 * 的接线**：切到照片库 Tab → 隐藏 file input 的 change → `adoptFiles` → `readThumb`
 * → `importPhotos` → `thumbs.put(photoId)` → 通知上层刷新。
 * 这段接线以前是 `PlaceholderActionDialog` —— 一个什么都不做的占位弹窗，
 * 谁也不知道它到底接对了没有。
 *
 * 用 jsdom 而不是 node：`<input type="file">` 的 change 事件、`File` 对象、
 * 事件冒泡都只有在 DOM 环境里才成立。代价是慢几秒，换来的是"按钮点下去真的会上传"
 * 这一条有人守着。
 *
 * ## 阶段 7 之后这个文件的重心变了
 *
 * 以前测的是"导入 → 自动聚类出行程"。现在导入**只写 Photo 表**，
 * 行程由用户手动组建，所以：
 *   - 文件输入框移到了「照片库」Tab（行程 Tab 里没有它），打开抽屉后要先切 Tab；
 *   - 不再断言 `tripIds`，改成断言"照片进了照片库、未定位的显示补标入口"；
 *   - 新增：点未定位照片 → 补标对话框真的打开。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import { MapPage, type MapPageProps } from './MapPage';
import { Toaster } from '../../components/ui/toaster';
import type { PhotoListItem } from '../../services/api/photos';
import { setNativeBridge, resetNativeBridge } from '../../services/native';
import type { NativeBridge } from '../../types/native';
import type { TripView } from '../../services/api/trips';

const calls: string[] = [];

function fakeBridge(): NativeBridge {
  return {
    platform: 'web',
    photos: {
      platform: 'web',
      pickPhotos: async () => [],
      adoptFiles: (files) => {
        calls.push(`adopt:${files.length}`);
        return files.map((_f, i) => ({ id: `asset-${i}`, width: 4, height: 3 }));
      },
      readThumb: async (assetId) => {
        calls.push(`thumb:${assetId}`);
        return new TextEncoder().encode('thumb').buffer;
      },
    },
    permissions: {
      query: async () => 'granted',
      request: async () => 'granted',
    },
    thumbs: {
      get: async () => null,
      put: async (assetId, maxEdge) => {
        calls.push(`put:${assetId}:${maxEdge}`);
      },
      clear: async () => {},
      sizeHint: 0,
    },
  };
}

function okFetch(body: unknown): typeof fetch {
  return ((async () =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })) as unknown as typeof fetch);
}

/** 连 Toaster 一起挂：toast 是挂在 App 壳上的，单渲染 MapPage 看不到它。 */
function renderPage(props: MapPageProps = {}): void {
  render(
    React.createElement(
      React.Fragment,
      null,
      React.createElement(MapPage, props),
      React.createElement(Toaster),
    ),
  );
}

/** 打开「行程与相册」抽屉，并切到指定 Tab。
 *
 *  两个原因都必须先打开：
 *   1. Radix 的 Sheet **关闭时不挂载内容**（那是 Modal 的语义，不该被 forceMount 破坏）；
 *   2. 文件输入框只在「照片库」Tab 里 —— 行程 Tab 是空视图的操作区，没有它。
 */
async function openDrawer(tab: 'trips' | 'photos'): Promise<void> {
  fireEvent.click(screen.getByLabelText('打开行程与相册抽屉'));
  await waitFor(() => expect(screen.getByRole('tab', { name: /照片库/ })).toBeTruthy());
  if (tab === 'photos') {
    fireEvent.click(screen.getByRole('tab', { name: /照片库/ }));
    await waitFor(() => expect(document.querySelector('input[type="file"]')).toBeTruthy());
  }
}

function photo(overrides: Partial<PhotoListItem> = {}): PhotoListItem {
  return {
    id: 'p-1',
    hash: 'h1',
    takenAt: '2026-10-01T02:00:00Z',
    takenAtLocal: '2026-10-01T10:00:00',
    tzName: 'UTC+08:00',
    lat: 34.56,
    lng: 112.47,
    coordSystem: 'WGS84',
    locationSource: 'exif',
    hasLocation: true,
    locationConfidence: 0.9,
    caption: null,
    createdAt: '2026-10-01T02:00:00Z',
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  resetNativeBridge();
  vi.unstubAllGlobals();
  calls.length = 0;
});

describe('照片库：导入闭环', () => {
  it('选文件 → adoptFiles → readThumb → importPhotos → thumbs.put(photoId) → 通知刷新', async () => {
    setNativeBridge(fakeBridge());
    vi.stubGlobal(
      'fetch',
      okFetch({
        imported: 1,
        duplicates: 0,
        failed: 0,
        photos: [
          {
            id: 'p-9',
            asset_id: 'a9',
            hash: 'h9',
            taken_at: '2026-10-01T02:00:00Z',
            taken_at_local: null,
            tz_name: null,
            lat: 34.5,
            lng: 112.4,
            coord_system: 'WGS84',
            location_source: 'exif',
            location_confidence: 0.9,
            semantic_tag: 'heritage',
            semantic_label: '龙门石窟',
          },
        ],
      }),
    );
    let refreshed = 0;

    renderPage({ onTripsChanged: () => void (refreshed += 1) });
    await openDrawer('photos');

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).toBeTruthy();
    expect(input.multiple).toBe(true);
    expect(input.accept).toBe('image/*');

    const file = new File([new Uint8Array([1, 2, 3])], 'a.jpg', { type: 'image/jpeg' });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(screen.getAllByText('已导入 1 张照片').length).toBeGreaterThan(0));

    expect(calls).toContain('adopt:1');
    expect(calls).toContain('thumb:asset-0');
    // 缩略图按后端 photo id 存，不是相册 assetId
    expect(calls).toContain('put:p-9:256');
    expect(refreshed).toBe(1);
    // 经典坑：选完之后必须清空 value，否则连选同一个文件不再触发 change
    expect(input.value).toBe('');
  });

  it('上传失败时显示中文错误，再选一次能重试（不卡死）', async () => {
    setNativeBridge(fakeBridge());
    // 第一次：后端报 404（开关没开）
    vi.stubGlobal(
      'fetch',
      ((async () => new Response('该能力未启用', { status: 404 })) as unknown as typeof fetch),
    );

    renderPage();
    await openDrawer('photos');
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;

    fireEvent.change(input, {
      target: { files: [new File([new Uint8Array([1])], 'a.jpg', { type: 'image/jpeg' })] },
    });
    await waitFor(() => expect(screen.getAllByText(/HTTP 404/).length).toBeGreaterThan(0));

    // 第二次：后端恢复
    vi.stubGlobal(
      'fetch',
      okFetch({
        imported: 2,
        duplicates: 0,
        failed: 0,
        photos: [
          {
            id: 'p1',
            asset_id: 'a',
            hash: '',
            taken_at: null,
            taken_at_local: null,
            tz_name: null,
            lat: null,
            lng: null,
            coord_system: 'WGS84',
            location_source: null,
            location_confidence: null,
            semantic_tag: null,
            semantic_label: null,
          },
        ],
      }),
    );
    fireEvent.change(input, {
      target: { files: [new File([new Uint8Array([2])], 'b.jpg', { type: 'image/jpeg' })] },
    });
    await waitFor(() => expect(screen.getAllByText('已导入 2 张照片').length).toBeGreaterThan(0));
    // 按钮从「上传中…」回到「导入照片」：重试没有被 error 态卡住
    expect(screen.getByText('导入照片')).toBeTruthy();
    expect(screen.queryByText('上传中…')).toBeNull();
  });

  it('选到非图片文件时给出提示，不发请求', async () => {
    setNativeBridge(fakeBridge());
    let fetched = 0;
    vi.stubGlobal(
      'fetch',
      ((async () => {
        fetched += 1;
        return new Response('{}', { status: 200 });
      }) as unknown as typeof fetch),
    );

    renderPage();
    await openDrawer('photos');
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: {
        files: [new File([new Uint8Array([1])], 'a.pdf', { type: 'application/pdf' })],
      },
    });

    await waitFor(() => expect(screen.getAllByText('请选择图片文件').length).toBeGreaterThan(0));
    expect(fetched).toBe(0);
    expect(calls).toEqual([]);
  });
});

describe('照片库：定位状态与补标入口', () => {
  it('有坐标的照片显示坐标，未定位的显示补标按钮', async () => {
    renderPage({
      photos: [
        photo({ id: 'p-located', takenAtLocal: '2026-10-01T10:00:00' }),
        photo({
          id: 'p-lost',
          takenAtLocal: '2026-10-02T10:00:00',
          lat: null,
          lng: null,
          locationSource: 'none',
          hasLocation: false,
          locationConfidence: 0,
        }),
      ],
    });
    await openDrawer('photos');

    // 已定位的显示坐标文本。查整行的 textContent 而不是 getByText：
    // JSX 把 `{lat}, {lng}` 渲染成多个文本节点，精确匹配会找不到
    const locatedRow = screen.getByText('2026-10-01T10:00:00').closest('li');
    expect(locatedRow?.textContent).toContain('34.5600');
    expect(locatedRow?.textContent).toContain('112.4700');
    // 未定位的是一个可点的按钮（不是一段死文本）
    const badge = screen.getByRole('button', { name: /未定位/ });
    expect(badge.textContent).toContain('未定位');
  });

  it('点未定位照片 → 补标对话框真的打开', async () => {
    renderPage({
      photos: [
        photo({
          id: 'p-lost',
          lat: null,
          lng: null,
          locationSource: 'none',
          hasLocation: false,
        }),
      ],
    });
    await openDrawer('photos');

    fireEvent.click(screen.getByRole('button', { name: /未定位/ }));
    // 对话框标题出现 = 补标 UI 真的接通了（jsdom 里高德地图是加载不了的，
    // 所以只断言到"对话框打开 + 有两个坐标输入框"这一层）
    await waitFor(() => expect(screen.getByText('补标坐标')).toBeTruthy());
    expect(document.getElementById('photo-lat')).toBeTruthy();
    expect(document.getElementById('photo-lng')).toBeTruthy();
  });

  it('空照片库显示引导文案，而不是空白', async () => {
    renderPage({ photos: [] });
    await openDrawer('photos');
    expect(screen.getByText(/没有坐标的照片可以手动补标/)).toBeTruthy();
  });
});

describe('行程 Tab：只建空壳，不自动加照片', () => {
  it('行程列表显示标题、时间与地点数', async () => {
    const trips: TripView[] = [
      {
        id: 't1',
        title: '洛阳两日',
        startedAt: '2026-10-01T00:00:00Z',
        endedAt: null,
        source: 'cluster',
        itemCount: 2,
        items: [
          {
            id: 'i1',
            photoId: 'ph1',
            takenAt: '2026-10-01T02:00:00Z',
            lat: 34.56,
            lng: 112.47,
            semanticTag: 'heritage',
            semanticLabel: '龙门石窟',
          },
          {
            id: 'i2',
            photoId: 'ph2',
            takenAt: '2026-10-01T09:00:00Z',
            lat: null,
            lng: null,
            semanticTag: 'food',
            semanticLabel: '洛邑古城',
          },
        ],
      },
    ];
    renderPage({ trips });
    await openDrawer('trips');

    expect(screen.getByText('洛阳两日')).toBeTruthy();
    expect(screen.getByText('2026-10-01')).toBeTruthy();
    expect(screen.getByText('2 个地点')).toBeTruthy();
    // 行程里有没坐标的照片 → 提示"未定位"（与照片库的补标入口呼应）
    expect(screen.getByText('1 个未定位')).toBeTruthy();
  });

  it('没有旅程时显示引导，且删除按钮不会凭空出现', async () => {
    renderPage({ trips: [] });
    await openDrawer('trips');
    expect(screen.getByText(/点「新建旅程」开始/)).toBeTruthy();
    expect(screen.queryByLabelText(/删除行程/)).toBeNull();
  });

  it('每趟行程都有重命名与删除入口（删除带二次确认）', async () => {
    const trips: TripView[] = [
      {
        id: 't1',
        title: '待删的旅程',
        startedAt: null,
        endedAt: null,
        source: 'manual',
        itemCount: 0,
        items: [],
      },
    ];
    renderPage({ trips });
    await openDrawer('trips');

    const remove = screen.getByLabelText('删除行程 待删的旅程');
    fireEvent.click(remove);
    // 二次确认：文案里必须明说"照片一条不少" —— 用户点删除时担心的正是照片跟着没。
    // 用 getAllByText：对话框标题与确认按钮都叫「删除旅程」，getByText 会因多命中抛错
    await waitFor(() =>
      expect(screen.getAllByText('删除旅程').length).toBeGreaterThan(0),
    );
    expect(screen.getByText(/照片一条不少/)).toBeTruthy();
    expect(screen.getByLabelText('重命名行程 待删的旅程')).toBeTruthy();
  });
});
