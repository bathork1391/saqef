#!/usr/bin/env bash
# run_sweep_and_freeze.sh - one-file bare-shell driver for the 2026-08-15 decided plan
# items 2+3: Fn freeze ablation + OF/Fn/Kn concurrency sweep (+ OW spot-check).
#
# PROTOCOL (agreed 2026-08-15, see AGENTS.md "Next experiments -- DECIDED PLAN"):
#   * quick-tier throughout: TOTAL=3000, REPEAT=3 -> results/*_quick outdirs,
#     never published until gates pass and promoted to REPEAT=5.
#   * trend-only framing: the concurrency sweep is self-consistent within itself
#     (all c-values same day, same quick protocol). NOT directly comparable to
#     lock4's N=5 shares; the c=4 point already exists in lock4 as the N=5 anchor.
#   * idle-w: lock4 N=5 medians (OF 4.235 / Fn 4.249 / Kn 5.739 / OW 4.882) via
#     --skip-idle-calib. Recalibration would add ~40 min and is not needed for
#     quick-tier trend runs (energy is not citable from REPEAT<5 anyway).
#   * distinct --stamp per concurrency value (run_lock_session.sh refuses to
#     clobber an existing same-stamp outdir).
#   * MUST run from a bare shell with agents quit -- the ambient quiet gate
#     self-certifies (<=15% host busy) and refuses to start above it.
#
# Usage:  bash tools/run_sweep_and_freeze.sh [--skip-freeze] [--skip-sweep] [--skip-ow] [--dry-run]
#   --skip-freeze  -> skip the Fn freeze-ablation legs
#   --skip-sweep   -> skip the OF/Fn/Kn concurrency sweep
#   --skip-ow      -> skip the OpenWhisk spot-check (2 configs, ~25 min)
#   --dry-run      -> print the plan only
set -uo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0
DO_FREEZE=1 DO_SWEEP=1 DO_OW=1
for arg in "$@"; do
    case "$arg" in
        --skip-freeze) DO_FREEZE=0 ;;
        --skip-sweep)  DO_SWEEP=0 ;;
        --skip-ow)     DO_OW=0 ;;
        --dry-run)     DRY_RUN=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

W_OF=4.235 W_FN=4.249 W_KN=5.739 W_OW=4.882

banner() { echo; echo "============================================================"; echo "  $1"; echo "============================================================"; }

echo "SAQEF quick-tier sweep + freeze-ablation driver"
echo "  repo   : $REPO"
echo "  protocol: TOTAL=3000 REPEAT=3 (_quick, trend-only, NOT lock4-comparable)"
echo "  idle-w : lock4 medians OF=$W_OF FN=$W_FN KN=$W_KN OW=$W_OW"
echo "  NOTE   : bare shell, agents QUIT. Each leg self-certifies quiet (15% gate)."
[ "$DRY_RUN" = 1 ] && echo "  MODE   : DRY-RUN -- print plan only"

# ---------------------------------------------------------------------------
# Fn freeze ablation (run_saqef.sh: FN_FREEZE_IDLE_MSECS=-1 hook, container creation;
# NOTE: fnproject semantics -- 0 = freeze WITHOUT delay, NEGATIVE = disable freeze)
# ---------------------------------------------------------------------------
run_freeze() {
    banner "Fn freeze ablation (quick-tier, REPEAT=3)"
    local env_base=(SAQEF_CONCURRENCY=4 SAQEF_TOTAL=3000 SAQEF_REPEAT=3 SAQEF_IDLE_W=$W_FN)
    echo "  leg 1/2: baseline (default freeze)"
    if [ "$DRY_RUN" = 0 ]; then
        env "${env_base[@]}" SAQEF_OUT=results/fn_freeze_baseline_quick \
            bash "$REPO/run_saqef.sh" all || die "freeze baseline leg failed"
    else
        echo "  DRY-RUN: env ${env_base[*]} SAQEF_OUT=results/fn_freeze_baseline_quick bash $REPO/run_saqef.sh all"
    fi
    echo "  leg 2/2: freeze OFF (FN_FREEZE_IDLE_MSECS=-1, negative disables per fnproject docs)"
    if [ "$DRY_RUN" = 0 ]; then
        env "${env_base[@]}" SAQEF_OUT=results/fn_freeze_off_quick \
            FN_FREEZE_IDLE_MSECS=-1 bash "$REPO/run_saqef.sh" all || die "freeze OFF leg failed"
    else
        echo "  DRY-RUN: env ${env_base[*]} SAQEF_OUT=results/fn_freeze_off_quick FN_FREEZE_IDLE_MSECS=-1 bash $REPO/run_saqef.sh all"
    fi
}

# ---------------------------------------------------------------------------
# Concurrency sweep via run_lock_session.sh (distinct stamps, skip-idle-calib)
# ---------------------------------------------------------------------------
run_sweep() {
    banner "OF/Fn/Kn concurrency sweep (c=1/2/8/16, quick-tier)"
    for c in 1 2 8 16; do
        echo "  concurrency=$c (stamp conc$c)"
        if [ "$DRY_RUN" = 1 ]; then
            echo "  DRY-RUN: bash tools/run_lock_session.sh --stamp conc$c --repeat 3 --total 3000 --concurrency $c --platforms of,fn,kn --skip-idle-calib --idle-w-of $W_OF --idle-w-fn $W_FN --idle-w-kn $W_KN"
            continue
        fi
        bash "$REPO/tools/run_lock_session.sh" --stamp "conc$c" --repeat 3 --total 3000 \
            --concurrency "$c" --platforms of,fn,kn --skip-idle-calib \
            --idle-w-of "$W_OF" --idle-w-fn "$W_FN" --idle-w-kn "$W_KN" || die "conc$c failed"
    done
}

# ---------------------------------------------------------------------------
# OpenWhisk spot-check (2 configs; ~25 min -- the time hog)
# ---------------------------------------------------------------------------
run_ow() {
    banner "OpenWhisk spot-check (c=4 vs c=8, quick-tier)"
    for c in 4 8; do
        echo "  concurrency=$c (stamp ow$c)"
        if [ "$DRY_RUN" = 1 ]; then
            echo "  DRY-RUN: bash tools/run_lock_session.sh --stamp ow$c --repeat 3 --total 3000 --concurrency $c --platforms ow --skip-idle-calib --idle-w-ow $W_OW"
            continue
        fi
        bash "$REPO/tools/run_lock_session.sh" --stamp "ow$c" --repeat 3 --total 3000 \
            --concurrency "$c" --platforms ow --skip-idle-calib --idle-w-ow "$W_OW" \
            || die "ow$c failed"
    done
}

[ "$DO_FREEZE" = 1 ] && run_freeze
[ "$DO_SWEEP" = 1 ] && run_sweep
[ "$DO_OW" = 1 ] && run_ow

echo
echo "DONE. Next (agent-safe): update figures/make_figures.py REGIMES + paper"
echo "numbers (label the sweep 'quick-tier trend-only, same-day'), commit."
