set -euo pipefail
NAME=__NAME__; HOST=__HOST__; PORT=__PORT__; CPORT=__CPORT__; DOCKERFILE=__DOCKERFILE__
ENVF=/etc/$NAME.env; CNAME="shiplane-$NAME"
command -v docker >/dev/null 2>&1 || { echo "Docker is not installed on this server: install it from the Apps page first"; exit 1; }
for f in /etc/caddy/sites/*.caddy; do
  [ -e "$f" ] || continue
  o=$(basename "$f" .caddy); [ "$o" = "$NAME" ] && continue
  if grep -qxF -e "http://$HOST {" -e "http://$HOST, https://$HOST {" "$f"; then echo "hostname $HOST is already used by site '$o'"; exit 1; fi
  if grep -qE "reverse_proxy 127\.0\.0\.1:$PORT( \{)?\$" "$f"; then echo "port $PORT is already used by site '$o'"; exit 1; fi
done
REL=/srv/$NAME/releases/$(date +%Y%m%d-%H%M%S)
TAG="shiplane/$NAME:$(basename "$REL")"

echo "unpacking release $(basename "$REL")"
sudo mkdir -p "$REL"
sudo tar -xzf /tmp/$NAME.tgz -C "$REL"; rm -f /tmp/$NAME.tgz
sudo chown -R root:root "$REL"
[ -f "$REL/$DOCKERFILE" ] || { echo "there is no $DOCKERFILE in the repository"; sudo rm -rf "$REL"; exit 1; }

echo "building the image (the first build can take several minutes)"
if ! sudo docker build -t "$TAG" -f "$REL/$DOCKERFILE" "$REL"; then echo "the image could not be built"; sudo rm -rf "$REL"; exit 1; fi

PREV=$(readlink -f /srv/$NAME/current 2>/dev/null || true)
PREVTAG=""; [ -n "$PREV" ] && [ -d "$PREV" ] && PREVTAG="shiplane/$NAME:$(basename "$PREV")"
[ -f "$ENVF" ] || sudo install -m 600 -o root -g root /dev/null "$ENVF"
setenv() { sudo sed -i "/^$1=/d" "$ENVF"; printf '%s=%s\n' "$1" "$2" | sudo tee -a "$ENVF" >/dev/null; }
__ENV__

run_image() {
  sudo docker rm -f "$CNAME" >/dev/null 2>&1 || true
  sudo docker run -d --name "$CNAME" --restart unless-stopped -p "127.0.0.1:$PORT:$CPORT" --env-file "$ENVF" -e "PORT=$CPORT" "$1" >/dev/null
}
echo "starting the container"
run_image "$TAG"
sudo ln -sfn "$REL" /srv/$NAME/current

sudo mkdir -p /etc/caddy/sites /etc/deploy-meta
grep -q '^import sites/\*.caddy' /etc/caddy/Caddyfile || echo 'import sites/*.caddy' | sudo tee -a /etc/caddy/Caddyfile >/dev/null
sudo tee /etc/deploy-meta/$NAME.json >/dev/null <<'META'
__META__
META
sudo tee /etc/caddy/sites/$NAME.caddy >/dev/null <<'SITE'
__SITE__
SITE
if ! sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
  echo "Caddy config invalid, removing this site's file"; sudo rm -f /etc/caddy/sites/$NAME.caddy; exit 1
fi
sudo systemctl reload caddy

# health gate: the container must keep running and answer below HTTP 500 within ~30 s, else the previous image comes back
OK=0; CODE=000
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  sleep 2
  CODE=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$PORT/" || true)
  if [ "$(sudo docker inspect -f '{{.State.Running}}' "$CNAME" 2>/dev/null || echo false)" = true ] && [ "$CODE" != 000 ] && [ "$CODE" -lt 500 ]; then OK=1; break; fi
done
if [ "$OK" = 0 ]; then
  echo "HEALTH CHECK FAILED: container $(sudo docker inspect -f '{{.State.Status}}' "$CNAME" 2>/dev/null || echo missing), HTTP $CODE"
  sudo docker logs --tail 30 "$CNAME" 2>&1 || true
  if [ -n "$PREVTAG" ] && sudo docker image inspect "$PREVTAG" >/dev/null 2>&1; then
    echo "rolling back to release $(basename "$PREV")"
    run_image "$PREVTAG"; sudo ln -sfn "$PREV" /srv/$NAME/current
    echo "ROLLED BACK to $(basename "$PREV")"
    exit 3
  fi
  echo "there is no earlier release to go back to"
  exit 4
fi

# keep the three newest releases and their images
ls -1dt /srv/$NAME/releases/* | tail -n +4 | while read -r old; do
  sudo docker rmi "shiplane/$NAME:$(basename "$old")" >/dev/null 2>&1 || true
  sudo rm -rf "$old"
done
sudo docker image prune -f >/dev/null 2>&1 || true
echo "RESULT $HOST -> HTTP $CODE (release $(basename "$REL"))"
echo "container: $(sudo docker inspect -f '{{.State.Status}}' "$CNAME")"
