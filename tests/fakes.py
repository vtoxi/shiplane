"""Shared test doubles: a fake server that answers shell commands by pattern, fake processes and a tiny test runner."""
import io
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeServer:
    """run(cfg, remote, ...) answers by the first rule whose text is contained in the command; records every call."""

    def __init__(self, rules=()):
        self.rules, self.calls = list(rules), []

    def when(self, text, rc=0, out=''):
        self.rules.insert(0, (text, (rc, out)))
        return self

    def run(self, cfg, remote, inp=None, timeout=60):
        self.calls.append({'cmd': remote, 'inp': inp})
        for text, answer in self.rules:
            if text in remote:
                return answer
        return 0, ''

    def ran(self, text):
        return [c for c in self.calls if text in c['cmd']]


class Sink(io.BytesIO):
    """A stdin that keeps what was written even after close()."""

    def __init__(self):
        super().__init__()
        self.data = b''

    def close(self):
        self.data = self.getvalue()
        super().close()


class FakeProc:
    def __init__(self, out=b'', rc=0):
        self.stdin, self.stdout, self.rc = Sink(), io.BytesIO(out), rc

    def wait(self):
        return self.rc


class FakeStream:
    """Stands in for subprocess.Popen in _stream(): writes canned bytes to the stdout file it was given."""

    def __init__(self, handler):
        self.handler = handler
        self.cmds = []

    def __call__(self, cmd, stdout=None, stderr=None, env=None, creationflags=0):
        self.cmds.append(cmd[-1])
        data, rc, err = self.handler(cmd[-1])
        stdout.write(data)
        proc = type('P', (), {})()
        proc.returncode = rc
        proc.communicate = lambda: (None, err)
        return proc


def run_tests(namespace, before=None):
    """Run every test_* function in namespace (calling before() first each time); print PASS/FAIL, exit 1 on any failure."""
    failed = 0
    for name, fn in sorted(namespace.items()):
        if name.startswith('test_') and callable(fn):
            try:
                if before:
                    before()
                fn()
                print('PASS', name)
            except Exception:
                failed += 1
                print('FAIL', name)
                traceback.print_exc()
    print('\n' + ('ALL PASSED' if not failed else '%d FAILED' % failed))
    sys.exit(1 if failed else 0)
