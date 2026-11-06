# Mail server control, run as root on the server by coderunner/mail.py. One JSON request in (a literal filled in by the caller, never shell text), one JSON answer out.
# The single source of truth is /etc/shiplane-mail/mail.json; every change re-renders the Postfix maps, the Dovecot user file, the sieve scripts and the
# Rspamd settings from it, checks them with the real tools, and only then switches them on (the previous files are put back if a check fails).
import base64, json, os, re, shutil, socket, subprocess, sys, tempfile, time

STATE = '/etc/shiplane-mail'
CONF = STATE + '/mail.json'
PF = '/etc/postfix/shiplane'
DV = '/etc/dovecot/shiplane'
VMAIL = '/var/vmail'
VUID = 5000
DKIM_DIR = '/var/lib/rspamd/dkim'
RSPAMD_LOCAL = '/etc/rspamd/local.d'
TLS = STATE + '/tls'

DOMAIN_RE = re.compile(r'^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$')
LOCAL_RE = re.compile(r'^[a-z0-9]([a-z0-9._+-]{0,62}[a-z0-9])?$')
SELECTOR_RE = re.compile(r'^[a-z0-9]{1,30}$')
SPAM_LEVELS = {'off': None, 'relaxed': (8, 20), 'standard': (6, 15), 'strict': (4, 10)}     # (add the Spam header at, reject at)
DEFAULT = {'hostname': '', 'domains': {}, 'accounts': {}, 'aliases': {}, 'relay': {}, 'options': {'spam': 'standard', 'rspamd': True, 'webmail': False, 'fail2ban': True, 'max_message_mb': 50}}


class Fail(Exception):
    pass


# ------------------------------------------------------------------ validation (pure)
def domain_ok(d):
    d = str(d or '').strip().lower().rstrip('.')
    if not DOMAIN_RE.fullmatch(d):
        raise Fail('That is not a valid domain name')
    return d


def email_ok(e):
    e = str(e or '').strip().lower()
    local, _, dom = e.partition('@')
    if not LOCAL_RE.fullmatch(local) or '..' in local or not DOMAIN_RE.fullmatch(dom):
        raise Fail('That is not a valid email address')
    return e


def password_ok(p):
    if not isinstance(p, str) or not 10 <= len(p) <= 128 or '\n' in p or '\r' in p or '\0' in p:
        raise Fail('Use a password of 10 to 128 characters')
    if len(set(p)) < 5:
        raise Fail('That password is too easy to guess')
    return p


def quota_ok(q):
    try:
        q = int(q)
    except (TypeError, ValueError):
        raise Fail('The quota must be a number of megabytes (0 = unlimited)')
    if not 0 <= q <= 1024 * 1024:
        raise Fail('The quota must be between 0 and 1048576 MB')
    return q


def new_conf():
    return json.loads(json.dumps(DEFAULT))


def hash_password(pw):
    """{SHA512-CRYPT} hash for Dovecot, made by openssl (the password goes in on stdin, never on a command line)."""
    r = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input=(pw + '\n').encode(), capture_output=True)
    h = r.stdout.decode().strip()
    if r.returncode or not h.startswith('$6$'):
        raise Fail('Could not hash the password')
    return '{SHA512-CRYPT}' + h


# ------------------------------------------------------------------ rendering (pure: no files, no commands)
def render_vdomains(c):
    return ''.join('%s OK\n' % d for d in sorted(c['domains']))


def render_vmailbox(c):
    return ''.join('%s %s/%s/\n' % (e, e.split('@')[1], e.split('@')[0]) for e, a in sorted(c['accounts'].items()) if not a.get('disabled'))


def render_valias(c):
    """Aliases, forwarders and catch-alls. Every mailbox also maps to itself: an explicit entry beats a catch-all, so real (and disabled) mailboxes are never swallowed by it."""
    lines = ['%s %s\n' % (e, e) for e, a in sorted(c['accounts'].items()) if not a.get('disabled')]
    for src, dests in sorted(c['aliases'].items()):
        lines.append('%s %s\n' % (src, ', '.join(dests)))
    return ''.join(lines)


