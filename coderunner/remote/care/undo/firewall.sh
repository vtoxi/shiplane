echo "== Undo: firewall off"
sudo ufw --force disable >/dev/null
echo "done: the firewall is switched off. Its rules are kept, so turning it on again restores them"
