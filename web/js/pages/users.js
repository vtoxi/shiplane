import { h, Button, Badge, Card, Table, EmptyState, Alert, PageHeader, Field, Input, Select, Console, icon, busy, confirmDialog, openModal, toast, Skeleton } from '../kit.js';
import { api } from '../api.js';
import { attempt } from '../util.js';

const ROLE_HELP = {
  super_admin: 'Everything, including users, API keys, settings and reboot.',
  admin: 'Deploy, restart, edit environment, databases, Cloudflare mapping.',
  viewer: 'Read-only: see sites, status and activity.',
};
const ROLE_TONE = { super_admin: 'brand', admin: 'info', viewer: 'neutral' };

function codeModal(title, email, code) {
  const c = Console({ lines: [`Account:          ${email}\nActivation code:  ${code}`] });
  openModal({ title, description: 'Give this code to the person (not by email to anyone else). They open the sign-in page, choose "Set up your account" and pick their own password. It works once and expires in 7 days.', body: c.el,
    actions: [Button({ label: 'Copy code', icon: 'copy', variant: 'primary', onClick: () => { navigator.clipboard && navigator.clipboard.writeText(code); toast('Copied', { tone: 'ok', timeout: 1500 }); } })] });
}

export function UsersPage() {
  let users = null;
  let meId = 0;
  let error = null;
  const box = h('div', {});
  const email = Input({ type: 'email', placeholder: 'person@example.com' });
  const role = Select({ value: 'admin', options: [{ value: 'viewer', label: 'Viewer' }, { value: 'admin', label: 'Admin' }, { value: 'super_admin', label: 'Super admin' }] });

  async function load() {
    try { const r = await api('/api/users'); users = r.users; meId = r.me; error = null; } catch (e) { error = e.message; }
    draw();
  }

  let mailReady = false;
  api('/api/alerts').then((a) => { mailReady = a.email.configured; mailNote.style.display = mailReady ? '' : 'none'; }).catch(() => {});
  const mailNote = h('label', { class: 'flex items-center gap-2 text-xs text-muted', style: 'display:none' }, h('input', { type: 'checkbox', id: 'mailcode', checked: true }), 'E-mail them the code');
  const add = Button({ label: 'Add user', icon: 'plus', variant: 'primary', onClick: () => busy(add, async () => {
    const send = mailReady && mailNote.querySelector('input').checked;
    const r = await attempt(api('/api/users/create', { email: email.value, role: role.value, email_code: send }));
    if (r) {
      email.value = '';
      if (send) toast(r.emailed ? 'The code was e-mailed' : `Could not e-mail the code: ${r.email_error}`, { tone: r.emailed ? 'ok' : 'bad', timeout: 8000 });
      codeModal('User added', r.user.email, r.code); await load();
    }
  }) });

  function draw() {
    if (error) { box.replaceChildren(Alert({ tone: 'bad', title: 'Could not read the users' }, error)); return; }
    if (!users) { box.replaceChildren(h('div', { class: 'p-6' }, Skeleton(3))); return; }
    box.replaceChildren(Table({
      columns: [
        { label: 'Account', render: (u) => h('div', {}, h('p', { class: 'font-medium' }, u.email, u.id === meId ? h('span', { class: 'ml-2 text-xs text-muted' }, '(you)') : null),
          h('p', { class: 'text-xs text-muted' }, u.activated ? (u.last_login ? 'Last sign-in ' + u.last_login : 'Never signed in') : 'Waiting for the person to set a password')) },
        { label: 'Role', render: (u) => Select({ value: u.role, options: ['viewer', 'admin', 'super_admin'].map((r) => ({ value: r, label: r.replace('_', ' ') })),
          onChange: async (v) => { if (!(await attempt(api('/api/users/update', { id: u.id, role: v }), 'Role changed'))) await load(); } }) },
        { label: 'Sites', render: (u) => (u.role === 'super_admin' ? h('span', { class: 'text-muted' }, 'all') : Button({ label: u.sites ? u.sites.join(', ') || 'none' : 'all sites', size: 'sm', variant: 'ghost', title: 'Limit this person to some sites', onClick: async () => {
          const box = Input({ value: (u.sites || []).join(', '), placeholder: 'shop, blog (empty = all sites)', mono: true });
          const m = openModal({ title: `Sites for ${u.email}`, description: 'Comma-separated site names. A limited person sees and manages only these sites, and nothing server-wide (databases, firewall, settings).', body: Field({ label: 'Sites' }, box),
            actions: [Button({ label: 'Save', variant: 'primary', onClick: async () => {
              const list = box.value.split(',').map((x) => x.trim()).filter(Boolean);
              if (await attempt(api('/api/users/update', { id: u.id, sites: list.length ? list : null }), 'Sites saved')) { m.close(); await load(); }
            } })] });
        } })) },
        { label: 'Status', render: (u) => h('div', { class: 'flex flex-wrap gap-1.5' }, Badge(u.status === 'active' ? (u.activated ? 'active' : 'invited') : 'disabled', u.status === 'active' ? (u.activated ? 'ok' : 'warn') : 'bad', { dot: true }), u.totp ? Badge('2FA', 'info') : null) },
        { label: '', class: 'text-right', render: (u) => h('div', { class: 'flex flex-wrap justify-end gap-2' },
          Button({ label: u.activated ? 'Reset password' : 'New code', size: 'sm', icon: 'refresh', onClick: async () => {
            const c = await confirmDialog({ title: `${u.activated ? 'Reset the password of' : 'Create a new code for'} ${u.email}?`, message: 'They are signed out everywhere and must set a new password with the code.', confirmLabel: 'Create code', tone: 'primary' });
            if (!c.confirmed) return;
            const r = await attempt(api('/api/users/reset-code', { id: u.id }));
            if (r) { codeModal('New activation code', u.email, r.code); await load(); }
          } }),
          u.totp ? Button({ label: 'Reset 2FA', size: 'sm', icon: 'shield', onClick: async () => {
            const c = await confirmDialog({ title: `Remove two-factor for ${u.email}?`, message: 'Use this when they lost their phone and recovery codes. They are signed out and can sign in with the password alone, then set 2FA up again.', confirmLabel: 'Remove 2FA', tone: 'primary' });
            if (c.confirmed && await attempt(api('/api/users/reset-2fa', { id: u.id }), 'Two-factor removed')) await load();
          } }) : null,
          Button({ label: u.status === 'active' ? 'Disable' : 'Enable', size: 'sm', onClick: async () => {
            if (await attempt(api('/api/users/update', { id: u.id, status: u.status === 'active' ? 'disabled' : 'active' }), u.status === 'active' ? 'Disabled and signed out' : 'Enabled')) await load();
          } }),
          u.id === meId ? null : Button({ icon: 'trash', iconOnly: true, size: 'sm', variant: 'ghost', title: 'Delete user', onClick: async () => {
            const c = await confirmDialog({ title: `Delete ${u.email}?`, message: 'The account is removed and signed out. Their past activity stays in the log.', confirmLabel: 'Delete user', requireText: u.email });
            if (c.confirmed && await attempt(api('/api/users/delete', { id: u.id }), 'User deleted')) await load();
          } })) },
      ],
      rows: users,
      empty: EmptyState({ icon: 'lock', title: 'No users' }),
    }));
  }

  draw();
  load();
  return { el: h('div', { class: 'space-y-6 animate-fade-up' },
    PageHeader({ title: 'Users', description: 'Only people listed here can sign in. Each one sets their own password.', actions: Button({ label: 'Refresh', icon: 'refresh', onClick: load }) }),
    Card({ title: 'Add a user', subtitle: 'They get a one-time activation code to set their password.' },
      h('div', { class: 'flex flex-col gap-3 sm:flex-row sm:items-end' }, h('div', { class: 'sm:w-80' }, Field({ label: 'Email' }, email)), h('div', { class: 'sm:w-48' }, Field({ label: 'Role' }, role)), add), mailNote,
      h('dl', { class: 'mt-4 grid gap-2 text-xs text-muted sm:grid-cols-3' }, Object.entries(ROLE_HELP).map(([k, v]) => h('div', {}, h('dt', { class: 'font-medium text-ink' }, k.replace('_', ' ')), h('dd', {}, v))))),
    Card({ title: 'Accounts', bodyClass: '' }, box)) };
}
