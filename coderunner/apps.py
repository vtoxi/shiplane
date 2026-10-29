"""Apps: install runtimes (Node.js, Python, PHP, .NET), services (MariaDB, PostgreSQL, Redis) and web apps (WordPress, phpMyAdmin...) on the server.

The catalog below is the single source of truth: the screen is drawn from it, and every install is rebuilt from it on the server side
(only listed apps and versions, only validated folders and names, only the fixed scripts in remote/apps/).
"""
import json
import posixpath
import re

from . import cloudflare, config, deploy
from .runner import explain_ssh, q, run, script

ROOTS = ('/var/www', '/srv', '/home')            # the only places a folder can be chosen or created
SEGMENT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$')
SECRET_RE = re.compile(r'^[A-Za-z0-9@%^*_+=.,:?!-]{12,64}$')
USER_RE = re.compile(r'^[a-z][a-z0-9_-]{2,31}$')
MAX_DEPTH = 8

PHP_VERSIONS = ['8.5', '8.4', '8.3', '8.2', '8.1']
DEFAULT_PHP = '8.3'
LARAVEL_MIN_PHP = {'12': '8.2', '11': '8.2', '10': '8.1'}


def _app(id_, name, kind, blurb, versions, default, file, tile, requires=(), needs_path=False, protect=False, note=''):
    return {'id': id_, 'name': name, 'kind': kind, 'blurb': blurb, 'versions': versions, 'default': default, 'file': file, 'tile': tile,
            'requires': list(requires), 'needs_path': needs_path, 'protect': protect, 'note': note}


CATALOG = [
    _app('node', 'Node.js', 'runtime', 'JavaScript runtime. Versions install side by side (commands node22, node20, ...).', ['24', '22', '20'], '22', 'node.sh', 'Nd'),
    _app('python', 'Python', 'runtime', 'Python with pip and venv. Versions install side by side (commands python3.12, ...).', ['3.13', '3.12', '3.11', '3.10'], '3.12', 'python.sh', 'Py'),
    _app('php', 'PHP', 'runtime', 'PHP-FPM with the usual extensions, for WordPress, Laravel and other PHP sites.', PHP_VERSIONS, '8.3', 'php.sh', 'Ph'),
    _app('dotnet', '.NET SDK', 'runtime', 'SDK and runtimes for ASP.NET Core apps. Versions install side by side.', ['10.0', '9.0', '8.0'], '8.0', 'dotnet.sh', '.N'),
    _app('mariadb', 'MariaDB', 'service', 'MySQL-compatible database server (from your OS packages).', ['default'], 'default', 'mariadb.sh', 'Mb'),
    _app('postgres', 'PostgreSQL', 'service', 'PostgreSQL database server (from your OS packages).', ['default'], 'default', 'postgres.sh', 'Pg'),
    _app('redis', 'Redis', 'service', 'In-memory cache and queue, listening on localhost only.', ['default'], 'default', 'redis.sh', 'Rd'),
    _app('docker', 'Docker', 'service', 'Runs containers. Needed for Docker sites (a Dockerfile in your repository or folder).', ['default'], 'default', 'docker.sh', 'Dk'),
    _app('nsd', 'NSD', 'service', 'A very small authoritative DNS server, for your own nameservers (DNS page). Stays stopped until you apply.', ['default'], 'default', 'nsd.sh', 'Ns'),
    _app('composer', 'Composer', 'tool', 'PHP package manager, needed for Laravel and many PHP apps.', ['2'], '2', 'composer.sh', 'Cp', requires=['php']),
    _app('wordpress', 'WordPress', 'webapp', 'The website and blog platform. Creates its own database and config.', ['latest', '6.6.2', '6.5.5', '6.4.5'], 'latest', 'wordpress.sh', 'Wp',
         requires=['php', 'mariadb'], needs_path=True),
    _app('laravel', 'Laravel', 'webapp', 'PHP framework. Creates a new project with Composer.', ['12', '11', '10'], '12', 'laravel.sh', 'Lv', requires=['php', 'composer'], needs_path=True),
    _app('phpmyadmin', 'phpMyAdmin', 'webapp', 'Web interface for MySQL/MariaDB. Always protected by a site password.', ['latest', '5.2.1', '5.1.4'], 'latest', 'phpmyadmin.sh', 'Pm',
         requires=['php', 'mariadb'], needs_path=True, protect=True),
    _app('adminer', 'Adminer', 'webapp', 'One-file database tool (MySQL, PostgreSQL...). Always protected by a site password.', ['latest', '4.8.1'], 'latest', 'adminer.sh', 'Ad',
         requires=['php'], needs_path=True, protect=True),
]
BY_ID = {a['id']: a for a in CATALOG}


