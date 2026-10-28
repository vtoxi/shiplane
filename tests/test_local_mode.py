"""Runs a throwaway copy of the app ON the server (local mode, as the service user) and exercises it through an SSH tunnel.

    python tests/test_local_mode.py

Needs the app installed on the server (scripts/install_on_server.py). Uses its own temporary data folder and port,
so the real accounts and settings are untouched. Creates and removes "srvtest*" sites and a temporary database.
"""
import http.cookiejar
import io
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from coderunner import config  # noqa: E402

cfg = config.load_server()
key = os.path.expanduser(cfg['key'])
ssh = ['ssh', '-i', key, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new', '%s@%s' % (cfg['user'], cfg['host'])]
PORT = 8770
TOKEN = secrets.token_urlsafe(24)
ok = True


def check(label, cond, extra=''):
    global ok
    ok &= bool(cond)
    print(('PASS ' if cond else 'FAIL ') + label + ((' | ' + str(extra)[:300]) if not cond and extra else ''))


remote = subprocess.Popen(ssh + ['sudo -u coderunner env CODERUNNER_DATA=/tmp/cr-localtest CODERUNNER_TOKEN=%s PYTHONUNBUFFERED=1 python3 /opt/coderunner/app.py --no-browser --port %d' % (TOKEN, PORT)],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
tunnel = subprocess.Popen(ssh + ['-N', '-L', '%d:127.0.0.1:%d' % (PORT, PORT)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
URL = 'http://127.0.0.1:%d' % PORT
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def call(path, body=None, raw=None):
    h = {}
    if body is not None or raw is not None:
        h['Content-Type'] = 'application/json' if raw is None else 'application/zip'
        h['X-Requested-With'] = 'coderunner'
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    try:
        with op.open(urllib.request.Request(URL + path, data=data, headers=h), timeout=240) as r:
            b = r.read()
            return r.status, (json.loads(b) if r.headers.get('Content-Type', '').startswith('application/json') else b)
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b)
        except ValueError:
            return e.code, b


def deploy(body):
    s, j = call('/api/deploy', body)
    assert s == 200, j
    since, lines = 0, []
    while True:
        s, r = call('/api/job?id=%s&since=%d' % (j['job'], since))
        lines += r['lines']
        since = r['next']
        if r['done']:
            return r['ok'], lines
        time.sleep(0.5)


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for n, t in files.items():
            z.writestr(n, t)
    return buf.getvalue()


try:
    for _ in range(40):
        try:
            op.open(URL + '/?t=' + TOKEN, timeout=3).read()
            break
        except Exception:
            time.sleep(0.5)
    s, sess = call('/api/session')
    check('instance on the server runs in local mode', s == 200 and sess['server']['mode'] == 'local' and sess['user'] == 'coderunner', sess)
    check('cloudflare is off here (no token in this throwaway instance)', sess['cloudflare'] is False)
    s, st = call('/api/status')
    check('status read locally with sudo', s == 200 and any(x['name'] == 'site' for x in st['sites']) and st['server']['services']['caddy'] == 'active', st if s != 200 else '')
    check('folder picker is disabled in local mode', call('/api/pick-folder', {})[0] == 400)

    s, up = call('/api/upload', raw=zip_bytes({'index.html': '<h1>srvtest</h1>'}))
    okd, lines = deploy({'name': 'srvtest', 'hostname': 'srvtest.example.com', 'type': 'static', 'source': up['path']})
    check('static deploy from an uploaded zip, run on the server', okd, lines[-4:])
    ndir = '/tmp/cr-localtest-node'
    subprocess.run(ssh + ['mkdir -p %s && printf %s > %s/server.mjs' % (ndir, json.dumps("import http from 'node:http';http.createServer((q,r)=>r.end('srvtest-node db='+Boolean(process.env.DATABASE_URL))).listen(Number(process.env.PORT),process.env.HOST);"), ndir)], check=True)
    okn, ln = deploy({'name': 'srvtest-node', 'hostname': 'srvtest-node.example.com', 'type': 'node', 'source': ndir, 'start': 'server.mjs', 'port': 5200, 'postgresDb': True})
    check('node app + postgres database created by the service user', okn, ln[-6:])
    time.sleep(4)
    s, st = call('/api/status')
    by = {x['name']: x for x in st['sites']}
    check('both test sites healthy from the public internet', by['srvtest'].get('public') == 200 and by['srvtest-node'].get('public') == 200 and by['srvtest-node'].get('service') == 'active', {k: (v.get('public'), v.get('service')) for k, v in by.items()})
    check('deploys are attributed to the service user', by['srvtest-node']['meta']['deployedBy'] == 'coderunner', by['srvtest-node']['meta'])
    s, r = call('/api/site/env', {'name': 'srvtest-node', 'set': {'HELLO': 'world'}})
    check('environment edit + restart', s == 200 and r['ok'], r)
    s, r = call('/api/databases/create', {'name': 'srvtest_db'})
    check('database create', s == 200 and len(r['password']) >= 16, r)
    s, b = call('/api/databases/backup', {'name': 'srvtest_db'})
    check('database backup', s == 200 and b['size'] > 0, b)
    s, d = call('/api/databases/download?file=' + b['file'])
    check('backup download streams a gzip', s == 200 and d[:2] == b'\x1f\x8b', (s, d[:10]))
    call('/api/databases/backup-delete', {'file': b['file']})
    check('database drop', call('/api/databases/drop', {'name': 'srvtest_db', 'confirm': 'srvtest_db'})[0] == 200)
    s, info = call('/api/server/info')
    check('server info (ufw, updates)', s == 200 and 'ufw' in info and isinstance(info['updates_count'], int), info if s != 200 else '')
    check('service logs', call('/api/server/service', {'name': 'caddy', 'action': 'logs'})[0] == 200)
    s, m = call('/api/metrics')
    check('metrics sample', s == 200 and m['ram_total'] > 0 and m['cpu_total'] > 0 and m['disk_total'] > 0, m if s != 200 else '')
    s, hl = call('/api/health')
    check('health endpoint', s == 200 and 'sites' in hl)
    s, bl = call('/api/billing')
    check('billing answers without provider tokens', s == 200 and bl['hetzner']['ready'] is False and bl['cloudflare']['ready'] is False, bl)
    check('log stream rejects an unknown service', call('/api/logs/stream?kind=service&name=ssh')[0] == 400)
    try:
        with op.open(URL + '/api/logs/stream?kind=service&name=caddy', timeout=25) as r:
            ctype, got = r.headers.get('Content-Type'), b''
            for _ in range(6):
                got += r.readline()
                if b'data:' in got:
                    break
        check('live log stream sends server-sent events', ctype == 'text/event-stream' and b'data:' in got, got[:200])
    except Exception as e:
        check('live log stream sends server-sent events', False, e)
    # ------------------------------------------------ restore, schedule, alerts, policy, auto-rollback
    def wait_job(jid):
        since, lines = 0, []
        while True:
            _, r = call('/api/job?id=%s&since=%d' % (jid, since))
            lines += r['lines']
            since = r['next']
            if r['done']:
                return r['ok'], lines
            time.sleep(0.5)

    psql = lambda sql_: subprocess.run(ssh + ['sudo -u postgres psql -d srvtest_rs -tAc "%s"' % sql_], capture_output=True, text=True).stdout.strip()
    before = {b['file'] for b in call('/api/databases')[1]['backups']}
    check('restore test database created', call('/api/databases/create', {'name': 'srvtest_rs'})[0] == 200)
    psql("create table t(v text); insert into t values ('one')")
    s, b = call('/api/databases/backup', {'name': 'srvtest_rs'})
    psql("insert into t values ('two')")
    check('database has 2 rows before the restore', psql('select count(*) from t') == '2')
    s, bad = call('/api/databases/restore', {'name': 'srvtest_rs', 'file': b['file'], 'confirm': 'wrong'})
    check('restore needs the name typed', s == 400, bad)
    s, r = call('/api/databases/restore', {'name': 'srvtest_rs', 'file': b['file'], 'confirm': 'srvtest_rs'})
    okr, lr = wait_job(r['job'])
    check('restore job succeeds', okr and any('restored srvtest_rs' in l for l in lr), lr[-5:])
    check('restore brought back the backed-up data (1 row)', psql('select count(*) from t') == '1', psql('select count(*) from t'))
    check('old database was swapped out and temporary ones removed', subprocess.run(ssh + ['sudo -u postgres psql -tAc "select count(*) from pg_database where datname in (\'srvtest_rs_old\', \'srvtest_rs_restore\')"'], capture_output=True, text=True).stdout.strip() == '0')
    mine = [x for x in call('/api/databases')[1]['backups'] if x['file'].startswith('srvtest_rs-')]
    check('a safety backup of the pre-restore data was taken', len(mine) >= 2, mine)

    s, sc = call('/api/backups/schedule', {'enabled': False, 'hour': 3, 'keep': 90})
    check('backup schedule saved', s == 200 and sc['hour'] == 3 and sc['keep'] == 90 and sc['enabled'] is False, sc)
    check('bad schedule refused', call('/api/backups/schedule', {'enabled': True, 'hour': 30, 'keep': 5})[0] == 400)
    s, r = call('/api/backups/run', {})
    okb, lb = wait_job(r['job'])
    check('"back up everything now" job succeeds', okb and any('srvtest_rs' in l for l in lb), lb[-5:])
    check('schedule shows the last run', call('/api/backups/schedule')[1]['last']['ok'] is True)
    for x in call('/api/databases')[1]['backups']:
        if x['file'] not in before:
            call('/api/databases/backup-delete', {'file': x['file']})   # leave the server's backup folder as it was
    call('/api/databases/drop', {'name': 'srvtest_rs', 'confirm': 'srvtest_rs'})

    check('alert settings readable, secrets absent', call('/api/alerts')[0] == 200 and 'token' not in json.dumps(call('/api/alerts')[1]))
    check('bad telegram token refused', call('/api/alerts', {'telegram_token': 'nope'})[0] == 400)
    check('test alert with no channel says so', call('/api/alerts/test', {})[0] == 400)
    check('policy needs your own 2fa first (token mode has no account)', call('/api/policy', {'require_2fa': True})[0] == 400)

    rb1 = '/tmp/cr-rb1'
    app_js = lambda body: "import http from 'node:http';" + body
    for d, code in (('/tmp/cr-rb1', "http.createServer((q,r)=>r.end('v1')).listen(Number(process.env.PORT),process.env.HOST);"),
                    ('/tmp/cr-rb2', "process.exit(1);"),
                    ('/tmp/cr-rb3', "http.createServer((q,r)=>r.end('v3')).listen(Number(process.env.PORT),process.env.HOST);")):
        subprocess.run(ssh + ['mkdir -p %s && printf %%s %s > %s/server.mjs' % (d, json.dumps(app_js(code)), d)], check=True)
    rbapp = {'name': 'srvtest-rb', 'hostname': 'srvtest-rb.example.com', 'type': 'node', 'start': 'server.mjs', 'port': 5201}
    ok1, l1 = deploy(dict(rbapp, source='/tmp/cr-rb1'))
    first = next(x for x in call('/api/status')[1]['sites'] if x['name'] == 'srvtest-rb')['current']
    check('good app deploys', ok1, l1[-4:])
    ok2, l2 = deploy(dict(rbapp, source='/tmp/cr-rb2'))
    site = next(x for x in call('/api/status')[1]['sites'] if x['name'] == 'srvtest-rb')
    check('a crashing release fails the deploy and is rolled back', not ok2 and any('ROLLED BACK' in l for l in l2), l2[-6:])
    check('the previous release is current again and the service is up', site['current'] == first and site['service'] == 'active', (site['current'], first, site['service']))
    check('rollback is in the activity log', any(e['kind'] == 'deploy-rollback' for e in call('/api/activity')[1]['events']))
    out = subprocess.run(ssh + ["curl -s -m 5 -H 'Host: srvtest-rb.example.com' http://127.0.0.1/"], capture_output=True, text=True).stdout
    check('the old version still answers', out == 'v1', out)
    ok3, l3 = deploy(dict(rbapp, source='/tmp/cr-rb3'))
    out = subprocess.run(ssh + ["curl -s -m 5 -H 'Host: srvtest-rb.example.com' http://127.0.0.1/"], capture_output=True, text=True).stdout
    check('a good release after the failed one deploys normally', ok3 and out == 'v3', (l3[-3:], out))

    check('this app protects itself from delete/stop', call('/api/action', {'name': 'coderunner', 'action': 'delete', 'confirm': 'coderunner'})[0] == 400 and call('/api/action', {'name': 'coderunner', 'action': 'stop'})[0] == 400)
    check('this app cannot be deployed over', call('/api/deploy', {'name': 'coderunner', 'hostname': 'x.example.com', 'type': 'static', 'source': '/tmp'})[0] == 400)
    for n in ('srvtest', 'srvtest-node', 'srvtest-rb'):
        s, r = call('/api/action', {'name': n, 'action': 'delete', 'confirm': n, 'dropDb': True})
        check('delete ' + n, s == 200 and r['ok'], r)
    s, st = call('/api/status')
    check('server is back to the original sites', sorted(x['name'] for x in st['sites']) == ['coderunner', 'site'], [x['name'] for x in st['sites']])
finally:
    tunnel.terminate()
    remote.terminate()
    subprocess.run(ssh + ['pkill -f "app.py --no-browser --port %d"; rm -rf /tmp/cr-localtest /tmp/cr-localtest-node /tmp/cr-rb1 /tmp/cr-rb2 /tmp/cr-rb3' % PORT], capture_output=True)

print('\nALL PASSED' if ok else '\nSOME FAILED')
sys.exit(0 if ok else 1)
