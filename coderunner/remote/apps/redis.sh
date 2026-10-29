echo "== Redis"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -q
sudo apt-get install -y -q redis-server
sudo systemctl enable --now redis-server
echo "installed: $(redis-server --version)"
