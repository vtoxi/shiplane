// Firewall (ufw) rules, the email deliverability check and the public status page.
import { h, Button, Badge, Card, Table, EmptyState, Alert, PageHeader, Tabs, Select, Field, Input, Switch, Skeleton, busy, confirmDialog, toast } from '../kit.js';
import { store } from '../store.js';
import { api } from '../api.js';
import { attempt } from '../util.js';

const fail = (box) => (e) => box.replaceChildren(Alert({ tone: 'bad', title: 'Could not load' }, e.message));

// ---------------------------------------------------------------- firewall
function firewallTab() {
  const box = h('div', { class: 'space-y-4' }, Skeleton(4));
  const action = Select({ value: 'allow', options: [{ value: 'allow', label: 'Allow' }, { value: 'deny', label: 'Deny' }, { value: 'limit', label: 'Limit (rate-limit)' }] });
  const port = Input({ placeholder: '443 or 6000:6100', mono: true });
  const proto = Select({ value: 'tcp', options: [{ value: 'tcp', label: 'TCP' }, { value: 'udp', label: 'UDP' }, { value: 'any', label: 'Any' }] });
  const from = Input({ placeholder: 'any, or 203.0.113.0/24', mono: true });
  const rule = () => ({ action: action.value, port: port.value.trim(), proto: proto.value, from: from.value.trim() || 'any' });

  async function change(path, body, title) {
    const p = await attempt(api(path, { ...body, preview: true }));
    if (!p) return;
    const c = await confirmDialog({ title, message: `This will run on the server:\n${p.commands.join('\n')}`, confirmLabel: 'Apply' });
    if (c.confirmed && await attempt(api(path, body), 'Firewall updated')) await load();
  }
  async function power(on) {
    const c = await confirmDialog({ title: on ? 'Turn the firewall on?' : 'Turn the firewall off?', message: on ? 'SSH is allowed first, so you cannot lock yourself out. Everything not allowed is blocked.' : 'All ports become reachable.', confirmLabel: on ? 'Turn on' : 'Turn off', requireText: 'firewall' });
    if (c.confirmed && await attempt(api('/api/firewall/power', { on, confirm: 'firewall' }), on ? 'Firewall is on' : 'Firewall is off')) await load();
  }
  async function load() {
    const f = await api('/api/firewall');
    if (!f.installed) { box.replaceChildren(Alert({ tone: 'warn', title: 'ufw is not installed' }, 'Install it from Server care (Firewall), then come back.')); return; }
    box.replaceChildren(
      Card({ title: 'Firewall', subtitle: `Default for incoming traffic: ${f.default_in || 'unknown'}. SSH port${f.ssh_ports.length > 1 ? 's' : ''} ${f.ssh_ports.join(', ')} can never be blocked from here.`,
        actions: h('div', { class: 'flex items-center gap-2' }, Badge(f.active ? 'on' : 'off', f.active ? 'ok' : 'warn'), Button({ label: f.active ? 'Turn off' : 'Turn on', size: 'sm', variant: f.active ? 'danger-soft' : 'primary', onClick: () => power(!f.active) })) },
        Table({
          columns: [
            { label: 'To', render: (r) => h('code', { class: 'font-mono text-xs' }, r.to) },
            { label: 'Action', render: (r) => Badge(r.action, r.action === 'deny' ? 'bad' : 'ok') },
            { label: 'From', class: 'text-muted', render: (r) => r.from },
            { label: '', class: 'text-right', render: (r) => Button({ icon: 'trash', iconOnly: true, size: 'sm', variant: 'ghost', title: 'Delete rule', onClick: () => {
              const [p, pr] = r.to.split('/');
              change('/api/firewall/delete', { action: r.action, port: p, proto: pr || 'any', from: r.from === 'Anywhere' ? 'any' : r.from }, 'Delete this rule?');
            } }) },
          ],
          rows: f.rules, empty: EmptyState({ icon: 'shield', title: 'No rules', body: 'Add one below.' }),
        })),
      Card({ title: 'Add a rule', subtitle: 'You see the exact command before anything runs.' },
        h('div', { class: 'grid gap-4 sm:grid-cols-4' }, Field({ label: 'Action' }, action), Field({ label: 'Port' }, port), Field({ label: 'Protocol' }, proto), Field({ label: 'From' }, from)),
        h('div', { class: 'mt-4' }, Button({ label: 'Preview and add', variant: 'primary', icon: 'plus', onClick: () => change('/api/firewall/add', rule(), 'Add this rule?') }))));
  }
  load().catch(fail(box));
  return box;
}

