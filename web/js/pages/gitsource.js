// "From Git" source for the Deploy page: sign in to GitHub / GitLab (device login or a pasted token), pick a private or public repository
// and branch, and let the detection suggest how to deploy it. Or type a public repository address by hand.
import { h, fill, Logo, Alert, Badge, Button, Field, Input, Select, Segmented, Skeleton, busy, toast } from '../kit.js';
import { api } from '../api.js';
import { attempt } from '../util.js';
import { store } from '../store.js';

// A refresh keeps the sign-in: the page remembers the session id for this tab (the token itself stays in the server's memory).
const KEEP = 'shiplane.git';
const keep = (v) => { try { v ? sessionStorage.setItem(KEEP, JSON.stringify(v)) : sessionStorage.removeItem(KEEP); } catch (e) { /* storage blocked: sign in again after a refresh */ } };
const kept = () => { try { return JSON.parse(sessionStorage.getItem(KEEP) || 'null'); } catch (e) { return null; } };

const BUILDS = [{ value: 'none', label: 'No build' }, { value: 'npm', label: 'npm run build' }, { value: 'commands', label: 'My own commands' }];

export function GitSource({ onDetect }) {
  const el = h('div', {});
  const state = { provider: 'github', session: '', user: '', repos: [], repo: '', branch: 'main', manual: false, polling: 0 };
  const body = h('div', { class: 'space-y-4' });
  const pick = h('div', { class: 'space-y-4' });
  const devBox = h('div', {});     // the sign-in code panel; `pick` is not on the page until a session exists
  let providers = [];
  const manualUrl = Input({ placeholder: 'https://github.com/you/project.git', mono: true });
  const branchInput = Input({ value: 'main', mono: true });
  const isSuper = store.session.me.role === 'super_admin';
  const build = Segmented({ label: 'Build', options: isSuper ? BUILDS : BUILDS.filter((b) => b.value !== 'commands'), value: 'none', onChange: () => paintBuild() });
  const buildCmd = Input({ placeholder: 'npm ci && npm run build', mono: true });
  const output = Input({ placeholder: 'dist (empty = the whole repository)', mono: true });
  const buildBox = h('div', { class: 'space-y-4' });
  const why = h('div', {});

  function paintBuild() {
    fill(buildBox, h('div', {}, h('span', { class: 'label' }, 'Build (static sites)'), build),
      build.get() === 'commands' ? Field({ label: 'Build commands', hint: 'One line, run in the repository as an unprivileged user (never root). Only super admins can set this.' }, buildCmd) : null,
      build.get() !== 'none' ? Field({ label: 'Folder to publish', hint: 'Where the finished site is after the build, for example dist or build.' }, output) : null);
  }

  function apply(det) {
    build.set(det.build); buildCmd.value = det.build_cmd || ''; output.value = det.output || '';
    fill(why, Alert({ tone: 'info', title: 'Detected' }, det.why, ' You can change anything below.'));
    paintBuild();
    if (onDetect) onDetect(det, state.repo.split('/').pop().toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, ''));
  }

  async function detect() {
    if (!state.session || !state.repo) return;
    fill(why, Skeleton(1));
    const det = await attempt(api(`/api/gitprov/detect?session=${encodeURIComponent(state.session)}&repo=${encodeURIComponent(state.repo)}&branch=${encodeURIComponent(state.branch)}`));
    if (det) apply(det); else fill(why, null);
  }

  async function chooseRepo(name) {
    state.repo = name;
    const r = state.repos.find((x) => x.repo === name);
    state.branch = (r && r.branch) || 'main';
    const res = await attempt(api(`/api/gitprov/branches?session=${encodeURIComponent(state.session)}&repo=${encodeURIComponent(name)}`));
    const names = (res && res.branches.length) ? res.branches : [state.branch];
    if (!names.includes(state.branch)) state.branch = names[0];
    drawRepos(names);
    detect();
  }

  function drawRepos(branches) {
    // searchable dropdown: click to open the whole list, type to filter. Inline styles because the compiled CSS only holds classes already in use.
    const menuCss = 'position:absolute;left:0;right:0;top:100%;z-index:30;margin-top:4px;max-height:16rem;overflow:auto;border:1px solid rgb(var(--c-line));border-radius:8px;background:rgb(var(--c-surface));box-shadow:0 8px 24px rgba(0,0,0,.18)';
    const menu = h('div', { style: menuCss + ';display:none' });
    const repoSel = h('input', { class: 'input font-mono text-xs w-full', value: state.repo, placeholder: 'Click to choose, or type to search your repositories', autocomplete: 'off', role: 'combobox' });
    const show = (q) => {
      const hits = state.repos.filter((r) => r.repo.toLowerCase().includes(q.toLowerCase())).slice(0, 100);
      fill(menu, hits.length ? hits.map((r) => h('div', { style: 'padding:6px 10px;cursor:pointer;font-size:12px;display:flex;justify-content:space-between;gap:8px;' + (r.repo === state.repo ? 'background:rgb(var(--c-brand-soft))' : ''),
        onmousedown: (e) => { e.preventDefault(); menu.style.display = 'none'; repoSel.value = r.repo; if (r.repo !== state.repo) chooseRepo(r.repo); } },
        h('span', { class: 'font-mono' }, r.repo), h('span', { class: 'text-muted' }, r.private ? 'private' : 'public')))
        : h('div', { style: 'padding:8px 10px;font-size:12px', class: 'text-muted' }, 'No repository matches'));
      menu.style.display = 'block';
    };
    repoSel.addEventListener('focus', () => { repoSel.select(); show(''); });
    repoSel.addEventListener('click', () => show(''));
    repoSel.addEventListener('input', () => show(repoSel.value));
    repoSel.addEventListener('blur', () => { menu.style.display = 'none'; repoSel.value = state.repo; });
    const repoField = h('div', { style: 'position:relative' }, repoSel, menu);
    const brSel = Select({ options: (branches || [state.branch]).map((b) => ({ value: b, label: b })), value: state.branch, onChange: (b) => { state.branch = b; detect(); } });
    fill(pick,
      h('div', { class: 'flex flex-wrap items-center gap-2' }, Badge(`${state.provider === 'github' ? 'GitHub' : 'GitLab'}: ${state.user}`, 'ok', { dot: true }),
        Button({ label: 'Sign out', size: 'sm', onClick: () => signOut() })),
      h('div', { class: 'grid gap-4 sm:grid-cols-[1fr_12rem]' }, Field({ label: 'Repository', hint: 'Private repositories are included. The server pulls with a read-only deploy key that is added for you.' }, repoField), Field({ label: 'Branch' }, brSel)),
      why, buildBox);
  }

  async function signedIn(session, user) {
    state.session = session; state.user = user;
    keep({ provider: state.provider, session, user });
    paint();                       // puts `pick` on the page (it shows a skeleton until the repositories arrive)
    const res = await attempt(api(`/api/gitprov/repos?session=${encodeURIComponent(session)}`));
    if (!res) { signOut(); return; }
    state.repos = res.repos;
    if (!state.repos.length) { fill(pick, Alert({ tone: 'warn', title: 'No repositories found' }, 'This account has no repositories the sign-in can see.')); return; }
    await chooseRepo(state.repos[0].repo);
  }

  async function signOut() {
    state.polling++;
    if (state.session) await api('/api/gitprov/forget', { session: state.session }).catch(() => {});
    state.session = ''; state.repos = []; state.repo = '';
    keep(null);
    paint();
  }

  async function device(btn) {
    const r = await busy(btn, () => attempt(api('/api/gitprov/device', { provider: state.provider })));   // the spinner covers only the start request, not the wait for approval
    if (!r) return;
    {
      const mine = ++state.polling;
      const copyCode = async () => {
        try { await navigator.clipboard.writeText(r.user_code); toast('Code copied', { tone: 'ok', timeout: 1500 }); }
        catch (e) { toast('Could not copy: select the code and press Ctrl+C', { tone: 'warn' }); }
      };
      fill(devBox, Alert({ tone: 'info', title: 'Finish the sign-in in your browser' },
        h('p', {}, 'Open ', h('a', { href: r.url, target: '_blank', rel: 'noopener', class: 'underline' }, r.url), ' and enter this code:'),
        h('div', { class: 'mt-2 flex flex-wrap items-center gap-3' },
          h('button', { type: 'button', title: 'Click to copy', class: 'font-mono text-2xl tracking-widest cursor-pointer', onClick: copyCode }, r.user_code),
          Button({ label: 'Copy', icon: 'copy', size: 'sm', onClick: copyCode })),
        h('p', { class: 'mt-2 text-xs' }, 'Waiting for you to approve it...'),
        h('div', { class: 'mt-3' }, Button({ label: 'Cancel', size: 'sm', onClick: () => { state.polling++; paint(); } }))));
      let wait = (r.interval || 5) * 1000;
      const end = Date.now() + (r.expires_in || 900) * 1000;
      while (mine === state.polling && el.isConnected && Date.now() < end) {
        await new Promise((ok) => setTimeout(ok, wait));
        if (mine !== state.polling) return;
        const p = await api('/api/gitprov/poll', { session: r.session }).catch((e) => ({ status: 'error', message: e.message }));
        if (p.status === 'ok') { state.polling++; return signedIn(r.session, p.user); }
        if (p.slow) wait += 5000;
        if (p.status === 'denied' || p.status === 'expired' || p.status === 'error') {
          state.polling++; toast(p.status === 'denied' ? 'The sign-in was refused' : p.message || 'The code expired: try again', { tone: 'bad' }); return paint();
        }
      }
    }
  }

  function paint() {
    fill(devBox, null);
    const info = providers.find((p) => p.id === state.provider) || { device: false };
    const name = state.provider === 'github' ? 'GitHub' : 'GitLab';
    const mode = Segmented({ label: 'Where is the repository?', value: state.manual ? 'url' : state.provider,
      options: providers.map((p) => ({ value: p.id, label: h('span', { class: 'inline-flex items-center gap-1.5' }, Logo(p.id, 'h-4 w-4'), p.name) })).concat([{ value: 'url', label: h('span', { class: 'inline-flex items-center gap-1.5' }, Logo('git', 'h-4 w-4'), 'Public address') }]),
      onChange: (v) => { state.manual = v === 'url'; if (!state.manual && v !== state.provider) { state.provider = v; signOut(); } else paint(); } });
    const token = Input({ placeholder: 'Paste a read-only access token', mono: true, autocomplete: 'off' });
    token.style.webkitTextSecurity = 'disc';                      // masked, but not a password field (browsers would offer to fill a saved login into the field before it)
    const dev = Button({ label: `Sign in with ${name}`, variant: 'primary', icon: 'external', disabled: !info.device, onClick: () => device(dev) });
    const useTok = Button({ label: 'Use token', onClick: () => busy(useTok, async () => {
      const r = await attempt(api('/api/gitprov/token', { provider: state.provider, token: token.value }));
      token.value = '';
      if (r) signedIn(r.session, r.user);
    }) });
    fill(body, mode,
      state.manual ? h('div', { class: 'grid gap-4 sm:grid-cols-[1fr_12rem]' }, Field({ label: 'Repository address', hint: 'The HTTPS address of a public repository.' }, manualUrl), Field({ label: 'Branch' }, branchInput))
        : state.session ? pick
          : h('div', { class: 'space-y-4 rounded-lg border border-line p-4' },
            h('div', {}, h('p', { class: 'flex items-center gap-2 text-sm font-medium' }, Logo(state.provider, 'h-5 w-5'), `Sign in with ${name} to pick a repository`), h('p', { class: 'mt-0.5 text-xs text-muted' }, 'Private repositories included. You approve it on their website with a short code.'),
              h('div', { class: 'mt-3 flex flex-wrap items-center gap-3' }, dev, info.device ? null : h('span', { class: 'text-xs text-muted' }, 'Not set up yet: add the OAuth client id on Integrations, or use a token below.')), devBox),
            h('div', {}, h('p', { class: 'text-sm font-medium' }, 'Or use an access token'),
              h('div', { class: 'mt-2 flex gap-2' }, token, useTok),
              h('p', { class: 'hint' }, 'A read-only token. It is kept in memory for 30 minutes and forgotten once the repository is connected.'))),
      state.manual ? buildBox : null);
    if (state.session && !state.manual) { if (state.repos.length) drawRepos(); else fill(pick, Skeleton(2)); }
    paintBuild();
  }

  api('/api/gitprov').then(async (r) => {
    providers = r.providers; paint();
    const k = kept();
    if (k && providers.some((p) => p.id === k.provider)) {       // still signed in on the server? then pick up where the page left off
      state.provider = k.provider;
      try { await api(`/api/gitprov/repos?session=${encodeURIComponent(k.session)}`); signedIn(k.session, k.user); } catch (e) { keep(null); paint(); }
    }
  }).catch((e) => fill(body, Alert({ tone: 'bad', title: 'Could not load the Git sign-in options' }, e.message)));
  fill(body, Skeleton(2));
  el.append(body);

  return {
    el,
    /** What the Deploy page sends: the sign-in session and repository, or the typed address, plus how to build. */
    get() {
      const git = { branch: state.manual ? branchInput.value.trim() : state.branch, build: build.get(), build_cmd: buildCmd.value, output: output.value };
      if (state.manual) return { git: Object.assign(git, { repo: manualUrl.value.trim() }) };
      if (!state.session) throw new Error('Sign in and pick a repository, or use a public address');
      return { session: state.session, repo: state.repo, git };
    },
    signedIn: () => !!state.session && !state.manual,
  };
}
