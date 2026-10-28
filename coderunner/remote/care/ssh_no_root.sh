echo "== SSH: root may not log in with a password"
CONF=/etc/ssh/sshd_config.d/00-shiplane-root.conf
sudo mkdir -p /etc/ssh/sshd_config.d
printf 'PermitRootLogin prohibit-password\n' | sudo tee "$CONF" >/dev/null
if ! sudo sshd -t; then echo "sshd rejected the change; undoing it"; sudo rm -f "$CONF"; exit 1; fi
sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd
echo "done: root can only use a key"
