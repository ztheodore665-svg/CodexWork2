// @vitest-environment jsdom
/** VerifyCard 的渲染测试。
 *
 * 只覆盖两件事：空态长得对、注入结果后三条 Badge 的文案对。
 * 三态映射本身由 `verification.test.ts` 在 Node 环境下守着，这里不重复断言 ——
 * 渲染测试的价值在于"组件真的把状态画出来了"，而不是再算一遍逻辑。
 *
 * 用 `React.createElement` 而不是 JSX：`vite.config.ts` 的 `test.include` 只收
 * `src/**\/*.test.ts`（`.tsx` 不在内），而那份配置属于不许改动的文件。
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';

import type { UnpackView } from '../../types/share';
import { VerifyCard } from './VerifyCard';

afterEach(cleanup);

/**
 * 传一个文件给卡片（走隐藏的 file input，和真实点"选择文件"同一条路径），
 * 然后**等到 `expected` 出现**再返回。
 *
 * 为什么必须显式等某个结果而不是"等 loading 消失"：
 * 验证是 `async` 的，`verifying` 在**第一轮 render 之后**才翻成 true。
 * 所以"找不到『正在本机验证…』"在异步开始之前就已经成立了 ——
 * waitFor 会立刻通过，断言随即跑在状态更新之前，拿到的是空态。
 * 等**结果**而不是等**中间态消失**，是这里唯一可靠的写法。
 */
async function dropFile(file: File, expected: string | RegExp): Promise<void> {
  const { container } = render(
    createElement(VerifyCard, { cred: { privHex: '11'.repeat(32) } }),
  );
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  expect(input).toBeTruthy();
  // `input.files` 是只读的 FileList：fireEvent 的 `target: { files }` 对它赋值
  // 会**静默失败**（属性没有 setter），于是组件读到 `files[0] === undefined`，
  // 什么事都不会发生。只能用 defineProperty 换掉整个属性。
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  fireEvent.change(input);
  await waitFor(() => expect(screen.getByText(expected)).toBeTruthy(), { timeout: 5000 });
}

function fileOf(name: string, body: string): File {
  return new File([body], name, { type: 'application/json' });
}

/**
 * jsdom 的 `File` / `Blob` **没有实现 `text()`**（标准是有的，Chrome 76+ / Safari 14+ 都支持）。
 *
 * 不补这个洞，组件里的 `await file.text()` 会抛 `file.text is not a function`，
 * 被 catch 兜成"读不出这个信物"——于是**每条**用例都只能测到"打不开"这一条路径，
 * 而本文件要测的恰恰是"读出来之后怎么判"。这是测试环境的缺口，不是产品缺陷。
 */
beforeAll(() => {
  if (typeof File.prototype.text !== 'function') {
    File.prototype.text = function readAsTextViaFileReader(this: File): Promise<string> {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(reader.error ?? new Error('读取失败'));
        reader.readAsText(this);
      });
    };
  }
});

function allPassView(): UnpackView {
  return {
    ok: true,
    groups: [],
    from: { displayName: '小林', pubkey: 'ed25519:' + 'ab'.repeat(32) },
    shareId: 's_test0001',
    commitment: 'sha256:' + 'cd'.repeat(32),
    signature: 'ef'.repeat(32),
    chainSeq: 0,
    chainPrevHash: 'sha256:' + '00'.repeat(32),
    chainOk: true,
    chainNote: '链首记录（seq=0 且 prevHash 指向创世哈希），链路完整',
    report: {
      structureOk: true,
      signatureOk: true,
      commitmentOk: true,
      chainOk: true,
      chainNote: '链首记录（seq=0 且 prevHash 指向创世哈希），链路完整',
    },
    canAccept: true,
  };
}

