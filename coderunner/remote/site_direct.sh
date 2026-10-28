set -euo pipefail
NAME=__NAME__; MODE=__MODE__
F=/etc/caddy/sites/$NAME.caddy; M=/etc/deploy-meta/$NAME.json
[ -f "$F" ] || { echo "there is no site called $NAME"; exit 1; }
sudo cp -a "$F" "$F.bak"
if [ "$MODE" = on ]; then
  sudo sed -i -E '1s#^http://([^ ,{]+) \{$#http://\1, https://\1 {#' "$F"
else
  sudo sed -i -E '1s#^http://([^ ,{]+), https://[^ ,{]+ \{$#http://\1 {#' "$F"
fi
[ -x /usr/local/sbin/shiplane-wildcard-attach ] && sudo /usr/local/sbin/shiplane-wildcard-attach || true   # use a wildcard certificate when one covers this site
if ! sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
  sudo mv -f "$F.bak" "$F"; echo "Caddy rejected the change: the site is as it was"; exit 1
fi
sudo rm -f "$F.bak"
if [ -f "$M" ]; then
  sudo MODE="$MODE" python3 - "$M" <<'PY'
import json, os, sys
p = sys.argv[1]
d = json.load(open(p))
d['direct'] = os.environ['MODE'] == 'on'
json.dump(d, open(p, 'w'), indent=1)
PY
fi
sudo systemctl reload caddy
if [ "$MODE" = on ]; then
  echo "$NAME now also answers on https:// directly (Caddy gets its own certificate once the name points at this server and ports 80 and 443 are open)"
else
  echo "$NAME is back to answering only through the tunnel"
fi
