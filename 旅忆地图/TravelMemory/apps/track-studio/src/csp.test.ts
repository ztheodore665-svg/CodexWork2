/** CSP 的单测（阶段 13 / W13-1 改造：从"一份策略"变成"按入口两套策略"）。
 *
 * 锁死四件事：
 *  ① 两个入口都必须有 `connect-src 'self'`（零后端的硬证据，**逐字**相等）；
 *  ② `standalone`（深色独立版）**一个高德域名都不许出现**；
 *  ③ `embed`（浅色嵌入版）才放行 `webapi.amap.com` 与 `*.amap.com`；
 *  ④ 注入幂等、找不到 `</head>` 时原样返回、大小写不敏感。
 *
 * 这些断言的价值在于：CSP 写错时**浏览器只会静默放宽**，没有任何报错。
 */

import { describe, expect, it } from 'vitest';

import { CSP_META_MARKER, buildCsp, injectCsp } from './csp';

/** 取某条指令（`connect-src 'self'` 这种完整片段）。 */
function directive(csp: string, name: string): string | undefined {
  return csp
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${name} `) || part === name);
}

describe('buildCsp —— standalone（深色独立版，index.html）', () => {
  it('包含零后端必需的两条：connect-src self、frame-ancestors', () => {
    const csp = buildCsp('standalone');
    expect(csp).toContain("connect-src 'self'");
    expect(csp).toContain('frame-ancestors');
    expect(csp).toContain('https://app.travelmemory.com');
    expect(csp.startsWith("default-src 'self'")).toBe(true);
  });

  it('不含任何高德域名（独立版零网络的硬约束）', () => {
    const csp = buildCsp('standalone');
    expect(csp).not.toContain('amap');
    expect(directive(csp, 'script-src')).toBe("script-src 'self'");
    expect(directive(csp, 'img-src')).toBe("img-src 'self' blob: data:");
  });

  it('不传参数时按最严的 standalone 来（fail-closed）', () => {
    expect(buildCsp()).toBe(buildCsp('standalone'));
  });
});

describe('buildCsp —— embed（浅色嵌入版，embed.html）', () => {
  it('script-src / img-src 放行高德', () => {
    const csp = buildCsp('embed');
    expect(csp).toContain('https://webapi.amap.com');
    expect(csp).toContain('https://*.amap.com');
    expect(directive(csp, 'script-src')).toBe("script-src 'self' https://webapi.amap.com");
    expect(directive(csp, 'img-src')).toBe("img-src 'self' blob: data: https://*.amap.com");
  });

  it('放行底图不等于放行网络：connect-src 仍然只允许 self', () => {
    expect(directive(buildCsp('embed'), 'connect-src')).toBe("connect-src 'self'");
  });

  it('frame-ancestors 与 standalone 一致（嵌入版也只是"允许被主应用嵌套"）', () => {
    expect(directive(buildCsp('embed'), 'frame-ancestors')).toBe(
      directive(buildCsp('standalone'), 'frame-ancestors'),
    );
  });
});

describe('buildCsp —— 两个入口的公共部分', () => {
  it('connect-src 逐字等于 "connect-src \'self\'"', () => {
    for (const kind of ['standalone', 'embed'] as const) {
      expect(directive(buildCsp(kind), 'connect-src')).toBe("connect-src 'self'");
    }
  });

  it('img-src 一律允许 data: / blob:（缩略图与视频预览）', () => {
    for (const kind of ['standalone', 'embed'] as const) {
      const csp = buildCsp(kind);
      expect(csp).toContain('data:');
      expect(csp).toContain('blob:');
    }
  });

  it('style-src 保留了最小的内联放宽（React 内联样式需要）', () => {
    for (const kind of ['standalone', 'embed'] as const) {
      expect(directive(buildCsp(kind), 'style-src')).toBe("style-src 'self' 'unsafe-inline'");
    }
  });
});

describe('injectCsp', () => {
  const HTML = '<!doctype html><html><head><title>x</title></head><body></body></html>';

  it('插在 </head> 之前，且内容就是对应入口的策略', () => {
    const out = injectCsp(HTML, 'embed');
    const metaAt = out.indexOf(CSP_META_MARKER);
    const headEndAt = out.toLowerCase().indexOf('</head>');
    expect(metaAt).toBeGreaterThan(-1);
    expect(metaAt).toBeLessThan(headEndAt);
    expect(out).toContain(buildCsp('embed'));
  });

  it('standalone 注入的产物里不含高德域名', () => {
    const out = injectCsp(HTML, 'standalone');
    expect(out).not.toContain('amap');
  });

  it('幂等：重复注入只有一份', () => {
    const once = injectCsp(HTML, 'standalone');
    const twice = injectCsp(once, 'standalone');
    expect(twice).toBe(once);
    expect(once.split('Content-Security-Policy').length - 1).toBe(1);
  });

  it('幂等对另一个入口同样成立（不会把 embed 的策略再叠一层）', () => {
    const once = injectCsp(HTML, 'embed');
    const twice = injectCsp(once, 'embed');
    expect(twice).toBe(once);
    expect(once.split('Content-Security-Policy').length - 1).toBe(1);
  });

  it('没有 </head> 时原样返回（不往文档中间乱插）', () => {
    const noHead = '<div>fragment</div>';
    expect(injectCsp(noHead, 'standalone')).toBe(noHead);
  });

  it('大小写不敏感地找 </HEAD>', () => {
    const upper = '<html><HEAD><title>y</title></HEAD><body></body></html>';
    const out = injectCsp(upper, 'embed');
    expect(out.indexOf(CSP_META_MARKER)).toBeLessThan(out.toLowerCase().indexOf('</head>'));
  });
});
