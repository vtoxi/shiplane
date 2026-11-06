"""Uploading a Plesk / cPanel backup from the browser in pieces, then reading it with Migrate: the module, and the real HTTP server end to end.

    python tests/test_backupupload.py
"""
import http.client
import json
import os
import shutil
import socket
import sys
import tarfile
import tempfile
import threading
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_route_sweep as sweep  # noqa: E402  (temporary data folder + fakes)
from fakes import run_tests  # noqa: E402
from coderunner import apikeys, auth, backupupload as bu, server  # noqa: E402
from scripts import make_sample_backup as sample  # noqa: E402

sweep.install_fakes()
TMP = tempfile.mkdtemp()


def plesk_zip():
    src = sample.build_plesk(os.path.join(TMP, 'export.tgz'))
    path = os.path.join(TMP, 'plesk-export.zip')
    with tarfile.open(src) as t, zipfile.ZipFile(path, 'w') as z:
        for m in t:
            if m.isfile():
                z.writestr(m.name, t.extractfile(m).read())
    return path


def raises(fn, text, kind=(ValueError, LookupError)):
    try:
        fn()
    except kind as e:
        assert text in str(e), str(e)
        return e
    raise AssertionError('expected ' + text)


def reset():
    d = bu.folder()
    for n in os.listdir(d):
        os.remove(os.path.join(d, n))


def push(uid, data, piece=1000):
    off = 0
    while off < len(data):
        import io
        part = data[off:off + piece]
        off = bu.chunk(uid, off, io.BytesIO(part), len(part))['offset']
    return off


# ------------------------------------------------------------------ the module
def test_upload_in_pieces_and_resume_after_a_drop():
    reset()
    data = open(plesk_zip(), 'rb').read()
    uid = bu.start('plesk-export.zip', len(data))['id']
    assert os.stat(bu.folder()).st_mode & 0o777 == 0o700 and os.stat(bu._part(uid)).st_mode & 0o777 == 0o600           # owner-only
    cut = len(data) // 2
    push(uid, data[:cut])
    assert bu.status(uid)['offset'] == cut
    import io
    e = raises(lambda: bu.chunk(uid, 500, io.BytesIO(b'x' * 10), 10), 'continue from there', ValueError)                # a stale piece is refused
    assert e.offset == cut
    raises(lambda: bu.complete(uid), 'arrived')                                                                          # not finished yet
    assert bu.chunk(uid, cut, io.BytesIO(data[cut:]), len(data) - cut)['offset'] == len(data)
    out = bu.complete(uid)
    assert out['size'] == len(data) and open(out['path'], 'rb').read() == data and out['path'].endswith('-plesk-export.zip')
    assert not os.path.exists(bu._part(uid)) and not os.path.exists(bu._meta_path(uid))


def test_a_cut_off_piece_leaves_nothing_behind():
    reset()
    import io
    uid = bu.start('b.tgz', 5000)['id']
    push(uid, b'\x1f\x8b' + b'a' * 1998, 1000)
    raises(lambda: bu.chunk(uid, 2000, io.BytesIO(b'y' * 300), 1000), 'cut short')                                       # promised 1000, sent 300
    assert bu.status(uid)['offset'] == 2000


def test_validation_limits_and_disk_check():
    reset()
    for bad in ('', 'x', 'notes.txt', '../a.zip', 'a/b.zip', '.hidden.zip', 'a;b.zip', 'x' * 200 + '.zip', 'backup.exe'):
        raises(lambda: bu.start(bad, 10), 'ending in')
    for size in (0, -5, 'abc', None, bu.MAX_BACKUP + 1):
        raises(lambda: bu.start('a.zip', size), '')
    real = shutil.disk_usage
    shutil.disk_usage = lambda p: shutil.os.stat_result((0,) * 10) if False else type('U', (), {'free': 2 * 1024 ** 3, 'total': 10, 'used': 1})()
    try:
        raises(lambda: bu.start('a.zip', 5 * 1024 ** 3), 'Not enough free disk space')
        assert bu.start('small.zip', 100)['id']                                                                          # a small one still fits
    finally:
        shutil.disk_usage = real
    reset()
    for i in range(bu.MAX_OPEN):
        bu.start('f%d.zip' % i, 10)
    raises(lambda: bu.start('more.zip', 10), 'already unfinished')


