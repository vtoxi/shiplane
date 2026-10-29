echo "== Undo: Shiplane's fail2ban rules removed"
sudo rm -f /etc/fail2ban/jail.d/shiplane.local
sudo systemctl restart fail2ban 2>/dev/null || true
echo "done: Shiplane's jail is removed; fail2ban itself stays installed with its default settings"
