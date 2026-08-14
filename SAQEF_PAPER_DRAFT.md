# The Hidden Cost of Orchestration: A Sustainability-Aware QoS Evaluation Framework (SAQEF)

**Working title — Paper Draft (v0.1)**

**Author:** [Name], Green Cloud Continuum project
**Date:** 2026-08-14
**Status:** Methodology validated on Fn + OpenFaaS and extended to OpenWhisk + Knative. CPU-time
shares are RAPL-validated on bare metal for Fn/OpenFaaS/Knative (8-core box + controlled 2-core
regime); the OpenWhisk standalone's energy model has a structural mismatch (§5.6), so its energy is
reported as model-estimated only, not RAPL-validated. Draft for refinement into the final research
paper.
**Source of record:** `SAQEF_TECHNICAL_REPORT.md` (full measurement log) + `saqef_harness.py` (instrumentation) + `run_saqef.sh` (one-command reproduction).

> **How to use this document.** This is the paper narrative. Every claim is traceable to the
> technical report and to the committed result data (`results/*_cpubound_*/runs.json`), and every
> reported number is recomputable from those files. Where a claim is scoped (single machine,
> standalone-emulator deployment, model-estimated energy), the scope is stated in place rather
> than repeated in a separate limitations section.

---

## Abstract

Serverless computing shifts infrastructure management to platform operators, but the *orchestration overhead* — the control-plane work that schedules, freezes, and coordinates function invocations — is rarely charged to the function. This paper presents **SAQEF**, a sustainability-aware QoS evaluation framework that attributes CPU time and energy to the control plane versus the function under controlled load, cross-validated against direct kernel counters and, on bare metal, against RAPL (median validation error 3.4% Fn / 2.3% OpenFaaS on the matched session; full 5-run ranges in §5.6). We apply it to four serverless platforms (Fn, OpenFaaS, Knative, and the OpenWhisk standalone) serving an identical CPU-bound 5 ms function, with all four platforms' sessions drawn from one matched, quiet-gated, single-day session (2026-08-14), each with a freshly calibrated energy baseline, so the ordering is directly comparable rather than stitched across days.

