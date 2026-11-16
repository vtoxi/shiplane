# One file operation on the server for the Files page. Runs as root; the request is a JSON literal filled in by coderunner/files.py (never shell text).
# Every path is resolved (symlinks followed) and must end up inside an allowed folder and outside the secrets list, whatever the request says.
import base64, fnmatch, json, os, shutil, stat, sys, tempfile, time

REQ = json.loads(__REQ__)
ROOTS = [os.path.realpath(r) for r in REQ['roots']]
READ_ONLY = [os.path.realpath(r) for r in REQ.get('read_only', [])]
DENY = ['/etc/shadow*', '/etc/gshadow*', '/etc/sudoers*', '/etc/ssh/ssh_host_*', '/etc/ssl/private*', '/etc/coderunner.env', '/etc/shiplane*', '/var/lib/coderunner*', '/opt/coderunner*',
        '/root/.ssh*', '/home/*/.ssh/id_*', '/home/*/.ssh/*_key', '/home/*/.aws*', '/home/*/.gnupg*', '*/.git/config.lock', '/etc/letsencrypt/archive*', '/etc/letsencrypt/live*']
MAX_LIST = 3000
SRV, HOME_ROOT, ETC, META = (REQ.get(k, d) for k, d in (('srv', '/srv'), ('home', '/home'), ('etc', '/etc'), ('meta', '/etc/deploy-meta')))


def out(obj, code=0):
    print(json.dumps(obj))
    sys.exit(code)


def fail(msg):
    out({'error': msg}, 3)


def inside(path, base):
    return path == base or path.startswith(base.rstrip('/') + '/')


def resolve(path, must_exist=True):
    """Real path of `path` (for a new file: of its folder plus the name), checked against the allowed folders and the secrets list."""
    if not isinstance(path, str) or not path.startswith('/') or '\0' in path or len(path) > 2000:
        fail('Bad path')
    parent, name = os.path.split(path.rstrip('/') or '/')
    real = os.path.realpath(path) if os.path.lexists(path) else os.path.join(os.path.realpath(parent), name)
    if must_exist and not os.path.lexists(path):
        fail('Not found: %s' % path)
    if not any(inside(real, r) for r in ROOTS):
        fail('That location is not available here')
    if any(fnmatch.fnmatch(real, pat) for pat in DENY):
        fail('That file holds secrets and is not available in the file manager')
    return real


def writable(real):
    if any(inside(real, r) for r in READ_ONLY):
        fail('That folder is read-only here')


def entry(base, name):
    p = os.path.join(base, name)
    try:
        st = os.lstat(p)
    except OSError:
        return None
    kind = 'link' if stat.S_ISLNK(st.st_mode) else 'dir' if stat.S_ISDIR(st.st_mode) else 'file' if stat.S_ISREG(st.st_mode) else 'other'
    e = {'name': name, 'type': kind, 'size': st.st_size, 'mtime': int(st.st_mtime), 'mode': oct(st.st_mode & 0o7777)[2:], 'uid': st.st_uid}
    if kind == 'link':
        try:
            e['target'] = os.readlink(p)
            e['type'] = 'dir' if os.path.isdir(p) else 'file'
            e['link'] = True
        except OSError:
            pass
    return e


def owner_of(path):
    st = os.stat(path)
    return st.st_uid, st.st_gid


def cwds():
    """Folders that running processes are working in (services started with WorkingDirectory=...)."""
    found = set()
    me = str(os.getpid())
    for pid in os.listdir('/proc') if os.path.isdir('/proc') else []:
        if pid.isdigit() and pid != me:
            try:
                found.add(os.readlink('/proc/%s/cwd' % pid))
            except OSError:
                pass
    return found


def live_site(name):
    return any(os.path.exists(f % name) for f in (META + '/%s.json', '/etc/systemd/system/%s.service', '/etc/caddy/sites/%s.caddy'))


def lock_reason(real, busy):
    """Why a folder must not be deleted or renamed here ('' when it is safe). The same answer marks the folder in the list."""
    real = os.path.realpath(real)
    parent, name = os.path.split(real)
    if parent == os.path.realpath(SRV) and live_site(name):
        return 'This is the live site "%s". Delete it from the Sites page instead.' % name
    gp, site = os.path.split(parent)
    if gp == os.path.realpath(SRV) and name in ('current', 'releases') and live_site(site):
        return 'Part of the live site "%s": its releases and the live version are managed by deploys.' % site
    if parent == os.path.realpath(HOME_ROOT):
        try:
            import pwd
            for u in pwd.getpwall():
                if os.path.realpath(u.pw_dir) == real:
                    return 'The home folder of the account "%s".' % u.pw_name
        except Exception:
            pass
    if parent == os.path.realpath(ETC):
        return 'A system settings folder: other software needs it.'
    for c in busy:
        if inside(c, real):
            return 'A running program is working in this folder.'
    return ''


def act_ls():
    real = resolve(REQ['path'])
    if not os.path.isdir(real):
        fail('Not a folder')
    entries, truncated = [], False
    busy = cwds()
    with os.scandir(real) as it:
        for i, e in enumerate(sorted(it, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))):
            if i >= MAX_LIST:
                truncated = True
                break
            x = entry(real, e.name)
            if x:
                if x['type'] == 'dir' and not x.get('link'):
                    why = lock_reason(os.path.join(real, e.name), busy)
                    if why:
                        x['lock'] = why
                entries.append(x)
    out({'path': REQ['path'], 'real': real, 'entries': entries, 'truncated': truncated, 'writable': os.access(real, os.W_OK) and not any(inside(real, r) for r in READ_ONLY)})


