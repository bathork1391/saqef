#!/usr/bin/env bash
# One-file driver for the FINAL PUBLICATION-LOCK session (cold-review pass #6
# disposition, AGENTS.md "Cold review pass #6" / handoff): a single, quiet-gated,
# back-to-back four-platform measurement of the IDENTICAL 5 ms CPU-bound handler
# on one box state, with idle-w recalibrated immediately beforehand -- closing
# the two residual review attacks:
#
#   1. "your four-platform experiment wasn't equally controlled" (Kn/OW baselines
#      predate the ambient gate; Fn/OF and Kn/OW are different sessions) -> all
#      four platforms measured here under the SAME self-certifying ambient gate,
#      same day, same box state.
#   2. "your energy validation claim doesn't match your own raw data" (stale
#      idle_w constants) -> idle-w is recalibrated per stack state (N>=3 repeated
#      60 s RAPL reads, wraparound-guarded, medians saved to disk) immediately
#      before the benches, and used for each platform's run.
#
# It does NOT modify metrics/cpubound.json (the regression references are a
# separate concern, re-anchored by tools/reanchor_and_kn_idle.sh afterwards).
# It only deploys, measures, gates-checks, tears down, and writes results to
# FRESH outdirs -- nothing pre-existing is overwritten.
#
# PREREQS: run from a bare bash shell with opencode/agents QUIT, launched by a
# human, terminal-to-terminal. Each `saqef run` enforces the ambient-load quiet
# gate (default <=15% host busy over a 20 s window) and WILL REFUSE to start on
# a busy box -- that is the self-certification (it is a precondition, not
# continuous detection, so keep the shell bare for the whole session). Docker,
# k3s/Knative substrate, and RAPL must be available as for any citable run.
#
# Usage:
#   bash tools/run_lock_session.sh                          # full lock session
#   bash tools/run_lock_session.sh --dry-run                # print the plan only
#   bash tools/run_lock_session.sh --idle-reps 5            # N reads/state (default 3)
#   bash tools/run_lock_session.sh --skip-idle-calib        # reuse saved calib (or --idle-w-*)
#   bash tools/run_lock_session.sh --idle-w-kn 4.561        # override a specific idle-w
#   bash tools/run_lock_session.sh --platforms kn,ow        # only these legs (recovery)
#   bash tools/run_lock_session.sh --repeat 5 --total 10000 --concurrency 4
#
# Results (all under results/, stamped, never clobbering existing data):
#   results/idle_w_calibration/lock_<stamp>/idle_w_<state>.txt   (N reads + median per state)
#   results/<platform>_cpubound_lock_<stamp>/                     (run_1..N + summary.json)
#   results/lock_session_<stamp>/lock_summary.json                (medians + gate status)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%F)"
IDLE_REPS=3
DRY_RUN=0
SKIP_IDLE=0
PLATFORMS="of,fn,kn,ow"
TOTAL=10000
CONCURRENCY=4
REPEAT=5
DISCARD_WARMUP=0          # drop the first N runs from the gate + any statistic.
                         # A cold JIT/classload makes the FIRST repeat an outlier
                         # (tier1ow8 run_1 cp_ms/inv=56.4 vs 29.6-33.4 after),
                         # which inflates within-leg variance and can flip a
                         # verdict: OpenWhisk's share SD collapses 4.09 -> 0.29
                         # at c=1 once run_1 is dropped. Use REPEAT=5+N so five
                         # usable runs remain.
IDLE_OF="" IDLE_FN="" IDLE_KN="" IDLE_OW=""
IDLE_BARE=""
DEFAULT_OF=4.3 DEFAULT_FN=4.3 DEFAULT_KN=4.561 DEFAULT_OW=4.3
DEPLOY_ONLY=0            # deploy+verify the platform(s), then teardown -- EXACTLY ONE
                         # bench invocation per platform at REQUESTS_PER_RUN (for the
                         # c=1 duration pilot shared by the tier1 driver). See run_leg.
REQUESTS_PER_RUN=3000    # harness --total for the DEPLOY_ONLY pilot bench
OW_DURATION=300          # OpenWhisk loadgen duration cap (default 300; see run_leg)
MAX_DRIFT_PCT=20         # gate: >this% throughput loss from run_1 to run_N fails the
                         # leg. Only meaningful for the repeat sweep; the
                         # deploy-only pilot bench gets a single snapshot.
MAX_SAMPLE_GAP_S=1.0     # gate: >this worst gap between CPU samples fails the
                         # run. Must match the harness --max-sample-gap default;
                         # set by --max-sample-gap.
                         # leg. 20% is ~4x the best observed legitimate run-to-run
                         # spread on a healthy leg, so it only trips on real decay.
DRIFT_TWO_SIDED=0        # 1 (--drift-two-sided): the drift gate also fails a throughput
                         # RISE above --max-drift-pct, not only a loss. Off by default so
                         # W1 and earlier sessions re-gate identically; W2 onward turns it
                         # on (runbook 28.9: the 28.8 bridge rose 42.7 % and passed).
SETTLE_AFTER_VERIFY=1    # 0 (--no-settle): skip the post-verify settle. Default on: wait
                         # until /proc/stat shows a quiet window (SETTLE_* below)
                         # after deploy+scale+verify, right before the quiet-gated run.
                         # Runbook 28.9: 5 of 10 Knative c=8 legs failed the 15 % gate
                         # (15.4-18.0 %) on the post-scale burst, against a ~10-12 % floor.
SETTLE_WINDOW_S=20       # --settle-window S: length of each settle window. 20 = the quiet
                         # gate's own window (runbook 29.2): a single 5 s lull (the 28.9
                         # setting, --settle-window 5 --settle-max 10) passed at 4.4 % and the
                         # gate's 20 s window then read 16.9 % (owlog29 ow8_cli, twice).
SETTLE_MAX_PCT=12        # --settle-max P: a settle window must read <= P % busy. 12 leaves
                         # 3 pp under the 15 % gate for window-to-window variation.
SETTLE_CAP_S=240         # give up after this long; the gate then decides (never fails a leg).
IDLE_SOURCE=""           # --idle-w-source DIR: the calibration the --idle-w-* values came
                         # from (another stamp's idle_w_calibration dir); recorded in the
                         # lock summary's idle_w_provenance and checked against it.
RAPL_FIT_WARN=0          # 1 (--rapl-fit-warn): a run whose RAPL fit is >15% is
                         # recorded as a WARNING, not a gate failure. Only for
                         # sessions whose question is the CP/fn CPU share, which
                         # RAPL does not enter (runbook 24.1, 24.6). Every other
                         # gate stays fatal. The >15% figure is the residual against
                         # the retired 3.5 W/core model (runbook 25.1, 25.6), so it
                         # makes MODEL-based energy non-citable; RAPL-based energy
                         # (e_rapl_j - idle_w*wall) is unaffected (25.2, 27.5).
WARMUP=""                # --warmup N: requests fired before each run's window. Empty = the metric's
                         # default (20). W4's cold arm passes 0 so nothing warms the emptied pool (§32).
