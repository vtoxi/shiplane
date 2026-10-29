"""Migration, step 1: look at an old hosting account and list what could be moved.

Two sources, one result shape:
  * a cPanel full-account backup (cpmove-USER.tar.gz), read in place - nothing is extracted to disk;
  * a live server over SSH (cPanel, Plesk), through the fixed script remote/discover.py.

Result: {panel, accounts, sites[{domain, aliases, docroot, size, kind, php}], databases[{name, engine, size}], cron[], mailboxes, notes[]}
This step only reads. Nothing is changed on either server.
"""
import json
import os
import re
import tarfile

from . import config
from . import deploy
from .runner import explain_ssh, run, script

MAX_SMALL = 1 << 20                 # config-like files read from the archive are capped at 1 MB
DEPTH = 5                           # folders deeper than this are folded into their parent when summing sizes
SKIP_USERDATA = {'main', 'cache'}
MARKERS = {'wp-config.php': 'wordpress', 'artisan': 'laravel', 'composer.json': 'php', 'package.json': 'node'}
BACKUP_RE = re.compile(r'\.(tar\.gz|tgz|tar)$', re.I)
ANY_BACKUP_RE = re.compile(r'\.(tar\.gz|tgz|tar|zip)$', re.I)


class NotCpanel(ValueError):
    pass


