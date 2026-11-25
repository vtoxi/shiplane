#!/usr/bin/env bash
# Turn this server into a WireGuard VPN so a phone (or laptop) can send ALL its traffic through it.
#
#   sudo ./setup_wireguard.sh                  # first run: install, create the server, add a device called "phone" and show its QR code
#   sudo ./setup_wireguard.sh --add laptop     # another device (name: a-z, 0-9, dash)
#   sudo ./setup_wireguard.sh --show phone     # show a device's QR code and config again
#   sudo ./setup_wireguard.sh --list           # devices, and who connected recently
#   sudo ./setup_wireguard.sh --remove phone   # revoke a device
#   sudo ./setup_wireguard.sh --status
#   sudo ./setup_wireguard.sh --uninstall --yes
#
# On the phone: install the WireGuard app (Android / iOS), tap + > "Scan from QR code", scan the code shown here, switch the tunnel on.
# Options (first run): --port 51820   --endpoint HOST[:PORT]   --dns "1.1.1.1, 9.9.9.9"   --iface eth0   --no-ipv6
#
# What it does: installs wireguard + qrencode from the distribution (Debian/Ubuntu); creates /etc/wireguard/wg0.conf (root only) with the
# private networks 10.8.0.0/24 and fd42:42:42::/64; turns on IP forwarding; NATs the VPN traffic out of the public interface; opens the
# UDP port in ufw when ufw is active; starts wg-quick@wg0 at boot. Devices cannot talk to each other (only out to the internet).
# Each device gets its own key pair and pre-shared key. Its private key exists only in its config under /etc/wireguard/shiplane/clients
# (root only) and in the QR code on this screen: scan it, then clear the terminal. Never paste a QR code or config anywhere.
#
# ALSO NEEDED on Hetzner: if the server has a Cloud Firewall (Hetzner console > Firewalls), add an inbound rule UDP 51820 (or your --port).
set -euo pipefail
umask 077

WG_DIR="${WG_DIR:-/etc/wireguard}"
STATE="$WG_DIR/shiplane"
CLIENTS="$STATE/clients"
IF=wg0
CONF="$WG_DIR/$IF.conf"
NET4=10.8.0; NET6=fd42:42:42
PORT=51820; DNS="1.1.1.1, 9.9.9.9"; ENDPOINT=""; IFACE=""; IPV6=auto
NOSYS="${WG_NO_SYSTEM:-0}"            # tests: skip packages, services, sysctl and firewall

die() { echo "error: $*" >&2; exit 1; }
say() { echo "== $*"; }
need_root() { [ "$NOSYS" = 1 ] || [ "$(id -u)" = 0 ] || die "run with sudo"; }
valid_name() { [[ "$1" =~ ^[a-z][a-z0-9-]{0,30}$ ]] || die "name must be 1-31 characters: a-z, 0-9 and dash, starting with a letter (got: $1)"; }

ACTION=default; NAME=phone; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --add|--show|--remove) ACTION="${1#--}"; NAME="${2:-}"; [ -n "$NAME" ] || die "$1 needs a device name"; shift 2;;
    --list|--status) ACTION="${1#--}"; shift;;
    --uninstall) ACTION=uninstall; shift;;
    --yes) YES=1; shift;;
    --port) PORT="${2:-}"; shift 2;;
    --endpoint) ENDPOINT="${2:-}"; shift 2;;
    --dns) DNS="${2:-}"; shift 2;;
    --iface) IFACE="${2:-}"; shift 2;;
    --no-ipv6) IPV6=off; shift;;
    -h|--help) sed -n '2,24p' "$0"; exit 0;;
    *) die "unknown option: $1 (try --help)";;
  esac
