// Helpers that glue the API to the UI feedback (toasts, jobs).
import { api } from './api.js';
import { toast } from './kit.js';

/** Await an API promise; show a success toast or an error toast. Returns the result, or null on failure. */
export async function attempt(promise, success) {
  try {
    const r = await promise;
    if (success) toast(typeof success === 'function' ? success(r) : success, { tone: 'ok' });
    return r;
  } catch (e) {
    toast(e.message, { tone: 'bad', title: 'Something went wrong', timeout: 10000 });
    return null;
  }
}

/** Poll a background job and stream its lines into a console. Resolves {ok}. */
export async function watchJob(id, con) {
  let since = 0;
  for (;;) {
    const r = await api(`/api/job?id=${id}&since=${since}`);
    since = r.next;
    if (r.lines.length) con.push(r.lines);
    if (r.done) return { ok: r.ok };
    await new Promise((res) => setTimeout(res, 600));
  }
}

/** mode: 'system' | 'light' | 'dark'. Saved in localStorage; 'system' follows the OS. */
export function setTheme(mode) {
  try { if (mode === 'system') localStorage.removeItem('cr-theme'); else localStorage.setItem('cr-theme', mode); } catch (e) { /* private mode */ }
  document.documentElement.classList.toggle('dark', mode === 'dark' || (mode === 'system' && matchMedia('(prefers-color-scheme: dark)').matches));
}

export function getTheme() {
  try { return localStorage.getItem('cr-theme') || 'system'; } catch (e) { return 'system'; }
}

export const hostsOf =(s) => [s.hostname].concat(s.redirects || []).filter((h) => h && h.indexOf('*') < 0);
