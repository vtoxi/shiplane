"""Calls every API handler with an empty request while the server, network and subprocesses are faked.

A handler may refuse (ValueError, PermissionError, LookupError, RuntimeError, OSError) but must never crash with a programming error
(AttributeError, sqlite errors, ...); TypeError/KeyError from wrong value types are answered 400 by the dispatcher, which would reach the browser as a 500.

    python tests/test_route_sweep.py
"""
import io
import os
import subprocess
import sys
import tempfile
import types
import urllib.request

DATA = tempfile.mkdtemp()
os.environ['CODERUNNER_DATA'] = DATA
os.environ['CODERUNNER_CONFIG'] = DATA
os.environ.pop('CLOUDFLARE_API_TOKEN', None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeProc, run_tests  # noqa: E402
from coderunner import auth, config, server  # noqa: E402

ALLOWED = (ValueError, PermissionError, LookupError, RuntimeError, OSError)
CFG = {'mode': 'ssh', 'auth': 'key', 'host': 'srv.example.com', 'port': 22, 'user': 'deploy', 'key': '', 'password': '', 'domains': []}
SKIP = set()
FAILURES = []


def fake_run(cfg, remote='', inp=None, timeout=60, **kw):
    return 0, ''


def fake_popen(cfg, remote='', **kw):
    return FakeProc(b'', 0)


def install_fakes():
    import coderunner
    for mod in list(sys.modules.values()):
        if getattr(mod, '__name__', '').startswith('coderunner'):
            for name, fn in (('run', fake_run), ('popen', fake_popen)):
                if hasattr(mod, name) and callable(getattr(mod, name)) and getattr(getattr(mod, name), '__module__', '') in ('coderunner.runner', ''):
                    setattr(mod, name, fn)
    subprocess.run = lambda *a, **k: types.SimpleNamespace(returncode=0, stdout='', stderr='')
    subprocess.Popen = lambda *a, **k: FakeProc(b'', 0)

    def no_net(*a, **k):
        raise OSError('network disabled in tests')
    urllib.request.urlopen = no_net
    server.cfg = lambda: CFG
    server.audit = lambda *a, **k: None
    server.ctx.user = {'id': 1, 'email': 'root@example.com', 'role': 'super_admin'}
    server.ctx.via_key, server.ctx.ip, server.ctx.cookies, server.ctx.raw, server.ctx.headers = False, '127.0.0.1', [], b'', {}
    auth.init('root@example.com')


def sweep(method, qs, body, tolerate=()):
    for (m, path), r in sorted(server.ROUTES.items()):
        if m != method or (m, path) in SKIP:
            continue
        try:
            r['fn'](dict(qs), dict(body))
        except ALLOWED + tuple(tolerate):
            pass
        except Exception as e:
            FAILURES.append('%s %s with %r -> %s: %s' % (m, path, body or qs, type(e).__name__, e))


def test_every_get_route_survives_an_empty_request():
    install_fakes()
    sweep('GET', {}, {})
    assert not FAILURES, '\n'.join(FAILURES)


def test_every_post_route_survives_an_empty_body():
    install_fakes()
    sweep('POST', {}, {})
    assert not FAILURES, '\n'.join(FAILURES)


def test_every_post_route_survives_junk_values():
    install_fakes()
    junk = {k: v for k in ('name', 'id', 'host', 'hostname', 'file', 'path', 'zone', 'type', 'action', 'email', 'confirm', 'command', 'url', 'token', 'repo', 'branch')
            for v in ('../x;\n',)}
    sweep('POST', {}, junk)
    # wrong types: the dispatcher answers 400 for TypeError/KeyError, so handlers may raise them here
    sweep('POST', {}, {k: 5 for k in junk}, (TypeError, KeyError))
    sweep('POST', {}, {k: [1, {'a': None}] for k in junk}, (TypeError, KeyError))
    assert not FAILURES, '\n'.join(FAILURES)


if __name__ == '__main__':
    run_tests(dict(globals()), before=FAILURES.clear)
