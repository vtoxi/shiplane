echo "== fail2ban (blocks repeated failed SSH logins)"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get install -y -q fail2ban >/dev/null
printf '[DEFAULT]\nignoreip = 127.0.0.1/8 ::1\n\n[sshd]\nenabled = true\nport = __SSH_PORTS_COMMA__\nmaxretry = 5\nfindtime = 10m\nbantime = 1h\n' | sudo tee /etc/fail2ban/jail.d/shiplane.local >/dev/null
sudo systemctl enable --now fail2ban >/dev/null 2>&1
sudo systemctl restart fail2ban
sleep 2
systemctl is-active fail2ban
echo "done: 5 failed logins in 10 minutes bans the address for 1 hour"
