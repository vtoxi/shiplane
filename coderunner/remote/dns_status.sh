IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
echo "IP $IP"
if command -v nsd >/dev/null 2>&1; then echo "NSD $(systemctl is-active nsd 2>/dev/null || true)"; else echo "NSD missing"; fi
if ss -lnu 2>/dev/null | grep -q ":53 " && ss -lnu | grep ":53 " | grep -qv "127.0.0"; then echo "LISTEN53 yes"; else echo "LISTEN53 no"; fi
if command -v ufw >/dev/null 2>&1 && sudo ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "UFW active"
  RULES=$(sudo ufw status 2>/dev/null)
  for p in 53 80 443; do if echo "$RULES" | grep -qE "^$p(/(tcp|udp))? +ALLOW"; then echo "FW$p allowed"; else echo "FW$p blocked"; fi; done
else
  echo "UFW inactive"
  for p in 53 80 443; do echo "FW$p allowed"; done
fi
