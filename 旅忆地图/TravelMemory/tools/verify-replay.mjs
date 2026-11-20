// 主应用「轨迹回放」层真机验收：键盘无障碍 + 完整播放周期帧率曲线。
//
// 后端依赖 Postgres 且库为空，直接起服务打不开行程抽屉。这里用 CDP Fetch 域在浏览器
// 层拦截 /api/v1/*，喂一份与展示站青甘大环线同坐标、同照片数的假数据 —— 不碰后端、
// 不改产品代码，且两边帧率可比。
//
//   cd F:/project/travelmemory
//   npm run build --workspace=travel-memory-frontend
//   node tools/verify-replay.mjs
//
// 自包含：自己拉起 frontend 的 vite preview + 无头 Edge，跑完自己清理。
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import os from 'node:os';
import path from 'node:path';

const require = createRequire('F:/project/travelmemory/package.json');
const WebSocket = require('ws');

const ROOT = 'F:/project/travelmemory';
const PORT = 4201;
const CDP = 9341;
const WINDOW = 5;
const ROUNDS = 9;
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PROFILE = path.join(os.tmpdir(), `tm-verify-replay-${process.pid}`);
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

// ── 假数据：与 showcase/src/data/demoJourneys.ts 的青甘大环线同站点 ──────────
const STATIONS = [
  ['西宁', 36.6171, 101.7782], ['塔尔寺', 36.4827, 101.5698], ['日月山', 36.4, 100.9833],
  ['青海湖·二郎剑', 36.5333, 100.5], ['黑马河', 36.7333, 100.0833], ['茶卡盐湖', 36.7, 99.0833],
  ['德令哈', 37.3667, 97.3667], ['大柴旦翡翠湖', 37.7167, 95.1833], ['当金山口', 39.2, 94.3],
  ['敦煌鸣沙山', 40.0833, 94.6667], ['莫高窟', 40.0422, 94.8053], ['嘉峪关', 39.8, 98.2833],
  ['张掖七彩丹霞', 38.95, 100.1167], ['祁连卓尔山', 38.1833, 100.2333],
  ['门源油菜花', 37.3833, 101.6167], ['西宁', 36.6171, 101.7782],
];
const pad = (n) => String(n).padStart(2, '0');
const wallClock = (i, j) => {
  // 16 站 × 2 张，跨 7 天；第 i 站第 j 张
  const minutes = i * 10 * 60 + j * 40;
  const day = 12 + Math.floor(minutes / 1440);
  const rem = minutes % 1440;
  return `2026-07-${pad(day)}T${pad(Math.floor(rem / 60))}:${pad(rem % 60)}:00`;
};
const PHOTOS = [];
const ITEMS = [];
for (let i = 0; i < STATIONS.length; i++) {
  for (let j = 0; j < 2; j++) {
    const idx = i * 2 + j;
    const [name, lat, lng] = STATIONS[i];
    const jitter = ((idx * 37) % 11 - 5) * 0.0012; // 让同站两张照片略有偏移，模拟真实抖动
    PHOTOS.push({
      id: `p${idx}`, hash: `h${idx}`, taken_at: null,
      taken_at_local: wallClock(i, j), tz_name: 'Asia/Shanghai',
      lat: lat + jitter, lng: lng + jitter, coord_system: 'WGS84',
      location_source: 'exif', has_location: true, location_confidence: 0.9,
      caption: name, created_at: '2026-07-20T00:00:00Z',
    });
    ITEMS.push({
      id: `item-${idx}`, photo_id: `p${idx}`, taken_at: null,
      lat: lat + jitter, lng: lng + jitter,
      semantic_tag: null, semantic_label: name,
    });
  }
}
const TRIPS = {
  trips: [{
    id: 'trip-qinghai', title: '青甘大环线',
    started_at: '2026-07-12T08:00:00Z', ended_at: '2026-07-18T20:00:00Z',
    source: 'cluster', item_count: ITEMS.length, items: ITEMS,
  }],
};