def render_disabled(c):
    """Disabled mailboxes refuse mail outright (they would otherwise fall into a catch-all)."""
    return ''.join('%s REJECT This mailbox is disabled\n' % e for e, a in sorted(c['accounts'].items()) if a.get('disabled'))


def render_sender_login(c):
    """Who may send as which address: an account as itself, and as every alias that points at it (the submission port refuses anything else)."""
    own = {}
    for e, a in c['accounts'].items():
        if not a.get('disabled'):
            own.setdefault(e, set()).add(e)
    for src, dests in c['aliases'].items():
        if src.startswith('@'):
            continue
        for d in dests:
            if d in c['accounts'] and not c['accounts'][d].get('disabled'):
                own.setdefault(src, set()).add(d)
    return ''.join('%s %s\n' % (a, ','.join(sorted(u))) for a, u in sorted(own.items()))


def render_dovecot_users(c):
    out = []
    for e, a in sorted(c['accounts'].items()):
        if a.get('disabled'):
            continue
        local, dom = e.split('@')
        extra = 'userdb_quota_rule=*:storage=%dM' % a['quota_mb'] if a.get('quota_mb') else 'userdb_quota_rule=*:storage=0'
        out.append('%s:%s:%d:%d::%s/%s/%s::%s\n' % (e, a['hash'], VUID, VUID, VMAIL, dom, local, extra))
    return ''.join(out)


def sieve_escape(s):
    return str(s).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '')


def render_vacation(address, ar):
    """The per-account sieve script that answers mail while the autoresponder is on (and keeps normal delivery)."""
    if not ar or not ar.get('enabled'):
        return None
    subject = sieve_escape(ar.get('subject') or 'Automatic reply')[:200]
    body = sieve_escape(ar.get('body') or 'I am away and will reply when I am back.')[:4000]
    days = max(1, min(int(ar.get('days') or 7), 30))
    return ('require ["vacation"];\nvacation :days %d :subject "%s" :addresses ["%s"] "%s";\n' % (days, subject, address, body))


BEFORE_SIEVE = ('require ["fileinto", "mailbox"];\n'
                'if anyof (header :contains "X-Spam" "Yes", header :contains "X-Spam-Status" "Yes") {\n    fileinto :create "Junk";\n    stop;\n}\n')


def ipv6_available():
    return os.path.exists('/proc/net/if_inet6')


def render_dovecot(c, ipv6=True):
    h = c['hostname']
    return '''# Written by Shiplane. Changes here are overwritten: use the Mail page.
protocols = imap pop3 lmtp
listen = %(listen)s
mail_home = /var/vmail/%%d/%%n
mail_location = maildir:~/Maildir
mail_uid = %(uid)d
mail_gid = %(uid)d
first_valid_uid = %(uid)d
last_valid_uid = %(uid)d
mail_plugins = $mail_plugins quota
auth_mechanisms = plain login
disable_plaintext_auth = yes
auth_username_format = %%Lu
login_greeting = Mail server ready.
log_path = syslog
syslog_facility = mail

ssl = required
ssl_cert = <%(tls)s/fullchain.pem
ssl_key = <%(tls)s/privkey.pem
ssl_min_protocol = TLSv1.2
ssl_prefer_server_ciphers = yes

passdb {
  driver = passwd-file
  args = scheme=SHA512-CRYPT username_format=%%u %(dv)s/users
}
userdb {
  driver = passwd-file
  args = username_format=%%u %(dv)s/users
}

namespace inbox {
  inbox = yes
  mailbox Drafts {
    special_use = \\Drafts
    auto = subscribe
  }
  mailbox Junk {
    special_use = \\Junk
    auto = subscribe
    autoexpunge = 60d
  }
  mailbox Trash {
    special_use = \\Trash
    auto = subscribe
    autoexpunge = 60d
  }
  mailbox Sent {
    special_use = \\Sent
    auto = subscribe
  }
}

service imap-login {
  inet_listener imap {
    port = 143
  }
  inet_listener imaps {
    port = 993
    ssl = yes
  }
}
service pop3-login {
  inet_listener pop3 {
    port = 110
  }
  inet_listener pop3s {
    port = 995
    ssl = yes
  }
}
service lmtp {
  unix_listener /var/spool/postfix/private/dovecot-lmtp {
    mode = 0600
    user = postfix
    group = postfix
  }
}
service auth {
  unix_listener /var/spool/postfix/private/auth {
    mode = 0660
    user = postfix
    group = postfix
  }
  unix_listener auth-userdb {
    mode = 0600
    user = vmail
  }
}

protocol lmtp {
  mail_plugins = $mail_plugins sieve
  postmaster_address = postmaster@%(host)s
}
protocol imap {
  mail_plugins = $mail_plugins imap_quota
}
plugin {
  quota = maildir:User quota
  quota_grace = 10%%%%
  sieve = file:~/sieve;active=~/.dovecot.sieve
  sieve_before = %(dv)s/before.sieve
}
''' % {'uid': VUID, 'tls': TLS, 'dv': DV, 'host': h, 'listen': '*, ::' if ipv6 else '*'}


