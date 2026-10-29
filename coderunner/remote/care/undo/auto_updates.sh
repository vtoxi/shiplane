echo "== Undo: automatic updates off"
sudo rm -f /etc/apt/apt.conf.d/52shiplane
printf 'APT::Periodic::Update-Package-Lists "0";\nAPT::Periodic::Unattended-Upgrade "0";\n' | sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null
echo "done: security updates no longer install by themselves"
