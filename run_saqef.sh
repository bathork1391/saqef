#!/usr/bin/env bash
# run_saqef.sh - one-command harness for the SAQEF Fn benchmark on Codespaces.
# Usage: ./run_saqef.sh [setup|check|verify|bench|gates|all]
#   setup  -> start Fn server, (re)install hey, register function + trigger, sanity curl
#   check  -> environment sanity (docker, RAPL, cgroup map, hey)
#   verify -> confirm the deployed function really burns ~5 ms CPU/invocation
#   bench  -> gold-standard run B (cgroup sampler + delta-check + hey, 5 repeats)
#   gates  -> print the accept/reject table for the runs
#   all    -> setup, check, verify, bench, gates (full pipeline)
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

URL="http://localhost:8080/t/app1/hello"
PLATFORM="fn"
CP="fnserver"
OUT="results/fn_cpubound_v9"
VERIFY_N=100
VERIFY_BUDGET_MS=5
TOTAL=3000
CONCURRENCY=20
DURATION=60
WARMUP=20
REPEAT=5

setup_fn() {
  echo "=== [setup] Fn server ==="
  if docker ps --format '{{.Names}}' | grep -qx fnserver; then
    echo "fnserver already running"
  else
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
  fi

  echo "=== [setup] hey (reinstall if broken/truncated) ==="
  HEY_BIN="$(command -v hey || true)"
  if [ -n "$HEY_BIN" ] && [ "$(stat -c%s "$HEY_BIN")" -ge 1000 ]; then
    echo "hey OK: $HEY_BIN ($(stat -c%s "$HEY_BIN") bytes)"
  else
    if command -v go >/dev/null 2>&1; then
      echo "installing hey via go install ..."
      go install github.com/rakyll/hey@latest
      GOHEY="$(go env GOPATH)/bin/hey"
      if [ -f "$GOHEY" ]; then
        sudo ln -sf "$GOHEY" /usr/local/bin/hey
        echo "hey installed from go: $(stat -c%s "$GOHEY") bytes"
      else
        echo "WARNING: go install produced no binary; python loadgen fallback is fine"
      fi
    else
      echo "go not available; trying binary download (storage.googleapis.com now 403s)..."
      curl -sL https://storage.googleapis.com/hey-release/hey_linux_amd64 -o hey
      chmod +x hey
      SIZE="$(stat -c%s hey 2>/dev/null || echo 0)"
      if [ "$SIZE" -ge 1000 ]; then
        sudo mv hey /usr/local/bin/hey
        echo "hey downloaded OK: $SIZE bytes"
      else
        echo "WARNING: download truncated ($SIZE bytes); python loadgen fallback is fine"
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
  python3 saqef_harness.py --url "$URL" --platform "$PLATFORM" --cp-containers "$CP" \
    --total "$TOTAL" --concurrency "$CONCURRENCY" --duration "$DURATION" \
    --warmup "$WARMUP" --repeat "$REPEAT" \
    --sampler cgroup --delta-check --loadgen hey --outdir "$OUT"
}

run_gates() {
  python3 - "$OUT" <<'PY'
import json, glob, sys
out = sys.argv[1]
print("per-run gate table (pass: delta% ~ 0, plausible=true, coverage% >= 95):")
for p in sorted(glob.glob(out + "/run_*")):
    s = json.load(open(p + "/summary.json"))
    cov = round(100 * s["sampling_covered_s"] / s["wall_s"], 1) if s["wall_s"] else 0.0
    print("  %-7s delta%%: %-7s fn_cpu_s: %-6s cp_cpu_s: %-6s ceiling: %-6s plausible: %-5s coverage%%: %s"
          % (p.split("/")[-1], s.get("cp_sampler_vs_delta_pct"),
             s["cpu_sec"]["function"], s["cpu_sec"]["control_plane"],
             s.get("cpu_sec_ceiling"), s.get("physical_plausible"), cov))
med = json.load(open(out + "/summary.json"))
print("median: cp_dynamic_share_pct=%s  slo_compliance=%s  throughput_rps=%s"
      % (med.get("cp_dynamic_share_pct"), med.get("slo_compliance"), med.get("throughput_rps")))
PY
}

case "${1:-all}" in
  setup)  setup_fn ;;
  check)  run_check ;;
  verify) run_verify ;;
  bench)  run_bench ;;
  gates)  run_gates ;;
  all)    setup_fn && run_check && run_verify && run_bench && run_gates ;;
  *) echo "usage: $0 [setup|check|verify|bench|gates|all]"; exit 1 ;;
esac
