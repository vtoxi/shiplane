echo "== Clock synchronisation"
export DEBIAN_FRONTEND=noninteractive
if ! systemctl list-unit-files 2>/dev/null | grep -q 'systemd-timesyncd\|chrony'; then sudo apt-get install -y -q systemd-timesyncd >/dev/null; fi
sudo timedatectl set-ntp true
sleep 2
echo "synchronised: $(timedatectl show -p NTPSynchronized --value)"
