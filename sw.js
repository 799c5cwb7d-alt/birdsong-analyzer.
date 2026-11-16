/* Birdsong Analyzer — fonctionnement hors connexion.
   La page (chiffrée) et ses bibliothèques sont gardées sur l'appareil ; avec du réseau, la dernière version est reprise. */
const CACHE = 'birdsong-3868b247198c';
const SHELL = ['./', './manifest.webmanifest', './icon-180.png', './icon-192.png', './icon-512.png'];
const LIBS = ['https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js', 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js'];
const KEEP = /^https:\/\/(cdnjs\.cloudflare\.com|fonts\.googleapis\.com|fonts\.gstatic\.com)\//;

self.addEventListener('install', e => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    await c.addAll(SHELL);
    await Promise.all(LIBS.map(u => fetch(u, { mode: 'no-cors' }).then(r => c.put(u, r)).catch(() => {})));
    self.skipWaiting();
  })());
});
self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k.startsWith('birdsong-') && k !== CACHE).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin === self.location.origin) {                 // la page : réseau d'abord (3,5 s au plus), sinon la copie gardée
    e.respondWith((async () => {
      const c = await caches.open(CACHE), key = req.mode === 'navigate' ? './' : req;
      const net = fetch(req).then(r => { if (r && r.ok) c.put(key, r.clone()); return r; });
      const cached = await c.match(key, { ignoreSearch: true });
      if (!cached) return net;
      const slow = new Promise(res => setTimeout(() => res(null), 3500));
      const r = await Promise.race([net.catch(() => null), slow]);
      return r && r.ok ? r : cached;
    })());
  } else if (KEEP.test(req.url)) {                           // bibliothèques et polices : copie gardée d'abord
    e.respondWith((async () => {
      const c = await caches.open(CACHE), cached = await c.match(req.url);
      if (cached) return cached;
      const r = await fetch(req);
      if (r && (r.ok || r.type === 'opaque')) c.put(req.url, r.clone());
      return r;
    })());
  }
});
