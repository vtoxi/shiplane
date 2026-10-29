"""Migration, step 2: copy the chosen sites and databases from the old hosting to this server, with live progress.

A run is a list of named steps (Prepare, Databases, Sites, Finish) whose status the screen shows as it happens.
One failed step never stops the others: the run finishes and says what needs attention.
Database passwords are generated here, written into the site's own config, and never logged or shown.
"""
import gzip
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import tarfile
import threading
import time
import uuid

from . import config, deploy, migrate, perf, plesk
from .runner import NOWIN, command, environment, explain_ssh, popen, q, run

RUNS = {}
LOCK = threading.Lock()
CHUNK = 1 << 20
APP_TYPE = {'static': 'static', 'php': 'php', 'wordpress': 'php', 'laravel': 'php'}   # node apps need a port and start file: deploy them by hand
DB_KEYS = ('DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_HOST')
DB_IDENT_RE = re.compile(r'^[a-z_][a-z0-9_]{0,31}$')
PASSWORD_RE = re.compile(r'^[A-Za-z0-9@%^*_+=.,:?!-]{12,64}$')   # no quotes, backslash, $, # or spaces: safe in SQL, PHP and .env without escaping


# ------------------------------------------------------------------- run state
class Run:
    def __init__(self, title):
        self.id = uuid.uuid4().hex
        self.title = title
        self.steps, self.log, self.dropped = [], [], 0
        self.status = 'running'            # running | done | failed
        self.started, self.finished = time.time(), None
        self.report = None

    def add(self, group, label):
        step = {'id': len(self.steps), 'group': group, 'label': label, 'status': 'pending', 'detail': ''}
        self.steps.append(step)
        return step

    def say(self, line):
        self.log.append(str(line).rstrip())
        cap = perf.get()['job_lines']
        if len(self.log) > cap:                              # keep the newest lines only
            cut = len(self.log) - cap + cap // 4
            del self.log[:cut]
            self.dropped += cut

    def mark(self, step, status, detail=''):
        step['status'], step['detail'] = status, detail
        self.say('%s: %s%s' % (step['label'], status, ' - ' + detail if detail else ''))

    def finish(self):
        self.status = 'failed' if any(s['status'] == 'failed' for s in self.steps) else 'done'
        self.finished = time.time()
        perf.invalidate('status')

    def brief(self):
        cur = next((s for s in self.steps if s['status'] == 'running'), None)
        return {'id': self.id, 'title': self.title, 'status': self.status, 'step': cur['label'] if cur else '',
                'done': sum(s['status'] in ('done', 'skipped', 'failed') for s in self.steps), 'total': len(self.steps)}

    def view(self, since=0):
        return dict(self.brief(), steps=self.steps, lines=self.log[max(0, since - self.dropped):], next=self.dropped + len(self.log), started=self.started, finished=self.finished, report=self.report)


def prune_runs(keep):
    """Forget the oldest finished migrations beyond `keep`."""
    finished = sorted((r.started, k) for k, r in RUNS.items() if r.status != 'running')
    for _, key in finished[:max(0, len(finished) - keep)]:
        RUNS.pop(key, None)


def get(run_id):
    run_ = RUNS.get(run_id)
    if not run_:
        raise LookupError('unknown migration')
    return run_


def active():
    """The running migration, or else the most recent one (so the sidebar can show how it ended)."""
    runs = sorted(RUNS.values(), key=lambda r: r.started)
    running = [r for r in runs if r.status == 'running']
    return (running or runs[-1:] or [None])[-1]


def _step(run_, step, fn):
    run_.mark(step, 'running')
    try:
        run_.mark(step, 'done', fn() or '')
        return True
    except Exception as e:  # shown on the screen next to the step
        run_.mark(step, 'failed', str(e)[:300])
        return False


# ----------------------------------------------------------- pure helpers (tested)
def site_name(domain):
    name = re.sub(r'[^a-z0-9]+', '-', domain.lower()).strip('-')[:41].strip('-')
    if not config.NAME_RE.match(name) or name in deploy.PROTECTED:
        name = ('site-' + name)[:41].strip('-')
    return name


