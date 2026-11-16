"""The Shiplane MCP server (mcp/shiplane_mcp.py): the real process, speaking MCP over stdio, against a fake Shiplane HTTP API.

    python tests/test_mcp.py
"""
import http.server
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'mcp', 'shiplane_mcp.py')
KEY = 'crk_secretkey_for_tests_123'
SEEN = []          # (method, path, auth header, body)
JOBS = {'j1': [{'lines': ['building', 'done'], 'next': 2, 'done': True, 'ok': True}]}


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_any(self):
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b''
        body = raw if self.headers.get('Content-Type') == 'application/zip' else (json.loads(raw) if raw else None)
        SEEN.append((self.command, self.path, self.headers.get('Authorization'), body))
        p = self.path.split('?')[0]
        if self.headers.get('Authorization') != 'Bearer ' + KEY:
            return self.reply({'error': 'not signed in'}, 401)
        if p == '/api/status':
            return self.reply({'sites': [{'name': 'blog', 'meta': {'type': 'static', 'hostname': 'blog.example.com'}}]})
        if p == '/api/job':
            return self.reply(JOBS['j1'][0])
        if p == '/api/upload':
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                SEEN.append(('ZIP', sorted(z.namelist()), None, None))
            return self.reply({'path': '/tmp/up.zip'})
        if p == '/api/deploy':
            return self.reply({'job': 'j1'})
        if p == '/api/action':
            return self.reply({'ok': True, 'text': 'deleted'})
        if p == '/api/files/list':
            return self.reply({'entries': []})
        if p == '/api/users':
            return self.reply({'error': 'needs super admin'}, 403)
        if p == '/api/databases/drop':
            return self.reply({'ok': True})
        self.reply({'error': 'nope'}, 404)

    do_GET = do_POST = handle_any


SERVER = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fake)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
URL = 'http://127.0.0.1:%d' % SERVER.server_address[1]


def session(requests, env=None):
    e = dict(os.environ, SHIPLANE_URL=URL, SHIPLANE_KEY=KEY)
    e.pop('SHIPLANE_MCP_READONLY', None)
    e.update(env or {})
    msgs = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}}, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}]
    msgs += [{'jsonrpc': '2.0', 'id': i + 2, **r} for i, r in enumerate(requests)]
    p = subprocess.run([sys.executable, SCRIPT], input='\n'.join(json.dumps(m) for m in msgs) + '\n', capture_output=True, text=True, env=e, timeout=60)
    assert KEY not in p.stdout + p.stderr, 'the API key leaked'
    return [json.loads(l) for l in p.stdout.splitlines()]


def call(name, args=None, env=None):
    out = session([{'method': 'tools/call', 'params': {'name': name, 'arguments': args or {}}}], env)[-1]['result']
    return out['isError'], out['content'][0]['text']


def test_handshake_and_listing():
    r = session([{'method': 'tools/list'}, {'method': 'ping'}, {'method': 'nope'}])
    assert r[0]['result']['serverInfo']['name'] == 'shiplane' and r[0]['result']['protocolVersion'] == '2025-06-18'
    tools = r[1]['result']['tools']
    names = [t['name'] for t in tools]
    assert len(names) == len(set(names)) and len(names) > 90, len(names)
    for t in tools:
        assert t['inputSchema']['type'] == 'object' and t['description'] and 'readOnlyHint' in t['annotations'], t['name']
        assert set(t['inputSchema'].get('required', [])) <= set(t['inputSchema']['properties']), t['name']
    assert {'shiplane_status', 'shiplane_deploy_folder', 'shiplane_site_delete', 'shiplane_api', 'shiplane_files_delete'} <= set(names)
    assert 'shiplane_terminal' not in ''.join(names)
    assert r[2]['result'] == {} and r[3]['error']['code'] == -32601


def test_read_only_mode_hides_every_write():
    r = session([{'method': 'tools/list'}], {'SHIPLANE_MCP_READONLY': '1'})[1]['result']['tools']
    assert r and all(t['annotations']['readOnlyHint'] for t in r) and 'shiplane_site_delete' not in [t['name'] for t in r]
    err, text = call('shiplane_site_restart', {'name': 'blog'}, {'SHIPLANE_MCP_READONLY': '1'})
    assert err and 'unknown tool' in text
    err, text = call('shiplane_api', {'method': 'POST', 'path': '/api/action'}, {'SHIPLANE_MCP_READONLY': '1'})
    assert err and 'unknown tool' in text            # shiplane_api is a write tool: hidden too


def test_reads_send_the_key_and_query():
    del SEEN[:]
    err, text = call('shiplane_files_list', {'path': '/srv'})
    assert not err and json.loads(text) == {'entries': []}
    assert SEEN[-1][:3] == ('GET', '/api/files/list?path=%2Fsrv', 'Bearer ' + KEY)
    err, text = call('shiplane_files_list', {})
    assert err and 'missing required argument: path' in text


def test_destructive_tools_need_confirmation():
    del SEEN[:]
    err, text = call('shiplane_database_drop', {'name': 'x'})
    assert err and 'confirm' in text and not SEEN
    err, text = call('shiplane_site_delete', {'name': 'blog', 'confirm': 'other'})
    assert err and 'exactly the site name' in text and not SEEN
    err, text = call('shiplane_site_delete', {'name': 'blog', 'confirm': 'blog', 'purge': True})
    assert not err and SEEN[-1][3] == {'name': 'blog', 'action': 'delete', 'confirm': 'blog', 'purge': True, 'dropDb': True}, SEEN[-1]


