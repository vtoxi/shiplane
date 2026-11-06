"""Upload a cPanel / Plesk backup from the browser to this server, in resumable pieces, then hand it to Migrate.

Backups are often several GB, so the browser sends the file in small pieces (default 8 MB): a dropped connection costs one piece, not the whole file,
the upload can be resumed after a reload, and nothing has to fit in memory or in one request (Cloudflare refuses requests over 100 MB).

Flow: start(name, size) -> chunk(id, offset, stream, length) ... -> complete(id) -> a path Migrate can read. Files live in <data>/uploads, owner-only.
"""
import json
import os
import re
import secrets
import shutil
import threading
import time

from . import config, migrate

MAX_BACKUP = 100 * 1024 ** 3                  # refuse anything bigger than 100 GB
CHUNK_MAX = 16 * 1024 * 1024                  # one piece at most; the browser sends 8 MB
MAX_OPEN = 3                                  # unfinished uploads at once
KEEP_DAYS = 14                                # finished and abandoned files older than this are deleted
NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._()+\-]{0,150}$')
ID_RE = re.compile(r'^[0-9a-f]{24}$')
_lock = threading.Lock()
_locks = {}                                   # id -> lock, so two pieces of one upload never write at once


def folder():
    d = os.path.join(config.DATA, 'uploads')
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _part(uid):
    return os.path.join(folder(), uid + '.part')


def _meta_path(uid):
    return os.path.join(folder(), uid + '.json')


