import glob
import http.client
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

CADDY = '/etc/caddy/sites'
META = '/etc/deploy-meta'
SRV = '/srv'
ETC = '/etc'
SYSTEMD = '/etc/systemd/system'
SERVICES = ('cloudflared', 'caddy', 'docker', 'postgresql', 'fail2ban', 'ufw')      # always listed
OPTIONAL = ('mssql-server', 'mariadb', 'mysql', 'redis-server')                     # listed when installed, like every PHP-FPM version
PHP_UNIT = re.compile(r'^(php\d+\.\d+-fpm)\.service$')
CGROUP = '/sys/fs/cgroup/system.slice'
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,40}$')
PROPS = 'Id,ActiveState,SubState,MemoryCurrent,NRestarts,ExecMainStartTimestamp'


def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True, cwd='/tmp').stdout.strip()


def rd(p):
    try:
        return open(p).read()
    except OSError:
        return ''


def unit_blocks(text):
    """`systemctl show a b c` prints one KEY=VALUE block per unit, separated by blank lines: {unit id: {key: value}}."""
    out = {}
    for block in re.split(r'\n\s*\n', text.strip()):
        kv = dict(l.split('=', 1) for l in block.splitlines() if '=' in l)
        if kv.get('Id'):
            out[kv['Id']] = kv
    return out


def local_http(host, connect=http.client.HTTPConnection):
    """HTTP status of the site as Caddy serves it on this machine (the 'Host' header picks the site); '000' when nothing answers."""
    try:
        c = connect('127.0.0.1', 80, timeout=5)
        c.request('GET', '/', headers={'Host': host})
        code = str(c.getresponse().status)
        c.close()
        return code
    except (OSError, http.client.HTTPException):
        return '000'


def os_name():
    """Pretty OS name from /etc/os-release (no subprocess: lsb_release is a slow script)."""
    for line in rd('/etc/os-release').splitlines():
        if line.startswith('PRETTY_NAME='):
            return line.split('=', 1)[1].strip().strip('"')
    return 'Linux'


JS_DEPS = (('@angular/core', 'angular'), ('next', 'next'), ('nuxt', 'nuxt'), ('nuxt3', 'nuxt'), ('@sveltejs/kit', 'svelte'), ('svelte', 'svelte'), ('astro', 'astro'), ('gatsby', 'gatsby'),
           ('@remix-run/react', 'remix'), ('@remix-run/node', 'remix'), ('@nestjs/core', 'nest'), ('@strapi/strapi', 'strapi'), ('ghost', 'ghost'), ('preact', 'preact'),
           ('react', 'react'), ('vue', 'vue'), ('express', 'express'), ('electron', 'electron'), ('vite', 'vite'))
PHP_DEPS = (('laravel/framework', 'laravel'), ('symfony/framework-bundle', 'symfony'), ('codeigniter4/framework', 'codeigniter'), ('drupal/core', 'drupal'), ('prestashop/prestashop', 'prestashop'))
HTML_MARKERS = (('ng-version', 'angular'), ('<app-root', 'angular'), ('/_next/', 'next'), ('id="__next"', 'next'), ('/_nuxt/', 'nuxt'), ('_app/immutable', 'svelte'), ('/_astro/', 'astro'),
                ('id="___gatsby"', 'gatsby'), ('id="root"', 'react'), ('id="app"', 'vue'))
PY_WORDS = (('manage.py', 'django'), ('django', 'django'), ('fastapi', 'fastapi'), ('uvicorn', 'fastapi'), ('flask', 'flask'), ('gunicorn', 'flask'))


