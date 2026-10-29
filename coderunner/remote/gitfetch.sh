set -euo pipefail
NAME=__NAME__; REPO=__REPO__; BRANCH=__BRANCH__; BUILD=__BUILD__; OUTPUT=__OUTPUT__; BUILDCMD=__BUILD_CMD__; USE_KEY=__USE_KEY__
BASE=/var/lib/shiplane-git; BU=shiplane-build
WORK="$BASE/$NAME"; KEY="$BASE/keys/$NAME"
export DEBIAN_FRONTEND=noninteractive
echo "== preparing"
id "$BU" >/dev/null 2>&1 || sudo useradd -r -m -d "$BASE" -s /usr/sbin/nologin "$BU"
command -v git >/dev/null 2>&1 || sudo apt-get install -y -q git >/dev/null
sudo mkdir -p "$BASE/keys"; sudo chown -R "$BU:$BU" "$BASE"; sudo chmod 700 "$BASE/keys"
SSHCMD="ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
[ "$USE_KEY" = true ] && SSHCMD="$SSHCMD -i $KEY -o IdentitiesOnly=yes"
as_build() { sudo -u "$BU" -H env HOME="$BASE" GIT_SSH_COMMAND="$SSHCMD" GIT_TERMINAL_PROMPT=0 "$@"; }
echo "== getting the code ($BRANCH)"
if [ -d "$WORK/.git" ]; then
  as_build git -C "$WORK" remote set-url origin "$REPO"
  as_build git -C "$WORK" fetch --depth 1 origin "$BRANCH"
  as_build git -C "$WORK" reset --hard FETCH_HEAD
  as_build git -C "$WORK" clean -fdx
else
  sudo rm -rf "$WORK"
  as_build git clone --depth 1 --branch "$BRANCH" -- "$REPO" "$WORK"
fi
echo "COMMIT $(as_build git -C "$WORK" rev-parse HEAD)"
echo "MESSAGE $(as_build git -C "$WORK" log -1 --pretty=%s | head -c 120)"
if [ "$BUILD" = npm ]; then
  command -v npm >/dev/null 2>&1 || { echo "npm is not installed: install Node.js from the Apps page first"; exit 1; }
  echo "== building (npm)"
  as_build bash -c 'cd "$1" && if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi && npm run build' _ "$WORK"
fi
if [ "$BUILD" = commands ]; then
  echo "== building: $BUILDCMD"
  as_build bash -c 'cd "$1" && eval "$2"' _ "$WORK" "$BUILDCMD" </dev/null || { echo "the build command failed"; exit 1; }
fi
SRC="$WORK"; [ -z "$OUTPUT" ] || SRC="$WORK/$OUTPUT"
# the build ran as another user, so look with sudo
sudo test -d "$SRC" || { echo "the output folder '$OUTPUT' does not exist in the repository after the build"; sudo ls -1 "$WORK" | head -20; exit 1; }
# Angular (17+) writes the site into a browser/ subfolder next to server/: publish that one
if ! sudo test -f "$SRC/index.html" && sudo test -f "$SRC/browser/index.html"; then SRC="$SRC/browser"; echo "using the browser/ folder of the build output"; fi
echo "== packing"
sudo tar -czf "/tmp/$NAME.tgz" --exclude=.git --exclude=node_modules -C "$SRC" .
echo "packed $(du -h "/tmp/$NAME.tgz" | cut -f1)"