done
[[ "$PORT" =~ ^[0-9]{2,5}$ ]] && [ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || die "--port must be a number from 1024 to 65535"
dns_re='^[0-9a-fA-F:., ]+$'
[[ "$DNS" =~ $dns_re ]] || die "--dns must be IP addresses separated by commas"
[ -z "$ENDPOINT" ] || [[ "$ENDPOINT" =~ ^[A-Za-z0-9.:-]+$ ]] || die "--endpoint looks wrong (example: vpn.example.com or 203.0.113.5:51820)"
need_root

# ---------------------------------------------------------------- helpers
default_iface() { ip -4 route show default 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="dev"){print $(i+1); exit}}'; }
have_ipv6() { [ "$IPV6" != off ] && [ -n "$(ip -6 addr show dev "$1" scope global 2>/dev/null | grep -m1 inet6 || true)" ]; }

server_setting() { grep -m1 "^# shiplane-$1:" "$STATE/server.info" 2>/dev/null | cut -d: -f2- | sed 's/^ //'; }

next_ip() {   # smallest unused host number 2..254
  local used n; used=$(grep -ho "AllowedIPs = $NET4\.[0-9]*" "$CONF" 2>/dev/null | sed "s/.*\.//" || true)
  for n in $(seq 2 254); do echo "$used" | grep -qx "$n" || { echo "$n"; return; }; done
  die "no free address left in $NET4.0/24"
}

apply_peers() { [ "$NOSYS" = 1 ] || { systemctl is-active --quiet "wg-quick@$IF" && wg syncconf "$IF" <(wg-quick strip "$IF") || true; }; }

# ---------------------------------------------------------------- server
install_server() {
  if [ -f "$CONF" ] && [ -f "$STATE/server.info" ]; then say "the VPN server already exists ($CONF): keeping it"; return; fi
  [ -z "$IFACE" ] && IFACE="$(default_iface)"
  [ -n "$IFACE" ] || die "could not find the public network interface: pass --iface eth0"
  [[ "$IFACE" =~ ^[A-Za-z0-9._-]+$ ]] || die "--iface looks wrong"
  if [ -z "$ENDPOINT" ]; then
    ENDPOINT="$(ip -4 addr show dev "$IFACE" scope global 2>/dev/null | awk '/inet /{sub("/.*","",$2); print $2; exit}')"
    [ -n "$ENDPOINT" ] || die "could not find this server's public IPv4 address: pass --endpoint YOUR.SERVER.IP"
    case "$ENDPOINT" in 10.*|192.168.*|172.1[6-9].*|172.2?.*|172.3[01].*) echo "warning: $ENDPOINT is a private address; if this server is behind NAT pass --endpoint with its public address";; esac
  fi
  case "$ENDPOINT" in *:*[0-9]) [[ "$ENDPOINT" == *:*:* ]] || { PORT="${ENDPOINT##*:}"; ENDPOINT="${ENDPOINT%:*}"; };; esac
  local v6=no; have_ipv6 "$IFACE" && v6=yes
  if [ "$NOSYS" != 1 ]; then
    command -v apt-get >/dev/null || die "needs Debian or Ubuntu (apt-get)"
    say "installing WireGuard"
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q wireguard wireguard-tools qrencode iptables >/dev/null
  fi
  mkdir -p "$WG_DIR" "$CLIENTS"; chmod 700 "$WG_DIR" "$STATE" "$CLIENTS"
  local priv pub; priv="$(wg genkey)"; pub="$(printf '%s' "$priv" | wg pubkey)"
  local up down
  up="iptables -I FORWARD 1 -i $IF -o $IF -j DROP; iptables -I FORWARD 2 -i $IF -j ACCEPT; iptables -I FORWARD 3 -o $IF -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -A POSTROUTING -s $NET4.0/24 -o $IFACE -j MASQUERADE"
  down="iptables -D FORWARD -i $IF -o $IF -j DROP; iptables -D FORWARD -i $IF -j ACCEPT; iptables -D FORWARD -o $IF -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; iptables -t nat -D POSTROUTING -s $NET4.0/24 -o $IFACE -j MASQUERADE"
  local addr="$NET4.1/24"
  if [ "$v6" = yes ]; then
    addr="$addr, $NET6::1/64"
    up="$up; ip6tables -I FORWARD 1 -i $IF -o $IF -j DROP; ip6tables -I FORWARD 2 -i $IF -j ACCEPT; ip6tables -I FORWARD 3 -o $IF -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; ip6tables -t nat -A POSTROUTING -s $NET6::/64 -o $IFACE -j MASQUERADE"
    down="$down; ip6tables -D FORWARD -i $IF -o $IF -j DROP; ip6tables -D FORWARD -i $IF -j ACCEPT; ip6tables -D FORWARD -o $IF -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT; ip6tables -t nat -D POSTROUTING -s $NET6::/64 -o $IFACE -j MASQUERADE"
  fi
  {
    echo "# Managed by scripts/setup_wireguard.sh. Edit devices with the script, not by hand."
    echo "[Interface]"
    echo "Address = $addr"
    echo "ListenPort = $PORT"
    echo "PrivateKey = $priv"
    echo "PostUp = $up"
    echo "PostDown = $down"
  } > "$CONF"; chmod 600 "$CONF"
  { echo "# shiplane-public: $pub"; echo "# shiplane-endpoint: $ENDPOINT:$PORT"; echo "# shiplane-dns: $DNS"; echo "# shiplane-ipv6: $v6"; echo "# shiplane-iface: $IFACE"; } > "$STATE/server.info"
  if [ "$NOSYS" != 1 ]; then
    say "turning on IP forwarding"
    { echo "net.ipv4.ip_forward=1"; [ "$v6" = yes ] && { echo "net.ipv6.conf.all.forwarding=1"; echo "net.ipv6.conf.$IFACE.accept_ra=2"; }; true; } > /etc/sysctl.d/99-shiplane-wireguard.conf
    sysctl -q --system >/dev/null 2>&1 || true
    if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then say "opening UDP $PORT in ufw"; ufw allow "$PORT/udp" comment 'WireGuard' >/dev/null; fi
    say "starting the VPN (and at every boot)"
    systemctl enable --now "wg-quick@$IF" >/dev/null
  fi
  say "the VPN server is ready on $ENDPOINT:$PORT (IPv6 $v6)"
}

