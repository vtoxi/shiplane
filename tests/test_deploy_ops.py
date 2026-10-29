"""Deploy jobs, site actions, rename, logs and status reading with fake servers (no server needed).

    python tests/test_deploy_ops.py
"""
import io
import json
import os
import sys
import tarfile
import tempfile
import time

DATA = tempfile.mkdtemp()
os.environ['CODERUNNER_DATA'] = DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeProc, FakeServer, run_tests  # noqa: E402
from coderunner import activity, alerts, cloudflare, config, deploy, dnsproviders, perf  # noqa: E402

CFG = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'deploy'}
SRC = tempfile.mkdtemp()
open(os.path.join(SRC, 'index.html'), 'w').write('hi')
APP = deploy.validate_app({'name': 'demo', 'hostname': 'demo.example.com', 'type': 'static', 'source': SRC})


def app(**kw):
    return dict(APP, **kw)
ORIGINAL = {n: getattr(deploy, n) for n in ('run', 'popen', 'cloudflare', 'dnsproviders', 'alerts', 'activity', 'ON_SITE_ACTION', 'PUBLIC_LOOKUP')}
NOTES, LOGGED = [], []


def reset():
    for n, v in ORIGINAL.items():
        setattr(deploy, n, v)
    deploy.alerts = type('A', (), {'notify': staticmethod(lambda text, site='': NOTES.append(text))})
    deploy.activity = type('L', (), {'log': staticmethod(lambda *a: LOGGED.append(a))})
    NOTES.clear()
    LOGGED.clear()
    perf.invalidate('status')


def raises(fn, text, kind=(ValueError, RuntimeError)):
    try:
        fn()
    except kind as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('expected an error containing %r' % text)


def fake_ssh(upload_rc=0, script_out=b'', script_rc=0):
    """popen(): the upload gets `upload_rc`, the deploy script prints `script_out` and exits `script_rc`."""
    procs = []

    def popen(cfg, remote):
        p = FakeProc(b'upload error' if upload_rc else b'', upload_rc) if remote.startswith('cat >') else FakeProc(script_out, script_rc)
        procs.append((remote, p))
        return p
    deploy.popen = popen
    return procs


def wait(jid):
    for _ in range(200):
        if deploy.JOBS[jid].done:
            return deploy.JOBS[jid]
        time.sleep(0.02)
    raise AssertionError('job did not finish')


def test_job_keeps_newest_lines_and_reads_from_a_position():
    j = deploy.Job()
    cap = perf.get()['job_lines']
    for i in range(cap + 50):
        j.say('line %d\r\n' % i)
    lines, pos = j.read(0)
    assert j.dropped > 0 and lines[-1] == 'line %d' % (cap + 49) and pos == cap + 50
    assert j.read(pos) == ([], pos)


def test_prune_and_start_job_report_errors():
    deploy.JOBS.clear()
    for i in range(5):
        j = deploy.Job()
        j.done, j.started = True, i
        deploy.JOBS['k%d' % i] = j
    deploy.prune_jobs(2)
    assert sorted(deploy.JOBS) == ['k3', 'k4']

    def boom(job):
        raise RuntimeError('kaput')
    job = wait(deploy.start_job(boom))
    assert 'ERROR: kaput' in job.lines


def test_build_tgz_skips_git_and_node_modules_only_for_node_with_package_json():
    d = tempfile.mkdtemp()
    for p in ('.git/x', 'node_modules/m/i.js', 'app.js', 'package.json'):
        os.makedirs(os.path.dirname(os.path.join(d, p)), exist_ok=True)
        open(os.path.join(d, p), 'w').write('x')
    names = lambda typ: {n.split('/')[0] for n in tarfile.open(deploy.build_tgz(d, typ)).getnames()}
    assert names('node') == {'app.js', 'package.json'}
    assert names('static') == {'app.js', 'package.json', 'node_modules'}


def test_successful_deploy_maps_cloudflare_and_dns():
    reset()
    fake_ssh(script_out=b'step 1\nstep 2\n')
    deploy.cloudflare = type('C', (), {'map_hosts': staticmethod(lambda hosts: ['mapped ' + h for h in hosts])})
    deploy.dnsproviders = type('D', (), {'map_hosts': staticmethod(lambda hosts: ['A ' + h for h in hosts])})
    seen = []
    deploy.ON_SITE_ACTION = lambda n, a: seen.append((n, a))
    job = wait(deploy.start_deploy(app(mapCf=True, mapDns=True, redirectFrom=['www.demo.example.com']), CFG, 'me'))
    assert job.ok and seen == [('demo', 'deploy')]
    text = '\n'.join(job.lines)
    assert 'step 2' in text and 'mapped www.demo.example.com' in text and 'A demo.example.com' in text and 'DONE: deployed https://demo.example.com' in text


