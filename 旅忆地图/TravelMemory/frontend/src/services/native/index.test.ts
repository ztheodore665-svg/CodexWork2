/** NativeBridge 出口的测试。跑在 Node（无 DOM）环境，不允许依赖 window/document。 */

import { describe, expect, it } from 'vitest';
import type { NativeBridge, PhotoAsset, PermissionState } from '../../types/native';
import {
  canUseDomNative,
  resetNativeBridge,
  resolveNativeBridge,
  setNativeBridge,
} from './index';

/** 测试用的假桥接。造出来是为了验证"注入覆盖"这条逃生通道本身是通的------
 *  真机接入时走的就是同一个 `setNativeBridge`。 */
function fakeBridge(): NativeBridge {
  const assets: PhotoAsset[] = [{ id: 'native:1', width: 10, height: 10 }];
  return {
    platform: 'android',
    photos: {
      platform: 'android',
      async pickPhotos() {
        return assets;
      },
      async readThumb() {
        return new ArrayBuffer(8);
      },
    },
    permissions: {
      async query(): Promise<PermissionState> {
        return 'granted';
      },
      async request(): Promise<PermissionState> {
        return 'granted';
      },
    },
    thumbs: {
      async get() {
        return null;
      },
      async put() {},
      async clear() {},
      get sizeHint(): number {
        return 0;
      },
    },
  };
}

describe('resolveNativeBridge', () => {
  it('默认解析到 web 平台，且四个能力都有实现', () => {
    const bridge = resolveNativeBridge();
    expect(bridge.platform).toBe('web');
    expect(bridge.photos).toBeTruthy();
    expect(bridge.permissions).toBeTruthy();
    expect(bridge.thumbs).toBeTruthy();
  });

  it('无 DOM 时不崩：Node 环境也能拿到 bridge', () => {
    expect(canUseDomNative()).toBe(false);
    expect(() => resolveNativeBridge()).not.toThrow();
  });

  it('连续两次解析拿到同一个实例（PhotoSource 有状态，必须复用）', () => {
    expect(resolveNativeBridge().photos).toBe(resolveNativeBridge().photos);
  });

  it('setNativeBridge 可注入覆盖', () => {
    const fake = fakeBridge();
    setNativeBridge(fake);
    expect(resolveNativeBridge()).toBe(fake);
    expect(resolveNativeBridge().platform).toBe('android');
  });

  it('resetNativeBridge 后回到默认实现', () => {
    setNativeBridge(fakeBridge());
    resetNativeBridge();
    expect(resolveNativeBridge()).not.toBe(fakeBridge());
    expect(resolveNativeBridge().platform).toBe('web');
  });

  it('setNativeBridge(null) 等价于 reset', () => {
    setNativeBridge(fakeBridge());
    setNativeBridge(null);
    expect(resolveNativeBridge().platform).toBe('web');
  });
});
