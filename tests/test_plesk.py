"""Reading Plesk backups: container formats, XML, domains, databases, document roots, and bad input (no server needed).

    python tests/test_plesk.py
"""
import io
import os
import sys
import tarfile
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import migrate, plesk  # noqa: E402
from scripts import make_sample_backup as sample  # noqa: E402

TMP = tempfile.mkdtemp()
INFO = sample.PLESK_INFO


def raises(fn, *args, text='', kind=ValueError):
    try:
        fn(*args)
    except kind as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


def plesk_tar(name, members, mode='w:gz'):
    path = os.path.join(TMP, name)
    with tarfile.open(path, mode) as t:
        for n, data in members.items():
            data = data if isinstance(data, bytes) else data.encode()
            info = tarfile.TarInfo(n)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return path


def tgz(files):
    return sample._tgz(files)


def to_zip(src, name):
    path = os.path.join(TMP, name)
    with tarfile.open(src) as t, zipfile.ZipFile(path, 'w') as z:
        for m in t:
            if m.isfile():
                z.writestr(m.name, t.extractfile(m).read())
    return path


# ------------------------------------------------------------ the sample export
def test_sample_export_inventory():
    inv = plesk.inspect(sample.build_plesk(os.path.join(TMP, 'sample.tgz')))
    sites = {s['domain']: s for s in inv['sites']}
    assert set(sites) == {'shop.example', 'blog.shop.example'} and inv['format'] == 'plesk' and inv['panel'] == 'Plesk backup'
    shop = sites['shop.example']
    assert shop['kind'] == 'wordpress' and shop['docroot'] == '/httpdocs' and shop['src']['prefix'] == 'httpdocs'
    assert shop['size'] == len(sample.WP_CONFIG) + len('<?php require __DIR__ . "/wp-blog-header.php";\n') + len('upload\n') * 20      # cgi-bin is outside the document root
    assert sites['blog.shop.example']['kind'] == 'static'
    assert {(d['name'], d['engine']) for d in inv['databases']} == {('shop_wp', 'mysql'), ('shop_stats', 'postgres')} and inv['mailboxes'] == 2
    assert any('PHP' in n for n in inv['notes']) and any('mailboxes' in n for n in inv['notes'])


def test_zip_export_works_like_tgz_and_dispatch_picks_the_right_reader():
    src = sample.build_plesk(os.path.join(TMP, 'for-zip.tgz'))
    z = to_zip(src, 'export.zip')
    assert [s['domain'] for s in plesk.inspect(z)['sites']] == ['blog.shop.example', 'shop.example']
    assert migrate.inspect_backup(z)['format'] == 'plesk' and migrate.inspect_backup(src)['format'] == 'plesk'
    assert migrate.inspect_backup(sample.build(os.path.join(TMP, 'cp.tar.gz')))['format'] == 'cpmove'
    assert migrate.inspect({'source': 'backup', 'path': src})['panel'] == 'Plesk backup'


# ------------------------------------------------------------ bad input
def test_backup_file_checks():
    raises(migrate.inspect_backup, os.path.join(TMP, 'nothing.tgz'), text='cPanel or Plesk backup file')
    txt = os.path.join(TMP, 'a.txt')
    open(txt, 'w').write('x')
    raises(migrate.inspect_backup, txt, text='cPanel or Plesk backup file')
    raises(migrate.inspect_backup, '', text='cPanel or Plesk backup file')
    other = plesk_tar('other.tgz', {'readme.txt': 'hello'})
    raises(migrate.inspect_backup, other, text='neither a cPanel')
    broken = os.path.join(TMP, 'broken.tgz')
    open(broken, 'wb').write(b'garbage')
    raises(migrate.inspect_backup, broken, text='Could not read the backup')
    badzip = os.path.join(TMP, 'bad.zip')
    open(badzip, 'wb').write(b'not a zip')
    raises(migrate.inspect_backup, badzip, text='Could not read the backup')


def test_plesk_specific_errors():
    raises(plesk.inspect, plesk_tar('noxml.tgz', {'domains/a.com/backup_user-data_1.tgz': tgz({'httpdocs/i.php': 'x'})}), text='no backup_info XML', kind=plesk.NotPlesk)
    big = plesk_tar('bigxml.tgz', {'backup_info_1.xml': b'<a>' + b'x' * (plesk.MAX_XML + 1)})
    raises(plesk.inspect, big, text='no backup_info XML', kind=plesk.NotPlesk)
    nodomain = plesk_tar('nodomain.tgz', {'backup_info_1.xml': '<migration-dump/>', 'backup_user-data_1.tgz': tgz({'httpdocs/i.html': 'x'})})
    raises(plesk.inspect, nodomain, text='not which domain')
    zst = plesk_tar('zst.tgz', {'backup_info_1.xml': INFO, 'domains/shop.example/backup_user-data_1.tzst': b'zstd bytes'})
    raises(plesk.inspect, zst, text='.tzst')
    inner = plesk_tar('badinner.tgz', {'backup_info_1.xml': INFO, 'domains/shop.example/backup_user-data_1.tgz': b'not a tar'})
    raises(plesk.inspect, inner, text='Could not read backup_user-data_1.tgz')