def test_mapping_failures_do_not_fail_the_deploy():
    reset()
    fake_ssh()

    def bad(hosts):
        raise RuntimeError('api down')
    deploy.cloudflare = type('C', (), {'map_hosts': staticmethod(bad)})
    deploy.dnsproviders = type('D', (), {'map_hosts': staticmethod(bad)})
    job = wait(deploy.start_deploy(app(mapCf=True, mapDns=True), CFG, 'me'))
    text = '\n'.join(job.lines)
    assert job.ok and 'Cloudflare mapping FAILED: api down' in text and 'DNS record FAILED: api down' in text


def test_upload_failure_is_reported():
    reset()
    fake_ssh(upload_rc=1)
    job = wait(deploy.start_deploy(APP, CFG, 'me'))
    assert not job.ok and any('upload failed: upload error' in l for l in job.lines)


def test_rollback_and_first_deploy_failure_alert_and_log():
    reset()
    fake_ssh(script_rc=3)
    job = wait(deploy.start_deploy(APP, CFG, 'me'))
    assert not job.ok and any(l.startswith('ROLLED BACK') for l in job.lines) and 'ROLLED BACK' in NOTES[0] and LOGGED[0][0] == 'deploy-rollback'
    NOTES.clear()
    LOGGED.clear()
    fake_ssh(script_rc=4)
    job = wait(deploy.start_deploy(APP, CFG, 'me'))
    assert any('first deploy' in l for l in job.lines) and 'FIRST DEPLOY FAILED' in NOTES[0] and LOGGED[0][0] == 'deploy-failed'


def test_script_job():
    reset()
    fake_ssh(script_out=b'hello\n')
    job = wait(deploy.start_script_job(CFG, 'echo hello'))
    assert job.ok and job.lines[-1] == 'DONE'
    fake_ssh(script_rc=2)
    job = wait(deploy.start_script_job(CFG, 'false'))
    assert not job.ok and job.lines[-1] == 'FAILED (exit code 2)'


def use(*rules):
    fake = FakeServer([(t, (rc, out)) for t, rc, out in rules])

    def run(cfg, remote, inp=None, timeout=60):
        return fake.run(cfg, remote + ' ' + (inp or b'').decode(), inp, timeout)
    deploy.run = run
    fake.calls_text = lambda: [c['cmd'] for c in fake.calls]
    return fake


def test_site_actions():
    reset()
    fake = use(('systemctl', 0, 'ok'))
    seen = []
    deploy.ON_SITE_ACTION = lambda n, a: seen.append((n, a))
    assert deploy.site_action(CFG, {'name': 'demo', 'action': 'stop'}) == (0, 'ok') and seen == [('demo', 'stop')]
    assert 'systemctl stop demo' in fake.calls[0]['cmd'] and 'docker stop -t 30' in fake.calls[0]['cmd']
    raises(lambda: deploy.site_action(CFG, {'name': 'coderunner', 'action': 'delete'}), 'cannot be deleted')
    raises(lambda: deploy.site_action(CFG, {'name': 'coderunner', 'action': 'stop'}), 'cannot be stopped')
    raises(lambda: deploy.site_action(CFG, {'name': 'demo', 'action': 'dance'}), 'unknown action')
    raises(lambda: deploy.site_action(CFG, {'name': 'Bad Name', 'action': 'start'}), 'bad site name')
    raises(lambda: deploy.site_action(CFG, {'name': 'demo', 'action': 'activate', 'release': '../x'}), 'bad release')
    rel = [r for r in ('20260101-000000',) if config.REL_RE.match(r)] or ['20260101-000000']
    deploy.site_action(CFG, {'name': 'demo', 'action': 'activate', 'release': rel[0]})
    assert rel[0] in fake.calls[-1]['inp'].decode()