# ---------------------------------------------------------------- devices
client_conf() { echo "$CLIENTS/$1.conf"; }

add_client() {
  local name="$1"; valid_name "$name"
  [ -f "$STATE/server.info" ] || die "the VPN server is not set up yet: run the script without options first"
  [ ! -e "$(client_conf "$name")" ] || die "a device called '$name' already exists (see --list, or --show $name)"
  local n priv pub psk spub endpoint dns v6
  n="$(next_ip)"
  priv="$(wg genkey)"; pub="$(printf '%s' "$priv" | wg pubkey)"; psk="$(wg genpsk)"
  spub="$(server_setting public)"; endpoint="$(server_setting endpoint)"; dns="$(server_setting dns)"; v6="$(server_setting ipv6)"
  local addr="$NET4.$n/32" allowed="$NET4.$n/32"
  [ "$v6" = yes ] && { addr="$addr, $NET6::$n/128"; allowed="$allowed, $NET6::$n/128"; }
  {
    echo
    echo "# shiplane-client: $name"
    echo "[Peer]"
    echo "PublicKey = $pub"
    echo "PresharedKey = $psk"
    echo "AllowedIPs = $allowed"
  } >> "$CONF"
  {
    echo "[Interface]"
    echo "PrivateKey = $priv"
    echo "Address = $addr"
    echo "DNS = $dns"
    echo
    echo "[Peer]"
    echo "PublicKey = $spub"
    echo "PresharedKey = $psk"
    echo "Endpoint = $endpoint"
    echo "AllowedIPs = 0.0.0.0/0, ::/0"      # everything, IPv6 included: nothing leaks around the tunnel
    echo "PersistentKeepalive = 25"
  } > "$(client_conf "$name")"; chmod 600 "$(client_conf "$name")"
  apply_peers
  say "device '$name' added ($NET4.$n)"
  show_client "$name"
}

