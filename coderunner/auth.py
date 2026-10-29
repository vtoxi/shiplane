"""Accounts, passwords, sessions and login throttling. SQLite + the standard library only.

* Only emails that exist in the users table can sign in (the whitelist is the table itself).
* A new account has no password. The owner of the email sets it once with a one-time activation code
  (shown to the admin who created the account, or written to data/first-admin-code.txt for the first super admin).
* Passwords are stored as salted scrypt hashes; sessions as SHA-256 hashes of random tokens.
"""
import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import struct
import threading
import time
from urllib.parse import quote

from . import config

ROLES = ('super_admin', 'admin', 'viewer')
RANK = {'viewer': 1, 'admin': 2, 'super_admin': 3}
EMAIL_RE = re.compile(r'^[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,190}\.[a-z]{2,}$')
SESSION_IDLE = 12 * 3600
SESSION_MAX = 30 * 86400
CODE_TTL = 7 * 86400
WINDOW = 600
LIMITS = {'login': 5, 'login-ip': 25, 'act': 5, 'act-ip': 25}
COMMON = {'password123', 'password1234', '1234567890', 'qwertyuiop', 'letmein123', 'admin12345', 'welcome123', 'iloveyou123'}
CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
_lock = threading.RLock()


@contextlib.contextmanager
def _db():
    os.makedirs(config.DATA, exist_ok=True)
    c = sqlite3.connect(os.path.join(config.DATA, 'users.db'), timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init(admin_email=''):
    """Create tables; make sure the first super admin exists and, until they set a password, has a fresh code."""
    with _lock, _db() as c:
        c.executescript('''
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, role TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active', pw_hash TEXT, code_hash TEXT, code_exp INTEGER, created TEXT, last_login TEXT);
            CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created INTEGER, last INTEGER, ip TEXT);
            CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, n INTEGER, first INTEGER);''')
        have = {r['name'] for r in c.execute('PRAGMA table_info(users)')}
        for col, ddl in (('totp_secret', 'TEXT'), ('totp_on', 'INTEGER DEFAULT 0'), ('totp_last', 'INTEGER DEFAULT 0'), ('recovery', 'TEXT'), ('sites', 'TEXT')):
            if col not in have:
                c.execute('ALTER TABLE users ADD COLUMN %s %s' % (col, ddl))
        email = normalize(admin_email) if admin_email else ''
        if email and not c.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
            c.execute('INSERT INTO users (email, role, created) VALUES (?, ?, ?)', (email, 'super_admin', _now()))
        for u in c.execute("SELECT id, email FROM users WHERE role='super_admin' AND pw_hash IS NULL").fetchall():
            row = c.execute('SELECT code_exp FROM users WHERE id=?', (u['id'],)).fetchone()
            path = os.path.join(config.DATA, 'first-admin-code.txt')
            if not row['code_exp'] or row['code_exp'] < time.time() or not os.path.isfile(path):
                code = _new_code(c, u['id'])
                with open(path, 'w', encoding='utf-8') as f:
                    f.write('Account: %s\nActivation code: %s\nValid for 7 days. Open the app, choose "Set up your account" and use this code.\n' % (u['email'], code))
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
        c.execute('DELETE FROM sessions WHERE created < ?', (time.time() - SESSION_MAX,))


def _now():
    return time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())


def normalize(email):
    email = str(email or '').strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError('That does not look like an email address')
    return email


def _hash_token(t):
    return hashlib.sha256(t.encode()).hexdigest()


def _new_code(c, user_id):
    raw = ''.join(secrets.choice(CODE_CHARS) for _ in range(16))
    code = '-'.join(raw[i:i + 4] for i in range(0, 16, 4))
    c.execute('UPDATE users SET code_hash=?, code_exp=? WHERE id=?', (_hash_token(raw), int(time.time()) + CODE_TTL, user_id))
    return code


# ------------------------------------------------------------------- passwords
def hash_password(pw):
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return 'scrypt$16384$8$1$%s$%s' % (salt.hex(), h.hex())


