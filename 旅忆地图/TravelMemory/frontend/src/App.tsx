import { useCallback, useEffect, useState } from 'react';
import { Toaster } from './components/ui/toaster';
import { MapPage } from './pages/MapPage';
import { listPhotos, type PhotoListItem } from './services/api/photos';
import { listTrips, type TripView } from './services/api/trips';
import type { UnpackedPlace } from './types/share';

/** 阶段 7 的应用壳：**地图是唯一底层，其余全部叠在它上面**。
 *
 * ## 为什么不再是三个 Tab
 *
 * 阶段 6 之前这里是「记忆问答 / 地图相册 / 信物分享」三个页签，默认落在问答上。
 * 那个结构对"旅忆地图"是错的：产品的第一屏应该是**地图**，
 * 而问答、相册、信物都是从地图出发的动作。Tab 结构把三者摆成平级，
 * 用户每次想聊一句都要先切走、看完地图再切回来。
 *
 * 现在壳只做两件事：
 *   1. 全屏渲染 `MapPage`（它自己管抽屉、FAB、对话框）；
 *   2. 持有**跨浮层共享**的三份数据：
 *      - `mapPlaces`：信物解出的地点 → 地图画模糊圈；
 *      - `trips`：行程列表 → 行程 Tab 展示、信物分享打包；
 *      - `photos`：照片库 → 阶段 7 起照片是**底层数据**，行程只是它的视图。
 *
 * ## 为什么照片与行程分成两份 state
 *
 * 阶段 7 之前行程是导入的**产物**，两者可以合成一个列表。
 * 现在照片可以不属于任何行程（导入只写 Photo），行程也可以一张照片都没有
 * （新建的空壳）。合成一份就没法表达"这 3 张照片还没归到任何旅程里"，
 * 而那是照片库 Tab 最该说清楚的一件事。
 *
 * 引入全局 store 是为这点数据付出的过度设计，useState 足够。
 */
export default function App() {
  const [mapPlaces, setMapPlaces] = useState<UnpackedPlace[]>([]);
  const [trips, setTrips] = useState<TripView[]>([]);
  const [photos, setPhotos] = useState<PhotoListItem[]>([]);

  /** 拉行程列表与照片库。
   *
   *  **失败一律静默降级成空列表**：`photo_import` 开关关着时后端对这两个
   *  `GET` 都给 404，那是"这个能力没开"，不是"页面坏了"。
   *  白屏或者顶部飘一条红字，都是在拿后端的一个开关惩罚用户。
   *
   *  两个请求**并行**发：它们互不依赖，串行会让抽屉打开时多等一个往返。 */
  const refreshTrips = useCallback(async () => {
    const [nextTrips, nextPhotos] = await Promise.all([
      listTrips().catch(() => [] as TripView[]),
      listPhotos().catch(() => [] as PhotoListItem[]),
    ]);
    setTrips(nextTrips);
    setPhotos(nextPhotos);
  }, []);

  useEffect(() => {
    void refreshTrips();
  }, [refreshTrips]);

  return (
    <div className="h-[100dvh] w-full overflow-hidden">
      <MapPage
        sharePlaces={mapPlaces}
        trips={trips}
        photos={photos}
        onTripsChanged={refreshTrips}
        onViewOnMap={setMapPlaces}
      />
      {/* 全局挂一份：toast 可能在任意浮层的深层回调里触发，多挂会互相遮挡 */}
      <Toaster />
    </div>
  );
}