def test_delete_needs_confirmation_and_cleans_up_cloudflare():
    reset()
    fake = use(('delete', 0, 'deleted demo\n'))
    raises(lambda: deploy.site_action(CFG, {'name': 'demo', 'action': 'delete', 'confirm': 'nope'}), 'type the site name')
    deploy.cloudflare = type('C', (), {'unmap_hosts': staticmethod(lambda hosts: ['unmapped ' + h for h in hosts])})
    old = config.cloudflare_ready
    config.cloudflare_ready = lambda: True
    try:
        rc, text = deploy.site_action(CFG, {'name': 'demo', 'action': 'delete', 'confirm': 'demo', 'dropDb': True, 'unmapCf': True, 'hosts': ['demo.example.com']})
        assert rc == 0 and 'unmapped demo.example.com' in text and 'true' in fake.calls[-1]['inp'].decode()

        def bad(hosts):
            raise RuntimeError('nope')
        deploy.cloudflare = type('C', (), {'unmap_hosts': staticmethod(bad)})
        rc, text = deploy.site_action(CFG, {'name': 'demo', 'action': 'delete', 'confirm': 'demo', 'unmapCf': True, 'hosts': ['demo.example.com']})
        assert 'Cloudflare cleanup FAILED: nope' in text
    finally:
        config.cloudflare_ready = old


def test_env_editing_and_reading():
    reset()
    fake = use(('setenv', 0, 'ok'), ('cat /etc/demo.env', 0, 'A=1\nbad key=2\nB=x=y\n'))
    deploy.edit_env(CFG, {'name': 'demo', 'set': {'A': "it's"}, 'unset': ['B']})
    sent = fake.calls[0]['inp'].decode()
    assert 'setenv' in sent and 'unsetenv' in sent
    raises(lambda: deploy.edit_env(CFG, {'name': 'demo'}), 'Nothing to change')
    raises(lambda: deploy.edit_env(CFG, {'name': 'demo', 'set': {'1x': 'y'}}), 'Bad variable name')
    raises(lambda: deploy.edit_env(CFG, {'name': 'demo', 'unset': ['1x']}), 'Bad variable name')
    assert deploy.env_values(CFG, 'demo') == [{'key': 'A', 'value': '1'}, {'key': 'B', 'value': 'x=y'}]
    use(('cat /etc/demo.env', 1, ''))
    assert deploy.env_values(CFG, 'demo') == []


def test_run_command_limits():
    reset()
    fake = use(('systemd-run', 0, 'done'))
    assert deploy.run_command(CFG, {'name': 'demo', 'command': ' ls -la '}) == (0, 'done')
    assert "'ls -la'" in fake.calls[0]['inp'].decode()
    for bad in ('', 'x' * 2001, 'a\0b'):
        raises(lambda: deploy.run_command(CFG, {'name': 'demo', 'command': bad}), 'Type a command')


def with_status(sites):
    perf.invalidate('status')
    deploy.status = lambda cfg: {'sites': sites}


def test_rename_host():
    reset()
    real_status = deploy.status
    try:
        with_status([{'name': 'demo', 'hostname': 'demo.example.com', 'redirects': ['old.example.com']}])
        raises(lambda: deploy.rename_host(CFG, {'name': 'coderunner', 'hostname': 'a.example.com'}), 'set when it is installed')
        raises(lambda: deploy.rename_host(CFG, {'name': 'demo', 'hostname': 'nodot'}), 'looks wrong')
        raises(lambda: deploy.rename_host(CFG, {'name': 'other', 'hostname': 'a.example.com'}), 'No site called')
        raises(lambda: deploy.rename_host(CFG, {'name': 'demo', 'hostname': 'demo.example.com'}), 'already its address')
        raises(lambda: deploy.rename_host(CFG, {'name': 'demo', 'hostname': 'old.example.com'}), 'already redirects')
        use(('ENVTOO', 1, 'caddy invalid'))
        raises(lambda: deploy.rename_host(CFG, {'name': 'demo', 'hostname': 'new.example.com'}), 'caddy invalid')
        use(('ENVTOO', 0, 'renamed\n'))
        seen = []
        deploy.ON_SITE_ACTION = lambda n, a: seen.append(a)
        deploy.cloudflare = type('C', (), {'map_hosts': staticmethod(lambda h: ['cf+' + h[0]]), 'unmap_hosts': staticmethod(lambda h: ['cf-' + h[0]])})
        deploy.dnsproviders = type('D', (), {'connected': staticmethod(lambda: True), 'map_hosts': staticmethod(lambda h: (_ for _ in ()).throw(RuntimeError('boom'))),
                                              'unmap_hosts': staticmethod(lambda h: [])})
        old = config.cloudflare_ready
        config.cloudflare_ready = lambda: True
        try:
            lines = deploy.rename_host(CFG, {'name': 'demo', 'hostname': 'NEW.example.com', 'mapCf': True, 'mapDns': True, 'env': True})
        finally:
            config.cloudflare_ready = old
        assert lines[0] == 'renamed' and 'cf+new.example.com' in lines and 'cf-demo.example.com' in lines
        assert any('DNS step FAILED: boom' in l for l in lines) and seen == ['deploy']
    finally:
        deploy.status = real_status


