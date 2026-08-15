#!/usr/bin/env bash
# run_io_bound.sh - I/O-bound workload variant (decided-plan item 4, lean form).
#
# Swaps the identical 5 ms busy-spin handler for `time.sleep(0.005)` (same wall
# duration, no busy CPU) in ALL FOUR platforms, runs ONE quick-tier pass
# (c=4, REPEAT=3, TOTAL=3000, quiet-gated), then restores source + images so the
# box is left exactly as found. Answers: is the CP-share ordering / flatness a
# property of the platforms, or an artifact of the CPU-bound spin workload?
#
# EXPECTED RESULT (paper framing): fn CPU/inv collapses (~0.5-1 ms wake-up only)
# while CP ms/inv stays workload-invariant (OF ~0.4, Fn ~0.5-0.8, Kn ~0.7-1.1,
# OW ~30) -> cp_dynamic_share_pct INFLATES on all four platforms and OW
# approaches ~95-98%. Ordering OF < Fn ~ Kn << OW surviving inflation IS the
# mechanism statement (per-invocation orchestration tax, independent of the
# function's CPU profile). If the ordering shifts, that is a real finding.
#
# PROTOCOL:
#   * quick-tier throughout: TOTAL=3000, REPEAT=3 -> results/*_quick outdirs,
#     trend-only framing (same rules as the 2026-08-15 concurrency sweep).
#   * idle-w: lock4 N=5 medians (OF 4.235 / Fn 4.249 / Kn 5.739 / OW 4.882) via
#     --skip-idle-calib. Energy is not citable from REPEAT<5 anyway.
#   * images: Fn and OW rebuild automatically at deploy (fn deploy --local reads
#     hello/; OW PUTs OW_FUNCTION/hello.py source inline). OF hello:latest and
#     Kn localhost:5000/saqef/kn-hello:0.0.1 are rebuilt+tagged+pushed HERE.
#   * restore: source files are git-checked-out and OF/Kn images rebuilt from the
#     restored source afterwards, so a future citable run measures the spin
#     workload again (a leftover I/O image would silently taint it).
#   * Knative cache: k3s containerd cache is cleared for kn-hello so the fresh
#     image is actually pulled (its cache otherwise wins over the registry).
#   * MUST run from a bare shell with agents QUIT - the ambient quiet gate
#     self-certifies (<=15% host busy) and refuses to start above it.
#
# Usage: bash tools/run_io_bound.sh [--dry-run] [--stamp NAME] [--platforms of,fn,kn,ow]
#                                [--skip-restore] [--keep-sources]
#   --stamp NAME      lock-session stamp (default: iobound)
#   --platforms LIST  default of,fn,kn,ow
#   --skip-restore    do NOT rebuild the spin OF/Kn images afterwards (box left
#                     with the I/O images; only use if a citable run will rebuild
#                     anyway - NOT recommended)
#   --keep-sources    do NOT git-restore the 4 handler sources at the end
#   --dry-run         print the plan only
set -uo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="iobound"
PLATFORMS="of,fn,kn,ow"
DRY_RUN=0
DO_RESTORE=1
RESTORE_SOURCES=1

for arg in "$@"; do
    case "$arg" in
        --dry-run)      DRY_RUN=1 ;;
        --skip-restore) DO_RESTORE=0 ;;
        --keep-sources) RESTORE_SOURCES=0 ;;
        --stamp)        die "--stamp needs a value (use --stamp=NAME)" ;;
        --stamp=*)      STAMP="${arg#*=}" ;;
        --platforms)    die "--platforms needs a value (use --platforms=of,fn,kn,ow)" ;;
        --platforms=*)  PLATFORMS="${arg#*=}" ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

W_OF=4.235 W_FN=4.249 W_KN=5.739 W_OW=4.882
HANDLERS=("hello/func.py" "OF_FUNCTION/handler.py" "KNATIVE_FUNCTION/app.py" "OW_FUNCTION/hello.py")

banner() { echo; echo "============================================================"; echo "  $1"; echo "============================================================"; }

# ---------------------------------------------------------------------------
# swap_handlers: busy-spin -> time.sleep(0.005), fails loud if the spin block is
# not found exactly once (guards against patching already-patched files).
# ---------------------------------------------------------------------------
swap_handlers() {
    python3 - "$REPO" "${HANDLERS[@]}" <<'PY'
import os, re, sys
repo, files = sys.argv[1], sys.argv[2:]
pat = re.compile(r"\n( *)(t0 = time\.perf_counter\(\)\n\1while time\.perf_counter\(\) - t0 < 0\.005:\n\1    pass)")
for f in files:
    p = os.path.join(repo, f)
    s = open(p).read()
    s2, n = pat.subn(lambda m: "\n" + m.group(1) + "time.sleep(0.005)", s)
    if n != 1:
        sys.exit("ERROR: expected exactly 1 busy-spin block in %s, found %d - refusing to patch" % (f, n))
    open(p, "w").write(s2)
    print("  swapped %s -> time.sleep(0.005)" % f)
PY
}

# ---------------------------------------------------------------------------
# build_of_image: rebuild hello:latest from OF_FUNCTION/ (current handler state)
# ---------------------------------------------------------------------------
build_of_image() {
    echo "  docker build -t hello:latest $REPO/OF_FUNCTION"
    docker build -t hello:latest "$REPO/OF_FUNCTION" >/dev/null || die "OF hello:latest build failed"
    echo "  hello:latest rebuilt"
}

