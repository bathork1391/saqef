#!/usr/bin/env bash
# One-file driver for the two remaining quiet-box box tasks (AGENTS.md "Box task"):
#
#   (b) Re-anchor the Fn/OpenFaaS regression references (11.60/7.67) via a same-day
#       OLD-RUNNER A/B under the now-self-certifying ambient-load quiet gate. The
#       old runners (run_openfaas.sh / run_saqef.sh) are the authoritative values;
#       `saqef regression` (the refactored CLI path) must then reproduce them within
#       tolerance. On success the script applies the fresh references to
#       metrics/cpubound.json (backed up first) with provenance in reference_notes,
#       then runs `saqef regression` to verify the new path agrees.
#
#   (c) Knative idle-w N>=3 recalibration (finding #13): the always-on idle premium
#       was calibrated from single 60 s RAPL reads (11.14 / 7.01 / 4.91 W across three
#       sessions -- single-digit watts is exactly where a one-sample read is fragile).
#       This measures N>=3 repeated 60 s reads (median + spread) in the two relevant
#       stack states: bare substrate (k3s+knative-serving+kourier, no hello) and WITH
#       hello at 16 replicas (the exact bench-time state). It reports both medians,
#       the premium (B - A), and the recommended --idle-w for a Knative bench (the
#       B median).
#
# PREREQS: run from a bare bash shell with opencode/agents QUIT. The clean legs
# enforce the ambient-load quiet gate (<=15% host busy over a 20 s window) and will
# refuse to start on a busy box -- that is the point. Platform images must be present
# as for any other citable run.
#
# Usage:
#   bash tools/reanchor_and_kn_idle.sh            # full: (b) then (c), applies refs (prompts)
#   bash tools/reanchor_and_kn_idle.sh --skip-legs  # skip the (b) bench legs; refs already
#                                                   # applied (e.g. after a crash at the
#                                                   # verify step) -> Fn teardown + regression
#                                                   # verify + (c). Saves ~25 min.
#   bash tools/reanchor_and_kn_idle.sh --dry-run  # measure + report only, never writes cpubound.json
#   bash tools/reanchor_and_kn_idle.sh --yes      # skip the apply confirmation prompt
#   bash tools/reanchor_and_kn_idle.sh --idle-reps 3   # N RAPL reads per condition (default 5)
#
# Results:
#   results/of_cpubound_crosscheck_<date>/   (openfaas old-runner A/B leg)
#   results/fn_cpubound_crosscheck_<date>/   (fn old-runner A/B leg)
#   printed RAPL medians for the two knative conditions (no files written by (c))
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%F)"
IDLE_REPS=5
DRY_RUN=0
ASSUME_YES=0
SKIP_LEGS=0
args=("$@")
i=0
while [ "$i" -lt "$#" ]; do
    arg="${args[$i]}"
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --yes) ASSUME_YES=1 ;;
        --skip-legs) SKIP_LEGS=1 ;;
        --idle-reps)
            i=$((i + 1))
            if [ "$i" -ge "$#" ]; then
                echo "--idle-reps requires a value" >&2; exit 2
            fi
            IDLE_REPS="${args[$i]}"
            ;;
        --idle-reps=*) IDLE_REPS="${arg#*=}" ;;
        --*) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
    i=$((i + 1))
done
case "$IDLE_REPS" in
    ''|*[!0-9]*) echo "--idle-reps must be a positive integer (got '$IDLE_REPS')" >&2; exit 2 ;;
esac

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO=sudo
fi

OF_OUT="results/of_cpubound_crosscheck_${STAMP}"
FN_OUT="results/fn_cpubound_crosscheck_${STAMP}"
CFG="$REPO/metrics/cpubound.json"

