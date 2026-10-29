"""Zip upload (safe unpacking) and Postgres operations, with a fake server (no server needed).

    python tests/test_uploads.py
"""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeServer, run_tests  # noqa: E402
from coderunner import dbops, uploads  # noqa: E402

CFG = {'mode': 'ssh', 'host': 'srv.example.com', 'user': 'deploy'}


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def save(data, length=None):
    return uploads.save_zip(io.BytesIO(data), len(data) if length is None else length)


def raises(fn, text):
    try:
        fn()
    except (ValueError, RuntimeError) as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('expected an error containing %r' % text)


def test_zip_with_files_at_the_top_unpacks_there():
    out = save(make_zip({'index.html': 'hi', 'a/b.txt': 'x'}))
    assert open(os.path.join(out, 'index.html')).read() == 'hi' and os.path.isfile(os.path.join(out, 'a', 'b.txt'))


def test_zip_of_one_folder_returns_the_folder_content():
    out = save(make_zip({'dist/index.html': 'hi', '__MACOSX/x': 'junk'}))
    assert os.path.basename(out) == 'dist' and os.path.isfile(os.path.join(out, 'index.html'))


def test_bad_sizes_and_truncated_and_not_a_zip():
    raises(lambda: uploads.save_zip(io.BytesIO(b''), 0), 'between')
    raises(lambda: uploads.save_zip(io.BytesIO(b''), uploads.MAX_ZIP + 1), 'between')
    raises(lambda: save(b'abc', 10), 'cut short')
    raises(lambda: save(b'not a zip at all'), 'not a valid zip')


def test_path_traversal_is_refused():
    raises(lambda: save(make_zip({'../evil.txt': 'x'})), 'unsafe path')
    raises(lambda: save(make_zip({'/etc/evil.txt': 'x'})), 'unsafe path')


def test_unpacked_size_limit():
    old = uploads.MAX_UNPACKED
    uploads.MAX_UNPACKED = 5
    try:
        raises(lambda: save(make_zip({'a.txt': 'x' * 100})), 'too large')
    finally:
        uploads.MAX_UNPACKED = old


def test_old_upload_folders_are_cleaned_up():
    import tempfile
    import time
    stale = tempfile.mkdtemp(prefix=uploads.PREFIX)
    os.utime(stale, (time.time() - 2 * 86400,) * 2)
    save(make_zip({'index.html': 'hi'}))
    assert not os.path.exists(stale)


class Fake(FakeServer):
    """Also matches the script sent on stdin (`bash -s`)."""

    def run(self, cfg, remote, inp=None, timeout=60):
        return super().run(cfg, remote + ' ' + (inp or b'').decode(), inp, timeout)


def use(*rules):
    fake = Fake([(t, (rc, out)) for t, rc, out in reversed(rules)])
    dbops.run = fake.run
    return fake


def test_names_are_validated():
    for bad in ('Bad Name', '1abc', 'a;drop', ''):
        raises(lambda: dbops.create(CFG, bad), 'Database name')
    raises(lambda: dbops.backup(CFG, 'x y'), 'Database name')
    raises(lambda: dbops.restore(CFG, 'x y', 'a.sql.gz'), 'Database name')
    raises(lambda: dbops.backup_path('../../etc/passwd'), 'bad backup')
    raises(lambda: dbops.delete_backup(CFG, 'x; rm -rf /'), 'bad backup')


def test_list_all_parses_databases_and_backups():
    use(('psql -tAF', 0, 'shop|shopuser|8192\ngarbage\nblog|bloguser|abc\n'),
        ('find /var/backups', 0, 'shop-20260101-020000.sql.gz|123|2026-01-01 02:00\nbad.txt|1|x\n'))
    r = dbops.list_all(CFG)
    assert r['databases'] == [{'name': 'shop', 'owner': 'shopuser', 'size': 8192}]
    assert r['backups'] == [{'file': 'shop-20260101-020000.sql.gz', 'size': 123, 'time': '2026-01-01 02:00'}]
    use(('psql -tAF', 1, 'no postgres'))
    raises(lambda: dbops.list_all(CFG), 'no postgres')


def test_create_returns_url_and_quotes_the_password():
    fake = use(('createdb', 0, 'PASSWORD=p@ss/word\n'))
    r = dbops.create(CFG, 'shop')
    assert r['name'] == 'shop' and r['password'] == 'p@ss/word' and 'p%40ss%2Fword' in r['url']
    assert fake.ran('bash -s')
    use(('createdb', 1, 'database shop already exists'))
    raises(lambda: dbops.create(CFG, 'shop'), 'already exists')


def test_backup_and_restore_and_delete():
    use(('pg_dump', 0, 'FILE=shop-20260101-020000.sql.gz SIZE=99'))
    assert dbops.backup(CFG, 'shop') == {'file': 'shop-20260101-020000.sql.gz', 'size': 99}
    use(('pg_dump', 1, 'disk full'))
    raises(lambda: dbops.backup(CFG, 'shop'), 'disk full')
    fake = use(('restored', 0, 'restored shop from x'))
    assert 'restored' in dbops.restore(CFG, 'shop', 'shop-20260101-020000.sql.gz')
    assert '/var/backups/coderunner/shop-20260101-020000.sql.gz' in fake.calls[0]['inp'].decode()
    use(('restored', 1, 'the backup could not be loaded'))
    raises(lambda: dbops.restore(CFG, 'shop', 'shop-20260101-020000.sql.gz'), 'could not be loaded')
    use(('rm -f', 0, 'deleted'))
    assert dbops.delete_backup(CFG, 'shop-20260101-020000.sql.gz') == 'deleted'
    use(('rm -f', 1, 'denied'))
    raises(lambda: dbops.delete_backup(CFG, 'shop-20260101-020000.sql.gz'), 'denied')


def test_drop_guards():
    raises(lambda: dbops.drop(CFG, 'postgres', 'postgres'), 'protected')
    raises(lambda: dbops.drop(CFG, 'shop', 'other'), 'Type the database name')
    fake = use(('DROP DATABASE', 0, 'dropped shop\n'))
    assert dbops.drop(CFG, 'shop', 'shop') == 'dropped shop' and fake.ran('DROP DATABASE shop')
    use(('DROP DATABASE', 1, 'busy'))
    raises(lambda: dbops.drop(CFG, 'shop', 'shop'), 'busy')


if __name__ == '__main__':
    run_tests(dict(globals()))
