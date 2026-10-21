// Pantry Chef service worker: caches static assets only.
// Pages and API responses always go to the network (no offline data).
//
// Strategy for /static/: serve from cache if present, and refresh the cached
// copy in the background ("stale-while-revalidate"), so CSS/JS updates show
// up on the next load. Bump CACHE when the precache list changes.

const CACHE = "pantry-chef-static-v1";
const PRECACHE = [
  "/static/style.css",
  "/static/htmx.min.js",
  "/static/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  // Delete caches from older versions of this worker.
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const isStatic =
    event.request.method === "GET" &&
    url.origin === self.location.origin &&
    url.pathname.startsWith("/static/");
  if (!isStatic) return; // let the browser handle it normally

  event.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(event.request);
      const network = fetch(event.request)
        .then((response) => {
          if (response.ok) cache.put(event.request, response.clone());
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
