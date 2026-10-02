#!/usr/bin/env bash
# One command for an unattended measurement night (runbook 24.8.5 / 26).
#
#   sudo bash tools/go.sh            set up, check, launch, then drop the desktop
#   sudo bash tools/go.sh --status   how far it got (run after the desktop comes back)
#
# What it does, so you don't have to remember it:
#   1. writes /etc/docker/daemon.json (64k log cap) and restarts docker -- only if needed
#   2. waits for the Knative pods to come back
#   3. runs the pre-flight; stops with a plain message if anything other than
#      "desktop still up" / "agent still open" is wrong
#   4. launches tools/run_final.sh as a system service (survives the desktop going away,
#      laptop will not sleep), which itself waits until the desktop and agents are gone
#   5. after a 60 s countdown, switches to text mode. The desktop comes back by itself
#      when the session finishes (~2 h).
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ "$(id -u)" -eq 0 ] || exec sudo bash "$0" "$@"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
UNIT=saqef-final
WANT='{ "log-driver": "json-file", "log-opts": { "max-size": "64k", "max-file": "1" } }'

if [ "${1:-}" = "--status" ]; then
    echo "== service"; systemctl status "$UNIT" --no-pager 2>/dev/null | sed -n 1,5p || echo "  not running"
    if [ -f "$REPO/results/final_session/DONE" ]; then echo "== FINISHED: $(cat "$REPO/results/final_session/DONE")"; fi
    echo "== legs so far"
    if [ -f "$REPO/results/final_session/checkpoint.tsv" ]; then
        column -t -s $'\t' "$REPO/results/final_session/checkpoint.tsv"
    else echo "  none yet"; fi
    echo "== last log lines"; tail -n 8 "$REPO/results/final_session/session.log" 2>/dev/null || echo "  no log yet"
    exit 0
fi

say() { echo ">>> $*"; }

if systemctl is-active --quiet "$UNIT"; then
    echo "A session is already running. Check it with: sudo bash tools/go.sh --status"; exit 1
fi
systemctl reset-failed "$UNIT" 2>/dev/null || true

# 1. docker log cap (idempotent)
if python3 - <<'PY'
import json, sys
try:
    o = json.load(open("/etc/docker/daemon.json")).get("log-opts", {})
    sys.exit(0 if o.get("max-size") == "64k" and str(o.get("max-file")) == "1" else 1)
except Exception:
    sys.exit(1)
PY
then
    say "docker log cap already set"
else
    [ -f /etc/docker/daemon.json ] && cp /etc/docker/daemon.json "/etc/docker/daemon.json.bak.$(date +%s)"
    mkdir -p /etc/docker
    echo "$WANT" > /etc/docker/daemon.json
    say "docker log cap written; restarting docker (Knative pods restart too)"
    systemctl restart docker
fi
# a restart is also needed if docker was started before the file was written
dj=$(stat -c %Y /etc/docker/daemon.json)
dk=$(date -d "$(systemctl show docker -p ActiveEnterTimestamp --value)" +%s 2>/dev/null || echo 0)
if [ "$dk" -le "$dj" ]; then say "restarting docker to load the log cap"; systemctl restart docker; fi

# 2. wait for Knative
say "waiting for Knative pods (up to 15 min)"
for i in $(seq 1 90); do
    pods=$(kubectl get pods -n knative-serving --no-headers 2>/dev/null)
    bad=$(echo "$pods" | awk 'NF{split($2,a,"/"); if (a[1]!=a[2] || $3!="Running") print $1}')
    [ -n "$pods" ] && [ -z "$bad" ] && { say "Knative ready"; break; }
    sleep 10
    [ "$i" = 90 ] && { echo "Knative did not come back in 15 min:"; echo "$pods"; exit 1; }
done

# 3. pre-flight: everything except desktop/agents must already be fine
out=$(bash "$REPO/tools/run_final.sh" --check 2>&1)
real=$(echo "$out" | grep "PROBLEM:" | grep -v -e "graphical session" -e "agent process")
if [ -n "$real" ]; then
    echo "$out"
    echo
    echo "STOPPED before measuring anything. Fix these first (or paste them to Claude):"
    echo "$real"
    exit 1
fi
say "pre-flight OK (desktop/agents are handled next)"

# 4. launch
systemd-run --unit "$UNIT" \
    systemd-inhibit --what=sleep:idle:handle-lid-switch --why="SAQEF final corpus" \
    bash "$REPO/tools/run_final.sh" >/dev/null
say "session launched as service '$UNIT'"

# 5. leave the desktop
echo
echo "  Close Claude Code / opencode / browser now. Keep the laptop on AC, lid open."
echo "  The screen switches to text mode in 60 s. Press Ctrl+C to stay on the desktop"
echo "  (the session then waits up to 20 min for the desktop to close, then gives up)."
echo "  When the desktop comes back by itself (~2 h), run:  sudo bash tools/go.sh --status"
for s in $(seq 60 -10 10); do echo "  ... $s s"; sleep 10; done
systemctl isolate multi-user.target
