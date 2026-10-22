#!/usr/bin/env bash
# setup_baremetal.sh - one-shot root provisioning for the SAQEF bare-metal
# milestone (Ubuntu 26.04, single host). Run with sudo:
#   sudo bash setup_baremetal.sh
# Installs: docker engine + compose, go, fn CLI, faas-cli, hey; grants the
# harness user (SAQEF_USER, default the sudo caller) the docker group; makes the RAPL
# package energy counter readable by that user (the
# harness reads it as the logged-in user, and it is root-only by default).
set -euo pipefail
HUSER="${SAQEF_USER:-${SUDO_USER:-$USER}}"

echo "=== [1/7] apt: docker + compose + go + curl ==="
apt-get update -y
apt-get install -y docker.io docker-compose-v2 golang-go curl

echo "=== [2/7] add $HUSER to docker group ==="
id "$HUSER" >/dev/null 2>&1 && usermod -aG docker "$HUSER" || echo "note: no user '$HUSER'; skip group"
grep docker /etc/group

echo "=== [3/7] RAPL energy_uj readable by the harness user ==="
if [ -f /sys/class/powercap/intel-rapl:0/energy_uj ]; then
  chmod a+r /sys/class/powercap/intel-rapl:0/energy_uj
  echo "current session: chmod a+r done"
  mkdir -p /etc/udev/rules.d
  cat > /etc/udev/rules.d/99-rapl-read.rules <<'EOF'
# Let any user read the RAPL package energy counter (SAQEF harness reads it).
SUBSYSTEM=="powercap", KERNEL=="intel-rapl:0", ACTION=="add", RUN+="/bin/chmod a+r /sys/class/powercap/intel-rapl:0/energy_uj"
EOF
  echo "udev rule installed (persists across reboot)"
else
  echo "WARNING: no /sys/class/powercap/intel-rapl:0/energy_uj - RAPL not exposed on this host"
fi

echo "=== [4/7] start docker ==="
systemctl enable docker || true
systemctl start docker || service docker start || true
docker version --format 'server={{.Server.Version}}' || echo "WARNING: docker not up yet"

echo "=== [5/7] CLI tools via go install ==="
export PATH="$PATH:$(go env GOPATH)/bin"
go install github.com/fnproject/cli@latest
go install github.com/openfaas/faas-cli@latest
go install github.com/rakyll/hey@latest
for b in fn faas-cli hey; do
  src="$(go env GOPATH)/bin/$b"
  if [ -f "$src" ]; then
    ln -sf "$src" "/usr/local/bin/$b"
    echo "linked /usr/local/bin/$b -> $src"
  else
    echo "WARNING: go did not produce $src"
  fi
done

echo "=== [6/7] RAPL read check as $HUSER ==="
sudo -u "$HUSER" python3 -c "print('energy_uj =', open('/sys/class/powercap/intel-rapl:0/energy_uj').read().strip())" 2>&1

echo "=== [7/7] versions ==="
docker --version
docker compose version
fn version
faas-cli version
if command -v hey >/dev/null; then echo "hey: $(hey -v 2>&1 | head -1)"; fi
python3 --version

echo ""
echo "DONE. Next: re-login or run 'newgrp docker' to pick up the docker group."
echo "For this session use:  sg docker -c 'docker ps'"
