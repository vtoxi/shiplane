"""Scheduled tasks, disk cleanup and domain/SSL checks with fake servers and sockets (no SSH, no network).

    python tests/test_maintenance.py
"""
import json
import os
import shlex
import socket
import ssl
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeServer, run_tests  # noqa: E402
from coderunner import maintenance as m  # noqa: E402

CFG = {}
ORIGINAL = {(m, 'run'): m.run, (m.deploy, 'status'): m.deploy.status, (m, '_check_host'): m._check_host,
            (m.socket, 'getaddrinfo'): socket.getaddrinfo, (m.socket, 'create_connection'): socket.create_connection,
            (m.ssl, 'create_default_context'): ssl.create_default_context}


def restore():
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)


def raises(fn, *args, text='', kind=(ValueError, LookupError, RuntimeError)):
    try:
        fn(*args)
    except kind as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


class CronServer:
    """Remembers /etc/cron.d files like a real server would."""

    def __init__(self, services=('web',), fail_write=False):
        self.files, self.log, self.services, self.fail_write = {}, [], services, fail_write

    def run(self, cfg, remote, inp=None, timeout=60):
        self.log.append(remote)
        if remote.startswith('id app_'):
            return 0, 'yes' if remote.split()[1][4:] in self.services else 'no'
        if remote.startswith('sudo cat '):
            return 0, self.files.get(shlex.split(remote)[2], '')
        if remote.startswith('sudo tee '):
            if self.fail_write:
                return 1, 'tee: permission denied'
            self.files[shlex.split(remote)[2]] = inp.decode()
            return 0, ''
        if remote.startswith('sudo rm -f '):
            self.files.pop(shlex.split(remote)[3], None)
            return 0, ''
        return 0, ''


# ---------------------------------------------------------------- validation
def test_schedule_validation():
    for ok in ('0 3 * * *', '*/5 * * * *', '0 0 1,15 * 1-5', '30 2 * * 0', '0 0 1 1 7', '0-30/10 * * * *'):
        assert m._check_schedule(ok) == ok
    assert m._check_schedule('  0   3 * * * ') == '0 3 * * *'
    for bad, text in (('0 3 * *', '5 parts'), ('', '5 parts'), ('0 3 * * * *', '5 parts'), ('60 * * * *', 'out of range'), ('* 25 * * *', 'out of range'),
                      ('0 0 0 * *', 'out of range'), ('0 0 * 13 *', 'out of range'), ('*/5 * * * 9', 'out of range'), ('a b c d e', 'looks wrong'),
                      ('0 3 * * x', 'looks wrong'), ('0 3 * * * ; rm', '5 parts'), ('1;2 * * * *', 'looks wrong'), ('*/ * * * *', 'looks wrong')):
        raises(m._check_schedule, bad, text=text)


def test_command_validation():
    for ok in ('node jobs/clean.js --days=30', 'php artisan schedule:run', '/usr/bin/node a.js', 'a-b_c.sh x@y,z+1'):
        assert m.CRON_CMD_RE.match(ok), ok
    for bad in ('a; rm -rf /', 'a | b', 'a && b', 'a `b`', 'a $(b)', 'a > f', 'a < f', "a 'b'", 'a "b"', 'a 50%', 'a\nb', '', 'x' * 201, 'a\\b'):
        assert not m.CRON_CMD_RE.match(bad), repr(bad)


# ---------------------------------------------------------------- cron (fake server)
def test_cron_add_list_delete_roundtrip():
    s = CronServer()
    m.run = s.run
    rc, text = m.cron_add(CFG, {'name': 'web', 'schedule': '0 3 * * *', 'command': 'node jobs/clean.js --days=30'})
    assert rc == 0 and text == 'added'
    written = s.files['/etc/cron.d/coderunner-web']
    assert written.startswith('PATH=') and '0 3 * * * app_web cd /srv/web/current && node jobs/clean.js --days=30\n' in written
    tasks = m.cron_list(CFG, 'web')
    assert [(t['schedule'], t['command']) for t in tasks] == [('0 3 * * *', 'node jobs/clean.js --days=30')]
    m.cron_add(CFG, {'name': 'web', 'schedule': '*/5 * * * *', 'command': 'node b.js'})
    assert len(m.cron_list(CFG, 'web')) == 2
    first = m.cron_list(CFG, 'web')[0]['id']
    m.cron_delete(CFG, {'name': 'web', 'id': first})
    assert [t['command'] for t in m.cron_list(CFG, 'web')] == ['node b.js']
    m.cron_delete(CFG, {'name': 'web', 'id': m.cron_list(CFG, 'web')[0]['id']})
    assert '/etc/cron.d/coderunner-web' not in s.files and m.cron_list(CFG, 'web') == []     # empty file is removed, not left behind


