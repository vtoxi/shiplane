set -uo pipefail
NAME=__NAME__; DROPDB=__DROPDB__; PURGE=__PURGE__
sudo systemctl disable --now $NAME >/dev/null 2>&1
if command -v docker >/dev/null 2>&1; then
  sudo docker rm -f "shiplane-$NAME" >/dev/null 2>&1
  sudo docker images -q "shiplane/$NAME" 2>/dev/null | sort -u | xargs -r sudo docker rmi -f >/dev/null 2>&1
fi
sudo rm -f /etc/systemd/system/$NAME.service /etc/$NAME.env /etc/caddy/sites/$NAME.caddy /etc/deploy-meta/$NAME.json
sudo rm -rf /srv/$NAME
sudo systemctl daemon-reload
if [ "$DROPDB" = true ] || [ "$PURGE" = true ]; then
  DBN=app_${NAME//-/_}
  cd /tmp
  sudo -u postgres psql -qc "DROP DATABASE IF EXISTS $DBN" </dev/null
  sudo -u postgres psql -qc "DROP ROLE IF EXISTS $DBN" </dev/null
fi
if [ "$PURGE" = true ]; then
  DBN=app_${NAME//-/_}
  if command -v mysql >/dev/null 2>&1 && systemctl is-active --quiet mariadb mysql 2>/dev/null; then
    sudo mysql -e "DROP DATABASE IF EXISTS \`$DBN\`; DROP USER IF EXISTS '$DBN'@'localhost'" </dev/null >/dev/null 2>&1
  fi
  sudo rm -rf "/var/lib/shiplane-git/$NAME" "/var/lib/shiplane-git/keys/$NAME" "/var/lib/shiplane-git/keys/$NAME.pub"
  sudo rm -f "/etc/cron.d/coderunner-$NAME" "/etc/logrotate.d/$NAME" "/var/log/$NAME.log"
  sudo rm -rf "/var/log/$NAME"
fi
sudo userdel app_$NAME >/dev/null 2>&1
sudo systemctl reload caddy
echo "deleted $NAME"