def postfix_settings(c):
    """(key, value) pairs for `postconf -e`: everything the mail server needs in main.cf."""
    o, h = c['options'], c['hostname']
    s = [('myhostname', h), ('mydestination', '$myhostname, localhost'), ('myorigin', '$myhostname'),
         ('mynetworks', '127.0.0.0/8 [::ffff:127.0.0.0]/104 [::1]/128'),          # only this machine may relay without logging in (the default would trust the whole subnet)
         ('inet_interfaces', 'all'), ('inet_protocols', 'all'), ('smtpd_banner', '$myhostname ESMTP'),
         ('disable_vrfy_command', 'yes'), ('smtpd_helo_required', 'yes'),
         ('message_size_limit', str(int(o.get('max_message_mb', 50)) * 1024 * 1024)), ('mailbox_size_limit', '0'),
         ('virtual_mailbox_domains', 'hash:%s/vdomains' % PF), ('virtual_mailbox_maps', 'hash:%s/vmailbox' % PF), ('virtual_alias_maps', 'hash:%s/valias' % PF),
         ('virtual_transport', 'lmtp:unix:private/dovecot-lmtp'),
         ('smtpd_sasl_type', 'dovecot'), ('smtpd_sasl_path', 'private/auth'), ('smtpd_sasl_auth_enable', 'no'),
         ('smtpd_tls_cert_file', TLS + '/fullchain.pem'), ('smtpd_tls_key_file', TLS + '/privkey.pem'),
         ('smtpd_tls_security_level', 'may'), ('smtpd_tls_auth_only', 'yes'), ('smtpd_tls_protocols', '>=TLSv1.2'), ('smtpd_tls_mandatory_protocols', '>=TLSv1.2'),
         ('smtp_tls_security_level', 'may'), ('smtp_tls_protocols', '>=TLSv1.2'), ('smtpd_tls_loglevel', '1'), ('smtp_tls_loglevel', '1'),
         ('smtpd_relay_restrictions', 'permit_mynetworks, permit_sasl_authenticated, defer_unauth_destination'),
         ('smtpd_recipient_restrictions', 'check_recipient_access hash:%s/disabled, permit_mynetworks, permit_sasl_authenticated, reject_non_fqdn_recipient, reject_unknown_recipient_domain, reject_unauth_destination' % PF),
         ('smtpd_sender_login_maps', 'hash:%s/sender_login' % PF)]
    if o.get('rspamd', True):
        s += [('smtpd_milters', 'inet:127.0.0.1:11332'), ('non_smtpd_milters', '$smtpd_milters'), ('milter_protocol', '6'), ('milter_default_action', 'accept')]
    else:
        s += [('smtpd_milters', ''), ('non_smtpd_milters', '')]
    r = c.get('relay') or {}
    if r.get('host'):
        s += [('relayhost', '[%s]:%d' % (r['host'], int(r.get('port') or 587))), ('smtp_sasl_auth_enable', 'yes' if r.get('user') else 'no'),
              ('smtp_sasl_password_maps', 'hash:%s/sasl_passwd' % PF), ('smtp_sasl_security_options', 'noanonymous'), ('smtp_tls_security_level', 'encrypt')]
    else:
        s += [('relayhost', ''), ('smtp_sasl_auth_enable', 'no'), ('smtp_sasl_password_maps', '')]
    return s


