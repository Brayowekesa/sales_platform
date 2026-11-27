// Service worker: lets the shop be installed as an app and shows a friendly page when offline.
// Pages always come from the network (prices, stock and carts must be fresh); only the
// offline page, the logo and resized product photos are kept on the phone.
const VERSION = 'esa-v2';
const IMG_CACHE = VERSION + '-img';
const MAX_IMAGES = 150;          // keep at most this many photos on the phone
const OFFLINE_URL = '/offline';
const PRECACHE = [OFFLINE_URL, '/static/images/icon-192.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION && k !== IMG_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Oldest photos first (cache.keys() keeps insertion order): drop them beyond MAX_IMAGES
function trimCache(cache) {
  return cache.keys().then((keys) =>
    Promise.all(keys.slice(0, Math.max(0, keys.length - MAX_IMAGES)).map((k) => cache.delete(k))));
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;           // CDN files: browser handles them

  if (PRECACHE.includes(url.pathname) && url.pathname !== OFFLINE_URL) {   // the offline page's logo
    event.respondWith(fetch(req).catch(() => caches.match(url.pathname)));
    return;
  }
  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  // Resized photos have the file's version in the URL, so a kept copy never goes stale
  if (url.pathname.startsWith('/img/')) {
    event.respondWith(
      caches.open(IMG_CACHE).then((cache) =>
        cache.match(req).then((hit) => hit || fetch(req).then((res) => {
          if (res.ok) event.waitUntil(cache.put(req, res.clone()).then(() => trimCache(cache)));
          return res;
        }))
      )
    );
  }
});
