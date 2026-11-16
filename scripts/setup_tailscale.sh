#!/usr/bin/env bash
# Put the server on your Tailscale network and serve Shiplane on it, so your own computers reach it directly at
# https://<server>.<tailnet>.ts.net  (no public exposure needed).
#
#   sudo ./setup_tailscale.sh                       # prints a login link to open in your browser
#   sudo TS_AUTHKEY=tskey-auth-... ./setup_tailscale.sh   # unattended (auth key from the Tailscale admin console; kept out of argv)
#   sudo ./setup_tailscale.sh --status | --remove
#
# Needs Debian/Ubuntu. In the Tailscale admin console enable MagicDNS and HTTPS Certificates (DNS page) first, or HTTPS serving will fail.
# What it changes: installs Tailscale from its official apt repository, joins the tailnet, runs `tailscale serve` to
# 127.0.0.1:8765 (this app stays bound to localhost), and ADDS the tailnet name to CODERUNNER_ALLOW_HOSTS in /etc/coderunner.env
# (existing hostnames are kept). Nothing else, and it does not open any firewall port.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
ENVF=/etc/coderunner.env; PORT=8765

allow_host() {   # $1 add|remove  $2 host
  [ -f "$ENVF" ] || { echo "(no $ENVF: this server runs the app another way, add $2 to CODERUNNER_ALLOW_HOSTS yourself)"; return 0; }
  local cur new
  cur=$(grep '^CODERUNNER_ALLOW_HOSTS=' "$ENVF" | head -1 | cut -d= -f2- || true)
  new=$(printf '%s' "$cur" | tr ',' '\n' | grep -vxF "$2" | grep -v '^$' || true)
  [ "$1" = add ] && new=$(printf '%s\n%s' "$new" "$2" | grep -v '^$')
  new=$(printf '%s' "$new" | paste -sd, -)
  if grep -q '^CODERUNNER_ALLOW_HOSTS=' "$ENVF"; then sed -i "s|^CODERUNNER_ALLOW_HOSTS=.*|CODERUNNER_ALLOW_HOSTS=$new|" "$ENVF"; else echo "CODERUNNER_ALLOW_HOSTS=$new" >> "$ENVF"; fi
  systemctl restart coderunner 2>/dev/null || true
}
dnsname() { tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'; }

case "${1:-}" in
  --status) tailscale status; tailscale serve status; exit 0;;
  --remove)
    NAME=$(dnsname 2>/dev/null || true)
    tailscale serve reset 2>/dev/null || true
    [ -n "$NAME" ] && allow_host remove "$NAME"
    tailscale down 2>/dev/null || true
    echo "Shiplane is no longer served on the tailnet (Tailscale itself stays installed; remove the machine in the admin console to finish)."; exit 0;;
  "") ;;
  *) echo "unknown option: $1"; exit 1;;
esac

command -v apt-get >/dev/null || { echo "Needs Debian or Ubuntu (apt-get)"; exit 1; }
if ! command -v tailscale >/dev/null; then
  . /etc/os-release
  echo "== installing Tailscale from pkgs.tailscale.com"
  apt-get install -y -q curl gnupg >/dev/null
  curl -fsSL "https://pkgs.tailscale.com/stable/${ID}/${VERSION_CODENAME}.noarmor.gpg" -o /usr/share/keyrings/tailscale-archive-keyring.gpg
  curl -fsSL "https://pkgs.tailscale.com/stable/${ID}/${VERSION_CODENAME}.tailscale-keyring.list" -o /etc/apt/sources.list.d/tailscale.list
  apt-get update -q >/dev/null; apt-get install -y -q tailscale >/dev/null
fi
systemctl enable --now tailscaled >/dev/null

if ! tailscale status >/dev/null 2>&1 || tailscale status 2>&1 | grep -qiE 'logged out|needs login'; then
  echo "== joining your tailnet"
  if [ -n "${TS_AUTHKEY:-}" ]; then tailscale up --authkey="$TS_AUTHKEY" --ssh=false
  else echo "Open the link below in your browser and approve this machine:"; tailscale up --ssh=false; fi
fi

NAME=$(dnsname)
echo "== serving Shiplane at https://$NAME"
tailscale serve --bg --https=443 "http://127.0.0.1:$PORT" >/dev/null
allow_host add "$NAME"
echo
echo "Done. From any computer signed in to your tailnet open:  https://$NAME"
echo "If that shows a certificate error, enable HTTPS Certificates on the DNS page of the Tailscale admin console and run this script again."
