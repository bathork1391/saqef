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
#   bash tools/workload.sh probe-mem KIB [P]  W2 (runbook §30): deploy each platform, GET, require
#                                          a 2xx whose reply names "kib" KIB (the arm that is
#                                          swapped in), tear down
#   bash tools/workload.sh probe-io ARM [P]  W5 (runbook §36): deploy each platform, GET at c = 1
#                                          and c = 8, require a 2xx naming io=ARM (spin|sleep|disk|
#                                          mixed), print the handler's wait and the file's filesystem
#   bash tools/workload.sh variant         print the swapped-in W2 arm's SAQEF_MEM_KIB, or "cpu"
#   bash tools/workload.sh io-variant      print the swapped-in W5 arm's SAQEF_IO, or "none"
#   bash tools/workload.sh probe-w6 ARM [P]  W6 (runbook §38): deploy each platform, GET 200 times
#                                          at c = 1 and 200 at c = 8; every 2xx reply must name w6=ARM,
#                                          and the share of non-2xx replies must match the arm's design
#                                          (base, alt: 0 %; fail5: 5 %; fail20: 20 %; ±3 points)
#   bash tools/workload.sh w6-variant      print the swapped-in W6 arm's SAQEF_W6, or "none"
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
        cp "$REPO/workloads/$w/$f" "$REPO/$f" || die "could not copy workloads/$w/$f over $f"
        cmp -s "$REPO/workloads/$w/$f" "$REPO/$f" || die "$f does not match workloads/$w/$f after the copy"
        echo "  $f <- workloads/$w/$f"
    done
}

