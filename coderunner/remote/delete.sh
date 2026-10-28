set -uo pipefail
NAME=__NAME__; DROPDB=__DROPDB__
sudo systemctl disable --now $NAME >/dev/null 2>&1
if command -v docker >/dev/null 2>&1; then
  sudo docker rm -f "shiplane-$NAME" >/dev/null 2>&1
  sudo docker images -q "shiplane/$NAME" 2>/dev/null | sort -u | xargs -r sudo docker rmi -f >/dev/null 2>&1
fi
sudo rm -f /etc/systemd/system/$NAME.service /etc/$NAME.env /etc/caddy/sites/$NAME.caddy /etc/deploy-meta/$NAME.json
sudo rm -rf /srv/$NAME
sudo systemctl daemon-reload
if [ "$DROPDB" = true ]; then
  DBN=app_${NAME//-/_}
  cd /tmp
  sudo -u postgres psql -qc "DROP DATABASE IF EXISTS $DBN" </dev/null
  sudo -u postgres psql -qc "DROP ROLE IF EXISTS $DBN" </dev/null
fi
sudo userdel app_$NAME >/dev/null 2>&1
sudo systemctl reload caddy
echo "deleted $NAME"
