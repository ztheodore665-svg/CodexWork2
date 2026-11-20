/** 进度条键盘操作的回归测试（WCAG 2.1.1）。
 *
 *  背景：回放层的进度条曾经带着 `role="slider"` + `tabIndex={0}`，却没有 `onKeyDown`
 *  —— 键盘用户聚焦上去后按什么都没反应。组件本身的测试很重（Radix Dialog + 高德 +
 *  canvas），所以把键位映射抽成 `sliderStep` 纯函数单独守。
 *
 *  这里只管「按键 → 目标 q」的映射；[0,1] 的夹取由 `Clock.seek` 负责，那边已有测试
 *  （packages/trail/src/clock.test.ts「seek 夹到 [0, 1]」「seek(NaN) 归零」）。
 */

import { describe, expect, it } from 'vitest';

import { sliderStep } from './ReplayStage';

describe('sliderStep · 方向键', () => {
  it('右 / 上前进 5%', () => {
    expect(sliderStep('ArrowRight', false, 0.4)).toBeCloseTo(0.45);
    expect(sliderStep('ArrowUp', false, 0.4)).toBeCloseTo(0.45);
  });

  it('左 / 下后退 5%', () => {
    expect(sliderStep('ArrowLeft', false, 0.4)).toBeCloseTo(0.35);
    expect(sliderStep('ArrowDown', false, 0.4)).toBeCloseTo(0.35);
  });

  it('按住 Shift 改成 1% 精调', () => {
    expect(sliderStep('ArrowRight', true, 0.4)).toBeCloseTo(0.41);
    expect(sliderStep('ArrowLeft', true, 0.4)).toBeCloseTo(0.39);
  });
});

describe('sliderStep · 翻页与两端', () => {
  it('PageUp / PageDown 是 ±10%，且不受 Shift 影响', () => {
    expect(sliderStep('PageUp', false, 0.4)).toBeCloseTo(0.5);
    expect(sliderStep('PageUp', true, 0.4)).toBeCloseTo(0.5);
    expect(sliderStep('PageDown', false, 0.4)).toBeCloseTo(0.3);
  });

  it('Home / End 直达两端，与当前位置无关', () => {
    expect(sliderStep('Home', false, 0.83)).toBe(0);
    expect(sliderStep('End', false, 0.12)).toBe(1);
  });
});

describe('sliderStep · 不抢别人的键', () => {
  it('Tab / Space / Enter / 字符键都返回 null（调用方便不会 preventDefault）', () => {
    for (const key of ['Tab', ' ', 'Enter', 'a', 'Escape', 'F5']) {
      expect(sliderStep(key, false, 0.5)).toBeNull();
    }
  });
});

describe('sliderStep · 越界交给 Clock.seek', () => {
  it('允许返回 <0 / >1，由 seek 统一夹住，不要在这里分叉', () => {
    expect(sliderStep('ArrowLeft', false, 0.02)).toBeLessThan(0);
    expect(sliderStep('ArrowRight', false, 0.98)).toBeGreaterThan(1);
  });
});
