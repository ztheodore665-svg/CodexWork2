/* 最小 Service Worker：只缓存应用壳，**不缓存任何用户数据**（§8.4）。
 *
 * 这个应用的"零网络请求"承诺还有一层含义：SW 绝不能成为"偷偷发请求"的通道。
 * 所以这里只处理**同源 GET**，跨源与非 GET 一律放行给浏览器（本应用本来也没有）。
 * 照片、`.trailshare`、`journey.json` 全部只存在于内存与用户的下载目录，不进 Cache Storage。
 */

const CACHE = 'tm-trailshare-v1';

/** 应用壳。assets 文件名带 hash，交给运行时 cache-first 填充。 */
const SHELL = ['/', '/index.html'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => {
        /* 离线首装拿不到壳时不阻塞，交给 fetch 策略兜底 */
      }),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  // 跨源一律不接管（本应用的 CSP 也不允许跨源请求）
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put('/index.html', copy)).catch(() => {});
          return response;
        })
        .catch(() => caches.match('/index.html').then((cached) => cached || Response.error())),
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