def verify_password(pw, stored):
    try:
        _, n, r, p, salt, h = stored.split('$')
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, AttributeError):
        return False


def check_password(pw, email=''):
    if not isinstance(pw, str) or len(pw) < 10:
        raise ValueError('Use at least 10 characters')
    if len(pw) > 200:
        raise ValueError('That password is too long')
    if pw.lower() in COMMON or len(set(pw)) < 5:
        raise ValueError('That password is too easy to guess')
    if email and (pw.lower() == email.lower() or pw.lower() == email.split('@')[0].lower()):
        raise ValueError('The password cannot be your email')
    if not (re.search(r'[A-Za-z]', pw) and re.search(r'[^A-Za-z]', pw)):
        raise ValueError('Mix letters with numbers or symbols')


# ------------------------------------------------------------------ throttling
def _locked(c, key):
    row = c.execute('SELECT n, first FROM attempts WHERE key=?', (key,)).fetchone()
    if not row:
        return 0
    if time.time() - row['first'] > WINDOW:
        c.execute('DELETE FROM attempts WHERE key=?', (key,))
        return 0
    limit = LIMITS[key.split(':', 1)[0]]
    return int(WINDOW - (time.time() - row['first'])) if row['n'] >= limit else 0


def _fail(c, key):
    row = c.execute('SELECT n, first FROM attempts WHERE key=?', (key,)).fetchone()
    if row and time.time() - row['first'] <= WINDOW:
        c.execute('UPDATE attempts SET n=n+1 WHERE key=?', (key,))
    else:
        c.execute('INSERT OR REPLACE INTO attempts (key, n, first) VALUES (?, 1, ?)', (key, int(time.time())))


