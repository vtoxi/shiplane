echo "== Firewall (ufw)"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get install -y -q ufw >/dev/null
for p in __SSH_PORTS__; do sudo ufw allow "$p/tcp" >/dev/null; echo "allowed SSH on port $p"; done
for p in __WEB_PORTS__; do sudo ufw allow "$p/tcp" >/dev/null; echo "allowed web traffic on port $p"; done
sudo ufw default deny incoming >/dev/null
sudo ufw default allow outgoing >/dev/null
sudo ufw --force enable >/dev/null
echo "done: everything else from outside is blocked"
sudo ufw status | head -12