CPU_PROBE_S=0            # >0: after the bench, run one native --idle-probe of CPU_PROBE_S
                         # seconds with the same stack state and save cp/fn CPU rates.
                         # This is the direct per-leg background-rate measurement the
                         # tier1 concurrency driver subtracts from CP/fn CPU-s (the
                         # window-length artifact the 2026-08-15 sweep exposed).

args=("$@")
i=0
while [ "$i" -lt "$#" ]; do
    arg="${args[$i]}"
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --skip-idle-calib) SKIP_IDLE=1 ;;
        --stamp) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--stamp needs a value" >&2; exit 2; }; STAMP="${args[$i]}" ;;
        --stamp=*) STAMP="${arg#*=}" ;;
        --idle-reps) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--idle-reps needs a value" >&2; exit 2; }; IDLE_REPS="${args[$i]}" ;;
        --idle-reps=*) IDLE_REPS="${arg#*=}" ;;
        --platforms) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--platforms needs a value" >&2; exit 2; }; PLATFORMS="${args[$i]}" ;;
        --platforms=*) PLATFORMS="${arg#*=}" ;;
        --idle-w-of) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--idle-w-of needs a value" >&2; exit 2; }; IDLE_OF="${args[$i]}" ;;
        --idle-w-of=*) IDLE_OF="${arg#*=}" ;;
        --idle-w-fn) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--idle-w-fn needs a value" >&2; exit 2; }; IDLE_FN="${args[$i]}" ;;
        --idle-w-fn=*) IDLE_FN="${arg#*=}" ;;
        --idle-w-kn) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--idle-w-kn needs a value" >&2; exit 2; }; IDLE_KN="${args[$i]}" ;;
        --idle-w-kn=*) IDLE_KN="${arg#*=}" ;;
        --idle-w-ow) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--idle-w-ow needs a value" >&2; exit 2; }; IDLE_OW="${args[$i]}" ;;
        --idle-w-ow=*) IDLE_OW="${arg#*=}" ;;
        --total) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--total needs a value" >&2; exit 2; }; TOTAL="${args[$i]}" ;;
        --concurrency) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--concurrency needs a value" >&2; exit 2; }; CONCURRENCY="${args[$i]}" ;;
        --repeat) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--repeat needs a value" >&2; exit 2; }; REPEAT="${args[$i]}" ;;
        --repeat=*) REPEAT="${arg#*=}" ;;
        --discard-warmup) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--discard-warmup needs a value" >&2; exit 2; }; DISCARD_WARMUP="${args[$i]}" ;;
        --discard-warmup=*) DISCARD_WARMUP="${arg#*=}" ;;
        --deploy-only) DEPLOY_ONLY=1 ;;
        --requests-per-run) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--requests-per-run needs a value" >&2; exit 2; }; REQUESTS_PER_RUN="${args[$i]}" ;;
        --requests-per-run=*) REQUESTS_PER_RUN="${arg#*=}" ;;
        --warmup) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--warmup needs a value" >&2; exit 2; }; WARMUP="${args[$i]}" ;;
        --ow-duration) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--ow-duration needs a value" >&2; exit 2; }; OW_DURATION="${args[$i]}" ;;
        --ow-duration=*) OW_DURATION="${arg#*=}" ;;
        --max-drift-pct) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--max-drift-pct needs a value" >&2; exit 2; }; MAX_DRIFT_PCT="${args[$i]}" ;;
        --max-sample-gap) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--max-sample-gap needs a value" >&2; exit 2; }; MAX_SAMPLE_GAP_S="${args[$i]}" ;;
        --max-sample-gap=*) MAX_SAMPLE_GAP_S="${arg#*=}" ;;
        --max-drift-pct=*) MAX_DRIFT_PCT="${arg#*=}" ;;
        --cpu-probe) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--cpu-probe needs a value" >&2; exit 2; }; CPU_PROBE_S="${args[$i]}" ;;
        --cpu-probe=*) CPU_PROBE_S="${arg#*=}" ;;
        --rapl-fit-warn) RAPL_FIT_WARN=1 ;;
        --drift-two-sided) DRIFT_TWO_SIDED=1 ;;
        --no-settle) SETTLE_AFTER_VERIFY=0 ;;
        --settle-window) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--settle-window needs a value" >&2; exit 2; }; SETTLE_WINDOW_S="${args[$i]}" ;;
        --settle-window=*) SETTLE_WINDOW_S="${arg#*=}" ;;
        --settle-max) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--settle-max needs a value" >&2; exit 2; }; SETTLE_MAX_PCT="${args[$i]}" ;;
        --settle-max=*) SETTLE_MAX_PCT="${arg#*=}" ;;
        --idle-w-source) i=$((i + 1)); [ "$i" -ge "$#" ] && { echo "--idle-w-source needs a value" >&2; exit 2; }; IDLE_SOURCE="${args[$i]}" ;;
        --idle-w-source=*) IDLE_SOURCE="${arg#*=}" ;;
        --*) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
    i=$((i + 1))
done
case "$IDLE_REPS" in
    ''|*[!0-9]*) echo "--idle-reps must be a positive integer (got '$IDLE_REPS')" >&2; exit 2 ;;
esac

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO=sudo; fi
SAQEF="$SUDO python3 $REPO/saqef"
CALIB_DIR="$REPO/results/idle_w_calibration/lock_$STAMP"
LOCK_DIR="$REPO/results/lock_session_$STAMP"
mkdir -p "$CALIB_DIR" "$LOCK_DIR"

die() { echo "ERROR: $*" >&2; exit 1; }
banner() { echo; echo "============================================================"; echo "  $1"; echo "============================================================"; }

