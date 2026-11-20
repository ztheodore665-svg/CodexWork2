// @vitest-environment jsdom
/** 生成页的预览必须和产物一致（W15-2 收尾）。
 *
 * 场景：用户在同一地点塞了 6 张照片。生成时 `buildTrip` 会把它拆成
 * 「西湖（1/2）」5 张 + 「西湖（2/2）」1 张（协议每地点 1–5 张，不可改）。
 * 如果预览还按原草稿渲染，用户看到 1 个地点 6 张照片、收件人收到 2 个地点 ——
 * 这一页存在的意义（所见即所得）就没了。
 */

import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import type { PhotoDraft, TripDraft } from '../../core/buildTrip';
import { PreviewPanel } from './PreviewPanel';

afterEach(cleanup);

function photo(index: number): PhotoDraft {
  return { id: `p${index}`, name: `p${index}.jpg`, bytes: new Uint8Array([index + 1]).buffer };
}

/** 一天、一个地点、`count` 张照片。 */
function draftWith(count: number): TripDraft {
  return {
    title: '杭州一日',
    days: [
      {
        id: 'd1',
        date: '2025-10-04',
        places: [
          { id: 'pl1', name: '西湖', tag: 'nature', intensity: 'mid', photos: Array.from({ length: count }, (_, i) => photo(i)) },
        ],
      },
    ],
  };
}

function renderPreview(draft: TripDraft): void {
  render(
    <PreviewPanel
      draft={draft}
      recipientCount={0}
      busy={false}
      progressText={null}
      error={null}
      embedded={false}
      onGenerate={() => {}}
      onBack={() => {}}
    />,
  );
}

describe('PreviewPanel —— 超限地点按拆分后的结构预览（W15-2）', () => {
  it('6 张同一地点 → 预览显示 2 个地点、6 张照片，且每个地点卡片 ≤5 张', () => {
    renderPreview(draftWith(6));

    // 头部计数是拆分后的：1 天 · 2 个地点 · 6 张照片
    expect(screen.getByText(/1 天 · 2 个地点 · 6 张照片/)).toBeTruthy();
    // 地点卡分别是 5 张与 1 张（上限 5 由协议钉死）
    expect(screen.getByText('5 张照片')).toBeTruthy();
    expect(screen.getByText('1 张照片')).toBeTruthy();
    expect(screen.queryByText('6 张照片')).toBeNull();
    // 名字带「（1/2）」「（2/2）」，与包里的地名一致
    expect(screen.getByText('西湖（1/2）')).toBeTruthy();
    expect(screen.getByText('西湖（2/2）')).toBeTruthy();
  });

  it('5 张（上限内）→ 原样 1 个地点 5 张照片，不拆分也不改名', () => {
    renderPreview(draftWith(5));

    expect(screen.getByText(/1 天 · 1 个地点 · 5 张照片/)).toBeTruthy();
    expect(screen.getByText('西湖')).toBeTruthy();
    expect(screen.queryByText('西湖（1/2）')).toBeNull();
  });

  it('超限草稿不再显示"最多 5 张"的阻塞错误，生成按钮可用', () => {
    renderPreview(draftWith(6));

    expect(screen.queryByText(/最多 5 张照片/)).toBeNull();
    const generate = screen.getByRole('button', { name: '生成信物' }) as HTMLButtonElement;
    expect(generate.disabled).toBe(false);
  });
});