show_client() {
  local f; f="$(client_conf "$1")"; [ -f "$f" ] || die "no device called '$1' (see --list)"
  echo
  echo "On the phone: WireGuard app > + > Scan from QR code. This code holds the device's private key: scan it, then clear this screen (Ctrl+L / 'clear')."
  echo
  qrencode -t ansiutf8 < "$f" || echo "(qrencode failed: copy the config below instead)"
  if [ "${SHOW_CONFIG:-0}" = 1 ]; then echo; cat "$f"; else echo; echo "Config file (for a laptop: copy it over a secure channel): $f"; fi
}

remove_client() {
  local name="$1"; valid_name "$name"
  [ -f "$(client_conf "$name")" ] || die "no device called '$name' (see --list)"
  local tmp; tmp="$(mktemp)"
  awk -v RS= -v ORS='\n\n' -v marker="# shiplane-client: $name" 'index($0, marker "\n") != 1' "$CONF" > "$tmp"
  cat "$tmp" > "$CONF"; rm -f "$tmp"; chmod 600 "$CONF"
  rm -f "$(client_conf "$name")"
  [ "$NOSYS" = 1 ] || { systemctl is-active --quiet "wg-quick@$IF" && wg syncconf "$IF" <(wg-quick strip "$IF") || true; }
  say "device '$name' removed: its key no longer works"
}

list_clients() {
  [ -f "$CONF" ] || die "the VPN server is not set up yet"
  local f name ip hs
  printf '%-20s %-34s %s\n' DEVICE ADDRESS "LAST CONNECTED"
  for f in "$CLIENTS"/*.conf; do
    [ -e "$f" ] || { echo "(no devices yet: --add NAME)"; return; }
    name="$(basename "$f" .conf)"
    ip="$(grep -m1 '^Address' "$f" | sed 's/Address = //')"
    hs="never"
    if [ "$NOSYS" != 1 ]; then
      local pub; pub="$(awk -v m="# shiplane-client: $name" '$0==m{f=1;next} f&&/^PublicKey/{print $3; exit}' "$CONF")"
      local t; t="$(wg show "$IF" latest-handshakes 2>/dev/null | awk -v p="$pub" '$1==p{print $2}')"
      [ -n "${t:-}" ] && [ "$t" != 0 ] && hs="$(( ($(date +%s) - t) / 60 )) min ago"
    fi
    printf '%-20s %-34s %s\n' "$name" "$ip" "$hs"
  done
}

uninstall() {
  [ "$YES" = 1 ] || die "this deletes the VPN server and every device key: run again with --uninstall --yes"
  if [ "$NOSYS" != 1 ]; then
    systemctl disable --now "wg-quick@$IF" >/dev/null 2>&1 || true
    local port; port="$(awk -F' = ' '/^ListenPort/{print $2}' "$CONF" 2>/dev/null || true)"
    command -v ufw >/dev/null && [ -n "$port" ] && ufw delete allow "$port/udp" >/dev/null 2>&1 || true
    rm -f /etc/sysctl.d/99-shiplane-wireguard.conf
  fi
  rm -rf "$STATE" "$CONF"
  say "removed. The wireguard package is still installed (apt remove wireguard to drop it); also remove the UDP rule in the Hetzner firewall."
}

case "$ACTION" in
  default) valid_name "$NAME"; install_server
           if [ -e "$(client_conf "$NAME")" ]; then say "device '$NAME' already exists"; show_client "$NAME"; else add_client "$NAME"; fi;;
  add) add_client "$NAME";;
  show) valid_name "$NAME"; SHOW_CONFIG=1 show_client "$NAME";;
  remove) remove_client "$NAME";;
  list) list_clients;;
  status) [ "$NOSYS" = 1 ] || { wg show; echo; }; list_clients;;
  uninstall) uninstall;;
esac
