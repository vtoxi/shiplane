"""SSH command building (key, password, port, local mode), friendly SSH errors, and the server/Cloudflare settings (no SSH needed).

    python tests/test_runner_config.py
"""
import json
import os
import subprocess
import sys
import tempfile

DATA = tempfile.mkdtemp()
os.environ['CODERUNNER_DATA'] = DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import config, runner  # noqa: E402

KEY = os.path.join(DATA, 'id_test')
open(KEY, 'w').write('key')
BASE = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'bob', 'key': KEY}
ORIGINAL = {(config, 'SERVER_FILE'): config.SERVER_FILE, (config, 'CF_FILE'): config.CF_FILE, (runner.subprocess, 'run'): subprocess.run, (runner.subprocess, 'Popen'): subprocess.Popen,
            (runner.os, 'name'): os.name, (runner.sys, 'executable'): sys.executable}


def restore():
    for (module, name), value in ORIGINAL.items():
        setattr(module, name, value)
    config.SERVER_FILE = os.path.join(DATA, 'server.json')
    config.CF_FILE = os.path.join(DATA, 'cloudflare.json')
    for f in (config.SERVER_FILE, config.CF_FILE):
        if os.path.exists(f):
            os.remove(f)
    for k in ('CLOUDFLARE_API_TOKEN',):
        os.environ.pop(k, None)
    for f in ('askpass.cmd', 'askpass.sh'):
        if os.path.exists(os.path.join(DATA, f)):
            os.remove(os.path.join(DATA, f))


def raises(fn, *args, text=''):
    try:
        fn(*args)
    except (ValueError, RuntimeError) as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


# ------------------------------------------------------------ settings validation
def test_check_server_defaults_and_cleaning():
    c = config.check_server(BASE)
    assert c['port'] == 22 and c['auth'] == 'key' and c['password'] == '' and c['domains'] == []
    c = config.check_server(dict(BASE, port='2222', domains=[' A.com ', '', 'b.org']))
    assert c['port'] == 2222 and c['domains'] == ['a.com', 'b.org']
    assert config.check_server({'mode': 'local', 'port': 'junk'})['port'] == 22              # not used in local mode


def test_check_server_rejects_bad_input():
    for bad, text in (({'mode': 'telnet'}, 'Mode must be'), ({'host': 'bad host'}, 'host looks wrong'), ({'host': ''}, 'host looks wrong'), ({'user': 'Bob'}, 'user looks wrong'),
                      ({'user': ''}, 'user looks wrong'), ({'key': ''}, 'key path'), ({'key': '-oProxyCommand=x'}, 'key path'), ({'port': '0'}, 'port'), ({'port': '70000'}, 'port'),
                      ({'port': 'abc'}, 'port'), ({'port': '-1'}, 'port'), ({'auth': 'password'}, 'Type the SSH password'), ({'domains': ['bad domain!']}, 'Bad domain')):
        raises(config.check_server, dict(BASE, **bad), text=text)


def test_load_and_save_server():
    assert config.load_server() == config.DEFAULT_SERVER                                      # no file yet
    open(config.SERVER_FILE, 'w').write('{broken')
    assert config.load_server() == config.DEFAULT_SERVER                                      # unreadable file falls back
    config.save_server(dict(BASE, auth='password', password='s3cret', key='ignored'))
    saved = json.load(open(config.SERVER_FILE))
    assert saved['password'] == 's3cret' and saved['key'] == '' and saved['port'] == 22
    if os.name != 'nt':
        assert oct(os.stat(config.SERVER_FILE).st_mode & 0o777) == '0o600'                    # owner-only: it can hold a password
    config.save_server(dict(BASE))                                                            # switching back to a key drops the stored password
    assert json.load(open(config.SERVER_FILE))['password'] == ''


def test_password_is_write_only():
    config.save_server(dict(BASE, auth='password', password='s3cret'))
    kept = config.resolve_server(dict(BASE, auth='password', password=''))
    assert kept['password'] == 's3cret'                                                       # blank field keeps the saved one
    raises(config.resolve_server, dict(BASE, auth='password', password='', host='other.example.com'), text='Type the SSH password')   # never reused for another host
    raises(config.resolve_server, dict(BASE, auth='password', password='', user='alice'), text='Type the SSH password')
    assert config.resolve_server(dict(BASE, auth='password', password='new'))['password'] == 'new'
    pub = config.public_server(config.load_server())
    assert 'password' not in pub and pub['hasPassword'] is True and config.public_server({'mode': 'ssh'})['hasPassword'] is False


# ------------------------------------------------------------ cloudflare settings
def test_cloudflare_settings():
    assert config.cloudflare() == {'api_token': '', 'account_id': '', 'tunnel_id': ''} and not config.cloudflare_ready()
    open(config.CF_FILE, 'w').write('{broken')
    assert not config.cloudflare_ready()
    json.dump({'api_token': ' tok ', 'account_id': 'a', 'tunnel_id': 't'}, open(config.CF_FILE, 'w'))
    assert config.cloudflare()['api_token'] == 'tok' and config.cloudflare_ready()
    os.environ['CLOUDFLARE_API_TOKEN'] = 'from-env'
    assert config.cloudflare()['api_token'] == 'from-env'
    json.dump({'api_token': '', 'account_id': 'a', 'tunnel_id': ''}, open(config.CF_FILE, 'w'))
    assert not config.cloudflare_ready()


