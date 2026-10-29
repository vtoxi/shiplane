// Server care: a security and health checklist. Each card explains itself, opens to show what is behind it (banned addresses, biggest folders,
// memory users ...) and offers the actions that belong there. Fixes say exactly what they will change before they run.
import { h, icon, fill, Button, Badge, Card, Alert, PageHeader, Console, Skeleton, Input, Meter, Table, confirmDialog, busy } from '../kit.js';
import { store, refresh } from '../store.js';
import { api } from '../api.js';
import { bytes } from '../format.js';
import { attempt, watchJob } from '../util.js';
import { withHelp } from '../help.js';

const TONE = { ok: 'ok', warn: 'warn', bad: 'bad' };
const WORD = { ok: 'Good', warn: 'Could be better', bad: 'Fix this' };
const ICON = { ok: ['check', 'text-ok'], warn: ['alert', 'text-warn'], bad: ['alert', 'text-bad'] };
// What each fix changes, in plain words. `typed` fixes could lock you out if misused, so the word "apply" must be typed.
const FIX = {
  ssh_keys_only: { typed: true, what: 'Turns off SSH password login. Only people with an SSH key can log in afterwards. The change is checked before it is applied and undone if the server rejects it.', tip: 'Keep your current SSH session open until you have confirmed that your key still works.' },
  ssh_no_root: { typed: false, what: 'Root can still log in with an SSH key, but no longer with a password.' },
  firewall: { typed: true, what: 'Installs and switches on the firewall (ufw). Your SSH port and any web ports (80/443) that are open now stay open; everything else from outside is blocked.', tip: 'SSH is allowed before the firewall is switched on, so you will not be locked out.' },
  fail2ban: { typed: false, what: 'Installs fail2ban: 5 failed SSH logins within 10 minutes ban that address for one hour.' },
  auto_updates: { typed: false, what: 'Installs security updates automatically every day. The server never restarts by itself: you decide when (Server page).' },
  time_sync: { typed: false, what: 'Switches on automatic clock synchronisation.' },
  swap: { typed: false, what: 'Creates a swap file (1 to 2 GB, depending on memory) and sets the server to use it only when memory runs short. It checks first that at least 5 GB of disk stays free.' },
};
// Each check belongs to a topic with its own icon; the page groups by topic so related checks sit together.
const TOPICS = [
  { id: 'access', label: 'Access', icon: 'lock', ids: ['ssh_password', 'ssh_root', 'fail2ban'] },
  { id: 'network', label: 'Network', icon: 'shield', ids: ['firewall', 'exposed'] },
  { id: 'updates', label: 'Updates', icon: 'refresh', ids: ['auto_updates', 'reboot'] },
  { id: 'resources', label: 'Resources', icon: 'disk', ids: ['disk', 'memory', 'swap'] },
  { id: 'system', label: 'System', icon: 'clock', ids: ['time_sync'] },
];
const topicOf = (id) => TOPICS.find((t) => t.ids.includes(id)) || TOPICS[TOPICS.length - 1];
const RANK = { bad: 0, warn: 1, ok: 2 };

/** The score as a ring: the coloured arc is the share of 100. */
function ScoreRing(score, toneVar) {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 120 120');
  svg.setAttribute('class', 'shrink-0');
  svg.setAttribute('width', '112');
  svg.setAttribute('height', '112');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', `Score ${score} out of 100`);
  const circ = 2 * Math.PI * 52;
  const ring = (stroke, dash) => {
    const c = document.createElementNS(NS, 'circle');
    Object.entries({ cx: 60, cy: 60, r: 52, fill: 'none', 'stroke-width': 10, stroke, 'stroke-linecap': 'round', transform: 'rotate(-90 60 60)' }).forEach(([k, v]) => c.setAttribute(k, v));
    if (dash != null) c.setAttribute('stroke-dasharray', `${dash} ${circ}`);
    return c;
  };
  const text = document.createElementNS(NS, 'text');
  Object.entries({ x: 60, y: 60, 'text-anchor': 'middle', 'dominant-baseline': 'central', 'font-size': 30, 'font-weight': 600, fill: 'currentColor' }).forEach(([k, v]) => text.setAttribute(k, v));
  text.textContent = String(score);
  svg.append(ring('rgb(var(--c-line))'), ring(`rgb(var(${toneVar}))`, (circ * score) / 100), text);
  return svg;
}

const ADVICE = { disk: ['#/maintenance?tab=disk', 'Open disk cleanup'], reboot: ['#/server', 'Open the Server page'] };
const mb = (n) => bytes(n * 1048576);
const bar = (value, max) => Meter({ value, max, warnAt: 101, badAt: 101 });