restore() {
    # safe.directory: go.sh runs as root on a user-owned repo; without it git refuses and the
    # echo handlers survived the 2026-10-03 payload session.
    git -c safe.directory="$REPO" -C "$REPO" checkout -- "${HANDLERS[@]}" \
        || die "handler restore failed -- working tree still has the swapped handlers"
    # Check the content too, not just git's exit code.
    git -c safe.directory="$REPO" -C "$REPO" diff --quiet HEAD -- "${HANDLERS[@]}" \
        || die "handlers still differ from HEAD after restore"
    echo "  handlers restored (CPU-bound spin)"
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

probe_mem() {
    local kib="$1" p fail=0
    for p in ${2:-openfaas fn knative openwhisk}; do
        echo "  probe-mem $p (want kib=$kib)"
        python3 "$REPO/saqef" deploy --platform "$p" >/dev/null 2>&1 || { echo "    deploy FAILED"; fail=1; continue; }
        python3 - "$REPO" "$p" "$kib" <<'PY' || fail=1
import re, sys, urllib.error, urllib.request
repo, plat, kib = sys.argv[1:]
sys.path.insert(0, repo)
from platforms import get_adapter
try:
    with urllib.request.urlopen(get_adapter(plat).url, timeout=60) as r:
        st, got = r.status, r.read().decode("utf-8", "replace")
except urllib.error.HTTPError as e:
    st, got = e.code, e.read().decode("utf-8", "replace")
except Exception as e:
    st, got = None, repr(e)
m = re.search(r"""kib["']?\s*[:=]\s*(\d+)""", got)
ok = st is not None and 200 <= st < 300 and m is not None and m.group(1) == kib
print("    status %s, reply %r %s" % (st, got[:80], "OK" if ok else "FAIL"))
sys.exit(0 if ok else 1)
PY
        python3 "$REPO/saqef" teardown --platform "$p" >/dev/null 2>&1 || true
        if [ "$p" = knative ]; then
            local w=0
            while [ $w -lt 360 ] && docker ps --format '{{.Names}}' | grep -qE 'user-container|queue-proxy|hello-0000'; do
                sleep 5; w=$((w + 5))
            done
        fi
        sleep 5
    done
    [ "$fail" = 0 ] && echo "  PROBE OK: every platform serves the kib=$kib arm" || echo "  PROBE FAILED"
    return $fail
}

probe_io() {
    # W5 (NEXT_STEPS "I/O-bound gap"), unmeasured: the io_<arm> handlers must be swapped in and
    # the OF/Kn images built. Per platform: 20 warm-up GETs, N sequential GETs, then N GETs at
    # c = 8; every reply must be 2xx and name the arm. Prints the handler's own wait (us) and the
    # filesystem /tmp sits on. Disk: a median wait under 50 us or fs tmpfs means fsync never
    # reached the device (WARN, not a failure: the probe exists to find that out).
    local arm="$1" p fail=0
    for p in ${2:-openfaas fn knative openwhisk}; do
        echo "  probe-io $p (want io=$arm)"
        # a failed deploy can leave the platform half up (fnserver holds :8080, which OpenFaaS
        # needs), so tear down before moving on
        python3 "$REPO/saqef" deploy --platform "$p" >/dev/null 2>&1 || { echo "    deploy FAILED"; fail=1
            python3 "$REPO/saqef" teardown --platform "$p" >/dev/null 2>&1; sleep 5; continue; }
        python3 - "$REPO" "$p" "$arm" "${SAQEF_PROBE_N:-300}" <<'PY' || fail=1
import re, statistics, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
repo, plat, arm, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
sys.path.insert(0, repo)
from platforms import get_adapter
url = get_adapter(plat).url
pat = re.compile(r"io=(\w+) us=(\d+) fs=(\S+?)[\"'}\s]")

def call(_=None):
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            st, got = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        st, got = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        st, got = None, repr(e)
    m = pat.search(got + " ")
    ok = st is not None and 200 <= st < 300 and m is not None and m.group(1) == arm
    return ok, (int(m.group(2)) if m else None), (m.group(3) if m else None), (time.perf_counter() - t) * 1e3, st, got[:80]

def q(v, f):
    return v[min(len(v) - 1, int(f * len(v)))]

def report(tag, rs):
    bad = [r for r in rs if not r[0]]
    us = sorted(r[1] for r in rs if r[0])
    ms = sorted(r[3] for r in rs if r[0])
    fs = sorted({r[2] for r in rs if r[0]})
    if not us:
        print("    %-5s all %d calls FAILED, e.g. status %s %r" % (tag, len(rs), bad[0][4], bad[0][5]))
        return len(bad), None, fs
    cv = 100 * statistics.pstdev(us) / statistics.mean(us) if statistics.mean(us) else 0
    print("    %-5s ok %d/%d  handler wait us: median %d p90 %d p99 %d max %d CV %.0f%%  client ms: median %.2f p99 %.2f  fs %s"
          % (tag, len(us), len(rs), statistics.median(us), q(us, .9), q(us, .99), us[-1], cv,
             statistics.median(ms), q(ms, .99), ",".join(fs)))
    if bad:
        print("          failed e.g. status %s %r" % (bad[0][4], bad[0][5]))
    return len(bad), statistics.median(us), fs

for _ in range(20):
    call()
b1, med, fs = report("c=1", [call() for _ in range(n)])
with ThreadPoolExecutor(8) as ex:
    b8, _, _ = report("c=8", list(ex.map(call, range(n))))
if arm == "disk" and med is not None and (med < 50 or "tmpfs" in fs):
    print("    WARN: fsync looks like a no-op here (median %d us, fs %s)" % (med, ",".join(fs)))
# c = 1 must be clean; c = 8 failures are reported (Fn grows its pool under load, runbook §31.11)
sys.exit(1 if b1 or med is None else 0)
PY
        python3 "$REPO/saqef" teardown --platform "$p" >/dev/null 2>&1 || true
        if [ "$p" = knative ]; then
            local w=0
            while [ $w -lt 360 ] && docker ps --format '{{.Names}}' | grep -qE 'user-container|queue-proxy|hello-0000'; do
                sleep 5; w=$((w + 5))
            done
        fi
        sleep 5
    done
    [ "$fail" = 0 ] && echo "  PROBE OK: every platform serves io=$arm" || echo "  PROBE FAILED"
    return $fail
}

probe_w6() {
    local arm="$1" p fail=0
    for p in ${2:-openfaas fn knative openwhisk}; do
        echo "  probe-w6 $p (want w6=$arm, ${SAQEF_PROBE_N:-200} GETs per phase)"
        python3 "$REPO/saqef" deploy --platform "$p" >/dev/null 2>&1 || { echo "    deploy FAILED"; fail=1
            python3 "$REPO/saqef" teardown --platform "$p" >/dev/null 2>&1; sleep 5; continue; }
        python3 - "$REPO" "$p" "$arm" "${SAQEF_PROBE_N:-200}" <<'PY' || fail=1
import collections, re, sys, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
repo, plat, arm, n = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
sys.path.insert(0, repo)
from platforms import get_adapter
url = get_adapter(plat).url
want = {"base": 0.0, "alt": 0.0, "fail5": 0.05, "fail20": 0.20}[arm]

def call(_=None):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            st, got = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        st, got = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        st, got = None, repr(e)
    m = re.search(r"w6=(\w+) call=(\w+) (ok|error)", got)
    return st, (m.group(1) if m else None), (m.group(2) if m else None), got[:80]

bad = 0
for _ in range(10):
    call()   # warm-up; with fail arms some of these fail by design
rs = [call() for _ in range(n)]
with ThreadPoolExecutor(8) as ex:
    rs8 = list(ex.map(call, range(n)))
for tag, rs in (("c=1", rs), ("c=8", rs8)):
    codes = collections.Counter(r[0] for r in rs)
    ok = [r for r in rs if r[0] is not None and 200 <= r[0] < 300]
    wrong = [r for r in ok if r[1] != arm]
    err = 1 - len(ok) / len(rs)
    kinds = collections.Counter(r[2] for r in ok)
    good = not wrong and abs(err - want) <= 0.03
    print("    %-4s codes %s  non-2xx %.1f %% (want %.0f %% ±3)  2xx naming w6=%s: %d/%d  calls %s  %s"
          % (tag, dict(codes), 100 * err, 100 * want, arm, len(ok) - len(wrong), len(ok), dict(kinds),
             "OK" if good else "FAIL e.g. %r" % ((wrong or rs)[0][3],)))
    bad += not good
sys.exit(1 if bad else 0)
PY
        python3 "$REPO/saqef" teardown --platform "$p" >/dev/null 2>&1 || true
        if [ "$p" = knative ]; then
            local w=0
            while [ $w -lt 360 ] && docker ps --format '{{.Names}}' | grep -qE 'user-container|queue-proxy|hello-0000'; do
                sleep 5; w=$((w + 5))
            done
        fi
        sleep 5
    done
    [ "$fail" = 0 ] && echo "  PROBE OK: every platform serves w6=$arm as designed" || echo "  PROBE FAILED"
    return $fail
}

w6_variant() {
    local k
    k=$(grep -hoE '^SAQEF_W6 = "[a-z0-9]+"' "$REPO/hello/func.py" "$REPO/OF_FUNCTION/handler.py" \
        "$REPO/KNATIVE_FUNCTION/app.py" "$REPO/OW_FUNCTION/hello.py" 2>/dev/null | sort -u)
    case "$(echo "$k" | grep -c .)" in
        0) echo none ;;
        1) k="${k##* }"; echo "${k//\"/}" ;;
        *) die "handlers disagree on SAQEF_W6: $(echo $k)" ;;
    esac
}

