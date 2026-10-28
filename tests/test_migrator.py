"""The migration engine: step tracking, safe unpacking, credential rewriting, database import, and whole runs against fake servers.

    python tests/test_migrator.py
"""
import gzip
import io
import os
import shutil
import sys
import tarfile
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeProc, FakeServer, FakeStream, run_tests  # noqa: E402
from coderunner import deploy, migrate, migrator  # noqa: E402
from scripts import make_sample_backup as sample  # noqa: E402

TMP = tempfile.mkdtemp()
ORIGINAL = {(m, n): getattr(m, n) for m, n in [(migrator, 'run'), (migrator, 'popen'), (migrator, '_feed'), (migrator, 'extract_backup'), (migrator, '_prepare'), (migrator, '_site'),
                                              (migrate, 'run'), (deploy, '_deploy_job'), (deploy, '_script_job'), (migrator.subprocess, 'Popen'), (migrator.tempfile, 'mkdtemp')]}
CAPS = dict(migrate.CAPABILITIES)


def restore():
    """Every test starts from the real functions: tests replace servers and processes with fakes."""
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)
    migrate.CAPABILITIES.clear()
    migrate.CAPABILITIES.update(CAPS)
    migrator.RUNS.clear()
CFG = {'mode': 'ssh', 'host': '203.0.113.50', 'user': 'bob', 'auth': 'password', 'password': 'x', 'key': ''}
BACKUP = sample.build(os.path.join(TMP, 'demo.tar.gz'))


def raises(fn, *args, text='', kind=Exception):
    try:
        fn(*args)
    except kind as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


def reset():
    migrator.RUNS.clear()


# ------------------------------------------------------------------ run state
def test_run_tracks_steps_and_progress():
    r = migrator.Run('t')
    a, b = r.add('G', 'one'), r.add('G', 'two')
    assert r.brief() == {'id': r.id, 'title': 't', 'status': 'running', 'step': '', 'done': 0, 'total': 2}
    r.mark(a, 'running')
    assert r.brief()['step'] == 'one'
    r.mark(a, 'done', 'ok')
    r.mark(b, 'skipped', 'why')
    assert r.brief()['done'] == 2
    r.finish()
    assert r.status == 'done' and r.finished
    v = r.view(1)
    assert v['lines'] == r.log[1:] and v['next'] == len(r.log) and v['steps'][1]['detail'] == 'why'
    r2 = migrator.Run('u')
    r2.mark(r2.add('G', 'x'), 'failed', 'boom')
    r2.finish()
    assert r2.status == 'failed'


def test_get_and_active():
    reset()
    assert migrator.active() is None
    raises(migrator.get, 'nope', text='unknown migration', kind=LookupError)
    old, new = migrator.Run('old'), migrator.Run('new')
    old.started -= 100
    old.status = 'done'
    migrator.RUNS.update({old.id: old, new.id: new})
    assert migrator.active() is new                      # a running one wins
    new.status = 'done'
    assert migrator.active() is new                      # else the latest
    assert migrator.get(old.id) is old
    reset()


def test_step_wrapper_reports_success_and_failure():
    r = migrator.Run('t')
    ok, bad = r.add('G', 'ok'), r.add('G', 'bad')
    assert migrator._step(r, ok, lambda: 'fine') is True and ok['status'] == 'done' and ok['detail'] == 'fine'
    assert migrator._step(r, ok, lambda: None) is True and ok['detail'] == ''

    def boom():
        raise RuntimeError('x' * 500)
    assert migrator._step(r, bad, boom) is False and bad['status'] == 'failed' and len(bad['detail']) == 300


# ------------------------------------------------------------------ pure helpers
def test_site_name():
    assert migrator.site_name('Demo-Blog.example') == 'demo-blog-example'
    assert migrator.site_name('a' * 60 + '.com') == 'a' * 41
    assert migrator.site_name('coderunner') == 'site-coderunner'          # reserved for this app
    assert migrator.site_name('-.-') == 'site'
    assert migrator.site_name('9lives.org') == '9lives-org'


def test_db_names():
    assert migrator.db_names('Demo_WP') == ('demo_wp', 'demo_wp')
    assert migrator.db_names('my-db$x') == ('my_db_x', 'my_db_x')
    assert migrator.db_names('1abc')[0] == 'db_1abc'
    assert len(migrator.db_names('x' * 80)[0]) == 32 and migrator.db_names('9' * 40)[0].startswith('db_')
    assert migrator.db_names('')[0] == 'db_'