The headline finding is that the control plane's share of dynamic CPU — and therefore the platform gap — is a property of the machine's core count, not of the platform alone. On an 8-core box, Fn's control plane consumes **10.5%** of dynamic CPU versus OpenFaaS's **7.7%** (gap 2.8 pp, below our a-priori 5 pp gate). Cpuset-pinning the same box to 2 cores — same protocol, same instrument, reproduced to within 0.2 pp in two repeated sessions — raises Fn to **14.0%** while OpenFaaS stays at **7.0%** (gap 7.1 pp): core scarcity inflates Fn's central-orchestrator overhead specifically, an asymmetric, platform-specific sensitivity. Across the four platforms the container-visible control-plane share spans an order of magnitude — **OpenFaaS 7.58 < Fn ≈ Knative (11.29 / 11.47) < OpenWhisk (standalone) 81.78** — and survives both of the metric's own known sensitivities (the CP/fn classification boundary and the function-side denominator; §5.6): the Fn–Kn tie is convention-sensitive (classify Knative's queue-proxy as control plane and it separates to 24.8%), while OF < {Fn,Kn} << OW survives every plausible reclassification. OpenWhisk's share has stayed within ~2 pp across five independent sessions; we make no claim about its distributed/production deployment mode, which was not measured. **The 5 pp gate is a per-machine-pair quantity, not a platform constant**; the machine-dependence and its asymmetry are the central contributions. A built-in delta-check caught and eliminated a 52× sampling overcount during development, demonstrating that the validation approach works as intended.

---

## 1. Introduction & Motivation

Cloud computing consolidated physical servers into pooled, elastic resources; containers then
standardized the deployment unit so applications could move between hosts as one artifact; and
serverless (FaaS) platforms took the final step of the abstraction ladder — removing the server
from the developer's model entirely. The operator's promise is that "the platform" absorbs the
infrastructure: gateways, schedulers, coordinators, cold-start machinery, and scale-to-zero
resource reclaim, all so that the developer pays only for what executes, per invocation. That
promise is real, but it does not make the platform's work disappear — it *relocates* it into an
always-on control plane whose cost is rarely visible to the developer who is billed per
invocation, and rarely visible to the operator who quotes an idle CPU percentage.

Their environmental cost is hidden in two places: (i) the **idle baseline** of always-on
gateways, schedulers, and coordinators, and (ii) the **dynamic overhead** of orchestrating each
invocation — route lookups, container spawn/freeze churn, queueing, and watchers. When operators
report "the control plane uses ~2% of CPU," they are describing the *static* fraction of total
machine capacity — which, on an idle-leaning, co-tenanted VM, obscures the fact that the marginal
work attributable to a function is disproportionately orchestration. As data-center electricity
grows, the question "what does the control plane actually cost, per unit of useful work?" becomes
a sustainability question, not just an accounting one. This paper answers it with a measurement
framework: it attributes CPU time and energy between the control plane and the function under
controlled load, and validates the attribution against independent kernel and RAPL counters rather
than asserting it.

**Central claim:** for a light CPU-bound function, the control plane is not a rounding error — on an 8-core box it is ~10% of the marginal (dynamic) CPU cost for Fn and ~8% for OpenFaaS, and its share — and the platform gap — grows as the machine gets smaller (Fn 14.0% vs OpenFaaS 7.0% when the same box is pinned to 2 cores; see §5.5). Orchestration is a first-class, capacity-dependent cost, not an overhead line item.

**Contributions:**
1. A reproducible, platform-agnostic measurement harness (`saqef_harness.py`) that attributes CPU/energy between control plane and function under controlled load, with built-in self-validation (delta-check, host-plausibility, coverage, platform-isolation assertions).
2. A workload-anchored methodology that fixes the "ratio of tiny quantities" instability that plagues no-op-workload energy comparisons, plus a frequency-invariance argument: cgroup CPU-time ratios are invariant-TSC wall-time, so the headline share is robust to per-core frequency/turbo differences by construction (§5.5 caveat, report §31.8).
3. The first RAPL-validated cross-platform control-plane overhead numbers on bare metal, with the core-count dependence **quantified by a controlled same-instrument experiment on a single physical host** (8-core i5-1145G7, core count varied by cpuset restriction rather than a second machine): Fn 10.5% vs OpenFaaS 7.7% of dynamic CPU at 8 cores (gap +2.8 pp, below the 5 pp gate), rising to Fn 14.0% vs OpenFaaS 7.0% (gap +7.0 pp, reproduced to 0.2 pp in two repeated sessions on the same instrument) when the same box is cpuset-pinned to 2 cores — an **asymmetric, platform-specific core-scarcity sensitivity** (Fn's share inflates, OpenFaaS's stays flat). The core-restriction design keeps the instrument (CPU model, DVFS, NUMA, thermal envelope) fixed and isolates the core-count variable, but a genuinely distinct second physical machine remains future work and would bound the generalizability (T5V #8).
4. A documented case study of the validation method catching a real 52× instrumentation bug (§8).
5. For sustainable serverless computing: the finding that **platform overhead shares and the gap between platforms are machine-pair properties, not platform constants** — the widely used 5 pp discrimination threshold fails/passes depending on the host's core count, so carbon-aware scheduling and "green function" claims must be evaluated per machine-pair with a citable per-machine-pair gate, not a global constant (§5.5, §7).

---

## 2. Background & Related Work

**Serverless overhead and cold starts.** It is well established that FaaS orchestration is not
free. Measurement studies of serverless platforms show that orchestration logic and container
management add latency and resource consumption on the request path (Wang et al., "Peeking Behind
the Curtains of Serverless Platforms," 2018), and that idle resource provisioning — the price of
being ready to serve — dominates the resource profile of a running platform (Shahrad et al. on
idle resources, 2020). Cold-start studies further quantify the latency of waking a function. What
these works share is a focus on *latency* or *aggregate* resource use: they establish the
phenomenon, but they do not *attribute* CPU or energy between the control plane and the function
with an independent validity check — which is precisely the granularity an operator needs to price
"green functions."

**Energy measurement methodology.** The energy side of serverless has received less attention.
Direct instrumentation studies such as FaasMeter (Fan et al., IC2E 2024) measure marginal FaaS
energy from first principles and inspired our self-validation pattern (`--delta-check`,
`--idle-probe`). Kernel-counter-proportional estimation — the Kepler/eBPF family — derives
per-process energy from CPU-time proportionality, which is the model family our CPU-time
attribution follows, with the per-core dynamic power constant (3.5 W/busy core) drawn from
Caribou's fine-grained multicore energy measurements (SOSP 2024). On bare metal, Intel RAPL
provides package-level Joules as ground truth for model validation, which is how we turn a
CPU-time model into a *checked* model rather than an assumption.

**QoS-aware and carbon-aware scheduling.** A growing body of work treats QoS and energy/carbon as
co-optimization targets, routing work to "greener" regions or clusters. These approaches need a
per-function, per-platform marginal cost as input — but in practice they estimate it from
aggregate power or published static figures, because measured control-plane attribution does not
exist. Serverless benchmarking frameworks likewise standardize *function* performance but not the
orchestration tax around it.

**The gap we target.** Most prior work reports QoS (latency/throughput) and/or aggregate energy,
but rarely **attributes energy between the control plane and the function with an independent
validity check** — and none reports how that attribution changes with machine capacity. SAQEF is
a reproducible, platform-agnostic measurement harness that fills exactly this gap: per-container
cgroup CPU attribution with a direct-counter delta-check, RAPL validation on bare metal, and a
protocol that makes every number traceable to a fully-gated session.

---

## 3. Research Questions

We ask three questions that move from methodology, to measurement, to discrimination — each
answerable only if the previous one is settled:

- **RQ1 (methodology):** Can container-level sampling attribute marginal CPU/energy to the control plane vs the function with a *verifiable* error bound?
- **RQ2 (measurement):** What fraction of dynamic CPU/energy does a serverless platform's control plane consume for a CPU-bound function under a realistic load? — answered first for Fn as the anchor platform (§5.1–5.4), then across all four platforms in a matched session (§5.6).
- **RQ3 (comparison):** Does the framework discriminate between platforms (Fn vs OpenFaaS)? — **answered (2026-08-06, core-count confirmed):** yes, with a caveat. On the 8-core bare-metal box the gap is 2.8 pp (gate fails), flat with concurrency within the box. A controlled same-instrument test (this 8-core box cpuset-pinned to 2 cores) confirms the gap is core-count-driven: it returns to 7.0 pp at 2 pinned cores. Direction is stable (Fn's share higher everywhere); the *magnitude* is a machine-pair property, and the mechanism is asymmetric — Fn's share is what inflates under core scarcity, not both platforms proportionally (see §5.5). Extended to four platforms in a matched session (2026-08-14 lock4, §5.6): the
container-visible control-plane share spans an order of magnitude — OpenFaaS ~7.3–7.6 < Fn ≈
Knative ~11.0–12.3 < OpenWhisk ~80.2–82.5 (attribution conventions differ; see §5.6 map).

Together, the three answers deliver a transferable, self-validating instrument (RQ1), the first
RAPL-validated control-plane overhead numbers across four platforms (RQ2), and a
per-machine-pair discrimination gate rather than a platform constant (RQ3).

---

## 4. Methodology

### 4.1 Design overview

This section defines the measurement pipeline and the two metrics it produces; §4.3–4.4 detail
each channel. One run of the protocol is a single **measurement window** built from three
synchronized channels: **(a) QoS load generation** — a load generator issues a fixed count of
requests to the deployed function and records per-request latency and throughput, i.e. what the
user experiences (§4.3, §4.4); **(b) per-container CPU sampling** — a background sampler reads
raw cumulative CPU seconds for every container on the host, which the reducer later attributes
between control-plane and function buckets, i.e. what the platform does (§4.4); and **(c)
pre/post direct-counter reads** — the cumulative control-plane counters are read *directly*
immediately before and after the window and compared against the sampler's accumulated total, an
independent validation that the whole sampling path is exact rather than assumed (§4.4,
`--delta-check`). The window is
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

**Denominator caveat — `cp_dynamic_share_pct` is not function-cost-independent.** The share's
denominator is `cp_cpu_s + fn_cpu_s`, so a platform whose *function-side* runtime happens to cost
more CPU per invocation lowers its own share even if control-plane overhead is identical —
the metric conflates "how much does orchestration cost" with "how much does everything else cost
relative to it." We measure meaningfully different function-side costs across platforms in this
study (Fn 5.66 ms, OpenFaaS 6.62 ms, Knative 6.82 ms, OpenWhisk 5.72 ms per invocation, running
the *identical* 5 ms handler — §5.6), so part of the cross-platform spread in
`cp_dynamic_share_pct` is attributable to runtime/sandbox differences on the function side, not
orchestration alone. We therefore report **per-invocation control-plane CPU time (ms)** as a
co-headline metric alongside the percentage throughout this paper — it is denominator-free and
answers "how many CPU-ms does the control plane spend per request" directly. As a sensitivity
check, normalizing every platform's function-side cost to a common flat 5 ms and recomputing the
share from each platform's own measured CP-ms/invocation gives OpenFaaS 9.81%, Fn 12.60%,
Knative 15.01%, OpenWhisk 83.69% (vs. the actual 7.58/11.29/11.47/81.78, lock4 session
§5.6) — the absolute
percentages shift by several points, but **the ordering OpenFaaS < Fn ≈ Knative << OpenWhisk is
unchanged**, which is the strongest evidence available that the paper's central ordering claim is
not an artifact of function-side cost differences. This check and its numbers are reported in
full in §5.6.

### 4.2 Environment

*Table 1 — Hardware and environment (origin instrument vs the 8-core bare-metal box that produced all reported numbers).*

| | Value (codespace — origin instrument, no results cited) | Value (bare metal, 2026-08-05) |
|---|---|---|
| Host | GitHub Codespaces, x86_64, 2 vCPU, Docker 29.3.0, Python 3.12 | 8-core Ubuntu, 16 GB, docker + swarm, RAPL readable |
| CPU | 2 shared vCPU (cloud VM, co-tenanted) | 11th Gen Intel Core i5-1145G7 @ 2.60 GHz — 4 cores / 8 threads, 1 socket, turbo to 4.4 GHz, governors ondemand/performance (3.30 GHz loaded at c=4; 3.60 GHz when pinned to 2 cores, report §31.8) |
| Platform | Fn — `fnproject/fnserver:latest` (0.3.x), containerized, iofs socket fix | Fn 0.3.x; OpenFaaS 0.8.3 (6-container CP, of-watchdog); OpenWhisk standalone:nightly; Knative Serving v1.23 + Kourier on k3s v1.36.3 |
| Function runtime | Python 3.12 FDK (`fnproject/python:3.12`) | Python FDK (hello:0.0.7 Fn / hello:latest OF; identical 5 ms spin handler on all four platforms) |
| Load generator | `hey` (Go) preferred; Python stdlib fallback | `hey -o csv`, TOTAL=10000, warmup 20 |
| RAPL | Unavailable (cloud VM) → CPU-time model | **Available** → model RAPL-validated, full 5-run range 4.2–36.1% (idle 4.3 W; see §5.5 caveat — error correlates with run length, not a platform difference) |

> **Hardware-dependence — explicit.** All absolute values (share, gap, per-request ms) are
> machine-pair-specific: the headline numbers would differ on a different core count, CPU model,
> DVFS policy, or co-tenancy regime — this is the *point* of §5.5's cross-regime table, not a
> caveat swept under the rug. What generalizes is the **framework and the per-machine-pair gate**:
> the protocol records the machine's `cpu_count`, governor, and frequency in every `summary.json`,
> and the 5 pp decision is re-derived per host pair rather than taken as a platform constant. A
> reader applying the method to their own hardware gets citable, machine-local numbers — the same
> instrument, re-anchored.

### 4.3 Workload

`hello/func.py` is a genuine **5 ms CPU spin** (`while time.perf_counter() - t0 < 0.005: pass`). It is deliberately a **stress-test metric, not a realistic-service mix**: `cp_dynamic_share_pct` is designed to isolate orchestration tax from IO-wait masking — a function that blocks on IO spends most of its measured "fn CPU" waiting, which dilutes the control-plane signal and undercounts per-request orchestration on every platform. The 5 ms spin makes the function's marginal CPU measurable and stable, so what varies across platforms is almost entirely the orchestration around the invocation rather than the handler itself. A no-op "hello" handler was evaluated and rejected: with a near-free function, both ratio numerator and denominator become tiny and noisy (fn freeze churn between calls → 0% CPU → `cp_dynamic_share` swings ±13 pp). **The metric must be workload-anchored** — itself a reviewer-relevant methodological finding.

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
embodied_DRAM = 1390 gCO2/GB ÷ (5 yr × 8760 h)                # amortized; reported for context only
```

**Scope of the carbon numbers.** This study reports **operational (power-on) carbon** only.
Embodied carbon is restricted to a single context line for the amortized DRAM share (above) and is
not included in any reported total; a full lifecycle assessment is out of scope. This is stated
here so that no reader mistakes the operational totals in §5.4/§5.6 for whole-lifecycle figures.

Constants: `P_BUSY_CORE_W = 3.5`, `PUE = 1.15`, `CI = 150 gCO2/kWh`, `SAMPLE_S = 1.0`. The idle
baseline `P_IDLE_BASE_W` is **calibrated per platform, per session** (it is box-state, not a
platform constant — see below) from a 60 s RAPL read with the stack up, zero traffic: Fn/
OpenFaaS 4.3 W (2026-08-05/06 baseline session, still valid for their citable energy numbers;
**recalibrated fresh per-leg on the 2026-08-14 lock4 session — OpenFaaS 4.235 W, Fn
4.249 W, Knative 5.739 W**, §5.6 table);
OpenWhisk 5.294 W → 3.889 W → 4.873 W across three sessions (lock4: 4.882 W); Knative 11.138 W → 7.007 W → 4.906 W
across three sessions, superseded on 2026-08-09 by the N=5 protocol (§5.6): 3.871 W bare /
4.561 W with `hello` at 16 replicas (median, spreads 0.25/0.41 W), re-confirmed by lock4's N=5
per-leg recalibration (bare 4.084 W / Knative 5.739 W). The model default of 30 W is
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
comparator on identical hardware; (c) RAPL-validated on bare metal, full 5-run range 4.2–36.1%
for Fn/OpenFaaS (median 18.7% / 8.2% respectively) — the model fits tightly (4.2–8.2%) on 3 of 5
runs per platform and loosely (18.7–36.1%) on the other 2 (both platforms' earliest, shortest
runs), consistent with a fixed `idle_w=4.3` constant under-fitting short/light runs where the
idle term dominates less of the true energy budget (§5.5's "corroborating note" gives the
directional evidence: longer 2026-08-09 regression runs fit far more consistently under the same
stale constant). We report the *whole* distribution here rather than only the well-fit subset —
citing "4.2–8.2%" without the other three runs materially overstates how tightly this session's
energy model is validated.

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

*Table 2 — Validation gates asserted on every run of every platform.*

| Gate | Definition | Accept |
|---|---|---|
| **Ambient-load quiet gate** | `ambient_load_check()` samples whole-host busy CPU over a 20 s window immediately before every bench (`ambient.busy_pct`) and records the top-CPU `ps` snapshot (`ambient.top_cpu`); **refuses to start** above `--max-ambient-cpu-pct` (default 15% of a host core). The reading and top-CPU snapshot land in `summary.json` → `ambient`, so a citable run **self-certifies a quiet box** | `busy_pct ≤ 15%`; exceeded → run aborted, `--no-quiet-gate` (exploratory/contamination-A/B only) |
| Delta-check | `cp_sampler_vs_delta_pct` | ≈ 0 (single-digit %) |
| Physical plausibility | `cpu_sec.fn + cpu_sec.cp ≤ cpu_count × wall_s` | true |
| **Host plausibility** | `host_cpu_sec ≤ cpu_count × host_window_s × 1.05`, where `host_window_s` is the host's *own* sampling window (`t_host_after − t_host_before`), not the load `wall_s` — self-consistent by construction: `/proc/stat` busy ticks over a window `W` can never exceed `cpu_count × W`, so the gate trips only on a real CPU-count/counter anomaly, never on fast-run window-edge alignment | true |
| **Host saturation** | `host_cpu_sec / (cpu_count × host_window_s)` | report per run; `host_saturated` flag if ≥ 85% — QoS is contention-contaminated |
| Coverage | `sampling_covered_s / wall_s` | ≥ 95% (bare-metal target) |
| QoS integrity | availability, SLO compliance | ≥ 99% |
| Determinism | two independent runs reproduce | within repeat variance |

**The quiet gate is a core contribution, not a detail.** An agent-style background load (e.g. a
code agent at ~2.8 cores, the contamination that invalidated the 2026-08-07 snapshot) inflates
`host_saturation_pct` and can drift `cp_dynamic_share_pct` by up to ~+2 pp on a central-
orchestrator platform (§7.5's A/B). The gate replaces the manual "quiet box" hope with a measured,
data-carried assertion: the pre-run reading and top-CPU snapshot are committed in every
`summary.json`, so a citable run's quietness is checkable by any reader, and a non-quiet box
refuses to produce one. The idle-probe calibration (idle-w, §4.5) is exempt — there the platform
stack itself is the subject under measurement. The measured contamination bound behind the gate's
15% ceiling is documented in §7.5.

---

## 5. Results (bare metal, RAPL-validated)

**How to read this section.** §5.1–5.4 work the measurement pipeline end-to-end on a single
platform — **Fn** (the fnproject platform, capitalized throughout) — using the original fully
RAPL-validated bare-metal baseline session (2026-08-05). The validation gates and attribution
math are identical on every platform, so one worked example suffices; §5.5 then applies the same
protocol to the Fn-vs-OpenFaaS core-count comparison, and §5.6 extends the comparison to all four
platforms in the matched lock4 session (2026-08-14). As a naming convention, *Fn* names the
platform and *fn* (lowercase) names the function-side CPU bucket — the deployed handler plus its
co-located request-path proxy (see the attribution map in §5.6).

### 5.1 QoS (Fn worked example)

*Fn platform, 2026-08-05 bare-metal baseline session (the worked-example run). Cross-platform QoS
is reported in §5.5–5.6; the full four-platform comparison is §5.6 / Table 9.*

*Table 3 — QoS (Fn worked example, 2026-08-05 baseline).*

| Metric | Value |
|---|---|
| Requests / success | 10000 / 10000 (availability 1.0) |
| Throughput | 597 rps |
| Latency p50 / p90 / p99 / max | 6.5 / 7.1 / 8.9 / 46.2 ms |
| SLO compliance (500 ms) | 100% |

### 5.2 Energy & CPU attribution (worked example — Fn, 2026-08-05 baseline, median run)

Worked example from the validated bare-metal Fn run (median of 5), illustrating the
attribution math; the cross-platform comparison is §5.5.

*Table 4 — Energy & CPU attribution (Fn worked example, 2026-08-05 baseline, median run).*

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

### 5.3 Validation results (worked example — Fn, 2026-08-05 baseline; the same gates are asserted on every run of every platform in §5.5–5.6)

*Table 5 — Validation results (Fn worked example, 2026-08-05 baseline).*

| Gate | Result |
|---|---|
| `cp_sampler_vs_delta_pct` | **0.00%** (sampler = direct counter) |
| `cp_delta_sec` vs sampler CP | 6.565 ≈ 6.56 s (exact) |
| `physical_plausible` | true on all 5 runs |
| `host_plausible` / host_sat | true / 74.3% (`host_saturated=false`) |
| coverage | 100.0% on all 5 runs |
| Reproduction | cross-session: 10.46 (quiet 2026-08-05) vs 11.60–12.92 (same-day drift, §5.5) |

### 5.4 Absolute per-invocation overhead (model-based; Fn worked example)

*Table 6 — Absolute per-invocation overhead (model-based; Fn worked example, 2026-08-05 baseline).*

| Quantity | Per invocation |
|---|---|
| Control-plane CPU | 6.56 s / 10000 = **0.66 ms CPU** |
| Control-plane dynamic energy | 23.0 J / 10000 = **2.30 mJ** |
| Control-plane carbon (dynamic) | ≈ **0.11 µg CO₂** |
| Total operational carbon (incl. idle base) | ≈ **1.40 µg CO₂** (idle ~25%) |
The idle-dominance is itself a result: at this light load, **~25% of operational carbon is the always-on baseline**, and the marginal cost of serving is split ~10/90 orchestration/function (0.66 ms CP vs 5.62 ms fn per invocation). This is precisely the regime where autoscaling ("scale to zero") pays off — and where the orchestration tax is most visible per unit of useful work.

### 5.5 Core-count dependence — Fn vs OpenFaaS (controlled same-instrument experiment, 2026-08-05/06)

**Figure 1 — The core-count effect (Fn vs OpenFaaS, same instrument).** Median `cp_dynamic_share_pct` (bar = reported median, error bars = full per-run spread across all sessions), 8 cores vs the same box cpuset-pinned to 2 cores, all validation gates green, reproduced in two repeated sessions on the same instrument. The dashed line is the 5 pp a-priori decision gate; arrows mark the Fn−OF gap. Reading: the gap (2.79 → 7.00 pp) is core-count-driven, and the 5 pp gate is a *machine-pair* property, not a platform constant. Data: `results/{fn,openfaas}_cpubound_baremetal` and `*_2core{,,_session2}`.

![figure1](figures/figure1_core_count.png)

*(Figure 2 — per-run shares and Figure 3 — attribution split appear in §5.6, where the
four-platform data they plot is introduced.)*

Same protocol on an 8-core Ubuntu box (RAPL-validated, idle 4.3 W): Fn vs OpenFaaS serving the
identical 5 ms CPU-bound function, `c=4 < cpu_count=8`, `TOTAL=10000`, 5 runs, 16 static OF
replicas, all gates green (`host_saturated=false`, delta-map 6/6, coverage 100%).

*Table 7 — Fn vs OpenFaaS on the 8-core box (c=4, REPEAT=5, 2026-08-05 baseline session).*

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
| RAPL validation err, all 5 runs (median) | 4.20–36.07% (18.66%) | 4.17–34.55% (8.20%) |

**RAPL validation caveat.** Per-run: Fn 36.07/19.10/18.66/5.47/4.20%, OpenFaaS 34.55/25.94/8.20/
5.16/4.17% (`runs.json`, `idle_w=4.3` throughout). Only 3 of 5 runs per platform land in a
"steady-state" 4.2–8.2% band; the other 2 (each platform's earliest runs) read 18.7–36.1%. This
is **not cited as validation-quality evidence** — the median (18.66% Fn, 8.20% OpenFaaS) is what
a reader should treat as this session's actual RAPL agreement, and even that is a looser bound
than the framework achieves under longer/heavier load (§5.5's corroborating note: the same
`idle_w=4.3` fits the 2026-08-09 regression session's longer runs to single digits on all but one
run per platform). `cp_dynamic_share_pct`, the paper's headline metric, does not depend on
`idle_w` at all (it is a pure cgroup CPU-time ratio, §4.1) and is unaffected by this caveat; only
the absolute Joule/gCO2 figures for this specific session inherit the wider error bound.

**Concurrency sensitivity (c=8, REPEAT=2, quick check):** Fn 10.47, OpenFaaS 7.62 at 91–93% host
saturation — the share and the gap look **flat with concurrency within the box** (gap 2.79 → 2.85
pp). REPEAT=2 is sufficient to confirm flatness against the already-validated c=4 baseline but is
**not yet the basis for a citable headline number** — bump to REPEAT=5 before this row is cited
standalone in the paper.

**Cross-regime reading (central contribution — core-count effect CONFIRMED 2026-08-06 with a
controlled, same-instrument experiment):**

*Table 8 — Core-count dependence: the same 8-core instrument, 8-core native vs cpuset-pinned to 2 cores (controlled same-instrument experiment).*

| regime | machine | Fn | OpenFaaS | gap |
|---|---|---|---|---|
| headroom, clean | 8-core, c=4 | 10.46 | 7.67 | +2.8 pp |
| saturated, clean | 8-core, c=8 | 10.47 | 7.62 | +2.9 pp |
| saturated, clean, SAME instrument, 2 repeated sessions | 8-core box cpuset-pinned to 2 cores, c=4 | 14.00 (13.91/14.08) | 7.00 (6.82/7.17) | +7.0 pp (6.91/7.09) |

The last row is the controlled confirmation: same box, same corrected protocol (fixed spin,
RAPL-calibrated idle-w, `--cpu-count-override`/`--host-cpu-list` so the saturation gate is scoped
to the 2 pinned cores, not the whole 8-core machine), REPEAT=5, all citability gates green — only
the core count changed. It was reproduced in a full repeated second session (fresh
teardown/redeploy/re-pin; Fn's rebuilt image even changed tag, 0.0.10→0.0.11, with no effect since
the pinning daemon targets "every running container," not an image tag) — the two sessions, each
at the full REPEAT=5 protocol with per-run gate tables, give session gaps of 7.09 and 6.91 pp,
reproduced to within 0.2 pp. The gap jumps from 2.8–2.9 pp at 8 cores to ~7.0 pp at 2
cores. **Core count driving the gap's magnitude is therefore an earned, reproduced finding,
not an inference across mismatched instruments.**

**Why 5 pp is a defensible decision gate (fixed before any data).** The gate was chosen a priori,
before the first four-platform measurement, on the argument that a platform gap must exceed the
repeatability of the underlying measurement or it is not a discriminable difference. That
condition is satisfied here with headroom: the gate (5 pp) is 2–3× the largest documented
within-box cross-session drift of a single platform's share (~2 pp, the Fn 10.46→12.92 range) and
well above the within-session repeatability (per-platform CVs 1.7–6.4%, i.e. ±0.1–0.7 pp per run).
A gap crossing 5 pp is therefore not attributable to box noise or run-to-run spread; a gap below
it (like Fn–OF at 8 cores, 2.8 pp, within ~1.5× of the same drift) is correctly read as "not
discriminable on this machine pair." The gate is explicitly a per-machine-pair quantity, not a
platform constant — a claim the 2-core row demonstrates directly.

The mechanism is *not* symmetric. The controlled data shows an asymmetric effect: Fn's
share rose sharply under core scarcity (10.46 → 14.00, +34%) while OpenFaaS's share was flat to
slightly lower (7.67 → 7.00, −9%). Why Fn's control-plane overhead specifically is
sensitive to core scarcity is observed, not yet mechanistically explained. The leading
hypothesis — and it is a *testable* one, not a conclusion — is that a single central orchestrator
on the request path (fnserver) starves for scheduler time under thread contention at 2 cores,
whereas OpenFaaS's per-replica of-watchdog proxies are co-located with their function and thus
incur no extra scheduling competition; a direct probe (`perf stat -e context-switches,migrations`
on the fnserver process at 2 vs 8 cores) would confirm or refute it, and is future work, not a
claim. Because
OpenFaaS's share is also a conservative bound (of-watchdog proxies inside the function cgroup),
its true share (and the true gap) is smaller still in every regime. **The 5 pp a-priori decision
gate is a machine-pair property, not a platform property** — the paper reports per-machine-pair
gates and presents both the machine-dependence and its asymmetry as findings. The threshold
itself (5 percentage points) was fixed *a priori*, before the data: it is the gap the study was
designed to detect, chosen to be small enough to separate platforms but large enough that a
pass/fail is not noise-dominated at N=5 (per-run CVs are ≈1–8%, so 5 pp is several CVs above the
measurement spread). It is a discrimination threshold for the study question, not a value fit to
the data.

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

### 5.6 Four-platform comparison (same 8-core box, c=4, REPEAT=5; lock4 session 2026-08-14 — all four platforms back-to-back the same day under the quiet gate)

**Figure 2 — Per-run shares, four platforms (8-core box; the matched lock4 session 2026-08-14, all four platforms back-to-back the same day; see the data-source note in Appendix A).** Every per-run value shown (n=5 per platform, one matched session), black tick = reported median. The platform ordering is the robust finding: OpenFaaS 7.58 < Fn ≈ Knative 9.82–12.31 (11.29 / 11.47) < OpenWhisk (standalone) 81.78 (attribution conventions differ — see the map below). OW's 81.8 uses the same y-axis; the scale is dominated by it, which is itself the point (an order-of-magnitude orchestration cost).

![figure2](figures/figure2_four_platform_scatter.png)

**Figure 3 — Attribution split (four platforms, 8-core box).** Per-run median dynamic CPU time decomposed into control-plane (solid), function (translucent), and unclassified (grey) bars, with the CP share labeled on top. Reading: the control plane is 7.6–11.5% of the marginal CPU time on all three lightweight platforms (OF/Fn/Kn) but ~81.8% on the standalone OpenWhisk emulator, whose single JVM orchestrator + per-activation docker-log store dominates (deployment-mode caveat, below).

![figure3](figures/figure3_attribution_split.png)

**Figure 4 — Control-plane CPU per invocation (ms CPU / request), four platforms.** Median CP CPU per run ÷ 10,000 requests (per-platform median of the lock4 session). Reading: of-watchdog (per-replica proxy inside the function cgroup) 0.54 ms ≈ fnserver (dedicated broker process) 0.72 ms ≈ Knative 0.88 ms (knative-serving + kourier gateway + activator), while the standalone OpenWhisk JVM costs ~25.7 ms — a ~29–47× orchestration tax per request vs the lightweight platforms. Data: `results/{openfaas,fn,knative,openwhisk}_cpubound_lock_lock4` (2026-08-14).

![figure4](figures/figure4_cp_cost_per_inv.png)

*(PDF: `figures/figure*.pdf`; regenerate with `python3 figures/make_figures.py` — data-driven, no script edits.)*

OpenWhisk (standalone) and Knative (Serving v1.23 + Kourier on k3s v1.36, docker runtime) were
added to the same protocol. All rows are full REPEAT=5 runs with per-run gate tables (delta ~0,
coverage 100%, host_plausible true). **Read the attribution map and the citability notes below
before citing the absolute values — the *ordering* is the robust finding.**

*Superseded numbers, not to be cited:* four earlier snapshots of this table are retired, each for a
documented reason: the 2026-08-07 snapshot (7.53 / 12.27 / 13.99 / 82.54) ran under an agent at
~2.8 cores; the 2026-08-08 quiet-box rerun (7.62 / 11.01 / 11.40 / 80.23) predated a same-day
classification-bug fix (isolation note below); the 2026-08-08/09 snapshot (7.40 / 11.60 / 12.44 /
82.36) stitched two different days; and the 2026-08-13/14 publication-lock pair (7.29 / 11.16 /
11.82 / 81.88) still stitched OpenWhisk across to a second day (its lock2 leg was corrupted by a
loadgen-timeout bug — only 1,993/10,000 requests completed; fixed with INCOMPLETE-RUN/
LOADGEN-FALLBACK gate checks, runbook items 12–18). All four remain in git history for an appendix
note on how the numbers evolved as bugs were found; only the table below is current. It comes
entirely from the **lock4 session (2026-08-14)** — all four platforms back-to-back the same day on
the same box under the self-certifying quiet gate (ambient 5.9–7.4% of a 15% ceiling), each leg
with a freshly calibrated `idle_w` (§4.5), count-complete (10,000/10,000 requests) with no loadgen
fallback and all gates green.

*Table 9 — Four-platform comparison (matched lock4 session, 2026-08-14; per-run validation gates green on every run; see §4.7 for gate definitions and Appendix A for per-run provenance).*

| platform | median `cp_dynamic_share_pct` | spread (min–max) | per-inv CP CPU | fn CPU/inv | per-inv CP energy (mJ) | per-inv CP carbon (µg CO₂) | host_sat% | energy/carbon |
|---|---|---|---|---|---|---|---|---|
| OpenFaaS | **7.58** | CI 6.78–7.66, CV 5.41% | 0.54 ms | 6.62 ms | 1.90 | 0.09 | 70.8 | validated — 1.0–2.3% steady-state |
| Fn | **11.29** | CI 9.82–11.50, CV 6.39% | 0.72 ms | 5.66 ms | 2.52 | 0.12 | 71.9 | validated — 0.6–3.4% steady-state |
| Knative | **11.47** | CI 10.94–12.31, CV 4.39% | 0.88 ms | 6.82 ms | 3.09 | 0.15 | 74.1 | validated — 4.1–8.8% steady-state |
| OpenWhisk (standalone) | **81.78** | CI 80.65–84.45, CV 1.71% | 25.66 ms | 5.72 ms | 89.8 | 4.30 | 60.7 | estimate only — 27.4–46.8% (median 42.0), structural |

*The two per-invocation energy/carbon columns are **model-based** (dynamic portion only: CP CPU
per invocation × 3.5 W/busy-core, then kWh × PUE × CI for carbon — §4.5), not measured directly,
and carry the same RAPL-validation verdict as the final column. "Validated" means the session's
own energy model fits the committed RAPL readings within the steady-state runs (full 5-run ranges
and medians: OF 1.01–34.55% (2.33), Fn 0.55–32.00% (3.36), Kn 2.45–27.18% (5.88); runs 1–2 carry
the documented warm-up transient, §5.6 energy-citability note below). OpenWhisk's absolute
J/gCO₂ are reported as an estimate only — the standalone JVM does not fit the linear model
(structural, stable across five sessions) — and are never presented as validated numbers.*

**Isolation note (the bug behind the OpenFaaS correction).** A same-day audit found OpenFaaS's
`cp_containers` matcher included a bare `"gateway"` substring that also matches Knative's
`kourier-gateway` pod (k3s/Knative-serving stay resident on this box across every platform's
session, by design). This silently folded a small amount of leftover Kourier CPU into OpenFaaS's
control-plane bucket in every prior OpenFaaS run on this box. Fixed by scoping the matcher to the
swarm-stack-prefixed container names (`openfaas_gateway`, ...); confirmed live afterward — the
fresh run's `delta_check_map` shows exactly the 6 real OpenFaaS containers, zero Kourier entries.
The effect on the reported share was small (OpenFaaS's regression-session share moved 7.62 → 7.14
in the 2026-08-08 evening leg, partly this fix and partly ordinary day-to-day box drift — the two
are not separated here; the 2026-08-09 leg re-reads 7.40, ref 7.61, and the lock4 leg below
re-reads 7.58).

**Reading.** The container-visible control-plane share spans an order of magnitude: of-watchdog
(per-replica, inside the function cgroup) 7.6 < fnserver (a dedicated broker process) 11.3 <
Knative's knative-serving + kourier gateway + activator 11.5 < the standalone's JVM
orchestrator+log-store 81.8. Fn and Knative sit close together on this box; OpenWhisk still burns
roughly a ~4.5× multiple of the function CPU in orchestration, consistent across five independent
sessions of varying box-state (82.54 contaminated → 80.23 quiet → 82.36 → 81.88 publication-lock →
81.78 lock4) —
the OpenWhisk
finding has now survived five separate measurement conditions with only ~2 pp of movement,
which argues it is a genuine structural property of the standalone emulator, not a measurement
artifact.

**Energy-citability note (fresh `idle_w` per leg — the previously-open gap is closed).** The
validated/estimate-only flags above describe the SAME session that produced the share in that row. Unlike the
2026-08-08/09 snapshots — where Fn/OpenFaaS's `idle-w=4.3` was a stale 2026-08-05/06 config-file
constant that `saqef regression` never re-measured, so their energy was citable only from the
separate §5.1 baseline sessions — the lock4 session calibrated `idle_w` on the spot,
immediately before each leg (OpenFaaS 4.235 W, Fn 4.249 W, Knative 5.739 W, OpenWhisk 4.882 W),
with the N=5 calibration provenance committed
(`results/idle_w_calibration/lock_lock4/`). RAPL validation still shows the documented 1–2-run
warm-up transient (Fn runs 1–2 ≤32.0%, OF ≤34.6%, Kn ≤27.2%; `saqef gates`'s "RAPL FIT DEGRADED"
flag fired correctly), with runs 3–5 fitting cleanly (Fn 0.6–3.4%, OF 1.0–2.3%, Kn 4.1–8.8%) —
so Fn/OpenFaaS/Knative energy/carbon is **citable from this table's own session** for the
steady-state runs, under the same discipline applied to every other multi-run metric (§5.5). This
resolves the "recalibrate Fn/OpenFaaS idle-w" item in §10 item 3. OpenWhisk remains NOT citable
(rapl err 27.4–46.8%, median 42.0% — the structural JVM/linear-model mismatch, §10 item 10). The
separate §5.1 baseline sessions (`fn_cpubound_baremetal`/`openfaas_cpubound_baremetal`, full 5-run
rapl 4.20–36.07% / 4.17–34.55%, median 18.66% / 8.20%) remain valid as the original bare-metal
energy references.

**Attribution map (must be printed next to the table).** The boundary rule is deliberately simple:
**per-replica proxies co-located with the function on the request path (of-watchdog, Knative's
queue-proxy, Fn's in-process watchdog) are classified as function overhead; centralized routers,
schedulers, and gateways are classified as control-plane overhead.** Applied per platform,
Knative's *fn* bucket includes the
per-replica **queue-proxy sidecar** (on the request path) AND the function; its *CP* bucket
includes the **kourier gateway + svclb-kourier** (data-plane) + activator + controller/autoscaler/
webhook/net-kourier-controller. OpenFaaS counts its of-watchdog proxy *in fn*. So the true
OF-vs-Kn control-plane gap is even smaller than the raw 7.6-vs-11.5 suggests; the reported CP shares
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

*Table 10 — Convention-normalized view: control-plane shares under the reported (proxy-co-located-in-fn) convention vs the worst-case reclassification.*

| platform | CP bucket | fn bucket | share (proxy co-located in fn — reported) | share (proxy→CP, worst case) |
|---|---|---|---|---|
| OpenFaaS | gateway, queue-worker, nats, prometheus, alertmanager, faas-swarm | hello replicas (of-watchdog co-located) | **7.58** | n/a — proxy inseparable from fn cgroup |
| Fn | fnserver | function containers (in-process watchdog) | **11.29** | n/a — no separable proxy |
| Knative | kourier-gateway, svclb-kourier, activator, autoscaler, controller, webhook, net-kourier-controller | user-container **+ queue-proxy** | **11.47** | **24.8** (queue-proxy→CP) |
| OpenWhisk | standalone JVM (controller+invoker+log-store) | action containers | **81.78** | n/a — no per-replica proxy |

The 24.8% figure is recomputed from the lock4 session's `samples.csv` data
(per-container CPU-time integrated over the run; reproduces the harness's own 11.47 to within
~0.03 pp, cross-validating
the method): Knative's queue-proxy is ~10.2 CPU-s per run vs ~57.8 s fn and ~8.8 s CP. Reading:
(1) the **Fn–Knative cluster tie is convention-sensitive** — classify the sidecar as CP and Kn
separates from Fn (11.5 → 24.8) — so the honest statement is a tie *within the co-located-proxy
convention*, and the paper reports them as a cluster, not an ordering; (2) **OpenFaaS <
{Fn, Knative} and both << OpenWhisk survive every plausible reclassification** (OW is an order
of magnitude away no matter where the sidecar lands), which is what makes the four-platform
ordering claim convention-robust. Per-invocation control-plane CPU (0.54 / 0.72 / 0.88 / 25.66 ms)
has the same convention caveat and the same robustness properties.

**Function-cost-normalized view (a second, independent robustness check — §4.1's denominator
caveat).** `cp_dynamic_share_pct`'s denominator is `cp_cpu_s + fn_cpu_s`, so a platform whose
function-side runtime is itself more expensive to execute lowers its own reported share even at
identical control-plane cost. We measure real function-side differences running the *identical*
5 ms handler: Fn 5.66 ms/inv, OpenFaaS 6.62 ms/inv, Knative 6.82 ms/inv, OpenWhisk 5.72 ms/inv
(§5.6 table). To test whether the four-platform ordering survives this confound, we recompute the
share from each platform's own measured per-invocation CP-ms, replacing the *measured* fn-ms with
a common flat 5 ms for every platform:

*Table 11 — Function-cost-normalized view: shares recomputed from each platform's own CP ms/inv with fn cost forced to a flat 5 ms (denominator robustness check).*

| platform | CP ms/inv | measured fn ms/inv | actual share | share @ flat 5 ms fn |
|---|---|---|---|---|
| OpenFaaS | 0.54 | 6.62 | **7.58** | 9.81 |
| Fn | 0.72 | 5.66 | **11.29** | 12.60 |
| Knative | 0.88 | 6.82 | **11.47** | 15.01 |
| OpenWhisk | 25.66 | 5.72 | **81.78** | 83.69 |

The absolute percentages move by several points in both directions (OpenFaaS and Knative rise
more than Fn, since their measured fn-ms was already above 5 ms; OpenWhisk barely moves, since
its measured fn-ms was already close to 5 ms) — but **the ordering OpenFaaS < Fn ≈ Knative <<
OpenWhisk is unchanged**. Combined with the convention-normalized check above (which stresses the
CP/fn *classification* boundary), this stresses the *denominator's other term* — between the two,
the paper's central ordering claim survives both of the metric's own known sensitivities. Neither
check makes `cp_dynamic_share_pct` a pure control-plane-only measure — it remains, by
construction, "control-plane CPU relative to the measured marginal execution CPU," not relative
to a platform-independent notion of control-plane cost — which is why per-invocation CP-ms
(denominator-free) is reported as a co-headline metric throughout this paper rather than the
percentage alone (§4.1).

**Idle-baseline finding — CLOSED (2026-08-09, N=5 per condition).** Three single-sample readings
of Knative's idle package power previously disagreed badly — 11.14 W (2026-08-07, contaminated
box); 7.01 W (2026-08-08 morning, "quiet" box); 4.91 W (2026-08-08 evening, `hello` deployed at
16 replicas) — and no repeats existed to separate real drift from single-sample noise. That
fragility is exactly why the finding was kept open. On 2026-08-09 the calibration was repeated
N=5 per condition (60 s RAPL reads, wraparound-guarded, median + spread):

*Table 12 — Knative idle-w calibration (N=5 per condition, 60 s RAPL reads, 2026-08-09 quiet box).*

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
0.25/0.41 W vs the >2× scatter of the old one-shots).

**Idle premium is direction-stable but day-state-dependent (lock4 N=5 recalibration).** The lock4
session (2026-08-14) re-ran the same N=5 per-condition protocol immediately before its Knative leg
(`results/idle_w_calibration/lock_lock4/`): bare k3s substrate **4.084 W** (spread ~0.3 W) vs
`hello` @ 16 replicas **5.739 W** (spread ~0.5 W) → **premium ≈ 1.66 W**. The premium's *direction*
is confirmed (Knative @ 16 warm replicas is always above the bare substrate, matching the
2026-08-09 finding), but its *magnitude* is not a stable constant: 0.690 W on 2026-08-09 vs ~1.66 W
on 2026-08-14, both N=5 medians on the same box — the absolute idle draw moves with day-to-day
thermal/frequency state (bare itself read 3.871 → 4.084 W). The paper therefore does not cite the
premium as a fixed number; it cites the two-condition gap as "some always-on premium, ~0.7–1.7 W
across two N=5 measurements" and, for energy modeling, each session uses its own freshly measured
per-leg `idle_w` (the lock4 table above uses 5.739 W for Knative). The linear busy-core energy
model fits Knative's dynamic (load) power well regardless (rapl err 4.1–8.8% on runs 3–5 in lock4,
1.3–4.7% on the 2026-08-09 session) — **Knative energy/carbon is citable for the dynamic/marginal
figures**, and the idle premium is now pinned by properly repeated measurements on both days. **Propagation fix (2026-08-13):** an internal
review caught that this idle-baseline correction had been derived but never actually applied to
the cited `results/knative_cpubound_baremetal` run — its committed `summary.json`/`runs.json`
still carried the stale single-sample **4.906 W** in `model.idle_w`, so the *absolute*
energy/carbon figures quoted from that run (`energy_J.total`, `carbon_gCO2.op_total`,
`kpi_gco2_per_slo_compliant_inv`) had not actually been recalibrated even though this section said
they should be. Fixed by deterministically recomputing those idle_w-dependent fields from the
already-committed per-run `cpu_sec`/`wall_s` with `idle_w=4.561` (condition B median above) — not
a rerun; the formula is byte-identical to `saqef_harness.py`'s own energy model, applied post hoc.
`cp_dynamic_share_pct`, `cp_dynamic_share_pct`-derived KPIs, and `carbon_gCO2.op_control_plane`
are idle_w-invariant by construction and were verified unchanged (asserted in the recompute
script). Net effect: Knative's `carbon_gCO2.op_total` moves 0.018 → 0.017 gCO2/run,
`energy_J.total` 371.1 → 364.1 J — a small correction, but the citable run's stored data now
actually matches the idle baseline this section claims for it, closing the gap a cold review
found between "closed" prose and stale committed numbers. **Corroboration (direction only):** the
idle-term-dominance hypothesis predicts that longer/heavier runs degrade less from a stale
`idle_w` even without recalibration, and the 2026-08-09 regression runs (TOTAL=10000, ~17–19 s
wall) show mostly clean RAPL fits — Fn 16.6/12.1/0.6/1.5/0.65, OF 18.9/14.8/2.3/0.6/5.8%, only
run_1 crossing the 15% degraded flag on each — versus every run failing at 20–55% on the shorter
contamination A/B legs (TOTAL=3000, ~5 s wall) under the same `idle_w=4.3`. A one-line observation,
not proof (`idle_w` was not recalibrated in either case). OpenWhisk's fit remains poor (rapl err
31–50% this session, consistent with 45–58% two days ago and 36% on the original contaminated
run) — a structural mismatch between the linear model and the standalone JVM's power draw,
confirmed stable across four independent sessions, so **OpenWhisk energy/carbon stays NOT
citable**; that is a separate, larger open item (Future Work §10), not a calibration gap this
protocol could close.

**Idle bare-vs-loaded decomposition for ALL four platforms (lock4, N=5 per state).** The same
lock4 calibration that re-read Knative's premium also measured every other platform's always-on
idle draw in the exact bench-time stack state (control plane up, warm replicas, **zero traffic**;
`results/idle_w_calibration/lock_lock4/*.txt`, raw reads committed and reviewer-re-derivable). The
ΔW over the bare substrate (4.084 W) is each platform's always-on control-plane + warm-replica
power — the static half of the paper's idle-vs-dynamic energy split (§4.5), now a measured row for
every platform rather than only Knative:

*Table 13 — lock4 always-on idle decomposition (N=5 repeated 60 s RAPL reads per state, 2026-08-14 quiet box, raw reads in `results/idle_w_calibration/lock_lock4/`).*

| stack state | idle W (median; min–max) | ΔW vs bare |
|---|---|---|
| bare substrate (k3s + knative-serving + kourier, no function) | 4.084 (3.864–4.515, spread 0.65) | — |
| OpenFaaS (control plane + hello @ 16) | 4.235 (4.017–4.418, spread 0.40) | **+0.151** |
| Fn (fnserver, function registered) | 4.249 (3.994–4.339, spread 0.35) | **+0.165** |
| OpenWhisk (standalone JVM) | 4.882 (4.464–5.907, spread 1.44) | **+0.798** |
| Knative (serving + hello @ 16) | 5.739 (5.435–6.272, spread 0.84) | **+1.655** |

**Reading:** the four platforms split into two regimes. Fn and OpenFaaS add only ~0.15 W of
always-on control-plane power over the bare substrate — their idle cost is essentially the
substrate itself. The OpenWhisk standalone adds ~0.8 W (its always-resident JVM + per-activation
container pool), and Knative's k8s-native stack adds ~1.66 W — the largest always-on premium, ~10×
the Fn/OpenFaaS value, driven by the warm 16-replica × queue-proxy deployment plus
knative-serving/kourier. This is the same conclusion as the Knative-only finding above but now
measured uniformly across all four platforms from one same-day, same-gate session. As with the
Knative premium (§5.6), magnitudes are day-state dependent — what matters is the *ordering*
(Fn ≈ OpenFaaS ≪ OpenWhisk < Knative), which is exactly the ordering of the platform stacks'
static footprint (design-principle C3, §11).

**Reproducibility note (five sessions, five box-states).** OpenWhisk's share has now been
measured five times under different conditions — 82.54 (contaminated), 80.23 (quiet), 82.36
(moderately loaded: host_sat 57–70%, `dockerd` itself ran at 45–64% CPU during the
bench, consistent with the per-activation `docker logs` hypothesis in §5.6), 81.88
(publication-lock lock3, quiet-gated), 81.78 (lock4, quiet-gated) — and stayed within a
~2 pp band throughout. That is strong evidence the ~80% finding is a genuine structural property of
the standalone emulator, not a measurement artifact. Knative moved more between the first two
sessions (13.99 → 11.40, outside the old session's own CI — contamination WAS materially inflating
that number), then 12.44 → 11.82 → 11.47 across the three later fully-gated sessions — a reminder that "quiet" and "identical" are not the same thing — ordinary
day-to-day box variance is real even on a quiet box, and every number in this table is traceable to
one specific, fully-gated session rather than averaged across days for exactly this reason. Fn
(11.60 → 11.16 → 11.29) and OpenFaaS (7.40 → 7.29 → 7.58) also re-read within their established
drift bands on the lock2 and lock4 sessions.

**QoS citability.** All four platforms' legs passed the host_sat < 85% non-contamination gate
this time too (70.8% OF / 71.9% Fn / 74.1% Kn / 60.7% OW), so QoS is citable from this table's
session:
OpenFaaS p50 7.0 / p99 13.1 ms @ 532.8 rps; Fn p50 6.5 / p99 9.4 ms @ 590.5 rps; Knative p50 7.6 / p99
11.0 ms @ 505.8 rps; OpenWhisk p50 110.7 / p99 189.2 ms @ 35.0 rps. SLO compliance is 1.0 on all
four. OpenWhisk's percentiles are still visibly worse than the quiet-morning reading (97.4/136.5 ms
@ 40.8 rps), with host_sat a little higher here (60.7% vs 42–56%) and the standalone's own
run-to-run throughput variance larger (35.0 rps median; run_5 dipped to ~28 rps) — a concrete
illustration of why the framework
reports host_sat alongside every QoS number rather than a bare latency figure.

---

## 6. Discussion

**Why the headline is `cp_dynamic_share_pct`, not `cp_share_pct`.** The static share (`cp_share_pct`) is what an operator sees on a dashboard; it hides the fact that the *marginal* cost of serving a function is dominated by orchestration — the dynamic share is ~10–14% and core-count dependent (§5.5). The dynamic share is the economically relevant quantity for per-invocation pricing, carbon-aware scheduling, and "green function" claims.

**What is measured vs estimated (honest line).** Measured directly: QoS, per-container CPU (validated to 0.01% against direct counters), physical plausibility, RAPL package Joules (bare metal). Modeled: absolute Joules and carbon (CPU-time proportionality, literature constants). The **relative** dynamic share is robust to the model constant (3.5 W/core scales both numerator and denominator), so `cp_dynamic_share_pct` is the most defensible number we produce.

**The ordering is robust; the magnitudes are convention- and machine-sensitive.** Across four
platforms and five sessions the container-visible control-plane share consistently spans an order
of magnitude — OF < {Fn,Kn} << OW — and this ordering survives both of the metric's own known
sensitivities: the CP/fn *classification* boundary (reclassifying Knative's queue-proxy as
control plane raises Kn from 11.47% to 24.8% but leaves OF < Fn and OW untouched, §5.6) and the
function-side *denominator* (normalizing all four platforms to a flat 5 ms function cost shifts
absolutes by several points but preserves the ordering, §5.6). The two edges are the fragile ones:
the Fn–Kn tie is convention-dependent, and the Fn–OF gap magnitude is machine-dependent (below
the 5 pp gate at 8 cores, above it at 2 cores, §5.5).

**Mechanism synthesis — where the control-plane CPU actually goes.** The asymmetric core-scarcity
result (§5.5: Fn's share +33% at 2 pinned cores, OpenFaaS flat-to-lower) and the asymmetric
contamination result (§7 threat 5: Fn +2.2 pp vs OpenFaaS +0.3 pp under an agent-style load) point
to the same architectural difference: Fn places a **central orchestrator (fnserver) on the request
path**, whose scheduler contends for cores under scarcity; OpenFaaS co-locates its per-replica
proxy (of-watchdog) *inside* the function cgroup, so its overhead is proportional to function
work and insensitive to core count. The mechanism is observed-not-explained at the syscall level
(proposed probe: `perf stat -e context-switches,migrations` on fnserver at 8 vs 2 cores — §10
future work), but the *design* lesson is concrete regardless. OpenWhisk's 25.66 ms/inv control
plane is dominated by the standalone's per-activation `docker logs` log-store read — a pure
orchestration artifact, not request-path logic — which is why its energy model fails RAPL
validation structurally and why its share is deployment-mode dependent (tested standalone mode
only).

**Implications for design (three principles).** (C1) Keep control-plane work per-replica and
co-located with the function rather than on a central request-path orchestrator: cheaper per
invocation (0.54 ms vs 0.72 ms) and immune to the core-scarcity amplification. (C2) Use
structured/streaming log collection, never per-activation container-log reads: OpenWhisk's
log-store is ~O(container-spawn + log I/O) *per request*, pure waste with zero QoS value. (C3)
Give autoscalers an explicit idle-watts budget and a carbon-aware cold-start policy: Knative's
always-on warm-replica baseline is a real, repeated measurement (~0.7–1.7 W premium across two
N=5 days; §5.6) that trades directly against cold-start energy and latency.

**Sustainability reading.** The "scale to zero" pitch understates a platform's true footprint
because the control plane idles whether or not functions run, and its per-invocation work is not
billed. The per-invocation orchestration numbers (§5.6: 0.54–25.66 ms CPU, 1.9–89.8 mJ dynamic
per invocation) turn that hidden tax into a schedulable quantity: route to the cheapest control
plane, price "green functions" correctly, and gate green claims on the host's actual core count —
since the same platform on a smaller machine costs *proportionally more*.

**What the framework generalizes.** SAQEF's value is the transferable protocol — container-level
attribution, a direct-counter delta-check, an RAPL gate, a quiet-box self-certification, and a
session-trail discipline — more than any single platform number. A new platform is a config
adapter; a new machine is a fresh (gated, honest) number. The n=1 scope is disclosed and the
machine-pair gate lets any operator re-derive the discriminator on their own hardware (§7
threat 9, §10).

---

## 7. Threats to Validity (explicit)

1. **No RAPL on the origin instrument (closed for the reported results).** The reported absolute energy/carbon come from the bare-metal 8-core box, where the model is RAPL-validated against a full 5-run session (4.2–36.1% Fn, 4.2–34.6% OpenFaaS; median 18.7% / 8.2%; idle calibrated 4.3 W, not the 30 W default) — see §5.5's RAPL validation caveat for the honest per-run breakdown and why 2 of 5 runs per platform read a wider error than the other 3. The earlier shared-VM instrument had no RAPL and is used only as the origin story (§4.2); its absolute numbers are not cited. The relative `cp_dynamic_share_pct` is model-constant-robust everywhere.
2. **Function CPU is a lower bound.** Function containers live 2–5 s and are only partially captured by the sparse nested-mount sampler (`sampling_covered_s` ranged 13–100% across runs; totals stay exact for *sampled* containers because of cumulative differencing). CP is exact (proven); treat `cpu_sec.function` as ≥ the reported value. Improves on bare metal.
3. **Co-tenancy (origin instrument only).** Host-level metrics from the abandoned shared-VM instrument included neighbor noise; that instrument contributes no reported numbers (definition unchanged: `orchestration_cpu_sec` is a host-wide residual, never presented as pure orchestration). The reported results come from a dedicated 8-core box, where host metrics are clean; the cgroup-exact control-plane container share (`cp_dynamic_share_pct`) is the claim.
4. **Contention-contaminated QoS (closed for the quiet c=4 baseline).** On the shared-VM origin instrument, `host_saturation_pct` ≈ 100% made latency percentiles reflect scheduler contention, not intrinsic platform overhead. On the 8-core box at c=4 (host_sat 74–77%) QoS is citable: Fn p50 6.5 / p99 8.9 ms @ 597 rps; OF p50 7.2 / p99 12.1 ms @ 532 rps; SLO 1.0. The c=8 quick runs (sat 91–93%) carry the `host_saturated` flag and their latency is NOT citable — consistent with the discipline that a saturated box measures reproducibly wrong.
5. **Agent-style background load moves the share — a measured, asymmetric bound.** We quantified how much a co-tenanted background load like the one that contaminated the 2026-08-07 session moves `cp_dynamic_share_pct` on this box, using the harness's own A/B tool (`tools/contamination_ab.py`: clean vs dirty leg, N=5/leg, profile-matched to that incident — 3 busy cores ≈ 300% CPU + 1.1 GB RSS; results in `results/{fn,openfaas}_contamination_ab/`). Fn's share moved **10.0 → 12.2 (+2.2 pp)**, reproduced within ~0.05 pp across two independent sessions, while OpenFaaS's moved **6.9 → 7.2 (+0.3 pp)** — a ~6× asymmetry. The share is inflated only where a *central orchestrator* sits on the request path (fnserver); OpenFaaS's per-replica of-watchdog model is nearly immune. Consequences: (a) the earlier inferred ~0.3–1 pp contamination estimate is superseded by this direct measurement; (b) no headline conclusion is overturned by an un-quiet box — the ordering OF < Fn survives both legs — but the margin is thin: the clean gap is 3.1 pp and the dirty gap 4.92 pp, just **0.08 pp (1.6%)** below the 5 pp gate, and the dirty-leg gap's run-to-run spread straddles it (≈4.7–6.1 pp across per-run values). The gate is therefore not only machine-pair dependent but *co-tenancy dependent*: a heavier-than-incident background load (an explicit heavier-load probe is future work) would likely push the Fn–OF gap across 5 pp, and an Fn share measured under agent-like load carries up to ~+2 pp of inflation; (c) the asymmetry independently supports the central-orchestrator-vs-per-replica design principle (§6), matching the core-scarcity direction (§5.5) — a hypothesis the four-platform set can test further (OpenWhisk's single-container control plane is the natural next probe, §10). The ambient-load quiet gate (§4.7, ≤15% host busy sampled before any bench) now self-certifies a citable run, with this A/B as the measured bound behind it.
6. **Four platforms, one machine, shared attribution caveat.** Fn, OpenFaaS, OpenWhisk and Knative are measured on the 8-core bare-metal box with the *same* instrument (§5.6), but the CP/fn attribution convention differs per platform (OF's of-watchdog in fn; Kn's queue-proxy in fn but kourier gateway in CP; OW's whole JVM as CP) — the reported share is a per-platform convention, so cross-platform share comparisons must cite the map. The discriminator's magnitude is machine-dependent (RQ3 answer, §5.5) — a bounded threat that is now quantified rather than unknown. OpenWhisk's share additionally depends on the standalone emulator's deployment mode (per-activation log-store) — an open attribution item (future work, §10), reported here under the deployed configuration rather than a production cluster.
7. **Control plane measured as one container** (`fnserver`), not decomposed into gateway/scheduler/queue sub-components. Decomposition inside the control plane is future work (§10).
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

*Table 14 — Cross-check ledger: every reported quantity and how it is independently verified.*

| # | Reported quantity | Independent cross-check | Current status |
|---|---|---|---|
| G1 | control-plane CPU | cgroup **delta-check**: direct before/after counter summed across **all** CP containers vs the sampler's sum (re-resolved per read) | **0.00–0.01%** across all v9.7 runs; Fn single-container set unchanged by the v9.9 whole-set fix |
| G2 | host busy accounting | `host_plausible = host_cpu_sec ≤ cpu_count × host_window_s × 1.05` (v9.10: host's own sampling window, self-consistent); `host_saturation_pct = host/host_window` per core; v9.8 `host_saturated` flag = sat ≥ 85% | 99.9–100.3% (v9.7 window), plausible=true (saturated but *reproducibly*); v9.10 makes the check alignment-proof at short wall windows |
| G3 | sampling coverage | `sampling_covered_s / wall_s`; stop-time flush + clamp so it cannot exceed 100% | **100.0% on all 5** v9.7 runs |
| G4 | function classification | allowlist (names + image + label keys) with a logged **unclassified bucket**; fail-open (a configured-but-matching-nothing allowlist warns instead of reverting to the denylist) | `unclassified_cpu_s = 0.0` with 12/12 fn containers matched by image; no warning fired |
| G5 | load-generator identity | `env.loadgen` records the **actual** generator (`py`/`hey`), plus `loadgen_requested` and `loadgen_fallback` | v9.6+ truthful; a silent fallback is impossible |
| G6 | cross-session repeatability | multi-session median discipline; `cv_pct`/`iqr`/`bootstrap_ci` within a session, session medians across sessions | Fn bare-metal: 10.46 (quiet 2026-08-05) / 11.60 / 12.27 / 12.92 (same-day 2026-08-07 sessions) — bounded ~2 pp drift; 2-core sessions 13.91/14.08 (0.2 pp); OF 7.53–7.72 across sessions; OpenWhisk CV 2.1%. **2026-08-09:** refs re-anchored same-day under the self-certifying quiet gate (Fn 11.49 / OF 7.61, old-runner A/B) and `saqef regression` reproduces them (Fn 11.27, dev 0.22 pp; OF 7.40, dev 0.21 pp) — **PASS both**, G6 GREEN (§8.1). **2026-08-14 lock4 session** (all four platforms back-to-back same day, quiet-gated, count-complete): Fn 11.29, OF 7.58, Kn 11.47, OW 81.78 — all within their established per-platform bands; against the same-day-anchored regression references (`metrics/cpubound.json`: Fn 11.49 / OF 7.61) the lock4 legs deviate **0.20 / 0.03 pp**, both under the 0.5 pp tolerance, so the gate stays green and needs no re-anchor for this box-state |
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

The framework was **frozen at v9.7**; later development (v9.8/v9.9, §8.1) was limited to
reviewer-verified gate gaps and dead-code-path bugs, so everything measured after the freeze is
comparable by construction.

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
2. ~~**OpenWhisk + Knative — cross-platform comparison**~~ — **done, lock4 session 2026-08-14**: four platforms on the same box, back-to-back the same day under the quiet gate (§5.6): OF 7.58 < Fn ≈ Knative 11.29/11.47 < OpenWhisk 81.78. QoS is citable for all four; energy/carbon is citable for OF/Fn/Kn from this session (fresh per-leg `idle_w`, §4.5) and remains not citable for OpenWhisk (structural). Remaining for OW: decide how much of the standalone's per-activation log-store is "control plane" vs emulator artifact (unchanged across five sessions of varying box-state — see the reproducibility note in §5.6, which argues against it being mostly a deployment artifact). ~~Knative's idle-w calibration~~ — **closed (2026-08-09, re-confirmed 2026-08-14)**: the N≥3 repeated-read protocol (wraparound-guarded `rapl_w_series()`) measured 3.871 W bare / 4.561 W with `hello` @ 16 replicas (N=5 medians, spreads 0.25/0.41 W), and lock4's N=5 per-leg recalibration re-read bare 4.084 W / Knative 5.739 W — the "always-on idle premium" is direction-stable across both days at **~0.7–1.7 W** (§5.6).
3. ~~**Bare metal** (dual-boot Ubuntu) — RAPL ground truth~~ — **done (2026-08-05)**: RAPL-validated, full 5-run median 18.7% (Fn) / 8.2% (OpenFaaS), idle 4.3 W — see §5.5's RAPL validation caveat. Remaining: a *third* machine to bound the machine-dependence trend. The recalibrated-`idle_w` item for Fn/OpenFaaS is **closed (2026-08-14)**: the lock4 session calibrated `idle_w` fresh per leg (OF 4.235 W, Fn 4.249 W, Kn 5.739 W) — §5.6's energy-citability note.
4. **Control-plane decomposition** — which fnserver subcomponent (gateway, scheduler, freeze manager, watchers) costs what.
5. **Fn-drift mechanism** — why fnserver's per-request CP cost drifts ~0.6→0.75 ms (and Fn's share ~10.5→12.9) run-to-run/session-to-session, and why Fn's share inflates +33% at 2 pinned cores while OF stays flat. `perf stat -e context-switches,migrations` on the fnserver process at 8 vs 2 cores (AGENTS.md future-work §B).
6. **Cold-start vs warm** — `--interarrival-ms 1000` isolation experiment: the "carbon cost of elasticity."
7. **Freeze-policy ablation** — `FN_FREEZE_IDLE_MSECS=0` vs default: quantify Fn's pause/unpause churn in energy terms.
8. **Realistic mixed workloads** — CPU/IO mixture, not just pure spin (subset of the workload-variation study, AGENTS.md future-work §A).
9. **A reference "floor" control plane** — nginx/haproxy in front of 16 static replicas of the same handler (no FaaS runtime) to anchor the platform shares to a lower bound: "real orchestration tax" = platform − floor (AGENTS.md future-work §C).
10. **OpenWhisk energy/carbon — a structurally separate open item.** The standalone JVM's power draw does not fit the linear busy-core model: `rapl_validation_err_pct` reads 27.4–46.8% (median 42.0%) in the lock4 session and 33–53% (median 48%) in lock3, stable across five independent sessions of varying box-state (contaminated 2026-08-07, quiet 2026-08-08 morning, quiet 2026-08-08 evening, quiet-gated lock2-corrupt OW leg 2026-08-13, quiet-gated lock3/lock4 2026-08-14) — the constant memory-touch load scales with neither cores nor concurrency. This is **not** the same class of gap as the idle-w calibration problem (§5.6 closed it for Knative and the lock sessions for Fn/OpenFaaS); recalibrating idle-w would not close it. A JVM-aware energy model (or resolving the standalone's deployment-mode question, §5.6) is required before OW energy/carbon can be cited. It is deliberately out of `saqef regression`'s scope: that gate exists to prove the refactored CLI reproduces old-runner values for the two platforms with tight references (Fn/OpenFaaS), and OW has neither an old runner nor a calibration-gap diagnosis. `cp_dynamic_share_pct` remains citable for OW (pure cgroup CPU-time ratio, model-free).

---

## 11. Research impact & how the results can be used

- **Methodology reuse:** the harness + delta-check pattern is a drop-in instrument for anyone measuring FaaS energy on any platform (container-level, cgroup-validated, honest about estimation).
- **Informing models:** Kepler-style CPU-time proportionality gains a real orchestration-overhead term (Fn-class platforms ≈10–30% of dynamic energy depending on machine capacity; OpenFaaS-class ≈8–16%), so serverless energy models stop treating orchestration as ~0.
- **Carbon-aware scheduling:** a per-invocation orchestration cost (lock4, §5.6: Fn 0.72 ms CPU ≈ 2.52 mJ dynamic CP-only; OF 0.54 ms ≈ 1.90 mJ; Kn 0.88 ms ≈ 3.09 mJ; OW 25.66 ms ≈ 89.8 mJ, model-based) makes it possible to route work to the cheapest control plane — and to price "green functions" correctly.
- **Design guidance:** autoscaling/scale-to-zero economics quantified — the always-on baseline is a large, often dominant slice of operational carbon (bare-metal Fn/OF 4.3 W idle; Knative's k8s-native stack carries a measured always-on idle premium of **~0.7–1.7 W** over that floor — 4.561 W vs 3.871 W bare on 2026-08-09 and 5.739 W vs 4.084 W on 2026-08-14, both N=5 medians, §5.6; direction stable, magnitude day-state dependent), and the machine-dependence result says orchestration overhead is *capacity-bound*: it buys back fast when functions get more cores, so co-locating functions on fewer, larger boxes (or vice-versa) directly tunes the orchestration tax.
- **Framework portability (new):** the discriminator is a per-machine-pair quantity with a stable *ranking*; the paper provides the recipe (protocol + gates) so a third platform or machine can be ranked without re-deriving the methodology.

---

## 12. Conclusion

**What happened.** We built a measurement framework that attributes the cost of running a
serverless function between the platform's control plane and the function itself, and — unlike
prior work — validated that attribution rather than asserting it: container-level CPU-time
sampling is checked against direct cumulative-counter reads (delta-check), the resulting energy
model is checked against RAPL package Joules on bare metal, every run is gated for physical
plausibility, host saturation, and ambient load, and every citable number comes from a repeated
(REPEAT=5), quiet-gated session with a freshly calibrated energy baseline. We applied it to four
serverless platforms — Fn, OpenFaaS, Knative, and the OpenWhisk standalone — serving an identical
CPU-bound function, culminating in a matched single-day four-platform session (lock4, 2026-08-14)
from which all four platforms' headline numbers are drawn.

**What we achieved.** Three claims, each earned by measurement:

1. **The gap is a machine-pair property, not a platform constant.** Fn's control-plane share of
   dynamic CPU is 10.5% on an 8-core box but 14.0% on the *same* box pinned to 2 cores, while
   OpenFaaS stays at 7.7% → 7.0%. The 5 pp discrimination gate passes or fails depending on the
   host's core count — so "green function" claims and carbon-aware routing must be evaluated per
   machine pair, with a citable gate, never against a global constant.
2. **The four-platform ordering is robust.** OpenFaaS 7.58 < Fn ≈ Knative (11.29 / 11.47) <
   OpenWhisk 81.78, surviving both of the metric's known sensitivities (the CP/fn classification
   boundary and the function-side denominator), the contamination A/B bound, and five independent
   sessions of varying box-state. Control-plane orchestration is a single-digit-percent tax on
   lean platforms and an ~4.5× multiple of function CPU on the heavyweight standalone.
3. **The mechanism is asymmetric and design-relevant.** Core scarcity inflates the share only
   where a *central orchestrator* sits on the request path (Fn +33%, +2.2 pp under contamination);
   per-replica, co-located proxies (OpenFaaS) are nearly immune. The framework's own self-checks
   caught a 52× sampling overcount, a classification collision, a carbon unit bug, and a
   loadgen-timeout corruption during development — validation is not decoration.

**Significance.** In modern computing, the serverless abstraction is sold on *operational
simplicity and elastic efficiency*, but its real cost sits in an always-on control plane that is
invisible to both the per-invocation bill and the dashboard's idle CPU. This work makes that tax
visible and schedulable. For **sustainability**: the marginal orchestration cost is now a
per-invocation quantity (0.54–25.66 ms CPU, 1.9–89.8 mJ dynamic, model-based), scale-to-zero
economics have a measured idle baseline behind them (Knative's ~0.7–1.7 W always-on premium over
the bare stack), and carbon-aware scheduling has the input it previously estimated from static
figures. For **QoS**: the same contention mechanism that inflates control-plane share is what
degrades latency under load — co-locating control-plane work per-replica (design principle C1)
both saves energy and removes a contention source. The honest boundary is equally important:
n=1 machine, tested standalone mode for OpenWhisk, and a session-trail discipline that never
averages across days — the numbers a reviewer can recompute from committed `runs.json` are the
numbers we report. The framework, not the platform-specific findings, is the transferable
artifact: a new platform is a config adapter, a new machine is a fresh gated number, and the
per-machine-pair gate is the tool a reviewer needs to test our claims on their own hardware.

## Appendix A — Consolidated results table (four platforms, bare-metal 8-core; lock4 session 2026-08-14 — all four platforms back-to-back the same day)

All rows are full REPEAT=5 runs on the same 8-core box with per-run gate tables (delta ~0,
coverage 100%, host_plausible true), count-complete (10,000/10,000 requests per leg) with no
loadgen fallback, ambient-load quiet gate active (5.9–7.4% of the 15% ceiling), and a freshly
calibrated `idle_w` per platform (§4.5). This is the fifth snapshot of this table and the first to
run all four platforms back-to-back the same day; it supersedes the earlier snapshots (2026-08-07
agent-contaminated: Fn 12.27 / OF 7.53 / Kn 13.99 / OW 82.54; 2026-08-08 quiet-box: 11.01 / 7.62 /
11.40 / 80.23; 2026-08-08/09 two-day stitch: 7.40 / 11.60 / 12.44 / 82.36; 2026-08-13/14
publication-lock pair: 7.29 / 11.16 / 11.82 / 81.88). **Do not cite the earlier snapshots** —
their evolution is recorded in git history and summarized in the paper's evolution note (§5.6).

*Table 15 — Consolidated four-platform results (lock4 session 2026-08-14, medians of REPEAT=5 per leg; all gates green, count-complete).*

| Metric (median of 5) | Fn | OpenFaaS | Knative | OpenWhisk |
|---|---|---|---|---|
| `cp_dynamic_share_pct` | **11.29** | **7.58** | **11.47** | **81.78** |
| spread (min–max) | 9.82–11.50 | 6.78–7.66 | 10.94–12.31 | 80.65–84.45 |
| per-request CP cost | 0.72 ms | 0.54 ms | 0.88 ms | 25.66 ms |
| per-inv CP energy (mJ) / carbon (µg, model) | 2.52 / 0.12 | 1.90 / 0.09 | 3.09 / 0.15 | 89.8 / 4.30 |
| per-request fn cost | 5.66 ms | 6.62 ms | 6.82 ms | 5.72 ms |
| energy per run (model, median of 5) | 295.3 J | 331.3 J | 383.0 J | 2447.4 J |
| carbon per run (model, median of 5) | 0.014 g | 0.016 g | 0.018 g | 0.117 g |
| QoS p50 / p99 | 6.5 / 9.4 ms ✓citable | 7.0 / 13.1 ms ✓citable | 7.6 / 11.0 ms ✓citable | 110.7 / 189.2 ms ✓citable |
| throughput | 590 rps | 533 rps | 506 rps | 35.0 rps |
| SLO compliance | 1.0 | 1.0 | 1.0 | 1.0 |
| host_sat | 71.9% | 70.8% | 74.1% | 60.7% |
| `idle_w` (fresh per-leg) | 4.249 W | 4.235 W | 5.739 W | 4.882 W |
| always-on ΔW vs bare substrate (4.084 W, N=5) | +0.165 | +0.151 | +1.655 | +0.798 |
| RAPL validation err (5-run range, median) | 0.55–32.00% (3.36%) | 1.01–34.55% (2.33%) | 2.45–27.18% (5.88%) | 27.36–46.77% (42.02%) |
| energy/carbon | validated (0.6–3.4% ss) | validated (1.0–2.3% ss) | validated (4.1–8.8% ss) | estimate only (27.4–46.8%) |

*Kn/OF fn cost includes the request-path sidecar/proxy inside the fn bucket (queue-proxy /
of-watchdog); OW CP cost is the standalone JVM incl. per-activation docker-log log-store
(deployment-mode caveat, §5.6). All four legs passed the host_sat < 85% contamination gate.
OpenWhisk's poor RAPL fit is stable across all five sessions regardless of box-state (structural
JVM/linear-model mismatch, not noise; §10 item 10). The always-on ΔW row is the lock4 idle
decomposition (Table 13; raw reads in `results/idle_w_calibration/lock_lock4/*.txt`). Full per-run
tables: `SAQEF_TECHNICAL_REPORT.md`
§30.1, `results/{openfaas,fn,knative,openwhisk}_cpubound_lock_lock4`
(prior snapshots: `results/{fn,openfaas}_cpubound_baremetal`, `results/regression/{openfaas,fn}`,
`results/{knative,openwhisk}_cpubound_baremetal`, `results/*_cpubound_lock_lock2/3`).*

**Energy-citability note (fresh `idle_w` per leg — the previously-open gap is closed).** The share
and energy numbers in this table come from the SAME lock4 legs. Each platform's `idle_w`
was calibrated on the spot immediately before its leg (OpenFaaS 4.235 W, Fn 4.249 W, Knative 5.739 W,
OpenWhisk 4.882 W), with the raw N=5 reads committed
(`results/idle_w_calibration/lock_lock4/`). This closes the gap flagged in §5.5/§10 item 3, where
Fn/OpenFaaS's energy was previously citable only from the separate 2026-08-05/06
`fn_cpubound_baremetal` / `openfaas_cpubound_baremetal` baseline sessions (RAPL err, full 5-run
range 4.20–36.07% / 4.17–34.55%, median 18.66% / 8.20% — those remain valid as the original
bare-metal references). On the lock4 legs, RAPL validation still shows the documented 1–2-run
warm-up transient (Fn runs 1–2 ≤32.0%, OF ≤34.6%, Kn ≤27.2%; `saqef gates`'s "RAPL FIT DEGRADED"
warning fired correctly) with runs 3–5 fitting cleanly (Fn 0.6–3.4%, OF 1.0–2.3%, Kn 4.1–8.8%) —
so Fn/OpenFaaS/Knative energy/carbon is citable from THIS table's session for the steady-state
runs, under the same discipline applied to every other multi-run metric in this study. OpenWhisk
remains not citable (rapl err 27.4–46.8%, median 42.0%, structural).

### Appendix B — Consolidated cross-platform table (bare metal, all regimes)

The regime table is necessarily Fn/OF in the 2-core column — only Fn and OpenFaaS were ever run
at 2-core pinning (the controlled core-count experiment, §5.5; Fn/OF bare c=4 rows use the same
2026-08-05 baseline as figure1 for internal regime consistency). OpenWhisk and Knative exist only
at 8-core bare metal (lock4 session 2026-08-14, current as of Appendix A; shares,
QoS, and — for Fn/OF/Kn — energy are all citable from that session). The four-platform
comparison lives in Appendix A and figures 2–3.

*Table 16 — Consolidated cross-platform results across all bare-metal regimes (8-core vs 2-core pinned).*

| Metric (median of 5) | Fn (bare c=4) | OF (bare c=4) | Kn (bare c=4) | OW (bare c=4) | Fn (2-core pinned) | OF (2-core pinned) |
|---|---|---|---|---|---|---|
| `cp_dynamic_share_pct` | 10.46 | 7.67 | 11.47 | 81.78 | 14.00 | 7.00 |
| gap Fn−OF (pp) | **+2.79 (gate fails)** | | (lock4: +3.71) | | **+7.00 (gate passes)** | |
| per-request CP cost | 0.66 ms | 0.56 ms | 0.88 ms | 25.66 ms | — | — |
| QoS p50 / p99 | 6.5 / 8.9 ms ✓ | 7.2 / 12.1 ms ✓ | 7.6 / 11.0 ms ✓ | 110.7 / 189.2 ms ✓ | not citable | not citable |
| throughput | 597 rps | 532 rps | 506 rps | 35.0 rps | — | — |
| SLO compliance | 1.0 | 1.0 | 1.0 | 1.0 | — | — |
| host_sat | 74–77% | 74–78% | 74.1% | 60.7% | 98.5–98.7% (saturated) | 98.5–98.7% (saturated) |
| RAPL validation err (5-run range, median) | 4.20–36.07% (18.66%) | 4.17–34.55% (8.20%) | 2.45–27.18% (5.88%) | 27.36–46.77% (42.02%) | 43–60% (pin) | 43–60% (pin) |
| energy/carbon | validated | validated | validated (ss) | estimate only | no (pin calibration) | no (pin calibration) |

*Fn/OF bare c=4 rows are RAPL-validated and contention-free (quiet 2026-08-05 baseline — the same
values figure1's 8-core bars use, so the regime table and the core-count figure agree; the lock4
session re-reads Fn/OF at 11.29/7.58, gap +3.71 pp, within the documented drift band — Appendix A).
Kn/OW bare rows are the 2026-08-14 lock4 session (Appendix A — quiet-gated,
count-complete, fresh per-leg `idle_w`); all QoS percentiles above are
citable (✓ flags); four earlier snapshots of the four-platform rows (2026-08-07 agent-contaminated,
2026-08-08 morning collected before the OpenFaaS/Knative classification fix, the 2026-08-08/09
two-day stitch, and the 2026-08-13/14 publication-lock pair) are all superseded (git history only,
not cited). The 2-core pinned rows are the
controlled core-count experiment: full
REPEAT=5 protocol, all gates green on the share, but `host_saturated=true` (98.5–98.7%, expected
at c=4 > 2 cores) makes latency from that pair not citable, and energy is not citable without
re-deriving idle-w for the pinned configuration (RAPL err 43–60%, §5.5 caveat). Full per-run
tables: `SAQEF_TECHNICAL_REPORT.md` §30.1, `results/*_cpubound_*`.*

---

*Everything in this draft is traceable to `SAQEF_TECHNICAL_REPORT.md` and executable via `run_saqef.sh all`.*
