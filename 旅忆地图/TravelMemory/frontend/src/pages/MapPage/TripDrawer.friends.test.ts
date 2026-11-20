// @vitest-environment jsdom
/** 「行程与相册」抽屉的**朋友分类**（W15-5 降级版，jsdom）。
 *
 * 渲染方式与 `importFlow.test.ts` 一致：整页 `<MapPage trips={...} />` +
 * `<Toaster />`，再用「打开行程与相册抽屉」的按钮把 Radix Sheet 真正打开
 * （Sheet 关闭时不挂载内容，直接渲染 TripDrawer 测不到用户看到的东西）。
 *
 * 文件名是 `.test.ts` 而不是 `.tsx`：`vitest.config.ts` 的 include 是
 * `src/**\/*.test.ts`（不认 `.tsx`），所以这里的元素只能用 `React.createElement` 写 ——
 * 与 `importFlow.test.ts` 同一套办法。
 *
 * 四条必须守住的：
 *  ① 默认「我的」里**看不到**朋友发来的行程（否则等于没分类，而且还容易误删）；
 *  ② 切到「朋友的」能看到发件人文件夹，展开后能认出"哪一趟、几张照片"；
 *  ③ 没有朋友行程时的空态要给**下一步动作**（让发送方点"接受并导回"）；
 *  ④ 地图叠加开关默认关闭，但必须可切换（阶段 17 接通）。
 */

import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import { MapPage } from './MapPage';
import { Toaster } from '../../components/ui/toaster';
import type { TripView } from '../../services/api/trips';

afterEach(cleanup);

function trip(overrides: Partial<TripView> = {}): TripView {
  return {
    id: 't1',
    title: '洛阳两日',
    startedAt: '2026-10-01T00:00:00Z',
    endedAt: '2026-10-02T00:00:00Z',
    source: 'manual',
    itemCount: 2,
    items: [],
    ...overrides,
  };
}

/** 一条朋友发来的行程（标题前缀由 TrailShare 的 `senderTitleOf` 生成）。 */
function friendTrip(overrides: Partial<TripView> = {}): TripView {
  return trip({
    id: 'f1',
    title: '来自 阿岚 的信物 · 西湖',
    startedAt: '2026-10-05T00:00:00Z',
    endedAt: '2026-10-05T00:00:00Z',
    source: 'manual',
    itemCount: 3,
    ...overrides,
  });
}

/** 打开抽屉（Sheet 关闭时不挂载内容，所以必须先点开）。 */
async function openTrips(): Promise<void> {
  fireEvent.click(screen.getByLabelText('打开行程与相册抽屉'));
  await waitFor(() => expect(screen.getByRole('tab', { name: /照片库/ })).toBeTruthy());
}

function renderPage(trips: readonly TripView[]): void {
  render(
    React.createElement(
      React.Fragment,
      null,
      React.createElement(MapPage, { trips }),
      React.createElement(Toaster),
    ),
  );
}

const switchToFriends = (): void => {
  fireEvent.click(screen.getByRole('button', { name: /朋友的/ }));
};

describe('行程分类：我的 / 朋友的（W15-5）', () => {
  it('默认「我的」看不到朋友发来的行程，只看到自己那趟', async () => {
    renderPage([trip(), friendTrip()]);
    await openTrips();

    expect(screen.getByText('洛阳两日')).toBeTruthy();
    expect(screen.queryByText('来自 阿岚 的信物 · 西湖')).toBeNull();
    // 分段控件的默认态
    expect(screen.getByRole('button', { name: /我的/ }).getAttribute('aria-pressed')).toBe('true');
  });

  it('切「朋友的」看到发件人文件夹，展开后能看到该行程与照片数', async () => {
    renderPage([trip(), friendTrip()]);
    await openTrips();
    switchToFriends();

    await waitFor(() => expect(screen.getByText('来自 阿岚')).toBeTruthy());
    expect(screen.getByText('已收到 1 位朋友分享的 1 趟旅程')).toBeTruthy();
    // 收起状态下看不到行程本身
    expect(screen.queryByText('来自 阿岚 的信物 · 西湖')).toBeNull();

    // 文件夹按钮靠 aria-expanded 定位（行程标题里也含「来自 阿岚」，用名字会撞上）
    const folder = screen.getByRole('button', { expanded: false });
    expect(folder.textContent).toContain('来自 阿岚');
    fireEvent.click(folder);

    await waitFor(() => expect(screen.getByText('来自 阿岚 的信物 · 西湖')).toBeTruthy());
    // "哪一趟"的辅助信息：日期 + 照片数
    expect(screen.getByText(/2026-10-05 · 3 张照片/)).toBeTruthy();
    expect(screen.getByRole('button', { expanded: true }).textContent).toContain('来自 阿岚');
  });

  it('朋友的行程没有分享 / 重命名 / 删除入口（只有回放）', async () => {
    renderPage([friendTrip()]);
    await openTrips();
    switchToFriends();
    fireEvent.click(screen.getByRole('button', { expanded: false }));

    await waitFor(() => expect(screen.getByText('来自 阿岚 的信物 · 西湖')).toBeTruthy());
    expect(screen.queryByLabelText(/分享行程/)).toBeNull();
    expect(screen.queryByLabelText(/重命名行程/)).toBeNull();
    expect(screen.queryByLabelText(/删除行程/)).toBeNull();
  });

  it('没有朋友行程时给空态文案（指向 TrailShare 的"接受并导回"）', async () => {
    renderPage([trip()]);
    await openTrips();
    switchToFriends();

    await waitFor(() => expect(screen.getByText(/还没有朋友分享的旅程/)).toBeTruthy());
    expect(screen.getByText(/接受并导回/)).toBeTruthy();
  });

  it('「在地图上显示朋友的分享」默认关闭，但可以打开', async () => {
    renderPage([friendTrip()]);
    await openTrips();
    switchToFriends();

    const checkbox = screen.getByRole('checkbox', { name: '在地图上显示朋友的分享' }) as HTMLElement;
    expect(checkbox).toBeTruthy();
    // 阶段 17 起它是真开关：默认关，但不 disabled
    expect(checkbox.hasAttribute('disabled')).toBe(false);
    expect(checkbox.getAttribute('data-state')).toBe('unchecked');

    fireEvent.click(checkbox);
    await waitFor(() => expect(checkbox.getAttribute('data-state')).toBe('checked'));

    fireEvent.click(checkbox);
    await waitFor(() => expect(checkbox.getAttribute('data-state')).toBe('unchecked'));

    // 文案不再承诺"阶段 16"，而是解释默认关闭的原因
    expect(screen.queryByText(/待阶段\s*16/)).toBeNull();
    expect(screen.getByText(/不会和你的照片混在一起/)).toBeTruthy();
  });
});
