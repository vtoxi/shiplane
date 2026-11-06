"""A real terminal in the browser (xterm.js on the page, a pseudo-terminal here). Super admins only.

The shell runs on the machine this app controls: directly when the app runs on the server, or through `ssh -tt` otherwise. Sessions stay on the server
for a while after the page closes (so a reload or a dropped connection reattaches with the screen intact) and are closed after 30 idle minutes.
Needs a POSIX host for the pseudo-terminal (Linux or macOS): a Windows computer running this app can use SSH instead.

Output is read by one thread per session into a bounded history; the browser follows it over Server-Sent Events and sends keystrokes with POST.
Nothing of what is typed is stored or logged (only that a session was opened, by whom, as which shell, and for how long).
"""
import base64
import os
import secrets
import signal
import subprocess
import sys
import threading
import time

from . import runner, settings

try:
    import fcntl
    import pty
    import struct
    import termios
    SUPPORTED = True
except ImportError:                      # Windows
    SUPPORTED = False

# The launcher also resets signals: a server started in the background by a shell inherits "ignore Ctrl+C", and the person's shell must not.
LAUNCHER = ('import fcntl, os, signal, sys, termios\n'
            'for n in ("SIGINT", "SIGQUIT", "SIGTSTP", "SIGTTIN", "SIGTTOU", "SIGHUP", "SIGTERM", "SIGPIPE", "SIGCHLD"): signal.signal(getattr(signal, n), signal.SIG_DFL)\n'
            'os.setsid(); fcntl.ioctl(0, termios.TIOCSCTTY, 0); os.execvp(sys.argv[1], sys.argv[1:])')
MAX_SESSIONS = 3
IDLE_SECONDS = 30 * 60
MAX_SECONDS = 12 * 3600
HISTORY = 256 * 1024                     # what a reattaching browser gets back
SHELLS = {'root': 'A root shell (sudo -i)', 'app': "The app's own account"}
SESSIONS = {}
_lock = threading.Lock()


def enabled():
    return SUPPORTED and settings.get('terminal').get('enabled', True) is not False


def set_enabled(on):
    settings.put('terminal', {'enabled': bool(on)})
    if not on:
        close_all('terminal switched off')
    return enabled()


class Session:
    def __init__(self, sid, owner, shell, argv, env, rows, cols):
        self.id, self.owner, self.shell = sid, owner, shell
        self.created = self.last_input = time.time()
        self.closed = False
        self.reason = ''
        self.base = 0                    # absolute byte offset of history[0]
        self.history = bytearray()
        self.cond = threading.Condition()
        self.bytes_out = 0
        master, slave = pty.openpty()
        self._resize_fd(master, rows, cols)
        # A small launcher makes the shell a session leader with the pseudo-terminal as its controlling terminal (job control, Ctrl+C, vim ...) without preexec_fn.
        self.proc = subprocess.Popen([sys.executable, '-c', LAUNCHER] + argv, stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True)
        os.close(slave)
        self.fd = master
        threading.Thread(target=self._pump, daemon=True).start()

    @staticmethod
    def _resize_fd(fd, rows, cols):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def _pump(self):
        while True:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                data = b''
            if not data:
                break
            with self.cond:
                self.history += data
                self.bytes_out += len(data)
                if len(self.history) > HISTORY:
                    cut = len(self.history) - HISTORY
                    del self.history[:cut]
                    self.base += cut
                self.cond.notify_all()
        self.close('the shell exited')

    def read(self, since, wait=15):
        """(bytes, next offset, closed). Blocks up to `wait` seconds for news. `since` older than the history restarts from the oldest byte kept."""
        with self.cond:
            if self.base + len(self.history) <= since and not self.closed:
                self.cond.wait(wait)
            start = max(since, self.base)
            return bytes(self.history[start - self.base:]), self.base + len(self.history), self.closed

    def write(self, data):
        if self.closed:
            raise ValueError('This terminal session has ended')
        self.last_input = time.time()
        os.write(self.fd, data)

    def resize(self, rows, cols):
        if not self.closed:
            self._resize_fd(self.fd, rows, cols)

    def close(self, reason='closed'):
        with self.cond:
            if self.closed:
                return
            self.closed, self.reason = True, reason
            self.cond.notify_all()
        try:
            os.killpg(self.proc.pid, signal.SIGHUP)
        except (OSError, ProcessLookupError):
            pass
        try:
            self.proc.wait(timeout=3)
        except Exception:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        try:
            os.close(self.fd)
        except OSError:
            pass

    def info(self):
        return {'id': self.id, 'shell': self.shell, 'owner': self.owner, 'created': int(self.created), 'idle': int(time.time() - self.last_input), 'closed': self.closed}


def argv_for(cfg, shell):
    """The command that starts the shell, and the environment it gets."""
    if shell not in SHELLS:
        raise ValueError('Choose root or the app account')
    inner = 'sudo -i' if shell == 'root' else 'exec bash -l'
    if runner.is_local(cfg):
        argv = ['sudo', '-i'] if shell == 'root' else ['bash', '-l']
        env = dict(os.environ, TERM='xterm-256color', LANG=os.environ.get('LANG') or 'C.UTF-8')
        return argv, env
    argv = runner.command(cfg, inner)
    argv.insert(1, '-tt')                                           # a real terminal on the far side too
    return argv, dict(runner.environment(cfg) or os.environ, TERM='xterm-256color')


def _reap(now=None):
    now = now or time.time()
    for sid, s in list(SESSIONS.items()):
        if s.closed and now - s.last_input > 3600:
            SESSIONS.pop(sid, None)
        elif not s.closed and (now - s.last_input > IDLE_SECONDS or now - s.created > MAX_SECONDS):
            s.close('closed after %d idle minutes' % (IDLE_SECONDS // 60) if now - s.last_input > IDLE_SECONDS else 'session time limit reached')


def open_session(cfg, owner, shell='root', rows=24, cols=80):
    if not SUPPORTED:
        raise ValueError('The terminal needs the app to run on Linux or macOS (this computer is Windows). Use SSH from your own terminal instead.')
    if not enabled():
        raise ValueError('The terminal is switched off. A super admin can switch it on.')
    rows, cols = max(5, min(int(rows or 24), 200)), max(20, min(int(cols or 80), 500))
    with _lock:
        _reap()
        if sum(1 for s in SESSIONS.values() if not s.closed) >= MAX_SESSIONS:
            raise ValueError('%d terminals are already open. Close one first.' % MAX_SESSIONS)
        argv, env = argv_for(cfg, shell)
        sid = secrets.token_hex(16)
        SESSIONS[sid] = Session(sid, owner, shell, argv, env, rows, cols)
    return SESSIONS[sid]


def get(sid, owner):
    s = SESSIONS.get(str(sid))
    if not s or s.owner != owner:                       # one person's terminal is never reachable by another
        raise LookupError('No such terminal session')
    return s


def mine(owner):
    _reap()
    return [s.info() for s in SESSIONS.values() if s.owner == owner and not s.closed]


def close_all(reason='closed'):
    for s in list(SESSIONS.values()):
        s.close(reason)


def decode_input(b64):
    try:
        data = base64.b64decode(b64 or '', validate=True)
    except Exception:
        raise ValueError('Bad input')
    if not 0 < len(data) <= 65536:
        raise ValueError('Bad input size')
    return data
