"""Deploy from Git: connect a site to a repository, deploy on demand or when a push arrives (signed webhook).

The server clones, builds and packs the code as an unprivileged user (never root); the existing deploy script then does what every deploy does
(new release, health check, automatic rollback). The commit is recorded on the release. Only repositories you trust should be connected:
a build runs the repository's own code.
"""
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import deque

from . import activity, alerts, cloudflare, deploy, gitproviders, perf, settings
from .runner import q, run, script

SUPPORTED_TYPES = ('static', 'node', 'php', 'command', 'docker')
REPO_PATTERNS = (
    re.compile(r'^https://[A-Za-z0-9][A-Za-z0-9.-]*(:\d{1,5})?/[A-Za-z0-9._~-]+(/[A-Za-z0-9._~-]+)+$'),            # no user:password@ in the address
    re.compile(r'^git@[A-Za-z0-9][A-Za-z0-9.-]*:[A-Za-z0-9._~-]+(/[A-Za-z0-9._~-]+)+$'),
    re.compile(r'^ssh://git@[A-Za-z0-9][A-Za-z0-9.-]*(:\d{1,5})?/[A-Za-z0-9._~-]+(/[A-Za-z0-9._~-]+)+$'),
)
BRANCH_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/-]{0,100}$')
OUTPUT_RE = re.compile(r'^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+){0,5}$')
BUILDS = ('none', 'npm', 'commands')
RATE_LIMIT, RATE_WINDOW = 30, 60                  # webhook calls per address per minute
_lock = threading.RLock()
RUNNING = {}                                      # site -> job id while a deploy is in progress
PENDING = {}                                      # site -> (cfg, user, source): a push arrived meanwhile, deploy once more afterwards
_hits = {}


# ------------------------------------------------------------------ validation
def clean(body):
    repo = str(body.get('repo', '')).strip()
    if repo.startswith('-') or '..' in repo or repo.endswith('/') or not any(p.match(repo) for p in REPO_PATTERNS):
        raise ValueError('The repository address must look like https://github.com/you/project.git or git@github.com:you/project.git (no password in the address: use a deploy key for private repositories)')
    branch = str(body.get('branch') or 'main').strip()
    if not BRANCH_RE.match(branch) or '..' in branch or branch.endswith(('/', '.lock', '.')) or '//' in branch:
        raise ValueError('The branch name looks wrong')
    build = str(body.get('build') or 'none')
    if build not in BUILDS:
        raise ValueError('Choose a build: none, npm (npm ci, then npm run build) or your own commands')
    build_cmd = deploy._one_line(body.get('build_cmd'), 'Build commands')
    if build == 'commands' and not build_cmd:
        raise ValueError('Write the build commands to run (for example: npm ci && npm run build)')
    output = str(body.get('output') or '').strip().strip('/')
    if output and (not OUTPUT_RE.match(output) or '..' in output.split('/')):
        raise ValueError('The output folder looks wrong (for example dist or build/public)')
    return {'repo': repo, 'branch': branch, 'build': build, 'build_cmd': build_cmd if build == 'commands' else '', 'output': output}


def _site(cfg, name):
    deploy._name(name)
    if name in deploy.PROTECTED:
        raise ValueError('That is the site running this app: it cannot be deployed from Git')
    site = next((s for s in deploy.status(cfg)['sites'] if s['name'] == name), None)
    if not site:
        raise LookupError('There is no site called %s. Deploy it once from the Deploy page first.' % name)
    if site['type'] not in SUPPORTED_TYPES:
        raise ValueError('Git deploys support static, Node and PHP sites (this one is %s)' % site['type'])
    return site


def _store():
    return {k: dict(v) for k, v in settings.get('git').items() if isinstance(v, dict)}


