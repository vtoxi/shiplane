// "Needs X": when something a page depends on is not installed on the server, say so and offer a one-click install (the same installer as the Apps page).
import { h, fill, Alert, Button, Console, busy } from './kit.js';
import { api } from './api.js';
import { attempt, watchJob } from './util.js';

let cache = null;
const FRESH = 60000;

async function server(force) {
  if (!force && cache && Date.now() - cache.at < FRESH) return cache.data;
  const data = await api('/api/apps');
  cache = { at: Date.now(), data };
  return data;
}

const present = (info) => !!info && (Object.keys(info.installed || {}).length > 0 || !!info.system);

/** The element stays empty while the app is installed (or while we cannot tell). onDone() runs after a successful install. */
export function NeedsBar(appId, why, onDone) {
  const el = h('div', {});
  async function check(force) {
    let data;
    try { data = await server(force); } catch (e) { fill(el, null); return; }
    const app = data.catalog.find((a) => a.id === appId);
    if (!app || present(data.status.apps[appId])) { fill(el, null); return; }
    const log = h('div', { class: 'mt-3' });
    const install = Button({ label: `Install ${app.name}`, icon: 'download', variant: 'primary', size: 'sm', onClick: () => busy(install, async () => {
      const r = await attempt(api('/api/apps/install', { id: appId, version: app.default }));
      if (!r) return;
      const con = Console({ empty: 'Starting...' });
      fill(log, con.el);
      const done = await watchJob(r.job, con);
      if (done.ok) { cache = null; await check(true); if (onDone) onDone(); }
    }) });
    fill(el, Alert({ tone: 'warn', title: `${app.name} is not installed on the server` }, why, h('div', { class: 'mt-3' }, install), log));
  }
  check(false);
  return el;
}
