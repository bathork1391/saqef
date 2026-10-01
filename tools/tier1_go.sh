#!/usr/bin/env bash
#
# tier1_go.sh -- get the bare-metal box ready for Tier-1 Experiment B and stop.
#
# WHY THIS EXISTS (2026-10-01):
# The previous wrapper (run_tier1_quiet.sh) tried to REPAIR a clock-skewed k3s
# cert by moving the leaf aside and restarting. That cannot work, and it made
# things worse. What is actually happening:
#
#   * k3s backdates its leaf certs by exactly 1h (notBefore = start - 1h).
#   * At the 15:21 boot the clock was ~1h12m FAST (NTP had not corrected yet),
#     so k3s minted notBefore=09:21:15Z.
#   * NTP then pulled the clock BACKWARDS to 09:09Z.
#   * The cert is now stamped in the future, so k3s rejects its own handshake
#     and sits in 'activating' forever.
#
# The fix is to WAIT. The cert becomes valid on its own and k3s finishes
# starting. No cert surgery, no datastore writes, no risk to cluster state.
#
# This script deliberately does NOT touch certificates. If the wait below
# expires, that is a DIFFERENT fault and the honest move is to read the log,
# not to start deleting CA-signed material.
#
# It also does NOT launch the 3h measurement. It stops once the substrate is
# verified, so you can eyeball the state, quit your agents, and launch the
# bench from a genuinely bare shell. An agent session attached to the box
# contaminates the numbers (+2.2 pp on Fn, measured), so the measuring process
# must not be the one that shares a terminal with an agent.
#
# Usage:
#   bash tools/tier1_go.sh            # prepare + verify, then stop
#   bash tools/tier1_go.sh --run      # prepare, then launch the bench too
#
# Env overrides:
#   SAQEF_CERT_WAIT_S   max seconds to wait for the cert notBefore (default 1800)
#   SAQEF_API_WAIT_S    max seconds to wait for the API after that (default 300)
#   SAQEF_AMBIENT_CEILING  ambient CPU %% ceiling (default 15)

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_WAIT_S="${SAQEF_CERT_WAIT_S:-1800}"
API_WAIT_S="${SAQEF_API_WAIT_S:-300}"
AMBIENT_CEILING="${SAQEF_AMBIENT_CEILING:-15}"
DRIVER="$REPO/tools/run_tier1_conc.sh"
LOG="$REPO/results/tier1_session.log"
DO_RUN=0

for a in "$@"; do
  case "$a" in
    --run) DO_RUN=1 ;;
    -h|--help) sed -n '3,32p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
bad()  { printf '\033[31m!! %s\033[0m\n' "$*" >&2; }
ok()   { printf '   \033[32mok\033[0m %s\n' "$*"; }

# Prefer a cached ticket; only prompt if there isn't one. `sudo -v` alone always
# demands a TTY, which breaks when this is driven from a non-interactive shell.
sudo -n true 2>/dev/null || sudo -v || {
  bad "need sudo (and a working password) to manage k3s"; exit 2; }

# ---------------------------------------------------------------------------
say "1/4  k3s clock-skew: WAIT, do not repair"
# ---------------------------------------------------------------------------
# k3s is in 'activating' and logging "current time ... is before <notBefore>"
# because its leaf cert was minted 1h backdated from a fast pre-NTP clock.
# notBefore is a fixed point in the future; waiting is sufficient and safe.

api_up() { sudo k3s kubectl get node >/dev/null 2>&1; }

# Read the notBefore of the cert k3s is actually SERVING on :6443. That is the
# one that gates startup. Reading the on-disk leaf can disagree with it, because
# the supervisor re-signs from the k3s-serving secret in etcd.
serving_notbefore() {
  local pem
  pem=$(mktemp) || return 1
  if echo | timeout 10 openssl s_client -connect 127.0.0.1:6443 2>/dev/null \
       | openssl x509 -outform PEM > "$pem" 2>/dev/null && [ -s "$pem" ]; then
    sudo openssl x509 -in "$pem" -noout -startdate 2>/dev/null | cut -d= -f2
  fi
  rm -f "$pem"
}

if api_up; then
  ok "k3s API already healthy -- nothing to wait for"
