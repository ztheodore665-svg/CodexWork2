// @vitest-environment jsdom
/** 首屏「从主应用相册选」的**文案三分支**（W14 收尾，P3；与 track-studio 的同名组件对齐）。
 *
 * 现状：`disabled` 判据是 `!libraryReady || busy`，但文案只有两分支，
 * 于是"已嵌入、主应用没传照片"被显示成「（未嵌入，不可用）」—— 与事实不符。
 * 这里锁住三种情况各自的说法，并确认 `disabled` 行为没被顺手改坏。
 */

import { act } from 'react-dom/test-utils';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { Landing, type LandingProps } from './Landing';

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

function props(overrides: Partial<LandingProps> = {}): LandingProps {
  return {
    embedded: false,
    libraryCount: 0,
    identity: null,
    identitySource: 'local',
    identityNote: null,
    busy: false,
    error: null,
    onPickSystem: () => undefined,
    onPickLibrary: () => undefined,
    onReceiveFile: () => undefined,
    ...overrides,
  };
}

/** 「从主应用相册选」那颗按钮。 */
function libraryButton(): HTMLButtonElement {
  const buttons = Array.from(container.querySelectorAll('button'));
  const found = buttons.find((button) => (button.textContent ?? '').includes('从主应用相册选'));
  if (!found) throw new Error('没有找到「从主应用相册选」按钮');
  return found;
}

async function render(overrides: Partial<LandingProps>): Promise<void> {
  await act(async () => {
    root.render(<Landing {...props(overrides)} />);
  });
}

describe('Landing —— 「从主应用相册选」的三分支文案', () => {
  it('未嵌入：说「未嵌入，不可用」，并置灰', async () => {
    await render({ embedded: false, libraryCount: 0 });
    const button = libraryButton();
    expect(button.textContent).toContain('（未嵌入，不可用）');
    expect(button.title).toBe('请从主应用进入此功能');
    expect(button.disabled).toBe(true);
  });

  it('已嵌入但主应用没传照片：**不再**出现「未嵌入」，改为说清没有照片', async () => {
    await render({ embedded: true, libraryCount: 0 });
    const button = libraryButton();

    expect(button.textContent).not.toContain('未嵌入');
    expect(button.textContent).toContain('（主应用没有可传入的照片）');
    expect(button.title).toBe('主应用没有可传入的照片');
    expect(button.disabled).toBe(true);
  });

  it('已嵌入且有照片：显示张数并可点', async () => {
    await render({ embedded: true, libraryCount: 2 });
    const button = libraryButton();
    expect(button.textContent).toContain('（2 张可用）');
    expect(button.title).toBe('使用主应用注入的照片');
    expect(button.disabled).toBe(false);
  });

  it('busy 时即使有照片也置灰（行为不变）', async () => {
    await render({ embedded: true, libraryCount: 2, busy: true });
    expect(libraryButton().disabled).toBe(true);
  });
});