def kind_of(head, name):
    for sig, k in ((b'\x89PNG', 'image'), (b'\xff\xd8\xff', 'image'), (b'GIF8', 'image'), (b'RIFF', 'image-maybe'), (b'\x00\x00\x01\x00', 'image')):
        if head.startswith(sig):
            return 'image' if k != 'image-maybe' or head[8:12] == b'WEBP' else 'binary'
    if b'\0' in head:                                    # after the image check: real PNG/GIF headers contain NUL bytes
        return 'binary'
    if name.lower().endswith('.svg'):
        return 'text'
    try:
        head.decode('utf-8')
    except UnicodeDecodeError as e:
        if e.start < len(head) - 4:
            return 'binary'
    return 'text'


def act_read():
    real = resolve(REQ['path'])
    if not os.path.isfile(real):
        fail('Not a file')
    st = os.stat(real)
    limit = int(REQ.get('max', 2 * 1024 * 1024))
    with open(real, 'rb') as f:
        head = f.read(8192)
        kind = kind_of(head, real)
        info = {'path': REQ['path'], 'real': real, 'size': st.st_size, 'mtime': st.st_mtime, 'mode': oct(st.st_mode & 0o7777)[2:], 'kind': kind, 'writable': os.access(real, os.W_OK) and not any(inside(real, r) for r in READ_ONLY)}
        want = kind == 'text' or (kind == 'image' and st.st_size <= 8 * 1024 * 1024) or REQ.get('raw')
        if want and st.st_size <= limit:
            f.seek(0)
            info['content_b64'] = base64.b64encode(f.read()).decode()
        elif want:
            info['too_large'] = True
    out(info)


def act_write():
    real = resolve(REQ['path'], must_exist=False)
    writable(real)
    data = base64.b64decode(REQ['content_b64'])
    if len(data) > 8 * 1024 * 1024:
        fail('That file is too large to save here')
    exists = os.path.lexists(real)
    if exists and not os.path.isfile(real):
        fail('That is not a regular file')
    if REQ.get('create_only') and exists:
        fail('A file with that name already exists')
    if exists and REQ.get('expect_mtime') is not None and abs(os.stat(real).st_mtime - float(REQ['expect_mtime'])) > 0.001 and not REQ.get('force'):
        out({'conflict': True, 'mtime': os.stat(real).st_mtime, 'message': 'This file changed on the server after you opened it.'}, 4)
    parent = os.path.dirname(real)
    if not os.path.isdir(parent):
        fail('The folder does not exist')
    fd, tmp = tempfile.mkstemp(prefix='.shiplane-tmp-', dir=parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        if exists:
            st = os.stat(real)
            os.chmod(tmp, st.st_mode & 0o7777)
            os.chown(tmp, st.st_uid, st.st_gid)
        else:
            pst = os.stat(parent)
            os.chmod(tmp, 0o644)
            os.chown(tmp, pst.st_uid, pst.st_gid)                  # a new file belongs to whoever owns its folder (www-data, the site user ...)
        os.replace(tmp, real)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    out({'ok': True, 'mtime': os.stat(real).st_mtime, 'size': len(data)})


def act_mkdir():
    real = resolve(REQ['path'], must_exist=False)
    writable(real)
    if os.path.lexists(real):
        fail('That name already exists')
    parent = os.path.dirname(real)
    if not os.path.isdir(parent):
        fail('The folder does not exist')
    os.mkdir(real, 0o755)
    pst = os.stat(parent)
    os.chown(real, pst.st_uid, pst.st_gid)
    out({'ok': True})


def act_rename():
    src = resolve(REQ['path'])
    dst = resolve(REQ['new_path'], must_exist=False)
    writable(src)
    writable(dst)
    if any(src == r for r in ROOTS):
        fail('That folder cannot be renamed')
    if os.path.lexists(dst):
        fail('That name already exists')
    if os.path.isdir(src) and not os.path.islink(src):
        why = lock_reason(src, cwds())
        if why:
            fail('This folder cannot be renamed: ' + why)
    if os.path.isdir(src) and inside(dst, src):
        fail('A folder cannot be moved into itself')
    if not os.path.isdir(os.path.dirname(dst)):
        fail('The destination folder does not exist')
    shutil.move(src, dst)
    out({'ok': True})


def act_delete():
    p = REQ['path']
    if os.path.islink(p):
        resolve(os.path.dirname(p.rstrip('/')))                      # a link is removed itself, wherever it points
    else:
        resolve(p)                                                   # checks the location and the secrets list
    real_parent = os.path.realpath(os.path.dirname(p))
    target = os.path.join(real_parent, os.path.basename(p.rstrip('/')))   # delete the entry itself: a symlink is removed, never followed
    writable(real_parent)
    if any(target == r for r in ROOTS) or target == '/':
        fail('That folder cannot be deleted')
    if os.path.islink(target) or os.path.isfile(target):
        os.remove(target)
    elif os.path.isdir(target):
        if os.listdir(target) and not REQ.get('recursive'):
            fail('The folder is not empty')
        if os.path.ismount(target):
            fail('That is a mount point')
        why = lock_reason(target, cwds())
        if why:
            fail('This folder cannot be deleted: ' + why)
        shutil.rmtree(target)
    out({'ok': True})


ACTIONS = {'ls': act_ls, 'read': act_read, 'write': act_write, 'mkdir': act_mkdir, 'rename': act_rename, 'delete': act_delete}
try:
    ACTIONS[REQ['action']]()
except PermissionError as e:
    fail('Permission denied: %s' % e.filename)
except OSError as e:
    fail('%s' % (e.strerror or e))