def test_a_file_that_is_not_an_archive_is_refused_and_removed():
    reset()
    uid = bu.start('fake.zip', 1200)['id']
    push(uid, b'#!/bin/sh\necho hi\n' + b'x' * 1182)
    raises(lambda: bu.complete(uid), 'not a zip, tar or gzip')
    assert os.listdir(bu.folder()) == []
    uid = bu.start('over.zip', 100)['id']
    import io
    raises(lambda: bu.chunk(uid, 0, io.BytesIO(b'z' * 200), 200), 'past the end')
    raises(lambda: bu.chunk(uid, 0, io.BytesIO(b''), 0), 'between 1 byte')
    raises(lambda: bu.chunk(uid, 0, io.BytesIO(b'z'), bu.CHUNK_MAX + 1), 'between 1 byte')
    for bad in ('../../etc/passwd', '', 'g' * 24, 'a' * 23):
        raises(lambda: bu.status(bad), 'Unknown upload', LookupError)


def test_listing_delete_abort_and_cleanup():
    reset()
    data = open(plesk_zip(), 'rb').read()
    uid = bu.start('keep-me.zip', len(data))['id']
    push(uid, data)
    done = bu.complete(uid)
    open_uid = bu.start('half.tgz', 100)['id']
    lst = bu.listing()
    assert [b['name'] for b in lst['backups']] == ['keep-me.zip'] and lst['backups'][0]['path'] == done['path'] and [u['id'] for u in lst['unfinished']] == [open_uid]
    raises(lambda: bu.delete('../keep'), 'Bad file name')
    raises(lambda: bu.delete('notes.txt'), 'Bad file name')
    raises(lambda: bu.delete('00000000-nope.zip'), 'No such backup', LookupError)
    old = time.time() - (bu.KEEP_DAYS + 1) * 86400
    os.utime(done['path'], (old, old))
    bu.cleanup()
    assert bu.listing()['backups'] == []                                                                                 # old finished backups are cleaned up
    bu.abort(open_uid)
    assert bu.listing()['unfinished'] == []
    raises(lambda: bu.abort(open_uid), 'Unknown upload', LookupError)


def test_two_writers_at_the_same_offset_cannot_corrupt_the_file():
    reset()
    import io
    uid = bu.start('race.zip', 20000)['id']
    results = []

    def send(byte):
        try:
            bu.chunk(uid, 0, io.BytesIO(byte * 10000), 10000)
            results.append('ok')
        except ValueError:
            results.append('refused')
    ts = [threading.Thread(target=send, args=(b,)) for b in (b'a', b'b', b'c', b'd')]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ['ok', 'refused', 'refused', 'refused'] and bu.status(uid)['offset'] == 10000


# ---------------------------------------------------------------- the real server
server.STATE['auth'] = 'token'
with socket.socket() as _s:
    _s.bind(('127.0.0.1', 0))
    PORT = _s.getsockname()[1]
SRV = server.make_server(PORT)
PORT = server.STATE['port']
threading.Thread(target=SRV.serve_forever, daemon=True).start()
TOKEN = server.STATE['token']
XRW = {'X-Requested-With': 'coderunner'}


def req(method, path, body=None, raw=None, headers=None, cookie=True):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=20)
    h = {'Host': '127.0.0.1:%d' % PORT}
    if cookie:
        h['Cookie'] = 'cr=%s' % TOKEN
    h.update(headers if headers is not None else XRW)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if data is not None:
        h.setdefault('Content-Length', str(len(data)))
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.status, r.read()
    c.close()
    return out[0], (json.loads(out[1]) if out[1][:1] in (b'{', b'[') else out[1])


