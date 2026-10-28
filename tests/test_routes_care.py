"""API handlers for server care, smart alerts and auto-heal, called directly (no HTTP, no server).

    python tests/test_routes_care.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from test_routes import AUDIT, call, raises, restore as restore_routes  # noqa: E402
from coderunner import care, gitdeploy, gitproviders, heal, perf, server, serverops, settings, watch  # noqa: E402

ORIGINAL = {(care, 'report'): care.report, (care, 'start_fix'): care.start_fix}


def restore():
    restore_routes()
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)
    path = os.path.join(server.config.DATA, 'settings.json')
    if os.path.exists(path):
        os.remove(path)


def test_roles():
    expect = {('GET', '/api/care'): 'admin', ('POST', '/api/care/fix'): 'super_admin', ('GET', '/api/watch'): 'admin', ('POST', '/api/watch'): 'super_admin',
              ('GET', '/api/heal'): 'admin', ('POST', '/api/heal'): 'super_admin'}
    for key, role in expect.items():
        assert server.ROUTES[key]['role'] == role and not server.ROUTES[key]['public'], key


def test_care_report_and_fix():
    care.report = lambda cfg: {'score': 80, 'findings': []}
    assert call('GET', '/api/care') == {'score': 80, 'findings': []}
    seen = []
    care.start_fix = lambda cfg, fix, confirm: seen.append((fix, confirm)) or 'job-5'
    assert call('POST', '/api/care/fix', {'fix': 'fail2ban', 'confirm': 'apply'}) == {'job': 'job-5'} and seen == [('fail2ban', 'apply')]
    assert AUDIT[-1] == ('care-fix', 'fail2ban', 'Install fail2ban', True)
    care.start_fix = ORIGINAL[(care, 'start_fix')]
    raises(lambda: call('POST', '/api/care/fix', {'fix': 'fail2ban'}), 'Confirm the change first', ValueError)
    assert len(AUDIT) == 1                                                                                         # a refused fix is not logged as done


def test_alert_and_heal_switches():
    out = call('GET', '/api/watch')
    assert [k['id'] for k in out['kinds']] == list(watch.KINDS) and all(k['on'] for k in out['kinds']) and out['kinds'][0]['label']
    out = call('POST', '/api/watch', {'values': {'oom': False}})
    assert {k['id']: k['on'] for k in out['kinds']}['oom'] is False and AUDIT[-1] == ('settings', 'smart alerts', 'oom=off', True)
    raises(lambda: call('POST', '/api/watch', {'values': {'bogus': True}}), 'Unknown alert', ValueError)
    out = call('GET', '/api/heal')
    assert [k['id'] for k in out['kinds']] == list(heal.KINDS)
    out = call('POST', '/api/heal', {'values': {'sites_hung': False, 'services': True}})
    assert {k['id']: k['on'] for k in out['kinds']} == {'sites_down': True, 'sites_hung': False, 'services': True}
    assert AUDIT[-1] == ('settings', 'auto-heal', 'services=on, sites_hung=off', True)
    raises(lambda: call('POST', '/api/heal', {'values': {'nonsense': 1}}), 'Unknown option', ValueError)
    assert call('POST', '/api/heal', {})['kinds'][0]['on'] is True                                                  # an empty save changes nothing
    assert settings.get('heal')['sites_hung'] is False


def test_git_setup_is_open_to_admins_but_commands_and_client_ids_stay_with_super_admins():
    for key, meta in server.ROUTES.items():
        if key[1].startswith('/api/gitprov') or key[1] in ('/api/git/create', '/api/git/save', '/api/git/key', '/api/git/disconnect'):
            assert meta['role'] == ('super_admin' if key[1] == '/api/gitprov/oauth' else 'admin') and not meta['public'], key
    old = getattr(server.ctx, 'user', None)
    try:
        server.ctx.user = {'id': 1, 'email': 'a@x', 'role': 'admin'}
        server._git_guard({'build': 'npm'}, {'type': 'static'}, None)                                    # allowed
        for bad in ({'build': 'commands'}, {'type': 'command'}):
            raises(lambda: server._git_guard(bad), 'super admin', PermissionError)
        raises(lambda: call('POST', '/api/git/create', {'app': {'type': 'command'}, 'git': {}}), 'super admin', PermissionError)
        raises(lambda: call('POST', '/api/git/save', {'build': 'commands'}), 'super admin', PermissionError)
        raises(lambda: call('POST', '/api/gitprov/connect', {'build': 'commands'}), 'super admin', PermissionError)
        gitproviders_providers = gitproviders.providers
        gitproviders.providers = lambda: [{'id': 'github', 'device': True, 'client_id': 'Iv1.abc'}]
        try:
            assert call('GET', '/api/gitprov')['providers'] == [{'id': 'github', 'device': True}]         # the id is only shown to super admins
            server.ctx.user = {'id': 2, 'email': 's@x', 'role': 'super_admin'}
            server._git_guard({'build': 'commands'}, {'type': 'command'})
            assert call('GET', '/api/gitprov')['providers'][0]['client_id'] == 'Iv1.abc'
        finally:
            gitproviders.providers = gitproviders_providers
    finally:
        server.ctx.user = old
    saved = {n: getattr(gitproviders, n) for n in ('providers', 'save_client_ids', 'device_start', 'device_poll', 'use_token', 'repos', 'branches', 'detect', 'forget', 'clone_address')}
    try:
        gitproviders.providers = lambda: [{'id': 'github', 'name': 'GitHub', 'device': True}]
        gitproviders.save_client_ids = lambda v: seen.append(v)
        gitproviders.device_start = lambda p: {'session': 's', 'p': p}
        gitproviders.device_poll = lambda sid: {'status': 'pending', 'sid': sid}
        gitproviders.use_token = lambda p, t: {'session': 's', 'user': 'octo'}
        gitproviders.repos = lambda sid: [sid]
        gitproviders.branches = lambda sid, repo: [sid, repo]
        gitproviders.detect = lambda sid, repo, br: {'sid': sid, 'repo': repo, 'br': br}
        gitproviders.forget = lambda sid: seen.append(('forget', sid))
        seen = []
        assert call('GET', '/api/gitprov')['providers'][0]['device'] and call('POST', '/api/gitprov/oauth', {'values': {'github': 'x'}})['providers']
        assert seen == [{'github': 'x'}] and AUDIT[-1][:2] == ('settings', 'git-login')
        assert call('POST', '/api/gitprov/device', {'provider': 'github'})['p'] == 'github'
        assert call('POST', '/api/gitprov/poll', {'session': 's'})['sid'] == 's'
        assert call('POST', '/api/gitprov/token', {'provider': 'github', 'token': 't' * 20})['user'] == 'octo' and AUDIT[-1][:3] == ('git-login', 'github', 'octo')
        assert call('GET', '/api/gitprov/repos', session='s') == {'repos': ['s']}
        assert call('GET', '/api/gitprov/branches', session='s', repo='a/b') == {'branches': ['s', 'a/b']}
        assert call('GET', '/api/gitprov/detect', session='s', repo='a/b', branch='dev') == {'sid': 's', 'repo': 'a/b', 'br': 'dev'}
        assert call('GET', '/api/gitprov/detect', session='s', repo='a/b')['br'] == 'main'
        assert call('POST', '/api/gitprov/forget', {'session': 's'}) == {'ok': True} and seen[-1] == ('forget', 's')
    finally:
        for n, fn in saved.items():
            setattr(gitproviders, n, fn)


def test_public_base_only_for_names_that_a_webhook_can_reach():
    for host, want in (('portal.example.com', 'https://portal.example.com'), ('Portal.Example.com', 'https://portal.example.com'), ('127.0.0.1:8765', ''), ('localhost', ''),
                       ('portal.example.com:8443', ''), ('app.localhost', ''), ('10.0.0.5', ''), ('[::1]', ''), ('nas.local', '')):
        server.ctx.headers = {'Host': host}
        assert server._public_base() == want, host


def test_creating_and_connecting_through_a_sign_in():
    server.ctx.headers = {'Host': 'portal.example.com'}
    saved = {(gitproviders, 'clone_address'): gitproviders.clone_address, (gitdeploy, 'create'): gitdeploy.create, (gitdeploy, 'provider_link'): gitdeploy.provider_link,
             (gitdeploy, 'save'): gitdeploy.save, (gitdeploy, 'public'): gitdeploy.public}
    try:
        gitproviders.clone_address = lambda sid, repo: 'git@github.com:%s.git' % repo
        links = []
        gitdeploy.provider_link = lambda cfg, name, sid, repo, base: links.append((name, sid, repo, base)) or {'key': True, 'hook': True}
        got = {}

        def create(cfg, body, user, before=None):
            got.update(body=body, linked=before('fresh') if before else None)
            return {'job': 'j', 'secret': 's', 'linked': got['linked']}
        gitdeploy.create = create
        out = call('POST', '/api/git/create', {'app': {'name': 'fresh'}, 'git': {'branch': 'main'}, 'session': 'sid', 'repo': 'acme/site'})
        assert got['body']['git']['repo'] == 'git@github.com:acme/site.git' and links == [('fresh', 'sid', 'acme/site', 'https://portal.example.com')] and out['linked']['hook']
        assert AUDIT[-1][:2] == ('git-create', 'fresh')
        call('POST', '/api/git/create', {'app': {'name': 'plain'}, 'git': {'repo': 'https://github.com/a/b'}})          # no sign-in: nothing is linked
        assert got['linked'] is None and len(links) == 1
        gitdeploy.save = lambda cfg, body: ({'branch': 'main', 'repo': body['repo']}, 'newsecret')
        gitdeploy.public = lambda name: {'connected': True}
        out = call('POST', '/api/gitprov/connect', {'name': 'web', 'session': 'sid', 'repo': 'acme/site'})
        assert out == {'connected': True, 'secret': 'newsecret', 'linked': {'key': True, 'hook': True}} and links[-1][0] == 'web' and AUDIT[-1][:2] == ('git-connect', 'web')
    finally:
        for (module, name), fn in saved.items():
            setattr(module, name, fn)


def test_service_and_container_routes_are_audited():
    seen = []
    real = serverops.service, serverops.container
    serverops.service = lambda cfg, name, action: seen.append((name, action)) or (0, 'ok\n')
    serverops.container = lambda cfg, name, action: seen.append(('c:' + name, action)) or (0, 'done')
    try:
        perf.cached('status-key', lambda: 1, ttl=60)
        for action in ('start', 'stop', 'restart', 'enable', 'disable'):
            assert call('POST', '/api/server/service', {'name': 'mariadb', 'action': action}) == {'ok': True, 'text': 'ok'}
            assert AUDIT[-1] == ('service-' + action, 'mariadb', '', True)
        n = len(AUDIT)
        call('POST', '/api/server/service', {'name': 'mariadb', 'action': 'logs'})
        call('POST', '/api/server/service', {'name': 'mariadb', 'action': 'status'})
        assert len(AUDIT) == n                                                                                       # reading is not a change
        assert call('POST', '/api/server/container', {'name': 'mssql', 'action': 'stop'})['ok'] is True and AUDIT[-1][0] == 'container-stop'
        call('POST', '/api/server/container', {'name': 'mssql', 'action': 'autostart-off'})
        assert AUDIT[-1][0] == 'container-autostart-off' and ('c:mssql', 'autostart-off') in seen
    finally:
        serverops.service, serverops.container = real
    assert server.ROUTES[('POST', '/api/server/service')]['role'] == 'admin' and server.ROUTES[('POST', '/api/server/container')]['role'] == 'admin'
    raises(lambda: call('POST', '/api/server/service', {'name': 'ssh', 'action': 'stop'}), 'unknown service', ValueError)
    raises(lambda: call('POST', '/api/server/container', {'name': 'a;b', 'action': 'stop'}), 'bad container name', ValueError)


if __name__ == '__main__':
    run_tests(globals(), before=restore)
