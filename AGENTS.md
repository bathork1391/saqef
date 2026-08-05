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
- **Bare-metal milestone COMPLETE** (8-core Ubuntu, RAPL available). Results in
  `results/fn_cpubound_baremetal` + `results/openfaas_cpubound_baremetal` (protocol-conformant,
  all gates green). Old saturated-invalid runs preserved in `*_sat_invalid/` (do not cite).
- **RAPL validated.** Machine idle package power measured **4.3 W** (60 s RAPL read, stack up).
  The hardcoded `idle_w=30` gave `rapl_validation_err_pct` ~45% everywhere; with
  `SAQEF_IDLE_W=4.3` validation settles to **4.2–5.5% on Fn** (runs 4–5) and **4.2–8.2% on
  OpenFaaS**, steady-state (OF run_1/2 transient 34.6/25.9; Fn run_1–3 transient 36→19→19 — both
  platforms show the same ~2-run stack/frequency settling). The earlier "0.9–6.1% on Fn" figure
  belongs to the DISCARDED tainted run and must NOT be cited. `SAQEF_IDLE_W` was added to both
  runner scripts (knob only, no measurement-path change).
- **Protocol-conformant bare-metal results** (`c=4 < cpu_count`, `TOTAL=10000`, 5 runs,
  host_sat ~74–77% -> `host_saturated=false`, coverage 100%, delta-map 6/6, delta%~0):
  Fn median `cp_dynamic_share_pct` **10.46** (9.80–11.74, CV 8.4% — run-order drift on fnserver,
  runs 4–5 ~11.7) vs OpenFaaS **7.67** (6.80–7.83, CV 6.4%) -> **gap ≈ 2.8 pp < 5 pp GATE FAILS**.
- **Regime-dependence is now the finding.** The gap is ~8 pp on the saturated 2-core codespace
  but ~2.8 pp on an 8-core box with headroom; the direction is stable (Fn's share higher) but
  the magnitude collapses. Cause: Fn's per-request CP cost is 1.22 ms under saturation vs
  0.66 ms here (0.56 ms for OpenFaaS both regimes). OpenFaaS's share is conservative (of-watchdog
  proxies inside the function cgroup), so the true gap is even smaller. The 5 pp decision gate
  is therefore NOT machine-invariant; paper must be reframed (per-machine-pair gate + the
  machine-dependence as a contribution).
- **Concurrency sensitivity (c=8, REPEAT=2, `*_c8_quick`): flat within the box.** Fn 10.47,
  OpenFaaS 7.62 at c=8 (host_sat ~91–93%) vs c=4 values 10.46/7.67 — the share and gap are
  invariant to concurrency/saturation on the 8-core box. The ~8 pp codespace gap is therefore a
  machine (CPU-count) effect, not a load effect: both platforms' shares are ~2.3× higher on the
  2-core VM, and Fn inflates proportionally more.
- QoS now citable for the first time (host_sat <85%): Fn p50 6.5 / p99 8.9 ms @ 597 rps;
  OpenFaaS p50 7.2 / p99 12.1 ms @ 532 rps; SLO compliance 1.0 both.
- Codespace numbers (Fn 23.59–24.59 vs OpenFaaS 15.82, gap ~8 pp) remain the saturated-regime
  dataset; the expert-review Findings A/B resolutions still stand.

## Bare-metal protocol (proven on this box)
1. `sudo bash setup_baremetal.sh` (docker + CLIs + RAPL readable by the user).
2. Calibrate idle watts: 60 s RAPL read with the platform up, zero traffic -> `SAQEF_IDLE_W`.
3. Run OpenFaaS FIRST (its function service must be gone before Fn — see pitfall below):
   `SAQEF_REPLICAS=16 SAQEF_CONCURRENCY=4 SAQEF_TOTAL=10000 SAQEF_REPEAT=5
    SAQEF_IDLE_W=<W> SAQEF_OUT=results/openfaas_cpubound_baremetal sudo bash run_openfaas.sh all`
4. `sudo docker stack rm openfaas` **and** `sudo docker service rm hello`, then Fn:
   `SAQEF_CONCURRENCY=4 SAQEF_TOTAL=10000 SAQEF_REPEAT=5
    SAQEF_IDLE_W=<W> SAQEF_OUT=results/fn_cpubound_baremetal sudo bash run_saqef.sh all`
5. `gates` must show `host_saturated=false` (else latency not citable) and the share comparison
   decides the paper question.

## Key pitfalls (learned, expensive)
- GIL concurrency parity: static replicas, never single-replica for the headline.
- Allowlist: `--fn-images hello` (the DEPLOYED image, not the base runtime image).
- Host-window alignment: `host_saturation_pct`/`host_plausible` are measured over the host's own
  sampling window (`host_window_s`, v9.10); v9.11 reads the host counter at `t0` so it equals
  `wall_s`. A 112%-style break is a window artifact, not a real anomaly.
- Delta-check: verify 6/6 `ok` AND delta% ~0 in the gates table before committing results.
- OpenFaaS autoscaler lag: scale statically before the run.
- **`docker stack rm openfaas` does NOT remove the `hello` function service** (it is deployed
  outside the stack). Its replicas fold into Fn's `fn_cpu` via the `hello` image allowlist and
  taint the Fn run — always `docker service rm hello` before Fn.
- **Calibrate `SAQEF_IDLE_W`** to the machine's measured idle package watts (60 s RAPL read,
  stack up, zero traffic) or `rapl_validation_err_pct` stays ~45% on bare metal.
- Codespace git push needs device-flow `gh auth login` after `unset GITHUB_TOKEN`.

## Durable knowledge (read these for depth)
- `SAQEF_TECHNICAL_REPORT.md` — full session log, numbered sections (24–29 = OpenFaaS saga).
- `SAQEF_PAPER_DRAFT.md` — paper structure + corrections log (v9.3…v9.11, methodology fixes).
- `OPENFAAS_SETUP.md` — OpenFaaS deployment + protocol steps (incl. §6 Step 1.5 concurrency parity).
