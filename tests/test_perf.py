"""Performance profiles: limits, the shared status cache, memory housekeeping and bounded logs (no server, no network).

    python tests/test_perf.py
"""
import builtins
import os
import sys
import tempfile
import threading
import time

DATA = tempfile.mkdtemp()
os.environ['CODERUNNER_DATA'] = DATA
os.environ.pop('CODERUNNER_PROFILE', None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import FakeServer, run_tests  # noqa: E402
from coderunner import config, deploy, migrator, perf, settings  # noqa: E402

ORIGINAL = {(perf, 'trim'): perf.trim, (perf, 'housekeeping'): perf.housekeeping, (deploy, '_status_now'): deploy._status_now, (deploy, 'run'): deploy.run, (perf, 'run'): perf.run, (perf, 'rss_mb'): perf.rss_mb, (perf, 'busy'): perf.busy, (builtins, 'open'): builtins.open}


def restore():
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)
    os.environ.pop('CODERUNNER_PROFILE', None)
    os.environ.pop('INVOCATION_ID', None)
    path = os.path.join(DATA, 'settings.json')
    if os.path.exists(path):
        os.remove(path)
    marker = os.path.join(DATA, 'limits-applied')
    if os.path.exists(marker):
        os.remove(marker)
    perf._cache.clear()
    perf.slots.resize(perf.PROFILES['light']['threads'])
    for k in perf.STATS:
        perf.STATS[k] = 0
    deploy.JOBS.clear()
    migrator.RUNS.clear()


def raises(fn, *args, text='', kind=ValueError):
    try:
        fn(*args)
    except kind as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


# ------------------------------------------------------------------ profiles
def test_light_is_the_default_and_every_profile_is_complete():
    assert perf.name() == 'light' and perf.get()['name'] == 'light' and perf.get()['threads'] == 8
    keys = set(perf.PROFILES['light'])
    for name, p in perf.PROFILES.items():
        assert set(p) == keys, name
    light, balanced, full = (perf.PROFILES[n] for n in ('light', 'balanced', 'full'))
    for k in ('threads', 'streams', 'job_keep', 'job_lines', 'run_keep', 'workers'):
        assert light[k] < balanced[k] < full[k], k                                                # each step up really allows more
    assert light['status_ttl'] > balanced['status_ttl'] > full['status_ttl'] and light['ui_poll'] > balanced['ui_poll'] > full['ui_poll']
    assert light['restart_mb'] < balanced['restart_mb'] and full['restart_mb'] == 0 and full['soft_mb'] == 0 and full['trim_every'] == 0


def test_choosing_a_profile_applies_at_once_and_is_validated():
    out = perf.set_profile('full')
    assert out['name'] == 'full' and perf.name() == 'full' and perf.slots.limit == 64
    assert settings.get('performance') == {'profile': 'full'}
    perf._cache['k'] = {'at': time.time(), 'value': 1}
    perf.set_profile('light')
    assert perf.slots.limit == 8 and not perf._cache                                                  # the cache is dropped with the profile
    raises(perf.set_profile, 'turbo', text='Choose one of')
    raises(perf.set_profile, None, text='Choose one of')
    assert perf.name() == 'light'
    os.environ['CODERUNNER_PROFILE'] = 'balanced'
    assert perf.name() == 'balanced'                                                                  # the server setting wins over the saved one
    os.environ['CODERUNNER_PROFILE'] = 'nonsense'
    assert perf.name() == 'light'
    settings.put('performance', {'profile': 'bogus'})
    del os.environ['CODERUNNER_PROFILE']
    assert perf.name() == 'light'


# --------------------------------------------------------------------- slots
def test_slots_limit_wait_refuse_and_resize():
    s = perf.Slots(2)
    assert s.acquire(0) and s.acquire(0) and s.busy == 2
    t0 = time.time()
    assert s.acquire(timeout=0.15) is False and 0.1 < time.time() - t0 < 1.0 and perf.STATS['refused'] == 1        # waits a moment, then refuses
    s.release()
    assert s.acquire(0) and s.busy == 2
    got = []
    waiter = threading.Thread(target=lambda: got.append(s.acquire(timeout=3)))
    waiter.start()
    time.sleep(0.1)
    s.release()                                                                                       # a waiting request is served as soon as a slot frees
    waiter.join(3)
    assert got == [True]
    s.resize(5)
    assert s.acquire(0) and s.acquire(0) and s.limit == 5
    s.resize(0)
    assert s.limit == 1                                                                               # never below one
    for _ in range(10):
        s.release()
    assert s.busy == 0                                                                                # release cannot go negative


