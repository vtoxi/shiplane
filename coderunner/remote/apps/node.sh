echo "== Node.js __VERSION__"
V=__VERSION__
case "$(uname -m)" in x86_64) A=x64 ;; aarch64|arm64) A=arm64 ;; *) echo "unsupported CPU: $(uname -m)"; exit 1 ;; esac
sudo apt-get install -y -q curl xz-utils ca-certificates >/dev/null
BASE="https://nodejs.org/dist/latest-v$V.x"
LINE=$(curl -fsSL "$BASE/SHASUMS256.txt" | grep "linux-$A.tar.xz\$" | head -1)
[ -n "$LINE" ] || { echo "no Node.js $V build found for this CPU"; exit 1; }
SUM=${LINE%% *}; FILE=${LINE##* }
echo "downloading $FILE"
curl -fsSL -o "/tmp/$FILE" "$BASE/$FILE"
echo "$SUM  /tmp/$FILE" | sha256sum -c -
DEST=/opt/runtimes/node-$V
sudo rm -rf "$DEST"; sudo mkdir -p "$DEST"
sudo tar -xJf "/tmp/$FILE" -C "$DEST" --strip-components=1; rm -f "/tmp/$FILE"
sudo chmod -R a+rX /opt/runtimes
for b in node npm npx; do sudo ln -sfn "$DEST/bin/$b" "/usr/local/bin/$b$V"; done
if [ ! -e /usr/local/bin/node ] && ! command -v node >/dev/null 2>&1; then
  for b in node npm npx; do sudo ln -sfn "$DEST/bin/$b" "/usr/local/bin/$b"; done
  echo "set as the default 'node' command"
fi
echo "installed: $("$DEST/bin/node" -v) (command: node$V)"