# ---------------------------------------------------------------------------
# check_preconditions -- hard fail on anything that would taint a run
# ---------------------------------------------------------------------------
check_preconditions() {
    banner "precondition checks"
    local problems=()
    command -v python3 >/dev/null || problems+=("python3 not found")
    # A discarded warm-up run still costs wall time, so the loop must have enough
    # left over to be citable. Fail here rather than after 3 hours of measuring.
    if ! [[ "$REPEAT" =~ ^[0-9]+$ ]] || ! [[ "$DISCARD_WARMUP" =~ ^[0-9]+$ ]]; then
        problems+=("--repeat and --discard-warmup must be integers (got $REPEAT/$DISCARD_WARMUP)")
    elif [ "$DISCARD_WARMUP" -gt 0 ]; then
        local usable=$((REPEAT - DISCARD_WARMUP))
        if [ "$usable" -lt 5 ]; then
            problems+=("--discard-warmup $DISCARD_WARMUP leaves $usable usable runs at --repeat $REPEAT; a citable leg needs >=5 (use --repeat $((5 + DISCARD_WARMUP)))")
        elif [ "$REPEAT" -lt 5 ]; then
            problems+=("--discard-warmup only makes sense at REPEAT>=5 (got $REPEAT)")
        else
            echo "  warm-up  : discarding first $DISCARD_WARMUP of $REPEAT run(s); $usable usable runs"
        fi
    fi
    if ! $SUDO docker info >/dev/null 2>&1; then
        problems+=("docker not reachable (even via sudo). Is dockerd up?")
    else
        echo "  docker: ok"
    fi
    if ! $SUDO docker ps --format '{{.Names}}' | grep -q '^k8s_'; then
        problems+=("k3s/Knative substrate not detected (no k8s_* containers). Start it: 'sudo systemctl start k3s', confirm 'sudo k3s kubectl get node', then re-run.")
    else
        echo "  k3s/Knative substrate: resident"
    fi
    leftovers=$($SUDO python3 - <<'PY'
import subprocess, sys
bad = []
svc = subprocess.run(["docker","service","ls","--format","{{.Name}}"],
                     capture_output=True, text=True).stdout.split()
if svc:
    bad.append("swarm services still up: %s" % ", ".join(svc))
ps = subprocess.run(["docker","ps","--format","{{.Names}}"],
                    capture_output=True, text=True).stdout
for name in ps.split():
    if name.startswith("fnserver"):
        bad.append("fnserver container still up (OpenFaaS isolation refuses it)")
    if "hello" in name or "user-container" in name or "queue-proxy" in name:
        bad.append("leftover Knative/OF function container: %s" % name)
print("\n".join(bad))
PY
)
    if [ -n "$leftovers" ]; then
        problems+=("box not clean: $leftovers")
    else
        echo "  no leftover services/containers: clean"
    fi
    if [ -n "${problems[*]}" ]; then
        for p in "${problems[@]}"; do echo "  PROBLEM: $p"; done
        if [ "$DRY_RUN" = 1 ]; then
            echo "  (dry-run: not failing -- here is what a real run would refuse on)"
        else
            die "preconditions not met; fix the above before running the lock session"
        fi
    fi
    ls /sys/class/powercap/intel-rapl*/energy_uj >/dev/null 2>&1 \
        || echo "  WARNING: RAPL energy_uj not readable -- energy model will report n/a (share is unaffected)"
    echo "  checks done."
}

# ---------------------------------------------------------------------------
# wait_knative_clean -- belt-and-suspenders drain wait after `teardown
# --platform knative`. The adapter's own wait_containers() (platforms/
# knative.py) only waits 120s and WARNS-BUT-CONTINUES on timeout; k3s
# draining 16 replica pods under load has been observed to outlast that
# budget and self-resolve, but the self-resolve time is NOT a fixed constant
# -- ~60s past the WARNING in one observation (AGENTS.md 2026-08-09
# "Follow-up" entry), >180s in another (2026-08-13, smoketest session: script
# died at the 180s ceiling but a manual check moments later found the
# cluster and docker both fully clean). Budget generously; a longer wait
# here is cheap, a spurious hard-fail on a run that would have finished on
# its own is not. Without this wait at all, the next platform's isolation
# guard trips on leftover user-container/queue-proxy pods a few seconds
# later.
# ---------------------------------------------------------------------------
wait_knative_clean() {
    local timeout="${1:-360}" waited=0 left=""
    while [ "$waited" -lt "$timeout" ]; do
        left=$($SUDO docker ps --format '{{.Names}}' | grep -E 'user-container|queue-proxy|hello-0000' || true)
        if [ -z "$left" ]; then
            [ "$waited" -gt 0 ] && echo "  knative teardown fully drained after ${waited}s"
            return 0
        fi
        sleep 5
        waited=$((waited + 5))
        echo "  waiting for knative teardown to drain (${waited}/${timeout}s)..."
    done
    die "knative teardown did not fully drain after ${timeout}s; leftover: $left -- clear manually (see TROUBLESHOOTING_RUNBOOK.md) before continuing"
}

# ---------------------------------------------------------------------------
# rapl_series STATE LABEL  -- N repeated 60 s RAPL reads for the CURRENT stack
# state; prints each read + median, and saves them under $CALIB_DIR. Reuses
# saqef_harness.py's wraparound-guarded read (same guard as the harness itself).
# Prints the MEDIAN on stdout (the only thing callers consume).
# ---------------------------------------------------------------------------
rapl_series() {
    local state="$1" label="$2"
    echo "  -- [$label] $IDLE_REPS x 60 s RAPL reads (zero traffic)..." >&2
    local out="$CALIB_DIR/idle_w_$state.txt"
    local med
    med=$(python3 - "$REPO" "$IDLE_REPS" "$out" "$label" "$state" <<'PY'
import json, os, sys, time, statistics
repo, n, out, label, state = sys.argv[1:]
sys.path.insert(0, repo)
import saqef_harness as h
reads, i, attempts = [], 0, 0
while i < int(n):
    attempts += 1
    if attempts > int(n) * 3:
        sys.exit("ERROR: too many discarded/wrapped RAPL reads; check intel-rapl:0")
    e0 = h.rapl_energy()
    time.sleep(60)
    e1 = h.rapl_energy()
    if e0 is None or e1 is None:
        print("      read %d/%d: RAPL unavailable, retrying" % (i + 1, int(n)))
        continue
    energy, flag = h.rapl_correct_wrap(e1 - e0)
    if energy is None:
        print("      read %d/%d: DISCARDED (rapl_wrap=%s), retrying" % (i + 1, int(n), flag))
        continue
    w = energy / 60.0
    reads.append(w)
    print("      read %d/%d: %.3f W%s" % (i + 1, int(n), w, "" if flag == "none" else "  [%s]" % flag))
    i += 1
med = statistics.median(reads)
rec = {"state": state, "label": label, "reads_w": [round(r, 3) for r in reads],
       "median_w": round(med, 3), "n": int(n),
       "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
with open(out, "w") as f:
    json.dump(rec, f, indent=2)
print("      -> median %.3f W  (min %.3f / max %.3f, n=%d)  saved to %s"
      % (med, min(reads), max(reads), len(reads), os.path.relpath(out, repo)))
print("RESULT_MEDIAN=%.3f" % med)
PY
)
    echo "$med" | grep '^RESULT_MEDIAN=' | cut -d= -f2
}

# ---------------------------------------------------------------------------
# run_leg PLATFORM -- deploy (scale/verify) run gates teardown for one platform
# ---------------------------------------------------------------------------
# Wait for the post-deploy/scale/verify burst to pass before the harness's 15 % quiet
# gate samples the box (runbook 28.9). Windows are as long as the gate's own (runbook
# 29.2), back to back, and every reading is logged. Never fails the leg: on timeout
# the gate decides.
settle_after_verify() {
    local w=0 b seen=""
    while [ $w -lt "$SETTLE_CAP_S" ]; do
        b=$(python3 - "$SETTLE_WINDOW_S" <<'PY'
import sys, time
def busy():
    v = list(map(int, open("/proc/stat").readline().split()[1:]))
    return sum(v) - v[3] - v[4], sum(v)
b0, t0 = busy(); time.sleep(float(sys.argv[1])); b1, t1 = busy()
print(round(100.0 * (b1 - b0) / max(1, t1 - t0), 1))
PY
)
        w=$((w + SETTLE_WINDOW_S))
        seen="$seen ${b}"
        if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) <= float(sys.argv[2]) else 1)" "$b" "$SETTLE_MAX_PCT"; then
            echo ">>> settle after verify: ${b}% busy after ${w}s (${SETTLE_WINDOW_S}s windows, <= ${SETTLE_MAX_PCT}%; readings:${seen})"; return 0
        fi
    done
    echo ">>> settle after verify: still ${b}% busy after ${w}s; the quiet gate decides (readings:${seen})"
}