def test_safe_join_blocks_escapes():
    base = os.path.join(TMP, 'base')
    os.makedirs(base, exist_ok=True)
    assert migrator.safe_join(base, 'a/b.txt') == os.path.join(base, 'a', 'b.txt')
    assert migrator.safe_join(base, './a//b') == os.path.join(base, 'a', 'b')
    for bad in ('../x', 'a/../../x', '/etc/passwd', 'C:/Windows/x', 'C:\\x', '', '.', '..'):
        assert migrator.safe_join(base, bad) is None or bad == '/etc/passwd', bad
    assert migrator.safe_join(base, 'a\\..\\..\\x') is None
    # a symlink inside base pointing outside must not be followed
    outside = os.path.join(TMP, 'outside')
    os.makedirs(outside, exist_ok=True)
    link = os.path.join(base, 'link')
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        return                                                                 # symlinks need privileges on Windows
    assert migrator.safe_join(base, 'link/evil.txt') is None


# --------------------------------------------------------------- unpacking
def make_tar(path, entries):
    with tarfile.open(path, 'w:gz') as t:
        for name, kind, data in entries:
            info = tarfile.TarInfo(name)
            if kind == 'file':
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
            elif kind == 'dir':
                info.type = tarfile.DIRTYPE
                t.addfile(info)
            else:
                info.type = tarfile.SYMTYPE
                info.linkname = data.decode()
                t.addfile(info)
    return path


def test_extract_backup_routes_files_to_the_deepest_site():
    stage = tempfile.mkdtemp(dir=TMP)
    roots = {'homedir/public_html': os.path.join(stage, 'blog'), 'homedir/public_html/shop': os.path.join(stage, 'shop'), 'homedir/landing': None}
    dbs = {'demo_wp': os.path.join(stage, 'wp.sql')}
    migrator.extract_backup(BACKUP, roots, dbs)
    assert os.path.isfile(os.path.join(stage, 'blog', 'wp-config.php'))
    assert os.path.isfile(os.path.join(stage, 'shop', 'index.php')) and not os.path.exists(os.path.join(stage, 'blog', 'shop'))
    assert not os.path.exists(os.path.join(stage, 'landing')) and 'wp_posts' in open(dbs['demo_wp']).read()
    assert not os.path.exists(os.path.join(stage, 'demo_shop.sql'))             # not selected


def test_extract_backup_ignores_traversal_and_links():
    path = make_tar(os.path.join(TMP, 'evil.tar.gz'), [
        ('x/homedir/site/ok.txt', 'file', b'ok'), ('x/homedir/site/../../../evil.txt', 'file', b'bad'),
        ('x/homedir/site/dir', 'dir', b''), ('x/homedir/site/link', 'link', b'/etc/passwd'), ('x/homedir/other/skip.txt', 'file', b'no')])
    stage = tempfile.mkdtemp(dir=TMP)
    migrator.extract_backup(path, {'homedir/site': os.path.join(stage, 'site')}, {})
    assert sorted(os.listdir(os.path.join(stage, 'site'))) == ['ok.txt']
    assert not os.path.exists(os.path.join(TMP, 'evil.txt')) and not os.path.exists(os.path.join(stage, 'evil.txt'))


def test_unpack_tgz_skips_unsafe_members():
    path = make_tar(os.path.join(TMP, 'tree.tgz'), [
        ('./index.html', 'file', b'<h1>'), ('./css/a.css', 'file', b'a'), ('../escape.txt', 'file', b'x'), ('./l', 'link', b'/etc'), ('./d', 'dir', b'')])
    stage = tempfile.mkdtemp(dir=TMP)
    migrator.unpack_tgz(path, stage)
    found = sorted(os.path.relpath(os.path.join(d, f), stage).replace('\\', '/') for d, _, fs in os.walk(stage) for f in fs)
    assert found == ['css/a.css', 'index.html'] and not os.path.exists(os.path.join(os.path.dirname(stage), 'escape.txt'))


# ----------------------------------------------------- rewriting credentials
NEW = {'demo_wp': {'db': 'demo_wp', 'user': 'demo_wp', 'password': 'NEWPW'}}


def folder(files):
    d = tempfile.mkdtemp(dir=TMP)
    for n, t in files.items():
        open(os.path.join(d, n), 'w').write(t)
    return d


def test_rewrite_wordpress_config():
    d = folder({'wp-config.php': sample.WP_CONFIG})
    changed, warnings = migrator.rewrite_credentials(d, NEW)
    text = open(os.path.join(d, 'wp-config.php')).read()
    assert changed == ['wp-config.php'] and warnings == []
    assert "'DB_USER', 'demo_wp'" in text and "'DB_PASSWORD', 'NEWPW'" in text and "'DB_HOST', 'localhost'" in text and 'old-secret' not in text
    assert "$table_prefix = 'wp_';" in text and 'wp-settings.php' in text            # the rest is untouched


