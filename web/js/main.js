// App shell: sidebar navigation, routing, theme toggle, polling.
import { h, icon, toast, Alert, Meter } from './kit.js';
import { api } from './api.js';
import { route, start } from './router.js';
import { store, subscribe, loadSession, refresh, startPolling } from './store.js';
import { setTheme } from './util.js';
import { mountDeployDock } from './deploydock.js';
import { SitesPage } from './pages/sites.js';
import { SitePage } from './pages/site.js';
import { DeployPage } from './pages/deploy.js';
import { DatabasesPage } from './pages/databases.js';
import { DomainsPage } from './pages/domains.js';
import { DnsPage } from './pages/dns.js';
import { ServerPage } from './pages/server.js';
import { ActivityPage } from './pages/activity.js';
import { SettingsPage, TwoFactorGate } from './pages/settings.js';
import { ManualPage } from './pages/manual.js';
import { AuthPage } from './pages/auth.js';
import { UsersPage } from './pages/users.js';
import { MaintenancePage } from './pages/maintenance.js';
import { ProtectPage } from './pages/protect.js';
import { FleetPage } from './pages/fleet.js';
import { MigratePage } from './pages/migrate.js';
import { AppsPage } from './pages/apps.js';
import { CarePage } from './pages/care.js';
import { MetricsPage } from './pages/metrics.js';
import { MigrationRunPage } from './pages/migration-run.js';

const RANK = { viewer: 1, admin: 2, super_admin: 3 };
const can = (role) => RANK[store.session.me.role] >= RANK[role];
const NAV = [
  { group: 'Sites', items: [
    { path: '/sites', label: 'Sites', icon: 'sites' }, { path: '/deploy', label: 'Deploy', icon: 'rocket', show: () => can('admin') },
    { path: '/databases', label: 'Databases', icon: 'database', show: () => can('admin') },
    { path: '/apps', label: 'Apps', icon: 'box', show: () => can('admin') }] },
  { group: 'Server', items: [
    { path: '/server', label: 'Server', icon: 'server', show: () => can('admin') },
    { path: '/fleet', label: 'All servers', icon: 'cloud', show: () => can('super_admin') },
    { path: '/metrics', label: 'Metrics', icon: 'activity', show: () => can('admin') },
    { path: '/care', label: 'Server care', icon: 'shield', show: () => can('admin') },
    { path: '/maintenance', label: 'Maintenance', icon: 'clock', show: () => can('admin') },
    { path: '/migrate', label: 'Migrate', icon: 'upload', show: () => can('admin') }] },
  { group: 'Network', items: [
    { path: '/domains', label: 'Domains', icon: 'cloud', show: () => can('admin') },
    { path: '/dns', label: 'DNS hosting', icon: 'globe', show: () => can('super_admin') },
    { path: '/protect', label: 'Firewall & email', icon: 'shield', show: () => can('admin') }] },
  { group: 'Workspace', items: [
    { path: '/integrations', label: 'Integrations', icon: 'link', show: () => can('super_admin') },
    { path: '/activity', label: 'Activity', icon: 'activity' }, { path: '/users', label: 'Users', icon: 'lock', show: () => store.session.auth === 'login' && can('super_admin') },
    { path: '/manual', label: 'Manual', icon: 'book' }, { path: '/settings', label: 'Settings', icon: 'settings' }] },
];

function Brand() {
  return h('a', { href: '#/sites', class: 'flex items-center gap-2.5 px-2' },
    h('span', { class: 'flex h-8 w-8 items-center justify-center rounded-lg bg-brand text-brand-ink' }, icon('terminal', 'h-4 w-4')),
    h('span', { class: 'text-base font-semibold tracking-tight' }, 'Shiplane'));
}

// Progress indicator for a running migration: visible on every page, links to the live view. Stays after it ends until dismissed.
const seen = (id) => { try { return sessionStorage.getItem('dismissed-run') === id; } catch (e) { return false; } };
const markSeen = (id) => { try { sessionStorage.setItem('dismissed-run', id); } catch (e) { /* private mode: it simply reappears */ } };

