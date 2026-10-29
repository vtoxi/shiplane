#!/bin/sh
# Opens a private SSH tunnel to the app running on your server, and opens its sign-in page.
#   SERVER=203.0.113.10 SSHUSER=deploy KEY=~/.ssh/id_ed25519 ./connect-server.sh
: "${SERVER:?set SERVER to your server address}"
: "${SSHUSER:?set SSHUSER to your server user}"
KEY="${KEY:-$HOME/.ssh/id_ed25519}"
URL="http://127.0.0.1:8765/"
(command -v xdg-open >/dev/null && xdg-open "$URL") || (command -v open >/dev/null && open "$URL") || echo "Open: $URL"
echo "Tunnel open. Press Ctrl+C to disconnect."
exec ssh -i "$KEY" -N -L 8765:127.0.0.1:8765 "$SSHUSER@$SERVER"
