# SAQEF — serverless platform overhead & carbon study

Durable memory for this project. Any opencode/agent session should start here, then read the
report sections referenced below. **The git repo IS the memory** — not any chat or account.

## What this is
Cross-platform measurement study: Fn vs OpenFaaS serving an identical CPU-bound function
(handler busy-spins ~5 ms per invocation; 3000 requests, concurrency 20, 5 runs). Headline
metric: `cp_dynamic_share_pct` = control-plane container CPU / dynamic (load-created) CPU —
a contention-robust discriminator. Decision gate: platform gap in the share must exceed 5 pp.

## How to run
- Fn: `./run_saqef.sh all` -> `results/fn_cpubound*`. See header of run_saqef.sh.
- OpenFaaS: `./run_openfaas.sh [stack|scale|check|verify|bench|gates|all]` -> `results/openfaas_cpubound*`.
- **Never commit a run until `gates` passes.** `SAQEF_REPEAT < 5` writes to a `_quick` outdir
  (never publish). The gates table shows delta%, CPmapped (must be 6/6 `ok`), coverage% (100),
  `host_plausible` (must be true), `physical_plausible`, `host_sat%` (~100 on the codespace is
  the honest saturated-box flag, not a failure).

## Current state (2026-08-05)
- Codespace (shared 2-vCPU VM, **no RAPL**, ~100% saturated): OpenFaaS **settled**.
- Concurrency parity (expert Finding A): a GIL-bound single Python process is ~1-way, so the
  function runs as **N = 2 x cpu_count static replicas** (`docker service scale hello=N`;
  statically scaled before the run to bypass autoscaler lag). `gunicorn` was rejected.
- Final median `cp_dynamic_share_pct`: OpenFaaS **15.82** (sessions 15.87 / 16.29 / 15.82;
  8-replica confirm = 16.12 -> replica-insensitive) vs Fn **23.59–24.59** -> gap ~8 pp, gate cleared.
- 1-replica OpenFaaS read 11.1 — the ratio is **NOT** scheduling-order-invariant; 1-replica
  understated CP (CP idle-waits on the starved function). Use 4-replica numbers.
- All gates pass on the committed set (delta-check 6/6, delta% ~0, coverage 100%, host & physical
  plausible). Expert review Finding A and B both resolved.
- Standing caveats: saturated + co-tenanted VM -> latency/QoS NOT citable (only the share is
  contention-robust); no RAPL -> energy/carbon use the CPU-time model (`busy_core_w` 2/3.5/5 W
  sensitivity band, share is invariant across it).

## Next milestone — bare-metal (Linux, RAPL)
1. Ubuntu LTS; docker engine + compose; `modprobe msr` if RAPL needs it.
2. `git clone https://github.com/bathork1391/saqef.git` (or `git pull` an existing clone).
3. `python3 saqef_harness.py --check` — RAPL should be available here -> real energy, which
   validates the CPU-time model via `rapl_validation_err_pct`.
4. Deploy Fn -> `./run_saqef.sh all` -> `results/fn_cpubound_baremetal`.
5. Deploy OpenFaaS -> `./run_openfaas.sh stack` then scale `hello` to 2 x cpu_count, then
   `./run_openfaas.sh all` -> `results/openfaas_cpubound_baremetal`.
6. Compare shares on the SAME box (this is the point — same hardware, both platforms).
7. Keep `--concurrency < cpu_count` for valid latency/QoS claims.
- Expect the numbers to shift (both platforms) — the gate is the gap, re-measured identically.

## Key pitfalls (learned, expensive)
- GIL concurrency parity: static replicas, never single-replica for the headline.
- Allowlist: `--fn-images hello` (the DEPLOYED image, not the base runtime image).
- Host-window alignment: `host_saturation_pct`/`host_plausible` are measured over the host's own
  sampling window (`host_window_s`, v9.10); v9.11 reads the host counter at `t0` so it equals
  `wall_s`. A 112%-style break is a window artifact, not a real anomaly.
- Delta-check: verify 6/6 `ok` AND delta% ~0 in the gates table before committing results.
- OpenFaaS autoscaler lag: scale statically before the run.
- Codespace git push needs device-flow `gh auth login` after `unset GITHUB_TOKEN`.

## Durable knowledge (read these for depth)
- `SAQEF_TECHNICAL_REPORT.md` — full session log, numbered sections (24–29 = OpenFaaS saga).
- `SAQEF_PAPER_DRAFT.md` — paper structure + corrections log (v9.3…v9.11, methodology fixes).
- `OPENFAAS_SETUP.md` — OpenFaaS deployment + protocol steps (incl. §6 Step 1.5 concurrency parity).
