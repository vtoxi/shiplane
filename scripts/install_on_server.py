#!/usr/bin/env python3
"""Install (or upgrade) Coderunner ON the server, so it can be used from anywhere with a login.

    python scripts/install_on_server.py --domain server.example.com --admin-email you@example.com

Run it from your own computer; it uses the SSH settings in server.json. Works on a fresh Debian/Ubuntu VPS from
any provider. What it sets up on the server:
  * python3, curl, Caddy and cloudflared from their official apt repositories, when missing
  * a system user "coderunner" with passwordless sudo (the app manages the server), code in /opt/coderunner
  * /etc/coderunner.env (root only): sign-in mode, secure cookies, allowed hostname, data folder
  * no provider credentials: Cloudflare, DNS providers and Git are connected per install in Settings
  * /var/lib/coderunner: accounts database, activity log, API key hashes
  * a systemd service (listens on 127.0.0.1:8765 only) and a Caddy site for the domain
Running it again upgrades the code and keeps settings, accounts and data.
"""
import argparse
import os
import shutil
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from coderunner import config  # noqa: E402
from coderunner.runner import popen, q  # noqa: E402

INCLUDE = ['app.py', 'README.md', 'coderunner', 'web']
SKIP = {'__pycache__', 'node_modules', '.git'}

INSTALL = r'''set -euo pipefail
APP=/opt/coderunner; DATA=/var/lib/coderunner; ENVF=/etc/coderunner.env
DOMAIN=__DOMAIN__; ADMIN=__ADMIN__
echo "== packages (fresh Debian/Ubuntu VPS from any provider)"
command -v apt-get >/dev/null || { echo "Needs Debian or Ubuntu (apt-get)"; exit 1; }
command -v sudo >/dev/null || { [ "$(id -u)" = 0 ] && apt-get update -qq && apt-get install -y -qq sudo >/dev/null; }
export DEBIAN_FRONTEND=noninteractive
NEED=""
for p in python3 curl gpg; do command -v $p >/dev/null || NEED="$NEED $p"; done
[ -n "$NEED" ] && { sudo apt-get update -qq; sudo apt-get install -y -qq $NEED >/dev/null; }
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -qq; sudo apt-get install -y -qq caddy >/dev/null
fi
if ! command -v cloudflared >/dev/null; then
  sudo mkdir -p --mode=0755 /usr/share/keyrings
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
  sudo apt-get update -qq; sudo apt-get install -y -qq cloudflared >/dev/null
fi
grep -q '^import sites/\*.caddy' /etc/caddy/Caddyfile || echo 'import sites/*.caddy' | sudo tee -a /etc/caddy/Caddyfile >/dev/null
echo "python3 $(python3 -V 2>&1 | cut -d' ' -f2), $(caddy version | cut -d' ' -f1), cloudflared $(cloudflared --version 2>&1 | awk '{print $3}')"
echo "== user and folders"
id coderunner >/dev/null 2>&1 || sudo useradd -r -m -d $APP -s /usr/sbin/nologin coderunner
sudo mkdir -p $APP $DATA
echo "== code"
sudo find $APP/coderunner $APP/web -mindepth 1 -delete 2>/dev/null || true
sudo tar -xzf /tmp/coderunner.tgz -C $APP
rm -f /tmp/coderunner.tgz
[ -f $APP/server.json ] || printf '{"mode":"local","host":"","user":"","key":"","domains":[]}\n' | sudo tee $APP/server.json >/dev/null
echo "== settings (/etc/coderunner.env, root only)"
if [ ! -f $ENVF ]; then
  sudo tee $ENVF >/dev/null <<ENV
CODERUNNER_AUTH=login
CODERUNNER_ADMIN_EMAIL=$ADMIN
CODERUNNER_SECURE_COOKIES=1
CODERUNNER_BEHIND_PROXY=1
CODERUNNER_ALLOW_HOSTS=$DOMAIN
CODERUNNER_DATA=$DATA
ENV
fi
sudo chmod 600 $ENVF
# providers are connected per install in Settings; an old env token moves into cloudflare.json so the screen can change it
if sudo grep -q '^CLOUDFLARE_API_TOKEN=' $ENVF; then
  sudo python3 - $ENVF $APP/cloudflare.json <<'PY'
import json, os, sys
env, cf = sys.argv[1], sys.argv[2]
tok = [l.split('=', 1)[1].strip() for l in open(env) if l.startswith('CLOUDFLARE_API_TOKEN=')][0]
try:
    c = json.load(open(cf))
except (OSError, ValueError):
    c = {}
if not c.get('api_token'):
    c['api_token'] = tok
    fd = os.open(cf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(c, f, indent=2)
keep = [l for l in open(env).readlines() if not l.startswith('CLOUDFLARE_API_TOKEN=')]   # read fully before truncating
open(env, 'w').writelines(keep)
PY
  echo "cloudflare token moved from $ENVF to the app settings (change it in Settings)"
fi
sudo chown -R coderunner:coderunner $APP $DATA
sudo chmod 700 $DATA
echo "== sudo for the service user (it manages this server)"
echo 'coderunner ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/coderunner >/dev/null
sudo chmod 440 /etc/sudoers.d/coderunner
sudo visudo -cf /etc/sudoers.d/coderunner >/dev/null
echo "== service"
sudo tee /etc/systemd/system/coderunner.service >/dev/null <<UNIT
[Unit]
Description=Coderunner (deploy and server manager)
After=network.target

[Service]
User=coderunner
WorkingDirectory=$APP
EnvironmentFile=$ENVF
Environment=PYTHONUNBUFFERED=1
Environment=MALLOC_ARENA_MAX=2
ExecStart=/usr/bin/python3 $APP/app.py --no-browser --port 8765
Restart=always
RestartSec=3
Nice=5
TasksMax=256
CPUWeight=50
IOWeight=50

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable coderunner >/dev/null 2>&1
sudo systemctl restart coderunner
echo "== web address"
sudo mkdir -p /etc/caddy/sites
sudo tee /etc/caddy/sites/coderunner.caddy >/dev/null <<SITE
http://$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:8765
}
SITE
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || { echo "Caddy config invalid"; sudo rm -f /etc/caddy/sites/coderunner.caddy; exit 1; }
sudo systemctl reload caddy
for i in $(seq 1 20); do
  OUT=$(curl -s -m 3 -H "Host: $DOMAIN" http://127.0.0.1:8765/api/auth/me 2>/dev/null || true)
  case "$OUT" in *'"auth"'*) break;; esac
  sleep 1
done
echo "app answers: $OUT"
echo "service: $(systemctl is-active coderunner)"
[ -f $DATA/first-admin-code.txt ] && echo "FIRST_CODE_PENDING=yes" || echo "FIRST_CODE_PENDING=no"
'''