# --------------------------------------------------------------------- cache
def test_cache_reuses_results_until_they_expire():
    calls = []
    fn = lambda: calls.append(1) or len(calls)  # noqa: E731
    assert perf.cached('a', fn, ttl=60) == 1 and perf.cached('a', fn, ttl=60) == 1 and len(calls) == 1
    assert perf.STATS['hits'] == 1 and perf.STATS['misses'] == 1
    assert perf.cached('a', fn, ttl=0) == 2                                                           # expired: computed again
    assert perf.cached('b', fn, ttl=60) == 3                                                          # another key is independent
    assert perf.cached(('status', 'h', 'u'), fn) == 4 and perf.cached(('status', 'h', 'u'), fn) == 4   # default time comes from the profile


def test_cache_invalidation():
    perf.cached(('status', 'h', 'u'), lambda: 1, ttl=60)
    perf.cached('other', lambda: 2, ttl=60)
    perf.invalidate('status')
    assert ('status', 'h', 'u') not in perf._cache and 'other' in perf._cache                         # only that family is dropped
    perf.invalidate('other')
    assert 'other' not in perf._cache
    perf.cached('x', lambda: 3, ttl=60)
    perf.cached(('y', 1), lambda: 4, ttl=60)
    perf.invalidate()
    assert not perf._cache


def test_concurrent_callers_share_one_computation():
    started, release, calls = threading.Event(), threading.Event(), []

    def slow():
        calls.append(1)
        started.set()
        release.wait(5)
        return 'value'
    results = []
    threads = [threading.Thread(target=lambda: results.append(perf.cached('slow', slow, ttl=60))) for _ in range(6)]
    threads[0].start()
    started.wait(5)
    for t in threads[1:]:
        t.start()
    time.sleep(0.2)
    perf.invalidate('slow')                                                                           # a computation in progress is not thrown away
    release.set()
    for t in threads:
        t.join(5)
    assert results == ['value'] * 6 and len(calls) == 1


def test_failures_are_not_cached_and_waiters_recover():
    def boom():
        raise RuntimeError('server unreachable')
    raises(perf.cached, 'f', boom, 60, text='server unreachable', kind=RuntimeError)
    assert 'f' not in perf._cache and perf.cached('f', lambda: 'ok', ttl=60) == 'ok'
    started, release = threading.Event(), threading.Event()

    def failing():
        started.set()
        release.wait(5)
        raise RuntimeError('first caller failed')
    errors, results = [], []

    def first():
        try:
            perf.cached('g', failing, ttl=60)
        except RuntimeError as e:
            errors.append(str(e))
    a = threading.Thread(target=first)
    a.start()
    started.wait(5)
    b = threading.Thread(target=lambda: results.append(perf.cached('g', lambda: 'second try', ttl=60)))
    b.start()
    time.sleep(0.2)
    release.set()
    a.join(5)
    b.join(5)
    assert errors == ['first caller failed'] and results == ['second try']                            # the waiting caller is not stuck with someone else's error


# --------------------------------------------------------------------- memory
class FakeFile:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return iter(self.text.splitlines(True))

    def __exit__(self, *a):
        return False


def test_rss_reads_linux_memory_and_tolerates_other_systems():
    real_open = builtins.open
    builtins.open = lambda p, *a, **k: FakeFile('Name:\tpython3\nVmRSS:\t   38400 kB\nThreads:\t3\n') if p == '/proc/self/status' else real_open(p, *a, **k)
    try:
        assert perf.rss_mb() == 37.5
        builtins.open = lambda p, *a, **k: FakeFile('Name:\tx\nVmRSS:\tbroken kB\n') if p == '/proc/self/status' else real_open(p, *a, **k)
        assert perf.rss_mb() is None
        builtins.open = lambda p, *a, **k: FakeFile('Name:\tx\n') if p == '/proc/self/status' else real_open(p, *a, **k)
        assert perf.rss_mb() is None
    finally:
        builtins.open = real_open
    if sys.platform != 'linux':
        assert perf.rss_mb() is None                                                                  # e.g. Windows: no figure, no crash


def test_trim_reports_what_was_released():
    sizes = iter([100.0, 80.0])
    perf.rss_mb = lambda: next(sizes)
    assert perf.trim() == 20.0 and perf.STATS['trims'] == 1
    sizes = iter([80.0, 90.0])
    perf.rss_mb = lambda: next(sizes)
    assert perf.trim() == 0.0                                                                         # never a negative number
    perf.rss_mb = lambda: None
    assert perf.trim() == 0.0


