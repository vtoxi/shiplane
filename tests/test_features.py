"""Health check path, Node version, kept node_modules, forwarded https, log search, .env-free off-server backups (no server needed).

    python tests/test_features.py
"""
import ast
import os
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import backups, config, deploy, runner, settings  # noqa: E402

SRC = tempfile.mkdtemp()
config.CF_FILE = os.path.join(SRC, 'no-cloudflare.json')
config.DATA = tempfile.mkdtemp()
os.environ.pop('CLOUDFLARE_API_TOKEN', None)
BASE = {'name': 'demo', 'hostname': 'demo.example.com', 'type': 'node', 'source': SRC, 'port': 5000, 'start': 'server.js'}


def bash_ok(text):
    return subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True).returncode == 0


def names(path):
    with tarfile.open(path) as t:
        return sorted(m.name for m in t.getmembers())


def test_node_modules_are_dropped_only_when_there_is_a_package_json():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, 'node_modules', 'next'))
    open(os.path.join(d, 'server.js'), 'w').close()
    assert 'node_modules' in names(deploy.build_tgz(d, 'node'))                     # nothing to reinstall from: keep it
    open(os.path.join(d, 'package.json'), 'w').close()
    assert 'node_modules' not in names(deploy.build_tgz(d, 'node'))                 # the server reinstalls it
    assert 'node_modules' in names(deploy.build_tgz(d, 'static'))


def test_tunnel_sites_tell_the_app_the_visitor_used_https():
    assert 'header_up X-Forwarded-Proto https' in deploy.deploy_script(deploy.validate_app(BASE), 'bob')
    assert 'header_up' not in deploy.deploy_script(deploy.validate_app(dict(BASE, direct=True)), 'bob')
    assert 'env HTTPS on' in deploy.deploy_script(deploy.validate_app(dict(BASE, type='php', port='')), 'bob')


def test_health_path_status_and_runtime_are_validated_and_reach_the_script():
    s = deploy.deploy_script(deploy.validate_app(dict(BASE, healthPath='/health/ready', healthStatus='204', runtime='/usr/local/n/versions/node/22.1.0/bin/node')), 'bob')
    assert "HEALTH=/health/ready; EXPECT=204; NODEBIN=/usr/local/n/versions/node/22.1.0/bin/node" in s and '__HEALTH__' not in s and '__NODE__' not in s and '__EXPECT__' not in s and bash_ok(s)
    d = deploy.validate_app(BASE)
    assert d['healthPath'] == '/' and d['healthStatus'] == '' and d['runtime'] == ''
    for bad, text in (({'healthPath': 'health'}, 'Health check path'), ({'healthPath': '/a/../b'}, 'Health check path'), ({'healthStatus': 'ok'}, 'number'), ({'healthStatus': 700}, 'between'),
                      ({'runtime': '/home/me/node'}, 'Node runtime'), ({'runtime': '/usr/bin/node', 'type': 'static'}, 'Node runtime')):
        try:
            deploy.validate_app(dict(BASE, **bad))
        except ValueError as e:
            assert text in str(e), (text, str(e))
        else:
            raise AssertionError(bad)


def test_log_search_builds_a_valid_safe_command():
    seen = []
    deploy.run = lambda cfg, cmd, **kw: (seen.append(cmd), (0, 'a\n'))[1]
    try:
        out = deploy.logs({}, 'demo', 200, "it's; rm -rf /", 'error', '1h')
        cmd = seen[-1]
        assert bash_ok(cmd) and "'it'\"'\"'s; rm -rf /'" in cmd and "--since '1 hour ago'" in cmd and 'tail -n 200' in cmd and out == 'a\n'
        assert 'journalctl -u demo -n 200 --no-pager' in (deploy.logs({}, 'demo', 200), seen[-1])[1]            # no filter: no long look-back, no grep
        assert 'grep' not in seen[-1]
        for kw in ({'level': 'debug'}, {'since': 'forever'}, {'query': 'x\ny'}):
            try:
                deploy.logs({}, 'demo', 50, **kw)
            except ValueError:
                continue
            raise AssertionError(kw)
    finally:
        deploy.run = runner.run
    deploy.run = lambda cfg, cmd, **kw: (0, '')
    assert deploy.logs({}, 'demo', 50, 'zzz') == '(no matching lines)'
    deploy.run = runner.run


def test_runtimes_lists_only_installed_system_node_versions():
    deploy.run = lambda cfg, cmd, **kw: (0, '/usr/bin/node v26.0.0\n/home/x/node v1.0.0\n/opt/node22/bin/node v22.4.1\njunk\n')
    try:
        assert deploy.runtimes({}) == [{'path': '/usr/bin/node', 'version': 'v26.0.0'}, {'path': '/opt/node22/bin/node', 'version': 'v22.4.1'}]
    finally:
        deploy.run = runner.run


