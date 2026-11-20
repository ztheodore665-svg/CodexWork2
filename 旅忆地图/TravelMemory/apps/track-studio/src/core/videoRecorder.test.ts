import { describe, expect, it } from 'vitest';

import { canRecord, isMobileUA, pickMimeType, recordCanvas } from './videoRecorder';

describe('isMobileUA', () => {
  it('识别 Android / iPhone / iPad', () => {
    expect(
      isMobileUA(
        'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36',
      ),
    ).toBe(true);
    expect(
      isMobileUA(
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1',
      ),
    ).toBe(true);
    expect(
      isMobileUA(
        'Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
      ),
    ).toBe(true);
  });

  it('桌面 Chrome / Firefox / Safari 不是移动端', () => {
    expect(
      isMobileUA(
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36',
      ),
    ).toBe(false);
    expect(isMobileUA('Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0')).toBe(
      false,
    );
    expect(
      isMobileUA(
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
      ),
    ).toBe(false);
  });

  it('空 UA 不算移动端', () => {
    expect(isMobileUA('')).toBe(false);
  });
});

describe('canRecord（node 环境没有 window/MediaRecorder）', () => {
  it('返回 false', () => {
    expect(canRecord()).toBe(false);
  });

  it('pickMimeType 返回 null', () => {
    expect(pickMimeType()).toBeNull();
  });
});

describe('recordCanvas（不可录时）', () => {
  it('直接返回 null，不抛错', async () => {
    const canvas = {} as HTMLCanvasElement;
    const result = await recordCanvas(canvas, 1000, {
      play: async () => {
        /* 不会被调用 */
      },
    });
    expect(result).toBeNull();
  });

  it('即使 play 会抛错也不会被调用（提前返回）', async () => {
    let called = false;
    const canvas = {} as HTMLCanvasElement;
    const result = await recordCanvas(canvas, 1000, {
      play: async () => {
        called = true;
        throw new Error('boom');
      },
    });
    expect(result).toBeNull();
    expect(called).toBe(false);
  });
});
