"""Checks of sign-in mode (accounts, roles, sessions, throttling, headers). No SSH needed.

    python tests/test_auth.py
"""
import http.client
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADMIN = 'admin@example.com'
ok = True


def check(label, cond, extra=''):
    global ok
    ok &= bool(cond)
    print(('PASS ' if cond else 'FAIL ') + label + ((' | ' + str(extra)[:300]) if not cond and extra else ''))


def start(port, **extra_env):
    data = tempfile.mkdtemp(prefix='cr-auth-test-')
    env = dict(os.environ, CODERUNNER_AUTH='login', CODERUNNER_DATA=data, CODERUNNER_ADMIN_EMAIL=ADMIN, **extra_env)
    for k in ('CODERUNNER_TOKEN', 'CLOUDFLARE_API_TOKEN'):
        env.pop(k, None)
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, 'app.py'), '--no-browser', '--port', str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True)
    p.stdout.readline(); p.stdout.readline()
    return p, data


class Browser:
    """Minimal HTTP client with its own cookie jar (the stdlib jar refuses Secure cookies over http)."""

    def __init__(self, port, host=None):
        self.port, self.cookies, self.host = port, {}, host or '127.0.0.1:%d' % port
        self.last_headers = {}

    def req(self, method, path, body=None, headers=None, csrf=True, bearer=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=60)
        h = {'Host': self.host}
        if self.cookies:
            h['Cookie'] = '; '.join('%s=%s' % kv for kv in self.cookies.items())
        if body is not None:
            h['Content-Type'] = 'application/json'
            if csrf and not bearer:
                h['X-Requested-With'] = 'coderunner'
        if bearer:
            h['Authorization'] = 'Bearer ' + bearer
        h.update(headers or {})
        c.request(method, path, body=None if body is None else json.dumps(body), headers=h)
        r = c.getresponse()
        data = r.read()
        self.last_headers = {k.lower(): v for k, v in r.getheaders()}
        for k, v in r.getheaders():
            if k.lower() == 'set-cookie':
                name, _, rest = v.partition('=')
                val = rest.split(';')[0]
                if 'Max-Age=0' in v or not val:
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = val
        try:
            parsed = json.loads(data)
        except ValueError:
            parsed = data
        return r.status, parsed

    def get(self, path, **kw):
        return self.req('GET', path, **kw)

    def post(self, path, body=None, **kw):
        return self.req('POST', path, body if body is not None else {}, **kw)


def sql(data, q, args=()):
    c = sqlite3.connect(os.path.join(data, 'users.db'))
    try:
        r = c.execute(q, args).fetchall()
        c.commit()
        return r
    finally:
        c.close()


def code_from(data):
    return re.search(r'Activation code: (\S+)', open(os.path.join(data, 'first-admin-code.txt')).read()).group(1)



