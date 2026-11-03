set -euo pipefail
# Show or change the address this app itself is served on: the allowed hostname in /etc/coderunner.env and the first line of
# /etc/caddy/sites/coderunner.caddy. Caddy is checked first and both files are put back if it refuses. The service restarts a few seconds later,
# after the answer has been sent, so the screen that asked is not cut off.
MODE=__MODE__; NEW=__NEW__; DIRECT=__DIRECT__
ENVF=/etc/coderunner.env; SITEF=/etc/caddy/sites/coderunner.caddy
if ! { sudo test -f "$ENVF" && sudo test -f "$SITEF"; }; then
  [ "$MODE" = show ] && { echo "INSTALLED=no"; exit 0; }
  echo "this app was not installed with install_on_server.py on this server, so its address cannot be changed from here"; exit 1
fi
CUR=$(sudo grep '^CODERUNNER_ALLOW_HOSTS=' "$ENVF" | head -1 | cut -d= -f2- || true)
if [ "$MODE" = show ]; then
  echo "INSTALLED=yes"; echo "HOSTS=$CUR"
  sudo grep -q 'https://' "$SITEF" && echo "DIRECT=yes" || echo "DIRECT=no"
  exit 0
fi
HEAD=$(sudo head -1 "$SITEF")
case "$HEAD" in *'{') ;; *) echo "$SITEF does not start with a site address line: change it by hand"; exit 1;; esac
RE=$(echo "$NEW" | sed 's/\./\\./g')
for f in /etc/caddy/sites/*.caddy /etc/caddy/Caddyfile; do
  [ "$f" = "$SITEF" ] && continue
  if sudo grep -qE "(^|[ ,/])$RE([ ,{:]|$)" "$f" 2>/dev/null; then echo "$NEW is already used in $f: remove or edit that first"; exit 1; fi
done
BE=$(mktemp); BS=$(mktemp); sudo cp "$ENVF" "$BE"; sudo cp "$SITEF" "$BS"
if [ "$DIRECT" = true ]; then LINE="http://$NEW, https://$NEW {"; else LINE="http://$NEW {"; fi
sudo sed -i "1s|.*|$LINE|" "$SITEF"
if sudo grep -q '^CODERUNNER_ALLOW_HOSTS=' "$ENVF"; then sudo sed -i "s|^CODERUNNER_ALLOW_HOSTS=.*|CODERUNNER_ALLOW_HOSTS=$NEW|" "$ENVF"; else echo "CODERUNNER_ALLOW_HOSTS=$NEW" | sudo tee -a "$ENVF" >/dev/null; fi
if ! CHECK=$(sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1); then
  sudo cp "$BE" "$ENVF"; sudo cp "$BS" "$SITEF"; rm -f "$BE" "$BS"
  echo "Caddy rejected the change, nothing was changed. Caddy says:"; echo "$CHECK" | tail -n 5; exit 1
fi
rm -f "$BE" "$BS"
sudo systemctl reload caddy
# restart after this answer is out: the app may be the very process that asked
sudo systemd-run --quiet --on-active=3 --unit=coderunner-readdress systemctl restart coderunner 2>/dev/null \
  || { sudo nohup sh -c 'sleep 3; systemctl restart coderunner' >/dev/null 2>&1 & }
echo "Now served at $NEW (was ${CUR:-not set}). The app restarts in a few seconds."
