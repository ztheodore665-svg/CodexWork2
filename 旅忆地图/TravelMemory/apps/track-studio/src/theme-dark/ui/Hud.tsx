/** 四角 HUD（阶段 13 / W13-2：从 `showcase/src/ui/Hud.tsx` **复制**）。
 *
 * 逻辑一行未改：`update()` 每帧被主循环调用，内部**只在值真的变化时写 DOM**，
 * 这是 60fps 下 HUD 不吃主线程的前提。日期 / 里程 / 进度 / 地名切换动画
 * （双图层交替淡入）也全部沿用 showcase 的实现。
 */

import { forwardRef, useImperativeHandle, useRef } from 'react';
import { formatDateDot, formatKmNumber, formatPercent, formatTimeHM, formatWeekday } from '@travelmemory/trail';

export interface HudHandle {
  /**
   * 每帧调用。全部参数都是原始值（不传对象），避免在主循环里产生垃圾。
   * 内部只在值真的变化时才写 DOM。
   */
  update(
    nowMs: number,
    dayIndex: number,
    dayCount: number,
    meters: number,
    progress: number,
    place: string,
  ): void;
}

interface HudProps {
  title: string;
  subtitle: string;
}

interface Cache {
  date: string;
  time: string;
  weekday: string;
  km: string;
  day: string;
  percent: string;
  place: string;
  caption: string;
}

function setText(el: HTMLElement | null, text: string): void {
  if (el && el.textContent !== text) el.textContent = text;
}

export const Hud = forwardRef<HudHandle, HudProps>(function Hud({ title, subtitle }, ref) {
  const dateRef = useRef<HTMLDivElement>(null);
  const weekdayRef = useRef<HTMLDivElement>(null);
  const timeRef = useRef<HTMLDivElement>(null);
  const kmRef = useRef<HTMLDivElement>(null);
  const dayRef = useRef<HTMLDivElement>(null);
  const percentRef = useRef<HTMLDivElement>(null);
  const placeRef = useRef<HTMLDivElement>(null);
  const captionARef = useRef<HTMLDivElement>(null);
  const captionBRef = useRef<HTMLDivElement>(null);

  const cache = useRef<Cache>({
    date: '',
    time: '',
    weekday: '',
    km: '',
    day: '',
    percent: '',
    place: '',
    caption: '',
  });
  const captionLayer = useRef(0);

  useImperativeHandle(
    ref,
    () => ({
      update(nowMs, dayIndex, dayCount, meters, progress, place) {
        const c = cache.current;

        const date = formatDateDot(nowMs);
        if (date !== c.date) {
          c.date = date;
          setText(dateRef.current, date);
        }

        const weekday = formatWeekday(nowMs);
        if (weekday !== c.weekday) {
          c.weekday = weekday;
          setText(weekdayRef.current, weekday);
        }

        const time = formatTimeHM(nowMs);
        if (time !== c.time) {
          c.time = time;
          setText(timeRef.current, time);
        }

        const km = formatKmNumber(meters);
        if (km !== c.km) {
          c.km = km;
          setText(kmRef.current, km);
        }

        const day = `第 ${dayIndex + 1} / ${Math.max(1, dayCount)} 天`;
        if (day !== c.day) {
          c.day = day;
          setText(dayRef.current, day);
        }

        const percent = formatPercent(progress);
        if (percent !== c.percent) {
          c.percent = percent;
          setText(percentRef.current, percent);
        }

        if (place !== c.place) {
          c.place = place;
          const el = placeRef.current;
          if (el) {
            el.textContent = place;
            el.classList.remove('hud-place-anim');
            // 触发 reflow 以重放动画（地名切换频率很低，代价可接受）
            void el.offsetWidth;
            el.classList.add('hud-place-anim');
          }
        }

        if (place !== c.caption) {
          c.caption = place;
          const show = captionLayer.current === 0 ? captionBRef.current : captionARef.current;
          const hide = captionLayer.current === 0 ? captionARef.current : captionBRef.current;
          if (show) {
            show.textContent = place;
            show.style.opacity = '1';
            show.style.transform = 'translateY(0)';
          }
          if (hide) {
            hide.style.opacity = '0';
            hide.style.transform = 'translateY(-6px)';
          }
          captionLayer.current = captionLayer.current === 0 ? 1 : 0;
        }
      },
    }),
    [],
  );

  return (
    <div className="hud">
      <div className="hud-corner hud-tl">
        <div className="hud-brand">Travelmemory</div>
        <div className="hud-title">{title}</div>
        <div className="hud-sub">{subtitle}</div>
      </div>

      <div className="hud-corner hud-tr">
        <div className="hud-big tnum" ref={dateRef}>
          --
        </div>
        <div className="hud-line">
          <span ref={weekdayRef}>--</span>
          <span style={{ margin: '0 6px', opacity: 0.4 }}>·</span>
          <span className="tnum" ref={timeRef}>
            --:--
          </span>
        </div>
      </div>

      <div className="hud-corner hud-bl">
        <div className="hud-line">里程</div>
        <div className="hud-big" style={{ fontSize: 17 }}>
          <span className="tnum" ref={kmRef}>
            0
          </span>
          <span style={{ fontSize: 10, color: 'var(--muted)', marginLeft: 6 }}>km</span>
        </div>
        <div className="hud-line" ref={dayRef}>
          第 1 / 1 天
        </div>
      </div>

      <div className="hud-corner hud-br">
        <div className="hud-progress" ref={percentRef}>
          0%
        </div>
        <div className="hud-place" ref={placeRef} />
      </div>

      <div className="hud-caption" style={{ position: 'absolute', width: '100%', height: 34 }}>
        <div
          ref={captionARef}
          className="hud-caption-text"
          style={{ position: 'absolute', left: 0, right: 0, opacity: 1 }}
        />
        <div
          ref={captionBRef}
          className="hud-caption-text"
          style={{ position: 'absolute', left: 0, right: 0, opacity: 0 }}
        />
      </div>
    </div>
  );
});