def catalog():
    """The catalog as the screen needs it (no script names)."""
    return [{k: v for k, v in a.items() if k != 'file'} for a in CATALOG]


# ------------------------------------------------------------------- folders
def check_path(path, allow_root=False):
    """A normalized folder path inside the allowed places, or ValueError. Nothing outside /var/www, /srv and /home can be chosen."""
    path = str(path or '').strip()
    if not re.match(r'^/[A-Za-z0-9._/-]*$', path) or '//' in path:
        raise ValueError('The folder path looks wrong. Use letters, digits, . _ - and /')
    parts = [p for p in path.split('/') if p]
    if any(p in ('.', '..') or not SEGMENT_RE.match(p) for p in parts):
        raise ValueError('The folder path looks wrong (no .. and no unusual names)')
    norm = posixpath.normpath(path)
    top = next((r for r in ROOTS if norm == r or norm.startswith(r + '/')), None)
    if top is None:
        raise ValueError('Choose a folder under %s' % ', '.join(ROOTS))
    if norm == top and not allow_root:
        raise ValueError('Choose or create a folder inside %s, not %s itself' % (top, top))
    if len(parts) > MAX_DEPTH:
        raise ValueError('That folder is nested too deeply')
    return norm


def list_dir(cfg, path):
    """Sub-folders of a folder on the server (for the folder browser). '/' lists the allowed places."""
    if str(path or '/').strip() in ('', '/'):
        return {'path': '/', 'parent': None, 'exists': True, 'dirs': [r.lstrip('/') for r in ROOTS], 'file_count': 0, 'writable': False, 'is_top': True}
    path = check_path(path, allow_root=True)
    rc, out = run(cfg, 'sudo python3 - %s' % q(path), inp=script('fslist.py').encode(), timeout=30)
    if rc or '{' not in out:
        raise RuntimeError(out.strip()[-300:] or 'could not read the folder')
    info = json.loads(out[out.index('{'):out.rindex('}') + 1])
    info['parent'] = '/' if path in ROOTS else posixpath.dirname(path)
    info['is_top'] = False
    return info


def make_dir(cfg, path):
    """Create a folder (and parents) inside the allowed places."""
    path = check_path(path)
    rc, out = run(cfg, 'sudo mkdir -p %s && echo created' % q(path))
    if rc:
        raise RuntimeError(out.strip()[-300:] or 'could not create the folder')
    return path


# --------------------------------------------------------------------- status
def status(cfg):
    """What is installed on the server: {app id: {installed: {version: detail}, system}}, plus the web apps this tool installed."""
    rc, out = run(cfg, 'sudo python3 -', inp=script('apps_probe.py').encode(), timeout=90)
    if rc or '{' not in out:
        raise RuntimeError(explain_ssh(out, cfg)[-400:] or 'could not read the server')
    data = json.loads(out[out.index('{'):out.rindex('}') + 1])
    instances = {}
    for i in data.get('instances', []):
        instances.setdefault(i['app'], []).append(i)
    for app in CATALOG:
        entry = data['apps'].setdefault(app['id'], {'installed': {}, 'system': ''})
        if app['needs_path']:
            entry['instances'] = instances.get(app['id'], [])
    return {'os': data.get('os', ''), 'apps': data['apps']}


# ---------------------------------------------------------------- installing
def _installed(state, app_id):
    return set(state['apps'].get(app_id, {}).get('installed', {}))


