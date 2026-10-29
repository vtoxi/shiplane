"""End-to-end checks of the whole app against the real server (and a fake Cloudflare).

    python tests/test_app.py

Needs working SSH access to the server (server.json). It creates temporary sites/databases named "apptest*"
and removes them again. Cloudflare calls go to tests/mock_cloudflare.py, never the real account.
"""
import http.cookiejar
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import mock_cloudflare as mock  # noqa: E402

srv, BASE = mock.start()
DATA = tempfile.mkdtemp(prefix='cr-test-data-')
PORT = 8799
env = dict(os.environ, CF_API_BASE=BASE, CLOUDFLARE_API_TOKEN=mock.GOOD, CODERUNNER_DATA=DATA)
proc = subprocess.Popen([sys.executable, os.path.join(ROOT, 'app.py'), '--no-browser', '--port', str(PORT)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True)
line = ''.join(proc.stdout.readline() for _ in range(3))
TOKEN = re.search(r't=(\S+)', line).group(1)
URL = 'http://127.0.0.1:%d' % PORT
ok = True


def check(label, cond, extra=''):
    global ok
    ok &= bool(cond)
    print(('PASS ' if cond else 'FAIL ') + label + ((' | ' + str(extra)[:300]) if not cond and extra else ''))


class Client:
    def __init__(self, bearer=None, host=None):
        self.bearer, self.host = bearer, host
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def open(self, req):
        if self.host:
            req.add_header('Host', self.host)
        if self.bearer:
            req.add_header('Authorization', 'Bearer ' + self.bearer)
        return self.op.open(req, timeout=240)

    def call(self, path, body=None, raw=None, headers=True):
        h = {}
        if body is not None or raw is not None:
            h['Content-Type'] = 'application/json' if raw is None else 'application/zip'
            if headers and not self.bearer:
                h['X-Requested-With'] = 'coderunner'
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        try:
            with self.open(urllib.request.Request(URL + path, data=data, headers=h)) as r:
                b = r.read()
                return r.status, (json.loads(b) if r.headers.get('Content-Type', '').startswith('application/json') else b)
        except urllib.error.HTTPError as e:
            b = e.read()
            try:
                return e.code, json.loads(b)
            except ValueError:
                return e.code, b


def wait_job(c, jid):
    since, lines = 0, []
    while True:
        s, r = c.call('/api/job?id=%s&since=%d' % (jid, since))
        lines += r['lines']
        since = r['next']
        if r['done']:
            return r['ok'], lines
        time.sleep(0.5)


def deploy(c, body):
    s, j = c.call('/api/deploy', body)
    assert s == 200, j
    return wait_job(c, j['job'])


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


try:
    # ------------------------------------------------------------ security
    anon = Client()
    check('no cookie: page refused', anon.call('/')[0] == 401)
    check('no cookie: API refused', anon.call('/api/status')[0] == 401)
    c = Client()
    c.op.open(URL + '/?t=' + TOKEN, timeout=20).read()
    check('wrong Host header refused', Client(host='evil.example').call('/api/session')[0] in (403, 401))
    check('POST without X-Requested-With refused', c.call('/api/action', {'name': 'x', 'action': 'restart'}, headers=False)[0] == 403)
    check('static files need auth', anon.call('/static/js/main.js')[0] == 401)
    s, b = c.call('/static/../server.json')
    check('path traversal blocked', s == 404, (s, b))

    # -------------------------------------------------------------- session
    s, sess = c.call('/api/session')
    check('session: ssh mode, cloudflare ready, key found', s == 200 and sess['server']['mode'] == 'ssh' and sess['cloudflare'] and sess['keyFound'], sess)
    s, st = c.call('/api/status')
    check('status lists the existing site', s == 200 and any(x['name'] == 'site' for x in st['sites']), st if s != 200 else '')
    check('status has server details', {'os', 'kernel', 'swap_total', 'services'} <= set(st['server']))

    # ----------------------------------------------------------- validation
    check('bad site name rejected', c.call('/api/deploy', {'name': 'Bad Name', 'hostname': 'x.example.com', 'type': 'static', 'source': '.'})[0] == 400)
    check('reserved port rejected', c.call('/api/deploy', {'name': 'ok', 'hostname': 'x.example.com', 'type': 'node', 'source': tempfile.gettempdir(), 'port': 8765, 'start': 'a.js'})[0] == 400)
    check('injection in action name rejected', c.call('/api/action', {'name': 'a; rm -rf /', 'action': 'restart'})[0] == 400)
    check('delete needs the typed name', c.call('/api/action', {'name': 'site', 'action': 'delete', 'confirm': 'nope'})[0] == 400)
    check('protected database cannot be dropped', c.call('/api/databases/drop', {'name': 'postgres', 'confirm': 'postgres'})[0] == 400)
    check('bad backup file name rejected', c.call('/api/databases/download?file=../../etc/passwd')[0] == 400)

    # --------------------------------------------- zip upload + static deploy
    s, up = c.call('/api/upload', raw=make_zip({'site/index.html': '<h1>apptest v1</h1>'}))
    check('zip upload unpacks (single folder is unwrapped)', s == 200 and os.path.isfile(os.path.join(up['path'], 'index.html')), up)
    s, bad = c.call('/api/upload', raw=b'not a zip at all')
    check('non-zip upload rejected', s == 400, bad)
    s, evil = c.call('/api/upload', raw=make_zip({'../evil.txt': 'x'}))
    check('zip with unsafe path rejected', s == 400, evil)
    okd, lines = deploy(c, {'name': 'apptest', 'hostname': 'apptest.example.com', 'type': 'static', 'source': up['path'], 'mapCf': True, 'redirectFrom': ['www.apptest.example.com']})
    check('static deploy ok', okd, lines[-5:])
    check('cloudflare mapping ran for the redirect host', any('www.apptest.example.com' in l and 'route added' in l for l in lines), lines)
    check('wildcard-covered host needed no new route', not any(l.strip() == 'tunnel route added: apptest.example.com' for l in lines))

    # ----------------------------------------------------- node app + postgres
    ndir = tempfile.mkdtemp()
    open(os.path.join(ndir, 'server.mjs'), 'w').write("import http from 'node:http';http.createServer((q,r)=>r.end('apptest-node foo='+(process.env.FOO||'')+' db='+Boolean(process.env.DATABASE_URL))).listen(Number(process.env.PORT),process.env.HOST);")
    okn, ln = deploy(c, {'name': 'apptest-node', 'hostname': 'apptest-node.example.com', 'type': 'node', 'source': ndir, 'start': 'server.mjs', 'port': 5100, 'postgresDb': True, 'env': {'NODE_ENV': 'production'}})
    check('node + postgres deploy ok', okn, ln[-6:])
    okd2, ld2 = deploy(c, {'name': 'apptest-dup', 'hostname': 'apptest.example.com', 'type': 'static', 'source': ndir})
    check('duplicate hostname refused', (not okd2) and any('already used' in x for x in ld2), ld2[-3:])
    okd3, ld3 = deploy(c, {'name': 'apptest-dup', 'hostname': 'apptest-dup.example.com', 'type': 'node', 'source': ndir, 'start': 'server.mjs', 'port': 5100})
    check('duplicate port refused', (not okd3) and any('port 5100' in x for x in ld3), ld3[-3:])
    time.sleep(4)
    s, st = c.call('/api/status')
    by = {x['name']: x for x in st['sites']}
    check('both sites healthy', by['apptest'].get('public') == 200 and by['apptest-node'].get('public') == 200 and by['apptest-node'].get('service') == 'active', {k: (v.get('public'), v.get('service')) for k, v in by.items()})
    check('failed attempts left nothing behind', 'apptest-dup' not in by)
    check('site metadata recorded', by['apptest-node']['meta'].get('deployedBy') == __import__('coderunner.config', fromlist=['x']).load_server()['user'] and by['apptest-node']['db'])

    # ------------------------------------------------- environment variables
    s, r = c.call('/api/site/env', {'name': 'apptest-node', 'set': {'FOO': 'bar-value'}})
    check('env set + restart ok', s == 200 and r['ok'], r)
    time.sleep(2)
    out = subprocess.run(['curl', '-s', '-m', '10', 'https://apptest-node.example.com/'], capture_output=True, text=True).stdout
    check('app sees the new variable', 'foo=bar-value' in out and 'db=true' in out, out)
    s, st = c.call('/api/status')
    keys = [x for x in st['sites'] if x['name'] == 'apptest-node'][0]['env_keys']
    check('status lists variable names only', 'FOO' in keys and 'DATABASE_URL' in keys and 'bar-value' not in json.dumps(st))
    s, r = c.call('/api/site/env', {'name': 'apptest-node', 'unset': ['FOO']})
    check('env unset ok', s == 200 and r['ok'], r)
    check('bad variable name rejected', c.call('/api/site/env', {'name': 'apptest-node', 'set': {'A B': '1'}})[0] == 400)
    check('static site has no env file', c.call('/api/site/env', {'name': 'apptest', 'set': {'A': '1'}})[1].get('ok') is False)

    # -------------------------------------------------- config, logs, releases
    s, cfg = c.call('/api/site/config?name=apptest-node')
    names = [x['path'].split('/')[-1] for x in cfg['sections']]
    check('config shows caddy, unit and meta', names == ['apptest-node.caddy', 'apptest-node.service', 'apptest-node.json'] and 'reverse_proxy 127.0.0.1:5100' in cfg['sections'][0]['text'], cfg)
    s, lg = c.call('/api/logs?name=apptest-node&lines=20')
    check('logs returned', s == 200 and 'apptest-node' in lg['text'], lg)
    open(os.path.join(up['path'], 'index.html'), 'w').write('<h1>apptest v2</h1>')
    time.sleep(1.2)
    deploy(c, {'name': 'apptest', 'hostname': 'apptest.example.com', 'type': 'static', 'source': up['path']})
    s, st = c.call('/api/status')
    rel = [x for x in st['sites'] if x['name'] == 'apptest'][0]['releases']
    check('two releases kept', len(rel) == 2, rel)
    s, r = c.call('/api/action', {'name': 'apptest', 'action': 'activate', 'release': rel[0]})
    check('rollback ok', s == 200 and r['ok'], r)
    body = urllib.request.urlopen(urllib.request.Request('https://apptest.example.com/', headers={'User-Agent': 't'}), timeout=20).read().decode()
    check('public site serves the rolled-back release', 'apptest v1' in body, body[:80])
    s, r = c.call('/api/action', {'name': 'apptest-node', 'action': 'restart'})
    check('restart ok', s == 200 and r['ok'], r)

    # ----------------------------------------------------------- databases
    s, r = c.call('/api/databases/create', {'name': 'apptest_db'})
    check('database created with a one-time password', s == 200 and len(r['password']) >= 16 and r['url'].endswith('/apptest_db'), r)
    check('duplicate database refused', c.call('/api/databases/create', {'name': 'apptest_db'})[0] == 500)
    check('bad database name rejected', c.call('/api/databases/create', {'name': 'Bad-Name'})[0] == 400)
    s, r = c.call('/api/databases/backup', {'name': 'apptest_db'})
    check('backup created', s == 200 and r['file'].startswith('apptest_db-') and r['size'] > 0, r)
    bfile = r['file']
    s, lst = c.call('/api/databases')
    check('list shows the database and its backup', any(d['name'] == 'apptest_db' for d in lst['databases']) and any(b['file'] == bfile for b in lst['backups']), lst)
    s, data = c.call('/api/databases/download?file=' + bfile)
    check('backup downloads as gzip', s == 200 and data[:2] == b'\x1f\x8b', (s, data[:20]))
    check('backup deleted', c.call('/api/databases/backup-delete', {'file': bfile})[0] == 200)
    check('drop needs the typed name', c.call('/api/databases/drop', {'name': 'apptest_db', 'confirm': 'x'})[0] == 400)
    check('database dropped', c.call('/api/databases/drop', {'name': 'apptest_db', 'confirm': 'apptest_db'})[0] == 200)

    # -------------------------------------------------------------- server
    s, info = c.call('/api/server/info')
    check('server info', s == 200 and isinstance(info['updates_count'], int) and 'ufw' in info and 'listening' in info, info if s != 200 else '')
    s, r = c.call('/api/server/service', {'name': 'caddy', 'action': 'logs'})
    check('service logs', s == 200 and r['ok'], r)
    check('unknown service refused', c.call('/api/server/service', {'name': 'sshd', 'action': 'restart'})[0] == 400)
    s, r = c.call('/api/server/container', {'name': 'portainer', 'action': 'logs'})
    check('container logs', s == 200 and r['ok'], r)
    check('reboot needs confirmation', c.call('/api/server/reboot', {'confirm': 'no'})[0] == 400)

    # ----------------------------------------------------------- cloudflare
    s, v = c.call('/api/cloudflare/verify')
    check('cloudflare verify', s == 200 and v['token'] == 'active' and v['tunnel']['status'] == 'healthy', v)
    s, m = c.call('/api/mapping')
    check('mapping lists tunnel routes', m['ready'] and 'example.com' in [r['host'] for r in m['rows']], m)

    # ------------------------------------------------------------- api keys
    s, k = c.call('/api/keys/create', {'name': 'phone'})
    check('api key created (shown once)', s == 200 and k['token'].startswith('crk_'), k)
    bearer = Client(bearer=k['token'])
    check('bearer key works without a cookie', bearer.call('/api/status')[0] == 200)
    check('bearer POST works without the CSRF header', bearer.call('/api/action', {'name': 'apptest-node', 'action': 'restart'})[0] == 200)
    check('bearer cannot manage keys', bearer.call('/api/keys')[0] == 403)
    check('wrong bearer refused', Client(bearer='crk_wrong').call('/api/status')[0] == 401)
    s, kl = c.call('/api/keys')
    check('key list never contains the secret', k['token'] not in json.dumps(kl) and 'hash' not in json.dumps(kl), kl)
    check('api key is hashed on disk', k['token'] not in open(os.path.join(DATA, 'apikeys.json')).read())
    check('routes endpoint lists the API', len(bearer.call('/api/routes')[1]['routes']) > 25)
    c.call('/api/keys/revoke', {'id': k['key']['id']})
    check('revoked key stops working', bearer.call('/api/status')[0] == 401)

    # --------------------------------------------------------- cleanup
    for n, h in (('apptest', ['apptest.example.com', 'www.apptest.example.com']), ('apptest-node', ['apptest-node.example.com'])):
        s, r = c.call('/api/action', {'name': n, 'action': 'delete', 'confirm': n, 'dropDb': True, 'unmapCf': True, 'hosts': h})
        check('delete ' + n, s == 200 and r['ok'], r)
    s, st = c.call('/api/status')
    check('only the original sites remain (site + this app)', sorted(x['name'] for x in st['sites']) == ['coderunner', 'site'], [x['name'] for x in st['sites']])
    check('fake cloudflare is clean again', mock.pristine())
    s, ev = c.call('/api/activity')
    kinds = {e['kind'] for e in ev['events']}
    check('activity log recorded the work', {'deploy', 'env', 'db-create', 'db-backup', 'delete', 'restart', 'activate', 'api-key-create'} <= kinds, kinds)
    check('activity never stores secret values', 'bar-value' not in json.dumps(ev) and mock.GOOD not in json.dumps(ev))

    # ------------------------------------------------------- local mode runner
    from coderunner import runner
    rc, out = runner.run({'mode': 'local'}, 'echo hello-local')
    check('local mode runs commands directly', rc == 0 and out.strip() == 'hello-local', out)
finally:
    proc.terminate()

print('\nALL PASSED' if ok else '\nSOME FAILED')
sys.exit(0 if ok else 1)
