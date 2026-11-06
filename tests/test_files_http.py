"""The Files routes through the real HTTP server: who may use them, what is audited, deletes need confirmation (the script runs for real on a temp tree).

    python tests/test_files_http.py
"""
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_route_sweep as sweep  # noqa: E402  (temporary data folder + fakes)
REAL_POPEN = subprocess.Popen
from fakes import run_tests  # noqa: E402
from coderunner import apikeys, auth, files, server  # noqa: E402

sweep.install_fakes()
TREE = tempfile.mkdtemp()
WEB, ETC = os.path.join(TREE, 'web'), os.path.join(TREE, 'etc')
os.makedirs(WEB)
os.makedirs(ETC)
files.ADMIN_ROOTS, files.SUPER_ROOTS, files.READ_ONLY = (WEB,), (WEB, ETC), ()


def _real_script_run(cfg, cmd, inp=None, timeout=60):
    p = REAL_POPEN([sys.executable, '-'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, _ = p.communicate(inp, timeout=timeout)
    return p.returncode, out.decode()


files.run = _real_script_run
AUDIT = []
server.audit = lambda kind, target='', detail='', ok=True: AUDIT.append((kind, target, detail))
server.STATE['auth'] = 'login'
auth.init('root@example.com')
with socket.socket() as _s:
    _s.bind(('127.0.0.1', 0))
    PORT = _s.getsockname()[1]
SRV = server.make_server(PORT)
PORT = server.STATE['port']
threading.Thread(target=SRV.serve_forever, daemon=True).start()
XRW = {'X-Requested-With': 'coderunner'}


def session(email, role, sites=None):
    user, _ = auth.create_user(email, role)
    if sites is not None:
        auth.update_user(user['id'], 0, sites=sites)
    with auth._db() as c:
        return auth._session(c, auth._user(c, user['id']), '127.0.0.1')


def req(token, method, path, body=None, key=None):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=20)
    h = dict(XRW, Host='127.0.0.1:%d' % PORT)
    h['Authorization' if key else 'Cookie'] = ('Bearer ' + key) if key else 'cr_session=' + token
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, (json.loads(data) if data[:1] == b'{' else data)


ADMIN, SUPER = session('a@example.com', 'admin'), session('s@example.com', 'super_admin')


def test_create_edit_preview_rename_delete_through_the_api():
    AUDIT.clear()
    p = WEB + '/site/index.html'
    assert req(ADMIN, 'POST', '/api/files/mkdir', {'path': WEB + '/site'})[0] == 200
    assert req(ADMIN, 'POST', '/api/files/write', {'path': p, 'content': '<h1>one</h1>', 'create': True})[0] == 200
    s, r = req(ADMIN, 'GET', '/api/files/read?path=' + p)
    assert s == 200 and r['kind'] == 'text' and r['writable']
    s, w = req(ADMIN, 'POST', '/api/files/write', {'path': p, 'content': '<h1>two</h1>', 'expect_mtime': r['mtime']})
    assert s == 200 and open(p).read() == '<h1>two</h1>'
    s, c = req(ADMIN, 'POST', '/api/files/write', {'path': p, 'content': 'stale', 'expect_mtime': r['mtime'] - 50})
    assert s == 200 and c['conflict'] and open(p).read() == '<h1>two</h1>'                                                  # conflict is reported, nothing is overwritten
    assert [e['name'] for e in req(ADMIN, 'GET', '/api/files/list?path=' + WEB + '/site')[1]['entries']] == ['index.html']
    assert req(ADMIN, 'POST', '/api/files/rename', {'path': p, 'name': 'home.html'})[0] == 200 and os.path.exists(WEB + '/site/home.html')
    assert req(ADMIN, 'POST', '/api/files/delete', {'path': WEB + '/site/home.html'})[0] == 400                               # no confirmation
    assert req(ADMIN, 'POST', '/api/files/delete', {'path': WEB + '/site/home.html', 'confirm': True})[0] == 200 and not os.path.exists(WEB + '/site/home.html')
    kinds = [a[0] for a in AUDIT]
    assert kinds == ['folder-create', 'file-create', 'file-write', 'file-rename', 'file-delete'] and all('<h1>' not in ' '.join(a) for a in AUDIT)   # audited, contents never logged


def test_who_may_use_the_files_page():
    open(ETC + '/app.conf', 'w').write('k=v\n')
    assert req(ADMIN, 'GET', '/api/files/read?path=' + ETC + '/app.conf')[0] == 400                                         # admins: web and home only
    assert req(SUPER, 'GET', '/api/files/read?path=' + ETC + '/app.conf')[0] == 200
    assert [e['name'] for e in req(SUPER, 'GET', '/api/files/list?path=/')[1]['entries']] == [WEB.lstrip('/'), ETC.lstrip('/')]
    limited = session('l@example.com', 'admin', ['shop'])
    viewer = session('v@example.com', 'viewer')
    for tok in (limited, viewer):
        assert req(tok, 'GET', '/api/files/list?path=' + WEB)[0] == 403
        assert req(tok, 'POST', '/api/files/write', {'path': WEB + '/x.txt', 'content': 'x', 'create': True})[0] == 403
    assert not os.path.exists(WEB + '/x.txt')
    for scope in ('viewer', 'deploy'):
        k, _ = apikeys.create('k-' + scope, scope)
        assert req(None, 'GET', '/api/files/list?path=' + WEB, key=k)[0] == 403, scope
    assert req('bad-session', 'GET', '/api/files/list?path=' + WEB)[0] == 401


if __name__ == '__main__':
    run_tests(dict(globals()))
