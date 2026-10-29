"""OpenID Connect sign-in against a mock identity provider (real HTTP on both sides, no internet).

    python tests/test_sso.py
"""
import base64
import hashlib
import http.client
import http.server
import json
import os
import socket
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_route_sweep as sweep  # noqa: E402  (temporary data folder + fakes)
from fakes import run_tests  # noqa: E402
from coderunner import auth, server, sso  # noqa: E402

CLIENT, SECRET = 'shiplane-client', 'topsecret-client-secret'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


IDP_PORT = free_port()
ISSUER = 'http://127.0.0.1:%d' % IDP_PORT
GRANTS = {}       # code -> {challenge, nonce, email, verified, redirect, id_claims?}
SEEN = {'userinfo': 0}


def b64(d):
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b'=').decode()


class IdP(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith('/.well-known/openid-configuration'):
            return self.send({'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/authorize', 'token_endpoint': ISSUER + '/token', 'userinfo_endpoint': ISSUER + '/userinfo'})
        if self.path == '/userinfo':
            SEEN['userinfo'] += 1
            if self.headers.get('Authorization') != 'Bearer at-1':
                return self.send({'error': 'bad token'}, 401)
            return self.send(GRANTS['_userinfo'])
        self.send({}, 404)

    def do_POST(self):
        form = urllib.parse.parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
        f = lambda k: form.get(k, [''])[0]
        g = GRANTS.pop(f('code'), None)
        if not g or f('client_secret') != SECRET or f('client_id') != CLIENT or f('redirect_uri') != g['redirect']:
            return self.send({'error': 'invalid_grant'}, 400)
        if b64_sha(f('code_verifier')) != g['challenge']:
            return self.send({'error': 'pkce failed'}, 400)
        out = {'access_token': 'at-1'}
        if 'claims' in g:
            out['id_token'] = 'h.' + b64(g['claims']) + '.s'
        self.send(out)


def b64_sha(v):
    return base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b'=').decode()


IDP = http.server.ThreadingHTTPServer(('127.0.0.1', IDP_PORT), IdP)
threading.Thread(target=IDP.serve_forever, daemon=True).start()

sweep.install_fakes()
import urllib.request  # noqa: E402
urllib.request.urlopen = urllib.request.build_opener().open          # the sweep disabled the network; the mock IdP is on localhost
server.STATE['auth'] = 'login'
auth.init('root@example.com')
auth.create_user('alice@example.com', 'admin')
PORT = free_port()
SRV = server.make_server(PORT)
PORT = server.STATE['port']
threading.Thread(target=SRV.serve_forever, daemon=True).start()


def call(path, headers=None):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    c.request('GET', path, headers=dict({'Host': '127.0.0.1:%d' % PORT}, **(headers or {})))
    r = c.getresponse()
    out = r.status, dict((k.lower(), v) for k, v in r.getheaders()), r.read().decode()
    c.close()
    return out


def configure(**over):
    sso._disco.clear()
    sso.PENDING.clear()
    sso.save(dict({'enabled': True, 'issuer': ISSUER, 'client_id': CLIENT, 'client_secret': SECRET, 'label': 'Acme SSO'}, **over))


def begin():
    s, h, _ = call('/api/auth/sso/start')
    assert s == 302, s
    q = urllib.parse.parse_qs(urllib.parse.urlparse(h['location']).query)
    return {k: v[0] for k, v in q.items()}


def grant(q, email='alice@example.com', verified=True, with_claims=True, **claim_over):
    code = 'code-' + q['state'][:6]
    g = {'challenge': q['code_challenge'], 'redirect': q['redirect_uri']}
    if with_claims:
        g['claims'] = dict({'iss': ISSUER, 'aud': CLIENT, 'nonce': q['nonce'], 'exp': time.time() + 300, 'sub': 'u1', 'email': email, 'email_verified': verified}, **claim_over)
    else:
        GRANTS['_userinfo'] = {'sub': 'u1', 'email': email, 'email_verified': verified}
    GRANTS[code] = g
    return code


def test_the_login_page_offers_sso_only_when_configured():
    sso.settings.put('sso', {})
    assert json.loads(call('/api/auth/me')[2])['sso'] is None
    assert call('/api/auth/sso/start')[0] == 400
    configure()
    assert json.loads(call('/api/auth/me')[2])['sso'] == {'label': 'Acme SSO'}


