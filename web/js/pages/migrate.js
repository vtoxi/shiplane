// Migrate: read an old hosting account (cPanel backup or a live server) and check the new server is ready for it.
import { h, Button, Badge, Card, Table, EmptyState, Alert, PageHeader, Segmented, Field, Input, Stat, Console, Switch, busy, fill } from '../kit.js';
import { bytes } from '../format.js';
import { api } from '../api.js';
import { attempt, watchJob } from '../util.js';
import { navigate } from '../router.js';

const KIND = { wordpress: 'WordPress', php: 'PHP', laravel: 'Laravel', node: 'Node', static: 'Static' };

export function MigratePage() {
  let source = 'backup';
  let inv = null;
  const form = h('div', {});
  const result = h('div', { class: 'space-y-6' });

  const path = Input({ placeholder: 'C:\\Backups\\cpmove-username.tar.gz  or  plesk-backup.tgz', mono: true });
  const host = Input({ placeholder: 'old.example.com or 203.0.113.10' });
  const user = Input({ placeholder: 'root' });
  const port = Input({ value: '22', placeholder: '22', mono: true });
  const key = Input({ placeholder: '~/.ssh/id_ed25519', mono: true });
  const password = Input({ type: 'password', autocomplete: 'new-password' });
  let auth = 'key';
  let request = null;
  let siteSwitches = [];
  let dbSwitches = [];

  const browse = Button({ label: 'Browse...', icon: 'folder', onClick: () => busy(browse, async () => { const r = await attempt(api('/api/pick-file', {})); if (r && r.path) path.value = r.path; }) });

  function drawForm() {
    const go = Button({ label: 'Analyze', icon: 'search', variant: 'primary', onClick: () => busy(go, () => analyze()) });
    form.replaceChildren(Card({ title: '1. Where are the old sites?' },
      h('div', { class: 'space-y-4' },
        Segmented({ label: 'Source', value: source, options: [{ value: 'backup', label: 'cPanel / Plesk backup file' }, { value: 'live', label: 'Live server (SSH)' }], onChange: (v) => { source = v; drawForm(); } }),
        source === 'backup'
          ? Field({ label: 'Backup file on this computer', hint: 'A full cPanel account backup (cpmove-*.tar.gz) or a backup exported from Plesk (.tgz, .tar or .zip). Nothing is extracted; it is only read. Works without SSH.' }, h('div', { class: 'flex gap-2' }, h('div', { class: 'min-w-0 flex-1' }, path), browse))
          : h('div', { class: 'grid gap-4 sm:grid-cols-4' },
            Field({ label: 'Host' }, host), Field({ label: 'SSH port', hint: 'Often 22; shared hosts may use another.' }, port), Field({ label: 'User', hint: 'Needs root. A normal shared-hosting login cannot be read: use a backup file.' }, user),
            Field({ label: 'Login' }, Segmented({ label: 'Login', value: auth, options: [{ value: 'key', label: 'SSH key' }, { value: 'password', label: 'Password' }], onChange: (v) => { auth = v; drawForm(); } })),
            h('div', { class: 'sm:col-span-4' }, auth === 'key' ? Field({ label: 'SSH key file' }, key) : Field({ label: 'Password', hint: 'Used for this analysis only. It is not saved.' }, password))),
        go)));
  }

  async function analyze() {
    request = source === 'backup' ? { source, path: path.value } : { source, host: host.value, port: port.value, user: user.value, auth, key: key.value, password: password.value };
    const r = await attempt(api('/api/migrate/inspect', request));
    if (r) { inv = r; drawResult(); }
  }

  async function install(cap, btn) {
    const con = Console({ empty: 'Starting...' });
    result.prepend(Card({ title: `Installing ${cap.label}` }, con.el));
    const r = await attempt(api('/api/migrate/prepare', { id: cap.id }));
    if (!r) { btn.disabled = false; return; }
    const done = await watchJob(r.job, con);
    if (done.ok) await analyze(); else btn.disabled = false;
  }

  function readiness() {
    if (!inv.capabilities.length) return Alert({ tone: 'ok', title: 'The new server is ready' }, 'Nothing extra is needed for what was found.');
    return Card({ title: '2. Is the new server ready?', subtitle: 'Components this move needs. Missing ones can be set up first, or automatically where that is safe.' },
      Table({
        columns: [
          { label: 'Needed', render: (c) => h('span', { class: 'font-medium' }, c.label) },
          { label: 'On the new server', render: (c) => Badge(c.installed ? 'Installed' : 'Missing', c.installed ? 'ok' : 'warn', { dot: true }) },
          { label: '', class: 'text-right', render: (c) => {
            if (c.installed) return '';
            if (c.auto) { const b = Button({ label: 'Install now', icon: 'download', variant: 'primary', size: 'sm', onClick: () => { b.disabled = true; install(c, b); } }); return b; }
            return h('span', { class: 'text-xs text-muted' }, 'Set up first');
          } },
        ],
        rows: inv.capabilities,
      }),
      ...inv.capabilities.filter((c) => !c.installed && c.advice).map((c) => h('div', { class: 'pt-4' }, Alert({ tone: 'warn', title: c.label }, c.advice))),
      h('div', { class: 'pt-4' }, Button({ label: 'Check again', icon: 'refresh', onClick: analyze })));
  }

  function drawResult() {
    const dbSize = inv.databases.reduce((a, d) => a + d.size, 0);
    const siteSize = inv.sites.reduce((a, s) => a + s.size, 0);
    fill(result,
      Alert({ tone: 'info', title: `Found on ${inv.panel}` }, inv.notes.length ? inv.notes.join(' ') : 'Everything found can be moved as it is.'),
      h('div', { class: 'grid gap-4 sm:grid-cols-4' },
        Stat({ label: 'Sites', value: String(inv.sites.length), hint: bytes(siteSize) }), Stat({ label: 'Databases', value: String(inv.databases.length), hint: bytes(dbSize) }),
        Stat({ label: 'Mailboxes', value: String(inv.mailboxes) }), Stat({ label: 'Cron jobs', value: String(inv.cron.length) })),
      Card({ title: 'Sites' }, Table({
        columns: [
          { label: 'Domain', render: (s) => h('span', { class: 'font-medium' }, s.domain) },
          { label: 'Type', render: (s) => Badge(KIND[s.kind] || s.kind, 'neutral') },
          { label: 'Folder', class: 'font-mono text-xs text-muted', render: (s) => s.docroot },
          { label: 'Size', class: 'text-muted', render: (s) => bytes(s.size) },
        ],
        rows: inv.sites,
        empty: EmptyState({ icon: 'sites', title: 'No sites found', body: 'This does not look like a hosting account with websites.' }),
      })),
      inv.databases.length ? Card({ title: 'Databases' }, Table({
        columns: [{ label: 'Name', render: (d) => h('span', { class: 'font-medium' }, d.name) }, { label: 'Engine', render: (d) => Badge(d.engine === 'mysql' ? 'MySQL' : 'PostgreSQL', 'neutral') }, { label: 'Size', class: 'text-muted', render: (d) => bytes(d.size) }],
        rows: inv.databases,
      })) : null,
      readiness(),
      startCard());
  }

  function startCard() {
    const sg = inv.suggestions || { sites: {}, databases: {} };
    const movable = (x) => x.kind !== 'node';
    const small = (label, input, hint) => Field({ label, hint }, input);
    siteSwitches = inv.sites.map((x) => {
      const sug = sg.sites[x.domain] || { domain: x.domain, site: x.domain };
      const domain = Input({ value: sug.domain, mono: true });
      const site = Input({ value: sug.site, mono: true });
      const sw = Switch({ label: x.domain, description: movable(x) ? `${KIND[x.kind] || x.kind}, ${bytes(x.size)}` : 'Node app: deploy it from the Deploy page', checked: movable(x) });
      const more = h('details', { class: 'mt-2' }, h('summary', { class: 'cursor-pointer text-xs text-muted' }, 'Customize'),
        h('div', { class: 'mt-2 grid gap-3 sm:grid-cols-2' }, small('Site name on the new server', site),
          small('Address (advanced)', domain, 'Leave as it is unless you must. The database is not changed: WordPress and similar apps keep their address inside it and need it updated.')));
      return { x, sw, el: h('div', {}, sw, movable(x) ? more : null), values: () => ({ domain: x.domain, new_domain: domain.value.trim(), site: site.value.trim() }) };
    });
    dbSwitches = inv.databases.map((x) => {
      const sug = sg.databases[x.name] || { name: x.name, user: x.name };
      const name = Input({ value: sug.name, mono: true });
      const user = Input({ value: sug.user, mono: true });
      const pw = Input({ type: 'password', placeholder: 'generated automatically', autocomplete: 'new-password' });
      const sw = Switch({ label: x.name, description: `${x.engine === 'mysql' ? 'MySQL' : 'PostgreSQL'}, ${bytes(x.size)}`, checked: true });
      const more = h('details', { class: 'mt-2' }, h('summary', { class: 'cursor-pointer text-xs text-muted' }, 'Customize'),
        h('div', { class: 'mt-2 grid gap-3 sm:grid-cols-2' }, small('Database name', name), small('Database user', user),
          h('div', { class: 'sm:col-span-2' }, small('Password', pw, 'Leave empty for a strong generated one. Your own: 12 to 64 characters from letters, digits and @ % ^ * _ + = . , : ? ! - . It is written into the site\'s config and never shown again.'))));
      return { x, sw, el: h('div', {}, sw, more), values: () => ({ name: x.name, new_name: name.value.trim(), user: user.value.trim(), password: pw.value }) };
    });
    const setAll = (list, on) => list.forEach((i) => i.sw.set(on));
    const bulk = (list) => h('span', { class: 'ml-2 text-xs font-normal text-muted' }, h('button', { type: 'button', class: 'hover:underline', onClick: () => setAll(list, true) }, 'all'), ' / ', h('button', { type: 'button', class: 'hover:underline', onClick: () => setAll(list, false) }, 'none'));
    const go = Button({ label: 'Start migration', icon: 'rocket', variant: 'primary', onClick: () => busy(go, async () => {
      const body = { ...request, sites: siteSwitches.filter((i) => i.sw.get()).map((i) => i.values()), databases: dbSwitches.filter((i) => i.sw.get()).map((i) => i.values()) };
      const r = await attempt(api('/api/migrate/start', body));
      if (r) navigate(`/migrate/run/${r.id}`);
    }) });
    return Card({ title: '3. Choose what to move', subtitle: 'Names are kept as they are unless you change them under Customize. Nothing on the old hosting is changed, and a database that already exists here is never overwritten.' },
      h('div', { class: 'grid gap-6 sm:grid-cols-2' },
        h('div', { class: 'space-y-4' }, h('p', { class: 'text-sm font-medium' }, 'Sites', bulk(siteSwitches)), siteSwitches.map((i) => i.el)),
        h('div', { class: 'space-y-4' }, h('p', { class: 'text-sm font-medium' }, 'Databases', dbSwitches.length ? bulk(dbSwitches) : null), dbSwitches.length ? dbSwitches.map((i) => i.el) : h('p', { class: 'text-sm text-muted' }, 'None found'))),
      h('div', { class: 'pt-4' }, go));
  }

  drawForm();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' },
    PageHeader({ title: 'Migrate', description: 'Move sites and databases from shared hosting (cPanel, Plesk) or an older server to this server. This step only reads: nothing is changed on either side.' }), form, result) };
}
