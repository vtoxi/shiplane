import json, os


def rd(p):
    try:
        return open(p).read()
    except OSError:
        return ''


c = [int(x) for x in rd('/proc/stat').splitlines()[0].split()[1:]]
mi = {l.split(':')[0]: int(l.split()[1]) for l in rd('/proc/meminfo').splitlines() if ':' in l}
rx = tx = 0
for l in rd('/proc/net/dev').splitlines()[2:]:
    n, v = l.split(':')
    if n.strip() != 'lo':
        f = v.split()
        rx, tx = rx + int(f[0]), tx + int(f[8])
du = os.statvfs('/')
print(json.dumps({'cpu_total': sum(c), 'cpu_idle': c[3] + c[4], 'ram_total': mi.get('MemTotal', 0) * 1024,
                  'ram_used': (mi.get('MemTotal', 0) - mi.get('MemAvailable', 0)) * 1024, 'disk_total': du.f_blocks * du.f_frsize,
                  'disk_used': (du.f_blocks - du.f_bfree) * du.f_frsize, 'net_rx': rx, 'net_tx': tx,
                  'load': float(rd('/proc/loadavg').split()[0]), 'cpus': os.cpu_count() or 1}))
