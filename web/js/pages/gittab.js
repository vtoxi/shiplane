// The "Git" tab of a site: connect a repository, deploy the branch now, and set up the webhook so every push deploys by itself.
import { h, fill, Logo, icon, Alert, Badge, Button, Card, Field, Input, KeyValue, Select, Skeleton, busy, confirmDialog, openModal, toast } from '../kit.js';
import { store } from '../store.js';
import { api } from '../api.js';
import { attempt } from '../util.js';
import { trackDeploy } from '../deploydock.js';
import { GitSource } from './gitsource.js';
import { NeedsBar } from '../needs.js';
import { withHelp } from '../help.js';

const BUILDS = [{ value: 'none', label: 'No build: deploy the files as they are' }, { value: 'npm', label: 'npm: npm ci, then npm run build' }, { value: 'commands', label: 'My own commands (one line)' }];
const copy = (text) => { if (navigator.clipboard) navigator.clipboard.writeText(text); toast('Copied', { tone: 'ok', timeout: 1500 }); };

/** A folded section: closed until opened, so the tab shows the status first and the settings only on demand. */
function Fold(title, subtitle, body, { open = false, danger = false } = {}) {
  return h('details', { class: 'card', open },
    h('summary', { class: 'card-header flex cursor-pointer items-center justify-between gap-3', style: 'list-style:none' },
      h('div', {}, h('h2', { class: danger ? 'text-sm text-bad' : 'text-sm' }, title), subtitle ? h('p', { class: 'mt-0.5 text-xs text-muted' }, subtitle) : null),
      icon('chevron', 'h-4 w-4 text-muted')),
    h('div', { class: 'card-body' }, body));
}

function CopyBox(text) {
  return h('div', { class: 'flex items-start gap-2' }, h('pre', { class: 'console min-w-0 flex-1 overflow-x-auto' }, text), Button({ label: 'Copy', icon: 'copy', size: 'sm', onClick: () => copy(text) }));
}

