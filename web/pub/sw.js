// Makes the app installable. It deliberately caches nothing: API answers and pages are always fetched fresh (they are private and change constantly).
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function () { /* network only */ });
