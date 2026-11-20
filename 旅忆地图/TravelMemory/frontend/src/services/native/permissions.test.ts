/** 权限测试。Node 环境下没有 navigator.permissions，正好覆盖降级分支。 */

import { describe, expect, it } from 'vitest';
import { createNativePermissions, createWebPermissions } from './permissions';

const NAMES = ['photos', 'camera', 'location'] as const;

describe('createWebPermissions', () => {
  it('query photos / camera 恒为 prompt（Web 没有可查询的权限位）', async () => {
    const permissions = createWebPermissions();
    await expect(permissions.query('photos')).resolves.toBe('prompt');
    await expect(permissions.query('camera')).resolves.toBe('prompt');
  });

  it('request photos / camera 同为 prompt：授权发生在用户选文件的那一刻', async () => {
    const permissions = createWebPermissions();
    await expect(permissions.request('photos')).resolves.toBe('prompt');
    await expect(permissions.request('camera')).resolves.toBe('prompt');
  });

  it('query location 在无 Permissions API 时为 unsupported', async () => {
    await expect(createWebPermissions().query('location')).resolves.toBe('unsupported');
  });

  it('request location 在无 geolocation 时为 unsupported，且不抛错', async () => {
    const result = await createWebPermissions().request('location');
    expect(['granted', 'denied', 'prompt', 'unsupported']).toContain(result);
    expect(
      typeof navigator === 'undefined' || !navigator.geolocation ? result === 'unsupported' : true,
    ).toBe(true);
  });

  it('不支持的能力不会让整个调用链崩溃', async () => {
    const permissions = createWebPermissions();
    for (const name of NAMES) {
      await expect(permissions.query(name)).resolves.toBeTruthy();
      await expect(permissions.request(name)).resolves.toBeTruthy();
    }
  });
});

describe('createNativePermissions', () => {
  it('query / request 都抛「未接入」', async () => {
    const permissions = createNativePermissions();
    for (const name of NAMES) {
      await expect(permissions.query(name)).rejects.toThrow(/未接入/);
      await expect(permissions.request(name)).rejects.toThrow(/未接入/);
    }
  });
});
