echo "== NSD (authoritative DNS server)"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -q
sudo apt-get install -y -q nsd
sudo systemctl disable --now nsd >/dev/null 2>&1 || true
echo "installed: $(nsd -v 2>&1 | head -1)"
echo "NSD stays stopped until you press Apply on the DNS page"
