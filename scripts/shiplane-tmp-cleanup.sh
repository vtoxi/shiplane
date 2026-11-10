#!/usr/bin/env bash
# Daily cleanup of temporary storage on the server: /tmp and /var/tmp.
#
#   sudo ./shiplane-tmp-cleanup.sh              # clean now
#   sudo ./shiplane-tmp-cleanup.sh --dry-run    # list what would go, delete nothing
#   sudo ./shiplane-tmp-cleanup.sh --install    # run it every day at 03:30 (/etc/cron.d/shiplane-tmp-cleanup)
#   sudo ./shiplane-tmp-cleanup.sh --uninstall
#
# Removes regular files and symlinks that nobody has read or changed for DAYS days (default 3), then empty folders left behind.
# Never touches: sockets, other file types, systemd-private-*, .X11-unix, .ICE-unix, tmux-*, ssh-* agent folders, or anything a
# running process has open. Set DAYS=7 to be gentler. Writes one line per run to /var/log/shiplane-tmp-cleanup.log.
set -uo pipefail
DAYS="${DAYS:-3}"
DIRS=(/tmp /var/tmp)
CRON=/etc/cron.d/shiplane-tmp-cleanup
LOG=/var/log/shiplane-tmp-cleanup.log
SELF="$(readlink -f "$0")"

case "${1:-}" in
  --install)
    [ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
    install -m 0755 "$SELF" /usr/local/sbin/shiplane-tmp-cleanup
    printf '30 3 * * * root DAYS=%s /usr/local/sbin/shiplane-tmp-cleanup >/dev/null 2>&1\n' "$DAYS" > "$CRON"
    chmod 0644 "$CRON"
    echo "installed: runs daily at 03:30, removes temp files untouched for $DAYS days ($CRON)"; exit 0;;
  --uninstall)
    rm -f "$CRON" /usr/local/sbin/shiplane-tmp-cleanup; echo "removed"; exit 0;;
  --dry-run) DRY=1;;
  "") DRY=0;;
  *) echo "unknown option: $1"; exit 1;;
esac

[[ "$DAYS" =~ ^[0-9]+$ ]] && [ "$DAYS" -ge 1 ] || { echo "DAYS must be a whole number, 1 or more"; exit 1; }
KEEP=( -name 'systemd-private-*' -o -name '.X11-unix' -o -name '.ICE-unix' -o -name 'tmux-*' -o -name 'ssh-*' -o -name 'snap-private-tmp' )
before=$(df -Pk "${DIRS[@]}" 2>/dev/null | awk 'NR>1{s+=$3} END{print s+0}')
count=0
for d in "${DIRS[@]}"; do
  [ -d "$d" ] || continue
  while IFS= read -r -d '' f; do
    # skip anything a running process holds open
    if command -v fuser >/dev/null 2>&1 && fuser -s "$f" 2>/dev/null; then continue; fi
    if [ "$DRY" = 1 ]; then echo "would remove $f"; else rm -f -- "$f"; fi
    count=$((count + 1))
  done < <(find "$d" -xdev \( "${KEEP[@]}" \) -prune -o \( -type f -o -type l \) -atime +"$DAYS" -mtime +"$DAYS" -print0 2>/dev/null)
  [ "$DRY" = 1 ] || find "$d" -xdev -mindepth 1 \( "${KEEP[@]}" \) -prune -o -type d -empty -mtime +"$DAYS" -exec rmdir -- {} + 2>/dev/null
done
after=$(df -Pk "${DIRS[@]}" 2>/dev/null | awk 'NR>1{s+=$3} END{print s+0}')
msg="$(date -Is) $([ "$DRY" = 1 ] && echo dry-run || echo cleaned) $count file(s) older than $DAYS days, freed about $(( (before - after) / 1024 )) MB"
echo "$msg"
[ "$DRY" = 1 ] || { echo "$msg" >> "$LOG" 2>/dev/null || true; }
