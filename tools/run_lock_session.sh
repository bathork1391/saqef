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
IDLE_OF="" IDLE_FN="" IDLE_KN="" IDLE_OW=""
IDLE_BARE=""
DEFAULT_OF=4.3 DEFAULT_FN=4.3 DEFAULT_KN=4.561 DEFAULT_OW=4.3

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
# rapl_series STATE LABEL  -- N repeated 60 s RAPL reads for the CURRENT stack
# state; prints each read + median, and saves them under $CALIB_DIR. Reuses
# saqef_harness.py's wraparound-guarded read (same guard as the harness itself).
# Prints the MEDIAN on stdout (the only thing callers consume).
# ---------------------------------------------------------------------------
rapl_series() {
    local state="$1" label="$2"
    echo "  -- [$label] $IDLE_REPS x 60 s RAPL reads (zero traffic)..."
    local out="$CALIB_DIR/idle_w_$state.txt"
    local med
    med=$(python3 - "$REPO" "$IDLE_REPS" "$out" "$label" <<'PY'
import json, os, sys, time, statistics
repo, n, out, label = sys.argv[1:]
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
run_leg() {
    local platform="$1" idle_w="$2"
    local out="$REPO/results/${platform}_cpubound_lock_$STAMP"
    banner "$(echo $platform | tr a-z A-Z) leg (idle_w=$idle_w)"
    if [ "$DRY_RUN" = 1 ]; then
        echo "DRY-RUN: would run: deploy --platform $platform"
        case "$platform" in
            openfaas|knative) echo "DRY-RUN: scale --platform $platform --replicas 16" ;;
        esac
        echo "DRY-RUN: verify --platform $platform"
        echo "DRY-RUN: run --platform $platform --total $TOTAL --concurrency $CONCURRENCY --repeat $REPEAT --idle-w $idle_w --out $out"
        echo "DRY-RUN: gates --out $out ; teardown --platform $platform"
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
    echo ">>> verify"
    $SAQEF verify --platform "$platform"
    echo ">>> run: total=$TOTAL concurrency=$CONCURRENCY repeat=$REPEAT out=$out"
    $SAQEF run --platform "$platform" --total "$TOTAL" --concurrency "$CONCURRENCY" \
        --repeat "$REPEAT" --idle-w "$idle_w" --out "$out"
    echo ">>> gates"
    $SAQEF gates --out "$out"
    echo ">>> teardown"
    $SAQEF teardown --platform "$platform"
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
    local state="$1" default="$2"
    if [ -f "$CALIB_DIR/idle_w_$state.txt" ]; then
        python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["median_w"])' \
            "$CALIB_DIR/idle_w_$state.txt" 2>/dev/null && return
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
    IDLE_OF=$(calib_median openfaas "${IDLE_OF:-$DEFAULT_OF}")
    IDLE_FN=$(calib_median fn "${IDLE_FN:-$DEFAULT_FN}")
    IDLE_KN=$(calib_median knative "${IDLE_KN:-$DEFAULT_KN}")
    IDLE_OW=$(calib_median openwhisk "${IDLE_OW:-$DEFAULT_OW}")
    echo ">> --skip-idle-calib: using idle_w OF=$IDLE_OF FN=$IDLE_FN KN=$IDLE_KN OW=$IDLE_OW"
    echo "   (explicit --idle-w-* overrides take precedence; otherwise the saved calib"
    echo "   medians are reused, or these defaults if no calib files exist)"
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
python3 - "$REPO" "$STAMP" "$PLATFORMS" "$IDLE_OF" "$IDLE_FN" "$IDLE_KN" "$IDLE_OW" <<'PY'
import glob, json, os, statistics, sys
repo, stamp, platforms, w_of, w_fn, w_kn, w_ow = sys.argv[1:]
w = {"openfaas": w_of, "fn": w_fn, "knative": w_kn, "openwhisk": w_ow}
order = {"openfaas": "OpenFaaS", "fn": "Fn", "knative": "Knative", "openwhisk": "OpenWhisk"}
summary, all_ok = {}, True
print("%-10s %8s %6s %6s %7s %8s %7s %s" % (
    "platform", "share%", "CV%", "p50ms", "p99ms", "rps", "host_sat", "gates"))
for plat in [p for p in ("openfaas", "fn", "knative", "openwhisk") if p in platforms.split(",")]:
    out = os.path.join(repo, "results", "%s_cpubound_lock_%s" % (plat, stamp))
    try:
        s = json.load(open(os.path.join(out, "summary.json")))
        runs = sorted(glob.glob(os.path.join(out, "run_*")))
    except Exception as e:
        print("%-10s FAIL -- no summary under %s (%s)" % (order[plat], out, e)); all_ok = False; continue
    share = s.get("cp_dynamic_share_pct")
    problems = []
    if len(runs) != 5:
        problems.append("runs=%d (want 5)" % len(runs))
    for p in runs:
        try:
            r = json.load(open(os.path.join(p, "summary.json")))
        except Exception:
            problems.append("%s unreadable" % os.path.basename(p)); continue
        if r.get("host_plausible") is not True:
            problems.append("%s host_plausible" % os.path.basename(p))
        dm = r.get("delta_check_map") or {}
        if dm and any(v != "ok" for v in dm.values()):
            problems.append("%s delta_check" % os.path.basename(p))
        if r.get("rapl_wrap") not in (None, "none"):
            problems.append("%s rapl_wrap=%s" % (os.path.basename(p), r.get("rapl_wrap")))
        amb = r.get("ambient") or {}
        if amb.get("load_pct") is not None and amb.get("load_pct") > amb.get("threshold_pct", 15):
            problems.append("%s ambient %.1f%%" % (os.path.basename(p), amb["load_pct"]))
        elif not amb:
            problems.append("%s NO ambient field (quiet gate not in measurement path)" % os.path.basename(p))
    shares = [r["cp_dynamic_share_pct"] for r in json.load(open(os.path.join(out, "runs.json")))]
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
    summary[plat] = {"label": order[plat], "cp_dynamic_share_pct": share,
                     "outdir": os.path.relpath(out, repo), "idle_w_used": w.get(plat),
                     "cv_pct": round(cv, 2), "host_saturation_pct": sat,
                     "ambient_present": all("ambient" in json.load(open(p + "/summary.json")) for p in runs),
                     "gates_ok": (ok == "OK")}
meta = {"stamp": stamp, "platforms": order, "idle_w_by_platform": w,
        "notes": ["single box state, back-to-back legs, quiet gate active (precondition only)",
                  "idle-w recalibrated this session (results/idle_w_calibration/lock_%s)" % stamp]}
outp = os.path.join(repo, "results", "lock_session_%s" % stamp, "lock_summary.json")
json.dump({"session": meta, "platforms": summary, "all_gates_ok": all_ok}, open(outp, "w"), indent=2)
print("\nlock summary written to %s" % os.path.relpath(outp, repo))
if not all_ok:
    sys.exit("FAIL: one or more legs failed the gate check -- do NOT cite from this session until resolved.")
print("ALL GATES OK -- session is citable under the same-discipline rules (N=5, quiet-gated, same day, same box).")
PY

echo
echo "DONE. Next (agent-safe): update figures/make_figures.py REGIMES + paper numbers,"
echo "re-anchor regression refs via tools/reanchor_and_kn_idle.sh, commit."