def test_busy_sees_running_work():
    assert perf.busy() is False
    job = deploy.Job()
    deploy.JOBS['j'] = job
    assert perf.busy() is True
    job.done = True
    assert perf.busy() is False
    run = migrator.Run('r')
    migrator.RUNS[run.id] = run
    assert perf.busy() is True
    run.status = 'done'
    assert perf.busy() is False
    assert perf.streams.acquire(0) and perf.busy() is True
    perf.streams.release()
    assert perf.busy() is False


def test_housekeeping_prunes_trims_and_restarts_only_when_idle():
    perf.set_profile('light')
    for i in range(20):
        j = deploy.Job()
        j.done, j.started = True, i
        deploy.JOBS['job%d' % i] = j
    running = deploy.Job()
    deploy.JOBS['running'] = running
    trims = []
    perf.trim = lambda: trims.append(1) or 5.0
    restarted = []
    assert perf.housekeeping(rss=lambda: 50.0, is_busy=lambda: False, restart=lambda: restarted.append(1)) == 'ok'
    assert len(deploy.JOBS) == perf.PROFILES['light']['job_keep'] + 1 and 'running' in deploy.JOBS and 'job19' in deploy.JOBS and 'job0' not in deploy.JOBS   # newest kept
    assert not trims and not restarted
    sizes = iter([120.0, 90.0])
    assert perf.housekeeping(rss=lambda: next(sizes), is_busy=lambda: False, restart=lambda: restarted.append(1)) == 'ok' and trims == [1]    # over the soft limit: memory handed back
    assert perf.housekeeping(rss=lambda: 300.0, is_busy=lambda: True, restart=lambda: restarted.append(1)) == 'ok' and not restarted           # too big but busy: never interrupt work
    assert perf.housekeeping(rss=lambda: 300.0, is_busy=lambda: False, restart=lambda: restarted.append(1)) == 'restarting' and restarted == [1] and perf.STATS['restarts'] == 1
    assert perf.housekeeping(rss=lambda: None, is_busy=lambda: False) == 'unknown'
    perf.set_profile('full')
    assert perf.housekeeping(rss=lambda: 5000.0, is_busy=lambda: False, restart=lambda: restarted.append(1)) == 'ok' and restarted == [1]   # Full power: no watchdog


def test_start_runs_housekeeping_in_the_background():
    ran = []
    real_sleep, real_house = perf.time.sleep, perf.housekeeping
    perf.housekeeping = lambda: ran.append(1)
    waits = []

    def sleep(n):
        waits.append(n)
        if len(waits) > 1:
            raise SystemExit
    perf.time.sleep = sleep
    try:
        perf.start()
        for _ in range(100):
            if len(waits) > 1:
                break
            real_sleep(0.02)
    finally:
        perf.time.sleep, perf.housekeeping = real_sleep, real_house
    assert waits[0] == 60 and ran == [1] and perf.slots.limit == 8 and perf.streams.limit == 3


# ---------------------------------------------------------- systemd limits
def test_limits_command_never_caps_memory():
    cmd = perf.limits_command(perf.PROFILES['light'])
    assert cmd == 'sudo systemctl set-property coderunner.service TasksMax=256 CPUWeight=50 IOWeight=50'
    assert 'TasksMax=infinity' in perf.limits_command(perf.PROFILES['full']) and 'CPUWeight=200' in perf.limits_command(perf.PROFILES['full'])
    assert all('Memory' not in perf.limits_command(p) for p in perf.PROFILES.values())               # deploys and installs share the cgroup: no kernel kill


def test_limits_are_applied_once_per_profile_and_only_on_the_server():
    srv = FakeServer().when('set-property', 0, '')
    perf.run = srv.run
    assert perf.apply_limits({'mode': 'ssh'}) is False                                                # on someone's PC: nothing to do
    assert perf.apply_limits({'mode': 'local'}) is False                                              # not started by systemd
    os.environ['INVOCATION_ID'] = 'abc'
    assert perf.apply_limits({'mode': 'local'}) is True and len(srv.calls) == 1
    assert perf.apply_limits({'mode': 'local'}) is False and len(srv.calls) == 1                      # same profile: not repeated
    perf.set_profile('balanced')
    assert perf.apply_limits({'mode': 'local'}) is True and 'CPUWeight=100' in srv.calls[-1]['cmd']
    perf.run = FakeServer().when('set-property', 1, 'denied').run
    perf.set_profile('full')
    assert perf.apply_limits({'mode': 'local'}) is False                                              # a failure is retried next time (no marker written)
    assert open(os.path.join(DATA, 'limits-applied')).read() == 'balanced'