def test_offsite_settings_never_expose_the_secret():
    good = {'endpoint': 'https://acc.r2.cloudflarestorage.com', 'bucket': 'my-backups', 'region': '', 'access_key': 'AKIA12345678', 'secret_key': 'S' * 24, 'prefix': 'shiplane/'}
    pub = backups.save_offsite(good)
    assert pub['configured'] and pub['has_secret'] and pub['region'] == 'auto' and 'secret_key' not in pub and 'S' * 24 not in str(pub)
    assert backups.save_offsite(dict(good, secret_key='', bucket='other-bucket'))['bucket'] == 'other-bucket'     # empty secret keeps the saved one
    assert settings.get('backups_offsite')['secret_key'] == 'S' * 24
    for bad in ({'endpoint': 'http://insecure.example.com'}, {'bucket': 'Bad Bucket'}, {'prefix': 'a b'}, {'access_key': 'x'}):
        try:
            backups.save_offsite(dict(good, **bad))
        except ValueError:
            continue
        raise AssertionError(bad)
    assert backups.save_offsite({'bucket': ''})['configured'] is False
    assert backups.test_offsite({})[0] is False


def test_the_upload_script_signs_a_request_and_sends_it():
    conf = {'endpoint': 'https://acc.example.com', 'bucket': 'b', 'region': 'auto', 'access_key': 'AKIA12345678', 'secret_key': 'S' * 24, 'key': 'p/f.sql.gz'}
    f = os.path.join(SRC, 'f.sql.gz')
    open(f, 'wb').write(b'data')
    text = open(os.path.join(os.path.dirname(runner.__file__), 'remote', 's3put.py')).read().replace('__CONF__', repr(__import__('json').dumps(conf))).replace('__FILE__', repr(f))
    ast.parse(text)
    sent = {}
    sys.stdout.flush()

    class Resp:
        status = 200

        def read(self):
            return b''

    class Conn:
        def __init__(self, *a, **k): sent['host'] = a

        def request(self, method, path, body=None, headers=None):
            sent.update(method=method, path=path, headers=headers)

        def getresponse(self):
            return Resp()

    import http.client
    real = http.client.HTTPSConnection
    http.client.HTTPSConnection = Conn
    try:
        exec(compile(text, 's3put', 'exec'), {})
    finally:
        http.client.HTTPSConnection = real
    h = sent['headers']
    assert sent['method'] == 'PUT' and sent['path'] == '/b/p/f.sql.gz' and h['Content-Length'] == '4'
    assert h['Authorization'].startswith('AWS4-HMAC-SHA256 Credential=AKIA12345678/') and '/auto/s3/aws4_request' in h['Authorization'] and 'S' * 24 not in h['Authorization']


def test_backup_verification_setting_job_and_nightly_run():
    from coderunner import activity, dbops
    assert backups.get()['verify'] is False and backups.save(True, 3, 5, True)['verify'] is True and backups.save(True, 3, 5)['verify'] is True    # omitted: unchanged
    notes, logged, saved, orig = [], [], (dbops.verify, backups.alerts, backups.activity), (dbops.list_all, dbops.backup)
    backups.alerts = type('A', (), {'notify': staticmethod(lambda t, site='': notes.append(t))})
    backups.activity = type('L', (), {'log': staticmethod(lambda *a: logged.append(a))})
    try:
        dbops.verify = lambda cfg, f: {'file': f, 'tables': 4}
        job = deploy.Job()
        backups._verify_job(job, {}, 'me', 'shop-20260101-020000.sql.gz')
        assert job.ok and 'restores correctly (4 tables)' in job.lines[-1] and logged[-1][-1] is True
        dbops.verify = lambda cfg, f: (_ for _ in ()).throw(RuntimeError('NOT RESTORABLE: boom'))
        job = deploy.Job()
        backups._verify_job(job, {}, 'me', 'shop-20260101-020000.sql.gz')
        assert not job.ok and job.lines[-1].startswith('FAILED') and 'could NOT be restored' in notes[-1] and logged[-1][-1] is False
        try:
            backups.start_verify({}, 'me', '../x')
        except ValueError:
            pass
        else:
            raise AssertionError('accepted a bad file name')
        # nightly: with the setting on, every new backup is test-restored
        said = []
        dbops.verify = lambda cfg, f: {'file': f, 'tables': 2}
        dbops.list_all = lambda cfg: {'databases': [{'name': 'shop'}], 'backups': []}
        dbops.backup = lambda cfg, n: {'file': n + '-20260101-020000.sql.gz', 'size': 5}
        backups.run_all({}, said.append)
        assert any('test restore: 2 table(s)' in l for l in said)
    finally:
        dbops.verify, backups.alerts, backups.activity = saved
        dbops.list_all, dbops.backup = orig
        backups.save(False, 3, 14, False)


if __name__ == '__main__':
    run_tests(globals())
