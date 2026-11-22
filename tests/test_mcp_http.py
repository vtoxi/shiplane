"""The hosted MCP endpoint (POST /mcp) on the real HTTP server: off by default, API-key only, scopes respected, nothing local exposed.

    python tests/test_mcp_http.py
"""
import json
import os
import sys
import urllib.request

REAL_URLOPEN = urllib.request.urlopen          # the shared HTTP harness switches the network off; the endpoint calls this same server over loopback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_http_security as H  # noqa: E402  (starts the real server on a free port, fakes SSH and subprocesses)
from fakes import run_tests  # noqa: E402
from coderunner import apikeys, server  # noqa: E402

urllib.request.urlopen = REAL_URLOPEN
ADMIN, _ = apikeys.create('mcp-admin', 'admin')
VIEWER, _ = apikeys.create('mcp-viewer', 'viewer')
DEPLOY, _ = apikeys.create('mcp-deploy', 'deploy')


def rpc(method, params=None, key=ADMIN, mid=1, extra=None, cookie=False, path='/mcp'):
    msg = {'jsonrpc': '2.0', 'method': method}
    if mid is not None:
        msg['id'] = mid
    if params is not None:
        msg['params'] = params
    headers = dict(extra or {})
    if key:
        headers['Authorization'] = 'Bearer ' + key
    s, h, b = H.req('POST', path, msg, headers, cookie=cookie)
    return s, h, (json.loads(b) if b else None)


def tool(name, args=None, key=ADMIN, path='/mcp'):
    s, _, out = rpc('tools/call', {'name': name, 'arguments': args or {}}, key, path=path)
    assert s == 200, (s, out)
    return out['result']['isError'], out['result']['content'][0]['text']


def switch(on):
    s, _, b = H.req('POST', '/api/mcp', {'enabled': on}, H.XRW)
    assert s == 200 and json.loads(b)['enabled'] is on, (s, b)


def test_it_is_off_until_a_super_admin_switches_it_on():
    switch(False)
    assert rpc('initialize')[0] == 404
    assert H.req('GET', '/mcp')[0] == 404
    s, _, b = H.req('GET', '/api/mcp')
    assert s == 200 and json.loads(b)['enabled'] is False
    assert H.req('POST', '/api/mcp', {'enabled': True}, {'Authorization': 'Bearer ' + ADMIN}, cookie=False)[0] in (401, 403)     # a key can never switch it on
    assert json.loads(H.req('GET', '/api/mcp')[2])['enabled'] is False


def test_only_an_api_key_signs_in_here():
    switch(True)
    try:
        s, h, _ = rpc('initialize', key=None)
        assert s == 401 and 'bearer' in h['www-authenticate'].lower()
        assert rpc('initialize', key=None, cookie=True)[0] == 401                       # the browser cookie of a signed-in super admin does not count
        assert rpc('initialize', key='crk_wrong')[0] == 401
        assert rpc('initialize', key=DEPLOY)[0] == 403                                  # a deploy-only key has no business here
        assert rpc('initialize', extra={'Origin': 'https://evil.example.com'})[0] == 403
        assert rpc('initialize', extra={'Origin': 'http://127.0.0.1:%d' % H.PORT})[0] == 200
        assert H.req('GET', '/mcp', cookie=False)[0] == 405
    finally:
        switch(False)


def test_a_conversation_with_an_admin_key():
    switch(True)
    try:
        s, h, out = rpc('initialize', {'protocolVersion': '2025-06-18'})
        assert s == 200 and out['result']['serverInfo']['name'] == 'shiplane' and out['result']['protocolVersion'] == '2025-06-18'
        assert rpc('notifications/initialized', mid=None)[0] == 202
        names = [t['name'] for t in rpc('tools/list', mid=2)[2]['result']['tools']]
        assert 'shiplane_status' in names and 'shiplane_site_delete' in names and len(names) > 90
        assert 'shiplane_deploy_folder' not in names                                    # reads folders of the machine running the server: never offered remotely
        assert rpc('ping', mid=3)[2]['result'] == {}
        err, text = tool('shiplane_routes')
        assert not err and '/api/status' in text                                        # a real call, made as this key, through the normal API
        err, text = tool('shiplane_api', {'method': 'GET', 'path': '/api/users'})
        assert err and 'HTTP 403' in text                                               # super-admin routes stay out of reach of an admin key
        err, text = tool('shiplane_api', {'method': 'POST', 'path': '/api/terminal/open'})
        assert err and 'Terminal is browser-only' in text
        err, text = tool('shiplane_deploy_folder', {'folder': '/etc', 'site': 'x'})
        assert err and 'unknown tool' in text
        err, text = tool('shiplane_database_drop', {'name': 'x'})
        assert err and 'confirm' in text                                                # the confirmation rule holds remotely too
    finally:
        switch(False)


