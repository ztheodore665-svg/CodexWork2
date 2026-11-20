import { beforeAll, describe, expect, it } from 'vitest';

/**
 * 回放层的性能契约 —— 锁住两张「不能写后读」的门票。
 *
 * 背景：展示站曾因 `Timeline.draw()` 每帧在写完 32 张卡片样式之后调
 * `getBoundingClientRect()`，强制同步重排，p90 卡在 32.8ms（每 3 帧丢 1 帧）。
 * 修掉后 16.5ms。现在把照片卡片移植进主应用，最大的风险就是顺手把那个坑复制过来。
 *
 * 组件测试要 jsdom + 32 个 DOM 节点 + 伪造 Scene/Projection，成本远高于收益，
 * 所以沿用 `showcase/src/timeline.test.ts` 的「声明契约」做法：直接读源码，
 * 去掉注释后断言关键函数体里没有布局读取 API。
 */

interface FsLike {
  readFileSync(target: URL | string, encoding: string): string;
}

let cardsSource = '';
let stageSource = '';

beforeAll(async () => {
  const specifier = ['node', 'fs'].join(':');
  const fs = (await import(/* @vite-ignore */ specifier)) as unknown as FsLike;
  cardsSource = fs.readFileSync(new URL('./PhotoCards.tsx', import.meta.url), 'utf8');
  stageSource = fs.readFileSync(new URL('./ReplayStage.tsx', import.meta.url), 'utf8');
});

function stripComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|\s)\/\/[^\n]*/g, '$1');
}

/** 会导致强制同步布局的读取 API */
const LAYOUT_READS = [
  'getBoundingClientRect',
  'getClientRects',
  'offsetWidth',
  'offsetHeight',
  'offsetTop',
  'offsetLeft',
  'clientWidth',
  'clientHeight',
  'getComputedStyle',
  'scrollTop',
];

/** 抽出 `update(...)` 到 `useImperativeHandle` 收尾之间的函数体 */
function updateBody(): string {
  const start = cardsSource.indexOf('      update(\n        scene: Scene,');
  const end = cardsSource.indexOf('    }),\n    [],', start);
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return stripComments(cardsSource.slice(start, end));
}

/** 抽出主循环 `frame` 的函数体。
 *  不能用 `raf = requestAnimationFrame(frame)` 当结束锚点 —— frame 内部第一行就有它，
 *  那样只会截到开头三行。用 `};\n\n    raf = ...` 这个「函数结束 + 首次启动」组合。 */
function frameBody(): string {
  const start = stageSource.indexOf('const frame = (now: number): void => {');
  const end = stageSource.indexOf('\n    };\n\n    raf = window.requestAnimationFrame(frame);', start);
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return stripComments(stageSource.slice(start, end));
}

describe('PhotoCards.update 每帧路径', () => {
  it('不读任何几何（写完样式立刻读布局会把 p90 顶回 32.8ms）', () => {
    const body = updateBody();
    for (const api of LAYOUT_READS) {
      expect(body, `update() 里出现了 ${api}`).not.toContain(api);
    }
  });

  it('只写内联样式，几何全部来自 proj / scene 参数', () => {
    const body = updateBody();
    expect(body).toContain('el.style.transform');
    expect(body).toContain('el.style.opacity');
    expect(body).toContain('proj.ox');
    expect(body).toContain('scene.photoMX');
  });

  it('缩略图走 thumbs 映射，取不到退回 gradient', () => {
    expect(cardsSource).toContain('thumbs?.get(photo.id) ?? photo.thumb');
    const jsx = cardsSource.slice(cardsSource.indexOf('return (\n    <>'));
    expect(jsx).toContain('photo.gradient');
  });
});

describe('ReplayStage 主循环顺序', () => {
  it('frame 函数体里没有任何布局读取（syncSize 定义在帧外、调用在帧首）', () => {
    const body = frameBody();
    for (const api of LAYOUT_READS) {
      expect(body, `frame() 里出现了 ${api}`).not.toContain(api);
    }
  });

  it('cards.update 在 renderer.draw 之前，且写后不再有几何读', () => {
    const body = frameBody();
    const atUpdate = body.indexOf('cards.update(');
    const atDraw = body.indexOf('renderer.draw(');
    expect(atUpdate).toBeGreaterThan(-1);
    expect(atDraw).toBeGreaterThan(atUpdate);
  });

  it('锚点接的是卡片句柄与 scene.photoCount，不再恒为空', () => {
    const body = frameBody();
    expect(body).toContain('drawState.anchors = cards.anchors');
    expect(body).toContain('drawState.anchorCount = scene.photoCount');
  });

  it('缩略图只触发一次重渲染，绝不出现在主循环里', () => {
    const body = frameBody();
    expect(body).not.toContain('setThumbs');
    expect(body).not.toContain('thumbs');
    // 主循环 effect 依赖固定是 [journey, clock]，缩略图不在其中
    expect(stageSource).toContain('}, [journey, clock]');
    expect(stageSource).toContain('if (!cancelled && map.size > 0) setThumbs(map);');
  });
});