run_leg() {
    local platform="$1" idle_w="$2"
    local out="$REPO/results/${platform}_cpubound_lock_$STAMP"
    # mirrors resolve_outdir() in `saqef`: repeat < 5 writes to <out>_quick,
    # never the bare path -- gates must look in the same place run wrote to.
    local gate_out="$out"
    [ "$REPEAT" -lt 5 ] && gate_out="${out}_quick"
    # OpenWhisk is slow (~65-70 rps at c=4): 10000 requests take ~150s. The
    # loadgen subprocess kill-switch is duration+120s, so the default 60s used
    # for the other three platforms leaves only ~30s margin -- `hey` gets
    # killed mid-run and falls back to the Python loadgen silently every time.
    # This is TROUBLESHOOTING_RUNBOOK.md #6, already fixed once in the older
    # per-platform manual protocol; it regressed here because this consolidated
    # driver never carried the platform-specific override forward (see #11).
    # 2026-10-01: duration is now overridable per invocation (--ow-duration) so
    # the tier1 driver can give an OpenWhisk c=1 leg a taller cap.
    local duration=60
    [ "$platform" = "openwhisk" ] && duration="$OW_DURATION"
    banner "$(echo $platform | tr a-z A-Z) leg (idle_w=$idle_w, duration=${duration}s)"
    if [ "$DRY_RUN" = 1 ]; then
        echo "DRY-RUN: would run: deploy --platform $platform"
        case "$platform" in
            openfaas|knative) echo "DRY-RUN: scale --platform $platform --replicas 16" ;;
        esac
        echo "DRY-RUN: verify --platform $platform"
        [ "$SETTLE_AFTER_VERIFY" = 1 ] && echo "DRY-RUN: settle until a ${SETTLE_WINDOW_S}s window reads <= ${SETTLE_MAX_PCT}% busy (${SETTLE_CAP_S} s cap)"
        echo "DRY-RUN: run --platform $platform --total $TOTAL --concurrency $CONCURRENCY --duration $duration --repeat $REPEAT${WARMUP:+ --warmup $WARMUP} --idle-w $idle_w --out $out"
        [ "$CPU_PROBE_S" -gt 0 ] && echo "DRY-RUN: run --platform $platform --idle-probe --duration $CPU_PROBE_S --repeat 1 --idle-w $idle_w --out $REPO/results/idle_probe_${STAMP}/$platform"
        echo "DRY-RUN: gates --out $gate_out ; teardown --platform $platform"
        return 0
    fi
    if [ -e "$out" ]; then
        die "outdir already exists: $out -- refusing to clobber a possibly-stale same-stamp set. Pick a fresh --stamp (or move the old dir away), then re-run."
    fi
    echo ">>> deploy"
    $SAQEF deploy --platform "$platform"
    case "$platform" in
        openfaas|knative)
            echo ">>> scale -> 16 replicas (GIL parity)"
            $SAQEF scale --platform "$platform" --replicas 16 ;;
    esac
    if [ "$platform" = knative ] && [ "${SAQEF_KN_SCALE_FROM_ZERO:-}" = 1 ]; then
        # W4 (§32): the minScale-0 patch makes a new revision; the 16 pods of the deployed one
        # sit in Terminating for the 300 s grace (PID-1 server ignores SIGTERM). Let them go
        # so no idle leftover pod is in any measured window.
        local tw=0
        while [ "$tw" -lt 420 ] && k3s kubectl get pods -n default -l serving.knative.dev/service=hello --no-headers 2>/dev/null | grep -q Terminating; do
            sleep 5; tw=$((tw + 5))
        done
        echo ">>> knative scale-from-zero: old revision's pods gone after ${tw}s"
    fi
    echo ">>> verify"
    $SAQEF verify --platform "$platform"
    # W2 (runbook §30.6): the DEPLOYED function must serve the arm this leg is for. Both arms
    # burn 5 ms, so a stale image serving the other arm would leave no trace in the data.
    # 20 GETs (several replicas), every reply must name SAQEF_EXPECT_KIB; otherwise the leg
    # fails here, before anything is measured. Unset for every other workload: no-op.
    if [ -n "${SAQEF_EXPECT_KIB:-}" ]; then
        python3 - "$REPO" "$platform" "$SAQEF_EXPECT_KIB" <<'PY'
import re, sys, urllib.request
repo, plat, want = sys.argv[1:]
sys.path.insert(0, repo)
from platforms import get_adapter
url, got = get_adapter(plat).url, []
for _ in range(20):
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            m = re.search(r"""kib["']?\s*[:=]\s*(\d+)""", r.read().decode("utf-8", "replace"))
            got.append(m.group(1) if m else None)
    except Exception as e:
        got.append(repr(e)[:60])
bad = [g for g in got if g != want]
print(">>> deployed arm check: want kib=%s, %d/20 replies match%s" % (want, 20 - len(bad), "" if not bad else " -- got %r" % sorted(set(map(str, bad)))))
sys.exit(1 if bad else 0)
PY
    fi
    if [ "$DEPLOY_ONLY" = 1 ]; then
        echo ">>> DEPLOY-ONLY pilot bench: total=$REQUESTS_PER_RUN concurrency=$CONCURRENCY duration=$duration repeat=1"
        $SAQEF run --platform "$platform" --total "$REQUESTS_PER_RUN" --concurrency "$CONCURRENCY" \
            --duration "$duration" --repeat 1 --idle-w "$idle_w" --out "$out"
        echo ">>> teardown"
        $SAQEF teardown --platform "$platform"
        [ "$platform" = "knative" ] && wait_knative_clean
        sleep 5
        return 0
    fi
    [ "$SETTLE_AFTER_VERIFY" = 1 ] && settle_after_verify
    echo ">>> run: total=$TOTAL concurrency=$CONCURRENCY duration=$duration repeat=$REPEAT out=$out"
    $SAQEF run --platform "$platform" --total "$TOTAL" --concurrency "$CONCURRENCY" \
        --duration "$duration" --repeat "$REPEAT" ${WARMUP:+--warmup "$WARMUP"} --idle-w "$idle_w" --out "$out"
    echo ">>> gates"
    $SAQEF gates --out "$gate_out"
    if [ "$CPU_PROBE_S" -gt 0 ]; then
        # Native --idle-probe: same stack state as the bench, zero traffic,
        # exactly one measurement. Saves a summary with the same cpu_sec
        # fields; dividing by wall_s gives the per-second background CPU rate.
        # This is the direct measurement of what the concurrency sweep's
        # window-length effect actually was -- the driver and the paper must
        # subtract this from CP/fn CPU-s before quoting per-inv-or-window costs.
        echo ">>> idle-CPU probe (${CPU_PROBE_S}s, zero traffic)"
        local pout="$REPO/results/idle_probe_${STAMP}/$platform"
        if [ -e "${pout}_quick" ] || [ -e "$pout" ]; then
            die "idle-probe outdir already exists for $platform (${pout}_quick) -- refusing to clobber"
        fi
        # repeat=1 writes to <out>_quick, mirroring resolve_outdir() in `saqef`;
        # pass the bare path and let the CLI apply the suffix (not pre-suffixed).
        $SAQEF run --platform "$platform" --idle-probe --duration "$CPU_PROBE_S" \
            --repeat 1 --idle-w "$idle_w" --out "$pout"
    fi
    echo ">>> teardown"
    $SAQEF teardown --platform "$platform"
    [ "$platform" = "knative" ] && wait_knative_clean
    sleep 5
}

