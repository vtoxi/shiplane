echo "== SSH: key login only (passwords switched off)"
CONF=/etc/ssh/sshd_config.d/00-shiplane-keys-only.conf
sudo mkdir -p /etc/ssh/sshd_config.d
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' | sudo tee "$CONF" >/dev/null
if ! sudo sshd -t; then echo "sshd rejected the change; undoing it"; sudo rm -f "$CONF"; exit 1; fi
sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd
if sudo sshd -T | grep -qi '^passwordauthentication no'; then echo "done: password login is off; keys still work"; else echo "the setting did not take effect (another file overrides it?)"; sudo rm -f "$CONF"; sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd; exit 1; fi