def _guard(c, kind, email, ip):
    for key in ('%s:%s' % (kind, email), '%s-ip:%s' % (kind, ip)):
        wait = _locked(c, key)
        if wait:
            raise PermissionError('Too many attempts. Try again in %d minutes.' % max(1, wait // 60 + 1))


def _fail_both(c, kind, email, ip):
    _fail(c, '%s:%s' % (kind, email))
    _fail(c, '%s-ip:%s' % (kind, ip))


# ----------------------------------------------------------- sessions / login
def login(email, password, ip='?'):
    """Returns (session_token, user), or (None, {'challenge': ...}) when a 2FA code is still needed. Raises ValueError (wrong credentials) or PermissionError (throttled)."""
    try:
        email = normalize(email)
    except ValueError:
        email = '?'
    result = None
    with _lock, _db() as c:
        _guard(c, 'login', email, ip)
        u = c.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        good = bool(u and u['pw_hash'] and u['status'] == 'active' and verify_password(str(password), u['pw_hash']))
        if not u or not u['pw_hash']:
            verify_password(str(password), 'scrypt$16384$8$1$' + '00' * 16 + '$' + '00' * 32)  # same cost, no timing hint
        if good and u['totp_on']:
            result = (None, {'challenge': _challenge(u['id'])})   # the lockout counter stays until the code is right
        elif good:
            c.execute('DELETE FROM attempts WHERE key=?', ('login:' + email,))
            result = (_session(c, u, ip), _public(u))
        else:
            _fail_both(c, 'login', email, ip)
    if not result:
        raise ValueError('Wrong email or password')
    return result


def session_user(token):
    if not token:
        return None
    with _lock, _db() as c:
        row = c.execute('SELECT s.token_hash, s.created, s.last, u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=?', (_hash_token(token),)).fetchone()
        now = time.time()
        if not row or row['status'] != 'active' or now - row['last'] > SESSION_IDLE or now - row['created'] > SESSION_MAX:
            if row:
                c.execute('DELETE FROM sessions WHERE token_hash=?', (row['token_hash'],))
            return None
        if now - row['last'] > 60:
            c.execute('UPDATE sessions SET last=? WHERE token_hash=?', (int(now), row['token_hash']))
        return _public(row)


def logout(token):
    with _lock, _db() as c:
        c.execute('DELETE FROM sessions WHERE token_hash=?', (_hash_token(token or ''),))


def activate(email, code, password, ip='?'):
    try:
        email = normalize(email)
    except ValueError:
        email = '?'
    user = None
    with _lock, _db() as c:
        _guard(c, 'act', email, ip)
        u = c.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        raw = re.sub(r'[^A-Z0-9]', '', str(code or '').upper())
        good = bool(u and u['status'] == 'active' and u['code_hash'] and u['code_exp'] and u['code_exp'] > time.time() and hmac.compare_digest(u['code_hash'], _hash_token(raw)))
        if not good:
            _fail_both(c, 'act', email, ip)
        else:
            check_password(password, email)  # a weak password raises before anything changes and does not burn the code
            c.execute('UPDATE users SET pw_hash=?, code_hash=NULL, code_exp=NULL WHERE id=?', (hash_password(password), u['id']))
            c.execute('DELETE FROM sessions WHERE user_id=?', (u['id'],))
            c.execute('DELETE FROM attempts WHERE key IN (?, ?)', ('act:' + email, 'login:' + email))
            user = _public(u)
            path = os.path.join(config.DATA, 'first-admin-code.txt')
            if u['role'] == 'super_admin' and os.path.isfile(path):
                os.remove(path)
    if not user:
        raise ValueError('That email or activation code is not valid (or it has expired)')
    return user


def change_password(user_id, old, new):
    with _lock, _db() as c:
        u = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not u or not u['pw_hash'] or not verify_password(str(old), u['pw_hash']):
            raise ValueError('Your current password is not right')
        check_password(new, u['email'])
        c.execute('UPDATE users SET pw_hash=? WHERE id=?', (hash_password(new), user_id))


# ------------------------------------------------------------------- two-factor
# TOTP (RFC 6238: SHA-1, 30 s, 6 digits) from the standard library. A correct password with 2FA on only buys a short-lived
# "challenge"; the session starts after the code. Wrong codes count against the same lockout as wrong passwords, and the
# counter is not reset by a correct password, so a stolen password cannot be used to guess codes without limit.
_CHAL = {}   # challenge token -> (user id, expiry); in memory, so a restart just asks people to sign in again


def _challenge(user_id):
    now = time.time()
    for k in [k for k, v in _CHAL.items() if v[1] < now]:
        del _CHAL[k]
    t = secrets.token_urlsafe(24)
    _CHAL[t] = (user_id, now + 300)
    return t


def _hotp(secret, step):
    d = hmac.new(base64.b32decode(secret), struct.pack('>Q', step), 'sha1').digest()
    o = d[-1] & 15
    return '%06d' % ((struct.unpack('>I', d[o:o + 4])[0] & 0x7fffffff) % 10 ** 6)


def _totp_ok(c, u, code):
    """True for the current (or neighbouring) 30 s code, once: a step that was already used is refused."""
    raw = re.sub(r'\s', '', str(code or ''))
    if not u['totp_secret'] or not re.fullmatch(r'\d{6}', raw):
        return False
    now = int(time.time() // 30)
    for step in (now - 1, now, now + 1):
        if step > (u['totp_last'] or 0) and hmac.compare_digest(_hotp(u['totp_secret'], step), raw):
            c.execute('UPDATE users SET totp_last=? WHERE id=?', (step, u['id']))
            return True
    return False


def _recovery_ok(c, u, code):
    raw = re.sub(r'[^A-Z0-9]', '', str(code or '').upper())
    left = json.loads(u['recovery'] or '[]')
    h = _hash_token(raw)
    if not raw or h not in left:
        return False
    left.remove(h)
    c.execute('UPDATE users SET recovery=? WHERE id=?', (json.dumps(left), u['id']))
    return True


def _session(c, u, ip):
    token = secrets.token_urlsafe(32)
    c.execute('INSERT INTO sessions (token_hash, user_id, created, last, ip) VALUES (?, ?, ?, ?, ?)', (_hash_token(token), u['id'], int(time.time()), int(time.time()), ip))
    c.execute('UPDATE users SET last_login=? WHERE id=?', (_now(), u['id']))
    return token


def login_2fa(challenge, code, ip='?'):
    """Second sign-in step: an authenticator code or a one-time recovery code. Returns (session_token, user)."""
    got = _CHAL.get(str(challenge))
    if not got or got[1] < time.time():
        raise ValueError('That sign-in step expired. Sign in again.')
    result = None
    with _lock, _db() as c:
        u = c.execute('SELECT * FROM users WHERE id=?', (got[0],)).fetchone()
        if not u or u['status'] != 'active' or not u['totp_on']:
            raise ValueError('That sign-in step expired. Sign in again.')
        _guard(c, 'login', u['email'], ip)
        if _totp_ok(c, u, code) or _recovery_ok(c, u, code):
            _CHAL.pop(str(challenge), None)
            c.execute('DELETE FROM attempts WHERE key=?', ('login:' + u['email'],))
            result = (_session(c, u, ip), _public(u))
        else:
            _fail_both(c, 'login', u['email'], ip)
    if not result:
        raise ValueError('That code is not right')
    return result


def _user(c, user_id):
    u = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    if not u:
        raise LookupError('no such user')
    return u


def totp_start(user_id):
    """New secret, not active until a code from the app proves it was set up. Returns (secret, otpauth uri)."""
    with _lock, _db() as c:
        u = _user(c, user_id)
        if u['totp_on']:
            raise ValueError('Two-factor sign-in is already on')
        secret = base64.b32encode(os.urandom(20)).decode().rstrip('=')
        c.execute('UPDATE users SET totp_secret=?, totp_last=0 WHERE id=?', (secret, user_id))
        return secret, 'otpauth://totp/Coderunner:%s?secret=%s&issuer=Coderunner' % (quote(u['email']), secret)


def totp_enable(user_id, code, ip='?'):
    """Checks the first code, turns 2FA on and returns 8 one-time recovery codes (only their hashes are kept)."""
    codes = None
    with _lock, _db() as c:
        u = _user(c, user_id)
        if u['totp_on'] or not u['totp_secret']:
            raise ValueError('Start the setup first')
        _guard(c, 'login', u['email'], ip)
        if _totp_ok(c, u, code):
            raw = [''.join(secrets.choice(CODE_CHARS) for _ in range(10)) for _ in range(8)]
            c.execute('UPDATE users SET totp_on=1, recovery=? WHERE id=?', (json.dumps([_hash_token(r) for r in raw]), user_id))
            codes = ['%s-%s' % (r[:5], r[5:]) for r in raw]
        else:
            _fail_both(c, 'login', u['email'], ip)
    if not codes:
        raise ValueError('That code is not right. Check the clock on your phone, then try the next code.')
    return codes


def totp_disable(user_id, password, code, ip='?'):
    err = None
    with _lock, _db() as c:
        u = _user(c, user_id)
        if not u['totp_on']:
            raise ValueError('Two-factor sign-in is not on')
        _guard(c, 'login', u['email'], ip)
        if not verify_password(str(password), u['pw_hash'] or ''):
            err = 'Your password is not right'
        elif not (_totp_ok(c, u, code) or _recovery_ok(c, u, code)):
            err = 'That code is not right'
        if err:
            _fail_both(c, 'login', u['email'], ip)
        else:
            c.execute('UPDATE users SET totp_on=0, totp_secret=NULL, recovery=NULL, totp_last=0 WHERE id=?', (user_id,))
    if err:
        raise ValueError(err)


def totp_reset(user_id):
    """Super admin removes someone's 2FA (lost phone and recovery codes). They are signed out and sign in with the password alone."""
    with _lock, _db() as c:
        _user(c, user_id)
        c.execute('UPDATE users SET totp_on=0, totp_secret=NULL, recovery=NULL, totp_last=0 WHERE id=?', (user_id,))
        c.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))


# ----------------------------------------------------------------------- users
def _public(u):
    return {'id': u['id'], 'email': u['email'], 'role': u['role'], 'status': u['status'], 'created': u['created'],
            'last_login': u['last_login'], 'activated': bool(u['pw_hash']), 'totp': bool(u['totp_on']),
            'recovery_left': len(json.loads(u['recovery'] or '[]')), 'sites': json.loads(u['sites']) if u['sites'] else None}


def list_users():
    with _lock, _db() as c:
        return [_public(u) for u in c.execute('SELECT * FROM users ORDER BY id').fetchall()]


def create_user(email, role):
    email = normalize(email)
    if role not in ROLES:
        raise ValueError('Pick a role')
    with _lock, _db() as c:
        if c.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
            raise ValueError('That email already has an account')
        cur = c.execute('INSERT INTO users (email, role, created) VALUES (?, ?, ?)', (email, role, _now()))
        return _public(c.execute('SELECT * FROM users WHERE id=?', (cur.lastrowid,)).fetchone()), _new_code(c, cur.lastrowid)


def _supers(c):
    return c.execute("SELECT COUNT(*) FROM users WHERE role='super_admin' AND status='active'").fetchone()[0]


NAME_RE = re.compile(r'^[a-z][a-z0-9-]{0,40}$')
ALL_SITES = object()


def check_sites(sites):
    """None = every site; otherwise a list of site names (a user limited to those sites, whatever their role short of super admin)."""
    if sites is None:
        return None
    if not isinstance(sites, list) or len(sites) > 200 or any(not isinstance(x, str) or not NAME_RE.match(x) for x in sites):
        raise ValueError('Sites must be a list of site names')
    return sorted(set(sites))


def update_user(user_id, actor_id, role=None, status=None, sites=ALL_SITES):
    with _lock, _db() as c:
        u = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not u:
            raise LookupError('no such user')
        if role is not None:
            if role not in ROLES:
                raise ValueError('Pick a role')
            if u['role'] == 'super_admin' and role != 'super_admin' and _supers(c) <= 1:
                raise ValueError('There must be at least one active super admin')
            c.execute('UPDATE users SET role=? WHERE id=?', (role, user_id))
        if sites is not ALL_SITES:
            new = check_sites(sites)
            if new is not None and (role or u['role']) == 'super_admin':
                raise ValueError('A super admin always has every site')
            c.execute('UPDATE users SET sites=? WHERE id=?', (json.dumps(new) if new is not None else None, user_id))
        if status is not None:
            if status not in ('active', 'disabled'):
                raise ValueError('Bad status')
            if status == 'disabled' and (user_id == actor_id or (u['role'] == 'super_admin' and _supers(c) <= 1)):
                raise ValueError('You cannot disable yourself or the last super admin')
            c.execute('UPDATE users SET status=? WHERE id=?', (status, user_id))
            if status == 'disabled':
                c.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))


def reset_code(user_id):
    with _lock, _db() as c:
        if not c.execute('SELECT 1 FROM users WHERE id=?', (user_id,)).fetchone():
            raise LookupError('no such user')
        code = _new_code(c, user_id)
        c.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        return code


def delete_user(user_id, actor_id):
    with _lock, _db() as c:
        u = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not u:
            raise LookupError('no such user')
        if user_id == actor_id:
            raise ValueError('You cannot delete your own account')
        if u['role'] == 'super_admin' and _supers(c) <= 1:
            raise ValueError('There must be at least one active super admin')
        c.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        c.execute('DELETE FROM users WHERE id=?', (user_id,))


def reset_code_for_email(email):
    """Recovery from the server's command line: a fresh activation code for an existing account."""
    email = normalize(email)
    with _lock, _db() as c:
        u = c.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
        if not u:
            raise LookupError('no account with that email')
        code = _new_code(c, u['id'])
        c.execute('DELETE FROM sessions WHERE user_id=?', (u['id'],))
        c.execute("UPDATE users SET status='active' WHERE id=?", (u['id'],))
        return code