# ---------------------------------------------------------------------------
# calibrate_all -- measure each stack state's idle package power, save medians
# ---------------------------------------------------------------------------
calibrate_all() {
    banner "idle-w calibration ($IDLE_REPS x 60 s per state; saves to $CALIB_DIR)"
    # order matters for isolation: OF -> Fn -> Kn (condition B = bench state) -> OW
    echo ">>> state: bare substrate (k3s + knative-serving + kourier, no function)"
    IDLE_BARE=$(rapl_series bare "bare k3s+knative substrate")

    echo ">>> state: OpenFaaS up (stack + hello @ 16, zero traffic)"
    $SAQEF deploy --platform openfaas
    $SAQEF scale --platform openfaas --replicas 16
    sleep 10
    IDLE_OF=$(rapl_series openfaas "OpenFaaS control plane + hello@16")
    $SAQEF teardown --platform openfaas
    sleep 5

    echo ">>> state: Fn up (fnserver + registered function, zero traffic)"
    $SAQEF deploy --platform fn
    sleep 10
    IDLE_FN=$(rapl_series fn "Fn fnserver")
    $SAQEF teardown --platform fn
    sleep 5

    echo ">>> state: Knative WITH hello @ 16 (exact bench-time stack state)"
    $SAQEF deploy --platform knative
    $SAQEF scale --platform knative --replicas 16
    $SAQEF verify --platform knative
    sleep 10
    IDLE_KN=$(rapl_series knative "Knative serving + hello@16")
    $SAQEF teardown --platform knative
    wait_knative_clean
    sleep 5

    echo ">>> state: OpenWhisk standalone up, zero traffic"
    $SAQEF deploy --platform openwhisk
    sleep 10
    IDLE_OW=$(rapl_series openwhisk "OpenWhisk standalone")
    $SAQEF teardown --platform openwhisk
    sleep 5
}

# ---------------------------------------------------------------------------
# calib_median STATE DEFAULT -- median from the saved calib file if present,
# else DEFAULT. Lets a later `--skip-idle-calib` invocation (recovery) reuse
# the fresh values actually measured in the first run instead of the hardcoded
# fallbacks.
# ---------------------------------------------------------------------------
calib_median() {
    local state="$1" default="$2" explicit="$3" recorded=""
    # FIXED 2026-10-01 (expert review): an explicit --idle-w-* must WIN over a
    # saved calib file. The old signature called `calib_median state default`
    # with default already replaced by "$IDLE_OF:-$DEFAULT_OF}", so a saved
    # calib file silently shadowed a deliberate --idle-w-* override -- the
    # opposite of what the echo below claimed. Explicit -> saved calib -> default.
    if [ -n "$explicit" ]; then echo "$explicit"; return; fi
    if [ -f "$CALIB_DIR/idle_w_$state.txt" ]; then
        recorded=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["median_w"])' \
            "$CALIB_DIR/idle_w_$state.txt" 2>/dev/null)
        [ -n "$recorded" ] && { echo "$recorded"; return; }
    fi
    echo "$default"
}

# ===========================================================================
echo "SAQEF publication-lock session"
echo "  repo   : $REPO"
echo "  stamp  : $STAMP"
echo "  calib  : $CALIB_DIR"
echo "  legs   : $PLATFORMS (total=$TOTAL concurrency=$CONCURRENCY repeat=$REPEAT)"
echo "  NOTE   : bare shell, agents QUIT. Each leg self-certifies quiet (15% gate)."
[ "$DRY_RUN" = 1 ] && echo "  MODE   : DRY-RUN -- print plan only"

check_preconditions

if [ "$SKIP_IDLE" = 1 ]; then
    IDLE_OF=$(calib_median openfaas "$DEFAULT_OF" "${IDLE_OF:-}")
    IDLE_FN=$(calib_median fn "$DEFAULT_FN" "${IDLE_FN:-}")
    IDLE_KN=$(calib_median knative "$DEFAULT_KN" "${IDLE_KN:-}")
    IDLE_OW=$(calib_median openwhisk "$DEFAULT_OW" "${IDLE_OW:-}")
    echo ">> --skip-idle-calib: using idle_w OF=$IDLE_OF FN=$IDLE_FN KN=$IDLE_KN OW=$IDLE_OW"
    echo "   (explicit --idle-w-* override the saved calib; saved calib overrides the"
    echo "   hardcoded defaults -- precedence is now the same order as the echo)"
    banner "skipping idle-w calibration"
elif [ "$DRY_RUN" = 1 ]; then
    # never measured in dry-run; show what the plan would measure with
    IDLE_OF="$DEFAULT_OF" IDLE_FN="$DEFAULT_FN" IDLE_KN="$DEFAULT_KN" IDLE_OW="$DEFAULT_OW"
    banner "DRY-RUN would calibrate idle-w per state (5 states x $IDLE_REPS x 60 s) and use the measured medians; placeholders shown below"
else
    calibrate_all
fi

case "$PLATFORMS" in
    *of*) run_leg openfaas "$IDLE_OF" ;;
esac
case "$PLATFORMS" in
    *fn*) run_leg fn "$IDLE_FN" ;;
esac
case "$PLATFORMS" in
    *kn*) run_leg knative "$IDLE_KN" ;;
esac
case "$PLATFORMS" in
    *ow*) run_leg openwhisk "$IDLE_OW" ;;
esac

# ===========================================================================
# validation + lock summary
# ===========================================================================
if [ "$DRY_RUN" = 1 ]; then
    banner "DRY-RUN complete -- nothing was measured. Remove --dry-run and run for real "
    echo "in a bare shell with agents quit."
    exit 0