def detect_framework(cur, meta, read=rd, exists=os.path.exists):
    """The framework a site is built with (an id with a logo), or ''. Cheap on purpose: a few small files of the current release, never a directory walk."""
    if meta.get('app') in ('wordpress', 'laravel', 'drupal', 'joomla'):
        return meta['app']                                              # installed from the Apps page
    if not cur:
        return ''
    kind = meta.get('type') or ''
    if exists(cur + '/wp-config.php') or exists(cur + '/wp-includes'):
        return 'wordpress'
    if exists(cur + '/configuration.php') and exists(cur + '/administrator'):
        return 'joomla'
    try:
        pkg = json.loads(read(cur + '/composer.json') or '{}')
        have = {**(pkg.get('require') or {}), **(pkg.get('require-dev') or {})}
    except (ValueError, AttributeError):
        have = {}
    for dep, fw in PHP_DEPS:
        if dep in have:
            return fw
    if exists(cur + '/artisan'):
        return 'laravel'
    try:
        pkg = json.loads(read(cur + '/package.json') or '{}')
        have = {**(pkg.get('dependencies') or {}), **(pkg.get('devDependencies') or {})}
    except (ValueError, AttributeError):
        have = {}
    for dep, fw in JS_DEPS:
        if dep in have:
            return fw
    if kind in ('static', 'app', 'node'):
        html = read(cur + '/index.html')[:60000]
        for marker, fw in HTML_MARKERS:
            if marker in html:
                return fw
    if kind in ('command', 'docker') or meta.get('start'):
        words = (str(meta.get('start')) + ' ' + str(meta.get('build'))).lower()
        for word, fw in PY_WORDS:
            if word in words:
                return fw
    return ''


def read_sites(workers=8, probe=local_http, shell=sh, postgres=True):
    files = [f for f in sorted(glob.glob(CADDY + '/*.caddy')) if NAME_RE.match(os.path.basename(f)[:-6])]
    services = [os.path.basename(f)[:-6] for f in files if os.path.exists('%s/%s.service' % (SYSTEMD, os.path.basename(f)[:-6]))]
    units = unit_blocks(shell('systemctl show %s -p %s --no-pager 2>/dev/null' % (' '.join(n + '.service' for n in services), PROPS))) if services else {}
    databases = set(shell('sudo -u postgres psql -tAc "select datname from pg_database" 2>/dev/null').split()) if postgres else set()
    sites = []
    for f in files:
        name = os.path.basename(f)[:-6]
        txt = rd(f)
        hosts = re.findall(r'^http://([^\s,{]+)(?:,\s*https://[^\s,{]+)?\s*\{', txt, re.M)
        try:
            meta = json.loads(rd('%s/%s.json' % (META, name)))
        except ValueError:
            meta = {}
        link = '%s/%s/current' % (SRV, name)
        cur = os.path.realpath(link) if os.path.islink(link) else ''
        rel_dir = '%s/%s/releases' % (SRV, name)
        proxy = re.search(r'reverse_proxy\s+127\.0\.0\.1:(\d+)', txt)
        has_svc = name in services
        host = re.sub(r'[^A-Za-z0-9.*-]', '', meta.get('hostname') or (hosts[0] if hosts else ''))
        s = {'name': name, 'hostname': host, 'redirects': [h for h in hosts if h != host], 'meta': meta,
             'type': meta.get('type') or ('app' if proxy else 'static'),
             'port': int(proxy.group(1)) if proxy else None,
             'current': os.path.basename(cur) if cur else None,
             'releases': sorted(os.listdir(rel_dir)) if os.path.isdir(rel_dir) else [], 'has_service': has_svc,
             'env_keys': sorted(l.split('=', 1)[0] for l in rd('%s/%s.env' % (ETC, name)).splitlines() if '=' in l)}
        if has_svc:
            kv = units.get(name + '.service', {})
            mc = kv.get('MemoryCurrent', '')
            s.update(service=kv.get('ActiveState'), sub=kv.get('SubState'), mem=int(mc) if mc.isdigit() else None,
                     restarts=kv.get('NRestarts'), since=kv.get('ExecMainStartTimestamp'))
        elif meta.get('type') == 'docker':
            s.update(docker_site(name, shell))
        s['framework'] = detect_framework(cur, meta)
        s['db'] = 'app_%s' % name.replace('-', '_') in databases
        sites.append(s)
    with ThreadPoolExecutor(max(1, min(workers, len(sites) or 1))) as ex:
        for s, code in zip(sites, ex.map(lambda x: probe(x['hostname'] or 'x'), sites)):
            s['local_http'] = code
    return sites


