/* 最小 Service Worker：只缓存应用壳，**不缓存任何用户数据**（§8.4）。
 *
 * 为什么"不缓存用户数据"要写成代码注释而不是文档：用户数据在这个应用里
 * 压根不落盘（照片只在内存），如果将来有人图省事把 `/api` 或 POST 结果塞进
 * cache，这个应用的"关页面即清空"语义就被悄悄破坏了。这里显式拒绝：
 * 非 GET、非本源的请求一律不碰。
 *
 * 策略：
 *  - `request.mode === 'navigate'`（打开页面）→ network-first，失败回退缓存的 index.html，
 *    这样联网时永远拿到最新版本，断网时也能进应用；
 *  - 同源静态资源（js/css/图片）→ cache-first + 后台更新（stale-while-revalidate）。
 */

const CACHE = 'tm-track-studio-v1';

/** 应用壳。只放构建产物里的入口，不枚举 assets（文件名带 hash，交给运行时填充）。 */
const SHELL = ['/', '/index.html'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => {
        /* 首次安装拿不到壳（离线首装）时不阻塞，由 fetch 策略兜底 */
      }),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  // 只处理 GET；POST/PUT 等一律放行（本应用本来就没有，防将来误加）
  if (request.method !== 'GET') return;

  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  // 跨源（高德瓦片、JS API）完全不接管：交给浏览器与 CSP
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          // 导航成功时顺手更新壳缓存（keep 一份最新 index.html）
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put('/index.html', copy)).catch(() => {});
          return response;
        })
        .catch(() =>
          caches
            .match('/index.html')
            .then((cached) => cached || Response.error()),
        ),
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
          }
          return response;
        })
        .catch(() => cached || Response.error());
      return cached || network;
    }),
  );
});
