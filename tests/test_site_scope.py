"""Per-site permissions: a user limited to some sites can touch only those (real HTTP server in login mode, fakes behind it).

    python tests/test_site_scope.py
"""
import http.client
import json
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_route_sweep as sweep  # noqa: E402  (temporary data folder + fakes)
from fakes import run_tests  # noqa: E402
from coderunner import auth, deploy, monitor, server  # noqa: E402

sweep.install_fakes()
server.STATE['auth'] = 'login'
auth.init('root@example.com')
with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    PORT = s.getsockname()[1]
SRV = server.make_server(PORT)
PORT = server.STATE['port']
threading.Thread(target=SRV.serve_forever, daemon=True).start()
SITES = [{'name': 'shop', 'hostname': 'shop.example.com'}, {'name': 'blog', 'hostname': 'blog.example.com'}]
deploy.status = lambda cfg: {'sites': SITES, 'server': {'host': 'x'}}
monitor.STATE.clear()
monitor.STATE.update({'shop': {'host': 'a', 'up': True, 'points': [[1, 5]]}, 'blog': {'host': 'b', 'up': True, 'points': [[1, 5]]}})


def login_as(email, role, sites=None):
    user, _ = auth.create_user(email, role)
    if sites is not None:
        auth.update_user(user['id'], 0, sites=sites)
    with auth._db() as c:
        return auth._session(c, auth._user(c, user['id']), '127.0.0.1')


def req(token, method, path, body=None):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    h = {'Host': '127.0.0.1:%d' % PORT, 'Cookie': 'cr_session=' + token, 'X-Requested-With': 'coderunner'}
    data = json.dumps(body).encode() if body is not None else None
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.status, r.read()
    c.close()
    return out


def test_limited_admin_sees_and_touches_only_their_sites():
    t = login_as('limited@example.com', 'admin', ['shop'])
    s, b = req(t, 'GET', '/api/status')
    assert s == 200 and [x['name'] for x in json.loads(b)['sites']] == ['shop'] and len(SITES) == 2          # filtered copy; the shared answer is untouched
    assert list(json.loads(req(t, 'GET', '/api/health')[1])['sites']) == ['shop']
    assert req(t, 'POST', '/api/action', {'name': 'shop', 'action': 'restart'})[0] == 200
    assert req(t, 'POST', '/api/action', {'name': 'blog', 'action': 'restart'})[0] == 403
    assert req(t, 'POST', '/api/deploy', {'name': 'blog'})[0] == 403
    assert req(t, 'POST', '/api/deploy', {'name': 'newsite'})[0] == 403                                       # cannot create sites outside the list
    assert req(t, 'GET', '/api/logs?name=shop&lines=20')[0] == 200 and req(t, 'GET', '/api/logs?name=blog&lines=20')[0] == 403
    assert req(t, 'GET', '/api/site/config?name=blog')[0] == 403 and req(t, 'GET', '/api/site/env/values?name=blog')[0] == 403
    assert req(t, 'POST', '/api/site/env', {'name': 'blog', 'set': {'A': '1'}})[0] == 403


def test_limited_users_are_refused_everything_server_wide():
    t = login_as('limited2@example.com', 'admin', ['shop'])
    for method, path in (('GET', '/api/databases'), ('GET', '/api/server/info'), ('GET', '/api/firewall'), ('GET', '/api/care'), ('GET', '/api/apps'), ('GET', '/api/cron'),
                         ('POST', '/api/server/service'), ('POST', '/api/databases/backup'), ('GET', '/api/databases/download?file=x'), ('GET', '/api/activity'), ('GET', '/api/disk')):
        assert req(t, method, path, {} if method == 'POST' else None)[0] == 403, path
    for q in ('kind=service&name=caddy', 'kind=site&name=blog', 'kind=container&name=shop'):
        assert req(t, 'GET', '/api/logs/stream?' + q)[0] == 403, q


def test_unlimited_users_and_super_admin_are_unaffected():
    a = login_as('free@example.com', 'admin')
    assert len(json.loads(req(a, 'GET', '/api/status')[1])['sites']) == 2 and req(a, 'GET', '/api/databases')[0] != 403
    assert req(a, 'POST', '/api/action', {'name': 'blog', 'action': 'restart'})[0] == 200
    root = login_as('root2@example.com', 'super_admin')
    assert req(root, 'GET', '/api/firewall')[0] != 403


def test_viewer_limited_to_sites_stays_read_only():
    t = login_as('view@example.com', 'viewer', ['shop'])
    assert req(t, 'GET', '/api/status')[0] == 200 and req(t, 'POST', '/api/action', {'name': 'shop', 'action': 'stop'})[0] == 403


def test_managing_site_lists():
    user, _ = auth.create_user('m@example.com', 'admin')
    auth.update_user(user['id'], 0, sites=['b-site', 'a-site', 'a-site'])
    assert [u for u in auth.list_users() if u['id'] == user['id']][0]['sites'] == ['a-site', 'b-site']
    auth.update_user(user['id'], 0, role='viewer')                                                              # untouched when not passed
    assert [u for u in auth.list_users() if u['id'] == user['id']][0]['sites'] == ['a-site', 'b-site']
    auth.update_user(user['id'], 0, sites=None)
    assert [u for u in auth.list_users() if u['id'] == user['id']][0]['sites'] is None
    for bad in ('shop', ['Bad Name'], [5], ['../x'], ['a'] * 201):
        try:
            auth.update_user(user['id'], 0, sites=bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    sup, _ = auth.create_user('s@example.com', 'super_admin')
    try:
        auth.update_user(sup['id'], 0, sites=['shop'])
    except ValueError as e:
        assert 'every site' in str(e)
    else:
        raise AssertionError('limited a super admin')


if __name__ == '__main__':
    run_tests(dict(globals()))