def test_rewrite_wordpress_edge_cases():
    other = folder({'wp-config.php': sample.WP_CONFIG})
    changed, warnings = migrator.rewrite_credentials(other, {})
    assert changed == [] and 'not being migrated: demo_wp' in warnings[0] and 'old-secret' in open(os.path.join(other, 'wp-config.php')).read()
    odd = folder({'wp-config.php': '<?php // no database settings'})
    assert migrator.rewrite_credentials(odd, NEW)[1] == ['wp-config.php uses a database that is not being migrated: unreadable']
    dq = folder({'wp-config.php': 'define("DB_NAME", "demo_wp"); define( "DB_USER" , "u" ); define("DB_PASSWORD","p\'q"); define("DB_HOST","10.0.0.5");'})
    migrator.rewrite_credentials(dq, NEW)
    text = open(os.path.join(dq, 'wp-config.php')).read()
    assert "'NEWPW'" in text and '10.0.0.5' not in text


def test_rewrite_env_file():
    env = 'APP=x\nDB_CONNECTION=mysql\nDB_HOST=localhost\nDB_DATABASE="demo_wp"\nDB_USERNAME=old\nDB_PASSWORD=old # c\n'
    d = folder({'.env': env})
    changed, warnings = migrator.rewrite_credentials(d, NEW)
    text = open(os.path.join(d, '.env')).read()
    assert changed == ['.env'] and warnings == []
    assert 'DB_DATABASE=demo_wp' in text and 'DB_USERNAME=demo_wp' in text and 'DB_PASSWORD=NEWPW' in text and 'DB_HOST=127.0.0.1' in text and 'APP=x' in text
    nocon = folder({'.env': 'DB_DATABASE=demo_wp\nDB_PASSWORD=x\n'})
    assert migrator.rewrite_credentials(nocon, NEW)[0] == ['.env']                 # no DB_CONNECTION line: assume MySQL
    pg = folder({'.env': 'DB_CONNECTION=pgsql\nDB_DATABASE=demo_wp\n'})
    changed, warnings = migrator.rewrite_credentials(pg, NEW)
    assert changed == [] and warnings                                              # PostgreSQL apps are not rewritten
    unknown = folder({'.env': 'DB_DATABASE=other\n'})
    assert migrator.rewrite_credentials(unknown, NEW)[1] == ['.env uses a database that is not being migrated: other']
    nodb = folder({'.env': 'APP=x\n'})
    assert migrator.rewrite_credentials(nodb, NEW) == ([], [])
    assert migrator.rewrite_credentials(folder({}), NEW) == ([], [])


# -------------------------------------------------------- servers (fake)
def test_stream_saves_bytes_and_reports_errors():
    out = os.path.join(TMP, 's.bin')
    migrator.subprocess.Popen = FakeStream(lambda cmd: (b'\x00\x01binary', 0, b''))
    migrator._stream(CFG, 'echo', out)
    assert open(out, 'rb').read() == b'\x00\x01binary'
    migrator.subprocess.Popen = FakeStream(lambda cmd: (b'', 2, b'tar: /nope: Cannot open'))
    raises(migrator._stream, CFG, 'tar', out, text='Cannot open')
    migrator.subprocess.Popen = FakeStream(lambda cmd: (b'', 1, b''))
    raises(migrator._stream, CFG, 'tar', out, text='returned an error')


def test_pull_tree_and_dump_commands_are_quoted():
    tgz_dir = tempfile.mkdtemp(dir=TMP)
    src = make_tar(os.path.join(TMP, 'pull.tgz'), [('./index.html', 'file', b'hi')])
    migrator.subprocess.Popen = fake = FakeStream(lambda cmd: (open(src, 'rb').read(), 0, b''))
    stage = os.path.join(tgz_dir, 'site')
    migrator._pull_tree(CFG, '/var/www/my site/httpdocs', stage)
    assert os.path.isfile(os.path.join(stage, 'index.html')) and not os.path.exists(stage + '.tgz')
    assert "'/var/www/my site/httpdocs'" in fake.cmds[0] and 'sudo' in fake.cmds[0]
    migrator.subprocess.Popen = fake = FakeStream(lambda cmd: (gzip.compress(b'SELECT 1;'), 0, b''))
    migrator._dump_live(CFG, 'mysql', "evil'; rm -rf /", os.path.join(TMP, 'd.gz'))
    assert 'mysqldump' in fake.cmds[0] and "evil'" not in fake.cmds[0].replace("'\"'\"'", '')
    migrator._dump_live(CFG, 'postgres', 'pgdb', os.path.join(TMP, 'd2.gz'))
    assert 'pg_dump pgdb' in fake.cmds[1]


