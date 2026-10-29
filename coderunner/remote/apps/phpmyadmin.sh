echo "== phpMyAdmin __VERSION__ on $HOST"
PV=__VERSION__
check_target
URL="https://files.phpmyadmin.net/phpMyAdmin/$PV/phpMyAdmin-$PV-all-languages.tar.gz"
[ "$PV" != latest ] || URL=https://www.phpmyadmin.net/downloads/phpMyAdmin-latest-all-languages.tar.gz
echo "downloading $URL"
curl -fsSL "$URL" -o /tmp/phpmyadmin.tgz
sudo mkdir -p "$ROOT"
sudo tar -xzf /tmp/phpmyadmin.tgz -C "$ROOT" --strip-components=1
rm -f /tmp/phpmyadmin.tgz
sudo cp "$ROOT/config.sample.inc.php" "$ROOT/config.inc.php"
sudo sed -i "s/blowfish_secret'\] = ''/blowfish_secret'] = '$(openssl rand -hex 16)'/" "$ROOT/config.inc.php"
sudo chown -R www-data:www-data "$ROOT"
{ auth_block; echo "  root * $ROOT"; echo "  encode gzip"; echo "  php_fastcgi unix//run/php/php$PHP-fpm.sock"; echo "  file_server"; } | write_site
write_meta phpmyadmin "$PV"
echo "phpMyAdmin is ready at http://$HOST/ (first the site password, then your database login)."
