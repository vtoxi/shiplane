// Fleet: every server in one list, side by side health, switch the one being managed, and adopt a fresh server.
import { h, fill, Button, Badge, Card, Table, EmptyState, Alert, PageHeader, Field, Input, Select, Console, Skeleton, confirmDialog, openModal, toast } from '../kit.js';
import { api } from '../api.js';
import { attempt, watchJob } from '../util.js';

export function FleetPage() {
  const body = h('div', { class: 'space-y-6' }, Skeleton(4));
  let rows = [];

  async function load() {
    try {
      const [list, ov] = await Promise.all([api('/api/fleet'), api('/api/fleet/overview')]);
      rows = list.servers.map((s) => Object.assign({}, s, ov.servers.find((o) => o.id === s.id) || {}));
      draw();
    } catch (e) { fill(body, Alert({ tone: 'bad', title: 'Could not load the servers' }, e.message)); }
  }

  async function adopt(s) {
    const c = await confirmDialog({ title: `Prepare ${s.name} for Shiplane?`, message: 'Installs the tools Shiplane needs and the Caddy web server, and creates its deploy folders. It does not change SSH, the firewall or existing sites. Safe to run again.', confirmLabel: 'Prepare server', requireText: 'adopt' });
    if (!c.confirmed) return;
    const r = await attempt(api('/api/fleet/adopt', { id: s.id, confirm: 'adopt' }));
    if (!r) return;
    const con = Console({ lines: [] });
    openModal({ title: `Preparing ${s.name}`, body: con.el, size: 'lg' });
    const res = await watchJob(r.job, con);
    toast(res.ok ? 'Server is ready' : 'Failed: read the log', { tone: res.ok ? 'ok' : 'bad' });
    await load();
  }

  function edit(s) {
    const f = { name: Input({ value: s ? s.name : '', placeholder: 'web-1' }), host: Input({ value: s ? s.host : '', placeholder: 'server.example.com', mono: true }), user: Input({ value: s ? s.user : 'root', mono: true }),
      port: Input({ type: 'number', value: String(s ? s.port : 22) }), auth: Select({ value: s ? s.auth : 'key', options: [{ value: 'key', label: 'SSH key' }, { value: 'password', label: 'Password' }] }),
      key: Input({ value: s ? s.key : '~/.ssh/id_ed25519', mono: true }), pw: Input({ type: 'password', placeholder: s && s.hasPassword ? 'saved - leave empty to keep' : '', autocomplete: 'off' }) };
    const m = openModal({ title: s ? `Edit ${s.name}` : 'Add a server', body: h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'Name' }, f.name), Field({ label: 'Host' }, f.host), Field({ label: 'User' }, f.user), Field({ label: 'SSH port' }, f.port),
      Field({ label: 'Sign in with' }, f.auth), Field({ label: 'SSH key path' }, f.key), Field({ label: 'Password' }, f.pw)),
      actions: [Button({ label: 'Save', variant: 'primary', onClick: async () => {
        const r = await attempt(api('/api/fleet/save', { id: s ? s.id : undefined, name: f.name.value, host: f.host.value, user: f.user.value, port: Number(f.port.value), auth: f.auth.value, key: f.key.value, password: f.pw.value }), 'Saved');
        if (r) { m.close(); await load(); }
      } })] });
  }

  const pct = (v) => (v == null ? '-' : `${v}%`);
  function draw() {
    fill(body,
      Card({ title: 'Servers', subtitle: 'The active server is the one every other page manages, and the one uptime checks, backups and auto-heal follow. Switching resets the uptime history.',
        actions: Button({ label: 'Add a server', icon: 'plus', variant: 'primary', size: 'sm', onClick: () => edit(null) }), bodyClass: '' },
        Table({
          columns: [
            { label: 'Server', render: (s) => h('div', {}, h('div', { class: 'font-medium' }, s.name, s.active ? Badge('active', 'ok') : null), h('div', { class: 'font-mono text-xs text-muted' }, `${s.user || ''}@${s.host || 'this computer'}`)) },
            { label: 'Status', render: (s) => (s.ok === false ? Badge('unreachable', 'bad') : s.problems && s.problems.length ? Badge(s.problems[0], 'warn') : Badge('healthy', 'ok')) },
            { label: 'Sites', class: 'text-muted', render: (s) => (s.sites == null ? '-' : s.sites) },
            { label: 'Memory', class: 'text-muted', render: (s) => pct(s.mem) },
            { label: 'Disk', class: 'text-muted', render: (s) => pct(s.disk) },
            { label: '', class: 'text-right', render: (s) => h('div', { class: 'flex flex-wrap justify-end gap-2' },
              s.active ? null : Button({ label: 'Manage', size: 'sm', onClick: async () => { if (await attempt(api('/api/fleet/activate', { id: s.id }), `Now managing ${s.name}`)) { await load(); location.hash = '#/sites'; } } }),
              s.ok !== false && s.caddy === false ? Button({ label: 'Prepare', size: 'sm', variant: 'primary', onClick: () => adopt(s) }) : null,
              Button({ label: 'Edit', size: 'sm', variant: 'ghost', onClick: () => edit(s) }),
              s.active ? null : Button({ icon: 'trash', iconOnly: true, size: 'sm', variant: 'ghost', title: 'Remove from the list', onClick: async () => {
                const c = await confirmDialog({ title: `Remove ${s.name}?`, message: 'Only removes it from this list. The server itself is not touched.', confirmLabel: 'Remove' });
                if (c.confirmed && await attempt(api('/api/fleet/delete', { id: s.id }), 'Removed')) await load();
              } })) },
          ],
          rows, empty: EmptyState({ icon: 'server', title: 'No servers yet', body: 'Add one to start.' }),
        })),
      rows.some((s) => s.problems && s.problems.length > 1) ? Alert({ tone: 'warn', title: 'Some servers need attention' }, rows.filter((s) => s.problems && s.problems.length).map((s) => `${s.name}: ${s.problems.join(', ')}`).join(' | ')) : null);
  }
  load();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' }, PageHeader({ title: 'Servers', description: 'All your servers in one place. Add a fresh one, prepare it with one click, and switch between them.' }), body) };
}
