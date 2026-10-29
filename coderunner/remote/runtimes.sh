# Node versions a service can use (system-wide locations only: services cannot read /root or /home)
seen=" "
for b in /usr/bin/node /usr/local/bin/node /opt/node*/bin/node /opt/nodejs*/bin/node /usr/local/n/versions/node/*/bin/node /usr/local/lib/nodejs/*/bin/node; do
  [ -x "$b" ] || continue
  r=$(readlink -f "$b"); case "$seen" in *" $r "*) continue;; esac; seen="$seen$r "
  echo "$b $("$b" -v 2>/dev/null)"
done
