echo "== Docker"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -q
sudo apt-get install -y -q docker.io
sudo systemctl enable --now docker
echo "installed: $(docker --version)"
