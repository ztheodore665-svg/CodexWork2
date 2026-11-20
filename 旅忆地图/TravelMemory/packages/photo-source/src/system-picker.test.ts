import { describe, expect, it } from 'vitest';

import {
  createRuntimePhotoId,
  createSystemSource,
  pickFromSystem,
  readPhotoFile,
  type PickSystemDeps,
} from './system-picker';
import { THUMBNAIL_MAX_EDGE } from './thumbnail';

const THUMB = `data:image/jpeg;base64,${'B'.repeat(400)}`;

function makeFile(content: string, name = 'IMG_0001.jpg', type = 'image/jpeg'): File {
  return new File([content], name, { type });
}

/** 测试用的最小依赖：不碰真实图片解码。 */
function baseDeps(overrides: PickSystemDeps = {}): PickSystemDeps {
  return {
    readExif: async () => ({}),
    createThumbnail: async () => THUMB,
    ...overrides,
  };
}

describe('createRuntimePhotoId', () => {
  it('形如 p_<16 位 hex>，每次不同', () => {
    const first = createRuntimePhotoId();
    const second = createRuntimePhotoId();
    expect(first).toMatch(/^p_[0-9a-f]{16}$/);
    expect(second).toMatch(/^p_[0-9a-f]{16}$/);
    expect(first).not.toBe(second);
  });
});

describe('readPhotoFile', () => {
  it('返回结构正确的 SystemPhoto（字节 / 文件名 / EXIF / 缩略图）', async () => {
    const photo = await readPhotoFile(
      makeFile('hello-world-bytes'),
      baseDeps({
        readExif: async () => ({ takenAt: '2026-03-01T02:03:04.000Z', lat: 30.259, lng: 120.13 }),
      }),
    );

    expect(photo.id).toMatch(/^p_[0-9a-f]{16}$/);
    expect(photo.name).toBe('IMG_0001.jpg');
    expect(photo.bytes).toBeInstanceOf(ArrayBuffer);
    expect(new TextDecoder().decode(photo.bytes)).toBe('hello-world-bytes');
    expect(photo.takenAt).toBe('2026-03-01T02:03:04.000Z');
    expect(photo.lat).toBeCloseTo(30.259, 6);
    expect(photo.lng).toBeCloseTo(120.13, 6);
    expect(photo.thumbnailDataUrl).toBe(THUMB);
    expect(photo.thumbnailDataUrl.startsWith('data:image/')).toBe(true);
  });

  it('读不到 EXIF 时只缺 takenAt/lat/lng，照片仍然返回', async () => {
    const photo = await readPhotoFile(
      makeFile('x'),
      baseDeps({ readExif: async () => ({}) }),
    );
    expect(photo.takenAt).toBeUndefined();
    expect(photo.lat).toBeUndefined();
    expect(photo.lng).toBeUndefined();
    expect(photo.bytes.byteLength).toBe(1);
  });

  it('EXIF 读取抛错时被吞掉，不影响照片本身', async () => {
    const photo = await readPhotoFile(
      makeFile('abc'),
      baseDeps({
        readExif: async () => {
          throw new Error('exif boom');
        },
      }),
    );
    expect(photo.takenAt).toBeUndefined();
    expect(new TextDecoder().decode(photo.bytes)).toBe('abc');
  });

  it('缩略图生成抛错时退化成内置 1×1 PNG，不抛错', async () => {
    const photo = await readPhotoFile(
      makeFile('abc'),
      baseDeps({
        createThumbnail: async () => {
          throw new Error('canvas boom');
        },
      }),
    );
    expect(photo.thumbnailDataUrl.startsWith('data:image/')).toBe(true);
  });

  it('缺省 readBytes 能读 Blob（arrayBuffer 不可用时退回 FileReader）', async () => {
    const photo = await readPhotoFile(
      makeFile('default-reader-ok'),
      baseDeps({ readExif: async () => ({}) }),
    );
    expect(new TextDecoder().decode(photo.bytes)).toBe('default-reader-ok');
  });

  it('没有 name 的 Blob 给中性兜底名', async () => {
    const photo = await readPhotoFile(new Blob(['z']), baseDeps());
    expect(photo.name).toBe('photo');
  });

  it('缩略图长边上限透传给生成器（缺省 480）', async () => {
    const received: number[] = [];
    await readPhotoFile(
      makeFile('x'),
      baseDeps({
        createThumbnail: async (_file, maxEdge) => {
          received.push(maxEdge);
          return THUMB;
        },
      }),
    );
    await readPhotoFile(
      makeFile('x'),
      baseDeps({
        maxEdge: 320,
        createThumbnail: async (_file, maxEdge) => {
          received.push(maxEdge);
          return THUMB;
        },
      }),
    );
    expect(received).toEqual([THUMBNAIL_MAX_EDGE, 320]);
  });
});