def test_feed_sends_the_dump_and_checks_the_result():
    plain = os.path.join(TMP, 'p.sql')
    open(plain, 'w').write('SELECT 1;')
    procs = []
    migrator.popen = lambda cfg, remote: procs.append(FakeProc(b'imported')) or procs[-1]
    assert migrator._feed(CFG, 'mysql db', plain, False, 'PRE;') == 'imported' and procs[0].stdin.data == b'PRE;SELECT 1;'
    gz = os.path.join(TMP, 'p.sql.gz')
    open(gz, 'wb').write(gzip.compress(b'SELECT 2;'))
    migrator._feed(CFG, 'mysql db', gz, True)
    assert procs[1].stdin.data == b'SELECT 2;'
    migrator.popen = lambda cfg, remote: FakeProc(b'ERROR 1064 near x', 1)
    raises(migrator._feed, CFG, 'mysql db', plain, False, text='ERROR 1064')
    migrator.popen = lambda cfg, remote: FakeProc(b'', 1)
    raises(migrator._feed, CFG, 'mysql db', plain, False, text='import failed')


def test_import_mysql_creates_user_and_loads_dump():
    srv = FakeServer().when('information_schema.schemata', 0, '0\n')
    fed = []
    migrator.run, migrator._feed = srv.run, lambda cfg, remote, path, gz, text='': fed.append((remote, path, gz)) or ''
    creds = migrator.import_database(CFG, 'mysql', 'Demo-WP', '/x.sql', False)
    assert creds['db'] == creds['user'] == 'demo_wp' and len(creds['password']) == 32 and creds['engine'] == 'mysql'
    setup = srv.ran('sudo mysql')[-1]['inp'].decode()
    assert 'CREATE DATABASE `demo_wp`' in setup and "IDENTIFIED BY '%s'" % creds['password'] in setup and 'GRANT ALL ON `demo_wp`.*' in setup
    assert fed == [('sudo mysql --default-character-set=utf8mb4 demo_wp', '/x.sql', False)]
    assert migrator.import_database(CFG, 'mysql', 'Demo-WP', '/x.sql', False)['password'] != creds['password']   # fresh password every time


def test_import_mysql_refuses_to_overwrite_and_reports_errors():
    migrator._feed = lambda *a, **k: ''
    exists = FakeServer().when('information_schema.schemata', 0, '1\n')
    migrator.run = exists.run
    raises(migrator.import_database, CFG, 'mysql', 'demo', '/x', False, text='already exists')
    assert not exists.ran('CREATE')
    migrator.run = FakeServer().when('information_schema.schemata', 1, 'ERROR 2002 cannot connect').run
    raises(migrator.import_database, CFG, 'mysql', 'demo', '/x', False, text='cannot reach MySQL')
    migrator.run = FakeServer().when('information_schema.schemata', 0, '0').when('sudo mysql', 1, 'access denied').run

    def fake(cfg, remote, inp=None, timeout=60):
        return (0, '0') if 'schemata' in remote else (1, 'access denied')
    migrator.run = fake
    raises(migrator.import_database, CFG, 'mysql', 'demo', '/x', False, text='could not create the database')


def test_import_postgres():
    fed = []
    migrator._feed = lambda cfg, remote, path, gz, text='': fed.append(remote) or ''
    srv = FakeServer().when('pg_database', 0, '')
    migrator.run = srv.run
    creds = migrator.import_database(CFG, 'postgres', 'Stats', '/x.gz', True)
    assert creds['engine'] == 'postgres' and 'CREATE ROLE "stats" LOGIN PASSWORD' in srv.ran('psql -v')[-1]['inp'].decode() and fed == ['sudo -u postgres psql -q -v ON_ERROR_STOP=1 -d stats']
    migrator.run = FakeServer().when('pg_database', 0, '1\n').run
    raises(migrator.import_database, CFG, 'postgres', 'Stats', '/x', False, text='already exists')

    def fake(cfg, remote, inp=None, timeout=60):
        return (0, '') if 'pg_database' in remote else (1, 'role exists')
    migrator.run = fake
    raises(migrator.import_database, CFG, 'postgres', 'Stats', '/x', False, text='could not create the database')


# ----------------------------------------------------------- whole runs
class World:
    """Fakes for everything a run touches. deployed records each site as it was handed to the deploy pipeline."""

    def __init__(self, **opt):
        self.opt = opt
        self.deployed, self.sources, self.imports, self.installs = [], {}, [], []
        self.new = FakeServer([('echo ok; sudo -n true', (0, 'ok\nsudo\n')), ('&& echo yes || echo no', (0, 'yes')), ('information_schema.schemata', (0, '0'))])
        for text, rc, out in opt.get('rules', []):
            self.new.when(text, rc, out)

    def install(self):
        o = self.opt
        migrator.run = self.new.run
        migrate.run = self.new.run
        migrator._feed = lambda cfg, remote, path, gz, text='': self.imports.append((remote, open(path, 'rb').read(), gz)) or ''

        def fake_deploy(job, app, cfg, user):
            self.deployed.append(app)
            self.sources[app['hostname']] = {n: open(os.path.join(app['source'], n), errors='replace').read() for n in os.listdir(app['source']) if os.path.isfile(os.path.join(app['source'], n))}
            job.say('uploading')
            job.ok = app['hostname'] not in o.get('deploy_fails', ())

        def fake_script(job, cfg, text):
            self.installs.append(text)
            job.say('apt output')
            job.ok = not o.get('install_fails')
        deploy._deploy_job, deploy._script_job = fake_deploy, fake_script
        return self


