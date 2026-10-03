#!/usr/bin/env bash
# Workload switch for run_final.sh (runbook §28). Keeps the handler swap in one place.
#
#   bash tools/workload.sh swap payload    copy workloads/payload/* over the four handlers
#   bash tools/workload.sh restore         git-checkout the four handlers (CPU-bound spin)
#   bash tools/workload.sh build           rebuild the OpenFaaS + Knative images from the
#                                          handlers as they are NOW (Fn and OpenWhisk build
#                                          from source at deploy, so they need nothing)
#   bash tools/workload.sh bodies DIR      write the payload bodies (1 KiB, 64 KiB, 512 KiB)
#   bash tools/workload.sh probe DIR [P]   deploy each platform (or the quoted list P), POST
#                                          every body, require a 2xx whose reply equals the
#                                          body, tear down
#
# run_final.sh calls `build` at the start of EVERY session, so the OF/Kn images always
# match the working-tree handlers -- a session killed before it could restore cannot
# leave an echo image behind for the next CPU-bound run.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Every file a workload may replace (workloads/<name>/ mirrors these paths).
HANDLERS=("hello/func.py" "OF_FUNCTION/handler.py" "OF_FUNCTION/index.py" "KNATIVE_FUNCTION/app.py" "OW_FUNCTION/hello.py")
SIZES=(1024 65536 524288)     # bytes; names below are what stamps and §28 use
size_name() { case "$1" in 1024) echo 1k ;; 65536) echo 64k ;; 524288) echo 512k ;; *) echo "${1}b" ;; esac; }
die() { echo "ERROR: $*" >&2; exit 1; }

swap() {
    local w="$1" f
    [ -d "$REPO/workloads/$w" ] || die "no workloads/$w"
    for f in "${HANDLERS[@]}"; do
        [ -f "$REPO/workloads/$w/$f" ] || continue
        cp "$REPO/workloads/$w/$f" "$REPO/$f"
        echo "  $f <- workloads/$w/$f"
    done
}

restore() {
    git -C "$REPO" checkout -- "${HANDLERS[@]}" && echo "  handlers restored (CPU-bound spin)"
}

build() {
    docker build -q -t hello:latest "$REPO/OF_FUNCTION" >/dev/null || die "OF hello:latest build failed"
    echo "  hello:latest rebuilt from OF_FUNCTION/"
    docker build -q -t localhost:5000/saqef/kn-hello:0.0.1 "$REPO/KNATIVE_FUNCTION" >/dev/null \
        || die "kn-hello build failed"
    if ! docker ps --filter name='^registry$' --format '{{.Names}}' | grep -q registry; then
        docker start registry >/dev/null 2>&1 \
            || docker run -d --name registry -p 5000:5000 registry:2 >/dev/null \
            || die "could not start the local image registry"
        sleep 2
    fi
    docker push -q localhost:5000/saqef/kn-hello:0.0.1 >/dev/null || die "kn-hello push failed"
    k3s crictl rmi localhost:5000/saqef/kn-hello:0.0.1 >/dev/null 2>&1 || true
    echo "  kn-hello rebuilt from KNATIVE_FUNCTION/ and pushed"
}

bodies() {
    local dir="$1" n
    mkdir -p "$dir"
    for n in "${SIZES[@]}"; do
        # deterministic printable ASCII, exactly n bytes, no newline
        python3 -c 'import sys; n=int(sys.argv[1]); p=b"saqef-payload-0123456789-abcdefghijklmnopqrstuvwxyz-"; sys.stdout.buffer.write((p*(n//len(p)+1))[:n])' \
            "$n" > "$dir/body_$(size_name "$n").txt"
    done
    ls -l "$dir"/body_*.txt | awk '{print "  " $5 " " $9}'
}

probe() {
    local dir="$1" p fail=0
    for p in ${2:-openfaas fn knative openwhisk}; do
        echo "  probe $p"
        python3 "$REPO/saqef" deploy --platform "$p" >/dev/null 2>&1 || { echo "    deploy FAILED"; fail=1; continue; }
        python3 - "$REPO" "$p" "$dir" <<'PY' || fail=1
import glob, os, sys, urllib.error, urllib.request
repo, plat, d = sys.argv[1:]
sys.path.insert(0, repo)
from platforms import get_adapter
url = get_adapter(plat).url
bad = 0
for f in sorted(glob.glob(os.path.join(d, "body_*.txt")), key=os.path.getsize):
    body = open(f, "rb").read()
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            st, got = r.status, r.read()
    except urllib.error.HTTPError as e:
        st, got = e.code, e.read()
    except Exception as e:
        st, got = None, repr(e).encode()
    ok = st is not None and 200 <= st < 300 and got == body
    bad += not ok
    print("    %-16s %7d B -> status %s, reply %d B %s" % (os.path.basename(f), len(body), st, len(got),
          "OK" if ok else "FAIL (%r)" % got[:80]))
sys.exit(1 if bad else 0)
PY
        python3 "$REPO/saqef" teardown --platform "$p" >/dev/null 2>&1 || true
        if [ "$p" = knative ]; then   # same wait as run_final.sh cleanup_platform
            local w=0
            while [ $w -lt 360 ] && docker ps --format '{{.Names}}' | grep -qE 'user-container|queue-proxy|hello-0000'; do
                sleep 5; w=$((w + 5))
            done
        fi
        sleep 5
    done
    [ "$fail" = 0 ] && echo "  PROBE OK: every platform echoes every body" || echo "  PROBE FAILED"
    return $fail
}

case "${1:-}" in
    swap) swap "${2:?workload name}" ;;
    restore) restore ;;
    build) build ;;
    bodies) bodies "${2:?dir}" ;;
    probe) probe "${2:?dir}" "${3:-}" ;;
    *) echo "usage: $0 swap WORKLOAD | restore | build | bodies DIR | probe DIR" >&2; exit 2 ;;
esac
