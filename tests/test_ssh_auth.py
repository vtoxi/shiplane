"""SSH login by key or password: command line, environment and write-only password (no SSH needed).

    python tests/test_ssh_auth.py
"""
import json
import os
import subprocess
import sys
import tempfile

os.environ['CODERUNNER_DATA'] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from coderunner import config, runner  # noqa: E402

PW = 'p@ss w0rd;&$`"\'%^'
CFG = config.check_server({'mode': 'ssh', 'auth': 'password', 'host': 'h.example.com', 'user': 'bob', 'password': PW})


def test_password_never_on_command_line():
    cmd = runner.command(CFG, 'echo hi')
    assert PW not in ' '.join(cmd) and '-i' not in cmd and 'BatchMode=yes' not in cmd
    assert 'PubkeyAuthentication=no' in cmd and cmd[-1] == 'echo hi'
    env = runner.environment(CFG)
    assert env['CR_SSH_PASSWORD'] == PW and env['SSH_ASKPASS_REQUIRE'] == 'force' and os.path.isfile(env['SSH_ASKPASS'])


def test_key_mode_unchanged():
    key = os.path.join(os.environ['CODERUNNER_DATA'], 'k')
    open(key, 'w').write('x')
    c = config.check_server({'mode': 'ssh', 'host': 'h.example.com', 'user': 'bob', 'key': key})
    cmd = runner.command(c, 'echo hi')
    assert cmd[:2] == ['ssh', '-i'] and 'BatchMode=yes' in cmd and runner.environment(c) is None and c['password'] == ''


def test_askpass_prints_exact_password():
    env = runner.environment(CFG)
    out = subprocess.run(env['SSH_ASKPASS'], capture_output=True, text=True, env=env, shell=os.name == 'nt').stdout
    assert out.rstrip('\r\n') == PW, repr(out)


def test_validation_and_write_only_password():
    for bad in ({'auth': 'password', 'password': ''}, {'auth': 'password', 'password': 'x', 'user': 'Bad User'}):
        try:
            config.check_server(dict({'mode': 'ssh', 'host': 'h.example.com', 'user': 'bob'}, **bad))
        except ValueError:
            continue
        raise AssertionError(bad)
    config.SERVER_FILE = os.path.join(os.environ['CODERUNNER_DATA'], 'server.json')
    config.save_server(dict(CFG, domains=['a.com']))
    assert json.load(open(config.SERVER_FILE))['password'] == PW
    config.save_server({'mode': 'ssh', 'auth': 'password', 'host': 'h.example.com', 'user': 'bob', 'password': ''})   # blank keeps the saved one
    assert json.load(open(config.SERVER_FILE))['password'] == PW
    pub = config.public_server(config.load_server())
    assert 'password' not in pub and pub['hasPassword'] is True


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            fn()
            print('PASS', name)
    print('\nALL PASSED')
