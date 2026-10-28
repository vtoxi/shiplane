"""Starting and stopping services and containers: the allow-list, the guards, and auto-heal respecting what you stopped on purpose (no server needed).

    python tests/test_serverops.py
"""
import json
import os
import sys
import tempfile

DATA = tempfile.mkdtemp()
os.environ['CODERUNNER_DATA'] = DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeServer, run_tests  # noqa: E402
from coderunner import activity, heal, serverops as so, watch  # noqa: E402

SSH_CFG = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'deploy'}
LOCAL_CFG = {'mode': 'local'}
ORIGINAL = {(so, 'run'): so.run, (heal.activity, 'log'): activity.log}


def restore():
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)
    activity.log = lambda *a, **k: None
    for f in ('care.json', 'settings.json'):
        path = os.path.join(DATA, f)
        if os.path.exists(path):
            os.remove(path)


def raises(fn, *args, text='', kind=ValueError):
    try:
        fn(*args)
    except kind as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


def test_the_allow_list():
    for name in ('cloudflared', 'caddy', 'docker', 'postgresql', 'fail2ban', 'ufw', 'mssql-server', 'mariadb', 'mysql', 'redis-server', 'php8.3-fpm', 'php8.5-fpm'):
        assert so.is_service(name), name
    for name in ('ssh', 'sshd', 'systemd-logind', 'php-fpm', 'php8.3', 'php83-fpm', 'php8.3-fpm; rm -rf /', 'caddy sshd', '', 'Caddy', 'php10.1-fpm', None):
        assert not so.is_service(name), name
    srv = FakeServer()
    so.run = srv.run
    for name in ('ssh', 'php8.3-fpm; reboot', 'x y'):
        raises(so.service, SSH_CFG, name, 'stop', text='unknown service')
    assert srv.calls == []                                                                                         # nothing outside the list ever reaches the server


def test_start_stop_enable_disable_commands():
    srv = FakeServer()
    so.run = srv.run
    expect = {'start': ('sudo systemctl start mariadb && echo "mariadb: started"'), 'stop': ('sudo systemctl stop mariadb && echo "mariadb: stopped"'),
              'enable': ('sudo systemctl enable mariadb && echo "mariadb: will start at boot"'), 'disable': ('sudo systemctl disable mariadb && echo "mariadb: will not start at boot"')}
    for action, cmd in expect.items():
        assert so.service(SSH_CFG, 'mariadb', action) == (0, '')
        assert srv.calls[-1]['cmd'] == cmd
    so.service(SSH_CFG, 'php8.4-fpm', 'stop')
    assert srv.calls[-1]['cmd'] == 'sudo systemctl stop php8.4-fpm && echo "php8.4-fpm: stopped"'
    so.service(SSH_CFG, 'mssql-server', 'start')
    assert 'systemctl start mssql-server' in srv.calls[-1]['cmd']
    assert 'journalctl -u redis-server' in (so.service(SSH_CFG, 'redis-server', 'logs'), srv.calls[-1]['cmd'])[1]
    so.service(SSH_CFG, 'redis-server', 'status')
    assert 'systemctl status redis-server' in srv.calls[-1]['cmd']
    so.service(SSH_CFG, 'caddy', 'restart')
    assert 'systemctl restart caddy' in srv.calls[-1]['cmd']
    raises(so.service, SSH_CFG, 'mariadb', 'nuke', text='unknown action')
    raises(so.service, SSH_CFG, 'mariadb', '', text='unknown action')


def test_docker_is_stopped_with_its_socket_or_it_starts_again():
    srv = FakeServer()
    so.run = srv.run
    so.service(SSH_CFG, 'docker', 'stop')
    assert 'systemctl stop docker.socket docker ' in srv.calls[-1]['cmd']
    so.service(SSH_CFG, 'docker', 'disable')
    assert 'systemctl disable docker.socket docker ' in srv.calls[-1]['cmd']
    so.service(SSH_CFG, 'docker', 'enable')
    assert 'systemctl enable docker.socket docker ' in srv.calls[-1]['cmd']
    so.service(SSH_CFG, 'docker', 'start')
    assert 'systemctl start docker &&' in srv.calls[-1]['cmd']                                                    # starting the daemon is enough


def test_you_cannot_cut_off_the_page_you_are_using_or_switch_the_firewall_off():
    srv = FakeServer()
    so.run = srv.run
    for name in ('caddy', 'cloudflared'):
        for action in ('stop', 'disable'):
            raises(so.service, LOCAL_CFG, name, action, text='reached through %s' % name)                          # the app runs ON the server: it would saw off its own branch
        so.service(LOCAL_CFG, name, 'restart')
        so.service(LOCAL_CFG, name, 'start')
        so.service(LOCAL_CFG, name, 'enable')
        so.service(SSH_CFG, name, 'stop')                                                                          # from a computer over SSH it is fine: the app is not behind it
    for action in ('stop', 'disable'):
        for cfg in (LOCAL_CFG, SSH_CFG):
            raises(so.service, cfg, 'ufw', action, text='switch the firewall off')
    so.service(SSH_CFG, 'ufw', 'restart')
    so.service(LOCAL_CFG, 'ufw', 'start')
    assert len([c for c in srv.calls if 'ufw' in c['cmd'] and ('stop' in c['cmd'] or 'disable' in c['cmd'])]) == 0