# ------------------------------------------------------------ the pieces
def test_parse_info_is_lenient_and_safe():
    got = plesk.parse_info(INFO.encode())
    assert got == {'databases': {'shop_wp': 'mysql', 'shop_stats': 'postgresql'}, 'domains': ['shop.example'], 'mailnames': 2}
    ns = b'<p:migration-dump xmlns:p="urn:x"><p:Domain name="A.COM"/><p:DATABASE name="d"/></p:migration-dump>'
    assert plesk.parse_info(ns) == {'databases': {'d': 'mysql'}, 'domains': ['a.com'], 'mailnames': 0}
    empty = {'databases': {}, 'domains': [], 'mailnames': 0}
    assert plesk.parse_info(b'<<<not xml') == empty
    assert plesk.parse_info(b'<!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>') == empty            # entity tricks are refused, not expanded
    assert plesk.parse_info(b'<!ENTITY y "z"><a/>') == empty


def test_domain_and_database_names():
    d = plesk._domain_of
    assert d(['domains', 'Shop.Example 12', 'backup_user-data_1.tgz']) == 'shop.example'
    assert d(['domains', 'a.com', 'sites', 'Blog.a.com', 'x.tgz']) == 'blog.a.com'
    assert d(['backup_user-data_1.tgz']) is None and d(['domains', 'x.tgz']) is None and d(['sites', 'x.tgz']) is None
    n = plesk._db_name
    assert n(['domains', 'a.com', 'databases', 'mydb', 'backup_mydb_2601011200.tgz']) == 'mydb'
    assert n(['databases', 'backup_wp_2601011200.sql']) == 'wp'
    assert n(['databases', 'wp.sql.gz']) == 'wp'
    assert n(['databases', 'shop_db_2601011200_2602021200.tgz']) == 'shop_db'
    assert n(['databases', '2601011200.sql']) == '2601011200.sql'                               # nothing left after cleaning: keep the file name


def test_container_and_tree_iterators():
    assert [n for n, *_ in plesk.iter_files(plesk_tar('plain.tar', {'a/b.txt': 'x', 'c.txt': 'y'}, 'w'))] == ['a/b.txt', 'c.txt']
    z = os.path.join(TMP, 'iter.zip')
    with zipfile.ZipFile(z, 'w') as zf:
        zf.writestr('folder/', '')
        zf.writestr('folder/f.txt', 'data')
    items = [(n, s, o().read()) for n, s, o in plesk.iter_files(z)]                  # a stream is only valid while its item is current
    assert items == [('folder/f.txt', 4, b'data')]
    tree = list(plesk.iter_tree(io.BytesIO(tgz({'./httpdocs/a.php': 'x'})), 'u.tgz'))
    assert [(p, s) for p, s, _ in tree] == [('httpdocs/a.php', 1)]
    raises(lambda: list(plesk.iter_tree(io.BytesIO(b''), 'u.tzst')), text='.tzst')
    raises(lambda: list(plesk.iter_tree(io.BytesIO(b'junk'), 'u.tgz')), text='Could not read u.tgz')


def test_document_root_detection():
    prefix, size, kind = plesk._scan_tree(io.BytesIO(tgz({'httpdocs/wp-config.php': 'abc', 'httpdocs/a/b.php': 'de', 'logs/x.log': 'ignored'})), 'u.tgz')
    assert (prefix, size, kind) == ('httpdocs', 5, 'wordpress')
    prefix, size, kind = plesk._scan_tree(io.BytesIO(tgz({'index.html': 'abcd'})), 'u.tgz')
    assert (prefix, size, kind) == ('', 4, 'static')                                           # no httpdocs folder: the archive is the document root
    assert plesk._scan_tree(io.BytesIO(tgz({'httpdocs/package.json': '{}'})), 'u.tgz')[2] == 'node'
    assert plesk._scan_tree(io.BytesIO(tgz({'httpdocs/lib/x.php': '<?php'})), 'u.tgz')[2] == 'php'


def test_layout_variants():
    members = {
        'backup_info_1.xml': INFO,
        'backup_user-data_1.tgz': tgz({'httpdocs/index.html': 'small'}),                                  # no domains/ folder: the XML names the only domain
        'databases/backup_shop_wp_2601011200.sql': 'x' * 10,
        'databases/shop_wp.sql': 'y' * 50,                                                                 # same database twice: the larger dump wins
    }
    inv = plesk.inspect(plesk_tar('flat.tgz', members))
    assert [s['domain'] for s in inv['sites']] == ['shop.example'] and inv['sites'][0]['src']['member'] == 'backup_user-data_1.tgz'
    assert [(d['name'], d['size'], d['member']) for d in inv['databases']] == [('shop_wp', 50, 'databases/shop_wp.sql')]
    two = {'backup_info_1.xml': INFO, 'domains/a.com/backup_user-data_1.tgz': tgz({'httpdocs/i': 'x'}), 'domains/a.com/backup_user-data_2.tgz': tgz({'httpdocs/i': 'xxxx'})}
    assert plesk.inspect(plesk_tar('two.tgz', two))['sites'][0]['size'] == 4                           # incremental copies: the biggest is used


if __name__ == '__main__':
    run_tests(globals())
