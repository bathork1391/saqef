#!/usr/bin/env bash
#
# One-command entry point for the shares-only tier-1 RE-MEASUREMENT
# (runbook 24.7.5, tag v9.14.1-remeasure). Everything the frozen protocol
# needs, in the order it needs it, with no memory of steps required.
#
#   bash tools/remeasure_shares.sh            # pre-flight, confirm, run (~2.5-3 h)
#   bash tools/remeasure_shares.sh --check    # pre-flight only, runs nothing
#   bash tools/remeasure_shares.sh --yes      # unattended
#
# WHY THIS EXISTS RATHER THAN tools/run_tier1_quiet.sh: that wrapper invokes
# the driver with NO arguments (`bash "$DRIVER"`), which is bare `tier1c<N>`
# stamps and NO --rapl-fit-warn. Running it for this campaign would execute a
# different protocol from the one 24.7.5 froze -- bare stamps that can collide
# with the pre-c05a9df datasets, and RAPL FIT gating instead of warning. The
# flags are not optional here, so they are hard-coded below rather than passed.
#
# MUST be run from a bare shell with agents quit. The harness re-checks the
# ambient gate before every single leg, so a contaminated box fails on its own
# -- this script just fails fast instead of two hours in.
#
# Deliberately no `set -e`: each step is checked explicitly so a failure
# prints WHY instead of a bare non-zero exit.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRIVER="$REPO/tools/run_tier1_conc.sh"
# shellcheck source=tools/tier1_log.sh
. "$REPO/tools/tier1_log.sh"
tier1_log_new "$REPO"
LOG="$TIER1_LOG"

# ---- frozen by runbook 24.7.5. Do not change without a new tag. ----------
TAG="v9.14.1-remeasure"
PFX="remeasure_shares_"
DRIVER_ARGS=(--stamp-prefix "$PFX" --rapl-fit-warn)
# The ONLY files permitted to differ between the frozen tag and HEAD. The
# runbook is where the protocol is written; this script is the pre-flight that
# checks it. Neither is on the measurement path, so neither can move a number.
ALLOWED_GAP="TROUBLESHOOTING_RUNBOOK.md|tools/remeasure_shares.sh"
# Anything here means measurement code drifted past the freeze. Hard fail.
FORBIDDEN_GAP='tools/run_tier1_conc\.sh|tools/run_lock_session\.sh|saqef_harness\.py|tests/'
AMBIENT_CEILING="${SAQEF_AMBIENT_CEILING:-15}"
PIN_PROFILE=1
ASSUME_YES=0
CHECK_ONLY=0
SKIP_DRY=0

for a in "$@"; do
  case "$a" in
    -y|--yes)       ASSUME_YES=1 ;;
    --check)        CHECK_ONLY=1 ;;
    --skip-dry-run) SKIP_DRY=1 ;;
    --no-pin)       PIN_PROFILE=0 ;;
    -h|--help)      sed -n '3,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
bad()  { printf '\n\033[31m!! %s\033[0m\n' "$*" >&2; }
ok()   { printf '   \033[32mok\033[0m %s\n' "$*"; }

# Snapshot everything the runs do NOT record, so a frequency question later has
# a record to answer from. `governor` is the only frequency control any run
# writes into summary.json; EPP, the thermal state and the power profile are
# invisible to the harness (runbook 24.7.7).
box_state() {
  local out="$REPO/results/${PFX}box_state"
  mkdir -p "$out"
  {
    date -u +"utc=%Y-%m-%dT%H:%M:%SZ"
    uname -r | sed 's/^/kernel=/'
    echo "profile=$(powerprofilesctl get 2>&1)"
    echo "ac_online=$(cat /sys/class/power_supply/AC/online 2>&1)"
    for f in scaling_driver scaling_governor energy_performance_preference \
             scaling_min_freq scaling_max_freq; do
      echo "cpufreq.$f=$(cat /sys/devices/system/cpu/cpu0/cpufreq/$f 2>&1)"
    done
    for f in no_turbo min_perf_pct max_perf_pct status; do
      echo "intel_pstate.$f=$(cat /sys/devices/system/cpu/intel_pstate/$f 2>&1)"
    done
    for z in /sys/class/thermal/thermal_zone*/; do
      echo "thermal.$(cat "$z/type" 2>&1)=$(cat "$z/temp" 2>&1)"
    done
  } > "$out/cpufreq.txt"
  echo "$out/cpufreq.txt"
}