def test_fixed_actions_and_unknown_arguments():
    del SEEN[:]
    call('shiplane_site_restart', {'name': 'blog', 'junk': 1})
    assert SEEN[-1][3] == {'name': 'blog', 'action': 'restart'}


def test_deploy_folder_zips_uploads_and_follows_the_job():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, 'node_modules'))
    os.makedirs(os.path.join(d, 'css'))
    for f in ('index.html', 'css/a.css', 'node_modules/skip.js'):
        open(os.path.join(d, f), 'w').write('x')
    del SEEN[:]
    err, text = call('shiplane_deploy_folder', {'folder': d, 'site': 'blog'})
    out = json.loads(text)
    assert not err and out['ok'] and out['done'] and 'building' in out['log'], text
    assert ('ZIP', ['css/a.css', 'index.html'], None, None) in SEEN
    dep = next(s for s in SEEN if s[1] == '/api/deploy')[3]
    assert dep['name'] == 'blog' and dep['type'] == 'static' and dep['hostname'] == 'blog.example.com' and dep['source'] == '/tmp/up.zip'    # saved settings reused
    err, text = call('shiplane_deploy_folder', {'folder': d, 'site': 'brand-new'})
    assert err and 'first deploy' in text
    err, text = call('shiplane_deploy_folder', {'folder': '/no/such/dir', 'site': 'blog'})
    assert err and 'existing folder' in text


def test_errors_are_clear():
    err, text = call('shiplane_api', {'method': 'GET', 'path': '/api/users'})
    assert err and 'HTTP 403' in text and 'browser-only' in text
    err, text = call('shiplane_api', {'method': 'POST', 'path': '/api/terminal/open'})
    assert err and 'Terminal is browser-only' in text
    err, text = call('shiplane_api', {'method': 'GET', 'path': '/etc/passwd'})
    assert err and '/api/' in text
    err, text = call('no_such_tool')
    assert err and 'unknown tool' in text
    err, text = call('shiplane_status', env={'SHIPLANE_KEY': 'wrong'})
    assert err and 'HTTP 401' in text
    err, text = call('shiplane_status', env={'SHIPLANE_URL': ''})
    assert err and 'SHIPLANE_URL' in text
    err, text = call('shiplane_status', env={'SHIPLANE_URL': 'http://127.0.0.1:1'})
    assert err and 'cannot reach' in text


def test_every_tool_points_at_a_real_route():
    """Drift guard: a renamed or removed Shiplane route must fail here, not silently in front of the AI."""
    sys.path.insert(0, ROOT)
    from coderunner import server
    import importlib.util
    spec = importlib.util.spec_from_file_location('shiplane_mcp', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    real = set(server.ROUTES)
    bad = [(t['name'], t['method'], t['path']) for t in mod.TOOLS if (t['method'], t['path']) not in real]
    assert not bad, bad
    for t in mod.TOOLS:                                           # a tool must not demand a role an API key can never have
        role = server.ROUTES[(t['method'], t['path'])].get('role')
        assert role != 'super_admin', (t['name'], 'needs a super admin: API keys can never call it')


def test_settings_can_come_from_an_env_file():
    d = tempfile.mkdtemp()
    f = os.path.join(d, 'shiplane.env')
    open(f, 'w').write('# comment\nexport SHIPLANE_URL="%s"\nSHIPLANE_KEY=%s  # inline\nOTHER_SECRET=nope\n' % (URL, KEY))
    base = {k: v for k, v in os.environ.items() if not k.startswith('SHIPLANE_')}
    msgs = '\n'.join(json.dumps(m) for m in ({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'shiplane_status', 'arguments': {}}})) + '\n'

    def ask(env, cwd=None):
        p = subprocess.run([sys.executable, SCRIPT], input=msgs, capture_output=True, text=True, env=dict(base, **env), cwd=cwd, timeout=30)
        assert KEY not in p.stdout + p.stderr
        return json.loads(p.stdout.splitlines()[-1])['result']
    assert not ask({'SHIPLANE_ENV_FILE': f})['isError']                                # explicit file
    open(os.path.join(d, '.env'), 'w').write('SHIPLANE_URL=%s\nSHIPLANE_KEY=%s\n' % (URL, KEY))
    assert not ask({}, cwd=d)['isError']                                               # ./.env of the folder Claude was started in
    sd = os.path.join(ROOT, 'scripts', '.env')
    assert not os.path.exists(sd)
    try:
        open(sd, 'w').write('SHIPLANE_URL=%s\nSHIPLANE_KEY=%s\n' % (URL, KEY))
        assert not ask({}, cwd=tempfile.mkdtemp())['isError']                              # scripts/.env of the repository, whatever folder Claude started in
    finally:
        os.remove(sd)
    assert 'HTTP 401' in ask({'SHIPLANE_KEY': 'wrong'}, cwd=d)['content'][0]['text']   # the real environment wins over the file
    assert ask({}, cwd=tempfile.mkdtemp())['isError']                                  # no file, no settings: a clear error, not a crash


def test_bad_input_does_not_kill_the_server():
    p = subprocess.run([sys.executable, SCRIPT], input='not json\n[1]\n{"jsonrpc":"2.0","id":9,"method":"ping"}\n', capture_output=True, text=True, timeout=30)
    lines = [json.loads(l) for l in p.stdout.splitlines()]
    assert lines[0]['error']['code'] == -32700 and lines[-1] == {'jsonrpc': '2.0', 'id': 9, 'result': {}}


if __name__ == '__main__':
    run_tests(globals())
