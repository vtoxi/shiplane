import { h, fill, Field, Button, Badge, Card, Table, EmptyState, Alert, PageHeader, Tabs, KeyValue, Console, Input, Textarea, Skeleton, icon, busy, confirmDialog, openModal, toast, serviceBadge, publicBadge, typeBadge } from '../kit.js';
import { store, subscribe, refresh, siteByName } from '../store.js';
import { bytes, ago, releaseDate, every } from '../format.js';
import { api } from '../api.js';
import { navigate } from '../router.js';
import { attempt, hostsOf } from '../util.js';
import { LogViewer, Spark } from '../live.js';
import { GitTab } from './gittab.js';
import { withHelp } from '../help.js';

export function SitePage({ params, query }) {
  const name = params.name;
  let tab = query.tab || 'overview';
  let logs = { text: '', lines: '200' };
  let cfg = null;
  const body = h('div', {});
  const head = h('div', {});
  let viewer = null;

  const go = (t) => { tab = t; draw(); history.replaceState(null, '', `#/sites/${name}?tab=${t}`); };

  async function action(site, act, extra, msg) {
    const r = await attempt(api('/api/action', { name: site.name, action: act, ...extra }), msg);
    await refresh({ quiet: true });
    return r;
  }

  function overview(s) {
    const m = s.meta || {};
    return h('div', { class: 'grid gap-6 lg:grid-cols-2' },
      Card({ title: 'Health', subtitle: 'Checked from this computer and from inside the server' },
        KeyValue([
          ['Public check', publicBadge(s)],
          ['Service', serviceBadge(s)],
          ['Through Caddy', `HTTP ${s.local_http}`],
          s.has_service ? ['Memory', s.mem ? bytes(s.mem) : null] : null,
          s.has_service ? ['Restarts', String(s.restarts || 0)] : null,
          s.has_service ? ['Running since', s.since] : null,
        ])),
      Card({ title: 'Details' },
        KeyValue([
          ['Address', h('a', { href: `https://${s.hostname}`, target: '_blank', rel: 'noopener noreferrer', class: 'inline-flex items-center gap-1 text-brand hover:underline' }, `https://${s.hostname}`, icon('external', 'h-3 w-3'))],
          ['Redirects from', s.redirects.join(', ')],
          ['Type', typeBadge(s.type, s.framework)],
          ['Local port', s.port ? String(s.port) : null],
          ['Database', s.db ? `app_${s.name.replace(/-/g, '_')} (Postgres)` : null],
          ['Environment', s.env_keys.length ? `${s.env_keys.length} variables` : null],
          ['Current release', s.current],
          ['Deployed', m.deployedAt ? `${m.deployedAt}${m.deployedBy ? ' by ' + m.deployedBy : ''}` : 'before the app existed'],
        ])),
      uptimeCard(s));
  }

  // Environment: every variable with its value (hidden until the eye is pressed), edited in place; one Save applies all changes and restarts once.
  function environment(s) {
    if (!s.has_service) return Card({ title: 'Environment variables' }, EmptyState({ icon: 'lock', title: 'Static sites have no environment', body: 'Environment variables apply to apps that run as a service (.NET, Node, commands).' }));
    const box = h('div', {}, Skeleton(4));
    const rows = [];                                   // {key, orig, input, keyInput, removed, el}
    const table = h('div', { class: 'divide-y divide-line' });
    const saveBtn = Button({ label: 'Save and restart', variant: 'primary', disabled: true, onClick: () => busy(saveBtn, save) });
    const changes = () => {
      const set = {}, unset = [];
      rows.forEach((r) => {
        const key = (r.keyInput ? r.keyInput.value : r.key).trim();
        if (r.removed) { if (r.orig != null) unset.push(r.key); return; }
        if (key && (r.orig == null || r.input.value !== r.orig)) set[key] = r.input.value;
      });
      return { set, unset };
    };
    const refreshSave = () => { const c = changes(); const n = Object.keys(c.set).length + c.unset.length; saveBtn.disabled = !n; saveBtn.lastChild.textContent = n ? `Save ${n} change${n > 1 ? 's' : ''} and restart` : 'Save and restart'; };
    function row(key, value) {
      const r = { key, orig: value };
      r.keyInput = key == null ? Input({ placeholder: 'NAME', mono: true, onInput: refreshSave }) : null;
      r.input = Input({ value: value == null ? '' : value, mono: true, type: key == null ? 'text' : 'password', placeholder: 'value', onInput: refreshSave });
      const eye = Button({ icon: key == null ? 'eyeoff' : 'eye', iconOnly: true, size: 'sm', variant: 'ghost', title: 'Show or hide the value', onClick: () => {
        const show = r.input.type === 'password';
        r.input.type = show ? 'text' : 'password';
        fill(eye, icon(show ? 'eyeoff' : 'eye', 'h-4 w-4'));
      } });
      const del = Button({ icon: 'trash', iconOnly: true, size: 'sm', variant: 'ghost', title: 'Remove', onClick: () => {
        if (r.orig == null) { rows.splice(rows.indexOf(r), 1); r.el.remove(); refreshSave(); return; }
        r.removed = !r.removed;
        r.el.style.opacity = r.removed ? '0.45' : '';
        r.input.disabled = r.removed;
        fill(del, icon(r.removed ? 'undo' : 'trash', 'h-4 w-4'));
        refreshSave();
      } });
      r.el = h('div', { class: 'grid grid-cols-1 items-center gap-2 py-2 sm:grid-cols-[minmax(10rem,16rem)_1fr_auto]' },
        r.keyInput || h('code', { class: 'truncate font-mono text-xs font-medium', title: key }, key), r.input, h('div', { class: 'flex gap-1' }, eye, del));
      rows.push(r);
      table.append(r.el);
      return r;
    }
    async function save() {
      const c = changes();
      const bad = Object.keys(c.set).find((k) => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(k));
      if (bad) { toast(`"${bad}" is not a valid name: letters, digits and _, not starting with a digit`, { tone: 'bad' }); return; }
      if (await attempt(api('/api/site/env', { name: s.name, set: c.set, unset: c.unset }), (r) => r.text)) { await refresh({ quiet: true }); draw(); }
    }
    const add = Button({ label: 'Add variable', icon: 'plus', size: 'sm', onClick: () => { const r = row(null, null); r.keyInput.focus(); } });
    const paste = Button({ label: 'Paste .env', icon: 'copy', size: 'sm', onClick: async () => {
      const text = Textarea({ rows: 8, placeholder: 'NAME=value\nOTHER=value', mono: true });
      openModal({ title: 'Paste a .env file', description: 'Each NAME=value line is added or replaces the value of an existing name. Nothing is saved until you press Save.', body: text, size: 'lg',
        actions: [{ label: 'Add to the list', variant: 'primary', onClick: (m) => {
          text.value.split(/\r?\n/).forEach((line) => {
            const mm = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
            if (!mm) return;
            const v = mm[2].replace(/^(['"])(.*)\1$/, '$2');
            const have = rows.find((r) => (r.keyInput ? r.keyInput.value : r.key) === mm[1]);
            if (have) { have.input.value = v; } else { const r = row(null, null); r.keyInput.value = mm[1]; r.input.value = v; }
          });
          refreshSave(); m.close();
        } }] });
    } });
    api(`/api/site/env/values?name=${encodeURIComponent(s.name)}`).then(({ vars }) => {
      vars.forEach((v) => row(v.key, v.value));
      fill(box,
        h('div', { class: 'mb-2 hidden grid-cols-[minmax(10rem,16rem)_1fr_auto] gap-2 text-xs font-medium uppercase tracking-wide text-subtle sm:grid' }, h('span', {}, 'Name'), h('span', {}, 'Value'), h('span', {})),
        vars.length ? null : h('p', { class: 'py-2 text-sm text-muted' }, 'No variables yet.'), table,
        h('div', { class: 'mt-4 flex flex-wrap items-center gap-2' }, add, paste, h('span', { class: 'flex-1' }), saveBtn));
    }).catch((e) => fill(box, Alert({ tone: 'bad', title: 'Could not read the variables' }, e.message)));
    return Card({ title: withHelp('Environment variables', 'environment-variables-and-secrets'), subtitle: 'Values are hidden until you press the eye. Reading them is recorded in Activity. Saving restarts the app once.' }, box);
  }

  function releases(s) {
    const list = s.releases.slice().reverse();
    return Card({ title: 'Releases', subtitle: 'The last 3 are kept. Activate an older one to roll back.', bodyClass: '' },
      Table({
        columns: [
          { label: 'Release', render: (r) => h('code', { class: 'font-mono text-xs' }, r) },
          { label: 'Created', class: 'text-muted', render: (r) => { const d = releaseDate(r); return d ? `${ago(r)} (${d.toISOString().replace('T', ' ').slice(0, 16)} UTC)` : r; } },
          { label: 'Status', render: (r) => (r === s.current ? Badge('current', 'ok', { dot: true }) : Badge('older', 'neutral')) },
          { label: '', class: 'text-right', render: (r) => (r === s.current ? '' : Button({ label: 'Activate', icon: 'undo', size: 'sm', onClick: async () => {
            const c = await confirmDialog({ title: `Switch to release ${r}?`, message: 'The site starts serving that release right away.', confirmLabel: 'Activate', tone: 'primary' });
            if (c.confirmed) { await action(s, 'activate', { release: r }, `Now on ${r}`); draw(); }
          } })) },
        ],
        rows: list,
        empty: EmptyState({ title: 'No releases', body: 'This site was not created by the app.' }),
      }));
  }

  function logsTab(s) {
    if (!s.has_service) return Card({ title: 'Service logs' }, EmptyState({ icon: 'file', title: 'Static sites have no service log', body: 'Caddy serves the files directly.' }));
    viewer = LogViewer({ kind: 'site', name: s.name });
    return h('div', { class: 'space-y-6' }, logSearch(s), Card({ title: 'Live logs', subtitle: 'journalctl, streaming as it happens' }, viewer.el));
  }

  function logSearch(s) {
    const q = h('input', { class: 'input text-xs', placeholder: 'Search text, for example: timeout', spellcheck: 'false' });
    const level = h('select', { class: 'input select' }, [['', 'Any level'], ['warning', 'Warnings and errors'], ['error', 'Errors only']].map(([v, l]) => h('option', { value: v }, l)));
    const since = h('select', { class: 'input select' }, [['', 'Latest'], ['15m', 'Last 15 minutes'], ['1h', 'Last hour'], ['6h', 'Last 6 hours'], ['24h', 'Last 24 hours'], ['7d', 'Last 7 days']].map(([v, l]) => h('option', { value: v }, l)));
    const out = Console({ lines: [], empty: 'Search results appear here.' });
    const go = Button({ label: 'Search', icon: 'search', variant: 'primary', onClick: () => busy(go, async () => {
      const r = await attempt(api(`/api/logs?name=${encodeURIComponent(s.name)}&lines=1000&q=${encodeURIComponent(q.value)}&level=${level.value}&since=${since.value}`));
      if (r) out.set(r.text.trim().split('\n'));
    }) });
    q.addEventListener('keydown', (e) => { if (e.key === 'Enter') go.click(); });
    return Card({ title: 'Search the logs', subtitle: 'Looks through the latest few thousand lines. Level is matched by keywords such as error, fatal, exception, warn.' },
      h('div', { class: 'grid gap-3 sm:grid-cols-[1fr_12rem_12rem_auto]' }, q, level, since, go), h('div', { class: 'mt-4' }, out.el));
  }

  function uptimeCard(s) {
    const body = h('div', { class: 'skeleton h-16' });
    const sub = h('span', {}, 'Public address checked regularly; red bars are failed checks');
    api('/api/health').then((r) => {
      sub.textContent = r.enabled ? `Public address checked ${every(r.interval)}; red bars are failed checks. Change this in Settings.` : 'Uptime checks are switched off. Turn them on in Settings.';
      const x = r.sites[s.name];
      body.className = '';
      body.replaceChildren(x ? h('div', {},
        h('div', { class: 'mb-3 flex flex-wrap items-center gap-3' }, Badge(x.up ? 'Up' : 'Down', x.up ? 'ok' : 'bad', { dot: true }),
          h('span', { class: 'text-sm text-muted' }, `${x.uptime}% over the last ${x.checks} checks`), x.last_ms ? h('span', { class: 'text-sm text-muted' }, `last response ${x.last_ms} ms`) : null),
        Spark({ values: x.points.map((q) => q[1]), times: x.points.map((q) => q[0]), fmt: (v) => (v == null ? 'Down: the check failed' : `Up, answered in ${v} ms`), tone: x.up ? 'text-ok' : 'text-bad' }))
        : h('p', { class: 'text-sm text-muted' }, 'No checks yet. The first one runs within a couple of minutes.'));
    }).catch(() => { body.className = 'text-sm text-muted'; body.textContent = 'Health history is not available.'; });
    return Card({ title: 'Uptime', subtitle: sub, class: 'lg:col-span-2' }, body);
  }

  function configTab(s) {
    const box = h('div', { class: 'space-y-6' }, Card({ title: 'Loading configuration...' }, h('div', { class: 'skeleton h-24' })));
    const paint = () => box.replaceChildren(...cfg.map((sec) => Card({ title: sec.path.split('/').pop(), subtitle: sec.path, actions: Button({ label: 'Copy', icon: 'copy', size: 'sm', variant: 'ghost', onClick: () => { navigator.clipboard && navigator.clipboard.writeText(sec.text); toast('Copied', { tone: 'ok', timeout: 1500 }); } }) }, Console({ lines: [sec.text] }).el)));
    if (cfg) paint(); else api(`/api/site/config?name=${s.name}`).then((r) => { cfg = r.sections; paint(); }).catch((e) => box.replaceChildren(Alert({ tone: 'bad', title: 'Could not read the configuration' }, e.message)));
    return h('div', { class: 'space-y-4' }, Alert({ tone: 'info' }, 'Read-only view of the files the app generated for this site. Change them by redeploying or editing the environment.'), box);
  }

  function runCard(s) {
    const input = h('input', { class: 'input font-mono text-xs w-full', placeholder: 'e.g. node migrate.js   or   npx prisma migrate deploy' });
    const out = h('pre', { class: 'mt-3 max-h-80 overflow-auto rounded bg-black/80 p-3 font-mono text-xs text-white hidden' });
    const go = Button({ label: 'Run', icon: 'play', onClick: () => busy(go, async () => {
      const command = input.value.trim();
      if (!command) return toast('Type a command first', { tone: 'warn' });
      const r = await attempt(api('/api/site/run', { name: s.name, command }), (x) => (x.ok ? 'Command finished' : 'Command failed'));
      if (r) { out.textContent = r.text || '(no output)'; out.classList.remove('hidden'); }
    }) });
    return Card({ title: 'Run a command', subtitle: 'Runs once in the current release as the site\'s own user, with its environment variables. Stops after 5 minutes.' },
      h('div', { class: 'flex gap-2' }, input, go), out);
  }

  // Change the address: Caddy, metadata and (optionally) the environment and DNS records all move to the new name.
  function renameCard(s) {
    if (s.name === 'coderunner') return null;
    const sess = store.session;
    const host = Input({ value: s.hostname, mono: true, placeholder: 'new.example.com' });
    const go = Button({ label: 'Change address', icon: 'globe', variant: 'primary', onClick: async () => {
      const to = host.value.trim().toLowerCase();
      if (!to || to === s.hostname) { toast('Type the new address first', { tone: 'warn' }); return; }
      const sw = [];
      if (s.has_service) sw.push({ id: 'env', label: `Replace ${s.hostname} in its environment variables`, description: 'For example APP_URL. The app restarts if anything changed.', default: true });
      if (sess.cloudflare) sw.push({ id: 'mapCf', label: 'Move the Cloudflare route and DNS record', description: `Adds ${to}, then removes ${s.hostname}.`, default: !(s.meta && s.meta.direct) });
      if ((sess.dnsProviders || []).length) sw.push({ id: 'mapDns', label: 'Move the A record (Route 53 / DigitalOcean)', description: `Adds ${to}, then removes ${s.hostname} if it points here.`, default: !!(s.meta && s.meta.direct) });
      const c = await confirmDialog({ title: `Move ${s.name} to ${to}?`, message: `The site stops answering on ${s.hostname} and answers on ${to}. Its files, service, database, releases and Git connection stay as they are. Links and bookmarks to the old address stop working (add it as a redirect later if you need it).`, confirmLabel: 'Change address', tone: 'primary', requireText: to, switches: sw });
      if (!c.confirmed) return;
      busy(go, async () => {
        const r = await attempt(api('/api/site/rename', Object.assign({ name: s.name, hostname: to }, c.values)));
        if (!r) return;
        openModal({ title: 'Address changed', body: Console({ lines: r.lines }).el, actions: [{ label: 'Done', variant: 'primary', onClick: (m) => m.close() }] });
        await refresh({ quiet: true });
        draw();
      });
    } });
    return Card({ title: withHelp('Address', 'change-a-site-s-address'), subtitle: `Now ${s.hostname}. Change it here and everything that uses it follows.` },
      h('div', { class: 'flex flex-col gap-3 sm:flex-row sm:items-end' }, h('div', { class: 'min-w-0 flex-1' }, Field({ label: 'New address' }, host)), go));
  }

  function manage(s) {
    const hosts = hostsOf(s);
    return h('div', { class: 'space-y-6' },
      renameCard(s),
      Card({ title: 'Service', subtitle: s.has_service ? 'Control the running process' : 'Static sites are served directly by Caddy; there is no process to control' },
        s.has_service ? h('div', { class: 'flex flex-wrap gap-2' },
          Button({ label: 'Restart', icon: 'refresh', onClick: () => action(s, 'restart', {}, 'Restarted') }),
          s.service === 'active' ? Button({ label: 'Stop', icon: 'stop', onClick: async () => { const c = await confirmDialog({ title: `Stop ${s.name}?`, message: 'The site goes offline until you start it again.', confirmLabel: 'Stop', tone: 'primary' }); if (c.confirmed) action(s, 'stop', {}, 'Stopped'); } })
            : Button({ label: 'Start', icon: 'play', variant: 'primary', onClick: () => action(s, 'start', {}, 'Started') })) : null),
      s.has_service && s.type !== 'docker' ? runCard(s) : null,
      Card({ title: 'Redeploy', subtitle: 'Upload a new build. The previous release is kept for rollback.' }, Button({ label: 'Redeploy this site', icon: 'rocket', variant: 'primary', href: `#/deploy?site=${s.name}` })),
      h('section', { class: 'card border-bad/40' },
        h('div', { class: 'card-header' }, h('div', {}, h('h2', { class: 'text-sm text-bad' }, 'Danger zone'), h('p', { class: 'mt-0.5 text-xs text-muted' }, 'Deleting removes the site, its files and its service from the server.'))),
        h('div', { class: 'card-body' }, Button({ label: 'Delete this site', icon: 'trash', variant: 'danger-soft', onClick: async () => {
          const sw = [];
          if (s.db) sw.push({ id: 'dropDb', label: 'Also delete its Postgres database and data', description: 'This cannot be undone.', default: false });
          if (store.session && store.session.cloudflare && hosts.length) sw.push({ id: 'unmapCf', label: 'Also remove its Cloudflare route and DNS record', description: hosts.join(', '), default: true });
          if (store.session && (store.session.dnsProviders || []).length && hosts.length) sw.push({ id: 'unmapDns', label: 'Also remove its A records (Route 53 / DigitalOcean)', description: 'Only records that point at this server.', default: true });
          const c = await confirmDialog({ title: `Delete ${s.name}?`, message: 'The site stops being served and its files are removed from the server.', confirmLabel: 'Delete site', requireText: s.name, switches: sw });
          if (!c.confirmed) return;
          const r = await attempt(api('/api/action', { name: s.name, action: 'delete', confirm: s.name, dropDb: !!c.values.dropDb, unmapCf: !!c.values.unmapCf, unmapDns: !!c.values.unmapDns, hosts }), (x) => x.text);
          if (r) { await refresh({ quiet: true }); navigate('/sites'); }
        } }))));
  }

  function checkNowButton(s) {
    const b = Button({ label: 'Check now', icon: 'refresh', onClick: () => busy(b, async () => {
      const r = await attempt(api('/api/site/check', { name: s.name }));
      if (!r) return;
      toast(r.up ? `Up: answered in ${r.ms} ms (HTTP ${r.code})` : `Down: ${r.code === 'err' ? 'no answer' : 'HTTP ' + r.code}`, { tone: r.up ? 'ok' : 'bad' });
      await refresh({ quiet: true });
      draw();
    }) });
    return b;
  }

  function draw() {
    if (viewer) { viewer.destroy(); viewer = null; }
    const s = siteByName(name);
    if (!store.status) { head.replaceChildren(PageHeader({ title: name })); body.replaceChildren(h('div', { class: 'skeleton h-40' })); return; }
    if (!s) { head.replaceChildren(PageHeader({ title: 'Site not found', crumbs: [{ label: 'Sites', href: '#/sites' }, { label: name }] })); body.replaceChildren(Card({}, EmptyState({ icon: 'search', title: `There is no site called "${name}"`, body: 'It may have been deleted.', action: Button({ label: 'Back to sites', href: '#/sites' }) }))); return; }
    head.replaceChildren(PageHeader({
      crumbs: [{ label: 'Sites', href: '#/sites' }, { label: s.name }], title: s.name,
      description: h('span', { class: 'inline-flex flex-wrap items-center gap-2' }, typeBadge(s.type, s.framework), serviceBadge(s), publicBadge(s)),
      actions: [checkNowButton(s), Button({ label: 'Open site', icon: 'external', href: `https://${s.hostname}`, newTab: true }), Button({ label: 'Redeploy', icon: 'rocket', variant: 'primary', href: `#/deploy?site=${s.name}` })],
    }));
    const tabs = Tabs({ active: tab, onChange: go, tabs: [{ id: 'overview', label: 'Overview' }, { id: 'env', label: 'Environment', count: s.has_service ? s.env_keys.length : null }, { id: 'releases', label: 'Releases', count: s.releases.length }, { id: 'git', label: 'Git' }, { id: 'logs', label: 'Logs' }, { id: 'config', label: 'Config' }, { id: 'manage', label: 'Manage' }] });
    const panel = { overview, env: environment, releases, logs: logsTab, config: configTab, manage, git: GitTab }[tab] || overview;
    body.replaceChildren(tabs, h('div', { class: 'pt-6' }, panel(s)));
  }

  // the Logs, Config, Git and Environment tabs hold live output or unsaved forms: a background refresh must not rebuild them
  const unsub = subscribe(() => { if (tab !== 'logs' && tab !== 'config' && tab !== 'git' && tab !== 'env' && !document.querySelector('dialog[open]')) draw(); });
  if (!store.status) refresh();
  draw();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' }, head, body), destroy: () => { unsub(); if (viewer) viewer.destroy(); } };
}