# ---------------------------------------------------------------- provenance
say "1/6  Provenance (this is what makes the session citable)"

if [ ! -f "$DRIVER" ]; then
  bad "driver not found at $DRIVER -- wrong checkout?"; exit 2
fi

dirty="$(git -C "$REPO" status --porcelain 2>/dev/null)"
if [ -z "$dirty" ]; then
  ok "working tree clean"
else
  bad "working tree is DIRTY. 24.1 lists this as a protocol deviation, and it"
  bad "is the sole reason all 15 bridge_tier1c1 legs are uncitable."
  printf '%s\n' "$dirty" | sed 's/^/     /' >&2
  exit 3
fi

git -C "$REPO" rev-parse -q --verify "refs/tags/$TAG" >/dev/null 2>&1 \
  || { bad "tag $TAG not found -- fetch, or you are on the wrong branch"; exit 3; }
ok "tag $TAG present ($(git -C "$REPO" rev-parse --short "$TAG^{}"))"

gap="$(git -C "$REPO" diff --name-only "$TAG..HEAD" 2>/dev/null)"
if [ -z "$gap" ]; then
  ok "HEAD is the frozen tag"
else
  illegal="$(printf '%s\n' "$gap" | grep -Ev "^($ALLOWED_GAP)$" || true)"
  if [ -n "$illegal" ]; then
    bad "MEASUREMENT CODE differs from $TAG. These files are on the"
    bad "measurement path and must not drift past the freeze:"
    printf '%s\n' "$illegal" | sed 's/^/     /' >&2
    bad "Run from the tag, or re-freeze deliberately."
    exit 3
  fi
  ok "docs-only gap from $TAG (allowed): $(printf '%s ' $gap)"
fi

# ------------------------------------------------------------------ ambient
say "2/6  Ambient gate (harness re-checks this before EVERY leg)"

if command -v docker >/dev/null && docker ps >/dev/null 2>&1; then
  ok "docker reachable"
else
  bad "docker not reachable (is the docker daemon up?)"; exit 2
fi

amb=$(python3 - <<'PY'
import time
def busy():
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:]]
    return sum(v), v[3] + v[4]          # total, idle+iowait
a = busy(); time.sleep(5); b = busy()
tot, idle = b[0] - a[0], b[1] - a[1]
print("%.1f" % (100.0 * (tot - idle) / tot) if tot else "0.0")
PY
)
if python3 -c "import sys; sys.exit(0 if float('${amb}') < ${AMBIENT_CEILING} else 1)"; then
  ok "ambient load ${amb}% (ceiling ${AMBIENT_CEILING}%)"
else
  bad "ambient load ${amb}% is OVER the ${AMBIENT_CEILING}% ceiling."
  bad "Top CPU consumers right now:"
  ps -eo pcpu,comm --sort=-pcpu 2>/dev/null | head -6 | sed 's/^/     /' >&2
  bad "Quit your editor/agents/terminals and re-run. (A serverless box is"
  bad "mostly idle by design; ~16% means a bench or an agent session is live.)"
  exit 3
fi

# --------------------------------------------------------------------- k3s
say "3/6  k3s / Knative substrate"

if timeout 60 sudo -n k3s kubectl get node >/dev/null 2>&1; then
  ok "k3s API healthy"
else
  bad "k3s API unreachable. tools/run_tier1_quiet.sh knows how to repair the"
  bad "clock-skewed serving cert that strands k3s in 'activating'; run it"
  bad "first, then re-run this script."
  bad "Inspect with: sudo journalctl -u k3s -n 40 | tail -20"
  exit 4
fi

# ------------------------------------------------------- CPU policy and state
say "4/6  CPU policy and box state"

if [ "$PIN_PROFILE" = 1 ] && command -v powerprofilesctl >/dev/null 2>&1; then
  before="$(powerprofilesctl get 2>/dev/null)"
  if [ "$before" = "performance" ]; then
    ok "power profile already 'performance'"
  else
    # power-profiles-daemon is what WRITES energy_performance_preference. Left
    # alone, EPP is a value this protocol needs fixed but never records -- and
    # it was observed changing within a single working session. Needs no sudo,
    # unlike the intel_pstate knobs. See runbook 24.7.7.
    if powerprofilesctl set performance >/dev/null 2>&1; then
      ok "power profile '$before' -> 'performance' (EPP now known, not inherited)"
    else
      bad "could not set the power profile (needs the power group)."
      bad "EPP will be inherited, unrecorded. Re-run with --no-pin to accept"
      bad "that knowingly, or fix polkit and re-run."
      exit 5
    fi
  fi
