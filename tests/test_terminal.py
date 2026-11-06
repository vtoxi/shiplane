"""The browser terminal: a real pseudo-terminal shell (module), and the HTTP routes + live event stream with who-may-use-it checks.

    python tests/test_terminal.py
"""
import base64
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time

os.environ['CODERUNNER_DATA'] = tempfile.mkdtemp()
os.environ.pop('CODERUNNER_AUTH', None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import apikeys, auth, server, terminal  # noqa: E402

LOCAL = {'mode': 'local'}
if not terminal.SUPPORTED:
    print('skipped: the terminal needs a POSIX host (pseudo-terminals)')
    sys.exit(0)


def raises(fn, text, kind=(ValueError, LookupError)):
    try:
        fn()
    except kind as e:
        assert text in str(e), str(e)
        return
    raise AssertionError('expected ' + text)


def wait_for(sess, text, since=0, timeout=8):
    got, off = b'', since
    end = time.time() + timeout
    while time.time() < end and text.encode() not in got:
        data, off, closed = sess.read(off, wait=0.5)
        got += data
        if closed and not data:
            break
    return got.decode(errors='replace'), off


def new(shell='app', **kw):
    return terminal.open_session(LOCAL, kw.get('owner', 'me@example.com'), shell, kw.get('rows', 24), kw.get('cols', 80))


def cleanup():
    terminal.close_all()
    terminal.SESSIONS.clear()


def test_a_real_shell_with_a_real_terminal():
    cleanup()
    s = new()
    s.write(b'echo answer-$((6*7)); tty\n')
    text, off = wait_for(s, '/dev/pts/')
    assert 'answer-42' in text and '/dev/pts/' in text                                                            # a pseudo-terminal, so vim, top and Ctrl+C work
    s.write(b'exit\n')
    wait_for(s, 'never-appears', off, timeout=3)
    for _ in range(60):
        if s.proc.poll() is not None:
            break
        time.sleep(0.05)
    assert s.closed and s.reason == 'the shell exited' and s.proc.poll() is not None
    raises(lambda: s.write(b'x'), 'has ended')


def test_ctrl_c_works_even_when_the_server_itself_ignores_it():
    import signal
    cleanup()
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)                                      # what a server started with `&` from a script inherits
    try:
        s = new()
        s.write(b'sleep 40\n')
        time.sleep(0.4)
        s.write(b'\x03')
        s.write(b'echo free-$((3+4))\n')
        assert 'free-7' in wait_for(s, 'free-7')[0]
    finally:
        signal.signal(signal.SIGINT, old)
        cleanup()


def test_ctrl_c_stops_the_command_not_the_shell_and_resize_reaches_the_program():
    cleanup()
    s = new(rows=24, cols=80)
    s.write(b'sleep 30\n')
    time.sleep(0.4)
    s.write(b'\x03')                                                                                                  # Ctrl+C
    s.write(b'echo still-alive-$((1+1))\n')
    assert 'still-alive-2' in wait_for(s, 'still-alive-2')[0] and not s.closed
    s.resize(40, 132)
    s.write(b'stty size\n')
    assert '40 132' in wait_for(s, '40 132')[0]
    cleanup()


def test_reattach_gets_the_screen_back_from_any_offset():
    cleanup()
    s = new()
    s.write(b'echo first-line\n')
    text, off = wait_for(s, 'first-line')
    again, off2 = wait_for(s, 'first-line', since=0)                                                                  # a new page load asks from 0
    assert 'first-line' in again and off2 >= off
    s.write(b'echo second-line\n')
    new_text, _ = wait_for(s, 'second-line', since=off)                                                               # a resumed stream only gets what is new
    assert 'second-line' in new_text and 'first-line' not in new_text.replace('echo first-line', '')
    cleanup()


def test_history_is_bounded_and_old_offsets_restart_from_the_oldest_byte():
    cleanup()
    s = new()
    old = terminal.HISTORY
    terminal.HISTORY = 4096
    try:
        s.write(b'head -c 20000 /dev/zero | tr "\\0" "x"; echo; echo done-$((1+1))marker\n')
        wait_for(s, 'done-2marker')                                                                                    # computed, so the typed command's own echo cannot match it
        data, off, _ = s.read(0, wait=0)
        assert s.base > 0 and len(s.history) <= 4096 + 70000 and off == s.base + len(s.history)                       # cut to the limit (a read may add a burst)
        assert s.read(0, wait=0)[0] == bytes(s.history)                                                               # offset 0 = "from the oldest I still have"
    finally:
        terminal.HISTORY = old
        cleanup()


