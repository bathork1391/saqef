# The Hidden Cost of Orchestration: A Sustainability-Aware QoS Evaluation Framework (SAQEF)

**Working title — Paper Draft (v0.1)**

**Author:** [Name], Green Cloud Continuum project
**Date:** 2026-08-08
**Status:** Methodology validated on Fn + OpenFaaS and extended to OpenWhisk + Knative, all
RAPL-validated on bare metal (4 platforms, 8-core box + controlled 2-core regime). Draft for
refinement into the final research paper.
**Source of record:** `SAQEF_TECHNICAL_REPORT.md` (full measurement log) + `saqef_harness.py` (instrumentation) + `run_saqef.sh` (one-command reproduction).

> **How to use this document.** This is the consolidated methodology + results narrative, written in paper structure. Every claim is traceable to the technical report and the harness. Sections marked **[CANDID]** are honest gaps that must be closed before submission; they are intentional, not omissions. As experiments expand (OpenFaaS, OpenWhisk, bare metal), fill in the marked placeholders and keep this document as the single narrative.

---

## Abstract

Serverless computing shifts infrastructure management to platform operators, but the *orchestration overhead* — the control-plane work that schedules, freezes, and coordinates function invocations — is rarely charged to the function. This paper presents **SAQEF**, a sustainability-aware QoS evaluation framework that attributes CPU time and energy to the control plane versus the function under controlled load, cross-validated against direct kernel counters and, on bare metal, against RAPL (steady-state error 4–8%). We apply it to two platforms (Fn and OpenFaaS) serving an identical CPU-bound 5 ms function, then extend the comparison to four platforms (adding Knative and the OpenWhisk standalone). The headline finding is that the control plane's share of dynamic CPU — and therefore the Fn-vs-OpenFaaS gap — is a property of the machine's core count, not of the platform alone. On an 8-core box, Fn's control plane consumes **10.5% of dynamic CPU** vs OpenFaaS's **7.7%** (gap 2.8 pp, below our 5 pp discrimination gate). Cpuset-pinning the same box to 2 cores — same protocol, same instrument, all validation gates green, reproduced in two independent sessions — raises Fn to **14.0%** while OpenFaaS stays at **7.0%** (gap 7.1 pp, reproduced to within 0.2 pp across two independent sessions): core scarcity inflates Fn's control-plane overhead specifically, an asymmetric, platform-specific sensitivity. Across four platforms, measured across three independent sessions of varying box-state (contaminated, quiet, and a fully-gated same-day rerun after fixing a real control-plane classification bug), the container-visible share spans an order of magnitude and holds its ordering throughout — OpenFaaS ~7.1–7.6 < Fn ≈ Knative ~10.5–12.4 < OpenWhisk ~80–82 (attribution conventions differ; §5.6 — under the consistent co-located-proxy-in-fn convention all three co-locate their request-path proxy in fn; the Fn–Kn tie is convention-sensitive and Kn spans 12–26% if the queue-proxy sidecar is counted as control plane, which does not move OW an inch). OpenWhisk's share stayed within a ~2 pp band across all three sessions (82.5 → 80.2 → 82.4), evidence that the "OpenWhisk is control-plane-heavy" finding is a structural property of the platform, not a measurement artifact; Knative moved more between sessions (14.0 → 11.4 → 12.4), a reminder that day-to-day box variance is real even on a nominally quiet box. **The 5 pp gate is a per-machine-pair quantity, not a platform constant**; the machine-dependence and its asymmetry are the central contributions. The framework's built-in delta-check also caught and eliminated a 52× sampling overcount during development, demonstrating that the validation approach works as intended.

---

## 1. Introduction & Motivation

Serverless (FaaS) platforms promise "scale to zero," pay-per-invocation pricing, and operational simplicity. Their environmental cost, however, is hidden in two places: (i) the **idle baseline** of always-on gateways, schedulers, and coordinators, and (ii) the **dynamic overhead** of orchestrating each invocation — route lookups, container spawn/freeze churn, queueing, and watchers. When operators report "the control plane uses ~2% of CPU," they are describing the *static* fraction of total machine capacity — which, on an idle-leaning, co-tenanted VM, obscures the fact that the marginal work attributable to a function is disproportionately orchestration.

**Central claim:** for a light CPU-bound function, the control plane is not a rounding error — on an 8-core box it is ~10% of the marginal (dynamic) CPU cost for Fn and ~8% for OpenFaaS, and its share — and the platform gap — grows as the machine gets smaller (Fn 14.0% vs OpenFaaS 7.0% when the same box is pinned to 2 cores; see §5.5). Orchestration is a first-class, capacity-dependent cost, not an overhead line item.

