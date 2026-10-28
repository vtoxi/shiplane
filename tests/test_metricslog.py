"""Metrics history: rates from counters, resets, the 24-hour cap, persistence, and the file staying small (no server needed).

    python tests/test_metricslog.py
"""
import os
import sys
import tempfile

DATA = tempfile.mkdtemp()
os.environ['CODERUNNER_DATA'] = DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import metricslog as ml, monitor, server  # noqa: E402

NOW = 1_800_000_000
ORIGINAL = {(monitor, 'check_resources'): monitor.check_resources, (monitor, '_sites'): monitor._sites, (monitor, 'check_once'): monitor.check_once}


def restore():
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)
    ml.reset()
    for f in ('metrics.json', 'settings.json', 'care.json'):
        path = os.path.join(DATA, f)
        if os.path.exists(path):
            os.remove(path)


def raises(fn, *args, text=''):
    try:
        fn(*args)
    except ValueError as e:
        assert text in str(e), (text, str(e))
        return
    raise AssertionError('expected an error containing %r' % text)


def reading(cpu_total=1000, cpu_idle=900, rx=0, tx=0, ram=40, disk=30, load=0.5):
    return {'cpu_total': cpu_total, 'cpu_idle': cpu_idle, 'ram_used': ram, 'ram_total': 100, 'disk_used': disk, 'disk_total': 100, 'net_rx': rx, 'net_tx': tx, 'load': load}


def test_the_first_reading_has_no_rates_the_second_has():
    first = ml.record(reading(), now=NOW)
    assert first == [NOW, None, 40.0, 30.0, 0.5, None, None]                                                  # memory/disk/load are instant; rates need two readings
    second = ml.record(reading(cpu_total=2000, cpu_idle=1500, rx=3_000_000, tx=600_000, ram=50), now=NOW + 300)
    assert second == [NOW + 300, 40.0, 50.0, 30.0, 0.5, 10000, 2000]                                          # 400 of 1000 ticks busy; 3 MB and 0.6 MB in 300 s
    assert ml.history('1h', now=NOW + 300) == [first, second]


def test_counters_that_go_backwards_leave_a_gap_not_a_spike():
    ml.record(reading(cpu_total=5000, cpu_idle=4000, rx=9_000_000, tx=9_000_000), now=NOW)
    after_reboot = ml.record(reading(cpu_total=100, cpu_idle=90, rx=1000, tx=2000), now=NOW + 300)
    assert after_reboot[1] is None and after_reboot[5] is None and after_reboot[6] is None
    nxt = ml.record(reading(cpu_total=1100, cpu_idle=590, rx=301000, tx=602000), now=NOW + 600)
    assert nxt[1] == 50.0 and nxt[5] == 1000 and nxt[6] == 2000                                               # it recovers on the very next reading


def test_degenerate_readings_never_crash_or_exceed_the_scale():
    ml.record(reading(), now=NOW)
    same = ml.record(reading(), now=NOW + 300)
    assert same[1] is None                                                                                     # no CPU ticks passed: unknown, not 0 or a division error
    same_instant = ml.record(reading(cpu_total=2000), now=NOW + 300)
    assert same_instant[1] is None and same_instant[5] is None                                                 # two readings at one instant
    over = ml.record(reading(cpu_total=3000, cpu_idle=700), now=NOW + 600)                                     # idle went backwards
    assert over[1] is None
    full = ml.record(reading(cpu_total=4000, cpu_idle=700), now=NOW + 900)
    assert full[1] == 100.0
    empty = dict(reading(), ram_total=0, disk_total=0)
    assert ml.record(empty, now=NOW + 1200)[2:4] == [None, None]                                               # no totals: no percentage
    no_load = dict(reading())
    del no_load['load']
    assert ml.record(no_load, now=NOW + 1500)[4] == 0.0


