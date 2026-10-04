#!/usr/bin/env bash
# One command for an unattended measurement night (runbook 24.8.5 / 26).
#
#   sudo bash tools/go.sh                     CPU-bound corpus (runbook 24.8)
#   sudo bash tools/go.sh --workload payload  W1 payload I/O night (runbook §28)
#   sudo bash tools/go.sh --arm owlog29       OpenWhisk log-collector A/B (runbook §29)
#   sudo bash tools/go.sh --workload memory   W2 memory-bound night (runbook §30)
#   sudo bash tools/go.sh --workload memory --rerun 2   W2 again as mem2_, with docker hygiene (§30.8)
#   sudo bash tools/go.sh --workload burst    W3 bursty arrivals (runbook §31; docker hygiene always on)
#   sudo bash tools/go.sh --workload burst --amend 31.15   W3: Fn's two legs again, uncapped (§31.15, ~40 min)
#   sudo bash tools/go.sh --workload cold     W4 cold start (runbook §32; ~2.5 h)
#   sudo bash tools/go.sh --workload cold --part 2   W4: only the blocks a power cut left unfinished
#   sudo bash tools/go.sh --status            how far the latest session got
#   sudo bash tools/go.sh --stop              stop the session now and bring the desktop back
#
# If the desktop does not come back: press Ctrl+Alt+F3 (Latitude: Ctrl+Alt+Fn+F3), log in, run
#   sudo bash ~/faas-work/SAQEF/saqef/tools/go.sh --stop
# (or just reboot: the default boot target is still the desktop).
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

WORKLOAD=cpu ACTION=run AMEND="" ARM="" RERUN="" PART=""
while [ $# -gt 0 ]; do
    case "$1" in
        --stop) ACTION=stop ;;
        --status) ACTION=status ;;
        --workload) WORKLOAD="${2:-}"; shift ;;
        --workload=*) WORKLOAD="${1#*=}" ;;
        --amend) AMEND="${2:-}"; shift ;;
        --arm) ARM="${2:-}"; shift ;;
        --rerun) RERUN="${2:-}"; shift ;;
        --part) PART="${2:-}"; shift ;;
        *) echo "unknown option: $1 (use --workload cpu|payload|memory|burst|cold, --part N, --amend ID, --arm ID, --rerun N, --status, --stop)" >&2; exit 2 ;;
    esac
    shift
done
case "$WORKLOAD" in
    cpu) MAX_H=4 ;;        # hard ceiling; a CPU session takes ~2 h
    payload) MAX_H=6 ;;    # 36 legs instead of 15; expected ~3.5-4 h
    memory) MAX_H=6 ;;     # 24 legs + 8 probe deploys + per-leg swaps; expected ~3.5 h (runbook §30.2)
    burst) MAX_H=4 ;;      # 12 legs; expected ~2 h (runbook §31.2)
    cold) MAX_H=4 ;;       # 8 legs, ~6 min Knative reset per cold run; expected ~2.5 h (runbook §32)
    *) echo "unknown --workload '$WORKLOAD' (cpu|payload|memory|burst|cold)" >&2; exit 2 ;;
esac
# Amendment (runbook §28.8 style): a few named legs under their own prefix.
AMEND_ARGS=() SESS_NAME="$(case "$WORKLOAD" in payload) echo payload ;; memory) echo mem ;; burst) echo burst ;; cold) echo cold ;; *) echo final ;; esac)"
if [ -n "$AMEND" ]; then
    AMEND_ARGS=(--amend "$AMEND"); MAX_H=2; SESS_NAME="${SESS_NAME}_amend${AMEND/./_}"
fi
# Arm (runbook §29 style): a named comparison under its own prefix; reuses AMEND_ARGS to reach run_final.
if [ -n "$ARM" ]; then
    AMEND_ARGS=(--arm "$ARM"); MAX_H=2; SESS_NAME="$ARM"
fi
# W4 continuation after a power cut (runbook §32): only the blocks without a complete result.
if [ -n "$PART" ]; then
    AMEND_ARGS=(--part "$PART"); [ "$PART" = 1 ] || SESS_NAME="cold_p${PART}"
fi
# Whole-session rerun (runbook §30.8 style): W2 only, prefix mem<N>_, run_final adds --hygiene.
if [ -n "$RERUN" ]; then
    AMEND_ARGS=(--rerun "$RERUN"); SESS_NAME="mem${RERUN}"
fi
if [ "$ACTION" = stop ]; then
    systemctl stop "$UNIT" 2>/dev/null || true
    systemctl stop saqef-guard.timer 2>/dev/null || true
    systemctl stop saqef-screen 2>/dev/null || true
    systemctl start display-manager
    echo "stopped; desktop restored. Partial results: sudo bash tools/go.sh --status"
    exit 0
