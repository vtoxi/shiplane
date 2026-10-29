echo "== Composer"
command -v php >/dev/null 2>&1 || { echo "Composer needs the PHP command line. Install PHP first."; exit 1; }
EXPECTED=$(curl -fsSL https://composer.github.io/installer.sig)
curl -fsSL https://getcomposer.org/installer -o /tmp/composer-setup.php
ACTUAL=$(php -r "echo hash_file('sha384', '/tmp/composer-setup.php');")
if [ "$EXPECTED" != "$ACTUAL" ]; then echo "Composer installer checksum does not match; stopping"; rm -f /tmp/composer-setup.php; exit 1; fi
sudo php /tmp/composer-setup.php --quiet --install-dir=/usr/local/bin --filename=composer
rm -f /tmp/composer-setup.php
echo "installed: $(composer --version 2>/dev/null | head -1)"
