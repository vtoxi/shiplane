set -uo pipefail
NAME=__NAME__
[ -f /etc/systemd/system/$NAME.service ] || { echo "this site has no service to run a command in (static, php and docker sites are not supported)"; exit 1; }
# same user, environment file, folder and sandbox as the service itself; stops after 5 minutes
sudo systemd-run --quiet --pipe --wait --collect -p User=app_$NAME -p EnvironmentFile=/etc/$NAME.env \
  -p WorkingDirectory=/srv/$NAME/current -p RuntimeMaxSec=300 -p NoNewPrivileges=true -p ProtectSystem=full -p ProtectHome=true -p PrivateTmp=true \
  /bin/bash -c __CMD__ </dev/null 2>&1
