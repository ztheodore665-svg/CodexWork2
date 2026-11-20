// 帧率验收工具：连续采 N 个窗口，同时记录播放进度、长帧率、瓦片数量。
// 自包含：自己拉起 showcase 的 vite preview + 无头 Edge，跑完自己清理。
//
//   cd F:/project/travelmemory
//   npm run build --workspace=travelmemory-showcase   # 先确保 showcase/dist 是最新的
//   node tools/perf-timeline.mjs 4189 9334 6 10       # 端口、CDP 端口、窗口秒数、窗口数
//
// 为什么必须"连续采完整播放周期"而不是只测 10 秒：帧率随行程进度变化，
// 前 70% 是 60fps、后 30% 是 46fps。只取 10 秒时结论完全取决于恰好落在哪一段。
// 完整调查与判据见 docs/stages/stage9-trail-replay.md §6.4。
//
// 判据：前 70% 行程 ≥59fps；后段 ≥45fps、p90 ≤33ms；无连续 3 帧以上长帧。
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import os from 'node:os';
import path from 'node:path';

const require = createRequire('F:/project/travelmemory/package.json');
const WebSocket = require('ws');

const ROOT = 'F:/project/travelmemory';
const PORT = Number(process.argv[2] ?? 4189);
const CDP = Number(process.argv[3] ?? 9334);
const WINDOW = Number(process.argv[4] ?? 6);
const ROUNDS = Number(process.argv[5] ?? 10);
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PROFILE = path.join(os.tmpdir(), `tm-timeline-${process.pid}`);
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

const vite = spawn(process.execPath, ['../node_modules/vite/bin/vite.js', 'preview', '--port', String(PORT), '--host', '127.0.0.1'], { cwd: path.join(ROOT, 'showcase'), stdio: ['ignore', 'pipe', 'pipe'] });
let viteLog = '';
vite.stdout.on('data', (d) => { viteLog += d.toString(); });
vite.stderr.on('data', (d) => { viteLog += d.toString(); });
vite.on('error', (e) => { viteLog += 'SPAWN ERROR: ' + e.message; });
children.push(vite);
children.push(spawn(EDGE, [`--remote-debugging-port=${CDP}`, '--remote-allow-origins=*', `--user-data-dir=${PROFILE}`, '--no-first-run', '--no-default-browser-check', '--window-size=1460,1000', '--headless=new', 'about:blank'], { stdio: 'ignore' }));

try {
  await waitHttp(`http://127.0.0.1:${PORT}/`);
} catch (e) {
  console.error('vite 输出:', viteLog);
  throw e;
}
await waitHttp(`http://127.0.0.1:${CDP}/json/version`);
const list = await fetch(`http://127.0.0.1:${CDP}/json/list`).then((r) => r.json());
const ws = new WebSocket(list.find((t) => t.type === 'page').webSocketDebuggerUrl, { perMessageDeflate: false });
await new Promise((r) => ws.once('open', r));
let seq = 0;
const pending = new Map();
ws.on('message', (raw) => {
  const j = JSON.parse(raw);
  if (j.id && pending.has(j.id)) { const { res, rej } = pending.get(j.id); pending.delete(j.id); j.error ? rej(new Error(JSON.stringify(j.error))) : res(j.result); }
});
const cdp = (m, p = {}) => new Promise((res, rej) => { const id = ++seq; pending.set(id, { res, rej }); ws.send(JSON.stringify({ id, method: m, params: p })); });
const ev = async (expression) => (await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result?.value;

await cdp('Page.enable'); await cdp('Runtime.enable');
await cdp('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await cdp('Page.navigate', { url: `http://127.0.0.1:${PORT}/` });
await sleep(4000);
await ev(`[...document.querySelectorAll('button')].find(b=>/演示轨迹/.test(b.textContent||''))?.click()`);
await sleep(1500);
await ev(`[...document.querySelectorAll('button')].find(b=>/青甘大环线/.test(b.textContent||''))?.click()`);
await sleep(3000);
await ev(`(()=>{const b=[...document.querySelectorAll('button')].find(x=>/^播放$/.test((x.getAttribute('aria-label')||'').trim()));if(b)b.click();return 1})()`);

// 连续采样器：只记录 [时间戳, 帧间隔]
await ev(`(() => { window.__f=[]; let last=performance.now(); window.__s=true;
  const loop=(t)=>{ window.__f.push([t,t-last]); last=t; if(window.__s) requestAnimationFrame(loop); };
  requestAnimationFrame(loop); return 1; })()`);

const progress = `(() => {
  const sl = document.querySelector('[role=slider]');
  const txt = document.body.innerText.match(/(\\d{1,3})%/);
  const tiles = document.querySelectorAll('.amap-container img, .amap-layers img').length;
  const tl = document.querySelector('.tl-track canvas');
  return JSON.stringify({ pct: sl ? sl.getAttribute('aria-valuenow') : (txt ? txt[1] : '?'), tiles,
    tl: tl ? [tl.width, tl.height] : null });
})()`;

console.log(`窗口 ${WINDOW}s × ${ROUNDS}，展示站青甘大环线，1440×900 DPR1`);
console.log(' #   时间    fps    p50   p90   p99   long%   进度%   时间轴canvas');
for (let i = 0; i < ROUNDS; i++) {
  await sleep(WINDOW * 1000);
  const stat = JSON.parse(await ev(`(() => { const raw=window.__f; const t1=performance.now(); const start=t1-${WINDOW * 1000}-60;
    const f=raw.filter(x=>x[0]>=start).map(x=>x[1]).sort((a,b)=>a-b); const n=f.length||1;
    const q=p=>f[Math.min(n-1,Math.floor(n*p))]??0; const m=f.reduce((a,b)=>a+b,0)/n;
    return JSON.stringify({ fps:+(1000/m).toFixed(1), p50:+q(.5).toFixed(1), p90:+q(.9).toFixed(1), p99:+q(.99).toFixed(1), long:+(f.filter(x=>x>24).length/n*100).toFixed(1), n }); })()`));
  const p = JSON.parse(await ev(progress));
  console.log(`${String(i + 1).padStart(2)}  ${String((i + 1) * WINDOW).padStart(4)}s  ${String(stat.fps).padStart(5)}  ${String(stat.p50).padStart(5)}  ${String(stat.p90).padStart(5)}  ${String(stat.p99).padStart(5)}  ${String(stat.long).padStart(5)}   ${String(p.pct).padStart(5)}   ${p.tl ? p.tl.join('x') : 'null'}`);
}
await ev('window.__s=false');
ws.close(); cleanup();
