import { describe, expect, it } from 'vitest';

import { readExifMeta, toDecimalDegrees, toIsoUtc } from './exif';

/**
 * 造一张**只有 EXIF 的最小 JPEG**：SOI + APP1(Exif) + EOI。
 *
 * TIFF 结构（小端）：
 * ```
 * 0   II 2A 00 [IFD0 偏移=8]
 * 8   IFD0: 1 项 → ExifIFDPointer(0x8769) = 26
 * 26  ExifIFD: 1 项 → DateTimeOriginal(0x9003) ASCII[20] 偏移 44
 * 44  'YYYY:MM:DD HH:MM:SS\0'
 * ```
 * 没有像素数据 —— exifr 只扫 marker，看得懂。
 */
function buildExifJpeg(dateTimeOriginal: string): ArrayBuffer {
  const tiff = new Uint8Array(64);
  const view = new DataView(tiff.buffer);
  tiff[0] = 0x49; // 'I'
  tiff[1] = 0x49; // 'I'
  view.setUint16(2, 42, true); // magic 42
  view.setUint32(4, 8, true); // IFD0 偏移

  view.setUint16(8, 1, true); // IFD0 条目数
  view.setUint16(10, 0x8769, true); // ExifIFDPointer
  view.setUint16(12, 4, true); // LONG
  view.setUint32(14, 1, true);
  view.setUint32(18, 26, true); // → Exif IFD
  view.setUint32(22, 0, true); // 下一个 IFD = 无

  view.setUint16(26, 1, true); // Exif IFD 条目数
  view.setUint16(28, 0x9003, true); // DateTimeOriginal
  view.setUint16(30, 2, true); // ASCII
  view.setUint32(32, 20, true);
  view.setUint32(36, 44, true); // → 字符串
  view.setUint32(40, 0, true);

  for (let index = 0; index < 19; index += 1) tiff[44 + index] = dateTimeOriginal.charCodeAt(index);
  tiff[63] = 0;

  const app1Payload = 6 + tiff.length; // 'Exif\0\0' + TIFF
  const segmentLength = app1Payload + 2; // 长度字段含自身 2 字节
  const jpeg = new Uint8Array(2 + 2 + 2 + app1Payload + 2);
  jpeg[0] = 0xff;
  jpeg[1] = 0xd8; // SOI
  jpeg[2] = 0xff;
  jpeg[3] = 0xe1; // APP1
  jpeg[4] = (segmentLength >> 8) & 0xff;
  jpeg[5] = segmentLength & 0xff;
  jpeg.set([0x45, 0x78, 0x69, 0x66, 0x00, 0x00], 6); // 'Exif\0\0'
  jpeg.set(tiff, 12);
  jpeg[jpeg.length - 2] = 0xff;
  jpeg[jpeg.length - 1] = 0xd9; // EOI
  return jpeg.buffer as ArrayBuffer;
}

describe('readExifMeta', () => {
  it('从真实 EXIF 里读出 DateTimeOriginal 并转成 ISO UTC（B14：EXIF 墙钟按 UTC 存）', async () => {
    const meta = await readExifMeta(buildExifJpeg('2026:01:01 14:05:00'));
    expect(meta.takenAt).toBe('2026-01-01T14:05:00.000Z');
    // 这张图没有 GPS：宁缺勿错，不写半个坐标
    expect(meta.lat).toBeUndefined();
    expect(meta.lng).toBeUndefined();
  });

  it('随机字节（不是图片）返回 {} 且不抛错', async () => {
    const random = new Uint8Array(128);
    for (let index = 0; index < random.length; index += 1) random[index] = (index * 37) % 251;
    await expect(readExifMeta(random.buffer as ArrayBuffer)).resolves.toEqual({});
  });

  it('空 buffer / 过短 buffer 返回 {}', async () => {
    await expect(readExifMeta(new ArrayBuffer(0))).resolves.toEqual({});
    await expect(readExifMeta(new ArrayBuffer(2))).resolves.toEqual({});
  });

  it('传入非 ArrayBuffer 也不抛错（postMessage 收到脏数据）', async () => {
    await expect(readExifMeta(undefined as unknown as ArrayBuffer)).resolves.toEqual({});
    await expect(readExifMeta('not-bytes' as unknown as ArrayBuffer)).resolves.toEqual({});
  });
});

describe('toIsoUtc —— DateTimeOriginal 的三种形态', () => {
  it('EXIF 字符串 → ISO UTC（用 Date.UTC 解释墙钟，不依赖本机时区）', () => {
    expect(toIsoUtc('2026:01:01 14:05:00')).toBe('2026-01-01T14:05:00.000Z');
    expect(toIsoUtc('2026-12-31 23:59:59')).toBe('2026-12-31T23:59:59.000Z');
    expect(toIsoUtc('  2026:07:04 08:00:00  ')).toBe('2026-07-04T08:00:00.000Z');
  });

  it('数组形态 [y, m, d, h, mi, s] → ISO UTC', () => {
    expect(toIsoUtc([2026, 3, 9, 1, 2, 3])).toBe('2026-03-09T01:02:03.000Z');
    expect(toIsoUtc([2026, 3, 9])).toBe('2026-03-09T00:00:00.000Z');
  });

  it('Date 形态直接用它的时刻', () => {
    expect(toIsoUtc(new Date('2026-05-06T07:08:09.000Z'))).toBe('2026-05-06T07:08:09.000Z');
    expect(toIsoUtc(new Date('invalid'))).toBeUndefined();
  });

  it('非法值返回 undefined（不填字段）', () => {
    expect(toIsoUtc('2026:13:01 00:00:00')).toBeUndefined(); // 13 月
    expect(toIsoUtc('2026:02:30 00:00:00')).toBeUndefined(); // 2 月 30 日
    expect(toIsoUtc('0000:00:00 00:00:00')).toBeUndefined();
    expect(toIsoUtc(undefined)).toBeUndefined();
    expect(toIsoUtc(null)).toBeUndefined();
    expect(toIsoUtc({})).toBeUndefined();
  });
});

describe('toDecimalDegrees —— 度分秒与半球修正', () => {
  it('度分秒数组 → 十进制（N 为正）', () => {
    expect(toDecimalDegrees([[30, 1], [15, 1], [3240, 100]], 'N')).toBeCloseTo(30.259, 6);
  });

  it('S / W 取负（写错就会把照片放到对跖点）', () => {
    expect(toDecimalDegrees([[30, 1], [15, 1], [3240, 100]], 'S')).toBeCloseTo(-30.259, 6);
    expect(toDecimalDegrees([[120, 1], [8, 1], [24, 1]], 'W')).toBeCloseTo(-120.14, 6);
  });

  it('已是十进制的值原样保留（按 Ref 修正符号）', () => {
    expect(toDecimalDegrees(31.2304, 'N')).toBeCloseTo(31.2304, 6);
    expect(toDecimalDegrees(121.4737, 'E')).toBeCloseTo(121.4737, 6);
    expect(toDecimalDegrees(121.4737, 'W')).toBeCloseTo(-121.4737, 6);
    expect(toDecimalDegrees('31/1', 'N')).toBeCloseTo(31, 6);
  });

  it('垃圾输入返回 undefined', () => {
    expect(toDecimalDegrees(undefined, 'N')).toBeUndefined();
    expect(toDecimalDegrees('abc', 'N')).toBeUndefined();
    expect(toDecimalDegrees([], 'N')).toBeUndefined();
    expect(toDecimalDegrees([[1, 0]], 'N')).toBeUndefined(); // 分母为 0
  });
});
