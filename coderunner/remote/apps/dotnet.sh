echo "== .NET SDK __VERSION__"
V=__VERSION__
sudo apt-get install -y -q curl ca-certificates libssl-dev >/dev/null
ICU=$(apt-cache search --names-only '^libicu[0-9]+$' | awk '{print $1}' | sort | tail -1)
[ -z "$ICU" ] || sudo apt-get install -y -q "$ICU" >/dev/null
curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh
sudo mkdir -p /opt/runtimes/dotnet
sudo bash /tmp/dotnet-install.sh --channel "$V" --install-dir /opt/runtimes/dotnet --no-path
rm -f /tmp/dotnet-install.sh
sudo chmod -R a+rX /opt/runtimes
sudo ln -sfn /opt/runtimes/dotnet/dotnet /usr/local/bin/dotnet
echo "installed SDKs: $(/opt/runtimes/dotnet/dotnet --list-sdks | tr '\n' ' ')"
