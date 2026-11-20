/** 地点看板（W12-4）：embed 模式 = 地图 + 侧栏列表；standalone 模式 = 纯列表。
 *
 * ## 为什么两种模式差别这么大
 *
 * 两个入口的承诺不同（见 `src/csp.ts`）：
 *  - **embed**：允许地图底图的网络请求，所以"地图 + 侧栏联动"是主体验；
 *  - **standalone**：全程零网络请求，**连瓦片都不能请求**，所以只能文本列表。
 *
 * 这个组件是唯一切换点。调用方（生成页预览 / 接收页）只要传 `embedded`，
 * 不用自己判断该渲染哪一套。
 *
 * ## 联动：点地图 → 侧栏滚到该项
 *
 * 地图和列表是同一份 `places` 的两种视图，选中状态在这个组件里（单点真相）。
 * 点击后滚到对应项用 `scrollIntoView({ block: 'center' })`；jsdom 里没有这个方法，
 * 所以调用前判一下类型 —— 否则组件测试会因为"滚不动"直接崩。
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import type { UnpackedPlace } from '../../types/share';
import { PlaceList } from './PlaceList';
import { PlaceMap } from './PlaceMap';

export interface PlaceBoardProps {
  places: readonly UnpackedPlace[];
  /** 是否在主应用里嵌入（= 可以用地图）。 */
  embedded: boolean;
  /** 空列表提示。 */
  emptyHint?: string;
}

/** CSS 选择器里安全地引用一个 placeId（协议 id 是 `p1_abcdef` 这类安全字符，仍然转义一下）。 */
function escapeAttribute(value: string): string {
  return value.replace(/["\\]/g, '\\$&');
}

export function PlaceBoard({ places, embedded, emptyHint }: PlaceBoardProps): JSX.Element {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  /** 选中的地点消失时（重新选文件/换包）清空选中，避免指向不存在的项。 */
  const knownIds = useMemo(() => new Set(places.map((place) => place.placeId)), [places]);
  useEffect(() => {
    if (selectedId !== null && !knownIds.has(selectedId)) setSelectedId(null);
  }, [knownIds, selectedId]);

  useEffect(() => {
    if (selectedId === null) return;
    const host = listRef.current;
    if (!host) return;
    const row = host.querySelector<HTMLElement>(`[data-place-id="${escapeAttribute(selectedId)}"]`);
    if (!row) return;
    if (typeof row.scrollIntoView === 'function') {
      row.scrollIntoView({ block: 'center' });
    }
  }, [selectedId]);

  if (!embedded) {
    return (
      <div className="place-board place-board-solo" ref={listRef}>
        <PlaceList places={places} emptyHint={emptyHint} />
      </div>
    );
  }

  return (
    <div className="place-board">
      <div className="place-board-map">
        <PlaceMap places={places} onSelect={setSelectedId} />
      </div>
      <div className="place-board-list" ref={listRef}>
        <PlaceList
          places={places}
          selectedId={selectedId}
          onSelect={setSelectedId}
          emptyHint={emptyHint}
        />
      </div>
    </div>
  );
}
