#!/usr/bin/env bash
# run_saqef.sh - one-command harness for the SAQEF Fn benchmark on Codespaces.
# Usage: ./run_saqef.sh [setup|check|verify|bench|gates|all]
#   reset  -> FRESH-SESSION protocol: remove fnserver + orphaned function containers
#   setup  -> reset, start Fn server, (re)install hey, register function + trigger, sanity curl
#   check  -> environment sanity (docker, RAPL, cgroup map, hey)
#   verify -> confirm the deployed function really burns ~5 ms CPU/invocation
#   bench  -> gold-standard run B (cgroup sampler + delta-check + hey; count-bound:
#             exactly $TOTAL requests, --duration is a SAFETY cap, not a hard stop)
#   gates  -> print the accept/reject table for the runs
#   all    -> setup, check, verify, bench, gates (full pipeline, always from a fresh fnserver)
#
# Env overrides for faster iteration:
#   SAQEF_TOTAL SAQEF_CONCURRENCY SAQEF_DURATION SAQEF_WARMUP SAQEF_REPEAT
#   SAQEF_FN_IMAGES (function-image allowlist; default = the hello function image)
#   e.g.  SAQEF_TOTAL=600 SAQEF_REPEAT=1 ./run_saqef.sh bench
#   If SAQEF_REPEAT < 5, results go to results/<name>_quick (never the final
#   outdir), so a 1-run pass cannot be mistaken for the 5-run publication set.
set -uo pipefail

# Functional smoke test for the hey binary. A real rakyll/hey emits a parseable
# JSON report for -o json; a stale 403-HTML page or a wrong binary does not.
# Size is a useless gate (a truncated/corrupted binary can still be >1000 bytes),
# so this is what actually decides whether to reuse or reinstall hey.
hey_smoke_ok() {
  "$1" -n 2 -c 1 -o json http://localhost:8080/ 2>/dev/null |
    python3 -c 'import sys, json; json.load(sys.stdin)'
}

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

URL="http://localhost:8080/t/app1/hello"
PLATFORM="fn"
CP="fnserver"
# Function allowlist image: match the DEPLOYED function image name (hello:*),
# NOT the base runtime fnproject/python:* -- the running containers are the
# built image (container image field), and BuildKit does not reliably keep the
# FROM lineage for an `ancestor=` filter. On Fn the deployed image is the only
# dependable handle for the opaque-ULID-named function containers.
FN_IMAGES="${SAQEF_FN_IMAGES:-hello}"
FN_IMAGES_ARG=""
[ -n "$FN_IMAGES" ] && FN_IMAGES_ARG="--fn-images $FN_IMAGES"
OUT="results/fn_cpubound_v9"
VERIFY_N=100
VERIFY_BUDGET_MS=5
TOTAL="${SAQEF_TOTAL:-3000}"
CONCURRENCY="${SAQEF_CONCURRENCY:-20}"
DURATION="${SAQEF_DURATION:-60}"
WARMUP="${SAQEF_WARMUP:-20}"
REPEAT="${SAQEF_REPEAT:-5}"
FULL_REPEAT=5

reset_fn() {
  echo "=== [reset] fresh-session protocol: remove fnserver + orphaned function containers ==="
  docker rm -f fnserver >/dev/null 2>&1 || true
  # Fn function containers are named with opaque ULIDs; the DEPLOYED image name
  # (hello:*) is the only reliable handle -- `ancestor=fnproject/python:3.12`
  # matches the base runtime, not the running hello:* images (BuildKit does not
  # preserve the FROM lineage). Clean orphans so leftover warm/zombie containers
  # from a prior session cannot be folded into fn_cpu and inflate it (report §18).
  while read -r c img; do
    case "$img" in
      hello:*) docker rm -f "$c" >/dev/null 2>&1 || true ;;
    esac
  done < <(docker ps -aq --format '{{.ID}} {{.Image}}')
  rm -rf /tmp/iofs /tmp/data
  echo "clean: $(docker ps -aq | wc -l) containers left"
}

