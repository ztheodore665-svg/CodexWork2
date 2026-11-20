/** 加载原语的行为契约。
 *
 *  为什么要单独一个包 + 一整份测试：合并前 `frontend/amap.ts` 与
 *  `showcase/src/map/baseMap.ts` 各有一份加载器，行为已经漂移 —— 超时一个 15s 一个 8s、
 *  失败一个抛错一个返回 null、安全密钥字段一个写错一个写对。文档 §8 明确要求
 *  「合并前先补测试」，这份测试就是那道门禁。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  isAmapReady,
  loadAmapScript,
  resetAmapLoaderForTest,
  type AmapNamespaceLike,
} from './loader';

const fakeNs = {
  Map: class {
    constructor(_container: HTMLElement, _options: Record<string, unknown>) { /* noop */ }
  },
} satisfies AmapNamespaceLike;

const inserted = (): HTMLScriptElement | null =>
  document.querySelector<HTMLScriptElement>('script[data-amap-loader]');

function fireLoad(): void {
  Reflect.set(window, 'AMap', fakeNs);
  const script = inserted();
  if (!script) throw new Error('没有插入 script，无法触发 onload');
  script.dispatchEvent(new Event('load'));
}

function fireError(): void {
  const script = inserted();
  if (!script) throw new Error('没有插入 script，无法触发 onerror');
  script.dispatchEvent(new Event('error'));
}