# --------------------------------------------------------------------- stats
def test_stats_summarise_the_app():
    perf.cached('s', lambda: 1, ttl=60)
    perf.cached('s', lambda: 1, ttl=60)
    s = perf.stats()
    assert s['profile'] == 'light' and s['limit'] == 8 and s['cache_hit_rate'] == 50 and s['cache_entries'] == 1 and s['restart_at_mb'] == 256
    assert s['threads'] >= 1 and s['uptime'] >= 0 and s['jobs'] == 0 and s['runs'] == 0
    perf._cache.clear()
    perf.STATS['hits'] = perf.STATS['misses'] = 0
    assert perf.stats()['cache_hit_rate'] is None
    perf.set_profile('full')
    assert perf.stats()['restart_at_mb'] is None


# ------------------------------------------------------- bounded logs and history
def test_job_output_is_capped_but_readers_keep_their_place():
    perf.set_profile('light')
    cap = perf.PROFILES['light']['job_lines']
    job = deploy.Job()
    for i in range(cap * 3):
        job.say('line %d\r\n' % i)
    assert len(job.lines) <= cap and job.dropped + len(job.lines) == cap * 3 and job.lines[-1] == 'line %d' % (cap * 3 - 1)
    lines, nxt = job.read(0)
    assert nxt == cap * 3 and lines[0] == 'line %d' % job.dropped                                     # a reader that fell far behind skips what was dropped
    lines, nxt2 = job.read(nxt - 2)
    assert lines == ['line %d' % (cap * 3 - 2), 'line %d' % (cap * 3 - 1)] and nxt2 == nxt
    assert job.read(nxt) == ([], nxt)
    perf.set_profile('full')
    big = deploy.Job()
    for i in range(cap * 3):
        big.say('x')
    assert big.dropped == 0                                                                           # Full power keeps everything (up to its much larger limit)


def test_old_finished_jobs_and_runs_are_forgotten_but_running_ones_stay():
    for i in range(12):
        j = deploy.Job()
        j.done, j.started = True, i
        deploy.JOBS['j%d' % i] = j
    deploy.JOBS['live'] = deploy.Job()
    deploy.prune_jobs(5)
    assert sorted(deploy.JOBS) == ['j10', 'j11', 'j7', 'j8', 'j9', 'live'] or len(deploy.JOBS) == 6 and 'live' in deploy.JOBS and 'j11' in deploy.JOBS
    deploy.prune_jobs(0)
    assert list(deploy.JOBS) == ['live']
    for i in range(6):
        r = migrator.Run('r%d' % i)
        r.status, r.started = 'done', i
        migrator.RUNS[r.id] = r
    live = migrator.Run('live')
    migrator.RUNS[live.id] = live
    migrator.prune_runs(2)
    assert len(migrator.RUNS) == 3 and live.id in migrator.RUNS
    assert sorted(r.title for r in migrator.RUNS.values() if r.status == 'done') == ['r4', 'r5']


def test_migration_log_is_capped_too():
    perf.set_profile('light')
    cap = perf.PROFILES['light']['job_lines']
    run = migrator.Run('t')
    for i in range(cap * 2):
        run.say('line %d' % i)
    assert len(run.log) <= cap and run.dropped + len(run.log) == cap * 2
    v = run.view(0)
    assert v['next'] == cap * 2 and v['lines'][-1] == 'line %d' % (cap * 2 - 1) and run.view(v['next'])['lines'] == []


def test_status_is_shared_and_dropped_after_changes():
    calls = []
    deploy._status_now = lambda cfg: calls.append(1) or {'sites': [], 'n': len(calls)}
    cfg = {'host': 'h', 'user': 'u'}
    try:
        assert deploy.status(cfg)['n'] == 1 and deploy.status(cfg)['n'] == 1 and len(calls) == 1
        assert deploy.status({'host': 'other', 'user': 'u'})['n'] == 2                                 # a different server is a different entry
        deploy.site_action_invalidates = None
        perf.invalidate('status')
        assert deploy.status(cfg)['n'] == 3
        deploy.run = lambda *a, **k: (0, 'ok')
        deploy.site_action(cfg, {'name': 'demo', 'action': 'restart'})                                   # any site action drops it
        assert deploy.status(cfg)['n'] == 4
        deploy.edit_env(cfg, {'name': 'demo', 'set': {'A': '1'}})
        assert deploy.status(cfg)['n'] == 5
        job = deploy.start_job(lambda j: None)
        for _ in range(100):
            if deploy.JOBS[job].done:
                break
            time.sleep(0.02)
        time.sleep(0.05)
        assert deploy.status(cfg)['n'] == 6                                                              # and so does the end of a background job
    finally:
        pass


if __name__ == '__main__':
    run_tests(globals(), before=restore)
