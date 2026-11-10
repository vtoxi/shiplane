"""The Files page: browse, read, create, edit, rename and delete files and folders on the server. Every operation is one fixed script (remote/files_op.py)
with a validated request; the script resolves symlinks and enforces the allowed folders again on the server, so a bad path cannot escape them.

Admins reach the web and home folders; super admins also /etc, /opt, /root and (read-only) /var/log and /var/backups. Files that hold secrets
(shadow, sudoers, host keys, private SSH keys, this app's own data) are never shown or changed.
"""
import base64
import json
import posixpath
import re

from .runner import run, script

ADMIN_ROOTS = ('/var/www', '/srv', '/home')
SUPER_ROOTS = ADMIN_ROOTS + ('/etc', '/opt', '/root', '/var/log', '/var/backups')
READ_ONLY = ('/var/log', '/var/backups')
GUARDS = {}                                   # test hook: where the sites, homes and settings live (defaults: /srv, /home, /etc)
MAX_EDIT = 2 * 1024 * 1024
MAX_DOWNLOAD = 25 * 1024 * 1024


def roots_for(role):
    return SUPER_ROOTS if role == 'super_admin' else ADMIN_ROOTS


def clean_path(path):
    path = str(path or '').strip()
    if not path.startswith('/') or '\0' in path or len(path) > 1000 or any(ord(c) < 32 for c in path):
        raise ValueError('The path looks wrong')
    norm = posixpath.normpath(path)
    return '/' + norm.lstrip('/')


def clean_name(name):
    name = str(name or '')
    if not name or name in ('.', '..') or '/' in name or len(name.encode('utf-8', 'replace')) > 255 or any(ord(c) < 32 for c in name) or name != name.strip():
        raise ValueError('That name is not allowed (no slashes, no leading or trailing spaces, up to 255 characters)')
    return name


def _op(cfg, role, action, **fields):
    top = [r for r in roots_for(role)]
    req = dict(GUARDS, **dict(fields, action=action, roots=top, read_only=[r for r in READ_ONLY if r in top]))
    text = script('files_op.py').replace('__REQ__', repr(json.dumps(req)))
    rc, out = run(cfg, 'sudo python3 -', inp=text.encode(), timeout=90)
    if '{' not in out:
        raise RuntimeError(out.strip()[-300:] or 'could not reach the server')
    data = json.loads(out[out.index('{'):out.rindex('}') + 1])
    if data.get('conflict'):
        return data
    if rc or data.get('error'):
        raise ValueError(data.get('error') or 'The operation failed')
    return data


def top_level(role):
    """The folders a person can start from."""
    return {'path': '/', 'entries': [{'name': r.lstrip('/'), 'type': 'dir', 'size': 0, 'mtime': 0, 'mode': '', 'root': True} for r in roots_for(role)], 'writable': False, 'truncated': False}


def list_dir(cfg, role, path):
    path = clean_path(path or '/')
    if path == '/':
        return top_level(role)
    return _op(cfg, role, 'ls', path=path)


def read(cfg, role, path, download=False):
    path = clean_path(path)
    out = _op(cfg, role, 'read', path=path, max=MAX_DOWNLOAD if download else MAX_EDIT, raw=bool(download))
    out.pop('real', None)
    return out


def write(cfg, role, path, content, expect_mtime=None, create_only=False, force=False):
    path = clean_path(path)
    clean_name(posixpath.basename(path))
    if not isinstance(content, str):
        raise ValueError('The content must be text')
    data = content.encode('utf-8')
    if len(data) > MAX_EDIT:
        raise ValueError('Files over %d MB cannot be edited here' % (MAX_EDIT // 1024 // 1024))
    return _op(cfg, role, 'write', path=path, content_b64=base64.b64encode(data).decode(), expect_mtime=expect_mtime, create_only=bool(create_only), force=bool(force))


def mkdir(cfg, role, path):
    path = clean_path(path)
    clean_name(posixpath.basename(path))
    return _op(cfg, role, 'mkdir', path=path)


def rename(cfg, role, path, new_name):
    path = clean_path(path)
    new_path = posixpath.join(posixpath.dirname(path), clean_name(new_name))
    return _op(cfg, role, 'rename', path=path, new_path=new_path)


def move(cfg, role, path, new_path):
    return _op(cfg, role, 'rename', path=clean_path(path), new_path=clean_path(new_path))


def delete(cfg, role, path, recursive=False):
    path = clean_path(path)
    if path == '/' or path in roots_for(role):
        raise ValueError('That folder cannot be deleted')
    return _op(cfg, role, 'delete', path=path, recursive=bool(recursive))