**Contributions:**
1. A reproducible, platform-agnostic measurement harness (`saqef_harness.py`) that attributes CPU/energy between control plane and function under controlled load, with built-in self-validation (delta-check, host-plausibility, coverage, platform-isolation assertions).
2. A workload-anchored methodology that fixes the "ratio of tiny quantities" instability that plagues no-op-workload energy comparisons, plus a frequency-invariance argument: cgroup CPU-time ratios are invariant-TSC wall-time, so the headline share is robust to per-core frequency/turbo differences by construction (§5.5 caveat, report §31.8).
3. The first RAPL-validated cross-platform control-plane overhead numbers on bare metal, with the core-count dependence **quantified by a controlled same-instrument experiment on a single physical host** (8-core i5-1145G7, core count varied by cpuset restriction rather than a second machine): Fn 10.5% vs OpenFaaS 7.7% of dynamic CPU at 8 cores (gap +2.8 pp, below the 5 pp gate), rising to Fn 14.0% vs OpenFaaS 7.0% (gap +7.0 pp, reproduced to 0.2 pp in two independent sessions) when the same box is cpuset-pinned to 2 cores — an **asymmetric, platform-specific core-scarcity sensitivity** (Fn's share inflates, OpenFaaS's stays flat). The core-restriction design keeps the instrument (CPU model, DVFS, NUMA, thermal envelope) fixed and isolates the core-count variable, but a genuinely distinct second physical machine remains future work and would bound the generalizability (T5V #8).
4. A documented case study of the validation method catching a real 52× instrumentation bug (§8).
5. For sustainable serverless computing: the finding that **platform overhead shares and the gap between platforms are machine-pair properties, not platform constants** — the widely used 5 pp discrimination threshold fails/passes depending on the host's core count, so carbon-aware scheduling and "green function" claims must be evaluated per machine-pair with a citable per-machine-pair gate, not a global constant (§5.5, §7).

---

## 2. Background & Related Work

- **FaasMeter** (Fan et al., IC2E 2024) — marginal-energy methodology for FaaS using direct energy measurements; the inspiration for our `--delta-check` and `--idle-probe` self-validation pattern.
- **Kepler / eBPF** — CPU-time-proportional energy estimation using kernel counters; the model family our CPU-time attribution follows.
- **Caribou** (SOSP 2024) — fine-grained multicore energy; source of the per-core power constant (3.5 W/busy core, dynamic portion) used in our model.
- **Serverless overhead studies** (Wang et al. "Peeking Behind the Curtains of Serverless Platforms"; Shahrad et al. on idle resources) — document that orchestration and cold starts consume significant resources; we contribute *measured marginal energy* for the Fn control plane specifically.
- **DockerStats / cgroup v2** — per-container CPU accounting; our primary sensor, validated against direct `cpu.stat` cumulative counters.

The gap we target: most prior work reports QoS (latency/throughput) and/or aggregate energy, but rarely **attributes energy between the control plane and the function with an independent validity check**.

---

## 3. Research Questions

- **RQ1 (methodology):** Can container-level sampling attribute marginal CPU/energy to the control plane vs the function with a *verifiable* error bound?
- **RQ2 (measurement):** What fraction of dynamic CPU/energy does the Fn control plane consume for a CPU-bound function under a realistic load?
- **RQ3 (comparison):** Does the framework discriminate between platforms (Fn vs OpenFaaS)? — **answered (2026-08-06, core-count confirmed):** yes, with a caveat. On the 8-core bare-metal box the gap is 2.8 pp (gate fails), flat with concurrency within the box. A controlled same-instrument test (this 8-core box cpuset-pinned to 2 cores) confirms the gap is core-count-driven: it returns to 7.0 pp at 2 pinned cores. Direction is stable (Fn's share higher everywhere); the *magnitude* is a machine-pair property, and the mechanism is asymmetric — Fn's share is what inflates under core scarcity, not both platforms proportionally (see §5.5). Extended to four platforms across three sessions (2026-08-08, §5.6): the
container-visible control-plane share spans an order of magnitude — OpenFaaS ~7.1–7.6 < Fn ≈
Knative ~10.5–12.4 < OpenWhisk ~80–82 (attribution conventions differ; see §5.6 map).

---

## 4. Methodology

### 4.1 Design overview

A single **measurement window** synchronizes three things: (a) QoS load generation, (b)
per-container CPU sampling, and (c) pre/post direct-counter reads for validation. The window is
repeated N times; per-run ratios are computed *inside* each run and then summarized across runs
(never reconstructed from independently-medianed components — §4.6).

We report two energy scopes because they answer different questions:

- **`cp_share_pct`** = control-plane energy / *total* machine energy (idle + dynamic). Answers:
  "how much of the *facility* cost is orchestration?" (small — 5.8–7.9% on the platforms we
  measure).
- **`cp_dynamic_share_pct`** = control-plane energy / *dynamic* (function + control-plane)
  energy, where the idle baseline is excluded. Answers: "of the *marginal work that load creates*,
  how much is orchestration?" This is the headline metric (10.5% on the 8-core box, 14.0% at 2
  pinned cores — §5.5). It is a pure ratio of cgroup CPU-times, so it is invariant to the energy
  model's power constant and, by the invariant-TSC argument (§5.5 caveat), robust to per-core
  frequency/turbo differences.

### 4.2 Environment

| | Value (codespace — origin instrument, no results cited) | Value (bare metal, 2026-08-05) |
|---|---|---|
| Host | GitHub Codespaces, x86_64, 2 vCPU, Docker 29.3.0, Python 3.12 | 8-core Ubuntu, 16 GB, docker + swarm, RAPL readable |
| CPU | 2 shared vCPU (cloud VM, co-tenanted) | 11th Gen Intel Core i5-1145G7 @ 2.60 GHz — 4 cores / 8 threads, 1 socket, turbo to 4.4 GHz, governors ondemand/performance (3.30 GHz loaded at c=4; 3.60 GHz when pinned to 2 cores, report §31.8) |
| Platform | Fn — `fnproject/fnserver:latest` (0.3.x), containerized, iofs socket fix | Fn 0.3.x; OpenFaaS 0.8.3 (6-container CP, of-watchdog); OpenWhisk standalone:nightly; Knative Serving v1.23 + Kourier on k3s v1.36.3 |
| Function runtime | Python 3.12 FDK (`fnproject/python:3.12`) | Python FDK (hello:0.0.7 Fn / hello:latest OF; identical 5 ms spin handler on all four platforms) |
| Load generator | `hey` (Go) preferred; Python stdlib fallback | `hey -o csv`, TOTAL=10000, warmup 20 |
| RAPL | Unavailable (cloud VM) → CPU-time model | **Available** → model RAPL-validated 4.2–8.2% (idle 4.3 W) |

> **Hardware-dependence — explicit.** All absolute values (share, gap, per-request ms) are
> machine-pair-specific: the headline numbers would differ on a different core count, CPU model,
> DVFS policy, or co-tenancy regime — this is the *point* of §5.5's cross-regime table, not a
> caveat swept under the rug. What generalizes is the **framework and the per-machine-pair gate**:
> the protocol records the machine's `cpu_count`, governor, and frequency in every `summary.json`,
> and the 5 pp decision is re-derived per host pair rather than taken as a platform constant. A
> reader applying the method to their own hardware gets citable, machine-local numbers — the same
> instrument, re-anchored.

> **[CANDID, mostly closed]**: RAPL validation on bare metal was a hard requirement (§7, §10) — now
> delivered (2026-08-05). Remaining CANDIDs: a second bare-metal machine, control-plane
> decomposition, cold-start vs warm, mixed workloads.

### 4.3 Workload

`hello/func.py` is a genuine **5 ms CPU spin** (`while time.perf_counter() - t0 < 0.005: pass`). Rationale: a *CPU-anchored* workload makes the function's marginal CPU measurable and stable. A no-op "hello" handler was evaluated and rejected: with a near-free function, both ratio numerator and denominator become tiny and noisy (fn freeze churn between calls → 0% CPU → `cp_dynamic_share` swings ±13 pp). **The metric must be workload-anchored** — itself a reviewer-relevant methodological finding.

Run profile (reported results, bare metal): 10000 requests, concurrency 4, warmup 20, repeat 5,
SLO 500 ms, duration cap 300 s, 16 static OpenFaaS replicas (GIL-concurrency parity: every
platform serves the same 5 ms spin with a static, multi-replica warm set — see §4.6). Runs are
**count-bound** (exactly N requests per platform; `--duration` is a *safety cap*, not a hard stop)
so cross-platform windows are identical in composition even when wall time differs.
**Every benchmark session starts from a freshly-restarted control plane** (`reset` in
`run_saqef.sh`): leftover warm/zombie function containers from a prior session were found to fold
into `fn_cpu` under the old denylist and inflate it (report §18), so a reused server is no longer
measurable via the runner.

### 4.4 Instrumentation

**Per-container CPU sampler.** A background thread reads per-container CPU in two modes:
- `--sampler cgroup` (primary): maps each running container to its cgroup dir and reads the **raw cumulative CPU seconds** (`cpu.stat` → `usage_usec`). Samples are stored as cumulative counters with true wall timestamps.
- `--sampler docker` (fallback): 1 Hz `docker stats` percentages.

**Key design decision — raw cumulative + true-timestamp differencing.** The reducer differences
*consecutive cumulative reads* using *true sample timestamps* (`dt = t_{i+1} - t_i`). This makes
the totals **exact regardless of sampling cadence** — slow cgroup reads, scheduling jitter, or
spurious wakeups cannot bias the integral (the reducer never re-multiplies by a sampler-internal
dt). This property is what lets the delta-check pass to 0.01% even when sampling coverage is low
on the nested mount (§5.3). Memory is captured the same way (`memory.current` /
`memory.usage_in_bytes`) so `cp_peak_mem_mb` is a real measurement in cgroup mode.

**Classification is allowlist-based.** Control-plane = containers matching
`--cp-containers`/`--cp-images`/`--cp-labels`; function = containers matching
`--fn-containers`/`--fn-images`/`--fn-labels`. When no function allowlist is given, *everything
not CP* is function (denylist default, back-compat). When an allowlist IS given, any container
matching neither CP nor function goes to a logged `unclassified_cpu_s` bucket (warn if > 0.5
CPU-s) — **so a stray container can never silently inflate `fn_cpu`**; and if the allowlist
matches *nothing*, the harness warns instead of silently falling back (a wrong image is loud).
`run_saqef.sh` passes `--fn-images hello` by default — the **deployed** function image name, not
the base runtime `fnproject/python:*` (Fn names function containers with opaque ULIDs, and the
running containers carry the built image, so the deployed image is the only reliable signal).
`container_inventory` (names) and `container_labels` (name → image + labels) are embedded in
every summary for audit. cgroup rescans run every `--rescan-s` s (default 0.25) to bound the
blind spot for containers born and dying within one scan.

**`--delta-check` (independent validation).** The cumulative counters of **all** control-plane
containers matching `--cp-containers` are read *directly* immediately before and after the window
(container list re-resolved on each read, so swarm task restarts cannot wedge the reader); the
summed direct delta is compared with the sampler's accumulated total:
`cp_sampler_vs_delta_pct = (sampler_total / direct_delta - 1) × 100`. ≈0 ⇒ the whole sampling
path is validated. For a multi-container control plane (OpenFaaS = 6 containers), the direct read
sums the entire set so the comparison is like-for-like.

**`--idle-probe` (static baseline).** Platform up, zero traffic for `--duration` s: measures the
orchestration baseline that exists even with no invocations (gateway, scheduler daemons).

**Load generator.** `hey` (external Go binary) removes the harness's own Python/GIL footprint
from host accounting; falls back to a Python generator. QoS percentiles are parsed from
`hey -o csv` per-request rows (the only machine-readable mode mainline hey ships) or from
measured request latencies.

### 4.5 Metrics & energy/carbon model

```
cpu_sec(container) = Σ (cum_{i+1} - cum_i)                    # cgroup mode (exact)
dynamic_J(container) = cpu_sec × P_BUSY_CORE_W                # 3.5 W/busy core (Caribou, SOSP'24)
total_J = P_IDLE_BASE_W × wall_s + Σ dynamic_J                # idle base: calibrated per platform
carbon_gCO2 = kWh × PUE × CI                              # kWh = J / 3.6e6; PUE 1.15, CI 150 gCO2/kWh
cp := containers matching --cp-containers (platform-specific)
KPI = operational_gCO2 / N_SLO-compliant_invocations        # incl. idle baseline (window-dependent)
KPI_dynamic = dynamic_gCO2 / N_SLO-compliant_invocations    # load-created carbon only (wall-independent)
embodied_DRAM = 1390 gCO2/GB ÷ (5 yr × 8760 h)                # amortized, reported for context
```

Constants: `P_BUSY_CORE_W = 3.5`, `PUE = 1.15`, `CI = 150 gCO2/kWh`, `SAMPLE_S = 1.0`. The idle
baseline `P_IDLE_BASE_W` is **calibrated per platform, per session** (it is box-state, not a
platform constant — see below) from a 60 s RAPL read with the stack up, zero traffic: Fn/
OpenFaaS 4.3 W (2026-08-05/06 baseline session, still valid for their citable energy numbers);
OpenWhisk 5.294 W → 3.889 W → 4.873 W across three sessions; Knative 11.138 W → 7.007 W → 4.906 W
across three sessions, superseded on 2026-08-09 by the N=5 protocol (§5.6): 3.871 W bare /
4.561 W with `hello` at 16 replicas (median, spreads 0.25/0.41 W). The model default of 30 W is
never used for the reported numbers.
**This box-state drift is itself a methodology finding, in two parts**: (1) `saqef regression`'s
Fn/OpenFaaS idle-w is a static config value that is never re-measured session to session, so a
session's RAPL validation can silently degrade if run long after the last calibration (§5.6
energy-citability note); (2) the calibration was originally a *single* point-in-time sample,
unlike every other metric in this study (N=5 with bootstrap CI) — and Knative's three
single-sample idle-w readings (11.1/7.0/4.9 W) proved exactly how fragile that is, spanning more
than 2× with no repeats to separate real drift from noise. The N≥3 repeated-read protocol
(`rapl_w_series()`, wraparound-guarded, median + spread) that closed the Knative question in
§5.6 is now the recommended calibration discipline for every platform.

**This is estimation, not measurement** for absolute Joules (±50% typical on absolute energy). It
is defensible as: (a) a transparent, reproducible model; (b) a *relative* cross-platform
comparator on identical hardware; (c) RAPL-validated on bare metal to 4.2–8.2% steady-state
error (Fn/OpenFaaS, where the calibrated idle-w makes the linear model fit).

**Model constant sources & sensitivity.** `busy_core_w = 3.5` follows Caribou (SOSP '24)
per-core dynamic power; `idle_w` is the RAPL-calibrated value per platform. Every summary emits a
`sensitivity` block that recomputes the dynamic share, dynamic energy, and operational carbon at
busy-core 2/3.5/5 W, plus `idle_band` (carbon at idle 15/30/45 W): the **dynamic share is
invariant to the busy-core constant** (it cancels in the ratio), so the headline ratio is
model-robust; absolute energy/carbon scale linearly and are honestly bounded by these bands.

### 4.6 Statistical protocol

- N=5 repeats per configuration, and every reported number is the **median across runs** of a
  per-run leaf, with min/max spread. Where the headline depends on reproducibility (the 2-core
  regime), the whole run is repeated as an *independent session* (fresh teardown/redeploy), so the
  headline is a median of session medians.
- **Ratios are computed per-run and then medianed** (never reconstructed from medians of raw
  components) — prevents internally-impossible aggregated ratios.
- `bootstrap_ci` (percentile bootstrap over runs), `cv_pct`, **and `iqr` (Q3−Q1)** report
  repeat-run uncertainty. At N=5 the bootstrap CI mostly reflects resampling combinatorics, so the
  IQR is the honest companion measure; the paper reports both. **Caveat on "CI overlap" arguments:**
  because a percentile bootstrap of a median over only 5 points is numerically close to (and can
  equal) the raw min/max, "session A's CI overlaps session B's CI" is a much weaker statistical
  claim at N=5 than the same statement would be at, say, N=30 — it is closer to "the ranges
  touched" than a rigorous significance test. Used correctly in this paper (e.g. §5.6's
  OpenWhisk-was-not-a-contamination-artifact argument, where the overlap is corroborated by IQR
  and by the share staying flat across the contaminated *and* quiet sessions), but any future use
  of CI overlap/non-overlap as the sole evidence for "this was/wasn't noise" should check IQR and
  the raw spread alongside it, not the CI in isolation.
- GIL-concurrency parity: every platform serves the identical 5 ms CPU-bound handler with a
  **static, multi-replica warm set** (16 replicas), never a single replica — otherwise Python's
  GIL serializes the handler and the function CPU collapses, inflating the share.
