"""Reading an old hosting account and checking the new server: cPanel backups, live servers, readiness (no server needed).

    python tests/test_migrate.py
"""
import io
import json
import os
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeServer, run_tests  # noqa: E402
from coderunner import migrate  # noqa: E402
from scripts import make_sample_backup as sample  # noqa: E402

TMP = tempfile.mkdtemp()
CFG = {'mode': 'ssh', 'host': 'new.example.com'}


def backup(name='b.tar.gz', members=None, extra=None):
    path = os.path.join(TMP, name)
    if members is None:
        return sample.build(path, extra)
    with tarfile.open(path, 'w:gz') as t:
        for n, text in members.items():
            data = text.encode()
            info = tarfile.TarInfo(n)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return path


def raises(fn, *args, text=''):
    try:
        fn(*args)
    except (ValueError, LookupError, RuntimeError) as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


# ----------------------------------------------------------------- cPanel backup
def test_sample_backup_inventory():
    inv = migrate.inspect_cpmove(backup())
    sites = {s['domain']: s for s in inv['sites']}
    assert set(sites) == {'demo-blog.example', 'shop.demo-blog.example', 'landing.example', 'api.example', 'crafts.example'}
    assert sites['demo-blog.example']['kind'] == 'wordpress' and sites['demo-blog.example']['aliases'] == ['www.demo-blog.example']
    assert sites['shop.demo-blog.example']['kind'] == 'php'
    assert sites['landing.example']['kind'] == 'static' and sites['api.example']['kind'] == 'node'
    assert sites['crafts.example']['kind'] == 'php'
    assert sites['shop.demo-blog.example']['size'] == len('<?php echo "Shop"; ?>\n') + len('{"name": "demo/shop"}\n')   # not counted in the parent
    assert sites['demo-blog.example']['php'] == 'ea-php82'
    assert {(d['name'], d['engine']) for d in inv['databases']} == {('demo_wp', 'mysql'), ('demo_shop', 'mysql'), ('demo_stats', 'postgres')}
    assert inv['mailboxes'] == 2 and len(inv['cron']) == 2 and inv['accounts'] == ['demo'] and inv['panel'] == 'cPanel backup'


def test_notes_describe_what_is_missing():
    notes = ' '.join(migrate.inspect_cpmove(backup())['notes'])
    assert 'PHP' in notes and 'MySQL' in notes and 'mailboxes' in notes and 'cron' in notes


def test_plain_static_account_has_no_notes():
    path = backup('plain.tar.gz', {'x/userdata/a.com': 'documentroot: /home/u/public_html\nservername: a.com\n', 'x/homedir/public_html/index.html': 'hi'})
    inv = migrate.inspect_cpmove(path)
    assert inv['notes'] == [] and inv['sites'][0]['kind'] == 'static' and inv['accounts'] == []


def test_member_handling_edge_cases():
    members = {
        'x/userdata/a.com': 'documentroot: /elsewhere/www\nservername: A.com\nserveralias: a.com www.a.com\n',   # outside the home folder; alias equal to the domain is dropped
        'x/userdata/broken.com': 'servername: broken.com\n',                                                      # no document root: ignored
        'x/userdata/x.com.cache': 'documentroot: /home/u/x\nservername: x.com\n',
        'x/userdata/big': 'a: b\n' + 'x' * (migrate.MAX_SMALL + 1),                                               # too large: ignored
        'x/psql/pg.sql': 'dump',
        'x/mysql/mysql.sql': 'grants',
        'x/mysql/sub/dir.sql': 'ignored',
        'x/cron/u': '# c\n\n* * * * * run\n@daily other\nnot a job\n',
        'x/cp/u': 'IP=1\n',
        'toplevelfile': 'no folder',
    }
    inv = migrate.inspect_cpmove(backup('edge.tar.gz', members))
    assert [s['domain'] for s in inv['sites']] == ['a.com'] and inv['sites'][0]['aliases'] == ['www.a.com']
    assert inv['sites'][0]['docroot'] == '/www'
    assert inv['databases'] == [{'name': 'pg', 'engine': 'postgres', 'size': 4}]
    assert inv['cron'] == ['* * * * * run', '@daily other'] and inv['accounts'] == ['u']


def test_php_detection_in_subfolder_and_markers():
    members = {'x/userdata/a.com': 'documentroot: /home/u/a\nservername: a.com\nhomedir: /home/u\n',
               'x/userdata/b.com': 'documentroot: /home/u/b\nservername: b.com\nhomedir: /home/u\n',
               'x/homedir/a/lib/helper.php': '<?php', 'x/homedir/b/artisan': '#!/usr/bin/env php'}
    kinds = {s['domain']: s['kind'] for s in migrate.inspect_cpmove(backup('k.tar.gz', members))['sites']}
    assert kinds == {'a.com': 'php', 'b.com': 'laravel'}


def test_rejects_bad_backups():
    raises(migrate.inspect_cpmove, os.path.join(TMP, 'missing.tar.gz'), text='cPanel backup file')
    txt = os.path.join(TMP, 'x.txt')
    open(txt, 'w').write('hi')
    raises(migrate.inspect_cpmove, txt, text='cPanel backup file')
    bad = os.path.join(TMP, 'bad.tar.gz')
    open(bad, 'wb').write(b'not a tar at all')
    raises(migrate.inspect_cpmove, bad, text='Could not read the backup')
    raises(migrate.inspect_cpmove, backup('other.tar.gz', {'x/readme.txt': 'hello'}), text='does not look like a cPanel')
    raises(migrate.inspect_cpmove, '', text='cPanel backup file')


