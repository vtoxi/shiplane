"""Uptime checks: the schedule setting, light-weight HEAD probes, history length and the check loop (no network, no SSH).

    python tests/test_monitor.py
"""
import os
import sys
import tempfile
import urllib.error

DATA = tempfile.mkdtemp()
os.environ['CODERUNNER_DATA'] = DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import config, monitor, settings  # noqa: E402

ORIGINAL = {(monitor.urllib.request, 'urlopen'): monitor.urllib.request.urlopen, (monitor, 'check_once'): monitor.check_once, (monitor, 'check_resources'): monitor.check_resources,
            (monitor, '_sites'): monitor._sites, (monitor, 'HEALTH_FILE'): monitor.HEALTH_FILE}


def restore():
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)
    monitor.HEALTH_FILE = os.path.join(DATA, 'health.json')
    monitor.STATE.clear()
    path = os.path.join(config.DATA, 'settings.json')
    if os.path.exists(path):
        os.remove(path)


def raises(fn, *args, text=''):
    try:
        fn(*args)
    except ValueError as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


def test_default_schedule_and_saving():
    assert monitor.schedule() == {'interval': 300, 'enabled': True}
    assert monitor.set_schedule(600, True) == {'interval': 600, 'enabled': True} and monitor.schedule()['interval'] == 600
    assert monitor.set_schedule('60', False) == {'interval': 60, 'enabled': False}
    assert monitor.set_schedule(3600, 0)['enabled'] is False


def test_bad_schedules_are_refused_and_bad_stored_values_ignored():
    for bad in (0, 30, 90, 7200, -120, 'often', None, 1.5e9):
        raises(monitor.set_schedule, bad, True, text='Choose one of')
    assert monitor.schedule()['interval'] == 300                                                  # nothing was saved
    settings.put('monitor', {'interval': 7, 'enabled': 'yes'})
    assert monitor.schedule() == {'interval': 300, 'enabled': True}                               # a hand-edited bad value falls back to the default


def test_history_length_follows_the_interval():
    assert monitor.keep(60) == 1440 and monitor.keep(300) == 288 and monitor.keep(3600) == 24 and monitor.keep(86400 * 2) == 24
    monitor.check_once({'a': 'a.example.com'}, probe=lambda h: 5, interval=3600)
    for _ in range(30):
        monitor.check_once({'a': 'a.example.com'}, probe=lambda h: 5, interval=3600)
    assert len(monitor.STATE['a']['points']) == 24                                                # 24 hours at one check an hour
    monitor.STATE.clear()
    monitor.set_schedule(600, True)
    monitor.check_once({'a': 'a.example.com'}, probe=lambda h: 5)                                 # without an explicit interval the saved one is used
    assert monitor.health()['interval'] == 600 and monitor.health()['enabled'] is True


class Resp:
    def read(self, n=-1):
        return b'x'


def test_probe_uses_head_and_only_falls_back_to_get_when_refused():
    seen = []

    def ok(req, timeout=0):
        seen.append(req.get_method())
        return Resp()
    monitor.urllib.request.urlopen = ok
    assert monitor.probe('a.example.com') >= 1 and seen == ['HEAD']                               # headers only: about 1 KB per check

    def head_refused(req, timeout=0):
        seen.append(req.get_method())
        if req.get_method() == 'HEAD':
            raise urllib.error.HTTPError(req.full_url, 405, 'no head', {}, None)
        return Resp()
    seen.clear()
    monitor.urllib.request.urlopen = head_refused
    assert monitor.probe('a.example.com') >= 1 and seen == ['HEAD', 'GET']


