set -euo pipefail
# Wildcard certificates (*.zone + zone) from Let's Encrypt with Certbot's DNS plugins, used by Caddy for direct-HTTPS sites.
# MODE: issue | remove | status. Credentials arrive inside this script (stdin, never argv) and are written root-only.
MODE=__MODE__; ZONE=__ZONE__; PROVIDER=__PROVIDER__; EMAIL=__EMAIL__
CRED_TEXT=__CRED__
WD=/etc/caddy/wildcard; CERTS=/etc/caddy/certs; HOOK=/usr/local/sbin/shiplane-cert-deploy; ATTACH=/usr/local/sbin/shiplane-wildcard-attach

if [ "$MODE" = status ]; then
  for f in $WD/*.caddy; do
    [ -e "$f" ] || continue
    z=$(basename "$f" .caddy)
    end=$(sudo openssl x509 -enddate -noout -in $CERTS/$z/fullchain.pem 2>/dev/null | cut -d= -f2 || true)
    echo "CERT|$z|$end"
  done
  exit 0
fi

ID=$(echo "$ZONE" | tr . _)
sudo install -d -m 755 $WD
sudo install -d -m 755 $CERTS

# Helpers kept on the server: renewals run without this app, and deploys re-attach sites through them.
sudo tee $HOOK >/dev/null <<'SH'
#!/bin/sh
# certbot deploy hook: copy a renewed Shiplane wildcard where Caddy (user caddy) can read it, then reload Caddy.
set -e
case "$(basename "$RENEWED_LINEAGE")" in shiplane-*) ;; *) exit 0 ;; esac
Z=$(echo "$RENEWED_DOMAINS" | tr ' ' '\n' | grep -v '^\*' | head -1)
install -d -o caddy -g caddy -m 700 /etc/caddy/certs/$Z
install -o caddy -g caddy -m 600 "$RENEWED_LINEAGE/fullchain.pem" "$RENEWED_LINEAGE/privkey.pem" /etc/caddy/certs/$Z/
systemctl reload caddy || true
SH
sudo tee $ATTACH >/dev/null <<'SH'
#!/bin/sh
# Make every direct-HTTPS site use the wildcard of its zone (one label under it, or the zone itself), and only those.
set -e
for f in /etc/caddy/sites/*.caddy; do
  [ -e "$f" ] || continue
  sed -i '/^\timport wild_/d' "$f"
  head -1 "$f" | grep -q 'https://' || continue
  host=$(head -1 "$f" | sed -E 's#^http://([^ ,{]+).*#\1#')
  for w in /etc/caddy/wildcard/*.caddy; do
    [ -e "$w" ] || continue
    z=$(basename "$w" .caddy)
    if [ "$host" = "$z" ] || echo "$host" | grep -qE "^[a-z0-9-]+\.$(echo "$z" | sed 's/\./\\./g')\$"; then
      sed -i "1a\\	import wild_$(echo "$z" | tr . _)" "$f"
      break
    fi
  done
done
SH
sudo chmod 755 $HOOK $ATTACH

wire_caddyfile() {   # snippets must be defined before the sites that import them
  if [ -n "$(ls $WD/*.caddy 2>/dev/null)" ]; then
    grep -q '^import wildcard/\*.caddy' /etc/caddy/Caddyfile && return
    if grep -q '^import sites/\*.caddy' /etc/caddy/Caddyfile; then
      sudo sed -i 's#^import sites/\*.caddy#import wildcard/*.caddy\nimport sites/*.caddy#' /etc/caddy/Caddyfile
    else
      echo 'import wildcard/*.caddy' | sudo tee -a /etc/caddy/Caddyfile >/dev/null
    fi
  else
    sudo sed -i '/^import wildcard\/\*.caddy/d' /etc/caddy/Caddyfile
  fi
}

apply_caddy() {
  wire_caddyfile
  sudo $ATTACH
  if ! sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    echo "Caddy rejected the change"; return 1
  fi
  sudo systemctl reload caddy
}

if [ "$MODE" = remove ]; then
  echo "== removing the wildcard for $ZONE"
  sudo certbot delete --non-interactive --cert-name "shiplane-$ID" >/dev/null 2>&1 || true
  sudo rm -rf "$WD/$ZONE.caddy" "$CERTS/$ZONE"
  apply_caddy
  echo "Removed. Sites under $ZONE get their own certificates from Caddy again."
  exit 0
fi

echo "== certbot and the $PROVIDER DNS plugin"
case "$PROVIDER" in
  cloudflare) PKG=python3-certbot-dns-cloudflare; ARGS="--dns-cloudflare --dns-cloudflare-credentials /etc/shiplane/dns/cloudflare.ini --dns-cloudflare-propagation-seconds 30" ;;
  digitalocean) PKG=python3-certbot-dns-digitalocean; ARGS="--dns-digitalocean --dns-digitalocean-credentials /etc/shiplane/dns/digitalocean.ini --dns-digitalocean-propagation-seconds 60" ;;
  route53) PKG=python3-certbot-dns-route53; ARGS="--dns-route53" ;;
  *) echo "unknown provider"; exit 1 ;;
esac
command -v certbot >/dev/null && dpkg -s $PKG >/dev/null 2>&1 || { sudo apt-get update -qq; sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot $PKG >/dev/null; }

echo "== DNS credentials (root only, /etc/shiplane/dns)"
sudo install -d -m 700 /etc/shiplane/dns
printf '%s\n' "$CRED_TEXT" | sudo tee /etc/shiplane/dns/$PROVIDER.ini >/dev/null
sudo chmod 600 /etc/shiplane/dns/$PROVIDER.ini
if [ "$PROVIDER" = route53 ]; then   # the route53 plugin reads AWS's credentials file; renewals run from certbot's timer
  export AWS_SHARED_CREDENTIALS_FILE=/etc/shiplane/dns/route53.ini
  sudo install -d /etc/systemd/system/certbot.service.d
  printf '[Service]\nEnvironment=AWS_SHARED_CREDENTIALS_FILE=/etc/shiplane/dns/route53.ini\n' | sudo tee /etc/systemd/system/certbot.service.d/shiplane-route53.conf >/dev/null
  sudo systemctl daemon-reload
fi

echo "== asking Let's Encrypt for *.$ZONE and $ZONE (DNS check, about a minute)"
sudo --preserve-env=AWS_SHARED_CREDENTIALS_FILE certbot certonly --non-interactive --agree-tos -m "$EMAIL" --keep-until-expiring \
  --cert-name "shiplane-$ID" -d "$ZONE" -d "*.$ZONE" --deploy-hook $HOOK $ARGS 2>&1 | grep -vi 'credentials\|token' || true
L=/etc/letsencrypt/live/shiplane-$ID
sudo test -f $L/fullchain.pem || { echo "Let's Encrypt did not issue the certificate (see above)"; exit 1; }
sudo RENEWED_LINEAGE=$L RENEWED_DOMAINS="$ZONE *.$ZONE" $HOOK
sudo systemctl enable --now certbot.timer >/dev/null 2>&1 || true

echo "== Caddy"
printf '(wild_%s) {\n\ttls /etc/caddy/certs/%s/fullchain.pem /etc/caddy/certs/%s/privkey.pem\n}\n' "$ID" "$ZONE" "$ZONE" | sudo tee $WD/$ZONE.caddy >/dev/null
if ! apply_caddy; then
  sudo rm -f $WD/$ZONE.caddy; wire_caddyfile; sudo $ATTACH; sudo systemctl reload caddy || true
  echo "The certificate was issued but Caddy refused it: nothing changed for your sites"; exit 1
fi
echo "Valid until $(sudo openssl x509 -enddate -noout -in $CERTS/$ZONE/fullchain.pem | cut -d= -f2). Certbot renews it by itself."
echo "WILDCARD_OK"
