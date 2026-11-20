// 合并加载器后的真机验收：三处地图都要真的出图，且安全密钥字段写对。
//
// 覆盖：
//   A. 主应用地图页（MapCanvas）
//   B. 主应用回放层（replayMap；会先卸载 A 的地图再新建一张）
//   C. 展示站回放层（baseMap；走的是「失败返回 null 降级」那条策略）
//
// 每处检查：window.AMap、script[data-amap-loader] 数量、_AMapSecurityConfig 的键、
// .amap-container 存在、canvas backing 尺寸、控制台报错。
//
//   cd F:/project/travelmemory && node tools/verify-amap.mjs
// 自包含：自己拉起两个预览 + 无头 Edge，跑完自己清理。
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import os from 'node:os';
import path from 'node:path';

const require = createRequire('F:/project/travelmemory/package.json');
const WebSocket = require('ws');

const ROOT = 'F:/project/travelmemory';
const PORT_FE = 4211;
const PORT_SC = 4212;
const CDP = 9351;
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PROFILE_DIR = path.join(os.tmpdir(), `tm-verify-amap-${process.pid}`);
const children = [];
const cleanup = () => { for (const c of children) { try { c.kill('SIGKILL'); } catch { /* gone */ } } };
process.on('exit', cleanup);

const waitHttp = async (url, ms = 30000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { const r = await fetch(url); if (r.status < 500) return; } catch { /* not up */ }
    await sleep(250);
  }
  throw new Error('服务未就绪: ' + url);
};

for (const [cwd, port] of [[path.join(ROOT, 'frontend'), PORT_FE], [path.join(ROOT, 'showcase'), PORT_SC]]) {
  children.push(spawn(process.execPath, ['../node_modules/vite/bin/vite.js', 'preview', '--port', String(port), '--host', '127.0.0.1'], { cwd, stdio: 'ignore' }));
}
children.push(spawn(EDGE, [
  `--remote-debugging-port=${CDP}`, '--remote-allow-origins=*', `--user-data-dir=${PROFILE_DIR}`,
  '--no-first-run', '--no-default-browser-check', '--window-size=1460,1000',
  '--headless=new', 'about:blank',
], { stdio: 'ignore' }));
await waitHttp(`http://127.0.0.1:${PORT_FE}/`);
await waitHttp(`http://127.0.0.1:${PORT_SC}/`);
await waitHttp(`http://127.0.0.1:${CDP}/json/version`);

const list = await fetch(`http://127.0.0.1:${CDP}/json/list`).then((r) => r.json());
const ws = new WebSocket(list.find((t) => t.type === 'page').webSocketDebuggerUrl, { perMessageDeflate: false });
await new Promise((r) => ws.once('open', r));

