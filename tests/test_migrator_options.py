"""Choosing and renaming what moves: databases, users, passwords, site names and hostnames (validation and whole runs).

    python tests/test_migrator_options.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import migrator  # noqa: E402
from test_migrator import BACKUP, CFG, World, backup_request, raises, reset, restore, wait  # noqa: E402


def fake_dbs(*specs):
    return [{'name': n, 'engine': e, 'size': 1} for n, e in specs]


def test_choices_accept_plain_names_and_objects():
    assert migrator._items(['a', {'name': 'b', 'user': 'u'}], 'name') == {'a': {'name': 'a'}, 'b': {'name': 'b', 'user': 'u'}}
    assert migrator._items(None, 'name') == {} and migrator._items([], 'name') == {}


def test_suggestions_are_what_runs_if_nothing_is_changed():
    inv = {'sites': [{'domain': 'My-Site.example'}], 'databases': [{'name': 'Cust-DB'}]}
    assert migrator.suggestions(inv) == {'sites': {'My-Site.example': {'domain': 'My-Site.example', 'site': 'my-site-example'}},
                                         'databases': {'Cust-DB': {'name': 'cust_db', 'user': 'cust_db'}}}


def test_database_options_defaults_and_overrides():
    dbs = fake_dbs(('Old-DB', 'mysql'), ('two', 'mysql'))
    out = migrator._db_options(dbs, {'Old-DB': {'name': 'Old-DB'}, 'two': {'name': 'two', 'new_name': 'Shop_DB', 'user': 'SHOP', 'password': 'My-Strong+Pass1'}})
    assert out['Old-DB']['db'] == out['Old-DB']['user'] == 'old_db' and len(out['Old-DB']['password']) == 32
    assert (out['two']['db'], out['two']['user'], out['two']['password']) == ('shop_db', 'shop', 'My-Strong+Pass1')
    again = migrator._db_options(dbs, {'Old-DB': {'password': ''}, 'two': {}})
    assert again['Old-DB']['password'] != out['Old-DB']['password']                                           # generated fresh each time


def test_database_options_reject_unsafe_input():
    one = fake_dbs(('d', 'mysql'))
    cases = (('new_name', 'bad name', 'Database name for d'), ('new_name', '1abc', 'Database name for d'), ('new_name', 'x' * 33, 'Database name for d'),
             ('new_name', 'a`b', 'Database name for d'), ('user', "x'; DROP", 'Database user for d'), ('user', 'a b', 'Database user for d'),
             ('password', 'short1!', 'Password for d'), ('password', "has'quote-12345", 'Password for d'), ('password', 'has space 12345', 'Password for d'),
             ('password', 'back\\slash-12345', 'Password for d'), ('password', 'dollar$sign-12345', 'Password for d'), ('password', 'hash#sign-12345', 'Password for d'),
             ('password', 'x' * 65, 'Password for d'))
    for field, value, text in cases:
        raises(migrator._db_options, one, {'d': {field: value}}, text=text, kind=ValueError)


def test_database_options_reject_clashes_and_share_users_safely():
    two = fake_dbs(('a', 'mysql'), ('b', 'mysql'))
    raises(migrator._db_options, two, {'a': {'new_name': 'same'}, 'b': {'new_name': 'same'}}, text='both be called same', kind=ValueError)
    ok = migrator._db_options(fake_dbs(('a', 'mysql'), ('a', 'postgres')), {'a': {'new_name': 'same'}})      # same name on different engines is fine
    assert ok['a']['db'] == 'same'
    shared = migrator._db_options(two, {'a': {'user': 'app'}, 'b': {'user': 'app'}})
    assert shared['a']['password'] == shared['b']['password'] and shared['a']['user'] == 'app'                  # one user, one password
    first = migrator._db_options(two, {'a': {'user': 'app'}, 'b': {'user': 'app', 'password': 'Chosen-Pass-123'}})
    assert first['a']['password'] == first['b']['password'] == 'Chosen-Pass-123'                              # an explicit password applies to the whole user
    second = migrator._db_options(two, {'a': {'user': 'app', 'password': 'Chosen-Pass-123'}, 'b': {'user': 'app'}})
    assert second['b']['password'] == 'Chosen-Pass-123'
    clash = {'a': {'user': 'app', 'password': 'First-Pass-1234'}, 'b': {'user': 'app', 'password': 'Other-Pass-1234'}}
    raises(migrator._db_options, two, clash, text='different passwords', kind=ValueError)
    clash['b']['password'] = 'First-Pass-1234'
    assert migrator._db_options(two, clash)['b']['password'] == 'First-Pass-1234'
    pg = fake_dbs(('a', 'postgres'), ('b', 'postgres'))
    raises(migrator._db_options, pg, {'a': {'user': 'u'}, 'b': {'user': 'u'}}, text='PostgreSQL user u', kind=ValueError)


SITES = [{'domain': 'old.example', 'aliases': ['www.old.example', 'shop.old.example', 'other.net', 'old.example']}, {'domain': 'b.example', 'aliases': []}]


def test_site_options_rename_domain_and_aliases():
    out = migrator._site_options(SITES, {'old.example': {'domain': 'old.example'}, 'b.example': {}})
    assert out['old.example'] == {'hostname': 'old.example', 'name': 'old-example', 'aliases': ['www.old.example', 'shop.old.example', 'other.net']}
    moved = migrator._site_options(SITES[:1], {'old.example': {'new_domain': 'NEW.example.org', 'site': 'My-Site'}})['old.example']
    assert moved == {'hostname': 'new.example.org', 'name': 'my-site', 'aliases': ['www.new.example.org', 'shop.new.example.org', 'other.net', 'old.example']}
    again = migrator._site_options(SITES[:1], {'old.example': {'new_domain': 'www.old.example'}})['old.example']
    assert 'www.old.example' not in again['aliases']                                                          # the new hostname is never its own redirect


def test_site_options_reject_bad_names():
    one = SITES[:1]
    for opts, text in (({'new_domain': 'nodot'}, 'looks wrong'), ({'new_domain': 'bad host.com'}, 'looks wrong'), ({'site': 'Bad Name'}, 'Site name for old.example'),
                       ({'site': 'coderunner'}, 'Site name for old.example'), ({'site': '-x'}, 'Site name for old.example')):
        raises(migrator._site_options, one, {'old.example': opts}, text=text, kind=ValueError)
    clash = {'old.example': {'new_domain': 'x.example'}, 'b.example': {'new_domain': 'x.example'}}
    raises(migrator._site_options, SITES, clash, text='both be served at x.example', kind=ValueError)
    raises(migrator._site_options, SITES, {'old.example': {'site': 'same'}, 'b.example': {'site': 'same'}}, text='both be named same', kind=ValueError)


def test_bad_choices_are_refused_before_anything_starts():
    reset()
    World().install()
    bad = {'source': 'backup', 'path': BACKUP, 'sites': [], 'databases': [{'name': 'demo_wp', 'password': 'weak'}]}
    raises(migrator.start, CFG, bad, 'bob', text='Password for demo_wp', kind=ValueError)
    bad['databases'] = ['demo_wp']
    bad['sites'] = [{'domain': 'landing.example', 'new_domain': 'oops'}]
    raises(migrator.start, CFG, bad, 'bob', text='looks wrong', kind=ValueError)
    assert not migrator.RUNS


def test_renamed_items_flow_through_the_whole_migration():
    reset()
    w = World().install()
    req = {'source': 'backup', 'path': BACKUP,
           'sites': [{'domain': 'demo-blog.example', 'new_domain': 'blog.newhome.example', 'site': 'newblog'}, 'landing.example'],
           'databases': [{'name': 'demo_wp', 'new_name': 'blog_prod', 'user': 'blog_user', 'password': 'My-Own-Password-9'}]}
    r = wait(migrator.start(CFG, req, 'bob'))
    assert r.status == 'done', [(s['label'], s['status'], s['detail']) for s in r.steps if s['status'] != 'done']
    blog = next(a for a in w.deployed if a['type'] == 'php')
    assert blog['hostname'] == 'blog.newhome.example' and blog['name'] == 'newblog' and blog['redirectFrom'] == ['www.blog.newhome.example']
    wp = w.sources['blog.newhome.example']['wp-config.php']
    assert "'DB_NAME', 'blog_prod'" in wp and "'DB_USER', 'blog_user'" in wp and "'DB_PASSWORD', 'My-Own-Password-9'" in wp
    sql = ' '.join(c['inp'].decode() for c in w.new.ran('sudo mysql') if c['inp'])
    assert 'CREATE DATABASE `blog_prod`' in sql and "'blog_user'@'localhost' IDENTIFIED BY 'My-Own-Password-9'" in sql and 'GRANT ALL ON `blog_prod`.*' in sql
    assert w.imports[0][0] == 'sudo mysql --default-character-set=utf8mb4 blog_prod'
    shown = '\n'.join(r.log) + str(r.report) + str(r.view())
    assert 'My-Own-Password-9' not in shown                                                                   # chosen passwords are never shown back
    assert r.report['databases'] == [{'old': 'demo_wp', 'engine': 'mysql', 'new': 'blog_prod', 'user': 'blog_user'}]
    assert 'blog.newhome.example' in r.report['domains'] and 'demo-blog.example' not in r.report['domains']
    warned = [m for m in r.report['manual'] if 'still contains the old address' in m]
    assert len(warned) == 1 and 'demo-blog.example now runs at blog.newhome.example' in warned[0]          # renaming the address is never silent; the static site needs no warning


def test_users_shared_by_two_databases_keep_every_site_working():
    reset()
    w = World().install()
    req = {'source': 'backup', 'path': BACKUP, 'sites': ['demo-blog.example', 'crafts.example'],
           'databases': [{'name': 'demo_wp', 'user': 'appuser'}, {'name': 'demo_shop', 'user': 'appuser'}]}
    r = wait(migrator.start(CFG, req, 'bob'))
    assert r.status == 'done'
    pw1 = w.sources['demo-blog.example']['wp-config.php'].split("'DB_PASSWORD', '")[1].split("'")[0]
    pw2 = [l for l in w.sources['crafts.example']['.env'].splitlines() if l.startswith('DB_PASSWORD=')][0].split('=', 1)[1]
    assert pw1 == pw2 and len(pw1) == 32                                                                      # both sites got the one password that the shared user really has


if __name__ == '__main__':
    run_tests(globals(), before=restore)
