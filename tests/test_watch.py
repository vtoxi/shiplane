"""Smart alerts: the rules at, below and above their limits, repeat control, switches, and the size of the state file (no server needed).

    python tests/test_watch.py
"""
import json
import os
import sys
import tempfile

DATA = tempfile.mkdtemp()
os.environ['CODERUNNER_DATA'] = DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import activity, config, deploy, settings, watch  # noqa: E402

DAY = 86400
NOW = 1_800_000_000
CFG = {'mode': 'local'}
ORIGINAL = {(watch.maintenance, 'domain_health'): watch.maintenance.domain_health, (watch.activity, 'log'): activity.log, (watch.deploy, 'status'): deploy.status}
LOGGED = []


def restore():
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)
    activity.log = lambda *a, **k: LOGGED.append(a)
    LOGGED.clear()
    watch.maintenance.domain_health = lambda cfg: []                  # never reach a real server or this machine from a test
    deploy.status = lambda cfg: {'sites': []}
    for f in ('care.json', 'settings.json'):
        path = os.path.join(DATA, f)
        if os.path.exists(path):
            os.remove(path)


def raises(fn, *args, text='', kind=ValueError):
    try:
        fn(*args)
    except kind as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


def metrics(used_pct=40, load=0.2, cpus=2):
    total = 100 * 10 ** 9
    return {'disk_used': total * used_pct // 100, 'disk_total': total, 'load': load, 'cpus': cpus}


class Out:
    def __init__(self):
        self.sent = []

    def __call__(self, text):
        self.sent.append(text)


def no_runner(cfg, cmd, timeout=0):
    return 0, 'OOM=0\nREBOOT=0\n'


def tick(m=None, now=NOW, out=None, status=None, runner=no_runner):
    out = out if out is not None else Out()
    sent = watch.tick(CFG, m or metrics(), now=now, send=out, status=status if status is not None else {'sites': []}, runner=runner)
    return sent, out.sent


# ------------------------------------------------------------------ settings
def test_all_alerts_are_on_until_switched_off():
    assert watch.enabled() == {k: True for k in watch.KINDS} and len(watch.KINDS) == 6
    out = watch.set_enabled({'oom': False, 'reboot': 0})
    assert out['oom'] is False and out['reboot'] is False and out['disk_forecast'] is True and watch.enabled() == out
    assert watch.set_enabled({'oom': True})['oom'] is True and watch.set_enabled({})['reboot'] is False
    raises(watch.set_enabled, {'nonsense': True}, text='Unknown alert: nonsense')
    raises(watch.set_enabled, {'oom': False, 'x': 1, 'y': 2}, text='x, y')
    assert watch.enabled()['oom'] is True                                                                      # a refused change saved nothing


# --------------------------------------------------------------- the forecast
def grow(per_day_gb, days, start_gb=20, step=6 * 3600):
    return [[NOW - int(days * DAY) + i * step, int((start_gb + per_day_gb * (i * step / DAY)) * 10 ** 9)] for i in range(int(days * DAY / step) + 1)]


def test_forecast_follows_the_trend():
    samples = grow(2, 10)                                                                                         # +2 GB a day from 20 GB
    used_gb = samples[-1][1] / 1e9
    left = watch.forecast(samples, 100 * 10 ** 9, NOW)
    assert abs(left - (97 - used_gb) / 2) < 0.2                                                                  # to 97% of 100 GB at 2 GB/day
    assert watch.forecast(grow(0, 10), 100 * 10 ** 9, NOW) is None                                              # flat
    assert watch.forecast(grow(-1, 10), 100 * 10 ** 9, NOW) is None                                             # shrinking
    assert watch.forecast(grow(2, 1.9), 100 * 10 ** 9, NOW) is None                                             # under two days of history: no guess
    assert watch.forecast(grow(2, 10)[:7], 100 * 10 ** 9, NOW) is None                                          # fewer than 8 samples
    assert watch.forecast([], 100, NOW) is None
    assert watch.forecast([[NOW, 5]] * 10, 100, NOW) is None                                                     # all at one instant: no slope
    assert watch.forecast(grow(5, 10, start_gb=95), 100 * 10 ** 9, NOW) == 0.0                                  # already past the limit
    old = [[t - 400 * DAY, u] for t, u in grow(2, 10)]
    assert watch.forecast(old, 100 * 10 ** 9, NOW) is None                                                       # samples older than the window are ignored


# ------------------------------------------------------------- repeat control
def test_a_condition_is_reported_once_then_daily_then_cleared():
    state, out = {}, Out()
    s = lambda active, now: watch._signal(state, 'k', active, 'BAD', 'GOOD', now, out)  # noqa: E731
    assert s(False, NOW) == '' and out.sent == []                                                                # nothing wrong, nothing said
    assert s(True, NOW) == 'BAD' and s(True, NOW + 3600) == '' and s(True, NOW + DAY - 1) == ''                 # once, not every round
    assert s(True, NOW + DAY) == 'BAD'                                                                           # a reminder after a day
    assert s(False, NOW + DAY + 60) == 'GOOD' and s(False, NOW + DAY + 120) == ''                               # the all-clear, once
    assert out.sent == ['BAD', 'BAD', 'GOOD'] and [a[0] for a in LOGGED] == ['alert'] * 3 and LOGGED[-1][4] is True and LOGGED[0][4] is False


# ----------------------------------------------------------------- disk rule
def feed(state_days, per_day, now0=NOW - 20 * DAY, used_pct_end=None):
    """Run the disk rule once every 6 hours along a straight-line history; returns every message sent."""
    if os.path.exists(watch._file()):
        os.remove(watch._file())                                                                                  # every history starts from nothing
    out, t = Out(), now0
    for i in range(int(state_days * 4)):
        t = now0 + i * 6 * 3600
        used = 20 + per_day * (i / 4)
        watch.tick(CFG, {'disk_used': int(used * 10 ** 9), 'disk_total': 100 * 10 ** 9, 'load': 0, 'cpus': 1}, now=t, send=out, status={'sites': []}, runner=no_runner)
    return out.sent


def test_disk_alert_needs_a_trend_and_a_short_horizon():
    assert feed(10, 0.5) == []                                                                                    # 0.5 GB/day on 100 GB: months left
    msgs = feed(10, 6)                                                                                            # 6 GB/day: 20 -> 80 GB, about 2.8 days to go
    assert 1 <= len(msgs) <= 5 and all('disk will be full in about' in m and 'day(s)' in m for m in msgs)           # first when under a week is left, then a reminder a day
    assert len(feed(3, 6)) == 0                                                                                    # three days of history at 6 GB/day: still weeks from full
    assert feed(1.5, 6) == []                                                                                     # under two days of history


def test_disk_alert_stays_quiet_when_the_resource_alert_already_speaks():
    assert feed(12, 6.5)[0:0] == [] and all('full in about' not in m for m in feed(14, 6) if '85' in m)
    out = Out()
    state = {'disk': grow(6, 10)}
    m = metrics(used_pct=90)
    assert watch._disk(state, m, NOW, True, out) == '' and out.sent == []                                       # 90% is the resource alert's job


def test_disk_samples_are_spaced_trimmed_and_recovery_is_announced():
    state, out = {}, Out()
    watch._disk(state, metrics(40), NOW, True, out)
    watch._disk(state, metrics(41), NOW + 3600, True, out)
    assert len(state['disk']) == 1                                                                               # at most one sample per 6 hours
    watch._disk(state, metrics(41), NOW + 6 * 3600, True, out)
    assert len(state['disk']) == 2
    state['disk'] = [[NOW - i * 6 * 3600, i] for i in range(300, 0, -1)]
    watch._disk(state, metrics(40), NOW + 6 * 3600, True, out)
    assert len(state['disk']) == watch.SAMPLES
    state = {'disk': grow(6, 10), 'alerts': {'disk_forecast': {'active': True, 'at': NOW - 100}}}
    assert 'back to a safe pace' in watch._disk(state, metrics(40, 0), NOW, False, out)                         # switched off while active: handled in tick; here: off means no forecast


def test_switching_a_kind_off_clears_its_state_without_an_all_clear():
    watch.set_enabled({'disk_forecast': False})
    state = {'alerts': {'disk_forecast': {'active': True, 'at': NOW}, 'crash_loop:api': {'active': True, 'at': NOW}}}
    watch._save(state)
    sent, said = tick()
    assert said == [] and sent == []
    kept = watch._load()['alerts']
    assert 'disk_forecast' not in kept and 'crash_loop:api' in kept                                                  # the muted kind is forgotten without an all-clear; the others keep their state


# ----------------------------------------------------------------- load rule
def test_load_alert_needs_several_rounds_in_a_row():
    out = Out()
    state = {}
    heavy = metrics(load=5, cpus=2)                                                                             # 2.5 per CPU
    assert watch._load_rule(state, heavy, NOW, True, out) == '' and watch._load_rule(state, heavy, NOW + 60, True, out) == ''
    assert 'overloaded (load 5.0 on 2 CPUs)' in watch._load_rule(state, heavy, NOW + 120, True, out)
    assert watch._load_rule(state, metrics(load=3, cpus=2), NOW + 180, True, out) == '' and state['load_streak'] == 3                   # between 1 and 2 per CPU: neither worse nor better
    assert 'back to normal' in watch._load_rule(state, metrics(load=0.5, cpus=2), NOW + 240, True, out)
    assert state['load_streak'] == 0
    state = {}
    for i in range(2):
        watch._load_rule(state, heavy, NOW + i, True, out)
    watch._load_rule(state, metrics(load=0.1), NOW + 9, True, out)                                               # a calm round resets the count
    assert watch._load_rule(state, heavy, NOW + 10, True, out) == ''
    one = {'load': 9, 'disk_used': 0, 'disk_total': 1}                                                           # no cpu count: assume one
    state = {}
    for i in range(3):
        text = watch._load_rule(state, one, NOW + i, True, out)
    assert '1 CPU)' in text
    assert watch._load_rule({'load_streak': 9}, heavy, NOW, False, out) == ''                                   # off: never alerts


# ------------------------------------------------------------ crash-loop rule
def site(name, restarts, service=True):
    return {'name': name, 'restarts': restarts, 'has_service': service}


def test_crash_loop_detection():
    state, out = {}, Out()
    assert watch._crash(state, {'sites': [site('api', '2')]}, NOW, True, out) == ''                              # first look: only a baseline
    assert watch._crash(state, {'sites': [site('api', '4')]}, NOW + 60, True, out) == ''                         # 2 restarts: below the limit
    assert 'api keeps crashing: it restarted 3 times' in watch._crash(state, {'sites': [site('api', '7')]}, NOW + 120, True, out)
    assert watch._crash(state, {'sites': [site('api', '7')]}, NOW + 180, True, out).startswith('[Shiplane] api is stable again')
    watch._crash(state, {'sites': [site('api', '0')]}, NOW + 240, True, out)                                      # a manual restart resets the counter: not a crash
    assert out.sent[-1].endswith('is stable again') or len(out.sent) == 2
    assert watch._crash({}, {'sites': [site('s', None), site('t', 'x'), site('u', '')]}, NOW, True, out) == ''  # unreadable counters are skipped
    state = {}
    watch._crash(state, {'sites': [site('static', '0', service=False)]}, NOW, True, out)
    assert watch._crash(state, {'sites': [site('static', '9', service=False)]}, NOW + 60, True, out) == ''       # no service, nothing to crash
    state = {'restarts': {'old': 4, 'api': 1}}
    watch._crash(state, {'sites': [site('api', '1')]}, NOW, True, out)
    assert 'old' not in state['restarts']                                                                         # deleted sites are forgotten
    assert watch._crash({'restarts': {'api': 0}}, {'sites': [site('api', '9')]}, NOW, False, out) == ''          # off


def test_two_sites_crashing_are_reported_separately():
    state, out = {'restarts': {'a': 0, 'b': 0}}, Out()
    watch._crash(state, {'sites': [site('a', '5'), site('b', '4')]}, NOW, True, out)
    assert len(out.sent) == 2 and 'a keeps crashing' in out.sent[0] and 'b keeps crashing' in out.sent[1]


# --------------------------------------------------------------- certificates
def cert(host, days, dns='1.1.1.1', err=None):
    return {'host': host, 'site': host, 'dns': dns, 'cert_days': days, 'issuer': 'x', 'tls_error': err}


def test_certificate_alerts():
    watch.maintenance.domain_health = lambda cfg: [cert('a.example.com', 30), cert('b.example.com', 13), cert('c.example.com', None, err='verify failed'), cert('d.example.com', -2),
                                                   cert('e.example.com', None, dns=None), cert('f.example.com', None)]
    out, state = Out(), {}
    text = watch._certs(state, CFG, NOW, True, out)
    assert len(out.sent) == 4 and 'b.example.com: its certificate expires in 13 day(s)' in text and 'certificate is broken (verify failed)' in text
    assert 'd.example.com: its certificate expires in -2 day(s)' in text and 'f.example.com: its certificate is broken (no certificate)' in text
    assert 'a.example.com' not in text and 'e.example.com' not in text                                           # healthy, and no DNS (a different problem)
    assert watch._certs(state, CFG, NOW + 3600, True, out) == '' and len(out.sent) == 4                          # not repeated within the day
    watch.maintenance.domain_health = lambda cfg: [cert('b.example.com', 80)]
    assert 'b.example.com: certificate is fine again' in watch._certs(state, CFG, NOW + 7200, True, out)
    watch.maintenance.domain_health = lambda cfg: 1 / 0
    assert watch._certs({}, CFG, NOW, False, out) == ''                                                           # off: not even looked at


# ----------------------------------------------------------- hourly server checks
def test_out_of_memory_and_reboot_alerts():
    state, out = {}, Out()
    on = watch.enabled()
    runner = lambda c, cmd, timeout=0: (0, 'OOM=2\nKilled process 99 (mariadbd)\nKilled process 100 (node)\nREBOOT=1\n')  # noqa: E731
    text = watch._hourly(state, CFG, NOW, on, out, runner)
    assert 'Linux stopped mariadbd, node' in text and 'needs a restart to finish security updates' in text and len(out.sent) == 2
    assert watch._hourly(state, CFG, NOW + 3600, on, out, runner) == '' and len(out.sent) == 2                   # same condition: quiet until tomorrow
    calm = lambda c, cmd, timeout=0: (0, 'OOM=0\nREBOOT=0\n')  # noqa: E731
    text = watch._hourly(state, CFG, NOW + 7200, on, out, calm)
    assert 'No memory kills in the last hour' in text and 'The restart has been done' in text
    assert watch._hourly({}, CFG, NOW, on, out, lambda c, cmd, timeout=0: (1, '')) == ''                          # the command failed: say nothing
    unnamed = lambda c, cmd, timeout=0: (0, 'OOM=1\nREBOOT=0\n')  # noqa: E731
    assert 'stopped a program' in watch._hourly({}, CFG, NOW, on, out, unnamed)
    off = dict(on, oom=False, reboot=False)
    assert watch._hourly({}, CFG, NOW, off, out, runner) == ''
    assert 'journalctl -k' in watch.HOURLY and 'reboot-required' in watch.HOURLY


# ------------------------------------------------------------------ the whole pass
def test_hourly_and_daily_work_are_rate_limited():
    calls = {'hourly': 0, 'daily': 0}

    def runner(cfg, cmd, timeout=0):
        calls['hourly'] += 1
        return 0, 'OOM=0\nREBOOT=0\n'

    def health(cfg):
        calls['daily'] += 1
        return []
    watch.maintenance.domain_health = health
    for i in range(5):                                                                                           # five rounds within a few minutes
        watch.tick(CFG, metrics(), now=NOW + i * 60, send=Out(), status={'sites': []}, runner=runner)
    assert calls == {'hourly': 1, 'daily': 1}
    watch.tick(CFG, metrics(), now=NOW + 3601, send=Out(), status={'sites': []}, runner=runner)
    assert calls == {'hourly': 2, 'daily': 1}
    watch.tick(CFG, metrics(), now=NOW + DAY + 5, send=Out(), status={'sites': []}, runner=runner)
    assert calls == {'hourly': 3, 'daily': 2}


def test_tick_uses_the_shared_status_when_none_is_given():
    watch.maintenance.domain_health = lambda cfg: []
    deploy.status = lambda cfg: {'sites': [site('api', '0')]}
    watch.tick(CFG, metrics(), now=NOW, send=Out(), runner=no_runner)
    assert watch._load()['restarts'] == {'api': 0}


def test_state_survives_restarts_and_corruption():
    watch.maintenance.domain_health = lambda cfg: []
    tick(now=NOW)
    state = watch._load()
    assert state['disk'][0][0] == NOW and state['last']['hourly'] == NOW
    open(watch._file(), 'w').write('{broken')
    assert watch._load() == {}
    open(watch._file(), 'w').write('[1, 2]')
    assert watch._load() == {}                                                                                   # valid JSON of the wrong shape
    tick(now=NOW + 10)                                                                                           # recovers by starting fresh
    assert watch._load()['disk'][0][0] == NOW + 10


def test_alerts_fire_end_to_end_and_never_twice():
    watch.maintenance.domain_health = lambda cfg: []
    runner = lambda c, cmd, timeout=0: (0, 'OOM=1\nKilled process 7 (redis-server)\nREBOOT=0\n')  # noqa: E731
    sent, said = tick(now=NOW, runner=runner)
    assert len(said) == 1 and 'redis-server' in said[0] and sent == said
    sent, said = tick(now=NOW + 60, runner=runner)
    assert said == []
    sent, said = tick(now=NOW + 3700, runner=runner)                                                             # hourly check again, still failing: no repeat inside the day
    assert said == []


def test_state_file_stays_small():
    watch.maintenance.domain_health = lambda cfg: []
    state = {'disk': [[NOW - i * 21600, 10 ** 11 + i] for i in range(watch.SAMPLES + 50)],
             'restarts': {'site%d' % i: i for i in range(60)}, 'alerts': {'crash_loop:site%d' % i: {'active': True, 'at': NOW} for i in range(60)}, 'last': {'hourly': NOW, 'daily': NOW}}
    watch._save(state)
    tick(now=NOW + 1)
    assert os.path.getsize(watch._file()) < 20 * 1024 and len(watch._load()['disk']) <= watch.SAMPLES                  # the disk budget in the plan


if __name__ == '__main__':
    run_tests(globals(), before=restore)
