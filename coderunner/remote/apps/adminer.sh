echo "== Adminer __VERSION__ on $HOST"
AV=__VERSION__
check_target
URL="https://github.com/vrana/adminer/releases/download/v$AV/adminer-$AV.php"
[ "$AV" != latest ] || URL=https://www.adminer.org/latest.php
echo "downloading $URL"
sudo mkdir -p "$ROOT"
sudo curl -fsSL "$URL" -o "$ROOT/index.php"
sudo chown -R www-data:www-data "$ROOT"
{ auth_block; echo "  root * $ROOT"; echo "  encode gzip"; echo "  php_fastcgi unix//run/php/php$PHP-fpm.sock"; echo "  file_server"; } | write_site
write_meta adminer "$AV"
echo "Adminer is ready at http://$HOST/ (first the site password, then your database login)."
