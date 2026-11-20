#!/usr/bin/env node
/**
 * 跨工作区依赖边界检查（阶段 16 / 红线 4 的前端一半）。
 *
 * ## 为什么不用 dependency-cruiser
 *
 * 它要引一棵 30MB 的依赖树，而本项目需要的规则只有四条：
 *
 *   1. 小应用（apps/*）不得 import 主应用（frontend/*）或另一个小应用
 *   2. 共享包（packages/*）不得 import 任何宿主（frontend / apps / showcase）
 *   3. 任何工作区只能在 package.json 已声明的依赖里 import `@travelmemory/*`
 *   4. GCJ-02 偏移常数只允许出现在 `packages/geo` 与 `backend/app/utils/coords.py`
 *
 * 四条规则用 200 行零依赖脚本就能钉死，且报错能直接给「文件:行号 → 目标」。
 * 引工具的价值在可视化依赖图，不在布尔判定 —— 后者自己写更可控。
 *
 * ## 为什么相对路径也要查
 *
 * `import '../../frontend/src/foo'` 不经过包名，package.json 里什么都看不出来，
 * 但它确实把两个工作区缝在了一起，而且绕过了类型检查的模块解析报错（路径存在就过）。
 * 这类越界只有解析成绝对路径才抓得到。
 */

import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)));
const PACKAGE_SCOPE = '@travelmemory/';

/** 测试文件允许跨工作区相对引用：parity 测试的全部意义就是同时 import 两份实现比对。 */
function isTestFile(path) {
  return /\.test\.(ts|tsx)$/.test(path) || path.split(sep).includes('__tests__');
}

/** 允许出现 GCJ-02 偏移常数的文件（其余位置一律视为自带偏移常数）。 */
const COORD_ALLOWLIST = new Set([
  'packages/geo/src/coords.ts',
  'backend/app/utils/coords.py',
]);

const VIOLATIONS = [];

function posix(path) {
  return path.split(sep).join('/');
}

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

/** 展开根 package.json 的 workspaces（只支持 `dir/*` 这一种通配，够用）。 */
function listWorkspaces() {
  const root = readJson(join(ROOT, 'package.json'));
  const dirs = [];
  for (const pattern of root.workspaces ?? []) {
    if (pattern.endsWith('/*')) {
      const base = pattern.slice(0, -2);
      const abs = join(ROOT, base);
      if (!existsSync(abs)) continue;
      for (const entry of readdirSync(abs)) {
        const dir = join(abs, entry);
        if (statSync(dir).isDirectory() && existsSync(join(dir, 'package.json'))) dirs.push(dir);
      }
    } else {
      const dir = join(ROOT, pattern);
      if (existsSync(join(dir, 'package.json'))) dirs.push(dir);
    }
  }
  return dirs;
}

function walk(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const entry of readdirSync(dir)) {
    const abs = join(dir, entry);
    const stat = statSync(abs);
    if (stat.isDirectory()) {
      if (entry === 'node_modules' || entry === 'dist') continue;
      walk(abs, out);
    } else if (/\.(ts|tsx|mts|cts)$/.test(entry) && !/\.d\.ts$/.test(entry)) {
      out.push(abs);
    }
  }
  return out;
}

/** 抓 import / export-from / 动态 import / require 的模块说明符。 */
function specifiersOf(source) {
  const found = [];
  const patterns = [
    /(?:^|\n)\s*import\s+[^'"]*?from\s*['"]([^'"]+)['"]/g,
    /(?:^|\n)\s*import\s*['"]([^'"]+)['"]/g,
    /(?:^|\n)\s*export\s+[^'"]*?from\s*['"]([^'"]+)['"]/g,
    /\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
    /\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
  ];
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      const line = source.slice(0, match.index).split('\n').length;
      found.push({ specifier: match[1], line });
    }
  }
  return found;
}

