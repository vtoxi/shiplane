"""API handlers for apps, server folders and uptime settings, called directly (no HTTP, no SSH).

    python tests/test_routes_apps.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fakes import run_tests  # noqa: E402
from test_routes import AUDIT, call, raises, restore as restore_routes  # noqa: E402
from coderunner import apps, monitor, server, settings  # noqa: E402

ORIGINAL = {(apps, 'status'): apps.status, (apps, 'install'): apps.install, (apps, 'list_dir'): apps.list_dir, (apps, 'make_dir'): apps.make_dir}


def restore():
    restore_routes()
    for (module, name), fn in ORIGINAL.items():
        setattr(module, name, fn)
    path = os.path.join(server.config.DATA, 'settings.json')
    if os.path.exists(path):
        os.remove(path)


def test_roles_of_the_new_endpoints():
    expect = {('GET', '/api/apps'): 'admin', ('POST', '/api/apps/install'): 'admin', ('GET', '/api/fs/list'): 'admin', ('POST', '/api/fs/mkdir'): 'admin',
              ('GET', '/api/monitor'): 'viewer', ('POST', '/api/monitor'): 'super_admin'}
    for key, role in expect.items():
        assert server.ROUTES[key]['role'] == role and not server.ROUTES[key]['public'], key


def test_apps_listing_and_install_are_audited():
    apps.status = lambda cfg: {'os': 'Ubuntu', 'apps': {}}
    out = call('GET', '/api/apps')
    assert out['status']['os'] == 'Ubuntu' and {a['id'] for a in out['catalog']} == set(apps.BY_ID) and 'file' not in out['catalog'][0]
    apps.install = lambda cfg, body: ('job-9', 'Node.js 22')
    assert call('POST', '/api/apps/install', {'id': 'node', 'version': '22'}) == {'job': 'job-9'}
    assert AUDIT[-1] == ('app-install', 'node', 'Node.js 22', True)
    apps.install = lambda cfg, body: (_ for _ in ()).throw(ValueError('Node.js 22 is already installed'))
    raises(lambda: call('POST', '/api/apps/install', {'id': 'node'}), 'already installed', ValueError)
    assert len(AUDIT) == 1                                                                         # a refused install is not logged as done


def test_folder_endpoints():
    apps.list_dir = lambda cfg, path: {'path': path, 'dirs': ['a']}
    fn = server.ROUTES[('GET', '/api/fs/list')]['fn']
    assert fn({'path': ['/var/www']}, {}) == {'path': '/var/www', 'dirs': ['a']} and fn({}, {})['path'] == '/'
    apps.make_dir = lambda cfg, path: path
    assert call('POST', '/api/fs/mkdir', {'path': '/var/www/new'}) == {'path': '/var/www/new'} and AUDIT[-1] == ('mkdir', '/var/www/new', '', True)
    apps.make_dir = real = ORIGINAL[(apps, 'make_dir')]
    raises(lambda: call('POST', '/api/fs/mkdir', {'path': '/etc/x'}), 'Choose a folder under', ValueError)
    apps.make_dir = real


def test_uptime_settings_endpoints():
    out = call('GET', '/api/monitor')
    assert out['interval'] == 300 and out['enabled'] is True and out['options'] == [60, 120, 300, 600, 900, 1800, 3600]
    assert call('POST', '/api/monitor', {'interval': 900, 'enabled': False}) == {'interval': 900, 'enabled': False}
    assert AUDIT[-1] == ('settings', 'uptime checks', 'every 900 s, off', True) and call('GET', '/api/monitor')['enabled'] is False
    raises(lambda: call('POST', '/api/monitor', {'interval': 45}), 'Choose one of', ValueError)
    assert settings.get('monitor')['interval'] == 900                                              # the refused value changed nothing
    assert call('POST', '/api/monitor', {'interval': 60})['enabled'] is True                       # enabled defaults to on
    assert monitor.health()['interval'] == 60 and call('GET', '/api/health')['enabled'] is True


if __name__ == '__main__':
    run_tests(globals(), before=restore)
