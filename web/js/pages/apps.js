// Apps: install runtimes, services and web apps on the server. Each app is a card with a version picker and a status.
import { h, icon, Logo, Button, Badge, Card, Alert, EmptyState, PageHeader, Tabs, Select, Field, Input, Switch, Console, Skeleton, openModal, busy } from '../kit.js';
import { api } from '../api.js';
import { attempt, watchJob } from '../util.js';

const KINDS = [{ id: 'all', label: 'All' }, { id: 'runtime', label: 'Runtimes' }, { id: 'service', label: 'Databases & services' }, { id: 'webapp', label: 'Web apps' }, { id: 'tool', label: 'Tools' }];
const TILE = { runtime: 'bg-brand text-brand-ink', service: 'bg-ok text-white', webapp: 'bg-info text-white', tool: 'bg-sunken text-fg' };
const HELP = 'Where should it go? Every site needs its own folder on the server. The usual choice is /var/www/<your address>, for example /var/www/blog.example.com for blog.example.com. The folder must be empty: nothing is overwritten.';

// ------------------------------------------------------- server folder browser
export function pickFolder({ start = '/var/www', title = 'Choose a folder on the server' } = {}) {
  return new Promise((resolve) => {
    let current = start;
    const body = h('div', { class: 'space-y-3' });
    const crumbs = h('div', { class: 'flex flex-wrap items-center gap-1 text-sm' });
    const list = h('div', { class: 'max-h-64 divide-y divide-line overflow-y-auto rounded-lg border border-line' });
    const status = h('p', { class: 'text-xs text-muted' });
    const newName = Input({ placeholder: 'new folder name', mono: true });
    let modal;

    async function go(path) {
      list.replaceChildren(Skeleton(3));
      try {
        const r = await api(`/api/fs/list?path=${encodeURIComponent(path)}`);
        current = r.path;
        const parts = current === '/' ? [] : current.split('/').filter(Boolean);
        crumbs.replaceChildren(h('button', { type: 'button', class: 'text-brand hover:underline', onClick: () => go('/') }, 'server'),
          ...parts.map((p, i) => [h('span', { class: 'text-subtle' }, '/'), h('button', { type: 'button', class: 'text-brand hover:underline', onClick: () => go('/' + parts.slice(0, i + 1).join('/')) }, p)]).flat());
        const rows = [];
        if (r.parent) rows.push(h('button', { type: 'button', class: 'flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-sunken', onClick: () => go(r.parent) }, icon('undo', 'h-4 w-4'), '.. (up)'));
        r.dirs.forEach((d) => rows.push(h('button', { type: 'button', class: 'flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-sunken', onClick: () => go((current === '/' ? '' : current) + '/' + d) }, icon('folder', 'h-4 w-4'), d)));
        list.replaceChildren(...(rows.length ? rows : [h('p', { class: 'p-3 text-sm text-muted' }, 'No folders here yet. Create one below.')]));
        status.textContent = r.is_top ? 'Pick where to start.' : !r.exists ? 'This folder does not exist yet.' : r.file_count ? `${r.file_count} file(s) in this folder: an app needs an empty one.` : 'This folder is empty: good.';
      } catch (e) { list.replaceChildren(Alert({ tone: 'bad', title: 'Could not read the folder' }, e.message)); }
    }

    const make = Button({ label: 'Create folder', icon: 'plus', size: 'sm', onClick: () => busy(make, async () => {
      const r = await attempt(api('/api/fs/mkdir', { path: (current === '/' ? '' : current) + '/' + newName.value.trim() }), 'Folder created');
      if (r) { newName.value = ''; go(r.path); }
    }) });
    body.append(h('p', { class: 'text-sm text-muted' }, HELP), crumbs, list, status, h('div', { class: 'flex items-end gap-2' }, h('div', { class: 'min-w-0 flex-1' }, newName), make));
    modal = openModal({ title, body, size: 'lg', onClose: () => resolve(null), actions: [
      { label: 'Cancel', onClick: () => { modal.close(); resolve(null); } },
      { label: 'Use this folder', variant: 'primary', onClick: () => { if (current === '/') return; modal.close(); resolve(current); } }] });
    go(current);
  });
}

