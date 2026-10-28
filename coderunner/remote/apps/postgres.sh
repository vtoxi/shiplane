echo "== PostgreSQL"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -q
sudo apt-get install -y -q postgresql
sudo systemctl enable --now postgresql
echo "installed: $(psql --version)"
