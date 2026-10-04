#!/usr/bin/env bash
# W4 cold start (runbook §32): the function pool of one platform, outside any measured window.
#
#   bash tools/pool.sh PLATFORM count            function containers running now (one integer)
#   bash tools/pool.sh PLATFORM reset            empty the pool, then exit 0 when count = 0
#   bash tools/pool.sh PLATFORM logs SINCE OUT   control-plane logs since epoch SINCE -> OUT (gzip)
#
# PLATFORM is fn | knative | openwhisk. The harness calls this between runs when
# SAQEF_POOL_CMD="bash tools/pool.sh <platform>" is set (count + logs on every run; reset only
# with SAQEF_POOL_RESET=1). How each pool is emptied (smoke-tested 2026-10-05, §32):
#   fn        Fn's own idle timeout (30 s) removes hot containers; wait for 0.
#   knative   minScale 0 (SAQEF_KN_SCALE_FROM_ZERO=1): the autoscaler takes the revision to 0
#             live pods (~65 s), then the pods' 300 s termination grace runs out (the kn-hello
#             server is PID 1 and ignores SIGTERM; every teardown since Part A waits the same).
#             Wait for both: no live pod, then no user-container.
#   openwhisk an idle action container lives ~10 min. Re-PUT the action (new revision, same
#             code) so no old container can serve it, then remove the stale ones; the invoker
#             builds new containers on the next request. Wait for 0.
# Exit 1 if the pool is not empty within the cap; the harness records it and the gate drops
# the run (§32 rule 4).
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLAT="${1:-}" ACT="${2:-}"

count() {
    case "$PLAT" in
        fn) docker ps --format '{{.Image}}' | grep -cE '^hello:' ;;
        knative) docker ps --format '{{.Names}}' | grep -c '^k8s_user-container_hello-' ;;
        openwhisk) docker ps --format '{{.Names}}' | grep -c '_guest_hello$' ;;
        *) echo "pool.sh: unknown platform '$PLAT'" >&2; exit 2 ;;
    esac
}
kn_live() {   # hello pods that are not Terminating
    k3s kubectl get pods -n default -l serving.knative.dev/service=hello --no-headers 2>/dev/null \
        | awk '$3 != "Terminating"' | grep -c .
}
wait_for() {   # cap_s label command... : poll every 2 s until the command prints 0
    local cap="$1" label="$2"; shift 2
    local t0=$SECONDS n
    while :; do
        n=$("$@")
        [ "$n" = 0 ] && { echo "pool.sh $PLAT: $label 0 after $((SECONDS - t0)) s"; return 0; }
        [ $((SECONDS - t0)) -ge "$cap" ] && { echo "pool.sh $PLAT: $label still $n after $cap s" >&2; return 1; }
        sleep 2
    done
}

case "$ACT" in
    count) count ;;
    reset)
        case "$PLAT" in
            fn) wait_for 180 "function containers" count ;;
            knative)
                wait_for 240 "live pods" kn_live && wait_for 420 "user-containers" count ;;
            openwhisk)
                python3 - "$REPO" <<'PY' || exit 1
import sys
sys.path.insert(0, sys.argv[1])
from platforms.openwhisk import adapter
print("pool.sh openwhisk: action re-PUT, version %s" % adapter.put_action())
PY
                for c in $(docker ps --format '{{.Names}}' | grep '_guest_hello$'); do
                    docker rm -f "$c" >/dev/null 2>&1
                done
                wait_for 60 "action containers" count ;;
            *) echo "pool.sh: unknown platform '$PLAT'" >&2; exit 2 ;;
        esac ;;
    logs)
        since="${3:?logs needs SINCE (epoch s)}" out="${4:?logs needs OUT}"
        case "$PLAT" in
            fn) names="fnserver" ;;
            knative) names=$(docker ps --format '{{.Names}}' | grep -E '^k8s_(activator|autoscaler)_' | tr '\n' ' ') ;;
            openwhisk) names="openwhisk" ;;
            *) echo "pool.sh: unknown platform '$PLAT'" >&2; exit 2 ;;
        esac
        for n in $names; do
            echo "===== $n"
            docker logs --timestamps --since "$since" "$n" 2>&1
        done | gzip > "$out" ;;
    *) echo "usage: pool.sh fn|knative|openwhisk count|reset|logs SINCE OUT" >&2; exit 2 ;;
esac