def test_full_sign_in_creates_a_session_for_an_existing_account():
    configure()
    q = begin()
    assert q['client_id'] == CLIENT and q['code_challenge_method'] == 'S256' and q['scope'] == 'openid email' and q['redirect_uri'].endswith('/api/auth/sso/callback')
    s, h, _ = call('/api/auth/sso/callback?code=%s&state=%s' % (grant(q), q['state']))
    assert s == 302 and h['location'] == '/' and 'cr_session=' in h['set-cookie'] and 'HttpOnly' in h['set-cookie']
    token = h['set-cookie'].split('cr_session=')[1].split(';')[0]
    assert json.loads(call('/api/auth/me', {'Cookie': 'cr_session=' + token})[2])['user']['email'] == 'alice@example.com'


def test_state_is_single_use_and_unknown_states_fail():
    configure()
    q = begin()
    code = grant(q)
    assert call('/api/auth/sso/callback?code=%s&state=%s' % (code, q['state']))[0] == 302
    assert call('/api/auth/sso/callback?code=%s&state=%s' % (code, q['state']))[0] == 400            # replay
    assert call('/api/auth/sso/callback?code=x&state=forged')[0] == 400


def test_unknown_unverified_and_disabled_accounts_are_refused():
    configure()
    for kw, text in (({'email': 'stranger@example.com'}, 'no active account'), ({'verified': False}, 'not verified')):
        q = begin()
        s, h, body = call('/api/auth/sso/callback?code=%s&state=%s' % (grant(q, **kw), q['state']))
        assert s == 400 and text in body and 'set-cookie' not in h, (kw, body)
    user = [u for u in auth.list_users() if u['email'] == 'alice@example.com'][0]
    auth.update_user(user['id'], 0, status='disabled')
    q = begin()
    assert call('/api/auth/sso/callback?code=%s&state=%s' % (grant(q), q['state']))[0] == 400
    auth.update_user(user['id'], 0, status='active')
    assert not [u for u in auth.list_users() if u['email'] == 'stranger@example.com']                  # SSO never creates accounts


def test_tampered_id_tokens_are_refused():
    configure()
    for over in ({'nonce': 'other'}, {'aud': 'someone-else'}, {'iss': 'http://evil.example.com'}, {'exp': time.time() - 3600}):
        q = begin()
        s, _, body = call('/api/auth/sso/callback?code=%s&state=%s' % (grant(q, **over), q['state']))
        assert s == 400 and 'did not match' in body, (over, body)


def test_userinfo_is_used_when_there_is_no_id_token():
    configure()
    q = begin()
    s, h, _ = call('/api/auth/sso/callback?code=%s&state=%s' % (grant(q, with_claims=False), q['state']))
    assert s == 302 and 'cr_session=' in h['set-cookie'] and SEEN['userinfo'] >= 1


def test_wrong_client_secret_or_pkce_fails_at_the_provider():
    configure()
    sso.settings.put('sso', dict(sso.get(), client_secret='wrong-secret-value'))
    q = begin()
    s, _, body = call('/api/auth/sso/callback?code=%s&state=%s' % (grant(q), q['state']))
    assert s == 400 and 'did not answer correctly' in body


def test_settings_validation_and_secret_handling():
    configure()
    assert 'client_secret' not in sso.public() and sso.public()['has_secret'] is True
    sso.save({'enabled': True, 'issuer': ISSUER, 'client_id': CLIENT, 'client_secret': '', 'label': 'Acme'})         # blank secret keeps the saved one
    assert sso.get()['client_secret'] == SECRET
    for bad in ({'issuer': 'ftp://x'}, {'issuer': 'http://example.com'}, {'issuer': 'https://u:p@x.com'}, {'label': '<script>'}, {'client_id': ''}, {'redirect_base': 'javascript:1'}):
        try:
            sso.save(dict({'enabled': True, 'issuer': ISSUER, 'client_id': CLIENT, 'client_secret': '', 'label': 'ok'}, **bad))
        except ValueError:
            continue
        raise AssertionError(bad)


def test_too_many_pending_sign_ins_and_token_mode():
    configure()
    sso.PENDING.update({'s%d' % i: {'expires': time.time() + 100} for i in range(sso.MAX_PENDING)})
    try:
        sso.start('http://x/cb')
    except PermissionError:
        pass
    else:
        raise AssertionError('no cap')
    sso.PENDING.clear()
    server.STATE['auth'] = 'token'
    try:
        assert call('/api/auth/sso/start')[0] == 400
    finally:
        server.STATE['auth'] = 'login'


if __name__ == '__main__':
    run_tests(dict(globals()))