def db_names(old):
    """Database and user name on the new server: lower case letters, digits and _, at most 32 characters."""
    new = re.sub(r'[^a-z0-9_]', '_', old.lower())[:32]
    if not new or not re.match(r'^[a-z_]', new):
        new = ('db_' + new)[:32]
    return new, new


def _items(value, key):
    """The chosen items as {old name: options}. Plain names (no options) and {key: name, ...} objects are both accepted."""
    out = {}
    for item in value or []:
        entry = item if isinstance(item, dict) else {key: item}
        out[str(entry.get(key, ''))] = entry
    return out


def suggestions(inv):
    """The names the migration would use if the user changes nothing (the screen pre-fills its fields with these)."""
    return {'sites': {s['domain']: {'domain': s['domain'], 'site': site_name(s['domain'])} for s in inv['sites']},
            'databases': {d['name']: dict(zip(('name', 'user'), db_names(d['name']))) for d in inv['databases']}}


def _db_options(dbs, chosen):
    """New database name, user and password for each chosen database. A blank password is generated; users shared by several
    databases get one password. Raises ValueError (before anything starts) for unsafe or clashing names."""
    out, taken, users = {}, set(), {}
    for d in dbs:
        o = chosen[d['name']]
        auto_db, auto_user = db_names(d['name'])
        db, user = str(o.get('new_name') or auto_db).strip().lower(), str(o.get('user') or auto_user).strip().lower()
        for label, value in (('Database name', db), ('Database user', user)):
            if not DB_IDENT_RE.match(value):
                raise ValueError('%s for %s: use lower-case letters, digits and _ (at most 32, not starting with a digit)' % (label, d['name']))
        pw = str(o.get('password') or '')
        if pw and not PASSWORD_RE.match(pw):
            raise ValueError('Password for %s: 12 to 64 characters from letters, digits and @ %% ^ * _ + = . , : ? ! -' % d['name'])
        if (d['engine'], db) in taken:
            raise ValueError('Two databases would both be called %s on the new server' % db)
        taken.add((d['engine'], db))
        key = (d['engine'], user)
        if key in users:
            if d['engine'] == 'postgres':
                raise ValueError('PostgreSQL user %s is used by two databases here; give each its own user' % user)
            if pw and users[key]['explicit'] and pw != users[key]['password']:
                raise ValueError('Database user %s is shared by two databases but given different passwords' % user)
            if pw:
                users[key].update(password=pw, explicit=True)
        else:
            users[key] = {'password': pw or secrets.token_hex(16), 'explicit': bool(pw)}
        out[d['name']] = {'db': db, 'user': user, 'engine': d['engine']}
    for name, o in out.items():
        o['password'] = users[(o['engine'], o['user'])]['password']
    return out


def _site_options(sites, chosen):
    """Hostname, site name and redirect aliases for each chosen site (the old domain unless renamed)."""
    out, hosts, names = {}, set(), set()
    for s in sites:
        o = chosen[s['domain']]
        host = str(o.get('new_domain') or s['domain']).strip().lower()
        if not config.HOST_RE.match(host) or '.' not in host:
            raise ValueError('Hostname for %s looks wrong: %s' % (s['domain'], host))
        name = str(o.get('site') or '').strip().lower() or site_name(host)
        if not config.NAME_RE.match(name) or name in deploy.PROTECTED:
            raise ValueError('Site name for %s: use a-z, 0-9 and - only (and not a reserved name): %s' % (s['domain'], name))
        if host in hosts:
            raise ValueError('Two sites would both be served at %s' % host)
        if name in names:
            raise ValueError('Two sites would both be named %s' % name)
        hosts.add(host)
        names.add(name)
        aliases = [a[:-len(s['domain'])] + host if a.endswith('.' + s['domain']) else a for a in s['aliases']]
        out[s['domain']] = {'hostname': host, 'name': name, 'aliases': [a for a in aliases if a != host and config.HOST_RE.match(a)]}
    return out


