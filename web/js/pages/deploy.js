import { h, fill, Logo, Button, Card, Field, Input, Select, Switch, Segmented, KeyValueEditor, Alert, PageHeader, icon, busy, toast, nextId } from '../kit.js';
import { store, refresh, siteByName, usedPorts } from '../store.js';
import { api } from '../api.js';
import { attempt } from '../util.js';
import { trackDeploy } from '../deploydock.js';
import { GitSource } from './gitsource.js';
import { NeedsBar } from '../needs.js';
import { withHelp } from '../help.js';

const TYPES = [
  { value: 'static', label: 'Static', hint: 'Angular/React build output or plain HTML. Served directly by Caddy.' },
  { value: 'dotnet', label: '.NET', hint: 'Output of "dotnet publish -c Release". Runs as a service.' },
  { value: 'node', label: 'Node / SSR', hint: 'Node app or Angular SSR (dist folder with server/server.mjs). Runs as a service.' },
  { value: 'command', label: 'Commands', hint: 'Any app: you write the build and start commands (super admins only). Runs as a service under its own unprivileged user.' },
  { value: 'docker', label: 'Docker', hint: 'Builds the Dockerfile in the repository or folder and runs it as a container. Needs Docker (Apps page).' },
  { value: 'php', label: 'PHP', hint: 'PHP files served by PHP-FPM through Caddy.' },
];
const APP_TYPES = ['dotnet', 'node', 'command', 'docker'];
const NEEDS = { node: ['node', 'Node apps run with Node.js.'], dotnet: ['dotnet', '.NET apps need the .NET SDK or runtime.'], docker: ['docker', 'Docker sites are built and run with Docker.'], php: ['php', 'PHP sites are served by PHP-FPM.'] };