def test_ownership_limits_and_validation():
    cleanup()
    a = new(owner='alice@example.com')
    raises(lambda: terminal.get(a.id, 'bob@example.com'), 'No such terminal', LookupError)                            # one person's shell is never reachable by another
    raises(lambda: terminal.get('0' * 32, 'alice@example.com'), 'No such terminal', LookupError)
    assert terminal.get(a.id, 'alice@example.com') is a and [x['id'] for x in terminal.mine('alice@example.com')] == [a.id] and terminal.mine('bob@example.com') == []
    for _ in range(terminal.MAX_SESSIONS - 1):
        new(owner='alice@example.com')
    raises(lambda: new(owner='alice@example.com'), 'already open')
    cleanup()
    raises(lambda: new('zsh'), 'Choose root or the app account')
    s = terminal.open_session(LOCAL, 'x', 'app', 99999, 3)                                                            # absurd sizes are clamped
    s.write(b'stty size\n')
    assert '200 20' in wait_for(s, '200 20')[0]
    cleanup()
    for bad in ('', 'not base64!!', base64.b64encode(b'x' * 70000).decode()):
        raises(lambda: terminal.decode_input(bad), 'Bad input')
    assert terminal.decode_input(base64.b64encode(b'ls\n').decode()) == b'ls\n'


def test_idle_and_age_limits_and_the_off_switch():
    cleanup()
    s = new()
    s.last_input -= terminal.IDLE_SECONDS + 5
    terminal._reap()
    assert s.closed and 'idle' in s.reason
    s2 = new()
    s2.created -= terminal.MAX_SECONDS + 5
    terminal._reap()
    assert s2.closed and 'time limit' in s2.reason
    s3 = new()
    terminal.set_enabled(False)
    assert s3.closed and not terminal.enabled()
    raises(lambda: new(), 'switched off')
    terminal.set_enabled(True)
    assert terminal.enabled()
    real = terminal.SUPPORTED
    terminal.SUPPORTED = False
    try:
        raises(lambda: new(), 'Windows')
    finally:
        terminal.SUPPORTED = real
    cleanup()


def test_how_the_shell_is_started():
    argv, env = terminal.argv_for(LOCAL, 'root')
    assert argv == ['sudo', '-i'] and env['TERM'] == 'xterm-256color'
    assert terminal.argv_for(LOCAL, 'app')[0] == ['bash', '-l']
    key = os.path.join(tempfile.mkdtemp(), 'k')
    open(key, 'w').write('x')
    ssh = {'mode': 'ssh', 'auth': 'key', 'host': 'srv.example.com', 'port': 2222, 'user': 'deploy', 'key': key}
    argv, _ = terminal.argv_for(ssh, 'root')
    assert argv[0] == 'ssh' and argv[1] == '-tt' and argv[-2] == 'deploy@srv.example.com' and argv[-1] == 'sudo -i' and '2222' in argv
    pw = dict(ssh, auth='password', password='hunter2-secret')
    argv, env = terminal.argv_for(pw, 'app')
    assert 'hunter2-secret' not in ' '.join(argv) and env['CR_SSH_PASSWORD'] == 'hunter2-secret'                       # the password never goes on a command line


# --------------------------------------------------------------- over HTTP
server.cfg = lambda: LOCAL
server.audit = lambda kind, target='', detail='', ok=True: AUDIT.append((kind, target, detail))
AUDIT = []
with socket.socket() as _s:
    _s.bind(('127.0.0.1', 0))
    PORT = _s.getsockname()[1]
SRV = server.make_server(PORT)
PORT = server.STATE['port']
threading.Thread(target=SRV.serve_forever, daemon=True).start()
XRW = {'X-Requested-With': 'coderunner'}


def req(method, path, body=None, headers=None, cookie=None, key=None):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=20)
    h = {'Host': '127.0.0.1:%d' % PORT}
    if key:
        h['Authorization'] = 'Bearer ' + key
    h['Cookie'] = cookie if cookie is not None else 'cr=%s' % server.STATE['token']
    h.update(XRW if headers is None else headers)
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, (json.loads(data) if data[:1] in (b'{', b'[') else data)