// ----------------------------------------------------------------- install form
function webAppForm(app, version, state) {
  const phpOptions = state.catalog.find((a) => a.id === 'php').versions;
  const installedPhp = Object.keys((state.status.apps.php || {}).installed || {});
  const host = Input({ placeholder: 'blog.example.com', mono: true });
  const path = Input({ placeholder: '/var/www/blog.example.com', mono: true });
  const php = Select({ options: phpOptions.map((v) => ({ value: v, label: `PHP ${v}${installedPhp.includes(v) ? ' (installed)' : ''}` })), value: installedPhp[0] || '8.3' });
  const user = Input({ placeholder: 'admin', mono: true });
  const pw = Input({ type: 'password', placeholder: '12+ characters', autocomplete: 'new-password' });
  const reqs = Switch({ label: 'Install what is missing', description: `Needs: ${app.requires.map((r) => state.catalog.find((a) => a.id === r).name).join(', ')}. Missing ones are installed first.`, checked: true });
  const cf = state.session && state.session.cloudflare ? Switch({ label: 'Map the address in Cloudflare', description: 'Adds the tunnel route and DNS record.', checked: false }) : null;
  host.addEventListener('input', () => { if (!path.dataset.touched) path.value = host.value.trim() ? `/var/www/${host.value.trim().toLowerCase()}` : ''; });
  path.addEventListener('input', () => { path.dataset.touched = '1'; });
  const browse = Button({ label: 'Browse...', icon: 'folder', onClick: async () => { const p = await pickFolder({ start: path.value.startsWith('/') ? path.value.replace(/\/[^/]*$/, '') || '/var/www' : '/var/www' }); if (p) { path.value = p; path.dataset.touched = '1'; } } });
  const el = h('div', { class: 'space-y-4' },
    Alert({ tone: 'info', title: 'How this works' }, `${app.name} gets its own address and its own folder. Use a domain (example.com) or a sub-domain (blog.example.com) whose DNS already points at this server or its Cloudflare tunnel. Installing inside a sub-path such as example.com/blog is not supported.`),
    Field({ label: 'Address', hint: 'The domain or sub-domain visitors will use.' }, host),
    Field({ label: 'Folder on the server', hint: HELP }, h('div', { class: 'flex gap-2' }, h('div', { class: 'min-w-0 flex-1' }, path), browse)),
    Field({ label: 'PHP version' }, php),
    app.protect ? h('div', { class: 'grid gap-4 sm:grid-cols-2' },
      Field({ label: 'Site user', hint: 'Database tools must not be open to the world: visitors need this user and password first.' }, user), Field({ label: 'Site password', hint: 'Letters, digits and @ % ^ * _ + = . , : ? ! -' }, pw)) : null,
    reqs, cf);
  return { el, values: () => ({ hostname: host.value.trim(), path: path.value.trim(), php: php.value, protect_user: user.value.trim(), protect_password: pw.value, install_requirements: reqs.get(), mapCf: cf ? cf.get() : false }) };
}

