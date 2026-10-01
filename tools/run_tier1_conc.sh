#!/usr/bin/env bash
# run_tier1_conc.sh - TIER 1, Experiment B: concurrency sweep with the
# background-CPU model (10-day-plan.md TIER 0.2 CORRECTED 2026-10-01).
#
# BACKGROUND-CPU MODEL (expert-reviewed and independently re-derived, 2026-10-01):
# A run's measured control-plane CPU is NOT purely per-invocation:
#     cp_cpu_s  = a_cp * requests + b_cp * wall_s          (b_cp ~ 1-5% of a core)
#     fn_cpu_s  = a_fn * requests + b_fn * wall_s
# The 2026-08-15 quick sweep's apparent 13-30% fall in CP ms/inv from c=1 to c=8
# was a WINDOW-LENGTH ARTIFACT: at fixed TOTAL, wall shrinks ~5x as c rises, so
# the b*wall background term is spread over the same request count -- the
# per-inv number then shrinks with wall even though a (the true per-invocation
# cost) is constant. Re-derived slopes confirm a per-invocation intercept
# (0.383/0.528/0.745 ms for OF/Fn/Kn) plus a small background
# (b_cp=1.2%/4.5%/5.0% of a core; b_fn=6.2%/7.4%/13.2%). Subtracting the
# background flattens the share (OF 1.20->1.00 pp, Fn 2.72->0.07 pp,
# Kn 2.15->1.12 pp).
#
# THOSE NUMBERS ARE THE QUICK-TIER REGRESSION over c=1/2/8 and are generated into
# saqef-paper VERIFIED_RESULTS.md section 14 by tools/emit_verified_results.py.
# That fit is 3 points / 2 parameters (~1 residual dof), so its flatness is a
# check against the fit, NOT an independent measurement.
#
# SO THIS RUN now does two things at a citable (n=5) tier:
#   (a) measures the per-invocation a_cp/a_fn and the background b_cp/b_fn by
#       regression over c=1,2,4,8 with a per-leg NATIVE --idle-probe as the
#       direct background-rate cross-check; and
#   (b) confirms the flat share (the paper's claim) at n=5.
#
# c=16 is DELIBERATELY EXCLUDED (reason corrected 2026-10-01): 16 CPU-bound
# spinners oversubscribe the 8 logical cores, so the workload itself changes
# regime (preemption), the fn-side per-inv CPU reading CAN legitimately drop
# below the 5 ms spin floor (the first run of a spin is wall-time based, so
# preempted spins read less CPU), and the legacy "2992 != 3000" is plain
# floor-division arithmetic in `hey` (3000/16 = 187 remainder 8; every worker
# gets floor(n/c)). None of these are "sampler truncation" -- that reading was
# WRONG and has been retracted. c=16 would still be its own regime (host_sat
# ~88%), so it stays out of this single-tier sweep.
#
# PROTOCOL (a NEW tier, superseding the 2026-08-15 quick sweep):
#   * TOTAL=3000, REPEAT=5 -> NO _quick suffix, full protocol discipline.
#   * c = 1, 2, 4, 8 (of,fn,kn) + OW at c = 1, 4, 8 (add c=4 so the OW curve is
#     internally same-day; the old c=4 anchor was lock4's, a different day).
#     Every c is complete-capacity (3000/3000 where c divides 3000).
#   * Per-leg NATIVE --idle-probe (--cpu-probe 60): same stack state as the
#     bench, zero traffic, one measurement -> results/idle_probe_<stamp>/<plat>/
#     gives the direct background rate b for that exact leg (the visible-level
#     cross-check of the regression intercepts).
#   * idle-w: lock4 N=5 medians via --skip-idle-calib (OF 4.235 / Fn 4.249 /
#     Kn 5.739 / OW 4.882). Energy not a goal; c=8 host_sat ~88-90% makes QoS
#     uncitable there (share and CP/fn per-inv are contention-robust outputs).
#   * OW c=1: --ow-duration 420 (lock4 p50 ~110 ms implies ~330 s wall; the
#     loadgen kill-switch is duration+120 s, so 300 would leave <90 s margin --
#     if ANY OW leg prints LOADGEN FALLBACK, that is duration, raise it).
#   * outdirs: results/<platform>_cpubound_lock_tier1c{N}/ and tier1ow{N}/ --
#     run_lock_session refuses to clobber, pick fresh stamps if re-running.
#   * MUST run from a bare shell with agents QUIT (ambient quiet gate, 15%).
#
# Runtime estimate: 3 lightweight x 4 c-points (~6-9 min each incl. deploy /
# verify / 1-min idle probe / teardown) + 3 OW legs (c=1 ~6 min x 5; c=4 ~1 min
# x 5; c=8 ~1 min x 5) + the c=1 OW duration pilot ~= 2.5-3 h.
#
# Usage:  bash tools/run_tier1_conc.sh [--skip-ow] [--dry-run]
#   --skip-ow    -> skip the three OpenWhisk legs (lightweight curve only)
#   --dry-run    -> print the plan only
set -uo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0 DO_OW=1
for arg in "$@"; do
    case "$arg" in
        --skip-ow) DO_OW=0 ;;
        --dry-run) DRY_RUN=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