io_variant() {
    local k
    k=$(grep -hoE '^SAQEF_IO = "[a-z]+"' "$REPO/hello/func.py" "$REPO/OF_FUNCTION/handler.py" \
        "$REPO/KNATIVE_FUNCTION/app.py" "$REPO/OW_FUNCTION/hello.py" 2>/dev/null | sort -u)
    case "$(echo "$k" | grep -c .)" in
        0) echo none ;;
        1) k="${k##* }"; echo "${k//\"/}" ;;
        *) die "handlers disagree on SAQEF_IO: $(echo $k)" ;;
    esac
}

variant() {
    local k
    k=$(grep -hoE '^SAQEF_MEM_KIB = [0-9]+' "$REPO/hello/func.py" "$REPO/OF_FUNCTION/handler.py" \
        "$REPO/KNATIVE_FUNCTION/app.py" "$REPO/OW_FUNCTION/hello.py" 2>/dev/null | sort -u)
    case "$(echo "$k" | grep -c .)" in
        0) echo cpu ;;
        1) echo "${k##* }" ;;
        *) die "handlers disagree on SAQEF_MEM_KIB: $(echo $k)" ;;
    esac
}

case "${1:-}" in
    swap) swap "${2:?workload name}" ;;
    restore) restore ;;
    build) build ;;
    bodies) bodies "${2:?dir}" ;;
    probe) probe "${2:?dir}" "${3:-}" ;;
    probe-mem) probe_mem "${2:?kib}" "${3:-}" ;;
    probe-io) probe_io "${2:?arm: spin, sleep, disk or mixed}" "${3:-}" ;;
    variant) variant ;;
    io-variant) io_variant ;;
    probe-w6) probe_w6 "${2:?arm: base, fail5, fail20 or alt}" "${3:-}" ;;
    w6-variant) w6_variant ;;
    *) echo "usage: $0 swap WORKLOAD | restore | build | bodies DIR | probe DIR | probe-mem KIB | probe-io ARM | variant | io-variant | probe-w6 ARM | w6-variant" >&2; exit 2 ;;
esac
