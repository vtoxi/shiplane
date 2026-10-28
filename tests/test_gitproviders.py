"""GitHub / GitLab sign-in: device login, token fallback, repository lists, auto-detection, one-click connect (fake services, no network).

    python tests/test_gitproviders.py
"""
import base64
import json
import os
import sys
import tempfile
import time
import urllib.error

os.environ['CODERUNNER_DATA'] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import gitproviders as gp, settings  # noqa: E402

REAL_FETCH = gp.fetch
CALLS = []
ROUTES = {}


def raises(fn, *args, text='', kind=ValueError):
    try:
        fn(*args)
    except kind as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


def b64(text):
    return {'encoding': 'base64', 'content': base64.b64encode(text.encode()).decode()}


def fake(method, url, headers=None, data=None, form=False):
    CALLS.append((method, url, data))
    for (m, part), answer in sorted(ROUTES.items(), key=lambda kv: -len(kv[0][1])):        # the most specific route wins
        if m == method and part in url:
            return answer(data) if callable(answer) else answer
    return 404, {'message': 'Not Found'}


def before():
    gp.fetch = fake
    CALLS.clear()
    ROUTES.clear()
    gp.SESSIONS.clear()
    os.environ.pop('SHIPLANE_GITHUB_CLIENT_ID', None)
    os.environ.pop('SHIPLANE_GITLAB_CLIENT_ID', None)
    path = os.path.join(os.environ['CODERUNNER_DATA'], 'settings.json')
    if os.path.exists(path):
        os.remove(path)


def signed_in(provider='github'):
    ROUTES[('GET', '/user')] = (200, {'login': 'octo', 'username': 'octo'})
    return gp.use_token(provider, 'ghp_' + 'a' * 30)['session']


def test_client_ids_come_from_settings_or_the_environment():
    assert [p['device'] for p in gp.providers()] == [False, False]
    gp.save_client_ids({'github': 'Iv1.abc123', 'gitlab': ''})
    assert [p['device'] for p in gp.providers()] == [True, False]
    os.environ['SHIPLANE_GITLAB_CLIENT_ID'] = 'gl-env-id-1'
    assert gp.client_id('gitlab') == 'gl-env-id-1' and gp.providers()[1]['device']
    raises(gp.save_client_ids, {'github': 'bad id!'}, text='client id looks wrong')
    raises(gp.client_id, 'bitbucket', text='Unknown provider')


def test_device_login_flow():
    raises(gp.device_start, 'github', text='not set up')
    gp.save_client_ids({'github': 'Iv1.abc123', 'gitlab': 'glid-12345'})
    ROUTES[('POST', 'login/device/code')] = (200, {'device_code': 'dc', 'user_code': 'ABCD-1234', 'verification_uri': 'https://github.com/login/device', 'interval': 5, 'expires_in': 900})
    started = gp.device_start('github')
    sid = started['session']
    assert started['user_code'] == 'ABCD-1234' and 'device_code' not in json.dumps(started) and gp.SESSIONS[sid]['device']['code'] == 'dc'
    ROUTES[('POST', 'login/oauth/access_token')] = (200, {'error': 'authorization_pending'})
    assert gp.device_poll(sid) == {'status': 'pending', 'slow': False}
    ROUTES[('POST', 'login/oauth/access_token')] = (200, {'error': 'slow_down'})
    assert gp.device_poll(sid)['slow'] is True
    ROUTES[('POST', 'login/oauth/access_token')] = (200, {'access_token': 'gho_secret'})
    ROUTES[('GET', '/user')] = (200, {'login': 'octo'})
    assert gp.device_poll(sid) == {'status': 'ok', 'user': 'octo'}
    assert gp.device_poll(sid)['status'] == 'ok'                                          # polling again is harmless
    assert 'gho_secret' not in json.dumps(started)
    ROUTES[('POST', 'login/oauth/access_token')] = (200, {'error': 'access_denied'})
    ROUTES[('POST', 'login/device/code')] = (200, {'device_code': 'dc2', 'user_code': 'X', 'verification_uri': 'u'})
    sid2 = gp.device_start('github')['session']
    assert gp.device_poll(sid2)['status'] == 'denied'
    raises(gp.device_poll, sid2, text='expired', kind=LookupError)                       # a denied sign-in is forgotten
    sid3 = gp.device_start('github')['session']
    ROUTES[('POST', 'login/oauth/access_token')] = (200, {'error': 'expired_token'})
    assert gp.device_poll(sid3)['status'] == 'expired'
    ROUTES[('POST', 'login/oauth/access_token')] = (200, None)
    assert gp.device_poll(gp.device_start('github')['session'])['status'] == 'pending'