W_OF=4.235 W_FN=4.249 W_KN=5.739 W_OW=4.882
TOTAL=3000
REPEAT=5

banner() { echo; echo "============================================================"; echo "  $1"; echo "============================================================"; }

echo "SAQEF Tier-1 Experiment B -- concurrency at a single clean tier"
echo "  repo   : $REPO"
echo "  protocol: TOTAL=$TOTAL REPEAT=$REPEAT (full protocol, NO _quick)  light c=1/2/4/8; OW c=1/4/8"
echo "  c=16     : EXCLUDED (8-core oversubscription changes the regime; legacy reads retracted)"
echo "  model   : cp_cpu_s = a*requests + b*wall  (background-CPU; per-leg --idle-probe = direct b)"
echo "  idle-w : lock4 medians OF=$W_OF FN=$W_FN KN=$W_KN OW=$W_OW (--skip-idle-calib)"
[ "$DO_OW" = 1 ] && echo "  OW     : c=1 duration 420 s (pilot + full leg); c=4,8 added for a same-day 3-point curve"
[ "$DO_OW" = 0 ] && echo "  OW     : SKIPPED (--skip-ow) -- NOT a four-platform result set"
# FIXED 2026-10-01 (expert review): these two conditions were swapped -- the
# bare-shell warning was gated on --skip-ow being OFF (so --skip-ow runs lost the
# one warning that matters most, and printed an OpenWhisk line for a platform it
# was not running).
echo "  NOTE   : bare shell, agents QUIT. Each leg self-certifies quiet (15% gate)."
[ "$DRY_RUN" = 1 ] && echo "  MODE   : DRY-RUN -- print plan only"

# ---------------------------------------------------------------------------
# lightweight curve (of,fn,kn at every concurrency)
# ---------------------------------------------------------------------------
run_light() {
    for c in 1 2 4 8; do
        stamp="tier1c$c"
        echo
        echo "  >>> concurrency=$c (stamp $stamp, of+fn+kn, N=$REPEAT, idle-probe 60s)"
        if [ "$DRY_RUN" = 1 ]; then
            echo "      DRY-RUN: bash tools/run_lock_session.sh --stamp $stamp --repeat $REPEAT --total $TOTAL \\"
            echo "            --concurrency $c --platforms of,fn,kn --skip-idle-calib --cpu-probe 60 \\"
            echo "            --idle-w-of $W_OF --idle-w-fn $W_FN --idle-w-kn $W_KN"
            continue
        fi
        bash "$REPO/tools/run_lock_session.sh" --stamp "$stamp" --repeat "$REPEAT" --total "$TOTAL" \
            --concurrency "$c" --platforms of,fn,kn --skip-idle-calib --cpu-probe 60 \
            --idle-w-of "$W_OF" --idle-w-fn "$W_FN" --idle-w-kn "$W_KN" \
            || die "tier1c$c lightweight legs failed"
    done
}

