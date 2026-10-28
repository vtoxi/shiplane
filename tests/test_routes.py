"""API handlers for settings, maintenance and migration called directly with fakes behind them (no HTTP, no SSH).

    python tests/test_routes.py
"""
import json
import os
import sys
import tempfile

DATA = tempfile.mkdtemp()
os.environ['CODERUNNER_DATA'] = DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeServer, run_tests  # noqa: E402
from coderunner import config, deploy, maintenance, migrate, migrator, server  # noqa: E402
from coderunner.deploy import pick_file  # noqa: E402
from scripts import make_sample_backup as sample  # noqa: E402

CFG = {'mode': 'ssh', 'auth': 'password', 'host': 'new.example.com', 'port': 22, 'user': 'bob', 'key': '', 'password': 'topsecret', 'domains': []}
BACKUP = sample.build(os.path.join(DATA, 'demo.tar.gz'))
AUDIT = []
ORIGINAL = {(server, 'cfg'): server.cfg, (server, 'audit'): server.audit, (server, 'run'): server.run, (config, 'SERVER_FILE'): config.SERVER_FILE,
            (maintenance, 'run'): maintenance.run, (migrate, 'run'): migrate.run, (migrator, 'run'): migrator.run, (migrator, 'start'): migrator.start}


def restore():
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)
    server.cfg = lambda: CFG
    server.audit = lambda kind, target='', detail='', ok=True: AUDIT.append((kind, target, detail, ok))
    config.SERVER_FILE = os.path.join(DATA, 'server.json')
    AUDIT.clear()
    migrator.RUNS.clear()


def call(method, path, body=None, **qs):
    return server.ROUTES[(method, path)]['fn']({k: [str(v)] for k, v in qs.items()}, body or {})


def raises(fn, text, kind=Exception):
    try:
        fn()
    except kind as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


# ------------------------------------------------------------ roles
def test_new_endpoints_need_the_right_role():
    expect = {('GET', '/api/domains/health'): 'admin', ('GET', '/api/cron'): 'admin', ('POST', '/api/cron/add'): 'admin', ('POST', '/api/cron/delete'): 'admin',
              ('GET', '/api/disk'): 'admin', ('POST', '/api/disk/clean'): 'admin', ('POST', '/api/migrate/inspect'): 'admin', ('POST', '/api/migrate/prepare'): 'admin',
              ('POST', '/api/migrate/start'): 'admin', ('GET', '/api/migrate/run'): 'admin', ('GET', '/api/migrate/active'): 'admin', ('POST', '/api/config'): 'super_admin'}
    for key, role in expect.items():
        assert server.ROUTES[key]['role'] == role and not server.ROUTES[key]['public'], key


# ------------------------------------------------------------ settings and the password
def test_session_never_returns_the_password():
    out = call('GET', '/api/session')
    assert 'password' not in out['server'] and out['server']['hasPassword'] is True and out['keyFound'] is True
    assert 'topsecret' not in json.dumps(out)


def test_connection_test_explains_failures_in_plain_language():
    server.run = lambda c, cmd, timeout=60: (255, 'ssh: connect to host new.example.com port 22: Connection timed out')
    out = call('POST', '/api/test-connection', dict(CFG, port=2222))
    assert out['ok'] is False and 'Could not reach new.example.com port 2222' in out['text'] and 'firewall' in out['text']
    server.run = lambda c, cmd, timeout=60: (0, 'connected as bob on host\nsudo: ok\n')
    config.save_server(CFG)
    out = call('POST', '/api/test-connection', dict(CFG, password=''))                       # blank password: the saved one is used
    assert out['ok'] is True and 'connected as bob' in out['text']
    raises(lambda: call('POST', '/api/test-connection', dict(CFG, port='abc')), 'port')


def test_saving_the_connection_audits_without_secrets():
    config.save_server(CFG)
    assert call('POST', '/api/config', dict(CFG, password='', port='2200')) == {'ok': True}
    saved = json.load(open(config.SERVER_FILE))
    assert saved['password'] == 'topsecret' and saved['port'] == 2200
    assert AUDIT == [('settings', 'connection', 'connection settings changed', True)]


# ------------------------------------------------------------ maintenance routes
def test_cron_routes_audit_every_change():
    files = {}

    def run(cfg, cmd, inp=None, timeout=60):
        if cmd.startswith('id app_'):
            return 0, 'yes'
        if cmd.startswith('sudo cat'):
            return 0, next(iter(files.values()), '')
        if cmd.startswith('sudo tee'):
            files['f'] = inp.decode()
        if cmd.startswith('sudo rm'):
            files.clear()
        return 0, ''
    maintenance.run = run
    out = call('POST', '/api/cron/add', {'name': 'web', 'schedule': '0 3 * * *', 'command': 'node a.js'})
    assert out == {'ok': True, 'text': 'added'}
    tasks = call('GET', '/api/cron', name='web')['tasks']
    assert tasks[0]['command'] == 'node a.js'
    assert call('POST', '/api/cron/delete', {'name': 'web', 'id': tasks[0]['id']})['ok'] is True
    assert [a[0] for a in AUDIT] == ['cron-add', 'cron-delete'] and AUDIT[0][2] == '0 3 * * * node a.js'
    raises(lambda: call('POST', '/api/cron/add', {'name': 'web', 'schedule': 'bad', 'command': 'x'}), '5 parts', ValueError)