def test_only_a_day_is_kept_and_history_picks_the_range():
    for i in range(400):
        ml.record(reading(cpu_total=1000 + i * 100, cpu_idle=900 + i * 90), keep=288, now=NOW + i * 300)
    pts = ml.history('24h', now=NOW + 399 * 300)
    assert len(ml._load()['points']) == 288 and pts[-1][0] == NOW + 399 * 300
    assert len(ml.history('1h', now=NOW + 399 * 300)) == 13 and len(ml.history('6h', now=NOW + 399 * 300)) == 73              # inclusive of both ends
    assert ml.history('1h', now=NOW + 10 * 86400) == []                                                        # nothing recent: an empty list, not an error
    ml.record(reading(), keep=1, now=NOW + 999999)
    assert len(ml._load()['points']) == 2                                                                      # never fewer than two points: a chart needs a line
    raises(ml.history, '7d', text='Choose one of')
    raises(ml.history, '', text='Choose one of')


def test_history_survives_a_restart_and_a_damaged_file():
    ml.record(reading(), now=NOW)
    ml.record(reading(cpu_total=2000, cpu_idle=1900, rx=500, tx=500), now=NOW + 300)
    ml.reset()                                                                                                 # the app restarts: memory is gone, the file stays
    assert len(ml.history('1h', now=NOW + 300)) == 2
    third = ml.record(reading(cpu_total=3000, cpu_idle=2100, rx=1000, tx=1000), now=NOW + 600)
    assert third[1] == 80.0 and third[5] == 1                                                                  # rates continue across the restart
    for damaged in ('{broken', '[1, 2]', '{"points": "no"}', '{"points": [], "last": 5}'):
        ml.reset()
        open(ml._file(), 'w').write(damaged)
        assert ml.history('24h', now=NOW) == []
        assert ml.record(reading(), now=NOW)[1] is None                                                        # starts afresh


def test_the_file_stays_small_and_atomic():
    for i in range(300):
        ml.record(reading(cpu_total=1000 + i * 1000, cpu_idle=900 + i * 700, rx=i * 123456, tx=i * 65432, ram=30 + i % 40, disk=40 + i // 50, load=(i % 7) / 3.0), keep=288, now=NOW + i * 300)
    assert os.path.getsize(ml._file()) < 30 * 1024
    assert not [n for n in os.listdir(DATA) if n.endswith('.tmp')] and ml._file().endswith('metrics.json')


def test_the_monitor_round_feeds_the_history():
    seen = []
    monitor.check_resources = lambda cfg: reading(cpu_total=7, cpu_idle=5)
    monitor._sites = lambda cfg: {}
    monitor.check_once = lambda sites, interval=None: None
    real = ml.record
    ml.record = lambda m, keep=288, now=None: seen.append((m['cpu_total'], keep)) or real(m, keep=keep, now=now)
    try:
        assert monitor.run_round(lambda: {'mode': 'local'}) == 300
        monitor.set_schedule(60, True)
        monitor.run_round(lambda: {'mode': 'local'})
        monitor.set_schedule(3600, True)
        monitor.run_round(lambda: {'mode': 'local'})
    finally:
        ml.record = real
    assert seen == [(7, 288), (7, 1440), (7, 24)]                                                              # a day of points whatever the interval
    ml.record = lambda m, keep=288, now=None: 1 / 0
    try:
        monitor.check_resources = lambda cfg: reading()
        assert monitor.run_round(lambda: {'mode': 'local'}) == 3600                                            # a failing history never stops the round
    finally:
        ml.record = real


def test_history_endpoint():
    ml.record(reading(), now=int(__import__('time').time()) - 100)
    fn = server.ROUTES[('GET', '/api/metrics/history')]
    assert fn['role'] == 'viewer' and not fn['public']
    out = fn['fn']({}, {})
    assert out['range'] == '24h' and out['interval'] == 300 and len(out['points']) == 1 and out['points'][0][2] == 40.0
    assert fn['fn']({'range': ['1h']}, {})['range'] == '1h' and fn['fn']({'range': ['1h']}, {})['now'] > 1_700_000_000
    raises(fn['fn'], {'range': ['forever']}, {}, text='Choose one of')


if __name__ == '__main__':
    run_tests(globals(), before=restore)
