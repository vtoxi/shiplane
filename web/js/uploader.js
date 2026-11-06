// Resumable upload of a (possibly multi-GB) backup file to the server, in pieces. A dropped connection costs one piece; a reload can continue.
const KEY = (f) => `cr-upload:${f.name}:${f.size}:${f.lastModified}`;
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked: no resume after a reload, everything else works */ } },
  del: (k) => { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
};

async function json(url, body) {
  const res = await fetch(url, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'coderunner' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({ error: 'Unexpected response from the app' }));
  if (!res.ok) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status, data });
  return data;
}

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  const t = setTimeout(resolve, ms);
  signal && signal.addEventListener('abort', () => { clearTimeout(t); reject(new DOMException('Paused', 'AbortError')); }, { once: true });
});

/**
 * Upload `file`. onProgress({ sent, total, speed, eta, resumed }). Pass an AbortSignal to pause (the upload stays on the server and can be resumed).
 * Resolves with { path, name, size } when the server has checked the finished file. Call cancelUpload(file) to throw the partial file away.
 */
export async function uploadBackup(file, { onProgress = () => {}, signal } = {}) {
  let id = store.get(KEY(file)), offset = 0, chunk = 8 * 1024 * 1024, resumed = false;
  if (id) {
    try { const st = await json(`/api/migrate/upload/status?id=${encodeURIComponent(id)}`); if (st.size === file.size && st.name === file.name) { offset = st.offset; resumed = offset > 0; } else id = null; } catch (e) { id = null; }
  }
  if (!id) {
    const st = await json('/api/migrate/upload/start', { name: file.name, size: file.size });
    id = st.id; chunk = st.chunk || chunk; offset = 0;
    store.set(KEY(file), id);
  }
  const t0 = Date.now(), base = offset;
  let speed = 0;
  while (offset < file.size) {
    const end = Math.min(file.size, offset + chunk);
    const piece = file.slice(offset, end);
    let attempt = 0, t1 = Date.now();
    for (;;) {
      try {
        const res = await fetch(`/api/migrate/upload/chunk?id=${encodeURIComponent(id)}&offset=${offset}`, { method: 'POST', headers: { 'X-Requested-With': 'coderunner', 'Content-Type': 'application/octet-stream' }, body: piece, signal });
        const data = await res.json().catch(() => ({}));
        if (res.status === 409 && typeof data.offset === 'number') { offset = data.offset; break; }      // the server already has more: continue from where it says
        if (res.ok) { offset = data.offset; break; }
        if (res.status < 500 && res.status !== 408 && res.status !== 429) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { fatal: true });
        throw new Error(data.error || `HTTP ${res.status}`);
      } catch (e) {
        if (e.name === 'AbortError' || e.fatal || ++attempt > 6) throw e;
        await sleep(Math.min(30000, 1000 * 2 ** attempt), signal);                                      // network hiccup: wait, then retry this piece
        try { offset = (await json(`/api/migrate/upload/status?id=${encodeURIComponent(id)}`)).offset; break; } catch (e2) { /* still down: loop retries the same piece */ }
      }
    }
    const dt = (Date.now() - t1) / 1000;
    if (dt > 0) speed = speed ? speed * 0.7 + (piece.size / dt) * 0.3 : piece.size / dt;
    onProgress({ sent: offset, total: file.size, speed, eta: speed ? (file.size - offset) / speed : null, resumed, elapsed: (Date.now() - t0) / 1000, since: offset - base });
  }
  const done = await json('/api/migrate/upload/complete', { id });
  store.del(KEY(file));
  return done;
}

export async function cancelUpload(file) {
  const id = store.get(KEY(file));
  store.del(KEY(file));
  if (id) { try { await json('/api/migrate/upload/abort', { id }); } catch (e) { /* already gone */ } }
}

export const listUploads = () => json('/api/migrate/upload/list');
export const deleteUpload = (file) => json('/api/migrate/upload/delete', { file });