def _read_meta(uid):
    if not ID_RE.match(str(uid)):
        raise LookupError('Unknown upload')
    try:
        with open(_meta_path(uid), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        raise LookupError('Unknown upload: it may have been finished, cancelled or cleaned up. Start it again.')


def _write_meta(uid, meta):
    with open(_meta_path(uid), 'w', encoding='utf-8') as f:
        json.dump(meta, f)


def _received(uid):
    try:
        return os.path.getsize(_part(uid))
    except OSError:
        return 0


def check_name(name):
    name = str(name or '').strip()
    if not NAME_RE.match(name) or '..' in name or not migrate.ANY_BACKUP_RE.search(name):
        raise ValueError('Choose a backup file ending in .zip, .tgz, .tar.gz or .tar (letters, numbers, spaces and . _ - ( ) + only in its name)')
    return name


def cleanup(now=None):
    """Delete abandoned parts and old finished backups."""
    now = now or time.time()
    d = folder()
    for n in os.listdir(d):
        p = os.path.join(d, n)
        try:
            if now - os.path.getmtime(p) > KEEP_DAYS * 86400 and os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


def need_free(size):
    """Room for the file itself plus about half of it again for what Migrate unpacks, and 1 GB to spare."""
    return int(size * 1.5) + 1024 ** 3


def start(name, size):
    name = check_name(name)
    try:
        size = int(size)
    except (TypeError, ValueError):
        raise ValueError('The file size is missing')
    if not 1 <= size <= MAX_BACKUP:
        raise ValueError('The file must be between 1 byte and %d GB' % (MAX_BACKUP // 1024 ** 3))
    cleanup()
    with _lock:
        open_now = [n for n in os.listdir(folder()) if n.endswith('.part')]
        if len(open_now) >= MAX_OPEN:
            raise ValueError('%d uploads are already unfinished. Finish or cancel one first (or wait: abandoned ones are removed after %d days).' % (len(open_now), KEEP_DAYS))
        free = shutil.disk_usage(folder()).free
        already = sum(os.path.getsize(os.path.join(folder(), n)) for n in open_now)
        if free - already < need_free(size):
            raise ValueError('Not enough free disk space on the server: this file needs about %.1f GB free (the file plus room to unpack it), and %.1f GB is free.'
                             % (need_free(size) / 1024 ** 3, max(0, free - already) / 1024 ** 3))
        uid = secrets.token_hex(12)
        fd = os.open(_part(uid), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        _write_meta(uid, {'name': name, 'size': size, 'created': int(time.time())})
    return {'id': uid, 'offset': 0, 'chunk': 8 * 1024 * 1024}


def status(uid):
    meta = _read_meta(uid)
    return {'id': uid, 'name': meta['name'], 'size': meta['size'], 'offset': _received(uid)}


def chunk(uid, offset, stream, length):
    """Append one piece at `offset`, which must be exactly where the file ends now. Returns the new offset."""
    meta = _read_meta(uid)
    try:
        offset, length = int(offset), int(length)
    except (TypeError, ValueError):
        raise ValueError('Bad piece')
    if not 1 <= length <= CHUNK_MAX:
        raise ValueError('A piece must be between 1 byte and %d MB' % (CHUNK_MAX // 1024 ** 2))
    with _lock:
        lock = _locks.setdefault(uid, threading.Lock())
    with lock:
        have = _received(uid)
        if offset != have:
            raise OffsetError(have)             # the browser asks where to continue from and resumes there
        if have + length > meta['size']:
            raise ValueError('This piece goes past the end of the file')
        left = length
        with open(_part(uid), 'ab') as f:
            while left:
                data = stream.read(min(1024 * 1024, left))
                if not data:
                    f.truncate(have)            # the connection dropped mid-piece: forget the partial piece
                    raise ValueError('The piece was cut short. Try again.')
                f.write(data)
                left -= len(data)
        return {'offset': have + length}


class OffsetError(ValueError):
    def __init__(self, offset):
        super().__init__('The server has %d bytes of this file: continue from there' % offset)
        self.offset = offset


def _looks_like_archive(path):
    with open(path, 'rb') as f:
        head = f.read(512)
    return head[:4] in (b'PK\x03\x04', b'PK\x05\x06') or head[:2] == b'\x1f\x8b' or head[257:262] == b'ustar' or head[:4] == b'\x28\xb5\x2f\xfd'


def complete(uid):
    """All bytes arrived: check them and give the file its final name. Returns {path, name, size}."""
    meta = _read_meta(uid)
    got = _received(uid)
    if got != meta['size']:
        raise ValueError('Only %d of %d bytes arrived. Continue the upload.' % (got, meta['size']))
    part = _part(uid)
    if not _looks_like_archive(part):
        os.remove(part)
        os.remove(_meta_path(uid))
        raise ValueError('That file is not a zip, tar or gzip archive, so it cannot be a backup. It was removed.')
    final = os.path.join(folder(), '%s-%s' % (uid[:8], meta['name']))
    os.replace(part, final)
    os.remove(_meta_path(uid))
    return {'path': final, 'name': meta['name'], 'size': meta['size']}


def abort(uid):
    _read_meta(uid)
    for p in (_part(uid), _meta_path(uid)):
        try:
            os.remove(p)
        except OSError:
            pass


def listing():
    """Unfinished uploads and finished backups on the server, newest first."""
    out = {'unfinished': [], 'backups': []}
    d = folder()
    for n in sorted(os.listdir(d)):
        p = os.path.join(d, n)
        if n.endswith('.json') and ID_RE.match(n[:-5]):
            try:
                out['unfinished'].append(dict(status(n[:-5]), created=_read_meta(n[:-5])['created']))
            except LookupError:
                pass
        elif migrate.ANY_BACKUP_RE.search(n) and os.path.isfile(p):
            out['backups'].append({'name': n[9:] if re.match(r'^[0-9a-f]{8}-', n) else n, 'file': n, 'size': os.path.getsize(p), 'path': p, 'time': int(os.path.getmtime(p))})
    out['backups'].sort(key=lambda b: -b['time'])
    out['unfinished'].sort(key=lambda u: -u['created'])
    return out


def delete(file):
    """Remove one finished backup (by the `file` name from listing())."""
    file = str(file or '')
    if file != os.path.basename(file) or not migrate.ANY_BACKUP_RE.search(file) or file.startswith('.'):
        raise ValueError('Bad file name')
    p = os.path.join(folder(), file)
    if not os.path.isfile(p):
        raise LookupError('No such backup')
    os.remove(p)
