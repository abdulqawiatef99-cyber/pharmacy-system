// Service worker: caches the app shell only. API responses are never cached
// (financial/stock data must always be live; offline writes are a later phase).
const CACHE = "pharm-shell-v39";
const SHELL = ["/", "/static/app.css", "/static/app.js", "/static/manifest.json", "/static/icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  // network first, fall back to cached shell
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone();
    if (r.ok && SHELL.includes(url.pathname)) caches.open(CACHE).then((c) => c.put(e.request, copy));
    return r;
  }).catch(() => caches.match(e.request).then((r) => r || caches.match("/"))));
});
