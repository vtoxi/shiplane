"""Auto-heal: what it restarts, what it leaves alone, the back-off, the hourly cap and respect for sites stopped on purpose (no server needed).

    python tests/test_heal.py
"""
import os
import sys
import tempfile

DATA = tempfile.mkdtemp()
os.environ['CODERUNNER_DATA'] = DATA
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from coderunner import activity, deploy, heal, watch  # noqa: E402

NOW = 1_800_000_000
CFG = {'mode': 'local'}
LOGGED = []
ORIGINAL = {(heal.activity, 'log'): activity.log, (deploy, 'run'): deploy.run}


def restore():
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)
    activity.log = lambda *a, **k: LOGGED.append(a)
    LOGGED.clear()
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


def site(name, service='active', has_service=True):
    return {'name': name, 'service': service, 'has_service': has_service}


def status(*sites, **services):
    return {'sites': list(sites), 'server': {'services': services}}


class Runner:
    """Records every command; `answers` is a list of results, the last one repeats."""

    def __init__(self, *answers):
        self.answers, self.cmds = list(answers) or [(0, 'active\n')], []

    def __call__(self, cfg, cmd, timeout=0):
        self.cmds.append(cmd)
        return self.answers[min(len(self.cmds) - 1, len(self.answers) - 1)]


def go(st, now=NOW, runner=None, up=None):
    said = []
    sent = heal.tick(CFG, status=st, up=up, now=now, runner=runner or Runner(), notify=said.append)
    assert sent == said
    return sent


# ------------------------------------------------------------------ settings
def test_options_default_on_and_validate():
    assert heal.enabled() == {'sites_down': True, 'sites_hung': True, 'services': True}
    assert heal.set_enabled({'sites_hung': False})['sites_hung'] is False and heal.enabled()['sites_down'] is True
    assert heal.set_enabled({'sites_hung': 1})['sites_hung'] is True
    raises(heal.set_enabled, {'everything': True}, text='Unknown option: everything')
    assert heal.enabled()['sites_hung'] is True


# ---------------------------------------------------------------- candidates
def test_which_sites_need_attention():
    on = heal.enabled()
    h = {}
    st = status(site('a', 'failed'), site('b', 'inactive'), site('c', 'active'), site('d', 'activating'), site('e', 'reloading'), site('f', 'failed', has_service=False),
                site('g', 'active'), site('coderunner', 'failed'), site('Bad Name', 'failed'))
    got = heal.candidates(st, h, on, {'g': False, 'c': True})
    assert got == [('site:a', 'a.service', 'down'), ('site:b', 'b.service', 'down'), ('site:g', 'g.service', 'hung')]
    assert heal.candidates(st, {'paused': ['a', 'b']}, on, {}) == []                                              # a and b were stopped on purpose
    assert heal.candidates(st, {}, dict(on, sites_down=False), {'g': False}) == [('site:g', 'g.service', 'hung')]
    assert heal.candidates(st, {}, dict(on, sites_hung=False), {'g': False}) == [('site:a', 'a.service', 'down'), ('site:b', 'b.service', 'down')]
    assert heal.candidates(status(site('x', 'active')), {}, on, {'x': None}) == []                                  # unknown answer is not "not answering"


def test_core_services_are_restarted_only_if_they_were_running_before():
    on = heal.enabled()
    h = {}
    assert heal.candidates(status(caddy='inactive', docker='inactive'), h, on, {}) == []                          # never ran here: probably not wanted
    assert heal.candidates(status(caddy='active', docker='inactive', ufw='inactive', postgresql='active'), h, on, {}) == [] and h['seen_active'] == ['caddy', 'postgresql']
    got = heal.candidates(status(caddy='failed', postgresql='activating', ufw='inactive', docker='inactive', other='failed'), h, on, {})
    assert got == [('svc:caddy', 'caddy.service', 'down')]                                                       # starting up, ufw, unknown names: left alone
    assert heal.candidates(status(caddy='failed'), h, dict(on, services=False), {}) == []
    assert heal.candidates({'sites': []}, {}, on, {}) == [] and heal.candidates({}, {}, on, {}) == []


