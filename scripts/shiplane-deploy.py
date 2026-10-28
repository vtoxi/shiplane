#!/usr/bin/env python3
"""Deploy a folder to a Shiplane site from the command line or CI. Python standard library only.

    export SHIPLANE_URL=https://server.example.com
    export SHIPLANE_KEY=crk_...                      # an API key from the Users page (role: admin)
    python shiplane-deploy.py ./dist --site my-api   # redeploy an existing site with its saved settings
    python shiplane-deploy.py ./dist --site new-site --hostname new.example.com --type static   # first deploy

Zips the folder, uploads it, starts the deploy, streams the log and exits non-zero if the deploy failed
(also when it was rolled back). Flags override a site's saved settings.
"""
import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile

FIELDS = ('type', 'hostname', 'port', 'dll', 'start', 'build', 'cport', 'dockerfile', 'postgresDb', 'redirectFrom', 'healthPath', 'healthStatus', 'runtime')


def call(base, key, method, path, data=None, raw=None):
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    req = urllib.request.Request(base + path, body, method=method, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json' if raw is None else 'application/zip'})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read() or b'null')
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get('error')
        except ValueError:
            msg = ''
        sys.exit('%s %s failed (HTTP %d): %s' % (method, path, e.code, msg or e.reason))


def zip_folder(folder):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d != '.git']
            for f in files:
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, folder))
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('folder')
    ap.add_argument('--site', required=True, help='site name')
    ap.add_argument('--url', default=os.environ.get('SHIPLANE_URL'), help='Shiplane address (or SHIPLANE_URL)')
    ap.add_argument('--key', default=os.environ.get('SHIPLANE_KEY'), help='API key (or SHIPLANE_KEY, preferred: keys in argv show up in process lists)')
    for f in FIELDS:
        ap.add_argument('--' + f.lower() if f != f.lower() else '--' + f, dest=f, default=None)
    a = ap.parse_args()
    if not (a.url and a.key and os.path.isdir(a.folder)):
        sys.exit('Need SHIPLANE_URL, SHIPLANE_KEY and an existing folder')
    base = a.url.rstrip('/')
    app = {'name': a.site, 'mapCf': False}
    site = next((s for s in call(base, a.key, 'GET', '/api/status')['sites'] if s['name'] == a.site), None)
    if site:
        app.update({k: v for k, v in (site.get('meta') or {}).items() if k in FIELDS})
    app.update({k: getattr(a, k) for k in FIELDS if getattr(a, k) is not None})
    if isinstance(app.get('redirectFrom'), str):
        app['redirectFrom'] = [x.strip() for x in app['redirectFrom'].split(',') if x.strip()]
    if isinstance(app.get('postgresDb'), str):
        app['postgresDb'] = app['postgresDb'].lower() in ('1', 'true', 'yes')
    if not app.get('type') or not app.get('hostname'):
        sys.exit('This site does not exist yet: pass --type and --hostname for the first deploy')
    print('Zipping %s ...' % a.folder)
    up = call(base, a.key, 'POST', '/api/upload', raw=zip_folder(a.folder))
    app['source'] = up['path']
    job, since = call(base, a.key, 'POST', '/api/deploy', app)['job'], 0
    while True:
        r = call(base, a.key, 'GET', '/api/job?id=%s&since=%d' % (job, since))
        for line in r['lines']:
            print(line)
        since = r['next']
        if r['done']:
            sys.exit(0 if r['ok'] else 1)
        time.sleep(2)


if __name__ == '__main__':
    main()
