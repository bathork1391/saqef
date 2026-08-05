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

## Current state (2026-08-05 / 06)
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
- **Concurrency sensitivity (c=8, REPEAT=2, `*_c8_quick`): flat within the box, but NOT YET
  citable as a trend line.** Fn 10.47, OpenFaaS 7.62 at c=8 (host_sat ~91–93%) vs c=4 values
  10.46/7.67 — share and gap look invariant to concurrency/saturation on this 8-core box.
  REPEAT=2 is fine for confirming flatness against the already-validated c=4 baseline, but if
  this becomes a headline/citable number, bump to REPEAT=5 for consistency with everything else
  reported.
- **CPU-count effect — CONFIRMED with a controlled, same-instrument experiment (2026-08-06).**
  (Resolves the expert-review catch above.) THIS bare-metal box was cpuset-pinned to 2 logical
  cores via `sudo bash pin_cpuset.sh 0,1 &` (repo script, formalized 2026-08-06 — loops
  `docker update --cpuset-cpus` over every running container every 0.5s for the life of the
  benchmark; a one-shot pin is not enough because Fn's function containers are ephemeral and new
  ones keep appearing under load, unlike OpenFaaS's static swarm replicas), plus `hey` + the
  harness process wrapped in `taskset -c 0,1`. Then Fn + OpenFaaS were rerun with
  today's corrected protocol (fixed spin, `SAQEF_IDLE_W=4.3`, REPEAT=5,
  `SAQEF_CPU_COUNT_OVERRIDE=2`, `SAQEF_HOST_CPU_LIST=0,1` — see pitfall below on why BOTH knobs
  are required, not just one). Result: **Fn 13.91 (13.34–14.07, CV 2.6%) vs OpenFaaS 6.82
  (6.28–6.96, CV 4.1%) → gap 7.09 pp** — up sharply from 2.79/2.85 pp at 4/8 cores on this same
  box, and now much closer to the original (flawed-instrument) codespace gap of 8.77 pp than to
  the clean 8-core gap. Direction confirmed: fewer cores → bigger gap, on the same corrected
  instrument. All gates pass on the citable metric (delta 6/6 ~0%, host_plausible true, coverage
  100%); `host_saturated=true` (98.5–98.7%, expected at concurrency=4 > 2 cores) means latency/QoS
  from this pair is NOT citable, same discipline as the c=8 sensitivity rows.
  - **Reproduced in an independent second session, same day.** Full teardown/redeploy/re-pin
    (Fn's function image tag even changed, 0.0.10→0.0.11, on rebuild — irrelevant, since
    `pin_cpuset.sh` pins by "every running container," not by image tag): session 2 gave Fn 14.08,
    OpenFaaS 7.17, gap 6.91 pp. Across the two independent sessions: Fn 13.91/14.08 (median 14.00),
    OpenFaaS 6.82/7.17 (median 7.00), **gap 7.09/6.91 pp (CV 1.3% across sessions)** — tight
    agreement, not a one-off. This is the number to cite in the paper for the 2-core row.
    Results: `results/{fn,openfaas}_cpubound_2core` (session 1) and
    `results/{fn,openfaas}_cpubound_2core_session2` (session 2).
  - **Correction to the mechanism — it is NOT symmetric.** The retracted hypothesis said "both
    platforms' shares are ~2.3× higher on the 2-vCPU VM." The controlled data says otherwise: Fn's
    share rose sharply (10.46 → 13.91, +33%) while OpenFaaS's share was flat-to-lower (7.67 →
    6.82, −11%). So core-count scarcity inflates Fn's control-plane overhead specifically; it does
    not inflate OpenFaaS's proportionally. **[CANDID for the paper]** the mechanism (fnserver
    scheduling contention under thread starvation vs of-watchdog's per-replica model) is not yet
    explained, only observed — do not assert a cause beyond "asymmetric, platform-specific
    core-scarcity sensitivity" without a follow-up micro-benchmark.
  - **Energy/carbon NOT citable from this experiment.** `rapl_validation_err_pct` ran **43–60%**
    on both platforms at 2 pinned cores — the same magnitude of error the box had under the OLD,
    wrong `idle_w=30` calibration, despite using the correct `SAQEF_IDLE_W=4.3` here. Likely cause:
    the 4.3 W idle baseline was calibrated with all 8 cores idle; under 2-core pinning the 2 active
    cores may run at different turbo/frequency behavior than the flat `busy_core_w` model assumes,
    and/or the 6 un-pinned-but-present idle cores' contribution to package power no longer matches
    the whole-box idle baseline. `cp_dynamic_share_pct` is unaffected (pure cgroup CPU-time ratio,
    no energy model involved) and remains the citable number; do not report absolute J/gCO2 from
    the `*_2core` result sets without re-deriving idle_w for the pinned configuration specifically.
  - **The 8/2-core mechanism fix, if reusing this method:** `cpu_count()` and `host_cpu_ticks()`
    both default to whole-machine values from `/proc/cpuinfo`/`/proc/stat`. `--cpu-count-override`
    (`SAQEF_CPU_COUNT_OVERRIDE`) alone is NOT sufficient — the first pass at this experiment used
    only that knob and got `host_sat%` of 111–121% (impossible >105%, correctly caught by the
    `host_plausible` gate) because `host_cpu_ticks()` was still summing the aggregate `/proc/stat`
    line across all 8 cores, including background activity (dockerd, kworkers, this very shell) on
    the 6 un-pinned cores. Fix: also set `--host-cpu-list`/`SAQEF_HOST_CPU_LIST` (e.g. `0,1`) so
    the numerator sums only the pinned cores' own `cpuN` lines. Both knobs now exist in
    `saqef_harness.py`/`run_saqef.sh`/`run_openfaas.sh`; the first (invalid) OpenFaaS attempt is
    preserved at `results/openfaas_cpubound_2core_hostmetric_invalid` (its `cp_dynamic_share_pct`
    was fine — only host_sat/host_plausible were wrong — but don't cite its gates table).
- QoS now citable for the first time (host_sat <85%, from the c=4 bare-metal run): Fn p50 6.5 /
  p99 8.9 ms @ 597 rps; OpenFaaS p50 7.2 / p99 12.1 ms @ 532 rps; SLO compliance 1.0 both.
- Codespace numbers (Fn 23.59–24.59 vs OpenFaaS 15.82, gap ~8 pp) remain the saturated-regime,
  flawed-instrument dataset — no longer needed to establish the core-count effect (the 2-core
  bare-metal result above does that on a clean instrument) but still usable as the original
  "regime differs" observation. The expert-review Findings A/B resolutions (GIL concurrency
  parity) still stand independently.

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

## Core-restricted variant (emulate an N-vCPU box on this bare-metal machine)
Confirms whether an effect is core-count-driven by holding the instrument fixed and varying only
core count — used 2026-08-06 to confirm the CPU-count effect (see Current state). Steps, for N=2
cores (repeat per-platform, OpenFaaS first, same teardown discipline as above):
1. Deploy + scale normally (`SAQEF_REPLICAS=2×N`), then start the pin daemon:
   `sudo bash pin_cpuset.sh 0,1 &` (pick N adjacent **physical** cores via `lscpu -e` — CORE
   column, not just CPU id, or you pin to two hyperthreads of one physical core by accident).
2. Run the bench wrapped in `taskset`, with BOTH override knobs set so the harness's own
   saturation ceiling and busy-tick numerator agree with the pin (see pitfall below — one knob
   alone silently mis-measures host saturation):
   `sudo env SAQEF_CPU_COUNT_OVERRIDE=N SAQEF_HOST_CPU_LIST=0,1 SAQEF_IDLE_W=<W>
    SAQEF_CONCURRENCY=4 SAQEF_TOTAL=10000 SAQEF_REPEAT=5 SAQEF_OUT=results/<name>_Ncore
    taskset -c 0,1 bash run_openfaas.sh bench` (or `run_saqef.sh bench`).
3. Kill the pin daemon (`kill` the backgrounded PID) once both platforms' bench runs are done.
4. `SAQEF_OUT=results/<name>_Ncore bash run_openfaas.sh gates` (gates ignores `SAQEF_CPU_COUNT_OVERRIDE`/
   `SAQEF_HOST_CPU_LIST` — they're baked into the already-written summary.json, not re-read).
5. Energy/carbon from a pinned run are NOT citable without re-deriving idle-w for that specific
   pin (see Current state caveat) — `cp_dynamic_share_pct` (cgroup CPU-time ratio) is unaffected
   and is the only citable number.

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
- `pin_cpuset.sh` — live cpuset-pinning daemon for the core-restricted variant (see that section).