export function DeployPage({ query }) {
  const root = h('div', { class: 'space-y-6 animate-fade-up' });
  const sess = store.session;
  const local = sess.server.mode === 'local';
  const serverDomains = sess.server.domains || [];     // covered by the tunnel's wildcard route
  let domains = serverDomains;
  let ownZones = [];                                     // zones on this server's own nameservers: any name under them works, served directly                           // plus the Cloudflare zones, once loaded

  async function init() {
    if (query.site && !store.status) await refresh();
    const dnsp = sess.dnsProviders || [];
    const zr = await api('/api/cloudflare/zones').catch(() => ({ zones: [], own: [] }));
    ownZones = zr.own || [];
    domains = [...new Set([...serverDomains, ...zr.zones])];
    const pre = query.site ? siteByName(query.site) : null;
    const meta = (pre && pre.meta) || {};
    const ids = { name: nextId(), sub: nextId(), dom: nextId(), src: nextId(), dll: nextId(), start: nextId(), port: nextId(), redir: nextId() };

    // ---------------------------------------------------------------- fields
    const name = Input({ id: ids.name, placeholder: 'my-api', value: pre ? pre.name : '', disabled: !!pre, mono: true });
    const type = Select({ options: TYPES.map((t) => ({ value: t.value, label: t.label })), value: pre ? (meta.type || (pre.type === 'app' ? 'node' : pre.type)) : 'static', onChange: () => sync() });
    type.get = () => type.value;
    type.set = (v) => { type.value = v; };
    const hostDomain = pre && domains.find((d) => pre.hostname === d || pre.hostname.endsWith('.' + d));
    const sub = Input({ id: ids.sub, placeholder: 'api', mono: true, value: pre && hostDomain ? (pre.hostname === hostDomain ? '' : pre.hostname.slice(0, -hostDomain.length - 1)) : '' });
    const dom = Select({ id: ids.dom, options: domains.map((d) => ({ value: d, label: d })).concat([{ value: '__custom__', label: 'Other domain...' }]), value: pre ? (hostDomain || '__custom__') : (domains[0] || '__custom__'), onChange: sync });
    const custom = Input({ placeholder: 'app.example.com', mono: true, value: pre && !hostDomain ? pre.hostname : '' });
    const redirects = Input({ id: ids.redir, placeholder: 'www.example.com', mono: true, value: pre ? (meta.redirectFrom || pre.redirects || []).join(', ') : '' });
    const srcMode = Segmented({ label: 'Source', options: [{ value: 'folder', label: local ? 'Folder on this server' : 'Folder' }, { value: 'zip', label: 'Upload a .zip' }, { value: 'git', label: 'From Git' }, { value: 'empty', label: 'Empty site' }], value: query.source === 'git' ? 'git' : local ? 'zip' : 'folder', onChange: sync });
    const src = Input({ id: ids.src, placeholder: local ? '/home/you/build/dist' : 'C:\\path\\to\\build-output', mono: true, value: '' });
    const browse = Button({ label: 'Browse...', icon: 'folder', onClick: () => busy(browse, async () => { const r = await attempt(api('/api/pick-folder', {})); if (r && r.path) { src.value = r.path; sync(); } }) });
    const zipStatus = h('p', { class: 'hint' }, 'Choose a .zip of your build output (a zip of a single folder works too). Up to 500 MB.');
    const zipInput = h('input', { type: 'file', accept: '.zip', class: 'input h-auto py-1.5 text-xs file:mr-3 file:rounded-md file:border-0 file:bg-sunken file:px-3 file:py-1.5 file:text-xs file:font-medium', onChange: async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      zipStatus.textContent = `Uploading ${f.name} (${(f.size / 1e6).toFixed(1)} MB)...`;
      try {
        const res = await fetch('/api/upload', { method: 'POST', headers: { 'X-Requested-With': 'coderunner' }, body: f });
        const j = await res.json();
        if (!res.ok || j.error) throw new Error(j.error || 'upload failed');
        src.value = j.path; zipStatus.textContent = `Uploaded and unpacked: ${j.path}`; sync();
      } catch (err) { zipStatus.textContent = 'Upload failed: ' + err.message; toast(err.message, { tone: 'bad', title: 'Upload failed' }); }
    } });
    const dll = Input({ id: ids.dll, placeholder: 'MyApi.dll', mono: true, value: meta.dll || '' });
    const start = Input({ id: ids.start, placeholder: 'server/server.mjs', mono: true, value: meta.start || '' });
    const used = usedPorts();
    let free = 5000; while (used.includes(free)) free++;
    const port = Input({ id: ids.port, type: 'number', min: 1024, placeholder: String(free), mono: true, value: pre && pre.port ? String(pre.port) : String(free) });
    const cmdStart = Input({ placeholder: 'npm start   or   .venv/bin/python app.py', mono: true });
    const cmdBuild = Input({ placeholder: 'npm ci && npm run build   (optional)', mono: true });
    const healthPath = Input({ placeholder: '/', mono: true, value: meta.healthPath || '' });
    const healthStatus = Input({ type: 'number', min: 100, placeholder: 'any below 500', mono: true, value: meta.healthStatus ? String(meta.healthStatus) : '' });
    const nodeVer = Select({ options: [{ value: '', label: 'System default' }], value: meta.runtime || '' });
    let nodeLoaded = false;
    const portNote = h('div', {});
    const cport = Input({ type: 'number', min: 1, placeholder: '8080', mono: true, value: '8080' });
    const dockerfile = Input({ placeholder: 'Dockerfile', mono: true, value: 'Dockerfile' });
    const gitSrc = GitSource({ onDetect: (d, repoName) => {
      type.set(d.type);
      if (d.type === 'node') start.value = d.start;
      if (d.type === 'command') { cmdStart.value = d.start; cmdBuild.value = d.build_cmd; }
      if (d.type === 'docker') { cport.value = String(d.cport); dockerfile.value = d.dockerfile; }
      if (!name.value && !pre) name.value = repoName;
      sync();
    } });
    const pg = Switch({ label: 'Create a Postgres database', description: 'Adds DATABASE_URL and ConnectionStrings__Default to the app. The password stays on the server.', checked: !!(meta.postgresDb || (pre && pre.db)) });
    const env = KeyValueEditor({ secret: true, valuePlaceholder: 'value' });
    const cf = Switch({ label: 'Map the hostname in Cloudflare', description: 'Adds the tunnel route and the proxied DNS record if they are missing. Never overwrites an existing record.', checked: true, onChange: (v) => { if (v) dnsSw.set(false); sync(); } });
    const dnsNames = dnsp.map((p) => ({ route53: 'Route 53', digitalocean: 'DigitalOcean' })[p]).join(' / ');
    const dnsSw = Switch({ label: `Add an A record in ${dnsNames}`, description: 'Points the hostname at this server and serves it directly with its own HTTPS certificate (ports 80 and 443 must be open). Never overwrites an existing record.', checked: !sess.cloudflare, onChange: (v) => { if (v) cf.set(false); sync(); } });

    // --------------------------------------------------------------- layout
    const wrap = { dll: h('div', {}, Field({ label: '.NET entry DLL', id: ids.dll, hint: 'The main DLL in the publish folder. The server runs .NET 10; an app built for .NET 8/9 needs DOTNET_ROLL_FORWARD=Major below.' }, dll)),
      start: h('div', {}, Field({ label: 'Start file', id: ids.start, hint: 'Relative to the folder you deploy.' }, start)),
      cmd: h('div', { class: 'space-y-4' }, Field({ label: 'Start command', hint: 'One line. The app must listen on the PORT environment variable (also HOST=127.0.0.1).' }, cmdStart), Field({ label: 'Build command', hint: 'Optional, runs once per deploy in the new release folder as the site own user.' }, cmdBuild)),
      docker: h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'Port inside the container', hint: 'The EXPOSE port of your Dockerfile.' }, cport), Field({ label: 'Dockerfile', hint: 'Path inside the repository or folder.' }, dockerfile)),
      port: h('div', {}, Field({ label: 'Local port', id: ids.port, hint: 'Where the app listens on the server (127.0.0.1 only). A free one is suggested.' }, port), portNote),
      node: h('div', {}, Field({ label: 'Node version', hint: 'Installed versions on the server (under /usr or /opt). Leave on System default unless the app needs a specific one.' }, nodeVer)),
      health: h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'Health check path', hint: 'Checked after each deploy; a failing release is rolled back. Default /' }, healthPath), Field({ label: 'Expected status', hint: 'Empty accepts anything below 500. Example: 200' }, healthStatus)) };
    const typeHint = h('p', { class: 'hint' });
    const needsBox = h('div', {});
    const typeLogo = h('span', { class: 'flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-sunken' });
    let needsFor = '';
    const hostPreview = h('p', { class: 'mt-3 inline-flex items-center gap-2 rounded-lg bg-sunken px-3 py-1.5 font-mono text-xs' });
    const hostNote = h('div', { class: 'mt-4' });
    const folderRow = h('div', { class: 'flex gap-2' }, src, local ? null : browse);
    const emptyNote = h('div', {}, Alert({ tone: 'info', title: 'Start with an empty site' }, 'Creates the address with a small starter page (static, PHP, Node or Commands sites). Nothing to upload: put your real code there later with Redeploy on the site\'s page, or connect a repository on its Git tab.'));
    const sourceBody = h('div', {}, folderRow, h('div', { class: 'mt-0' }, zipInput, zipStatus), gitSrc.el, emptyNote);
    const summary = h('ul', { class: 'space-y-2 text-sm text-muted' });
    const goBtn = Button({ label: 'Deploy', icon: 'rocket', variant: 'primary', class: 'w-full', onClick: () => submit() });
    const progress = h('div', {});

    const form = h('div', { class: 'space-y-6' },
      Card({ title: 'Where is the code?', subtitle: local ? 'Upload a zip, give a path on this server, or deploy straight from a Git repository' : 'A build folder, a zip, or a Git repository (private ones too)' },
        h('div', { class: 'mb-4' }, srcMode), sourceBody),
      Card({ title: 'About the site' },
        h('div', { class: 'space-y-4' }, Field({ label: 'Site name', id: ids.name, hint: pre ? 'The name cannot change when redeploying.' : 'Lowercase letters, numbers and dashes. Used for folders, the service and the database.' }, name),
          h('div', {}, h('span', { class: 'label' }, 'Type'), h('div', { class: 'flex items-center gap-3' }, typeLogo, h('div', { class: 'min-w-0 flex-1' }, type)), typeHint), needsBox)),
      Card({ title: 'Address', subtitle: 'Where the site will be reachable' },
        h('div', { class: 'grid gap-4 sm:grid-cols-2' }, Field({ label: 'Subdomain', id: ids.sub, hint: 'Leave empty to use the domain itself.' }, sub), Field({ label: 'Domain', id: ids.dom }, dom)),
        h('div', { id: 'customWrap', class: 'mt-4' }, Field({ label: 'Full hostname' }, custom)), hostPreview, hostNote,
        h('div', { class: 'mt-4' }, Field({ label: 'Also redirect these hostnames here', id: ids.redir, hint: 'Optional, comma separated. Example: www.example.com' }, redirects))),
      Card({ title: withHelp('Runtime', 'deploying-each-type'), subtitle: 'How the app starts' }, h('div', { id: 'runtimeBody', class: 'space-y-4' }, wrap.dll, wrap.start, wrap.cmd, wrap.docker, wrap.node, wrap.port, wrap.health,
        h('div', { id: 'pgWrap' }, pg), h('div', {}, h('span', { class: 'label' }, 'Environment variables'), env))),
      sess.cloudflare || dnsp.length ? Card({ title: withHelp('DNS', 'domains-and-cloudflare') }, h('div', { class: 'space-y-4' }, sess.cloudflare ? cf : null, dnsp.length ? dnsSw : null)) : null);

    const side = h('aside', { class: 'space-y-6 lg:sticky lg:top-6 lg:self-start' },
      Card({ title: 'Review' }, summary, h('div', { class: 'mt-5' }, goBtn)), progress);

    // ------------------------------------------------------------------ logic
    const hostname = () => (dom.value === '__custom__' ? custom.value.trim().toLowerCase() : (sub.value.trim().toLowerCase() ? sub.value.trim().toLowerCase() + '.' : '') + dom.value);
    const covered = (host) => ownZones.some((d) => host === d || host.endsWith('.' + d)) || serverDomains.some((d) => host === d || (host.endsWith('.' + d) && host.slice(0, -d.length - 1).indexOf('.') < 0));

    function sync() {
      const t = type.get(), isApp = APP_TYPES.includes(t), host = hostname(), git = srcMode.get() === 'git';
      typeHint.textContent = TYPES.find((x) => x.value === t).hint;
      fill(typeLogo, Logo(t, 'h-6 w-6'));
      if (needsFor !== t) { needsFor = t; fill(needsBox, NEEDS[t] ? NeedsBar(NEEDS[t][0], NEEDS[t][1] + ' Install it here, then deploy.') : null); }
      if (t === 'node' && !nodeLoaded) {
        nodeLoaded = true;
        api('/api/runtimes').then((r) => { const cur = nodeVer.value || meta.runtime || ''; nodeVer.replaceChildren(...[{ path: '', version: '' }].concat(r.node).map((n) => h('option', { value: n.path, selected: n.path === cur }, n.path ? `Node ${n.version} (${n.path})` : 'System default'))); }).catch(() => {});
      }
      const taken = (store.status ? store.status.sites : []).find((x) => x.port && String(x.port) === String(port.value) && (!pre || x.name !== pre.name));
      fill(portNote, taken ? Alert({ tone: 'warn', title: `Port ${port.value} is used by "${taken.name}"` }, 'Pick another port, or the deploy will be refused.') : null);
      wrap.health.hidden = !isApp; wrap.node.hidden = t !== 'node';
      wrap.dll.hidden = t !== 'dotnet'; wrap.start.hidden = t !== 'node'; wrap.cmd.hidden = t !== 'command'; wrap.docker.hidden = t !== 'docker'; wrap.port.hidden = !isApp;
      form.querySelector('#pgWrap').hidden = !isApp; form.querySelector('#runtimeBody').parentElement.parentElement.hidden = !isApp;
      form.querySelector('#customWrap').hidden = dom.value !== '__custom__'; sub.disabled = dom.value === '__custom__';
      hostPreview.replaceChildren(icon('globe', 'h-3.5 w-3.5'), host ? 'https://' + host : 'https://...');
      const mapping = (sess.cloudflare && cf.get()) || (dnsp.length && dnsSw.get());
      hostNote.replaceChildren(...(host && !covered(host) && !mapping ? [Alert({ tone: 'warn', title: 'Not covered by the wildcard route' }, 'This hostname needs a Cloudflare tunnel route and a DNS record or the site will not be reachable. ', sess.cloudflare ? 'Turn on "Map the hostname in Cloudflare" below.' : 'Ask the owner to map it, or add it in Cloudflare by hand.')] : []));
      folderRow.style.display = srcMode.get() !== 'folder' ? 'none' : ''; zipInput.hidden = zipStatus.hidden = srcMode.get() !== 'zip'; gitSrc.el.hidden = !git; emptyNote.hidden = srcMode.get() !== 'empty';
      const steps = [srcMode.get() === 'empty' ? 'Create a starter page as the first release' : git ? 'Download the repository on the server (a read-only deploy key is added for private ones) and build it' : `Pack and upload ${srcMode.get() === 'zip' ? 'the unpacked zip' : 'the folder'} as a new release`, host ? `Serve https://${host} through Caddy` : 'Serve the site through Caddy'];
      if (isApp) steps.push(`Run it as a service on port ${port.value || '?'}, restarted automatically`);
      if (isApp && pg.get()) steps.push('Create a Postgres database and user for it');
      if (sess.cloudflare && cf.get()) steps.push('Add the Cloudflare tunnel route and DNS record');
      if (dnsp.length && dnsSw.get()) steps.push(`Add an A record in ${dnsNames} and serve it with its own certificate`);
      steps.push('Keep the last 3 releases so you can roll back');
      summary.replaceChildren(...steps.map((s) => h('li', { class: 'flex gap-2' }, icon('check', 'mt-0.5 h-4 w-4 text-ok'), s)));
    }

    async function submit() {
      const t = type.get();
      const body = { name: name.value, hostname: hostname(), type: t, source: src.value, dll: dll.value, start: t === 'command' ? cmdStart.value : start.value, build: cmdBuild.value,
        cport: cport.value, dockerfile: dockerfile.value, port: port.value, healthPath: healthPath.value, healthStatus: healthStatus.value, runtime: nodeVer.value, postgresDb: pg.get(), mapCf: !!(sess.cloudflare && cf.get()), mapDns: !!(dnsp.length && dnsSw.get()), redirectFrom: redirects.value.split(','), env: env.get() };
      let r;
      if (srcMode.get() === 'empty') {
        if (!['static', 'php', 'node', 'command'].includes(t)) { toast('An empty site can be Static, PHP, Node or Commands', { tone: 'bad' }); return; }
        r = await attempt(api('/api/deploy', Object.assign({}, body, { empty: true })));
      } else if (srcMode.get() === 'git') {
        let pick;
        try { pick = gitSrc.get(); } catch (e) { toast(e.message, { tone: 'bad' }); return; }
        if (pre) {   // redeploying an existing site: connect the repository to it and deploy, never create a second site with the same name
          if (!pick.session) { toast('Pick the repository after signing in, or use the Git tab of the site for a public address', { tone: 'warn' }); return; }
          if (!await attempt(api('/api/gitprov/connect', Object.assign({ name: pre.name }, pick.git, { session: pick.session, repo: pick.repo })))) return;
          r = await attempt(api('/api/git/deploy', { name: pre.name }));
          if (r && !r.job) { toast('A deploy of this site is already running; this one is queued right after it', { tone: 'info' }); return; }
        } else r = await attempt(api('/api/git/create', Object.assign({ app: body }, pick)));
      } else r = await attempt(api('/api/deploy', body));
      if (!r) return;
      const linked = r.linked;
      const note = [linked && linked.hook ? 'The repository now deploys by itself on every push.' : '', linked && linked.hook_note ? linked.hook_note : '',
        r.secret && !linked ? 'Set up the push webhook from the Git tab of the site.' : ''].filter(Boolean).join(' ');
      trackDeploy(r.job, body.name, { host: body.hostname, note });
      progress.replaceChildren(Alert({ tone: 'info', title: 'Deploy started' }, 'Follow it in the panel at the bottom right of the screen. It keeps going if you leave this page or refresh.',
        h('div', { class: 'mt-3 flex gap-2' }, Button({ label: 'Site page', size: 'sm', variant: 'primary', href: `#/sites/${body.name}` }))));
    }

    form.addEventListener('input', sync);
    form.addEventListener('change', sync);
    form.querySelectorAll('[role=switch]').forEach((b) => b.addEventListener('click', () => setTimeout(sync)));
    root.replaceChildren(PageHeader({ title: pre ? `Redeploy ${pre.name}` : 'Deploy a site', description: pre ? 'Upload a new build. The old release stays available for rollback.' : 'Publish a build folder, a zip or a Git repository to the server in one step.', crumbs: [{ label: 'Sites', href: '#/sites' }, { label: pre ? pre.name : 'Deploy' }] }),
      h('div', { class: 'grid gap-6 lg:grid-cols-[1fr_20rem]' }, form, side));
    sync();
  }

  root.append(h('div', { class: 'skeleton h-64' }));
  init();
  return { el: root };
}
