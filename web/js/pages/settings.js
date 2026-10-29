import { h, fill, Skeleton, Button, Badge, Card, Table, EmptyState, Alert, PageHeader, Field, Input, Segmented, Select, Switch, Console, KeyValue, BrandTitle, icon, busy, confirmDialog, openModal, toast } from '../kit.js';
import { store, loadSession, refresh } from '../store.js';
import { api } from '../api.js';
import { attempt, getTheme, setTheme } from '../util.js';
import { withHelp } from '../help.js';

/** Two-factor setup / status. Used in Settings and on the forced-setup screen. */
export function TwoFactorCard() {
  const me = store.session.me;
  const twofa = h('div', {});
  function drawTwofa() {
    if (me.totp) {
      const pw = Input({ type: 'password', autocomplete: 'current-password' });
      const code = Input({ placeholder: '123456 or a recovery code', mono: true, autocomplete: 'one-time-code' });
      const off = Button({ label: 'Turn off two-factor', variant: 'danger-soft', onClick: () => busy(off, async () => {
        if (await attempt(api('/api/auth/2fa/disable', { password: pw.value, code: code.value }), 'Two-factor turned off')) location.reload();
      }) });
      twofa.replaceChildren(h('div', { class: 'mb-4 flex flex-wrap items-center gap-3' }, Badge('On', 'ok', { dot: true }), h('span', { class: 'text-sm text-muted' }, `${me.recovery_left} recovery codes left`)),
        h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'Password' }, pw), Field({ label: 'Authenticator or recovery code' }, code)), h('div', { class: 'mt-4' }, off));
      return;
    }
    const begin = Button({ label: 'Set up two-factor', icon: 'shield', variant: 'primary', onClick: () => busy(begin, async () => {
      const r = await attempt(api('/api/auth/2fa/start', {}));
      if (!r) return;
      const code = Input({ placeholder: '6-digit code', mono: true, autocomplete: 'one-time-code' });
      const on = Button({ label: 'Verify and turn on', variant: 'primary', onClick: () => busy(on, async () => {
        const res = await attempt(api('/api/auth/2fa/enable', { code: code.value }));
        if (!res) return;
        const c = Console({ lines: [res.codes.join('\n')] });
        openModal({ title: 'Save your recovery codes', description: 'Each works once if you lose your phone. They are shown only now: store them in a password manager.', body: c.el, onClose: () => location.reload(),
          actions: [Button({ label: 'Copy codes', icon: 'copy', onClick: () => { navigator.clipboard && navigator.clipboard.writeText(res.codes.join('\n')); toast('Copied', { tone: 'ok', timeout: 1500 }); } })] });
      }) });
      twofa.replaceChildren(
        h('p', { class: 'mb-3 text-sm' }, 'In your authenticator app (Google Authenticator, Microsoft Authenticator, Authy, 1Password) add an account, choose "Enter a setup key" and type this key (time based):'),
        Console({ lines: [r.secret] }).el,
        h('div', { class: 'mt-4 flex flex-col gap-3 sm:flex-row sm:items-end' }, h('div', { class: 'sm:w-48' }, Field({ label: 'Code shown in the app' }, code)), on));
    }) });
    twofa.replaceChildren(h('p', { class: 'mb-4 text-sm text-muted' }, 'Off. With two-factor on, signing in also needs a 6-digit code from an app on your phone, so a stolen password alone is not enough.'), begin);
  }

  drawTwofa();
  return Card({ title: 'Two-factor sign-in', subtitle: 'Authenticator app (TOTP)' }, twofa);
}

/** Shown instead of the app when the administrator requires two-factor and this account has none yet. */
export function TwoFactorGate() {
  return h('div', { class: 'mx-auto max-w-lg space-y-4 px-4 py-10 animate-fade-up' },
    h('h1', { class: 'text-xl' }, 'Set up two-factor sign-in'),
    Alert({ tone: 'warn' }, 'Your administrator requires two-factor sign-in for every account. Set it up now to continue.'),
    TwoFactorCard(),
    Button({ label: 'Sign out', variant: 'ghost', onClick: async () => { await api('/api/auth/logout', {}).catch(() => {}); location.reload(); } }));
}

/** Super admin: require two-factor for everyone. */
function PolicyCard() {
  const s = store.session;
  const sw = Switch({ label: 'Require two-factor for everyone', description: s.require2fa_env ? 'Forced by the server settings (CODERUNNER_REQUIRE_2FA).' : 'People without it are asked to set it up at their next sign-in and cannot use the app until they do. API keys are not affected.', checked: s.require2fa,
    onChange: async (v) => { if (!(await attempt(api('/api/policy', { require_2fa: v }), v ? 'Two-factor is now required' : 'Two-factor is optional again'))) sw.set(!v); else s.require2fa = v; } });
  if (s.require2fa_env) sw.querySelector('button').disabled = true;
  return Card({ title: 'Security policy' }, sw);
}

