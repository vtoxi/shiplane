echo "== WordPress __VERSION__ on $HOST"
WPV=__VERSION__
check_target
DB="wp_${SITE//-/_}"; DB=${DB:0:32}
if [ "$(sudo mysql -N -e "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='$DB'")" != 0 ]; then
  echo "A database called $DB already exists; nothing was changed"; exit 1
fi
URL=https://wordpress.org/latest.tar.gz; [ "$WPV" = latest ] || URL="https://wordpress.org/wordpress-$WPV.tar.gz"
echo "downloading $URL"
curl -fsSL "$URL" -o /tmp/wordpress.tgz
sudo mkdir -p "$ROOT"
sudo tar -xzf /tmp/wordpress.tgz -C "$ROOT" --strip-components=1
rm -f /tmp/wordpress.tgz
PW=$(openssl rand -hex 16)
echo "creating the database $DB"
sudo mysql <<SQL
CREATE DATABASE \`$DB\` CHARACTER SET utf8mb4;
CREATE USER '$DB'@'localhost' IDENTIFIED BY '$PW';
GRANT ALL ON \`$DB\`.* TO '$DB'@'localhost';
FLUSH PRIVILEGES;
SQL
sudo cp "$ROOT/wp-config-sample.php" "$ROOT/wp-config.php"
sudo sed -i "s/database_name_here/$DB/; s/username_here/$DB/; s/password_here/$PW/" "$ROOT/wp-config.php"
for K in AUTH_KEY SECURE_AUTH_KEY LOGGED_IN_KEY NONCE_KEY AUTH_SALT SECURE_AUTH_SALT LOGGED_IN_SALT NONCE_SALT; do
  sudo sed -i -E "s/define\( *'$K', *'put your unique phrase here' *\);/define( '$K', '$(openssl rand -hex 32)' );/" "$ROOT/wp-config.php"
done
VER=$(grep -oP "\\\$wp_version = '\K[^']+" "$ROOT/wp-includes/version.php" || echo "$WPV")
sudo chown -R www-data:www-data "$ROOT"
{ auth_block; echo "  root * $ROOT"; echo "  encode gzip"; echo "  php_fastcgi unix//run/php/php$PHP-fpm.sock"; echo "  file_server"; } | write_site
write_meta wordpress "$VER"
echo "WordPress $VER is installed. Open http://$HOST/wp-admin/install.php to create the admin account."