def test_cron_add_rejections_never_touch_the_server():
    s = CronServer()
    m.run = s.run
    good = {'name': 'web', 'schedule': '0 3 * * *', 'command': 'x'}
    raises(m.cron_add, CFG, dict(good, name='web; rm -rf /'), text='bad site name')
    raises(m.cron_add, CFG, dict(good, name='../etc'), text='bad site name')
    raises(m.cron_add, CFG, dict(good, schedule='* * *'), text='5 parts')
    raises(m.cron_add, CFG, dict(good, command='a; b'), text='Command:')
    assert not s.log and not s.files
    raises(m.cron_add, CFG, dict(good, name='static-site'), text='service')                  # static sites have no service user
    assert not s.files


def test_cron_limit_and_write_failure():
    s = CronServer()
    m.run = s.run
    for i in range(m.CRON_MAX):
        assert m.cron_add(CFG, {'name': 'web', 'schedule': '0 %d * * *' % (i % 24), 'command': 'job%d' % i})[0] == 0
    raises(m.cron_add, CFG, {'name': 'web', 'schedule': '1 1 * * *', 'command': 'one more'}, text='At most 20')
    assert len(m.cron_list(CFG, 'web')) == m.CRON_MAX
    s2 = CronServer(fail_write=True)
    m.run = s2.run
    rc, text = m.cron_add(CFG, {'name': 'web', 'schedule': '0 3 * * *', 'command': 'x'})
    assert rc == 1 and 'permission denied' in text


def test_cron_delete_unknown_task_and_parsing_edge_cases():
    s = CronServer()
    m.run = s.run
    raises(m.cron_delete, CFG, {'name': 'web', 'id': 'deadbeef00'}, text='no longer exists')
    s.files['/etc/cron.d/coderunner-web'] = 'PATH=/bin\n# comment\n\n0 3 * * * app_web cd /srv/web/current && node a.js\n5 5 * * *\n'
    tasks = m.cron_list(CFG, 'web')
    assert len(tasks) == 2 and tasks[0]['command'] == 'node a.js' and tasks[1]['command'] == '' and tasks[1]['schedule'] == '5 5 * * *'
    assert len({t['id'] for t in tasks}) == 2
    raises(m.cron_list, CFG, 'Bad Name', text='bad site name')


# ---------------------------------------------------------------- domain and SSL health
class FakeTLS:
    def __init__(self, cert):
        self.cert = cert

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getpeercert(self):
        return self.cert


class FakeCtx:
    def __init__(self, cert=None, error=None):
        self.cert, self.error = cert, error

    def wrap_socket(self, raw, server_hostname=None):
        if self.error:
            raise self.error
        return FakeTLS(self.cert)


class FakeConn:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False


def cert_in(days):
    return {'notAfter': time.strftime('%b %d %H:%M:%S %Y GMT', time.gmtime(time.time() + days * 86400 + 3600)), 'issuer': ((('organizationName', 'Cloudflare, Inc.'),),)}


def test_check_host_reads_dns_and_certificate():
    m.socket.getaddrinfo = lambda host, port, type=0: [(0, 0, 0, '', ('104.16.1.1', 443))]
    m.socket.create_connection = lambda addr, timeout: FakeConn()
    m.ssl.create_default_context = lambda: FakeCtx(cert_in(30))
    r = m._check_host('a.example.com')
    assert r['dns'] == '104.16.1.1' and r['cert_days'] in (29, 30) and r['issuer'] == 'Cloudflare, Inc.' and r['tls_error'] is None
    m.ssl.create_default_context = lambda: FakeCtx(cert_in(-3))
    assert m._check_host('a.example.com')['cert_days'] < 0                                      # expired certificates are reported, not hidden
    m.ssl.create_default_context = lambda: FakeCtx({'notAfter': cert_in(5)['notAfter']})
    assert m._check_host('a.example.com')['issuer'] is None


def test_check_host_failures():
    def no_dns(*a, **k):
        raise socket.gaierror('not found')
    m.socket.getaddrinfo = no_dns
    r = m._check_host('gone.example.com')
    assert r['dns'] is None and r['cert_days'] is None and r['tls_error'] is None
    m.socket.getaddrinfo = lambda host, port, type=0: [(0, 0, 0, '', ('1.2.3.4', 443))]
    m.socket.create_connection = lambda addr, timeout: FakeConn()
    m.ssl.create_default_context = lambda: FakeCtx(error=ssl.SSLError('certificate verify failed: self signed'))
    r = m._check_host('bad.example.com')
    assert r['dns'] == '1.2.3.4' and r['cert_days'] is None and 'self signed' in r['tls_error']

    def refuse(*a, **k):
        raise ConnectionRefusedError('refused')
    m.socket.create_connection = refuse
    assert 'refused' in m._check_host('closed.example.com')['tls_error']
    m.socket.create_connection = lambda addr, timeout: FakeConn()
    m.ssl.create_default_context = lambda: FakeCtx({})
    assert m._check_host('nocert.example.com')['tls_error']                                       # a certificate without an expiry date