def test_clean_hosts():
    assert config.clean_hosts('A.com, b.org\nc.net') == ['a.com', 'b.org', 'c.net']
    assert config.clean_hosts([' x.io ', '']) == ['x.io']
    for bad in ('', ' , ', 'nodots', 'bad host.com', ['a.com', '-x']):
        raises(config.clean_hosts, bad, text='')


# ------------------------------------------------------------ command line
def test_key_login_command():
    cmd = runner.command(config.check_server(BASE), 'echo hi')
    assert cmd[:3] == ['ssh', '-i', KEY] and 'BatchMode=yes' in cmd and cmd[cmd.index('-p') + 1] == '22' and cmd[-2:] == ['bob@srv.example.com', 'echo hi']


def test_password_login_command_and_custom_port():
    c = config.check_server(dict(BASE, auth='password', password='pw', port=2222))
    cmd = runner.command(c, 'ls')
    assert cmd[cmd.index('-p') + 1] == '2222' and '-i' not in cmd and 'BatchMode=yes' not in cmd and 'PubkeyAuthentication=no' in cmd and 'pw' not in ' '.join(cmd)
    assert runner.command({'mode': 'ssh', 'auth': 'key', 'key': KEY, 'user': 'u', 'host': 'h'}, 'x')[runner.command({'mode': 'ssh', 'auth': 'key', 'key': KEY, 'user': 'u', 'host': 'h'}, 'x').index('-p') + 1] == '22'


def test_local_mode_and_missing_key():
    assert runner.command({'mode': 'local'}, 'ls') == ['bash', '-c', 'ls'] and runner.is_local({'mode': 'local'}) and not runner.is_local(BASE)
    assert runner.environment({'mode': 'local', 'auth': 'password', 'password': 'x'}) is None and runner.environment(config.check_server(BASE)) is None
    assert runner.who(BASE) == 'bob' and runner.who({'mode': 'local'})
    raises(runner.command, dict(BASE, key=os.path.join(DATA, 'missing')), 'x', text='SSH key not found')


def test_askpass_helpers_on_both_platforms():
    c = config.check_server(dict(BASE, auth='password', password='pw'))
    path = runner.environment(c)['SSH_ASKPASS']
    assert os.path.basename(path) == ('askpass.cmd' if os.name == 'nt' else 'askpass.sh')
    first = open(path).read()
    assert runner.environment(c)['SSH_ASKPASS'] == path and open(path).read() == first          # created once, then reused
    assert 'pw' not in first                                                                    # the helper holds no secret
    for fake_os, expect in (('nt', 'CR_SSH_PASSWORD'), ('posix', '#!/bin/sh')):
        os.remove(path) if os.path.exists(path) else None
        runner.os.name = fake_os
        try:
            made = runner._askpass()
        finally:
            runner.os.name = ORIGINAL[(runner.os, 'name')]
        assert expect in open(made).read()
        os.remove(made)


def test_run_and_popen_pass_the_password_through_the_environment():
    c = config.check_server(dict(BASE, auth='password', password='pw', port=2200))
    seen = {}

    class Done:
        returncode, stdout, stderr = 3, b'out', b'err'
    runner.subprocess.run = lambda cmd, **kw: seen.update(run=(cmd, kw)) or Done()
    rc, text = runner.run(c, 'ls', inp=b'in', timeout=5)
    assert (rc, text) == (3, 'outerr') and seen['run'][1]['env']['CR_SSH_PASSWORD'] == 'pw' and seen['run'][1]['input'] == b'in' and seen['run'][1]['timeout'] == 5
    runner.subprocess.Popen = lambda cmd, **kw: seen.update(popen=(cmd, kw)) or 'proc'
    assert runner.popen(c, 'ls') == 'proc' and seen['popen'][1]['stdin'] == subprocess.PIPE and seen['popen'][1]['env']['CR_SSH_PASSWORD'] == 'pw'
    runner.popen(c, 'ls', stdin=False)
    assert seen['popen'][1]['stdin'] == subprocess.DEVNULL


def test_script_loader_normalizes_line_endings():
    text = runner.script('deploy.sh')
    assert '\r' not in text and 'SVC=' in text
    assert runner.q("it's") == "'it'\"'\"'s'"


# ------------------------------------------------------------ friendly SSH errors
def test_explain_ssh():
    c = {'host': 'old.example.com', 'port': 2222}
    t = runner.explain_ssh('ssh: connect to host old.example.com port 22: Connection timed out', c)
    assert 'old.example.com port 2222' in t and 'SSH port' in t and 'firewall' in t and t.endswith('(ssh: connect to host old.example.com port 22: Connection timed out)')
    assert 'Could not reach' in runner.explain_ssh('No route to host', c)
    assert 'Nothing is accepting SSH' in runner.explain_ssh('ssh: connect to host h port 22: Connection refused', c)
    assert 'host name could not be found' in runner.explain_ssh('ssh: Could not resolve hostname x: Name or service not known', c)
    assert 'rejected the login' in runner.explain_ssh('bob@h: Permission denied (publickey,password).', c)
    assert 'the server port 22' not in runner.explain_ssh('timed out') and 'the server' in runner.explain_ssh('timed out')
    assert runner.explain_ssh('something else entirely') == 'something else entirely' and runner.explain_ssh('') == '' and runner.explain_ssh(None) == ''


if __name__ == '__main__':
    run_tests(globals(), before=restore)
