"""The real HTTP server in token mode (SSH, network and subprocesses faked): who gets in, what is refused, and the response headers.

    python tests/test_http_security.py
"""
import http.client
import io
import json
import os
import socket
import sys
import threading
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_route_sweep as sweep  # noqa: E402  (sets the temporary data folder and provides the fakes)
from fakes import run_tests  # noqa: E402
from coderunner import apikeys, server  # noqa: E402

os.environ.pop('CODERUNNER_AUTH', None)
sweep.install_fakes()


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


SRV = server.make_server(free_port())
PORT = server.STATE['port']
threading.Thread(target=SRV.serve_forever, daemon=True).start()
TOKEN = server.STATE['token']


def req(method, path, body=None, headers=None, host=None, cookie=True, raw=None):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    h = {'Host': host or '127.0.0.1:%d' % PORT}
    if cookie:
        h['Cookie'] = 'cr=%s' % TOKEN
    h.update(headers or {})
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if data is not None:
        h.setdefault('Content-Length', str(len(data)))
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.status, dict((k.lower(), v) for k, v in r.getheaders()), r.read()
    c.close()
    return out


XRW = {'X-Requested-With': 'coderunner'}


def test_host_header_must_be_allowed():
    assert req('GET', '/api/status', host='evil.example.com')[0] == 403
    assert req('POST', '/api/status', {}, XRW, host='evil.example.com')[0] == 403
    assert req('GET', '/api/auth/me', host='localhost:%d' % PORT)[0] == 200


def test_no_cookie_means_no_access():
    for path in ('/', '/api/status', '/static/js/api.js', '/api/logs/stream', '/api/databases/download'):
        assert req('GET', path, cookie=False)[0] == 401, path
    assert req('GET', '/', cookie=False)[0] == 401
    assert req('GET', '/api/status', headers={'Cookie': 'cr=wrong'}, cookie=False)[0] == 401
    assert req('GET', '/api/status', headers={'Cookie': '\x00broken;;='}, cookie=False)[0] in (401, 400)


def test_launch_link_sets_the_cookie_only_for_the_right_token():
    s, h, _ = req('GET', '/?t=' + TOKEN, cookie=False)
    assert s == 302 and 'HttpOnly' in h['set-cookie'] and 'SameSite=Lax' in h['set-cookie'] and h['location'] == '/'
    assert req('GET', '/?t=nope', cookie=False)[0] == 401
    assert req('GET', '/')[0] == 200


def test_writes_need_the_custom_header_except_the_webhook():
    assert req('POST', '/api/cron/delete', {})[0] == 403                       # cookie alone (a cross-site form post) is refused
    assert req('POST', '/api/cron/delete', {}, XRW)[0] in (200, 400)           # past the header check: the handler decides
    s, _, body = req('POST', '/api/hooks/git?site=demo', {})                   # reaches the handler, which refuses the missing signature
    assert s in (400, 403, 404, 429), s
    assert s != 403 or b'not authorised' not in body


def test_api_keys():
    token, _ = apikeys.create('ci')
    assert req('GET', '/api/session', headers={'Authorization': 'Bearer ' + token}, cookie=False)[0] == 200
    assert req('GET', '/api/session', headers={'Authorization': 'Bearer crk_nope'}, cookie=False)[0] == 401
    assert req('POST', '/api/cron/delete', {}, {'Authorization': 'Bearer ' + token}, cookie=False)[0] in (200, 400)   # API clients need no browser header


def test_bad_bodies_and_unknown_routes():
    assert req('POST', '/api/cron/delete', headers=XRW, raw=b'{not json')[0] == 400
    assert req('POST', '/api/cron/delete', headers=dict(XRW, **{'Content-Length': str(server.MAX_JSON + 1)}), raw=b'{}')[0] == 413
    assert req('GET', '/api/nothing')[0] == 404
    assert req('GET', '/nothing')[0] == 404
    assert req('POST', '/api/nothing', {}, XRW)[0] == 404


def test_wrong_value_types_are_a_400_not_a_crash():
    s, _, body = req('POST', '/api/site/run', {'name': 5, 'command': 5}, XRW)
    assert s == 400 and b'wrong type' in body, (s, body)


def test_static_files_cannot_escape_the_web_folder():
    for path in ('/static/../server.json', '/static/..%2f..%2fCLAUDE.md', '/static/../../etc/passwd', '/static//etc/passwd'):
        assert req('GET', path)[0] in (404, 400), path
    assert req('GET', '/static/js/api.js')[0] == 200


def test_security_headers_on_every_answer():
    for path in ('/api/auth/me', '/nothing', '/'):
        h = req('GET', path)[1]
        assert h['x-content-type-options'] == 'nosniff' and h['x-frame-options'] == 'DENY' and 'default-src' in h['content-security-policy'] and h['cache-control'] in ('no-store', 'no-cache'), path


def test_head_has_no_body():
    s, h, body = req('HEAD', '/api/auth/me')
    assert s == 200 and body == b''


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for n, d in files.items():
            z.writestr(n, d)
    return buf.getvalue()


def test_upload_needs_a_session_and_refuses_bad_zips():
    assert req('POST', '/api/upload', headers=XRW, raw=make_zip({'a': 'b'}), cookie=False)[0] == 401
    assert req('POST', '/api/upload', headers=XRW, raw=b'not a zip')[0] == 400
    assert req('POST', '/api/upload', headers=XRW, raw=make_zip({'../evil': 'x'}))[0] == 400
    s, _, body = req('POST', '/api/upload', headers=XRW, raw=make_zip({'index.html': 'hi'}))
    assert s == 200 and os.path.isfile(os.path.join(json.loads(body)['path'], 'index.html'))


if __name__ == '__main__':
    run_tests(dict(globals()))