# ------------------------------------------------------------------ acting
def test_a_dead_site_is_started_at_once_and_reported():
    r = Runner((0, 'active\n'))
    sent = go(status(site('api', 'failed')), runner=r)
    assert len(sent) == 1 and 'api was down; restarted it automatically. It is running again.' in sent[0]
    assert r.cmds == ['sudo systemctl reset-failed api.service 2>/dev/null; sudo systemctl start api.service && sleep 3; systemctl is-active api.service']
    assert LOGGED[-1][0] == 'auto-heal' and LOGGED[-1][1] == 'api' and LOGGED[-1][4] is True


def test_a_hung_site_is_restarted_not_just_started():
    r = Runner()
    sent = go(status(site('api', 'active')), runner=r, up={'api': False})
    assert 'api was not answering' in sent[0] and 'systemctl restart api.service' in r.cmds[0]


def test_a_failed_restart_is_said_honestly():
    sent = go(status(site('api', 'failed')), runner=Runner((0, 'failed\n')))
    assert 'still not running; trying again shortly' in sent[0] and LOGGED[-1][4] is False
    assert 'still not running' in go(status(site('b', 'failed')), runner=Runner((1, 'Job failed\n')))[0]
    assert 'still not running' in go(status(site('c', 'failed')), runner=Runner((0, '')))[0]                       # no answer at all counts as not running


def test_back_off_grows_and_nothing_is_said_for_every_retry():
    r = Runner((0, 'failed\n'))
    st = status(site('api', 'failed'))
    assert len(go(st, NOW, r)) == 1 and len(r.cmds) == 1
    assert go(st, NOW + 30, r) == [] and len(r.cmds) == 1                                                        # inside the first minute
    assert go(st, NOW + 61, r) == [] and len(r.cmds) == 2                                                        # second try, no new message
    assert go(st, NOW + 61 + 119, r) == [] and len(r.cmds) == 2                                                  # needs two minutes now
    go(st, NOW + 61 + 121, r)
    assert len(r.cmds) == 3
    go(st, NOW + 61 + 121 + 239, r)
    assert len(r.cmds) == 3                                                                                       # then four
    go(st, NOW + 61 + 121 + 241, r)
    assert len(r.cmds) == 4


def test_it_gives_up_after_five_tries_in_an_hour_and_says_so_once():
    r = Runner((0, 'failed\n'))
    st = status(site('api', 'failed'))
    t, sent_all = NOW, []
    for gap in (0, 61, 121, 241, 481):                                                                           # the exact waiting times
        t += gap
        sent_all += go(st, t, r)
    assert len(r.cmds) == 5
    sent_all += go(st, t + 961, r)
    assert len(r.cmds) == 5 and sum('Could not bring api back after 5 automatic tries' in m for m in sent_all) == 1
    assert go(st, t + 1500, r) == [] and go(st, t + 5000, r) == [] and len(r.cmds) == 5                          # stays quiet and stops trying
    assert any(a[0] == 'auto-heal' and 'gave up' in a[2] for a in LOGGED)
    assert 'api recovered' in go(status(site('api', 'active')), t + 6000, r)[0]                                  # and welcomes it back


def test_hung_sites_get_fewer_tries():
    r = Runner()
    st = status(site('api', 'active'))
    go(st, NOW, r, up={'api': False})
    go(st, NOW + 61, r, up={'api': False})
    sent = go(st, NOW + 200, r, up={'api': False})
    assert len(r.cmds) == 2 and 'Could not bring api back after 2 automatic tries' in sent[0]


def test_old_tries_expire_after_an_hour():
    r = Runner((0, 'failed\n'))
    st = status(site('api', 'failed'))
    for i, gap in enumerate((0, 61, 121, 241)):
        go(st, NOW + sum((0, 61, 121, 241)[:i + 1]), r)
    assert len(r.cmds) == 4
    go(st, NOW + 4000, r)                                                                                         # more than an hour later: the old tries no longer count
    assert len(r.cmds) == 5 and not [m for m in heal.watch._load()['heal']['targets'].values() if m['gave_up']]


