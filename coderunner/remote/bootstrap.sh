set -euo pipefail
# Make an unmanaged Ubuntu/Debian server ready for Shiplane: the tools its scripts use, Caddy (web server + HTTPS) and the folders it deploys into.
# Safe to run again. It does not touch SSH, the firewall or existing sites: use Server care for hardening afterwards.
export DEBIAN_FRONTEND=noninteractive
sudo -n true 2>/dev/null || { echo "this login cannot use sudo without a password: use root or a user with passwordless sudo"; exit 1; }
. /etc/os-release
case "${ID:-}" in ubuntu|debian) ;; *) echo "this is ${PRETTY_NAME:-an unknown system}: automatic setup supports Ubuntu and Debian only"; exit 1;; esac
echo "== Tools"
sudo apt-get update -q >/dev/null
sudo apt-get install -y -q curl ca-certificates tar gzip python3 openssl gnupg apt-transport-https debian-keyring debian-archive-keyring >/dev/null
echo "== Caddy"
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -q >/dev/null
  sudo apt-get install -y -q caddy >/dev/null
fi
echo "== Folders"
sudo mkdir -p /etc/caddy/sites /etc/deploy-meta /srv
sudo touch /etc/caddy/Caddyfile
grep -q '^import sites/\*.caddy' /etc/caddy/Caddyfile || echo 'import sites/*.caddy' | sudo tee -a /etc/caddy/Caddyfile >/dev/null
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || { echo "the existing Caddy configuration is invalid: fix it first (sudo caddy validate --config /etc/caddy/Caddyfile)"; exit 1; }
sudo systemctl enable --now caddy >/dev/null 2>&1 || true
sudo systemctl reload caddy 2>/dev/null || sudo systemctl restart caddy
echo "done: $(caddy version | head -1) is running; this server is ready for deploys. Next: Server care for a security check."