// ── 启动服务 ──────────────────────────────────────────────────────────────
const vite = spawn(process.execPath, ['../node_modules/vite/bin/vite.js', 'preview', '--port', String(PORT), '--host', '127.0.0.1'], {
  cwd: path.join(ROOT, 'frontend'), stdio: ['ignore', 'pipe', 'pipe'],
});
let vlog = '';
vite.stdout.on('data', (d) => { vlog += d.toString(); });
vite.stderr.on('data', (d) => { vlog += d.toString(); });
children.push(vite);
children.push(spawn(EDGE, [
  `--remote-debugging-port=${CDP}`, '--remote-allow-origins=*', `--user-data-dir=${PROFILE}`,
  '--no-first-run', '--no-default-browser-check', '--window-size=1460,1000',
  '--headless=new', 'about:blank',
], { stdio: 'ignore' }));
try { await waitHttp(`http://127.0.0.1:${PORT}/`); } catch (e) { console.error(vlog); throw e; }
await waitHttp(`http://127.0.0.1:${CDP}/json/version`);

const list = await fetch(`http://127.0.0.1:${CDP}/json/list`).then((r) => r.json());
const ws = new WebSocket(list.find((t) => t.type === 'page').webSocketDebuggerUrl, { perMessageDeflate: false });
await new Promise((r) => ws.once('open', r));

let seq = 0;
const pending = new Map();
const cdp = (method, params = {}) => new Promise((res, rej) => {
  const id = ++seq;
  pending.set(id, { res, rej });
  ws.send(JSON.stringify({ id, method, params }));
});
const b64 = (s) => Buffer.from(s, 'utf8').toString('base64');
const reply = (requestId, body, code = 200) => cdp('Fetch.fulfillRequest', {
  requestId, responseCode: code,
  responseHeaders: [
    { name: 'Content-Type', value: 'application/json; charset=utf-8' },
    { name: 'Access-Control-Allow-Origin', value: '*' },
  ],
  body: b64(body),
});

const apiLog = [];
ws.on('message', (raw) => {
  const j = JSON.parse(raw);
  if (j.id && pending.has(j.id)) {
    const { res, rej } = pending.get(j.id);
    pending.delete(j.id);
    j.error ? rej(new Error(JSON.stringify(j.error))) : res(j.result);
    return;
  }
  if (j.method === 'Fetch.requestPaused') {
    const { requestId, request } = j.params;
    const url = request.url;
    apiLog.push(`${request.method} ${url.replace(/^https?:\/\/[^/]+/, '')}`);
    if (url.includes('/api/v1/trips')) reply(requestId, JSON.stringify(TRIPS));
    else if (url.includes('/api/v1/photos') && !url.includes('/import')) reply(requestId, JSON.stringify(PHOTOS));
    else reply(requestId, '{}', 200); // 其他接口给空对象，避免 toast 报错干扰
  }
});
const ev = async (expression) => {
  const r = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
  return r.result?.value;
};