def test_disk_and_domain_routes():
    report = {'disk_total': 10, 'disk_free': 5, 'sites': [], 'system': {'journal': 0, 'apt': 0, 'docker': []}}
    maintenance.run = FakeServer().when('python3', 0, json.dumps(report)).run
    assert call('GET', '/api/disk') == report
    free = iter(['100', '350'])
    maintenance.run = lambda c, cmd, inp=None, timeout=60: (0, next(free)) if cmd.startswith('df') else (0, 'cleaned')
    out = call('POST', '/api/disk/clean', {'what': 'journal'})
    assert out == {'ok': True, 'freed': 250, 'text': 'cleaned'} and AUDIT[-1] == ('cleanup', 'journal', 'freed 250 bytes', True)
    deploy_status = deploy.status
    deploy.status = lambda cfg: {'sites': [{'name': 'a', 'hostname': 'a.example.com', 'public': 200, 'public_ms': 5}]}
    maintenance._check_host = lambda host: {'host': host, 'dns': '1.1.1.1', 'cert_days': 40, 'issuer': 'x', 'tls_error': None}
    try:
        assert call('GET', '/api/domains/health')['domains'][0]['site'] == 'a'
    finally:
        deploy.status = deploy_status


# ------------------------------------------------------------ migration routes
def test_inspect_route_reports_what_the_new_server_lacks():
    migrate.run = FakeServer().when('php-fpm', 0, 'no').when('mysqld', 0, 'yes').when('postfix', 0, 'no').run
    out = call('POST', '/api/migrate/inspect', {'source': 'backup', 'path': BACKUP})
    assert len(out['sites']) == 5 and {c['id']: c['installed'] for c in out['capabilities']} == {'php': False, 'mysql': True, 'postgres': False, 'mail': False}
    assert AUDIT[-1][0] == 'migrate-inspect' and '5 sites, 3 databases' in AUDIT[-1][2]
    raises(lambda: call('POST', '/api/migrate/inspect', {'source': 'backup', 'path': 'C:/nope.tar.gz'}), 'cPanel or Plesk backup file', ValueError)


def test_prepare_route_only_installs_allowed_components():
    started = []
    deploy.start_script_job = lambda cfg, text: started.append(text) or 'job-1'
    assert call('POST', '/api/migrate/prepare', {'id': 'php'}) == {'job': 'job-1'} and AUDIT[-1] == ('migrate-install', 'php', '', True)
    raises(lambda: call('POST', '/api/migrate/prepare', {'id': 'mail'}), 'cannot be installed', ValueError)
    raises(lambda: call('POST', '/api/migrate/prepare', {}), 'cannot be installed', ValueError)


def test_start_run_and_active_routes():
    assert call('GET', '/api/migrate/active') == {'run': None}
    raises(lambda: call('GET', '/api/migrate/run', id='nope'), 'unknown migration', LookupError)
    migrator.start = lambda cfg, body, user: 'run-7'
    assert call('POST', '/api/migrate/start', {'path': BACKUP, 'sites': ['a'], 'databases': ['d', 'e']}) == {'id': 'run-7'}
    assert AUDIT[-1] == ('migrate-start', BACKUP, '1 sites, 2 databases', True)
    r = migrator.Run('demo')
    r.add('G', 'one')
    migrator.RUNS[r.id] = r
    assert call('GET', '/api/migrate/active')['run']['id'] == r.id
    view = call('GET', '/api/migrate/run', id=r.id, since=0)
    assert view['title'] == 'demo' and view['steps'][0]['label'] == 'one'
    assert call('GET', '/api/migrate/run', id=r.id, since=99)['lines'] == []


def test_inspect_route_suggests_the_names_that_would_be_used():
    migrate.run = FakeServer().run
    out = call('POST', '/api/migrate/inspect', {'source': 'backup', 'path': BACKUP})
    assert out['suggestions']['databases']['demo_wp'] == {'name': 'demo_wp', 'user': 'demo_wp'}
    assert out['suggestions']['sites']['demo-blog.example'] == {'domain': 'demo-blog.example', 'site': 'demo-blog-example'}


def test_file_picker_route_and_dialog():
    deploy.pick_file = lambda: 'C:/Backups/cpmove-demo.tar.gz'
    try:
        assert call('POST', '/api/pick-file') == {'path': 'C:/Backups/cpmove-demo.tar.gz'}
        server.cfg = lambda: dict(CFG, mode='local')
        raises(lambda: call('POST', '/api/pick-file'), 'not available when the app runs on the server', ValueError)
        server.cfg = lambda: CFG
        server.ctx.via_key = True
        raises(lambda: call('POST', '/api/pick-file'), 'browser app', PermissionError)                 # API keys cannot open a dialog
    finally:
        server.ctx.via_key = False
        deploy.pick_file = pick_file
    assert server.ROUTES[('POST', '/api/pick-file')]['role'] == 'admin'
    seen = {}

    class Done:
        stdout = ' C:/x/y.tgz \n'
    deploy.subprocess.run = lambda cmd, **kw: seen.update(cmd=cmd) or Done()
    try:
        assert pick_file() == 'C:/x/y.tgz' and 'askopenfilename' in seen['cmd'][-1] and '*.tgz' in seen['cmd'][-1]
        deploy.subprocess.run = lambda cmd, **kw: (_ for _ in ()).throw(OSError('no display'))
        assert pick_file() == ''                                                                         # no desktop: empty answer, not a crash
    finally:
        deploy.subprocess.run = __import__('subprocess').run


if __name__ == '__main__':
    run_tests(globals(), before=restore)