def test_a_failed_action_does_not_tell_auto_heal_anything():
    told = []
    so.ON_SERVICE_ACTION = lambda n, a: told.append((n, a))
    try:
        so.run = FakeServer().when('systemctl stop', 1, 'Failed to stop').run
        assert so.service(SSH_CFG, 'mariadb', 'stop')[0] == 1 and told == []
        so.run = FakeServer().run
        so.service(SSH_CFG, 'mariadb', 'stop')
        so.service(SSH_CFG, 'mariadb', 'start')
        so.service(SSH_CFG, 'mariadb', 'logs')
        assert told == [('mariadb', 'stop'), ('mariadb', 'start')]                                                 # only real changes are reported
    finally:
        so.ON_SERVICE_ACTION = heal.on_service_action


# ------------------------------------------------------------- containers
def test_server_info_and_reboot():
    payload = {'updates_count': 2, 'reboot_required': False}
    so.run = FakeServer().when('python3', 0, 'noise ' + json.dumps(payload) + ' tail').run
    assert so.info(SSH_CFG) == payload
    so.run = FakeServer().when('python3', 1, 'sudo: a password is required').run
    raises(so.info, SSH_CFG, text='password is required', kind=RuntimeError)
    so.run = FakeServer().when('python3', 0, 'no json').run
    raises(so.info, SSH_CFG, text='no json', kind=RuntimeError)
    so.run = FakeServer().when('python3', 1, '').run
    raises(so.info, SSH_CFG, text='could not read server info', kind=RuntimeError)
    srv = FakeServer()
    so.run = srv.run
    so.reboot(SSH_CFG)
    assert 'systemd-run --on-active=3 systemctl reboot' in srv.calls[0]['cmd']                                      # delayed, so the answer gets back before the connection drops
    assert 'dpkg' in so.UPGRADE_SH or 'apt-get' in so.UPGRADE_SH and 'reboot-required' in so.UPGRADE_SH


def test_container_actions():
    srv = FakeServer()
    so.run = srv.run
    for action, cmd in (('start', 'sudo docker start mssql'), ('stop', 'sudo docker stop -t 60 mssql'), ('restart', 'sudo docker restart -t 60 mssql'),
                        ('autostart-on', 'sudo docker update --restart=unless-stopped mssql'), ('autostart-off', 'sudo docker update --restart=no mssql'), ('logs', 'sudo docker logs --tail 200 mssql 2>&1')):
        so.container(SSH_CFG, 'mssql', action)
        assert srv.calls[-1]['cmd'] == cmd
    for bad in ('', 'a b', 'a;b', '-x', '$(id)', 'x' * 70, 'a\nb'):
        raises(so.container, SSH_CFG, bad, 'stop', text='bad container name')
    raises(so.container, SSH_CFG, 'mssql', 'rm', text='unknown action')
    raises(so.container, SSH_CFG, 'mssql', 'autostart-maybe', text='unknown action')
    assert len(srv.calls) == 6


# ----------------------------------------------- auto-heal respects the user
def status(**services):
    return {'sites': [], 'server': {'services': services}}


def tick(st, now, runner):
    return heal.tick({'mode': 'local'}, status=st, now=now, runner=runner, notify=lambda t: None)


def test_a_service_you_stopped_is_not_restarted_by_auto_heal():
    cmds = []
    runner = lambda cfg, cmd, timeout=0: cmds.append(cmd) or (0, 'active\n')  # noqa: E731
    tick(status(mariadb='active', docker='active'), 1000, runner)                                                  # heal learns they were running
    so.run = FakeServer().run
    so.service(SSH_CFG, 'mariadb', 'stop')                                                                         # you stop it to save memory
    assert 'svc:mariadb' in watch._load()['heal']['paused']
    assert tick(status(mariadb='inactive', docker='active'), 1100, runner) == [] and cmds == []                    # ... and it stays stopped
    tick(status(mariadb='inactive', docker='failed'), 1200, runner)                                                # docker dying by itself is still repaired
    assert len(cmds) == 1 and 'docker.service' in cmds[0]
    so.service(SSH_CFG, 'mariadb', 'start')
    assert watch._load()['heal']['paused'] == []
    assert len(tick(status(mariadb='inactive', docker='active'), 1300, runner)) == 1                               # started by you, then it died: heal is back on duty


def test_disabling_at_boot_also_counts_as_a_decision_and_enabling_undoes_it():
    so.run = FakeServer().run
    so.service(SSH_CFG, 'redis-server', 'disable')
    assert 'svc:redis-server' in watch._load()['heal']['paused']
    so.service(SSH_CFG, 'redis-server', 'enable')
    assert 'svc:redis-server' not in watch._load()['heal']['paused']
    so.service(SSH_CFG, 'php8.3-fpm', 'stop')
    so.service(SSH_CFG, 'php8.3-fpm', 'restart')
    assert watch._load()['heal']['paused'] == []                                                                   # a restart is a start


def test_php_fpm_and_the_new_services_are_healed_when_they_die_on_their_own():
    cmds = []
    runner = lambda cfg, cmd, timeout=0: cmds.append(cmd) or (0, 'active\n')  # noqa: E731
    tick(status(**{'php8.3-fpm': 'active', 'mssql-server': 'active', 'redis-server': 'active'}), 1000, runner)
    tick(status(**{'php8.3-fpm': 'failed', 'mssql-server': 'failed', 'redis-server': 'inactive'}), 1100, runner)
    assert sorted(c.split('start ')[1].split(' ')[0].strip("'") for c in cmds) == ['mssql-server.service', 'php8.3-fpm.service', 'redis-server.service']
    assert heal.on_service_action('ufw', 'stop') is None                                                           # whatever the name, it never raises


if __name__ == '__main__':
    run_tests(globals(), before=restore)