fi
banner "gate validation + lock summary"
python3 - "$REPO" "$STAMP" "$PLATFORMS" "$REPEAT" "$IDLE_OF" "$IDLE_FN" "$IDLE_KN" "$IDLE_OW" "$MAX_DRIFT_PCT" "$DISCARD_WARMUP" "$MAX_SAMPLE_GAP_S" "$RAPL_FIT_WARN" "$IDLE_SOURCE" "$DRIFT_TWO_SIDED" <<'PY'
import glob, json, os, statistics, sys
repo, stamp, platforms, repeat, w_of, w_fn, w_kn, w_ow = sys.argv[1:9]
# max_drift_pct is the 9th arg, appended after the existing 8 so that callers
# written against the older argv shape (tests/test_saqef_cli.py invokes this
# block directly with 8 args) keep working. Absent -> the 20% default.
max_drift = float(sys.argv[9]) if len(sys.argv) > 9 else 20.0
# discard_warmup is the 10th arg, same backward-compatible convention. It drops
# the first N repeats from the gate, because a cold JIT/classload makes the
# first repeat an outlier that inflates within-leg variance (OpenWhisk share SD
# at c=1: 4.09 with run_1, 0.29 without).
discard_warmup = int(sys.argv[10]) if len(sys.argv) > 10 else 0
# max_sample_gap_s is the 11th arg, same convention. Gate threshold for the real
# worst interval the CPU sampler went blind for; must match --max-sample-gap.
max_sample_gap_s = float(sys.argv[11]) if len(sys.argv) > 11 else 1.0
# rapl_fit_warn is the 12th arg, same convention. 1 -> RAPL FIT goes to the
# leg's "warnings" instead of "problems" (see RAPL_FIT_WARN in the shell part).
rapl_fit_warn = (sys.argv[12] == "1") if len(sys.argv) > 12 else False
# idle_source is the 13th arg, same convention: the calibration dir the
# --idle-w-* values were read from when this stamp did not calibrate itself
# (run_final.sh calibrates once under lock_final_calib, then runs each leg
# under its own stamp). "" -> unknown source.
idle_source = sys.argv[13] if len(sys.argv) > 13 else ""
# drift_two_sided is the 14th arg, same convention. 1 -> a throughput rise beyond
# max_drift also fails (runbook 28.9). Absent/0 -> loss only, as before.
drift_two_sided = (sys.argv[14] == "1") if len(sys.argv) > 14 else False
w = {"openfaas": w_of, "fn": w_fn, "knative": w_kn, "openwhisk": w_ow}
order = {"openfaas": "OpenFaaS", "fn": "Fn", "knative": "Knative", "openwhisk": "OpenWhisk"}
# short codes used by --platforms (of,fn,kn,ow), matching run_lock_session.sh's own case-statement matching
short = {"openfaas": "of", "fn": "fn", "knative": "kn", "openwhisk": "ow"}
quick_suffix = "_quick" if int(repeat) < 5 else ""
summary, all_ok = {}, True
print("%-10s %8s %6s %6s %7s %8s %7s %s" % (
    "platform", "share%", "CV%", "p50ms", "p99ms", "rps", "host_sat", "gates"))