await cdp('Page.enable');
await cdp('Runtime.enable');
await cdp('Fetch.enable', { patterns: [{ urlPattern: '*/api/v1/*', requestStage: 'Request' }] });
await cdp('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await cdp('Page.navigate', { url: `http://127.0.0.1:${PORT}/` });
await sleep(4500);

console.log('拦截到的接口:', [...new Set(apiLog)].slice(0, 8).join('  '));

// ── 打开行程抽屉 → 点回放 ───────────────────────────────────────────────────
const state0 = await ev(`(() => JSON.stringify({
  body: document.body.innerText.slice(0, 180),
  drawerBtns: [...document.querySelectorAll('button')].map(b => (b.getAttribute('aria-label')||'') + '|' + (b.textContent||'').trim().slice(0,16)).slice(0,18)
}))()`);
console.log('页面状态:', state0);

await ev(`(() => { const b = document.querySelector('button[aria-label*="行程与相册"]'); if (b) b.click(); return 1; })()`);
await sleep(1200);
const entry = await ev(`(() => JSON.stringify([...document.querySelectorAll('button')]
  .map(b => b.getAttribute('aria-label')||'')
  .filter(x => /回放|播放/.test(x)).slice(0, 6)))()`);
console.log('回放入口:', entry);
await ev(`(() => { const b = [...document.querySelectorAll('button')]
  .find(x => /回放/.test(x.getAttribute('aria-label')||'')); if (b) b.click(); return 1; })()`);
await sleep(3000);

const stage = JSON.parse(await ev(`(() => { const r = document.querySelector('.replay-root');
  if (!r) return JSON.stringify({ open: false });
  const rect = r.getBoundingClientRect();
  const c = r.querySelector('canvas');
  const cr = c?.getBoundingClientRect();
  const sl = r.querySelector('[role=slider]');
  return JSON.stringify({ open: true, size: [Math.round(rect.width), Math.round(rect.height)],
    canvas: cr ? [Math.round(cr.width), Math.round(cr.height)] : null,
    backing: c ? [c.width, c.height] : null,
    slider: sl ? { now: sl.getAttribute('aria-valuenow'), text: sl.getAttribute('aria-valuetext'), tabIndex: sl.tabIndex } : null }); })()`));
console.log('回放层:', JSON.stringify(stage));
if (!stage.open) { console.error('回放层没打开，后续验收中止'); ws.close(); cleanup(); process.exit(1); }

// ── A. 键盘无障碍真机复验 ──────────────────────────────────────────────────
console.log('\n── A. 键盘复验（真实 Input.dispatchKeyEvent）──');
// 先暂停，避免播放推进污染 seek 断言
await ev(`(() => { const b=[...document.querySelectorAll('.replay-root button')].find(x=>/暂停/.test(x.getAttribute('aria-label')||'')); if(b)b.click(); return 1; })()`);
await sleep(400);
await ev(`(() => { const sl=document.querySelector('[role=slider]'); sl.focus();
  sl.addEventListener('keydown', (e)=>{ setTimeout(()=>{ window.__lastKey=e.key; window.__lastPrevented=e.defaultPrevented; },0); });
  return 1; })()`);

const key = async (k, code, vk, modifiers = 0) => {
  const base = { key: k, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers };
  await cdp('Input.dispatchKeyEvent', { type: 'keyDown', ...base });
  await cdp('Input.dispatchKeyEvent', { type: 'keyUp', ...base });
  await sleep(120);
};
const read = async () => JSON.parse(await ev(`(() => { const sl=document.querySelector('[role=slider]');
  return JSON.stringify({ now:+sl.getAttribute('aria-valuenow'), text:sl.getAttribute('aria-valuetext'),
    focused: document.activeElement===sl, key:window.__lastKey||'', prevented:window.__lastPrevented }); })()`));

// 归零起点
await ev(`(()=>{const sl=document.querySelector('[role=slider]');sl.focus();return 1})()`);
await key('Home', 'Home', 36);
const r0 = await read();
await key('ArrowRight', 'ArrowRight', 39);
const r1 = await read();
await key('ArrowRight', 'ArrowRight', 39, 8); // Shift 精调
const r2 = await read();
await key('PageUp', 'PageUp', 33);
const r3 = await read();
await key('End', 'End', 35);
const r4 = await read();
await key('ArrowRight', 'ArrowRight', 39); // 到头后仍不越界
const r5 = await read();
await key('a', 'KeyA', 65); // 不该被拦
const r6 = await read();

console.log(`  Home        → now=${r0.now}  text="${r0.text}"`);
console.log(`  ArrowRight  → now=${r1.now}  prevented=${r1.prevented}`);
console.log(`  Shift+Right → now=${r2.now}  （期望 +1）`);
console.log(`  PageUp      → now=${r3.now}  （期望 +10）`);
console.log(`  End         → now=${r4.now}  text="${r4.text}"`);
console.log(`  End 后再右  → now=${r5.now}  （期望仍 100）`);
console.log(`  字符键 a    → now=${r6.now}  prevented=${r6.prevented}  （期望 false）`);
const kbPass =
  r0.now === 0 && r1.now === 5 && r1.prevented === true &&
  r2.now === 6 && r3.now === 16 && r4.now === 100 && r5.now === 100 &&
  r6.prevented === false;
console.log(`  键盘结论: ${kbPass ? 'PASS' : 'FAIL'}`);

// Esc 关闭 + 焦点归还
await key('Escape', 'Escape', 27);
await sleep(700);
const afterEsc = JSON.parse(await ev(`(() => JSON.stringify({
  open: !!document.querySelector('.replay-root'),
  active: document.activeElement?.getAttribute('aria-label') || document.activeElement?.tagName,
}))()`));
console.log(`  Esc        → open=${afterEsc.open}  焦点="${afterEsc.active}"  ${!afterEsc.open ? 'PASS' : 'FAIL'}`);

// ── B. 主应用回放层的完整帧率曲线 ─────────────────────────────────────────
console.log('\n── B. 主应用回放帧率曲线（重开一次，采完整播放周期）──');
await ev(`(() => { const b=[...document.querySelectorAll('button')].find(x=>/回放/.test(x.getAttribute('aria-label')||'')); if(b)b.click(); return 1; })()`);
await sleep(2500);
const reopened = await ev(`!!document.querySelector('.replay-root')`);
console.log('  重开回放层:', reopened);
if (!reopened) { console.error('重开失败'); ws.close(); cleanup(); process.exit(1); }

await ev(`(() => { window.__fr=[]; let last=performance.now(); window.__ss=true;
  const l=(t)=>{ window.__fr.push([t,t-last]); last=t; if(window.__ss) requestAnimationFrame(l); };
  requestAnimationFrame(l); return 1; })()`);
console.log('   #   时间    fps    p50   p90   p99   long%   进度%');
const fpsRows = [];
for (let i = 0; i < ROUNDS; i++) {
  await sleep(WINDOW * 1000);
  const stat = JSON.parse(await ev(`(() => { const raw=window.__fr; const t1=performance.now(); const start=t1-${WINDOW * 1000}-60;
    const f=raw.filter(x=>x[0]>=start).map(x=>x[1]).sort((a,b)=>a-b); const n=f.length||1;
    const q=p=>f[Math.min(n-1,Math.floor(n*p))]??0; const m=f.reduce((a,b)=>a+b,0)/n;
    return JSON.stringify({ fps:+(1000/m).toFixed(1), p50:+q(.5).toFixed(1), p90:+q(.9).toFixed(1),
      p99:+q(.99).toFixed(1), long:+(f.filter(x=>x>24).length/n*100).toFixed(1), n }); })()`));
  const pct = await ev(`document.querySelector('.replay-root [role=slider]')?.getAttribute('aria-valuenow') ?? '?'`);
  const row = { t: (i + 1) * WINDOW, fps: stat.fps, p90: stat.p90, long: stat.long, pct };
  fpsRows.push(row);
  console.log(`  ${String(i + 1).padStart(2)}  ${String(row.t).padStart(4)}s  ${String(stat.fps).padStart(5)}  ${String(stat.p50).padStart(5)}  ${String(stat.p90).padStart(5)}  ${String(stat.p99).padStart(5)}  ${String(stat.long).padStart(5)}   ${String(pct).padStart(5)}`);
}
await ev('window.__ss=false');

console.log('\n汇总 JSON（供文档引用）:');
console.log(JSON.stringify({ keyboard: kbPass, esc: !afterEsc.open, fps: fpsRows }));
ws.close();
cleanup();
