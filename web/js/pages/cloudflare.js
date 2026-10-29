import { h, Button, Badge, Card, Table, EmptyState, Alert, PageHeader, Field, Textarea, Console, icon, busy, confirmDialog, Skeleton } from '../kit.js';
import { store } from '../store.js';
import { api } from '../api.js';
import { attempt } from '../util.js';
import { withHelp } from '../help.js';

const routeBadge = (r) => Badge(r === 'missing' ? 'missing' : r, r === 'missing' ? 'bad' : 'ok', { dot: true });
const dnsBadge = (r) => (r.dns === 'conflict' ? Badge('conflict: ' + r.detail, 'warn') : Badge(r.dns, r.dns === 'missing' ? 'bad' : 'ok', { dot: true }));

/** The Cloudflare tab of the Domains page (embedded: no page header of its own). */
export function CloudflarePage({ embedded = false } = {}) {
  const ready = store.session && store.session.cloudflare;
  if (!ready) {
    return { el: h('div', { class: 'space-y-6 animate-fade-up' }, PageHeader({ title: 'Cloudflare', description: 'Connect hostnames to the tunnel.' }),
      Card({}, EmptyState({ icon: 'lock', title: 'Not available on this computer', body: 'Cloudflare mapping needs an API token, which only the owner has. Without it, sites under the wildcard domain still work; for any other domain ask the owner to map it.' }))) };
  }

  let rows = null;
  let error = null;
  let health = null;
  const list = h('div', {});
  const healthBox = h('div', {});
  const hosts = Textarea({ placeholder: 'app.example.com', rows: 3 });
  const out = Console({ lines: [], empty: 'Results appear here.' });
  const outCard = h('div', { class: 'mt-4', hidden: true }, out.el);
  const readHosts = () => hosts.value.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);
  const show = (lines) => { outCard.hidden = false; out.set(lines); };

  async function load() {
    try { rows = (await api('/api/mapping')).rows; error = null; } catch (e) { error = e.message; }
    draw();
  }

  async function verify() {
    healthBox.replaceChildren(h('div', { class: 'skeleton h-14' }));
    try {
      const v = await api('/api/cloudflare/verify');
      healthBox.replaceChildren(h('div', { class: 'flex flex-wrap items-center gap-3' },
        Badge('Token ' + v.token, v.token === 'active' ? 'ok' : 'bad', { dot: true }),
        v.tunnel ? Badge(`Tunnel ${v.tunnel.name}: ${v.tunnel.status}, ${v.tunnel.connections} connections`, v.tunnel.status === 'healthy' ? 'ok' : 'warn', { dot: true }) : Badge('Tunnel: ' + (v.tunnel_error || 'unknown'), 'warn')));
    } catch (e) { healthBox.replaceChildren(Alert({ tone: 'bad', title: 'Could not verify' }, e.message)); }
  }

  // A name that already has its own A/AAAA/CNAME record is never changed automatically: the person must read what goes away and type the name.
  async function replaceRecord(r) {
    const c = await confirmDialog({ title: `Replace the DNS record of ${r.host}?`,
      message: `${r.host} currently points to ${r.detail}. This deletes that record and points the name at this server's tunnel instead. Whatever is served from the old address stops answering on this name (for example another host or an online store). Other records (mail, TXT) are not touched. Write the old value down if you may want it back.`,
      confirmLabel: 'Replace the record', requireText: r.host });
    if (!c.confirmed) return;
    const res = await attempt(api('/api/mapping/replace', { host: r.host, confirm: r.host }));
    if (res) { show(res.lines); await load(); }
  }

  function draw() {
    if (error) { list.replaceChildren(Alert({ tone: 'bad', title: 'Could not read Cloudflare' }, error)); return; }
    if (!rows) { list.replaceChildren(Skeleton(4)); return; }
    list.replaceChildren(Table({
      columns: [
        { label: 'Hostname', render: (r) => h('span', { class: 'font-medium' }, r.host) },
        { label: 'Zone', class: 'text-muted', render: (r) => r.zone },
        { label: 'Tunnel route', render: (r) => routeBadge(r.route) },
        { label: 'DNS', render: (r) => dnsBadge(r) },
        { label: '', class: 'text-right', render: (r) => (r.host.includes('*') ? '' : h('div', { class: 'flex justify-end gap-2' }, r.dns === 'conflict' && store.session.me.role === 'super_admin' ? Button({ label: 'Replace record', icon: 'refresh', size: 'sm', onClick: () => replaceRecord(r) }) : null, Button({ label: 'Remove', icon: 'trash', size: 'sm', variant: 'danger-soft', onClick: async () => {
          const c = await confirmDialog({ title: `Remove the mapping for ${r.host}?`, message: 'The tunnel route and its DNS record are deleted. The site stops being reachable on this name.', confirmLabel: 'Remove mapping', requireText: r.host });
          if (!c.confirmed) return;
          const res = await attempt(api('/api/mapping/unmap', { hosts: [r.host] }));
          if (res) { show(res.lines); await load(); }
        } }))) },
      ],
      rows,
      empty: EmptyState({ icon: 'cloud', title: 'No hostnames mapped yet', body: 'Add one above.' }),
    }));
  }

  const check = Button({ label: 'Check first', onClick: () => busy(check, async () => { const r = await attempt(api('/api/mapping/check', { hosts: readHosts() })); if (r) show(r.text); }) });
  const map = Button({ label: 'Map', icon: 'link', variant: 'primary', onClick: () => busy(map, async () => {
    const r = await attempt(api('/api/mapping/map', { hosts: readHosts() }));
    if (r) { show(r.lines); await load(); }
  }) });

  load();
  verify();
  const refreshBtn = Button({ label: 'Refresh', icon: 'refresh', onClick: () => { load(); verify(); } });
  return { el: h('div', { class: 'space-y-6 animate-fade-up' },
    embedded ? null : PageHeader({ title: 'Cloudflare', description: 'Connect hostnames to the tunnel: a tunnel route plus a proxied DNS record, never overwriting an existing record.', actions: refreshBtn }),
    Card({ title: 'Connection', subtitle: 'Read-only health check of the API token and the tunnel' }, h('div', { class: 'flex flex-wrap items-center justify-between gap-3' }, healthBox, embedded ? refreshBtn : null)),
    Card({ title: withHelp('Add a mapping', 'cloudflare'), subtitle: 'One hostname per line. Two-level names such as a.b.example.com need Cloudflare\'s paid certificate add-on for HTTPS.' },
      Field({ label: 'Hostname(s)' }, hosts), h('div', { class: 'mt-4 flex gap-2' }, check, map), outCard),
    Card({ title: 'Mapped hostnames', subtitle: 'Everything the tunnel currently routes', bodyClass: '' }, list)) };
}
