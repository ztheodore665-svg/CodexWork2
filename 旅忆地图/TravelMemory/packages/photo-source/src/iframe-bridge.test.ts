import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  createLibrarySource,
  getInjectedPhotos,
  isEmbedded,
  resetInjectedPhotos,
  setInjectedPhotos,
} from './iframe-bridge';
import { MAX_THUMBNAIL_BYTES, type SystemPhoto } from './types';

const THUMB = `data:image/jpeg;base64,${'C'.repeat(400)}`;

function makePhoto(id: string): SystemPhoto {
  return {
    id,
    bytes: new ArrayBuffer(8),
    name: `${id}.jpg`,
    thumbnailDataUrl: THUMB,
  };
}

/** 改写地址栏查询串，模拟"被主应用以 ?embedded=1 嵌入"。 */
function setEmbedded(embedded: boolean): void {
  window.history.replaceState(null, '', embedded ? '/?embedded=1' : '/');
}

beforeEach(() => {
  resetInjectedPhotos();
  setEmbedded(false);
});

afterEach(() => {
  resetInjectedPhotos();
  setEmbedded(false);
});

describe('isEmbedded', () => {
  it('查询串没有 embedded=1 时为 false', () => {
    expect(isEmbedded()).toBe(false);
    window.history.replaceState(null, '', '/?embedded=0');
    expect(isEmbedded()).toBe(false);
    window.history.replaceState(null, '', '/?foo=bar');
    expect(isEmbedded()).toBe(false);
  });

  it('?embedded=1 时为 true', () => {
    setEmbedded(true);
    expect(isEmbedded()).toBe(true);
  });
});

describe('createLibrarySource —— available()', () => {
  it('未嵌入 + 未注入 → false', () => {
    expect(createLibrarySource().available()).toBe(false);
  });

  it('嵌入但未注入（主应用还没发 init）→ false', () => {
    setEmbedded(true);
    expect(createLibrarySource().available()).toBe(false);
  });

  it('注入了但没嵌入 → false', () => {
    setInjectedPhotos([makePhoto('p_1')]);
    expect(createLibrarySource().available()).toBe(false);
  });

  it('嵌入 + 注入非空 → true', () => {
    setEmbedded(true);
    setInjectedPhotos([makePhoto('p_1')]);
    expect(createLibrarySource().available()).toBe(true);
  });

  it('resetInjectedPhotos 之后回到 false', () => {
    setEmbedded(true);
    setInjectedPhotos([makePhoto('p_1')]);
    expect(createLibrarySource().available()).toBe(true);
    resetInjectedPhotos();
    expect(createLibrarySource().available()).toBe(false);
    expect(getInjectedPhotos()).toEqual([]);
  });
});

describe('createLibrarySource —— pick()', () => {
  it('可用时返回注入的照片副本（改了返回值不影响内部状态）', async () => {
    setEmbedded(true);
    setInjectedPhotos([makePhoto('p_1'), makePhoto('p_2')]);
    const picked = await createLibrarySource().pick();

    expect(picked.map((photo) => photo.id)).toEqual(['p_1', 'p_2']);
    picked.pop();
    expect(getInjectedPhotos()).toHaveLength(2);
  });

  it('未注入时抛中文错误', async () => {
    setEmbedded(true);
    await expect(createLibrarySource().pick()).rejects.toThrow(/还没有注入相册照片/);
  });

  it('未嵌入时抛中文错误（调用方漏判 available 的兜底）', async () => {
    setInjectedPhotos([makePhoto('p_1')]);
    await expect(createLibrarySource().pick()).rejects.toThrow(/不在主应用嵌入模式/);
  });
});

describe('setInjectedPhotos —— 脏数据过滤', () => {
  it('过滤掉结构不对的元素，只保留像 SystemPhoto 的', () => {
    setInjectedPhotos([
      makePhoto('p_ok'),
      { id: 'no-thumb', name: 'x.jpg', bytes: new ArrayBuffer(1) } as unknown as SystemPhoto,
      { name: 'no-id', thumbnailDataUrl: THUMB } as unknown as SystemPhoto,
      null as unknown as SystemPhoto,
      'garbage' as unknown as SystemPhoto,
    ]);
    expect(getInjectedPhotos().map((photo) => photo.id)).toEqual(['p_ok']);
  });

  it('传 null / undefined / 非数组等于清空', () => {
    setInjectedPhotos([makePhoto('p_1')]);
    setInjectedPhotos(null);
    expect(getInjectedPhotos()).toEqual([]);
    setInjectedPhotos([makePhoto('p_2')]);
    setInjectedPhotos(undefined);
    expect(getInjectedPhotos()).toEqual([]);
    setInjectedPhotos([makePhoto('p_3')]);
    setInjectedPhotos('nope' as unknown as SystemPhoto[]);
    expect(getInjectedPhotos()).toEqual([]);
  });
});

describe('常量一致性', () => {
  it('MAX_THUMBNAIL_BYTES 与 journey-transfer 保持一致（200KB）', () => {
    // 两个包零依赖，只能靠这个断言在当前仓库里提醒"改了要同步"
    expect(MAX_THUMBNAIL_BYTES).toBe(200_000);
  });
});
