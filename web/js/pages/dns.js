// Your own nameservers: set them up, edit zones and records, open the firewall (confirmed), and let sites answer on 80/443 directly.
import { h, fill, Alert, Badge, Button, Card, Console, EmptyState, Field, Input, PageHeader, Select, Skeleton, Table, Tabs, busy, confirmDialog, toast } from '../kit.js';
import { store, refresh } from '../store.js';
import { api } from '../api.js';
import { attempt, watchJob } from '../util.js';
import { NeedsBar } from '../needs.js';
import { withHelp } from '../help.js';

const TYPES = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS'];

export function DnsPage() {
  const body = h('div', { class: 'space-y-6' });
  let tab = (location.hash.match(/[?&]tab=(\w+)/) || [])[1] || 'setup', info = null, status = null, zone = '', zdata = null;

  async function load() {
    fill(body, Skeleton(4));
    try { info = await api('/api/dns'); status = await api('/api/dns/status').catch(() => null); if (!zone && info.zones.length) zone = info.zones[0].domain; zdata = zone ? await api(`/api/dns/zone?domain=${encodeURIComponent(zone)}`) : null; } catch (e) { fill(body, Alert({ tone: 'bad', title: 'Could not read the DNS settings' }, e.message)); return; }
    draw();
  }

  const light = (label, ok, note) => h('div', { class: 'flex items-center justify-between gap-3 py-1.5 text-sm' }, h('span', {}, label), Badge(note, ok === true ? 'ok' : ok === false ? 'bad' : 'neutral', { dot: true }));

  function setup() {
    const ns1 = Input({ value: info.ns[0] || '', placeholder: 'ns1.example.com', mono: true });
    const ns2 = Input({ value: info.ns[1] || '', placeholder: 'ns2.example.com (advised)', mono: true });
    const admin = Input({ value: info.admin || '', placeholder: 'you@example.com (optional)' });
    const save = Button({ label: 'Save nameservers', variant: 'primary', onClick: () => busy(save, async () => { if (await attempt(api('/api/dns/nameservers', { ns: [ns1.value, ns2.value], admin: admin.value }), 'Saved')) load(); }) });
    const job = h('div', {});
    const apply = Button({ label: 'Apply to the server', icon: 'rocket', variant: 'primary', disabled: !info.ns.length || !info.zones.length, onClick: () => busy(apply, async () => {
      const r = await attempt(api('/api/dns/apply', {}));
      if (!r) return;
      const con = Console({ empty: 'Starting...' });
      fill(job, Card({ title: 'Applying', subtitle: 'Installs NSD the first time, checks every zone, then reloads it' }, con.el));
      const done = await watchJob(r.job, con);
      toast(done.ok ? 'DNS is being served' : 'It did not finish: read the output', { tone: done.ok ? 'ok' : 'bad', timeout: 6000 });
      load();
    }) });
    const open53 = Button({ label: 'Open port 53', size: 'sm', onClick: () => openPorts('dns', 'Open port 53 (DNS)?', 'Port 53 (TCP and UDP) becomes reachable from the whole internet. That is how nameservers work: anyone may ask them for your records. The DNS server only answers for your zones.') });
    return h('div', { class: 'space-y-6' },
      Alert({ tone: 'warn', title: 'Read this before you point a domain here' },
        h('ul', { class: 'list-disc space-y-1 pl-5' },
          h('li', {}, 'One server is one point of failure: while it is down, every domain on it is unreachable and mail bounces. Use a second nameserver (a free secondary service or a second server) when it matters.'),
          h('li', {}, 'A domain whose nameservers move here leaves Cloudflare: no tunnel, no proxy, no free certificate. Sites then answer directly (see Direct HTTPS) and the server address becomes public.'),
          h('li', {}, 'Nothing changes on the server until you press Apply, and the firewall only changes when you confirm it here.'))),
      NeedsBar('nsd', 'The DNS server program has to be on the server before zones can be served. Installing it does not start serving anything.', load),
      Card({ title: 'This server', subtitle: 'What the server reports right now' }, status ? h('div', { class: 'divide-y divide-line' },
        light('Server address', null, status.ip || 'unknown'),
        light('DNS server (NSD)', status.nsd === 'active', status.nsd === 'missing' ? 'not installed yet' : status.nsd),
        light('Listening on port 53', status.listening, status.listening ? 'yes' : 'no'),
        light('Firewall', null, status.firewall),
        light('Port 53 open', status.open['53'] === 'allowed', status.open['53'])) : Alert({ tone: 'info' }, 'Could not read the server status.'),
      status && status.open['53'] !== 'allowed' ? h('div', { class: 'mt-3' }, open53) : null),
      Card({ title: 'Your nameservers', subtitle: 'The names your domains will list as their nameservers' },
        h('div', { class: 'space-y-4' }, h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'First nameserver' }, ns1), Field({ label: 'Second nameserver' }, ns2)),
          Field({ label: 'Contact address', hint: 'Goes into the zone data (SOA) so others can reach you about DNS problems.' }, admin), h('div', {}, save))),
      Card({ title: 'Put it on the server', subtitle: 'Writes the zones and starts or reloads NSD. A zone that does not check out is never installed.' }, h('div', { class: 'space-y-3' }, apply, job)),
      Card({ title: 'Then, at your domain registrar', subtitle: 'The two steps only you can do' },
        h('ol', { class: 'list-decimal space-y-1.5 pl-5 text-sm text-muted' },
          h('li', {}, `Create "glue" records (also called host records or child nameservers) for ${info.ns.join(' and ') || 'your nameservers'}, pointing to ${status && status.ip ? status.ip : 'this server\'s address'}. Needed when the nameserver is inside the domain it serves.`),
          h('li', {}, 'Change the domain\'s nameservers to those names. It can take up to 48 hours to spread. Check with: dig NS yourdomain.com'),
          h('li', {}, 'Before you switch, copy every record you need (mail, verification TXT, ...) from the old DNS into the Zones tab.'))));
  }

  async function openPorts(which, title, message) {
    const c = await confirmDialog({ title, message, confirmLabel: 'Open the port', requireText: 'open' });
    if (!c.confirmed) return;
    const r = await attempt(api('/api/dns/ports', { which, confirm: 'open' }));
    if (r) { toast(r.lines.join(', ') || 'Done', { tone: 'ok' }); load(); }
  }

  function zones() {
    const domain = Input({ placeholder: 'example.com', mono: true });
    const ip = Input({ value: status ? status.ip : '', placeholder: 'server address', mono: true });
    const add = Button({ label: 'Add zone', variant: 'primary', onClick: () => busy(add, async () => { const r = await attempt(api('/api/dns/zone', { domain: domain.value, ip: ip.value }), 'Zone added'); if (r) { zone = r.domain; load(); } }) });
    const addCard = Card({ title: 'Add a domain', subtitle: info.ns.length ? 'Starts with your nameservers and the bare name and www at this server' : 'Set your nameservers on the Setup tab first' },
      h('div', { class: 'grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end' }, Field({ label: 'Domain' }, domain), Field({ label: 'Server address' }, ip), add));
    if (!info.zones.length) return h('div', { class: 'space-y-6' }, addCard);
    const pick = Select({ options: info.zones.map((z) => ({ value: z.domain, label: z.domain })), value: zone, onChange: (v) => { zone = v; load(); } });
    const f = { name: Input({ placeholder: '@ or www', mono: true }), type: Select({ options: TYPES.map((t) => ({ value: t, label: t })), value: 'A' }), value: Input({ placeholder: 'value', mono: true }), prio: Input({ placeholder: 'priority', type: 'number' }), ttl: Input({ value: '300', type: 'number' }) };
    let editing = '';
    const save = Button({ label: 'Save record', variant: 'primary', onClick: () => busy(save, async () => {
      const r = await attempt(api('/api/dns/record', { domain: zone, id: editing, name: f.name.value || '@', type: f.type.value, value: f.value.value, prio: f.prio.value, ttl: f.ttl.value }), 'Saved');
      if (r) load();
    }) });
    const table = Table({ columns: [
      { label: 'Name', render: (r) => h('span', { class: 'font-mono text-xs' }, r.name) },
      { label: 'Type', render: (r) => Badge(r.type, 'neutral') },
      { label: 'Value', render: (r) => h('span', { class: 'break-all font-mono text-xs' }, (r.prio != null ? r.prio + ' ' : '') + r.value) },
      { label: 'TTL', class: 'text-muted', render: (r) => String(r.ttl) },
      { label: '', class: 'text-right', render: (r) => h('div', { class: 'flex justify-end gap-2' },
        Button({ label: 'Edit', size: 'sm', onClick: () => { editing = r.id; f.name.value = r.name; f.type.value = r.type; f.value.value = r.value; f.prio.value = r.prio != null ? r.prio : ''; f.ttl.value = String(r.ttl); f.value.focus(); } }),
        Button({ label: 'Delete', size: 'sm', variant: 'danger-soft', onClick: async () => {
          const c = await confirmDialog({ title: `Delete ${r.type} ${r.name}?`, message: 'The record is removed from the zone. It stops being served after the next Apply.', confirmLabel: 'Delete record' });
          if (c.confirmed && await attempt(api('/api/dns/record/delete', { domain: zone, id: r.id }), 'Deleted')) load();
        } })) }],
      rows: zdata.records, empty: EmptyState({ title: 'No records' }) });
    return h('div', { class: 'space-y-6' }, addCard,
      Card({ title: 'Records', subtitle: `Changes are saved here at once and go live when you press Apply on the Setup tab. Serial ${zdata.serial}.`, actions: h('div', { class: 'w-56' }, pick) }, table),
      Card({ title: 'Add or change a record', subtitle: 'Names are relative to the domain: @ is the domain itself. MX needs a priority; text values cannot contain quotes.' },
        h('div', { class: 'grid gap-4 sm:grid-cols-6' }, h('div', { class: 'sm:col-span-1' }, Field({ label: 'Name' }, f.name)), h('div', {}, Field({ label: 'Type' }, f.type)), h('div', { class: 'sm:col-span-2' }, Field({ label: 'Value' }, f.value)), Field({ label: 'Priority (MX)' }, f.prio), Field({ label: 'TTL (seconds)' }, f.ttl)),
        h('div', { class: 'mt-4 flex flex-wrap gap-2' }, save, Button({ label: 'Clear', onClick: () => { editing = ''; Object.values(f).forEach((x) => { if (x !== f.type && x !== f.ttl) x.value = ''; }); } }),
          Button({ label: 'Delete this zone', variant: 'danger-soft', onClick: async () => {
            const c = await confirmDialog({ title: `Delete the zone ${zone}?`, message: 'All its records are removed from Shiplane. The server stops answering for it after the next Apply.', confirmLabel: 'Delete zone', requireText: zone });
            if (c.confirmed && await attempt(api('/api/dns/zone/delete', { domain: zone, confirm: zone }), 'Zone deleted')) { zone = ''; load(); }
          } }))));
  }

  function direct() {
    const sites = ((store.status && store.status.sites) || []).filter((s) => s.name !== 'coderunner' && s.hostname);
    const flip = async (s, on) => {
      const c = await confirmDialog({ title: `${on ? 'Serve' : 'Stop serving'} ${s.hostname} directly?`,
        message: on ? `${s.hostname} will also answer on https:// by itself: Caddy asks Let's Encrypt for a certificate, which only works once the name points at this server and ports 80 and 443 are open. The tunnel keeps working.` : `${s.hostname} goes back to answering only through the tunnel.`,
        confirmLabel: on ? 'Turn on' : 'Turn off', requireText: s.name });
      if (!c.confirmed) return;
      if (await attempt(api('/api/dns/direct', { name: s.name, on, confirm: s.name }), on ? 'Turned on' : 'Turned off')) { await refresh({ quiet: true }); load(); }
    };
    return h('div', { class: 'space-y-6' },
      Alert({ tone: 'warn', title: 'Before you serve sites directly' }, 'Open ports 80 and 443 only when you need them. With 80 open, every site address is reachable over plain HTTP by anyone who knows the server address, so keep the portal behind the tunnel (this app never offers it here) and give each public site its own domain.'),
      Card({ title: 'Firewall', subtitle: 'Ports 80 and 443 are closed unless you open them here' }, status ? h('div', { class: 'space-y-3' },
        light('Port 80 (HTTP)', status.open['80'] === 'allowed', status.open['80']), light('Port 443 (HTTPS)', status.open['443'] === 'allowed', status.open['443']),
        Button({ label: 'Open ports 80 and 443', size: 'sm', onClick: () => openPorts('web', 'Open ports 80 and 443?', 'Web traffic can then reach this server directly, without Cloudflare in front. Certificates are issued by Caddy for the sites you switch to direct.') })) : Alert({ tone: 'info' }, 'Could not read the firewall.')),
      Card({ title: 'Sites', subtitle: 'Each site can also answer by itself on https://, next to the tunnel' }, sites.length ? Table({ columns: [
        { label: 'Site', render: (s) => h('span', { class: 'font-medium' }, s.name) }, { label: 'Address', class: 'text-muted', render: (s) => s.hostname },
        { label: 'Direct HTTPS', render: (s) => (s.meta && s.meta.direct ? Badge('on', 'ok', { dot: true }) : Badge('off', 'neutral')) },
        { label: '', class: 'text-right', render: (s) => (s.meta && s.meta.direct ? Button({ label: 'Turn off', size: 'sm', onClick: () => flip(s, false) }) : Button({ label: 'Turn on', size: 'sm', variant: 'primary', onClick: () => flip(s, true) })) }],
        rows: sites }) : EmptyState({ title: 'No sites yet' })));
  }

  function draw() {
    const panes = { setup, zones, direct };
    fill(body, Tabs({ tabs: [{ id: 'setup', label: 'Setup' }, { id: 'zones', label: 'Zones', count: info.zones.length }, { id: 'direct', label: 'Direct HTTPS' }], active: tab, onChange: (t) => { tab = t; history.replaceState(null, '', `#/dns?tab=${t}`); draw(); } }), h('div', { class: 'pt-6' }, panes[tab]()));
  }

  (store.status ? Promise.resolve() : refresh({ quiet: true })).finally(load);
  return { el: h('div', { class: 'space-y-6 animate-fade-up' },
    PageHeader({ title: withHelp('DNS hosting', 'your-own-nameservers-dns'), description: 'Run your own nameservers on this server, edit your records, and serve sites directly over HTTPS.', actions: Button({ label: 'Refresh', icon: 'refresh', onClick: load }) }), body) };
}