function MigrationDock({ compact = false } = {}) {
  const el = h('a', { class: compact ? 'hidden items-center gap-2 text-xs' : 'mx-3 mb-3 hidden rounded-lg border border-line p-3 text-left', href: '#/migrate' });
  let last = null;
  el.paint = (run) => {
    const show = run && (run.status === 'running' || !seen(run.id));
    el.classList.toggle('hidden', !show);
    el.classList.toggle('flex', !!show && compact);
    el.classList.toggle('block', !!show && !compact);
    if (!show) return;
    el.href = `#/migrate/run/${run.id}`;
    const running = run.status === 'running';
    const tone = running ? 'text-brand animate-spin' : run.status === 'done' ? 'text-ok' : 'text-bad';
    const label = running ? 'Migrating...' : run.status === 'done' ? 'Migration finished' : 'Migration needs attention';
    const head = h('span', { class: 'flex items-center gap-2 text-xs font-medium' }, h('span', { class: tone }, icon(running ? 'loader' : run.status === 'done' ? 'check' : 'alert', 'h-4 w-4')), label);
    if (compact) { el.replaceChildren(head); return; }
    el.replaceChildren(head, h('span', { class: 'mt-1 block truncate text-xs text-muted' }, running ? (run.step || 'Starting...') : run.title),
      running ? h('span', { class: 'mt-2 block' }, Meter({ value: run.done, max: run.total || 1, warnAt: 101, badAt: 101 })) : h('span', { class: 'mt-1 block text-xs text-brand' }, 'View details'));
  };
  el.addEventListener('click', () => { if (last && last.status !== 'running') { markSeen(last.id); el.classList.add('hidden'); } });
  el.track = (run) => { last = run; };
  return el;
}

function startMigrationWatch(docks) {
  let prev = null;
  const tick = async () => {
    try {
      const { run } = await api('/api/migrate/active');
      if (prev && prev.id === run?.id && prev.status === 'running' && run.status !== 'running') {
        toast(run.status === 'done' ? 'Migration finished' : 'Migration finished with problems', { tone: run.status === 'done' ? 'ok' : 'warn', title: run.title, timeout: 10000 });
      }
      prev = run;
      docks.forEach((d) => { d.track(run); d.paint(run); });
    } catch (e) { /* the app may be restarting: try again on the next tick */ }
  };
  const loop = async () => {
    if (!document.hidden) await tick();
    setTimeout(loop, prev && prev.status === 'running' ? 3000 : 30000);   // quick while a migration runs, relaxed otherwise, silent in a hidden tab
  };
  loop();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });
}

