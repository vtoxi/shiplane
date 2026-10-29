import json, os, re, subprocess


def sh(c):
    return subprocess.run(c, shell=True, capture_output=True, text=True, cwd='/tmp').stdout.strip()


def size(path):
    out = sh('du -sb %s 2>/dev/null' % path)
    return int(out.split()[0]) if out and out.split()[0].isdigit() else 0


fs = os.statvfs('/')
out = {'disk_total': fs.f_blocks * fs.f_frsize, 'disk_free': fs.f_bavail * fs.f_frsize, 'sites': [],
       'system': {'journal': size('/var/log/journal'), 'apt': size('/var/cache/apt'), 'docker': sh("docker system df --format '{{.Type}}|{{.Size}}|{{.Reclaimable}}' 2>/dev/null").splitlines()}}
for name in sorted(os.listdir('/srv')) if os.path.isdir('/srv') else []:
    if not re.match(r'^[a-z0-9][a-z0-9-]{0,40}$', name) or not os.path.isdir('/srv/%s/releases' % name):
        continue
    cur = os.path.basename(os.path.realpath('/srv/%s/current' % name))
    rels = [{'id': r, 'size': size('/srv/%s/releases/%s' % (name, r)), 'current': r == cur}
            for r in sorted(os.listdir('/srv/%s/releases' % name), reverse=True)]
    out['sites'].append({'name': name, 'size': size('/srv/' + name), 'releases': rels})
print(json.dumps(out))