def totp_now(secret, offset=0):
    """Independent RFC 6238 implementation (SHA-1, 30 s, 6 digits) to check the app's codes against."""
    import base64
    import hashlib
    import hmac
    import struct
    d = hmac.new(base64.b32decode(secret), struct.pack('>Q', int(time.time() // 30) + offset), hashlib.sha1).digest()
    o = d[-1] & 15
    return '%06d' % ((struct.unpack('>I', d[o:o + 4])[0] & 0x7fffffff) % 10 ** 6)


def fresh(data):
    sql(data, 'update users set totp_last=0')   # forget the replay marker so one test can use the same 30 s code again


proc, DATA = start(8798)
proc2 = None
try:
    anon = Browser(8798)

    # ------------------------------------------------ before anyone signs in
    s, b = anon.get('/')
    check('sign-in shell is public', s == 200 and b'id="app"' in b)
    check('app files are public (needed by the sign-in page)', anon.get('/static/js/main.js')[0] == 200 and anon.get('/static/app.css')[0] == 200)
    check('the manual needs sign-in', anon.get('/static/manual.md')[0] == 401)
    s, b = anon.get('/api/status')
    check('API says sign in required', s == 401 and b.get('auth') == 'login', b)
    s, b = anon.get('/api/auth/me')
    check('me: not signed in, mode is login', s == 200 and b['auth'] == 'login' and b['authenticated'] is False and b['user'] is None, b)
    anon.get('/?t=guess')
    check('the launch-token link does nothing in login mode', anon.get('/api/status')[0] == 401 and 'cr' not in anon.cookies)
    check('wrong Host header refused', Browser(8798, host='evil.example').get('/api/auth/me')[0] == 403)
    check('uploads need sign-in', anon.post('/api/upload', {})[0] in (401, 403))

    # ------------------------------------------------------ first-time setup
    check('first admin seeded with a code file (mode 600 on unix)', os.path.isfile(os.path.join(DATA, 'first-admin-code.txt')))
    rows = sql(DATA, 'select email, role, pw_hash from users')
    check('only the whitelisted super admin exists, no password yet', rows == [(ADMIN, 'super_admin', None)], rows)
    s, b = anon.post('/api/auth/login', {'email': ADMIN, 'password': 'whatever-123!'})
    check('cannot sign in before setting a password', s == 400 and b['error'] == 'Wrong email or password', b)
    code = code_from(DATA)
    s, b = anon.post('/api/auth/activate', {'email': ADMIN, 'code': 'AAAA-BBBB-CCCC-DDDD', 'password': 'Sup3r-secret-pass'})
    check('wrong activation code refused', s == 400 and 'not valid' in b['error'], b)
    s, b = anon.post('/api/auth/activate', {'email': 'someone@else.com', 'code': code, 'password': 'Sup3r-secret-pass'})
    check('code is useless for another email (same message)', s == 400 and 'not valid' in b['error'], b)
    s, b = anon.post('/api/auth/activate', {'email': ADMIN, 'code': code, 'password': 'short1!'})
    check('weak password refused', s == 400 and '10 characters' in b['error'], b)
    s, b = anon.post('/api/auth/activate', {'email': ADMIN, 'code': code, 'password': 'aaaaaaaaaaaa'})
    check('weak password does not burn the code', s == 400 and code_from(DATA) == code and sql(DATA, 'select code_hash from users')[0][0] is not None, b)
    s, b = anon.post('/api/auth/activate', {'email': ADMIN, 'code': code.lower().replace('-', ' '), 'password': 'Sup3r-secret-pass'}, csrf=False)
    check('activation needs the CSRF header', s == 403)
    admin = Browser(8798)
    s, b = admin.post('/api/auth/activate', {'email': ADMIN, 'code': code.lower().replace('-', ' '), 'password': 'Sup3r-secret-pass'})
    check('right code + strong password activates and signs in', s == 200 and b['user']['role'] == 'super_admin' and 'cr_session' in admin.cookies, b)
    check('code file is removed after activation', not os.path.exists(os.path.join(DATA, 'first-admin-code.txt')))
    s, b = anon.post('/api/auth/activate', {'email': ADMIN, 'code': code, 'password': 'An0ther-secret-pass'})
    check('the code works only once', s == 400, b)
    pw_row = sql(DATA, 'select pw_hash from users')[0][0]
    check('password stored as a salted scrypt hash, never plain', pw_row.startswith('scrypt$') and 'Sup3r' not in pw_row)
    sess_rows = sql(DATA, 'select token_hash from sessions')
    check('session stored hashed', len(sess_rows) == 1 and admin.cookies['cr_session'] not in sess_rows[0][0] and len(sess_rows[0][0]) == 64)

    # ------------------------------------------------------------- signed in
    s, b = admin.get('/api/auth/me')
    check('me: signed in as super admin', b['authenticated'] and b['user']['email'] == ADMIN and b['user']['role'] == 'super_admin', b)
    check('manual available once signed in', admin.get('/static/manual.md')[0] == 200)
    s, b = admin.get('/api/session')
    check('session reports login mode and the account', s == 200 and b['auth'] == 'login' and b['me']['email'] == ADMIN, b)
    check('state-changing request without the CSRF header refused', admin.post('/api/auth/logout', csrf=False)[0] == 403)
    check('security headers present', all(k in admin.last_headers for k in ('content-security-policy', 'x-frame-options', 'x-content-type-options', 'referrer-policy')) and admin.last_headers['x-frame-options'] == 'DENY')
    check('CSP blocks inline scripts and framing', "script-src 'self'" in admin.last_headers['content-security-policy'] and "frame-ancestors 'none'" in admin.last_headers['content-security-policy'])

    # ------------------------------------------------ wrong passwords + lockout
    probe = Browser(8798)
    msgs = set()
    for i in range(4):
        s, b = probe.post('/api/auth/login', {'email': ADMIN, 'password': 'wrong-password-%d' % i})
        msgs.add((s, b.get('error')))
    s, b = probe.post('/api/auth/login', {'email': 'nobody@example.com', 'password': 'wrong-password-x'})
    msgs.add((s, b.get('error')))
    check('wrong password and unknown email give the same message', msgs == {(400, 'Wrong email or password')}, msgs)
    s, b = probe.post('/api/auth/login', {'email': ADMIN, 'password': 'wrong-password-5'})
    check('5th failure still just fails', s == 400, b)
    s, b = probe.post('/api/auth/login', {'email': ADMIN, 'password': 'Sup3r-secret-pass'})
    check('locked out after 5 failures, even with the right password', s == 429 and 'Too many' in b['error'], b)
    sql(DATA, 'delete from attempts')
    s, b = probe.post('/api/auth/login', {'email': ADMIN, 'password': 'Sup3r-secret-pass'})
    check('sign-in works again once the window passes', s == 200 and 'cr_session' in probe.cookies, b)
    check('a different email is not locked by another one', Browser(8798).post('/api/auth/login', {'email': 'x@example.com', 'password': 'nope-nope-1!'})[0] == 400)

    # ------------------------------------------------------------ users + roles
    check('admin page API lists users', admin.get('/api/users')[1]['users'][0]['email'] == ADMIN)
    s, b = admin.post('/api/users/create', {'email': 'not-an-email', 'role': 'admin'})
    check('bad email refused', s == 400, b)
    s, b = admin.post('/api/users/create', {'email': ADMIN.upper(), 'role': 'admin'})
    check('duplicate email refused (case-insensitive)', s == 400, b)
    s, b = admin.post('/api/users/create', {'email': 'viewer@example.com', 'role': 'viewer'})
    check('viewer created with a one-time code', s == 200 and re.match(r'^[A-Z2-9]{4}(-[A-Z2-9]{4}){3}$', b['code']), b)
    vcode = b['code']
    s, b = admin.post('/api/users/create', {'email': 'ops@example.com', 'role': 'admin'})
    acode = b['code']
    check('the activation code is stored hashed', vcode.replace('-', '') not in json.dumps(sql(DATA, 'select code_hash from users')))
    viewer, ops = Browser(8798), Browser(8798)
    check('viewer activates and signs in', viewer.post('/api/auth/activate', {'email': 'viewer@example.com', 'code': vcode, 'password': 'Viewer-pass-12'})[0] == 200)
    check('admin activates and signs in', ops.post('/api/auth/activate', {'email': 'ops@example.com', 'code': acode, 'password': 'Ops-secret-pass1'})[0] == 200)
    check('viewer can read', viewer.get('/api/activity')[0] == 200 and viewer.get('/api/mapping')[0] == 200)
    for path in ('/api/logs?name=site', '/api/databases', '/api/server/info', '/api/users', '/api/keys'):
        s, b = viewer.get(path)
        check('viewer cannot GET ' + path, s == 403 and 'role' in b['error'], b)
    for path in ('/api/action', '/api/deploy', '/api/site/env', '/api/databases/create', '/api/server/reboot', '/api/users/create', '/api/mapping/map'):
        s, b = viewer.post(path, {})
        check('viewer cannot POST ' + path, s == 403 and 'role' in b['error'], b)
    check('viewer cannot download backups or upload', viewer.get('/api/databases/download?file=x')[0] == 403 and viewer.post('/api/upload', {})[0] == 403)
    for path in ('/api/users', '/api/keys'):
        check('admin cannot GET ' + path, ops.get(path)[0] == 403)
    for path in ('/api/users/create', '/api/server/reboot', '/api/config', '/api/keys/create', '/api/test-connection'):
        check('admin cannot POST ' + path, ops.post(path, {})[0] == 403)
    check('admin passes the role check on its own endpoints', ops.post('/api/databases/create', {'name': 'Bad-Name'})[0] == 400 and ops.post('/api/auth/change-password', {'old': 'x', 'new': 'y'})[0] == 400)
    check('routes list shows required roles', any(r['path'] == '/api/users' and r['role'] == 'super_admin' for r in admin.get('/api/routes')[1]['routes']))

    # ------------------------------------------- api keys keep working (admin)
    s, k = admin.post('/api/keys/create', {'name': 'phone'})
    bearer = Browser(8798)
    check('api key works without a cookie, as admin', bearer.get('/api/activity', bearer=k['token'])[0] == 200 and bearer.get('/api/users', bearer=k['token'])[0] == 403)

    # ------------------------------------------ disable / reset / delete rules
    vid = [u for u in admin.get('/api/users')[1]['users'] if u['email'] == 'viewer@example.com'][0]['id']
    check('viewer session is valid before disabling', viewer.get('/api/activity')[0] == 200)
    check('disable a user', admin.post('/api/users/update', {'id': vid, 'status': 'disabled'})[0] == 200)
    check('disabled user is signed out at once', viewer.get('/api/activity')[0] == 401)
    check('disabled user cannot sign in', Browser(8798).post('/api/auth/login', {'email': 'viewer@example.com', 'password': 'Viewer-pass-12'})[0] == 400)
    admin.post('/api/users/update', {'id': vid, 'status': 'active'})
    check('enabled again: can sign in', Browser(8798).post('/api/auth/login', {'email': 'viewer@example.com', 'password': 'Viewer-pass-12'})[0] == 200)
    s, b = admin.post('/api/users/reset-code', {'id': vid})
    check('reset gives a new code and signs the user out everywhere', s == 200 and 'code' in b and viewer.get('/api/activity')[0] == 401)
    check('old password stops working only after the new one is set', Browser(8798).post('/api/auth/login', {'email': 'viewer@example.com', 'password': 'Viewer-pass-12'})[0] == 200)
    check('new code sets a new password', Browser(8798).post('/api/auth/activate', {'email': 'viewer@example.com', 'code': b['code'], 'password': 'Viewer-new-pass-9'})[0] == 200)
    check('old password no longer works', Browser(8798).post('/api/auth/login', {'email': 'viewer@example.com', 'password': 'Viewer-pass-12'})[0] == 400)
    me_id = admin.get('/api/users')[1]['me']
    check('cannot delete yourself', admin.post('/api/users/delete', {'id': me_id})[0] == 400)
    check('cannot disable yourself', admin.post('/api/users/update', {'id': me_id, 'status': 'disabled'})[0] == 400)
    check('cannot demote the only super admin', admin.post('/api/users/update', {'id': me_id, 'role': 'admin'})[0] == 400)
    s, b = admin.post('/api/users/create', {'email': 'second@example.com', 'role': 'super_admin'})
    sid = [u for u in admin.get('/api/users')[1]['users'] if u['email'] == 'second@example.com'][0]['id']
    check('a second super admin can be added and promoted', s == 200)
    check('delete another user', admin.post('/api/users/delete', {'id': sid})[0] == 200 and admin.post('/api/users/delete', {'id': 9999})[0] == 404)
    check('role change works', admin.post('/api/users/update', {'id': vid, 'role': 'admin'})[0] == 200)

    # --------------------------------------------------- change password / logout
    s, b = ops.post('/api/auth/change-password', {'old': 'wrong-old-pass-1', 'new': 'Brand-new-pass-77'})
    check('change password: wrong current password refused', s == 400 and 'current password' in b['error'], b)
    s, b = ops.post('/api/auth/change-password', {'old': 'Ops-secret-pass1', 'new': 'weak'})
    check('change password: weak new password refused', s == 400, b)
    check('change password works', ops.post('/api/auth/change-password', {'old': 'Ops-secret-pass1', 'new': 'Brand-new-pass-77'})[0] == 200)
    check('new password signs in, old one does not', Browser(8798).post('/api/auth/login', {'email': 'ops@example.com', 'password': 'Brand-new-pass-77'})[0] == 200 and Browser(8798).post('/api/auth/login', {'email': 'ops@example.com', 'password': 'Ops-secret-pass1'})[0] == 400)
    old_cookie = dict(ops.cookies)
    check('logout ends the session', ops.post('/api/auth/logout')[0] == 200 and 'cr_session' not in ops.cookies)
    replay = Browser(8798)
    replay.cookies = old_cookie
    check('a copied cookie is dead after logout', replay.get('/api/activity')[0] == 401)

    # ------------------------------------------------------------- expiry
    sql(DATA, "update sessions set last = last - 13*3600 where user_id = ?", (me_id,))
    check('idle session (12h) expires', admin.get('/api/activity')[0] == 401)
    sql(DATA, 'delete from attempts')
    admin2 = Browser(8798)
    admin2.post('/api/auth/login', {'email': ADMIN, 'password': 'Sup3r-secret-pass'})
    sql(DATA, "update sessions set created = created - 31*86400 where user_id = ?", (me_id,))
    check('session older than 30 days expires', admin2.get('/api/activity')[0] == 401)

    # --------------------------------------------------------------- activity
    admin3 = Browser(8798)
    admin3.post('/api/auth/login', {'email': ADMIN, 'password': 'Sup3r-secret-pass'})
    ev = admin3.get('/api/activity')[1]['events']
    kinds = {e['kind'] for e in ev}
    blob = json.dumps(ev)
    check('activity records sign-ins, failures, user changes', {'login', 'login-failed', 'user-create', 'user-update', 'account-activated', 'password-changed'} <= kinds, kinds)
    check('activity never contains passwords or codes', 'Sup3r' not in blob and 'Viewer-' not in blob and vcode not in blob and acode not in blob)
    check('failed attempts record the client address', any(e['kind'] == 'login-failed' and e['detail'] for e in ev))

    # ------------------------------------------------------ recovery command
    rec_env = dict(os.environ, CODERUNNER_DATA=DATA)
    out = subprocess.run([sys.executable, os.path.join(ROOT, 'app.py'), '--reset-code', 'second@example.com'], capture_output=True, text=True, env=rec_env)
    check('recovery command refuses an unknown account', out.returncode != 0 and 'no account' in (out.stderr + out.stdout))
    out = subprocess.run([sys.executable, os.path.join(ROOT, 'app.py'), '--reset-code', ADMIN], capture_output=True, text=True, env=rec_env)
    rcode = re.search(r': ([A-Z2-9]{4}(?:-[A-Z2-9]{4}){3})', out.stdout)
    check('recovery command prints a one-time code', out.returncode == 0 and rcode, out.stdout + out.stderr)
    check('recovery signs the account out', Browser(8798).get('/api/activity')[0] == 401)
    rb = Browser(8798)
    check('the recovery code sets a new password', rb.post('/api/auth/activate', {'email': ADMIN, 'code': rcode.group(1), 'password': 'Recovered-pass-55'})[0] == 200)
    check('the old password no longer works', Browser(8798).post('/api/auth/login', {'email': ADMIN, 'password': 'Sup3r-secret-pass'})[0] == 400)

    # ------------------------------------------------------------ two-factor
    s, b = rb.post('/api/auth/2fa/start')
    check('2fa setup returns a secret and an otpauth link', s == 200 and len(b['secret']) == 32 and b['uri'].startswith('otpauth://totp/Coderunner:') and b['secret'] in b['uri'], b)
    secret = b['secret']
    check('2fa is not active until a code is checked', sql(DATA, 'select totp_on from users')[0][0] == 0 and Browser(8798).post('/api/auth/login', {'email': ADMIN, 'password': 'Recovered-pass-55'})[1].get('need_2fa') is None)
    s, b = rb.post('/api/auth/2fa/enable', {'code': '000000'})
    check('wrong first code refused', s == 400, b)
    s, b = rb.post('/api/auth/2fa/enable', {'code': totp_now(secret)})
    codes = b.get('codes') or []
    check('right code turns it on and returns 8 recovery codes', s == 200 and len(codes) == 8 and all(re.fullmatch(r'[A-Z2-9]{5}-[A-Z2-9]{5}', x) for x in codes), b)
    rec = sql(DATA, 'select recovery from users')[0][0]
    check('only hashes of the recovery codes are stored', codes[0].replace('-', '') not in rec and rec.count('"') == 16)
    check('me shows two-factor on', rb.get('/api/auth/me')[1]['user']['totp'] is True and rb.get('/api/auth/me')[1]['user']['recovery_left'] == 8)

    t1 = Browser(8798)
    s, b = t1.post('/api/auth/login', {'email': ADMIN, 'password': 'Recovered-pass-55'})
    chal = b.get('challenge')
    check('password alone no longer signs in: a challenge comes back, no cookie', s == 200 and b.get('need_2fa') is True and chal and 'cr_session' not in t1.cookies and t1.get('/api/status')[0] == 401, b)
    s, b = t1.post('/api/auth/login-2fa', {'challenge': chal, 'code': '123456'})
    check('wrong code refused', s == 400 and 'cr_session' not in t1.cookies, b)
    s, b = t1.post('/api/auth/login-2fa', {'challenge': 'nonsense', 'code': totp_now(secret, 1)})
    check('unknown challenge refused', s == 400 and 'expired' in b['error'], b)
    code_ = totp_now(secret, 1)
    s, b = t1.post('/api/auth/login-2fa', {'challenge': chal, 'code': code_})
    check('right code signs in', s == 200 and 'cr_session' in t1.cookies and t1.get('/api/status')[0] != 401, b)
    t2 = Browser(8798)
    chal2 = t2.post('/api/auth/login', {'email': ADMIN, 'password': 'Recovered-pass-55'})[1]['challenge']
    s, b = t2.post('/api/auth/login-2fa', {'challenge': chal2, 'code': code_})
    check('the same code cannot be used twice (replay)', s == 400 and 'cr_session' not in t2.cookies, b)
    fresh(DATA)
    s, b = t2.post('/api/auth/login-2fa', {'challenge': chal2, 'code': totp_now(secret)})
    check('a fresh code works on the second challenge', s == 200, b)
    check('a challenge is single use', Browser(8798).post('/api/auth/login-2fa', {'challenge': chal2, 'code': totp_now(secret, -1)})[0] == 400)

    t3 = Browser(8798)
    chal3 = t3.post('/api/auth/login', {'email': ADMIN, 'password': 'Recovered-pass-55'})[1]['challenge']
    s, b = t3.post('/api/auth/login-2fa', {'challenge': chal3, 'code': codes[0].lower()})
    check('a recovery code signs in', s == 200 and 'cr_session' in t3.cookies, b)
    chal4 = Browser(8798).post('/api/auth/login', {'email': ADMIN, 'password': 'Recovered-pass-55'})[1]['challenge']
    t4 = Browser(8798)
    s, b = t4.post('/api/auth/login-2fa', {'challenge': chal4, 'code': codes[0]})
    check('a recovery code works only once', s == 400, b)
    check('recovery codes left goes down', t3.get('/api/auth/me')[1]['user']['recovery_left'] == 7)

    s, b = t3.post('/api/auth/2fa/disable', {'password': 'wrong-password-1', 'code': codes[1]})
    check('turning 2fa off needs the password', s == 400 and sql(DATA, 'select totp_on from users')[0][0] == 1, b)
    s, b = t3.post('/api/auth/2fa/disable', {'password': 'Recovered-pass-55', 'code': '111111'})
    check('turning 2fa off needs a good code too', s == 400 and sql(DATA, 'select totp_on from users')[0][0] == 1, b)
    sql(DATA, 'delete from attempts')
    fresh(DATA)
    s, b = t3.post('/api/auth/2fa/disable', {'password': 'Recovered-pass-55', 'code': totp_now(secret)})
    check('password + code turns it off', s == 200 and sql(DATA, 'select totp_on, totp_secret from users')[0] == (0, None), b)
    s, b = Browser(8798).post('/api/auth/login', {'email': ADMIN, 'password': 'Recovered-pass-55'})
    check('sign-in is password-only again', s == 200 and 'user' in b and 'need_2fa' not in b, b)

    # a super admin can remove 2fa for someone who lost their phone
    s, b = rb.post('/api/auth/2fa/start')
    secret = b['secret']
    sql(DATA, 'delete from attempts')
    fresh(DATA)
    check('turn it on again', rb.post('/api/auth/2fa/enable', {'code': totp_now(secret)})[0] == 200)
    uid = sql(DATA, 'select id from users')[0][0]
    check('users list shows the 2FA flag', [u for u in rb.get('/api/users')[1]['users'] if u['id'] == uid][0]['totp'] is True)
    s, b = rb.post('/api/users/reset-2fa', {'id': uid})
    check('super admin reset removes 2fa and signs the person out', s == 200 and sql(DATA, 'select totp_on from users')[0][0] == 0 and rb.get('/api/status')[0] == 401, b)

    # a stolen password cannot be used to guess codes without limit
    rb = Browser(8798)
    rb.post('/api/auth/login', {'email': ADMIN, 'password': 'Recovered-pass-55'})
    rb.post('/api/auth/2fa/start')
    sql(DATA, "update users set totp_on=1, totp_secret='GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ' where email=?", (ADMIN,))
    sql(DATA, 'delete from attempts')
    last = None
    for i in range(7):
        g = Browser(8798)
        ch = g.post('/api/auth/login', {'email': ADMIN, 'password': 'Recovered-pass-55'})[1].get('challenge')
        if not ch:
            last = 429
            break
        last = g.post('/api/auth/login-2fa', {'challenge': ch, 'code': '%06d' % (100000 + i)})[0]
    check('wrong codes lock sign-in even though the password is right', last == 429, last)
    sql(DATA, "update users set totp_on=0, totp_secret=NULL where email=?", (ADMIN,))
    sql(DATA, 'delete from attempts')

    # ------------------------------------------------- require two-factor for everyone
    s, b = rb.post('/api/policy', {'require_2fa': True})
    check('cannot require 2fa before having it yourself', s == 400 and 'own two-factor' in b['error'], b)
    sql(DATA, 'delete from attempts')
    secret = rb.post('/api/auth/2fa/start')[1]['secret']
    fresh(DATA)
    check('super admin turns their own 2fa on', rb.post('/api/auth/2fa/enable', {'code': totp_now(secret)})[0] == 200)
    s, b = rb.post('/api/policy', {'require_2fa': True})
    check('policy can be switched on', s == 200 and b['require2fa'] is True, b)
    check('session tells the UI the policy is on', rb.get('/api/session')[1]['require2fa'] is True)

    s, b = rb.post('/api/users/create', {'email': 'ann@example.com', 'role': 'admin'})
    ann = Browser(8798)
    s, b = ann.post('/api/auth/activate', {'email': 'ann@example.com', 'code': b['code'], 'password': 'Ann-secret-pass-9'})
    check('new person can still sign in and set a password', s == 200 and 'cr_session' in ann.cookies, b)
    s, b = ann.get('/api/activity')
    check('without 2fa the app is closed to them', s == 403 and b.get('need_2fa_setup') is True, b)
    check('so are uploads, log streams and backup downloads', ann.post('/api/upload', {})[0] == 403 and ann.get('/api/logs/stream?kind=service&name=caddy')[0] == 403 and ann.get('/api/databases/download?file=x')[0] == 403)
    check('but sign-in state and setup stay reachable', ann.get('/api/auth/me')[1]['authenticated'] is True and ann.get('/api/session')[0] == 200)
    sql(DATA, 'delete from attempts')
    asecret = ann.post('/api/auth/2fa/start')[1]['secret']
    check('they can set 2fa up', ann.post('/api/auth/2fa/enable', {'code': totp_now(asecret)})[0] == 200)
    check('then the app opens', ann.get('/api/activity')[0] == 200)

    tok = rb.post('/api/keys/create', {'name': 'policy-test'})[1]['token']
    check('API keys are not affected by the policy', Browser(8798).get('/api/activity', bearer=tok)[0] == 200)
    check('policy can be switched off', rb.post('/api/policy', {'require_2fa': False})[0] == 200 and rb.get('/api/session')[1]['require2fa'] is False)
    sql(DATA, 'update users set totp_on=0, totp_secret=NULL, recovery=NULL')
    sql(DATA, 'delete from attempts')

    # ---------------------------------------------- secure-cookie deployment
    proc2, data2 = start(8797, CODERUNNER_SECURE_COOKIES='1', CODERUNNER_ALLOW_HOSTS='server.example.com', CODERUNNER_BEHIND_PROXY='1')
    b2 = Browser(8797, host='server.example.com')
    check('allowed hostname accepted', b2.get('/api/auth/me')[0] == 200)
    code2 = code_from(data2)
    s, b = b2.post('/api/auth/activate', {'email': ADMIN, 'code': code2, 'password': 'Sup3r-secret-pass'})
    ck = [v for k, v in [(k, v) for k, v in [('set-cookie', b2.last_headers.get('set-cookie', ''))]]][0]
    check('session cookie is HttpOnly, SameSite=Lax and Secure', 'HttpOnly' in ck and 'SameSite=Lax' in ck and 'Secure' in ck and ck.startswith('cr_session='), ck)
    check('HSTS header sent when secure', 'strict-transport-security' in b2.last_headers)
    b2.get('/api/auth/me')
    x = Browser(8797, host='server.example.com')
    for i in range(5):
        x.req('POST', '/api/auth/login', {'email': 'a@example.com', 'password': 'nope-nope-%d' % i}, headers={'CF-Connecting-IP': '203.0.113.7'})
    ev = b2.get('/api/activity')[1]['events']
    check('client address comes from CF-Connecting-IP behind the proxy', any(e['kind'] == 'login-failed' and '203.0.113.7' in e['detail'] for e in ev))
finally:
    proc.terminate()
    if proc2:
        proc2.terminate()

print('\nALL PASSED' if ok else '\nSOME FAILED')
sys.exit(0 if ok else 1)
