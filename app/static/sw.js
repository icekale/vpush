/* V Push Service Worker —— network-first：静态外壳离线可用，API 永不缓存 */
const CACHE = "dav-shell-07fa69461387";
const SHELL = [
  "/",
  "/app.adf650e6d791.js",
  // asset-modules:start
  "/core/dialog.9c1fa70ae93e.js",
  "/core/html.817ab2339b89.js",
  "/core/icons.7fb4f43f1230.js",
  "/core/lightbox.63db409ddbd8.js",
  "/core/platforms.e3f9b30aa971.js",
  "/views/admin/cicc.0cd7529b8514.js",
  "/views/admin/codes.2b9b97a7ba6e.js",
  "/views/admin/dashboard.7486ddf94a35.js",
  "/views/admin/ima-collector.f5df4416f5ed.js",
  "/views/admin/infra.e13cdf4ab5b3.js",
  "/views/admin/knowledge.42883ccab396.js",
  "/views/admin/kol.9fb9705ffce4.js",
  "/views/admin/news.7de3464911e1.js",
  "/views/admin/users.171b6837216e.js",
  "/views/feishu-personal.d34f03bb7da2.js",
  "/views/ima.4ee73e107acf.js",
  "/views/market.0a97a470169d.js",
  "/views/news.f928e325cb18.js",
  "/views/post-card-export.27dbd2f5c5fa.js",
  "/views/push-settings.c6dcb9dee07b.js",
  // asset-modules:end
  "/style.cb17e6b4b128.css",
  "/vendor/design-tokens.965639a4e5c3.css",
  "/logo-mark.svg",
  "/icon-192.png",
  "/icon-512.png",
  "/icon-192-dark.png",
  "/icon-512-dark.png",
  "/manifest.webmanifest",
  "/manifest-dark.webmanifest",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  let url;
  try {
    url = new URL(e.request.url);
  } catch {
    return;
  }
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return; // 动态数据永不缓存
  if (e.request.mode === "navigate") {
    e.respondWith(networkFirstNavigate(e.request));
    return;
  }
  e.respondWith(networkFirst(e.request));
});

self.addEventListener("push", (e) => {
  let data = {};
  try {
    data = e.data ? e.data.json() : {};
  } catch {
    data = { body: e.data ? e.data.text() : "" };
  }
  e.waitUntil(self.registration.showNotification(data.title || "VPush", {
    body: data.body || "",
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    data: { url: data.url || "/" },
    tag: data.tag || "vpush",
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      if ("focus" in client) {
        await client.focus();
        if ("navigate" in client && url) await client.navigate(url);
        return;
      }
    }
    await self.clients.openWindow(url);
  })());
});

async function networkFirstNavigate(req) {
  try {
    const fresh = await fetch(req, { cache: "reload" });
    if (fresh && fresh.ok) return fresh;
  } catch {
    /* 离线 */
  }
  return (await caches.match("/")) || Response.error();
}

async function networkFirst(req) {
  let fresh;
  try {
    // cache:"reload" 绕过 HTTP 缓存：CF 会给裸 URL 模块强加 4h 浏览器缓存，
    // 不绕过的话「网络优先」会被浏览器缓存短路，发版后模块最长滞后 4 小时
    fresh = await fetch(req, { cache: "reload" });
  } catch {
    const cached = await caches.match(req, { ignoreSearch: true });
    return cached || Response.error();
  }
  if (fresh && fresh.ok && fresh.type === "basic") {
    // 后台写缓存：Cache.put 对 206（大文件 Range）等响应会抛错，
    // 绝不能影响已经拿到的网络响应
    let cacheKey;
    try {
      // 用裸路径作缓存键，避免 ?v= 版本号 query 撑爆缓存
      cacheKey = new Request(new URL(req.url).pathname);
    } catch {
      return fresh;
    }
    caches.open(CACHE)
      .then((cache) => cache.put(cacheKey, fresh.clone()))
      .catch(() => {});
  }
  return fresh;
}