def public(name):
    """What the screen may see of a site's Git setup. The webhook secret is never part of it."""
    c = _store().get(name)
    if not c:
        return {'connected': False}
    return {'connected': True, 'repo': c['repo'], 'branch': c['branch'], 'build': c['build'], 'build_cmd': c.get('build_cmd', ''), 'output': c['output'], 'key': bool(c.get('key')), 'last': c.get('last'),
            'running': bool(RUNNING.get(name) and not deploy.JOBS.get(RUNNING[name], deploy.Job()).done), 'hook': '/api/hooks/git?site=' + name}


def save(cfg, body):
    """Connect (or change) a site. Returns (public view, the webhook secret when it was just created, else '')."""
    name = str(body.get('name', ''))
    _site(cfg, name)
    fields = clean(body)
    with _lock:
        store = _store()
        old = store.get(name, {})
        new_secret = '' if old.get('secret') and not body.get('new_secret') else secrets.token_urlsafe(24)
        store[name] = dict(old, **fields, secret=new_secret or old['secret'])
        settings.put('git', store)
    return public(name), new_secret


def disconnect(cfg, name):
    deploy._name(name)
    with _lock:
        store = _store()
        if name not in store:
            raise LookupError('This site is not connected to Git')
        del store[name]
        settings.put('git', store)
        PENDING.pop(name, None)
    run(cfg, 'sudo rm -rf %s %s %s' % (q('/var/lib/shiplane-git/' + name), q('/var/lib/shiplane-git/keys/' + name), q('/var/lib/shiplane-git/keys/' + name + '.pub')), timeout=60)


def make_key(cfg, name):
    """Create (or show) the deploy key of a site: add the public key to the repository as a read-only deploy key."""
    deploy._name(name)
    with _lock:
        store = _store()
        if name not in store:
            raise LookupError('Connect the repository first')
    rc, out = run(cfg, 'sudo bash -s', inp=script('gitkey.sh').replace('__NAME__', q(name)).encode(), timeout=60)
    m = re.search(r'^PUBKEY (ssh-ed25519 [A-Za-z0-9+/=]+(?: \S+)?)$', out, re.M)
    if rc or not m:
        raise RuntimeError(out.strip()[-300:] or 'could not create the key')
    with _lock:
        store = _store()
        if name in store:
            store[name]['key'] = True
            settings.put('git', store)
    return m.group(1)


# -------------------------------------------------------------------- the deploy
def fetch_script(name, conf):
    values = {'name': name, 'repo': conf['repo'], 'branch': conf['branch'], 'build': conf['build'], 'build_cmd': conf.get('build_cmd', ''), 'output': conf['output'], 'use_key': 'true' if conf.get('key') else 'false'}
    text = script('gitfetch.sh')
    for k, v in values.items():
        text = text.replace('__%s__' % k.upper(), q(v))
    if re.search(r'__[A-Z_]+__', text):
        raise ValueError('Fetch script has unfilled placeholders')
    return text


def app_from_site(site):
    """What deploy_script needs, rebuilt from the site's saved metadata (its type, port, address and redirects)."""
    m = site.get('meta') or {}
    return {'name': site['name'], 'hostname': m.get('hostname') or site['hostname'], 'type': site['type'], 'port': m.get('port') or site.get('port') or '', 'dll': m.get('dll', ''),
            'start': m.get('start', ''), 'build': m.get('build', ''), 'cport': m.get('cport', ''), 'dockerfile': m.get('dockerfile', ''), 'postgresDb': bool(m.get('postgresDb')), 'redirectFrom': list(m.get('redirectFrom') or []), 'env': {}, 'direct': bool(m.get('direct'))}


class Tap:
    """Passes every line on to the job and remembers the commit lines of the fetch script (the job keeps only recent lines)."""

    def __init__(self, job):
        self.job, self.sha, self.msg = job, '', ''

    def say(self, line):
        line = str(line).rstrip()
        if line.startswith('COMMIT ') and re.fullmatch(r'[0-9a-f]{40}', line[7:].strip()):
            self.sha = line[7:].strip()
        elif line.startswith('MESSAGE '):
            self.msg = re.sub(r'[^\x20-\x7e]', '?', line[8:])[:120]
        self.job.say(line)