# ---------------------------------------------------------------------------
# OpenWhisk spot (c=1, 4, 8). c=1 first as a deploy-only 1-run PILOT so we
# learn the true wall time before committing a full n=5 leg at that concurrency.
# ---------------------------------------------------------------------------
run_ow() {
    # pilot: deploy-only, 1 run, same TOTAL -- tells us wall_s and whether the
    # duration/kill-switch is comfortable before we spend 5 runs.
    stamp="tier1ow1"
    echo
    echo "  >>> OW c=1 duration PILOT (stamp ${stamp}_pilot, deploy-only, repeat=1)"
    if [ "$DRY_RUN" = 1 ]; then
        echo "      DRY-RUN: bash tools/run_lock_session.sh --stamp ${stamp}_pilot --repeat 1 --total $TOTAL \\"
        echo "            --concurrency 1 --platforms ow --skip-idle-calib --idle-w-ow $W_OW \\"
        echo "            --deploy-only --requests-per-run $TOTAL --ow-duration 420"
    else
    bash "$REPO/tools/run_lock_session.sh" --stamp "${stamp}_pilot" --repeat 1 --total "$TOTAL" \
        --concurrency 1 --platforms ow --skip-idle-calib --idle-w-ow "$W_OW" \
        --deploy-only --requests-per-run "$TOTAL" --ow-duration 420 \
        || die "OW c=1 pilot failed"
        # FIXED 2026-10-01 (expert review): the pilot's whole purpose is to learn
        # the TRUE wall time before committing 5 runs, so "an outdir appeared" is
        # not a sufficient check -- a pilot truncated by the duration cap (or one
        # that fell back to the python loadgen) still produces a folder, and would
        # silently authorise a full leg that hits the same kill-switch. Read the
        # pilot's summary and refuse on: short count, killed by the cap, or
        # loadgen fallback.
        pilot_summary() {
            for d in "$REPO/results/openwhisk_cpubound_lock_${stamp}_pilot" \
                     "$REPO/results/openwhisk_cpubound_lock_${stamp}_pilot_quick"; do
                [ -f "$d/summary.json" ] && { echo "$d/summary.json"; return 0; }
            done
            return 1
        }
        if ! pj="$(pilot_summary)"; then
            die "OW c=1 pilot produced no summary.json -- refusing to run the full leg blind"
        fi
        if ! python3 - "$pj" 420 "$TOTAL" <<'PY'
import json, sys
p, cap, total = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
s = json.load(open(p))
req, want = s.get("requests"), s.get("total_requested")
wall = s.get("wall_s")
env = s.get("env") or {}
bad = []
if req is not None and want is not None and req != want:
    bad.append("INCOMPLETE RUN %s/%s" % (req, want))
if req is None or int(req) < total:
    bad.append("requests=%s < requested %d" % (req, total))
if wall is None:
    bad.append("no wall_s")
elif wall >= cap:
    bad.append("wall_s=%.1f >= duration cap %.0fs -> hey was killed mid-run" % (wall, cap))
if env.get("loadgen_fallback"):
    bad.append("LOADGEN FALLBACK (loadgen=%s != requested=%s)"
               % (env.get("loadgen"), env.get("loadgen_requested")))
print("    pilot: wall_s=%.1f cap=%.0f requests=%s/%s loadgen=%s" % (
    wall if wall is not None else float("nan"), cap, req, want, env.get("loadgen")))
if bad:
    print("    PILOT REJECTED: " + "; ".join(bad))
    sys.exit(1)
frac = 100.0 * wall / cap
# A pilot that only just fits is NOT a pass -- the full leg runs the same TOTAL
# and any per-run variance tips it over the cap into the silent loadgen
# fallback this pilot exists to prevent. 80% is the working margin.
if frac > 80.0:
    print("    pilot MARGINAL: %.0f%% of the %.0fs cap. It passes, but raise "
          "--ow-duration (e.g. %d) before the n=5 leg or a slow run will trip the "
          "kill-switch." % (frac, cap, int(cap * 1.5 / 60 + 1) * 60))
else:
    print("    pilot OK -- %.0f%% of the cap; a full leg fits with margin" % frac)
PY
        then
            die "OW c=1 pilot did not complete cleanly (see PILOT REJECTED above) -- raise --ow-duration or fix the loadgen before the n=5 leg"
        fi
    fi
    for c in 1 4 8; do
        stamp="tier1ow$c"
        dur=300; [ "$c" = 1 ] && dur=420
        echo
        echo "  >>> OpenWhisk concurrency=$c (stamp $stamp, N=$REPEAT, ow-duration=${dur}s, idle-probe 60s)"
        if [ "$DRY_RUN" = 1 ]; then
            echo "      DRY-RUN: bash tools/run_lock_session.sh --stamp $stamp --repeat $REPEAT --total $TOTAL \\"
            echo "            --concurrency $c --platforms ow --skip-idle-calib --cpu-probe 60 \\"
            echo "            --idle-w-ow $W_OW --ow-duration $dur"
            continue
        fi
        bash "$REPO/tools/run_lock_session.sh" --stamp "$stamp" --repeat "$REPEAT" --total "$TOTAL" \
            --concurrency "$c" --platforms ow --skip-idle-calib --cpu-probe 60 \
            --idle-w-ow "$W_OW" --ow-duration "$dur" \
            || die "tier1ow$c failed"
    done
}

