echo "== Undo: root password rule removed"
sudo rm -f /etc/ssh/sshd_config.d/00-shiplane-root.conf
sudo sshd -t
sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd
echo "done: the setting Shiplane added is removed (the server's own SSH settings apply again)"