beforeEach(() => {
  resetAmapLoaderForTest();
  vi.useRealTimers();
  document.head.querySelectorAll('script[data-amap-loader]').forEach((s) => s.remove());
  Reflect.deleteProperty(window, 'AMap');
  Reflect.deleteProperty(window, '_AMapSecurityConfig');
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('前置条件', () => {
  it('已有 window.AMap 就直接返回，一个 script 都不插', async () => {
    Reflect.set(window, 'AMap', fakeNs);
    expect(isAmapReady()).toBe(true);
    await expect(loadAmapScript({ key: 'any' })).resolves.toBe(fakeNs);
    expect(inserted()).toBeNull();
  });

  it('key 为空时明确 reject，且不发起网络请求', async () => {
    await expect(loadAmapScript({ key: '   ' })).rejects.toThrow('未配置高德 JS API Key');
    expect(inserted()).toBeNull();
  });

  it('没有浏览器环境时明确报错，不静默成功', async () => {
    vi.stubGlobal('window', undefined);
    await expect(loadAmapScript({ key: 'k' })).rejects.toThrow('浏览器环境');
  });
});

describe('正常加载', () => {
  it('插入带 key 的 script，onload 后 resolve 出命名空间', async () => {
    const p = loadAmapScript({ key: 'my key' });
    const script = inserted();
    expect(script).not.toBeNull();
    expect(script?.src).toContain('v=2.0&key=my%20key');
    expect(script?.async).toBe(true);
    expect(isAmapReady()).toBe(false);
    fireLoad();
    await expect(p).resolves.toBe(fakeNs);
    expect(isAmapReady()).toBe(true);
  });

  it('安全密钥写成官方字段 securityJsCode（不是 securityCode）', async () => {
    const p = loadAmapScript({ key: 'k', securityJsCode: '  secret  ' });
    const cfg = (window as unknown as { _AMapSecurityConfig?: Record<string, unknown> })
      ._AMapSecurityConfig;
    // 配置必须在插 script 之前就写好，所以这里同步就能读到
    expect(inserted()).not.toBeNull();
    expect(cfg).toEqual({ securityJsCode: 'secret' });
    expect(cfg).not.toHaveProperty('securityCode');
    fireLoad();
    await p;
  });

  it('也支持生产推荐的 serviceHost 代理转发', async () => {
    const p = loadAmapScript({ key: 'k', serviceHost: ' https://x.test/_AMapService ' });
    expect((window as unknown as { _AMapSecurityConfig?: Record<string, unknown> })
      ._AMapSecurityConfig).toEqual({ serviceHost: 'https://x.test/_AMapService' });
    fireLoad();
    await p;
  });

  it('没给安全配置时不覆盖别人已经设好的全局配置', async () => {
    Reflect.set(window, '_AMapSecurityConfig', { securityJsCode: 'preset' });
    const p = loadAmapScript({ key: 'k' });
    expect((window as unknown as { _AMapSecurityConfig?: Record<string, unknown> })
      ._AMapSecurityConfig).toEqual({ securityJsCode: 'preset' });
    fireLoad();
    await p;
  });

  it('并发调用共享同一个 Promise，只插一个 script', async () => {
    const p1 = loadAmapScript({ key: 'k' });
    const p2 = loadAmapScript({ key: 'k' });
    expect(document.querySelectorAll('script[data-amap-loader]')).toHaveLength(1);
    fireLoad();
    await expect(p1).resolves.toBe(fakeNs);
    await expect(p2).resolves.toBe(fakeNs);
  });

  it('复用已存在的 script[data-amap-loader]，不重复插入', async () => {
    const existing = document.createElement('script');
    existing.setAttribute('data-amap-loader', '1');
    document.head.appendChild(existing);
    const p = loadAmapScript({ key: 'k' });
    expect(document.querySelectorAll('script[data-amap-loader]')).toHaveLength(1);
    Reflect.set(window, 'AMap', fakeNs);
    existing.dispatchEvent(new Event('load'));
    await expect(p).resolves.toBe(fakeNs);
  });

  it('成功会被缓存，第二次调用不再插 script', async () => {
    const p1 = loadAmapScript({ key: 'k' });
    fireLoad();
    await p1;
    Reflect.deleteProperty(window, 'AMap'); // 模拟 SDK 全局被别的逻辑清掉
    await expect(loadAmapScript({ key: 'k' })).resolves.toBe(fakeNs);
    expect(document.querySelectorAll('script[data-amap-loader]')).toHaveLength(1);
  });
});

describe('失败与重试', () => {
  it('onerror 时 reject、移除死标签、清掉去重锁', async () => {
    const p = loadAmapScript({ key: 'k' });
    fireError();
    await expect(p).rejects.toThrow('脚本加载失败');
    expect(inserted()).toBeNull();
    expect(isAmapReady()).toBe(false);

    const p2 = loadAmapScript({ key: 'k' });
    expect(inserted()).not.toBeNull();
    fireLoad();
    await expect(p2).resolves.toBe(fakeNs);
  });

  it('超时用调用方给的时长，超时后仍可重试', async () => {
    vi.useFakeTimers();
    const p1 = loadAmapScript({ key: 'k', timeoutMs: 8000 });
    // 先把 catch 挂上再推时钟：否则 advanceTimersByTime 内部 flush microtask 时
    // reject 已经发生但还没有 handler，vitest 会记一笔 Unhandled Rejection。
    const rejects = expect(p1).rejects.toThrow('超时（8000ms）');
    await vi.advanceTimersByTimeAsync(7999);
    expect(inserted()).not.toBeNull();
    await vi.advanceTimersByTimeAsync(1);
    await rejects;
    expect(inserted()).toBeNull();
    vi.useRealTimers();

    const p2 = loadAmapScript({ key: 'k' });
    fireLoad();
    await expect(p2).resolves.toBe(fakeNs);
  });

  it('超时那一刻脚本刚好加载完 → 直接采纳，不误判失败', async () => {
    vi.useFakeTimers();
    const p = loadAmapScript({ key: 'k', timeoutMs: 8000 });
    Reflect.set(window, 'AMap', fakeNs);
    await vi.advanceTimersByTimeAsync(8000);
    await expect(p).resolves.toBe(fakeNs);
    vi.useRealTimers();
  });

  it('script 报了 onload 但 window.AMap 没出现 → reject（key 可能无效）', async () => {
    const p = loadAmapScript({ key: 'k' });
    inserted()?.dispatchEvent(new Event('load'));
    await expect(p).rejects.toThrow('window.AMap 未定义');
  });
});
