set -euo pipefail
NAME=__NAME__; HOST=__HOST__; TYPE=__TYPE__; PORT=__PORT__; DLL=__DLL__; START=__START__; BUILD=__BUILD__; DB=__DB__; HEALTH=__HEALTH__; EXPECT=__EXPECT__; NODEBIN=__NODE__
ENVF=/etc/$NAME.env
SVC=true; case "$TYPE" in static|php) SVC=false;; esac   # static and php sites have no systemd service of their own
for f in /etc/caddy/sites/*.caddy; do
  [ -e "$f" ] || continue
  o=$(basename "$f" .caddy); [ "$o" = "$NAME" ] && continue
  if grep -qxF -e "http://$HOST {" -e "http://$HOST, https://$HOST {" "$f"; then echo "hostname $HOST is already used by site '$o'"; exit 1; fi
  if [ "$SVC" = true ] && grep -qE "reverse_proxy 127\.0\.0\.1:$PORT( \{)?\$" "$f"; then echo "port $PORT is already used by site '$o'"; exit 1; fi
done
if [ "$SVC" = true ] && ! grep -qE "reverse_proxy 127\.0\.0\.1:$PORT( \{)?\$" /etc/caddy/sites/$NAME.caddy 2>/dev/null && ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; then
  echo "port $PORT is already in use by another program on this server: pick a different port"; exit 1
fi
[ -z "$NODEBIN" ] || [ -x "$NODEBIN" ] || { echo "the Node runtime $NODEBIN is not installed on this server"; exit 1; }
REL=/srv/$NAME/releases/$(date +%Y%m%d-%H%M%S)

echo "unpacking release $(basename "$REL")"
sudo mkdir -p "$REL"
sudo tar -xzf /tmp/$NAME.tgz -C "$REL"; rm -f /tmp/$NAME.tgz
if [ "$TYPE" = node ] && [ -f "$REL/package.json" ]; then
  echo "installing node dependencies"
  NPM="sudo npm"; [ -z "$NODEBIN" ] || NPM="sudo env PATH=$(dirname "$NODEBIN"):$PATH npm"   # install with the Node version the site will run on
  (cd "$REL" && { $NPM ci --omit=dev --silent </dev/null || $NPM install --omit=dev --silent </dev/null; })
fi
if [ "$TYPE" = command ]; then
  id app_$NAME >/dev/null 2>&1 || sudo useradd -r -s /usr/sbin/nologin app_$NAME
  if [ -n "$BUILD" ]; then
    echo "building: $BUILD"
    sudo chown -R app_$NAME "$REL"        # the build runs as the site's own unprivileged user, never as root
    if ! (cd "$REL" && sudo -u app_$NAME -H bash -c "$BUILD" </dev/null); then echo "the build command failed"; sudo rm -rf "$REL"; exit 1; fi
  fi
  printf '#!/bin/bash\n%s\n' "$START" | sudo tee "$REL/.shiplane-start.sh" >/dev/null
fi
if [ "$TYPE" = php ]; then sudo chown -R www-data:www-data "$REL"; else sudo chown -R root:root "$REL"; fi   # PHP-FPM runs as www-data and needs to write uploads
sudo chmod -R a+rX "$REL"
PREV=$(readlink -f /srv/$NAME/current 2>/dev/null || true)
sudo ln -sfn "$REL" /srv/$NAME/current
ls -1dt /srv/$NAME/releases/* | tail -n +4 | xargs -r sudo rm -rf

if [ "$SVC" = true ]; then
  id app_$NAME >/dev/null 2>&1 || sudo useradd -r -s /usr/sbin/nologin app_$NAME
  [ -f "$ENVF" ] || sudo install -m 640 -o root -g app_$NAME /dev/null "$ENVF"
  setenv() { sudo sed -i "/^$1=/d" "$ENVF"; printf '%s=%s\n' "$1" "$2" | sudo tee -a "$ENVF" >/dev/null; }
  __ENV__
  if [ "$DB" = true ]; then
    DBN=app_${NAME//-/_}
    if ! sudo grep -q '^DATABASE_URL=' "$ENVF"; then
      echo "creating postgres database $DBN"
      PW=$(openssl rand -hex 16)
      if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DBN'" </dev/null | grep -q 1; then
        sudo -u postgres psql -qc "ALTER ROLE $DBN PASSWORD '$PW'" </dev/null
      else
        sudo -u postgres psql -qc "CREATE ROLE $DBN LOGIN PASSWORD '$PW'" </dev/null
      fi
      sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DBN'" </dev/null | grep -q 1 || sudo -u postgres createdb -O $DBN $DBN </dev/null
      setenv DATABASE_URL "postgresql://$DBN:$PW@127.0.0.1:5432/$DBN"
      setenv ConnectionStrings__Default "Host=127.0.0.1;Port=5432;Database=$DBN;Username=$DBN;Password=$PW"
    fi
  fi
  case "$TYPE" in
    dotnet) EXEC="$(command -v dotnet) /srv/$NAME/current/$DLL";;
    command) EXEC="/bin/bash /srv/$NAME/current/.shiplane-start.sh";;
    *) EXEC="${NODEBIN:-$(command -v node)} /srv/$NAME/current/$START";;
  esac
  sudo tee /etc/systemd/system/$NAME.service >/dev/null <<UNIT
[Unit]
Description=$NAME
After=network.target

[Service]
WorkingDirectory=/srv/$NAME/current
ExecStart=$EXEC
EnvironmentFile=$ENVF
Restart=always
RestartSec=3
User=app_$NAME
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable $NAME >/dev/null 2>&1
fi

sudo mkdir -p /etc/caddy/sites /etc/deploy-meta
grep -q '^import sites/\*.caddy' /etc/caddy/Caddyfile || echo 'import sites/*.caddy' | sudo tee -a /etc/caddy/Caddyfile >/dev/null
sudo tee /etc/deploy-meta/$NAME.json >/dev/null <<'META'
__META__
META
sudo tee /etc/caddy/sites/$NAME.caddy >/dev/null <<'SITE'
__SITE__
SITE
[ -x /usr/local/sbin/shiplane-wildcard-attach ] && sudo /usr/local/sbin/shiplane-wildcard-attach || true   # use a wildcard certificate when one covers this site
if ! sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
  echo "Caddy config invalid, removing this site's file"; sudo rm -f /etc/caddy/sites/$NAME.caddy; exit 1
fi
sudo systemctl reload caddy
[ "$SVC" = false ] || sudo systemctl restart $NAME

if [ "$SVC" = true ]; then
  # health gate: the service must be running and answer the health path (below HTTP 500, or the expected status) within ~24 s, else the previous release comes back
  OK=0; CODE=000
  for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    sleep 2
    CODE=$(curl -s -o /dev/null -m 5 -w '%{http_code}' -H "Host: $HOST" "http://127.0.0.1$HEALTH" || true)
    if systemctl is-active --quiet $NAME && [ "$CODE" != 000 ] && { if [ -n "$EXPECT" ]; then [ "$CODE" = "$EXPECT" ]; else [ "$CODE" -lt 500 ]; fi; }; then OK=1; break; fi
  done
  if [ "$OK" = 0 ]; then
    echo "HEALTH CHECK FAILED: service $(systemctl is-active $NAME || true), HTTP $CODE"
    if [ -n "$PREV" ] && [ -d "$PREV" ] && [ "$PREV" != "$REL" ]; then
      echo "rolling back to release $(basename "$PREV")"
      sudo ln -sfn "$PREV" /srv/$NAME/current
      sudo systemctl restart $NAME
      sleep 3
      echo "ROLLED BACK to $(basename "$PREV"), service: $(systemctl is-active $NAME || true)"
      exit 3
    fi
    # first release: there is nothing to go back to, so stop instead of leaving a crash loop
    echo "this was the first release, so there is nothing to go back to: stopping the service"
    sudo systemctl stop $NAME || true; sudo systemctl disable $NAME >/dev/null 2>&1 || true
    sudo rm -f /srv/$NAME/current
    exit 4
  fi
fi
sleep 2
CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" http://127.0.0.1/ || true)
echo "RESULT $HOST -> HTTP $CODE (release $(basename "$REL"))"
[ "$SVC" = false ] || echo "service: $(systemctl is-active $NAME)"