# ------------------------------------------------------------------ live server
LIVE = {'panel': 'Plesk', 'accounts': [], 'sites': [{'domain': 'a.com', 'aliases': [], 'docroot': '/var/www/vhosts/a.com/httpdocs', 'size': 5, 'kind': 'static', 'php': ''}],
        'databases': [], 'cron': [], 'mailboxes': 0}


def live(monkey_server):
    migrate.run = monkey_server.run


def test_inspect_live_password_login_and_dispatch():
    srv = FakeServer().when('python3', 0, 'warning text\n' + json.dumps(LIVE) + '\n')
    live(srv)
    inv = migrate.inspect({'source': 'live', 'host': 'old.example.com', 'user': 'root', 'auth': 'password', 'password': 'pw', 'key': ''})
    assert inv['panel'] == 'Plesk' and inv['notes'] == []
    assert srv.calls[0]['inp'] and b'/var/cpanel/userdata' in srv.calls[0]['inp']


def test_inspect_live_errors():
    live(FakeServer().when('python3', 255, 'Permission denied'))
    raises(migrate.inspect_live, {'host': 'old.example.com', 'user': 'root', 'auth': 'password', 'password': 'x'}, text='Permission denied')
    live(FakeServer().when('python3', 0, 'no json here'))
    raises(migrate.inspect_live, {'host': 'old.example.com', 'user': 'root', 'auth': 'password', 'password': 'x'}, text='no json here')
    live(FakeServer().when('python3', 1, ''))
    raises(migrate.inspect_live, {'host': 'old.example.com', 'user': 'root', 'auth': 'password', 'password': 'x'}, text='could not read')
    raises(migrate.inspect_live, {'host': 'bad host!', 'user': 'root', 'auth': 'password', 'password': 'x'}, text='host looks wrong')
    raises(migrate.inspect_live, {'host': 'old.example.com', 'user': 'root', 'auth': 'password', 'password': ''}, text='Type the SSH password')
    raises(migrate.inspect_live, {'host': 'old.example.com', 'user': 'root', 'key': '-oProxy'}, text='key path looks wrong')


# --------------------------------------------------------- readiness of the new server
INV = {'sites': [{'kind': 'wordpress'}, {'kind': 'static'}], 'databases': [{'engine': 'mysql'}, {'engine': 'postgres'}], 'mailboxes': 3, 'cron': []}


def test_needed_components():
    assert migrate.needed(INV) == ['php', 'mysql', 'postgres', 'mail']
    assert migrate.needed({'sites': [{'kind': 'static'}], 'databases': [], 'mailboxes': 0}) == []
    assert migrate.needed({'sites': [{'kind': 'laravel'}], 'databases': [], 'mailboxes': 0}) == ['php']


def test_capabilities_report_installed_and_missing():
    srv = FakeServer().when('php-fpm', 0, 'no').when('mysqld', 0, 'yes').when('psql', 0, 'yes').when('postfix', 0, 'no')
    migrate.run = srv.run
    caps = {c['id']: c for c in migrate.capabilities(CFG, INV)}
    assert not caps['php']['installed'] and caps['php']['auto'] and caps['mysql']['installed']
    assert not caps['mail']['installed'] and not caps['mail']['auto'] and 'DKIM' in caps['mail']['advice']
    assert migrate.capabilities(CFG, {'sites': [], 'databases': [], 'mailboxes': 0}) == []


def test_install_capability_rules():
    started = []
    migrate.deploy.start_script_job = lambda cfg, text: started.append(text) or 'job1'
    assert migrate.install_capability(CFG, 'mysql') == 'job1' and 'mariadb-server' in started[0] and started[0].startswith('set -e')
    assert 'php-fpm' in migrate.CAPABILITIES['php'][2] and 'php-fpm.sock' in migrate.CAPABILITIES['php'][2]
    raises(migrate.install_capability, CFG, 'mail', text='cannot be installed automatically')
    raises(migrate.install_capability, CFG, 'rm -rf', text='cannot be installed automatically')


def test_directory_entries_and_unknown_panel():
    path = os.path.join(TMP, 'dirs.tar.gz')
    with tarfile.open(path, 'w:gz') as t:
        d = tarfile.TarInfo('x/userdata')
        d.type = tarfile.DIRTYPE
        t.addfile(d)
        data = b'documentroot: /home/u/www\nservername: a.com\n'
        f = tarfile.TarInfo('x/userdata/a.com')
        f.size = len(data)
        t.addfile(f, io.BytesIO(data))
    assert [s['domain'] for s in migrate.inspect_cpmove(path)['sites']] == ['a.com']          # folders inside the archive are skipped
    notes = migrate.finish({'panel': 'unknown', 'sites': [], 'databases': [], 'mailboxes': 0, 'cron': []})['notes']
    assert len(notes) == 1 and 'full account backup' in notes[0]                                # a shared-hosting login cannot be read live


def test_live_inspection_of_a_login_without_panel_data_explains_the_alternative():
    empty = dict(LIVE, panel='unknown', sites=[])
    live(FakeServer().when('python3', 0, json.dumps(empty)))
    inv = migrate.inspect_live({'host': 'old.example.com', 'user': 'shareduser', 'auth': 'password', 'password': 'x'})
    assert inv['sites'] == [] and 'backup' in inv['notes'][0]


def test_live_timeout_is_explained_in_plain_language():
    live(FakeServer().when('python3', 255, 'ssh: connect to host 203.0.113.50 port 22: Connection timed out'))
    raises(migrate.inspect_live, {'host': '203.0.113.50', 'port': 2222, 'user': 'u', 'auth': 'password', 'password': 'x'}, text='203.0.113.50 port 2222')
    raises(migrate.inspect_live, {'host': '203.0.113.50', 'user': 'u', 'auth': 'password', 'password': 'x', 'port': '99999'}, text='SSH port must be')


if __name__ == '__main__':
    run_tests(globals())
