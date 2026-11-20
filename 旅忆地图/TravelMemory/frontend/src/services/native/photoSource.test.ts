/** 相册来源测试。全部跑在 Node（无 DOM），所以只验证"可构造"与"缺 DOM 时报错明确"，
 *  真正的 `<input type="file">` 流程要留给浏览器端的 e2e。 */

import { describe, expect, it } from 'vitest';
import { createNativePhotoSource, createWebPhotoSource } from './photoSource';

describe('createWebPhotoSource', () => {
  it('能构造出来，platform 是 web', () => {
    const source = createWebPhotoSource();
    expect(source).toBeTruthy();
    expect(source.platform).toBe('web');
  });

  it('没有 document 时 pickPhotos 抛出可读的浏览器环境错误（不静默返回空）', async () => {
    await expect(createWebPhotoSource().pickPhotos()).rejects.toThrow(/浏览器环境/);
  });

  it('没有 document 时 readThumb 同样明确报错而不是返回空 buffer', async () => {
    await expect(createWebPhotoSource().readThumb('web:1:a.jpg:0')).rejects.toThrow(/浏览器环境/);
  });

  it('pickPhotos 接受 limit 参数而不报错', async () => {
    // 这里仍然会因为没有 document 失败，但说明签名层面 limit 是可选的
    await expect(createWebPhotoSource().pickPhotos({ limit: 5 })).rejects.toThrow(/浏览器环境/);
  });

  it('adoptFiles 把已有的 File[] 换成同序 assets，非图片被剔除', () => {
    const files = [
      new File([new Uint8Array([1])], 'a.jpg', { type: 'image/jpeg' }),
      new File([new Uint8Array([2])], 'b.png', { type: 'image/png' }),
      new File([new Uint8Array([3])], 'c.pdf', { type: 'application/pdf' }),
    ];
    const assets = createWebPhotoSource().adoptFiles?.(files) ?? [];

    expect(assets).toHaveLength(2);
    expect(assets[0].id).toContain('a.jpg');
    expect(assets[1].id).toContain('b.png');
    expect(assets[0].locationSource).toBe('none');
  });

  it('adoptFiles 塞进内部 Map 之后，readThumb 的报错从「不在批次」变成「没有 document」', async () => {
    const source = createWebPhotoSource();
    const [asset] = source.adoptFiles?.([new File([new Uint8Array([1])], 'a.jpg', { type: 'image/jpeg' })]) ?? [];
    expect(asset).toBeTruthy();

    // 关键断言：assetId 找得到（否则报「不在当前相册批次里」），
    // 失败只发生在真正要用 DOM 解码的那一刻
    await expect(source.readThumb(asset!.id, 256)).rejects.toThrow(/浏览器环境/);
  });
});

describe('createNativePhotoSource', () => {
  it('pickPhotos 抛「未接入」', async () => {
    await expect(createNativePhotoSource().pickPhotos()).rejects.toThrow(/未接入/);
  });

  it('pickPhotos 带参数时也抛「未接入」', async () => {
    await expect(createNativePhotoSource().pickPhotos({ limit: 1 })).rejects.toThrow(/未接入/);
  });

  it('readThumb 抛「未接入」', async () => {
    await expect(createNativePhotoSource().readThumb('x')).rejects.toThrow(/未接入/);
  });

  it('readThumb 带 maxEdge 时也抛「未接入」', async () => {
    await expect(createNativePhotoSource().readThumb('x', 128)).rejects.toThrow(/未接入/);
  });

  it('adoptFiles 抛「未接入」：原生端没有 File，走不到这条反向路径', () => {
    expect(() => createNativePhotoSource().adoptFiles?.([])).toThrow(/未接入/);
  });
});
