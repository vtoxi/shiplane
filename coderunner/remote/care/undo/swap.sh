echo "== Undo: swap file removed"
[ -f /swapfile ] || { echo "no /swapfile on this server"; exit 0; }
sudo swapoff /swapfile
sudo rm -f /swapfile
sudo sed -i '\|^/swapfile |d' /etc/fstab
sudo rm -f /etc/sysctl.d/99-shiplane-swap.conf
echo "done: the swap file is gone"
