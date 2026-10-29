import { h, Logo, Button, Badge, Card, Table, EmptyState, Alert, PageHeader, Field, Input, Select, Switch, Console, Tabs, icon, busy, confirmDialog, openModal, toast, Skeleton } from '../kit.js';
import { store } from '../store.js';
import { bytes } from '../format.js';
import { api } from '../api.js';
import { attempt, watchJob } from '../util.js';
import { NeedsBar } from '../needs.js';
import { withHelp } from '../help.js';

export function DatabasesPage() {
  let data = null;
  let error = null;
  const body = h('div', { class: 'space-y-6' });

  async function load() {
    try { data = await api('/api/databases'); data.schedule = await api('/api/backups/schedule'); data.engines = await api('/api/dbengines').catch(() => null); error = null; } catch (e) { error = e.message; }
    draw();
  }

  /** Show a background job's log in a modal; reload the lists when it ends. */
  async function showJob(title, jobId) {
    const con = Console({ lines: [] });
    const m = openModal({ title, body: con.el, size: 'lg' });
    const res = await watchJob(jobId, con);
    toast(res.ok ? `${title}: done` : `${title}: failed, read the log`, { tone: res.ok ? 'ok' : 'bad' });
    await load();
    return m;
  }

  function scheduleCard() {
    const sc = data.schedule;
    const on = Switch({ label: 'Back up every database each night', description: 'Dumps are kept on the server. Download them now and then to keep a copy off the server.', checked: sc.enabled });
    const verify = Switch({ label: 'Test-restore each new backup', description: 'Loads the backup into a scratch database and drops it again, so you know it works before you need it. The live data is not touched.', checked: !!sc.verify });
    const hour = Select({ value: String(sc.hour), options: Array.from({ length: 24 }, (_, i) => ({ value: String(i), label: `${String(i).padStart(2, '0')}:00 UTC` })) });
    const keep = Input({ type: 'number', min: 1, value: String(sc.keep) });
    const save = Button({ label: 'Save schedule', variant: 'primary', onClick: () => busy(save, async () => {
      if (await attempt(api('/api/backups/schedule', { enabled: on.get(), hour: Number(hour.value), keep: Number(keep.value), verify: verify.get() }), 'Schedule saved')) await load();
    }) });
    const now = Button({ label: 'Back up everything now', icon: 'download', onClick: async () => { const r = await attempt(api('/api/backups/run', {})); if (r) showJob('Backup', r.job); } });
    const last = sc.last && sc.last.summary ? h('p', { class: 'mt-4 text-sm text-muted' }, `Last run: ${sc.last.t || '-'}: `, Badge(sc.last.ok === false ? 'failed' : sc.last.ok ? 'ok' : 'running', sc.last.ok === false ? 'bad' : sc.last.ok ? 'ok' : 'info'), ` ${sc.last.summary}`) : null;
    return Card({ title: withHelp('Automatic backups', 'backups-and-restore'), subtitle: 'Old backups beyond the limit are removed (manual ones count too)' },
      on, h('div', { class: 'mt-3' }, verify), h('div', { class: 'mt-4 grid gap-4 sm:grid-cols-2' }, Field({ label: 'Time of day' }, hour), Field({ label: 'Backups to keep per database' }, keep)),
      h('div', { class: 'mt-4 flex flex-wrap gap-2' }, save, now), last);
  }

  function offsiteCard() {
    const o = data.schedule.offsite || {};
    const f = { endpoint: Input({ value: o.endpoint || '', placeholder: 'https://ACCOUNT_ID.r2.cloudflarestorage.com', mono: true }), bucket: Input({ value: o.bucket || '', placeholder: 'my-backups', mono: true }),
      region: Input({ value: o.region || 'auto', placeholder: 'auto (R2) or us-east-1', mono: true }), prefix: Input({ value: o.prefix || '', placeholder: 'shiplane/ (optional)', mono: true }),
      access_key: Input({ value: o.access_key || '', placeholder: 'Access key ID', mono: true }), secret_key: Input({ type: 'password', placeholder: o.has_secret ? 'saved: leave empty to keep it' : 'Secret access key', mono: true }) };
    const vals = () => Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value]));
    const save = Button({ label: 'Save', variant: 'primary', onClick: () => busy(save, async () => { if (await attempt(api('/api/backups/offsite', vals()), 'Saved')) await load(); }) });
    const test = Button({ label: 'Send a test file', onClick: () => busy(test, async () => { const r = await attempt(api('/api/backups/offsite/test', {})); if (r) toast(r.text, { tone: r.ok ? 'ok' : 'bad', timeout: 8000 }); }) });
    const off = Button({ label: 'Turn off', variant: 'ghost', onClick: () => busy(off, async () => { if (await attempt(api('/api/backups/offsite', { bucket: '' }), 'Off-server copy removed')) await load(); }) });
    return Card({ title: withHelp('Off-server copy', 'health-checks-node-versions-log-search-and-off-server-backups'), subtitle: 'Every backup is also uploaded to a bucket, so losing the server does not lose the backups. Works with Cloudflare R2, Amazon S3 and any S3-compatible service.' },
      h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'Endpoint', hint: 'R2: https://ACCOUNT_ID.r2.cloudflarestorage.com. S3: https://s3.REGION.amazonaws.com' }, f.endpoint), Field({ label: 'Bucket' }, f.bucket),
        Field({ label: 'Region' }, f.region), Field({ label: 'Folder in the bucket' }, f.prefix), Field({ label: 'Access key ID' }, f.access_key), Field({ label: 'Secret access key', hint: 'Stored on this computer only and never shown again. Use a key limited to this bucket.' }, f.secret_key)),
      h('div', { class: 'mt-4 flex flex-wrap gap-2' }, save, o.configured ? test : null, o.configured ? off : null),
      h('p', { class: 'mt-3 text-xs text-muted' }, 'Old copies in the bucket are not deleted by Shiplane: use the bucket\'s own lifecycle rule to expire them.'));
  }

  function secretModal(r) {
    const c = Console({ lines: [`database: ${r.name}\nuser:     ${r.user || r.name}\npassword: ${r.password}\n\n${r.url}`] });
    openModal({ title: 'Database created', description: 'Copy these now. The password is not stored anywhere and cannot be shown again.', body: c.el,
      actions: [Button({ label: 'Copy connection string', icon: 'copy', onClick: () => { navigator.clipboard && navigator.clipboard.writeText(r.url); toast('Copied', { tone: 'ok', timeout: 1500 }); } })] });
  }

  let tab = (location.hash.match(/[?&]tab=(\w+)/) || [])[1] || 'postgres';           // links (the Manual) can open a tab

  /** {label, tone} for an engine's tab: is its server installed and running. */
  function engineState(e) {
    const eng = data.engines;
    if (e === 'postgres') return { label: 'installed, running', tone: 'ok' };
    if (!eng) return { label: 'unknown', tone: 'neutral' };
    if (e === 'mysql') { const m = eng.mysql || {}; return !m.installed ? { label: 'not installed', tone: 'neutral' } : m.running ? { label: 'installed, running', tone: 'ok' } : { label: 'installed, stopped', tone: 'warn' }; }
    const c = eng.mssql || [];
    return !c.length ? { label: 'not installed', tone: 'neutral' } : c.some((x) => x.running) ? { label: 'installed, running', tone: 'ok' } : { label: 'installed, stopped', tone: 'warn' };
  }

  function createCard(e) {
    const containers = ((data.engines && data.engines.mssql) || []).filter((c) => c.running);
    const nameInput = Input({ placeholder: 'reports', mono: true });
    const userInput = Input({ placeholder: 'same as the name', mono: true, autocomplete: 'off' });
    const pwInput = Input({ placeholder: 'generated', mono: true, autocomplete: 'new-password' });
    pwInput.style.webkitTextSecurity = 'disc';
    const container = Select({ options: containers.map((c) => ({ value: c.name, label: c.name })), value: containers.length ? containers[0].name : '' });
    const saPw = Input({ placeholder: 'SQL Server administrator (sa) password', mono: true, autocomplete: 'off' });
    saPw.style.webkitTextSecurity = 'disc';
    const hint = e === 'postgres' ? 'A Postgres database with its own login. Apps you deploy with the database switch create theirs automatically.'
      : e === 'mysql' ? 'A MySQL / MariaDB database and a user that can only use it.'
        : 'A SQL Server database and a login that owns it, created in the container with its sa password (used once, never stored).';
    const create = Button({ label: 'Create database', icon: 'plus', variant: 'primary', onClick: () => busy(create, async () => {
      const body = { name: nameInput.value.trim(), user: userInput.value.trim(), password: pwInput.value };
      if (e === 'mssql') { body.container = container.value; body.sa_password = saPw.value; }
      const r = await attempt(api(e === 'postgres' ? '/api/databases/create' : `/api/dbengines/${e}/create`, body));
      if (r) { secretModal(r); await load(); }
    }) });
    return Card({ title: withHelp('New database', 'databases'), subtitle: hint },
      h('div', { class: 'space-y-4' },
        e === 'mssql' ? h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'SQL Server container' }, container), Field({ label: 'sa password' }, saPw)) : null,
        h('div', { class: 'flex flex-col gap-3 sm:flex-row sm:items-end' }, h('div', { class: 'sm:w-72' }, Field({ label: 'Name', hint: 'Start with a letter; a-z, 0-9, _' }, nameInput)),
          h('div', { class: 'sm:w-56' }, Field({ label: 'User', hint: 'Optional; same rules' }, userInput)),
          h('div', { class: 'sm:w-56' }, Field({ label: 'Password', hint: 'Optional; 8-64 chars, no quotes, $ or spaces' }, pwInput)), create)));
  }

  function mysqlTab(m) {
    if (!m || !m.installed) return [Card({ title: 'MySQL / MariaDB is not installed', subtitle: 'Install MariaDB to create MySQL databases here.' }, NeedsBar('mariadb', 'Install it here, then come back to this tab.'))];
    if (!m.running) return [Alert({ tone: 'warn', title: 'MariaDB is installed but stopped' }, 'Start it on the Server page to see and create its databases.')];
    return [createCard('mysql'), Card({ title: 'Databases', bodyClass: '' }, Table({
      columns: [
        { label: 'Database', render: (d) => h('span', { class: 'font-medium' }, d.name) },
        { label: 'Size', class: 'text-muted', render: (d) => bytes(d.size) },
        { label: '', class: 'text-right', render: (d) => h('div', { class: 'flex justify-end gap-2' },
          Button({ label: 'Back up', icon: 'download', size: 'sm', onClick: function () { busy(this, async () => { const r = await attempt(api('/api/databases/backup', { name: d.name, engine: 'mysql' }), (x) => `Backup saved on the server: ${x.file} (${bytes(x.size)})`); if (r) await load(); }); } }),
          Button({ label: 'Drop', icon: 'trash', size: 'sm', variant: 'danger-soft', onClick: async () => {
            const c = await confirmDialog({ title: `Drop ${d.name}?`, message: 'The database, its data and its user of the same name are deleted. Take a backup first if you may need it.', confirmLabel: 'Drop database', requireText: d.name });
            if (c.confirmed && await attempt(api('/api/dbengines/mysql/drop', { name: d.name, confirm: d.name }), `Dropped ${d.name}`)) await load();
          } })) },
      ],
      rows: m.databases, empty: EmptyState({ icon: 'database', title: 'No databases', body: 'Create one above.' }) }))];
  }

  function mssqlTab(list) {
    if (!list.length) return [Card({ title: 'No SQL Server container found' }, h('p', { class: 'text-sm text-muted' }, 'SQL Server runs in Docker here. Start a container from an image whose name contains "mssql" (mcr.microsoft.com/mssql/server) and it shows up.'))];
    const name = Input({ placeholder: 'database to drop', mono: true });
    const sa = Input({ placeholder: 'sa password', mono: true, autocomplete: 'off' });
    sa.style.webkitTextSecurity = 'disc';
    const which = Select({ options: list.map((c) => ({ value: c.name, label: c.name })), value: list[0].name });
    const drop = Button({ label: 'Drop database', icon: 'trash', variant: 'danger-soft', onClick: async () => {
      const c = await confirmDialog({ title: `Drop ${name.value}?`, message: 'The SQL Server database and its login are deleted.', confirmLabel: 'Drop database', requireText: name.value.trim() || 'confirm' });
      if (c.confirmed && await attempt(api('/api/dbengines/mssql/drop', { container: which.value, sa_password: sa.value, name: name.value.trim(), confirm: name.value.trim() }), `Dropped ${name.value}`)) await load();
    } });
    return [
      list.some((c) => c.running) ? createCard('mssql') : Alert({ tone: 'warn', title: 'The SQL Server container is stopped' }, 'Start it on the Server page to create databases.'),
      Card({ title: 'Containers', subtitle: 'Shiplane cannot list SQL Server databases without the sa password: browse them with SSMS or Azure Data Studio through an SSH tunnel.' },
        h('ul', { class: 'space-y-2 text-sm' }, list.map((c) => h('li', { class: 'flex flex-wrap items-center gap-2' }, Badge(c.running ? 'running' : 'stopped', c.running ? 'ok' : 'warn', { dot: true }), h('span', { class: 'font-medium' }, c.name), h('span', { class: 'text-muted' }, `${c.image} (${c.status})`))))),
      Card({ title: 'Drop a database' }, h('div', { class: 'grid gap-3 sm:grid-cols-4 sm:items-end' }, Field({ label: 'Container' }, which), Field({ label: 'Database' }, name), Field({ label: 'sa password' }, sa), drop))];
  }

  function postgresTab() {
    const siteDb = new Set((store.status ? store.status.sites : []).filter((s) => s.db).map((s) => 'app_' + s.name.replace(/-/g, '_')));
    return [
      createCard('postgres'),
      Card({ title: 'Databases', bodyClass: '' },
        Table({
          columns: [
            { label: 'Database', render: (d) => h('div', {}, h('p', { class: 'font-medium' }, d.name), siteDb.has(d.name) ? h('span', { class: 'text-xs text-muted' }, 'used by a site') : null) },
            { label: 'Owner', class: 'text-muted', render: (d) => d.owner },
            { label: 'Size', class: 'text-muted', render: (d) => bytes(d.size) },
            { label: '', class: 'text-right', render: (d) => h('div', { class: 'flex justify-end gap-2' },
              Button({ label: 'Back up', icon: 'download', size: 'sm', disabled: d.name === 'postgres', onClick: function () { busy(this, async () => { const r = await attempt(api('/api/databases/backup', { name: d.name }), (x) => `Backup saved on the server: ${x.file} (${bytes(x.size)})`); if (r) await load(); }); } }),
              d.name === 'postgres' ? null : Button({ label: 'Drop', icon: 'trash', size: 'sm', variant: 'danger-soft', onClick: async () => {
                const c = await confirmDialog({ title: `Drop ${d.name}?`, message: siteDb.has(d.name) ? 'A deployed site uses this database. It will break until you recreate it.' : 'The database and all its data are deleted. Take a backup first if you may need it.', confirmLabel: 'Drop database', requireText: d.name });
                if (c.confirmed && await attempt(api('/api/databases/drop', { name: d.name, confirm: d.name }), `Dropped ${d.name}`)) await load();
              } })) },
          ],
          rows: data.databases,
          empty: EmptyState({ icon: 'database', title: 'No databases', body: 'Create one above.' }),
        }))];
  }

  // Backups have their own tab: today they cover the PostgreSQL databases only, and the tab says so.
  function backupsTab() {
    return [
      Alert({ tone: 'info', title: 'PostgreSQL and MySQL / MariaDB' }, 'Nightly backups, the off-server copy, test restore and restore cover PostgreSQL and MySQL / MariaDB databases. SQL Server databases are not backed up by Shiplane yet: dump them yourself before risky changes.'),
      scheduleCard(),
      offsiteCard(),
      Card({ title: 'Backups', subtitle: 'Gzipped SQL dumps kept on the server in /var/backups/coderunner. Download them to keep a copy off the server.', bodyClass: '' },
        Table({
          columns: [
            { label: 'File', render: (b) => h('code', { class: 'font-mono text-xs' }, b.file) },
            { label: 'Created (server time)', class: 'text-muted', render: (b) => b.time },
            { label: 'Size', class: 'text-muted', render: (b) => bytes(b.size) },
            { label: '', class: 'text-right', render: (b) => h('div', { class: 'flex justify-end gap-2' },
              Button({ label: 'Download', icon: 'download', size: 'sm', href: `/api/databases/download?file=${encodeURIComponent(b.file)}` }),
              Button({ label: 'Test restore', icon: 'check', size: 'sm', onClick: async () => { const r = await attempt(api('/api/databases/verify', { file: b.file })); if (r) showJob('Test restore', r.job); } }),
              Button({ label: 'Restore', icon: 'undo', size: 'sm', onClick: async () => {
                const db = b.file.replace(/-\d{8}-\d{6}(\.my)?\.sql\.gz$/, '');
                const c = await confirmDialog({ title: `Restore ${db} from this backup?`, message: `${b.file}: the current data of ${db} is replaced by the backup. A safety backup of the current data is taken first. Connected apps are disconnected for a moment.`, confirmLabel: 'Restore', requireText: db });
                if (!c.confirmed) return;
                const r = await attempt(api('/api/databases/restore', { name: db, file: b.file, confirm: db }));
                if (r) showJob(`Restore ${db}`, r.job);
              } }),
              Button({ icon: 'trash', iconOnly: true, size: 'sm', variant: 'ghost', title: 'Delete backup', onClick: async () => {
                const c = await confirmDialog({ title: 'Delete this backup?', message: b.file, confirmLabel: 'Delete backup' });
                if (c.confirmed && await attempt(api('/api/databases/backup-delete', { file: b.file }), 'Backup deleted')) await load();
              } })) },
          ],
          rows: data.backups,
          empty: EmptyState({ icon: 'download', title: 'No backups yet', body: 'Use "Back up" on a database in the PostgreSQL tab, or switch on the nightly schedule above.' }),
        }))];
  }

  function draw() {
    if (error) { body.replaceChildren(Alert({ tone: 'bad', title: 'Could not read the databases' }, error, h('div', { class: 'mt-3' }, Button({ label: 'Try again', size: 'sm', icon: 'refresh', onClick: load })))); return; }
    if (!data) { body.replaceChildren(Card({}, Skeleton(4))); return; }
    const eng = data.engines;
    const names = { postgres: ['PostgreSQL', 'postgres'], mysql: ['MySQL / MariaDB', 'mariadb'], mssql: ['SQL Server', 'mssql'] };
    const tabs = Object.entries(names).map(([id, [label, logo]]) => {
      const st = engineState(id);
      return { id, text: `${label} · ${st.label}`, label: h('span', { class: 'inline-flex items-center gap-2' }, Logo(logo, 'h-4 w-4') || icon('database', 'h-4 w-4'), label, Badge(st.label, st.tone)) };
    });
    tabs.push({ id: 'backups', text: `Backups (${data.backups.length})`, label: h('span', { class: 'inline-flex items-center gap-2' }, icon('download', 'h-4 w-4'), 'Backups', Badge(String(data.backups.length), 'neutral')) });
    const content = tab === 'backups' ? backupsTab() : tab === 'postgres' ? postgresTab() : tab === 'mysql' ? mysqlTab(eng && eng.mysql) : mssqlTab((eng && eng.mssql) || []);
    body.replaceChildren(Tabs({ tabs, active: tab, onChange: (t) => { tab = t; history.replaceState(null, '', `#/databases?tab=${t}`); draw(); } }), ...content);
  }

  draw();
  load();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' },
    PageHeader({ title: 'Databases', description: 'PostgreSQL, MySQL / MariaDB and SQL Server on the server: one tab per engine with its status, databases and create form.', actions: Button({ label: 'Refresh', icon: 'refresh', onClick: load }) }), body) };
}