- JSON outputs sanitized (NaN/Inf → null) for strict downstream parsers.

### 4.7 Validation gates (must ALL pass per run)

| Gate | Definition | Accept |
|---|---|---|
| Delta-check | `cp_sampler_vs_delta_pct` | ≈ 0 (single-digit %) |
| Physical plausibility | `cpu_sec.fn + cpu_sec.cp ≤ cpu_count × wall_s` | true |
| **Host plausibility** | `host_cpu_sec ≤ cpu_count × host_window_s × 1.05`, where `host_window_s` is the host's *own* sampling window (`t_host_after − t_host_before`), not the load `wall_s` — self-consistent by construction: `/proc/stat` busy ticks over a window `W` can never exceed `cpu_count × W`, so the gate trips only on a real CPU-count/counter anomaly, never on fast-run window-edge alignment | true |
| **Host saturation** | `host_cpu_sec / (cpu_count × host_window_s)` | report per run; `host_saturated` flag if ≥ 85% — QoS is contention-contaminated |
| Coverage | `sampling_covered_s / wall_s` | ≥ 95% (bare-metal target) |
| QoS integrity | availability, SLO compliance | ≥ 99% |
| Determinism | two independent runs reproduce | within repeat variance |

---

## 5. Results (bare metal, Fn-validated first — median of 5 runs × 2+ sessions)

### 5.1 QoS

| Metric | Value |
|---|---|
| Requests / success | 10000 / 10000 (availability 1.0) |
| Throughput | 597 rps |
| Latency p50 / p90 / p99 / max | 6.5 / 7.1 / 8.9 / 46.2 ms |
| SLO compliance (500 ms) | 100% |

### 5.2 Energy & CPU attribution (bare-metal Fn, median run)

Worked example from the validated bare-metal Fn run (median of 5), illustrating the
attribution math; the cross-platform comparison is §5.5.

| Metric | Value | Meaning |
|---|---|---|
| `cpu_sec.control_plane` | 6.56 s | fnserver CPU over 16.75 s window |
| `cpu_sec.function` | 56.22 s | function containers (lower bound — see §7) |
| `cpu_sec_ceiling` | 133.98 s | 8 cores × 16.75 s (physical max) |
| `cp_share_pct` | 7.88% | CP / total machine energy |
| **`cp_dynamic_share_pct`** | **10.46%** | CP / (function + CP) dynamic energy |
| Dynamic energy | 219.7 J | CP 23.0 J + function 196.8 J |
| Total energy (window) | 291.8 J | 4.3 W idle × 16.75 s + 219.7 J dynamic |
| `cp_peak_mem_mb` | 34.6 MB | fnserver peak RSS (cgroup mode) |

### 5.3 Validation results (bare-metal Fn)

| Gate | Result |
|---|---|
| `cp_sampler_vs_delta_pct` | **0.00%** (sampler = direct counter) |
| `cp_delta_sec` vs sampler CP | 6.565 ≈ 6.56 s (exact) |
| `physical_plausible` | true on all 5 runs |
| `host_plausible` / host_sat | true / 74.3% (`host_saturated=false`) |
| coverage | 100.0% on all 5 runs |
| Reproduction | cross-session: 10.46 (quiet 2026-08-05) vs 11.60–12.92 (same-day drift, §5.5) |

### 5.4 Absolute per-invocation overhead (model-based, bare-metal Fn)

| Quantity | Per invocation |
|---|---|
| Control-plane CPU | 6.56 s / 10000 = **0.66 ms CPU** |
| Control-plane dynamic energy | 23.0 J / 10000 = **2.30 mJ** |
| Control-plane carbon (dynamic) | ≈ **0.11 µg CO₂** |
| Total operational carbon (incl. idle base) | ≈ **1.40 µg CO₂** (idle ~25%) |
The idle-dominance is itself a result: at this light load, **~25% of operational carbon is the always-on baseline**, and the marginal cost of serving is split ~10/90 orchestration/function (0.66 ms CP vs 5.62 ms fn per invocation). This is precisely the regime where autoscaling ("scale to zero") pays off — and where the orchestration tax is most visible per unit of useful work.

### 5.5 Cross-platform, RAPL-validated results (bare metal, 2026-08-05)

**Figure 1 — The core-count effect (Fn vs OpenFaaS, same instrument).** Median `cp_dynamic_share_pct` (bar = reported median, error bars = full per-run spread across all sessions), 8 cores vs the same box cpuset-pinned to 2 cores, all validation gates green, reproduced in two independent sessions. The dashed line is the 5 pp a-priori decision gate; arrows mark the Fn−OF gap. Reading: the gap (2.79 → 7.00 pp) is core-count-driven, and the 5 pp gate is a *machine-pair* property, not a platform constant. Data: `results/{fn,openfaas}_cpubound_baremetal` and `*_2core{,,_session2}`.

![figure1](figures/figure1_core_count.png)

*(Figure 2 — per-run shares and Figure 3 — attribution split appear in §5.6, where the
four-platform data they plot is introduced.)*

Same protocol on an 8-core Ubuntu box (RAPL-validated, idle 4.3 W): Fn vs OpenFaaS serving the
identical 5 ms CPU-bound function, `c=4 < cpu_count=8`, `TOTAL=10000`, 5 runs, 16 static OF
replicas, all gates green (`host_saturated=false`, delta-map 6/6, coverage 100%).

| Metric (median of 5) | Fn | OpenFaaS |
|---|---|---|
| `cp_dynamic_share_pct` | **10.46** (9.80–11.74, CV 8.1%) | **7.67** (6.80–7.83, CV 6.7%) |
| gap (pp) | **+2.79 — below the 5 pp gate** | |
| `cp_share_pct` (CP / total, real idle) | 7.88 | 5.79 |
| per-request CP cost | 0.66 ms | 0.56 ms |
| per-request fn cost | 5.62 ms | 6.71 ms |
| QoS p50 / p99 | 6.5 / 8.9 ms | 7.2 / 12.1 ms |
| throughput | 597 rps | 532 rps |
| SLO compliance | 1.0 | 1.0 |
| RAPL validation err (steady-state) | 4.2–5.5% | 4.2–8.2% |

**Concurrency sensitivity (c=8, REPEAT=2, quick check):** Fn 10.47, OpenFaaS 7.62 at 91–93% host
saturation — the share and the gap look **flat with concurrency within the box** (gap 2.79 → 2.85
pp). REPEAT=2 is sufficient to confirm flatness against the already-validated c=4 baseline but is
**not yet the basis for a citable headline number** — bump to REPEAT=5 before this row is cited
standalone in the paper.

**Cross-regime reading (central contribution — core-count effect CONFIRMED 2026-08-06 with a
controlled, same-instrument experiment):**

| regime | machine | Fn | OpenFaaS | gap |
|---|---|---|---|---|
| headroom, clean | 8-core, c=4 | 10.46 | 7.67 | +2.8 pp |
| saturated, clean | 8-core, c=8 | 10.47 | 7.62 | +2.9 pp |
| saturated, clean, SAME instrument, 2 independent sessions | 8-core box cpuset-pinned to 2 cores, c=4 | 14.00 (13.91/14.08) | 7.00 (6.82/7.17) | +7.0 pp (6.91/7.09) |

The last row is the controlled confirmation: same box, same corrected protocol (fixed spin,
RAPL-calibrated idle-w, `--cpu-count-override`/`--host-cpu-list` so the saturation gate is scoped
to the 2 pinned cores, not the whole 8-core machine), REPEAT=5, all citability gates green — only
the core count changed. It was reproduced in a full independent second session (fresh
teardown/redeploy/re-pin; Fn's rebuilt image even changed tag, 0.0.10→0.0.11, with no effect since
the pinning daemon targets "every running container," not an image tag) — the two sessions, each
at the full REPEAT=5 protocol with per-run gate tables, give session gaps of 7.09 and 6.91 pp,
reproduced to within 0.2 pp. The gap jumps from 2.8–2.9 pp at 8 cores to ~7.0 pp at 2
cores. **Core count driving the gap's magnitude is therefore an earned, reproduced finding,
not an inference across mismatched instruments.**

