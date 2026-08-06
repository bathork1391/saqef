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
- **Paper/figure cleanup (2026-08-07, post-knative):** the paper and figures were refocused
  **bare-metal-only** per user direction. Codespace results (the 2-vCPU shared-VM origin
  instrument) were REMOVED from all results sections — RQ3, the §5.5 cross-regime table, §7
  threats, §10, §11, and Appendix B's codespace columns — and remain only as a one-paragraph
  origin story in §4.2 (hardware table: "origin instrument, no results cited"). Figures were
  redesigned from two-panel to **four single-story figures with NO dates on axes** (provenance
  in paper captions), legends at the bottom, OW handled on shared axes with labels:
  figure1_core_count (Fn-vs-OF core-count), figure2_four_platform_scatter, figure3_attribution_split,
  figure4_cp_cost_per_inv. §5.1–5.4 re-anchored to the bare-metal Fn run (was codespace-era).
  §5.4 carbon values hand-recomputed with the fixed kWh formula (summary.json files from
  2026-08-05 predate the 2026-08-06 carbon fix and still carry the 1000× bug — do not read
  carbon from them directly). G6 gate row updated to bare-metal cross-session numbers.
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
  (Drift is a BOUNDED warm-up/settling transient, not accumulation: fnserver RSS flat 34→33 MB
  across runs 2–5, CPU% plateaus then dips at run_5 — §31.9.)
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
    OpenFaaS 6.82/7.17 (median 7.00), **gap 7.09/6.91 pp across sessions** — reproduced to within
    0.2 pp. Note: BOTH sessions ran the full REPEAT=5 protocol with per-run gate tables (delta 6/6,
    host_plausible, coverage%) — the n=2 is the count of independent SESSIONS, not runs; each
    session already matches the reproducibility standard applied to every other citable number.
    Do NOT write "CV across sessions" (n=2 is not a distribution). This is the number to cite in
    the paper for the 2-core row.
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
  - **Expert-review disposition (2026-08-06; full review recorded in report §31.7).** (a) The
    core-count effect — 2.79 → 7.0 pp, same instrument, both sessions at full REPEAT=5 with per-run
    gates — is judged an **earned, publishable result**, and the machine-dependence of the 5 pp
    gate is a methodological contribution on its own. (b) **Frequency parity — VERIFIED
    (2026-08-06 evening; §31.8, evidence in `results/freqcheck_evidence/`).** Direct
    `scaling_cur_freq` sampling during a short pinned 2-core run vs a c=4 run: loaded cores 0,1 sit
    at **3.60/3.60 GHz (identical — no per-core DVFS asymmetry)** vs **3.30 GHz** on the c=4 loaded
    cores. The reviewer's turbo-headroom mechanism is REAL (pinned cores ~+9% higher; this is the
    measured cause of the 43–60% RAPL error, plus the un-pinned idle cores downclocking to 400
    MHz), but it does NOT confound the share: Linux cgroup CPU-time accrues via sched_clock /
    invariant TSC — wall-time-on-core, not cycles — so a ratio of CPU-times is frequency-invariant
    by construction, and with cores 0/1 at identical clocks there is no CP-on-faster-core channel
    either. **Original 2-core results stand as final.** (c) Mechanism stays observed-not-explained;
    concrete future work: `perf stat -e context-switches,migrations` on the fnserver process (or
    /proc/<pid>/status voluntary_ctxt_switches) at 2 vs 8 cores.
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
- **OpenWhisk adapter PROVEN end-to-end (2026-08-06 evening; commit after saqef-v2.1).**
  `platforms/openwhisk.py` + `OW_FUNCTION/hello.py` pass deploy/verify/bench/gates and 26/26 tests.
  Deployment = the Apache `openwhisk/standalone:nightly` image: the whole control plane is ONE
  container (`cp=openwhisk`); the invoker spawns per-activation action containers from
  `action-python-v3.11` (`fn_images`). Isolation: any swarm service or `fnserver` forbidden
  (data-driven contract). Standalone's two `wsk0_*_prewarm_nodejs20` containers are invoker
  warmup (excluded from fn_images, idle ~0%, land in unclassified fail-open).
  - **Frozen-GET constraint:** the measurement harness is GET-only, OpenWhisk's native invoke is
    POST → the action is a WEB ACTION: `GET http://127.0.0.1:3233/api/v1/web/guest/default/hello`
    → HTTP 204 (blocking). Verify 100/100, `function_cpu_ms_per_inv=5.5` (budget MATCHES).
  - **Rate-limit fix (expensive to learn):** the standalone defaults to **60 invokes/minute per
    action** (`standalone.conf: limits-actions-invokes-perMinute=60`) → ~40% HTTP 429 at
    concurrency 4, which silently corrupts verify AND bench availability. The key is enforced by
    WhiskConfig from `whisk-config.limits.actions.invokes.perMinute` (the `whisk-config.`-prefix
    dotted path — standalone.conf's kebab keys under `whisk.config` are a separate UNREAD path).
    Override via `JVM_EXTRA_ARGS` (`/init` passes it into `java`): raised per-minute + concurrent
    + trigger-fire limits to 1e9/1000. After the fix: 300/300 → 204.
  - **Static docker CLI shadow-mount:** the standalone ships a 2018 docker client (API 1.38) that
    host dockerd ≥29 rejects; `deploy()` mounts a modern STATIC CLI at `/usr/bin/docker`
    (`vendor/docker`, gitignored, fetched from download.docker.com, cleaned up after extraction).
  - **Proof bench** (quick, c=4, repeat=2, total=2000): `cp_dynamic_share_pct` **87.5 / 81.1**
    (run_1/run_2), delta 0.0%, CPmapped 1/1, coverage 100%, availability 1.0, unclassified 0.0.
    OpenWhisk's CP uses ~4–5× the function CPU steady-state (per-activation `docker logs` in the
    standalone's DockerCliLogStoreProvider) → a real, defensible contrast vs Fn ~11.6 / OF ~7.7
    shares. NOT yet a citable paper number: needs a full REPEAT=5 run + gates before citation, and
    decide in the paper how much of the standalone's log-store overhead is "control plane" vs
    deployment-mode artifact.
  - **Full REPEAT=5 run (2026-08-07, `results/openwhisk_cpubound_baremetal`):** `cp_dynamic_share_pct`
    **82.54** (median; runs 2–5 flat 82.0–82.6, CV 2.1%, IQR 0.26; run_1 transient 86.24), slo_compliance
    1.0, availability 1.0, fn_cpu_s rock-solid 57.3–57.5 s, all gates green (delta ~0, CPmapped 1/1,
    coverage 100%, host_plausible true, host_saturated false ~64–71%). The standalone's CP burns
    262–365 CPU-s/run vs ~57 s function CPU — **~4.6× the function CPU, i.e. share 82.5 vs Fn 12.3 /
    OF 7.5**. OpenWhisk is the "heavy control-plane" pole of the study. **Caveats:** (a) box was
    NOT quiet (opencode agent at ~2.8 cores — see TROUBLESHOOTING_RUNBOOK.md §1): the share is
    contention-robust and flat across runs even as host load rose, but a quiet-box rerun is the
    clean provenance for citation; (b) `rapl_validation_err_pct` 36.3% — the standalone JVM's power
    draw does not fit the linear busy-core energy model, so OW **energy/carbon is NOT citable**
    (share is, being a pure cgroup CPU-time ratio); (c) throughput declined across runs (47→25 rps)
    as host load accumulated — QoS percentiles from this session are agent-contaminated, rerun for
    latency. Per-invocation CP cost ≈ 262 CPU-s / 10000 inv ≈ **26 ms CPU per invocation** (vs
    fnserver 0.79 ms / of-watchdog 0.56 ms) — the standalone's `docker logs` log-store and JVM
    orchestration dominate. Deployment-mode decision for the paper: how much is "control plane" vs
    standalone-emulator artifact (same § as the OW adapter notes).
  - `.gitignore` had CRLF line endings (broke the `vendor/docker` pattern); rewritten LF +
    `vendor/` + `results/*_quick*/` guard. `results/verify.json` is a tracked working artifact —
    revert before committing (OpenWhisk verify overwrote it — now fixed: verify outdir defaults to
    `results/<platform>_verify`, so cross-platform verify can no longer collide).
- **Knative adapter DONE + full REPEAT=5 run (2026-08-07, `results/knative_cpubound_baremetal`).**
  `platforms/knative.py` + `KNATIVE_FUNCTION/` (the identical ~5 ms spin handler) deploy/verify/
  bench/gates clean; 38/38 tests (adapter schema now includes knative; byte-identical argv tests
  for bench + verify). Deployment = Knative Serving v1.23 + Kourier on k3s v1.36.3 (docker
  runtime, so every pod is a docker container the harness can classify), function = the hello
  Service with **static scaling** minScale=maxScale=16, containerConcurrency 4 (GIL parity).
  - **Result: median `cp_dynamic_share_pct` = 13.99 (13.22–14.24, CV ~2.8%)** — statistically
    tied with Fn's 8-core value (11.6–12.9 box-drift range, today's crosschecks 12.27–12.92) and
    ~1.9× OpenFaaS (7.53). So the ordering on this box is now OF 7.5 < Fn ≈ Knative 12–14 <
    OpenWhisk 82.5. All gates pass on the citable share (delta ~0, CPmapped 15–17/15–17 ok,
    coverage 100%, host_plausible true); `fn_replicas` shows 32 (16 user-container + 16
    queue-proxy — the `_count_fn_containers` gate display now matches fn_containers by name for
    platforms whose function image shows only a bare digest).
  - **Attribution map (must be documented in the paper, asymmetric vs OF):** Knative's *fn*
    bucket includes the per-replica **queue-proxy sidecar** (on the request path, same honesty
    caveat OF solves by putting of-watchdog inside the function cgroup) AND the function; its *CP*
    bucket includes **kourier-gateway + svclb-kourier** (the data-plane gateway) + activator +
    controller/autoscaler/webhook/net-kourier-controller. OpenFaaS counts its proxy in fn, so the
    true OF-vs-Kn control-plane gap is even smaller than the raw 7.5-vs-14 share suggests. Per-
    invocation CP cost ≈ 10.5–11.4 CPU-s / 10000 inv ≈ **~1.1 ms CPU per invocation** (fnserver
    0.79 / of-watchdog 0.56 / OW 26).
  - **k3s embedded control plane is measurement-invisible:** apiserver/etcd/scheduler/
    controller-manager run INSIDE the `k3s server` process, not as docker containers → their CPU
    lands in `host_cpu_sec` residual, NOT in the CP bucket. Document as a boundary (a production
    cluster would bill it as shared infra); the container-visible CP (knative-serving + kourier)
    is what the share measures.
  - **Idle baseline finding:** calibrated idle package watts with the knative stack up (60 s RAPL,
    zero traffic) = **11.14 W** vs 4.3 W bare — the k8s-native platform carries a ~7 W always-on
    baseline (16 warm replicas + 32 proxies + knative-serving + kourier). This is itself a citable
    sustainability observation, but it is why `rapl_validation_err_pct` is 22.6–32.2% (the linear
    busy-core energy model does not fit a stack whose idle draw already exceeds the flat model) →
    knative **energy/carbon NOT citable** (share is).
  - **QoS NOT citable from this session:** host_sat 84.3–87.2% (3/5 runs ≥85, flagged) — box not
    quiet (opencode agent ~2.8 cores). slo_compliance 1.0, p50 8.3–8.7 / p99 13.8–14.8 ms @
    431–452 rps, but those percentiles are agent-contaminated; quiet-box rerun before citing
    latency (TROUBLESHOOTING_RUNBOOK.md §1 + §10).
  - Run knative (bench only) with: `python3 saqef run --platform knative --total 10000
    --concurrency 4 --duration 300 --warmup 20 --repeat 5 --idle-w <calibrated> --out
    results/<name>`; deploy/verify first. Calibrate `--idle-w` with the stack up — it is ~2.6× the
    bare-box value.