export function GitTab(site) {
  const box = h('div', { class: 'space-y-6' });
  const log = h('div', {});
  const canEdit = () => ['admin', 'super_admin'].includes(store.session.me.role);
  const supported = ['static', 'node', 'php', 'command', 'docker'].includes(site.type);

  async function load() {
    fill(box, Skeleton(3));
    try { draw(await api(`/api/git?name=${encodeURIComponent(site.name)}`)); } catch (e) { fill(box, Alert({ tone: 'bad', title: 'Could not read the Git settings' }, e.message)); }
  }

  function showSecret(g, secret) {
    const url = location.origin + g.hook;
    openModal({ title: 'Webhook details', description: 'Copy the secret now: it is shown only once. If you lose it, make a new one.', size: 'lg',
      body: h('div', { class: 'space-y-4' },
        Field({ label: 'Payload URL' }, CopyBox(url)), Field({ label: 'Secret' }, CopyBox(secret)),
        h('ol', { class: 'list-decimal space-y-1 pl-5 text-sm text-muted' },
          h('li', {}, 'GitHub: repository, Settings, Webhooks, Add webhook. Paste the Payload URL and the Secret, set Content type to application/json, choose "Just the push event".'),
          h('li', {}, 'GitLab: project, Settings, Webhooks. Paste the URL and put the secret in "Secret token". Tick "Push events".'),
          h('li', {}, `Only pushes to ${g.branch} deploy. Several quick pushes become one deploy.`))),
      actions: [{ label: 'Done', variant: 'primary', onClick: (m) => m.close() }] });
  }

  // What this kind of site needs on the server (offered as a one-click install when it is missing).
  function siteNeeds(g) {
    if (site.type === 'docker') return NeedsBar('docker', 'This site is built and run with Docker.');
    if (site.type === 'node' || (g && g.build === 'npm')) return NeedsBar('node', 'Node apps and npm builds need Node.js.');
    return null;
  }

  // Not connected yet: sign in to GitHub/GitLab (or type a public address), pick the repository and connect it in one click.
  function pickForm() {
    const src = GitSource({});
    const go = Button({ label: 'Connect repository', variant: 'primary', onClick: () => busy(go, async () => {
      let pick;
      try { pick = src.get(); } catch (e) { toast(e.message, { tone: 'bad' }); return; }
      const r = await attempt(api(pick.session ? '/api/gitprov/connect' : '/api/git/save', Object.assign({ name: site.name }, pick.git, pick.session ? { session: pick.session, repo: pick.repo } : {})), 'Connected');
      if (!r) return;
      await load();
      if (r.linked && r.linked.hook_note) toast(r.linked.hook_note, { tone: 'info', timeout: 12000 });
      else if (r.secret && !r.linked) showSecret(r, r.secret);
    }) });
    return Card({ title: 'Deploy from Git', subtitle: 'Every deploy gets the newest commit of a branch, builds it and switches the site to it, with the same health check and rollback as always.' },
      h('div', { class: 'space-y-4' }, siteNeeds(), src.el, Alert({ tone: 'info' }, 'The server builds the code as an unprivileged user, never as root. A build still runs the repository\'s own scripts, so connect only repositories you trust.'), h('div', {}, go)));
  }

  function form(g) {
    if (!g) return pickForm();
    const repo = Input({ value: g ? g.repo : '', placeholder: 'https://github.com/you/project.git', mono: true });
    const branch = Input({ value: g ? g.branch : 'main', mono: true });
    const build = Select({ options: BUILDS, value: g ? g.build : 'none' });
    const buildCmd = Input({ value: g ? g.build_cmd || '' : '', placeholder: 'npm ci && npm run build', mono: true });
    const output = Input({ value: g ? g.output : '', placeholder: 'dist (empty = the whole repository)', mono: true });
    const save = Button({ label: g ? 'Save changes' : 'Connect repository', variant: 'primary', onClick: () => busy(save, async () => {
      const r = await attempt(api('/api/git/save', { name: site.name, repo: repo.value, branch: branch.value, build: build.value, build_cmd: buildCmd.value, output: output.value }), g ? 'Saved' : 'Connected');
      if (!r) return;
      await load();
      if (r.secret) showSecret(r, r.secret);
    }) });
    const body = h('div', { class: 'space-y-4' },
        Field({ label: 'Repository address', hint: 'HTTPS for public repositories. For a private one use the SSH address (git@github.com:you/project.git) and a deploy key.' }, repo),
        h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'Branch' }, branch), Field({ label: 'Build' }, build)),
        Field({ label: 'Build commands', hint: 'Only used with "My own commands". Run in the repository as an unprivileged user. Command and Docker sites build themselves when deployed, so leave this on No build for them.' }, buildCmd),
        Field({ label: 'Folder to publish', hint: 'Where the finished site is after the build, for example dist or build. Leave empty to publish the whole repository.' }, output),
        h('div', {}, save));
    return Fold('Repository and build', `${g.repo} · ${g.branch} · ${(BUILDS.find((b) => b.value === g.build) || BUILDS[0]).label.split(':')[0]}`, body);
  }

  function connected(g) {
    const last = g.last;
    const deployNow = Button({ label: 'Deploy now', icon: 'rocket', variant: 'primary', disabled: g.running, onClick: () => busy(deployNow, async () => {
      const r = await attempt(api('/api/git/deploy', { name: site.name }));
      if (!r) return;
      if (r.queued) { toast('A deploy is already running: the newest code goes out right after it', { tone: 'info' }); return; }
      trackDeploy(r.job, site.name, { host: site.hostname, onDone: () => load() });     // progress shows in the floating panel
    }) });
    const result = !last ? Badge('not deployed from Git yet', 'neutral') : last.rolled_back ? Badge('rolled back: failed its health check', 'bad') : last.ok ? Badge('deployed', 'ok', { dot: true }) : Badge('failed', 'bad', { dot: true });
    return h('div', { class: 'space-y-4' }, siteNeeds(g),
      Card({ title: h('span', { class: 'inline-flex items-center gap-2' }, Logo(/gitlab/.test(g.repo) ? 'gitlab' : /github/.test(g.repo) ? 'github' : 'git', 'h-4 w-4'), withHelp('Connected repository', 'deploy-from-git')), actions: deployNow },
        KeyValue([
          ['Repository', h('code', { class: 'font-mono text-xs' }, g.repo)],
          ['Branch', h('code', { class: 'font-mono text-xs' }, g.branch)],
          ['Last deploy', last ? `${new Date(last.at * 1000).toLocaleString()} by ${last.by}` : null],
          ['Commit', last && last.sha ? h('span', {}, h('code', { class: 'font-mono text-xs' }, last.sha.slice(0, 8)), ' ', last.message) : null],
          ['Result', result]])),
      log,
      Fold('Deploy on every push', `GitHub or GitLab calls this site whenever you push to ${g.branch}`,
        h('div', { class: 'space-y-3' }, Field({ label: 'Payload URL' }, CopyBox(location.origin + g.hook)),
          canEdit() ? Button({ label: 'Show setup steps with a new secret', size: 'sm', onClick: async () => {
            const c = await confirmDialog({ title: 'Make a new secret?', message: 'The old secret stops working at once. Put the new one into GitHub or GitLab.', confirmLabel: 'Make new secret', tone: 'primary' });
            if (!c.confirmed) return;
            const r = await attempt(api('/api/git/save', { name: site.name, repo: g.repo, branch: g.branch, build: g.build, build_cmd: g.build_cmd, output: g.output, new_secret: true }));
            if (r && r.secret) showSecret(r, r.secret);
          } }) : null)),
      canEdit() ? form(g) : null,
      canEdit() ? Fold('Private repository', g.key ? 'A deploy key exists' : 'Needed only when the repository is private', h('div', { class: 'space-y-3' },
        h('p', { class: 'text-sm text-muted' }, 'Create a key, then add its public half to the repository as a read-only deploy key (GitHub: Settings, Deploy keys) and use the git@... address.'),
        Button({ label: g.key ? 'Show the deploy key' : 'Create a deploy key', size: 'sm', onClick: async () => {
          const r = await attempt(api('/api/git/key', { name: site.name }));
          if (r) openModal({ title: 'Deploy key', description: 'Add this public key to the repository. Keep "Allow write access" off.', size: 'lg', body: CopyBox(r.public_key), actions: [{ label: 'Done', variant: 'primary', onClick: (m) => m.close() }] });
          await load();
        } }))) : null,
      canEdit() ? Fold('Disconnect', 'Stops Git deploys. The site keeps running its current release.', h('div', { class: 'flex flex-wrap items-center justify-between gap-3' },
        h('p', { class: 'text-sm text-muted' }, 'Removes the server\'s copy of the code and the deploy key.'),
        Button({ label: 'Disconnect', variant: 'danger-soft', onClick: async () => {
          const c = await confirmDialog({ title: 'Disconnect from Git?', message: 'The webhook stops working. The site keeps running its current release.', confirmLabel: 'Disconnect' });
          if (c.confirmed && await attempt(api('/api/git/disconnect', { name: site.name }), 'Disconnected')) await load();
        } })), { danger: true }) : null);
  }

  function draw(g) {
    if (!supported) { fill(box, Alert({ tone: 'info', title: 'Not available for this kind of site' }, 'Git deploys work for static, Node, PHP, command and Docker sites.')); return; }
    if (g.connected) fill(box, connected(g));
    else fill(box, canEdit() ? form(null) : Alert({ tone: 'info', title: 'Not connected' }, 'Ask a super admin to connect a repository.'));
  }

  load();
  return h('div', {}, box);
}