SUBMISSION = [('smtpd_tls_security_level', 'encrypt'), ('smtpd_sasl_auth_enable', 'yes'), ('smtpd_tls_auth_only', 'yes'),
              ('smtpd_client_restrictions', 'permit_sasl_authenticated,reject'), ('smtpd_relay_restrictions', 'permit_sasl_authenticated,reject'),
              ('smtpd_sender_restrictions', 'reject_sender_login_mismatch'), ('smtpd_recipient_restrictions', 'check_recipient_access hash:%s/disabled,reject_non_fqdn_recipient,reject_unknown_recipient_domain,permit_sasl_authenticated,reject' % PF),
              ('milter_macro_daemon_name', 'ORIGINATING')]
MASTER = [('submission', 'submission inet n - y - - smtpd', SUBMISSION),
          ('smtps', 'smtps inet n - y - - smtpd', SUBMISSION + [('smtpd_tls_wrappermode', 'yes')])]


def render_rspamd(c):
    """{filename in /etc/rspamd/local.d: content}."""
    o = c['options']
    lv = SPAM_LEVELS.get(o.get('spam', 'standard')) or (15, 15)
    sel = ''.join('%s %s\n' % (d, v['selector']) for d, v in sorted(c['domains'].items()))
    return {
        'worker-proxy.inc': 'milter = yes;\ntimeout = 120s;\nupstream "local" {\n  default = yes;\n  self_scan = yes;\n}\nbind_socket = "127.0.0.1:11332";\n',
        'dkim_signing.conf': 'enabled = true;\npath = "%s/$domain.$selector.key";\nselector_map = "/etc/rspamd/shiplane_dkim_selectors.map";\nallow_username_mismatch = true;\nuse_esld = false;\nsign_authenticated = true;\nsign_local = true;\n' % DKIM_DIR,
        'milter_headers.conf': 'use = ["authentication-results", "x-spam-level"];\nextended_spam_headers = true;\nauthenticated_headers = ["authentication-results"];\n',
        'actions.conf': 'reject = %d;\nadd_header = %d;\ngreylist = %d;\n' % (lv[1], lv[0], max(1, lv[0] - 2)) if o.get('spam', 'standard') != 'off' else 'reject = 1000;\nadd_header = 1000;\ngreylist = 1000;\n',
        '../shiplane_dkim_selectors.map': sel,
    }


def render_sasl_passwd(c):
    r = c.get('relay') or {}
    if r.get('host') and r.get('user'):
        return '[%s]:%d %s:%s\n' % (r['host'], int(r.get('port') or 587), r['user'], r.get('password', ''))
    return ''


# ------------------------------------------------------------------ the model (pure operations on the dict)
def add_domain(c, domain, selector='mail'):
    domain = domain_ok(domain)
    if not SELECTOR_RE.fullmatch(selector):
        raise Fail('The DKIM selector must be letters and digits only')
    if domain in c['domains']:
        raise Fail('That domain is already set up')
    c['domains'][domain] = {'selector': selector, 'created': int(time.time())}
    return domain


def remove_domain(c, domain):
    domain = domain_ok(domain)
    if domain not in c['domains']:
        raise Fail('No such domain')
    users = [e for e in c['accounts'] if e.endswith('@' + domain)]
    if users:
        raise Fail('Delete its %d mailbox%s first' % (len(users), '' if len(users) == 1 else 'es'))
    del c['domains'][domain]
    for src in [s for s in c['aliases'] if s.endswith('@' + domain)]:
        del c['aliases'][src]
    return domain


def add_account(c, email, hashed, quota_mb=1024, name=''):
    email = email_ok(email)
    dom = email.split('@')[1]
    if dom not in c['domains']:
        raise Fail('Add the domain %s first' % dom)
    if email in c['accounts']:
        raise Fail('That mailbox already exists')
    if email in c['aliases']:
        raise Fail('An alias with that address exists: remove it first')
    c['accounts'][email] = {'hash': hashed, 'quota_mb': quota_ok(quota_mb), 'name': str(name or '')[:80], 'disabled': False, 'created': int(time.time())}
    return email