def test_domain_health_only_checks_known_sites():
    sites = [{'name': 'a', 'hostname': 'a.example.com', 'public': 200, 'public_ms': 80}, {'name': 'wild', 'hostname': '*.example.com'},
             {'name': 'none', 'hostname': ''}, {'name': 'weird', 'hostname': 'bad host!'}, {'name': 'b', 'hostname': 'b.example.com'}]
    m.deploy.status = lambda cfg: {'sites': sites}
    seen = []
    m._check_host = lambda host: seen.append(host) or {'host': host, 'dns': '1.1.1.1', 'cert_days': 50, 'issuer': 'x', 'tls_error': None}
    out = m.domain_health(CFG)
    assert sorted(seen) == ['a.example.com', 'b.example.com']                                    # nothing outside the server's own site list is contacted
    assert [(d['site'], d['public']) for d in out] == [('a', 200), ('b', None)] and out[0]['public_ms'] == 80


# ---------------------------------------------------------------- disk
REPORT = {'disk_total': 100, 'disk_free': 40, 'sites': [], 'system': {'journal': 1, 'apt': 2, 'docker': []}}


def test_disk_report_parses_and_reports_errors():
    m.run = FakeServer().when('python3', 0, 'noise\n' + json.dumps(REPORT) + '\n').run
    assert m.disk_report(CFG) == REPORT
    m.run = FakeServer().when('python3', 1, 'sudo: a password is required').run
    raises(m.disk_report, CFG, text='password is required')
    m.run = FakeServer().when('python3', 0, 'no json').run
    raises(m.disk_report, CFG, text='no json')
    m.run = FakeServer().when('python3', 1, '').run
    raises(m.disk_report, CFG, text='could not read disk usage')


def test_disk_clean_releases_commands_and_free_space():
    free = iter(['1000', '1700'])
    srv = FakeServer().when('df -B1', 0, '')
    srv.run_orig = srv.run

    def run(cfg, remote, inp=None, timeout=60):
        if remote.startswith('df'):
            return 0, next(free)
        return srv.run_orig(cfg, remote, inp, timeout)
    m.run = run
    rc, label, freed, out = m.disk_clean(CFG, {'what': 'releases', 'name': 'web', 'keep': 2})
    cmd = srv.calls[0]['inp'].decode()
    assert rc == 0 and freed == 700 and label == 'old releases of web' and 'KEEP=2' in cmd and '/srv/web' in cmd and 'current' in cmd


def test_disk_clean_limits_and_fixed_commands():
    m.run = lambda cfg, remote, inp=None, timeout=60: (0, '1000' if remote.startswith('df') else 'done')
    for given, expected in ((0, 1), (-5, 1), (99, 10), ('4', 4)):
        captured = []
        m.run = lambda cfg, remote, inp=None, timeout=60, c=captured: (0, '1') if remote.startswith('df') else (c.append(inp.decode()) or (0, ''))
        m.disk_clean(CFG, {'what': 'releases', 'keep': given})
        assert 'KEEP=%d' % expected in captured[0] and '/srv/*' in captured[0], (given, captured)
    for what in ('journal', 'apt', 'docker'):
        sent = []
        m.run = lambda cfg, remote, inp=None, timeout=60, s=sent: (0, '5') if remote.startswith('df') else (s.append(inp.decode()) or (0, 'ok'))
        rc, label, freed, out = m.disk_clean(CFG, {'what': what})
        assert label == what and sent[0] == m.CLEANERS[what] and out == 'ok' and freed == 0     # free space did not grow: never negative
    raises(m.disk_clean, CFG, {'what': 'rm -rf /'}, text='unknown cleanup')
    raises(m.disk_clean, CFG, {}, text='unknown cleanup')
    raises(m.disk_clean, CFG, {'what': 'releases', 'name': '../etc'}, text='bad site name')
    raises(m.disk_clean, CFG, {'what': 'releases', 'name': 'a b'}, text='bad site name')
    raises(m.disk_clean, CFG, {'what': 'releases', 'keep': 'many'}, text='invalid literal', kind=ValueError)


def test_free_space_parsing():
    m.run = FakeServer().when('df', 0, '  12345\n').run
    assert m._free(CFG) == 12345
    m.run = FakeServer().when('df', 1, '').run
    assert m._free(CFG) == 0


if __name__ == '__main__':
    run_tests(globals(), before=restore)
