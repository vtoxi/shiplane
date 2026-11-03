"""Moving the app to a new address: request checks, the Cloudflare order, and remote/app_address.sh run for real against temp files.

    python tests/test_app_address.py
"""
import os
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import appaddress, cloudflare, config  # noqa: E402
from coderunner.runner import script  # noqa: E402

CFG = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'deploy'}
SENT, MOVES = [], []
STATE = {}


def before():
    SENT.clear()
    MOVES.clear()
    STATE.update(installed=True, hosts='server.rankin.work', direct='no', apply_rc=0)

    def fake_run(cfg, cmd, inp=None, timeout=60):
        text = inp.decode()
        SENT.append(text)
        if 'MODE=show' in text:
            return 0, 'INSTALLED=%s\nHOSTS=%s\nDIRECT=%s\n' % ('yes' if STATE['installed'] else 'no', STATE['hosts'], STATE['direct'])
        return STATE['apply_rc'], 'Now served at x\n' if not STATE['apply_rc'] else 'Caddy rejected the change, nothing was changed.\n'
    appaddress.run = fake_run
    config.cloudflare_ready = lambda: True
    cloudflare.map_hosts = lambda hosts: MOVES.append(('map', hosts)) or ['added ' + hosts[0]]
    cloudflare.unmap_hosts = lambda hosts: MOVES.append(('unmap', hosts)) or ['removed ' + hosts[0]]


def raises(a, text, exc=ValueError):
    try:
        appaddress.change(CFG, a)
    except exc as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('did not raise')


def test_show_reads_the_state():
    assert appaddress.show(CFG) == {'installed': True, 'hosts': ['server.rankin.work'], 'direct': False}
    STATE.update(installed=False, hosts='')
    assert appaddress.show(CFG)['installed'] is False
    appaddress.run = lambda *a, **k: (1, 'ssh: boom')
    try:
        appaddress.show(CFG)
    except RuntimeError as e:
        assert 'boom' in str(e)
    else:
        raise AssertionError('did not raise')


def test_requests_are_checked_before_anything_runs():
    raises({'hostname': 'bad name', 'confirm': 'bad name'}, 'looks wrong')
    raises({'hostname': 'nodot', 'confirm': 'nodot'}, 'looks wrong')
    raises({'hostname': 'shiplane.uk', 'confirm': 'shiplane.u'}, 'Type the new address')
    raises({'hostname': 'server.rankin.work', 'confirm': 'server.rankin.work'}, 'already answers')
    STATE['installed'] = False
    raises({'hostname': 'shiplane.uk', 'confirm': 'shiplane.uk'}, 'not installed')
    assert not any('MODE=apply' in s for s in SENT) and not MOVES


def test_apply_sends_a_validated_hostname_and_moves_cloudflare_new_first():
    lines = appaddress.change(CFG, {'hostname': ' Shiplane.UK ', 'confirm': 'shiplane.uk', 'direct': True, 'cloudflare': True})
    assert 'NEW=shiplane.uk' in SENT[-1] and 'DIRECT=true' in SENT[-1] and 'MODE=apply' in SENT[-1]
    assert MOVES == [('unmap', ['server.rankin.work'])] and lines[-1] == 'removed server.rankin.work'      # direct https: no tunnel route for the new name
    MOVES.clear()
    appaddress.change(CFG, {'hostname': 'shiplane.uk', 'confirm': 'shiplane.uk', 'cloudflare': True})
    assert MOVES == [('map', ['shiplane.uk']), ('unmap', ['server.rankin.work'])] and 'DIRECT=false' in SENT[-1]
    MOVES.clear()
    appaddress.change(CFG, {'hostname': 'shiplane.uk', 'confirm': 'shiplane.uk'})
    assert not MOVES                                                                                      # Cloudflare only when asked
    config.cloudflare_ready = lambda: False
    appaddress.change(CFG, {'hostname': 'shiplane.uk', 'confirm': 'shiplane.uk', 'cloudflare': True})
    assert not MOVES                                                                                      # ... and only when connected


def test_a_cloudflare_failure_does_not_undo_the_move_and_says_what_is_left():
    def bad(hosts):
        raise RuntimeError('token rejected')
    cloudflare.map_hosts = bad
    lines = appaddress.change(CFG, {'hostname': 'shiplane.uk', 'confirm': 'shiplane.uk', 'cloudflare': True})
    assert lines[0] == 'Now served at x' and 'Cloudflare step FAILED: token rejected' in lines[-1]


def test_a_refused_apply_raises_with_the_reason():
    STATE['apply_rc'] = 1
    raises({'hostname': 'shiplane.uk', 'confirm': 'shiplane.uk'}, 'Caddy rejected', RuntimeError)
    assert not MOVES                                                                                      # Cloudflare is not touched after a failure


