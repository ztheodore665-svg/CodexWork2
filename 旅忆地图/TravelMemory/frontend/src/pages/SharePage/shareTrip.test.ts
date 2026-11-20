// @vitest-environment jsdom
/** 「生成信物」读到**导入出来的**行程（而不是内置示例）。
 *
 * 这是阶段 6 最后一段断头路的验收：以前 SharePage 只能打包 `demoTrip()` 那份写死的
 * 洛阳两日，用户导入的照片无论如何进不了信物。这一份测试会在 jsdom 里真的点一次
 * 「生成并下载」，确认包是从后端行程数据打出来的。
 *
 * 两个必须手补的环境差异：jsdom 的 `crypto` 没有 `subtle`（AES-GCM / Ed25519 跑不起来），
 * 从 `node:crypto` 借一个；jsdom 也没有 `URL.createObjectURL`（文件下载用）。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import { SharePage } from './SharePage';
import { Toaster } from '../../components/ui/toaster';
import { setNativeBridge, resetNativeBridge } from '../../services/native';
import type { NativeBridge } from '../../types/native';
import type { TripView } from '../../services/api/trips';

/** 默认的 Web 桥在 jsdom 里是空的内存缓存，那样所有地点都会因为取不到字节被
 *  `hydrateTripPhotos` 丢掉，测不到打包的成功路径。 */
function bridgeWithThumbs(): NativeBridge {
  return {
    platform: 'web',
    photos: {
      platform: 'web',
      pickPhotos: async () => [],
      adoptFiles: (files) => files.map((_f, i) => ({ id: `asset-${i}`, width: 4, height: 3 })),
      readThumb: async () => new TextEncoder().encode('thumb').buffer,
    },
    permissions: { query: async () => 'granted', request: async () => 'granted' },
    thumbs: {
      get: async (assetId: string) => new TextEncoder().encode(`thumb-of-${assetId}`).buffer,
      put: async () => {},
      clear: async () => {},
      sizeHint: 1,
    },
  };
}

afterEach(() => {
  cleanup();
  resetNativeBridge();
  vi.unstubAllGlobals();
});

describe('SharePage 读到导入出来的行程', () => {
  it('默认选中的是导入的行程，并真的能打包成信物', async () => {
    // jsdom 的 crypto 没有 subtle，密码学那一层（AES-GCM / Ed25519）跑不起来。
    // specifier 拼出来而不是写死 `await import('node:crypto')`：本项目没装
    // `@types/node`（也不为此多一个依赖），写死会让 `tsc --noEmit` 直接红在
    // "Cannot find module 'node:crypto'"。运行时照常拿到 Node 的 webcrypto。
    const moduleId = ['node', 'crypto'].join(':');
    const { webcrypto } = (await import(/* @vite-ignore */ moduleId)) as { webcrypto: Crypto };
    Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
    (URL as unknown as { createObjectURL: () => string }).createObjectURL = () => 'blob:mock';
    (URL as unknown as { revokeObjectURL: () => void }).revokeObjectURL = () => {};

    vi.stubGlobal('fetch', (async (url: string) => {
      if (String(url).includes('/privacy/obfuscate')) {
        return new Response(
          JSON.stringify({ lat: 34.57, lng: 112.48, radius: 800, mode: 'approx', epsilon: 1, coord_system: 'WGS84' }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      return new Response('{}', { status: 404 });
    }) as unknown as typeof fetch);

    const trips: TripView[] = [
      {
        id: 't-real', title: '我的洛阳行', startedAt: '2026-10-01T00:00:00Z', endedAt: null,
        source: 'cluster', itemCount: 1,
        items: [
          { id: 'i1', photoId: 'ph1', takenAt: '2026-10-01T02:00:00Z', lat: 34.5636, lng: 112.4699, semanticTag: 'heritage', semanticLabel: '龙门石窟' },
        ],
      },
    ];
    setNativeBridge(bridgeWithThumbs());
    render(React.createElement(React.Fragment, null, React.createElement(SharePage, { trips }), React.createElement(Toaster)));

    const select = document.getElementById('trip-source') as HTMLSelectElement;
    expect(select).toBeTruthy();
    // 默认优先用导入出来的行程
    expect(select.value).toBe('t-real');
    expect(screen.getByText('我导入的照片')).toBeTruthy();

    fireEvent.click(screen.getByText('生成并下载 .trailshare'));
    await waitFor(
      () => {
        const err = screen.queryAllByText('生成失败');
        if (err.length > 0) throw new Error('生成失败：' + document.body.textContent?.slice(0, 2000));
        expect(screen.getAllByText('信物已生成').length).toBeGreaterThan(0);
      },
      { timeout: 40000 },
    );
    expect(screen.getAllByText(/t-real|我的洛阳行/).length).toBeGreaterThan(0);

    // 也能切回内置示例
    fireEvent.change(select, { target: { value: '__demo__' } });
    expect(screen.getByText('内置示例旅程')).toBeTruthy();
  }, 60000);
});
