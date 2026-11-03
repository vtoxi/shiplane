"""The GitHub deploy setup script: argument checks, secrets travel on stdin only, the private key never lingers (fake ssh/gh).

    python tests/test_setup_github_deploy.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
from fakes import run_tests  # noqa: E402
import setup_github_deploy as sg  # noqa: E402

ARGS = ['--host', 'srv.example.com', '--user', 'deploy', '--port', '2222', '--domain', 'shiplane.example.com', '--admin-email', 'me@example.com', '--repo', 'vtoxi/shiplane']


class Fake:
    def __init__(self, fail=None):
        self.calls, self.fail, self.key = [], fail or {}, None

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        name = cmd[0] + (' ' + cmd[1] if cmd[0] == 'gh' else '')
        if cmd[0] == 'ssh-keygen' and '-q' in cmd:
            self.key = cmd[cmd.index('-f') + 1]
            open(self.key, 'w').write('-----BEGIN OPENSSH PRIVATE KEY-----\nSECRETKEYBODY\n')
            open(self.key + '.pub', 'w').write('ssh-ed25519 AAAA')
        out = b'srv.example.com ssh-ed25519 AAAAHOSTKEY\n' if cmd[0] == 'ssh-keyscan' else b'256 SHA256:abc srv (ED25519)\n' if cmd[0] == 'ssh-keygen' else b''
        return types.SimpleNamespace(returncode=self.fail.get(name, 0), stdout=out, stderr=b'boom')

    def names(self):
        return [c[0][0] + (' ' + c[0][1] if c[0][0] == 'gh' else '') for c in self.calls]


def with_tools(fn):
    real = sg.shutil.which
    sg.shutil.which = lambda t: '/usr/bin/' + t
    try:
        return fn()
    finally:
        sg.shutil.which = real


def test_repo_detection_and_validation():
    assert sg.repo_from_remote('https://github.com/vtoxi/shiplane') == 'vtoxi/shiplane'
    assert sg.repo_from_remote('git@github.com:vtoxi/shiplane.git') == 'vtoxi/shiplane'
    assert sg.repo_from_remote('https://gitlab.com/a/b') == ''
    for bad in (['--host', 'bad host!'], ['--user', 'Root'], ['--port', '0'], ['--domain', 'nodot'], ['--admin-email', 'nope'], ['--repo', 'no-slash']):
        try:
            with_tools(lambda: sg.main(ARGS + bad, runner=Fake(), ask=lambda p: 'y'))
        except SystemExit as e:
            assert isinstance(e.code, str), bad
            continue
        raise AssertionError(bad)


def test_happy_path_sets_everything_with_values_on_stdin_and_removes_the_key():
    fake = Fake()
    with_tools(lambda: sg.main(ARGS + ['--run'], runner=fake, ask=lambda p: 'y'))
    sets = [(c[0], c[1]) for c in fake.calls if c[0][0] == 'gh' and c[0][2] == 'set']
    assert {c[0][3] for c in sets} == {'DEPLOY_HOST', 'DEPLOY_USER', 'DEPLOY_SSH_KEY', 'DEPLOY_KNOWN_HOSTS', 'DEPLOY_DOMAIN', 'ADMIN_EMAIL', 'DEPLOY_PORT'}
    by = {c[0][3]: c for c in sets}
    assert by['DEPLOY_SSH_KEY'][1]['input'].startswith(b'-----BEGIN') and by['DEPLOY_PORT'][1]['input'] == b'2222' and by['DEPLOY_KNOWN_HOSTS'][1]['input'].startswith(b'srv.example.com')
    assert all('SECRETKEYBODY' not in ' '.join(c[0]) and 'AAAAHOSTKEY' not in ' '.join(c[0]) for c in fake.calls)       # nothing secret on a command line
    assert all('--body' not in c[0] for c in sets)
    assert any(c[0][:2] == ['gh', 'api'] and 'environments/production' in c[0][-1] for c in fake.calls)
    assert fake.calls[-1][0][:3] == ['gh', 'workflow', 'run']
    copy = [c[0] for c in fake.calls if c[0][0] == 'ssh-copy-id'][0]
    assert 'StrictHostKeyChecking=yes' in copy and any(x.startswith('UserKnownHostsFile=') for x in copy)                # pinned to the confirmed host key
    names = fake.names()
    assert names.index('ssh-keyscan') < names.index('ssh-copy-id')                                                       # confirm first, then trust
    assert not os.path.exists(fake.key) and not os.path.exists(os.path.dirname(fake.key))                              # private key deleted


def test_keep_key_and_decline_fingerprint():
    fake = Fake()
    with_tools(lambda: sg.main(ARGS + ['--keep-key'], runner=fake, ask=lambda p: 'y'))
    assert os.path.exists(fake.key)
    os.remove(fake.key)
    fake = Fake()
    try:
        with_tools(lambda: sg.main(ARGS, runner=fake, ask=lambda p: 'n'))
    except SystemExit as e:
        assert 'Stopped' in str(e.code)
    else:
        raise AssertionError('continued without confirmation')
    assert 'gh secret' not in fake.names() and 'ssh-copy-id' not in fake.names() and not os.path.exists(fake.key)          # nothing copied or saved, key removed


def test_failures_stop_before_saving_anything():
    for tool, text in (('ssh-copy-id', 'Could not add the key'), ('ssh', 'passwordless sudo'), ('gh auth', 'gh auth login')):
        fake = Fake(fail={tool: 1})
        try:
            with_tools(lambda: sg.main(ARGS, runner=fake, ask=lambda p: 'y'))
        except SystemExit as e:
            assert text in str(e.code), (tool, e.code)
        else:
            raise AssertionError(tool)
        assert 'gh secret' not in fake.names(), tool


def test_missing_tools_and_gh_errors():
    real = sg.shutil.which
    sg.shutil.which = lambda t: None
    try:
        sg.main(ARGS, runner=Fake(), ask=lambda p: 'y')
    except SystemExit as e:
        assert 'not installed' in str(e.code)
    else:
        raise AssertionError
    finally:
        sg.shutil.which = real
    try:
        sg.gh_set('secret', 'X', 'v', 'a/b', runner=Fake(fail={'gh secret': 1}))
    except RuntimeError as e:
        assert 'boom' in str(e)
    else:
        raise AssertionError


def test_vars_only_creates_environment_and_variables_with_placeholders():
    fake = Fake()
    with_tools(lambda: sg.main(['--vars-only', '--repo', 'vtoxi/shiplane', '--port', '22', '--domain', 'shiplane.example.com'], runner=fake, ask=lambda p: 'y'))
    sets = {c[0][3]: c[1]['input'] for c in fake.calls if c[0][:3] == ['gh', 'variable', 'set']}
    assert sets == {'DEPLOY_DOMAIN': b'shiplane.example.com', 'ADMIN_EMAIL': b'CHANGE-ME', 'DEPLOY_PORT': b'22'}
    assert any(c[0][:2] == ['gh', 'api'] and c[0][-1].endswith('environments/production') for c in fake.calls)
    assert not any(c[0][0] in ('ssh', 'ssh-keygen', 'ssh-copy-id') for c in fake.calls) and not any(c[0][:3] == ['gh', 'secret', 'set'] for c in fake.calls)
    for bad in (['--domain', 'nodot'], ['--admin-email', 'nope'], ['--repo', 'bad']):
        try:
            with_tools(lambda: sg.main(['--vars-only', '--repo', 'a/b'] + bad, runner=Fake()))
        except SystemExit:
            continue
        raise AssertionError(bad)
    try:
        with_tools(lambda: sg.main(['--host', 'x.example.com'], runner=Fake()))
    except SystemExit as e:
        assert 'required' in str(e.code)
    else:
        raise AssertionError('missing arguments accepted')


if __name__ == '__main__':
    run_tests(dict(globals()))