describe('pickFromSystem', () => {
  it('选中的文件被读成 SystemPhoto 数组', async () => {
    const files = [makeFile('one', 'a.jpg'), makeFile('two', 'b.jpg')];
    const photos = await pickFromSystem(
      {},
      baseDeps({ waitForSelection: async () => files }),
    );

    expect(photos).toHaveLength(2);
    expect(photos.map((photo) => photo.name)).toEqual(['a.jpg', 'b.jpg']);
    expect(new TextDecoder().decode(photos[1].bytes)).toBe('two');
  });

  it('用户取消（选择器 resolve 空数组）时返回 []', async () => {
    const photos = await pickFromSystem({}, baseDeps({ waitForSelection: async () => [] }));
    expect(photos).toEqual([]);
  });

  it('waitForSelection 抛错时返回 []（不把异常抛给 UI）', async () => {
    const photos = await pickFromSystem(
      {},
      baseDeps({
        waitForSelection: async () => {
          throw new Error('picker boom');
        },
      }),
    );
    expect(photos).toEqual([]);
  });

  it('limit 生效', async () => {
    const files = [makeFile('1'), makeFile('2'), makeFile('3'), makeFile('4')];
    const photos = await pickFromSystem(
      { limit: 2 },
      baseDeps({ waitForSelection: async () => files }),
    );
    expect(photos).toHaveLength(2);
  });

  it('limit 非法（0 / 负数 / NaN）时视为不限', async () => {
    const files = [makeFile('1'), makeFile('2'), makeFile('3')];
    for (const limit of [0, -5, Number.NaN]) {
      const photos = await pickFromSystem(
        { limit },
        baseDeps({ waitForSelection: async () => files }),
      );
      expect(photos).toHaveLength(3);
    }
  });

  it('单张读失败只跳过那一张', async () => {
    const files = [makeFile('ok-1'), makeFile('bad'), makeFile('ok-2')];
    let call = 0;
    const photos = await pickFromSystem(
      {},
      baseDeps({
        waitForSelection: async () => files,
        // 注意：jsdom 25 的 Blob **没有** `arrayBuffer()`，缺省实现因此走 FileReader 分支；
        // 这里注入的假 reader 用固定字节，避免依赖宿主实现。
        readBytes: async (file) => {
          call += 1;
          if (call === 2) throw new Error('read boom');
          return new TextEncoder().encode(file.size.toString()).buffer as ArrayBuffer;
        },
      }),
    );
    expect(call).toBe(3);
    expect(photos).toHaveLength(2);
  });
});

describe('createSystemSource', () => {
  it('id / label / available / pick 符合 PhotoSource 契约', async () => {
    const source = createSystemSource({}, baseDeps({ waitForSelection: async () => [makeFile('q')] }));
    expect(source.id).toBe('system');
    expect(source.label).toBe('从系统相册选');
    expect(source.available()).toBe(true);
    const photos = await source.pick();
    expect(photos).toHaveLength(1);
  });
});
