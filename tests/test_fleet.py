"""Fleet: saved servers, the side-by-side overview, switching the active server and adopting a fresh one (fakes, no SSH).

    python tests/test_fleet.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

CONF = tempfile.mkdtemp()
os.environ['CODERUNNER_DATA'] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeProc, run_tests  # noqa: E402
from coderunner import config, deploy, fleet, monitor  # noqa: E402

config.CONF = CONF
config.SERVER_FILE = os.path.join(CONF, 'server.json')
KEYED = {'name': 'web-1', 'host': 'web1.example.com', 'user': 'deploy', 'auth': 'key', 'key': '~/.ssh/id_ed25519', 'port': 22}
PWD = {'name': 'db-1', 'host': 'db1.example.com', 'user': 'root', 'auth': 'password', 'password': 's3cret-pw', 'port': 2222}
PROBE_OK = 'os=Ubuntu_24.04\nload=0.50\ncpus=2\nmem=40\ndisk=55\nsites=3\nfailed=0\nreboot=0\ncaddy=1\n'


def raises(fn, text):
    try:
        fn()
    except (ValueError, LookupError, RuntimeError) as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('expected ' + text)


def reset():
    for f in ('fleet.json', 'server.json'):
        if os.path.exists(os.path.join(CONF, f)):
            os.remove(os.path.join(CONF, f))
    config.save_server({'mode': 'ssh', 'auth': 'key', 'host': 'main.example.com', 'user': 'deploy', 'key': '~/.ssh/k', 'port': 22, 'domains': []})


def test_the_current_connection_becomes_the_first_server():
    reset()
    out = fleet.list_servers()
    assert [s['host'] for s in out['servers']] == ['main.example.com'] and out['active'] == out['servers'][0]['id'] and out['servers'][0]['active']


def test_the_first_server_added_becomes_the_active_one():
    for f in ('fleet.json', 'server.json'):
        if os.path.exists(os.path.join(CONF, f)):
            os.remove(os.path.join(CONF, f))
    assert fleet.list_servers() == {'servers': [], 'active': ''}
    a = fleet.save(KEYED)
    assert a['active'] and config.load_server()['host'] == 'web1.example.com'
    assert not fleet.save(PWD)['active']                                                   # later ones wait for "Manage"


def test_add_update_and_the_password_is_write_only():
    reset()
    a = fleet.save(KEYED)
    b = fleet.save(PWD)
    assert 'password' not in b and b['hasPassword'] is True and b['port'] == 2222
    assert 's3cret-pw' not in json.dumps(fleet.list_servers())
    assert oct(os.stat(os.path.join(CONF, 'fleet.json')).st_mode & 0o777) == '0o600'
    fleet.save(dict(PWD, id=b['id'], password='', name='db-renamed'))                                   # empty password keeps the saved one
    stored = [s for s in json.load(open(os.path.join(CONF, 'fleet.json')))['servers'] if s['id'] == b['id']][0]
    assert stored['password'] == 's3cret-pw' and stored['name'] == 'db-renamed' and a['id'] != b['id']


def test_validation_and_duplicates():
    reset()
    fleet.save(KEYED)
    raises(lambda: fleet.save(dict(KEYED, name='other')), 'already in the list')
    raises(lambda: fleet.save(dict(KEYED, host='x.example.com', name='WEB-1')), 'already a server with that name')
    raises(lambda: fleet.save(dict(KEYED, host='y.example.com', name='')), 'Give the server a name')
    raises(lambda: fleet.save(dict(KEYED, host='bad host!', name='z')), 'host looks wrong')
    raises(lambda: fleet.save(dict(KEYED, host='z.example.com', name='z', user='ro ot')), 'user looks wrong')
    raises(lambda: fleet.save(dict(PWD, name='p', host='p.example.com', password='')), 'SSH password')
    raises(lambda: fleet.save(dict(KEYED, id='nope', name='n', host='n.example.com')), 'no such server')
    out = fleet.save(dict(KEYED, name='local-try', host='q.example.com', mode='local'))                 # extra servers are always reached over SSH
    assert out['mode'] == 'ssh'


def test_delete_and_activate():
    reset()
    first = fleet.list_servers()['active']
    b = fleet.save(KEYED)
    raises(lambda: fleet.delete(first), 'Switch to another')
    monitor.STATE['shop'] = {'host': 'x', 'up': True, 'points': [[1, 2]]}
    out = fleet.activate(b['id'])
    assert out['active'] and config.load_server()['host'] == 'web1.example.com' and fleet.list_servers()['active'] == b['id']
    assert 'shop' not in monitor.STATE                                                                   # history of the old server is gone
    fleet.delete(first)
    assert [s['id'] for s in fleet.list_servers()['servers']] == [b['id']]
    raises(lambda: fleet.delete('missing'), 'no such server')
    raises(lambda: fleet.activate('missing'), 'no such server')


def test_editing_the_active_server_updates_server_json():
    reset()
    first = fleet.list_servers()['servers'][0]
    fleet.save(dict(first, name='renamed', port=2200, password=''))
    assert config.load_server()['port'] == 2200


def test_overview_reports_health_and_unreachable_servers():
    reset()
    fleet.save(KEYED)
    fleet.save(PWD)

    def fake_run(cfg, remote, inp=None, timeout=60):
        if cfg['host'] == 'web1.example.com':
            return 0, PROBE_OK
        if cfg['host'] == 'db1.example.com':
            return 255, 'ssh: connect to host db1.example.com port 2222: Connection timed out'
        return 0, PROBE_OK.replace('disk=55', 'disk=93').replace('failed=0', 'failed=2').replace('caddy=1', 'caddy=0')
    fleet.run = fake_run
    rows = {r['name']: r for r in fleet.overview()}
    assert rows['web-1']['ok'] and rows['web-1']['sites'] == 3 and rows['web-1']['mem'] == 40 and rows['web-1']['problems'] == [] and rows['web-1']['os'] == 'Ubuntu 24.04'
    assert not rows['db-1']['ok'] and 'timed out' in rows['db-1']['error'] and rows['db-1']['problems'] == ['unreachable']
    bad = rows['main.example.com']
    assert bad['active'] and set(bad['problems']) == {'disk 93% full', '2 failed services', 'not set up for deploys yet (Adopt)'}


def test_probe_parsing_is_defensive():
    assert fleet.parse_probe('garbage\nload=abc\n')['load'] is None and fleet.parse_probe('')['sites'] is None


def test_adopt_needs_confirmation_and_runs_the_bootstrap_on_that_server():
    reset()
    b = fleet.save(KEYED)
    raises(lambda: fleet.start_adopt(b['id'], 'yes'), 'Type "adopt"')
    raises(lambda: fleet.start_adopt('missing', 'adopt'), 'no such server')
    sent = {}

    def popen(cfg, remote):
        sent['host'] = cfg['host']
        p = FakeProc(b'== Tools\ndone: ok\n', 0)
        real_close = p.stdin.close
        p.stdin.close = lambda: (real_close(), sent.__setitem__('script', p.stdin.data.decode()))
        return p
    real = deploy.popen
    deploy.popen = popen
    try:
        jid = fleet.start_adopt(b['id'], 'adopt')
        for _ in range(200):
            if deploy.JOBS[jid].done:
                break
            time.sleep(0.02)
    finally:
        deploy.popen = real
    job = deploy.JOBS[jid]
    assert job.ok and job.lines[-1].startswith('DONE') and sent['host'] == 'web1.example.com' and 'caddy' in sent['script']
    assert 'ufw' not in sent['script'] and 'sshd' not in sent['script']                                   # never touches SSH or the firewall


def test_remote_scripts_are_valid_bash():
    for name in ('bootstrap.sh', 'fleet_probe.sh'):
        text = open(os.path.join(config.REMOTE, name)).read()
        assert subprocess.run(['bash', '-n'], input=text.encode(), capture_output=True).returncode == 0, name


if __name__ == '__main__':
    run_tests(dict(globals()))
