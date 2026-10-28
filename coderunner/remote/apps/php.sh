echo "== PHP __VERSION__ (PHP-FPM and common extensions)"
V=__VERSION__
. /etc/os-release
sudo apt-get update -q
sudo apt-get install -y -q ca-certificates curl lsb-release apt-transport-https gnupg >/dev/null
if ! apt-cache show "php$V-fpm" >/dev/null 2>&1; then
  echo "adding the PHP package repository for $ID"
  case "$ID" in
    ubuntu) sudo apt-get install -y -q software-properties-common; sudo add-apt-repository -y ppa:ondrej/php ;;
    debian)
      curl -fsSLo /tmp/sury-keyring.deb https://packages.sury.org/debsuryorg-archive-keyring.deb
      sudo dpkg -i /tmp/sury-keyring.deb; rm -f /tmp/sury-keyring.deb
      echo "deb [signed-by=/usr/share/keyrings/debsuryorg-archive-keyring.gpg] https://packages.sury.org/php/ $VERSION_CODENAME main" | sudo tee /etc/apt/sources.list.d/php.list >/dev/null ;;
    *) echo "Automatic PHP install supports Ubuntu and Debian only (this server is $ID)"; exit 1 ;;
  esac
  sudo apt-get update -q
fi
sudo apt-get install -y -q "php$V-fpm" "php$V-cli" "php$V-mysql" "php$V-pgsql" "php$V-curl" "php$V-gd" "php$V-mbstring" "php$V-xml" "php$V-zip" "php$V-intl" "php$V-bcmath"
sudo systemctl enable --now "php$V-fpm"
sleep 2
test -S "/run/php/php$V-fpm.sock" || { echo "PHP-FPM socket /run/php/php$V-fpm.sock not found"; exit 1; }
echo "installed: $("php$V" -v | head -1)"
