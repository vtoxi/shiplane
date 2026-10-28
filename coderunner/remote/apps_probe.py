import glob, json, os, re, subprocess


def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True, cwd='/tmp').stdout.strip()


def first(c):
    out = sh(c)
    return out.splitlines()[0] if out else ''


out = {'os': first('. /etc/os-release 2>/dev/null; echo "$PRETTY_NAME"') or 'Linux', 'apps': {}, 'instances': []}
apps = out['apps']
apps['node'] = {'installed': {os.path.basename(p)[5:]: first(p + '/bin/node -v') for p in sorted(glob.glob('/opt/runtimes/node-*'))}, 'system': first('command -v node >/dev/null && node -v')}
apps['python'] = {'installed': {re.sub(r'^python', '', os.path.basename(p)): first(p + ' --version') for p in sorted(glob.glob('/usr/local/bin/python3.*'))}, 'system': first('command -v python3 >/dev/null && python3 --version')}
apps['php'] = {'installed': {os.path.basename(p): first('php%s -v 2>/dev/null | head -1' % os.path.basename(p)) for p in sorted(glob.glob('/etc/php/*')) if os.path.isdir(p + '/fpm')}, 'system': ''}
dotnet = {}
for line in sh('/opt/runtimes/dotnet/dotnet --list-sdks 2>/dev/null').splitlines():
    m = re.match(r'^(\d+\.\d+)\.\S+', line)
    if m:
        dotnet[m.group(1)] = line.split()[0]
apps['dotnet'] = {'installed': dotnet, 'system': first('command -v dotnet >/dev/null && dotnet --version')}
for key, cmd, ver in (('mariadb', 'command -v mariadbd >/dev/null 2>&1 || command -v mysqld >/dev/null 2>&1', 'mariadbd --version 2>/dev/null || mysqld --version'),
                      ('postgres', 'command -v psql', 'psql --version'), ('redis', 'command -v redis-server', 'redis-server --version'),
                      ('composer', 'command -v composer', 'composer --version 2>/dev/null | head -1'),
                      ('docker', 'command -v docker', 'docker --version'), ('nsd', 'command -v nsd', 'nsd -v 2>&1 | head -1')):
    present = subprocess.run(cmd, shell=True, capture_output=True, cwd='/tmp').returncode == 0
    apps[key] = {'installed': {'default' if key != 'composer' else '2': first(ver)} if present else {}, 'system': ''}
for f in sorted(glob.glob('/etc/deploy-meta/*.json')):
    try:
        meta = json.load(open(f))
    except ValueError:
        continue
    if meta.get('app'):
        out['instances'].append({k: str(meta.get(k, '')) for k in ('app', 'name', 'hostname', 'path', 'version', 'php')})
print(json.dumps(out))
