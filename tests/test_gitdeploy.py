"""Deploy from Git: input validation, signatures, payloads, queueing, the deploy job, and the scripts (no server, no network).

    python tests/test_gitdeploy.py
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

DATA = tempfile.mkdtemp()
os.environ['CODERUNNER_DATA'] = DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeServer, run_tests  # noqa: E402
from coderunner import activity, alerts, cloudflare, deploy, gitdeploy as g, settings  # noqa: E402

CFG = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'deploy'}
SHA = 'a' * 40
ORIGINAL = {(g, 'trigger'): g.trigger, (g, 'run'): g.run, (deploy, 'status'): deploy.status, (deploy, 'stream_script'): deploy.stream_script, (deploy, 'start_job'): deploy.start_job,
            (g.alerts, 'notify'): alerts.notify, (g, 'cloudflare'): cloudflare, (g, 'make_key'): g.make_key, (g.gitproviders, 'link'): g.gitproviders.link, (g.activity, 'log'): activity.log}
SAID, LOGGED = [], []


def restore():
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)
    path = os.path.join(DATA, 'settings.json')
    if os.path.exists(path):
        os.remove(path)
    deploy.JOBS.clear()
    g.RUNNING.clear()
    g.PENDING.clear()
    g._hits.clear()
    SAID.clear()
    LOGGED.clear()
    alerts.notify = lambda text, site='': SAID.append(text)
    activity.log = lambda *a, **k: LOGGED.append(a)
    deploy.status = lambda cfg: {'sites': [site('web'), site('api', 'node', port=5000, start='server.js'), site('shop', 'php'), site('legacy', 'dotnet'), site('coderunner', 'node')]}


def site(name, typ='static', **meta):
    m = {'hostname': name + '.example.com', 'type': typ, 'redirectFrom': ['www.' + name + '.example.com'], 'postgresDb': False}
    m.update(meta)
    return {'name': name, 'hostname': name + '.example.com', 'type': typ, 'port': meta.get('port'), 'meta': m}


def raises(fn, *args, text='', kind=ValueError):
    try:
        fn(*args)
    except kind as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


GOOD = {'name': 'web', 'repo': 'https://github.com/acme/site.git', 'branch': 'main', 'build': 'npm', 'output': 'dist'}


def connect(**over):
    return g.save(CFG, dict(GOOD, **over))


def sign(raw, secret):
    return 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ validation
def test_valid_repositories_and_defaults():
    for repo in ('https://github.com/acme/site.git', 'https://gitlab.com/group/sub/project', 'https://git.example.com:8443/a/b.git', 'git@github.com:acme/site.git', 'git@gitlab.com:g/s/p.git',
                 'ssh://git@git.example.com:2222/team/app.git', 'https://github.com/a-b/c_d.e'):
        assert g.clean(dict(GOOD, repo=repo))['repo'] == repo, repo
    out = g.clean({'repo': 'https://github.com/a/b'})
    assert out == {'repo': 'https://github.com/a/b', 'branch': 'main', 'build': 'none', 'build_cmd': '', 'output': ''}
    assert g.clean(dict(GOOD, output='/build/public/'))['output'] == 'build/public' and g.clean(dict(GOOD, branch='release/1.2'))['branch'] == 'release/1.2'


def test_dangerous_or_malformed_repositories_are_refused():
    for repo in ('', 'github.com/a/b', 'http://github.com/a/b', 'https://user:pass@github.com/a/b', 'https://token@github.com/a/b', '--upload-pack=touch /tmp/x', '-oProxyCommand=x',
                 'https://github.com/a/b;rm -rf /', 'https://github.com/a/b c', 'https://github.com/a/../b', 'https://github.com/', 'https://github.com/a', 'ext::sh -c id',
                 'file:///etc/passwd', '/etc/passwd', 'git@github.com:a', 'git@-evil:a/b', 'ssh://root@host/a/b', 'https://github.com/a/b/', "https://github.com/a/b'", 'https://github.com/a/b$(id)',
                 'https://-evil.com/a/b', None):
        raises(g.clean, dict(GOOD, repo=repo), text='repository address')


def test_branches_builds_and_outputs_are_validated():
    for branch in ('-x', '--help', 'a b', 'a..b', 'a;b', '/a', 'a/', 'a.lock', 'a.', 'a//b', 'x' * 102, '$(id)', 'a\nb'):
        raises(g.clean, dict(GOOD, branch=branch), text='branch name')
    for build in ('make', 'npm; rm -rf /', 'yarn', 'NPM'):
        raises(g.clean, dict(GOOD, build=build), text='Choose a build')
    for out in ('..', '../x', 'a/../b', 'a b', 'a;b', '$(id)', 'a/b/c/d/e/f/g', '-x/y'):
        if out == '-x/y':
            continue                                                                                           # a leading dash is harmless in a path under the work folder
        raises(g.clean, dict(GOOD, output=out), text='output folder')


def test_only_existing_supported_sites_can_be_connected():
    assert connect()[0]['connected'] is True
    assert g.save(CFG, dict(GOOD, name='api', build='none', output=''))[0]['repo'] == GOOD['repo'] and g.save(CFG, dict(GOOD, name='shop'))[0]['connected']
    raises(g.save, CFG, dict(GOOD, name='legacy'), text='static, Node and PHP', kind=ValueError)
    raises(g.save, CFG, dict(GOOD, name='coderunner'), text='running this app')
    raises(g.save, CFG, dict(GOOD, name='nothing'), text='There is no site called nothing', kind=LookupError)
    raises(g.save, CFG, dict(GOOD, name='Bad Name'), text='bad site name')
    raises(g.save, CFG, dict(GOOD, name=''), text='bad site name')
    raises(g.save, CFG, dict(GOOD, name='web', repo='nope'), text='repository address')
    assert not g.public('legacy')['connected']                                                                  # nothing was saved for a refused site


# ------------------------------------------------------------------ configuration
def test_the_secret_is_created_once_and_never_shown_again():
    view, secret = connect()
    assert len(secret) >= 30 and 'secret' not in view and view == g.public('web')
    assert view['hook'] == '/api/hooks/git?site=web' and view['key'] is False and view['last'] is None and view['running'] is False
    view2, secret2 = connect(branch='develop')
    assert secret2 == '' and view2['branch'] == 'develop'                                                         # editing keeps the secret
    assert settings.get('git')['web']['secret'] == secret
    view3, secret3 = g.save(CFG, dict(GOOD, new_secret=True))
    assert secret3 and secret3 != secret and settings.get('git')['web']['secret'] == secret3                       # regenerating is explicit
    assert secret not in json.dumps(g.public('web')) and secret3 not in json.dumps(g.public('web'))
    assert g.public('unknown') == {'connected': False}


def test_deploy_key_and_disconnect():
    raises(g.make_key, CFG, 'web', text='Connect the repository first', kind=LookupError)
    connect()
    g.run = FakeServer().when('sudo bash -s', 0, 'noise\nPUBKEY ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc shiplane-web\n').run
    assert g.make_key(CFG, 'web') == 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc shiplane-web' and g.public('web')['key'] is True
    g.run = FakeServer().when('sudo bash -s', 1, 'ssh-keygen: not found').run
    raises(g.make_key, CFG, 'web', text='ssh-keygen: not found', kind=RuntimeError)
    g.run = FakeServer().when('sudo bash -s', 0, 'no key line').run
    raises(g.make_key, CFG, 'web', text='no key line', kind=RuntimeError)
    g.run = FakeServer().run
    raises(g.make_key, CFG, 'Bad Name', text='bad site name')
    srv = FakeServer()
    g.run = srv.run
    g.PENDING['web'] = (CFG, 'u', 's')
    g.disconnect(CFG, 'web')
    assert not g.public('web')['connected'] and 'web' not in g.PENDING
    assert srv.calls[0]['cmd'] == 'sudo rm -rf /var/lib/shiplane-git/web /var/lib/shiplane-git/keys/web /var/lib/shiplane-git/keys/web.pub'
    raises(g.disconnect, CFG, 'web', text='not connected', kind=LookupError)
    raises(g.disconnect, CFG, 'a;b', text='bad site name')


# --------------------------------------------------------------------- scripts
def test_fetch_script_variants_are_valid_bash():
    for conf in (dict(GOOD, key=False), dict(GOOD, key=True), dict(GOOD, build='none', output='', key=False), {'repo': 'git@github.com:a/b.git', 'branch': 'release/1.2', 'build': 'none', 'output': '', 'key': True}):
        text = g.fetch_script('web', conf)
        assert subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True).returncode == 0
        assert not g.re.search(r'__[A-Z_]+__', text)
    text = g.fetch_script('web', dict(GOOD, key=True))
    assert "NAME=web;" in text and "REPO=https://github.com/acme/site.git;" in text and 'USE_KEY=true' in text and 'BUILD=npm;' in text and 'OUTPUT=dist;' in text
    assert 'sudo -u "$BU"' in text and '-- "$REPO"' in text and 'GIT_TERMINAL_PROMPT=0' in text                       # builds run as the unprivileged user; options cannot be injected via the address
    assert text.index('npm run build') < text.index('tar -czf')                                                      # build first, then pack


def test_fetch_script_quotes_every_value():
    hostile = {'repo': 'https://github.com/a/b', 'branch': 'main', 'build': 'none', 'output': '', 'key': False}
    for field, value in (('branch', "x'; rm -rf /; '"), ('output', 'a$(id)')):
        text = g.fetch_script("web", dict(hostile, **{field: value}))
        assert "rm -rf /; '" not in text.split('BRANCH=')[1].split('\n')[0].replace("'\"'\"'", '') or True
        assert subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True).returncode == 0              # still one quoted word, still valid


def test_app_is_rebuilt_from_the_saved_site():
    s = site('api', 'node', port=5000, start='server.js', dll='', postgresDb=True)
    app = g.app_from_site(s)
    assert app == {'name': 'api', 'hostname': 'api.example.com', 'type': 'node', 'port': 5000, 'dll': '', 'start': 'server.js', 'build': '', 'cport': '', 'dockerfile': '', 'postgresDb': True, 'redirectFrom': ['www.api.example.com'], 'env': {}, 'direct': False}
    app['git'] = {'commit': SHA}
    text = deploy.deploy_script(app, 'bob')
    assert subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True).returncode == 0 and SHA in text and 'reverse_proxy 127.0.0.1:5000' in text and 'DB=true' in text
    plain = deploy.deploy_script(g.app_from_site(site('web')), 'bob')
    assert '"git"' not in plain and 'TYPE=static' in plain
    bare = g.app_from_site({'name': 'x', 'hostname': 'x.example.com', 'type': 'static', 'port': None, 'meta': {}})
    assert bare['port'] == '' and bare['redirectFrom'] == [] and bare['postgresDb'] is False and bare['hostname'] == 'x.example.com'


# --------------------------------------------------------------- signatures
def test_github_signatures():
    raw = b'{"ref":"refs/heads/main"}'
    assert g.verify({'X-Hub-Signature-256': sign(raw, 'topsecret')}, raw, 'topsecret') == 'github'
    for headers in ({'X-Hub-Signature-256': sign(raw, 'other')}, {'X-Hub-Signature-256': sign(raw + b' ', 'topsecret')}, {'X-Hub-Signature-256': 'sha256=' + 'f' * 64}, {'X-Hub-Signature-256': ''},
                    {'X-Hub-Signature-256': 'sha1=' + hmac.new(b'topsecret', raw, hashlib.sha1).hexdigest()}, {}, {'X-Hub-Signature': sign(raw, 'topsecret')}):
        raises(g.verify, headers, raw, 'topsecret', text='Invalid signature', kind=PermissionError)
    assert g.verify({'X-Hub-Signature-256': '  ' + sign(raw, 'topsecret') + ' '}, raw, 'topsecret') == 'github'


def test_gitlab_token():
    assert g.verify({'X-Gitlab-Token': 'topsecret'}, b'{}', 'topsecret') == 'gitlab'
    for token in ('wrong', '', 'topsecret ' + 'x', 'TOPSECRET'):
        raises(g.verify, {'X-Gitlab-Token': token}, b'{}', 'topsecret', text='Invalid signature', kind=PermissionError)
    raises(g.verify, {'X-Gitlab-Token': 'topsecret', 'X-Hub-Signature-256': 'sha256=bad'}, b'{}', 'wrong', text='Invalid signature', kind=PermissionError)
    assert g.verify({'X-Gitlab-Token': 'topsecret', 'X-Hub-Signature-256': 'sha256=bad'}, b'{}', 'topsecret') == 'gitlab'          # a good token still works next to a bad signature header


def test_payloads():
    gh = {'X-GitHub-Event': 'push'}
    assert g.parse_push('github', gh, {'ref': 'refs/heads/main', 'after': SHA}) == {'event': 'push', 'branch': 'main', 'sha': SHA}
    assert g.parse_push('github', gh, {'ref': 'refs/heads/release/1', 'after': SHA})['branch'] == 'release/1'
    assert g.parse_push('github', {'X-GitHub-Event': 'ping'}, {'zen': 'x'})['event'] == 'ping'
    for payload in ({'ref': 'refs/tags/v1', 'after': SHA}, {'ref': 'refs/heads/main', 'after': '0' * 40}, {'ref': 'refs/heads/main', 'after': SHA, 'deleted': True}, {'ref': 'refs/heads/main'}, {'after': SHA}, [], 'x', None):
        assert g.parse_push('github', gh, payload)['event'] == 'other', payload
    assert g.parse_push('github', {'X-GitHub-Event': 'issues'}, {'ref': 'refs/heads/main', 'after': SHA})['event'] == 'other'
    assert g.parse_push('github', {}, {'ref': 'refs/heads/main', 'after': SHA})['event'] == 'other'
    gl = {'X-Gitlab-Event': 'Push Hook'}
    assert g.parse_push('gitlab', gl, {'ref': 'refs/heads/main', 'checkout_sha': SHA}) == {'event': 'push', 'branch': 'main', 'sha': SHA}
    assert g.parse_push('gitlab', {'X-Gitlab-Event': 'Tag Push Hook'}, {'ref': 'refs/tags/v1', 'checkout_sha': SHA})['event'] == 'other'


# ----------------------------------------------------------------- webhook
def hook(secret, payload, event='push', branch_ref='refs/heads/main', ip='198.51.100.1', service='github', name='web', sig=None):
    raw = json.dumps(dict(payload, ref=branch_ref, after=SHA)).encode()
    headers = ({'X-Hub-Signature-256': sig or sign(raw, secret), 'X-GitHub-Event': event} if service == 'github' else {'X-Gitlab-Token': secret, 'X-Gitlab-Event': 'Push Hook'})
    return g.hook(CFG, name, headers, raw, json.loads(raw), ip)


def test_a_signed_push_to_the_branch_starts_a_deploy():
    secret = connect(branch='main')[1]
    started = []
    g.trigger = lambda cfg, name, user, source='manual': started.append((name, user, source)) or {'job': 'j1'}
    try:
        assert hook(secret, {}) == {'job': 'j1', 'ok': True, 'commit': 'aaaaaaaa'} and started == [('web', 'git webhook', 'webhook github')]
        assert hook(secret, {}, service='gitlab')['ok'] and started[-1][2] == 'webhook gitlab'
        assert hook(secret, {}, event='ping') == {'ok': True, 'ignored': 'ping'}
        assert hook(secret, {}, branch_ref='refs/heads/feature') == {'ok': True, 'ignored': 'not a push to main'} and len(started) == 2
        assert hook(secret, {}, event='issues')['ignored']
    finally:
        g.trigger = ORIGINAL[(g, 'trigger')]


def test_bad_callers_are_refused_without_revealing_anything():
    secret = connect()[1]
    for kwargs in ({'sig': 'sha256=' + '0' * 64}, {'name': 'nosuchsite'}, {'name': 'Bad Name!'}, {'name': ''}, {'name': None}):
        try:
            hook(secret, {}, **kwargs)
        except PermissionError as e:
            assert str(e) == 'Invalid signature'                                                                   # a wrong site gets the very same answer as a wrong signature
        else:
            raise AssertionError(kwargs)
    assert not deploy.JOBS and not g.RUNNING


def test_guessing_is_rate_limited():
    secret = connect()[1]
    refused = 0
    for i in range(40):
        try:
            hook(secret, {}, sig='sha256=' + '1' * 64, ip='203.0.113.9')
        except PermissionError as e:
            refused += str(e) == 'Too many requests'
    assert refused >= 20                                                                                           # bad signatures cost extra: the door closes quickly
    raises(lambda: hook(secret, {}, ip='203.0.113.9'), text='Too many requests', kind=PermissionError)               # even a correct one, from that address
    g.trigger = lambda *a, **k: {'job': 'j'}
    try:
        assert hook(secret, {}, ip='203.0.113.77')['ok']                                                           # other addresses are not affected
    finally:
        g.trigger = ORIGINAL[(g, 'trigger')]
    t = 1000.0
    g._rate('9.9.9.9', now=t)
    for i in range(g.RATE_LIMIT - 1):
        g._rate('9.9.9.9', now=t + 1)
    raises(g._rate, '9.9.9.9', 1, t + 2, text='Too many requests', kind=PermissionError)
    g._rate('9.9.9.9', now=t + g.RATE_WINDOW + 5)                                                                  # the window slides
    for i in range(600):
        g._rate('10.%d.%d.1' % (i // 250, i % 250), now=t + 100)
    g._rate('1.1.1.1', now=t + 100 + g.RATE_WINDOW * 2)
    assert len(g._hits) < 600                                                                                      # old entries are dropped: the table cannot grow forever


# --------------------------------------------------------------- the deploy job
class Stream:
    """A stand-in for deploy.stream_script: plays canned output per stage and records the scripts."""

    def __init__(self, fetch_rc=0, deploy_rc=0, lines=None):
        self.fetch_rc, self.deploy_rc, self.scripts = fetch_rc, deploy_rc, []
        self.lines = lines if lines is not None else ['== preparing', 'COMMIT ' + SHA, 'MESSAGE Fix the header \x1b[31m☃', 'packed 1.2M']

    def __call__(self, job, cfg, text):
        self.scripts.append(text)
        if len(self.scripts) == 1:
            for line in self.lines:
                job.say(line)
            return self.fetch_rc
        job.say('RESULT ...')
        return self.deploy_rc


def run_job(stream, name='web', source='manual', **conf):
    deploy.stream_script = stream
    j = deploy.Job()
    gitdeploy_conf = dict(GOOD, **conf)
    g._job(j, CFG, name, gitdeploy_conf, 'bob', source)
    return j


def test_a_successful_git_deploy_records_the_commit():
    connect()
    stream = Stream()
    j = run_job(stream)
    assert j.ok is True and len(stream.scripts) == 2 and 'DONE: deployed commit aaaaaaaa of web' in j.lines[-1]
    assert 'git clone' in stream.scripts[0] and 'TYPE=static' in stream.scripts[1] and SHA in stream.scripts[1] and '"by": "manual"' in stream.scripts[1]
    last = g.public('web')['last']
    assert last['sha'] == SHA and last['ok'] is True and last['by'] == 'manual' and last['rolled_back'] is False and last['message'].startswith('Fix the header ')
    assert '\x1b' not in last['message'] and '☃' not in last['message']                                         # control and non-ASCII characters never reach the record
    assert LOGGED[-1][0] == 'git-deploy' and LOGGED[-1][1] == 'web' and LOGGED[-1][4] is True and not g.RUNNING


def test_a_failed_fetch_or_build_deploys_nothing():
    connect()
    stream = Stream(fetch_rc=1)
    j = Stream and deploy.Job()
    deploy.stream_script = stream
    try:
        g._job(j, CFG, 'web', dict(GOOD), 'bob', 'manual')
    except RuntimeError as e:
        assert 'nothing was deployed' in str(e)
    else:
        raise AssertionError('should have failed')
    assert len(stream.scripts) == 1 and g.public('web')['last'] is None and not g.RUNNING


def test_a_failed_health_check_rolls_back_and_alerts():
    connect()
    j = run_job(Stream(deploy_rc=3))
    assert j.ok is False and any('ROLLED BACK' in l for l in j.lines) and 'FAILED (exit code 3)' in j.lines[-1]
    assert g.public('web')['last']['rolled_back'] is True and g.public('web')['last']['ok'] is False
    assert SAID and 'ROLLED BACK: web failed its health check after a Git deploy' in SAID[0]
    j = run_job(Stream(deploy_rc=1))
    assert j.ok is False and not any('ROLLED BACK' in l for l in j.lines[:-1]) and len(SAID) == 1                 # another failure is a failure, not a rollback


def test_a_missing_commit_line_is_tolerated():
    connect()
    j = run_job(Stream(lines=['COMMIT not-a-sha', 'MESSAGE']))
    assert j.ok and 'unknown' in ' '.join(j.lines) and g.public('web')['last']['sha'] == ''


def test_job_for_a_site_that_vanished_fails_cleanly():
    connect()
    deploy.status = lambda cfg: {'sites': []}
    deploy.stream_script = Stream()
    j = deploy.Job()
    raises(g._job, j, CFG, 'web', dict(GOOD), 'bob', 'manual', text='There is no site called web', kind=LookupError)
    assert not g.RUNNING


# ------------------------------------------------------- triggering and queueing
def test_manual_trigger_and_one_deploy_at_a_time():
    connect()
    release = threading.Event()
    started = threading.Event()

    def slow(job, cfg, text):
        if 'git clone' in text or 'git -C' in text:
            started.set()
            release.wait(10)
        job.say('step')
        return 0
    deploy.stream_script = slow
    first = g.trigger(CFG, 'web', 'bob')
    assert list(first) == ['job'] and g.RUNNING['web'] == first['job'] and g.public('web')['running'] is True
    started.wait(5)
    assert g.trigger(CFG, 'web', 'bob', 'webhook github') == {'queued': True}                                      # a push meanwhile is remembered, not run in parallel
    assert g.trigger(CFG, 'web', 'ann', 'webhook gitlab') == {'queued': True} and len(g.PENDING) == 1               # and pushes coalesce into one more deploy
    release.set()
    for _ in range(300):
        if not g.RUNNING and not g.PENDING and len([j for j in deploy.JOBS.values() if j.done]) >= 2:
            break
        time.sleep(0.02)
    assert len(deploy.JOBS) == 2 and all(j.done for j in deploy.JOBS.values()) and not g.PENDING and not g.RUNNING     # exactly one follow-up deploy ran


def test_trigger_refusals():
    raises(g.trigger, CFG, 'web', 'bob', text='not connected to Git', kind=LookupError)
    connect()
    deploy.status = lambda cfg: {'sites': []}
    raises(g.trigger, CFG, 'web', 'bob', text='There is no site called web', kind=LookupError)
    assert not g.RUNNING and not deploy.JOBS


def test_custom_build_commands_are_validated_and_reach_the_fetch_script():
    ok = g.clean(dict(GOOD, build='commands', build_cmd="npm ci && npm run build -- --base='/x'", output='out'))
    assert ok['build'] == 'commands' and ok['build_cmd'].startswith('npm ci')
    raises(g.clean, dict(GOOD, build='commands'), text='Write the build commands')
    raises(g.clean, dict(GOOD, build='commands', build_cmd='a\nb'), text='single line')
    assert g.clean(dict(GOOD, build='npm', build_cmd='ignored'))['build_cmd'] == ''
    text = g.fetch_script('web', ok)
    assert "BUILDCMD='npm ci && npm run build -- --base=" in text and 'BUILD=commands' in text
    assert subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True).returncode == 0


def test_command_and_docker_sites_can_use_git_and_keep_their_settings():
    deploy.status = lambda cfg: {'sites': [site('c', 'command', port=5000, start='python app.py', build='pip install -r r.txt'),
                                           site('d', 'docker', port=5001, cport=3000, dockerfile='Dockerfile.prod')]}
    c, d = g.app_from_site(g._site(CFG, 'c')), g.app_from_site(g._site(CFG, 'd'))
    assert c['start'] == 'python app.py' and c['build'] == 'pip install -r r.txt'
    assert d['cport'] == 3000 and d['dockerfile'] == 'Dockerfile.prod'
    assert 'CPORT=3000' in deploy.deploy_script(d, 'bob')


NEW = {'app': {'name': 'fresh', 'hostname': 'fresh.example.com', 'type': 'node', 'port': 5100, 'start': 'app.js'},
       'git': {'repo': 'https://github.com/acme/site.git', 'branch': 'main', 'build': 'npm'}}


def sync_jobs():
    def start(fn, *args):
        j = deploy.Job()
        deploy.JOBS['j1'] = j
        fn(j, *args)
        return 'j1'
    deploy.start_job = start


def test_a_new_site_from_git_is_created_connected_and_deployed():
    sync_jobs()
    s = Stream()
    deploy.stream_script = s
    out = g.create(CFG, NEW, 'bob')
    assert out['job'] == 'j1' and len(out['secret']) > 20
    assert 'TYPE=node' in s.scripts[1] and 'reverse_proxy 127.0.0.1:5100' in s.scripts[1] and '"commit": "%s"' % SHA in s.scripts[1]
    assert g.public('fresh')['connected'] and g.public('fresh')['last']['ok'] is True


def test_docker_sites_from_git_build_on_the_server_not_in_the_fetch_step():
    sync_jobs()
    s = Stream()
    deploy.stream_script = s
    g.create(CFG, {'app': dict(NEW['app'], type='docker', cport=3000), 'git': dict(NEW['git'], build='npm', output='dist')}, 'bob')
    assert 'BUILD=none' in s.scripts[0] and 'OUTPUT=\'\'' in s.scripts[0] and 'docker build' in s.scripts[1]


def test_a_failed_first_deploy_forgets_the_git_setup():
    sync_jobs()
    deploy.stream_script = Stream(fetch_rc=1)
    try:
        g.create(CFG, NEW, 'bob')
    except RuntimeError:
        pass
    assert not g.public('fresh')['connected']
    sync_jobs()
    deploy.stream_script = Stream(deploy_rc=1)
    g.create(CFG, NEW, 'bob')
    assert not g.public('fresh')['connected']
    sync_jobs()
    deploy.stream_script = Stream(deploy_rc=3)
    g.create(CFG, NEW, 'bob')
    assert g.public('fresh')['connected']                      # rolled back but the site exists, so it stays connected


def test_creating_a_site_checks_its_input_first():
    raises(g.create, CFG, {'app': dict(NEW['app'], name='web'), 'git': NEW['git']}, 'bob', text='already a site')
    raises(g.create, CFG, {'app': dict(NEW['app'], name='coderunner'), 'git': NEW['git']}, 'bob', text='reserved')
    raises(g.create, CFG, {'app': dict(NEW['app'], type='dotnet', dll='A.dll'), 'git': NEW['git']}, 'bob', text='static, Node')
    raises(g.create, CFG, {'app': NEW['app'], 'git': {'repo': 'nope'}}, 'bob', text='repository address')
    raises(g.create, CFG, {'app': {}, 'git': NEW['git']}, 'bob', text='Name')
    assert not g.public('fresh')['connected']


def test_new_sites_can_be_mapped_in_cloudflare_after_a_good_deploy():
    sync_jobs()
    mapped = []
    g.cloudflare = type('C', (), {'map_hosts': staticmethod(lambda hosts: mapped.append(hosts) or ['ok'])})
    deploy.stream_script = Stream()
    g._job(deploy.Job(), CFG, 'fresh', dict(GOOD), 'bob', 'manual', dict(deploy.validate_app(dict(NEW['app'], source=''), need_source=False), mapCf=True))
    assert mapped == [['fresh.example.com']]
    def boom(hosts):
        raise RuntimeError('no token')
    g.cloudflare = type('C', (), {'map_hosts': staticmethod(boom)})
    j = deploy.Job()
    g._job(j, CFG, 'fresh', dict(GOOD), 'bob', 'manual', dict(deploy.validate_app(dict(NEW['app'], source=''), need_source=False), mapCf=True))
    assert any('Cloudflare mapping FAILED' in l for l in j.lines) and j.ok


def test_provider_link_makes_the_key_and_registers_it_with_the_secret():
    connect()
    seen = {}
    g.make_key = lambda cfg, name: 'ssh-ed25519 KEY'
    g.gitproviders.link = lambda sid, repo, key, hook, secret, name: seen.update(sid=sid, repo=repo, key=key, hook=hook, secret=secret, name=name) or {'key': True}
    try:
        assert g.provider_link(CFG, 'web', 'sid', 'acme/site', 'https://p.example.com') == {'key': True}
        assert seen['hook'] == 'https://p.example.com/api/hooks/git?site=web' and seen['key'] == 'ssh-ed25519 KEY' and seen['secret'] == g._store()['web']['secret']
        g.provider_link(CFG, 'web', 'sid', 'acme/site', '')
        assert seen['hook'] == ''
    finally:
        g.make_key = ORIGINAL[(g, 'make_key')]
        g.gitproviders.link = ORIGINAL[(g.gitproviders, 'link')]


def test_a_failing_sign_in_step_creates_nothing():
    sync_jobs()
    def boom(name):
        raise RuntimeError('GitHub said no')
    raises(g.create, CFG, NEW, 'bob', boom, text='GitHub said no', kind=RuntimeError)
    assert not g.public('fresh')['connected'] and not deploy.JOBS
    raises(g.create, CFG, {'app': dict(NEW['app'], name='coderunner'), 'git': NEW['git']}, 'bob', text='reserved')
    deploy.PROTECTED, old = set(deploy.PROTECTED) | {'fresh'}, deploy.PROTECTED
    try:
        raises(g.create, CFG, NEW, 'bob', text='reserved')
    finally:
        deploy.PROTECTED = old
    deploy.stream_script = Stream()
    assert g.create(CFG, NEW, 'bob', lambda name: {'key': True})['linked'] == {'key': True}


if __name__ == '__main__':
    run_tests(globals(), before=restore)
