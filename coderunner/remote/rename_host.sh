set -euo pipefail
# Change a site's address: its Caddy file (header, https, redirects), its metadata and, if asked, its environment file. Caddy is checked first.
NAME=__NAME__; OLD=__OLD__; NEW=__NEW__; ENVTOO=__ENVTOO__
F=/etc/caddy/sites/$NAME.caddy; M=/etc/deploy-meta/$NAME.json; ENVF=/etc/$NAME.env
[ -f "$F" ] || { echo "there is no site called $NAME"; exit 1; }
for f in /etc/caddy/sites/*.caddy; do
  [ "$f" = "$F" ] && continue
  if grep -qE "(^|[ ,/])$(echo "$NEW" | sed 's/\./\\./g')([ ,{]|$)" "$f"; then echo "hostname $NEW is already used by site '$(basename "$f" .caddy)'"; exit 1; fi
done
sudo cp -a "$F" "$F.bak"
# the old name as a whole word only (so old.example.com does not touch xold.example.com)
sudo python3 - "$F" "$OLD" "$NEW" <<'PY'
import re, sys
p, old, new = sys.argv[1:]
s = open(p).read()
t = re.sub(r'(?<![A-Za-z0-9.-])%s(?![A-Za-z0-9-]|\.[A-Za-z0-9])' % re.escape(old), new, s)
if t == s:
    sys.exit('the old address was not found in the Caddy file')
open(p, 'w').write(t)
PY
[ -x /usr/local/sbin/shiplane-wildcard-attach ] && sudo /usr/local/sbin/shiplane-wildcard-attach || true
if ! sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
  sudo mv -f "$F.bak" "$F"; echo "Caddy rejected the change: the site is as it was"; exit 1
fi
sudo rm -f "$F.bak"
if [ -f "$M" ]; then
  sudo python3 - "$M" "$NEW" <<'PY'
import json, sys
p, new = sys.argv[1:]
d = json.load(open(p))
d['hostname'] = new
json.dump(d, open(p, 'w'), indent=1)
PY
fi
sudo systemctl reload caddy
echo "Caddy now serves $NAME at $NEW (was $OLD)"
if [ "$ENVTOO" = true ] && [ -f "$ENVF" ]; then
  n=$(sudo grep -c -F "$OLD" "$ENVF" || true)
  if [ "$n" != 0 ]; then
    sudo python3 - "$ENVF" "$OLD" "$NEW" <<'PY'
import sys
p, old, new = sys.argv[1:]
open(p, 'w').write(open(p).read().replace(old, new))
PY
    sudo systemctl restart "$NAME" 2>/dev/null || true
    echo "replaced $OLD in $n environment line(s) and restarted the app"
  else
    echo "no environment variable mentioned $OLD"
  fi
fi