def wait(run_id, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        r = migrator.get(run_id)
        if r.status != 'running':
            return r
        time.sleep(0.02)
    raise AssertionError('migration did not finish')


def backup_request(sites, dbs, path=None):
    return {'source': 'backup', 'path': path or BACKUP, 'sites': sites, 'databases': dbs}


def steps(r, group=None):
    return {s['label']: s for s in r.steps if group is None or s['group'] == group}


def test_full_backup_migration_happy_path():
    reset()
    w = World().install()
    r = wait(migrator.start(CFG, backup_request(['demo-blog.example', 'landing.example', 'crafts.example'], ['demo_wp', 'demo_shop']), 'bob'))
    assert r.status == 'done', [(s['label'], s['status'], s['detail']) for s in r.steps if s['status'] != 'done']
    assert all(s['status'] == 'done' for s in r.steps)
    by_host = {a['hostname']: a for a in w.deployed}
    assert set(by_host) == {'demo-blog.example', 'landing.example', 'crafts.example'}
    assert by_host['demo-blog.example']['type'] == 'php' and by_host['landing.example']['type'] == 'static'
    assert by_host['demo-blog.example']['name'] == 'demo-blog-example' and by_host['demo-blog.example']['redirectFrom'] == ['www.demo-blog.example']
    # credentials in the migrated files point at the new databases, with passwords that only exist on the new server
    wp = w.sources['demo-blog.example']['wp-config.php']
    assert "'DB_NAME', 'demo_wp'" in wp and 'old-secret-password' not in wp
    pw = wp.split("'DB_PASSWORD', '")[1].split("'")[0]
    assert len(pw) == 32 and pw in ' '.join(c['inp'].decode() for c in w.new.ran('sudo mysql') if c['inp'])
    assert 'old-shop-password' not in w.sources['crafts.example']['.env'] and 'DB_DATABASE=demo_shop' in w.sources['crafts.example']['.env']
    assert [i[1].decode().split('\n')[0] for i in w.imports if b'orders' in i[1]] and len(w.imports) == 2
    # secrets never reach the log or the report
    text = '\n'.join(r.log) + str(r.report)
    assert pw not in text and 'NEWPW' not in text
    assert sorted(r.report['domains']) == ['crafts.example', 'demo-blog.example', 'landing.example'] and r.report['server_ip'] == '203.0.113.50'
    assert any('mailboxes were not moved' in m for m in r.report['manual']) and len([m for m in r.report['manual'] if 'cron job' in m]) == 2
    assert sorted(d['old'] for d in r.report['databases']) == ['demo_shop', 'demo_wp']


def test_status_updates_while_running():
    reset()
    w = World().install()
    seen = []
    orig = migrator._site

    def slow(*a, **k):
        seen.append(migrator.active().brief())
        return orig(*a, **k)
    migrator._site = slow
    try:
        wait(migrator.start(CFG, backup_request(['landing.example'], []), 'bob'))
    finally:
        migrator._site = orig
    assert seen and seen[0]['status'] == 'running' and 0 < seen[0]['done'] < seen[0]['total']


def test_start_validation():
    reset()
    World().install()
    raises(migrator.start, CFG, backup_request(['nope.example'], []), 'bob', text='Not found in the old account: nope.example', kind=ValueError)
    raises(migrator.start, CFG, backup_request([], ['nodb']), 'bob', text='nodb', kind=ValueError)
    raises(migrator.start, CFG, backup_request([], []), 'bob', text='Choose at least one', kind=ValueError)
    raises(migrator.start, CFG, backup_request(['x'], [], path=os.path.join(TMP, 'none.tar.gz')), 'bob', text='cPanel or Plesk backup file', kind=ValueError)
    assert not migrator.RUNS


def test_only_one_migration_at_a_time():
    reset()
    World().install()
    busy = migrator.Run('busy')
    migrator.RUNS[busy.id] = busy
    raises(migrator.start, CFG, backup_request(['landing.example'], []), 'bob', text='already running', kind=ValueError)
    busy.status = 'done'
    wait(migrator.start(CFG, backup_request(['landing.example'], []), 'bob'))


def test_new_server_unreachable_or_without_sudo():
    reset()
    w = World(rules=[('echo ok; sudo -n true', 255, 'ssh: connect timed out')]).install()
    r = wait(migrator.start(CFG, backup_request(['landing.example'], []), 'bob'))
    assert r.status == 'failed' and 'cannot reach the new server' in steps(r)['Connect to the new server']['detail']
    reset()
    World(rules=[('echo ok; sudo -n true', 0, 'ok\n')]).install()
    r = wait(migrator.start(CFG, backup_request(['landing.example'], []), 'bob'))
    assert 'passwordless sudo' in steps(r)['Connect to the new server']['detail']


def test_missing_components_are_installed_first():
    reset()
    w = World(rules=[('php-fpm', 0, 'no'), ('mysqld', 0, 'no')]).install()
    r = wait(migrator.start(CFG, backup_request(['demo-blog.example'], ['demo_wp']), 'bob'))
    assert r.status == 'done' and len(w.installs) == 2 and any('php-fpm' in t for t in w.installs) and any('mariadb-server' in t for t in w.installs)
    detail = steps(r)['Check the required components']['detail']
    assert 'PHP' in detail and 'MySQL' in detail
    assert any('Installing PHP' in line for line in r.log) and any('apt output' in line for line in r.log)


def test_failed_or_impossible_installs_are_reported():
    reset()
    World(rules=[('php-fpm', 0, 'no')], install_fails=True).install()
    r = wait(migrator.start(CFG, backup_request(['crafts.example'], []), 'bob'))
    assert 'installing PHP' in steps(r)['Check the required components']['detail']
    reset()
    World(rules=[('php-fpm', 0, 'no')]).install()
    saved = migrate.CAPABILITIES['php']
    migrate.CAPABILITIES['php'] = saved[:2] + (None, 'do it yourself')
    try:
        r = wait(migrator.start(CFG, backup_request(['crafts.example'], []), 'bob'))
    finally:
        migrate.CAPABILITIES['php'] = saved
    assert 'cannot be installed automatically' in steps(r)['Check the required components']['detail']


def test_existing_database_is_never_overwritten_and_site_still_deploys():
    reset()
    w = World(rules=[('information_schema.schemata', 0, '1\n')]).install()
    r = wait(migrator.start(CFG, backup_request(['demo-blog.example'], ['demo_wp']), 'bob'))
    s = steps(r)
    assert s['Create and import demo_wp']['status'] == 'failed' and 'already exists' in s['Create and import demo_wp']['detail']
    assert not w.imports and not [c for c in w.new.calls if c['inp'] and b'CREATE DATABASE' in c['inp']]
    assert 'old-secret-password' in w.sources['demo-blog.example']['wp-config.php']          # unchanged, so the failure is visible
    assert 'not being migrated' in ' '.join(r.log) and r.status == 'failed'
    assert any('Create and import demo_wp' in m for m in r.report['manual'])


def test_postgres_in_a_backup_is_flagged_not_silently_skipped():
    reset()
    World().install()
    r = wait(migrator.start(CFG, backup_request([], ['demo_stats']), 'bob'))
    s = steps(r)
    assert s['Export demo_stats']['status'] == 'failed' and 'live-server source' in s['Export demo_stats']['detail']
    assert s['Create and import demo_stats']['status'] == 'skipped' and r.status == 'failed'


def test_import_error_marks_the_step_failed():
    reset()
    World().install()
    migrator._feed = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('ERROR 1064 syntax'))
    r = wait(migrator.start(CFG, backup_request([], ['demo_wp']), 'bob'))
    assert 'ERROR 1064' in steps(r)['Create and import demo_wp']['detail']