def test_gitlab_device_login_uses_its_own_endpoints():
    gp.save_client_ids({'gitlab': 'glid-12345'})
    ROUTES[('POST', '/oauth/authorize_device')] = (200, {'device_code': 'g', 'user_code': 'G-1', 'verification_uri': 'https://gitlab.com/oauth/device'})
    sid = gp.device_start('gitlab')['session']
    ROUTES[('POST', '/oauth/token')] = (200, {'access_token': 'glpat-x'})
    ROUTES[('GET', '/user')] = (200, {'username': 'lab'})
    assert gp.device_poll(sid)['user'] == 'lab'


def test_device_login_errors():
    gp.save_client_ids({'github': 'Iv1.abc123'})
    ROUTES[('POST', 'login/device/code')] = (500, None)
    raises(gp.device_start, 'github', text='did not start', kind=RuntimeError)
    ROUTES[('POST', 'login/device/code')] = (200, {'error': 'x'})
    raises(gp.device_start, 'github', text='did not start', kind=RuntimeError)
    sid = signed_in()
    s = gp.SESSIONS[sid]
    s['token'] = ''
    raises(gp.device_poll, sid, text='no device login')


def test_token_fallback_validates_and_forgets_bad_tokens():
    raises(gp.use_token, 'github', 'short', text='does not look like')
    raises(gp.use_token, 'nope', 'x' * 20, text='Unknown provider')
    ROUTES[('GET', '/user')] = (401, {'message': 'Bad credentials'})
    raises(gp.use_token, 'github', 'x' * 20, text='did not accept', kind=PermissionError)
    assert not gp.SESSIONS                                                              # a rejected token is not kept
    ROUTES[('GET', '/user')] = (200, {'login': 'octo'})
    out = gp.use_token('github', 'x' * 20)
    assert out['user'] == 'octo' and 'x' * 20 not in json.dumps(out)
    ROUTES[('GET', '/user')] = (200, 'not a dict')
    raises(gp.use_token, 'github', 'x' * 20, text='did not accept', kind=PermissionError)


def test_sessions_expire_and_are_capped():
    sid = signed_in()
    gp.SESSIONS[sid]['at'] -= gp.TTL + 1
    raises(gp.repos, sid, text='expired', kind=LookupError)
    ids = [signed_in() for _ in range(gp.MAX_SESSIONS + 3)]
    assert len(gp.SESSIONS) <= gp.MAX_SESSIONS and ids[-1] in gp.SESSIONS and ids[0] not in gp.SESSIONS
    gp.forget(ids[-1])
    gp.forget('nothing')
    assert ids[-1] not in gp.SESSIONS
    gp.SESSIONS.update({'a%d' % i: {'provider': 'github', 'token': 't', 'user': '', 'at': time.time() + i, 'device': None} for i in range(8)})
    gp._purge(time.time())
    assert len(gp.SESSIONS) <= gp.MAX_SESSIONS


