/** 内置示例旅程：让"生成信物"在没有任何输入时也能跑通。
 *
 * ## 为什么需要它
 *
 * 完整的旅程编辑器（建天 → 加地点 → 传照片 → 勾选可见性）是 TrailShare 的 UI 主体，
 * 属于后续阶段。但 W6-4 的验收要求"打包 → 下载 → 解包 → 模糊圈"这条链路今天就能跑，
 * 所以先内置一份**固定数据**：两个地点、两天、一个带 WGS84 坐标（走地理模糊）、
 * 一个不带（退化成规范原版 PlaceMeta）。
 *
 * 照片字节用 UTF-8 文本而不是 JPEG：链路只关心"非空字节 + AES-GCM 往返"，
 * 用文本能在断言失败时直接看出内容。真照片的入口是 W6-3 的相册桥
 * （`services/native/`），接上后把这里的 `photoBytes` 换掉即可。
 */

import type { Trip, VisibilityState } from '../../types/share';

function photoBytes(text: string): ArrayBuffer {
  return new TextEncoder().encode(text).buffer;
}

/** 洛阳两日：龙门石窟（带 WGS84 坐标）+ 洛邑古城（不带坐标）。 */
export function demoTrip(): Trip {
  return {
    id: 't_demo0001',
    title: '洛阳两日（示例）',
    createdAt: '2026-10-20T00:00:00.000Z',
    days: [
      {
        id: 'd_demo1',
        date: '2026-10-01',
        places: [
          {
            id: 'p_demo1',
            name: '龙门石窟',
            label: '上午',
            tag: 'heritage',
            intensity: 'high',
            coverIndex: 0,
            // WGS84（红线：存储用 WGS84）。展示时由 MapPage 转 GCJ-02。
            lat: 34.5636,
            lng: 112.4699,
            photos: [
              { id: 'ph_demo1', image: photoBytes('demo-photo-1'), caption: '伊河两岸' },
              { id: 'ph_demo2', image: photoBytes('demo-photo-2'), caption: '万佛洞' },
            ],
          },
        ],
      },
      {
        id: 'd_demo2',
        date: '2026-10-02',
        places: [
          {
            id: 'p_demo2',
            name: '洛邑古城',
            label: '晚间',
            tag: 'food',
            intensity: 'mid',
            coverIndex: 0,
            // 没有坐标：不调模糊接口，PlaceMeta 退化成规范原版（不写 lat/lng/radius）
            photos: [{ id: 'ph_demo3', image: photoBytes('demo-photo-3') }],
          },
        ],
      },
    ],
  };
}

/** 全可见（不屏蔽任何条目）。第二天的勾选树留给后续阶段。 */
export function demoVisibility(): VisibilityState {
  return {
    id: 't_demo0001::g1',
    tripId: 't_demo0001',
    groupId: 'g1',
    hiddenDayIds: [],
    hiddenPlaceIds: [],
    hiddenPhotoIds: [],
    hiddenCaptionPhotoIds: [],
  };
}