export function AppsPage() {
  const state = { catalog: [], status: null, session: null, kind: (() => { const t = (location.hash.match(/[?&]tab=(\w+)/) || [])[1]; return KINDS.some((k) => k.id === t) ? t : 'all'; })(), running: null };
  const grid = h('div', { class: 'grid gap-4 sm:grid-cols-2 xl:grid-cols-3' });
  const log = h('div', {});
  const tabs = h('div', {});

  async function load() {
    grid.replaceChildren(Skeleton(4));
    try {
      const [a, s] = await Promise.all([api('/api/apps'), api('/api/session')]);
      state.catalog = a.catalog; state.status = a.status; state.session = s;
      draw();
    } catch (e) { grid.replaceChildren(Alert({ tone: 'bad', title: 'Could not read the server' }, e.message)); }
  }

  async function runInstall(app, version, extra) {
    const con = Console({ empty: 'Starting...' });
    state.running = app.id;
    log.replaceChildren(Card({ title: `Installing ${app.name}${version === 'default' ? '' : ' ' + version}`, subtitle: 'Live output from the server' }, con.el));
    draw();
    const r = await attempt(api('/api/apps/install', { id: app.id, version, ...extra }));
    if (!r) { state.running = null; log.replaceChildren(); draw(); return; }
    const done = await watchJob(r.job, con);
    state.running = null;
    h && con.push([done.ok ? 'Finished. The card now shows the new status.' : 'Failed. Read the output above; nothing is left half-done that needs cleaning by hand unless it says so.']);
    await load();
  }

  function card(app) {
    const info = (state.status.apps[app.id] || {});
    const installed = Object.keys(info.installed || {});
    const instances = info.instances || [];
    let version = app.default;
    const sel = Select({ options: app.versions.map((v) => ({ value: v, label: (v === 'default' ? 'OS default' : v === 'latest' ? 'Latest' : v) + (installed.includes(v) ? ' (installed)' : '') })), value: version, onChange: (v) => { version = v; paint(); } });
    const button = Button({ label: 'Install', icon: 'download', variant: 'primary', onClick: () => start() });
    const badge = h('span', {});
    const line = h('p', { class: 'text-xs text-muted' });
    const busyNow = () => state.running !== null;
    function paint() {
      const have = app.needs_path ? false : installed.includes(version);
      button.disabled = have || busyNow();
      button.replaceChildren(icon(have ? 'check' : 'download', 'h-4 w-4'), have ? ' Installed' : app.needs_path && instances.length ? ' Install on another address' : ' Install');
      const active = app.needs_path ? instances.length > 0 : installed.length > 0;
      badge.replaceChildren(state.running === app.id ? Badge('Installing...', 'brand', { dot: true }) : active ? Badge(app.needs_path ? `On ${instances.length} site${instances.length === 1 ? '' : 's'}` : 'Installed', 'ok', { dot: true }) : info.system ? Badge('System: ' + info.system, 'neutral') : Badge('Not installed', 'neutral'));
      line.textContent = app.needs_path ? instances.map((i) => `${i.hostname} (${i.version || '?'})`).join(', ') : installed.map((v) => (info.installed[v] ? `${v}: ${info.installed[v]}` : v)).join('  |  ');
    }
    async function start() {
      if (!app.needs_path) { await runInstall(app, version, {}); return; }
      const form = webAppForm(app, version, state);
      const m = openModal({ title: `Install ${app.name}${version === 'latest' ? '' : ' ' + version}`, body: form.el, size: 'lg', actions: [
        { label: 'Cancel', onClick: () => m.close() },
        { label: 'Install', variant: 'primary', onClick: () => { m.close(); runInstall(app, version, form.values()); } }] });
    }
    paint();
    return h('section', { class: 'card flex flex-col' },
      h('div', { class: 'card-body flex flex-1 flex-col gap-3' },
        h('div', { class: 'flex items-start gap-3' },
          h('span', { class: `flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-sm font-semibold ${Logo(app.id) ? 'bg-sunken' : (TILE[app.kind] || TILE.tool)}`, 'aria-hidden': 'true' }, Logo(app.id, 'h-6 w-6') || app.tile),
          h('div', { class: 'min-w-0 flex-1' }, h('div', { class: 'flex items-center justify-between gap-2' }, h('h2', { class: 'truncate text-sm' }, app.name), badge), h('p', { class: 'mt-1 text-xs text-muted' }, app.blurb))),
        line,
        h('div', { class: 'mt-auto flex items-end gap-2 pt-1' }, h('div', { class: 'min-w-0 flex-1' }, sel), button)));
  }

  function draw() {
    if (!state.status) return;
    tabs.replaceChildren(Tabs({ tabs: KINDS.map((k) => ({ id: k.id, label: k.label })), active: state.kind, onChange: (k) => { state.kind = k; history.replaceState(null, '', `#/apps?tab=${k}`); draw(); } }));
    const apps = state.catalog.filter((a) => state.kind === 'all' || a.kind === state.kind);
    grid.replaceChildren(...(apps.length ? apps.map(card) : [EmptyState({ icon: 'box', title: 'Nothing here' })]));
  }

  load();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' },
    PageHeader({ title: 'Apps', description: 'Install what your sites need: language runtimes, databases and ready-made web apps. Installed items show their status here.',
      actions: Button({ label: 'Refresh', icon: 'refresh', onClick: load }) }), tabs, log, grid) };
}
