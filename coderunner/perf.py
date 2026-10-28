"""Performance profiles: how much memory, CPU and polling this app may use.

Light (the default) is built for small servers: few threads, cached status reads, short job logs and a memory watchdog.
Full power removes the limits. The profile is a setting (Settings, Performance) and can be forced with CODERUNNER_PROFILE.
Nothing here trades correctness for speed: caches are dropped the moment something changes, limits only queue or refuse extra work.
"""
import gc
import os
import threading
import time

from . import settings
from .runner import q, run

PROFILES = {
    'light': {'label': 'Light', 'about': 'Smallest footprint. Best for servers with 1-2 GB of memory. Screens refresh every 30 seconds.',
              'threads': 8, 'streams': 3, 'status_ttl': 20, 'ui_poll': 30, 'live_poll': 10, 'job_keep': 8, 'job_lines': 1500, 'run_keep': 3, 'workers': 3,
              'trim_every': 60, 'soft_mb': 96, 'restart_mb': 256, 'tasks_max': 256, 'weight': 50},
    'balanced': {'label': 'Balanced', 'about': 'More parallel work and fresher screens (every 15 seconds). A good fit for servers with 4 GB or more.',
                 'threads': 16, 'streams': 6, 'status_ttl': 10, 'ui_poll': 15, 'live_poll': 5, 'job_keep': 25, 'job_lines': 6000, 'run_keep': 8, 'workers': 6,
                 'trim_every': 120, 'soft_mb': 160, 'restart_mb': 512, 'tasks_max': 512, 'weight': 100},
    'full': {'label': 'Full power', 'about': 'No limits: many parallel requests, near-live screens (every 5 seconds), long logs, no memory watchdog. Uses more memory and CPU.',
             'threads': 64, 'streams': 20, 'status_ttl': 2, 'ui_poll': 5, 'live_poll': 2, 'job_keep': 100, 'job_lines': 50000, 'run_keep': 30, 'workers': 16,
             'trim_every': 0, 'soft_mb': 0, 'restart_mb': 0, 'tasks_max': 0, 'weight': 200},
}
DEFAULT = 'light'
STARTED = time.time()
_lock = threading.RLock()
_cache = {}                  # key -> {'at': time, 'value': value, 'event': Event while being computed}
STATS = {'hits': 0, 'misses': 0, 'refused': 0, 'trims': 0, 'restarts': 0}


# ------------------------------------------------------------------ profile
def name():
    """The active profile: CODERUNNER_PROFILE, else the saved setting, else Light."""
    for choice in (os.environ.get('CODERUNNER_PROFILE'), settings.get('performance').get('profile')):
        if choice in PROFILES:
            return choice
    return DEFAULT


def get():
    return dict(PROFILES[name()], name=name())


def set_profile(choice):
    """Save the profile and apply it at once (threads, cache, logs). Returns the profile."""
    if choice not in PROFILES:
        raise ValueError('Choose one of: %s' % ', '.join(PROFILES))
    settings.put('performance', {'profile': choice})
    invalidate()
    slots.resize(PROFILES[choice]['threads'])
    return get()


# --------------------------------------------------------- limited request slots
class Slots:
    """At most `limit` requests are served at once; extras wait briefly, then are refused (HTTP 503) instead of piling up threads."""

    def __init__(self, limit):
        self.limit, self.busy, self.cond = limit, 0, threading.Condition()

    def resize(self, limit):
        with self.cond:
            self.limit = max(1, int(limit))
            self.cond.notify_all()

    def acquire(self, timeout=2.0):
        end = time.time() + timeout
        with self.cond:
            while self.busy >= self.limit:
                left = end - time.time()
                if left <= 0:
                    STATS['refused'] += 1
                    return False
                self.cond.wait(left)
            self.busy += 1
            return True

    def release(self):
        with self.cond:
            self.busy = max(0, self.busy - 1)
            self.cond.notify()


slots = Slots(PROFILES[DEFAULT]['threads'])
streams = Slots(PROFILES[DEFAULT]['streams'])


# --------------------------------------------------------------------- cache
def cached(key, fn, ttl=None):
    """fn() at most once per `ttl` seconds (the profile's status_ttl by default). Callers arriving while it is being computed wait for
    that one result instead of starting their own. A failure is never cached."""
    ttl = get()['status_ttl'] if ttl is None else ttl
    with _lock:
        entry = _cache.get(key)
        if entry and 'event' not in entry and time.time() - entry['at'] < ttl:
            STATS['hits'] += 1
            return entry['value']
        if entry and 'event' in entry:
            waiting = entry['event']
        else:
            waiting = None
            _cache[key] = {'event': threading.Event(), 'at': 0, 'value': None}
            mine = _cache[key]
            STATS['misses'] += 1
    if waiting is not None:
        waiting.wait(60)
        with _lock:
            done = _cache.get(key)
            if done and 'event' not in done:
                return done['value']
        return fn()                                   # the first caller failed: try on our own
    try:
        value = fn()
    except BaseException:
        with _lock:
            _cache.pop(key, None)
        mine['event'].set()
        raise
    with _lock:
        _cache[key] = {'at': time.time(), 'value': value}
    mine['event'].set()
    return value