run_light
[ "$DO_OW" = 1 ] && run_ow

if [ "$DRY_RUN" = 1 ]; then
    echo
    echo "DRY-RUN complete -- nothing was measured. Remove --dry-run and run in a"
    echo "bare shell with agents quit (~2.5-3 h)."
    exit 0
fi

# ---------------------------------------------------------------------------
# cross-stamp aggregation (CORRECTED 2026-10-01): per-run division by the run's
# OWN request count; per-run wall; host_sat; fn container count; RAW cp/fn are
# stored, and the background-CPU model (a*requests + b*wall) is fit on the RAW
# per-inv data. A per-leg --idle-probe rate is then subtracted (Table 1) so the
# corrected per-invocation costs + share show the 'true' platform property.
# Any post hoc paper number must be re-derived from here, not from this table.
# ---------------------------------------------------------------------------
banner "cross-stamp aggregation (from committed result files)"
python3 - "$REPO" <<'PY'
import json, os, statistics, sys
repo = sys.argv[1]
# stamp pattern per platform: openfaas_cpubound_lock_tier1cN (light) /
# tier1owN (ow). All REPEAT=5, so no _quick suffix.
short2plat = {"openfaas": ("OpenFaaS", (1, 2, 4, 8)),
              "fn": ("Fn", (1, 2, 4, 8)),
              "knative": ("Knative", (1, 2, 4, 8)),
              "openwhisk": ("OpenWhisk", (1, 4, 8))}
def find_run_dir(plat, stamp):
    d = os.path.join(repo, "results", "%s_cpubound_lock_%s" % (plat, stamp))
    if os.path.isdir(d):
        return d
    return d + "_quick" if os.path.isdir(d + "_quick") else None

def probe_rate(repo, stamp, plat):
    """per-second background CPU rate from the per-leg --idle-probe (if present)."""
    d = os.path.join(repo, "results", "idle_probe_%s" % stamp, plat)
    if not os.path.isdir(d) and os.path.isdir(d + "_quick"):
        d = d + "_quick"
    if not os.path.isdir(d):
        return None                      # leg genuinely ran without --cpu-probe
    # FIXED 2026-10-01 (expert review): this used to return None whenever
    # runs.json was absent -- which is exactly what a --repeat 1 probe produced
    # before the harness was fixed. That made the whole independent cross-check a
    # SILENT no-op: every corrected column printed "--" and the flatness check
    # said "no probe data" while the script still exited 0. Prefer runs.json,
    # fall back to the single-run summary.json, and if neither is readable, SAY
    # SO loudly instead of degrading quietly.
    src = None
    for name, wrap in (("runs.json", list), ("summary.json", lambda v: [v])):
        f = os.path.join(d, name)
        if os.path.isfile(f):
            try:
                src = (wrap(json.load(open(f))), name)
                break
            except Exception as e:
                print("  !! %s c-probe: %s present but unreadable (%s)"
                      % (plat, name, type(e).__name__))
    if src is None:
        print("  !! %s: probe dir %s has NO readable runs.json/summary.json --"
              " background correction IMPOSSIBLE for this leg" % (plat, os.path.basename(d)))
        return None
    r = src[0][0]
    wall = r.get("wall_s") or 1.0
    cp = r.get("cpu_sec", {}).get("control_plane", 0.0)
    fn = r.get("cpu_sec", {}).get("function", 0.0)
    return {"cp_per_s": cp / wall, "fn_per_s": fn / wall, "wall": wall,
            "src": src[1], "cp_cpu_s": cp, "fn_cpu_s": fn}

