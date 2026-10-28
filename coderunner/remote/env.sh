set -euo pipefail
NAME=__NAME__; ENVF=/etc/$NAME.env
[ -f "$ENVF" ] || { echo "no environment file for $NAME (static sites have none)"; exit 1; }
setenv() { sudo sed -i "/^$1=/d" "$ENVF"; printf '%s=%s\n' "$1" "$2" | sudo tee -a "$ENVF" >/dev/null; }
unsetenv() { sudo sed -i "/^$1=/d" "$ENVF"; }
__OPS__
sudo systemctl restart $NAME
sleep 1
echo "updated environment and restarted $NAME ($(systemctl is-active $NAME))"