for plat in [p for p in ("openfaas", "fn", "knative", "openwhisk") if short[p] in platforms.split(",")]:
    out = os.path.join(repo, "results", "%s_cpubound_lock_%s%s" % (plat, stamp, quick_suffix))
    try:
        s = json.load(open(os.path.join(out, "summary.json")))
        all_runs = sorted(glob.glob(os.path.join(out, "run_*")))
    except Exception as e:
        print("%-10s FAIL -- no summary under %s (%s)" % (order[plat], out, e)); all_ok = False
        # Full key set, nulls for the values that need a summary.json. The gate
        # table and every downstream reader (runbook 24.3 rule 1 walks
        # cp_dynamic_share_pct / ambient_present on all four platforms) index
        # these keys, so a leg that failed for lack of a summary must still
        # carry them rather than 4 keys and a KeyError later.
        summary[plat] = {"label": order[plat], "cp_dynamic_share_pct": None,
                         "outdir": os.path.relpath(out, repo), "idle_w_used": w.get(plat),
                         "cv_pct": None, "host_saturation_pct": None,
                         "ambient_present": False,
                         "gates_ok": False,
                         "problems": ["no summary.json (%s)" % type(e).__name__],
                         "runs": []}
        continue
    # Discard the warm-up repeats BEFORE gating: a cold first repeat is an
    # outlier, and gating on it can fail a leg for a transient that the protocol
    # explicitly throws away.
    runs = all_runs[discard_warmup:] if discard_warmup else all_runs
    if discard_warmup:
        dropped = ", ".join(os.path.basename(p) for p in all_runs[:discard_warmup])
        print("%-10s note  -- discarded warm-up run(s): %s" % (order[plat], dropped))
    share = s.get("cp_dynamic_share_pct")
    problems = []
    warnings = []   # recorded and printed, but do not fail the leg
    # quick-tier (REPEAT<5, _quick outdir) is exploratory by design and must
    # NOT fail the lock gate on run count -- only full REPEAT=5 citable sessions
    # require exactly 5 USABLE runs. Any other gate (host_plausible, delta_check,
    # rapl_wrap, ambient) still applies to quick-tier runs.
    if int(repeat) >= 5 and len(runs) != 5:
        problems.append("runs=%d (want 5)" % len(runs))
    run_details = []
    # Per-run usable verdict, computed once here. runs.json stays the harness's raw
    # output; anything that aggregates runs reads acceptance.json instead (runbook 23).
    run_verdicts = [{"name": os.path.basename(p), "usable": False,
                     "problems": ["warm-up discarded"]}
                    for p in all_runs[:discard_warmup]] if discard_warmup else []
    for p in runs:
        try:
            r = json.load(open(os.path.join(p, "summary.json")))
        except Exception:
            problems.append("%s unreadable" % os.path.basename(p))
            run_verdicts.append({"name": os.path.basename(p), "usable": False,
                                 "problems": ["unreadable"]})
            continue
        nm = os.path.basename(p)
        n_before = len(problems)
        # Raw energies, so the RAPL FIT verdict can be re-derived (and its sign
        # seen) offline. Pre-2026-10-02 runs carry only the error %, so the two
        # joule fields read null there. Per-run verdicts: run_verdicts below,
        # written to the leg's acceptance.json.
        # One output name, "rapl_validation_err_pct" (what the corpus uses).
        # rapl_fit_err_pct is accepted on READ only: 90d153f wrote it for the
        # 2026-10-02 legs, and those summaries are the only ones that have it.
        fit = r.get("rapl_validation_err_pct")
        if fit is None:
            fit = r.get("rapl_fit_err_pct")
        run_details.append({
            "name": nm,
            "e_model_j": r.get("e_model_j"),
            "e_rapl_j": r.get("e_rapl_j"),
            "rapl_validation_err_pct": fit,
            "rapl_wrap": r.get("rapl_wrap"),
            "rapl_available": r.get("rapl_available"),
        })
        if r.get("host_plausible") is not True:
            problems.append("%s host_plausible" % nm)
        dm = r.get("delta_check_map") or {}
        if dm and any(v != "ok" for v in dm.values()):
            problems.append("%s delta_check" % nm)
        if r.get("rapl_wrap") not in (None, "none"):
            problems.append("%s rapl_wrap=%s" % (nm, r.get("rapl_wrap")))
        # RAPL FIT gate (added 2026-10-01). The harness flags
        # rapl_validation_err_pct > 15 as "NOT citable" in saqef's own table,
        # but nothing here read it: tier1ow8 printed a FIT DEGRADED warning for
        # runs 4 and 5 (24.7%, 29.2%) and this gate still reported ALL GATES OK.
        # Same key and same 15% threshold as saqef_harness.py/saqef so the lock
        # verdict can never be greener than the per-run table. Uses `fit`, which
        # already folded in the read-only 90d153f alias -- reading the key again
        # here would silently skip the gate on the 2026-10-02 legs, which is
        # exactly the "gate reports OK on a FIT DEGRADED run" bug above.
        re_ = fit
        if re_ is not None and re_ > 15.0:
            (warnings if rapl_fit_warn else problems).append(
                "%s RAPL FIT %.1f%% (>15%%, NOT citable)" % (nm, re_))
        # SAMPLING QUALITY gate. sample_totals() computes the real worst gap
        # between CPU samples and sets sampling_gap_ok=False when it exceeds the
        # harness's --max-sample-gap. The harness only WARNED on that, so a leg
        # with a multi-second stall in the middle of the measurement window
        # could still be cited -- the per-invocation CPU figures would be
        # averaged over an interval the instrument never actually saw.
        #
        # The gate compares the MEASURED gap against its OWN --max-sample-gap
        # rather than trusting sampling_gap_ok, which already embeds the
        # harness's threshold: trusting the boolean would make the gate's
        # threshold decorative. The boolean is used only when no number is
        # available. Only a definite failure fails; absent keys stay silent so
        # pre-2026-10-01 datasets are unaffected.
        gap_s = r.get("sampling_max_gap_s")
        gap_bad = (gap_s is not None and gap_s > max_sample_gap_s) \
            or (gap_s is None and r.get("sampling_gap_ok") is False)
        if gap_bad:
            problems.append("%s SAMPLING GAP %s (limit %.2fs, window not covered)" % (
                nm, ("%.2fs" % gap_s) if gap_s is not None else "unknown",
                max_sample_gap_s))
        # runbook #6/#12: a run cut short by the loadgen kill-switch completes
        # fewer requests than asked for and/or silently falls back to the python
        # loadgen. Both used to print OK (nothing checked either) -- see the
        # lock2 OpenWhisk 1993/10000 incident.
        req, want = r.get("requests"), r.get("total_requested")
        if req is not None and want is not None and req != want:
            problems.append("%s INCOMPLETE %s/%s" % (nm, req, want))
        env = r.get("env") or {}
        if env.get("loadgen_fallback"):
            problems.append("%s LOADGEN FALLBACK (%s!=%s)" % (nm, env.get("loadgen"), env.get("loadgen_requested")))
        # A run whose successes fall short is not usable either (2026-10-03 OW 512k:
        # the JVM died mid-run 3, and runs 4-6 recorded 3000 requests, 0 successes).
        if env.get("burst_size"):
            # W3 bursty arrivals (runbook §31): failed requests are the measurement there, not
            # a broken run, so the 99 % rule below does not apply. A run with no success at all
            # (platform down) is still unusable. Closed-loop runs never set burst_size.
            if r.get("successes") is not None and r.get("successes") < 1:
                problems.append("%s NO SUCCESSES 0/%s (burst mode)" % (nm, want))
        elif r.get("successes") is not None and want and r.get("successes") < 0.99 * want:
            problems.append("%s SUCCESSES %s/%s" % (nm, r.get("successes"), want))
        # W4 cold start (runbook §32): a run whose pool reset failed, or whose pool was not
        # empty when the window opened, did not measure a cold start. Runs without a `pool`
        # record (every other workload) are unaffected.
        pool = r.get("pool") or {}
        if pool.get("reset_requested"):
            if pool.get("reset_rc") != 0:
                problems.append("%s POOL RESET FAILED rc=%s" % (nm, pool.get("reset_rc")))
            elif pool.get("at_start") != 0:
                problems.append("%s POOL NOT EMPTY (%s function containers at window start)" % (nm, pool.get("at_start")))
        run_verdicts.append({"name": nm, "usable": len(problems) == n_before,
                             "problems": problems[n_before:]})
    # MONOTONE DRIFT gate (added 2026-10-01). Every tier1ow* leg degraded
    # monotonically run-over-run (c=8: 57.0 -> 29.1 rps, host_cpu 307 -> 446
    # CPU-s) while the per-run gates all passed, because each repeat is an
    # independent snapshot: nothing compared them. A median over a decaying
    # sequence is not a central estimate of anything, and the whole 5-run
    # median is what lands in the paper. Compare first vs last repeat and
    # fail if the box lost more than --max-drift-pct of throughput.
    drift_pct = None   # recorded for every leg, so passing margins are visible too
    try:
        rp = [json.load(open(os.path.join(p, "summary.json"))).get("throughput_rps")
              for p in runs]
        if len(rp) >= 3 and all(v for v in rp):
            drop = (rp[0] - rp[-1]) / rp[0] * 100.0
            drift_pct = round(drop, 2)
            if drop > max_drift or (drift_two_sided and -drop > max_drift):
                # Name the actual run dirs: with --discard-warmup the surviving
                # run_N no longer starts at 1, so a hardcoded "run_1..run_N"
                # would point at a run this gate deliberately ignored.
                first, last = os.path.basename(runs[0]), os.path.basename(runs[-1])
                problems.append("DRIFT %s..%s throughput %.1f -> %.1f rps "
                                "(%.1f%% %s > %.0f%%) -- median is not citable"
                                % (first, last, rp[0], rp[-1], abs(drop),
                                   "loss" if drop > 0 else "rise", max_drift))
    except Exception as e:
        problems.append("drift check unreadable (%s)" % type(e).__name__)
    # ambient/quiet-gate is measured once per leg, before the whole --repeat
    # batch starts (saqef_harness.py main(), not run_once()), so it only ever
    # lands on the leg-level merged summary.json -- never on run_N/summary.json.
    amb = s.get("ambient") or {}
    if amb.get("load_pct") is not None and amb.get("load_pct") > amb.get("threshold_pct", 15):
        problems.append("leg ambient %.1f%%" % amb["load_pct"])
    elif not amb:
        problems.append("NO ambient field on leg summary.json (quiet gate not in measurement path)")
    # FIXED 2026-10-01 (expert review): this read was unguarded, so a --repeat 1
    # invocation (the tier-1 OpenWhisk duration pilot) had no runs.json at all and
    # raised FileNotFoundError here -- outside any try -- killing the whole
    # session ~2h in with "OW c=1 pilot failed". A missing/short runs.json is a
    # GATE PROBLEM to report, never a traceback.
    try:
        shares = [r.get("cp_dynamic_share_pct")
                  for r in json.load(open(os.path.join(out, "runs.json")))]
    except Exception as e:
        shares = []
        problems.append("no readable runs.json (%s)" % type(e).__name__)
    # FIXED 2026-10-03 (runbook 27.11): the headline share came from the leg's
    # summary.json, i.e. the median over ALL runs including the discarded warm-up
    # (final_ OW: 76.715 cited vs 76.57 over usable runs). Share and CV are now
    # taken over the usable runs only, the same runs the gates saw.
    # A run with no successes has a null share; median() over a None used to crash
    # the whole gate step (2026-10-03, every OW 512k leg).
    if discard_warmup and shares:
        shares = [x for x in shares[discard_warmup:] if x is not None]
        share = round(statistics.median(shares), 3) if shares else None
    shares = [x for x in shares if x is not None]
    cv = (statistics.pstdev(shares) / statistics.mean(shares) * 100.0) if shares else float("nan")
    sat = s.get("host_saturation_pct")
    qos = s.get("latency_ms") or {}
    ok = "OK" if not problems and share is not None else "FAIL"
    if problems:
        ok = "FAIL"
        all_ok = False
    print("%-10s %8s %6.1f %6.1f %7.1f %8.1f %7s %s %s" % (
        order[plat], (share if share is not None else "n/a"), cv,
        qos.get("p50"), qos.get("p99"), s.get("throughput_rps") or 0,
        ("%.0f" % sat) if sat is not None else "n/a",
        ok, "; ".join(problems)))
    for wmsg in warnings:
        print("%-10s   WARN %s (--rapl-fit-warn: recorded, not gating)" % ("", wmsg))
    # Stability of the two CITED quantities over the usable runs, recorded (not gated)
    # for every leg (runbook 28.9: the throughput drift gate never looks at them).
    stab = {}
    try:
        raw = json.load(open(os.path.join(out, "runs.json")))
        names = {v["name"] for v in run_verdicts if v["usable"]}
        use = [r for i, r in enumerate(raw) if "run_%d" % (i + 1) in names and r.get("successes")]
        for key, lab in (("control_plane", "cp"), ("function", "fn")):
            xs = [r["cpu_sec"][key] / r["successes"] * 1000.0 for r in use]
            if len(xs) >= 2:
                stab[lab + "_ms_inv_runs"] = [round(x, 4) for x in xs]
                stab[lab + "_cv_pct"] = round(statistics.stdev(xs) / statistics.mean(xs) * 100.0, 2)
                stab[lab + "_drift_pct"] = round((xs[-1] - xs[0]) / xs[0] * 100.0, 2)
    except Exception as e:
        stab = {"stability_unreadable": type(e).__name__}
    acc = {"stamp": stamp, "platform": plat, "leg_gates_ok": (ok == "OK"),
           "leg_problems": problems, "warnings": warnings,
           "drift_pct": drift_pct, "max_drift_pct": max_drift,
           "drift_two_sided": drift_two_sided, "stability": stab,
           "usable_runs": [v["name"] for v in run_verdicts if v["usable"]],
           "runs": run_verdicts}
    json.dump(acc, open(os.path.join(out, "acceptance.json"), "w"), indent=2)
    summary[plat] = {"label": order[plat], "cp_dynamic_share_pct": share,
                     "outdir": os.path.relpath(out, repo), "idle_w_used": w.get(plat),
                     "cv_pct": round(cv, 2), "host_saturation_pct": sat,
                     # leg-level s, NOT run_N: ambient never lands on run_N
                     # summaries (see the ambient gate above).
                     "ambient_present": "ambient" in s,
                     "gates_ok": (ok == "OK"),
                     # Same strings the table prints. Before 2026-10-02 they
                     # went to stdout only, so a FAIL in lock_summary.json
                     # could not be traced without replaying every gate.
                     "problems": problems,
                     "warnings": warnings,
                     "runs": run_details}
