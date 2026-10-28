// Maintenance: domain & SSL health, scheduled tasks per site, disk usage and cleanup.
import { h, Button, Badge, Card, Table, EmptyState, Alert, PageHeader, Tabs, Select, Field, Input, Meter, Skeleton, busy, confirmDialog } from '../kit.js';
import { store, refresh } from '../store.js';
import { bytes } from '../format.js';
import { api } from '../api.js';
import { attempt } from '../util.js';

const CERT_WARN_DAYS = 14;

function loading(box, fn) {
  box.replaceChildren(Skeleton(4));
  fn().catch((e) => box.replaceChildren(Alert({ tone: 'bad', title: 'Could not load' }, e.message)));
}

// ------------------------------------------------------------------ health
function healthTab() {
  const box = h('div', {});
  const certBadge = (d) => {
    if (d.dns == null) return Badge('No DNS record', 'bad');
    if (d.cert_days == null) return Badge(d.tls_error ? 'TLS error' : 'No certificate', 'bad');
    if (d.cert_days < 0) return Badge('Expired', 'bad');
    return Badge(`${d.cert_days} days left`, d.cert_days < CERT_WARN_DAYS ? 'warn' : 'ok');
  };
  async function load() {
    const r = await api('/api/domains/health');
    box.replaceChildren(Table({
      columns: [
        { label: 'Site', render: (d) => h('a', { href: `#/sites/${d.site}`, class: 'font-medium hover:underline' }, d.site) },
        { label: 'Address', class: 'text-muted', render: (d) => d.host },
        { label: 'DNS', class: 'font-mono text-xs text-muted', render: (d) => d.dns || '-' },
        { label: 'Certificate', render: certBadge },
        { label: 'Issuer', class: 'text-muted', render: (d) => d.issuer || d.tls_error || '-' },
        { label: 'HTTP', render: (d) => (d.public == null ? '-' : Badge(String(d.public), typeof d.public === 'number' && d.public < 400 ? 'ok' : 'bad')) },
      ],
      rows: r.domains,
      empty: EmptyState({ icon: 'globe', title: 'No public sites', body: 'Deploy a site to see its domain health here.' }),
    }));
  }
  loading(box, load);
  return h('div', { class: 'space-y-4' },
    Alert({ tone: 'info' }, 'Checked live from this computer. Behind Cloudflare the certificate shown is Cloudflare\'s edge certificate, which renews automatically.'),
    Card({ title: 'Domains and certificates', actions: Button({ label: 'Re-check', icon: 'refresh', size: 'sm', onClick: () => loading(box, load) }) }, box));
}

// ------------------------------------------------------------- scheduled tasks
function cronTab() {
  const sites = (store.status ? store.status.sites : []).filter((s) => s.has_service);
  if (!sites.length) return Card({ title: 'Scheduled tasks' }, EmptyState({ icon: 'clock', title: 'No Node or .NET sites yet', body: 'Scheduled tasks run as the site\'s own service user, so they need a site that runs as a service.' }));
  let name = sites[0].name;
  const list = h('div', {});
  const schedule = Input({ placeholder: '0 3 * * *', mono: true });
  const command = Input({ placeholder: 'node scripts/cleanup.js', mono: true });

  async function load() {
    const r = await api(`/api/cron?name=${encodeURIComponent(name)}`);
    list.replaceChildren(Table({
      columns: [
        { label: 'Schedule', class: 'font-mono text-xs', render: (t) => t.schedule },
        { label: 'Command', class: 'font-mono text-xs', render: (t) => t.command },
        { label: '', class: 'text-right', render: (t) => Button({ label: 'Remove', icon: 'trash', size: 'sm', variant: 'danger-soft', onClick: async () => {
          const c = await confirmDialog({ title: 'Remove this task?', message: `${t.schedule}  ${t.command}`, confirmLabel: 'Remove' });
          if (c.confirmed && await attempt(api('/api/cron/delete', { name, id: t.id }), 'Task removed')) loading(list, load);
        } }) },
      ],
      rows: r.tasks,
      empty: EmptyState({ icon: 'clock', title: 'No scheduled tasks', body: `Nothing is scheduled for ${name} yet.` }),
    }));
  }

  const add = Button({ label: 'Add task', icon: 'plus', variant: 'primary', onClick: () => busy(add, async () => {
    if (await attempt(api('/api/cron/add', { name, schedule: schedule.value, command: command.value }), 'Task added')) {
      schedule.value = ''; command.value = '';
      loading(list, load);
    }
  }) });

  loading(list, load);
  return h('div', { class: 'space-y-6' },
    Field({ label: 'Site' }, Select({ options: sites.map((s) => ({ value: s.name, label: s.name })), value: name, onChange: (v) => { name = v; loading(list, load); } })),
    Card({ title: 'Tasks', subtitle: 'Run inside the site\'s current release, as its service user' }, list),
    Card({ title: 'New task', subtitle: 'Schedule is minute, hour, day of month, month, weekday. Example: 0 3 * * * runs daily at 03:00 (server time).' },
      h('div', { class: 'grid gap-4 sm:grid-cols-3' },
        Field({ label: 'Schedule' }, schedule),
        h('div', { class: 'sm:col-span-2' }, Field({ label: 'Command', hint: 'Letters, digits, spaces and . / : = @ + , - _ only. Put anything complex in a script inside your site. Environment variables from the site are not loaded; read them from your own config.' }, command)),
      ), h('div', { class: 'pt-4' }, add)));
}

