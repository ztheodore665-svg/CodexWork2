// @vitest-environment jsdom
/** 接收页的「接受 / 拒绝 / 导出」交互（W15-4）。
 *
 * 只测**这一层自己的职责**：把决定交给父组件、把状态渲染出来、没东西可导出时禁用按钮。
 * 落盘（IndexedDB）与 journey 生成分别在 `receivedStore.test.ts` /
 * `receivedToJourney.test.ts` 里测，这里全部用桩函数。
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import type { UnpackedPlace, UnpackView } from '../../types/share';
import { ReceivePanel, type PlaceDecision } from './ReceivePanel';

afterEach(cleanup);

function place(overrides: Partial<UnpackedPlace> = {}): UnpackedPlace {
  return {
    placeId: 'p1_aaa',
    name: '西湖',
    date: '2026-11-01',
    tag: 'nature',
    intensity: 'mid',
    photos: [{ photoId: 'ph_1', image: new ArrayBuffer(2) }],
    ...overrides,
  };
}

function result(overrides: Partial<UnpackView> = {}): UnpackView {
  return {
    ok: true,
    groups: [
      {
        groupId: 'g1_self',
        places: [
          place(),
          place({ placeId: 'p2_bbb', name: '灵隐寺', date: '2026-11-02', tag: 'heritage' }),
        ],
        fromName: '小旅',
        fromPubkey: 'aa'.repeat(32),
        shareId: 's_demo',
        processed: false,
      },
    ],
    from: { displayName: '小旅', pubkey: 'aa'.repeat(32) },
    shareId: 's_demo',
    commitment: 'c'.repeat(64),
    signature: 'sig',
    chainSeq: 1,
    chainPrevHash: '0'.repeat(64),
    chainOk: true,
    chainNote: '',
    report: { structureOk: true, signatureOk: true, commitmentOk: true, chainOk: true, chainNote: '' },
    canAccept: true,
    ...overrides,
  };
}

interface Handlers {
  onAccept: ReturnType<typeof vi.fn>;
  onReject: ReturnType<typeof vi.fn>;
  onExport: ReturnType<typeof vi.fn>;
}

function mount(
  options: {
    decisions?: Record<string, PlaceDecision>;
    canAccept?: boolean;
    embedded?: boolean;
    decisionError?: string | null;
  } = {},
): Handlers {
  const onAccept = vi.fn();
  const onReject = vi.fn();
  const onExport = vi.fn();
  render(
    <ReceivePanel
      embedded={options.embedded ?? false}
      result={result(options.canAccept === false ? { canAccept: false } : {})}
      busy={false}
      fileName="demo.trailshare"
      decisions={options.decisions ?? {}}
      decisionError={options.decisionError ?? null}
      onFile={() => {}}
      onAccept={onAccept}
      onReject={onReject}
      onExport={onExport}
      onBack={() => {}}
    />,
  );
  return { onAccept, onReject, onExport };
}

describe('ReceivePanel —— 接受 / 拒绝 / 导出（W15-4）', () => {
  it('未决定时每个地点都有「接受这个地点」「拒绝」，点接受把地点交回父组件', () => {
    const handlers = mount();

    const acceptButtons = screen.getAllByRole('button', { name: '接受这个地点' });
    expect(acceptButtons).toHaveLength(2);

    fireEvent.click(acceptButtons[0]);
    expect(handlers.onAccept).toHaveBeenCalledTimes(1);
    expect(handlers.onAccept.mock.calls[0][0]).toMatchObject({ placeId: 'p1_aaa', name: '西湖' });
  });

  it('点拒绝把地点交回父组件（与接受互不影响）', () => {
    const handlers = mount();

    fireEvent.click(screen.getAllByRole('button', { name: '拒绝' })[1]);

    expect(handlers.onReject).toHaveBeenCalledTimes(1);
    expect(handlers.onReject.mock.calls[0][0]).toMatchObject({ placeId: 'p2_bbb' });
    expect(handlers.onAccept).not.toHaveBeenCalled();
  });

  it('已接受的地点显示「已接受」并计入顶部统计，不再显示按钮', () => {
    mount({ decisions: { p1_aaa: 'accepted' } });

    expect(screen.getByText('已接受 1 个地点（共 2 个）')).toBeTruthy();
    expect(screen.getByText('已接受')).toBeTruthy();
    // 只剩第二个地点的两个按钮
    expect(screen.getAllByRole('button', { name: '接受这个地点' })).toHaveLength(1);
  });

  it('已拒绝的地点显示「已拒绝」且不计入统计', () => {
    mount({ decisions: { p1_aaa: 'rejected', p2_bbb: 'accepted' } });

    expect(screen.getByText('已拒绝')).toBeTruthy();
    expect(screen.getByText('已接受 1 个地点（共 2 个）')).toBeTruthy();
  });

  it('没有已接受地点时导出按钮 disabled；有则可用并能点', () => {
    const none = mount();
    const exportButton = screen.getByRole('button', { name: '下载 journey.json' }) as HTMLButtonElement;
    // 没东西可导出时点不动，也不该回调
    expect(exportButton.disabled).toBe(true);
    fireEvent.click(exportButton);
    expect(none.onExport).not.toHaveBeenCalled();
    cleanup();

    const some = mount({ decisions: { p1_aaa: 'accepted' } });
    const enabled = screen.getByRole('button', { name: '下载 journey.json' }) as HTMLButtonElement;
    expect(enabled.disabled).toBe(false);
    fireEvent.click(enabled);
    expect(some.onExport).toHaveBeenCalledTimes(1);
  });

  it('embed 时导出按钮文案是「导回主应用」', () => {
    mount({ embedded: true, decisions: { p1_aaa: 'accepted' } });
    expect(screen.getByRole('button', { name: '导回主应用' })).toBeTruthy();
  });

  it('校验未通过（canAccept=false）时接受/拒绝按钮 disabled', () => {
    mount({ canAccept: false });

    for (const button of screen.getAllByRole('button', { name: '接受这个地点' })) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }
    for (const button of screen.getAllByRole('button', { name: '拒绝' })) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it('落盘失败时显示错误行，页面不崩', () => {
    mount({ decisionError: '当前浏览器不支持本地存储' });

    expect(screen.getByText('保存决定时出错：当前浏览器不支持本地存储')).toBeTruthy();
    // 仍然可以继续操作其它地点
    expect(screen.getAllByRole('button', { name: '接受这个地点' })).toHaveLength(2);
  });
});