def test_end_to_end_upload_a_plesk_zip_then_read_it_with_migrate():
    reset()
    data = open(plesk_zip(), 'rb').read()
    s, st = req('POST', '/api/migrate/upload/start', {'name': 'plesk-export.zip', 'size': len(data)})
    assert s == 200 and st['chunk'] == 8 * 1024 * 1024
    uid, off = st['id'], 0
    while off < len(data):
        part = data[off:off + 700]
        s, out = req('POST', '/api/migrate/upload/chunk?id=%s&offset=%d' % (uid, off), raw=part)
        assert s == 200, out
        off = out['offset']
        if off == 1400:                                                                                                  # a retried piece the server already has
            s, out = req('POST', '/api/migrate/upload/chunk?id=%s&offset=700' % uid, raw=data[700:1400])
            assert s == 409 and out['offset'] == 1400
    assert req('GET', '/api/migrate/upload/status?id=' + uid)[1]['offset'] == len(data)
    s, done = req('POST', '/api/migrate/upload/complete', {'id': uid})
    assert s == 200 and os.path.isfile(done['path'])
    s, inv = req('POST', '/api/migrate/inspect', {'source': 'backup', 'path': done['path']})                              # the file Migrate reads is the uploaded one
    assert s == 200 and inv['format'] == 'plesk' and {x['domain'] for x in inv['sites']} == {'shop.example', 'blog.shop.example'}
    s, lst = req('GET', '/api/migrate/upload/list')
    assert [b['name'] for b in lst['backups']] == ['plesk-export.zip']
    assert req('POST', '/api/migrate/upload/delete', {'file': lst['backups'][0]['file']})[0] == 200 and req('GET', '/api/migrate/upload/list')[1]['backups'] == []


def test_http_error_cases_and_who_may_upload():
    reset()
    s, st = req('POST', '/api/migrate/upload/start', {'name': 'a.zip', 'size': 100})
    uid = st['id']
    assert req('POST', '/api/migrate/upload/chunk?id=%s&offset=0' % uid, raw=b'PK\x03\x04' + b'0' * 96, cookie=False)[0] == 401                   # no session
    assert req('POST', '/api/migrate/upload/chunk?id=%s&offset=0' % uid, raw=b'x' * 10, headers={})[0] == 403                                          # no browser header
    assert req('POST', '/api/migrate/upload/chunk?id=nope&offset=0', raw=b'x' * 10)[0] == 404
    assert req('POST', '/api/migrate/upload/chunk?id=%s&offset=0' % uid, raw=b'x' * 200)[0] == 400                                                     # past the end
    s, out = req('POST', '/api/migrate/upload/chunk?id=%s&offset=0' % uid, raw=b'z', headers=dict(XRW, **{'Content-Length': str(bu.CHUNK_MAX + 1)}))
    assert s == 413
    for scope in ('viewer', 'deploy'):                                                                                    # read-only and deploy-only keys cannot upload
        key, _ = apikeys.create('k-' + scope, scope)
        h = {'Authorization': 'Bearer ' + key}
        assert req('POST', '/api/migrate/upload/chunk?id=%s&offset=0' % uid, raw=b'z' * 10, headers=h, cookie=False)[0] == 403, scope
        assert req('POST', '/api/migrate/upload/start', {'name': 'b.zip', 'size': 10}, headers=h, cookie=False)[0] == 403, scope
    key, _ = apikeys.create('k-admin', 'admin')                                                                           # an admin key may (API clients need no browser header)
    assert req('POST', '/api/migrate/upload/chunk?id=%s&offset=0' % uid, raw=b'PK\x03\x04' + b'0' * 96, headers={'Authorization': 'Bearer ' + key}, cookie=False)[0] == 200


def test_site_limited_users_cannot_upload():
    reset()
    server.STATE['auth'] = 'login'
    try:
        auth.init('root@example.com')
        user, _ = auth.create_user('lim@example.com', 'admin')
        auth.update_user(user['id'], 0, sites=['shop'])
        with auth._db() as c:
            tok = auth._session(c, auth._user(c, user['id']), '127.0.0.1')
        h = dict(XRW, Cookie='cr_session=' + tok)
        s, st = req('POST', '/api/migrate/upload/start', {'name': 'a.zip', 'size': 100}, headers=h, cookie=False)
        assert s == 403
        reset()
        uid = bu.start('a.zip', 100)['id']
        assert req('POST', '/api/migrate/upload/chunk?id=%s&offset=0' % uid, raw=b'x' * 10, headers=h, cookie=False)[0] == 403
        assert bu.status(uid)['offset'] == 0
    finally:
        server.STATE['auth'] = 'token'


if __name__ == '__main__':
    run_tests(dict(globals()))