calib_dir = os.path.join(repo, "results", "idle_w_calibration", "lock_%s" % stamp)
# rapl_series() writes one idle_w_<state>.txt FILE per state. This used to count
# subdirectories, which calibration never creates, so every session -- including
# ones that did recalibrate -- was labelled "NOT recalibrated".
calib_states = sorted(os.listdir(calib_dir)) if os.path.isdir(calib_dir) else []
calib_states = [d for d in calib_states
                if d.startswith("idle_w_") and d.endswith(".txt")
                and os.path.isfile(os.path.join(calib_dir, d))]
def _calib_files(d):
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.startswith("idle_w_") and f.endswith(".txt")
                  and os.path.isfile(os.path.join(d, f)))
src_dir = os.path.abspath(idle_source) if idle_source else ""
src_states = _calib_files(src_dir) if src_dir else []
if calib_states:
    idle_note = ("idle-w recalibrated this session (%d state(s) under %s)"
                 % (len(calib_states), os.path.relpath(calib_dir, repo)))
elif src_states:
    # Name the source and check the values actually used against its medians,
    # so the note cannot claim a calibration the numbers did not come from.
    mism = []
    for plat in order:
        f = os.path.join(src_dir, "idle_w_%s.txt" % plat)
        if not w.get(plat) or not os.path.isfile(f):
            continue
        try:
            med = float(json.load(open(f))["median_w"])
        except (OSError, ValueError, KeyError):
            mism.append("%s: unreadable" % plat); continue
        if abs(float(w[plat]) - med) > 1e-6:
            mism.append("%s: used %s, calib %s" % (plat, w[plat], med))
    rel = os.path.relpath(src_dir, repo)
    if mism:
        idle_note = ("idle-w NOT from %s -- --idle-w-* values differ from its medians (%s)"
                     % (rel, "; ".join(mism)))
    else:
        idle_note = ("idle-w calibrated earlier in this session (%d state(s) under %s); "
                     "this leg used those medians via --idle-w-*" % (len(src_states), rel))
else:
    idle_note = ("idle-w NOT recalibrated this session -- the values above are "
                 "INHERITED medians passed via --idle-w-*; %s holds no state files. "
                 "Do not read this as a fresh calibration." % os.path.relpath(calib_dir, repo))
meta = {"stamp": stamp, "platforms": order, "idle_w_by_platform": w,
        "max_drift_pct": max_drift,
        "max_sample_gap_s": max_sample_gap_s,
        # "warn" means the >15% model residual did not gate this session. It
        # measures disagreement with the retired 3.5 W/core model, not RAPL's
        # quality: model-based energy is not citable, RAPL-based energy is
        # (runbook 25.1, 25.2, 25.6, 27.5).
        "rapl_fit_gate": "warn" if rapl_fit_warn else "fail",
        "discard_warmup": discard_warmup,
        "usable_runs_per_leg": int(repeat) - discard_warmup,
        "idle_w_provenance": idle_note,
        "notes": ["single box state, back-to-back legs, quiet gate active (precondition only)",
                  idle_note]}
outp = os.path.join(repo, "results", "lock_session_%s" % stamp, "lock_summary.json")
json.dump({"session": meta, "platforms": summary, "all_gates_ok": all_ok}, open(outp, "w"), indent=2)
print("\nlock summary written to %s" % os.path.relpath(outp, repo))
if not all_ok:
    sys.exit("FAIL: one or more legs failed the gate check -- do NOT cite from this session until resolved.")
tier = "quick-tier (REPEAT<5, exploratory, NOT citable until promoted to REPEAT=5)" if int(repeat) < 5 else "citable"
print("ALL GATES OK -- session is %s under the same-discipline rules (quiet-gated, same day, same box)." % tier)
if rapl_fit_warn:
    print("NOTE: --rapl-fit-warn was set -- CP/fn shares are gated as usual; the >15%% model "
          "residual was recorded, not gated. Cite RAPL-based energy (e_rapl_j - idle_w*wall), "
          "never the 3.5 W/core model's energy_J (runbook 25.2, 27.5).")
PY

echo
echo "DONE. Next (agent-safe): update figures/make_figures.py REGIMES + paper numbers,"
echo "re-anchor regression refs via tools/reanchor_and_kn_idle.sh, commit."
