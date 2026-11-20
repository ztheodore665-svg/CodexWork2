#!/usr/bin/env node
/**
 * 无依赖 CDP 真浏览器验证脚本（QA 用，不进实现、不进现有测试）。
 *
 * 依赖只有 Node 22 自带的全局 `WebSocket` / `fetch`。连到
 * `--remote-debugging-port=<port>` 的 Chrome/Edge，用 CDP 完成三项验证：
 *
 *   --task=webm     在页面里 import('/src/core/videoRecorder.ts') 真录一段 canvas → WebM
 *   --task=offline  等 SW 接管 → 断网 → reload → 看应用壳是否仍在
 *   --task=network  Network.enable 后导航，收集全部请求，算 external / violations
 *
 * 用法：
 *   node browser-verify.mjs --task=webm    --url=http://localhost:5174/
 *   node browser-verify.mjs --task=offline --url=http://localhost:5175/
 *   node browser-verify.mjs --task=network --url=http://localhost:5174/ --clickDemo
 *   （可选 --port=9222，--clickDemo 仅 track-studio 用）
 *
 * 输出：单行 JSON（stdout）。任何诊断信息走 stderr。
 */

const args = parseArgs(process.argv.slice(2));
const PORT = Number(args.port || 9222);
const BROWSER = `http://127.0.0.1:${PORT}`;
const TASK = args.task || 'network';
const URL_ARG = args.url;
const CLICK_DEMO = Boolean(args.clickDemo);
const PAGE_TIMEOUT = Number(args.timeout || 20000);

if (!URL_ARG) {
  fail('缺少 --url=');
}
const BASE = args.base || new URL(URL_ARG).origin;

/* ─────────────────────────── CDP client ─────────────────────────── */

class CDP {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.id = 0;
    this.pending = new Map();
    this.listeners = new Set();
  }

  connect() {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.wsUrl);
      this.ws.onopen = () => resolve();
      this.ws.onerror = (e) => reject(new Error(`ws error: ${e?.message || 'unknown'}`));
      this.ws.onmessage = (ev) => {
        let msg;
        try {
          msg = JSON.parse(typeof ev.data === 'string' ? ev.data : String(ev.data));
        } catch {
          return;
        }
        if (msg.id && this.pending.has(msg.id)) {
          const { resolve: res, reject: rej } = this.pending.get(msg.id);
          this.pending.delete(msg.id);
          if (msg.error) rej(new Error(JSON.stringify(msg.error)));
          else res(msg.result);
          return;
        }
        for (const fn of this.listeners) fn(msg);
      };
      this.ws.onclose = () => {
        for (const { reject: rej } of this.pending.values()) rej(new Error('ws closed'));
        this.pending.clear();
      };
    });
  }

  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  on(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  waitFor(method, timeout = PAGE_TIMEOUT) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.listeners.delete(handler);
        reject(new Error(`timeout waiting ${method}`));
      }, timeout);
      const handler = (msg) => {
        if (msg.method === method) {
          clearTimeout(timer);
          this.listeners.delete(handler);
          resolve(msg.params);
        }
      };
      this.listeners.add(handler);
    });
  }

  close() {
    try {
      this.ws?.close();
    } catch {
      /* ignore */
    }
  }
}

/* ─────────────────────── target 管理 ─────────────────────── */

async function newPageTarget(initialUrl = 'about:blank') {
  // 新版本 Chrome/Edge：PUT /json/new?<url> 建新标签
  try {
    const res = await fetch(`${BROWSER}/json/new?${encodeURIComponent(initialUrl)}`, { method: 'PUT' });
    if (res.ok) {
      const t = await res.json();
      if (t.webSocketDebuggerUrl) return t;
    }
  } catch {
    /* 落到 fallback */
  }
  // fallback：用 /json/list 里已有的 page target，稍后 Page.navigate 过去
  const list = await (await fetch(`${BROWSER}/json/list`)).json();
  const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
  if (!page) throw new Error('找不到可用的 page target');
  return page;
}

async function closeTarget(id) {
  if (!id) return;
  try {
    await fetch(`${BROWSER}/json/close/${id}`);
  } catch {
    /* ignore */
  }
}

async function connectPage(initialUrl) {
  const target = await newPageTarget(initialUrl);
  const cdp = new CDP(target.webSocketDebuggerUrl);
  await cdp.connect();
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  return { cdp, targetId: target.id };
}

