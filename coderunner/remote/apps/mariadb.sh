echo "== MariaDB"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -q
sudo apt-get install -y -q mariadb-server
sudo systemctl enable --now mariadb
echo "installed: $(mariadbd --version 2>/dev/null || mysqld --version)"
