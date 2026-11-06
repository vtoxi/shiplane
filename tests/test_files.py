"""The Files page backend: the real remote/files_op.py script run against a throwaway folder tree (no sudo, no SSH), and the HTTP routes.

    python tests/test_files.py
"""
import base64
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import files  # noqa: E402

REAL_RUN = subprocess.run
TREE = tempfile.mkdtemp()
WEB, ETC, LOGS, OUTSIDE = (os.path.join(TREE, n) for n in ('web', 'etc', 'logs', 'outside'))
for d in (WEB, ETC, LOGS, OUTSIDE):
    os.makedirs(d)
files.ADMIN_ROOTS = (WEB,)
files.SUPER_ROOTS = (WEB, ETC, LOGS)
files.READ_ONLY = (LOGS,)
CFG = {'mode': 'local'}


def local_run(cfg, remote, inp=None, timeout=60):
    """Stands in for `sudo python3 -`: the same script, the same stdin, run by the current Python."""
    r = REAL_RUN([sys.executable, '-'], input=inp, capture_output=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).decode()


files.run = local_run


def raises(fn, text):
    try:
        fn()
    except ValueError as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('expected ' + text)


def put(path, data=b'hello\n', mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
    if mode:
        os.chmod(path, mode)
    return path


def test_listing_sorts_folders_first_and_reports_details():
    put(os.path.join(WEB, 'site', 'index.html'), b'<h1>hi</h1>')
    put(os.path.join(WEB, 'site', 'a.txt'), b'a', 0o640)
    os.makedirs(os.path.join(WEB, 'site', 'Zdir'))
    out = files.list_dir(CFG, 'admin', os.path.join(WEB, 'site'))
    assert [e['name'] for e in out['entries']] == ['Zdir', 'a.txt', 'index.html'] and out['entries'][0]['type'] == 'dir' and out['writable']
    a = out['entries'][1]
    assert a['size'] == 1 and a['mode'] == '640' and a['type'] == 'file' and a['mtime'] > 0
    assert files.list_dir(CFG, 'admin', '/')['entries'][0]['name'] == WEB.lstrip('/') and files.list_dir(CFG, 'admin', '')['path'] == '/'
    raises(lambda: files.list_dir(CFG, 'admin', os.path.join(WEB, 'missing')), 'Not found')
    raises(lambda: files.list_dir(CFG, 'admin', os.path.join(WEB, 'site', 'a.txt')), 'Not a folder')


def test_read_text_image_binary_and_size_limits():
    t = put(os.path.join(WEB, 'r', 'note.txt'), 'héllo wörld\n'.encode())
    r = files.read(CFG, 'admin', t)
    assert r['kind'] == 'text' and base64.b64decode(r['content_b64']).decode() == 'héllo wörld\n' and 'real' not in r and r['mtime'] > 0
    png = put(os.path.join(WEB, 'r', 'p.png'), base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='))   # a real PNG: its header contains NUL bytes
    assert files.read(CFG, 'admin', png)['kind'] == 'image' and 'content_b64' in files.read(CFG, 'admin', png)
    binf = put(os.path.join(WEB, 'r', 'b.bin'), b'\x00\x01\x02' * 100)
    rb = files.read(CFG, 'admin', binf)
    assert rb['kind'] == 'binary' and 'content_b64' not in rb
    assert 'content_b64' in files.read(CFG, 'admin', binf, download=True)                                                   # a download may carry any file
    big = put(os.path.join(WEB, 'r', 'big.txt'), b'x' * 5000)
    old = files.MAX_EDIT
    files.MAX_EDIT = 1000
    try:
        rbig = files.read(CFG, 'admin', big)
        assert rbig['too_large'] is True and 'content_b64' not in rbig
        raises(lambda: files.write(CFG, 'admin', big, 'y' * 2000), 'cannot be edited')
    finally:
        files.MAX_EDIT = old
    latin = put(os.path.join(WEB, 'r', 'latin.txt'), b'caf\xe9 ' * 200)
    assert files.read(CFG, 'admin', latin)['kind'] == 'binary'                                                              # not UTF-8: not offered as text


def test_write_create_update_conflict_and_atomicity():
    p = os.path.join(WEB, 'w', 'new.txt')
    os.makedirs(os.path.dirname(p))
    out = files.write(CFG, 'admin', p, 'one\n', create_only=True)
    assert out['ok'] and open(p).read() == 'one\n'
    raises(lambda: files.write(CFG, 'admin', p, 'x', create_only=True), 'already exists')
    os.chmod(p, 0o600)
    m = files.read(CFG, 'admin', p)['mtime']
    assert files.write(CFG, 'admin', p, 'two\n', expect_mtime=m)['ok'] and open(p).read() == 'two\n' and oct(os.stat(p).st_mode & 0o777) == '0o600'   # mode kept
    stale = files.write(CFG, 'admin', p, 'mine\n', expect_mtime=m - 100)                                                      # someone else saved since
    assert stale.get('conflict') and open(p).read() == 'two\n'
    assert files.write(CFG, 'admin', p, 'forced\n', expect_mtime=m - 100, force=True)['ok'] and open(p).read() == 'forced\n'
    assert not [n for n in os.listdir(os.path.dirname(p)) if n.startswith('.shiplane-tmp-')]                               # no leftovers
    assert files.write(CFG, 'admin', p, 'unicode ✓ ok\n')['ok'] and open(p, encoding='utf-8').read() == 'unicode ✓ ok\n'
    raises(lambda: files.write(CFG, 'admin', os.path.join(WEB, 'nodir', 'x.txt'), 'x'), 'does not exist')
    raises(lambda: files.write(CFG, 'admin', os.path.join(WEB, 'w'), 'x'), 'not a regular file')
    raises(lambda: files.write(CFG, 'admin', p, b'bytes'), 'must be text')


def test_mkdir_rename_move_and_delete():
    base = os.path.join(WEB, 'm')
    os.makedirs(base)
    files.mkdir(CFG, 'admin', os.path.join(base, 'sub'))
    assert os.path.isdir(os.path.join(base, 'sub'))
    raises(lambda: files.mkdir(CFG, 'admin', os.path.join(base, 'sub')), 'already exists')
    put(os.path.join(base, 'a.txt'))
    files.rename(CFG, 'admin', os.path.join(base, 'a.txt'), 'b.txt')
    assert os.path.exists(os.path.join(base, 'b.txt')) and not os.path.exists(os.path.join(base, 'a.txt'))
    put(os.path.join(base, 'c.txt'))
    raises(lambda: files.rename(CFG, 'admin', os.path.join(base, 'b.txt'), 'c.txt'), 'already exists')
    files.move(CFG, 'admin', os.path.join(base, 'b.txt'), os.path.join(base, 'sub', 'b.txt'))
    assert os.path.exists(os.path.join(base, 'sub', 'b.txt'))
    raises(lambda: files.move(CFG, 'admin', os.path.join(base, 'sub'), os.path.join(base, 'sub', 'deeper')), 'into itself')
    raises(lambda: files.delete(CFG, 'admin', os.path.join(base, 'sub')), 'not empty')
    files.delete(CFG, 'admin', os.path.join(base, 'sub'), recursive=True)
    assert not os.path.exists(os.path.join(base, 'sub'))
    files.delete(CFG, 'admin', os.path.join(base, 'c.txt'))
    raises(lambda: files.delete(CFG, 'admin', os.path.join(base, 'gone')), 'Not found')
    raises(lambda: files.delete(CFG, 'admin', WEB), 'cannot be deleted')
    for bad in ('', '.', '..', 'a/b', ' x', 'x ', 'a\nb', 'n' * 300):
        raises(lambda: files.rename(CFG, 'admin', os.path.join(base, 'a'), bad), 'not allowed')


def test_nothing_escapes_the_allowed_folders():
    put(os.path.join(OUTSIDE, 'secret.txt'), b'top secret')
    os.symlink(OUTSIDE, os.path.join(WEB, 'link-out'))                                                                      # a link out of the web folder
    os.symlink(os.path.join(OUTSIDE, 'secret.txt'), os.path.join(WEB, 'file-link'))
    raises(lambda: files.read(CFG, 'admin', os.path.join(WEB, 'file-link')), 'not available')
    raises(lambda: files.list_dir(CFG, 'admin', os.path.join(WEB, 'link-out')), 'not available')
    raises(lambda: files.write(CFG, 'admin', os.path.join(WEB, 'link-out', 'evil.txt'), 'x'), 'not available')
    raises(lambda: files.mkdir(CFG, 'admin', os.path.join(WEB, 'link-out', 'd')), 'not available')
    raises(lambda: files.rename(CFG, 'admin', os.path.join(WEB, 'link-out', 'secret.txt'), 'x'), 'not available')
    assert not os.path.exists(os.path.join(OUTSIDE, 'evil.txt'))
    files.delete(CFG, 'admin', os.path.join(WEB, 'file-link'))                                                              # deleting the link removes only the link
    files.delete(CFG, 'admin', os.path.join(WEB, 'link-out'))
    assert os.path.exists(os.path.join(OUTSIDE, 'secret.txt')) and not os.path.lexists(os.path.join(WEB, 'link-out'))
    raises(lambda: files.read(CFG, 'admin', OUTSIDE + '/secret.txt'), 'not available')
    raises(lambda: files.read(CFG, 'admin', WEB + '/../outside/secret.txt'), 'not available')                               # .. is resolved first
    raises(lambda: files.list_dir(CFG, 'admin', '/etc'), 'not available')
    for bad in ('relative/path', 'a\0b', '/x' + 'y' * 1100):
        raises(lambda: files.read(CFG, 'admin', bad), 'looks wrong')


def test_secret_files_are_never_shown_or_changed():
    p = put(os.path.join(WEB, 'proj', '.git', 'config.lock'), b'x')
    raises(lambda: files.read(CFG, 'admin', p), 'holds secrets')
    raises(lambda: files.write(CFG, 'admin', p, 'y'), 'holds secrets')


def test_roles_and_read_only_folders():
    assert files.roots_for('admin') == (WEB,) and ETC in files.roots_for('super_admin')
    put(os.path.join(ETC, 'app.conf'), b'k=v\n')
    raises(lambda: files.read(CFG, 'admin', os.path.join(ETC, 'app.conf')), 'not available')                                # admins do not get /etc
    assert files.write(CFG, 'super_admin', os.path.join(ETC, 'app.conf'), 'k=w\n')['ok']
    put(os.path.join(LOGS, 'syslog'), b'line\n')
    assert files.read(CFG, 'super_admin', os.path.join(LOGS, 'syslog'))['kind'] == 'text'                                   # logs can be read
    raises(lambda: files.write(CFG, 'super_admin', os.path.join(LOGS, 'syslog'), 'tampered'), 'read-only')
    raises(lambda: files.delete(CFG, 'super_admin', os.path.join(LOGS, 'syslog')), 'read-only')
    raises(lambda: files.mkdir(CFG, 'super_admin', os.path.join(LOGS, 'newdir')), 'read-only')
    assert files.list_dir(CFG, 'super_admin', LOGS)['writable'] is False


def test_the_server_reports_a_missing_script_result_clearly():
    files.run = lambda cfg, cmd, inp=None, timeout=60: (255, 'ssh: connect to host x port 22: Connection refused')
    try:
        try:
            files.list_dir(CFG, 'admin', WEB)
        except RuntimeError as e:
            assert 'Connection refused' in str(e)
        else:
            raise AssertionError
    finally:
        files.run = local_run


if __name__ == '__main__':
    run_tests(dict(globals()))
