// Floating deploy panel: follows deploys from any page, survives a refresh (the job ids are remembered in this browser; the server keeps the logs),
// can be minimized, and links to the site's own page. Mounted once by main.js; pages call trackDeploy().
import { h, fill, Badge, Button, Console, icon, toast } from './kit.js';
import { DeploySteps } from './live.js';
import { api } from './api.js';
import { refresh } from './store.js';
import { navigate } from './router.js';

const KEY = 'shiplane.deploys';      // [{ job, site, note }]
const MIN = 'shiplane.dock.min';
const MAX_RUNS = 3;
const runs = new Map();              // job -> { site, note, onDone, state, parts }
let panel = null, list = null, head = null, minimized = false;

const read = () => { try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) { return []; } };
const write = (a) => { try { localStorage.setItem(KEY, JSON.stringify(a.slice(-MAX_RUNS))); } catch (e) { /* storage blocked: the panel just does not survive a refresh */ } };

function ensurePanel() {
  if (panel) return;
  try { minimized = localStorage.getItem(MIN) === '1'; } catch (e) { /* default: open */ }
  head = h('div', { style: 'display:flex;align-items:center;gap:8px;padding:10px 12px;cursor:default' });
  list = h('div', { style: 'max-height:min(70vh,34rem);overflow:auto;padding:0 12px 12px' });
  panel = h('section', { class: 'card', role: 'region', 'aria-label': 'Deployments', style: 'position:fixed;right:16px;bottom:16px;z-index:60;width:min(26rem,calc(100vw - 32px));box-shadow:0 12px 40px rgba(0,0,0,.25)' }, head, list);
  document.body.append(panel);
  paintHead();
}

function paintHead() {
  const all = [...runs.values()];
  const active = all.filter((r) => !r.state.done), bad = all.filter((r) => r.state.ok === false);
  const label = active.length ? `Deploying ${active.map((r) => r.site).join(', ')}` : bad.length ? `Deploy failed: ${bad[0].site}` : all.length ? (all[all.length - 1].state.ok ? `Deployed ${all[all.length - 1].site}` : `Finished: ${all[all.length - 1].site}`) : 'Deployments';
  const lead = active.length ? icon('loader', 'h-4 w-4 animate-spin text-brand') : bad.length ? icon('x', 'h-4 w-4 text-bad') : icon('check', 'h-4 w-4 text-ok');
  head.replaceChildren(lead, h('strong', { class: 'min-w-0 flex-1 truncate text-sm', style: 'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' }, label),
    Button({ icon: minimized ? 'up' : 'minus', iconOnly: true, variant: 'ghost', size: 'sm', title: minimized ? 'Show' : 'Minimize', onClick: () => setMin(!minimized) }));
  list.style.display = minimized ? 'none' : '';
}

function setMin(v) {
  minimized = v;
  try { localStorage.setItem(MIN, v ? '1' : '0'); } catch (e) { /* ignore */ }
  paintHead();
}

function forget(job) {
  runs.delete(job);
  write(read().filter((r) => r.job !== job));
  const r = list && list.querySelector(`[data-job="${job}"]`);
  if (r) r.remove();
  if (!runs.size && panel) { panel.remove(); panel = list = head = null; } else if (panel) paintHead();
}

function addCard(job, site) {
  const steps = DeploySteps(), con = Console({ empty: 'Starting...' }), badge = h('span', {}, Badge('Deploying...', 'info', { dot: true }));
  con.el.style.maxHeight = '11rem';
  const actions = h('div', { style: 'display:flex;gap:8px;flex-wrap:wrap;margin-top:12px' });
  const note = h('p', { class: 'mt-3 text-xs text-muted' });
  const el = h('div', { 'data-job': job, style: 'padding-top:4px;border-top:1px solid rgb(var(--c-line))' },
    h('div', { style: 'display:flex;align-items:center;justify-content:space-between;gap:8px;margin:8px 0' }, h('strong', { class: 'text-sm' }, site), badge),
    h('div', { style: 'margin-bottom:12px' }, steps.el), con.el, note, actions);
  list.append(el);
  return { steps, con, badge, actions, note };
}

async function follow(job, run) {
  const { site, parts } = run;
  let since = 0;
  for (;;) {
    let r;
    try { r = await api(`/api/job?id=${job}&since=${since}`); } catch (e) {
      // the server no longer has this job (it was restarted or the list was pruned): all that is left is its site page
      run.state = { done: true, ok: null };
      parts.badge.replaceChildren(Badge('Finished', 'neutral'));
      parts.con.set('The log is no longer on the server. Open the site page to see its current state.');
      break;
    }
    since = r.next;
    if (r.lines.length) { parts.con.push(r.lines); parts.steps.feed(r.lines); }
    if (r.done) {
      run.state = { done: true, ok: r.ok };
      parts.steps.finish(r.ok);
      parts.badge.replaceChildren(r.ok ? Badge('Deployed', 'ok', { dot: true }) : Badge('Failed', 'bad', { dot: true }));
      break;
    }
    await new Promise((ok) => setTimeout(ok, 1000));
  }
  if (run.state.ok != null) {
    refresh({ quiet: true });
    toast(run.state.ok ? `${site} deployed` : `${site}: the deploy did not finish, open the panel for the log`, { tone: run.state.ok ? 'ok' : 'bad', timeout: 8000 });
    if (run.state.ok && run.note) parts.note.textContent = run.note;
    if (run.onDone) run.onDone(run.state.ok);
  }
  fill(parts.actions,
    Button({ label: 'Site page', icon: 'external', size: 'sm', variant: 'primary', onClick: () => { setMin(true); navigate(`/sites/${site}`); } }),
    run.state.ok ? Button({ label: 'Open site', size: 'sm', href: `https://${run.host}`, newTab: true }) : null,
    Button({ label: 'Dismiss', size: 'sm', variant: 'ghost', onClick: () => forget(job) }));
  paintHead();
}

function start(job, site, extra = {}, open = true) {
  if (runs.has(job)) return;
  ensurePanel();
  const run = Object.assign({ site, state: { done: false, ok: null } }, extra, { parts: addCard(job, site) });
  runs.set(job, run);
  if (open && minimized) setMin(false);           // a new deploy opens the panel; a restored one keeps the size you left it at
  paintHead();
  follow(job, run);
}

/** Follow a deploy job in the floating panel. `host` adds an "Open site" button, `note` is shown after a good deploy, `onDone(ok)` runs at the end. */
export function trackDeploy(job, site, { host = '', note = '', onDone } = {}) {
  write(read().filter((r) => r.job !== job).concat([{ job, site, host, note }]));
  start(job, site, { host, note, onDone });
}

/** Call once at start-up: brings back the deploys that were running or unfinished before a refresh. */
export function mountDeployDock() {
  for (const r of read()) start(r.job, r.site, { host: r.host || '', note: r.note || '' }, false);
}
