"""Unit checks of the Cloudflare mapping logic against the fake API (never the real account).

    python tests/test_cloudflare.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import mock_cloudflare as mock  # noqa: E402

srv, BASE = mock.start()
os.environ['CF_API_BASE'] = BASE
os.environ['CLOUDFLARE_API_TOKEN'] = mock.GOOD
import json, tempfile  # noqa: E401,E402
os.environ['CODERUNNER_CONFIG'] = tempfile.mkdtemp()      # never the real cloudflare.json of this computer
with open(os.path.join(os.environ['CODERUNNER_CONFIG'], 'cloudflare.json'), 'w') as f:
    json.dump({'api_token': '', 'account_id': mock.ACCT, 'tunnel_id': mock.TUN}, f)
from coderunner import cloudflare, config  # noqa: E402

ok = True
S = mock.STATE


def check(label, cond, extra=''):
    global ok
    ok &= bool(cond)
    print(('PASS ' if cond else 'FAIL ') + label + ((' | ' + str(extra)[:300]) if not cond and extra else ''))


rows = {r['host']: r for r in cloudflare.state(['api.example.com', 'a.b.example.com', 'old.example.com', 'example.com', 'x.example.org'])[3]}
check('one-level host covered by the wildcard (route and DNS)', rows['api.example.com']['route'] == 'wildcard' and rows['api.example.com']['dns'] == 'wildcard')
check('two-level host: missing and flagged', rows['a.b.example.com']['route'] == 'missing' and rows['a.b.example.com']['dns'] == 'missing' and rows['a.b.example.com']['multilevel'])
check('existing A record is a conflict', rows['old.example.com']['dns'] == 'conflict' and 'A 1.2.3.4' in rows['old.example.com']['detail'])
check('apex is explicit and ok', rows['example.com']['route'] == 'explicit' and rows['example.com']['dns'] == 'ok')
check('second zone resolved', rows['x.example.org']['zone'] == 'example.org')
check('list default = every tunnel hostname', [r['host'] for r in cloudflare.state()[3]] == ['example.com', 'www.example.com', '*.example.com'])

out = cloudflare.map_hosts(['a.b.example.com', 'newapp.example.org', 'old.example.com'])
ing = S['config']['ingress']
check('routes added only where missing', {'hostname': 'a.b.example.com', 'service': 'http://127.0.0.1:80'} in ing and {'hostname': 'newapp.example.org', 'service': 'http://127.0.0.1:80'} in ing and not any(r.get('hostname') == 'old.example.com' for r in ing))
check('catch-all stays last and unique', ing[-1] == {'service': 'http_status:404'} and sum(1 for r in ing if 'hostname' not in r) == 1, ing)
check('other tunnel config preserved', S['config']['originRequest'] == {'noTLSVerify': False} and ing[0]['hostname'] == 'example.com')
made = [r for r in S['records'] if r['name'] in ('a.b.example.com', 'newapp.example.org')]
check('proxied CNAMEs to the tunnel created', len(made) == 2 and all(r['type'] == 'CNAME' and r['content'] == mock.TARGET and r['proxied'] is True for r in made), made)
check('conflicting record untouched and reported', any(r['id'] == 'r5' for r in S['records']) and any('NOT changed' in l and 'old.example.com' in l for l in out), out)

n = len(S['calls'])
out2 = cloudflare.map_hosts(['a.b.example.com', 'newapp.example.org'])
check('mapping again is idempotent (no writes)', out2 == ['Nothing to change: already mapped'] and not any(c[0] in ('PUT', 'POST') for c in S['calls'][n:]), out2)

out3 = cloudflare.unmap_hosts(['a.b.example.com', 'newapp.example.org', 'old.example.com'])
check('unmap removes only those routes and CNAMEs', len(out3) == 4 and mock.pristine(), (out3, S['config']['ingress']))
check('unmap never touches the wildcard, apex, TXT or foreign records', {r['id'] for r in S['records']} == {'r1', 'r2', 'r3', 'r4', 'r5'})
check('unmapping nothing says so', cloudflare.unmap_hosts(['nothing.example.com']) == ['Nothing to remove in Cloudflare'])

try:
    cloudflare.state(['x.nope.example'])
    check('unknown zone raises', False)
except RuntimeError as e:
    check('unknown zone: clear error', 'No Cloudflare zone' in str(e), e)


def raises(fn, text):
    try:
        fn()
    except ValueError as e:
        return text in str(e)
    return False


check('replace needs the typed hostname', raises(lambda: cloudflare.replace_dns('old.example.com', 'nope'), 'Type the hostname'))
check('replace refuses wildcards', raises(lambda: cloudflare.replace_dns('*.example.com', '*.example.com'), 'Wildcard'))
check('replace refuses a host that has no conflict', raises(lambda: cloudflare.replace_dns('example.com', 'example.com'), 'no conflicting'))
before = {r['id'] for r in S['records']}
res = cloudflare.replace_dns('old.example.com', 'old.example.com')
after = [r for r in S['records'] if r['name'] == 'old.example.com']
check('replace removes the old A record (the wildcard CNAME then covers the name)', 'r5' not in {r['id'] for r in S['records']} and not after, (res, after))
check('replace reports what it removed', any('removed' in l and 'A 1.2.3.4' in l for l in res) and res[-1] == 'Nothing to change: already mapped', res)
check('replace touched nothing else', before - {'r5'} <= {r['id'] for r in S['records']})


# ---- connect / disconnect from the screen (a temporary cloudflare.json, never the real one)
import json  # noqa: E402
import tempfile  # noqa: E402
REAL_CF_FILE = config.CF_FILE
config.CF_FILE = os.path.join(tempfile.mkdtemp(), 'cloudflare.json')
env_token = os.environ.pop('CLOUDFLARE_API_TOKEN')
check('not connected without a token', cloudflare.connection() == {'connected': False, 'source': '', 'account_id': '', 'tunnel_id': ''}, cloudflare.connection())
check('a malformed token is refused before any request', raises(lambda: cloudflare.discover('short'), 'does not look like'))
try:
    cloudflare.discover('x' * 30)
    check('a wrong token is rejected by Cloudflare', False)
except RuntimeError as e:
    check('a wrong token is rejected by Cloudflare', 'Invalid API Token' in str(e) and 'x' * 30 not in str(e), e)
d = cloudflare.discover(mock.GOOD)
check('discover lists the account and its tunnels (one account is picked for you)', d['account_id'] == mock.ACCT and d['accounts'][0]['name'] == 'Test account' and d['tunnels'][0]['id'] == mock.TUN, d)
check('discover with a typed account id', cloudflare.discover(mock.GOOD, mock.ACCT)['tunnels'][0]['name'] == 'coderunner')
check('a bad account id is refused', raises(lambda: cloudflare.discover(mock.GOOD, 'nope'), 'account id'))
check('nothing was saved by discovering', not os.path.exists(config.CF_FILE))
check('connect refuses a bad tunnel id', raises(lambda: cloudflare.connect(mock.GOOD, mock.ACCT, 'nope'), 'looks wrong'))
try:
    cloudflare.connect(mock.GOOD, mock.ACCT, '00000000-0000-0000-0000-000000000000')
    check('connect proves the tunnel exists', False)
except RuntimeError:
    check('connect proves the tunnel exists', not os.path.exists(config.CF_FILE))
c = cloudflare.connect(mock.GOOD, mock.ACCT, mock.TUN)
saved = json.load(open(config.CF_FILE))
check('connect saves the file and never returns the token', c == {'connected': True, 'source': 'file', 'account_id': mock.ACCT, 'tunnel_id': mock.TUN} and saved['api_token'] == mock.GOOD and mock.GOOD not in json.dumps(c), c)
check('the saved file is owner-only', os.name == 'nt' or oct(os.stat(config.CF_FILE).st_mode & 0o777) == '0o600')
check('disconnect forgets the token', cloudflare.disconnect()['connected'] is False and not os.path.exists(config.CF_FILE))
check('disconnecting twice is harmless', cloudflare.disconnect()['connected'] is False)
os.environ['CLOUDFLARE_API_TOKEN'] = mock.GOOD
check('an environment token is shown as such', cloudflare.connection()['source'] == 'environment')
check('connect and disconnect refuse while the environment sets the token', raises(lambda: cloudflare.connect(mock.GOOD, mock.ACCT, mock.TUN), 'environment') and raises(cloudflare.disconnect, 'environment'))
os.environ['CLOUDFLARE_API_TOKEN'] = env_token
config.CF_FILE = REAL_CF_FILE

os.environ['CLOUDFLARE_API_TOKEN'] = 'wrong-token-VALUE-999'
try:
    cloudflare.state(['api.example.com'])
    check('bad token raises', False)
except RuntimeError as e:
    check('bad token: clear error, token not leaked', 'Invalid API Token' in str(e) and 'wrong-token' not in str(e), e)
os.environ['CLOUDFLARE_API_TOKEN'] = mock.GOOD

v = cloudflare.verify()
check('verify: token active, tunnel healthy', v['token'] == 'active' and v['tunnel'] == {'name': 'coderunner', 'status': 'healthy', 'connections': 4}, v)

for bad in ('a b.example.com', 'x;rm.example.com', 'nodot', ''):
    try:
        config.clean_hosts([bad] if bad else '')
        check('clean_hosts rejects %r' % bad, False)
    except ValueError:
        check('clean_hosts rejects %r' % bad, True)
check('clean_hosts accepts newline separated input', config.clean_hosts('a.example.com\nb.example.com') == ['a.example.com', 'b.example.com'])

print('\nALL PASSED' if ok else '\nSOME FAILED')
sys.exit(0 if ok else 1)