fi
if [ "$ACTION" = status ]; then
    S=$(ls -td "$REPO"/results/final_session "$REPO"/results/payload_session "$REPO"/results/payload_amend*_session "$REPO"/results/owlog*_session "$REPO"/results/mem_session "$REPO"/results/mem[2-9]_session "$REPO"/results/burst_session "$REPO"/results/burst_amend*_session "$REPO"/results/cold_session "$REPO"/results/cold_p*_session 2>/dev/null | head -1)
    echo "== service"; systemctl status "$UNIT" --no-pager 2>/dev/null | sed -n 1,5p || echo "  not running"
    [ -n "$S" ] || { echo "  no session yet"; exit 0; }
    echo "== session: $(basename "$S")"
    if [ -f "$S/DONE" ]; then echo "== FINISHED: $(cat "$S/DONE")"; fi
    echo "== legs so far"
    if [ -f "$S/checkpoint.tsv" ]; then
        column -t -s $'\t' "$S/checkpoint.tsv"
    else echo "  none yet"; fi
    echo "== last log lines"; tail -n 8 "$S/session.log" 2>/dev/null || echo "  no log yet"
    exit 0
fi

say() { echo ">>> $*"; }

if systemctl is-active --quiet "$UNIT"; then
    echo "A session is already running. Check it with: sudo bash tools/go.sh --status"; exit 1
fi
systemctl reset-failed "$UNIT" 2>/dev/null || true

# 0. a session that died hard can leave a workload's handlers (payload, mem_*) swapped in;
#    put them back. Any handler that matches a file under workloads/ counts.
for f in hello/func.py OF_FUNCTION/handler.py OF_FUNCTION/index.py KNATIVE_FUNCTION/app.py OW_FUNCTION/hello.py; do
    hit=""
    for w in "$REPO"/workloads/*/; do
        [ -f "$w$f" ] && cmp -s "$REPO/$f" "$w$f" && hit=1
    done
    if [ -n "$hit" ]; then
        say "restoring handlers left swapped by an earlier session"
        bash "$REPO/tools/workload.sh" restore
        break
    fi
done

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
# unix form: date -d cannot parse zone abbreviations like PKT
dk=$(systemctl show docker --timestamp=unix -p ActiveEnterTimestamp --value | tr -d @)
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
out=$(bash "$REPO/tools/run_final.sh" --check --workload "$WORKLOAD" "${AMEND_ARGS[@]}" 2>&1)
real=$(echo "$out" | grep "PROBLEM:" | grep -v -e "graphical session" -e "agent process")
if [ -n "$real" ]; then
    echo "$out"
    echo
    echo "STOPPED before measuring anything. Fix these first (or paste them to Claude):"
    echo "$real"
    exit 1
fi
say "pre-flight OK (desktop/agents are handled next)"

# 4. launch, with two more safety nets besides run_final.sh's own exit trap:
#    - RuntimeMaxSec: systemd kills the session if it runs past MAX_H (a hang),
#      which fires the trap and restores the desktop;
#    - saqef-guard: an independent timer that restores the desktop 15 min after
#      that, even if the session process is wedged beyond a clean kill.
systemctl stop saqef-guard.timer 2>/dev/null || true
systemctl reset-failed saqef-guard.service 2>/dev/null || true
systemd-run --unit saqef-guard --on-active="$((MAX_H * 60 + 15))min" \
    systemctl start display-manager >/dev/null
systemd-run --unit "$UNIT" -p RuntimeMaxSec="${MAX_H}h" \
    systemd-inhibit --what=sleep:idle:handle-lid-switch --why="SAQEF $WORKLOAD session" \
    bash "$REPO/tools/run_final.sh" --workload "$WORKLOAD" "${AMEND_ARGS[@]}" >/dev/null
say "session ($WORKLOAD) launched as service '$UNIT'"

# 5. leave the desktop
echo
echo "  Close Claude Code / opencode / browser now. Keep the laptop on AC, lid open."
echo "  The screen switches to a text status page (tty8) in 60 s -- that is NOT a hang;"
echo "  do not press the power button (it kills the session). Press Ctrl+C to stay on the desktop"
echo "  (the session then waits up to 20 min for the desktop to close, then gives up)."
echo "  When the desktop comes back by itself (at most ${MAX_H} h 15 min), run:"
echo "      sudo bash tools/go.sh --status"
echo "  Stuck in text mode? Ctrl+Alt+F3 (Latitude: Ctrl+Alt+Fn+F3), log in, then: sudo bash $REPO/tools/go.sh --stop"
for s in $(seq 60 -10 10); do echo "  ... $s s"; sleep 10; done
# Status page first, as its own system service: go.sh runs in a desktop terminal
# that dies with gdm, so anything after "stop display-manager" never runs (2 Oct:
# the page never appeared and the boot splash sat there for 13 min). The unit
# waits for gdm to go, then switches to tty8 itself.
systemctl stop saqef-screen 2>/dev/null || true
systemctl reset-failed saqef-screen 2>/dev/null || true
systemd-run --unit saqef-screen bash "$REPO/tools/screen_status.sh" "$SESS_NAME" >/dev/null
# Only stop the display manager. NOT "systemctl isolate multi-user.target": isolate
# also stops every unit that target does not pull in -- including the transient
# saqef-final service just launched (that killed the 2 Oct session after 60 s).
systemctl stop display-manager