def fn_container_count(s):
    labels = s.get("container_labels") or {}
    inven = s.get("container_inventory") or []
    if not labels:
        return None
    plat = s.get("platform")
    if plat == "openwhisk":
        # FIXED 2026-10-01 (expert review): a bare "wsk0_" prefix counted the two
        # invoker warmup containers (wsk0_1/2_prewarm_nodejs20) as functions and
        # returned 4 instead of 2 on every OW leg. Verified across all six
        # committed OW datasets (lock2/3/4, ow4/ow8, iobound): the action
        # containers are the *_guest_hello pair, and the count is 2 at EVERY
        # concurrency (the invoker reuses them), so this column is NOT a
        # replica-count signal for OW.
        return sum(1 for n in inven if "_guest_" in n)
    if plat == "knative":
        # k8s fn containers are named user-container (queue-proxy is the sidecar);
        # image is a digest, so match by name.
        return sum(1 for n in inven if "user-container" in n)
    return sum(1 for n in inven
               if (labels.get(n) or {}).get("image", "").rsplit("/", 1)[-1].startswith("hello"))

raw = {}   # plat -> list of (c, req, wall_s, cp_s, fn_s, share)  [RAW, NO correction]
legs = {}  # (plat, c) -> {"bg": probe_rate or None, "d": outdir}
for plat, (label, cs) in short2plat.items():
    for c in cs:
        stamp = ("tier1ow%d" if plat == "openwhisk" else "tier1c%d") % c
        d = find_run_dir(plat, stamp)
        if not d:
            print("  (missing %s c=%d -> skipped)" % (label, c)); continue
        legs[(plat, c)] = {"bg": probe_rate(repo, stamp, plat), "d": d}
        runs = json.load(open(os.path.join(d, "runs.json")))
        ndrop = 0
        for runnr, r in enumerate(runs, 1):
            req = r.get("requests") or r.get("total_requested") or 3000
            # FIXED 2026-10-01 (expert review): an incomplete run used to be warned
            # about and then still APPENDED to raw[], so a truncated leg silently
            # fed the fit and Table 1. A short count means the run was cut off
            # before its own steady state, so its per-invocation numbers are not
            # comparable -- exclude it and say how many were dropped.
            if r.get("requests") != r.get("total_requested"):
                print("  WARN %s c=%d run_%d requests=%s != total_requested=%s -- INCOMPLETE, EXCLUDED from fit"
                      % (label, c, runnr, r.get("requests"), r.get("total_requested")))
                ndrop += 1
                continue
            cp_s = r["cpu_sec"]["control_plane"]; fn_s = r["cpu_sec"]["function"]
            wall = r.get("wall_s") or 1.0
            raw.setdefault(plat, []).append((c, req, wall, cp_s, fn_s, r["cp_dynamic_share_pct"]))
        if ndrop:
            print("       -> %s c=%d: %d/%d runs excluded from the fit" % (label, c, ndrop, len(runs)))

def med(arr):
    return statistics.median(arr) if arr else float("nan")

print()
print("Table 1 -- per-invocation costs by concurrency (RAW vs --idle-probe corrected)")
print("corrected* = raw cp/fn minus that leg's probe background rate x wall. medians of n=5.")
print("%-9s %3s | %10s %10s %8s %10s %8s %10s %8s %8s" % (
    "platform", "c", "CP ms/inv", "fn ms/inv", "share%", "CP* ms/inv", "fn* ms/inv",
    "sh*%", "wall_s", "fn cnt"))
for (plat, c), lg in sorted(legs.items()):
    label = short2plat[plat][0]
    s = json.load(open(os.path.join(lg["d"], "summary.json")))
    recs = [r for r in raw[plat] if r[0] == c]
    n = len(recs)
    cpm = [r[3] / r[1] * 1000.0 for r in recs]
    fnm = [r[4] / r[1] * 1000.0 for r in recs]
    bg = lg["bg"]
    if bg:
        cps = [r[3] / r[1] * 1000.0 - bg["cp_per_s"] * r[2] / r[1] * 1000.0 for r in recs]
        fns = [r[4] / r[1] * 1000.0 - bg["fn_per_s"] * r[2] / r[1] * 1000.0 for r in recs]
        shs = [100.0 * (r[3] - bg["cp_per_s"] * r[2]) / (r[3] - bg["cp_per_s"] * r[2] +
                                                         r[4] - bg["fn_per_s"] * r[2]) for r in recs]
        shs = [x for x in shs if x == x]
    else:
        cps = [None] * n; fns = [None] * n
        shs = [float("nan")] * n
    wallm = med([r[2] for r in recs]) if recs else float("nan")
    fc = fn_container_count(s)
    print("%-9s %3d | %10.3f %10.3f %8.2f %10s %8s %8s %8.1f %8s" % (
        label, c, med(cpm), med(fnm), med([r[5] for r in recs]),
        ("%.3f" % med(cps)) if any(v is not None for v in cps) else "--",
        ("%.3f" % med(fns)) if any(v is not None for v in fns) else "--",
        (("%.2f" % med(shs)) if any(v == v for v in shs) else "--"),
        wallm, fc if fc is not None else "??"))