def pack():
    fd, path = tempfile.mkstemp(suffix='.tgz')
    os.close(fd)
    with tarfile.open(path, 'w:gz') as t:
        for item in INCLUDE:
            full = os.path.join(ROOT, item)
            if os.path.isdir(full):
                for dirpath, dirs, files in os.walk(full):
                    dirs[:] = [d for d in dirs if d not in SKIP]
                    for f in files:
                        if not f.endswith('.pyc'):
                            p = os.path.join(dirpath, f)
                            t.add(p, arcname=os.path.relpath(p, ROOT).replace(os.sep, '/'))
            else:
                t.add(full, arcname=item)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--domain', required=True, help='hostname the app will be served on, e.g. server.example.com')
    ap.add_argument('--admin-email', required=True, help='the first super admin (they set their own password)')
    args = ap.parse_args()
    cfg = config.load_server()
    if cfg.get('mode') == 'local':
        sys.exit('Run this from your own computer (server.json must be in "ssh" mode).')
    script = INSTALL.replace('__DOMAIN__', q(args.domain)).replace('__ADMIN__', q(args.admin_email.lower()))

    print('Packing the app ...')
    tgz = pack()
    print('Uploading %.2f MB to %s ...' % (os.path.getsize(tgz) / 1e6, cfg['host']))
    p = popen(cfg, 'cat > /tmp/coderunner.tgz')
    with open(tgz, 'rb') as f:
        shutil.copyfileobj(f, p.stdin)
    p.stdin.close()
    if p.wait():
        sys.exit('upload failed: ' + p.stdout.read().decode(errors='replace'))
    os.remove(tgz)
    print('Installing ...')
    p = popen(cfg, 'bash -s')
    p.stdin.write(script.encode())
    p.stdin.close()
    for line in iter(p.stdout.readline, b''):
        print('  ' + line.decode('utf-8', 'replace').rstrip())
    if p.wait():
        sys.exit('install failed')
    print('\nDone. Open https://%s and choose "Set up your account".' % args.domain)
    print('Get your one-time activation code with:\n  ssh %s%s@%s "sudo cat /var/lib/coderunner/first-admin-code.txt"' % ('-i %s ' % cfg['key'] if cfg['key'] else '', cfg['user'], cfg['host']))


if __name__ == '__main__':
    main()