// ------------------------------------------------------------------- disk
function diskTab() {
  const box = h('div', { class: 'space-y-6' });
  async function clean(body, title, message) {
    const c = await confirmDialog({ title, message, confirmLabel: 'Clean up', tone: 'primary' });
    if (!c.confirmed) return;
    const r = await attempt(api('/api/disk/clean', body), (x) => `Done. Freed ${bytes(x.freed)}`);
    if (r) { await refresh({ quiet: true }); loading(box, load); }
  }
  async function load() {
    const d = await api('/api/disk');
    const used = d.disk_total - d.disk_free;
    const sysRows = [
      { label: 'System logs (journal)', size: d.system.journal, action: () => clean({ what: 'journal' }, 'Trim system logs?', 'Keeps the newest 200 MB of logs.') },
      { label: 'Package cache (apt)', size: d.system.apt, action: () => clean({ what: 'apt' }, 'Clear the package cache?', 'Downloaded installers are removed; nothing installed is touched.') },
      { label: 'Unused Docker images', size: null, action: () => clean({ what: 'docker' }, 'Remove unused Docker images?', 'Only images no container uses are removed.') },
    ];
    box.replaceChildren(
      Card({ title: 'Disk', subtitle: `${bytes(used)} used of ${bytes(d.disk_total)}` }, Meter({ value: used, max: d.disk_total })),
      Card({ title: 'Sites', subtitle: 'Each deploy keeps a release so you can roll back. Cleaning keeps the newest ones and never the live release.',
        actions: Button({ label: 'Keep newest 3 everywhere', icon: 'trash', size: 'sm', onClick: () => clean({ what: 'releases', keep: 3 }, 'Remove old releases of all sites?', 'The live release and the 3 newest are kept.') }) },
        Table({
          columns: [
            { label: 'Site', render: (s) => h('a', { href: `#/sites/${s.name}`, class: 'font-medium hover:underline' }, s.name) },
            { label: 'Total', render: (s) => bytes(s.size) },
            { label: 'Releases', class: 'text-muted', render: (s) => `${s.releases.length} (${bytes(s.releases.reduce((a, r) => a + r.size, 0))})` },
            { label: '', class: 'text-right', render: (s) => (s.releases.length > 1 ? Button({ label: 'Keep newest 1', size: 'sm', onClick: () => clean({ what: 'releases', name: s.name, keep: 1 }, `Remove old releases of ${s.name}?`, 'Only the live release is kept. You will not be able to roll back.') }) : '') },
          ],
          rows: d.sites,
          empty: EmptyState({ icon: 'disk', title: 'No sites', body: 'Nothing deployed with this app yet.' }),
        })),
      Card({ title: 'System' }, Table({
        columns: [
          { label: 'What', render: (r) => r.label },
          { label: 'Size', class: 'text-muted', render: (r) => (r.size == null ? (d.system.docker.join('; ') || 'not installed') : bytes(r.size)) },
          { label: '', class: 'text-right', render: (r) => Button({ label: 'Clean', size: 'sm', onClick: r.action }) },
        ],
        rows: sysRows,
      })));
  }
  loading(box, load);
  return box;
}

const TABS = [{ id: 'health', label: 'Domains & SSL' }, { id: 'cron', label: 'Scheduled tasks' }, { id: 'disk', label: 'Disk & cleanup' }];
const PANELS = { health: healthTab, cron: cronTab, disk: diskTab };

export function MaintenancePage({ query }) {
  let tab = PANELS[query.tab] ? query.tab : 'health';
  const body = h('div', {});
  const draw = () => {
    body.replaceChildren(Tabs({ tabs: TABS, active: tab, onChange: (t) => { tab = t; history.replaceState(null, '', `#/maintenance?tab=${t}`); draw(); } }), h('div', { class: 'pt-6' }, PANELS[tab]()));
  };
  if (!store.status) refresh().then(draw);
  draw();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' }, PageHeader({ title: 'Maintenance', description: 'Keep the server healthy: certificates, scheduled tasks and disk space.' }), body) };
}
