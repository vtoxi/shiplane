set -euo pipefail
NAME=__NAME__; REL=__REL__
[ -d /srv/$NAME/releases/$REL ] || { echo "release $REL not found"; exit 1; }
sudo ln -sfn /srv/$NAME/releases/$REL /srv/$NAME/current
[ -f /etc/systemd/system/$NAME.service ] && sudo systemctl restart $NAME
echo "active release is now $REL"
