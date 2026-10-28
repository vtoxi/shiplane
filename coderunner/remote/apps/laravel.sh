echo "== Laravel __VERSION__ on $HOST"
LV=__VERSION__
check_target
command -v composer >/dev/null 2>&1 || { echo "Composer is required. Install it first."; exit 1; }
export COMPOSER_ALLOW_SUPERUSER=1 COMPOSER_HOME=/tmp/composer-home
echo "creating the project with Composer (this takes a few minutes)"
sudo -E composer create-project --no-interaction --prefer-dist laravel/laravel "$ROOT" "^$LV.0"
sudo sed -i "s#^APP_URL=.*#APP_URL=http://$HOST#" "$ROOT/.env"
sudo chown -R www-data:www-data "$ROOT"
sudo chmod -R ug+rwx "$ROOT/storage" "$ROOT/bootstrap/cache"
{ auth_block; echo "  root * $ROOT/public"; echo "  encode gzip"; echo "  php_fastcgi unix//run/php/php$PHP-fpm.sock"; echo "  file_server"; } | write_site
write_meta laravel "$LV"
echo "Laravel $LV is installed at http://$HOST/ . Its settings are in $ROOT/.env"