else
  state=$(systemctl is-active k3s 2>/dev/null); [ -n "$state" ] || state=unknown
  info "k3s service state: $state"
  if [ "$state" = "inactive" ] || [ "$state" = "failed" ]; then
    info "service is not running -- starting it"
    sudo systemctl start k3s
  fi

  nb_h=$(serving_notbefore)
  if [ -z "$nb_h" ]; then
    info "could not read the served cert yet; will poll the API directly"
  else
    info "  served cert notBefore: $nb_h"
    info "  now (UTC)            : $(date -u +'%b %e %H:%M:%S %Y GMT')"
    now_s=$(date -u +%s); nb_s=$(date -u -d "$nb_h" +%s 2>/dev/null || echo "")
    if [ -n "$nb_s" ] && [ "$nb_s" -gt "$now_s" ]; then
      wait_for=$((nb_s - now_s + 15))
      info "  -> notBefore is $((nb_s - now_s))s in the FUTURE."
      info "  -> this is the 1h-backdate-vs-NTP-correction bug. Waiting ${wait_for}s"
      info "     for it to pass. NOT repairing: the cert is correct, the clock"
      info "     moved after it was minted."
      if [ "$wait_for" -gt "$CERT_WAIT_S" ]; then
        bad "notBefore is further out than SAQEF_CERT_WAIT_S=${CERT_WAIT_S}s."
        bad "A >30min gap is not a normal NTP correction. Check the RTC and the"
        bad "boot-time clock before waiting: sudo timedatectl show"
        exit 4
      fi
    else
      info "  -> notBefore is in the past, so the cert is NOT the blocker."
      info "     Read the log instead: sudo journalctl -u k3s -n 40"
    fi
  fi

  info "polling the API for up to $((CERT_WAIT_S + API_WAIT_S))s (no edits, no restarts)"
  deadline=$(( $(date +%s) + CERT_WAIT_S + API_WAIT_S ))
  last=""
  while :; do
    if api_up; then
      ok "k3s API answered"
      break
    fi
    rem=$(( deadline - $(date +%s) ))
    if [ "$rem" -le 0 ]; then
      bad "k3s API still unreachable after $((CERT_WAIT_S + API_WAIT_S))s."
      bad "Do NOT start the bench. Inspect, do not delete certs:"
      bad "  sudo journalctl -u k3s --since '-10 min' --no-pager | grep -v 'Failed to validate' | tail -30"
      bad "  sudo k3s certificate rotate --help    # supported path, needs API up"
      exit 4
    fi
    # Progress only on change, so a quiet wait does not spam the terminal.
    cur=$(sudo k3s kubectl get node >/dev/null 2>&1 && echo up || systemctl is-active k3s 2>/dev/null)
    if [ "$cur" != "$last" ]; then
      info "  [$((CERT_WAIT_S + API_WAIT_S - rem))s] state: ${cur:-unknown} (${rem}s left)"
      last="$cur"
    fi
    sleep 5
  done
fi

sudo k3s kubectl get node 2>/dev/null | sed 's/^/   /'

# ---------------------------------------------------------------------------
say "2/4  Knative substrate"
# ---------------------------------------------------------------------------
# check_preconditions in run_lock_session.sh hard-fails unless docker ps shows
# k8s_* containers. With the API finally up the system pods schedule; give
# them a bounded window rather than assuming.
info "waiting for k8s_* containers to appear (system pods coming up)"
sub_deadline=$(( $(date +%s) + 240 ))
while :; do
  n=$(sudo docker ps --format '{{.Names}}' 2>/dev/null | grep -c '^k8s_')
  if [ "$n" -gt 0 ]; then
    ok "substrate resident ($n k8s_* containers)"
    break
  fi
  if [ "$(date +%s)" -ge "$sub_deadline" ]; then
    bad "no k8s_* containers after 240s."
    bad "Check: sudo k3s kubectl get pods -A"
    bad "The Knative legs will refuse to run without this."
    exit 4
  fi
  sleep 5
done
sudo k3s kubectl get pods -A --no-headers 2>/dev/null | awk '{print $4}' \
  | sort | uniq -c | sed 's/^/   /'

# ---------------------------------------------------------------------------
say "3/4  Ambient check"
# ---------------------------------------------------------------------------
amb=$(python3 - <<'PY'
import time
def busy():
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:]]
    return sum(v), v[3] + v[4]
a = busy(); time.sleep(5); b = busy()
tot, idle = b[0] - a[0], b[1] - a[1]
print("%.1f" % (100.0 * (tot - idle) / tot) if tot else "0.0")
PY
)
if python3 -c "import sys; sys.exit(0 if float('$amb') < $AMBIENT_CEILING else 1)"; then
  ok "ambient ${amb}% (ceiling ${AMBIENT_CEILING}%)"
else
  bad "ambient ${amb}% is OVER the ${AMBIENT_CEILING}% ceiling."
  bad "Top CPU right now:"
  ps -eo pcpu,comm --sort=-pcpu 2>/dev/null | head -6 | sed 's/^/     /' >&2
  bad "This is expected while an agent session is attached -- it is counting you."
  bad "Quit the agent, wait ~60s, re-run this script from a bare shell."
  exit 3
fi

# ---------------------------------------------------------------------------
say "4/4  Next"
# ---------------------------------------------------------------------------
info "Substrate is up and the box is quiet. The bench itself:"
info ""
info "  cd $REPO"
if [ "$DO_RUN" = 1 ]; then
  info "  bash tools/run_tier1_conc.sh 2>&1 | tee $LOG"
  info ""
  bad "NOTE: with --run, the measuring process shares this terminal."
  bad "If an agent is attached, the numbers are contaminated. Quit first."
  info ""
  read -r -p "   Launch the ~2.5-3h bench now? [y/N] " a
  case "$a" in
    [yY]|[yY][eE][sS]) ;;
    *) info "not launching."; exit 0 ;;
  esac
  bash "$DRIVER" 2>&1 | tee "$LOG"
  exit "${PIPESTATUS[0]}"
else
  info "  bash tools/run_tier1_quiet.sh --check    # re-verify, runs nothing"
  info "  bash tools/run_tier1_quiet.sh --yes      # full 3h run"
  info ""
  info "Quit your agent/editor first, then run one of those from a bare shell."
  info "This script stops here on purpose: it should not be the process that"
  info "measures while you are still attached to the box."
  info ""
  info "log dir: $REPO/results/"
fi