def _kv(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r'^([A-Za-z_]+):\s*(.*)$', line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def _kind(files_here, php_files):
    for marker, kind in MARKERS.items():
        if marker in files_here:
            return kind
    return 'php' if php_files else 'static'


def inspect_cpmove(path):
    path = os.path.abspath(os.path.expanduser(str(path)))
    if not BACKUP_RE.search(path) or not os.path.isfile(path):
        raise ValueError('Choose a cPanel backup file (cpmove-*.tar.gz) that exists on this computer')
    small = {}                       # path inside the archive (first folder removed) -> text
    dir_size, markers, mail = {}, {}, set()
    dbs, cron, account = {}, [], ''
    try:
        with tarfile.open(path, 'r:*') as tar:
            for m in tar:
                if not m.isfile():
                    continue
                rel = m.name.split('/', 1)[1] if '/' in m.name else m.name
                parts = rel.split('/')
                if parts[0] == 'homedir':
                    key = '/'.join(parts[:DEPTH])
                    dir_size[key] = dir_size.get(key, 0) + m.size
                    if parts[-1] in MARKERS or parts[-1].endswith('.php'):
                        d = markers.setdefault('/'.join(parts[:-1]), set())
                        d.add(parts[-1] if parts[-1] in MARKERS else '*.php')
                    if len(parts) >= 4 and parts[1] == 'mail' and '.' in parts[2] and parts[2] not in ('cur', 'new', 'tmp'):
                        mail.add((parts[2], parts[3]))
                elif parts[0] == 'mysql' and rel.endswith('.sql') and len(parts) == 2 and parts[1] != 'mysql.sql':
                    dbs[parts[1][:-4]] = {'name': parts[1][:-4], 'engine': 'mysql', 'size': m.size}
                elif parts[0] == 'psql' and len(parts) == 2:
                    name = re.sub(r'\.(tar|sql)$', '', parts[1])
                    dbs[name] = {'name': name, 'engine': 'postgres', 'size': m.size}
                elif parts[0] in ('userdata', 'cron', 'cp') and len(parts) == 2 and m.size <= MAX_SMALL:
                    small[rel] = tar.extractfile(m).read().decode('utf-8', 'replace')
    except (tarfile.TarError, OSError, EOFError) as e:
        raise ValueError('Could not read the backup: %s' % e)
    if not any(k.startswith('userdata/') for k in small):
        raise NotCpanel('This does not look like a cPanel account backup (no userdata folder)')
    for k, v in small.items():
        if k.startswith('cron/'):
            account = account or k.split('/', 1)[1]
            cron += [l for l in v.splitlines() if l.strip() and not l.lstrip().startswith('#') and re.match(r'^[\d*@]', l.lstrip())]
        elif k.startswith('cp/'):
            account = k.split('/', 1)[1]
    sites = []
    for k, v in sorted(small.items()):
        name = k.split('/', 1)[1]
        if not k.startswith('userdata/') or name in SKIP_USERDATA or name.endswith('_SSL') or name.endswith('.cache'):
            continue
        d = _kv(v)
        if not d.get('documentroot') or not d.get('servername'):
            continue
        home = d.get('homedir', '/home/%s' % account).rstrip('/')
        sub = d['documentroot'][len(home) + 1:] if d['documentroot'].startswith(home + '/') else d['documentroot'].strip('/').split('/', 2)[-1]
        sites.append({'domain': d['servername'].lower(), 'aliases': [a.lower() for a in d.get('serveralias', '').split() if a.lower() != d['servername'].lower()],
                      'docroot': '/' + sub, '_rel': 'homedir/' + sub, 'php': d.get('phpversion', '')})
    rels = sorted((s['_rel'] for s in sites), key=len, reverse=True)
    for s in sites:      # each file counts for the deepest docroot that contains it, so nested addon domains are not counted twice
        s['size'] = sum(sz for key, sz in dir_size.items() if (key + '/').startswith(s['_rel'] + '/') and next(r for r in rels if (key + '/').startswith(r + '/')) == s['_rel'])
        here = {n for d, ns in markers.items() if d == s['_rel'] for n in ns}
        s['kind'] = _kind(here, '*.php' in here or any(d.startswith(s['_rel'] + '/') and '*.php' in ns for d, ns in markers.items()))
        del s['_rel']
    return finish({'format': 'cpmove', 'panel': 'cPanel backup', 'accounts': [account] if account else [], 'sites': sites, 'databases': sorted(dbs.values(), key=lambda x: x['name']),
                   'cron': cron, 'mailboxes': len(mail)})


def inspect_backup(path):
    """A backup file from cPanel (cpmove) or Plesk (export): the kind is recognised from what is inside."""
    from . import plesk
    path = os.path.abspath(os.path.expanduser(str(path)))
    if not ANY_BACKUP_RE.search(path) or not os.path.isfile(path):
        raise ValueError('Choose a cPanel or Plesk backup file (.tar.gz, .tgz, .tar or .zip) that exists on this computer')
    if path.lower().endswith('.zip'):
        return plesk.inspect(path)
    try:
        return inspect_cpmove(path)
    except NotCpanel:
        try:
            return plesk.inspect(path)
        except plesk.NotPlesk:
            raise ValueError('This is neither a cPanel (cpmove) nor a Plesk backup. Expected a userdata folder or a backup_info XML file inside.')


def inspect(req):
    """Inspect the source described by a request: {source: 'backup', path} or {source: 'live', host, user, auth, key|password}."""
    return inspect_backup(req.get('path', '')) if req.get('source') == 'backup' else inspect_live(req)


def inspect_live(src):
    """Discover sites and databases on a running cPanel/Plesk server. Needs a root-capable SSH login."""
    cfg = config.check_server({'mode': 'ssh', 'auth': src.get('auth'), 'host': src.get('host', ''), 'port': src.get('port'), 'user': src.get('user', ''), 'key': src.get('key', ''), 'password': src.get('password', '')})
    rc, out = run(cfg, 'sudo python3 -', inp=script('discover.py').encode(), timeout=180)
    if rc or '{' not in out:
        raise RuntimeError(explain_ssh(out, cfg)[-500:] or 'could not read the old server')
    return finish(json.loads(out[out.index('{'):out.rindex('}') + 1]))


def finish(inv):
    """Add the plain-language notes the screen shows: what will work now and what will not."""
    notes = []
    if inv['panel'] == 'unknown':
        notes.append('No cPanel or Plesk data was found. A normal shared-hosting login cannot read it: create a full account backup in cPanel (Backup, then Download a Full Account Backup) and use the backup file source instead.')
    kinds = {s['kind'] for s in inv['sites']}
    if kinds & {'php', 'wordpress', 'laravel'}:
        notes.append('PHP sites (%s) need PHP support on the new server.' % ', '.join(sorted(kinds & {'php', 'wordpress', 'laravel'})))
    if any(d['engine'] == 'mysql' for d in inv['databases']):
        notes.append('MySQL/MariaDB databases need MySQL support on the new server.')
    if inv['mailboxes']:
        notes.append('%d mailboxes found. Mail needs its own mail server on the new server; it is not moved with the websites.' % inv['mailboxes'])
    if inv['cron']:
        notes.append('%d cron jobs found. Paths in them usually point at the old home folder and must be reviewed.' % len(inv['cron']))
    inv['notes'] = notes
    return inv


# ------------------------------------------------- is the new server ready for this?
APT = 'export DEBIAN_FRONTEND=noninteractive; sudo apt-get update -q && sudo apt-get install -y -q %s'
PHP_START = ("\nsudo systemctl enable --now $(systemctl list-unit-files 'php*-fpm.service' --no-legend | awk '{print $1}' | head -1)"
             "\nsleep 2; test -S /run/php/php-fpm.sock || { echo 'PHP-FPM socket /run/php/php-fpm.sock not found'; exit 1; }")
# id -> label, command that prints "yes" when present, auto-install command (None = cannot be done safely from here), what to do instead
CAPABILITIES = {
    'php': ('PHP (PHP-FPM)', 'command -v php-fpm >/dev/null 2>&1 || ls /usr/sbin/php-fpm* >/dev/null 2>&1',
            APT % 'php-fpm php-cli php-mysql php-pgsql php-curl php-gd php-mbstring php-xml php-zip php-intl' + PHP_START, None),
    'mysql': ('MySQL / MariaDB', 'command -v mysqld >/dev/null 2>&1 || command -v mariadbd >/dev/null 2>&1', APT % 'mariadb-server', None),
    'postgres': ('PostgreSQL', 'command -v psql >/dev/null 2>&1', APT % 'postgresql', None),
    'mail': ('Mail server (Postfix + Dovecot)', 'command -v postfix >/dev/null 2>&1 && command -v dovecot >/dev/null 2>&1', None,
             'A mail server needs DNS records (MX, SPF, DKIM, reverse DNS) and ongoing care, so it is not installed automatically. '
             'Set one up first (for example a mail suite such as Mailcow on this or another server) or keep your mail with a hosted provider, then migrate the websites.'),
}


def needed(inv):
    kinds = {s['kind'] for s in inv['sites']}
    want = []
    if kinds & {'php', 'wordpress', 'laravel'}:
        want.append('php')
    if any(d['engine'] == 'mysql' for d in inv['databases']):
        want.append('mysql')
    if any(d['engine'] == 'postgres' for d in inv['databases']):
        want.append('postgres')
    if inv['mailboxes']:
        want.append('mail')
    return want


def capabilities(cfg, inv):
    """What the migration needs on the NEW server, whether it is there, and whether it can be installed from here."""
    out = []
    for cid in needed(inv):
        label, check, install, advice = CAPABILITIES[cid]
        rc, text = run(cfg, '(%s) && echo yes || echo no' % check)
        out.append({'id': cid, 'label': label, 'installed': 'yes' in text, 'auto': install is not None, 'advice': advice})
    return out


def install_capability(cfg, cid):
    """Start a background install job for one capability. Returns the job id."""
    if cid not in CAPABILITIES or CAPABILITIES[cid][2] is None:
        raise ValueError('That cannot be installed automatically. Set it up first, then check again.')
    return deploy.start_script_job(cfg, 'set -e\n%s\necho installed\n' % CAPABILITIES[cid][2])