let seq = 0;
const pending = new Map();
const errors = [];
const b64 = (s) => Buffer.from(s, 'utf8').toString('base64');
ws.on('message', (raw) => {
  const j = JSON.parse(raw);
  if (j.id && pending.has(j.id)) {
    const { res, rej } = pending.get(j.id);
    pending.delete(j.id);
    j.error ? rej(new Error(JSON.stringify(j.error))) : res(j.result);
    return;
  }
  if (j.method === 'Runtime.exceptionThrown') {
    errors.push('EXC ' + (j.params.exceptionDetails?.text ?? ''));
  }
  if (j.method === 'Runtime.consoleAPICalled' && j.params.type === 'error') {
    errors.push('CONSOLE ' + j.params.args.map((a) => a.value ?? a.description ?? '').join(' ').slice(0, 180));
  }
  if (j.method === 'Fetch.requestPaused') {
    const { requestId, request } = j.params;
    const url = request.url;
    let body = '{}';
    if (url.includes('/api/v1/trips')) {
      // 最小可用行程：2 个可定位点，足够打开回放入口
      body = JSON.stringify({ trips: [{
        id: 't1', title: '验收行程', started_at: '2026-07-12T08:00:00Z',
        ended_at: '2026-07-12T12:00:00Z', source: 'cluster', item_count: 2,
        items: [
          { id: 'i0', photo_id: 'p0', taken_at: null, lat: 36.6171, lng: 101.7782, semantic_label: '西宁' },
          { id: 'i1', photo_id: 'p1', taken_at: null, lat: 36.4, lng: 100.9833, semantic_label: '日月山' },
        ],
      }] });
    } else if (url.includes('/api/v1/photos') && !url.includes('/import')) {
      const mk = (i, lat, lng) => ({
        id: `p${i}`, hash: `h${i}`, taken_at: null,
        taken_at_local: `2026-07-12T0${8 + i}:00:00`, tz_name: 'Asia/Shanghai',
        lat, lng, coord_system: 'WGS84', location_source: 'exif', has_location: true,
        location_confidence: 0.9, caption: '', created_at: '2026-07-20T00:00:00Z',
      });
      body = JSON.stringify([mk(0, 36.6171, 101.7782), mk(1, 36.4, 100.9833)]);
    }
    cdp('Fetch.fulfillRequest', {
      requestId, responseCode: 200,
      responseHeaders: [{ name: 'Content-Type', value: 'application/json' }, { name: 'Access-Control-Allow-Origin', value: '*' }],
      body: b64(body),
    });
  }
});
const cdp = (method, params = {}) => new Promise((res, rej) => {
  const id = ++seq;
  pending.set(id, { res, rej });
  ws.send(JSON.stringify({ id, method, params }));
});
const ev = async (expression) => (await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result?.value;

await cdp('Page.enable');
await cdp('Runtime.enable');
await cdp('Fetch.enable', { patterns: [{ urlPattern: '*/api/v1/*', requestStage: 'Request' }] });
await cdp('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });

const probe = `(() => {
  const cfg = window._AMapSecurityConfig;
  const cont = document.querySelector('.amap-container');
  const cv = cont?.querySelector('canvas');
  const scope = document.querySelector('.replay-root') ?? document;
  const replayCont = scope.querySelector('.amap-container');
  return JSON.stringify({
    hasAMap: typeof window.AMap !== 'undefined',
    scripts: document.querySelectorAll('script[data-amap-loader]').length,
    allAmapScripts: document.querySelectorAll('script[src*="webapi.amap.com"]').length,
    configKeys: cfg ? Object.keys(cfg) : null,
    wrongField: cfg ? Object.prototype.hasOwnProperty.call(cfg, 'securityCode') : false,
    container: !!cont,
    replayContainer: !!replayCont,
    canvas: cv ? [cv.width, cv.height] : null,
    copyright: /AutoNavi/.test(document.body.innerText),
  });
})()`;

const report = (label, raw) => {
  const p = JSON.parse(raw);
  const pass = p.hasAMap && p.scripts === 1 && p.allAmapScripts === 1 &&
    p.container && !p.wrongField && !!p.canvas && p.canvas[0] > 100;
  console.log(`  ${label.padEnd(22)} AMap=${p.hasAMap} script=${p.scripts}/${p.allAmapScripts} ` +
    `config=${JSON.stringify(p.configKeys)} 错误字段=${p.wrongField} container=${p.container} ` +
    `canvas=${p.canvas?.join('x')} 版权=${p.copyright}  → ${pass ? 'PASS' : 'FAIL'}`);
  return pass;
};

console.log('── 加载器合并后的真机验收 ──');

// A. 主应用地图页
await cdp('Page.navigate', { url: `http://127.0.0.1:${PORT_FE}/` });
await sleep(5000);
const a = report('A 主应用地图页', await ev(probe));

// B. 主应用回放层（会先卸载 A 的地图）
await ev(`(() => { const b = document.querySelector('button[aria-label*="行程与相册"]'); if (b) b.click(); return 1; })()`);
await sleep(1000);
await ev(`(() => { const b = [...document.querySelectorAll('button')].find(x => /回放/.test(x.getAttribute('aria-label')||'')); if (b) b.click(); return 1; })()`);
await sleep(4000);
const b = report('B 主应用回放层', await ev(probe));

// C. 展示站回放层
await cdp('Page.navigate', { url: `http://127.0.0.1:${PORT_SC}/` });
await sleep(4000);
await ev(`[...document.querySelectorAll('button')].find(b=>/演示轨迹/.test(b.textContent||''))?.click()`);
await sleep(1300);
await ev(`[...document.querySelectorAll('button')].find(b=>/青甘大环线/.test(b.textContent||''))?.click()`);
await sleep(4500);
const c = report('C 展示站回放层', await ev(probe));

console.log(`\n控制台报错 ${errors.length} 条:`);
for (const e of errors.slice(0, 12)) console.log('  ' + e);
console.log(`\n总判定: ${a && b && c ? 'PASS' : 'FAIL'}`);
ws.close();
cleanup();
