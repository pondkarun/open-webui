/* Pond AI service worker — minimal offline shell for the PWA.
 *
 * Strategy:
 *  - Precache the app shell (icons, manifest, logo).
 *  - Network-first for navigations and API calls (chat must be live);
 *    fall back to cached shell when offline.
 *  - Cache-first for hashed immutable assets (/ _app/immutable/).
 */
const VERSION = 'pond-ai-v1';
const SHELL_CACHE = `${VERSION}-shell`;
const ASSET_CACHE = `${VERSION}-assets`;

const SHELL_ASSETS = [
	'/',
	'/manifest.json',
	'/static/logo.png',
	'/static/favicon.png',
	'/static/favicon.svg',
	'/static/apple-touch-icon.png'
];

self.addEventListener('install', (event) => {
	event.waitUntil(
		caches
			.open(SHELL_CACHE)
			.then((cache) => cache.addAll(SHELL_ASSETS))
			.catch(() => undefined) // never block install on a missing asset
	);
	self.skipWaiting();
});

self.addEventListener('activate', (event) => {
	event.waitUntil(
		caches
			.keys()
			.then((keys) =>
				Promise.all(
					keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))
				)
			)
			.then(() => self.clients.claim())
	);
});

self.addEventListener('fetch', (event) => {
	const req = event.request;
	if (req.method !== 'GET') return;

	const url = new URL(req.url);
	if (url.origin !== self.location.origin) return;
	// Never cache API traffic, websockets, or streams.
	if (
		url.pathname.startsWith('/api') ||
		url.pathname.startsWith('/ws') ||
		url.pathname.startsWith('/v1') ||
		url.pathname.startsWith('/oauth') ||
		url.pathname.startsWith('/auth')
	) {
		return;
	}

	// Hashed, immutable build assets: cache-first.
	if (url.pathname.includes('/_app/immutable/')) {
		event.respondWith(
			caches.match(req).then(
				(cached) =>
					cached ||
					fetch(req).then((res) => {
						const copy = res.clone();
						caches.open(ASSET_CACHE).then((cache) => cache.put(req, copy));
						return res;
					})
			)
		);
		return;
	}

	// Navigations: network-first, offline falls back to cached shell.
	if (req.mode === 'navigate') {
		event.respondWith(
			fetch(req)
				.then((res) => {
					const copy = res.clone();
					caches.open(SHELL_CACHE).then((cache) => cache.put('/', copy));
					return res;
				})
				.catch(() => caches.match('/'))
		);
		return;
	}

	// Other same-origin GETs (icons, fonts, manifest): stale-while-revalidate.
	event.respondWith(
		caches.match(req).then((cached) => {
			const network = fetch(req)
				.then((res) => {
					const copy = res.clone();
					caches.open(ASSET_CACHE).then((cache) => cache.put(req, copy));
					return res;
				})
				.catch(() => cached);
			return cached || network;
		})
	);
});
