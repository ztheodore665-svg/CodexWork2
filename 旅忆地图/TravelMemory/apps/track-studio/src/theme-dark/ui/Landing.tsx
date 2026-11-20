/** 深色版首屏（阶段 13 / W13-2：从 `showcase/src/ui/Landing.tsx` 移植并改造）。
 *
 * 与 showcase 的差异只有**两条入口**这一处（W13-2 要求 4）：
 *
 * 1. 「用演示轨迹开始」→ 展开内置演示线（`data/demoJourneys.ts`），一键进播放；
 * 2. 「从系统相册导入」→ 走 `core/photoImport` 那条链路（`<input type=file>` 多选），
 *    导入后进补点页（`SetupPanel`），没有坐标的照片按浅色版同一套规则处理
 *    （保留在列表里、标「待定位」、**绝不伪造坐标**）。
 *
 * 刻意**不出现**的两个入口（W13-2 要求 6）：
 *  - 「从主应用相册选」—— 深色版是独立打开的页面，没有父窗口给它注入照片；
 *  - 「导回主应用」—— 同上，没有可回传的父窗口。
 *
 * 拖拽导入保留：一张一张点文件选择器在桌面上太慢，而拖拽天然就是"多张照片"。
 */

import { useRef, useState, type DragEvent } from 'react';

export interface DemoTripSummary {
  id: string;
  title: string;
  subtitle: string;
  photoCount: number;
}

export interface LandingProps {
  trips: DemoTripSummary[];
  onSelectDemo: (id: string) => void;
  /** 打开系统相册（`<input type="file" multiple>`）。 */
  onPickFiles: () => void;
  /** 拖进来的文件，交给上层走同一条导入链路。 */
  onDropFiles: (files: File[]) => void;
  /** 正在读照片（禁用按钮，避免连点导入两批）。 */
  busy: boolean;
  /** 导入失败的提示（读取照片异常）。 */
  error: string | null;
}

export function Landing({
  trips,
  onSelectDemo,
  onPickFiles,
  onDropFiles,
  busy,
  error,
}: LandingProps): JSX.Element {
  const [showDemos, setShowDemos] = useState(false);
  const [dragging, setDragging] = useState(false);
  /** dragenter/dragleave 会在子元素间反复触发，用深度计数判断"真的离开了"。 */
  const dragDepth = useRef(0);

  const handleDragEnter = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault();
    dragDepth.current += 1;
    setDragging(true);
  };

  const handleDragLeave = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length > 0) onDropFiles(files);
  };

  return (
    <div
      className={`landing${dragging ? ' drop-active' : ''}`}
      onDragEnter={handleDragEnter}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <div className="landing-grid" />
      <div className="landing-inner fade-in">
        <div className="landing-kicker">Travelmemory / 轨迹演示</div>
        <h1 className="landing-title">旅忆地图</h1>
        <p className="landing-sub">
          把散落在相册里的照片，还原成一条会生长的旅程轨迹。
          <br />
          拖动时间轴回看任意时刻，镜头会跟着光点一起走。
        </p>

        <div className="landing-actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setShowDemos((value) => !value)}
          >
            用演示轨迹开始
          </button>
          <button type="button" className="btn" disabled={busy} onClick={onPickFiles}>
            {busy ? '正在读取照片…' : '从系统相册导入'}
          </button>
        </div>

        {showDemos ? (
          <div className="landing-demos fade-in">
            {trips.map((trip) => (
              <button
                key={trip.id}
                type="button"
                className="demo-card"
                onClick={() => onSelectDemo(trip.id)}
                style={{ textAlign: 'left' }}
              >
                <div className="demo-card-title">{trip.title}</div>
                <div className="demo-card-meta">
                  {trip.subtitle} · {trip.photoCount} 张
                </div>
              </button>
            ))}
          </div>
        ) : null}

        {error ? <p className="setup-hint">{error}</p> : null}

        <div className="drop-hint">也可以直接把照片拖进这个页面</div>
      </div>
      <div className="landing-foot">LOCAL ONLY · 照片不会离开你的电脑</div>
    </div>
  );
}