/** Settings, or (view 'integrations') the Integrations page: the same cards, split so connecting providers lives in one place. */
export function SsoCard() {
  const box = h('div', {}, Skeleton(3));
  (async () => {
    const c = await api('/api/sso');
    const on = Switch({ label: 'Single sign-on (OpenID Connect)', description: 'Lets people sign in with Google, Microsoft, Okta, Keycloak and similar. Only people who already have an account here can sign in; SSO never creates accounts.', checked: c.enabled });
    const f = { issuer: Input({ value: c.issuer, placeholder: 'https://accounts.google.com', mono: true }), id: Input({ value: c.client_id, mono: true }),
      secret: Input({ type: 'password', placeholder: c.has_secret ? 'saved - leave empty to keep' : '', autocomplete: 'off' }), label: Input({ value: c.label || 'SSO' }), base: Input({ value: c.redirect_base, placeholder: 'https://shiplane.example.com (only if behind a proxy)', mono: true }) };
    const save = Button({ label: 'Save', variant: 'primary', onClick: () => busy(save, async () => {
      await attempt(api('/api/sso', { enabled: on.get(), issuer: f.issuer.value, client_id: f.id.value, client_secret: f.secret.value, label: f.label.value, redirect_base: f.base.value }), 'Saved');
    }) });
    box.replaceChildren(Card({ title: 'Single sign-on', subtitle: `Register this redirect address with your provider: ${(c.redirect_base || location.origin)}${c.callback}` }, on,
      h('div', { class: 'mt-4 grid gap-4 sm:grid-cols-2' }, Field({ label: 'Issuer address' }, f.issuer), Field({ label: 'Button label' }, f.label), Field({ label: 'Client ID' }, f.id), Field({ label: 'Client secret' }, f.secret), Field({ label: 'Public address of this app' }, f.base)),
      h('div', { class: 'mt-4' }, save)));
  })().catch((e) => box.replaceChildren(Alert({ tone: 'bad', title: 'Could not load' }, e.message)));
  return box;
}