/** 把相对说明符解析成绝对路径（不带扩展名时也试一遍常见后缀）。 */
function resolveRelative(fromFile, specifier) {
  const base = resolve(dirname(fromFile), specifier);
  const candidates = [base, `${base}.ts`, `${base}.tsx`, join(base, 'index.ts'), join(base, 'index.tsx')];
  return candidates.find((candidate) => existsSync(candidate)) ?? base;
}

const workspaces = listWorkspaces().map((dir) => {
  const pkg = readJson(join(dir, 'package.json'));
  return {
    dir,
    name: pkg.name,
    deps: new Set([
      ...Object.keys(pkg.dependencies ?? {}),
      ...Object.keys(pkg.devDependencies ?? {}),
      ...Object.keys(pkg.peerDependencies ?? {}),
    ]),
  };
});

const byDir = new Map(workspaces.map((w) => [w.dir, w]));
const byName = new Map(workspaces.map((w) => [w.name, w]));

function workspaceOf(absPath) {
  for (const ws of workspaces) {
    if (absPath.startsWith(ws.dir + sep)) return ws;
  }
  return null;
}

function relRoot(absPath) {
  return posix(relative(ROOT, absPath));
}

function report(rule, file, line, detail) {
  VIOLATIONS.push({ rule, file: relRoot(file), line, detail });
}

for (const ws of workspaces) {
  const files = walk(join(ws.dir, 'src'));
  for (const file of files) {
    const source = readFileSync(file, 'utf8');

    for (const { specifier, line } of specifiersOf(source)) {
      if (specifier.startsWith('.')) {
        const target = resolveRelative(file, specifier);
        const targetWs = workspaceOf(target);
        if (targetWs && targetWs.dir !== ws.dir && !isTestFile(file)) {
          report('相对路径越界', file, line, `跨到了 ${targetWs.name}（${specifier}）`);
        }
        continue;
      }

      if (!specifier.startsWith(PACKAGE_SCOPE)) continue;

      const pkgName = specifier.split('/').slice(0, 2).join('/');
      const target = byName.get(pkgName);
      if (!target) {
        report('未声明的共享包', file, line, `${pkgName} 不在任何工作区里`);
        continue;
      }

      // 同包内自引用（如 `@travelmemory/geo` 内部 import 自己）不算越界
      if (target.dir === ws.dir) continue;

      if (!ws.deps.has(pkgName)) {
        report('未声明的依赖', file, line, `${pkgName} 没写进 ${ws.name} 的 package.json`);
        continue;
      }

      const wsKind = posix(relative(ROOT, ws.dir)).split('/')[0];
      if (wsKind === 'packages' && !posix(relative(ROOT, target.dir)).startsWith('packages/')) {
        report('共享包反向依赖宿主', file, line, `${ws.name} → ${target.name}`);
      }
      if (wsKind === 'apps' && posix(relative(ROOT, target.dir)).startsWith('frontend/')) {
        report('小应用依赖主应用', file, line, `${ws.name} → ${target.name}`);
      }
    }

    // 红线 10：偏移常数只能有一个出处
    if (/0\.00669342162296594323|6378245\.0/.test(source)) {
      const rel = relRoot(file);
      if (!COORD_ALLOWLIST.has(rel)) {
        report('坐标常数重复', file, 1, 'GCJ-02 偏移常数只允许在 packages/geo 与 utils/coords.py');
      }
    }
  }
}

const packagesCount = workspaces.filter((w) => posix(relative(ROOT, w.dir)).startsWith('packages/')).length;
const appsCount = workspaces.filter((w) => posix(relative(ROOT, w.dir)).startsWith('apps/')).length;

if (VIOLATIONS.length > 0) {
  console.error(`跨工作区依赖边界检查失败：${VIOLATIONS.length} 处\n`);
  for (const item of VIOLATIONS) {
    console.error(`  [${item.rule}] ${item.file}:${item.line} → ${item.detail}`);
  }
  console.error('\n规则见 tools/check-import-boundaries.mjs 文件头。');
  process.exit(1);
}

console.log(
  `跨工作区依赖边界检查通过：${workspaces.length} 个工作区（${appsCount} 个应用 / ${packagesCount} 个共享包）`,
);
