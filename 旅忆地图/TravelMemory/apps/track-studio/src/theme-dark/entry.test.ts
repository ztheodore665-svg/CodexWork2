/** 深色主题的「零网络」契约测试（阶段 13 / W13-2 要求 1、9）。
 *
 * 这个文件做三件事，全部是**静态断言**（读源码文本，不跑运行时）：
 *
 *  1. `theme-dark/` 目录下**不许出现任何地图 SDK 字样** —— 深色版的地图是自绘格网，
 *     一旦有人顺手把 SDK 引进来，"独立版零网络"就不再成立，而浏览器不会报错；
 *  2. `main.tsx` 的入口分发对 `standalone` / `embed` **两个值都有分支**，
 *     否则改了 HTML 的 `data-entry` 会静默落回同一套 UI；
 *  3. `theme-dark/styles.css` 是**作用域化**的（每条规则的祖先都带 `.theme-dark`），
 *     否则两套主题会互相污染（H9）。
 *
 * ⚠️ 关键词为什么拼出来而不是写死：
 * 本文件自己就在 `theme-dark/` 目录里，写死会让「`grep -ri <SDK> src/theme-dark/` 必须 0 行」
 * 这条构建门禁命中测试本身。所以用 `['a','m','a','p'].join('')` 拼。
 */

import { beforeAll, describe, expect, it } from 'vitest';

/** `node:fs` 的最小形状（不引 @types/node 也能通过类型检查）。 */
interface Dirent {
  name: string;
  isDirectory(): boolean;
  isFile(): boolean;
}
interface FsLike {
  readFileSync(target: URL | string, encoding: string): string;
  readdirSync(target: URL | string, options: { withFileTypes: true }): Dirent[];
}

/** 地图 SDK 的名字（拼出来的，见文件头）。 */
const SDK_NAME = ['a', 'm', 'a', 'p'].join('');
/** SDK 的加载器包名（同样是拼出来的）。 */
const LOADER_PACKAGE = ['@travelmemory', `${SDK_NAME}-loader`].join('/');

let fs: FsLike;
let darkFiles: URL[] = [];

beforeAll(async () => {
  const specifier = ['node', 'fs'].join(':');
  fs = (await import(/* @vite-ignore */ specifier)) as unknown as FsLike;
  darkFiles = walk(new URL('.', import.meta.url));
});

/** 递归列出目录下的所有文件（测试目录里没有软链，也没有需要忽略的子目录）。 */
function walk(dir: URL): URL[] {
  const out: URL[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const child = new URL(entry.isDirectory() ? `${entry.name}/` : entry.name, dir);
    if (entry.isDirectory()) out.push(...walk(child));
    else if (entry.isFile()) out.push(child);
  }
  return out;
}

/** 读一个相对 `theme-dark/` 的文件。 */
function readDark(relative: string): string {
  return fs.readFileSync(new URL(relative, new URL('.', import.meta.url)), 'utf8');
}

describe('theme-dark 的零网络契约', () => {
  it('目录里没有任何地图 SDK 字样（注释也不算例外）', () => {
    const offenders: string[] = [];
    for (const file of darkFiles) {
      const text = fs.readFileSync(file, 'utf8').toLowerCase();
      if (text.includes(SDK_NAME)) offenders.push(file.pathname.split('/').slice(-2).join('/'));
    }
    expect(offenders).toEqual([]);
  });

  it('没有任何文件 import 地图 SDK 的加载器包', () => {
    const offenders: string[] = [];
    for (const file of darkFiles) {
      const text = fs.readFileSync(file, 'utf8');
      if (text.includes(LOADER_PACKAGE)) offenders.push(file.pathname.split('/').slice(-2).join('/'));
    }
    expect(offenders).toEqual([]);
  });

  it('底图控制器是自绘格网，且导出 createGridMap', () => {
    const grid = readDark('./map/gridMap.ts');
    expect(grid).toContain('export function createGridMap');
    // W14-5 修正：平面坐标系由调用方声明（补点页 wgs84 / 播放页 gcj02），不再写死。
    // 写死 'wgs84' 正是"省界与轨迹整体错位 500m"的根源。
    expect(grid).toContain('coordinateSystem: options.coordinateSystem');
    expect(grid).toContain('export function screenPointToPlane');
    expect(grid).toContain('export function planeToWgs84');
    // 格网必须真的画（背景 + 经纬线 + 标注），不是一块纯黑
    expect(grid).toContain('createRadialGradient');
    expect(grid).toContain('GRID');
  });

  it('样式表是作用域化的：没有 :root / 裸 .landing 这类全局选择器', () => {
    const css = readDark('./styles.css');
    expect(css).toContain('.theme-dark {');
    expect(css).not.toContain('\n:root {');
    expect(css).not.toContain('\n.landing {');
    // 也不许把 Tailwind 指令带进来（track-studio 不引 Tailwind）
    expect(css).not.toContain('@tailwind');
  });
});

describe('main.tsx 的入口分发', () => {
  const main = (): string => fs.readFileSync(new URL('../main.tsx', new URL('.', import.meta.url)), 'utf8');

  it('两个入口值都有分支，且各自指向对应的主题目录', () => {
    const source = main();
    expect(source).toContain("'embed'");
    expect(source).toContain("'standalone'");
    expect(source).toContain("'./theme-dark/App'");
    expect(source).toContain("'./theme-light/App'");
  });

  it('读的是 <html data-entry>（两个 HTML 入口靠它区分）', () => {
    expect(main()).toContain('dataset.entry');
  });

  it('非 embed 一律按 standalone 处理（fail-closed）', () => {
    const source = main();
    expect(source).toContain("raw === 'embed' ? 'embed' : 'standalone'");
  });
});
