#!/usr/bin/env bash
# run_openfaas.sh - one-command SAQEF runner for OpenFaaS (Docker Swarm), the
# OpenFaaS counterpart of run_saqef.sh (Fn). Mirrors its conventions exactly so
# both platforms are driven the same way (protocol parity is a gate condition).
# Usage: ./run_openfaas.sh [stack|scale|check|verify|bench|gates|all]
#   stack  -> (re)deploy the swarm stack from OPENFAAS_DEPLOY/ (idempotent) + wait for gateway
#   scale  -> static replica scale of the hello function service (concurrency parity, §6 Step 1.5)
#   check  -> environment sanity (docker, RAPL, cgroup map, hey)
#   verify -> confirm the deployed function really burns ~5 ms CPU/invocation
#   bench  -> gold-standard run (cgroup sampler + delta-check + hey; count-bound:
#             exactly $TOTAL requests, --duration is a SAFETY cap, not a hard stop)
#   gates  -> print the accept/reject table for the runs (incl. CP cgroup mapping)
#   all    -> stack, scale, check, verify, bench, gates
#
# Env overrides for faster iteration:
#   SAQEF_TOTAL SAQEF_CONCURRENCY SAQEF_DURATION SAQEF_WARMUP SAQEF_REPEAT
#   SAQEF_FN_IMAGES SAQEF_REPLICAS SAQEF_OUT
#   e.g.  SAQEF_TOTAL=600 SAQEF_REPEAT=1 ./run_openfaas.sh bench
#   If SAQEF_REPEAT < 5, results go to <SAQEF_OUT>_quick (never the final
#   outdir), so a 1-run pass cannot be mistaken for the 5-run publication set.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

URL="http://127.0.0.1:8080/function/hello"
PLATFORM="openfaas"
CP="gateway,faas-swarm,prometheus,nats,queue-worker,alertmanager"
FN_IMAGES="${SAQEF_FN_IMAGES:-hello}"
FN_IMAGES_ARG=""
[ -n "$FN_IMAGES" ] && FN_IMAGES_ARG="--fn-images $FN_IMAGES"
OUT="${SAQEF_OUT:-results/openfaas_cpubound}"
REPLICAS="${SAQEF_REPLICAS:-4}"
VERIFY_N=100
VERIFY_BUDGET_MS=5
TOTAL="${SAQEF_TOTAL:-3000}"
CONCURRENCY="${SAQEF_CONCURRENCY:-20}"
DURATION="${SAQEF_DURATION:-60}"
WARMUP="${SAQEF_WARMUP:-20}"
REPEAT="${SAQEF_REPEAT:-5}"
FULL_REPEAT=5

setup_stack() {
  echo "=== [stack] (re)deploy OpenFaaS swarm stack from OPENFAAS_DEPLOY/ ==="
  docker swarm init >/dev/null 2>&1 || true
  (cd OPENFAAS_DEPLOY && docker stack deploy openfaas -c docker-compose.yml) || exit 1
  echo "waiting for gateway (0.8.3, no auth) at $URL ..."
  for i in $(seq 1 45); do
    curl -sf http://127.0.0.1:8080/version >/dev/null 2>&1 && break
    sleep 2
  done
  docker service ls
}

setup_scale() {
  echo "=== [scale] hello function replicas -> $REPLICAS (concurrency parity: GIL-bound single process is ~1-way) ==="
  if ! docker service ls --format '{{.Name}}' | grep -qx hello; then
    echo "ERROR: no 'hello' swarm service - deploy the function first (OPENFAAS_SETUP.md §4)"; exit 1
  fi
  docker service scale hello="$REPLICAS"
  for i in $(seq 1 30); do
    docker service ls --format '{{.Name}} {{.Replicas}}' | awk '$1=="hello" {print $2}' | grep -qx "$REPLICAS/$REPLICAS" && break
    sleep 2
  done
  docker ps --format '{{.Names}}' | grep hello || echo "WARNING: no hello containers visible"
}

run_check() {
  python3 saqef_harness.py --check
}

run_verify() {
  python3 saqef_harness.py --verify --sampler cgroup \
    --url "$URL" --platform "$PLATFORM" --cp-containers "$CP" \
    $FN_IMAGES_ARG \
    --verify-n "$VERIFY_N" --verify-budget-ms "$VERIFY_BUDGET_MS"
}

run_bench() {
  if [ "$REPEAT" -lt "$FULL_REPEAT" ]; then
    echo ""
    echo "#######################################################################"
    echo "# QUICK/ITERATION RUN: SAQEF_REPEAT=$REPEAT < $FULL_REPEAT             #"
    echo "# Results written to ${OUT}_quick - NOT for publication.              #"
    echo "# Run 'SAQEF_REPEAT=$FULL_REPEAT ./run_openfaas.sh bench' for finals. #"
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
  [ "$REPEAT" -lt "$FULL_REPEAT" ] && GATES_OUT="${OUT}_quick"
  python3 - "$GATES_OUT" <<'PY'
import json, glob, sys
out = sys.argv[1]
print("per-run gate table (pass: delta% ~ 0, CPmapped=6/6, plausible=true, host_saturated flags, 95 <= coverage% <= 100):")
for p in sorted(glob.glob(out + "/run_*")):
    s = json.load(open(p + "/summary.json"))
    cov = round(100 * s["sampling_covered_s"] / s["wall_s"], 1) if s["wall_s"] else 0.0
    m = s.get("delta_check_map") or {}
    okmap = sum(1 for v in m.values() if v == "ok")
    nfn = sum(1 for n in s.get("container_inventory", []) if n.startswith("hello"))
    flag = "  <-- QoS CONTENTION-CONTAMINATED (>=85% sat): do not cite latency"
    covflag = "  <-- COVERAGE INVARIANT BROKEN (>100%): do not cite"
    flags = (flag if s.get("host_saturated") else "") + (covflag if cov > 100 else "")
    print("  %-7s delta%%: %-7s delta_s: %-6s cp_cpu_s: %-6s fn_cpu_s: %-6s CPmapped: %s/%s fn_replicas: %s unclass: %s coverage%%: %s%s"
          % (p.split("/")[-1], s.get("cp_sampler_vs_delta_pct"),
             s.get("cp_delta_sec"), s["cpu_sec"]["control_plane"], s["cpu_sec"]["function"],
             okmap, len(m), nfn, s["unclassified_cpu_s"], cov, flags))
med = json.load(open(out + "/summary.json"))
print("median: cp_dynamic_share_pct=%s  slo_compliance=%s  throughput_rps=%s"
      % (med.get("cp_dynamic_share_pct"), med.get("slo_compliance"), med.get("throughput_rps")))
PY
}

case "${1:-all}" in
  stack)  setup_stack ;;
  scale)  setup_scale ;;
  check)  run_check ;;
  verify) run_verify ;;
  bench)  run_bench ;;
  gates)  run_gates ;;
  all)    setup_stack && setup_scale && run_check && run_verify && run_bench && run_gates ;;
  *) echo "usage: $0 [stack|scale|check|verify|bench|gates|all]"; exit 1 ;;
esac