def test_node_apps_are_skipped_with_a_reason():
    reset()
    w = World().install()
    r = wait(migrator.start(CFG, backup_request(['api.example'], []), 'bob'))
    s = steps(r)
    assert [s[k]['status'] for k in s if 'api.example' in k] == ['skipped'] * 3 and 'Deploy page' in s['Deploy api.example']['detail']
    assert not w.deployed and r.status == 'done'


def test_site_without_files_stops_before_deploy():
    reset()
    w = World().install()
    path = sample.build(os.path.join(TMP, 'nofiles.tar.gz'))
    with tarfile.open(path) as src, tarfile.open(os.path.join(TMP, 'nofiles2.tar.gz'), 'w:gz') as dst:
        for m in src:
            if 'homedir/landing' not in m.name:
                dst.addfile(m, src.extractfile(m) if m.isfile() else None)
    r = wait(migrator.start(CFG, backup_request(['landing.example'], [], path=os.path.join(TMP, 'nofiles2.tar.gz')), 'bob'))
    s = steps(r)
    assert 'no files' in s['Collect the files of landing.example']['detail'] and s['Deploy landing.example']['status'] == 'skipped' and not w.deployed


def test_deploy_failure_and_validation_failure():
    reset()
    w = World(deploy_fails=('landing.example',)).install()
    r = wait(migrator.start(CFG, backup_request(['landing.example', 'crafts.example'], []), 'bob'))
    s = steps(r)
    assert s['Deploy landing.example']['status'] == 'failed' and s['Deploy crafts.example']['status'] == 'done'
    assert r.report['domains'] == ['crafts.example'] and r.status == 'failed'
    reset()
    World().install()
    path = sample.build(os.path.join(TMP, 'badhost.tar.gz'), {'userdata/bad_host.example': 'documentroot: /home/demo/landing\nservername: bad_host\nhomedir: /home/demo\n'})
    raises(migrator.start, CFG, backup_request(['bad_host'], [], path=path), 'bob', text='Hostname for bad_host looks wrong', kind=ValueError)   # refused before anything runs
    assert not migrator.RUNS


