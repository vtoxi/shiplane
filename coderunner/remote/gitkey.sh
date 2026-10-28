set -euo pipefail
NAME=__NAME__
BASE=/var/lib/shiplane-git; BU=shiplane-build; KEY="$BASE/keys/$NAME"
id "$BU" >/dev/null 2>&1 || sudo useradd -r -m -d "$BASE" -s /usr/sbin/nologin "$BU"
sudo mkdir -p "$BASE/keys"; sudo chown -R "$BU:$BU" "$BASE"; sudo chmod 700 "$BASE/keys"
if [ ! -f "$KEY" ]; then
  sudo -u "$BU" -H ssh-keygen -q -t ed25519 -N '' -C "shiplane-$NAME" -f "$KEY"
fi
echo "PUBKEY $(sudo cat "$KEY.pub")"