else
  info "--no-pin: leaving the power profile alone. EPP is then inherited and"
  info "unrecorded, which 24.7.7 warns about. This is a knowing deviation."
fi

state_file="$(box_state)"
ok "box state snapshot -> ${state_file#"$REPO"/}"
info "$(grep -E 'profile=|energy_performance|x86_pkg_temp' "$state_file" | tr '\n' ' ')"
case "$(cat /sys/class/power_supply/AC/online 2>/dev/null)" in
  1) ok "on AC" ;;
  *) bad "NOT on AC -- this is a laptop and a battery run is not a bench run."; exit 5 ;;
esac

# ------------------------------------------------------------ stamp clobber
# Match the run's real output dirs only. The ${PFX}box_state/ dir this script
# writes above also matches a bare *${PFX}* glob, which would make this warn on
# every single run and train you to ignore it.
collide="$(ls -d "$REPO"/results/*"_cpubound_lock_${PFX}"* \
                   "$REPO"/results/lock_session_"${PFX}"* \
                   "$REPO"/results/idle_probe_"${PFX}"* 2>/dev/null || true)"
if [ -n "$collide" ]; then
  bad "results already exist for prefix '${PFX}'. The driver refuses to"
  bad "clobber a stamp, so it would fail partway into a 3 h session:"
  printf '%s\n' "$collide" | sed "s|$REPO/|     |" >&2
  bad "Either move those aside, or edit PFX at the top of this script and"
  bad "re-tag -- a new prefix is a protocol change, so it needs a new tag."
  exit 6
fi
ok "no existing results for prefix '${PFX}'"

if [ "$CHECK_ONLY" = 1 ]; then
  say "--check only: stopping before the run"
  exit 0
fi

# ------------------------------------------------------------------- dry run
if [ "$SKIP_DRY" = 0 ]; then
  say "5/6  Dry run (prints every stamp and command, measures nothing)"
  bash "$DRIVER" "${DRIVER_ARGS[@]}" --dry-run || {
    bad "dry run failed -- do not start a 3 h session"; exit 2; }
fi

# ------------------------------------------------------------------ confirm
say "6/6  Confirm"
if [ "$ASSUME_YES" = 0 ]; then
  info "This runs ~2.5-3 h and will occupy the box. Do not start other work."
  info "Watch the first Fn leg: it is the first real 'fn deploy --no-bump'."
  printf '   Start it now? [y/N] '
  read -r a
  case "$a" in
    [yY]|[yY][eE][sS]) ;;
    *) info "aborted at your request"; exit 0 ;;
  esac
fi

say "Running shares-only re-measurement (~2.5-3 h)"
info "tag    : $TAG"
info "prefix : $PFX"
info "energy : VOID by design (24.7.4) -- shares are the citable output"
info "log    : $LOG"
info ""
bash "$DRIVER" "${DRIVER_ARGS[@]}" 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}
tier1_log_publish "$REPO"

echo
if [ "$rc" -eq 0 ]; then
  ok "driver exited 0 -- aggregation complete"
else
  bad "driver exited $rc -- READ THE LOG before trusting any table."
  case "$rc" in
    1) bad "exit 1 usually means a PROTOCOL ERROR (a leg ran without a usable"
       bad "idle probe), not a measurement failure." ;;
    *) bad "a leg's bench or gate failed; see the log tail." ;;
  esac
fi

# Post-run box state: this box is a laptop and x86_pkg_temp sat at 63-67 C at
# idle, so a delivered-frequency drop across the session is a real possibility
# that no run records. Comparing this against the pre-run snapshot is the only
# cheap way to see whether it happened.
after="$(box_state)"
info "post-run box state -> ${after#"$REPO"/}"
if [ -f "$state_file" ]; then
  info "thermal drift (pre -> post):"
  paste <(grep '^thermal\.' "$state_file") <(grep '^thermal\.' "$after") \
    | while IFS=$'\t' read -r a b; do
        [ "$a" = "$b" ] && continue
        info "   ${a#thermal.} ${a##*=} -> ${b##*=} mC"
      done
  info "(no lines above = thermal zones unchanged)"
fi

info "log kept at: $LOG"
info ""
info "NEXT: copy the data off this machine -- results/ is gitignored:"
info "   rsync -av --progress results/${PFX}* <remote>:/path/to/bench-data/"