def safe_join(base, sub):
    """base/sub, or None when sub is absolute or climbs out of base (archive path-traversal guard)."""
    parts = [p for p in sub.replace('\\', '/').split('/') if p not in ('', '.')]
    if not parts or '..' in parts or re.match(r'^[A-Za-z]:', parts[0]):
        return None
    dest = os.path.join(base, *parts)
    return dest if os.path.realpath(dest).startswith(os.path.realpath(base) + os.sep) else None


def _write(src, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as out:
        shutil.copyfileobj(src, out)


def extract_backup(path, roots, dbs):
    """One pass over a cPanel backup. roots: {'homedir/public_html': folder or None}; dbs: {'name': file}.
    Each file goes to the deepest matching root, so a nested site is not copied into its parent. None means 'not selected'."""
    ordered = sorted(roots, key=len, reverse=True)
    with tarfile.open(path, 'r:*') as tar:
        for m in tar:
            if not m.isfile():
                continue
            rel = m.name.split('/', 1)[1] if '/' in m.name else m.name
            if rel.startswith('mysql/') and rel.endswith('.sql') and rel[6:-4] in dbs:
                _write(tar.extractfile(m), dbs[rel[6:-4]])
                continue
            for root in ordered:
                if rel.startswith(root + '/'):
                    dest = roots[root] and safe_join(roots[root], rel[len(root) + 1:])
                    if dest:
                        _write(tar.extractfile(m), dest)
                    break


def _plain_sql(stream, member, dest):
    """Write a Plesk database dump as plain SQL: unpack a .tgz/.tar (first file), gunzip a .gz, or copy a .sql as it is."""
    low = member.lower()
    if low.endswith(('.tgz', '.tar.gz', '.tar')):
        with tarfile.open(fileobj=stream, mode='r|*') as t:
            for m in t:
                if m.isfile():
                    _write(t.extractfile(m), dest)
                    return
        raise ValueError('The dump archive %s is empty' % member)
    _write(gzip.GzipFile(fileobj=stream) if low.endswith('.gz') else stream, dest)


def extract_plesk(path, inv, site_dests, db_dests):
    """One pass over a Plesk backup. site_dests: {domain: folder}, db_dests: {name: file} (only the chosen ones)."""
    sites = {s['src']['member']: (s['src']['prefix'], site_dests[s['domain']]) for s in inv['sites'] if s['domain'] in site_dests}
    dbs = {d['member']: db_dests[d['name']] for d in inv['databases'] if d['name'] in db_dests}
    for name, size, opener in plesk.iter_files(path):
        if name in sites:
            prefix, folder = sites[name]
            head = prefix + '/' if prefix else ''
            for rel, _, inner in plesk.iter_tree(opener(), name):
                dest = rel.startswith(head) and safe_join(folder, rel[len(head):])
                if dest:
                    _write(inner(), dest)
        elif name in dbs:
            _plain_sql(opener(), name, dbs[name])


def unpack_tgz(tgz, stage):
    """Unpack a tar.gz of a folder into stage. Only plain files; links, devices and paths that escape stage are skipped."""
    with tarfile.open(tgz, 'r:gz') as tar:
        for m in tar:
            dest = safe_join(stage, m.name) if m.isfile() else None
            if dest:
                _write(tar.extractfile(m), dest)


def rewrite_credentials(stage, mapping):
    """Point wp-config.php and .env at the new database. mapping: {old_db: {db, user, password}}.
    Returns (changed files, warnings)."""
    changed, warnings = [], []
    wp = os.path.join(stage, 'wp-config.php')
    if os.path.isfile(wp):
        text = open(wp, encoding='utf-8', errors='replace').read()
        m = re.search(r"""define\s*\(\s*['"]DB_NAME['"]\s*,\s*(['"])(.*?)\1""", text, re.S)
        new = mapping.get(m.group(2)) if m else None
        if new:
            values = {'DB_NAME': new['db'], 'DB_USER': new['user'], 'DB_PASSWORD': new['password'], 'DB_HOST': 'localhost'}
            for key in DB_KEYS:
                text = re.sub(r"""(define\s*\(\s*['"]%s['"]\s*,\s*)(['"]).*?\2""" % key, lambda x: "%s'%s'" % (x.group(1), values[key]), text, flags=re.S)
            open(wp, 'w', encoding='utf-8').write(text)
            changed.append('wp-config.php')
        else:
            warnings.append('wp-config.php uses a database that is not being migrated: %s' % (m.group(2) if m else 'unreadable'))
    env = os.path.join(stage, '.env')
    if os.path.isfile(env):
        text = open(env, encoding='utf-8', errors='replace').read()
        db = re.search(r'(?m)^DB_DATABASE\s*=\s*(.*?)\s*$', text)
        conn = re.search(r'(?m)^DB_CONNECTION\s*=\s*(\w+)', text)
        new = mapping.get(db.group(1).strip('"\'')) if db else None
        if new and (not conn or conn.group(1) in ('mysql', 'mariadb')):
            values = {'DB_DATABASE': new['db'], 'DB_USERNAME': new['user'], 'DB_PASSWORD': new['password'], 'DB_HOST': '127.0.0.1'}
            for key, val in values.items():
                text = re.sub(r'(?m)^(%s\s*=).*$' % key, lambda x: x.group(1) + val, text)
            open(env, 'w', encoding='utf-8').write(text)
            changed.append('.env')
        elif db:
            warnings.append('.env uses a database that is not being migrated: %s' % db.group(1))
    return changed, warnings


# ------------------------------------------------------------ talking to servers
def _stream(cfg, remote, path):
    """Save the stdout of a remote command to a local file (binary safe: stderr is kept apart)."""
    with open(path, 'wb') as out:
        p = subprocess.Popen(command(cfg, remote), stdout=out, stderr=subprocess.PIPE, env=environment(cfg), creationflags=NOWIN)
        err = p.communicate()[1]
    if p.returncode:
        raise RuntimeError(err.decode('utf-8', 'replace').strip()[-300:] or 'the old server returned an error')


SUDO = '[ "$(id -u)" = 0 ] && S= || S=sudo; '


def _pull_tree(src, docroot, stage):
    tgz = stage + '.tgz'
    _stream(src, SUDO + '$S tar czf - -C %s .' % q(docroot), tgz)
    unpack_tgz(tgz, stage)
    os.remove(tgz)


def _dump_live(src, engine, name, path):
    inner = '$S mysqldump --single-transaction --quick --routines --triggers %s' % q(name) if engine == 'mysql' else 'sudo -u postgres pg_dump %s' % q(name)
    _stream(src, 'bash -c %s' % q(SUDO + 'set -o pipefail; %s | gzip -c' % inner), path)


def _feed(cfg, remote, path, gz, input_text=''):
    """Run a remote command whose stdin is the (optionally gzipped) dump; returns its output."""
    p = popen(cfg, remote)
    if input_text:
        p.stdin.write(input_text.encode())
    with (gzip.open(path, 'rb') if gz else open(path, 'rb')) as f:
        shutil.copyfileobj(f, p.stdin, CHUNK)
    p.stdin.close()
    out = p.stdout.read().decode('utf-8', 'replace')
    if p.wait():
        raise RuntimeError(out.strip()[-300:] or 'import failed')
    return out


def import_database(cfg, engine, old, path, gz, creds=None):
    """Create an empty database + user with a fresh password on this server and load the dump. Returns the new credentials."""
    db, user = (creds['db'], creds['user']) if creds else db_names(old)
    pw = (creds or {}).get('password') or secrets.token_hex(16)
    if engine == 'mysql':
        rc, out = run(cfg, 'sudo mysql -N -e %s' % q("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='%s'" % db))
        if rc:
            raise RuntimeError('cannot reach MySQL on the new server: %s' % out.strip()[-200:])
        if out.strip() != '0':
            raise RuntimeError('a database called %s already exists on the new server; nothing was changed' % db)
        setup = ("CREATE DATABASE `%s` CHARACTER SET utf8mb4; CREATE USER IF NOT EXISTS '%s'@'localhost' IDENTIFIED BY '%s'; "
                 "ALTER USER '%s'@'localhost' IDENTIFIED BY '%s'; GRANT ALL ON `%s`.* TO '%s'@'localhost'; FLUSH PRIVILEGES;") % (db, user, pw, user, pw, db, user)
        rc, out = run(cfg, 'sudo mysql', inp=setup.encode())
        if rc:
            raise RuntimeError('could not create the database: %s' % out.strip()[-200:])
        _feed(cfg, 'sudo mysql --default-character-set=utf8mb4 %s' % q(db), path, gz)
    else:
        rc, out = run(cfg, 'sudo -u postgres psql -tAc %s' % q("SELECT 1 FROM pg_database WHERE datname='%s'" % db))
        if rc or out.strip():
            raise RuntimeError('a database called %s already exists or PostgreSQL is not reachable; nothing was changed' % db)
        setup = 'CREATE ROLE "%s" LOGIN PASSWORD \'%s\'; CREATE DATABASE "%s" OWNER "%s";' % (user, pw, db, user)
        rc, out = run(cfg, 'sudo -u postgres psql -v ON_ERROR_STOP=1', inp=setup.encode())
        if rc:
            raise RuntimeError('could not create the database: %s' % out.strip()[-200:])
        _feed(cfg, 'sudo -u postgres psql -q -v ON_ERROR_STOP=1 -d %s' % q(db), path, gz)
    return {'db': db, 'user': user, 'password': pw, 'engine': engine}


# ------------------------------------------------------------------- the run
def start(cfg, req, user):
    """Validate the selection against a fresh look at the source, then run the migration in the background. Returns the run id."""
    with LOCK:
        if any(r.status == 'running' for r in RUNS.values()):
            raise ValueError('A migration is already running')
        prune_runs(perf.get()['run_keep'])
        inv = migrate.inspect(req)
        chosen_sites, chosen_dbs = _items(req.get('sites'), 'domain'), _items(req.get('databases'), 'name')
        sites = [s for s in inv['sites'] if s['domain'] in chosen_sites]
        dbs = [d for d in inv['databases'] if d['name'] in chosen_dbs]
        missing = (set(chosen_sites) - {s['domain'] for s in sites}) | (set(chosen_dbs) - {d['name'] for d in dbs})
        if missing:
            raise ValueError('Not found in the old account: ' + ', '.join(sorted(missing)))
        if not sites and not dbs:
            raise ValueError('Choose at least one site or database to move')
        db_opts, site_opts = _db_options(dbs, chosen_dbs), _site_options(sites, chosen_sites)
        run_ = Run('Migrating %d site%s and %d database%s' % (len(sites), '' if len(sites) == 1 else 's', len(dbs), '' if len(dbs) == 1 else 's'))
        RUNS[run_.id] = run_
    plan = {'connect': run_.add('Prepare', 'Connect to the new server'), 'parts': run_.add('Prepare', 'Check the required components'),
            'read': run_.add('Prepare', 'Read the old account'),
            'dbs': [(d, run_.add('Databases', 'Export %s' % d['name']), run_.add('Databases', 'Create and import %s' % d['name'])) for d in dbs],
            'sites': [(s, run_.add('Sites', 'Collect the files of %s' % s['domain']), run_.add('Sites', 'Update database credentials in %s' % s['domain']),
                       run_.add('Sites', 'Deploy %s' % s['domain'])) for s in sites],
            'report': run_.add('Finish', 'Prepare the report and the DNS checklist'), 'db_opts': db_opts, 'site_opts': site_opts}
    threading.Thread(target=_execute, args=(run_, cfg, req, inv, plan, user), daemon=True).start()
    return run_.id


def _execute(run_, cfg, req, inv, plan, user):
    stage = tempfile.mkdtemp(prefix='shiplane-migrate-')
    src = None if req.get('source') == 'backup' else config.check_server({'mode': 'ssh', 'auth': req.get('auth'), 'host': req.get('host', ''), 'port': req.get('port'), 'user': req.get('user', ''),
                                                                          'key': req.get('key', ''), 'password': req.get('password', '')})
    mapping, deployed, failed_dbs = {}, [], set()
    try:
        _prepare(run_, cfg, src, req, inv, plan, stage)
        for d, export, load in plan['dbs']:
            _database(run_, cfg, src, req, d, export, load, stage, mapping, failed_dbs, plan['db_opts'][d['name']])
        for s, collect, creds, dep in plan['sites']:
            _site(run_, cfg, src, req, s, collect, creds, dep, stage, mapping, deployed, user, plan['site_opts'][s['domain']])
        _step(run_, plan['report'], lambda: _report(run_, cfg, inv, plan, deployed))
    except Exception as e:  # something outside any step went wrong: say so instead of leaving the run hanging
        run_.say('ERROR: %s' % e)
        for s in run_.steps:
            if s['status'] in ('pending', 'running'):
                run_.mark(s, 'failed', 'stopped: %s' % e)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        run_.finish()


def _prepare(run_, cfg, src, req, inv, plan, stage):
    _step(run_, plan['connect'], lambda: _connect(cfg))
    _step(run_, plan['parts'], lambda: _ensure_components(run_, cfg, inv, plan))

    def read():
        if src is None and inv.get('format') == 'plesk':
            extract_plesk(os.path.abspath(os.path.expanduser(req['path'])), inv,
                          {s['domain']: os.path.join(stage, 'site-' + s['domain']) for s, *_ in plan['sites']},
                          {d['name']: os.path.join(stage, 'db-%s.sql' % d['name']) for d, *_ in plan['dbs'] if d['engine'] == 'mysql'})
            return 'unpacked the selected sites and databases'
        if src is None:
            roots = {'homedir' + s['docroot']: None for s in inv['sites']}
            for s, *_ in plan['sites']:
                roots['homedir' + s['docroot']] = os.path.join(stage, 'site-' + s['domain'])
            migrate_dbs = {d['name']: os.path.join(stage, 'db-%s.sql' % d['name']) for d, *_ in plan['dbs'] if d['engine'] == 'mysql'}
            extract_backup(os.path.abspath(os.path.expanduser(req['path'])), roots, migrate_dbs)
            return 'unpacked the selected sites and databases'
        rc, out = run(src, 'echo ok')
        if rc or 'ok' not in out:
            raise RuntimeError('cannot log in to the old server: %s' % explain_ssh(out, src)[-300:])
        return 'logged in to the old server'
    _step(run_, plan['read'], read)


def _connect(cfg):
    rc, out = run(cfg, 'echo ok; sudo -n true && echo sudo')
    if rc or 'ok' not in out:
        raise RuntimeError('cannot reach the new server: %s' % explain_ssh(out, cfg)[-300:])
    if 'sudo' not in out:
        raise RuntimeError('the new server user needs passwordless sudo (or must be root)')
    return 'connected'


def _ensure_components(run_, cfg, inv, plan):
    wanted = set(migrate.needed({'sites': [s for s, *_ in plan['sites']], 'databases': [d for d, *_ in plan['dbs']], 'mailboxes': 0}))
    missing = [c for c in migrate.capabilities(cfg, {'sites': [s for s, *_ in plan['sites']], 'databases': [d for d, *_ in plan['dbs']], 'mailboxes': 0}) if not c['installed'] and c['id'] in wanted]
    for c in missing:
        if not c['auto']:
            raise RuntimeError('%s is missing and cannot be installed automatically' % c['label'])
        run_.say('Installing %s ...' % c['label'])
        job = deploy.Job()
        job.say = lambda line: run_.say('  ' + line.rstrip())
        deploy._script_job(job, cfg, 'set -e\n%s\necho installed\n' % migrate.CAPABILITIES[c['id']][2])
        if not job.ok:
            raise RuntimeError('installing %s failed' % c['label'])
    return 'installed: ' + ', '.join(c['label'] for c in missing) if missing else 'everything needed is present'


def _database(run_, cfg, src, req, d, export, load, stage, mapping, failed_dbs, opts):
    gz = src is not None
    path = os.path.join(stage, 'db-%s.sql' % d['name']) + ('.gz' if gz else '')

    def do_export():
        if d['engine'] == 'postgres' and src is None:
            raise ValueError('PostgreSQL dumps inside a cPanel backup are not imported yet; use the live-server source')
        if src is not None:
            _dump_live(src, d['engine'], d['name'], path)
        elif not os.path.isfile(path):
            raise RuntimeError('the dump is missing from the backup')
        return '%.1f MB' % (os.path.getsize(path) / 1e6)
    if not _step(run_, export, do_export):
        failed_dbs.add(d['name'])
        run_.mark(load, 'skipped', 'the export failed')
        return

    def do_load():
        mapping[d['name']] = import_database(cfg, d['engine'], d['name'], path, gz, opts)
        return 'database %s, user %s' % (opts['db'], opts['user'])
    if not _step(run_, load, do_load):
        failed_dbs.add(d['name'])


def _site(run_, cfg, src, req, s, collect, creds, dep, stage, mapping, deployed, user, opts):
    folder = os.path.join(stage, 'site-' + s['domain'])
    kind = APP_TYPE.get(s['kind'])
    if kind is None:
        for st in (collect, creds, dep):
            run_.mark(st, 'skipped', 'Node apps need a start file and port: deploy this one from the Deploy page')
        return

    def do_collect():
        if src is not None:
            _pull_tree(src, s['docroot'], folder)
        if not os.path.isdir(folder) or not os.listdir(folder):
            raise RuntimeError('no files were found for this site')
        return '%d files' % sum(len(f) for _, _, f in os.walk(folder))
    if not _step(run_, collect, do_collect):
        run_.mark(creds, 'skipped', 'no files')
        run_.mark(dep, 'skipped', 'no files')
        return

    def do_creds():
        changed, warnings = rewrite_credentials(folder, mapping)
        for w in warnings:
            run_.say('  warning: ' + w)
        return 'updated ' + ', '.join(changed) if changed else ('no database settings found to update' + (' (see warnings)' if warnings else ''))
    _step(run_, creds, do_creds)

    def do_deploy():
        app = deploy.validate_app({'name': opts['name'], 'hostname': opts['hostname'], 'type': kind, 'source': folder, 'redirectFrom': opts['aliases']})
        job = deploy.Job()
        job.say = lambda line: run_.say('  ' + line.rstrip())
        deploy._deploy_job(job, app, cfg, user)
        if not job.ok:
            raise RuntimeError('the deploy did not finish; see the log')
        deployed.append(opts['hostname'])
        return 'serving http://%s' % opts['hostname']
    _step(run_, dep, do_deploy)


def _report(run_, cfg, inv, plan, deployed):
    manual = []
    if inv['mailboxes']:
        manual.append('%d mailboxes were not moved. Set up a mail server first, then copy the mail.' % inv['mailboxes'])
    manual += ['Review this cron job (paths point at the old home folder): %s' % c for c in inv['cron']]
    for s, *_ in plan['sites']:
        opts = plan['site_opts'][s['domain']]
        if opts['hostname'] in deployed and opts['hostname'] != s['domain'] and s['kind'] in ('wordpress', 'php', 'laravel'):
            manual.append('%s now runs at %s, but its database still contains the old address %s. Update it in the application (for WordPress: siteurl and home, or a search-replace) before it works there.'
                          % (s['domain'], opts['hostname'], s['domain']))
    manual += ['%s: %s' % (s['label'], s['detail']) for s in run_.steps if s['status'] == 'failed']
    run_.report = {'server_ip': cfg.get('host', ''), 'domains': deployed, 'manual': manual,
                   'databases': [{'old': d['name'], 'engine': d['engine'], 'new': plan['db_opts'][d['name']]['db'], 'user': plan['db_opts'][d['name']]['user']} for d, *_ in plan['dbs']]}
    return '%d site%s ready to switch' % (len(deployed), '' if len(deployed) == 1 else 's')
