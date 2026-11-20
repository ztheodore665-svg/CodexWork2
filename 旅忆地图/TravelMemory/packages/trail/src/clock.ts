/**
 * 播放时钟。q 不放进 React state —— 每帧变化的量只能活在可变对象里。
 */

export class Clock {
  /** 0..1 归一化进度 */
  q = 0;
  playing = false;
  speed = 1;
  /** 1x 下跑完整条轨迹的时长 */
  durationMs = 42000;

  /** 推进时钟。到 1 时停住，不越界。 */
  tick(dtMs: number): void {
    if (!this.playing) return;
    if (dtMs <= 0) return;
    const next = this.q + (dtMs * this.speed) / this.durationMs;
    if (next >= 1) {
      this.q = 1;
      this.playing = false;
      return;
    }
    this.q = next;
  }

  seek(q: number): void {
    // NaN 无兜底会把 q 永久污染成 NaN（之后所有进度都算不出来）
    this.q = !Number.isFinite(q) ? 0 : q < 0 ? 0 : q > 1 ? 1 : q;
  }

  /** 播放/暂停切换；已播完时按播放则从头开始 */
  toggle(): void {
    if (this.q >= 1) {
      this.q = 0;
      this.playing = true;
      return;
    }
    this.playing = !this.playing;
  }

  play(): void {
    if (this.q >= 1) this.q = 0;
    this.playing = true;
  }

  pause(): void {
    this.playing = false;
  }

  setSpeed(speed: number): void {
    this.speed = speed;
  }

  replay(): void {
    this.q = 0;
    this.playing = true;
  }
}