def test_site_config_sections():
    reset()
    use(('/etc/caddy/sites/demo.caddy', 0, '### /etc/caddy/sites/demo.caddy\nline1\n\n### /etc/deploy-meta/demo.json\n(not present)\n'))
    secs = deploy.site_config(CFG, 'demo')
    assert [s['path'] for s in secs] == ['/etc/caddy/sites/demo.caddy', '/etc/deploy-meta/demo.json'] and secs[0]['text'] == 'line1'


def test_logs_filters():
    reset()
    fake = use(('journalctl', 0, 'a\nb\n'))
    assert deploy.logs(CFG, 'demo', 5000, query='err', level='error', since='1h') == 'a\nb\n'
    cmd = fake.calls[0]['cmd']
    assert '-n 5000' in cmd and "--since '1 hour ago'" in cmd and 'grep -iE' in cmd and 'grep -iF' in cmd and 'tail -n 1000' in cmd
    use(('journalctl', 0, '  \n'))
    assert deploy.logs(CFG, 'demo', 1) == '(no matching lines)'
    raises(lambda: deploy.logs(CFG, 'demo', 50, level='bogus'), 'Unknown log filter')
    raises(lambda: deploy.logs(CFG, 'demo', 50, since='3y'), 'Unknown log filter')
    raises(lambda: deploy.logs(CFG, 'demo', 50, query='a\nb'), 'one line')


def test_runtimes_only_accepts_safe_paths():
    reset()
    use(('Node versions', 0, '/usr/bin/node v20.1.0\n/tmp/evil/node v1.0.0\n/opt/node-22/bin/node v22.0.0\n/usr/bin/node bad\n'))
    assert deploy.runtimes(CFG) == [{'path': '/usr/bin/node', 'version': 'v20.1.0'}, {'path': '/opt/node-22/bin/node', 'version': 'v22.0.0'}]


def test_status_reads_the_server_and_probes_public_addresses():
    reset()
    payload = {'sites': [{'name': 'a', 'hostname': 'a.example.com'}, {'name': 'b', 'hostname': '*.example.com'}, {'name': 'c', 'hostname': 'c.example.com'},
                         {'name': 'd', 'hostname': 'd.example.com'}]}
    use(('python3 -', 0, 'noise ' + json.dumps(payload) + ' tail'))
    deploy.PUBLIC_LOOKUP = lambda h: (200, 12) if h == 'a.example.com' else None
    import urllib.error
    import urllib.request

    def fake_open(req, timeout=0):
        if 'c.example' in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 503, 'x', {}, None)
        if 'd.example' in req.full_url:
            raise OSError('unreachable')
        raise AssertionError('unexpected probe')
    real = urllib.request.urlopen
    urllib.request.urlopen = fake_open
    try:
        data = deploy._status_now(CFG)
    finally:
        urllib.request.urlopen = real
    by = {s['name']: s for s in data['sites']}
    assert by['a']['public'] == 200 and by['a']['public_ms'] == 12 and 'public' not in by['b'] and by['c']['public'] == 503 and by['d']['public'] == 'err'
    use(('python3 -', 1, 'no python'))
    raises(lambda: deploy._status_now(CFG), 'no python')


def test_file_pickers_fall_back_to_empty():
    real = deploy.subprocess.run
    deploy.subprocess.run = lambda *a, **k: type('R', (), {'stdout': ' /x/y \n'})()
    try:
        assert deploy.pick_file() == '/x/y' and deploy.pick_folder() == '/x/y'

        def boom(*a, **k):
            raise OSError('no display')
        deploy.subprocess.run = boom
        assert deploy.pick_file() == '' and deploy.pick_folder() == ''
    finally:
        deploy.subprocess.run = real


if __name__ == '__main__':
    reset()
    run_tests(dict(globals()), before=lambda: (reset(), deploy.JOBS.clear()))