def add_alias(c, source, dests):
    source = str(source or '').strip().lower()
    if source.startswith('@'):
        domain_ok(source[1:])
        dom = source[1:]
    else:
        email_ok(source)
        dom = source.split('@')[1]
    if dom not in c['domains']:
        raise Fail('Add the domain %s first' % dom)
    if source in c['accounts']:
        raise Fail('A mailbox with that address exists: delete it first')
    clean = []
    for d in dests if isinstance(dests, list) else [dests]:
        d = email_ok(d)
        if d == source:
            raise Fail('An address cannot forward to itself')
        if d not in clean:
            clean.append(d)
    if not clean or len(clean) > 20:
        raise Fail('Give 1 to 20 destination addresses')
    c['aliases'][source] = clean
    return source


# ------------------------------------------------------------------ files and commands (on the server)
def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def write(path, text, mode=0o640, owner=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.tmp-')
    with os.fdopen(fd, 'w') as f:
        f.write(text)
    os.chmod(tmp, mode)
    if owner:
        shutil.chown(tmp, *owner)
    os.replace(tmp, path)


def load():
    try:
        with open(CONF) as f:
            c = json.load(f)
    except (OSError, ValueError):
        return new_conf()
    for k, v in DEFAULT.items():
        c.setdefault(k, json.loads(json.dumps(v)))
    return c


def save(c):
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    write(CONF, json.dumps(c, indent=1), 0o600)


def dkim_key_path(domain, selector):
    return '%s/%s.%s.key' % (DKIM_DIR, domain, selector)


def make_dkim(domain, selector):
    """A new 2048-bit key for the domain; returns the public part (the p= value of the DNS record)."""
    os.makedirs(DKIM_DIR, exist_ok=True)
    key = dkim_key_path(domain, selector)
    if not os.path.exists(key):
        r = sh(['openssl', 'genrsa', '-out', key, '2048'])
        if r.returncode:
            raise Fail('Could not create the DKIM key: %s' % r.stderr.strip()[-200:])
    os.chmod(key, 0o640)
    try:
        shutil.chown(key, 'root', '_rspamd')
        shutil.chown(DKIM_DIR, 'root', '_rspamd')
        os.chmod(DKIM_DIR, 0o750)
    except (LookupError, OSError):
        pass
    return dkim_public(key)


def dkim_public(keyfile):
    der = subprocess.run(['openssl', 'rsa', '-in', keyfile, '-pubout', '-outform', 'der'], capture_output=True).stdout
    return base64.b64encode(der).decode() if der else ''


def apply(c):
    """Render everything, check it with the real tools, switch it on. Returns notes. Raises Fail (and leaves the old files) if a check fails."""
    notes = []
    files = {
        PF + '/vdomains': render_vdomains(c), PF + '/vmailbox': render_vmailbox(c), PF + '/valias': render_valias(c),
        PF + '/sender_login': render_sender_login(c), PF + '/sasl_passwd': render_sasl_passwd(c), PF + '/disabled': render_disabled(c),
    }
    backup = {}
    for p in list(files) + [DV + '/users', DV + '/before.sieve', '/etc/dovecot/dovecot.conf']:
        try:
            backup[p] = open(p).read()
        except OSError:
            backup[p] = None
    try:
        for p, text in files.items():
            write(p, text, 0o600 if p.endswith('sasl_passwd') else 0o644)
            sh(['postmap', p])
        write(DV + '/users', render_dovecot_users(c), 0o640, ('root', 'dovecot'))
        write(DV + '/before.sieve', BEFORE_SIEVE, 0o644)
        sh(['sievec', DV + '/before.sieve'])
        write('/etc/dovecot/dovecot.conf', render_dovecot(c, ipv6_available()), 0o644)
        chk = sh(['doveconf', '-n'])
        if chk.returncode:
            raise Fail('Dovecot rejected the settings: %s' % (chk.stderr.strip()[-300:] or 'unknown error'))
        s = postfix_settings(c)
        r = sh(['postconf', '-e'] + ['%s=%s' % kv for kv in s])
        if r.returncode:
            raise Fail('Postfix rejected the settings: %s' % r.stderr.strip()[-300:])
        for name, spec, params in MASTER:
            sh(['postconf', '-M', '%s/inet=%s' % (name, spec)])
            for k, v in params:
                sh(['postconf', '-P', '%s/inet/%s=%s' % (name, k, v)])
        ck = sh(['postfix', 'check'])
        problems = [l for l in ck.stderr.splitlines() if l.strip() and 'warning:' not in l]          # warnings (for example "IPv6 is not available here") do not stop anything
        if ck.returncode or problems:
            raise Fail('Postfix check failed: %s' % ' '.join(problems or [ck.stderr])[-300:])
        for name, text in render_rspamd(c).items():
            if os.path.isdir('/etc/rspamd'):
                write(os.path.normpath(os.path.join(RSPAMD_LOCAL, name)), text, 0o644)
    except Exception:
        for p, text in backup.items():                                           # a check failed: put the previous files back
            if text is not None:
                try:
                    write(p, text, 0o600 if p.endswith('sasl_passwd') else 0o644)
                except OSError:
                    pass
        raise
    for acct, a in c['accounts'].items():
        write_autoreply(acct, a)
    reload_services(notes)
    return notes


def write_autoreply(email, a):
    local, dom = email.split('@')
    home = '%s/%s/%s' % (VMAIL, dom, local)
    script = render_vacation(email, a.get('autoreply'))
    path = home + '/.dovecot.sieve'
    if script is None:
        for p in (path, path.replace('.sieve', '.svbin')):
            try:
                os.remove(p)
            except OSError:
                pass
        return
    os.makedirs(home, exist_ok=True)
    write(path, script, 0o644)
    try:
        shutil.chown(path, VUID, VUID)
        shutil.chown(home, VUID, VUID)
    except OSError:
        pass


def reload_services(notes):
    for svc in ('postfix', 'dovecot', 'rspamd'):
        if shutil.which('systemctl') and sh(['systemctl', 'is-system-running']).stdout.strip() not in ('', 'offline'):
            r = sh(['systemctl', 'reload-or-restart', svc])
            if r.returncode and svc != 'rspamd':
                notes.append('%s did not reload: %s' % (svc, r.stderr.strip()[-120:]))
        elif svc == 'postfix':
            sh(['postfix', 'reload'])
        elif svc == 'dovecot':
            sh(['doveadm', 'reload'])


def maildir_size(email):
    local, dom = email.split('@')
    p = '%s/%s/%s/Maildir' % (VMAIL, dom, local)
    if not os.path.isdir(p):
        return 0
    r = sh(['du', '-sb', p])
    try:
        return int(r.stdout.split()[0])
    except (IndexError, ValueError):
        return 0


def public_view(c):
    return {'hostname': c['hostname'], 'options': c['options'], 'relay': dict({k: v for k, v in (c.get('relay') or {}).items() if k != 'password'}, has_password=bool((c.get('relay') or {}).get('password'))),
            'domains': [dict(name=d, selector=v['selector'], dkim_public=dkim_public(dkim_key_path(d, v['selector'])) if os.path.exists(dkim_key_path(d, v['selector'])) else '',
                             accounts=sum(1 for e in c['accounts'] if e.endswith('@' + d)), aliases=sum(1 for s in c['aliases'] if s.endswith('@' + d))) for d, v in sorted(c['domains'].items())],
            'accounts': [dict(email=e, quota_mb=a.get('quota_mb', 0), used=maildir_size(e), disabled=bool(a.get('disabled')), name=a.get('name', ''), autoreply=a.get('autoreply') or {}) for e, a in sorted(c['accounts'].items())],
            'aliases': [{'source': s, 'dests': d} for s, d in sorted(c['aliases'].items())]}


def port_open(host, port, timeout=1.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def status():
    c = load()
    svc = {}
    for name, ports in (('postfix', (25, 587, 465)), ('dovecot', (993, 995, 143, 110)), ('rspamd', (11332,))):
        installed = bool(shutil.which({'postfix': 'postconf', 'dovecot': 'doveconf', 'rspamd': 'rspamadm'}[name]))
        svc[name] = {'installed': installed, 'ports': {str(p): port_open('127.0.0.1', p) for p in ports} if installed else {}}
    q = sh(['postqueue', '-j']) if shutil.which('postqueue') else None
    queue = len([l for l in (q.stdout.splitlines() if q else []) if l.strip()])
    cert = None
    if os.path.exists(TLS + '/fullchain.pem'):
        r = sh(['openssl', 'x509', '-in', TLS + '/fullchain.pem', '-noout', '-enddate', '-issuer', '-subject'])
        info = dict(l.split('=', 1) for l in r.stdout.splitlines() if '=' in l)
        cert = {'expires': info.get('notAfter', ''), 'self_signed': info.get('issuer', '').strip() == info.get('subject', '').strip()}
    return {'configured': bool(c['hostname']), 'hostname': c['hostname'], 'services': svc, 'queue': queue, 'cert': cert, 'webmail': os.path.isdir('/usr/share/roundcube'),
            'domains': len(c['domains']), 'accounts': len(c['accounts']), 'aliases': len(c['aliases'])}


def test_port25():
    """Can this server send mail the normal way? Many hosts (Hetzner, AWS, Google, Azure) block outgoing port 25 until you ask."""
    targets = ['gmail-smtp-in.l.google.com', 'mx1.mail.icloud.com', 'mx.mail.yahoo.com']
    out = []
    for t in targets:
        try:
            with socket.create_connection((t, 25), timeout=6) as s:
                banner = s.recv(120).decode('utf-8', 'replace').strip()
            out.append({'host': t, 'open': True, 'banner': banner[:80]})
            break
        except OSError as e:
            out.append({'host': t, 'open': False, 'error': str(e)[:80]})
    return {'ok': any(x['open'] for x in out), 'tries': out}


def queue_action(action, qid=None):
    if action == 'list':
        r = sh(['postqueue', '-j'])
        items = []
        for line in r.stdout.splitlines()[:300]:
            try:
                m = json.loads(line)
                items.append({'id': m['queue_id'], 'queue': m['queue_name'], 'size': m['message_size'], 'time': m['arrival_time'], 'sender': m['sender'],
                              'to': [x['address'] for x in m.get('recipients', [])][:5], 'why': (m.get('recipients') or [{}])[0].get('delay_reason', '')[:140]})
            except (ValueError, KeyError):
                continue
        return {'items': items}
    if action == 'flush':
        sh(['postqueue', '-f'])
        return {'ok': True}
    if action == 'delete':
        if not re.fullmatch(r'[0-9A-Za-z]{6,20}', str(qid or '')):
            raise Fail('Bad queue id')
        sh(['postsuper', '-d', qid])
        return {'ok': True}
    raise Fail('Unknown queue action')


def logs(n=200):
    for p in ('/var/log/mail.log', '/var/log/maillog'):
        if os.path.exists(p):
            return {'lines': sh(['tail', '-n', str(max(10, min(int(n), 1000))), p]).stdout.splitlines()}
    r = sh(['journalctl', '-u', 'postfix@-', '-u', 'dovecot', '-n', str(max(10, min(int(n), 1000))), '--no-pager'])
    return {'lines': r.stdout.splitlines()}


def commit(c):
    """Switch the new settings on; remember them only if that worked, so a refused change leaves everything as it was."""
    notes = apply(c)
    save(c)
    return notes


def init(req):
    c = load()
    host = domain_ok(req['hostname'])
    c['hostname'] = host
    c['options'].update({k: v for k, v in (req.get('options') or {}).items() if k in c['options']})
    return {'ok': True, 'notes': commit(c)}


def act(req):
    a = req['action']
    c = load()
    if a == 'status':
        return status()
    if a == 'view':
        return public_view(c)
    if a == 'port25':
        return test_port25()
    if a == 'queue':
        return queue_action(req.get('op'), req.get('id'))
    if a == 'logs':
        return logs(req.get('lines', 200))
    if a == 'init':
        return init(req)
    if not c['hostname']:
        raise Fail('Set the mail server up first')
    if a == 'domain_add':
        d = add_domain(c, req['domain'], req.get('selector') or 'mail')
        pub = make_dkim(d, c['domains'][d]['selector'])
        return {'domain': d, 'dkim_public': pub, 'notes': commit(c)}
    if a == 'domain_remove':
        d = remove_domain(c, req['domain'])
        return {'ok': True, 'notes': commit(c)}
    if a == 'account_add':
        e = add_account(c, req['email'], hash_password(password_ok(req['password'])), req.get('quota_mb', 1024), req.get('name', ''))
        notes = commit(c)
        home = '%s/%s/%s' % (VMAIL, e.split('@')[1], e.split('@')[0])
        os.makedirs(home + '/Maildir', mode=0o700, exist_ok=True)
        for root, dirs, fl in os.walk(home):
            shutil.chown(root, VUID, VUID)
        return {'email': e, 'notes': notes}
    if a in ('account_password', 'account_quota', 'account_disable', 'account_enable', 'account_autoreply', 'account_delete'):
        e = email_ok(req['email'])
        if e not in c['accounts']:
            raise Fail('No such mailbox')
        acct = c['accounts'][e]
        if a == 'account_password':
            acct['hash'] = hash_password(password_ok(req['password']))
        elif a == 'account_quota':
            acct['quota_mb'] = quota_ok(req['quota_mb'])
        elif a in ('account_disable', 'account_enable'):
            acct['disabled'] = a == 'account_disable'
        elif a == 'account_autoreply':
            ar = req.get('autoreply') or {}
            acct['autoreply'] = {'enabled': bool(ar.get('enabled')), 'subject': str(ar.get('subject') or '')[:200], 'body': str(ar.get('body') or '')[:4000], 'days': int(ar.get('days') or 7)}
        elif a == 'account_delete':
            del c['accounts'][e]
        notes = commit(c)
        if a == 'account_delete' and req.get('delete_mail'):
            local, dom = e.split('@')
            shutil.rmtree('%s/%s/%s' % (VMAIL, dom, local), ignore_errors=True)
        return {'ok': True, 'notes': notes}
    if a == 'alias_add':
        s = add_alias(c, req['source'], req['dests'])
        return {'source': s, 'notes': commit(c)}
    if a == 'alias_remove':
        s = str(req['source']).strip().lower()
        if s not in c['aliases']:
            raise Fail('No such alias')
        del c['aliases'][s]
        return {'ok': True, 'notes': commit(c)}
    if a == 'relay':
        r = req.get('relay') or {}
        if not r.get('host'):
            c['relay'] = {}
        else:
            host = str(r['host']).strip().lower()
            if not re.fullmatch(r'[a-z0-9.-]{3,253}', host):
                raise Fail('The relay host looks wrong')
            port = int(r.get('port') or 587)
            if not 1 <= port <= 65535:
                raise Fail('The relay port looks wrong')
            old = c.get('relay') or {}
            user = str(r.get('user') or '')
            if re.search(r'[\s:]', user) or len(user) > 200:
                raise Fail('The relay user name looks wrong')
            pw = str(r.get('password') or '') or old.get('password', '')
            if re.search(r'[\s]', pw) or len(pw) > 300:
                raise Fail('The relay password looks wrong')
            c['relay'] = {'host': host, 'port': port, 'user': user, 'password': pw}
        return {'ok': True, 'notes': commit(c)}
    if a == 'options':
        o = req.get('options') or {}
        if 'spam' in o:
            if o['spam'] not in SPAM_LEVELS:
                raise Fail('Spam level must be off, relaxed, standard or strict')
            c['options']['spam'] = o['spam']
        if 'max_message_mb' in o:
            mb = int(o['max_message_mb'])
            if not 1 <= mb <= 200:
                raise Fail('The message size limit must be 1 to 200 MB')
            c['options']['max_message_mb'] = mb
        return {'ok': True, 'notes': commit(c)}
    raise Fail('Unknown action')


def main():
    req = json.loads(__REQ__)
    try:
        print(json.dumps(act(req)))
    except Fail as e:
        print(json.dumps({'error': str(e)}))
        sys.exit(3)
    except (OSError, subprocess.SubprocessError, KeyError, ValueError) as e:
        print(json.dumps({'error': 'Mail operation failed: %s' % (str(e)[:200])}))
        sys.exit(3)


if __name__ == '__main__':
    main()