def _job(job, cfg, name, conf, user, source, new_app=None):
    try:
        site = new_app or _site(cfg, name)
        tap = Tap(job)
        job.say('Deploying %s from %s (%s), started by %s' % (name, conf['repo'], conf['branch'], source))
        if deploy.stream_script(tap, cfg, fetch_script(name, conf)):
            raise RuntimeError('getting or building the code failed: nothing was deployed')
        app = dict(new_app) if new_app else app_from_site(site)
        app['git'] = {'repo': conf['repo'], 'branch': conf['branch'], 'commit': tap.sha, 'message': tap.msg, 'by': source}
        job.say('Deploying commit %s ...' % (tap.sha[:8] or 'unknown'))
        rc = deploy.stream_script(job, cfg, deploy.deploy_script(app, user))
        job.ok = rc == 0
        if new_app and not job.ok and rc != 3:
            _forget(name)
        _remember(name, {'sha': tap.sha, 'message': tap.msg, 'at': int(time.time()), 'by': source, 'ok': job.ok, 'rolled_back': rc == 3})
        activity.log('git-deploy', name, '%s %s' % (tap.sha[:8], tap.msg[:80]), user, job.ok)
        if rc == 3:
            alerts.notify('[Shiplane] ROLLED BACK: %s failed its health check after a Git deploy, so the previous release is serving again' % name, site=name)
            job.say('ROLLED BACK: the new release failed its health check, the previous one is serving again')
        if new_app and job.ok and new_app.get('mapCf'):
            job.say('Mapping in Cloudflare ...')
            try:
                for line in cloudflare.map_hosts([app['hostname']] + app['redirectFrom']):
                    job.say('  ' + line)
            except Exception as e:
                job.say('  Cloudflare mapping FAILED: %s (the site is deployed; map it from the Cloudflare page)' % e)
        job.say('DONE: deployed commit %s of %s' % (tap.sha[:8], name) if job.ok else 'FAILED (exit code %s)' % rc)
    except BaseException:
        if new_app:
            _forget(name)                                      # a site that never came to exist keeps no Git setup
        raise
    finally:
        with _lock:
            RUNNING.pop(name, None)
            again = PENDING.pop(name, None)
        if again:                                              # a push arrived while this deploy ran: deploy the newest code once more
            try:
                trigger(again[0], name, again[1], again[2])
            except Exception:
                pass
        perf.invalidate('status')


def _forget(name):
    with _lock:
        store = _store()
        if store.pop(name, None) is not None:
            settings.put('git', store)


def provider_link(cfg, name, sid, repo, base):
    """One-click connect: a deploy key for the site, added to the repository together with a push webhook (when `base` is public HTTPS)."""
    key = make_key(cfg, name)
    secret = _store()[name]['secret']
    return gitproviders.link(sid, repo, key, (base + '/api/hooks/git?site=' + name) if base else '', secret, name)


def create(cfg, body, user, before=None):
    """A new site straight from a repository: {'app': {name, hostname, type, port, ...}, 'git': {repo, branch, build, ...}}. Returns {'job': id}."""
    app = deploy.validate_app(dict(body.get('app') or {}, source=''), need_source=False)
    name = app['name']
    if name in deploy.PROTECTED:
        raise ValueError('That name is reserved for this app itself')
    if app['type'] not in SUPPORTED_TYPES:
        raise ValueError('Git deploys support static, Node, PHP, command and Docker sites')
    conf = clean(body.get('git') or {})
    if app['type'] in ('command', 'docker'):
        conf.update(build='none', build_cmd='', output='')           # these types build on the server themselves
    with _lock:
        if name in _store() or any(s['name'] == name for s in deploy.status(cfg)['sites']):
            raise ValueError('There is already a site called %s' % name)
        store = _store()
        store[name] = dict(conf, secret=secrets.token_urlsafe(24))
        settings.put('git', store)
        linked = None
        if before:
            try:
                linked = before(name)
            except BaseException:
                _forget(name)
                raise
        conf = _store()[name]                                  # `before` may have added the deploy key
        job = deploy.start_job(_job, cfg, name, conf, user, 'manual', app)
        RUNNING[name] = job
    return {'job': job, 'secret': conf['secret'], 'linked': linked}