export function CarePage({ query = {} } = {}) {
  let data = null;
  const open = new Set(query.open ? [query.open] : []);
  const cache = {};
  const canAct = () => store.session && store.session.me && store.session.me.role === 'super_admin';
  const head = h('div', {});
  const body = h('div', { class: 'space-y-4' }, Skeleton(4));
  const log = h('div', {});

  async function load() {
    Object.keys(cache).forEach((k) => delete cache[k]);
    fill(body, Skeleton(4));
    try { data = await api('/api/care'); draw(); } catch (e) { fill(body, Alert({ tone: 'bad', title: 'Could not check the server' }, e.message)); }
  }

  async function runJob(title, start) {
    const r = await attempt(start);
    if (!r) return;
    const con = Console({ empty: 'Starting...' });
    fill(log, Card({ title, subtitle: 'Live output from the server' }, con.el));
    const done = await watchJob(r.job, con);
    con.push([done.ok ? 'Finished. Checking the server again...' : 'It did not finish. Nothing else was changed; read the output above.']);
    await load();
  }

  async function fix(f) {
    const info = FIX[f.fix.id];
    const c = await confirmDialog({ title: f.fix.label + '?', message: info.what + (info.tip ? ' ' + info.tip : ''), confirmLabel: f.fix.label, tone: 'primary', requireText: info.typed ? 'apply' : undefined });
    if (c.confirmed) await runJob(f.fix.label, api('/api/care/fix', { fix: f.fix.id, confirm: 'apply' }));
  }

  async function act(action, ip, panel, finding) {
    const c = await confirmDialog({ title: `${action === 'ban' ? 'Ban' : 'Unban'} ${ip}?`, message: action === 'ban' ? 'This address can no longer reach SSH until you unban it.' : 'This address may try to log in again.', confirmLabel: action === 'ban' ? 'Ban' : 'Unban', tone: 'primary' });
    if (!c.confirmed) return;
    if (await attempt(api('/api/care/action', { action, ip, confirm: 'apply' }), (r) => r.text)) { delete cache[finding]; await showDetail(finding, panel); }
  }

  // ------------------------------------------------------------ detail views
  const sectionTitle = (t) => h('p', { class: 'mb-2 text-xs font-medium uppercase tracking-wide text-muted' }, t);
  const none = (t) => h('p', { class: 'text-sm text-muted' }, t);

  const VIEWS = {
    fail2ban(d, panel, id) {
      if (!d.installed) return none('fail2ban is not running, so nobody is banned.');
      const add = Input({ placeholder: '203.0.113.7', mono: true });
      return h('div', { class: 'space-y-4' },
        h('p', { class: 'text-sm' }, `${d.banned.length} banned now. ${d.total_banned} banned and ${d.total_failed} failed logins since fail2ban started; ${d.currently_failed} failing right now.`),
        d.banned.length ? Table({ columns: [{ label: 'Banned address', class: 'font-mono text-xs', render: (ip) => ip },
          { label: '', class: 'text-right', render: (ip) => (canAct() ? Button({ label: 'Unban', size: 'sm', onClick: () => act('unban', ip, panel, id) }) : '') }], rows: d.banned }) : none('Nobody is banned at the moment.'),
        canAct() ? h('div', { class: 'flex items-end gap-2' }, h('div', { class: 'w-56' }, add), Button({ label: 'Ban an address', size: 'sm', onClick: () => act('ban', add.value, panel, id) })) : null);
    },
    disk(d, panel, id) {
      const crumbs = d.path === '/' ? ['/'] : ['/', ...d.path.split('/').filter(Boolean)];
      const go = async (p) => { cache[id + ':path'] = p; delete cache[id]; await showDetail(id, panel); };
      return h('div', { class: 'space-y-4' },
        d.mounts.length ? h('div', {}, sectionTitle('Disks'), h('ul', { class: 'space-y-1 text-sm' }, d.mounts.map((m) => h('li', { class: 'flex justify-between gap-3' }, h('span', { class: 'font-mono text-xs' }, m.mount), h('span', { class: 'text-muted' }, `${m.used} of ${m.size} (${m.pct}%)`))))) : null,
        h('div', {}, sectionTitle('Biggest folders in'),
          h('div', { class: 'mb-3 flex flex-wrap items-center gap-1 text-sm' }, crumbs.map((c, i) => [i ? h('span', { class: 'text-subtle' }, '/') : null,
            h('button', { type: 'button', class: 'text-brand hover:underline', onClick: () => go(i === 0 ? '/' : '/' + crumbs.slice(1, i + 1).join('/')) }, c)])),
          d.items.length ? h('ul', { class: 'space-y-2' }, d.items.map((i) => h('li', {},
            h('button', { type: 'button', class: 'flex w-full items-center justify-between gap-3 text-left text-sm hover:text-brand', onClick: () => go(i.path), title: 'Open this folder' }, h('span', { class: 'min-w-0 truncate font-mono text-xs' }, i.path), h('span', { class: 'shrink-0 text-muted' }, bytes(i.kb * 1024))),
            bar(i.kb, d.items[0].kb)))) : none(d.partial ? 'The scan took too long and was stopped to protect your sites. Open a smaller folder.' : 'Nothing found here.')),
        h('div', { class: 'flex flex-wrap gap-2' }, Button({ label: 'Open disk cleanup', size: 'sm', href: '#/maintenance?tab=disk' })));
    },
    memory(d, panel, id) {
      const sites = ((store.status && store.status.sites) || []).filter((s) => s.has_service && s.mem).sort((a, b) => b.mem - a.mem).slice(0, 8);
      return h('div', { class: 'space-y-4' },
        h('p', { class: 'text-sm' }, `${mb(d.used_mb)} used of ${mb(d.total_mb)}, ${mb(d.available_mb)} available. Swap: ${d.swap_total_mb ? `${mb(d.swap_used_mb)} of ${mb(d.swap_total_mb)}` : 'none'}.`),
        h('div', {}, sectionTitle('Biggest programs'), d.processes.length ? h('ul', { class: 'space-y-2' }, d.processes.map((p) => h('li', {},
          h('div', { class: 'flex justify-between gap-3 text-sm' }, h('span', { class: 'min-w-0 truncate' }, p.name, h('span', { class: 'ml-2 text-xs text-muted' }, p.user)), h('span', { class: 'shrink-0 text-muted' }, `${mb(p.rss_mb)}, ${p.cpu}% CPU`)),
          bar(p.rss_mb, d.processes[0].rss_mb)))) : none('Could not read the process list.')),
        sites.length ? h('div', {}, sectionTitle('Your sites by memory'), h('ul', { class: 'space-y-2' }, sites.map((s) => h('li', { class: 'flex items-center justify-between gap-3 text-sm' },
          h('a', { href: `#/sites/${s.name}`, class: 'hover:text-brand hover:underline' }, s.name), h('span', { class: 'flex items-center gap-3' }, h('span', { class: 'text-muted' }, bytes(s.mem)),
            canAct() ? Button({ label: 'Restart', icon: 'refresh', size: 'sm', onClick: async () => { if (await attempt(api('/api/action', { name: s.name, action: 'restart' }), `Restarted ${s.name}`)) { await refresh({ quiet: true }); delete cache[id]; showDetail(id, panel); } } }) : null))))) : null,
        h('div', { class: 'flex flex-wrap gap-2' }, Button({ label: 'Server page', size: 'sm', href: '#/server' })));
    },
    ssh(d, panel, id) {
      return h('div', { class: 'space-y-3' }, h('p', { class: 'text-sm' }, d.failed_24h ? `${d.failed_24h} failed login attempts in the last 24 hours.` : 'No failed login attempts in the last 24 hours.'),
        d.top.length ? Table({ columns: [{ label: 'Address', class: 'font-mono text-xs', render: (r) => r.ip }, { label: 'Attempts', class: 'text-muted', render: (r) => r.count },
          { label: '', class: 'text-right', render: (r) => (canAct() ? Button({ label: 'Ban', size: 'sm', onClick: () => act('ban', r.ip, panel, id) }) : '') }], rows: d.top }) : null);
    },
    firewall: (d) => h('div', { class: 'space-y-2' }, h('p', { class: 'text-sm' }, d.active ? 'Rules, in the order they are checked:' : 'The firewall is off.'), d.rules.length ? h('pre', { class: 'console max-h-64' }, d.rules.join('\n')) : null),
    updates(d) {
      return h('div', { class: 'space-y-3' }, h('p', { class: 'text-sm' }, d.count ? `${d.count} package${d.count === 1 ? '' : 's'} can be updated${d.packages.length < d.count ? ' (first ' + d.packages.length + ' shown)' : ''}.` : 'Everything is up to date.'),
        d.packages.length ? h('pre', { class: 'console max-h-48' }, d.packages.join('\n')) : null,
        d.reboot_packages.length ? Alert({ tone: 'warn', title: 'A restart is waiting for' }, d.reboot_packages.join(', ')) : null,
        h('div', { class: 'flex flex-wrap gap-2' }, d.count && canAct() ? Button({ label: 'Install updates now', icon: 'download', variant: 'primary', size: 'sm', onClick: async () => {
          const c = await confirmDialog({ title: 'Install all updates now?', message: 'Packages are upgraded in place. Services using them restart; the server itself does not.', confirmLabel: 'Install', tone: 'primary' });
          if (c.confirmed) await runJob('Installing updates', api('/api/server/upgrade', {}));
        } }) : null, Button({ label: 'Restart from the Server page', size: 'sm', href: '#/server' })));
    },
    exposed: (d) => h('div', { class: 'space-y-2' }, h('p', { class: 'text-sm' }, 'Everything that listens on a public address:'),
      Table({ columns: [{ label: 'Port', render: (l) => String(l.port) }, { label: 'Program', class: 'text-muted', render: (l) => l.proc || 'unknown' }, { label: 'Address', class: 'font-mono text-xs text-muted', render: (l) => l.addr },
        { label: '', render: (l) => (d.risky[l.port] ? Badge(`${d.risky[l.port]}: should not be public`, 'bad') : '') }], rows: d.listening, empty: none('Nothing is listening publicly.') })),
    time: (d) => h('pre', { class: 'console max-h-48' }, d.text || 'No details.'),
  };

  async function showDetail(id, panel) {
    fill(panel, Skeleton(3));
    try {
      const path = id === 'disk' ? cache['disk:path'] || '/' : '';
      cache[id] = cache[id] || await api(`/api/care/detail?id=${encodeURIComponent(id)}${path ? '&path=' + encodeURIComponent(path) : ''}`);
      fill(panel, VIEWS[cache[id].kind](cache[id], panel, id));
    } catch (e) { fill(panel, Alert({ tone: 'bad', title: 'Could not load the details' }, e.message)); }
  }

  // ----------------------------------------------------------------- cards
  function finding(f) {
    const [ic, cls] = ICON[f.status];
    const advice = ADVICE[f.id];
    const panel = h('div', { class: 'mt-4 border-t border-line pt-4', hidden: !open.has(f.id) });
    const chevron = h('span', { class: 'shrink-0 text-subtle transition-transform' }, icon('chevron', 'h-4 w-4'));
    const paint = () => { chevron.style.transform = open.has(f.id) ? 'rotate(90deg)' : ''; panel.hidden = !open.has(f.id); };
    const toggle = () => { if (open.has(f.id)) open.delete(f.id); else { open.add(f.id); showDetail(f.id, panel); } paint(); };
    const action = f.fix
      ? (canAct() ? h('div', { class: 'mt-3 flex flex-wrap items-center gap-3' },
        (() => { const b = Button({ label: f.fix.label, icon: 'shield', variant: f.status === 'bad' ? 'primary' : 'secondary', size: 'sm', disabled: !!f.fix.blocked, onClick: () => busy(b, () => fix(f)) }); return b; })(),
        f.fix.blocked ? h('p', { class: 'text-xs text-muted' }, f.fix.blocked) : null) : h('p', { class: 'mt-3 text-xs text-muted' }, 'Ask a super admin to apply the fix.'))
      : (f.status !== 'ok' && advice ? h('div', { class: 'mt-3' }, Button({ label: advice[1], size: 'sm', href: advice[0] })) : null);
    const topic = topicOf(f.id);
    const card = h('section', { class: 'card', id: 'care-' + f.id }, h('div', { class: 'card-body' },
      h('div', { class: 'flex items-start gap-3' },
        h('span', { class: 'relative mt-0.5 flex shrink-0 items-center justify-center rounded-lg bg-sunken text-muted', style: 'width:2.25rem;height:2.25rem', title: topic.label },
          icon(topic.icon, 'h-4 w-4'), h('span', { class: 'absolute rounded-full bg-surface ' + cls, style: 'right:-4px;bottom:-4px' }, icon(ic, 'h-3.5 w-3.5'))),
        h('div', { class: 'min-w-0 flex-1' },
          h('button', { type: 'button', class: 'flex w-full items-start justify-between gap-3 text-left', 'aria-expanded': String(open.has(f.id)), onClick: (e) => { toggle(); e.currentTarget.setAttribute('aria-expanded', String(open.has(f.id))); } },
            h('span', { class: 'min-w-0' }, h('span', { class: 'block text-sm font-medium' }, f.title), h('span', { class: 'mt-1 block text-sm' }, f.detail),
              f.status !== 'ok' ? h('span', { class: 'mt-1 block text-xs text-muted' }, f.why) : null),
            h('span', { class: 'flex shrink-0 flex-col items-end gap-1' }, h('span', { class: 'flex items-center gap-2' }, Badge(WORD[f.status], TONE[f.status], { dot: true }), chevron),
              f.status !== 'ok' && f.weight ? h('span', { class: 'text-xs text-muted tabular-nums' }, `-${f.status === 'bad' ? f.weight : f.weight / 2} points`) : null)),
          action, panel))));
    paint();
    if (open.has(f.id)) { showDetail(f.id, panel); if (query.open === f.id) setTimeout(() => card.scrollIntoView({ block: 'center' }), 0); }
    return card;
  }

  function draw() {
    const toneVar = data.score >= 90 ? '--c-ok' : data.score >= 70 ? '--c-warn' : '--c-bad';
    const count = (st) => data.findings.filter((f) => f.status === st).length;
    const problems = data.findings.filter((f) => f.status !== 'ok').sort((a, b) => RANK[a.status] - RANK[b.status] || b.weight - a.weight);
    const good = data.findings.filter((f) => f.status === 'ok');
    fill(head, PageHeader({ title: withHelp('Server care', 'server-care-security-early-warnings-and-self-repair'), description: `Security and health of ${data.os}. Checked just now; nothing runs in the background. Open any check for details.`,
      actions: Button({ label: 'Check again', icon: 'refresh', onClick: load }) }));
    const stat = (n, label, tone, ic) => h('div', { class: 'flex items-center gap-2 rounded-lg bg-sunken px-3 py-2' }, h('span', { class: 'text-' + tone }, icon(ic, 'h-4 w-4')),
      h('span', { class: 'text-lg font-semibold tabular-nums' }, String(n)), h('span', { class: 'text-xs text-muted' }, label));
    const topicChips = TOPICS.map((t) => {
      const fs = data.findings.filter((f) => t.ids.includes(f.id));
      if (!fs.length) return null;
      const worst = fs.reduce((w, f) => (RANK[f.status] < RANK[w] ? f.status : w), 'ok');
      return h('span', { class: 'inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-xs' }, icon(t.icon, 'h-3.5 w-3.5 text-muted'), t.label,
        h('span', { class: ICON[worst][1] }, icon(ICON[worst][0], 'h-3.5 w-3.5')));
    });
    const goodList = h('div', { class: 'grid gap-4 lg:grid-cols-2' }, ...good.map(finding));
    const goodFold = good.length ? h('details', { class: 'card', open: good.some((f) => open.has(f.id)) },
      h('summary', { class: 'card-header flex cursor-pointer items-center justify-between gap-3', style: 'list-style:none' },
        h('span', { class: 'flex shrink-0 items-center gap-2 whitespace-nowrap text-sm font-medium' }, h('span', { class: 'text-ok' }, icon('check', 'h-4 w-4')), `${good.length} check${good.length === 1 ? '' : 's'} passing`),
        h('span', { class: 'hidden flex-wrap justify-end gap-1.5 sm:flex' }, ...good.map((f) => h('span', { class: 'rounded bg-sunken px-2 py-0.5 text-xs text-muted' }, f.title)))),
      h('div', { class: 'card-body' }, goodList)) : null;
    fill(body,
      data.supported ? null : Alert({ tone: 'warn', title: 'Checks only' }, 'Automatic fixes support Ubuntu and Debian. You can still use the checklist as a guide.'),
      h('section', { class: 'card' }, h('div', { class: 'card-body flex flex-col items-center gap-5 sm:flex-row' },
        ScoreRing(data.score, toneVar),
        h('div', { class: 'min-w-0 flex-1 space-y-3' },
          h('div', {}, h('p', { class: 'text-lg font-semibold' }, data.label), h('p', { class: 'text-sm text-muted' }, problems.length ? `Fixing the items below raises the score. The biggest gains are listed first.` : 'Everything checks out.')),
          h('div', { class: 'flex flex-wrap gap-2' }, stat(count('bad'), 'to fix', 'bad', 'alert'), stat(count('warn'), 'could be better', 'warn', 'alert'), stat(count('ok'), 'good', 'ok', 'check')),
          h('div', { class: 'flex flex-wrap gap-1.5' }, ...topicChips)))),
      problems.length ? h('div', { class: 'grid gap-4 lg:grid-cols-2' }, ...problems.map(finding)) : null,
      goodFold);
  }

  load();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' }, head, body, log) };
}