- **External-review findings fixed (2026-08-06; committed 2026-08-07 as `57f8a6f`).**
  - **🔴 Carbon ×1000 unit bug — FIXED in `saqef_harness.py`.** `carbon_gCO2` used `J/3600` (Wh)
    with `CI` in gCO2/kWh → every gCO₂ figure was 1000× too high. Formula now `J/3.6e6` (kWh).
    `energy_J`, `cp_dynamic_share_pct` (unit-free), `rapl_validation_err_pct` NEVER affected. Paper
    values corrected (§5.4 145 µg → 0.145 µg; 7.5 mg → 7.5 µg; v9.3-era 39.6 mg → ≈39.6 µg).
  - **🟠 Isolation guard is now data-driven.** `assert_platform_isolation()` in the harness consumes
    `--forbidden-services`/`--forbidden-containers` argv (OpenWhisk was silently falling through
    the old hardcoded fn/openfaas elif to `(True,"")`). Adapter `isolation` fields now enforced at
    measurement time on all three platforms.
  - **🟡 `cmd_verify` default outdir** → `results/<platform>_verify` (was shared `results/`).
  - **🟡 `saqef` deploy comment** corrected (OpenFaaS = `docker service create`, not faas-cli).
  - **🟡 `run_openfaas.sh` replica default 4 → 16** (protocol is 16 static replicas).
  - **Replica audit (reviewer's "10" was a misread):** OpenFaaS runs are protocol-clean — baremetal
    and regression = 16 replicas (16/16/16/16/16 across runs 1–5), 2-core = 4 (2×N protocol),
    legacy codespace = 4/1 (pre-citable). "10" was Fn's *dynamic function-container count*.
  - **⚠️ `saqef_harness.py` NOW DIVERGES from `saqef-v2.0-frozen`** (carbon fix + isolation port).
    Fixed + committed 2026-08-07 (`57f8a6f`). **Regression rerun executed live 2026-08-07** (c=4,
    10000, REPEAT=5, idle_w=4.3): OpenFaaS **7.53 PASS** (ref 7.67, dev 0.14 pp); Fn **12.27 vs ref
    11.60 → dev 0.67 pp > 0.5 pp FAIL**, but proven box drift by same-day old-runner A/B
    (`results/fn_cpubound_crosscheck2` = **12.92**) — the refactor is faithful (see
    TROUBLESHOOTING_RUNBOOK.md §2/§3). The 11.60 reference is stale for this box; recalibrate it
    from a quiet-box old-runner A/B before trusting the Fn gate (OF's 7.67 stays). Measurement-path
    bytes beyond the two edits are untouched.

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
  taint the Fn run — always `docker service rm hello` before Fn. **Enforced since 2026-08-06
  (§31.9):** `saqef_harness.py` now asserts platform isolation at the top of every bench run —
  Fn sessions fail loud if ANY swarm service is up, OpenFaaS sessions fail loud if `fnserver` is
  up. The rule is a precondition check, not a measurement-path change; no citable run invalidated.
- **Calibrate `SAQEF_IDLE_W`** to the machine's measured idle package watts (60 s RAPL read,
  stack up, zero traffic) or `rapl_validation_err_pct` stays ~45% on bare metal.
- Codespace git push needs device-flow `gh auth login` after `unset GITHUB_TOKEN`.

## Durable knowledge (read these for depth)
- `SAQEF_TECHNICAL_REPORT.md` — full session log, numbered sections (24–29 = OpenFaaS saga).
- `SAQEF_PAPER_DRAFT.md` — paper structure + corrections log (v9.3…v9.11, methodology fixes).
- `OPENFAAS_SETUP.md` — OpenFaaS deployment + protocol steps (incl. §6 Step 1.5 concurrency parity).
- `pin_cpuset.sh` — live cpuset-pinning daemon for the core-restricted variant (see that section).

## Next-session plan (agreed 2026-08-06, user + external expert greenlighted)
**Sequencing — do in this order:**
1. ~~**Figures from committed data**~~ — **DONE (2026-08-07), redesigned (2026-08-07).**
   `figures/make_figures.py` (data-driven, matplotlib, harness stays stdlib-only): **four
   single-story figures, no dates on axes, legends at the bottom** (provenance lives in the
   paper captions, not axis labels):
   - figure1_core_count — the Fn-vs-OF core-count effect (8-core quiet vs 2-core pinned, same
     instrument), 5 pp gate line + gap arrows (2.79 → 7.00 pp).
   - figure2_four_platform_scatter — per-run shares, all four platforms, 8-core same-day
     (OF 7.53 < Fn 12.27 [11.52–12.96] ≈ Knative 13.99 < OW 82.54); every per-run value shown
     (n=5/session, Fn 3 sessions = 15 points), black tick = reported median.
   - figure3_attribution_split — CP / fn / unclassified CPU-time, four platforms.
   - figure4_cp_cost_per_inv — control-plane CPU per invocation (of-watchdog 0.56, fnserver
     0.79, Kn ~1.1, OW ~27 ms).
   Adding a platform/regime = one dict entry in REGIMES + rerun.
   Regenerate: `python3 figures/make_figures.py`.
2. **Adapter refactor** (fn + openfaas) WITH an automated **regression gate** before the new
   plumbing is trusted: `saqef regression` reruns c=4 8-core both platforms and FAILS if median
   `cp_dynamic_share_pct` deviates > ~0.5 pp from known-good **11.60 / 7.67**. Do NOT trust by code
   review alone; prove by rerun.
   - **fn reference recalibrated 2026-08-06 (was 10.46).** The refactored CLI's first full
     `saqef regression` passed OpenFaaS (7.55, dev 0.12 pp) but "failed" Fn at 11.96. A same-day
     A/B with the OLD runner (`run_saqef.sh all`, fresh setup, results/fn_cpubound_crosscheck)
     measured **11.60** — same flat plateau, so the drift is the box, not the refactor (argv is
     byte-identical). Cause: fnserver per-request CP cost 0.61→0.75 ms vs the 2026-08-05 runs
     (which ramped 9.8→11.7). fn_cpu_s identical (~56.7 s) both days. Full context in
     `metrics/cpubound.json` → `regression.reference_notes`. The gate is day-sensitive for Fn;
     it catches refactor-scale breaks (100%/0-success bugs), not ~0.5 pp box noise.
3. **OpenWhisk adapter — DONE (proven in isolation on 2026-08-06 after the framework passed
   regression; see Current state).** If reused for a citable number: full REPEAT=5 run + gates, and
   decide how much of the standalone's per-activation log-store overhead is "control plane".

**Architecture decisions (agreed):**
- Adapter-per-platform (`platforms/<name>.py`), metric-as-config (JSON recipes under `metrics/`,
  NOT scripts — metrics are modes of the existing harness), single CLI entrypoint:
  `saqef run --platform X --metric Y --iterations N --total ... --concurrency ...`.
- Adapter protocol must allow **bespoke sampling/hooks**, not just different flag values — a future
  platform may not express its control plane as container names (systemd service, non-container CP,
  dynamic function count). Design the adapter interface for that now.
- **`assert_platform_isolation` becomes data-driven — THE point of the refactor.** Each adapter
  declares `expected_containers` / `forbidden_containers` / `forbidden_services`; the guard
  consumes them. Today it is hardcoded elif for fn/openfaas (`saqef_harness.py:747`) — the exact
  copy-paste-drift surface that caused the original taint bug.
- Do NOT let the future-GUI idea shape the design. Build for 3 platforms, not imagined N. A clean
  scriptable CLI is good practice regardless; if a real GUI requirement appears, its shape will be
  known then. (GUI would only ever call the same `saqef` CLI.)
- **Harness measurement path stays byte-identical** during refactor; adapters are thin config +
  deploy/teardown orchestration. Old `run_saqef.sh`/`run_openfaas.sh` stay working until the new
  path passes regression — no capability loss.

**"Do not regress" manifest — each previously-fixed bug must still be enforced after refactor:**
1. hello-allowlist overlap → isolation guard (data-driven, mandatory per-adapter fields).
2. `docker service rm hello` ordering / OpenFaaS-leftover taint.
3. GIL concurrency parity: static replicas, never single-replica.
4. `SAQEF_IDLE_W` calibrated per platform (idle-wats, not the 30 W default).
5. host-window alignment (v9.10/v9.11) + `host_plausible` gate.
6. delta-check 6/6 `ok`, delta% ~0; coverage ≤100%; `unclassified_cpu_s` informative (fail-open).
7. Count-bound runs (TOTAL), `--duration` only a safety cap.
Make each of these a *mandatory* field/assertion in the adapter contract so a fixed bug cannot
silently vanish. Existing pitfalls list above remains authoritative.

## Handoff log — user out (2026-08-07 evening; box NOT quiet — an agent was burning ~2.8 cores)

**Do NOT start any benchmark while an agent is running.** This list is the accepted plan for the
next human/quiet session. Order matters; the two reruns below are the only new measurements the
paper still needs.

### Confidence tiering (honest, agreed 2026-08-07)
- **Defensible as-is (measurement-path + multiple sessions):** OpenFaaS 8-core share ~7.5–7.7%
  (three independent measurements within ~0.15 pp); the **2-core pinned gap ≈ 7.0 pp** (Fn 14.00
  vs OF 7.00, two independent sessions, frequency-parity verified, invariant-TSC ratio → DVFS-safe);
  Fn-vs-OF 8-core direction (Fn higher, magnitude ~2.8 pp, below the 5 pp gate); the fixed carbon
  values (0.145 µg/inv etc., hand-recomputed with J/3.6e6); OF per-inv CP 0.56 ms / Fn 0.66–0.79 ms.
- **Fishy — needs one quiet rerun each before citation:** OpenWhisk 82.54 and Knative 13.99
  (single sessions, box contaminated, attribution caveats). **OW is the highest risk**: how much of
  82.5% is "control plane" vs standalone-emulator `docker logs` artifact? Kn's queue-proxy-in-fn /
  kourier-in-CP asymmetry + invisible k3s apiserver/etcd are what an expert will probe.
- **NOT citable by design:** energy/carbon from 2-core, OW, Kn runs (RAPL error 22–60% — linear
  busy-core model doesn't fit JVM/always-on-k8s stacks); QoS from OW/Kn (agent-contaminated). Only
  Fn/OF quiet QoS (p50 6.5 vs 7.2 ms) is citable.
- **Biggest scientific risk to pre-empt:** the 8-core result *failed* the 5 pp gate; the 2-core
  rescue came after. Framed honestly as regime-dependence, but the paper must lead with the
  controlled same-instrument defense (and the ~25% Fn box drift 10.46→12.92 needs a stated cause or
  an explicit box-state caveat, not a footnote).

### 1. Quiet-box rerun runbook for OW + Kn (do FIRST, before any other measurement)
Both need a box quiet of agents/this shell, `SAQEF_REPEAT=5`, fresh deploy, and idle-w calibrated
per platform with the stack up (OW 5.29 W, Kn 11.14 W — measured with stack up, zero traffic).
- OpenWhisk: full `saqef run --platform openwhisk ... --idle-w 5.29` (deploy/verify/bench/gates).
  Kill/never-run the previous standalones (host_load was rising 47→25 rps). `results/verify.json`
  is a tracked artifact — revert before commit.
- Knative: `saqef run --platform knative ... --idle-w 11.14`. Expect share ≈ 13–14 (confirm),
  QoS now citable (was p50 8.3–8.7/p99 13.8–14.8 @ ~440 rps, agent-contaminated), energy STILL NOT
  citable even quiet (idle 11.14 W breaks the linear model — rapl err 22–32% is structural).
- After each: `gates` must show host_saturated=false, delta ~0, coverage 100%, CPmapped ok.
- Outcome for the paper: OW energy/QoS either become citable (quiet) or get a permanent
  "deployment-mode artifact" qualification; Kn energy/QoS get their clean provenance.

### 2. Fn-drift investigation (mechanism; share values themselves are fine)
The 11.60-vs-12.92 range is a *box-state* drift (RSS flat, fnserver per-request CP 0.61→0.75 ms),
observed-not-explained. Concrete plan: `perf stat -e context-switches,migrations` on the fnserver
process (or `/proc/<pid>/status` voluntary_ctxt_switches) at 8 cores vs 2 pinned cores, plus the
recalibration A/B: rerun OLD runner `run_saqef.sh all` on a quiet box same-day to re-anchor the
regression reference (AGENTS.md §"fn reference recalibrated" — do NOT trust 11.60 or 12.92 as a
constant; pick the fresh quiet-box value and note it is day-sensitive). The regression gate's job
is to catch refactor-scale breaks, not ~0.5 pp noise.

### 3. Codespace disposition — DECISION (user asked 2026-08-07; agreed)
- **Remove ALL codespace results/findings from tables/figures.** Already done 2026-08-07 — the
  only remaining mentions are (a) the §4.2 hardware-table origin row ("origin instrument, no
  results cited") and (b) the §7.4 contention-contamination lesson (what a saturated co-tenanted
  box does to QoS) — both narrative, zero codespace numbers.
- **The ONE thing worth keeping is the story, not the numbers:** the origin instrument's
  saturated-regime *direction* (gap larger under core scarcity) was later confirmed by the clean
  2-core pinned experiment — worth one sentence in §4.2 as narrative arc ("the direction observed
  on the origin instrument was confirmed on a clean instrument"), NEVER with codespace figures.
  The flawed-instrument lesson (co-tenancy + no RAPL + saturation ⇒ reproducible-but-worthless
  QoS) is a methodology contribution for §7, not a result.
- **Do not re-add codespace columns** to any table, and do not cite Fn 23.59–24.59 / OF 15.82 / gap
  ~8 pp anywhere. The clean 2-core result supersedes them on a defensible instrument.

### 4. Methodology rewrite — IN PROGRESS (2026-08-07, partially applied, uncommitted)
Session applied: §4.1 design overview (clean two-scope framing, removed stale 1.9%), §4.2 kept
origin table + hardware-dependence note, §4.3 workload (removed N≥10 contradiction; added the
"count-bound + fresh CP" rationale), §4.4 instrumentation (stripped v9.x fix-chatter, kept the
design rationales), §4.5 model (removed historical-bug comment + "pending" RAPL + stale 30 W;
calibrated idle-w now per platform), §4.6 (N=5 + session medians + GIL parity), §4.7 gates table
(stripped v9.x markers). **REMAINING:** §5.5/§5.6 numbers vs prose pass; a "how to read this
table" note for §5.1–5.4 QoS/attribution tables and Appendix A/B self-sufficiency pass; verify no
codespace-era protocol numbers anywhere in §4–§5 (grep clean as of this session: only the §4.2
origin row). Then update this file's "Current state" block, commit, push.