def invalidate(prefix=None):
    """Forget cached results (everything, or the keys whose first part is `prefix`). Call it after any change."""
    with _lock:
        for key in [k for k in _cache if prefix is None or (isinstance(k, tuple) and k and k[0] == prefix) or k == prefix]:
            if 'event' not in _cache[key]:
                del _cache[key]


# --------------------------------------------------------------------- memory
def rss_mb():
    """Resident memory of this process in MB (Linux), or None where it cannot be read."""
    try:
        with open('/proc/self/status', encoding='ascii') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def trim():
    """Give freed memory back to the operating system: a garbage collection plus glibc malloc_trim. Returns MB released (0 if unknown)."""
    before = rss_mb()
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except (OSError, AttributeError):
        pass
    STATS['trims'] += 1
    after = rss_mb()
    return round(max(0.0, before - after), 1) if before is not None and after is not None else 0.0


def busy():
    """True while something must not be interrupted: a deploy, install or migration is running, or a stream is open."""
    from . import deploy, migrator
    return (any(not j.done for j in list(deploy.JOBS.values())) or any(r.status == 'running' for r in list(migrator.RUNS.values())) or streams.busy > 0)


def housekeeping(rss=rss_mb, is_busy=busy, restart=None):
    """One pass: forget old jobs and runs, hand memory back when it grew, and restart cleanly if it is far too large and nothing is running."""
    from . import deploy, migrator
    p = get()
    deploy.prune_jobs(p['job_keep'])
    migrator.prune_runs(p['run_keep'])
    size = rss()
    if size is None:
        return 'unknown'
    if p['soft_mb'] and size > p['soft_mb']:
        trim()
        size = rss() or size
    if p['restart_mb'] and size > p['restart_mb'] and not is_busy():
        STATS['restarts'] += 1
        (restart or (lambda: os.kill(os.getpid(), 15)))()   # systemd starts it again within seconds; sessions and settings are on disk
        return 'restarting'
    return 'ok'


def start():
    """Background housekeeping (not in Full power) and one-time systemd limits."""
    def loop():
        while True:
            every = get()['trim_every']
            time.sleep(every if every else 300)
            if every:
                try:
                    housekeeping()
                except Exception:
                    pass
    threading.Thread(target=loop, daemon=True).start()
    slots.resize(get()['threads'])
    streams.resize(get()['streams'])


# ----------------------------------------------------------- systemd limits
def limits_command(profile):
    """The systemctl command that applies the profile's safe resource limits to the service (CPU share, task count, I/O share).
    Memory is deliberately not capped by the kernel: deploys and installs run in the same group and must never be killed halfway.
    The task limit counts threads too, so it is generous: it only stops a runaway, never a normal npm or dotnet build."""
    tasks = profile['tasks_max'] or 'infinity'
    return 'sudo systemctl set-property coderunner.service TasksMax=%s CPUWeight=%d IOWeight=%d' % (q(tasks), int(profile['weight']), int(profile['weight']))


def apply_limits(cfg):
    """Apply the active profile's limits once per profile change, when running as the systemd service on the server."""
    if cfg.get('mode') != 'local' or not os.environ.get('INVOCATION_ID'):
        return False
    from . import config
    marker = os.path.join(config.DATA, 'limits-applied')
    wanted = name()
    try:
        if open(marker, encoding='ascii').read().strip() == wanted:
            return False
    except OSError:
        pass
    rc, _ = run(cfg, limits_command(PROFILES[wanted]), timeout=30)
    if rc == 0:
        with open(marker, 'w', encoding='ascii') as f:
            f.write(wanted)
    return rc == 0


# ---------------------------------------------------------------------- stats
def stats():
    from . import deploy, migrator
    p = get()
    total = STATS['hits'] + STATS['misses']
    return {'profile': p['name'], 'rss_mb': rss_mb(), 'threads': threading.active_count(), 'busy': slots.busy, 'limit': slots.limit,
            'streams': streams.busy, 'jobs': len(deploy.JOBS), 'runs': len(migrator.RUNS), 'cache_entries': len(_cache),
            'cache_hit_rate': round(100.0 * STATS['hits'] / total) if total else None, 'refused': STATS['refused'], 'trims': STATS['trims'],
            'restart_at_mb': p['restart_mb'] or None, 'uptime': int(time.time() - STARTED)}