def test_a_healthy_site_clears_its_history_without_noise():
    r = Runner((0, 'failed\n'))
    go(status(site('api', 'failed')), NOW, r)
    assert 'site:api' in watch._load()['heal']['targets']
    assert go(status(site('api', 'active')), NOW + 30, r) == []                                                   # it came back by itself: nothing to say
    assert watch._load()['heal']['targets'] == {}
    assert len(go(status(site('api', 'failed')), NOW + 40, r)) == 1                                              # next time it dies, it starts from the first try again


def test_core_services_are_restarted_too():
    r = Runner()
    go(status(caddy='active', cloudflared='active'), NOW, r)
    assert r.cmds == []
    sent = go(status(caddy='failed', cloudflared='active'), NOW + 10, r)
    assert 'caddy was down' in sent[0] and 'systemctl start caddy.service' in r.cmds[0]


# --------------------------------------------------------- respecting the user
def test_a_site_stopped_on_purpose_is_left_alone_until_started_again():
    heal.on_site_action('api', 'stop')
    heal.on_site_action('api', 'stop')
    assert watch._load()['heal']['paused'] == ['api']
    r = Runner()
    assert go(status(site('api', 'inactive')), NOW, r) == [] and r.cmds == []
    heal.on_site_action('api', 'start')
    assert watch._load()['heal']['paused'] == []
    assert len(go(status(site('api', 'inactive')), NOW + 1, r)) == 1
    for action in ('restart', 'activate', 'deploy', 'delete'):
        heal.on_site_action('api', 'stop')
        heal.on_site_action('api', action)
        assert watch._load()['heal']['paused'] == [], action
    heal.on_site_action('api', 'explode')                                                                         # anything else is not our business
    heal.on_site_action('api', 'delete')                                                                          # deleting something never paused is fine too


def test_starting_again_wipes_a_give_up():
    r = Runner((0, 'failed\n'))
    st = status(site('api', 'failed'))
    t = NOW
    for gap in (0, 61, 121, 241, 481, 961):
        t += gap
        go(st, t, r)
    assert watch._load()['heal']['targets']['site:api']['gave_up'] is True
    heal.on_site_action('api', 'restart')                                                                         # the user tried by hand
    assert 'site:api' not in watch._load()['heal']['targets']
    assert len(go(st, t + 10, r)) == 1                                                                            # so a later death is handled again


def test_the_deploy_code_tells_auto_heal_what_the_user_did():
    seen = []
    saved = deploy.ON_SITE_ACTION
    deploy.ON_SITE_ACTION = lambda name, act: seen.append((name, act))
    deploy.run = lambda cfg, cmd, inp=None, timeout=60: (0, 'ok')
    try:
        deploy.site_action(CFG, {'name': 'api', 'action': 'stop'})
        deploy.site_action(CFG, {'name': 'api', 'action': 'start'})
        deploy.site_action(CFG, {'name': 'api', 'action': 'restart'})
        raises(deploy.site_action, CFG, {'name': 'coderunner', 'action': 'stop'}, text='cannot be stopped')
        assert seen == [('api', 'stop'), ('api', 'start'), ('api', 'restart')]                                    # the app's own site is never tracked
    finally:
        deploy.ON_SITE_ACTION = saved
    assert deploy.ON_SITE_ACTION is heal.on_site_action


def test_state_survives_a_corrupt_file_and_stays_small():
    open(watch._file(), 'w').write('{broken')
    r = Runner((0, 'failed\n'))
    assert len(go(status(site('api', 'failed')), NOW, r)) == 1
    many = status(*[site('s%d' % i, 'failed') for i in range(40)])
    go(many, NOW + 1, r)
    assert os.path.getsize(watch._file()) < 20 * 1024


def test_nothing_is_done_when_everything_is_fine():
    r = Runner()
    assert go(status(site('a'), site('b'), caddy='active', docker='active'), NOW, r) == [] and r.cmds == []
    assert not os.path.exists(watch._file()) or watch._load().get('heal', {}).get('targets') == {}
    out = heal.tick(CFG, status=status(site('a')), now=NOW, runner=r, notify=lambda t: None)                      # default arguments (no up map) work
    assert out == []


if __name__ == '__main__':
    run_tests(globals(), before=restore)