print()
print("Table 2 -- background-CPU model fit on RAW data across ALL runs of each platform")
print("per-inv cp = a + b * (wall per inv); likewise fn. a = the TRUE per-invocation")
print("cost (ms), b = the per-second background rate (cpu-s/s). The 2026-08-15")
print("'amortisation' was b*wall spread over fewer requests at low c -- NOT a real")
print("drop in a. (Regressing cp on (requests, wall) directly is collinear here:")
print("requests is constant across legs, so per-inv vs per-inv-wall is the")
print("identifiable form.)")
for plat, (label, cs) in short2plat.items():
    recs = raw.get(plat, [])
    if len(recs) < 4: continue
    for which, idx in (("CP", 3), ("fn", 4)):
        xs = [r[2] / r[1] * 1000.0 for r in recs]   # per-inv wall ms
        ys = [r[idx] / r[1] * 1000.0 for r in recs]  # per-inv cpu ms
        n = len(recs)
        sx = sum(xs); sy = sum(ys)
        sxx = sum(x*x for x in xs); sxy = sum(x*y for x, y in zip(xs, ys))
        den = sxx - sx * sx / n
        if den == 0: continue
        b = (sxy - sx * sy / n) / den   # centered LS: cpu-s per wall-s (background rate)
        a = (sy - b * sx) / n           # ms per invocation (TRUE per-inv cost)
        resid = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
        tot = sum((y - sy / n) ** 2 for y in ys)
        r2 = 1 - resid / tot if tot else float("nan")
        print("  %-9s %s: a=%.3f ms/inv  b=%.3f cpu-s/s (%.1f%% of one core)  R^2=%.2f  (n=%d)"
              % (label, which, a, b, b * 100, r2, n))

print()
print("Share flatness check on RAW and probe-CORRECTED share (paper: share flat):")
for plat, (label, cs) in short2plat.items():
    shares_raw, shares_corr = {}, {}
    for c in cs:
        recs = [r for r in raw.get(plat, []) if r[0] == c]
        if not recs: continue
        shares_raw[c] = med([r[5] for r in recs])
        bg = legs.get((plat, c), {}).get("bg")
        if bg:
            vals = [100.0 * (r[3] - bg["cp_per_s"] * r[2]) /
                    (r[3] - bg["cp_per_s"] * r[2] + r[4] - bg["fn_per_s"] * r[2])
                    for r in recs]
            shares_corr[c] = med([v for v in vals if v == v])
        else:
            shares_corr[c] = float("nan")
    if len(shares_raw) >= 2:
        sr = {k: round(v, 2) for k, v in shares_raw.items()}
        sc = {k: (round(v, 2) if v == v else "--") for k, v in shares_corr.items()}
        spread = max(shares_raw.values()) - min(shares_raw.values())
        scvals = [v for v in shares_corr.values() if v == v]
        if len(scvals) >= 2:
            spread_c = max(scvals) - min(scvals)
            verdict = "(flat)" if spread_c <= 2.0 else "(NOT flat -- investigate)"
            tail = "  corrected spread %.2f pp %s" % (spread_c, verdict)
        else:
            tail = "  (no probe data for corrected share)"
        print("  %-9s raw shares %s  corr shares %s  raw spread %.2f pp%s"
              % (label, sr, sc, spread, tail))
PY

echo
echo "DONE. Agent-safe next steps: re-run this aggregation, update 10-day-plan.md"
echo "TIER 0.2 (background-CPU model confirmed), then decide Experiment C (spin magnitude)."
echo "Do NOT alter paper text yet."