async function evalJs(cdp, expression, { awaitPromise = true } = {}) {
  const res = await cdp.send('Runtime.evaluate', {
    expression,
    awaitPromise,
    returnByValue: true,
    userGesture: true,
  });
  if (res.exceptionDetails) {
    throw new Error(
      `evaluate 抛错: ${res.exceptionDetails.exception?.description || res.exceptionDetails.text}`,
    );
  }
  return res.result?.value;
}

async function navigateAndWait(cdp, url) {
  const loaded = cdp.waitFor('Page.loadEventFired', PAGE_TIMEOUT);
  await cdp.send('Page.navigate', { url });
  await loaded;
}

/* ─────────────────────────── 任务 ─────────────────────────── */

async function taskWebm() {
  const { cdp, targetId } = await connectPage(URL_ARG);
  try {
    await navigateAndWait(cdp, URL_ARG);
    // 等 React 首屏挂载（dev 下 Vite 会现编译，给点时间）
    await new Promise((r) => setTimeout(r, 1200));

    const expression = `(async () => {
      const m = await import('/src/core/videoRecorder.ts');
      const meta = {
        canRecord: m.canRecord(),
        hasMediaRecorder: ('MediaRecorder' in window),
        vp9: ('MediaRecorder' in window) ? MediaRecorder.isTypeSupported('video/webm;codecs=vp9') : false,
        vp8: ('MediaRecorder' in window) ? MediaRecorder.isTypeSupported('video/webm;codecs=vp8') : false,
        hasCaptureStream: typeof HTMLCanvasElement !== 'undefined' && typeof HTMLCanvasElement.prototype.captureStream === 'function',
        ua: navigator.userAgent
      };
      if (!m.canRecord()) return { blob: null, meta, reason: 'canRecord() === false' };
      const c = document.createElement('canvas');
      c.width = 320; c.height = 180;
      document.body.appendChild(c);
      const ctx = c.getContext('2d');
      const blob = await m.recordCanvas(c, 800, {
        play: async () => {
          const t0 = performance.now();
          await new Promise((res) => {
            const tick = () => {
              ctx.fillStyle = 'hsl(' + ((performance.now() / 5) % 360) + ' 80% 50%)';
              ctx.fillRect(0, 0, 320, 180);
              if (performance.now() - t0 > 800) res();
              else requestAnimationFrame(tick);
            };
            tick();
          });
        }
      });
      c.remove();
      return { blob: blob ? { size: blob.size, type: blob.type } : null, meta };
    })()`;

    const out = await evalJs(cdp, expression);
    const pass = Boolean(out?.blob && out.blob.size > 2000 && /webm/i.test(out.blob.type || ''));
    return { task: 'webm', pass, ...out };
  } finally {
    cdp.close();
    await closeTarget(targetId);
  }
}

async function taskOffline() {
  const { cdp, targetId } = await connectPage(URL_ARG);
  try {
    await cdp.send('Network.enable');
    await navigateAndWait(cdp, URL_ARG);
    await new Promise((r) => setTimeout(r, 800));

    // 等 SW ready + 接管
    const swState = await evalJs(
      cdp,
      `(async () => {
        let ready = false, controller = false, scope = '', error = '';
        try {
          const reg = await navigator.serviceWorker.ready;
          scope = reg.scope;
          await new Promise((r) => setTimeout(r, 400));
          controller = !!navigator.serviceWorker.controller;
          ready = true;
        } catch (e) { error = String(e && e.message || e); }
        let cacheKeys = [];
        try { cacheKeys = await caches.keys(); } catch (e) { cacheKeys = ['<err:' + e + '>']; }
        let regs = 0;
        try { regs = (await navigator.serviceWorker.getRegistrations()).length; } catch (e) { regs = -1; }
        return { ready, controller, scope, error, cacheKeys, regs };
      })()`,
    );

    // 断网
    await cdp.send('Network.emulateNetworkConditions', {
      offline: true,
      latency: 0,
      downloadThroughput: 0,
      uploadThroughput: 0,
    });

    // 离线重载（最多 2 次，第一次可能因 SW 尚未控制而失败）
    let dom = null;
    let reloadError = '';
    for (let attempt = 0; attempt < 2 && dom === null; attempt += 1) {
      try {
        const loaded = cdp.waitFor('Page.loadEventFired', PAGE_TIMEOUT);
        await cdp.send('Page.reload', { ignoreCache: false });
        await loaded;
        await new Promise((r) => setTimeout(r, 600));
        dom = await evalJs(
          cdp,
          `(() => {
            const el = document.querySelector('#root');
            const text = (el && el.innerText) || '';
            return { len: text.length, hasKeyword: text.includes('从系统相册导入'), head: text.slice(0, 60) };
          })()`,
          { awaitPromise: false },
        );
      } catch (e) {
        reloadError = String(e && e.message || e);
      }
    }

    // 恢复联网
    await cdp
      .send('Network.emulateNetworkConditions', {
        offline: false,
        latency: 0,
        downloadThroughput: -1,
        uploadThroughput: -1,
      })
      .catch(() => {});

    const pass = Boolean(dom && dom.len > 100 && dom.hasKeyword);
    return { task: 'offline', pass, swState, offlineDom: dom, reloadError };
  } finally {
    cdp.close();
    await closeTarget(targetId);
  }
}

