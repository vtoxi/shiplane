SITE=__SITE__; HOST=__HOST__; ROOT=__ROOT__; PHP=__PHP__; BA_USER=__BA_USER__; BA_PW=__BA_PW__
export DEBIAN_FRONTEND=noninteractive

check_target() {   # never overwrite: the folder must be empty and the address and name unused
  if [ -d "$ROOT" ] && [ -n "$(ls -A "$ROOT" 2>/dev/null)" ]; then echo "The folder $ROOT is not empty. Choose an empty folder so nothing is overwritten."; exit 1; fi
  for f in /etc/caddy/sites/*.caddy; do
    [ -e "$f" ] || continue
    if grep -qxF "http://$HOST {" "$f"; then echo "The address $HOST is already used by the site $(basename "$f" .caddy)"; exit 1; fi
  done
  if [ -e "/etc/caddy/sites/$SITE.caddy" ]; then echo "A site called $SITE already exists"; exit 1; fi
  command -v caddy >/dev/null 2>&1 || { echo "Caddy is not installed on this server"; exit 1; }
}

auth_block() {     # optional password protection in front of the whole site
  if [ -n "$BA_USER" ]; then
    echo "  basic_auth {"
    echo "    $BA_USER $(caddy hash-password --plaintext "$BA_PW")"
    echo "  }"
  fi
}

write_site() {     # stdin: the lines of the Caddy site block
  sudo mkdir -p /etc/caddy/sites /etc/deploy-meta
  grep -q '^import sites/\*.caddy' /etc/caddy/Caddyfile || echo 'import sites/*.caddy' | sudo tee -a /etc/caddy/Caddyfile >/dev/null
  { echo "http://$HOST {"; cat; echo "}"; } | sudo tee "/etc/caddy/sites/$SITE.caddy" >/dev/null
  if ! sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    echo "Caddy rejected the new site; removing it"; sudo rm -f "/etc/caddy/sites/$SITE.caddy"; exit 1
  fi
  sudo systemctl reload caddy
}

write_meta() {     # $1 app  $2 version
  printf '{"name":"%s","hostname":"%s","type":"php","app":"%s","version":"%s","path":"%s","php":"%s"}\n' "$SITE" "$HOST" "$1" "$2" "$ROOT" "$PHP" | sudo tee "/etc/deploy-meta/$SITE.json" >/dev/null
}