def test_unexpected_crash_fails_open_steps_instead_of_hanging():
    reset()
    World().install()
    saved = migrator._prepare
    migrator._prepare = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('disk full'))
    try:
        r = wait(migrator.start(CFG, backup_request(['landing.example'], []), 'bob'))
    finally:
        migrator._prepare = saved
    assert r.status == 'failed' and all(s['status'] == 'failed' and 'disk full' in s['detail'] for s in r.steps) and r.finished


def test_staging_folder_is_removed_afterwards():
    reset()
    World().install()
    made = []
    orig = tempfile.mkdtemp
    migrator.tempfile.mkdtemp = lambda **k: made.append(orig(**k)) or made[-1]
    try:
        wait(migrator.start(CFG, backup_request(['landing.example'], ['demo_wp']), 'bob'))
    finally:
        migrator.tempfile.mkdtemp = orig
    assert made and not any(os.path.exists(m) for m in made)


# ------------------------------------------------------------ live server source
def live_world(**opt):
    w = World(**opt).install()
    old = FakeServer([('echo ok', (0, 'ok\n'))])
    inv = {'panel': 'cPanel', 'accounts': ['u'], 'cron': [], 'mailboxes': 0, 'sites': [
        {'domain': 'a.com', 'aliases': ['www.a.com'], 'docroot': '/home/u/public_html', 'size': 10, 'kind': 'wordpress', 'php': ''},
        {'domain': 'b.com', 'aliases': [], 'docroot': '/home/u/b', 'size': 10, 'kind': 'static', 'php': ''}],
        'databases': [{'name': 'u_wp', 'engine': 'mysql', 'size': 5}, {'name': 'u_pg', 'engine': 'postgres', 'size': 5}]}
    tree = make_tar(os.path.join(TMP, 'live.tgz'), [('./wp-config.php', 'file', sample.WP_CONFIG.replace('demo_wp', 'u_wp').encode()), ('./index.php', 'file', b'<?php')])

    def streams(cmd):
        if 'tar czf' in cmd:
            return (b'', 1, b'tar: permission denied') if opt.get('tar_fails') else (open(tree, 'rb').read(), 0, b'')
        return gzip.compress(b'-- dump of ' + (b'pg' if 'pg_dump' in cmd else b'mysql')), 0, b''
    migrator.subprocess.Popen = FakeStream(streams)
    migrate.run = lambda cfg, remote, inp=None, timeout=60: (old.run(cfg, remote, inp, timeout) if 'discover' not in remote and b'cpanel' not in (inp or b'').lower()[:0] else (0, ''))
    import json
    base = migrate.run

    def run_any(cfg, remote, inp=None, timeout=60):
        if inp and b'userdata' in inp:
            return 0, json.dumps(inv)
        if cfg.get('host') == 'old.example.com':
            return old.run(cfg, remote, inp, timeout)
        return w.new.run(cfg, remote, inp, timeout)
    migrate.run = run_any
    migrator.run = run_any
    return w, old


LIVE_REQ = {'source': 'live', 'host': 'old.example.com', 'user': 'root', 'auth': 'password', 'password': 'oldpw', 'key': ''}


def test_live_server_migration_with_password_login():
    reset()
    w, old = live_world()
    r = wait(migrator.start(CFG, dict(LIVE_REQ, sites=['a.com', 'b.com'], databases=['u_wp', 'u_pg']), 'bob'))
    assert r.status == 'done', [(s['label'], s['status'], s['detail']) for s in r.steps if s['status'] != 'done']
    assert {a['hostname'] for a in w.deployed} == {'a.com', 'b.com'}
    assert "'DB_NAME', 'u_wp'" in w.sources['a.com']['wp-config.php'] and 'old-secret' not in w.sources['a.com']['wp-config.php']
    assert [i[2] for i in w.imports] == [True, True]                                 # live dumps arrive gzipped
    assert any('postgres' in i[0] for i in w.imports) and 'oldpw' not in '\n'.join(r.log)


