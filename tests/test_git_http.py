"""The Git webhook through the real HTTP server: signed pushes work without a login, forged ones do not, and nothing else lost its protection.

    python tests/test_git_http.py
"""
import hashlib
import hmac
import http.client
import json
import os
import socket
import sys
import tempfile
import threading

os.environ['CODERUNNER_DATA'] = tempfile.mkdtemp()
os.environ.pop('CODERUNNER_AUTH', None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import activity, gitdeploy, server, settings  # noqa: E402

SECRET = 'webhook-secret-for-tests-1234567890'
SHA = 'b' * 40
STARTED, LOGGED = [], []
REAL_TRIGGER = gitdeploy.trigger


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


SRV = server.make_server(free_port())
PORT = server.STATE['port']
threading.Thread(target=SRV.serve_forever, daemon=True).start()
COOKIE = 'cr=%s' % server.STATE['token']


def restore():
    settings.put('git', {'web': {'repo': 'https://github.com/a/b.git', 'branch': 'main', 'build': 'none', 'output': '', 'secret': SECRET}})
    gitdeploy._hits.clear()
    STARTED.clear()
    LOGGED.clear()
    gitdeploy.trigger = lambda cfg, name, user, source='manual': STARTED.append((name, user, source)) or {'job': 'job-1'}
    activity.log = lambda *a, **k: LOGGED.append(a)


def post(path, body=b'{}', headers=None, cookie=False, content_length=None):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    h = {'Host': '127.0.0.1:%d' % PORT, 'Content-Type': 'application/json'}
    if cookie:
        h['Cookie'] = COOKIE
    h.update(headers or {})
    c.putrequest('POST', path)
    for k, v in h.items():
        c.putheader(k, v)
    c.putheader('Content-Length', str(len(body) if content_length is None else content_length))
    c.endheaders(body if content_length is None else b'')
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, (json.loads(data) if data[:1] == b'{' else data)


def signed(payload, secret=SECRET, event='push'):
    raw = json.dumps(payload).encode()
    return raw, {'X-Hub-Signature-256': 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(), 'X-GitHub-Event': event}


PUSH = {'ref': 'refs/heads/main', 'after': SHA}


def test_a_signed_github_push_deploys_without_any_login():
    raw, headers = signed(PUSH)
    status, out = post('/api/hooks/git?site=web', raw, headers)                                                   # no cookie, no X-Requested-With: GitHub cannot send them
    assert status == 200 and out == {'job': 'job-1', 'ok': True, 'commit': 'bbbbbbbb'} and STARTED == [('web', 'git webhook', 'webhook github')]
    assert LOGGED and LOGGED[-1][0] == 'git-webhook' and LOGGED[-1][1] == 'web' and LOGGED[-1][2] == 'bbbbbbbb'
    assert SECRET not in json.dumps(out)


def test_gitlab_token_and_ignored_events():
    status, out = post('/api/hooks/git?site=web', json.dumps(PUSH).encode(), {'X-Gitlab-Token': SECRET, 'X-Gitlab-Event': 'Push Hook'})
    assert status == 200 and out['ok'] and STARTED[-1][2] == 'webhook gitlab'
    raw, headers = signed({'zen': 'hi'}, event='ping')
    assert post('/api/hooks/git?site=web', raw, headers) == (200, {'ok': True, 'ignored': 'ping'})
    raw, headers = signed(dict(PUSH, ref='refs/heads/feature'))
    assert post('/api/hooks/git?site=web', raw, headers) == (200, {'ok': True, 'ignored': 'not a push to main'})
    raw, headers = signed(dict(PUSH, ref='refs/tags/v1'))
    assert post('/api/hooks/git?site=web', raw, headers)[1]['ignored'] and len(STARTED) == 1
    assert not [l for l in LOGGED if l[0] == 'git-webhook' and l[2] == '']                                        # ignored events leave no trace in the activity log


def test_forged_or_unsigned_calls_are_refused_the_same_way():
    raw, good = signed(PUSH)
    cases = [(raw, {}), (raw, {'X-GitHub-Event': 'push'}), (raw, dict(good, **{'X-Hub-Signature-256': 'sha256=' + '0' * 64})), (raw + b' ', good),
             (signed(PUSH, secret='the-wrong-secret')[0], signed(PUSH, secret='the-wrong-secret')[1]), (raw, {'X-Gitlab-Token': 'nope', 'X-Gitlab-Event': 'Push Hook'})]
    for body, headers in cases:
        status, out = post('/api/hooks/git?site=web', body, headers)
        assert (status, out) == (403, {'error': 'Invalid signature'}), (status, out)
    assert post('/api/hooks/git?site=ghost', raw, good) == (403, {'error': 'Invalid signature'})                    # a site that does not exist answers the same
    assert post('/api/hooks/git', raw, good) == (403, {'error': 'Invalid signature'})
    assert STARTED == []                                                                                           # not one deploy was started


def test_guessing_the_secret_runs_into_the_rate_limit():
    raw, headers = signed(PUSH)
    headers = dict(headers, **{'X-Hub-Signature-256': 'sha256=' + 'a' * 64})
    codes = [post('/api/hooks/git?site=web', raw, headers)[0] for _ in range(25)]
    assert codes[0] == 403 and 429 in codes and codes.index(429) < 20                                              # the door closes quickly
    raw, good = signed(PUSH)
    assert post('/api/hooks/git?site=web', raw, good)[0] == 429                                                   # and a correct request from the same address waits too
    assert STARTED == []


def test_bad_bodies():
    assert post('/api/hooks/git?site=web', b'not json', {'X-GitHub-Event': 'push'})[0] == 400
    assert post('/api/hooks/git?site=web', b'x', {}, content_length=server.MAX_JSON + 1)[0] == 413
    assert STARTED == []


def test_nothing_else_lost_its_protection():
    for path in ('/api/care/fix', '/api/git/save', '/api/git/deploy', '/api/apps/install', '/api/server/service'):
        assert post(path, b'{}', cookie=True)[0] == 403, path                                                        # a logged-in browser still needs the custom header
        assert post(path, b'{}', {'X-Requested-With': 'coderunner'})[0] == 401, path                                 # and the header alone is not a login
    status, out = post('/api/git/deploy', json.dumps({'name': 'web'}).encode(), {'X-Requested-With': 'coderunner'}, cookie=True)
    assert status in (200, 404, 400) and not STARTED or STARTED[-1][2] == 'manual'
    assert server.ROUTES[('POST', '/api/hooks/git')]['public'] is True
    assert [k for k, r in server.ROUTES.items() if r['public'] and k[1].startswith('/api/hooks')] == [('POST', '/api/hooks/git')]    # the webhook is the only public write endpoint of its kind


def test_the_webhook_cannot_be_used_with_get():
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    c.request('GET', '/api/hooks/git?site=web', headers={'Host': '127.0.0.1:%d' % PORT})
    assert c.getresponse().status in (401, 404)
    c.close()


if __name__ == '__main__':
    try:
        run_tests(globals(), before=restore)
    finally:
        SRV.shutdown()
