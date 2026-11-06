// Mail: a free mail server on this server (Postfix, Dovecot, Rspamd): domains, mailboxes, aliases, DNS help, spam, relay, queue and logs.
import { h, fill, Button, Badge, Card, Table, EmptyState, Alert, PageHeader, Tabs, Field, Input, Select, Console, Skeleton, KeyValue, busy, confirmDialog, openModal, toast } from '../kit.js';
import { bytes } from '../format.js';
import { api } from '../api.js';
import { attempt, watchJob } from '../util.js';
import { store } from '../store.js';

const TABS = [{ id: 'overview', label: 'Overview' }, { id: 'domains', label: 'Domains' }, { id: 'mailboxes', label: 'Mailboxes' }, { id: 'aliases', label: 'Aliases & forwarders' }, { id: 'settings', label: 'Spam & sending' }, { id: 'queue', label: 'Queue & logs' }];
const copy = (text) => { navigator.clipboard && navigator.clipboard.writeText(text); toast('Copied', { tone: 'ok', timeout: 1500 }); };
const genPassword = () => { const a = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'; const r = crypto.getRandomValues(new Uint32Array(18)); return [...r].map((x) => a[x % a.length]).join('') + '-9'; };

export function MailPage({ query }) {
  let tab = TABS.some((t) => t.id === query.tab) ? query.tab : 'overview';
  let data = null;
  const isSuper = store.session.me.role === 'super_admin';
  const body = h('div', { class: 'space-y-6' }, Skeleton(4));

  async function load() {
    try { data = await api('/api/mail'); } catch (e) { fill(body, Alert({ tone: 'bad', title: 'Could not read the mail server' }, e.message)); return; }
    draw();
  }

  function draw() {
    const configured = data.status.configured;
    fill(body, configured ? Tabs({ tabs: TABS, active: tab, onChange: (t) => { tab = t; history.replaceState(null, '', `#/mail?tab=${t}`); draw(); } }) : null,
      configured ? h('div', { class: 'pt-2' }, { overview, domains, mailboxes, aliases, settings, queue }[tab]()) : setup());
  }

  // ------------------------------------------------------------------ set up
  function setup() {
    const st = data.status;
    const installed = Object.values(st.services).some((s) => s.installed);
    const host = Input({ placeholder: 'mail.example.com', mono: true });
    const spam = Select({ value: 'standard', options: [{ value: 'relaxed', label: 'Relaxed' }, { value: 'standard', label: 'Standard (recommended)' }, { value: 'strict', label: 'Strict' }, { value: 'off', label: 'Off' }] });
    const go = Button({ label: installed ? 'Finish setting it up' : 'Install the mail server', icon: 'download', variant: 'primary', disabled: !isSuper, onClick: async () => {
      const c = await confirmDialog({ title: 'Install the mail server?', message: `Installs Postfix, Dovecot and Rspamd on this server and sets them up for ${host.value.trim() || 'your mail host'}. Mail ports are not opened and DNS is not changed until you say so. This takes a few minutes.`, confirmLabel: 'Install', tone: 'primary' });
      if (!c.confirmed) return;
      const r = await attempt(api('/api/mail/install', { hostname: host.value.trim(), options: { spam: spam.value } }));
      if (!r) return;
      const con = Console({ empty: 'Starting...' });
      openModal({ title: 'Installing the mail server', body: con.el, size: 'lg' });
      const res = await watchJob(r.job, con);
      toast(res.ok ? 'Mail server installed' : 'Failed: read the log', { tone: res.ok ? 'ok' : 'bad' });
      load();
    } });
    return h('div', { class: 'space-y-6' },
      Alert({ tone: 'info', title: 'A free mail server, like the one in cPanel or Plesk' }, 'Mailboxes, aliases and forwarders, catch-all, autoresponders, spam filtering, DKIM signing, IMAP/POP3/SMTP for any mail app, and optional sending through a relay. Everything is managed here.'),
      Card({ title: 'Set up', subtitle: 'Needs an Ubuntu or Debian server. Takes a few minutes.' },
        h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'Mail server name', hint: 'A name that points at this server, like mail.example.com. It is what mail apps connect to and what other servers see.' }, host), Field({ label: 'Spam filter' }, spam)),
        isSuper ? null : Alert({ tone: 'warn', title: 'A super admin has to install it' }, ''),
        h('div', { class: 'mt-4' }, go)),
      Card({ title: 'Before you start' },
        h('ul', { class: 'list-disc space-y-2 pl-5 text-sm text-muted' },
          h('li', {}, 'Add a DNS A record for the mail server name pointing at this server.'),
          h('li', {}, 'Outgoing port 25 must be open at your hosting provider. Hetzner, AWS, Google Cloud and Azure block it on new accounts until you ask; otherwise use a relay (Spam & sending tab).'),
          h('li', {}, 'Set the reverse DNS (PTR) of the server\'s IP to the mail server name in your provider\'s panel. Mail from an IP without it is often refused.'),
          h('li', {}, 'Mail uses ports 25, 465, 587, 993 and 995. If the firewall is on, open them from the Overview tab after installing.'))));
  }

  // ---------------------------------------------------------------- overview
  function overview() {
    const st = data.status, v = data.view;
    const svc = (name, label) => { const s = st.services[name]; const ports = Object.entries(s.ports || {}); const ok = s.installed && ports.length && ports.every(([, up]) => up); return h('div', { class: 'flex items-center justify-between gap-3' }, h('span', { class: 'text-sm font-medium' }, label), s.installed ? Badge(ok ? 'running' : 'not answering', ok ? 'ok' : 'bad', { dot: true }) : Badge('not installed', 'neutral')); };
    const out = [];
    out.push(Card({ title: 'Status', subtitle: `Mail server name: ${st.hostname}` }, h('div', { class: 'space-y-3' }, svc('postfix', 'Sending and receiving (Postfix)'), svc('dovecot', 'Mailboxes, IMAP and POP3 (Dovecot)'), svc('rspamd', 'Spam filter and DKIM (Rspamd)')),
      h('div', { class: 'mt-4 grid gap-3 sm:grid-cols-4' }, [['Domains', st.domains], ['Mailboxes', st.accounts], ['Aliases', st.aliases], ['In the queue', st.queue]].map(([k, n]) => h('div', {}, h('p', { class: 'text-xs uppercase tracking-wide text-muted' }, k), h('p', { class: 'text-2xl font-semibold' }, String(n)))))));
    if (st.cert && st.cert.self_signed) out.push(Alert({ tone: 'warn', title: 'The mail certificate is temporary' }, `Mail apps will warn about it. Point ${st.hostname} at this server (DNS A record); the app then gets a real Let's Encrypt certificate through Caddy by itself within hours.`));
    if (st.queue > 20) out.push(Alert({ tone: 'warn', title: `${st.queue} messages are waiting in the queue` }, 'Mail is not leaving the server. Open Queue & logs to see why (often port 25 is blocked: use a relay).'));
    if (isSuper) {
      const ports = Button({ label: 'Open the mail ports in the firewall', icon: 'shield', onClick: () => busy(ports, async () => {
        const p = await attempt(api('/api/mail/ports', { preview: true }));
        if (!p) return;
        if (!p.commands.length) { toast(p.note || 'Nothing to do', { tone: 'info', timeout: 7000 }); return; }
        const c = await confirmDialog({ title: 'Open the mail ports?', message: `This will run on the server:\n${p.commands.join('\n')}`, confirmLabel: 'Open them', requireText: 'mail' });
        if (c.confirmed && await attempt(api('/api/mail/ports', { confirm: 'mail' }), 'Mail ports opened')) load();
      }) });
      const test = Button({ label: 'Test sending (port 25)', icon: 'upload', onClick: () => busy(test, async () => {
        const r = await attempt(api('/api/mail/port25', {}));
        if (r) toast(r.ok ? 'Outgoing port 25 works. This server can send mail directly.' : 'Outgoing port 25 is blocked by your hosting provider. Ask them to open it, or send through a relay (Spam & sending tab).', { tone: r.ok ? 'ok' : 'bad', timeout: 12000 });
      }) });
      out.push(Card({ title: 'Checks' }, h('div', { class: 'flex flex-wrap gap-2' }, ports, test),
        h('p', { class: 'mt-3 text-xs text-muted' }, 'Also needed: a reverse DNS (PTR) record for this server\'s IP pointing at ' + st.hostname + ', set in your hosting provider\'s panel.')));
    }
    out.push(Card({ title: 'Settings for mail apps', subtitle: 'Use these in Outlook, Thunderbird, Apple Mail, Gmail, your phone...' }, KeyValue([
      ['Username', 'the full address, for example you@' + ((v.domains[0] || {}).name || 'example.com')], ['Password', 'the mailbox password'],
      ['Incoming (IMAP)', `${st.hostname}, port 993, SSL/TLS`], ['Incoming (POP3)', `${st.hostname}, port 995, SSL/TLS`], ['Outgoing (SMTP)', `${st.hostname}, port 587 with STARTTLS (or 465 with SSL/TLS)`]])));
    return h('div', { class: 'space-y-6' }, ...out);
  }

  // ----------------------------------------------------------------- domains
  async function showDns(domain) {
    const r = await attempt(api(`/api/mail/dns?domain=${encodeURIComponent(domain)}`));
    if (!r) return;
    openModal({ title: `DNS records for ${domain}`, description: 'Add these at the place that hosts the DNS of this domain (your registrar, Cloudflare, Route 53...). Then press Check.', size: 'lg', body: h('div', { class: 'space-y-3' },
      Table({ columns: [{ label: 'Type', render: (x) => Badge(x.type, 'neutral') }, { label: 'Name', render: (x) => h('code', { class: 'text-xs', style: 'display:block;min-width:13rem;word-break:break-all' }, x.name) },
        { label: 'Value', render: (x) => h('div', {}, h('code', { class: 'break-all text-xs' }, (x.priority ? x.priority + ' ' : '') + x.value), h('p', { class: 'mt-1 text-xs text-muted' }, x.why)) },
        { label: '', render: (x) => h('div', { class: 'flex flex-col gap-1' }, Button({ label: 'Copy name', size: 'sm', variant: 'ghost', onClick: () => copy(x.name) }), Button({ label: 'Copy value', size: 'sm', variant: 'ghost', onClick: () => copy((x.priority ? x.priority + ' ' : '') + x.value) })) }], rows: r.records }),
      h('p', { class: 'text-xs text-muted' }, 'Also set the reverse DNS (PTR) of the server IP to ' + r.hostname + ' in your hosting provider\'s panel.')) });
  }

  async function checkDns(domain) {
    const r = await attempt(api(`/api/mail/check?domain=${encodeURIComponent(domain)}`));
    if (!r) return;
    openModal({ title: `Mail DNS check for ${domain}`, size: 'lg', body: h('div', { class: 'space-y-3' }, r.findings.map((f) => Alert({ tone: f.status, title: f.title }, f.detail, f.fix ? h('p', { class: 'mt-2 text-sm' }, h('strong', {}, 'Fix: '), f.fix) : null))) });
  }

  function domains() {
    const add = Button({ label: 'Add a domain', icon: 'plus', variant: 'primary', size: 'sm', disabled: !isSuper, onClick: () => {
      const name = Input({ placeholder: 'example.com', mono: true });
      const m = openModal({ title: 'Add a mail domain', description: 'Creates its DKIM signing key. Then add the DNS records shown next.', body: Field({ label: 'Domain' }, name), actions: [Button({ label: 'Add', variant: 'primary', onClick: async () => {
        const r = await attempt(api('/api/mail/domain/add', { domain: name.value }), 'Domain added');
        if (r) { m.close(); await load(); showDns(r.domain); }
      } })] });
      setTimeout(() => name.focus(), 50);
    } });
    return Card({ title: 'Domains', subtitle: 'Domains this server handles mail for.', actions: add, bodyClass: '' }, Table({
      columns: [
        { label: 'Domain', render: (d) => h('span', { class: 'font-medium' }, d.name) },
        { label: 'Mailboxes', class: 'text-muted', render: (d) => String(d.accounts) }, { label: 'Aliases', class: 'text-muted', render: (d) => String(d.aliases) },
        { label: 'DKIM key', render: (d) => Badge(d.dkim_public ? 'ready' : 'missing', d.dkim_public ? 'ok' : 'warn') },
        { label: '', class: 'text-right', render: (d) => h('div', { class: 'flex flex-wrap justify-end gap-2' }, Button({ label: 'DNS records', size: 'sm', onClick: () => showDns(d.name) }), Button({ label: 'Check DNS', size: 'sm', icon: 'check', onClick: () => checkDns(d.name) }),
          isSuper ? Button({ icon: 'trash', iconOnly: true, size: 'sm', variant: 'ghost', title: 'Remove domain', onClick: async () => {
            const c = await confirmDialog({ title: `Remove ${d.name}?`, message: 'The domain stops receiving mail here. Delete its mailboxes first.', confirmLabel: 'Remove', requireText: d.name });
            if (c.confirmed && await attempt(api('/api/mail/domain/remove', { domain: d.name, confirm: d.name }), 'Domain removed')) load();
          } }) : null) },
      ], rows: data.view.domains, empty: EmptyState({ icon: 'globe', title: 'No domains yet', body: 'Add the domain you want mail for, then its DNS records.' }) }));
  }

  // --------------------------------------------------------------- mailboxes
  function mailboxModal() {
    const doms = data.view.domains.map((d) => d.name);
    if (!doms.length) { toast('Add a domain first', { tone: 'warn' }); return; }
    const local = Input({ placeholder: 'name', mono: true }), dom = Select({ value: doms[0], options: doms.map((d) => ({ value: d, label: '@' + d })) });
    const pw = Input({ value: genPassword(), mono: true }), quota = Input({ type: 'number', value: '1024', min: 0 }), name = Input({ placeholder: 'Full name (optional)' });
    const m = openModal({ title: 'New mailbox', body: h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'Address' }, h('div', { class: 'flex gap-2' }, local, dom)), Field({ label: 'Full name' }, name),
      Field({ label: 'Password', hint: 'At least 10 characters. A strong one is suggested: copy it before you save.' }, pw), Field({ label: 'Space (MB)', hint: '0 = unlimited' }, quota)),
      actions: [Button({ label: 'Copy password', variant: 'ghost', onClick: () => copy(pw.value) }), Button({ label: 'Create', variant: 'primary', onClick: async () => {
        if (await attempt(api('/api/mail/account/add', { email: `${local.value.trim()}@${dom.value}`, password: pw.value, quota_mb: Number(quota.value) || 0, name: name.value }), 'Mailbox created')) { m.close(); load(); }
      } })] });
    setTimeout(() => local.focus(), 50);
  }

  const update = (email, op, extra, msg) => attempt(api('/api/mail/account/update', Object.assign({ email, op }, extra || {})), msg).then((r) => { if (r) load(); return r; });

  function autoreplyModal(a) {
    const ar = a.autoreply || {};
    const subject = Input({ value: ar.subject || 'Automatic reply' }), text = h('textarea', { class: 'input', rows: 5 }), days = Input({ type: 'number', value: String(ar.days || 7), min: 1 });
    text.value = ar.body || 'I am away and will reply when I am back.';
    const m = openModal({ title: `Autoresponder for ${a.email}`, description: 'Replies once to each sender (not again for the number of days below). Mail is still delivered to the mailbox.', body: h('div', { class: 'space-y-4' }, Field({ label: 'Subject' }, subject), Field({ label: 'Message' }, text), Field({ label: 'Do not reply again to the same sender for (days)' }, days)),
      actions: [ar.enabled ? Button({ label: 'Turn off', variant: 'danger-soft', onClick: async () => { if (await update(a.email, 'autoreply', { autoreply: { enabled: false } }, 'Autoresponder off')) m.close(); } }) : null,
        Button({ label: 'Turn on', variant: 'primary', onClick: async () => { if (await update(a.email, 'autoreply', { autoreply: { enabled: true, subject: subject.value, body: text.value, days: Number(days.value) || 7 } }, 'Autoresponder on')) m.close(); } })].filter(Boolean) });
  }

  function mailboxes() {
    const add = Button({ label: 'New mailbox', icon: 'plus', variant: 'primary', size: 'sm', onClick: mailboxModal });
    return Card({ title: 'Mailboxes', actions: add, bodyClass: '' }, Table({
      columns: [
        { label: 'Address', render: (a) => h('div', {}, h('span', { class: 'font-medium' }, a.email), a.name ? h('div', { class: 'text-xs text-muted' }, a.name) : null) },
        { label: 'Space used', class: 'text-muted', render: (a) => `${bytes(a.used)}${a.quota_mb ? ' of ' + a.quota_mb + ' MB' : ''}` },
        { label: 'Status', render: (a) => (a.disabled ? Badge('disabled', 'warn') : a.autoreply && a.autoreply.enabled ? Badge('auto-reply on', 'info') : Badge('active', 'ok')) },
        { label: '', class: 'text-right', render: (a) => h('div', { class: 'flex flex-wrap justify-end gap-1' },
          Button({ label: 'Password', size: 'sm', variant: 'ghost', onClick: () => { const pw = Input({ value: genPassword(), mono: true }); const m = openModal({ title: `New password for ${a.email}`, body: Field({ label: 'Password', hint: 'Copy it before you save.' }, pw), actions: [Button({ label: 'Copy', variant: 'ghost', onClick: () => copy(pw.value) }), Button({ label: 'Save', variant: 'primary', onClick: async () => { if (await update(a.email, 'password', { password: pw.value }, 'Password changed')) m.close(); } })] }); } }),
          Button({ label: 'Space', size: 'sm', variant: 'ghost', onClick: () => { const q = Input({ type: 'number', value: String(a.quota_mb), min: 0 }); const m = openModal({ title: `Space for ${a.email}`, body: Field({ label: 'Megabytes', hint: '0 = unlimited' }, q), actions: [Button({ label: 'Save', variant: 'primary', onClick: async () => { if (await update(a.email, 'quota', { quota_mb: Number(q.value) || 0 }, 'Saved')) m.close(); } })] }); } }),
          Button({ label: 'Auto-reply', size: 'sm', variant: 'ghost', onClick: () => autoreplyModal(a) }),
          Button({ label: a.disabled ? 'Enable' : 'Disable', size: 'sm', variant: 'ghost', onClick: () => update(a.email, a.disabled ? 'enable' : 'disable', {}, a.disabled ? 'Enabled' : 'Disabled: it no longer receives or sends mail') }),
          Button({ icon: 'trash', iconOnly: true, size: 'sm', variant: 'ghost', title: 'Delete', onClick: async () => {
            const c = await confirmDialog({ title: `Delete ${a.email}?`, message: 'The mailbox stops working. You can also delete the mail in it.', confirmLabel: 'Delete', requireText: a.email, switches: [{ id: 'mail', label: 'Also delete all its mail', description: 'Cannot be undone.', default: false }] });
            if (c.confirmed) update(a.email, 'delete', { confirm: a.email, delete_mail: !!(c.values && c.values.mail) }, 'Deleted');
          } })) },
      ], rows: data.view.accounts, empty: EmptyState({ icon: 'download', title: 'No mailboxes yet', body: 'Create the first one with New mailbox.' }) }));
  }

  // ----------------------------------------------------------------- aliases
  function aliases() {
    const add = Button({ label: 'New alias', icon: 'plus', variant: 'primary', size: 'sm', onClick: () => {
      const src = Input({ placeholder: 'info@example.com, or @example.com for a catch-all', mono: true }), dests = Input({ placeholder: 'alice@example.com, bob@gmail.com', mono: true });
      const m = openModal({ title: 'New alias or forwarder', description: 'Mail sent to the first address is delivered to every address on the right (they can be outside addresses). @example.com catches everything not matched by a mailbox or another alias.',
        body: h('div', { class: 'space-y-4' }, Field({ label: 'Address' }, src), Field({ label: 'Deliver to', hint: 'Separate several with commas.' }, dests)),
        actions: [Button({ label: 'Add', variant: 'primary', onClick: async () => { if (await attempt(api('/api/mail/alias/add', { source: src.value, dests: dests.value }), 'Alias added')) { m.close(); load(); } } })] });
      setTimeout(() => src.focus(), 50);
    } });
    return Card({ title: 'Aliases & forwarders', subtitle: 'Extra addresses that deliver to one or more places, and catch-alls.', actions: add, bodyClass: '' }, Table({
      columns: [{ label: 'Address', render: (a) => h('span', { class: 'font-medium' }, a.source.startsWith('@') ? `anything@${a.source.slice(1)} (catch-all)` : a.source) }, { label: 'Delivers to', class: 'text-muted', render: (a) => a.dests.join(', ') },
        { label: '', class: 'text-right', render: (a) => Button({ icon: 'trash', iconOnly: true, size: 'sm', variant: 'ghost', title: 'Remove', onClick: async () => { const c = await confirmDialog({ title: `Remove ${a.source}?`, message: 'Mail to it is no longer delivered.', confirmLabel: 'Remove' }); if (c.confirmed && await attempt(api('/api/mail/alias/remove', { source: a.source }), 'Removed')) load(); } }) }],
      rows: data.view.aliases, empty: EmptyState({ icon: 'link', title: 'No aliases', body: 'Make info@ deliver to a person, forward mail to Gmail, or catch everything for a domain.' }) }));
  }

  // ---------------------------------------------------------------- settings
  function settings() {
    if (!isSuper) return Alert({ tone: 'info', title: 'Only super admins change these settings' }, '');
    const v = data.view, o = v.options, r = v.relay || {};
    const spam = Select({ value: o.spam, options: [{ value: 'off', label: 'Off' }, { value: 'relaxed', label: 'Relaxed: fewer false alarms' }, { value: 'standard', label: 'Standard (recommended)' }, { value: 'strict', label: 'Strict: catches more, may flag good mail' }] });
    const size = Input({ type: 'number', value: String(o.max_message_mb), min: 1 });
    const save = Button({ label: 'Save', variant: 'primary', onClick: () => busy(save, async () => { if (await attempt(api('/api/mail/options', { options: { spam: spam.value, max_message_mb: Number(size.value) } }), 'Saved')) load(); }) });
    const f = { host: Input({ value: r.host || '', placeholder: 'smtp.example-relay.com', mono: true }), port: Input({ type: 'number', value: String(r.port || 587) }), user: Input({ value: r.user || '' }), pw: Input({ type: 'password', placeholder: r.has_password ? 'saved - leave empty to keep' : '', autocomplete: 'off' }) };
    const saveRelay = Button({ label: r.host ? 'Save relay' : 'Use this relay', variant: 'primary', onClick: () => busy(saveRelay, async () => { if (await attempt(api('/api/mail/relay', { relay: { host: f.host.value, port: Number(f.port.value), user: f.user.value, password: f.pw.value } }), 'Saved')) load(); }) });
    const off = r.host ? Button({ label: 'Send directly again', variant: 'ghost', onClick: async () => { if (await attempt(api('/api/mail/relay', { relay: {} }), 'Sending directly')) load(); } }) : null;
    return h('div', { class: 'space-y-6' },
      Card({ title: 'Spam filter and limits' }, h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'Spam filter', hint: 'Suspected spam goes to each mailbox\'s Junk folder; the worst is refused.' }, spam), Field({ label: 'Largest message (MB)' }, size)), h('div', { class: 'mt-4' }, save)),
      Card({ title: 'Send through a relay (optional)', subtitle: 'If your host blocks port 25 (Hetzner, AWS, Google Cloud, Azure do by default), outgoing mail can go through a service such as Brevo, Mailgun, Amazon SES or SendGrid instead. Incoming mail is not affected.' },
        h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'Relay host' }, f.host), Field({ label: 'Port' }, f.port), Field({ label: 'User name' }, f.user), Field({ label: 'Password or API key' }, f.pw)),
        h('div', { class: 'mt-4 flex gap-2' }, saveRelay, off)));
  }

  // ------------------------------------------------------------------- queue
  function queue() {
    if (!isSuper) return Alert({ tone: 'info', title: 'Only super admins can see the queue and logs' }, '');
    const box = h('div', {}, Skeleton(3)), logBox = h('pre', { class: 'max-h-96 overflow-auto rounded-md bg-sunken p-3 text-xs' });
    async function loadQueue() {
      try {
        const q = await api('/api/mail/queue');
        fill(box, Table({ columns: [{ label: 'Id', render: (m) => h('code', { class: 'text-xs' }, m.id) }, { label: 'From', class: 'text-muted', render: (m) => m.sender || '(bounce)' }, { label: 'To', class: 'text-muted', render: (m) => m.to.join(', ') },
          { label: 'Waiting because', class: 'text-xs text-muted', render: (m) => m.why || 'in progress' }, { label: 'Size', class: 'text-muted', render: (m) => bytes(m.size) },
          { label: '', class: 'text-right', render: (m) => Button({ icon: 'trash', iconOnly: true, size: 'sm', variant: 'ghost', title: 'Delete this message', onClick: async () => { if (await attempt(api('/api/mail/queue', { op: 'delete', id: m.id }), 'Deleted')) loadQueue(); } }) }],
          rows: q.items, empty: EmptyState({ icon: 'check', title: 'The queue is empty', body: 'Every message has been delivered.' }) }));
      } catch (e) { fill(box, Alert({ tone: 'bad', title: 'Could not read the queue' }, e.message)); }
    }
    async function loadLogs() { try { logBox.textContent = (await api('/api/mail/logs?lines=300')).lines.join('\n') || '(nothing logged yet)'; logBox.scrollTop = logBox.scrollHeight; } catch (e) { logBox.textContent = e.message; } }
    loadQueue(); loadLogs();
    return h('div', { class: 'space-y-6' },
      Card({ title: 'Messages waiting to be sent', actions: h('div', { class: 'flex gap-2' }, Button({ label: 'Try them all now', size: 'sm', onClick: async () => { await attempt(api('/api/mail/queue', { op: 'flush' }), 'Retrying'); loadQueue(); } }), Button({ icon: 'refresh', iconOnly: true, size: 'sm', title: 'Refresh', onClick: loadQueue })), bodyClass: '' }, box),
      Card({ title: 'Mail log', actions: Button({ icon: 'refresh', iconOnly: true, size: 'sm', title: 'Refresh', onClick: loadLogs }) }, logBox));
  }

  load();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' }, PageHeader({ title: 'Mail', description: 'A free mail server on this server: mailboxes, aliases, forwarders, spam filtering and DKIM, for any domain you own.' }), body) };
}
