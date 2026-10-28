set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
DOMAINS="__DOMAINS__"
command -v nsd >/dev/null 2>&1 || { echo "installing NSD (a small authoritative DNS server)"; sudo apt-get install -y -q nsd >/dev/null; }
IP=$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
[ -n "$IP" ] || { echo "could not find this server's public address"; exit 1; }
STAGE=$(mktemp -d); trap 'rm -rf "$STAGE"' EXIT
echo "writing the zones"
__ZONES__
for d in $DOMAINS; do
  nsd-checkzone "$d" "$STAGE/$d.zone" >/dev/null || { echo "the zone $d is not valid: nothing was changed"; nsd-checkzone "$d" "$STAGE/$d.zone" || true; exit 1; }
done
Z=/etc/nsd/shiplane
sudo mkdir -p "$Z" /etc/nsd/nsd.conf.d
for f in "$Z"/*.zone; do
  [ -e "$f" ] || continue
  keep=false; for d in $DOMAINS; do [ "$(basename "$f")" = "$d.zone" ] && keep=true; done
  [ "$keep" = true ] || sudo rm -f "$f"
done
for d in $DOMAINS; do sudo install -m 644 "$STAGE/$d.zone" "$Z/$d.zone"; done
grep -q 'nsd.conf.d' /etc/nsd/nsd.conf || echo 'include: "/etc/nsd/nsd.conf.d/*.conf"' | sudo tee -a /etc/nsd/nsd.conf >/dev/null
{
  echo "server:"; echo "  ip-address: $IP"; echo "  hide-version: yes"; echo "  server-count: 1"; echo "  tcp-count: 10"
  for d in $DOMAINS; do printf 'zone:\n  name: "%s"\n  zonefile: "shiplane/%s.zone"\n' "$d" "$d"; done
} | sudo tee /etc/nsd/nsd.conf.d/shiplane.conf >/dev/null
sudo nsd-checkconf /etc/nsd/nsd.conf || { echo "the NSD configuration is not valid"; exit 1; }
sudo systemctl enable nsd >/dev/null 2>&1 || true
sudo systemctl restart nsd
sleep 1
systemctl is-active --quiet nsd || { echo "NSD did not start:"; sudo journalctl -u nsd -n 10 --no-pager; exit 1; }
echo "NSD is serving $(echo $DOMAINS | wc -w) zone(s) on $IP (port 53)"
echo "if the firewall is closed, open port 53 from the DNS page"
