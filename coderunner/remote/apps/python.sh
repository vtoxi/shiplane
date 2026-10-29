echo "== Python __VERSION__"
V=__VERSION__
sudo apt-get install -y -q curl ca-certificates >/dev/null
if ! command -v uv >/dev/null 2>&1; then
  echo "installing uv (the Python installer from astral.sh)"
  curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi
sudo mkdir -p /opt/runtimes/python
sudo env UV_PYTHON_INSTALL_DIR=/opt/runtimes/python uv python install "$V"
BIN=$(sudo env UV_PYTHON_INSTALL_DIR=/opt/runtimes/python uv python find "$V")
sudo chmod -R a+rX /opt/runtimes
sudo ln -sfn "$BIN" "/usr/local/bin/python$V"
echo "installed: $("/usr/local/bin/python$V" --version) (command: python$V)"