# ---------------------------------------------------------------------------
# push_kn_image: rebuild kn-hello, ensure the local registry, push, and clear
# k3s's containerd cache so the fresh image is actually pulled at deploy.
# ---------------------------------------------------------------------------
push_kn_image() {
    echo "  docker build -t localhost:5000/saqef/kn-hello:0.0.1 $REPO/KNATIVE_FUNCTION"
    docker build -t localhost:5000/saqef/kn-hello:0.0.1 "$REPO/KNATIVE_FUNCTION" >/dev/null \
        || die "kn-hello build failed"
    if ! docker ps --filter name='^registry$' --format '{{.Names}}' | grep -q registry; then
        docker run -d --name registry -p 5000:5000 registry:2 >/dev/null \
            || docker start registry >/dev/null 2>&1 \
            || die "could not start the local image registry"
        sleep 2
    fi
    docker push localhost:5000/saqef/kn-hello:0.0.1 >/dev/null || die "kn-hello push failed"
    echo "  kn-hello pushed to localhost:5000"
    sudo k3s crictl rmi localhost:5000/saqef/kn-hello:0.0.1 >/dev/null 2>&1 || true
    echo "  k3s containerd cache cleared for kn-hello (fresh pull at deploy)"
}

# ---------------------------------------------------------------------------
# restore_sources: git-checkout the 4 handlers back to the committed spin version
# ---------------------------------------------------------------------------
restore_sources() {
    git -C "$REPO" checkout -- "${HANDLERS[@]}" 2>/dev/null && echo "  handler sources restored (busy spin)"
}

# ===========================================================================
echo "SAQEF I/O-bound workload variant (decided-plan item 4, lean)"
echo "  repo     : $REPO"
echo "  stamp    : $STAMP"
echo "  platforms: $PLATFORMS"
echo "  protocol : TOTAL=3000 REPEAT=3 c=4 (_quick, trend-only, NOT lock4-comparable)"
echo "  idle-w   : lock4 medians OF=$W_OF FN=$W_FN KN=$W_KN OW=$W_OW (--skip-idle-calib)"
echo "  NOTE     : bare shell, agents QUIT. Each leg self-certifies quiet (15% gate)."
[ "$DRY_RUN" = 1 ] && echo "  MODE     : DRY-RUN -- print plan only"
echo "  KN CACHE : k3s containerd cache for kn-hello will be cleared (fresh pull)."

if [ "$DRY_RUN" = 1 ]; then
    banner "DRY-RUN plan"
    echo "  1. swap_handlers   (4 files: busy spin -> time.sleep(0.005))"
    echo "  2. build_of_image  (hello:latest from OF_FUNCTION)"
    echo "  3. push_kn_image   (build + tag + push localhost:5000/saqef/kn-hello:0.0.1, clear k3s cache)"
    echo "  4. lock session:  bash tools/run_lock_session.sh --stamp $STAMP --repeat 3 --total 3000 \\"
    echo "        --concurrency 4 --platforms $PLATFORMS --skip-idle-calib \\"
    echo "        --idle-w-of $W_OF --idle-w-fn $W_FN --idle-w-kn $W_KN --idle-w-ow $W_OW"
    echo "  5. restore: [source: git checkout] [images: rebuild OF hello:latest + Kn kn-hello from restored source]"
    echo "DRY-RUN complete - nothing was modified. Remove --dry-run and run in a bare shell with agents quit."
    exit 0
fi

banner "step 1/5: swap handlers to time.sleep(0.005)"
swap_handlers
trap 'restore_sources; echo "NOTE: sources restored by trap (interrupted run)."' EXIT

banner "step 2/5: rebuild OpenFaaS hello:latest"
build_of_image

banner "step 3/5: rebuild + push Knative kn-hello, clear k3s cache"
push_kn_image

banner "step 4/5: lock session (quick-tier, c=4)"
bash "$REPO/tools/run_lock_session.sh" --stamp "$STAMP" --repeat 3 --total 3000 \
    --concurrency 4 --platforms "$PLATFORMS" --skip-idle-calib \
    --idle-w-of "$W_OF" --idle-w-fn "$W_FN" --idle-w-kn "$W_KN" --idle-w-ow "$W_OW" \
    || die "lock session (stamp $STAMP) failed"

banner "step 5/5: restore box to the spin-workload state"
if [ "$RESTORE_SOURCES" = 1 ]; then
    restore_sources
else
    echo "  --keep-sources: handler sources left as time.sleep(0.005)"
fi
if [ "$DO_RESTORE" = 1 ]; then
    build_of_image
    push_kn_image
    echo "  OF/Kn images rebuilt from the restored spin source"
else
    echo "  --skip-restore: images left with the I/O handler - rebuild before any citable run"
fi
trap - EXIT

echo
echo "DONE. Results in results/*_cpubound_lock_${STAMP}_quick/ + results/lock_session_${STAMP}/lock_summary.json"
echo "Next (agent-safe): update figures/make_figures.py + paper with an I/O-bound quick-tier row,"
echo "label 'quick-tier trend-only, same-day', commit."