def test_a_read_only_key_sees_and_can_do_only_reads():
    switch(True)
    try:
        tools = rpc('tools/list', key=VIEWER)[2]['result']['tools']
        assert tools and all(t['annotations']['readOnlyHint'] for t in tools)
        err, text = tool('shiplane_site_restart', {'name': 'x'}, key=VIEWER)
        assert err and 'unknown tool' in text
        assert 'READ-ONLY' in rpc('initialize', key=VIEWER)[2]['result']['instructions']
    finally:
        switch(False)


def test_bad_requests_are_refused_cleanly():
    switch(True)
    try:
        s, _, b = H.req('POST', '/mcp', raw=b'not json', headers={'Authorization': 'Bearer ' + ADMIN, 'Content-Type': 'application/json'}, cookie=False)
        assert s == 400 and json.loads(b)['error']['code'] == -32700
        s, _, b = H.req('POST', '/mcp', raw=b'[1,2]', headers={'Authorization': 'Bearer ' + ADMIN}, cookie=False)
        assert s == 400                                                                 # no batches
        assert H.req('POST', '/mcp', raw=b'', headers={'Authorization': 'Bearer ' + ADMIN}, cookie=False)[0] == 400
        assert rpc('no/such/method')[2]['error']['code'] == -32601
    finally:
        switch(False)


def test_it_cannot_starve_the_server_of_request_slots():
    switch(True)
    held = [server.MCP_SLOTS.acquire() for _ in range(2)]
    try:
        s, h, _ = rpc('ping')
        assert s == 503 and h.get('retry-after') == '2'
    finally:
        for _ in held:
            server.MCP_SLOTS.release()
        switch(False)
    switch(True)
    try:
        assert rpc('ping')[0] == 200
    finally:
        switch(False)


def test_code_mode_two_tools_same_rules():
    switch(True)
    try:
        cm = '/mcp?mode=code'
        tools = rpc('tools/list', path=cm)[2]['result']['tools']
        assert [t['name'] for t in tools] == ['shiplane_search', 'shiplane_execute']
        assert 'Code Mode' in rpc('initialize', path=cm)[2]['result']['instructions']
        err, text = tool('shiplane_search', {'code': '[t["name"] for t in tools if "delete" in t["name"]]'}, path=cm)
        assert not err and 'shiplane_site_delete' in text and 'shiplane_deploy_folder' not in tool('shiplane_search', {'code': '[t["name"] for t in tools]'}, path=cm)[1]
        err, text = tool('shiplane_execute', {'code': 'r = routes()\n"/api/status" in str(r)'}, path=cm)
        assert not err and text == 'true', text                                       # a real call through the normal API, as this key
        err, text = tool('shiplane_execute', {'code': 'api("GET", "/api/users")'}, path=cm)
        assert err and 'HTTP 403' in text                                              # super-admin routes stay out of reach
        err, text = tool('shiplane_execute', {'code': 'tool("shiplane_database_drop", name="x")'}, path=cm)
        assert err and 'confirm' in text
        err, text = tool('shiplane_execute', {'code': 'tool("shiplane_deploy_folder", folder="/etc", site="x")'}, path=cm)
        assert err and 'unknown tool' in text
        err, text = tool('shiplane_execute', {'code': 'open("/etc/passwd")'}, path=cm)
        assert err and 'NameError' in text
        tools = rpc('tools/list', key=VIEWER, path=cm)[2]['result']['tools']
        assert all(t['annotations']['readOnlyHint'] for t in tools)
        err, text = tool('shiplane_execute', {'code': 'tool("shiplane_site_restart", name="x")'}, key=VIEWER, path=cm)
        assert err and 'unknown tool' in text                                          # read-only keys stay read-only inside snippets
        err, text = tool('shiplane_execute', {'code': 'api("POST", "/api/action", body={})'}, key=VIEWER, path=cm)
        assert err and 'unknown tool' in text
        assert [t['name'] for t in rpc('tools/list', mid=2)[2]['result']['tools']][0] == 'shiplane_status'      # plain /mcp is unchanged
    finally:
        switch(False)


if __name__ == '__main__':
    run_tests(globals())
