set -euo pipefail
# Installs a mail server on Ubuntu/Debian: Postfix (SMTP), Dovecot (IMAP, POP3, delivery), Rspamd (spam filter + DKIM signing), and the folders and TLS certificate they need.
# Safe to run again. Settings are written afterwards by the Mail page (mailctl init). It does not open firewall ports or change DNS.
export DEBIAN_FRONTEND=noninteractive
HOST=__HOST__
sudo -n true 2>/dev/null || { echo "this login cannot use sudo without a password"; exit 1; }
. /etc/os-release
case "${ID:-}" in ubuntu|debian) ;; *) echo "automatic setup supports Ubuntu and Debian only (this is ${PRETTY_NAME:-unknown})"; exit 1;; esac
if ss -ltn 2>/dev/null | grep -qE '[:.](25|587|465|993|995|143|110)\s'; then
  OTHER=$(sudo ss -ltnp 2>/dev/null | grep -E '[:.](25|587|465|993|995|143|110)\s' | grep -vE 'master|dovecot|postfix' || true)
  [ -z "$OTHER" ] || { echo "another program already uses a mail port:"; echo "$OTHER"; exit 1; }
fi
echo "== packages"
echo "postfix postfix/main_mailer_type select Internet Site" | sudo debconf-set-selections
echo "postfix postfix/mailname string $HOST" | sudo debconf-set-selections
sudo apt-get update -q >/dev/null
sudo apt-get install -y -q postfix dovecot-core dovecot-imapd dovecot-pop3d dovecot-lmtpd dovecot-sieve rspamd openssl ca-certificates >/dev/null
echo "== mail user and folders"
getent group vmail >/dev/null || sudo groupadd -g 5000 vmail
id vmail >/dev/null 2>&1 || sudo useradd -u 5000 -g 5000 -d /var/vmail -s /usr/sbin/nologin vmail
sudo install -d -m 0700 -o vmail -g vmail /var/vmail
sudo install -d -m 0755 /etc/postfix/shiplane /etc/dovecot/shiplane
sudo install -d -m 0700 /etc/shiplane-mail
getent group ssl-cert >/dev/null || sudo groupadd -r ssl-cert
sudo usermod -aG ssl-cert postfix 2>/dev/null || true
sudo install -d -m 0750 -o root -g ssl-cert /etc/shiplane-mail/tls
if [ ! -s /etc/shiplane-mail/tls/fullchain.pem ]; then
  echo "== temporary certificate (replaced by a real one when $HOST has an address Caddy can get a certificate for)"
  sudo openssl req -x509 -newkey rsa:2048 -nodes -days 60 -subj "/CN=$HOST" -keyout /etc/shiplane-mail/tls/privkey.pem -out /etc/shiplane-mail/tls/fullchain.pem >/dev/null 2>&1
fi
sudo chgrp ssl-cert /etc/shiplane-mail/tls/*.pem; sudo chmod 0640 /etc/shiplane-mail/tls/privkey.pem; sudo chmod 0644 /etc/shiplane-mail/tls/fullchain.pem
echo "== certificate sync from Caddy"
sudo tee /usr/local/sbin/shiplane-mail-cert-sync >/dev/null <<'SYNC'
#!/bin/bash
# Copies the Let's Encrypt certificate Caddy obtained for the mail host into place and reloads the mail services when it changed.
HOST="$(python3 -c "import json;print(json.load(open('/etc/shiplane-mail/mail.json'))['hostname'])" 2>/dev/null)"
[ -n "$HOST" ] || exit 0
CRT=$(find /var/lib/caddy /root/.local/share/caddy /home/*/.local/share/caddy -path "*certificates*/$HOST/$HOST.crt" 2>/dev/null | head -1)
[ -n "$CRT" ] || exit 0
KEY="${CRT%.crt}.key"; DST=/etc/shiplane-mail/tls
if ! cmp -s "$CRT" $DST/fullchain.pem || ! cmp -s "$KEY" $DST/privkey.pem; then
  install -m 0644 "$CRT" $DST/fullchain.pem && install -m 0640 -g ssl-cert "$KEY" $DST/privkey.pem
  systemctl reload postfix dovecot 2>/dev/null || { postfix reload 2>/dev/null; doveadm reload 2>/dev/null; }
  echo "mail certificate updated for $HOST"
fi
SYNC
sudo chmod 0755 /usr/local/sbin/shiplane-mail-cert-sync
printf '17 */6 * * * root /usr/local/sbin/shiplane-mail-cert-sync >/dev/null 2>&1\n' | sudo tee /etc/cron.d/shiplane-mail-cert >/dev/null
if [ -d /etc/caddy/sites ] && ! grep -rqs -F "$HOST" /etc/caddy/sites /etc/caddy/Caddyfile 2>/dev/null; then
  echo "== address for $HOST in Caddy (so it gets a real certificate)"
  printf '%s {\n\trespond "Mail server" 200\n}\n' "$HOST" | sudo tee /etc/caddy/sites/shiplane-mail.caddy >/dev/null
  if sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then sudo systemctl reload caddy; else sudo rm -f /etc/caddy/sites/shiplane-mail.caddy; echo "(Caddy would not accept it: add $HOST to Caddy by hand to get a certificate)"; fi
fi
echo "== done: packages and folders are ready"
