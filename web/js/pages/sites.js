import { h, Button, Card, Stat, Table, EmptyState, Alert, PageHeader, Segmented, Skeleton, Input, icon, serviceBadge, publicBadge, typeBadge } from '../kit.js';
import { store, subscribe, refresh } from '../store.js';
import { bytes, duration, ago } from '../format.js';
import { navigate } from '../router.js';
import { api } from '../api.js';
import { Spark } from '../live.js';

export const isHealthy = (s) => (!s.has_service || s.service === 'active') && (s.public == null || (typeof s.public === 'number' && s.public >= 200 && s.public < 400));

export function SitesPage() {
  let query = '';
  let filter = 'all';
  let health = {};
  const billing = h('div', {});
  const money = (n, cur) => new Intl.NumberFormat(undefined, { style: 'currency', currency: cur || 'EUR' }).format(n);
  const stats = h('div', { class: 'grid gap-4 sm:grid-cols-2 lg:grid-cols-4' });
  const list = h('div', {});
  const search = Input({ placeholder: 'Search sites...', onInput: (e) => { query = e.target.value.toLowerCase(); draw(); } });
  search.setAttribute('data-search', '');
  search.classList.add('pl-9');
  const seg = Segmented({ label: 'Filter by type', value: 'all', onChange: (v) => { filter = v; draw(); },
    options: [{ value: 'all', label: 'All' }, { value: 'static', label: 'Static' }, { value: 'dotnet', label: '.NET' }, { value: 'node', label: 'Node' }, { value: 'issues', label: 'Needs attention' }] });

  function showOnly(kind) {            // a stat card was clicked: show exactly those sites and bring the list into view
    filter = kind; query = ''; search.value = ''; seg.set(kind); draw();
    list.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function draw() {
    const st = store.status;
    if (!st) {
      stats.replaceChildren(...[0, 1, 2, 3].map(() => h('div', { class: 'card p-4' }, Skeleton(2))));
      list.replaceChildren(store.error ? Alert({ tone: 'bad', title: 'Could not read the server' }, store.error, h('div', { class: 'mt-3' }, Button({ label: 'Try again', icon: 'refresh', size: 'sm', onClick: () => refresh() }))) : h('div', { class: 'p-6' }, Skeleton(4)));
      return;
    }
    const v = st.server;
    const healthy = st.sites.filter(isHealthy).length;
    stats.replaceChildren(
      Stat({ label: 'Sites', value: st.sites.length, hint: `${healthy} healthy`, icon: 'sites', onClick: () => showOnly('all') }),
      Stat({ label: 'Needs attention', value: st.sites.length - healthy, hint: st.sites.length - healthy ? 'Show the sites with problems' : 'All good', icon: 'alert', onClick: () => showOnly('issues') }),
      Stat({ label: 'Memory', value: `${bytes(v.ram_total - v.ram_avail)}`, hint: `of ${bytes(v.ram_total)}`, icon: 'cpu', meter: { value: v.ram_total - v.ram_avail, max: v.ram_total }, href: '#/care?open=memory' }),
      Stat({ label: 'Uptime', value: duration(v.uptime), hint: `load ${v.load.join(' ')}`, icon: 'clock', href: '#/server' }));
    const rows = st.sites.filter((s) => (!query || (s.name + ' ' + s.hostname).toLowerCase().includes(query))
      && (filter === 'all' || (filter === 'issues' ? !isHealthy(s) : s.type === filter)));
    list.replaceChildren(Table({
      columns: [
        { label: 'Site', render: (s) => h('div', { class: 'min-w-0' }, h('p', { class: 'font-medium' }, s.name), h('a', { href: `https://${s.hostname}`, target: '_blank', rel: 'noopener noreferrer', class: 'inline-flex items-center gap-1 text-xs text-muted hover:text-brand', onClick: (e) => e.stopPropagation() }, s.hostname, icon('external', 'h-3 w-3'))) },
        { label: 'Type', render: (s) => typeBadge(s.type, s.framework) },
        { label: 'Service', render: serviceBadge },
        { label: 'Public', render: publicBadge },
        { label: 'Uptime 24h', class: 'hidden md:table-cell', render: (s) => { const x = health[s.name]; return x ? h('div', { class: 'flex items-center gap-2' }, h('div', { class: 'w-16' }, Spark({ times: x.points.map((q) => q[0]), fmt: (v) => (v == null ? 'Down: the check failed' : `Up, answered in ${v} ms`), values: x.points.map((q) => q[1]), height: 20, tone: x.up ? 'text-ok' : 'text-bad' })), h('span', { class: 'text-xs text-muted' }, `${x.uptime}%`)) : h('span', { class: 'text-subtle' }, '-'); } },
        { label: 'Released', class: 'hidden md:table-cell text-muted', render: (s) => ago(s.current) },
        { label: 'Memory', class: 'hidden lg:table-cell text-muted', render: (s) => (s.mem ? bytes(s.mem) : '-') },
        { label: '', class: 'w-8 text-subtle', render: () => icon('chevron') },
      ],
      rows,
      onRowClick: (s) => navigate('/sites/' + s.name),
      empty: st.sites.length ? EmptyState({ icon: 'search', title: 'No sites match', body: 'Try another search or filter.' })
        : EmptyState({ icon: 'rocket', title: 'No sites yet', body: 'Deploy your first site: pick a build folder and press Deploy.', action: Button({ label: 'Deploy a site', icon: 'rocket', variant: 'primary', href: '#/deploy' }) }),
    }));
  }

  const loadHealth = () => api('/api/health').then((r) => { health = r.sites; draw(); }).catch(() => {});
  loadHealth();
  const tm = setInterval(() => { if (!document.hidden) loadHealth(); }, 30000);
  const role = store.session.me.role;
  if (role !== 'viewer') {
    billing.replaceChildren(Card({ title: 'Billing', subtitle: 'Estimates from the provider APIs, not invoices' }, h('div', { class: 'skeleton h-16' })));
    api('/api/billing').then((b) => {
      const hz = b.hetzner, cf = b.cloudflare;
      const hzBody = hz.ready ? h('div', {},
        h('p', { class: 'text-2xl font-semibold tracking-tight' }, money(hz.month_so_far, hz.currency), h('span', { class: 'ml-2 text-sm font-normal text-muted' }, `so far this month, ${money(hz.month_total, hz.currency)} per month in total`)),
        h('ul', { class: 'mt-3 space-y-1 text-sm text-muted' }, hz.servers.map((x) => h('li', {}, `${x.name} (${x.type}, ${x.location}): ${money(x.monthly, hz.currency)}/mo, traffic ${bytes(x.traffic_used)} of ${bytes(x.traffic_included)}`))))
        : h('p', { class: 'text-sm text-muted' }, hz.error ? `Could not read Hetzner (${hz.error}). Check the token.` : h('span', {}, 'Connect Hetzner on ', h('a', { href: '#/integrations?tab=cloud', class: 'text-brand hover:underline' }, 'Integrations, Cloud providers'), ' to see the server cost here.'));
      const cfBody = cf.ready ? (cf.items.length ? h('ul', { class: 'space-y-1 text-sm' }, cf.items.map((i) => h('li', {}, `${i.name}: ${money(i.price, i.currency)} ${i.frequency}${i.renews ? ', renews ' + i.renews : ''}`)))
        : h('p', { class: 'text-sm text-muted' }, 'Free plan. Tunnel and DNS cost nothing.'))
        : h('p', { class: 'text-sm text-muted' }, cf.error === 'HTTP 403' ? 'The token cannot read billing. Add the "Billing: Read" account permission to see paid plans (Tunnel and DNS are free anyway).' : `Not available (${cf.error || 'not configured'}).`);
      billing.replaceChildren(Card({ title: 'Billing', subtitle: 'Estimates from the provider APIs, not invoices' },
        h('div', { class: 'grid gap-6 md:grid-cols-2' }, h('div', {}, h('h3', { class: 'mb-2 text-xs font-medium uppercase tracking-wide text-muted' }, 'Hetzner'), hzBody), h('div', {}, h('h3', { class: 'mb-2 text-xs font-medium uppercase tracking-wide text-muted' }, 'Cloudflare'), cfBody))));
    }).catch(() => billing.replaceChildren());
  }
  const unsub = subscribe(draw);
  draw();
  if (!store.status) refresh();
  const el = h('div', { class: 'space-y-6 animate-fade-up' },
    PageHeader({ title: 'Sites', description: `Everything running on ${store.session ? store.session.server.host || 'this server' : 'the server'}.`,
      actions: [Button({ label: 'Refresh', icon: 'refresh', onClick: () => refresh() }), Button({ label: 'Deploy a site', icon: 'rocket', variant: 'primary', href: '#/deploy' })] }),
    stats,
    billing,
    h('section', { class: 'card' },
      h('div', { class: 'flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3' },
        h('div', { class: 'relative w-full sm:w-72' }, h('span', { class: 'pointer-events-none absolute left-3 top-2.5 text-subtle' }, icon('search', 'h-4 w-4')), search), seg),
      list));
  return { el, destroy: () => { unsub(); clearInterval(tm); } };
}