def test_live_server_login_and_copy_failures():
    reset()
    w, old = live_world()
    old.when('echo ok', 255, 'Permission denied (publickey,password)')
    r = wait(migrator.start(CFG, dict(LIVE_REQ, sites=['b.com'], databases=[]), 'bob'))
    assert 'cannot log in to the old server' in steps(r)['Read the old account']['detail']
    reset()
    w, old = live_world(tar_fails=True)
    r = wait(migrator.start(CFG, dict(LIVE_REQ, sites=['b.com'], databases=[]), 'bob'))
    assert 'permission denied' in steps(r)['Collect the files of b.com']['detail'] and not w.deployed


def test_missing_dump_in_a_backup():
    reset()
    World().install()
    saved = migrator.extract_backup
    migrator.extract_backup = lambda *a, **k: None                                  # nothing was unpacked
    try:
        r = wait(migrator.start(CFG, backup_request([], ['demo_wp']), 'bob'))
    finally:
        migrator.extract_backup = saved
    assert 'missing from the backup' in steps(r)['Export demo_wp']['detail']


# ------------------------------------------------------------ Plesk backups
PLESK = sample.build_plesk(os.path.join(TMP, 'plesk.tgz'))


def test_extract_plesk_unpacks_only_the_chosen_items():
    inv = migrate.inspect_backup(PLESK)
    stage = tempfile.mkdtemp(dir=TMP)
    migrator.extract_plesk(PLESK, inv, {'shop.example': os.path.join(stage, 'shop')}, {'shop_wp': os.path.join(stage, 'wp.sql')})
    assert sorted(os.listdir(os.path.join(stage, 'shop'))) == ['index.php', 'wp-config.php', 'wp-content']          # cgi-bin is outside httpdocs
    assert 'wp_posts' in open(os.path.join(stage, 'wp.sql')).read() and not os.path.exists(os.path.join(stage, 'blog'))
    assert not os.path.exists(os.path.join(stage, 'stats.sql'))


def test_extract_plesk_ignores_traversal_inside_the_user_data_archive():
    evil = os.path.join(TMP, 'evil-plesk.tgz')
    sample.build_plesk(evil, {'domains/shop.example/backup_user-data_2601011200.tgz': sample._tgz({'httpdocs/ok.txt': 'ok', 'httpdocs/../../escape.txt': 'bad', 'other/skip.txt': 'x'})})
    inv = migrate.inspect_backup(evil)
    stage = tempfile.mkdtemp(dir=TMP)
    migrator.extract_plesk(evil, inv, {'shop.example': os.path.join(stage, 'shop')}, {})
    assert os.listdir(os.path.join(stage, 'shop')) == ['ok.txt'] and not os.path.exists(os.path.join(stage, 'escape.txt'))


def test_plain_sql_handles_every_dump_format():
    out = os.path.join(TMP, 'dump.sql')
    migrator._plain_sql(io.BytesIO(sample._tgz({'a.sql': 'FROM TGZ'})), 'x/db.tgz', out)
    assert open(out).read() == 'FROM TGZ'
    migrator._plain_sql(io.BytesIO(gzip.compress(b'FROM GZ')), 'x/db.sql.gz', out)
    assert open(out).read() == 'FROM GZ'
    migrator._plain_sql(io.BytesIO(b'PLAIN'), 'x/db.sql', out)
    assert open(out).read() == 'PLAIN'
    empty = io.BytesIO()
    with tarfile.open(fileobj=empty, mode='w:gz'):
        pass
    raises(migrator._plain_sql, io.BytesIO(empty.getvalue()), 'x/empty.tgz', out, text='is empty', kind=ValueError)


def test_plesk_migration_end_to_end():
    reset()
    w = World().install()
    r = wait(migrator.start(CFG, backup_request(['shop.example', 'blog.shop.example'], ['shop_wp'], path=PLESK), 'bob'))
    assert r.status == 'done', [(s['label'], s['status'], s['detail']) for s in r.steps if s['status'] != 'done']
    wp = w.sources['shop.example']['wp-config.php']
    assert "'DB_NAME', 'shop_wp'" in wp and 'old-secret-password' not in wp and "'DB_HOST', 'localhost'" in wp
    assert {a['hostname']: a['type'] for a in w.deployed} == {'shop.example': 'php', 'blog.shop.example': 'static'}
    assert len(w.imports) == 1 and b'wp_posts' in w.imports[0][1] and w.imports[0][2] is False
    assert r.report['manual'] and any('mailboxes' in m for m in r.report['manual'])


def test_plesk_postgres_dump_is_flagged():
    reset()
    World().install()
    r = wait(migrator.start(CFG, backup_request([], ['shop_stats'], path=PLESK), 'bob'))
    assert 'live-server source' in steps(r)['Export shop_stats']['detail'] and r.status == 'failed'


if __name__ == '__main__':
    run_tests(globals(), before=restore)
