/* Planner PWA — service worker.
 *
 * Задачи:
 *  1. Отдавать последние загруженные страницы, когда сети нет.
 *  2. Держать статику (CSS/JS/шрифты) локально, чтобы интерфейс не зависел от сети.
 *  3. Отдавать офлайн-заглушку вместо ошибки браузера.
 *
 * POST-запросы НЕ перехватываются: офлайн-очередь действий живёт в /pwa/offline.js.
 * Подробности и обоснования: pwa/ARCHITECTURE.md
 */

const VERSION = 'planner-pwa-v2';

const STATIC_CACHE = `${VERSION}-static`;
const PAGE_CACHE = `${VERSION}-pages`;
const DATA_CACHE = `${VERSION}-data`;

const OFFLINE_URL = '/pwa/offline.html';

// Кэшируется при установке. Один недоступный файл не должен ломать установку,
// поэтому каждый адрес скачивается отдельно и ошибки гасятся.
const PRECACHE_URLS = [
    OFFLINE_URL,
    '/pwa/offline.js',
    '/web/static/manifest.json',
    '/web/static/css/tailwind.css',
    '/web/static/css/style.css',
    '/web/static/js/htmx.min.js',
    '/web/static/js/alpine.min.js',
    '/web/static/js/date-mask.js',
];

// Страницы и пути, которые не кэшируем никогда.
const NEVER_CACHE_PATHS = ['/login', '/logout'];

/**
 * Записать ответ в кэш, вычистив запрещающие заголовки.
 *
 * Сервер отдаёт HTML и API с `Cache-Control: no-cache, no-store`. Cache API
 * отклоняет запись такого ответа, поэтому тело пересобирается в новый Response,
 * от заголовков остаётся только content-type.
 */
async function cachePutClean(cache, request, response) {
    const headers = new Headers();
    const contentType = response.headers.get('content-type');
    if (contentType) headers.set('content-type', contentType);
    const body = await response.blob();
    await cache.put(request, new Response(body, {
        status: response.status,
        statusText: response.statusText,
        headers,
    }));
}

/**
 * Ответ годен для кэша: успешный, не редирект и не страница входа.
 *
 * Редирект отсекается намеренно: при истёкшей куке запрос `/` отдаёт 200 с
 * HTML страницы входа, и он не должен попасть в кэш под ключом главной.
 */
function isCacheable(response) {
    return Boolean(response) && response.ok && response.status === 200 && !response.redirected;
}

function offlineFallback(message) {
    return new Response(message, {
        status: 503,
        headers: { 'content-type': 'text/plain; charset=utf-8' },
    });
}

/**
 * Заглушка для HTMX-фрагмента.
 *
 * Отдавать сюда полный HTML-документ нельзя: htmx вставит его внутрь блока
 * страницы. Статус 200 обязателен — на не-2xx htmx по умолчанию ничего не
 * подменяет, и Вера увидела бы просто «ничего не произошло».
 */
function offlineFragment() {
    return new Response(
        '<div class="offline-fragment">Нет сети. Эти данные не сохранены на устройстве.</div>',
        { status: 200, headers: { 'content-type': 'text/html; charset=utf-8' } }
    );
}

async function networkFirst(event, request, cacheName, fallback, useFragment) {
    try {
        const response = await fetch(request);
        if (isCacheable(response)) {
            const cache = await caches.open(cacheName);
            await cachePutClean(cache, request, response.clone());
        }
        return response;
    } catch (error) {
        const cached = await caches.match(request, { cacheName });
        if (cached) return cached;
        if (useFragment) return offlineFragment();
        if (fallback) {
            const fallbackResponse = await caches.match(fallback);
            if (fallbackResponse) return fallbackResponse;
        }
        return offlineFallback('Нет сети');
    }
}

async function staleWhileRevalidate(event, request) {
    const cache = await caches.open(STATIC_CACHE);
    const cached = await cache.match(request);

    const network = fetch(request)
        .then(async (response) => {
            if (isCacheable(response)) await cachePutClean(cache, request, response.clone());
            return response;
        })
        .catch(() => null);

    if (cached) {
        event.waitUntil(network);
        return cached;
    }

    const response = await network;
    if (response) return response;
    return offlineFallback('');
}

self.addEventListener('install', (event) => {
    event.waitUntil((async () => {
        const cache = await caches.open(STATIC_CACHE);
        await Promise.all(PRECACHE_URLS.map(async (url) => {
            try {
                const response = await fetch(url, { cache: 'reload' });
                if (isCacheable(response)) await cachePutClean(cache, url, response);
            } catch (error) {
                // Файл может отсутствовать — установка продолжается.
            }
        }));
    })());
});

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        const keys = await caches.keys();
        const keep = new Set([STATIC_CACHE, PAGE_CACHE, DATA_CACHE]);
        await Promise.all(keys.map((key) => (keep.has(key) ? null : caches.delete(key))));
        await self.clients.claim();
    })());
});

self.addEventListener('message', (event) => {
    const data = event.data || {};
    if (data.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('fetch', (event) => {
    const request = event.request;

    // Офлайн-очередь для POST/PUT/DELETE — в /pwa/offline.js.
    if (request.method !== 'GET') return;

    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return;
    if (NEVER_CACHE_PATHS.includes(url.pathname)) return;
    // Токен в адресной строке не кэшируем.
    if (url.searchParams.has('token')) return;

    if (request.mode === 'navigate') {
        // Здесь заглушка — целая страница: её открывает сам браузер.
        event.respondWith(networkFirst(event, request, PAGE_CACHE, OFFLINE_URL, false));
        return;
    }

    if (url.pathname.startsWith('/api/')) {
        event.respondWith(networkFirst(event, request, DATA_CACHE, null, false));
        return;
    }

    if (
        url.pathname.startsWith('/web/static/')
        || url.pathname.startsWith('/pwa/')
        || url.pathname.startsWith('/uploads/')
    ) {
        event.respondWith(staleWhileRevalidate(event, request));
        return;
    }

    // Прочие GET — это HTMX-фрагменты (/tasks/list, /managers/widget и т.п.):
    // заглушкой для них служит короткая вставка, а не целая страница.
    event.respondWith(networkFirst(event, request, PAGE_CACHE, null, true));
});
