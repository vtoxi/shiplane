echo "== Automatic security updates"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get install -y -q unattended-upgrades >/dev/null
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' | sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null
printf 'Unattended-Upgrade::Automatic-Reboot "false";\n' | sudo tee /etc/apt/apt.conf.d/52shiplane >/dev/null
sudo systemctl enable --now apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true
echo "done: security updates install by themselves; the server never reboots by itself"