async function taskNetwork() {
  // 新开一个干净 target（about:blank）→ Network.enable → 再导航
  const { cdp, targetId } = await connectPage('about:blank');
  try {
    const requests = [];
    cdp.on((msg) => {
      if (msg.method === 'Network.requestWillBeSent') {
        const req = msg.params.request;
        requests.push({ url: req.url, type: msg.params.type, method: req.method });
      }
    });

    await cdp.send('Network.enable');
    await navigateAndWait(cdp, URL_ARG);
    // load 后 2 秒
    await new Promise((r) => setTimeout(r, 2000));

    let clicked = false;
    if (CLICK_DEMO) {
      try {
        clicked = await evalJs(
          cdp,
          `(() => {
            const btn = [...document.querySelectorAll('button')].find((b) => (b.textContent || '').includes('演示路线'));
            if (btn) { btn.click(); return true; }
            return false;
          })()`,
          { awaitPromise: false },
        );
      } catch {
        clicked = false;
      }
      await new Promise((r) => setTimeout(r, 3000));
    }

    const urls = [...new Set(requests.map((r) => r.url))];
    // 只有 http(s) 才是"网络请求"；data: / blob: 是内联资源（如 1×1 兜底缩略图），不是请求。
    const isHttp = (u) => /^https?:\/\//i.test(u);
    const externalHttp = urls.filter((u) => isHttp(u) && !u.startsWith(BASE));
    const nonHttp = urls.filter((u) => !isHttp(u));
    const violations = externalHttp.filter((u) => !/amap\.com/i.test(u));
    const pass = BASE.includes('5175')
      ? externalHttp.length === 0 // trailshare：连高德都不碰
      : violations.length === 0; // track-studio：允许 amap

    return {
      task: 'network',
      pass,
      base: BASE,
      clickedDemo: clicked,
      counts: {
        total: urls.length,
        externalHttp: externalHttp.length,
        nonHttp: nonHttp.length,
        violations: violations.length,
      },
      all: urls,
      externalHttp,
      nonHttp,
      violations,
    };
  } finally {
    cdp.close();
    await closeTarget(targetId);
  }
}

/* ─────────────────────────── main ─────────────────────────── */

function parseArgs(argv) {
  const out = {};
  for (const item of argv) {
    const m = /^--([^=]+)(?:=(.*))?$/.exec(item);
    if (!m) continue;
    const key = m[1];
    const val = m[2];
    out[key] = val === undefined ? true : val;
  }
  return out;
}

function fail(message) {
  process.stderr.write(`[browser-verify] ${message}\n`);
  process.exit(2);
}

async function main() {
  // 确认浏览器在
  const version = await fetch(`${BROWSER}/json/version`).then((r) => r.json());
  process.stderr.write(`[browser-verify] ${version.Browser}\n`);

  let result;
  if (TASK === 'webm') result = await taskWebm();
  else if (TASK === 'offline') result = await taskOffline();
  else if (TASK === 'network') result = await taskNetwork();
  else fail(`未知 --task=${TASK}`);

  process.stdout.write(JSON.stringify(result, null, 2) + '\n');
  process.exit(result.pass ? 0 : 1);
}

main().catch((error) => {
  process.stderr.write(`[browser-verify] FATAL: ${error?.stack || error}\n`);
  process.stdout.write(JSON.stringify({ task: TASK, pass: false, error: String(error?.message || error) }) + '\n');
  process.exit(3);
});