def _remember(name, last):
    with _lock:
        store = _store()
        if name in store:
            store[name]['last'] = last
            settings.put('git', store)


def trigger(cfg, name, user, source='manual'):
    """Start a deploy of the connected branch. Returns {'job': id} or {'queued': True} when one is already running."""
    with _lock:
        conf = _store().get(name)
        if not conf:
            raise LookupError('This site is not connected to Git')
        running = RUNNING.get(name)
        if running and not deploy.JOBS.get(running, deploy.Job()).done:
            PENDING[name] = (cfg, user, source)
            return {'queued': True}
        _site(cfg, name)
        job = deploy.start_job(_job, cfg, name, conf, user, source)
        RUNNING[name] = job
        return {'job': job}


# ------------------------------------------------------------------- the webhook
def _rate(ip, cost=1, now=None):
    now = time.time() if now is None else now
    q_ = _hits.setdefault(ip, deque())
    while q_ and now - q_[0] > RATE_WINDOW:
        q_.popleft()
    if len(q_) + cost > RATE_LIMIT:
        raise PermissionError('Too many requests')
    q_.extend([now] * cost)
    if len(_hits) > 500:                                       # never grow without bound
        for k in [k for k, v in _hits.items() if not v or now - v[-1] > RATE_WINDOW]:
            _hits.pop(k, None)


def verify(headers, raw, secret):
    """Which service signed this request ('github' or 'gitlab'), or PermissionError. Comparisons are constant-time."""
    got = headers.get('X-Hub-Signature-256')
    if got is not None:
        want = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if hmac.compare_digest(got.strip(), want):
            return 'github'
    token = headers.get('X-Gitlab-Token')
    if token is not None and hmac.compare_digest(token.strip().encode(), secret.encode()):
        return 'gitlab'
    raise PermissionError('Invalid signature')


def parse_push(service, headers, payload):
    """{'event': 'ping' | 'push' | 'other', 'branch', 'sha'} from a GitHub or GitLab payload."""
    event = headers.get('X-GitHub-Event') if service == 'github' else headers.get('X-Gitlab-Event')
    if event in ('ping',):
        return {'event': 'ping', 'branch': '', 'sha': ''}
    if event not in ('push', 'Push Hook') or not isinstance(payload, dict):
        return {'event': 'other', 'branch': '', 'sha': ''}
    ref = str(payload.get('ref', ''))
    sha = str(payload.get('after') or payload.get('checkout_sha') or '')
    if not ref.startswith('refs/heads/') or payload.get('deleted') or not sha or set(sha) == {'0'}:
        return {'event': 'other', 'branch': '', 'sha': ''}                       # tags, deleted branches
    return {'event': 'push', 'branch': ref[len('refs/heads/'):], 'sha': sha[:40]}


def hook(cfg, name, headers, raw, payload, ip):
    """Handle a webhook call. Raises PermissionError (403/429) for bad callers; otherwise returns what was done."""
    _rate(ip)
    conf = _store().get(name) if re.fullmatch(r'[a-z0-9][a-z0-9-]{0,40}', name or '') else None
    if not conf:
        _rate(ip, cost=2)                                                       # guessing site names costs more
        raise PermissionError('Invalid signature')                              # the same answer as a bad signature: do not reveal which sites exist
    try:
        service = verify(headers, raw, conf['secret'])
    except PermissionError:
        _rate(ip, cost=2)
        raise
    info = parse_push(service, headers, payload)
    if info['event'] == 'ping':
        return {'ok': True, 'ignored': 'ping'}
    if info['event'] != 'push' or info['branch'] != conf['branch']:
        return {'ok': True, 'ignored': 'not a push to %s' % conf['branch']}
    out = trigger(cfg, name, 'git webhook', 'webhook %s' % service)
    return dict(out, ok=True, commit=info['sha'][:8])