export function SettingsPage({ view = 'settings' } = {}) {
  const s = store.session.server;
  const me = store.session.me;
  const isSuper = me.role === 'super_admin';
  const loginMode = store.session.auth === 'login';
  const cfgBox = h('div', {});
  const keysBox = h('div', {});
  function carePrefsCard() {
    const box = h('div', {});
    Promise.all([api('/api/watch'), api('/api/heal')]).then(([w, f]) => {
      const alertsSet = w.kinds.map((i) => ({ ...i, sw: Switch({ label: i.label, checked: i.on }) }));
      const healSet = f.kinds.map((i) => ({ ...i, sw: Switch({ label: i.label, checked: i.on }) }));
      const save = Button({ label: 'Save', variant: 'primary', disabled: !isSuper, onClick: () => busy(save, async () => {
        const values = (set) => Object.fromEntries(set.map((i) => [i.id, i.sw.get()]));
        const a = await attempt(api('/api/watch', { values: values(alertsSet) }));
        const b = await attempt(api('/api/heal', { values: values(healSet) }), 'Saved');
        return a && b;
      }) });
      box.replaceChildren(h('div', { class: 'space-y-6' },
        h('div', { class: 'space-y-3' }, h('div', {}, h('p', { class: 'text-sm font-medium' }, 'Warn me about'), h('p', { class: 'text-xs text-muted' }, 'Sent to your alert channels above, once per problem, with an all-clear when it is over.')), alertsSet.map((i) => i.sw)),
        h('div', { class: 'space-y-3' }, h('div', {}, h('p', { class: 'text-sm font-medium' }, 'Fix problems automatically'), h('p', { class: 'text-xs text-muted' }, 'Waits longer after each try, gives up after five tries in an hour, and never touches a site you stopped yourself.')), healSet.map((i) => i.sw)),
        save));
    }).catch((e) => box.replaceChildren(Alert({ tone: 'bad', title: 'Could not read these settings' }, e.message)));
    return Card({ title: 'Server care', subtitle: 'What the server tells you about, and what it fixes by itself. The checklist is on the Server care page.' }, box);
  }
  function perfCard() {
    const box = h('div', {});
    const mb = (v) => (v == null ? 'not available here' : `${v} MB`);
    const clock = (sec) => (sec >= 86400 ? `${Math.floor(sec / 86400)} d ${Math.floor((sec % 86400) / 3600)} h` : sec >= 3600 ? `${Math.floor(sec / 3600)} h ${Math.floor((sec % 3600) / 60)} min` : `${Math.floor(sec / 60)} min`);
    async function draw() {
      const r = await api('/api/perf');
      const st = r.stats;
      const seg = Segmented({ label: 'Performance profile', value: st.profile, onChange: () => {}, options: Object.entries(r.profiles).map(([id, p]) => ({ value: id, label: p.label })) });
      const about = h('p', { class: 'text-sm text-muted' }, r.profiles[st.profile].about);
      seg.addEventListener('click', () => setTimeout(() => { about.textContent = r.profiles[seg.get()].about; }, 0));
      const save = Button({ label: 'Apply', variant: 'primary', disabled: r.forced || !isSuper, onClick: () => busy(save, async () => { if (await attempt(api('/api/perf', { profile: seg.get() }), 'Profile applied')) { await loadSession(); draw(); } }) });
      const free = Button({ label: 'Free memory now', icon: 'refresh', onClick: () => busy(free, async () => { const x = await attempt(api('/api/perf/trim', {}), (y) => `Released ${y.freed_mb} MB`); if (x) draw(); }) });
      box.replaceChildren(h('div', { class: 'space-y-4' },
        r.forced ? Alert({ tone: 'info' }, 'The profile is fixed by the server settings (CODERUNNER_PROFILE).') : null,
        seg, about,
        KeyValue([['This app is using', mb(st.rss_mb)], ['Restarts itself above', st.restart_at_mb ? `${st.restart_at_mb} MB (only when idle)` : 'never'], ['Requests in progress', `${st.busy} of ${st.limit} allowed`],
          ['Threads', String(st.threads)], ['Screen data reused', st.cache_hit_rate == null ? 'no data yet' : `${st.cache_hit_rate}% of reads`], ['Running for', clock(st.uptime)]]),
        h('div', { class: 'flex flex-wrap gap-2' }, save, free)));
    }
    draw().catch((e) => box.replaceChildren(Alert({ tone: 'bad', title: 'Could not read the performance figures' }, e.message)));
    return Card({ title: 'Performance', subtitle: 'How much memory and CPU this app may use. Light is the default and is fast enough for most servers.' }, box);
  }
  function gitLoginCard() {
    const box = h('div', { class: 'space-y-4' });
    api('/api/gitprov').then((r) => {
      const inputs = {};
      r.providers.forEach((p) => { inputs[p.id] = Input({ value: p.client_id, placeholder: p.from_env ? 'set by an environment variable' : 'OAuth app client id', mono: true }); });
      const save = Button({ label: 'Save', variant: 'primary', onClick: () => busy(save, async () => {
        await attempt(api('/api/gitprov/oauth', { values: Object.fromEntries(Object.entries(inputs).map(([k, v]) => [k, v.value])) }), 'Saved');
      }) });
      box.replaceChildren(
        h('p', { class: 'text-sm text-muted' }, 'To pick repositories after signing in with GitHub or GitLab, create a free OAuth app on your own account and paste its client id here (a client id is public, not a secret). Without it you can still use an access token or a public address on the Deploy page.'),
        h('ul', { class: 'list-disc space-y-1 pl-5 text-xs text-muted' },
          h('li', {}, 'GitHub: Settings, Developer settings, OAuth Apps, New. Tick "Enable Device Flow". The callback URL can be anything.'),
          h('li', {}, 'GitLab: Preferences, Applications, add one with scope "api" and untick "Confidential". Device authorization needs GitLab 17.2 or newer.')),
        h('div', { class: 'grid gap-4 sm:grid-cols-2' }, r.providers.map((p) => h('div', { class: 'space-y-3 rounded-lg border border-line p-4' },
          BrandTitle(p.id, p.name, { fallback: 'git', badge: p.device ? Badge('device login on', 'ok', { dot: true }) : Badge('token only', 'neutral') }),
          Field({ label: 'OAuth client id' }, inputs[p.id])))),
        save);
    }).catch((e) => box.replaceChildren(Alert({ tone: 'bad', title: 'Could not read the Git login settings' }, e.message)));
    return Card({ title: withHelp('GitHub and GitLab sign-in', 'deploy-from-git'), subtitle: 'Lets people pick private repositories on the Deploy page' }, box);
  }

  function uptimeCard() {
    const box = h('div', {});
    api('/api/monitor').then((m) => {
      const on = Switch({ label: 'Check site addresses regularly', description: 'Sends a small request (headers only, about 1 KB) to each site and raises an alert after two failed checks in a row. Switch it off to stop all checks.', checked: m.enabled });
      const sel = Select({ options: m.options.map((s) => ({ value: String(s), label: s >= 3600 ? 'Every hour' : s === 60 ? 'Every minute' : `Every ${s / 60} minutes` })), value: String(m.interval) });
      const save = Button({ label: 'Save', variant: 'primary', onClick: () => busy(save, () => attempt(api('/api/monitor', { interval: Number(sel.value), enabled: on.get() }), 'Saved')) });
      box.replaceChildren(h('div', { class: 'space-y-4' }, on, h('div', { class: 'sm:w-64' }, Field({ label: 'How often', hint: 'Every 5 minutes is plenty for most sites. A down site is reported after two failed checks, so a longer gap means a later alert.' }, sel)), save));
    }).catch((e) => box.replaceChildren(Alert({ tone: 'bad', title: 'Could not read the uptime settings' }, e.message)));
    return Card({ title: 'Uptime checks', subtitle: 'How often each site\'s public address is checked' }, box);
  }
  const cfBox = h('div', {});

  // ------------------------------------------------------------ connection
  const mode = Segmented({ label: 'Mode', value: s.mode || 'ssh', onChange: sync, options: [{ value: 'ssh', label: 'Control a server from this computer' }, { value: 'local', label: 'This app runs on the server' }] });
  const host = Input({ value: s.host, mono: true });
  const user = Input({ value: s.user, mono: true });
  const port = Input({ value: String(s.port || 22), mono: true });
  const key = Input({ value: s.key, mono: true });
  const auth = Segmented({ label: 'Login method', value: s.auth || 'key', onChange: sync, options: [{ value: 'key', label: 'SSH key (recommended)' }, { value: 'password', label: 'Password' }] });
  const password = Input({ type: 'password', placeholder: s.hasPassword ? 'saved - type to replace' : '', autocomplete: 'new-password' });
  const domains = Input({ value: (s.domains || []).join(', '), placeholder: 'example.com', mono: true });
  const keyField = h('div', { class: 'sm:col-span-2' }, Field({ label: 'Your SSH private key file', hint: 'Each person uses their own key and user. Never share a private key.' }, key));
  const passwordField = h('div', { class: 'sm:col-span-2' }, Field({ label: 'SSH password', hint: 'Kept on this computer in server.json (owner-only file) and never shown again. A key is safer. The server user also needs passwordless sudo, or must be root.' }, password));
  const sshFields = h('div', { class: 'grid gap-4 sm:grid-cols-2' },
    Field({ label: 'Server address' }, host), Field({ label: 'SSH port', hint: 'Usually 22.' }, port), Field({ label: 'Your server user' }, user),
    h('div', { class: 'sm:col-span-2' }, Field({ label: 'Login method' }, auth)),
    keyField, passwordField);
  const result = h('div', {});
  const body = () => ({ mode: mode.get(), host: host.value, port: port.value, user: user.value, auth: auth.get(), key: key.value, password: password.value, domains: domains.value.split(',') });
  function sync() { sshFields.hidden = mode.get() === 'local'; keyField.hidden = auth.get() === 'password'; passwordField.hidden = auth.get() !== 'password'; }
  const test = Button({ label: 'Test connection', icon: 'link', onClick: () => busy(test, async () => {
    try { const r = await api('/api/test-connection', body()); result.replaceChildren(Alert({ tone: r.ok ? 'ok' : 'bad', title: r.ok ? 'Connected' : 'Could not connect' }, h('pre', { class: 'whitespace-pre-wrap font-mono text-xs' }, r.text))); }
    catch (e) { result.replaceChildren(Alert({ tone: 'bad', title: 'Could not connect' }, e.message)); }
  }) });
  const save = Button({ label: 'Save', variant: 'primary', onClick: () => busy(save, async () => {
    if (await attempt(api('/api/config', body()), 'Settings saved')) { await loadSession(); await refresh({ quiet: true }); }
  }) });

  // -------------------------------------------------------------- api keys
  async function loadKeys() {
    try {
      const { keys } = await api('/api/keys');
      keysBox.replaceChildren(Table({
        columns: [
          { label: 'Name', render: (k) => h('span', { class: 'font-medium' }, k.name) },
          { label: 'Scope', class: 'text-muted', render: (k) => k.scope || 'admin' },
          { label: 'Created', class: 'text-muted', render: (k) => k.created },
          { label: 'Expires', class: 'text-muted', render: (k) => k.expires ? new Date(k.expires * 1000).toLocaleDateString() : 'never' },
          { label: 'Last used', class: 'text-muted', render: (k) => k.last_used || 'never' },
          { label: '', class: 'text-right', render: (k) => Button({ label: 'Revoke', icon: 'trash', size: 'sm', variant: 'danger-soft', onClick: async () => {
            const c = await confirmDialog({ title: `Revoke "${k.name}"?`, message: 'Anything using this key stops working immediately.', confirmLabel: 'Revoke key' });
            if (c.confirmed && await attempt(api('/api/keys/revoke', { id: k.id }), 'Key revoked')) loadKeys();
          } }) },
        ],
        rows: keys,
        empty: EmptyState({ icon: 'lock', title: 'No API keys', body: 'Create one to let another app (for example a mobile app) use this API.' }),
      }));
    } catch (e) { keysBox.replaceChildren(Alert({ tone: 'bad', title: 'Could not read the keys' }, e.message)); }
  }
  const keyName = Input({ placeholder: 'phone' });
  const keyScope = Select({ value: 'admin', options: [{ value: 'admin', label: 'Admin (everything)' }, { value: 'viewer', label: 'Read-only' }, { value: 'deploy', label: 'Deploy only (for CI)' }] });
  const keyDays = Input({ type: 'number', min: 0, value: '0' });
  const createKey = Button({ label: 'Create key', icon: 'plus', variant: 'primary', onClick: () => busy(createKey, async () => {
    const r = await attempt(api('/api/keys/create', { name: keyName.value, scope: keyScope.value, days: Number(keyDays.value) || 0 }));
    if (!r) return;
    keyName.value = '';
    const c = Console({ lines: [r.token] });
    openModal({ title: 'Your new API key', description: 'Copy it now. Only a hash is stored, so it cannot be shown again.', body: c.el,
      actions: [Button({ label: 'Copy', icon: 'copy', variant: 'primary', onClick: () => { navigator.clipboard && navigator.clipboard.writeText(r.token); toast('Copied', { tone: 'ok', timeout: 1500 }); } })] });
    loadKeys();
  }) });

  // ------------------------------------------------------------ cloudflare
  // Connect or disconnect with the person's own Cloudflare account: an API token (Cloudflare has no sign-in flow for apps like this one), kept only in cloudflare.json.
  function drawCloudflare(conn) {
    if (conn.connected) {
      const off = Button({ label: 'Disconnect', variant: 'danger-soft', disabled: conn.source === 'environment', onClick: async () => {
        const c = await confirmDialog({ title: 'Disconnect Cloudflare?', message: 'The saved token is deleted from this computer. Mappings that already exist in Cloudflare stay as they are, and new ones cannot be made until you connect again.', confirmLabel: 'Disconnect' });
        if (c.confirmed && await attempt(api('/api/cloudflare/disconnect', {}), 'Disconnected')) location.reload();
      } });
      fill(cfBox, Alert({ tone: 'ok', title: 'Cloudflare is connected' }, conn.source === 'environment' ? 'The token comes from the CLOUDFLARE_API_TOKEN variable of this app.' : 'Manage its hostnames on the Domains page.'),
        h('div', { class: 'mt-4' }, KeyValue([['Account', conn.account_id], ['Tunnel', conn.tunnel_id], ['Token', conn.source === 'environment' ? 'environment variable' : 'saved on this server (never shown)']])),
        isSuper ? h('div', { class: 'mt-4' }, off, conn.source === 'environment' ? h('span', { class: 'ml-3 text-xs text-muted' }, 'Remove the variable to disconnect.') : null) : null);
      return;
    }
    if (!isSuper) { fill(cfBox, Alert({ tone: 'info', title: 'Cloudflare is not connected' }, 'Ask a super admin to connect it.')); return; }
    const token = Input({ placeholder: 'Paste the API token', mono: true });
    token.style.webkitTextSecurity = 'disc';
    const next = h('div', { class: 'mt-4 space-y-4' });
    const check = Button({ label: 'Check the token', variant: 'primary', onClick: () => busy(check, () => discover('')) });
    async function discover(accountId) {
      const d = await attempt(api('/api/cloudflare/discover', { token: token.value, account_id: accountId }));
      if (!d) return;
      const acct = d.accounts.length > 1 ? Select({ options: d.accounts.map((a) => ({ value: a.id, label: a.name })), value: d.account_id || d.accounts[0].id, onChange: (v) => discover(v) }) : null;
      const acctText = Input({ value: d.account_id, placeholder: '32-character account id', mono: true });
      const tun = Select({ options: d.tunnels.map((t) => ({ value: t.id, label: `${t.name} (${t.status || 'unknown'})` })), value: d.tunnels[0] && d.tunnels[0].id });
      const go = Button({ label: 'Connect', variant: 'primary', disabled: !d.tunnels.length, onClick: () => busy(go, async () => {
        if (await attempt(api('/api/cloudflare/connect', { token: token.value, account_id: acct ? acct.value : acctText.value, tunnel_id: tun.value }), 'Cloudflare connected')) location.reload();
      }) });
      fill(next, Alert({ tone: 'ok', title: 'The token works' }, d.accounts.length ? `Account: ${d.accounts.map((a) => a.name).join(', ')}` : 'It cannot list accounts, so type the account id (Cloudflare dashboard, right column of any domain).'),
        acct ? Field({ label: 'Account' }, acct) : Field({ label: 'Account id' }, h('div', { class: 'flex gap-2' }, acctText, Button({ label: 'Look up tunnels', onClick: () => discover(acctText.value) }))),
        d.tunnels.length ? Field({ label: 'Tunnel', hint: 'The Cloudflare Tunnel that reaches this server (cloudflared runs it).' }, tun) : Alert({ tone: 'warn', title: 'No tunnel found' }, 'Create one in Cloudflare (Zero Trust, Networks, Tunnels) and install cloudflared on the server, then check again.'),
        h('div', {}, go));
    }
    fill(cfBox, Alert({ tone: 'info', title: 'Cloudflare is not connected' }, 'Needed only to map your own domains to this server. Everything else works without it.'),
      h('ol', { class: 'mt-4 list-decimal space-y-1 pl-5 text-sm text-muted' },
        h('li', {}, 'In Cloudflare: My Profile, API Tokens, Create Token, Custom token.'),
        h('li', {}, 'Permissions: Account / Cloudflare Tunnel / Edit, Zone / Zone / Read, Zone / DNS / Edit (add Account / Account Settings / Read to list accounts).'),
        h('li', {}, 'Paste the token here. It is stored only in cloudflare.json on this computer (owner-only) and never shown again.')),
      h('div', { class: 'mt-4 flex gap-2' }, h('div', { class: 'min-w-0 flex-1' }, token), check), next);
  }
  fill(cfBox, Skeleton(2));
  api('/api/cloudflare/connection').then(drawCloudflare).catch((e) => fill(cfBox, Alert({ tone: 'bad', title: 'Could not read the Cloudflare connection' }, e.message)));

  // ---------------------------------------------------------- dns providers
  // Route 53 and DigitalOcean: A records to this server's IP (no tunnel). Credentials are checked, then kept only in dnsproviders.json.
  // Hetzner Cloud: a read-only token so the Sites page can show what the server costs.
  const hzBox = h('div', {}, Skeleton(2));
  api('/api/hetzner').then((c) => {
    const title = withHelp(BrandTitle('hetzner', 'Hetzner Cloud', { fallback: 'server' }), 'live-monitoring-logs-and-billing');
    if (c.connected) {
      const off = Button({ label: 'Disconnect', variant: 'danger-soft', disabled: c.source !== 'screen', onClick: async () => {
        const k = await confirmDialog({ title: 'Disconnect Hetzner?', message: 'The saved token is deleted. The cost card on Sites goes away until you connect again.', confirmLabel: 'Disconnect' });
        if (k.confirmed && await attempt(api('/api/hetzner/disconnect', {}), 'Disconnected')) location.reload();
      } });
      fill(hzBox, Card({ title, subtitle: 'Server cost on the Sites page' }, Alert({ tone: 'ok', title: 'Hetzner is connected' }, c.source === 'screen' ? 'The token is saved on this server and never shown.' : 'The token comes from the server configuration (an override); change it there.'),
        isSuper ? h('div', { class: 'mt-4' }, off) : null));
      return;
    }
    if (!isSuper) { fill(hzBox, Card({ title }, Alert({ tone: 'info', title: 'Hetzner is not connected' }, 'Ask a super admin to connect it.'))); return; }
    const tok = Input({ placeholder: 'Read-only API token', mono: true, autocomplete: 'off' });
    tok.style.webkitTextSecurity = 'disc';
    const go = Button({ label: 'Connect', variant: 'primary', onClick: () => busy(go, async () => {
      const r = await attempt(api('/api/hetzner/connect', { token: tok.value }));
      if (r) { toast(`Hetzner connected: ${r.servers} server(s)`, { tone: 'ok' }); location.reload(); }
    }) });
    fill(hzBox, Card({ title, subtitle: 'Server cost on the Sites page' },
      Alert({ tone: 'info', title: 'Hetzner is not connected' }, 'Optional. Shows the monthly price and an estimate for this month on the Sites page.'),
      h('ol', { class: 'mt-4 list-decimal space-y-1 pl-5 text-sm text-muted' }, h('li', {}, 'Hetzner Cloud console, your project, Security, API tokens, Generate API token.'), h('li', {}, 'Permission: Read. Paste it here.')),
      h('div', { class: 'mt-4 flex gap-2' }, h('div', { class: 'min-w-0 flex-1' }, tok), go)));
  }).catch((e) => fill(hzBox, Alert({ tone: 'bad', title: 'Could not read the Hetzner connection' }, e.message)));

  // Route 53 and DigitalOcean: one card each, like Cloudflare. Their A records point at this server's public IP (found by the server, editable).
  const dnsBox = h('div', { class: 'space-y-6' });
  function drawDnsProviders(conn) {
    const secretInput = (placeholder) => { const i = Input({ placeholder, mono: true, autocomplete: 'off' }); i.style.webkitTextSecurity = 'disc'; return i; };
    const help = {
      route53: ['In AWS IAM: create a user with a policy allowing route53:ListHostedZones, route53:ListResourceRecordSets and route53:ChangeResourceRecordSets.', 'Create an access key for it and paste the key id and secret here.'],
      digitalocean: ['In DigitalOcean: API, Generate New Token, with read and write scope (or custom scopes domain:read and domain:create/delete).', 'Paste the token here.'],
    };
    const how = 'Maps hostnames with an A record to this server. Such sites are served directly with their own certificate, so ports 80 and 443 must be open (DNS hosting, Direct HTTPS).';
    fill(dnsBox, ...conn.providers.map((p) => {
      const title = withHelp(BrandTitle(p.id, p.name, { fallback: 'cloud' }), 'route-53-and-digitalocean');
      if (p.connected) {
        const off = Button({ label: 'Disconnect', variant: 'danger-soft', onClick: async () => {
          const c = await confirmDialog({ title: `Disconnect ${p.name}?`, message: 'The saved credentials are deleted. Records already made stay as they are.', confirmLabel: 'Disconnect' });
          if (c.confirmed && await attempt(api('/api/dnsproviders/disconnect', { provider: p.id }), 'Disconnected')) location.reload();
        } });
        return Card({ title, subtitle: 'A records pointing at this server' },
          Alert({ tone: 'ok', title: `${p.name} is connected` }, 'Manage its hostnames on the Domains page.'),
          h('div', { class: 'mt-4' }, KeyValue([p.key_id ? ['Access key', p.key_id] : null, ['Records point at', conn.ip], [p.key_id ? 'Secret' : 'Token', 'saved on this server (never shown)']].filter(Boolean))),
          isSuper ? h('div', { class: 'mt-4' }, off) : null);
      }
      if (!isSuper) return Card({ title, subtitle: 'A records pointing at this server' }, Alert({ tone: 'info', title: `${p.name} is not connected` }, 'Ask a super admin to connect it.'));
      const ip = Input({ value: conn.ip || conn.detected_ip || '', placeholder: 'public IPv4 of this server', mono: true });
      const f = p.id === 'route53' ? { key_id: Input({ placeholder: 'Access key id (AKIA...)', mono: true }), secret: secretInput('Secret access key') } : { token: secretInput('API token (dop_v1_...)') };
      const go = Button({ label: 'Connect', variant: 'primary', onClick: () => busy(go, async () => {
        const r = await attempt(api('/api/dnsproviders/connect', { provider: p.id, ip: ip.value.trim(), creds: Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value.trim()])) }));
        if (r) { toast(`${p.name} connected: ${r.zones.length} domain(s)`, { tone: 'ok' }); location.reload(); }
      }) });
      return Card({ title, subtitle: 'A records pointing at this server' },
        Alert({ tone: 'info', title: `${p.name} is not connected` }, how),
        h('ol', { class: 'mt-4 list-decimal space-y-1 pl-5 text-sm text-muted' }, help[p.id].map((t) => h('li', {}, t))),
        h('div', { class: 'mt-4 grid gap-3 sm:grid-cols-2' }, ...Object.values(f), Field({ label: 'Server public IP', hint: conn.ip ? 'Shared by Route 53 and DigitalOcean.' : 'Found by the server; change it if the server is behind NAT.' }, ip)),
        h('div', { class: 'mt-4' }, go));
    }));
  }
  fill(dnsBox, Skeleton(2));
  api('/api/dnsproviders').then(drawDnsProviders).catch((e) => fill(dnsBox, Alert({ tone: 'bad', title: 'Could not read the DNS providers' }, e.message)));

  // ---------------------------------------------------------------- alerts
  const alertBox = h('div', {});
  async function loadAlerts() {
    try {
      const a = await api('/api/alerts');
      const tok = Input({ type: 'password', mono: true, placeholder: a.telegram.configured ? 'saved - type to replace' : '123456789:AA...' });
      const chat = Input({ mono: true, value: a.telegram.chat, placeholder: '123456789' });
      const slack = Input({ mono: true, placeholder: a.slack.configured ? 'saved - type to replace' : 'https://hooks.slack.com/services/...' });
      const discord = Input({ mono: true, placeholder: a.discord.configured ? 'saved - type to replace' : 'https://discord.com/api/webhooks/...' });
      const hook = Input({ mono: true, placeholder: a.webhook.configured ? `saved (${a.webhook.host}) - type to replace` : 'https://example.com/shiplane-alerts' });
      const hookSecret = Input({ mono: true, placeholder: a.webhook.signed ? 'saved - type to replace' : 'optional, 16+ characters', autocomplete: 'off' });
      hookSecret.style.webkitTextSecurity = 'disc';
      const withRemove = (input, btn) => (btn ? h('div', { class: 'flex gap-2' }, h('div', { class: 'min-w-0 flex-1' }, input), btn) : input);
      const forget = (label, field) => Button({ label: `Remove ${label}`, size: 'sm', variant: 'ghost', onClick: async () => { if (await attempt(api('/api/alerts', { [field]: '' }), `${label} removed`)) loadAlerts(); } });
      const mailUser = Input({ value: a.email.user, placeholder: 'you@gmail.com', autocomplete: 'off' });
      const mailPw = Input({ mono: true, placeholder: a.email.configured ? 'saved - type to replace' : 'xxxx xxxx xxxx xxxx', autocomplete: 'off' });
      mailPw.style.webkitTextSecurity = 'disc';
      const mailTo = Input({ value: a.email.to, placeholder: a.email.user || 'defaults to your own address' });
      const save = Button({ label: 'Save', variant: 'primary', onClick: () => busy(save, async () => {
        const body = { telegram_chat: chat.value, email_user: mailUser.value, email_to: mailTo.value };
        if (mailPw.value) body.email_password = mailPw.value;
        if (tok.value) body.telegram_token = tok.value;
        if (hook.value) body.webhook_url = hook.value;
        if (hookSecret.value) body.webhook_secret = hookSecret.value;
        if (slack.value) body.slack_url = slack.value;
        if (discord.value) body.discord_url = discord.value;
        if (await attempt(api('/api/alerts', body), 'Alert settings saved')) loadAlerts();
      }) });
      const test = Button({ label: 'Send a test alert', icon: 'play', onClick: () => busy(test, async () => {
        const r = await attempt(api('/api/alerts/test', {}));
        if (r) r.results.forEach((x) => toast(x.ok ? `${x.channel}: delivered` : `${x.channel}: ${x.error}`, { tone: x.ok ? 'ok' : 'bad', timeout: 8000 }));
      }) });
      const clear = Button({ label: 'Remove all', variant: 'ghost', onClick: async () => {
        const c = await confirmDialog({ title: 'Remove the alert channels?', message: 'You stop getting alerts until you set them up again.', confirmLabel: 'Remove' });
        if (c.confirmed && await attempt(api('/api/alerts', { telegram_token: '', telegram_chat: '', webhook_url: '', slack_url: '', discord_url: '', email_user: '' }), 'Alert channels removed')) loadAlerts();
      } });
      const set = (on) => (on ? Badge('set', 'ok', { dot: true }) : Badge('not set', 'neutral'));
      const block = (logo, name, on, fallback, ...kids) => h('div', { class: 'space-y-3 rounded-lg border border-line p-4' }, BrandTitle(logo, name, { fallback, badge: set(on) }), ...kids);
      alertBox.replaceChildren(
        h('p', { class: 'mb-4 text-sm text-muted' }, 'Sent when: a site fails two checks in a row (and when it is back), the disk passes 85% or memory 92%, a backup fails, or a deploy is rolled back automatically. Set up any number of channels.'),
        h('div', { class: 'grid gap-4 lg:grid-cols-2' },
          block('slack', 'Slack', a.slack.configured, 'activity', Field({ label: 'Incoming webhook', hint: 'Slack: Apps, Incoming Webhooks, Add to Slack, pick a channel, copy the URL.' }, withRemove(slack, a.slack.configured && forget('Slack', 'slack_url')))),
          block('discord', 'Discord', a.discord.configured, 'activity', Field({ label: 'Webhook', hint: 'Channel settings, Integrations, Webhooks, New Webhook, Copy Webhook URL.' }, withRemove(discord, a.discord.configured && forget('Discord', 'discord_url')))),
          block('telegram', 'Telegram', a.telegram.configured, 'activity',
            Field({ label: 'Bot token', hint: 'Create a bot with @BotFather and copy its token. Stored on the server, never shown again.' }, tok),
            Field({ label: 'Chat id', hint: 'Send your bot a message, then open https://api.telegram.org/bot<token>/getUpdates and read "chat":{"id":...}.' }, chat)),
          block('gmail', 'E-mail (Gmail)', a.email.configured, 'activity',
            h('p', { class: 'text-xs text-muted' }, 'Also sends new-user activation codes if you tick it. Use an app password (2-step verification on, then myaccount.google.com/apppasswords), not your normal password.'),
            h('div', { class: 'grid gap-3 sm:grid-cols-2' }, Field({ label: 'Your Google address' }, mailUser), Field({ label: 'App password (16 letters)' }, mailPw)),
            Field({ label: 'Send alerts to', hint: 'Leave empty to send them to your own address.' }, mailTo)),
          h('div', { class: 'lg:col-span-2' }, block('', 'Other webhook (JSON)', a.webhook.configured, 'link',
            h('div', { class: 'grid gap-3 sm:grid-cols-2' },
              Field({ label: 'Address', hint: 'Gets {event, title, text, site, time} by POST. Mattermost, Teams workflows, n8n, Zapier and your own code work. Must be https.' }, withRemove(hook, a.webhook.configured && forget('webhook', 'webhook_url'))),
              Field({ label: 'Signing secret', hint: 'Optional. Each request then carries X-Shiplane-Signature: sha256=HMAC of the body.' }, hookSecret))))),
        h('div', { class: 'mt-4 flex flex-wrap items-center gap-2' }, save, test, clear));
    } catch (e) { alertBox.replaceChildren(Alert({ tone: 'bad', title: 'Could not read the alert settings' }, e.message)); }
  }

  const theme = Segmented({ label: 'Theme', value: getTheme(), onChange: setTheme, options: [{ value: 'system', label: 'System' }, { value: 'light', label: 'Light' }, { value: 'dark', label: 'Dark' }] });
  const oldPw = Input({ type: 'password', autocomplete: 'current-password' });
  const newPw = Input({ type: 'password', autocomplete: 'new-password', placeholder: 'At least 10 characters' });
  const changePw = Button({ label: 'Change password', variant: 'primary', onClick: () => busy(changePw, async () => {
    if (await attempt(api('/api/auth/change-password', { old: oldPw.value, new: newPw.value }), 'Password changed')) { oldPw.value = ''; newPw.value = ''; }
  }) });
  const accountCard = loginMode ? Card({ title: 'Your account', subtitle: `${me.email} (${me.role.replace('_', ' ')})` },
    h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'Current password' }, oldPw), Field({ label: 'New password' }, newPw)), h('div', { class: 'mt-4' }, changePw)) : null;

  if (isSuper) loadKeys();
  if (isSuper) loadAlerts();
  sync();
  const apiKeysCard = isSuper ? Card({ title: 'API keys', subtitle: 'Let another program (for example a mobile app) call this API with "Authorization: Bearer <key>". See the Manual for the endpoint list.' },
    h('div', { class: 'mb-4 flex flex-col gap-3 sm:flex-row sm:items-end' }, h('div', { class: 'sm:w-64' }, Field({ label: 'Key name' }, keyName)), h('div', { class: 'sm:w-48' }, Field({ label: 'Scope' }, keyScope)), h('div', { class: 'sm:w-32' }, Field({ label: 'Expires in (days)', hint: '0 = never' }, keyDays)), createKey), keysBox) : null;
  const connectionCard = isSuper ? Card({ title: 'Connection', subtitle: 'Saved next to the app in server.json' },
    h('div', { class: 'space-y-4' }, h('div', {}, h('span', { class: 'label' }, 'Mode'), mode), sshFields, Field({ label: 'Domains served by the wildcard route', hint: 'Comma separated. Hostnames under these (one level) work without any Cloudflare change.' }, domains),
      h('div', { class: 'flex gap-2' }, test, save), result)) : null;
  const aboutCard = Card({ title: 'About' }, KeyValue([['Version', store.session.version], ['Signed in as', store.session.user], ['Platform', store.session.platform], ['SSH login', store.session.server.mode === 'local' ? 'not needed' : store.session.server.auth === 'password' ? (store.session.server.hasPassword ? 'password saved' : 'password missing') : (store.session.keyFound ? 'key found' : 'key not found at that path')]]));
  // One group at a time, chosen from a menu, so the page never becomes one endless scroll.
  const integrations = view === 'integrations';
  const GROUPS = (integrations ? [
    { id: 'dns', label: 'DNS providers', cards: [Card({ title: withHelp(BrandTitle('cloudflare', 'Cloudflare'), 'cloudflare'), subtitle: 'Tunnel routes and proxied DNS records' }, cfBox), dnsBox] },
    { id: 'cloud', label: 'Cloud providers', cards: [hzBox] },
    { id: 'git', label: 'Git providers', cards: [isSuper ? gitLoginCard() : null] },
    { id: 'alerts', label: 'Notifications', cards: [isSuper ? Card({ title: withHelp('Alert channels', 'alerts'), subtitle: 'Slack, Discord, Telegram, a webhook or e-mail' }, alertBox) : null] },
  ] : [
    { id: 'account', label: 'Account and security', cards: [accountCard, loginMode ? TwoFactorCard() : null, isSuper && loginMode ? PolicyCard() : null, isSuper && loginMode ? SsoCard() : null] },
    { id: 'server', label: 'Server connection', cards: [connectionCard] },
    { id: 'monitoring', label: 'Monitoring', cards: [carePrefsCard(), isSuper ? uptimeCard() : null] },
    { id: 'performance', label: 'Performance', cards: [perfCard()] },
    { id: 'api', label: 'API keys', cards: [apiKeysCard] },
    { id: 'about', label: 'Appearance and about', cards: [Card({ title: 'Appearance' }, theme), aboutCard] },
  ]).map((g) => Object.assign(g, { cards: g.cards.filter(Boolean) })).filter((g) => g.cards.length);
  let active = (location.hash.match(/[?&]tab=(\w+)/) || [])[1];
  if (!GROUPS.some((g) => g.id === active)) active = GROUPS[0].id;
  const menu = h('nav', { class: 'flex gap-1 overflow-x-auto lg:flex-col lg:overflow-visible', 'aria-label': 'Settings sections' });
  const panel = h('div', { class: 'min-w-0 space-y-6' });
  function show(id) {
    active = id;
    history.replaceState(null, '', `#/${view}?tab=${id}`);
    if (window.matchMedia && window.matchMedia('(max-width: 639px)').matches) { menu.replaceChildren(Select({ options: GROUPS.map((g) => ({ value: g.id, label: g.label })), value: active, onChange: show })); menu.style.display = 'block'; } else
    menu.replaceChildren(...GROUPS.map((g) => h('button', { type: 'button', 'aria-current': String(g.id === active), class: 'whitespace-nowrap rounded-md px-3 py-2 text-left text-sm ' + (g.id === active ? 'bg-sunken font-medium text-ink' : 'text-muted hover:bg-sunken hover:text-ink'), onClick: () => show(g.id) }, g.label)));
    panel.replaceChildren(...GROUPS.find((g) => g.id === id).cards);
  }
  show(active);
  return { el: h('div', { class: 'space-y-6 animate-fade-up' },
    integrations ? PageHeader({ title: 'Integrations', description: 'Connect the outside services this Shiplane works with. Each install connects its own accounts; secrets are checked, stored on this server only and never shown again.' })
      : PageHeader({ title: 'Settings', description: 'Your account, how this app reaches the server, and preferences.' }),
    h('div', { class: 'grid grid-cols-1 gap-6 lg:grid-cols-[14rem_1fr]' }, h('aside', { class: 'min-w-0 lg:sticky lg:top-6 lg:self-start' }, menu), panel)) };
}