def test_probe_results():
    def raising(code):
        def f(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, code, 'x', {}, None)
        return f
    monitor.urllib.request.urlopen = raising(404)
    assert monitor.probe('a.example.com') >= 1                                                    # the site answered: it is up
    monitor.urllib.request.urlopen = raising(401)
    assert monitor.probe('a.example.com') >= 1
    for code in (500, 502, 503):
        monitor.urllib.request.urlopen = raising(code)
        assert monitor.probe('a.example.com') is None
    monitor.urllib.request.urlopen = raising(501)                                                 # refuses HEAD and then GET: counted by the GET answer
    assert monitor.probe('a.example.com') is None

    def unreachable(req, timeout=0):
        raise OSError('connection refused')
    monitor.urllib.request.urlopen = unreachable
    assert monitor.probe('a.example.com') is None

    def broken_get(req, timeout=0):
        if req.get_method() == 'HEAD':
            raise urllib.error.HTTPError(req.full_url, 405, 'x', {}, None)
        raise urllib.error.HTTPError(req.full_url, 503, 'x', {}, None)
    monitor.urllib.request.urlopen = broken_get
    assert monitor.probe('a.example.com') is None


def test_round_runs_checks_only_when_enabled_and_never_crashes():
    calls = []
    monitor._sites = lambda cfg: {'a': 'a.example.com'}
    monitor.check_once = lambda sites, interval=None: calls.append(('sites', sorted(sites), interval))
    monitor.check_resources = lambda cfg: calls.append(('resources',))
    monitor.set_schedule(120, True)
    assert monitor.run_round(lambda: {}) == 120 and calls == [('sites', ['a'], 120), ('resources',)]
    calls.clear()
    monitor.set_schedule(900, False)
    assert monitor.run_round(lambda: {}) == 900 and calls == [('resources',)]                      # switched off: no requests to the sites at all

    def boom(*a, **k):
        raise RuntimeError('server unreachable')
    monitor.check_once, monitor.check_resources = boom, boom
    monitor.set_schedule(300, True)
    assert monitor.run_round(lambda: {}) == 300                                                   # failures are swallowed, the loop keeps going


def test_start_loops_with_the_saved_interval():
    waits = []
    rounds = []
    real_sleep, real_round = monitor.time.sleep, monitor.run_round
    monitor.run_round = lambda load: rounds.append(1) or 42

    def sleep(n):
        waits.append(n)
        raise SystemExit
    monitor.time.sleep = sleep
    try:
        monitor.start(lambda: {})
        for _ in range(100):
            if waits:
                break
            real_sleep(0.02)
    finally:
        monitor.time.sleep, monitor.run_round = real_sleep, real_round
    assert rounds and waits == [42]


def test_sites_remembers_who_deployed_what_for_the_alerts():
    real = monitor.deploy.status
    monitor.deploy.status = lambda cfg: {'sites': [{'name': 'shop', 'hostname': 'shop.example.com', 'meta': {'deployedBy': 'a@example.com'}},
                                                  {'name': 'blog', 'hostname': 'blog.example.com'}, {'name': 'wild', 'hostname': '*.example.com', 'meta': {}}]}
    try:
        assert ORIGINAL[(monitor, '_sites')]({}) == {'shop': 'shop.example.com', 'blog': 'blog.example.com'}
    finally:
        monitor.deploy.status = real
    assert monitor.alerts.OWNERS == {'shop': 'a@example.com', 'blog': ''}
    monitor.alerts.OWNERS.clear()



def test_check_now_records_one_site_like_a_scheduled_check():
    monitor.STATE.clear()
    monitor.STATE['other'] = {'host': 'o.example.com', 'points': [[1, 5]], 'up': True, 'fails': 0}
    monitor.CODES['a.example.com'] = (200, 0)
    out = monitor.check_now('a', 'a.example.com', probe=lambda host: 42)
    assert out == {'up': True, 'ms': 42, 'code': 200}
    assert monitor.STATE['a']['points'][-1][1] == 42 and 'other' in monitor.STATE      # other sites are left alone
    monitor.CODES['a.example.com'] = ('err', 0)
    assert monitor.check_now('a', 'a.example.com', probe=lambda host: None) == {'up': False, 'ms': None, 'code': 'err'}

if __name__ == '__main__':
    run_tests(globals(), before=restore)
