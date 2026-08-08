#!/usr/bin/env bash
# Full contamination A/B: Fn then OpenFaaS, sequentially, single command.
#
# Prereqs (REQUIRED):
#   * opencode / all agents QUIT -- run this from a bare terminal. The clean
#     leg enforces the ambient-load quiet gate and will refuse to start above
#     15% host-busy, but the point of the run is a quiet box.
#   * platform images present (as for any other citable run on this box).
#
# Runs, per platform: deploy -> contamination A/B (N=5 clean + N=5 dirty,
# profile-matched to the 2026-08-07 incident) -> teardown. On any failure the
# trap tears down BOTH platforms so the box is left clean for the next session
# (the isolation guard would otherwise fail loud on a leftover deployment).
#
# Results: results/{fn,openfaas}_contamination_ab/{clean,dirty}/summary.json
#          results/{fn,openfaas}_contamination_ab/contamination_ab.json
#
# Usage:  bash tools/run_contamination_ab.sh
#         bash tools/run_contamination_ab.sh --fn-only | --openfaas-only
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AB="$REPO/tools/contamination_ab.py"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO=sudo
fi

MODE="${1:-all}"

cleanup() {
    echo ""
    echo ">>> Leaving the box clean: tearing down any partial deployment..."
    $SUDO python3 "$REPO/saqef" teardown --platform fn || true
    $SUDO python3 "$REPO/saqef" teardown --platform openfaas || true
}
trap cleanup EXIT

run_leg() {
    local platform="$1"
    local replicas="${2:-}"
    echo "============================================================"
    echo "  LEG: $platform"
    echo "============================================================"
    echo ">>> deploy $platform"
    $SUDO python3 "$REPO/saqef" deploy --platform "$platform"
    if [ -n "$replicas" ]; then
        echo ">>> static scale $platform -> $replicas"
        $SUDO python3 "$REPO/saqef" scale --platform "$platform" --replicas "$replicas"
    fi
    echo ">>> contamination A/B $platform (clean + dirty, N=5 each)"
    $SUDO python3 "$AB" --platform "$platform"
    echo ">>> teardown $platform"
    $SUDO python3 "$REPO/saqef" teardown --platform "$platform"
    echo ""
    echo ">>> DONE $platform -- verdict saved to"
    echo "    results/${platform}_contamination_ab/contamination_ab.json"
}

if [ "$MODE" = "all" ] || [ "$MODE" = "--fn-only" ]; then
    run_leg fn
fi
if [ "$MODE" = "all" ] || [ "$MODE" = "--openfaas-only" ]; then
    run_leg openfaas 16
fi

echo ""
echo "============================================================"
echo "  ALL LEGS DONE. Review:"
echo "    git status"
echo "    cat results/fn_contamination_ab/contamination_ab.json"
echo "    cat results/openfaas_contamination_ab/contamination_ab.json"
echo "============================================================"
