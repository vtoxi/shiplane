echo "== Swap file (__SIZE_MB__ MB)"
if [ -n "$(swapon --show --noheadings)" ]; then echo "swap is already on"; exit 0; fi
NEED=$(( (__SIZE_MB__ + 5120) * 1024 ))
FREE=$(df --output=avail / | tail -1)
if [ "$FREE" -lt "$NEED" ]; then echo "not enough disk space: the swap file would leave less than 5 GB free"; exit 1; fi
sudo fallocate -l __SIZE_MB__M /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=__SIZE_MB__ status=none
sudo chmod 600 /swapfile
sudo mkswap /swapfile >/dev/null
sudo swapon /swapfile
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
printf 'vm.swappiness=10\n' | sudo tee /etc/sysctl.d/99-shiplane-swap.conf >/dev/null
sudo sysctl -q -p /etc/sysctl.d/99-shiplane-swap.conf
echo "done: $(swapon --show --noheadings | head -1)"