The mechanism is *not* symmetric. The controlled data shows an asymmetric effect: Fn's
share rose sharply under core scarcity (10.46 → 14.00, +34%) while OpenFaaS's share was flat to
slightly lower (7.67 → 7.00, −9%). **[CANDID]** why Fn's control-plane overhead specifically is
sensitive to core scarcity (fnserver scheduling contention under thread starvation is the leading
hypothesis, vs of-watchdog's per-replica isolation model) is observed, not yet mechanistically
explained — a follow-up micro-benchmark, not a claim, is the honest way to close this. Because
OpenFaaS's share is also a conservative bound (of-watchdog proxies inside the function cgroup),
its true share (and the true gap) is smaller still in every regime. **The 5 pp a-priori gate is a
machine-pair property, not a platform property** — the paper reports per-machine-pair gates and
presents both the machine-dependence and its asymmetry as findings.

> **Caveat — energy/carbon not citable from the 2-core-pinned row.** `rapl_validation_err_pct`
> ran **43–60%** on both platforms under core pinning (the same magnitude as the OLD, wrong
> `idle_w=30` calibration), even though the correct RAPL-calibrated `idle_w=4.3` was used. Likely
> cause: the 4.3 W idle baseline was measured with all 8 cores idle; under 2-core pinning the
> active cores' turbo/frequency behavior and/or the idle contribution of the 6 un-pinned-but-present
> cores no longer match that baseline. `cp_dynamic_share_pct` is a pure cgroup CPU-time ratio and
> is unaffected **to first order** — numerator and denominator accrue CPU-time on the same 2 pinned
> cores, and Linux CPU-time is frequency-normalized, so a common turbo boost cancels in the ratio.
> The residual is a small second-order per-core-DVFS term (cores 0 vs 1 at different clocks with a
> systematic CP-on-faster-core split), bounded but unverified. The share from this row is citable
> with that caveat; absolute J/gCO2 are not, without re-deriving idle-w for the pinned
> configuration. **Frequency-parity verification — CLOSED (2026-08-06; report §31.8).** Direct
> `scaling_cur_freq` sampling during a pinned run vs a c=4 run found the pinned loaded cores at
> **3.60/3.60 GHz (identical — no per-core DVFS asymmetry)** vs **3.30 GHz** at c=4. The ~+9%
> frequency rise under pinning is real and is the measured cause of the RAPL error above, but it
> does not confound the share: Linux CPU-time accrues via the invariant TSC (wall-time-on-core,
> not cycles), so a ratio of CPU-times is frequency-invariant by construction, and with cores 0/1
> at identical clocks there is no CP-on-faster-core channel either. **Mechanism remains future
> work:** `perf stat -e context-switches,migrations` on fnserver at 2 vs 8 cores.

### 5.6 Four-platform comparison (same 8-core box, c=4, REPEAT=5; Fn/OF from the 2026-08-09 regression leg, Kn/OW from the 2026-08-08 evening rerun)

**Figure 2 — Per-run shares, four platforms (8-core box; Fn/OF from the 2026-08-09 regression leg, Kn/OW from the 2026-08-08 evening rerun — see the data-source note in Appendix A).** Every per-run value shown (n=5 per session; Fn has 3 sessions → 15 points; markers distinguish sessions), black tick = reported median. The platform ordering is the robust finding: OpenFaaS 7.40 < Fn ≈ Knative 10.56–12.96 (11.60 three-session median) < OpenWhisk 82.36 (attribution conventions differ — see the map below). OW's 82.4 uses the same y-axis; the scale is dominated by it, which is itself the point (an order-of-magnitude orchestration cost).

![figure2](figures/figure2_four_platform_scatter.png)

**Figure 3 — Attribution split (four platforms, 8-core box).** Per-run median dynamic CPU time decomposed into control-plane (solid), function (translucent), and unclassified (grey) bars, with the CP share labeled on top. Reading: the control plane is 7.4–12.4% of the marginal CPU time on all three lightweight platforms (OF/Fn/Kn) but ~82.4% on the standalone OpenWhisk emulator, whose single JVM orchestrator + per-activation docker-log store dominates (deployment-mode caveat, below).

![figure3](figures/figure3_attribution_split.png)

**Figure 4 — Control-plane CPU per invocation (ms CPU / request), four platforms.** Median CP CPU per run ÷ 10,000 requests (per-platform aggregate across sessions). Reading: of-watchdog (per-replica proxy inside the function cgroup) 0.53 ms ≈ fnserver (dedicated broker process) 0.75 ms aggregate (session medians 0.72–0.82 ms) ≈ Knative 0.96 ms (knative-serving + kourier gateway + activator), while the standalone OpenWhisk JVM costs ~26.8 ms — a ~28–54× orchestration tax per request vs the lightweight platforms. Data: `results/regression/{openfaas,fn}` (2026-08-09 leg) + `results/{knative,openwhisk}_cpubound_baremetal` (2026-08-08 evening rerun).

![figure4](figures/figure4_cp_cost_per_inv.png)

*(PDF: `figures/figure*.pdf`; regenerate with `python3 figures/make_figures.py` — data-driven, no script edits.)*

OpenWhisk (standalone) and Knative (Serving v1.23 + Kourier on k3s v1.36, docker runtime) were
added to the same protocol. All rows are full REPEAT=5 runs with per-run gate tables (delta ~0,
coverage 100%, host_plausible true). **Read the attribution map and the citability notes below
before citing the absolute values — the *ordering* is the robust finding.**

*Superseded numbers, not to be cited:* two earlier snapshots of this table are retired. The
2026-08-07 snapshot (OpenFaaS 7.53, Fn 12.27, Knative 13.99, OpenWhisk 82.54) was collected while a
benchmark agent consumed ~2.8 cores. A 2026-08-08 morning quiet-box rerun (OpenFaaS 7.62, Fn 11.01,
Knative 11.40, OpenWhisk 80.23) fixed that contamination but was itself collected before a same-day
audit found and fixed a real classification bug (OpenFaaS's control-plane matcher was silently
counting a small amount of leftover Knative CPU as its own — see the isolation note below). **The
table below is the current one to cite**: Fn/OpenFaaS from the **2026-08-09 regression leg**
(self-certifying quiet gate, ambient 9.9%/11.7% of a 15% ceiling, reproducing the same-day
re-anchored references 11.49/7.61 within 0.25 pp — G6 GREEN, §8.1), Knative/OpenWhisk from the
2026-08-08 evening rerun on the same box, with the classification and isolation fixes applied and
validated live. All earlier snapshots remain in git
history for an appendix note on how the numbers evolved as bugs were found, but only this table's
numbers are current.

| platform | median `cp_dynamic_share_pct` | spread (min–max) | per-inv CP CPU | fn CPU/inv | host_sat% | energy citable? |
|---|---|---|---|---|---|---|
| OpenFaaS | **7.40** (2026-08-09 leg; ref 7.61) | 7.08–7.50 | 0.53 ms | 6.59 ms | 70.1–73.6 | not from this session — see note |
| Fn | **11.60** (3-session median; 2026-08-09 leg 11.27) | 10.56–12.96 across sessions | 0.75 ms (session medians 0.72–0.82) | 5.64 ms (5.53–5.69 across sessions) | 70.5–71.8 | not from this session — see note |
| Knative | **12.44** | CI 12.12–12.97, CV 2.73% | 0.96 ms | 6.79 ms | 74–78 | **yes** (rapl err 1.3–4.7%, all 5 runs steady-state — no warm-up transient this session) |
| OpenWhisk | **82.36** | CI 81.94–86.86, CV 2.44% | 26.82 ms | 5.75 ms | 57–70 | no (rapl err 31–50%, structural; run_1 an unexplained 0.19% outlier) |

**Isolation note (the bug behind the OpenFaaS correction).** A same-day audit found OpenFaaS's
`cp_containers` matcher included a bare `"gateway"` substring that also matches Knative's
`kourier-gateway` pod (k3s/Knative-serving stay resident on this box across every platform's
session, by design). This silently folded a small amount of leftover Kourier CPU into OpenFaaS's
control-plane bucket in every prior OpenFaaS run on this box. Fixed by scoping the matcher to the
swarm-stack-prefixed container names (`openfaas_gateway`, ...); confirmed live afterward — the
fresh run's `delta_check_map` shows exactly the 6 real OpenFaaS containers, zero Kourier entries.
The effect on the reported share was small (OpenFaaS's regression-session share moved 7.62 → 7.14
in the 2026-08-08 evening leg, partly this fix and partly ordinary day-to-day box drift — the two
are not separated here; the 2026-08-09 leg re-reads 7.40, ref 7.61).

**Reading.** The container-visible control-plane share spans an order of magnitude: of-watchdog
(per-replica, inside the function cgroup) 7.4 < fnserver (a dedicated broker process) ~10.6–11.6 <
Knative's knative-serving + kourier gateway + activator ~12.4 < the standalone's JVM
orchestrator+log-store 82.4. Fn and Knative sit close together on this box; OpenWhisk still burns
roughly a ~7× multiple of the function CPU in orchestration, consistent across three independent
sessions of varying box-state (82.54 contaminated → 80.23 quiet → 82.36 today) — the OpenWhisk
finding has now survived three separate measurement conditions with only ~2 pp of movement,
which argues it is a genuine structural property of the standalone emulator, not a measurement
artifact.

**Energy-citability note (important — do not conflate sessions).** The "yes/no" flags above
describe the SAME session that produced the share in that row. Fn/OpenFaaS's `idle-w=4.3` is a
config-file constant from the 2026-08-05/06 calibration that `saqef regression` never re-measures;
by 2026-08-09 it was stale enough that this session's own RAPL validation is 16.6% (Fn) / 18.9%
(OpenFaaS) on run_1 — above the citable range, though runs 2–5 fit cleanly (≤12.1% / ≤14.8%) —
still above the 4.2–8.2% / 4.2–5.5% figure historically (and correctly) cited for Fn/
OpenFaaS energy, which comes from the separate, earlier `fn_cpubound_baremetal` /
`openfaas_cpubound_baremetal` sessions (§5.1, still valid and still the citable energy numbers for
these two platforms — just not from *this* share-comparison table's session). `saqef gates` now
prints a loud "RAPL FIT DEGRADED" flag above 15% error, which fired correctly on run_1 of both
platforms in the 2026-08-09 leg (and on 3 of Fn's 5 / 4 of OpenFaaS's 5 in the 2026-08-08 evening
leg) — a live confirmation the flag works, and a reminder that
Fn/OpenFaaS's `idle_w` needs recalibrating before it is trusted for energy again.

**Attribution map (must be printed next to the table).** Knative's *fn* bucket includes the
per-replica **queue-proxy sidecar** (on the request path) AND the function; its *CP* bucket
includes the **kourier gateway + svclb-kourier** (data-plane) + activator + controller/autoscaler/
webhook/net-kourier-controller. OpenFaaS counts its of-watchdog proxy *in fn*. So the true
OF-vs-Kn control-plane gap is even smaller than the raw 7.1-vs-12.4 suggests; the reported CP shares
are per-platform *attribution conventions*, and the asymmetry is intentional (mirrors where the
platform itself puts its proxy). The k3s **embedded control plane** (apiserver/etcd/scheduler/
controller-manager) runs inside the `k3s server` process — invisible to the container sampler,
lands in the host residual; a production cluster would bill it as shared infra, so the Knative CP
share is a *container-visible* lower bound. OpenWhisk's CP is the standalone emulator's single JVM
(controller + invoker + scheduler + log-store); its per-activation `docker logs` log-store is
"control plane" here because the standalone ships it that way — how much is emulator artifact is
the deployment-mode decision flagged in §5.

**Convention-normalized view (expert review #5; one apples-to-apples number to stand on).** A
hostile reviewer's first objection is that the four-platform ordering compares four differently
defined quantities. The defense has two parts. First, the *reported* convention is already the
consistent one: all of Fn, OpenFaaS, and Knative co-locate their request-path proxy in the
function bucket (OF's of-watchdog runs inside the function cgroup, Knative's queue-proxy is a
per-replica sidecar, Fn's watchdog is in-process inside the function container) — so fn CPU
means "function + its co-located request-path proxy" on all three. Second, the one convention
that CAN be varied from the raw data — classifying Knative's queue-proxy as control plane
instead of function — does not change the ordering:

| platform | CP bucket | fn bucket | share (proxy co-located in fn — reported) | share (proxy→CP, worst case) |
|---|---|---|---|---|
| OpenFaaS | gateway, queue-worker, nats, prometheus, alertmanager, faas-swarm | hello replicas (of-watchdog co-located) | **7.40** | n/a — proxy inseparable from fn cgroup |
| Fn | fnserver | function containers (in-process watchdog) | **11.60** (3-session) | n/a — no separable proxy |
| Knative | kourier-gateway, svclb-kourier, activator, autoscaler, controller, webhook, net-kourier-controller | user-container **+ queue-proxy** | **12.44** | **25.7** (queue-proxy→CP) |
| OpenWhisk | standalone JVM (controller+invoker+log-store) | action containers | **82.36** | n/a — no per-replica proxy |

The 25.7% figure is recomputed from the same `samples.csv` data (per-container CPU-time
integrated over the run; reproduces the harness's own 12.44 to within 0.02 pp, cross-validating
the method): Knative's queue-proxy is ~10.3 CPU-s per run vs ~57.6 s fn and ~9.4 s CP. Reading:
(1) the **Fn–Knative cluster tie is convention-sensitive** — classify the sidecar as CP and Kn
separates from Fn (12.4 → 25.7) — so the honest statement is a tie *within the co-located-proxy
convention*, and the paper reports them as a cluster, not an ordering; (2) **OpenFaaS <
{Fn, Knative} and both << OpenWhisk survive every plausible reclassification** (OW is an order
of magnitude away no matter where the sidecar lands), which is what makes the four-platform
ordering claim convention-robust. Per-invocation control-plane CPU (0.53 / 0.75 / 0.96 / 26.82 ms)
has the same convention caveat and the same robustness properties.

**Idle-baseline finding — CLOSED (2026-08-09, N=5 per condition).** Three single-sample readings
of Knative's idle package power previously disagreed badly — 11.14 W (2026-08-07, contaminated
box); 7.01 W (2026-08-08 morning, "quiet" box); 4.91 W (2026-08-08 evening, `hello` deployed at
16 replicas) — and no repeats existed to separate real drift from single-sample noise. That
fragility is exactly why the finding was kept open. On 2026-08-09 the calibration was repeated
N=5 per condition (60 s RAPL reads, wraparound-guarded, median + spread):

| condition | stack state | median (min–max) |
|---|---|---|
| A | bare k3s + knative-serving + kourier, **no `hello`** | **3.871 W** (3.692–3.946, spread 0.25 W) |
| B | `hello` @ 16 replicas (exact bench-time state) | **4.561 W** (4.309–4.719, spread 0.41 W) |

**Premium B − A = 0.690 W** — a real but small always-on premium over the ~4.2–4.3 W
bare/Fn/OpenFaaS baseline, and consistent to within noise with the earlier back-of-envelope
decomposition (~4.2 W substrate + ~0.7 W for the 16 warm replicas + 32 proxies). The condition-B
median (≈4.56 W) is the recommended `--idle-w` for a Knative bench. **Use these N=5 medians, not
any single-sample reading, as the citable Knative idle baseline and its ~0.7 W always-on idle
premium** (design-principle C3, §11). Both conditions were statistically well-behaved (spreads
0.25/0.41 W vs the >2× scatter of the old one-shots). The linear busy-core energy model fits
Knative's dynamic (load) power well regardless (rapl err 1.3–4.7% this session, all 5 runs) —
**Knative energy/carbon is citable for the dynamic/marginal figures**, and its idle premium is
now pinned by a properly repeated measurement. **Corroboration (direction only):** the
idle-term-dominance hypothesis predicts that longer/heavier runs degrade less from a stale
`idle_w` even without recalibration, and the 2026-08-09 regression runs (TOTAL=10000, ~17–19 s
wall) show mostly clean RAPL fits — Fn 16.6/12.1/0.6/1.5/0.65, OF 18.9/14.8/2.3/0.6/5.8%, only
run_1 crossing the 15% degraded flag on each — versus every run failing at 20–55% on the shorter
contamination A/B legs (TOTAL=3000, ~5 s wall) under the same `idle_w=4.3`. A one-line observation,
not proof (`idle_w` was not recalibrated in either case). OpenWhisk's fit remains poor (rapl err
31–50% this session, consistent with 45–58% two days ago and 36% on the original contaminated
run) — a structural mismatch between the linear model and the standalone JVM's power draw,
confirmed stable across three independent sessions, so **OpenWhisk energy/carbon stays NOT
citable**; that is a separate, larger open item (Future Work §10), not a calibration gap this
protocol could close.

**Reproducibility note (three sessions, three box-states).** OpenWhisk's share has now been
measured three times under different conditions — 82.54 (contaminated), 80.23 (quiet), 82.36
(today, moderately loaded: host_sat rose to 57–70%, `dockerd` itself ran at 45–64% CPU during the
bench, consistent with the per-activation `docker logs` hypothesis in §5.6) — and stayed within a
~2 pp band throughout. That is strong evidence the ~80% finding is a genuine structural property of
the standalone emulator, not a measurement artifact. Knative moved more between the first two
sessions (13.99 → 11.40, outside the old session's own CI — contamination WAS materially inflating
that number) but then moved again today (11.40 → 12.44) under a session that was NOT itself agent-
contaminated, which is a reminder that "quiet" and "identical" are not the same thing — ordinary
day-to-day box variance is real even on a quiet box, and every number in this table is traceable to
one specific, fully-gated session rather than averaged across days for exactly this reason.

**QoS citability.** All four platforms' sessions passed the host_sat < 85% non-contamination gate
this time too (70–79% for OF/Fn/Kn, 57–70% for OW), so QoS is citable from this table's sessions:
OpenFaaS p50 6.9 / p99 12.8 ms @ 537 rps; Fn p50 6.4 / p99 9.2 ms @ 593 rps; Knative p50 7.6 / p99
11.8 ms @ 494 rps; OpenWhisk p50 113.6 / p99 182.0 ms @ 34.2 rps. SLO compliance is 1.0 on all
four. OpenWhisk's percentiles are visibly worse than the quiet-session reading (97.4/136.5 ms) —
consistent with the higher host_sat this session, and a concrete illustration of why the framework
reports host_sat alongside every QoS number rather than a bare latency figure.

---

## 6. Discussion

**Why the headline is `cp_dynamic_share_pct`, not `cp_share_pct`.** The static share (`cp_share_pct`) is what an operator sees on a dashboard; it hides the fact that the *marginal* cost of serving a function is dominated by orchestration — the dynamic share is ~10–14% and core-count dependent (§5.5). The dynamic share is the economically relevant quantity for per-invocation pricing, carbon-aware scheduling, and "green function" claims.

**Workload anchoring.** Ratios over near-zero denominators are noise (the no-op workload case). A CPU-anchored function makes the dynamic share measurable and reproducible (±3 pp across runs, ±0.3 pp across reproductions).

**What is measured vs estimated (honest line).** Measured directly: QoS, per-container CPU (validated to 0.01% against direct counters), physical plausibility, RAPL package Joules (bare metal). Modeled: absolute Joules and carbon (CPU-time proportionality, literature constants). The **relative** dynamic share is robust to the model constant (3.5 W/core scales both numerator and denominator), so `cp_dynamic_share_pct` is the most defensible number we produce.

---

## 7. Threats to Validity (explicit)

1. **No RAPL on the origin instrument (closed for the reported results).** The reported absolute energy/carbon come from the bare-metal 8-core box, where the model is RAPL-validated to 4.2–8.2% steady-state (idle calibrated 4.3 W, not the 30 W default). The earlier shared-VM instrument had no RAPL and is used only as the origin story (§4.2); its absolute numbers are not cited. The relative `cp_dynamic_share_pct` is model-constant-robust everywhere.
2. **Function CPU is a lower bound.** Function containers live 2–5 s and are only partially captured by the sparse nested-mount sampler (`sampling_covered_s` ranged 13–100% across runs; totals stay exact for *sampled* containers because of cumulative differencing). CP is exact (proven); treat `cpu_sec.function` as ≥ the reported value. Improves on bare metal.
3. **Co-tenancy (origin instrument only).** Host-level metrics from the abandoned shared-VM instrument included neighbor noise; that instrument contributes no reported numbers (definition unchanged: `orchestration_cpu_sec` is a host-wide residual, never presented as pure orchestration). The reported results come from a dedicated 8-core box, where host metrics are clean; the cgroup-exact control-plane container share (`cp_dynamic_share_pct`) is the claim.
4. **Contention-contaminated QoS (closed for the quiet c=4 baseline).** On the shared-VM origin instrument, `host_saturation_pct` ≈ 100% made latency percentiles reflect scheduler contention, not intrinsic platform overhead. On the 8-core box at c=4 (host_sat 74–77%) QoS is citable: Fn p50 6.5 / p99 8.9 ms @ 597 rps; OF p50 7.2 / p99 12.1 ms @ 532 rps; SLO 1.0. The c=8 quick runs (sat 91–93%) carry the `host_saturated` flag and their latency is NOT citable — consistent with the discipline that a saturated box measures reproducibly wrong.
5. **Agent-style background load moves the share — a measured, asymmetric bound.** We quantified how much a co-tenanted background load like the one that contaminated the 2026-08-07 session moves `cp_dynamic_share_pct` on this box, using the harness's own A/B tool (`tools/contamination_ab.py`: clean vs dirty leg, N=5/leg, profile-matched to that incident — 3 busy cores ≈ 300% CPU + 1.1 GB RSS; results in `results/{fn,openfaas}_contamination_ab/`). Fn's share moved **10.0 → 12.2 (+2.2 pp)**, reproduced within ~0.05 pp across two independent sessions, while OpenFaaS's moved **6.9 → 7.2 (+0.3 pp)** — a ~6× asymmetry. The share is inflated only where a *central orchestrator* sits on the request path (fnserver); OpenFaaS's per-replica of-watchdog model is nearly immune. Consequences: (a) the earlier inferred ~0.3–1 pp contamination estimate is superseded by this direct measurement; (b) no headline conclusion is overturned by an un-quiet box — the ordering OF < Fn survives both legs — but the margin is thin: the clean gap is 3.1 pp and the dirty gap 4.92 pp, just **0.08 pp (1.6%)** below the 5 pp gate, and the dirty-leg gap's run-to-run spread straddles it (≈4.7–6.1 pp across per-run values). The gate is therefore not only machine-pair dependent but *co-tenancy dependent*: a heavier-than-incident background load (an explicit heavier-load probe is future work) would likely push the Fn–OF gap across 5 pp, and an Fn share measured under agent-like load carries up to ~+2 pp of inflation; (c) the asymmetry independently supports the central-orchestrator-vs-per-replica design principle (§6), matching the core-scarcity direction (§5.5) — a hypothesis the four-platform set can test further (OpenWhisk's single-container control plane is the natural next probe, §12). The ambient-load quiet gate (§4.7, ≤15% host busy sampled before any bench) now self-certifies a citable run, with this A/B as the measured bound behind it.
6. **Four platforms, one machine, shared attribution caveat.** Fn, OpenFaaS, OpenWhisk and Knative are measured on the 8-core bare-metal box with the *same* instrument (§5.6), but the CP/fn attribution convention differs per platform (OF's of-watchdog in fn; Kn's queue-proxy in fn but kourier gateway in CP; OW's whole JVM as CP) — the reported share is a per-platform convention, so cross-platform share comparisons must cite the map. The discriminator's magnitude is machine-dependent (RQ3 answer, §5.5) — a bounded threat that is now quantified rather than unknown. OpenWhisk's share additionally depends on the standalone emulator's deployment mode (per-activation log-store) — flagged **[CANDID]** for the paper.
7. **Control plane measured as one container** (`fnserver`), not decomposed into gateway/scheduler/queue sub-components. **[CANDID]**: profiling inside the control plane.
8. **Model constants** (idle 30 W, 3.5 W/core, PUE 1.15, CI 150) are literature defaults; CI in particular is regional/temporal.
9. **One physical host; the machine-dependence is itself the new threat.** All headline numbers come from a single 8-core box; the "two regimes" are 8-core native vs the SAME box cpuset-restricted to 2 cores, not two independent machines. The core-restriction design deliberately holds the instrument fixed (CPU model, DVFS, NUMA, thermal envelope — §31.8 verifies frequency parity) so only core count varies, which is exactly why the experiment is a valid core-count discriminator; but a genuinely different physical machine (different core count / NUMA / thermal envelope) could behave differently and is explicit future work. The paper therefore claims a core-count effect demonstrated on one host with core restriction, NOT a cross-machine constant, and presents the per-machine-pair gate so reviewers can apply the framework to their own hardware.

> **v9.3 review-driven corrections (external expert review, 2026-08-04):** (a) `host_cpu_sec` sums **busy** ticks only (was total → `orchestration_*` inflated ~5×); (b) **memory captured** in cgroup mode — `cp_peak_mem_mb` was 0.0, now real (88.6 MB on the v9.2 run); (c) **KPI fixed** — now operational gCO₂ per SLO-compliant invocation (≈39.6 mg incl. idle base on the saturated-vm run; note: this v9.3-era figure carried the pre-2026-08-06 J/3600 Wh bug and is ÷1000 → ≈39.6 µg under the corrected kWh formula); (d) **`sensitivity` block added** — dynamic share, dynamic energy, and carbon at busy-core 2/3.5/5 W (share invariant, absolutes banded); (e) **`orchestration_*` defined explicitly** as a host-wide residual (§7.3) and excluded from claims; (f) **quick-run guard** — `SAQEF_REPEAT < 5` writes to a `_quick` outdir so 1-run passes can't be mistaken for the 5-run publication set.
>
> **v9.4 review-driven corrections (second external expert review, 2026-08-04):** (g) **host plausibility gate** — `host_plausible = host_cpu_sec ≤ cpu_count × wall × 1.05`, with the cgroup quota (`cpu.max`) printed by `--check`; (h) **host saturation ratio** reported per run and flagged ≥ 85% (QoS caveat, §7.4); (i) **fn allowlist** (`--fn-containers`) — non-matching containers land in an `unclassified_cpu_s` bucket + `container_inventory` audit, never silently in `fn_cpu`; (j) **count-bound runs** — `-n` only, `--duration` is a safety cap with a post-run wall assertion (hey's `-z`/`-n` precedence is build-dependent); (k) **`--rescan-s`** (default 0.25) shrinks the blind spot for ephemeral containers; (l) **`iqr`** reported alongside `bootstrap_ci`/`cv_pct`, N≥10 for publication.
>
> **v9.5 corrections (between-session finding, 2026-08-04):** (m) **fresh-session protocol** — `run_saqef.sh reset` + `setup` always restart fnserver and remove orphaned function containers, so leftover warm containers can't inflate `fn_cpu` (root cause candidate for the 3.2× session swing); (n) **allowlist now exercised** — image/label signals (`--fn-images` etc.) added, making `unclassified_cpu_s` informative instead of a guaranteed 0.0; (o) **marginal KPI** — `kpi_gco2_per_inv_dynamic` (load-created carbon only) is wall-independent, unlike the ~93%-idle-dominated operational KPI; (p) neither session's absolute numbers are quoted in isolation — bare-metal multi-session medians are required.
>
> **v9.7 corrections (image-handle bug, 2026-08-04):** (q) the v9.5 allowlist default `fnproject/python:3.12` and the `reset_fn` `ancestor=` filter both referenced the **base runtime** image, but Fn's running function containers carry the **deployed** image (`hello:0.0.14`) — BuildKit does not preserve the FROM lineage. Net effect: the allowlist silently matched nothing and reverted to the denylist, and leftover warm `hello:*` containers were not cleaned. Fixed by defaulting `--fn-images` to the deployed image name (`hello`) and cleaning `hello:*` containers by image-name pattern; (r) **fail-open classification** — when an fn allowlist is configured but matches no container, the harness now WARNs and routes strays to `unclassified_cpu_s` instead of silently folding them into `fn_cpu`. The v9.5 numbers are unaffected (`container_labels` proved a genuine two-bucket world), but the protection is now actually enforced.

> **v9.8 corrections (third external expert review, 2026-08-04):** (s) **hey gate is functional, not size-based** — the old `stat -c%s ≥ 1000` check could reuse a stale ≥1000-byte wrong binary forever; `hey_smoke_ok()` now runs the candidate and wipes/reinstalls on any failure (G8); (t) **the ≥85% saturation QoS-caveat is enforced, not just documented** — new `host_saturated` field in every `summary.json` (`host_saturated_flag`: sat ≥ 85%), and the `gates` table marks a saturated run "QoS CONTENTION-CONTAMINATED". Both fixes are gates/audit only; no measurement path changed, so all prior numbers stand under their existing caveats.
>
> **v9.10 corrections (host-window alignment, 2026-08-05):** (u) **`host_plausible` / `host_saturation_pct` now measured over the host's own sampling window** (`host_window_s = t_host_after − t_host_before`), not the load `wall_s`. The host counter is read a few ms *outside* the load window; that fixed edge-slop is a constant fraction of the window, so at short wall times (e.g. the 10.4 s OpenFaaS v3 run at 4 replicas) it inflated `host_saturation_pct` past 100% (112%) and tripped `host_plausible=false` as an artifact, not a real signal. Because `/proc/stat` busy ticks over window `W` can never exceed `cpu_count × W`, the v9.10 definition is self-consistent: the gate can now only trip on a genuine CPU-count/counter anomaly. `host_window_s` is exposed per run for audit; the container-side `cpu_sec_ceiling` still uses the load window. (v) **the OpenFaaS `gates` table (`run_openfaas.sh`) regained the host columns** (`host_cpu_s`, `host_sat%`, `host_plausible`) dropped from the Fn table in an earlier port, with a hard "HOST IMPLAUSIBLE — do not cite host metrics" marker when false — a run can no longer be committed with this flag unnoticed.
>
> **v9.11 corrections (host read ordering, 2026-08-05):** (w) **the host `/proc/stat` read moved to immediately before `t0`**, so `host_window_s == wall_s` by construction. The v3 rerun on v9.10 exposed `host_window_s = 11.774` vs `wall_s = 10.23` — the ~1.5 s gap being the delta-check reader construction (`cp_cgroup_reader`, ~1.5 s on this box) that used to sit between the host read and `t0`; on a box that is ~100% busy regardless of load, that headroom added ~2 cores × 1.5 s ≈ 3 s of busy ticks to `host_cpu_sec`, which is exactly the excess that produced the old wall-based 112%. The cgroup CP counter (`cp_cum_before`) was already read after the construction, so the CP delta window was unaffected. Host metrics remain excluded from claims (§7.3).
>
> **v9.9 hey CSV root-cause fix + first real hey run (2026-08-04):** (u) **root cause** — mainline rakyll/hey **has never had an `-o json` mode**; any non-`csv` `-o` value is parsed as a literal text/template, so `-o json` prints the string `json` (rc=0). The v9.7 "not a rakyll/hey binary" diagnosis was wrong (`go version -m`: genuine `rakyll/hey v0.1.5`). The fix requests the one documented machine mode, **`-o csv`**, and parses the per-request rows (header-normalized `responsetime`/`statuscode`/`offset`; wall = max offset). `./run_saqef.sh all` (v9.9, `hello:0.0.20`, 5×3000) ran with **`loadgen: "hey"` for the first time**: 335.4 rps, 0 errors, p50 47.0 ms, `cp_dynamic_share_pct` **24.07** — within ~0.3 pp of the Python-generator medians, proving container-level energy attribution is loadgen-agnostic. (v) **coverage invariant** — the hey branch overwrote `wall` with hey's max-offset (last request *start*), making `sampling_covered_s` > `wall_s` and coverage read 103%; fixed by keeping the harness clock as the single attribution window (hey's duration exposed as `loadgen.wall_s`), restoring G3 coverage ≤ 100%, with the gates table now flagging any >100% as a hard break. (w) **`-t` units** — hey's `-t` is seconds; the raw ms value (30000 → 30,000 s) is now converted. Output renamed `hey.json` → `hey.csv`. G8 status: `hey_smoke_ok()` probes `-o csv` and **passes**.
>
> **v9.12 bare-metal session (2026-08-05):** (x) **`SAQEF_IDLE_W` knob** added to both runner scripts — the hardcoded `idle_w=30` was a 7× overestimate of the 8-core box's real 4.3 W idle package power, keeping `rapl_validation_err_pct` ≈ 45%; calibrated it to 4.2–8.2% steady-state on both platforms. Knob only; no measurement-path change. (y) **swarm advertise-addr fix** in `run_openfaas.sh` — `docker swarm init --advertise-addr 127.0.0.1` for multi-homed hosts. (z) **isolation pitfall recorded**: `docker stack rm openfaas` does not remove the `hello` function service (deployed outside the stack); its idle replicas fold into Fn's `fn_cpu` via the image allowlist with all gates still green. Protocol now requires `docker service rm hello` before any Fn run; one tainted Fn rerun was discarded (share 11.68 vs clean 10.46; its "0.9–6.1% RAPL" and "6.7/9.4 ms @ 577 rps" figures must NOT be cited).
>
> **v9.14 review findings — carbon unit bug + isolation-guard data-driven port (2026-08-06):** (cc) **carbon ×1000 unit bug fixed** — `carbon_gCO2` was computed as `(J/3600) × PUE × CI`: `J/3600` yields **Wh**, but `CI` is **gCO2/kWh**, so every gCO₂ figure (op/cp totals, idle_band, KPI, sensitivity `op_carbon_gCO2_by_busy_w`) was 1000× too high. Formula now `J/3.6e6` (kWh) before `× PUE × CI`. `energy_J`, `cp_dynamic_share_pct` (unit-free ratio), and `rapl_validation_err_pct` were never affected. §5.4/Table §7.2 values corrected (145 µg → 0.145 µg dynamic; 7.5 mg → 7.5 µg total); the v9.3-era 39.6 mg KPI is ÷1000 → ≈39.6 µg. (dd) **`assert_platform_isolation` made data-driven** — previously hardcoded elif for fn/openfaas (OpenWhisk fell through to `(True,"")`, a copy-paste-drift surface); now consumes the adapter's `--forbidden-services`/`--forbidden-containers` argv (manifest #1/#2), so every platform's isolation contract is enforced at measurement time. Harness now diverges from `saqef-v2.0-frozen` (carbon + isolation); new frozen tag required after test pass. (ee) **verify output namespace** — `cmd_verify` default now `results/<platform>_verify` (was the shared `results/`, which an OpenWhisk verify silently overwrote on the tracked `results/verify.json` working artifact). (ff) **stale faas-cli comment** corrected in `saqef deploy` (OpenFaaS uses direct `docker service create`, not faas-cli). (gg) **`run_openfaas.sh` replica default 4 → 16** (protocol is 16 static replicas; the drift default silently weakened the GIL-concurrency parity guarantee if `SAQEF_REPLICAS` was forgotten).


---

## 8. Measurement-validation discipline (methodological contribution)

The central methodological claim is not a number; it is that **every reported quantity has an independent cross-check, and the harness actively validates itself** rather than assuming its own correctness. Each measurement threat discovered during development produced a fix plus a gate that proves the fix, and the gates are now part of the output (a failed gate fails the run, not the narrative). This section is the paper's "how we know" appendix — it is what separates this framework from a script that prints plausible-looking numbers.

### 8.1 The validation gates (each number is double-checked)

| # | Reported quantity | Independent cross-check | Current status |
|---|---|---|---|
| G1 | control-plane CPU | cgroup **delta-check**: direct before/after counter summed across **all** CP containers vs the sampler's sum (re-resolved per read) | **0.00–0.01%** across all v9.7 runs; Fn single-container set unchanged by the v9.9 whole-set fix |
| G2 | host busy accounting | `host_plausible = host_cpu_sec ≤ cpu_count × host_window_s × 1.05` (v9.10: host's own sampling window, self-consistent); `host_saturation_pct = host/host_window` per core; v9.8 `host_saturated` flag = sat ≥ 85% | 99.9–100.3% (v9.7 window), plausible=true (saturated but *reproducibly*); v9.10 makes the check alignment-proof at short wall windows |
| G3 | sampling coverage | `sampling_covered_s / wall_s`; stop-time flush + clamp so it cannot exceed 100% | **100.0% on all 5** v9.7 runs |
| G4 | function classification | allowlist (names + image + label keys) with a logged **unclassified bucket**; fail-open (a configured-but-matching-nothing allowlist warns instead of reverting to the denylist) | `unclassified_cpu_s = 0.0` with 12/12 fn containers matched by image; no warning fired |
| G5 | load-generator identity | `env.loadgen` records the **actual** generator (`py`/`hey`), plus `loadgen_requested` and `loadgen_fallback` | v9.6+ truthful; a silent fallback is impossible |
| G6 | cross-session repeatability | multi-session median discipline; `cv_pct`/`iqr`/`bootstrap_ci` within a session, session medians across sessions | Fn bare-metal: 10.46 (quiet 2026-08-05) / 11.60 / 12.27 / 12.92 (same-day 2026-08-07 sessions) — bounded ~2 pp drift; 2-core sessions 13.91/14.08 (0.2 pp); OF 7.53–7.72 across sessions; OpenWhisk CV 2.1%. **2026-08-09:** refs re-anchored same-day under the self-certifying quiet gate (Fn 11.49 / OF 7.61, old-runner A/B) and `saqef regression` reproduces them (Fn 11.27, dev 0.22 pp; OF 7.40, dev 0.21 pp) — **PASS both**, G6 GREEN (§8.1) |
| G7 | KPI wall-independence | marginal (idle-excluded) KPI vs operational KPI; busy-power sensitivity band (2/3.5/5 W) | dynamic KPI invariant to window; share invariant to busy power |
| G8 | instrument/toolchain identity | `hey_smoke_ok()`: a candidate `hey` must emit a parseable `-o csv` report (`-n 2 -c 1 -o csv`: header + ≥1 data row) or it is wiped and reinstalled | **v9.9: passes** — first real `loadgen: "hey"` run; a stale/corrupted binary ≥1000 bytes can no longer be reused; python fallback still records truthfully |

### 8.2 What the discipline caught (bug taxonomy → fix)

1. **Rate-based CPU sampling was cadence-corruptible (the flagship bug).** The sampler computed percent rates with its own internal dt; the reducer re-multiplied by a different dt, and scheduling jitter produced spurious spikes — the control plane was attributed **5188% of a 2-core host's budget** (a hard physical impossibility, 52× overcount). The delta-check flagged it immediately. Fix: store raw cumulative counters and difference with true timestamps — **exact by construction, cadence-immune**. Post-fix delta-check: 0.01%. This is why sparse sampling on constrained environments still yields exact totals, and why the framework can run on a shared VM.
2. **Host accounting silently bled past the window.** The host counter was read *after* stopping the sampler thread; on a saturated box the thread can be starved up to the join timeout, inflating host CPU by up to 10 s. Fix: read the host counter before `stop`/`join`. Gate: G2 now reads 99.9–100.3% with zero >100.0% outliers, not the earlier 104–107% spread.
3. **Leftover containers folded into function CPU (between-session corruption).** Reused `fnserver` + orphaned warm fn containers inflated `fn_cpu` 4.2× (16.05 → 3.83 ms/inv). Fix: fresh-session protocol — every `all` removes the control plane and *all* deployed-image containers before starting. Gate: G6 (session medians now agree within noise).
4. **Denylist classification silently mis-attributes strays.** Without an allowlist, any stray container becomes "function." Fix: allowlist (names/images/labels) + unclassified bucket; plus **fail-open**: an allowlist that matches nothing warns and sends strays to unclassified rather than reverting. Gate: G4.
5. **Wrong allowlist default was *invisible*.** The default referenced the base runtime image while Fn's running containers carry the deployed image — the allowlist matched nothing and silently deactivated. Fix: default to the deployed image name + fail-open warning. Gate: G4 (no warning; 12/12 matched by image).
6. **Load-generator identity could lie.** The summary recorded the *requested* generator while the runs silently fell back. Fix: record actual + requested + fallback flag, and make every hey failure path print a reason. Gate: G5.
7. **Coverage tail truncated the window.** The sampler's last scheduled rescan could be starved past the window end (93.6%/92.9% coverage on two runs). Fix: stop-time flush + clamp. Gate: G3 (100% ×5).

### 8.3 Why this belongs in the paper

- **Self-validation is not decorative**: it caught a data-corrupting bug (G1) and four silent mis-attribution bugs (G3–G6) that would have polluted every platform's numbers with a clean-looking output.
- **Honesty is enforced by construction**: an unvalidated number cannot be emitted — if a gate fails, the run is flagged, not silently accepted. Remaining uncertainty is *named* (RAPL absent, host saturated, single platform), not hidden.
- **Reproducibility is checkable**: a reader with `run_saqef.sh all` gets the same gates, the same audit trail (`container_inventory`, `container_labels`, per-run `summary.json`), and can reject any run whose gates fail.

The framework was **frozen at v9.7**; further development stops unless a genuine bug surfaces (v9.8: two third-expert-verified gate gaps, §8.1 G2/G8; v9.9: three genuine bugs — hey's `-o csv` mode, the coverage/wall invariant, and `-t` units — in the previously-dead hey code path, §8.1 G3/G8). Everything measured after the freeze is comparable by construction.

---

## 9. Reproducibility

One-command pipeline (`run_saqef.sh`): `setup → check → verify → bench → gates`. Every `all` starts from a fresh session (`reset` removes a reused `fnserver` and orphaned function containers), so leftover-container pollution cannot recur.

```bash
chmod +x run_saqef.sh
./run_saqef.sh all
```

Artifacts per run: `summary.json`, `samples.csv`, `requests.csv`, `hey.csv` (raw `hey -o csv` when the hey loadgen is used), `verify.json`, `runs.json` (median + bootstrap CI + CV + IQR + spread). Environment snapshot (cpu_count, governor, freq, sampler, loadgen — including `loadgen_requested`/`loadgen_fallback`, container inventory/labels) recorded inside each summary. Stdlib-only Python; no pip installs. `verify.json` is a CPU-budget sanity check (`function_cpu_ms_per_inv` vs the deployed handler's spin), not a QoS measurement — its tail latency (cold first calls right after `fn deploy`) is not representative and must not be quoted.

Full command log and historical decisions: `SAQEF_TECHNICAL_REPORT.md` §§3, 4, 11–19.

---

## 10. Future Work

1. ~~**OpenFaaS** (same function image, same protocol)~~ — **done (2026-08-05)**: gap 2.8 pp on the 8-core box, 7.0 pp when the same box is pinned to 2 cores (per-machine-pair gate, §5.5).
2. ~~**OpenWhisk + Knative — cross-platform comparison**~~ — **done, four sessions culminating 2026-08-09**: four platforms on the same box (§5.6): OF 7.40 < Fn ≈ Knative 10.56–12.96 < OpenWhisk 82.36. QoS and (for Knative) energy/carbon are citable from the current session. Remaining for OW: decide how much of the standalone's per-activation log-store is "control plane" vs emulator artifact (unchanged across three sessions of varying box-state — see the reproducibility note in §5.6, which argues against it being mostly a deployment artifact). ~~Knative's idle-w calibration~~ — **closed (2026-08-09)**: the N≥3 repeated-read protocol (wraparound-guarded `rapl_w_series()`) measured 3.871 W bare / 4.561 W with `hello` @ 16 replicas (N=5 medians, spreads 0.25/0.41 W) — the "always-on idle premium" is **0.690 W**, now citable (§5.6).
3. ~~**Bare metal** (dual-boot Ubuntu) — RAPL ground truth~~ — **done (2026-08-05)**: RAPL-validated 4.2–8.2%, idle 4.3 W. Remaining: a *third* machine to bound the machine-dependence trend.
4. **Control-plane decomposition** — which fnserver subcomponent (gateway, scheduler, freeze manager, watchers) costs what.
5. **Fn-drift mechanism** — why fnserver's per-request CP cost drifts ~0.6→0.75 ms (and Fn's share ~10.5→12.9) run-to-run/session-to-session, and why Fn's share inflates +33% at 2 pinned cores while OF stays flat. `perf stat -e context-switches,migrations` on the fnserver process at 8 vs 2 cores (AGENTS.md future-work §B).
6. **Cold-start vs warm** — `--interarrival-ms 1000` isolation experiment: the "carbon cost of elasticity."
7. **Freeze-policy ablation** — `FN_FREEZE_IDLE_MSECS=0` vs default: quantify Fn's pause/unpause churn in energy terms.
8. **Realistic mixed workloads** — CPU/IO mixture, not just pure spin (subset of the workload-variation study, AGENTS.md future-work §A).
9. **A reference "floor" control plane** — nginx/haproxy in front of 16 static replicas of the same handler (no FaaS runtime) to anchor the platform shares to a lower bound: "real orchestration tax" = platform − floor (AGENTS.md future-work §C).
10. **OpenWhisk energy/carbon — a structurally separate open item.** The standalone JVM's power draw does not fit the linear busy-core model: `rapl_validation_err_pct` reads 31–50% (36% on the original run), stable across three independent sessions of varying box-state (contaminated 2026-08-07, quiet 2026-08-08, quiet 2026-08-08 evening) — the constant memory-touch load scales with neither cores nor concurrency. This is **not** the same class of gap as the idle-w calibration problem (§5.6 closed it for Knative); recalibrating idle-w would not close it. A JVM-aware energy model (or resolving the standalone's deployment-mode question, §5.6) is required before OW energy/carbon can be cited. It is deliberately out of `saqef regression`'s scope: that gate exists to prove the refactored CLI reproduces old-runner values for the two platforms with tight references (Fn/OpenFaaS), and OW has neither an old runner nor a calibration-gap diagnosis. `cp_dynamic_share_pct` remains citable for OW (pure cgroup CPU-time ratio, model-free).

---

## 11. Research impact & how the results can be used

- **Methodology reuse:** the harness + delta-check pattern is a drop-in instrument for anyone measuring FaaS energy on any platform (container-level, cgroup-validated, honest about estimation).
- **Informing models:** Kepler-style CPU-time proportionality gains a real orchestration-overhead term (Fn-class platforms ≈10–30% of dynamic energy depending on machine capacity; OpenFaaS-class ≈8–16%), so serverless energy models stop treating orchestration as ~0.
- **Carbon-aware scheduling:** a per-invocation orchestration cost (bare metal: Fn 0.66 ms CPU ≈ 2.3 mJ dynamic CP-only; OF 0.56 ms ≈ 2.0 mJ) makes it possible to route work to the cheapest control plane — and to price "green functions" correctly.
- **Design guidance:** autoscaling/scale-to-zero economics quantified — the always-on baseline is a large, often dominant slice of operational carbon (bare-metal Fn/OF 4.3 W idle; Knative's k8s-native stack carries a measured always-on idle premium of **0.690 W** over that floor — 4.561 W vs 3.871 W bare with `hello` @ 16 replicas, N=5 medians, §5.6), and the machine-dependence result says orchestration overhead is *capacity-bound*: it buys back fast when functions get more cores, so co-locating functions on fewer, larger boxes (or vice-versa) directly tunes the orchestration tax.
- **Framework portability (new):** the discriminator is a per-machine-pair quantity with a stable *ranking*; the paper provides the recipe (protocol + gates) so a third platform or machine can be ranked without re-deriving the methodology.

---

## Appendix A — Consolidated results table (four platforms, bare-metal 8-core; Fn/OF from the 2026-08-09 regression leg, Kn/OW from the 2026-08-08 evening rerun)

All rows are full REPEAT=5 runs on the same 8-core box with per-run gate tables (delta ~0,
coverage 100%, host_plausible true). This is the third and current snapshot of this table: it
supersedes both the 2026-08-07 agent-contaminated snapshot (Fn 12.27, OpenFaaS 7.53, Knative 13.99,
OpenWhisk 82.54) and the 2026-08-08 morning "quiet-box" snapshot (Fn 11.01, OpenFaaS 7.62, Knative
11.40, OpenWhisk 80.23), the latter collected before a same-day audit found and fixed a real
OpenFaaS/Knative classification collision (§5.6 isolation note). Fn's row is the 3-session median
(11.60, unchanged by today's leg; today's single-session value is 11.27). The Fn/OpenFaaS rows
were refreshed again by the **2026-08-09 regression leg** (quiet gate self-certified, ambient
9.9/11.7% of a 15% ceiling): Fn **11.27**, OpenFaaS **7.40** — reproducing the same-day
re-anchored references (11.49/7.61) within 0.25 pp, G6 GREEN (§8.1); the 2026-08-08 evening Fn/OF
values below (11.60/7.14) are the session snapshot, superseded for Fn/OF by the 2026-08-09 rows in
the table. **Do not cite the 2026-08-07 agent-contaminated or 2026-08-08-morning snapshots**;
they remain in git history for an appendix note on how the numbers evolved.

| Metric (median of 5) | Fn | OpenFaaS | Knative | OpenWhisk |
|---|---|---|---|---|
| `cp_dynamic_share_pct` | **11.60** (2026-08-09 leg **11.27**) | **7.40** (ref 7.61) | **12.44** | **82.36** |
| spread (min–max) | 10.56–12.96 (sessions) | 7.08–7.50 (2026-08-09) | 12.12–12.97 | 81.94–86.86 |
| per-request CP cost | 0.75 ms (session medians 0.72–0.82) | 0.53 ms | 0.96 ms | 26.82 ms |
| per-request fn cost | 5.64 ms (5.53–5.69 across sessions) | 6.59 ms | 6.79 ms | 5.75 ms |
| QoS p50 / p99 | 6.5 / 9.9 ms ✓citable | 7.0 / 13.1 ms ✓citable | 7.6 / 11.8 ms ✓citable | 113.6 / 182.0 ms ✓citable |
| throughput | 581 rps | 525 rps | 494 rps | 34.2 rps |
| SLO compliance | 1.0 | 1.0 | 1.0 | 1.0 |
| host_sat | 70.5–71.8% | 70.1–73.6% | 74–78% | 57–70% |
| RAPL validation err (this session) | 0.6–16.6% (run_1 degraded) | 0.6–18.9% (run_1 degraded) | 1.3–4.7% (all steady-state) | 31–50% (run_1 outlier 0.19%) |
| energy/carbon citable? | not from this session† | not from this session† | **yes** | **no** |

*Kn/OF fn cost includes the request-path sidecar/proxy inside the fn bucket (queue-proxy /
of-watchdog); OW CP cost is the standalone JVM incl. per-activation docker-log log-store
(deployment-mode caveat, §5.6). Knative's RAPL fit was better this session than either prior one
(1.3–4.7%, no warm-up transient) — a data point, not yet a trend, since only 3 sessions exist.
OpenWhisk's poor fit is stable across all three sessions regardless of box-state (structural JVM/
linear-model mismatch, not noise); run_1's 0.19% is an unexplained single-run outlier, not
investigated further. Full per-run tables: `SAQEF_TECHNICAL_REPORT.md` §30.1,
`results/{fn_cpubound_baremetal,openfaas_cpubound_baremetal,knative_cpubound_baremetal,
openwhisk_cpubound_baremetal,regression}`.*

**†Energy-citability footnote (do not conflate with the share number above).** Fn/OpenFaaS's
`cp_dynamic_share_pct` in this table comes from `saqef regression`, which reuses a config-file
`idle_w=4.3` calibrated on 2026-08-05/06 and never re-measures it. In the 2026-08-09 leg that value
was stale enough that run_1's RAPL validation is 16.6% (Fn) / 18.9% (OpenFaaS) — above the citable
range; runs 2–5 fit cleanly (≤12.1% / ≤14.8%). Fn and OpenFaaS's actual citable energy numbers
(RAPL err 4.2–5.5% / 4.2–8.2%) come from the separate, dedicated `fn_cpubound_baremetal` /
`openfaas_cpubound_baremetal` sessions in §5.1, which used a freshly-calibrated idle-w for their
own session. The share and the energy citability of Fn/OpenFaaS are therefore reported from two
different (but both fully-gated) sessions — a distinction that matters for any reader who wants to
cite absolute Joules/gCO₂ alongside this table's share row. `saqef gates`'s "RAPL FIT DEGRADED"
warning fired on run_1 of both platforms in the 2026-08-09 leg (and on 3/5 Fn / 4/5 OpenFaaS runs
in the 2026-08-08 evening leg) — confirmed working, and Fn/OpenFaaS's `idle_w` should be
recalibrated before the next session that wants to cite their energy from this table.

### Appendix B — Consolidated cross-platform table (bare metal, all regimes)

The regime table is necessarily Fn/OF in the 2-core column — only Fn and OpenFaaS were ever run
at 2-core pinning (the controlled core-count experiment, §5.5). OpenWhisk and Knative exist only
at 8-core bare metal (2026-08-08 evening rerun, current as of Appendix A; shares, QoS, and — for
Knative — energy are all citable from this session). The four-platform comparison lives in
Appendix A and figures 2–3.

| Metric (median of 5) | Fn (bare c=4) | OF (bare c=4) | Kn (bare c=4) | OW (bare c=4) | Fn (2-core pinned) | OF (2-core pinned) |
|---|---|---|---|---|---|---|
| `cp_dynamic_share_pct` | 10.46 | 7.67 | 12.44 | 82.36 | 14.00 | 7.00 |
| gap Fn−OF (pp) | **+2.79 (gate fails)** | | (3-session App. A: +4.46) | | **+7.00 (gate passes)** | |
| per-request CP cost | 0.66 ms | 0.56 ms | 0.96 ms | 26.82 ms | — | — |
| QoS p50 / p99 | 6.5 / 8.9 ms ✓ | 7.2 / 12.1 ms ✓ | 7.6 / 11.8 ms ✓ | 113.6 / 182.0 ms ✓ | not citable | not citable |
| throughput | 597 rps | 532 rps | 494 rps | 34.2 rps | — | — |
| SLO compliance | 1.0 | 1.0 | 1.0 | 1.0 | — | — |
| host_sat | 74–77% | 74–78% | 74–78% | 57–70% | 98.5–98.7% (saturated) | 98.5–98.7% (saturated) |
| RAPL validation err | 4.2–5.5% | 4.2–8.2% | 1.3–4.7% (all steady-state) | 31–50% | 43–60% (pin) | 43–60% (pin) |
| energy/carbon citable | yes | yes | **yes** | **no** | no (pin calibration) | no (pin calibration) |

*Fn/OF bare c=4 rows are RAPL-validated and contention-free (quiet 2026-08-05 baseline).
Kn/OW bare rows are the 2026-08-08 evening rerun (post classification-fix; box moderately loaded
for OW, host_sat 57–70%, still below the 85% contamination gate) — all QoS percentiles above are
citable (✓ flags); two earlier snapshots of these two rows (2026-08-07 agent-contaminated, and a
2026-08-08 morning "quiet" rerun collected before the OpenFaaS/Knative classification fix) are
both superseded (git history only, not cited). The 2-core pinned rows are the
controlled core-count experiment: full
REPEAT=5 protocol, all gates green on the share, but `host_saturated=true` (98.5–98.7%, expected
at c=4 > 2 cores) makes latency from that pair not citable, and energy is not citable without
re-deriving idle-w for the pinned configuration (RAPL err 43–60%, §5.5 caveat). Full per-run
tables: `SAQEF_TECHNICAL_REPORT.md` §30.1, `results/*_cpubound_*`.*

---

*Everything in this draft is traceable to `SAQEF_TECHNICAL_REPORT.md` and executable via `run_saqef.sh all`. Update this document as each [CANDID] gap closes.*
