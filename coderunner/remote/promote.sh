set -euo pipefail
# Promote a staging site's live release to production: copy it into the production site's releases, switch the symlink, restart, health-check, roll back on failure.
FROM=__FROM__; TO=__TO__
meta() { python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print(m.get("type","")); print(m.get("hostname","")); print(m.get("healthPath") or "/"); print(m.get("healthStatus") or "")' "/etc/deploy-meta/$1.json"; }
{ read -r FT; read -r _; read -r _; read -r _; } < <(meta "$FROM") || { echo "$FROM is not a Shiplane site"; exit 1; }
{ read -r TT; read -r HOST; read -r HEALTH; read -r EXPECT; } < <(meta "$TO") || { echo "$TO is not a Shiplane site"; exit 1; }
[ "$FT" = "$TT" ] || { echo "$FROM is a $FT site and $TO is a $TT site: promote only works between sites of the same type"; exit 1; }
case "$TT" in docker) echo "Docker sites cannot be promoted this way"; exit 1;; esac
SRC=$(readlink -f /srv/$FROM/current 2>/dev/null || true)
[ -n "$SRC" ] && [ -d "$SRC" ] || { echo "$FROM has no live release to promote"; exit 1; }
SVC=true; case "$TT" in static|php) SVC=false;; esac
REL=/srv/$TO/releases/$(date +%Y%m%d-%H%M%S)
echo "copying release $(basename "$SRC") of $FROM to $TO"
sudo mkdir -p "$REL"
sudo cp -a "$SRC"/. "$REL"/
sudo touch "$REL"      # cp -a keeps the source folder's old date; deploy.sh prunes releases by date, so this one must count as new
PREV=$(readlink -f /srv/$TO/current 2>/dev/null || true)
sudo ln -sfn "$REL" /srv/$TO/current
ls -1d /srv/$TO/releases/* | sort -r | tail -n +4 | xargs -r sudo rm -rf      # by name (they are timestamps): the copy keeps the old release's file dates, so mtime would be wrong
if [ "$SVC" = true ]; then
  sudo systemctl restart "$TO"
  OK=0; CODE=000
  for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    sleep 2
    CODE=$(curl -s -o /dev/null -m 5 -w '%{http_code}' -H "Host: $HOST" "http://127.0.0.1$HEALTH" || true)
    if systemctl is-active --quiet "$TO" && [ "$CODE" != 000 ] && { if [ -n "$EXPECT" ]; then [ "$CODE" = "$EXPECT" ]; else [ "$CODE" -lt 500 ]; fi; }; then OK=1; break; fi
  done
  if [ "$OK" = 0 ]; then
    echo "HEALTH CHECK FAILED: service $(systemctl is-active "$TO" || true), HTTP $CODE"
    if [ -n "$PREV" ] && [ -d "$PREV" ] && [ "$PREV" != "$REL" ]; then
      sudo ln -sfn "$PREV" /srv/$TO/current; sudo systemctl restart "$TO"; sleep 3
      echo "ROLLED BACK to $(basename "$PREV"), service: $(systemctl is-active "$TO" || true)"
      exit 3
    fi
    exit 4
  fi
fi
echo "promoted: $TO now serves release $(basename "$REL") (from $FROM)"
