// App state: the session (who/where, feature flags) and the latest server status. Pages subscribe to changes.
import { api } from './api.js';

export const store = { session: null, status: null, error: null, loading: false, updated: null };
const listeners = new Set();

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
const emit = () => listeners.forEach((fn) => fn(store));

export async function loadSession() {
  store.session = await api('/api/session');
  emit();
}

export async function refresh({ quiet = false } = {}) {
  if (store.loading) return;
  store.loading = true;
  if (!quiet) emit();
  try {
    store.status = await api('/api/status');
    store.error = null;
    store.updated = Date.now();
  } catch (e) {
    store.error = e.message;
  } finally {
    store.loading = false;
    emit();
  }
}

export function startPolling(ms = 20000) {
  setInterval(() => { if (!document.hidden) refresh({ quiet: true }); }, ms);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && Date.now() - (store.updated || 0) > ms) refresh({ quiet: true }); });   // a hidden tab asks for nothing
}

export const siteByName = (name) => (store.status ? store.status.sites.find((s) => s.name === name) : null);
export const usedPorts = () => (store.status ? store.status.sites.map((s) => s.port).filter(Boolean) : []);
