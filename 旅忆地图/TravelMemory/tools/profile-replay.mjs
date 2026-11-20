// 展示站回放「前段满帧 vs 后段掉帧」的 CPU 归因。
//
// 做法：同一页面、同一次播放，分别在播放开始后 3s 和 30s 各抓 10 秒 V8 CPU profile，
// 按函数名聚合 self time，对比后段多出来的耗时落在谁身上。同时采 rAF 帧间隔确认
// 两轮确实对应「满帧」和「掉帧」两种状态。
//
//   cd F:/project/travelmemory && node tools/profile-replay.mjs
//
// 自包含：自己拉起 showcase 的 vite preview + 无头 Edge，跑完自己清理。
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import os from 'node:os';
import path from 'node:path';

const require = createRequire('F:/project/travelmemory/package.json');
const WebSocket = require('ws');

const ROOT = 'F:/project/travelmemory';
const PORT = 4202;
const CDP = 9342;
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PROFILE = path.join(os.tmpdir(), `tm-profile-replay-${process.pid}`);
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

const vite = spawn(process.execPath, ['../node_modules/vite/bin/vite.js', 'preview', '--port', String(PORT), '--host', '127.0.0.1'], {
  cwd: path.join(ROOT, 'showcase'), stdio: ['ignore', 'pipe', 'pipe'],
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
const ws = new WebSocket(list.find((t) => t.type === 'page').webSocketDebuggerUrl, { perMessageDeflate: false, maxPayload: 128 * 1024 * 1024 });
await new Promise((r) => ws.once('open', r));
let seq = 0;
const pending = new Map();
ws.on('message', (raw) => {
  const j = JSON.parse(raw);
  if (j.id && pending.has(j.id)) {
    const { res, rej } = pending.get(j.id);
    pending.delete(j.id);
    j.error ? rej(new Error(JSON.stringify(j.error))) : res(j.result);
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
await cdp('Profiler.enable');
await cdp('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await cdp('Page.navigate', { url: `http://127.0.0.1:${PORT}/` });
await sleep(4000);
await ev(`[...document.querySelectorAll('button')].find(b=>/演示轨迹/.test(b.textContent||''))?.click()`);
await sleep(1500);
await ev(`[...document.querySelectorAll('button')].find(b=>/青甘大环线/.test(b.textContent||''))?.click()`);
await sleep(3000);
await ev(`(()=>{const b=[...document.querySelectorAll('button')].find(x=>/^播放$/.test((x.getAttribute('aria-label')||'').trim()));if(b)b.click();return 1})()`);

const shortName = (url) => {
  if (!url) return '(native)';
  const m = url.match(/[^/\\]+\.js(?:\?.*)?$/);
  if (m) return m[0].split('?')[0];
  if (url.startsWith('http')) { try { return new URL(url).host; } catch { return url.slice(0, 28); } }
  return url.slice(0, 28);
};

const aggregate = (profile) => {
  const nodes = new Map(profile.nodes.map((n) => [n.id, n]));
  const byFn = new Map();
  for (let i = 0; i < profile.samples.length; i++) {
    const node = nodes.get(profile.samples[i]);
    if (!node) continue;
    const dt = profile.timeDeltas[i] ?? 0;
    const cf = node.callFrame;
    const name = cf.functionName || '(anonymous)';
    const key = `${name} @ ${shortName(cf.url)}:${cf.lineNumber + 1}`;
    const cur = byFn.get(key) ?? { ms: 0, hits: 0 };
    cur.ms += dt / 1000;
    cur.hits += 1;
    byFn.set(key, cur);
  }
  return byFn;
};

const round = async (label, waitBeforeMs, spanMs) => {
  await sleep(waitBeforeMs);
  await ev(`(() => { window.__fr=[]; let last=performance.now(); window.__ss=true;
    const l=(t)=>{ window.__fr.push(t-last); last=t; if(window.__ss) requestAnimationFrame(l); };
    requestAnimationFrame(l); return 1; })()`);
  await cdp('Profiler.start');
  await sleep(spanMs);
  const { profile } = await cdp('Profiler.stop');
  const fps = JSON.parse(await ev(`(() => { window.__ss=false; const f=window.__fr.slice(3).sort((a,b)=>a-b);
    const n=f.length||1, m=f.reduce((a,b)=>a+b,0)/n;
    const pct=document.querySelector('[role=slider]')?.getAttribute('aria-valuenow') ?? '?';
    return JSON.stringify({ fps:+(1000/m).toFixed(1), long:+(f.filter(x=>x>24).length/n*100).toFixed(1), pct }); })()`));
  console.log(`\n【${label}】耗时 ${spanMs / 1000}s，进度 ${fps.pct}%，fps ${fps.fps}，长帧 ${fps.long}%`);
  return { label, fps, byFn: aggregate(profile) };
};

const early = await round('前段（对照）', 3000, 10000);
const late = await round('后段（掉帧）', 30000 - 3000 - 10000, 10000);

const total = (m) => [...m.values()].reduce((a, b) => a + b.ms, 0);
console.log(`\n采样总时长：前段 ${total(early.byFn).toFixed(0)}ms / 后段 ${total(late.byFn).toFixed(0)}ms`);

const keys = new Set([...early.byFn.keys(), ...late.byFn.keys()]);
const rows = [...keys].map((k) => {
  const a = early.byFn.get(k)?.ms ?? 0;
  const b = late.byFn.get(k)?.ms ?? 0;
  return { k, a, b, d: b - a };
}).sort((x, y) => y.b - x.b);

console.log('\n── 按「后段 self time」排序（ms，10 秒窗口）──');
console.log('  后段    前段     增量  函数');
for (const r of rows.slice(0, 22)) {
  console.log(`${r.b.toFixed(1).padStart(7)} ${r.a.toFixed(1).padStart(7)} ${(r.d >= 0 ? '+' : '') + r.d.toFixed(1).padStart(7)}  ${r.k}`);
}
console.log('\n── 按「增量」排序：后段比前段多出来的开销 ──');
for (const r of [...rows].sort((x, y) => y.d - x.d).slice(0, 12)) {
  console.log(`${(r.d >= 0 ? '+' : '') + r.d.toFixed(1).padStart(7)}ms  (${r.a.toFixed(1)} → ${r.b.toFixed(1)})  ${r.k}`);
}

ws.close();
cleanup();