describe('VerifyCard', () => {
  it('还没选文件时是空态：三条都是「未比对」', () => {
    render(createElement(VerifyCard, { cred: { privHex: '11'.repeat(32) } }));

    expect(screen.getByText('把信物文件拖到这里')).toBeTruthy();
    expect(screen.getAllByText('未比对')).toHaveLength(3);
    // 三条检查项标题都在
    expect(screen.getByText('来源真实')).toBeTruthy();
    expect(screen.getByText('内容未篡改')).toBeTruthy();
    expect(screen.getByText('链锚定状态')).toBeTruthy();
  });

  it('注入全绿的解包结果后：三条 Badge 都是「通过」', () => {
    render(
      createElement(VerifyCard, {
        cred: { privHex: '11'.repeat(32) },
        externalResult: allPassView(),
      }),
    );

    expect(screen.getAllByText('通过')).toHaveLength(3);
    expect(screen.getByText(/无需信任服务器/)).toBeTruthy();
    expect(screen.getByText('小林')).toBeTruthy();
  });
});

describe('VerifyCard 收到伪造文件', () => {
  it('JSON 合法但不是信物 → 三条断言全红，并明确劝阻接受', async () => {
    // 结构完全不对：unpackForViewer 会给出 kind='invalid'
    await dropFile(fileOf('fake.trailshare', JSON.stringify({ hello: 'world' })), '不要接受这个信物');

    // 三条 Badge 全部「失败」
    expect(screen.getAllByText('失败')).toHaveLength(3);
    expect(screen.getByText('不要接受这个信物')).toBeTruthy();
    // 三条的 detail 共用同一句失败原因（整包都不可用，不是某一项的问题），
    // 所以它会出现三次 —— 用 getAllByText 而不是 getByText。
    expect(screen.getAllByText(/不是 TrailShare 信物|已损坏/).length).toBeGreaterThan(0);
  });

  it('连 JSON 都不是的文件 → 报"读不出"，而不是假装验签失败', async () => {
    await dropFile(fileOf('fake.trailshare', 'this is not json at all <<<'), '读不出这个信物');

    // "读不出"和"验证失败"是两件事：前者是"这个文件没法验证"，后者是"验证不通过"。
    // 涂红会把一个打不开的文件说成"内容被篡改过"，那是谎报。
    expect(screen.getByText('读不出这个信物')).toBeTruthy();
    expect(screen.queryByText('不要接受这个信物')).toBeNull();
  });

  it('空文件也按"读不出"处理，不抛未捕获异常', async () => {
    await dropFile(fileOf('empty.trailshare', ''), '读不出这个信物');
    expect(screen.getByText('读不出这个信物')).toBeTruthy();
  });
});

describe('VerifyCard 的信任感来自"逐条可核对"', () => {
  it('链未锚定时是灰色「未比对」，不是红色「失败」', () => {
    const unknownChain: UnpackView = {
      ...allPassView(),
      chainOk: null,
      chainNote: '本地账本里没有 prevHash 指向的记录（跨设备接收）',
      report: {
        structureOk: true,
        signatureOk: true,
        commitmentOk: true,
        chainOk: null,
        chainNote: '本地账本里没有 prevHash 指向的记录（跨设备接收）',
      },
    };
    render(
      createElement(VerifyCard, {
        cred: { privHex: '11'.repeat(32) },
        externalResult: unknownChain,
      }),
    );

    // 前两条仍然绿：已证实的结论不会因为第三条判不了就被推翻
    expect(screen.getAllByText('通过')).toHaveLength(2);
    // 第三条是灰色"未比对"—— 没拿到证伪证据就不能涂红
    expect(screen.getAllByText('未比对')).toHaveLength(1);
    expect(screen.getByText(/跨设备接收时这是预期现象/)).toBeTruthy();
    expect(screen.queryByText('不要接受这个信物')).toBeNull();
  });

  it('承诺被篡改时「内容未篡改」那条变红，且不再声称"可以放心接受"', () => {
    const tampered: UnpackView = {
      ...allPassView(),
      report: {
        structureOk: true,
        signatureOk: true,
        commitmentOk: false, // 承诺与内容对不上
        chainOk: true,
        chainNote: '链首记录（seq=0 且 prevHash 指向创世哈希），链路完整',
      },
    };
    render(
      createElement(VerifyCard, {
        cred: { privHex: '11'.repeat(32) },
        externalResult: tampered,
      }),
    );

    expect(screen.getAllByText('失败')).toHaveLength(1);
    expect(screen.getByText('不要接受这个信物')).toBeTruthy();
    expect(screen.queryByText(/无需信任服务器/)).toBeNull();
  });
});
