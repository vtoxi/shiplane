#!/bin/sh
# First start: make the folders and an SSH key of its own (the container never uses your personal key). Then run the app.
set -eu
mkdir -p "$CODERUNNER_DATA" "$CODERUNNER_CONFIG" "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
if [ ! -f "$HOME/.ssh/id_ed25519" ]; then
  ssh-keygen -q -t ed25519 -N '' -C "shiplane-container" -f "$HOME/.ssh/id_ed25519"
  echo "=============================================================="
  echo " A new SSH key was created for this container."
  echo " Add this public key to the server's ~/.ssh/authorized_keys:"
  echo
  cat "$HOME/.ssh/id_ed25519.pub"
  echo "=============================================================="
fi
exec "$@"