def docker_site(name, shell=sh, read=rd):
    """A Docker site has no systemd unit: its state and memory come from the container `shiplane-NAME`."""
    row = shell("docker inspect -f '{{.State.Status}}|{{.RestartCount}}|{{.State.StartedAt}}|{{.Id}}' shiplane-%s 2>/dev/null" % name).strip()
    state, _, rest = row.partition('|')
    count, _, rest = rest.partition('|')
    since, _, cid = rest.partition('|')
    if not state:
        return {'has_service': True, 'service': 'inactive', 'sub': 'missing', 'mem': None, 'restarts': None, 'since': None}
    mem = read('%s/docker-%s.scope/memory.current' % (CGROUP, cid.strip())).strip()
    return {'has_service': True, 'service': 'active' if state == 'running' else ('failed' if state in ('dead', 'exited') else state),
            'sub': state, 'mem': int(mem) if mem.isdigit() else None, 'restarts': count, 'since': since}


def service_names(shell=sh):
    present = set(shell("systemctl list-unit-files --type=service --no-legend 2>/dev/null | awk '{print $1}'").split())
    names = list(SERVICES) + [n for n in OPTIONAL if n + '.service' in present and not (n == 'mysql' and 'mariadb.service' in present)]     # mysql.service is just another name for MariaDB there
    return names + sorted(m.group(1) for u in present for m in [PHP_UNIT.match(u)] if m)


def container_facts(shell=sh, read=rd):
    """Every container (stopped ones too), its restart policy and, where the cgroup shows it, its memory."""
    lines = [x for x in shell("docker ps -a --format '{{.Names}}|{{.Status}}' 2>/dev/null").splitlines() if x]
    info = {}
    if lines:
        for row in shell("docker inspect -f '{{.Name}}|{{.HostConfig.RestartPolicy.Name}}|{{.Id}}' $(docker ps -aq) 2>/dev/null").splitlines():
            name, _, rest = row.partition('|')
            policy, _, cid = rest.partition('|')
            mem = read('%s/docker-%s.scope/memory.current' % (CGROUP, cid.strip())).strip()
            info[name.lstrip('/')] = {'policy': policy or 'no', 'mem': int(mem) if mem.isdigit() else None}
    return lines, info


def read_server(shell=sh):
    mi = {}
    for l in rd('/proc/meminfo').splitlines():
        if ':' in l:
            mi[l.split(':')[0]] = int(l.split()[1])
    du = os.statvfs('/')
    names = service_names(shell)
    blocks = unit_blocks(shell('systemctl show %s -p Id,ActiveState,UnitFileState,MemoryCurrent --no-pager 2>/dev/null' % ' '.join(n + '.service' for n in names)))
    services = {n: blocks.get(n + '.service', {}).get('ActiveState', 'unknown') for n in names}
    service_info = {n: {'enabled': blocks.get(n + '.service', {}).get('UnitFileState', ''), 'mem': int(blocks[n + '.service']['MemoryCurrent']) if n + '.service' in blocks and blocks[n + '.service'].get('MemoryCurrent', '').isdigit() else None} for n in names}
    containers, container_info = container_facts(shell) if services['docker'] == 'active' else ([], {})
    return {'uptime': float(rd('/proc/uptime').split()[0]), 'load': rd('/proc/loadavg').split()[:3],
            'os': os_name(), 'kernel': os.uname().release,
            'ram_total': mi.get('MemTotal', 0) * 1024, 'ram_avail': mi.get('MemAvailable', 0) * 1024,
            'swap_total': mi.get('SwapTotal', 0) * 1024, 'swap_free': mi.get('SwapFree', 0) * 1024,
            'disk_total': du.f_blocks * du.f_frsize, 'disk_free': du.f_bavail * du.f_frsize,
            'services': services, 'service_info': service_info, 'containers': containers, 'container_info': container_info}


if __name__ == '__main__':
    server = read_server()
    print(json.dumps({'server': server, 'sites': read_sites(postgres=server['services']['postgresql'] == 'active')}))
