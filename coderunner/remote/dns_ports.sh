set -euo pipefail
MODE=__MODE__
if ! command -v ufw >/dev/null 2>&1 || ! sudo ufw status | grep -q "Status: active"; then echo "the firewall (ufw) is not active here: nothing to open"; exit 0; fi
case "$MODE" in
  dns) PORTS="53/tcp 53/udp";;
  web) PORTS="80/tcp 443/tcp";;
  *) echo "unknown port set"; exit 1;;
esac
for p in $PORTS; do sudo ufw allow "$p" >/dev/null; echo "opened $p"; done