def read_stream(path, until, cookie=None, timeout=10):
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=timeout)
    c.request('GET', path, headers={'Host': '127.0.0.1:%d' % PORT, 'Cookie': cookie if cookie is not None else 'cr=%s' % server.STATE['token']})
    r = c.getresponse()
    if r.status != 200:
        out = r.status, r.read()
        c.close()
        return out
    got, last = b'', 0
    end = time.time() + timeout
    while time.time() < end and until.encode() not in got:
        line = r.fp.readline()
        if not line:
            break
        if line.startswith(b'data: ') and not line.startswith(b'data: {"reason"'):
            ev = json.loads(line[6:])
            got += base64.b64decode(ev['d'])
            last = ev['n']
    c.close()
    return 200, got.decode(errors='replace'), last


def test_http_open_type_stream_reattach_close():
    cleanup()
    AUDIT.clear()
    s, info = req('POST', '/api/terminal/open', {'shell': 'app', 'rows': 30, 'cols': 100})
    assert s == 200 and info['shell'] == 'app'
    sid = info['id']
    typed = base64.b64encode(b'echo http-$((19+23))\n').decode()
    assert req('POST', '/api/terminal/input', {'id': sid, 'data': typed})[0] == 200
    s, out, last = read_stream('/api/terminal/stream?id=%s&since=0' % sid, 'http-42')
    assert s == 200 and 'http-42' in out
    s, again, _ = read_stream('/api/terminal/stream?id=%s&since=0' % sid, 'http-42')                                  # a reload gets the screen back
    assert 'http-42' in again
    assert req('POST', '/api/terminal/resize', {'id': sid, 'rows': 50, 'cols': 120})[0] == 200
    assert req('GET', '/api/terminal')[1]['sessions'][0]['id'] == sid
    assert req('POST', '/api/terminal/close', {'id': sid})[0] == 200
    assert req('POST', '/api/terminal/input', {'id': sid, 'data': typed})[0] == 400                                   # ended
    assert [a[0] for a in AUDIT] == ['terminal-open', 'terminal-close'] and all('http-' not in ' '.join(a) for a in AUDIT)   # audited, never what was typed
    cleanup()


def test_who_may_use_the_terminal():
    cleanup()
    sid = req('POST', '/api/terminal/open', {'shell': 'app'})[1]['id']
    assert req('POST', '/api/terminal/open', {'shell': 'app'}, headers={})[0] == 403                                  # no browser header
    assert req('POST', '/api/terminal/open', {'shell': 'app'}, cookie='')[0] == 401                                   # not signed in
    assert read_stream('/api/terminal/stream?id=%s' % sid, 'x', cookie='')[0] == 401
    for scope in ('admin', 'viewer', 'deploy'):                                                                       # no API key, whatever its scope
        k, _ = apikeys.create('k-' + scope, scope)
        assert req('POST', '/api/terminal/open', {'shell': 'app'}, key=k, cookie='')[0] in (403,), scope
        assert req('GET', '/api/terminal', key=k, cookie='')[0] == 403, scope
        assert read_stream('/api/terminal/stream?id=%s' % sid, 'x', cookie='')[0] == 401
    assert read_stream('/api/terminal/stream?id=nope', 'x')[0] == 404
    cleanup()


def test_admins_and_other_super_admins_cannot_reach_it():
    cleanup()
    server.STATE['auth'] = 'login'
    try:
        auth.init('root@example.com')
        users = {}
        for email, role in (('admin@example.com', 'admin'), ('boss1@example.com', 'super_admin'), ('boss2@example.com', 'super_admin')):
            u, _ = auth.create_user(email, role)
            with auth._db() as c:
                users[email] = 'cr_session=' + auth._session(c, auth._user(c, u['id']), '127.0.0.1')
        assert req('POST', '/api/terminal/open', {'shell': 'app'}, cookie=users['admin@example.com'])[0] == 403
        assert req('GET', '/api/terminal', cookie=users['admin@example.com'])[0] == 403
        s, info = req('POST', '/api/terminal/open', {'shell': 'app'}, cookie=users['boss1@example.com'])
        assert s == 200
        assert read_stream('/api/terminal/stream?id=%s' % info['id'], 'x', cookie=users['admin@example.com'])[0] == 403
        assert read_stream('/api/terminal/stream?id=%s' % info['id'], 'x', cookie=users['boss2@example.com'])[0] == 404        # another super admin cannot attach
        assert req('POST', '/api/terminal/input', {'id': info['id'], 'data': base64.b64encode(b'id\n').decode()}, cookie=users['boss2@example.com'])[0] == 404
        assert req('POST', '/api/terminal/close', {'id': info['id']}, cookie=users['boss2@example.com'])[0] == 404
        assert not terminal.SESSIONS[info['id']].closed
    finally:
        server.STATE['auth'] = 'token'
        cleanup()


if __name__ == '__main__':
    run_tests(dict(globals()))
