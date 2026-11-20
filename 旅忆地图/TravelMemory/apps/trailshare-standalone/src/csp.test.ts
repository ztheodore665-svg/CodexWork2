/** CSP（W12-4 改造后：两个入口两套策略）。
 *
 * 这个文件是"承诺是否可信"的守门人，所以断言写得比业务测试更硬：
 *  - standalone **一个 amap 字样都不能有**（零网络的唯一证据）；
 *  - embed 必须放行 `webapi.amap.com` 与 `*.amap.com`（否则地图加载不了）；
 *  - 两者都幂等、都带 `frame-ancestors`、都只允许同源脚本/连接。
 */

import { describe, expect, it } from 'vitest';

import {
  AMAP_ASSET_ORIGIN,
  AMAP_SCRIPT_ORIGIN,
  CSP_META_MARKER,
  PARENT_ORIGIN,
  buildCsp,
  entryOf,
  injectCsp,
} from './csp';

/** 取出某条指令的整段（`connect-src 'self'`）。 */
function directive(csp: string, name: string): string {
  const found = csp
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${name} `));
  return found ?? '';
}

describe('buildCsp —— standalone（全程零网络请求）', () => {
  const csp = buildCsp('standalone');

  it('首行是 default-src self，且只允许同源脚本/连接', () => {
    expect(csp.startsWith("default-src 'self'")).toBe(true);
    expect(directive(csp, 'script-src')).toBe("script-src 'self'");
    expect(directive(csp, 'connect-src')).toBe("connect-src 'self'");
  });

  it('**不含任何 amap 字样**：连地图瓦片的例外都没有', () => {
    expect(csp.toLowerCase()).not.toContain('amap');
  });

  it('带 frame-ancestors 与主应用 origin', () => {
    expect(directive(csp, 'frame-ancestors')).toContain('frame-ancestors');
    expect(csp).toContain(PARENT_ORIGIN);
  });

  it('img-src 允许 data: 与 blob:（缩略图 / 预览）', () => {
    expect(directive(csp, 'img-src')).toContain('data:');
    expect(directive(csp, 'img-src')).toContain('blob:');
  });

  it('缺省参数就是 standalone（最严格的策略是安全默认值）', () => {
    expect(buildCsp()).toBe(csp);
  });
});

describe('buildCsp —— embed（除地图底图外零网络）', () => {
  const csp = buildCsp('embed');

  it('放行高德脚本与瓦片域名', () => {
    expect(directive(csp, 'script-src')).toContain(AMAP_SCRIPT_ORIGIN);
    expect(directive(csp, 'img-src')).toContain(AMAP_ASSET_ORIGIN);
  });

  it('**connect-src 不含 amap**：高德只允许走 script/img，不许发 fetch/XHR', () => {
    // 任务书 §6.4.3：embed 的 connect-src 必须是 `'self'`。
    // 放行 *.amap.com 会把"除了地图底图没有其他网络请求"降级成"还能向高德发请求"，
    // 所以这条断言要能挡住将来顺手放宽的改动。
    expect(directive(csp, 'connect-src')).toBe("connect-src 'self'");
    expect(directive(csp, 'connect-src').toLowerCase()).not.toContain('amap');
  });

  it('script-src 除 webapi.amap.com 外没有第二个外域', () => {
    const scriptSrc = directive(csp, 'script-src');
    expect(scriptSrc).toContain("'self'");
    const origins = scriptSrc
      .replace("script-src 'self'", '')
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    expect(origins).toEqual([AMAP_SCRIPT_ORIGIN]);
  });

  it('两个入口的 connect-src 一致（都只有 self）', () => {
    expect(directive(buildCsp('embed'), 'connect-src')).toBe("connect-src 'self'");
    expect(directive(buildCsp('standalone'), 'connect-src')).toBe("connect-src 'self'");
  });

  it('带 frame-ancestors 与主应用 origin', () => {
    expect(directive(csp, 'frame-ancestors')).toContain(PARENT_ORIGIN);
  });

  it('两份策略确实不同（不是同一条字符串）', () => {
    expect(buildCsp('embed')).not.toBe(buildCsp('standalone'));
  });
});

describe('injectCsp', () => {
  const HTML = '<!doctype html><html><head><title>x</title></head><body></body></html>';

  it('按入口注入对应策略，且只插一次（幂等）', () => {
    const standalone = injectCsp(HTML, 'standalone');
    const embed = injectCsp(HTML, 'embed');

    expect(standalone).toContain(buildCsp('standalone'));
    expect(standalone.toLowerCase()).not.toContain('amap');
    expect(embed).toContain(buildCsp('embed'));
    expect(embed).toContain(AMAP_SCRIPT_ORIGIN);
    expect(embed).not.toBe(standalone);

    // 幂等：再注入一次不改动任何字符
    expect(injectCsp(standalone, 'standalone')).toBe(standalone);
    expect(injectCsp(embed, 'embed')).toBe(embed);
    // 插在 </head> 之前
    expect(standalone.indexOf(CSP_META_MARKER)).toBeLessThan(standalone.indexOf('</head>'));
  });

  it('缺省参数仍然是 standalone', () => {
    expect(injectCsp(HTML)).toContain(buildCsp('standalone'));
  });

  it('没有 </head> 时原样返回', () => {
    const fragment = '<div>only a fragment</div>';
    expect(injectCsp(fragment, 'embed')).toBe(fragment);
  });
});

describe('entryOf', () => {
  it('优先看 data-entry 标记', () => {
    expect(entryOf('<html data-entry="embed">')).toBe('embed');
    expect(entryOf('<html data-entry="standalone">')).toBe('standalone');
  });

  it('没有标记时退回看构建路径', () => {
    expect(entryOf('<html>', '/src/../embed.html')).toBe('embed');
    expect(entryOf('<html>', '/index.html')).toBe('standalone');
    expect(entryOf('<html>')).toBe('standalone');
  });
});