setup_fn() {
  reset_fn
  echo "=== [setup] Fn server ==="
  mkdir -p /tmp/iofs /tmp/data
  docker run -d --rm --name fnserver \
    -v /tmp/iofs:/iofs \
    -e FN_IOFS_DOCKER_PATH=/tmp/iofs \
    -e FN_IOFS_PATH=/iofs \
    -v /tmp/data:/app/data \
    -v /var/run/docker.sock:/var/run/docker.sock \
    --privileged -p 8080:8080 \
    --entrypoint ./fnserver -e FN_LOG_LEVEL=DEBUG \
    fnproject/fnserver
  sleep 6

  echo "=== [setup] hey (reinstall unless it passes a functional smoke test) ==="
  HEY_BIN="$(command -v hey || true)"
  if [ -n "$HEY_BIN" ] && hey_smoke_ok "$HEY_BIN"; then
    echo "hey OK: $HEY_BIN (functional smoke test passed)"
  else
    echo "hey broken/missing (no parseable JSON report); wiping and reinstalling ..."
    for p in "$HEY_BIN" /go/bin/hey /usr/local/bin/hey; do
      [ -n "$p" ] && sudo rm -f "$p" 2>/dev/null || true
    done
    if command -v go >/dev/null 2>&1; then
      echo "installing hey via go install ..."
      go install github.com/rakyll/hey@latest
      GOHEY="$(go env GOPATH)/bin/hey"
      if [ -f "$GOHEY" ] && hey_smoke_ok "$GOHEY"; then
        sudo ln -sf "$GOHEY" /usr/local/bin/hey
        echo "hey installed from go and smoke-tested OK: $GOHEY ($(stat -c%s "$GOHEY") bytes)"
      else
        echo "WARNING: go-installed hey failed its smoke test; python loadgen fallback is fine"
      fi
    else
      echo "go not available; trying binary download (storage.googleapis.com now 403s)..."
      curl -sL https://storage.googleapis.com/hey-release/hey_linux_amd64 -o hey
      chmod +x hey
      if hey_smoke_ok ./hey; then
        sudo mv hey /usr/local/bin/hey
        echo "hey downloaded and smoke-tested OK"
      else
        echo "WARNING: download unusable (truncated 403 page?); python loadgen fallback is fine"
        rm -f hey
      fi
    fi
  fi

  echo "=== [setup] function + trigger ==="
  export FN_API_URL=http://localhost:8080
  [ -d hello ] || fn init --runtime python hello
  (cd hello && fn deploy --create-app --app app1 --local) || true
  fn create trigger -s /hello -t http app1 hello http-hello 2>/dev/null || echo "trigger already exists"

  echo "=== [setup] sanity ==="
  curl -s "$URL"; echo
  docker ps
}

run_check() {
  python3 saqef_harness.py --check
}

run_verify() {
  python3 saqef_harness.py --verify --sampler cgroup \
    --url "$URL" --platform "$PLATFORM" --cp-containers "$CP" \
    --verify-n "$VERIFY_N" --verify-budget-ms "$VERIFY_BUDGET_MS"
}

run_bench() {
  if [ "$REPEAT" -lt "$FULL_REPEAT" ]; then
    echo ""
    echo "#######################################################################"
    echo "# QUICK/ITERATION RUN: SAQEF_REPEAT=$REPEAT < $FULL_REPEAT             #"
    echo "# Results written to results/..._quick - NOT for publication.          #"
    echo "# Run 'SAQEF_REPEAT=$FULL_REPEAT ./run_saqef.sh bench' for final numbers. #"
    echo "#######################################################################"
    echo ""
    python3 saqef_harness.py --url "$URL" --platform "$PLATFORM" --cp-containers "$CP" \
      $FN_IMAGES_ARG \
      --total "$TOTAL" --concurrency "$CONCURRENCY" --duration "$DURATION" \
      --warmup "$WARMUP" --repeat "$REPEAT" \
      --sampler cgroup --delta-check --loadgen hey --outdir "${OUT}_quick"
  else
    python3 saqef_harness.py --url "$URL" --platform "$PLATFORM" --cp-containers "$CP" \
      $FN_IMAGES_ARG \
      --total "$TOTAL" --concurrency "$CONCURRENCY" --duration "$DURATION" \
      --warmup "$WARMUP" --repeat "$REPEAT" \
      --sampler cgroup --delta-check --loadgen hey --outdir "$OUT"
  fi
}

run_gates() {
  GATES_OUT="$OUT"
  if [ "$REPEAT" -lt "$FULL_REPEAT" ]; then
    GATES_OUT="${OUT}_quick"
  fi
  python3 - "$GATES_OUT" <<'PY'
import json, glob, sys
out = sys.argv[1]
print("per-run gate table (pass: delta% ~ 0, plausible=true, host_plausible=true, host_saturated=false, coverage% >= 95):")
for p in sorted(glob.glob(out + "/run_*")):
    s = json.load(open(p + "/summary.json"))
    cov = round(100 * s["sampling_covered_s"] / s["wall_s"], 1) if s["wall_s"] else 0.0
    flag = "  <-- QoS CONTENTION-CONTAMINATED (>=85% sat): do not cite latency"
    print("  %-7s delta%%: %-7s cp_cpu_s: %-6s fn_cpu_s: %-6s plausible: %-5s host_sat%%: %-5s host_plausible: %-5s host_saturated: %-5s coverage%%: %s%s"
          % (p.split("/")[-1], s.get("cp_sampler_vs_delta_pct"),
             s["cpu_sec"]["control_plane"], s["cpu_sec"]["function"],
             s.get("physical_plausible"), s.get("host_saturation_pct"),
             s.get("host_plausible"), s.get("host_saturated"), cov,
             flag if s.get("host_saturated") else ""))
med = json.load(open(out + "/summary.json"))
print("median: cp_dynamic_share_pct=%s  slo_compliance=%s  throughput_rps=%s"
      % (med.get("cp_dynamic_share_pct"), med.get("slo_compliance"), med.get("throughput_rps")))
PY
}

case "${1:-all}" in
  reset)  reset_fn ;;
  setup)  setup_fn ;;
  check)  run_check ;;
  verify) run_verify ;;
  bench)  run_bench ;;
  gates)  run_gates ;;
  all)    setup_fn && run_check && run_verify && run_bench && run_gates ;;
  *) echo "usage: $0 [reset|setup|check|verify|bench|gates|all]"; exit 1 ;;
esac