function buildShell() {
  const links = [];
  const nav = h('nav', { class: 'flex-1 space-y-6 overflow-y-auto px-3 py-4', 'aria-label': 'Main' },
    NAV.map((g) => {
      const items = g.items.filter((i) => !i.show || i.show());
      return items.length ? h('div', {}, h('p', { class: 'mb-1.5 px-3 text-xs font-medium uppercase tracking-wide text-subtle' }, g.group),
        items.map((i) => { const a = h('a', { href: '#' + i.path, class: 'nav-link', 'data-path': i.path }, icon(i.icon, 'h-4 w-4'), i.label); links.push(a); return a; })) : null;
    }));
  const dot = h('span', { class: 'h-2 w-2 rounded-full bg-subtle' });
  const conn = h('div', { class: 'min-w-0' }, h('p', { class: 'truncate text-xs font-medium' }), h('p', { class: 'text-xs text-muted' }));
  const themeBtn = h('button', { class: 'btn btn-ghost btn-icon btn-sm', 'aria-label': 'Toggle theme', title: 'Toggle light/dark', onClick: () => { const dark = !document.documentElement.classList.contains('dark'); setTheme(dark ? 'dark' : 'light'); paintTheme(); } });
  const paintTheme = () => themeBtn.replaceChildren(icon(document.documentElement.classList.contains('dark') ? 'sun' : 'moon', 'h-4 w-4'));
  paintTheme();
  const loginMode = store.session.auth === 'login';
  const signOut = h('button', { class: 'btn btn-ghost btn-icon btn-sm', 'aria-label': 'Sign out', title: 'Sign out', onClick: async () => { await api('/api/auth/logout', {}).catch(() => {}); location.reload(); } }, icon('power', 'h-4 w-4'));
  const account = loginMode ? h('div', { class: 'flex items-center justify-between gap-2 border-t border-line px-3 py-2.5' },
    h('div', { class: 'min-w-0' }, h('p', { class: 'truncate text-xs font-medium' }, store.session.me.email), h('p', { class: 'text-xs capitalize text-muted' }, store.session.me.role.replace('_', ' '))), signOut) : null;
  const dock = MigrationDock();
  const topDock = MigrationDock({ compact: true });
  const side = h('aside', { id: 'sidebar', class: 'fixed inset-y-0 left-0 z-40 hidden w-64 flex-col border-r border-line bg-surface lg:flex' },
    h('div', { class: 'flex h-14 items-center border-b border-line px-3' }, Brand()), nav, dock, account,
    h('div', { class: 'flex items-center justify-between gap-2 border-t border-line p-3' },
      h('div', { class: 'flex min-w-0 items-center gap-2.5' }, dot, conn), themeBtn));
  const backdrop = h('div', { class: 'fixed inset-0 z-30 hidden bg-black/40 lg:hidden', onClick: () => toggleNav(false) });
  const toggleNav = (open) => { side.classList.toggle('hidden', !open); side.classList.toggle('flex', open || window.innerWidth >= 1024); backdrop.classList.toggle('hidden', !open); };
  const top = h('header', { class: 'sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-surface/90 px-4 backdrop-blur lg:hidden' },
    h('button', { class: 'btn btn-ghost btn-icon btn-sm', 'aria-label': 'Open menu', onClick: () => toggleNav(true) }, icon('menu', 'h-5 w-5')), Brand(), h('span', { class: 'ml-auto' }, topDock));
  const outlet = h('main', { class: 'mx-auto w-full max-w-6xl px-4 py-6 sm:px-8 sm:py-8', id: 'main' });
  const el = h('div', {}, backdrop, side, h('div', { class: 'lg:pl-64' }, top, outlet));

  const paintConn = () => {
    const s = store.session.server;
    conn.children[0].textContent = s.mode === 'local' ? 'This server' : `${s.user}@${s.host}`;
    conn.children[1].textContent = store.error ? 'Not reachable' : store.status ? (s.mode === 'local' ? 'Running locally' : 'Connected over SSH') : 'Connecting...';
    dot.className = 'h-2 w-2 rounded-full ' + (store.error ? 'bg-bad' : store.status ? 'bg-ok' : 'bg-subtle');
  };
  subscribe(paintConn);
  paintConn();

  return { el, outlet, docks: [dock, topDock], mark(path) {
    links.forEach((a) => { const p = a.dataset.path; if (path === p || path.startsWith(p + '/')) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    toggleNav(false);
    document.title = `${({ sites: 'Sites', deploy: 'Deploy', databases: 'Databases', server: 'Server', metrics: 'Metrics', care: 'Server care', apps: 'Apps', maintenance: 'Maintenance', protect: 'Firewall & email', fleet: 'All servers', migrate: 'Migrate', domains: 'Domains', integrations: 'Integrations', dns: 'DNS hosting', activity: 'Activity', users: 'Users', manual: 'Manual', settings: 'Settings' })[path.split('/')[1]] || 'Shiplane'} - Shiplane`;
  } };
}

async function boot() {
  const app = document.getElementById('app');
  window.addEventListener('auth-required', () => location.reload());
  let who;
  try { who = await api('/api/auth/me'); } catch (e) {
    app.replaceChildren(h('div', { class: 'mx-auto max-w-lg p-8' }, Alert({ tone: 'bad', title: 'Could not start' }, e.message)));
    return;
  }
  if (!who.authenticated) {
    if (who.auth === 'login') { app.replaceChildren(AuthPage({ onDone: () => location.reload() })); return; }
    app.replaceChildren(h('div', { class: 'mx-auto max-w-lg p-8' }, Alert({ tone: 'warn', title: 'Open the link from the terminal' }, 'The app needs the access link printed in the terminal window where you started it.')));
    return;
  }
  try { await loadSession(); } catch (e) {
    app.replaceChildren(h('div', { class: 'mx-auto max-w-lg p-8' }, Alert({ tone: 'bad', title: 'Could not start' }, e.message)));
    return;
  }
  if (store.session.auth === 'login' && store.session.require2fa && !store.session.me.totp) { app.replaceChildren(TwoFactorGate()); return; }
  const shell = buildShell();
  app.replaceChildren(shell.el);
  window.addEventListener('keydown', (e) => {
    if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { const s = document.querySelector('[data-search]'); if (s) { e.preventDefault(); s.focus(); } }
  });
  route('/sites', SitesPage);
  route('/sites/:name', SitePage);
  route('/deploy', DeployPage);
  route('/databases', DatabasesPage);
  route('/domains', DomainsPage);
  route('/cloudflare', () => { location.replace('#/domains?tab=cloudflare'); return DomainsPage(); });
  route('/integrations', () => SettingsPage({ view: 'integrations' }));
  route('/dns', DnsPage);
  route('/server', ServerPage);
  route('/metrics', MetricsPage);
  route('/care', CarePage);
  route('/apps', AppsPage);
  route('/maintenance', MaintenancePage);
  route('/protect', ProtectPage);
  route('/fleet', FleetPage);
  route('/migrate', MigratePage);
  route('/migrate/run/:id', MigrationRunPage);
  route('/activity', ActivityPage);
  route('/users', UsersPage);
  route('/manual', ManualPage);
  route('/settings', SettingsPage);
  start(shell.outlet, ({ path }) => shell.mark(path));
  refresh();
  startPolling(((store.session.perf && store.session.perf.ui_poll) || 20) * 1000);
  startMigrationWatch(shell.docks);
  mountDeployDock();           // brings back deploys that were running before a refresh
}

boot();
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});   // makes the app installable (it caches nothing)