def plan(req, state):
    """Validate an install request against the catalog and the server's current state. Returns the ordered list of
    (app id, version, params) to run: missing requirements first (when allowed), then the app itself. Raises ValueError otherwise."""
    app = BY_ID.get(req.get('id'))
    if not app:
        raise ValueError('Unknown app')
    version = str(req.get('version') or app['default'])
    if version not in app['versions']:
        raise ValueError('%s %s is not available here' % (app['name'], version))
    steps, params = [], {}
    php = ''
    if app['needs_path']:
        php = str(req.get('php') or DEFAULT_PHP)
        if php not in PHP_VERSIONS:
            raise ValueError('PHP %s is not available here' % php)
        if app['id'] == 'laravel' and tuple(map(int, php.split('.'))) < tuple(map(int, LARAVEL_MIN_PHP[version].split('.'))):
            raise ValueError('Laravel %s needs PHP %s or newer' % (version, LARAVEL_MIN_PHP[version]))
        host = str(req.get('hostname') or '').strip().lower()
        if not config.HOST_RE.match(host) or '.' not in host:
            raise ValueError('Type the address (domain or sub-domain) the app will be served on, for example blog.example.com')
        site = str(req.get('site') or '').strip().lower() or re.sub(r'[^a-z0-9]+', '-', host).strip('-')[:41].strip('-')
        if not config.NAME_RE.match(site) or site in deploy.PROTECTED:
            raise ValueError('The site name looks wrong (a-z, 0-9 and - only)')
        root = check_path(req.get('path'))
        user, pw = str(req.get('protect_user') or '').strip(), str(req.get('protect_password') or '')
        if app['protect'] or user or pw:
            if not USER_RE.match(user):
                raise ValueError('Site user name: 3 to 32 characters, lower-case letters, digits, - and _')
            if not SECRET_RE.match(pw):
                raise ValueError('Site password: 12 to 64 characters from letters, digits and @ %% ^ * _ + = . , : ? ! -')
        params = {'site': site, 'host': host, 'root': root, 'php': php, 'ba_user': user, 'ba_pw': pw}
        for existing in state['apps'].get(app['id'], {}).get('instances', []):
            if existing['hostname'] == host or existing['name'] == site:
                raise ValueError('%s is already installed on %s' % (app['name'], existing['hostname']))
    elif version in _installed(state, app['id']):
        raise ValueError('%s %s is already installed' % (app['name'], version))
    missing = []
    for dep in app['requires']:
        want = php if dep == 'php' and app['needs_path'] else BY_ID[dep]['default']
        have = _installed(state, dep)
        if want not in have and not (dep == 'php' and not app['needs_path'] and have):     # a tool that only needs "some PHP" is satisfied by any installed version
            missing.append((dep, want))
    if missing and not req.get('install_requirements'):
        names = ', '.join(BY_ID[d]['name'] + ('' if v == 'default' else ' ' + v) for d, v in missing)
        raise ValueError('Needs %s first. Tick "install what is missing" or install it from its own card.' % names)
    for dep, want in missing:
        steps.append((dep, want, {}))
    steps.append((app['id'], version, params))
    return steps


def render(app_id, version, params):
    """The shell script for one install step, with every value validated and quoted."""
    app = BY_ID[app_id]
    text = script('apps/' + app['file'])
    values = dict(params, version=version)
    if app['needs_path']:
        text = script('apps/_site.sh') + text
    for key, val in values.items():
        text = text.replace('__%s__' % key.upper(), q(val))
    left = re.findall(r'__[A-Z_]+__', text)
    if left:
        raise ValueError('Unfilled placeholders in %s: %s' % (app['file'], ', '.join(left)))
    return text


def build(steps):
    """One script for the whole install: stop at the first error."""
    return 'set -euo pipefail\n' + '\n'.join('(\n%s\n)' % render(a, v, p) for a, v, p in steps) + '\n'


def _install_job(job, cfg, text, hosts):
    rc = deploy.stream_script(job, cfg, text)
    job.ok = rc == 0
    if job.ok and hosts:
        job.say('Mapping in Cloudflare ...')
        try:
            for line in cloudflare.map_hosts(hosts):
                job.say('  ' + line)
        except Exception as e:  # the app is installed; mapping can be redone from the Cloudflare page
            job.say('  Cloudflare mapping FAILED: %s' % e)
    job.say('DONE' if job.ok else 'FAILED (exit code %s)' % rc)


def install(cfg, req):
    """Validate, then start the install as a background job. Returns (job id, summary for the activity log)."""
    steps = plan(req, status(cfg))
    app_id, version, params = steps[-1]
    hosts = [params['host']] if req.get('mapCf') and params.get('host') else []
    if hosts and not config.cloudflare_ready():
        raise ValueError('Cloudflare mapping is not set up on this computer')
    summary = '%s %s%s' % (BY_ID[app_id]['name'], version, ' on ' + params['host'] if params else '')
    return deploy.start_job(_install_job, cfg, build(steps), hosts), summary
