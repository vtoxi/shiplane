// Domains: one tab per DNS provider (its status in the tab), each listing hostnames and mapping or removing them. Connecting lives on Integrations.
import { h, fill, Logo, icon, Button, Badge, Card, Table, EmptyState, Alert, PageHeader, Field, Input, Select, Textarea, Console, Tabs, Skeleton, busy, confirmDialog, openModal, toast } from '../kit.js';
import { store } from '../store.js';
import { api } from '../api.js';
import { attempt, watchJob } from '../util.js';
import { CloudflarePage } from './cloudflare.js';
import { withHelp } from '../help.js';

const PROVIDERS = [
  { id: 'cloudflare', label: 'Cloudflare', how: 'Tunnel route and proxied DNS record' },
  { id: 'route53', label: 'Amazon Route 53', how: 'A record to this server, served with its own certificate' },
  { id: 'digitalocean', label: 'DigitalOcean', how: 'A record to this server, served with its own certificate' },
];

export function DomainsPage() {
  const sess = store.session;
  const isOn = (id) => (id === 'cloudflare' ? !!sess.cloudflare : (sess.dnsProviders || []).includes(id));
  let tab = (location.hash.match(/[?&]tab=(\w+)/) || [])[1] || (PROVIDERS.find((p) => isOn(p.id)) || PROVIDERS[0]).id;
  const body = h('div', { class: 'space-y-6' });

  function notConnected(p) {
    return Card({}, EmptyState({ icon: 'link', title: `${p.label} is not connected`, body: `${p.how}. Connect your own ${p.label} account to manage its hostnames here.`,
      action: sess.me.role === 'super_admin' ? Button({ label: 'Connect on Integrations', variant: 'primary', href: '#/integrations?tab=dns' }) : null }));
  }

  // Route 53 and DigitalOcean share one view: their A records, and add or remove records that point at this server.
  function providerTab(p) {
    const list = h('div', {}, Skeleton(4));
    const hosts = Textarea({ placeholder: 'app.example.com', rows: 3 });
    const out = Console({ lines: [], empty: 'Results appear here.' });
    const outBox = h('div', { class: 'mt-4', hidden: true }, out.el);
    const show = (lines) => { outBox.hidden = false; out.set(lines); };
    const readHosts = () => hosts.value.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);
    async function load() {
      try {
        const { records } = await api(`/api/dnsproviders/records?provider=${p.id}`);
        fill(list, Table({
          columns: [
            { label: 'Hostname', render: (r) => h('span', { class: 'font-medium' }, r.host) },
            { label: 'Zone', class: 'text-muted', render: (r) => r.zone },
            { label: 'Points to', render: (r) => (r.here ? Badge('this server', 'ok', { dot: true }) : h('code', { class: 'font-mono text-xs text-muted' }, r.values.join(', '))) },
            { label: '', class: 'text-right', render: (r) => (r.here ? Button({ label: 'Remove', icon: 'trash', size: 'sm', variant: 'danger-soft', onClick: async () => {
              const c = await confirmDialog({ title: `Remove the A record of ${r.host}?`, message: 'The site stops being reachable on this name.', confirmLabel: 'Remove record', requireText: r.host });
              if (!c.confirmed) return;
              const res = await attempt(api('/api/dnsproviders/unmap', { hosts: [r.host] }));
              if (res) { show(res.lines); load(); }
            } }) : '') },
          ],
          rows: records,
          empty: EmptyState({ icon: 'globe', title: 'No A records yet', body: 'Add one above, or deploy a site with "Add an A record" on.' }) }));
      } catch (e) { fill(list, Alert({ tone: 'bad', title: `Could not read ${p.label}` }, e.message)); }
    }
    const add = Button({ label: 'Add records', icon: 'link', variant: 'primary', onClick: () => busy(add, async () => {
      const r = await attempt(api('/api/dnsproviders/map', { hosts: readHosts() }));
      if (r) { show(r.lines); load(); }
    }) });
    load();
    return [
      Alert({ tone: 'info', title: 'Served directly' }, 'These names point straight at the server, so ports 80 and 443 must be open and the site must have Direct HTTPS on (deploys with "Add an A record" do this for you).'),
      Card({ title: withHelp('Add records', 'route-53-and-digitalocean'), subtitle: 'One hostname per line. A name that already points somewhere else is never overwritten.' }, Field({ label: 'Hostname(s)' }, hosts), h('div', { class: 'mt-4' }, add), outBox),
      Card({ title: 'A records', subtitle: 'In every zone of this account', bodyClass: '' }, list)];
  }

  // Wildcard certificates: one Let's Encrypt certificate for *.domain, obtained through the DNS provider, used by direct-HTTPS sites.
  function certsTab() {
    const list = h('div', {}, Skeleton(3));
    const form = h('div', {}, Skeleton(2));
    const isSuper = sess.me.role === 'super_admin';
    async function load() {
      let d;
      try { d = await api('/api/wildcards'); } catch (e) { fill(list, Alert({ tone: 'bad', title: 'Could not read the certificates' }, e.message)); fill(form, ''); return; }
      fill(list, Table({
        columns: [
          { label: 'Covers', render: (c) => h('span', { class: 'font-medium' }, `*.${c.zone} and ${c.zone}`) },
          { label: 'Valid until', class: 'text-muted', render: (c) => c.expires || 'unknown' },
          { label: 'Renewal', render: () => Badge('automatic', 'ok', { dot: true }) },
          { label: '', class: 'text-right', render: (c) => (isSuper ? Button({ label: 'Remove', icon: 'trash', size: 'sm', variant: 'danger-soft', onClick: async () => {
            const ok = await confirmDialog({ title: `Remove the wildcard for ${c.zone}?`, message: 'Direct-HTTPS sites under it go back to their own certificates from Caddy (issued on the next visit).', confirmLabel: 'Remove', requireText: c.zone });
            if (ok.confirmed && await attempt(api('/api/wildcards/remove', { zone: c.zone, confirm: c.zone }), 'Removed')) load();
          } }) : '') },
        ],
        rows: d.certs,
        empty: EmptyState({ icon: 'shield', title: 'No wildcard certificates', body: 'Each direct-HTTPS site gets its own certificate until you add one.' }) }));
      if (!isSuper) { fill(form, ''); return; }
      const have = new Set(d.certs.map((c) => c.zone));
      const free = d.zones.filter((z) => !have.has(z.zone));
      if (!free.length) { fill(form, Alert({ tone: 'info', title: d.zones.length ? 'Every connected domain has one' : 'No domain to cover' }, d.zones.length ? 'Nothing left to add.' : 'Connect Cloudflare, Route 53 or DigitalOcean on Integrations first.')); return; }
      const names = { cloudflare: 'Cloudflare', route53: 'Route 53', digitalocean: 'DigitalOcean' };
      const zone = Select({ options: free.map((z) => ({ value: z.zone, label: `${z.zone} (${names[z.provider]})` })), value: free[0].zone });
      const email = Input({ value: /@/.test(sess.me.email || '') ? sess.me.email : '', placeholder: 'you@example.com' });
      const go = Button({ label: 'Get certificate', icon: 'shield', variant: 'primary', onClick: () => busy(go, async () => {
        const r = await attempt(api('/api/wildcards/issue', { zone: zone.value, email: email.value.trim() }));
        if (!r) return;
        const con = Console({ lines: [] });
        openModal({ title: `Wildcard for ${zone.value}`, body: con.el, size: 'lg' });
        const res = await watchJob(r.job, con);
        toast(res.ok ? 'Certificate issued' : 'Not issued: read the log', { tone: res.ok ? 'ok' : 'bad' });
        load();
      }) });
      fill(form, h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'Domain', hint: 'The certificate covers the domain and every name one level under it.' }, zone),
        Field({ label: "E-mail for Let's Encrypt", hint: 'Only used for expiry notices, which renewals normally make unnecessary.' }, email)), h('div', { class: 'mt-4' }, go));
    }
    load();
    return [
      Alert({ tone: 'info', title: 'For sites served directly' }, 'Behind the Cloudflare tunnel, Cloudflare already serves HTTPS. A wildcard helps sites with Direct HTTPS on: one certificate for all of them, no per-site issuance, renewed automatically by Certbot on the server.'),
      Card({ title: withHelp('Add a wildcard certificate', 'wildcard-certificates'), subtitle: "Let's Encrypt checks a DNS record, created through your DNS provider's connection" }, form),
      Card({ title: 'Wildcard certificates', subtitle: 'On the server', bodyClass: '' }, list)];
  }

  function draw() {
    const tabs = PROVIDERS.map((p) => ({ id: p.id, text: `${p.label} · ${isOn(p.id) ? 'connected' : 'not connected'}`,
      label: h('span', { class: 'inline-flex items-center gap-2' }, Logo(p.id, 'h-4 w-4') || icon('cloud', 'h-4 w-4'), p.label, Badge(isOn(p.id) ? 'connected' : 'not connected', isOn(p.id) ? 'ok' : 'neutral')) }));
    tabs.push({ id: 'certs', text: 'Wildcard certificates', label: h('span', { class: 'inline-flex items-center gap-2' }, Logo('letsencrypt', 'h-4 w-4') || icon('shield', 'h-4 w-4'), 'Wildcard certificates') });
    const p = PROVIDERS.find((x) => x.id === tab);
    const content = tab === 'certs' ? certsTab() : !isOn(p.id) ? [notConnected(p)] : p.id === 'cloudflare' ? [CloudflarePage({ embedded: true }).el] : providerTab(p);
    fill(body, Tabs({ tabs, active: tab, onChange: (t) => { tab = t; history.replaceState(null, '', `#/domains?tab=${t}`); draw(); } }), ...content);
  }

  draw();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' },
    PageHeader({ title: 'Domains', description: 'Point hostnames at this server through your DNS providers. Connect providers on Integrations.' }), body) };
}
