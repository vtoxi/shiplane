// Tiny fetch wrapper for the local JSON API.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

// Thin bar across the top while a request is in flight (background polling stays silent).
const QUIET = /^\/api\/(metrics|health|status|job|fs\/list|migrate\/(active|run))/;
let inflight = 0, shown = 0, bar = null;
function busyBar(delta) {
  inflight += delta;
  if (!bar) { bar = document.createElement('div'); bar.className = 'topbar'; bar.setAttribute('aria-hidden', 'true'); document.body.append(bar); }
  clearTimeout(shown);
  if (inflight > 0) shown = setTimeout(() => bar.classList.add('on'), 150);   // fast calls never flash it
  else bar.classList.remove('on');
}

export async function api(path, body) {
  const loud = !QUIET.test(path);
  if (loud) busyBar(1);
  try { return await call(path, body); } finally { if (loud) busyBar(-1); }
}

async function call(path, body) {
  const opts = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'coderunner' },
    body: JSON.stringify(body),
  };
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new ApiError('Cannot reach the app. If you run it yourself, start it again and reopen the link.', 0);
  }
  const data = await res.json().catch(() => ({ error: 'Unexpected response from the app' }));
  if (res.status === 401 && data.auth === 'login') window.dispatchEvent(new Event('auth-required')); // session ended: back to sign-in
  if (!res.ok || data.error) throw new ApiError(data.error || `HTTP ${res.status}`, res.status);
  return data;
}