def test_repositories_and_branches_for_both_services():
    sid = signed_in()
    ROUTES[('GET', '/user/repos')] = (200, [{'full_name': 'acme/site', 'private': True, 'default_branch': 'main'}, {'full_name': 'bad name', 'private': False, 'default_branch': 'x'}, 'junk'])
    assert gp.repos(sid) == [{'repo': 'acme/site', 'private': True, 'branch': 'main'}]
    ROUTES[('GET', '/repos/acme/site/branches')] = (200, [{'name': 'main'}, {'name': '-bad'}, 'junk'])
    assert gp.branches(sid, 'acme/site') == ['main']
    raises(gp.branches, sid, '../etc/passwd', text='looks wrong')
    raises(gp.branches, sid, 'a b', text='looks wrong')
    ROUTES[('GET', '/user/repos')] = (401, {})
    raises(gp.repos, sid, text='refused', kind=PermissionError)
    gl = signed_in('gitlab')
    ROUTES[('GET', '/projects?membership')] = (200, [{'path_with_namespace': 'team/app', 'visibility': 'private', 'default_branch': None}, {'path_with_namespace': 'team/open', 'visibility': 'public', 'default_branch': 'dev'}])
    assert gp.repos(gl) == [{'repo': 'team/app', 'private': True, 'branch': 'main'}, {'repo': 'team/open', 'private': False, 'branch': 'dev'}]
    ROUTES[('GET', '/projects/team%2Fapp/repository/branches')] = (200, [{'name': 'main'}])
    assert gp.branches(gl, 'team/app') == ['main']
    assert gp.clone_address(sid, 'acme/site') == 'git@github.com:acme/site.git'
    assert gp.clone_address(gl, 'team/app') == 'git@gitlab.com:team/app.git'


def top(names, provider='github'):
    ROUTES[('GET', '/contents?ref=' if provider == 'github' else '/repository/tree')] = (200, [{'name': n} for n in names])


def test_detection_picks_a_sensible_mode():
    sid = signed_in()
    top(['README.md'])
    assert gp.detect(sid, 'acme/site', 'main')['type'] == 'static'
    top(['Dockerfile', 'package.json'])
    ROUTES[('GET', '/contents/Dockerfile')] = (200, b64('FROM node\nEXPOSE 4000\n'))
    d = gp.detect(sid, 'acme/site', 'main')
    assert d['type'] == 'docker' and d['cport'] == 4000
    ROUTES[('GET', '/contents/Dockerfile')] = (404, {})
    assert gp.detect(sid, 'acme/site', 'main')['cport'] == 8080
    top(['package.json'])
    ROUTES[('GET', '/contents/package.json')] = (200, b64(json.dumps({'scripts': {'build': 'vite build'}, 'devDependencies': {'vite': '5'}})))
    d = gp.detect(sid, 'acme/site', 'main')
    assert d['type'] == 'static' and d['build'] == 'npm' and d['output'] == 'dist'
    ROUTES[('GET', '/contents/package.json')] = (200, b64(json.dumps({'scripts': {'build': 'x'}, 'dependencies': {'react-scripts': '5'}})))
    assert gp.detect(sid, 'acme/site', 'main')['output'] == 'build'
    ROUTES[('GET', '/contents/package.json')] = (200, b64(json.dumps({'scripts': {'start': 'node s.js'}})))
    d = gp.detect(sid, 'acme/site', 'main')
    assert d['type'] == 'command' and d['start'] == 'npm start' and 'npm' in d['build_cmd']
    ROUTES[('GET', '/contents/package.json')] = (200, b64(json.dumps({'main': 'server/app.js'})))
    d = gp.detect(sid, 'acme/site', 'main')
    assert d['type'] == 'node' and d['start'] == 'server/app.js'
    ROUTES[('GET', '/contents/package.json')] = (200, b64('{broken'))
    assert gp.detect(sid, 'acme/site', 'main')['start'] == 'index.js'
    ROUTES[('GET', '/contents/package.json')] = (200, b64('[1]'))
    assert gp.detect(sid, 'acme/site', 'main')['type'] == 'node'
    top(['composer.json'])
    assert gp.detect(sid, 'acme/site', 'main')['type'] == 'php'
    top(['requirements.txt'])
    assert gp.detect(sid, 'acme/site', 'main')['type'] == 'command'
    raises(gp.detect, sid, 'acme/site', '-x', text='branch name')


def test_detection_on_gitlab_reads_files_through_the_repository_api():
    gl = signed_in('gitlab')
    top(['Dockerfile'], 'gitlab')
    ROUTES[('GET', '/repository/files/Dockerfile')] = (200, b64('EXPOSE 9000'))
    assert gp.detect(gl, 'team/app', 'main')['cport'] == 9000
    ROUTES[('GET', '/repository/files/Dockerfile')] = (404, {})
    assert gp.detect(gl, 'team/app', 'main')['cport'] == 8080


