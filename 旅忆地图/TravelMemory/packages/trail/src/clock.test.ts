import { describe, expect, it } from 'vitest';
import { Clock } from './clock';

/**
 * Clock 是播放进度 q 的唯一持有者。q 不进 React state（逐帧变化的量只能活在
 * 可变对象里），所以一旦 q 被污染成 NaN，整条播放链路都会失效且无法自愈。
 *
 * 本文件锁住三类不变量：
 *  1. q 恒落在 [0, 1]（tick 与 seek 两条写入口都要夹住）；
 *  2. NaN / 非有限输入必须被归零，不能穿透（src/engine/clock.ts:28 的兜底契约）；
 *  3. 播放状态机：播完置 pause、末尾再按播放从头开始、replay 归零重播。
 */
describe('Clock', () => {
  it('初始状态：q=0、未播放、1x、时长 42s', () => {
    const clock = new Clock();
    expect(clock.q).toBe(0);
    expect(clock.playing).toBe(false);
    expect(clock.speed).toBe(1);
    expect(clock.durationMs).toBe(42000);
  });

  it('未播放时 tick 不推进', () => {
    const clock = new Clock();
    clock.tick(1000);
    expect(clock.q).toBe(0);
  });

  it('tick 按 speed 线性推进', () => {
    const clock = new Clock();
    clock.play();
    clock.tick(clock.durationMs / 4);
    expect(clock.q).toBeCloseTo(0.25, 10);

    clock.setSpeed(2);
    clock.tick(clock.durationMs / 4);
    expect(clock.q).toBeCloseTo(0.75, 10);
  });

  it('tick 忽略非正的 dtMs', () => {
    const clock = new Clock();
    clock.play();
    clock.tick(-100);
    expect(clock.q).toBe(0);
    clock.tick(0);
    expect(clock.q).toBe(0);
  });

  it('tick 到 1 时停住并自动暂停（不越界）', () => {
    const clock = new Clock();
    clock.play();
    clock.tick(clock.durationMs * 0.5);
    clock.tick(clock.durationMs * 10);
    expect(clock.q).toBe(1);
    expect(clock.playing).toBe(false);
  });

  it('seek 夹到 [0, 1]', () => {
    const clock = new Clock();
    clock.seek(0.3);
    expect(clock.q).toBeCloseTo(0.3, 12);

    clock.seek(-5);
    expect(clock.q).toBe(0);

    clock.seek(5);
    expect(clock.q).toBe(1);
  });

  it('seek(NaN) 归零，不把 q 污染成 NaN', () => {
    const clock = new Clock();
    clock.seek(0.42);
    clock.seek(Number.NaN);
    expect(Number.isFinite(clock.q)).toBe(true);
    expect(clock.q).toBe(0);
  });

  it('非有限输入（NaN / ±Infinity）一律归零，不污染 q', () => {
    // 契约：所有非有限输入都回到起点 0，而不是夹到端点。
    // 注意 +Infinity 也走这条分支（`!Number.isFinite` 先命中），不夹到 1 ——
    // 这是有意的「垃圾进 → 回起点」策略；qFromClientX 的除数是 Math.max(1, ...)，
    // 实际调用链不会产出 Infinity，故不视为缺陷。
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
      const clock = new Clock();
      clock.seek(0.42);
      clock.seek(bad);
      expect(Number.isFinite(clock.q)).toBe(true);
      expect(clock.q).toBe(0);
    }
  });

  it('NaN 的 q 不会通过 tick 传播（q 始终有限）', () => {
    const clock = new Clock();
    clock.seek(Number.NaN);
    clock.play();
    clock.tick(clock.durationMs / 2);
    expect(Number.isFinite(clock.q)).toBe(true);
    expect(clock.q).toBeCloseTo(0.5, 10);
  });

  it('toggle：播放/暂停切换；播完后再按播放从头开始', () => {
    const clock = new Clock();
    clock.toggle();
    expect(clock.playing).toBe(true);
    clock.toggle();
    expect(clock.playing).toBe(false);

    clock.seek(1);
    clock.toggle();
    expect(clock.q).toBe(0);
    expect(clock.playing).toBe(true);
  });

  it('play 在末尾会从头开始；pause 保持 q', () => {
    const clock = new Clock();
    clock.seek(1);
    clock.play();
    expect(clock.q).toBe(0);
    expect(clock.playing).toBe(true);

    clock.seek(0.6);
    clock.pause();
    expect(clock.playing).toBe(false);
    expect(clock.q).toBeCloseTo(0.6, 12);
  });

  it('replay 归零并播放', () => {
    const clock = new Clock();
    clock.seek(0.9);
    clock.pause();
    clock.replay();
    expect(clock.q).toBe(0);
    expect(clock.playing).toBe(true);
  });
});