die() { echo "ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# validate_results OUTDIR  -> echo "median share  OK/FAIL  problems..."
# Checks the old-runner A/B leg produced a citable set: repeat runs present,
# host_plausible true everywhere, delta-check all "ok", no RAPL wraparound.
# Emits:  <median> <OK|FAIL> <problem-list>
# ---------------------------------------------------------------------------
validate_results() {
    python3 - "$1" <<'PY'
import glob, json, os, sys
out = sys.argv[1]
runs = sorted(glob.glob(os.path.join(out, "run_*")))
problems = []
if not runs:
    sys.exit("FAIL no run_* dirs under %s (env vars not passed through sudo?)" % out)
for p in runs:
    try:
        s = json.load(open(os.path.join(p, "summary.json")))
    except Exception as e:
        problems.append("%s: unreadable (%s)" % (os.path.basename(p), e))
        continue
    if s.get("host_plausible") is not True:
        problems.append("%s: host_plausible=%s" % (os.path.basename(p), s.get("host_plausible")))
    dm = s.get("delta_check_map") or {}
    if dm and any(v != "ok" for v in dm.values()):
        problems.append("%s: delta_check not all ok (%s)" % (os.path.basename(p), dm))
    if s.get("rapl_wrap") not in (None, "none"):
        problems.append("%s: rapl_wrap=%s" % (os.path.basename(p), s.get("rapl_wrap")))
try:
    med = json.load(open(os.path.join(out, "summary.json")))
except Exception as e:
    sys.exit("FAIL no outdir summary.json (%s)" % e)
share = med.get("cp_dynamic_share_pct")
if share is None:
    sys.exit("FAIL outdir summary.json has no cp_dynamic_share_pct")
print("%.2f %s %s" % (share, "OK" if not problems else "FAIL", "; ".join(problems)))
PY
}

# ---------------------------------------------------------------------------
# apply_references OF_SHARE FN_SHARE  -- backup + write metrics/cpubound.json
# ---------------------------------------------------------------------------
apply_references() {
    local of_share="$1" fn_share="$2"
    local ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    cp "$CFG" "$CFG.bak-$STAMP"
    python3 - "$of_share" "$fn_share" "$STAMP" "$ts" "$CFG" <<'PY'
import json, sys
of_share, fn_share, stamp, ts, cfg = sys.argv[1:]
d = json.load(open(cfg))
note = ("RE-ANCHORED %s (%s) from same-day old-runner A/B on the quiet box under the "
        "self-certifying ambient-load gate: fn=%s (outdir results/fn_cpubound_crosscheck_%s), "
        "openfaas=%s (outdir results/of_cpubound_crosscheck_%s). Supersedes the "
        "2026-08-06 references. Box-state drift, not a refactor change (old-runner A/B "
        "per TROUBLESHOOTING_RUNBOOK.md §2/§3)." % (stamp, ts, fn_share, stamp, of_share, stamp))
d["regression"]["reference_share_pct"]["fn"] = float(fn_share)
d["regression"]["reference_share_pct"]["openfaas"] = float(of_share)
d["regression"]["reference_notes"] = note
json.dump(d, open(cfg, "w"), indent=2)
print("applied: fn=%s openfaas=%s -> %s (backup %s.bak-%s)"
      % (fn_share, of_share, cfg, cfg, stamp))
PY
}

# ---------------------------------------------------------------------------
# rapl_w_series N  -> N repeated 60 s RAPL reads (package watts); prints each,
# then a median/min/max summary line. Works for ANY stack state currently up.
# Reuses saqef_harness.py's rapl_correct_wrap()/rapl_max_range_j() (the same
# wraparound guard the harness itself relies on) instead of a raw (e1-e0)/60
# delta -- a wrapped single-digit-watt read here would otherwise fold a
# garbage (possibly negative) figure into the median unflagged, exactly the
# fragility N>=3 reads are meant to fix. A wrapped/uncertain read is
# discarded and retried rather than counted.
# ---------------------------------------------------------------------------
rapl_w_series() {
    local n="$1"
    python3 - "$REPO" "$n" <<'PY'
import sys, time, statistics
repo, n = sys.argv[1], int(sys.argv[2])
sys.path.insert(0, repo)
import saqef_harness as h

reads = []
i = 0
attempts = 0
max_attempts = n * 3
while i < n:
    attempts += 1
    if attempts > max_attempts:
        sys.exit("ERROR: too many discarded/wrapped RAPL reads (%d attempts for n=%d); "
                  "check /sys/class/powercap/intel-rapl:0 is healthy" % (attempts, n))
    e0 = h.rapl_energy()
    time.sleep(60)
    e1 = h.rapl_energy()
    if e0 is None or e1 is None:
        print("    read %d/%d: RAPL unavailable, retrying" % (i + 1, n))
        continue
    energy, flag = h.rapl_correct_wrap(e1 - e0)
    if energy is None:
        print("    read %d/%d: DISCARDED (rapl_wrap=%s), retrying" % (i + 1, n, flag))
        continue
    w = energy / 60.0
    reads.append(w)
    tag = "" if flag == "none" else "  [%s]" % flag
    print("    read %d/%d: %.3f W%s" % (i + 1, n, w, tag))
    i += 1
med = statistics.median(reads)
print("    -> median %.3f W  (min %.3f / max %.3f, n=%d)"
      % (med, min(reads), max(reads), n))
PY
}

banner() { echo; echo "============================================================"; echo "  $1"; echo "============================================================"; }

# ===========================================================================
echo "SAQEF quiet-box box tasks (b)+(c)"
echo "  repo  : $REPO"
echo "  date  : $STAMP"
echo "  mode  : $([ "$DRY_RUN" = 1 ] && echo 'DRY-RUN (measure + report only)' || echo 'full (applies refs)')"
echo "  NOTE  : run from a bare shell, opencode/agents QUIT. The quiet gate will"
echo "          refuse to start on a busy box (that is the self-certification)."
if ! command -v python3 >/dev/null; then die "python3 not found"; fi
if ! docker info >/dev/null 2>&1; then
    echo "WARNING: docker not reachable as $USER -- will retry via sudo."
fi

# ---------------------------------------------------------------------------
banner "(b) re-anchor Fn/OpenFaaS regression references (old-runner A/B)"
echo ">>> checking k3s/Knative substrate is resident (references must be anchored"
echo "    WITH the substrate present, since that's now part of the box's real"
echo "    operating state -- see AGENTS.md)"
if ! $SUDO docker ps --format '{{.Names}}' | grep -q '^k8s_'; then
    die "k3s/Knative substrate not detected (no k8s_* containers in 'docker ps'). Fn/OpenFaaS re-anchor must run WITH the substrate resident. Bring k3s up first (e.g. 'sudo systemctl start k3s'; confirm with 'sudo k3s kubectl get node') and re-run."
fi
echo "    substrate present (k8s_* containers found)."
if [ "$SKIP_LEGS" = 1 ]; then
    echo ">>> --skip-legs: the A/B bench legs already ran and the references were"
    echo ">>> applied in a prior invocation. Re-validating the existing crosscheck"
    echo ">>> outdirs (provenance) then going straight to the regression verify + (c)."
    OF_V="$(validate_results "$REPO/$OF_OUT")"
    FN_V="$(validate_results "$REPO/$FN_OUT")"
    echo "  openfaas: $OF_V"
    echo "  fn      : $FN_V"
else
    echo ">>> OpenFaaS old runner (16 replicas, c=4, N=10000, repeat=5, idle_w=4.3)"
    echo "    out: $OF_OUT"
    # run_openfaas.sh 'all' deploys ONLY the control-plane stack; its 'scale' step
    # requires the 'hello' function service (deployed OUTSIDE the stack) to exist
    # first -- otherwise it aborts with "no 'hello' swarm service" before benching.
    # Deploy stack + function via the proven adapter path (idempotent; the old
    # runner's own setup_stack then redeploys the stack harmlessly). This mirrors
    # the documented manual prerequisite (OPENFAAS_SETUP.md §4) and what 'saqef
    # regression' does internally.
    $SUDO python3 "$REPO/saqef" deploy --platform openfaas
    $SUDO env SAQEF_REPLICAS=16 SAQEF_CONCURRENCY=4 SAQEF_TOTAL=10000 SAQEF_REPEAT=5 \
        SAQEF_IDLE_W=4.3 SAQEF_OUT="$OF_OUT" bash "$REPO/run_openfaas.sh" all
    echo ">>> teardown OpenFaaS (stack + the 'hello' service -- required before Fn)"
    $SUDO docker stack rm openfaas || true
    $SUDO docker service rm hello || true
    sleep 3

    echo ">>> Fn old runner (c=4, N=10000, repeat=5, idle_w=4.3)"
    echo "    out: $FN_OUT"
    $SUDO env SAQEF_CONCURRENCY=4 SAQEF_TOTAL=10000 SAQEF_REPEAT=5 \
        SAQEF_IDLE_W=4.3 SAQEF_OUT="$FN_OUT" bash "$REPO/run_saqef.sh" all

    echo ">>> reading old-runner medians + gate checks..."
    OF_V="$(validate_results "$REPO/$OF_OUT")"
    FN_V="$(validate_results "$REPO/$FN_OUT")"
    echo "  openfaas: $OF_V"
    echo "  fn      : $FN_V"
    OF_SHARE="$(echo "$OF_V" | cut -d' ' -f1)"
    FN_SHARE="$(echo "$FN_V" | cut -d' ' -f1)"
    OF_OK="$(echo "$OF_V" | cut -d' ' -f2)"
    FN_OK="$(echo "$FN_V" | cut -d' ' -f2)"

    if [ "$OF_OK" != "OK" ] || [ "$FN_OK" != "OK" ]; then
        echo "ERROR: one or both A/B legs failed the gate check -- NOT applying any"
        echo "reference. Inspect the runs above before re-running."
        exit 1
    fi

    if [ "$DRY_RUN" = 1 ]; then
        echo "DRY-RUN: would apply references fn=$FN_SHARE openfaas=$OF_SHARE to $CFG and run saqef regression."
    else
        echo ">>> applying fresh references fn=$FN_SHARE openfaas=$OF_SHARE to $CFG (backup: $CFG.bak-$STAMP)"
        if [ "$ASSUME_YES" = 0 ]; then
            read -r -p "  apply these references now? [y/N] " ans
            [ "$ans" = "y" ] || [ "$ans" = "Y" ] || { echo "skipped apply (references unchanged)."; exit 1; }
        fi
        apply_references "$OF_SHARE" "$FN_SHARE"
    fi
fi

if [ "$DRY_RUN" = 0 ]; then
    # run_saqef.sh leaves fnserver up after its leg, and `saqef regression` runs
    # OpenFaaS FIRST -- whose isolation guard refuses to deploy while fnserver is
    # present. Without this teardown the whole (b) work dies at the verify step
    # (the failure that previously forced re-running the full A/B from scratch).
    # A leftover Knative 'hello' ksvc (e.g. resident from a prior session, not
    # created by this script's legs) trips the same OpenFaaS isolation guard --
    # tear it down here too rather than only at step (c), which runs AFTER this
    # verify (this is the failure mode that motivated this fix).
    echo ">>> Fn teardown (run_saqef.sh leaves fnserver up; OpenFaaS isolation refuses it)"
    $SUDO python3 "$REPO/saqef" teardown --platform fn || true
    echo ">>> Knative teardown (leftover 'hello' ksvc also trips OpenFaaS isolation)"
    $SUDO python3 "$REPO/saqef" teardown --platform knative || true
    echo ">>> verifying the refactored CLI path agrees: saqef regression"
    $SUDO python3 "$REPO/saqef" regression
fi

# ---------------------------------------------------------------------------
banner "(c) Knative idle-w N>=3 recalibration (finding #13)"
echo ">>> condition A: bare substrate (k3s + knative-serving + kourier, NO hello)"
echo "    make sure no leftover knative hello is up:"
$SUDO python3 "$REPO/saqef" teardown --platform knative || true
sleep 2
rapl_w_series "$IDLE_REPS"
echo ">>> condition B: WITH hello at 16 replicas (exact bench-time stack state)"
$SUDO python3 "$REPO/saqef" deploy --platform knative
$SUDO python3 "$REPO/saqef" verify --platform knative
sleep 5
rapl_w_series "$IDLE_REPS"

echo ""
echo ">>> summary"
echo "    Use the CONDITION B median as --idle-w for any Knative bench"
echo "    (the stack state during measurement includes hello at 16 replicas)."
echo "    Premium = B - A; compare both against the 4.3 W Fn/OpenFaaS bare baseline."
echo ""
echo "DONE."