def test_one_click_connect_adds_a_read_only_key_and_a_webhook():
    sid = signed_in()
    ROUTES[('POST', '/repos/acme/site/keys')] = (201, {})
    ROUTES[('POST', '/repos/acme/site/hooks')] = (201, {})
    out = gp.link(sid, 'acme/site', 'ssh-ed25519 AAAA', 'https://portal.example.com/api/hooks/git?site=web', 'sec', 'web')
    assert out == {'key': True, 'hook': True, 'hook_note': ''} and sid not in gp.SESSIONS
    keys = [c for c in CALLS if c[1].endswith('/keys')][0][2]
    hook = [c for c in CALLS if c[1].endswith('/hooks')][0][2]
    assert keys['read_only'] is True and hook['config']['secret'] == 'sec' and hook['events'] == ['push']


def test_connect_without_a_public_address_skips_the_webhook_and_says_why():
    sid = signed_in()
    ROUTES[('POST', '/repos/acme/site/keys')] = (422, {'message': 'key is already in use'})
    out = gp.link(sid, 'acme/site', 'ssh-ed25519 AAAA', '', 'sec', 'web')
    assert out['key'] and not out['hook'] and 'public HTTPS' in out['hook_note']
    assert not [c for c in CALLS if c[1].endswith('/hooks')]


def test_connect_failures():
    sid = signed_in()
    ROUTES[('POST', '/repos/acme/site/keys')] = (500, {'message': 'boom'})
    raises(gp.link, sid, 'acme/site', 'k', '', 's', 'web', text='Could not add the deploy key', kind=RuntimeError)
    ROUTES[('POST', '/repos/acme/site/keys')] = (500, 'plain')
    raises(gp.link, sid, 'acme/site', 'k', '', 's', 'web', text='Could not add the deploy key', kind=RuntimeError)
    ROUTES[('POST', '/repos/acme/site/keys')] = (201, {})
    ROUTES[('POST', '/repos/acme/site/hooks')] = (422, {})
    out = gp.link(sid, 'acme/site', 'k', 'https://p.example.com/h', 's', 'web')
    assert out['key'] and not out['hook'] and '422' in out['hook_note']
    gl = signed_in('gitlab')
    ROUTES[('POST', '/projects/team%2Fapp/deploy_keys')] = (201, {})
    ROUTES[('POST', '/projects/team%2Fapp/hooks')] = (201, {})
    assert gp.link(gl, 'team/app', 'k', 'https://p.example.com/h', 's', 'web')['hook']
    assert [c for c in CALLS if c[1].endswith('/deploy_keys')][-1][2]['can_push'] is False


class Resp:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        return self.body


def test_the_real_http_helper_handles_every_outcome():
    real = gp.urllib.request.urlopen
    sent = []
    try:
        gp.urllib.request.urlopen = lambda req, timeout: sent.append(req) or Resp(200, b'{"a": 1}')
        assert REAL_FETCH('POST', 'https://x.example.com/a', {'X': 'y'}, {'k': 'v'}) == (200, {'a': 1})
        assert sent[0].data == b'{"k": "v"}' and sent[0].get_header('Content-type') == 'application/json'
        assert REAL_FETCH('POST', 'https://x.example.com/a', None, {'k': 'v'}, form=True)[0] == 200 and sent[1].data == b'k=v'
        gp.urllib.request.urlopen = lambda req, timeout: Resp(200, b'not json')
        assert REAL_FETCH('GET', 'https://x.example.com/a') == (200, None)
        gp.urllib.request.urlopen = lambda req, timeout: Resp(204, b'')
        assert REAL_FETCH('GET', 'https://x.example.com/a') == (204, None)

        def http_error(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, 'no', {}, __import__('io').BytesIO(b'{"message": "no"}'))
        gp.urllib.request.urlopen = http_error

        assert REAL_FETCH('GET', 'https://x.example.com/a') == (401, {'message': 'no'})

        def down(req, timeout):
            raise urllib.error.URLError('timed out')
        gp.urllib.request.urlopen = down
        raises(REAL_FETCH, 'GET', 'https://x.example.com/a', text='Could not reach x.example.com', kind=RuntimeError)
    finally:
        gp.urllib.request.urlopen = real


if __name__ == '__main__':
    run_tests(globals(), before=before)