def _run_script(mode, new, direct, validate_ok=True, site_head='http://server.rankin.work {', other=None):
    tmp = tempfile.mkdtemp().replace('\\', '/')
    caddy = tmp + '/caddy'
    os.makedirs(caddy + '/sites')
    open(caddy + '/Caddyfile', 'w').write('import sites/*.caddy\n')
    open(caddy + '/sites/coderunner.caddy', 'w', newline='\n').write(site_head + '\n\tencode gzip\n\treverse_proxy 127.0.0.1:8765\n}\n')
    if other:
        open(caddy + '/sites/other.caddy', 'w').write(other)
    env_file = tmp + '/coderunner.env'
    open(env_file, 'w', newline='\n').write('CODERUNNER_AUTH=login\nCODERUNNER_ALLOW_HOSTS=server.rankin.work\nCODERUNNER_DATA=/d\n')
    bin_dir = tmp + '/bin'
    os.makedirs(bin_dir)
    for name, body in (('sudo', 'exec "$@"'), ('systemctl', 'echo "systemctl $*" >> %s/log' % tmp), ('systemd-run', 'echo "systemd-run $*" >> %s/log' % tmp),
                       ('caddy', 'exit 0' if validate_ok else 'echo "ambiguous site definition" >&2; exit 1')):
        open(bin_dir + '/' + name, 'w', newline='\n').write('#!/bin/bash\n%s\n' % body)
        os.chmod(bin_dir + '/' + name, 0o755)
    text = (script('app_address.sh').replace('__MODE__', "'%s'" % mode).replace('__NEW__', "'%s'" % new).replace('__DIRECT__', direct)
            .replace('/etc/coderunner.env', env_file).replace('/etc/caddy', caddy))
    r = subprocess.run(['bash', '-c', text], env=dict(os.environ, PATH=bin_dir + os.pathsep + os.environ['PATH']), capture_output=True, text=True)
    read = lambda p: open(p).read() if os.path.exists(p) else ''
    return r, read(env_file), read(caddy + '/sites/coderunner.caddy'), read(tmp + '/log')


def test_script_syntax_and_show():
    assert subprocess.run(['bash', '-n'], input=script('app_address.sh').encode(), capture_output=True).returncode == 0
    r, *_ = _run_script('show', '-', 'false')
    assert r.returncode == 0 and 'INSTALLED=yes' in r.stdout and 'HOSTS=server.rankin.work' in r.stdout and 'DIRECT=no' in r.stdout, r.stdout + r.stderr
    r, *_ = _run_script('show', '-', 'false', site_head='http://a.example, https://a.example {')
    assert 'DIRECT=yes' in r.stdout


def test_script_apply_rewrites_only_the_address_and_schedules_the_restart():
    r, env, site, log = _run_script('apply', 'shiplane.uk', 'true')
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'CODERUNNER_ALLOW_HOSTS=shiplane.uk\n' in env and 'rankin' not in env and 'CODERUNNER_DATA=/d' in env
    assert site.startswith('http://shiplane.uk, https://shiplane.uk {\n') and 'reverse_proxy 127.0.0.1:8765' in site and 'encode gzip' in site
    assert 'systemctl reload caddy' in log and 'systemd-run' in log and 'restart coderunner' in log
    r, env, site, log = _run_script('apply', 'shiplane.uk', 'false')
    assert site.startswith('http://shiplane.uk {\n')


def test_script_apply_puts_everything_back_when_caddy_refuses():
    r, env, site, log = _run_script('apply', 'shiplane.uk', 'true', validate_ok=False)
    assert r.returncode == 1 and 'Caddy rejected' in r.stdout and 'ambiguous' in r.stdout
    assert 'ALLOW_HOSTS=server.rankin.work' in env and site.startswith('http://server.rankin.work {') and not log        # nothing reloaded or restarted


def test_script_refuses_an_address_another_site_uses_but_not_a_longer_name():
    r, env, site, log = _run_script('apply', 'shiplane.uk', 'false', other='http://shiplane.uk {\n}\n')
    assert r.returncode == 1 and 'already used in' in r.stdout and 'other.caddy' in r.stdout and 'server.rankin.work' in env
    r, *_ = _run_script('apply', 'shiplane.uk', 'false', other='http://app.shiplane.uk.au {\n}\nhttp://xshiplane.uk {\n}\n')
    assert r.returncode == 0, r.stdout                                                                       # only the whole name counts


def test_script_refuses_a_site_file_it_cannot_understand():
    r, *_ = _run_script('apply', 'shiplane.uk', 'false', site_head='# custom')
    assert r.returncode == 1 and 'start with a site address' in r.stdout


if __name__ == '__main__':
    run_tests(globals(), before=before)
