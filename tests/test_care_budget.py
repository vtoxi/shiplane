"""The plan's budget, enforced: smart alerts and auto-heal must not grow in memory or on disk, however long they run (no server needed).

    python tests/test_care_budget.py
"""
import gc
import os
import random
import sys
import tempfile
import tracemalloc

DATA = tempfile.mkdtemp()
os.environ['CODERUNNER_DATA'] = DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import activity, deploy, heal, watch  # noqa: E402

NOW = 1_800_000_000
CFG = {'mode': 'local'}
SITES = ['site%d' % i for i in range(40)]


def restore():
    activity.log = lambda *a, **k: None
    watch.maintenance.domain_health = lambda cfg: [{'host': '%s.example.com' % s, 'site': s, 'dns': '1.1.1.1', 'cert_days': random.choice([5, 30, 90, None]), 'issuer': 'x', 'tls_error': 'e'} for s in SITES]
    deploy.status = lambda cfg: {'sites': []}
    for f in ('care.json', 'settings.json'):
        path = os.path.join(DATA, f)
        if os.path.exists(path):
            os.remove(path)


def round_(i, rnd):
    """One monitor round with a busy, unreliable server: sites that crash, hang, die and come back, a disk that fills, load spikes."""
    now = NOW + i * 300
    status = {'sites': [{'name': s, 'service': rnd.choice(['active', 'active', 'active', 'failed', 'inactive', 'activating']), 'has_service': True, 'restarts': str(rnd.randint(0, 50))} for s in SITES],
              'server': {'services': {'caddy': rnd.choice(['active', 'failed']), 'postgresql': rnd.choice(['active', 'inactive']), 'cloudflared': 'active'}}}
    metrics = {'disk_used': 20 * 10 ** 9 + i * 10 ** 6, 'disk_total': 100 * 10 ** 9, 'load': rnd.choice([0.1, 0.5, 6.0]), 'cpus': 2}
    runner = lambda c, cmd, timeout=0: (0, rnd.choice(['OOM=0\nREBOOT=0\n', 'OOM=1\nKilled process 9 (x)\nREBOOT=1\n', 'active\n']))  # noqa: E731
    watch.tick(CFG, metrics, now=now, send=lambda t: None, status=status, runner=runner)
    heal.tick(CFG, status=status, up={s: rnd.choice([True, False, None]) for s in SITES}, now=now, runner=runner, notify=lambda t: None)


TRACED = sys.gettrace() is not None or 'coverage' in sys.modules      # a coverage run traces every line: far slower, and its own bookkeeping would pollute the memory figure


def test_a_week_of_rounds_does_not_grow_memory_or_disk():
    rnd = random.Random(7)
    rounds = 150 if TRACED else 2000
    for i in range(300 if not TRACED else 100):                                                                                          # warm up: caches, regexes, state reaches its working size
        round_(i, rnd)
    gc.collect()
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    start = 300 if not TRACED else 100
    size_mid = os.path.getsize(watch._file())
    for i in range(start, start + rounds):                                                                        # about a week at one round every five minutes: 2,000 more rounds
        round_(i, rnd)
    gc.collect()
    grown = tracemalloc.get_traced_memory()[0] - before
    tracemalloc.stop()
    size_end = os.path.getsize(watch._file())
    assert TRACED or grown < 512 * 1024, 'memory grew by %d KB' % (grown // 1024)                                           # under half a megabyte, and that is allocator noise, not a leak
    assert size_end < 20 * 1024, 'state file is %d KB' % (size_end // 1024)
    assert size_end < size_mid * 1.5 + 2048                                                                       # and it has stopped growing


def test_the_state_is_the_only_file_and_it_is_rewritten_atomically():
    round_(0, random.Random(1))
    names = sorted(os.listdir(DATA))
    assert 'care.json' in names and not [n for n in names if n.endswith('.tmp')]                                  # no stray temp files, no log files, nothing else was created by care
    assert [n for n in names if n not in ('care.json', 'settings.json')] == []


if __name__ == '__main__':
    run_tests(globals(), before=restore)
