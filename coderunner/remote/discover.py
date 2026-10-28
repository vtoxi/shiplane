import json, os, re, shlex, subprocess


def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True, cwd='/tmp').stdout.strip()


def size(path):
    out = sh('du -sb %s 2>/dev/null' % shlex.quote(path))
    return int(out.split()[0]) if out and out.split()[0].isdigit() else 0


def kind(root):
    for marker, k in (('wp-config.php', 'wordpress'), ('artisan', 'laravel'), ('composer.json', 'php'), ('package.json', 'node')):
        if os.path.exists(os.path.join(root, marker)):
            return k
    return 'php' if sh('ls %s/*.php 2>/dev/null | head -1' % shlex.quote(root)) else 'static'


inv = {'panel': 'unknown', 'accounts': [], 'sites': [], 'databases': [], 'cron': [], 'mailboxes': 0}
if os.path.isdir('/var/cpanel/userdata'):
    inv['panel'] = 'cPanel'
    for user in sorted(os.listdir('/var/cpanel/userdata')):
        d = '/var/cpanel/userdata/' + user
        if not os.path.isdir(d) or user == 'nobody':
            continue
        inv['accounts'].append(user)
        for f in sorted(os.listdir(d)):
            if f in ('main', 'cache') or f.endswith(('_SSL', '.cache')):
                continue
            kv = dict(re.findall(r'^([A-Za-z_]+):\s*(.*)$', open(os.path.join(d, f)).read(), re.M))
            root = kv.get('documentroot', '')
            if root and kv.get('servername'):
                inv['sites'].append({'domain': kv['servername'].lower(), 'aliases': [a for a in kv.get('serveralias', '').split() if a != kv['servername']],
                                     'docroot': root, 'size': size(root), 'kind': kind(root), 'php': kv.get('phpversion', '')})
        inv['mailboxes'] += len(sh('ls -d /home/%s/mail/*/*/ 2>/dev/null' % shlex.quote(user)).splitlines())
        inv['cron'] += [l for l in sh('cat /var/spool/cron/%s 2>/dev/null' % shlex.quote(user)).splitlines() if re.match(r'^[\d*@]', l)]
elif os.path.isdir('/var/www/vhosts') and os.path.exists('/opt/psa'):
    inv['panel'] = 'Plesk'
    for dom in sorted(os.listdir('/var/www/vhosts')):
        root = '/var/www/vhosts/%s/httpdocs' % dom
        if os.path.isdir(root) and re.match(r'^[a-z0-9.-]+$', dom):
            inv['sites'].append({'domain': dom, 'aliases': [], 'docroot': root, 'size': size(root), 'kind': kind(root), 'php': ''})
if sh('command -v mysql'):
    for row in sh("mysql -N -e \"SELECT table_schema, SUM(data_length+index_length) FROM information_schema.tables WHERE table_schema NOT IN ('mysql','information_schema','performance_schema','sys') GROUP BY table_schema\" 2>/dev/null").splitlines():
        name, _, sz = row.partition('\t')
        if re.match(r'^[A-Za-z0-9_$-]+$', name):
            inv['databases'].append({'name': name, 'engine': 'mysql', 'size': int(float(sz or 0))})
if sh('command -v psql'):
    for name in sh("sudo -u postgres psql -tAc \"SELECT datname FROM pg_database WHERE NOT datistemplate AND datname<>'postgres'\" 2>/dev/null").splitlines():
        if re.match(r'^[A-Za-z0-9_$-]+$', name):
            inv['databases'].append({'name': name, 'engine': 'postgres', 'size': int(sh("sudo -u postgres psql -tAc \"SELECT pg_database_size('%s')\" 2>/dev/null" % name) or 0)})
print(json.dumps(inv))