// ------------------------------------------------------------------- email
function emailTab() {
  const domain = Input({ placeholder: 'example.com', mono: true });
  const selector = Input({ placeholder: 'optional DKIM selector', mono: true });
  const out = h('div', { class: 'mt-4 space-y-3' });
  const tone = { ok: 'ok', warn: 'warn', bad: 'bad' };
  const go = Button({ label: 'Check', variant: 'primary', icon: 'check', onClick: () => busy(go, async () => {
    const q = `domain=${encodeURIComponent(domain.value.trim())}${selector.value.trim() ? `&selector=${encodeURIComponent(selector.value.trim())}` : ''}`;
    const r = await attempt(api(`/api/email/check?${q}`));
    if (!r) return;
    out.replaceChildren(...r.findings.map((f) => Alert({ tone: tone[f.status], title: f.title }, f.detail, f.fix ? h('p', { class: 'mt-2 text-sm' }, h('strong', {}, 'Fix: '), f.fix) : null)));
  }) });
  return Card({ title: 'Email deliverability', subtitle: 'Checks MX, SPF, DKIM and DMARC over DNS. Nothing is changed.' },
    h('div', { class: 'grid gap-4 sm:grid-cols-3 sm:items-end' }, Field({ label: 'Domain' }, domain), Field({ label: 'DKIM selector' }, selector), go), out);
}

// ------------------------------------------------------------- status page
function statusTab() {
  const box = h('div', {}, Skeleton(3));
  async function load() {
    const c = await api('/api/statuspage');
    const on = Switch({ label: 'Public status page', description: 'A read-only page for customers: site names, up/down and uptime. No addresses.', checked: c.enabled });
    const title = Input({ value: c.title || 'Status' });
    const save = (rotate) => busy(save.btn, async () => { if (await attempt(api('/api/statuspage', { enabled: on.get(), title: title.value, sites: c.sites || [], rotate }), 'Saved')) await load(); });
    save.btn = Button({ label: 'Save', variant: 'primary', onClick: () => save(false) });
    const link = c.path ? location.origin + c.path : '';
    box.replaceChildren(Card({ title: 'Status page' }, on, h('div', { class: 'mt-4 sm:w-80' }, Field({ label: 'Title' }, title)),
      link ? h('p', { class: 'mt-4 text-sm' }, 'Link: ', h('a', { href: link, target: '_blank', rel: 'noopener', class: 'font-mono text-xs underline break-all' }, link)) : null,
      h('div', { class: 'mt-4 flex gap-2' }, save.btn, c.path ? Button({ label: 'New link', variant: 'ghost', onClick: async () => { const k = await confirmDialog({ title: 'Make a new link?', message: 'The old link stops working immediately.', confirmLabel: 'New link' }); if (k.confirmed) save(true); } }) : null,
        c.path ? Button({ label: 'Copy link', variant: 'ghost', icon: 'copy', onClick: () => { navigator.clipboard && navigator.clipboard.writeText(link); toast('Copied', { tone: 'ok', timeout: 1500 }); } }) : null)));
  }
  load().catch(fail(box));
  return box;
}

const TABS = [{ id: 'firewall', label: 'Firewall' }, { id: 'email', label: 'Email check' }, { id: 'status', label: 'Status page' }];
const PANELS = { firewall: firewallTab, email: emailTab, status: statusTab };

export function ProtectPage({ query }) {
  let tab = PANELS[query.tab] ? query.tab : 'firewall';
  const body = h('div', {});
  const draw = () => body.replaceChildren(Tabs({ tabs: TABS.filter((t) => t.id !== 'status' || store.session.me.role === 'super_admin'), active: tab, onChange: (t) => { tab = t; history.replaceState(null, '', `#/protect?tab=${t}`); draw(); } }), h('div', { class: 'pt-6' }, PANELS[tab]()));
  draw();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' }, PageHeader({ title: 'Firewall & email', description: 'Open and close ports safely, check your domain’s email setup, and share a public status page.' }), body) };
}
