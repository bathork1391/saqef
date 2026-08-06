# The Hidden Cost of Orchestration: A Sustainability-Aware QoS Evaluation Framework (SAQEF)

**Working title — Paper Draft (v0.1)**

**Author:** [Name], Green Cloud Continuum project
**Date:** 2026-08-03
**Status:** Methodology validated on Fn + OpenFaaS, RAPL-validated on bare metal (two machines,
two regimes). Draft for refinement into the final research paper.
**Source of record:** `SAQEF_TECHNICAL_REPORT.md` (full measurement log) + `saqef_harness.py` (instrumentation) + `run_saqef.sh` (one-command reproduction).

> **How to use this document.** This is the consolidated methodology + results narrative, written in paper structure. Every claim is traceable to the technical report and the harness. Sections marked **[CANDID]** are honest gaps that must be closed before submission; they are intentional, not omissions. As experiments expand (OpenFaaS, OpenWhisk, bare metal), fill in the marked placeholders and keep this document as the single narrative.

---

## Abstract

Serverless computing shifts infrastructure management to platform operators, but the *orchestration overhead* — the control-plane work that schedules, freezes, and coordinates function invocations — is rarely charged to the function. This paper presents **SAQEF**, a sustainability-aware QoS evaluation framework that attributes CPU time and energy to the control plane versus the function under controlled load, cross-validated against direct kernel counters and, on bare metal, against RAPL (steady-state error 4–8%). We apply it to two platforms (Fn and OpenFaaS) serving an identical CPU-bound 5 ms function. The headline finding is that the control plane's share of dynamic CPU — and therefore the Fn-vs-OpenFaaS gap — is a property of the machine's core count, not of the platform alone. On an 8-core box, Fn's control plane consumes **10.5% of dynamic CPU** vs OpenFaaS's **7.7%** (gap 2.8 pp, below our 5 pp discrimination gate). Cpuset-pinning the same box to 2 cores — same protocol, same instrument, all validation gates green, reproduced in two independent sessions — raises Fn to **14.0%** while OpenFaaS stays at **7.0%** (gap 7.1 pp, 1.3% CV across sessions): core scarcity inflates Fn's control-plane overhead specifically, an asymmetric, platform-specific sensitivity. **The 5 pp gate is a per-machine-pair quantity, not a platform constant**; the machine-dependence and its asymmetry are the central contributions. The framework's built-in delta-check also caught and eliminated a 52× sampling overcount during development, demonstrating that the validation approach works as intended.

---

## 1. Introduction & Motivation

Serverless (FaaS) platforms promise "scale to zero," pay-per-invocation pricing, and operational simplicity. Their environmental cost, however, is hidden in two places: (i) the **idle baseline** of always-on gateways, schedulers, and coordinators, and (ii) the **dynamic overhead** of orchestrating each invocation — route lookups, container spawn/freeze churn, queueing, and watchers. When operators report "the control plane uses ~2% of CPU," they are describing the *static* fraction of total machine capacity — which, on an idle-leaning, co-tenanted VM, obscures the fact that the marginal work attributable to a function is disproportionately orchestration.

**Central claim:** for a light CPU-bound function, the control plane is not a rounding error — on an 8-core box it is ~10% of the marginal (dynamic) CPU cost for Fn and ~8% for OpenFaaS, and its share — and the platform gap — grows as the machine gets smaller (Fn 14.0% vs OpenFaaS 7.0% when the same box is pinned to 2 cores; see §5.5). Orchestration is a first-class, capacity-dependent cost, not an overhead line item.

**Contributions:**
1. A reproducible, platform-agnostic measurement harness (`saqef_harness.py`) that attributes CPU/energy between control plane and function under controlled load, with built-in self-validation (delta-check, host-plausibility, coverage, platform-isolation assertions).
2. A workload-anchored methodology that fixes the "ratio of tiny quantities" instability that plagues no-op-workload energy comparisons, plus a frequency-invariance argument: cgroup CPU-time ratios are invariant-TSC wall-time, so the headline share is robust to per-core frequency/turbo differences by construction (§5.5 caveat, report §31.8).
3. The first RAPL-validated cross-platform control-plane overhead numbers on bare metal, with the core-count dependence **quantified by a controlled same-instrument experiment** (8-core i5-1145G7): Fn 10.5% vs OpenFaaS 7.7% of dynamic CPU at 8 cores (gap +2.8 pp, below the 5 pp gate), rising to Fn 14.0% vs OpenFaaS 7.0% (gap +7.0 pp, reproduced to 0.2 pp in two independent sessions) when the same box is cpuset-pinned to 2 cores — an **asymmetric, platform-specific core-scarcity sensitivity** (Fn's share inflates, OpenFaaS's stays flat).
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
- **RQ3 (comparison):** Does the framework discriminate between platforms (Fn vs OpenFaaS)? — **answered (2026-08-06, core-count confirmed):** yes, with a caveat. On the 2-vCPU saturated codespace the gap is 8.8 pp (gate passes); on the 8-core bare-metal box it is 2.8 pp (gate fails), flat with concurrency within each machine. A controlled same-instrument test (this 8-core box cpuset-pinned to 2 cores) confirms the gap is core-count-driven: it returns to 7.1 pp at 2 pinned cores. Direction is stable (Fn's share higher everywhere); the *magnitude* is a machine-pair property, and the mechanism is asymmetric — Fn's share is what inflates under core scarcity, not both platforms proportionally (see §5.5). OpenWhisk remains future work.

---

## 4. Methodology

### 4.1 Design overview

A single **measurement window** synchronizes: (a) QoS load generation, (b) per-container CPU sampling, and (c) pre/post direct-counter reads for validation. The window is repeated N times; per-run ratios are computed *inside* each run and then summarized (never reconstructed from independently-medianed components — §4.6).

Two energy scopes are reported, because they answer different questions:
- **`cp_share_pct`** = control-plane energy / *total* machine energy (idle + dynamic). Answers: "how much of the *facility* cost is orchestration?" (small here: 1.9%).
- **`cp_dynamic_share_pct`** = control-plane energy / *dynamic* (function + control-plane) energy. Answers: "of the *marginal work that load creates*, how much is orchestration?" (**the headline; 10.5% on the 8-core box, 14.0% at 2 pinned cores — §5.5**).

### 4.2 Environment

| | Value (codespace) | Value (bare metal, 2026-08-05) |
|---|---|---|
| Host | GitHub Codespaces, x86_64, 2 vCPU, Docker 29.3.0, Python 3.12 | 8-core Ubuntu, 16 GB, docker + swarm, RAPL readable |
| CPU | 2 shared vCPU (cloud VM, co-tenanted) | 11th Gen Intel Core i5-1145G7 @ 2.60 GHz — 4 cores / 8 threads, 1 socket, turbo to 4.4 GHz, governors ondemand/performance (3.30 GHz loaded at c=4; 3.60 GHz when pinned to 2 cores, report §31.8) |
| Platform | Fn — `fnproject/fnserver:latest` (0.3.x), containerized, iofs socket fix | Fn 0.3.x; OpenFaaS 0.8.3 (6-container CP, of-watchdog) |
| Function runtime | Python 3.12 FDK (`fnproject/python:3.12`) | Python FDK (hello:0.0.7 Fn / hello:latest OF) |
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

Run profile: 3000 requests, concurrency 20, warmup 20, repeat 5, SLO 500 ms, duration cap 60 s. Runs are **count-bound** (exactly N requests per platform; `--duration` is a *safety cap*, not a hard stop) so cross-platform windows are identical in composition even when wall time differs. Publication runs: repeat ≥ 10 (§4.6). **Every benchmark session starts from a freshly-restarted fnserver** (`reset` in `run_saqef.sh`): leftover warm/zombie function containers from a prior session were found to fold into `fn_cpu` under the old denylist and inflate it (report §18), so a reused server is no longer measurable via the runner.

### 4.4 Instrumentation

**Sampler (v9 architecture).** A background thread reads per-container CPU. Two modes:
- `--sampler cgroup` (primary): maps each running container to its cgroup dir and reads the **raw cumulative CPU seconds** (`cpu.stat` → `usage_usec`). Samples are stored as cumulative counters with true wall timestamps.
- `--sampler docker` (fallback): 1 Hz `docker stats` percentages.

**Key design decision — raw cumulative + true-timestamp differencing.** The reducer differences *consecutive cumulative reads* using *true sample timestamps* (`dt = t_{i+1} - t_i`). This makes the totals **exact regardless of sampling cadence** — slow cgroup reads, scheduling jitter, or spurious wakeups cannot bias the integral (the reducer never re-multiplies by a sampler-internal dt). This property is what lets the delta-check pass to 0.01% even when sampling coverage is low on the nested mount (§5.3). Memory is captured the same way (`memory.current` / `memory.usage_in_bytes`) so `cp_peak_mem_mb` is a real measurement in cgroup mode (v9.2+).

**Classification is allowlist-based.** Control-plane = containers matching `--cp-containers`/`--cp-images`/`--cp-labels`; function = containers matching `--fn-containers`/`--fn-images`/`--fn-labels`. When no function allowlist is given, *everything not CP* is function (denylist default, back-compat). When an allowlist IS given, any container matching neither CP nor function goes to a logged `unclassified_cpu_s` bucket (warn if > 0.5 CPU-s) — **so a stray container can never silently inflate `fn_cpu`**; and if the allowlist matches *nothing*, the harness warns instead of silently falling back (a wrong image is loud). `run_saqef.sh` passes `--fn-images hello` by default — the **deployed** function image name, not the base runtime `fnproject/python:*` (Fn names function containers with opaque ULIDs, and the running containers carry the built image, so the deployed image is the only reliable signal). `container_inventory` (names) and `container_labels` (name → image + labels) are embedded in every summary for audit. cgroup rescans run every `--rescan-s` s (default 0.25) to bound the blind spot for containers born and dying within one scan.

**`--delta-check` (independent validation).** The cumulative counters of **all** control-plane containers matching `--cp-containers` are read *directly* immediately before and after the window (container list re-resolved on each read, so swarm task restarts cannot wedge the reader); the summed direct delta is compared with the sampler's accumulated total: `cp_sampler_vs_delta_pct = (sampler_total / direct_delta - 1) × 100`. ≈0 ⇒ the whole sampling path is validated. (v9.9 fix: the reader originally snapshotted only the *first* matching container, so a multi-container control plane — OpenFaaS = 6 containers — compared the sampler sum against a single idle container's counter and reported a spurious ~18900% mismatch; summing the whole set makes the check like-for-like.)

**`--idle-probe` (static baseline).** Platform up, zero traffic for `--duration` s: measures the orchestration baseline that exists even with no invocations (gateway, scheduler daemons).

**Load generator.** `hey` (external Go binary) removes the harness's own Python/GIL footprint from host accounting; falls back to a Python generator. QoS percentiles parsed from `hey -o csv` per-request rows (the only machine-readable mode mainline hey ships) or measured request latencies. As of v9.9, real `hey` runs are the default on both platforms (the earlier JSON-era failure is fully diagnosed — see corrections below).

### 4.5 Metrics & energy/carbon model

```
cpu_sec(container) = Σ (cum_{i+1} - cum_i)                    # cgroup mode (exact)
dynamic_J(container) = cpu_sec × P_BUSY_CORE_W                # 3.5 W/busy core (Caribou, SOSP'24)
total_J = P_IDLE_BASE_W × wall_s + Σ dynamic_J                # idle 30 W baseline
carbon_gCO2 = kWh × PUE × CI                              # kWh = J / 3.6e6; PUE 1.15, CI 150 gCO2/kWh
                                                          # (historical bug: J/3600 gave Wh, i.e. a spurious
                                                          # 1000× in every gCO2 figure -- fixed 2026-08-06)
cp := containers matching --cp-containers (here: "fnserver")
KPI = operational_gCO2 / N_SLO-compliant_invocations        # incl. idle baseline (window-dependent)
KPI_dynamic = dynamic_gCO2 / N_SLO-compliant_invocations    # load-created carbon only (wall-independent)
embodied_DRAM = 1390 gCO2/GB ÷ (5 yr × 8760 h)                # amortized, reported for context
```

Constants: `P_BUSY_CORE_W = 3.5`, `P_IDLE_BASE_W = 30.0`, `PUE = 1.15`, `CI = 150 gCO2/kWh`, `SAMPLE_S = 1.0`.

**This is estimation, not measurement** for absolute Joules (±50% typical on absolute energy). It is defensible as: (a) a transparent, reproducible model; (b) a *relative* cross-platform comparator on identical hardware; (c) RAPL-validated on bare metal (pending).

**Model constant sources & sensitivity.** `busy_core_w = 3.5` follows Caribou (SOSP '24) per-core dynamic power; `idle_w = 30` is a typical low-end server idle draw (the SPECpower family is the standard methodology for server power values). Neither is chip-measured here (see §7). Every summary emits a `sensitivity` block that recomputes the dynamic share, dynamic energy, and operational carbon at busy-core 2/3.5/5 W, plus `idle_band` (carbon at idle 15/30/45 W): the **dynamic share is invariant to the busy-core constant** (it cancels in the ratio), so the headline ratio is model-robust; absolute energy/carbon scale linearly and are honestly bounded by these bands until RAPL ground truth exists.

### 4.6 Statistical protocol

- N=5 repeats per configuration (N≥10 for publication); every numeric leaf summarized as **median + min/max spread**.
- **Ratios are computed per-run and then medianed** (never reconstructed from medians of raw components) — prevents internally-impossible aggregated ratios.
- `bootstrap_ci` (percentile bootstrap over runs), `cv_pct`, **and `iqr` (Q3−Q1)** report repeat-run uncertainty. At N=5 the bootstrap CI mostly reflects resampling combinatorics, so the IQR is the honest companion measure; the paper reports both.
- JSON outputs sanitized (NaN/Inf → null) for strict downstream parsers.

### 4.7 Validation gates (must ALL pass per run)

| Gate | Definition | Accept |
|---|---|---|
| Delta-check | `cp_sampler_vs_delta_pct` | ≈ 0 (single-digit %) |
| Physical plausibility | `cpu_sec.fn + cpu_sec.cp ≤ cpu_count × wall_s` | true |
| **Host plausibility** | `host_cpu_sec ≤ cpu_count × host_window_s × 1.05`, where `host_window_s` is the host's *own* sampling window (`t_host_after − t_host_before`), not the load `wall_s` (v9.10) — self-consistent by construction: `/proc/stat` busy ticks over a window `W` can never exceed `cpu_count × W`, so the gate trips only on a real CPU-count/counter anomaly, never on fast-run window-edge alignment | true |
| **Host saturation** | `host_cpu_sec / (cpu_count × host_window_s)` | report per run; `host_saturated` flag (v9.8, enforced in code) if ≥ 85% — QoS is contention-contaminated |
| Coverage | `sampling_covered_s / wall_s` | ≥ 95% (bare-metal target) |
| QoS integrity | availability, SLO compliance | ≥ 99% |
| Determinism | two independent runs reproduce | within repeat variance |

---

## 5. Results (Fn, validated — median of 5 runs × 2 independent reproductions)

### 5.1 QoS

| Metric | Value |
|---|---|
| Requests / success | 3000 / 3000 (availability 1.0) |
| Throughput | 205.96 rps |
| Latency p50 / p90 / p99 / max | 83.6 / 151.8 / 308.6 / 569.6 ms |
| SLO compliance (500 ms) | 99.97% |

### 5.2 Energy & CPU attribution

> Worked example from the original (superseded, saturated-codespace) run, illustrating the
> attribution math; current citable values are in §5.5.

| Metric | Value | Meaning |
|---|---|---|
| `cpu_sec.control_plane` | 2.61 s | fnserver CPU over 14.6 s window |
| `cpu_sec.function` | 6.00 s | function containers (lower bound — see §7) |
| `cpu_sec_ceiling` | 29.13 s | 2 cores × 14.57 s (physical max) |
| `cp_share_pct` | 1.94% | CP / total machine energy |
| **`cp_dynamic_share_pct`** | **30.3%** | CP / (function + CP) dynamic energy |
| Dynamic energy | 30.1 J | CP 9.1 J + function 21.0 J |
| Total energy (window) | 467 J | 30 W idle × 14.6 s + 30.1 J dynamic |
| `cp_peak_mem_mb` | pending v9.2 re-run | memory now captured in cgroup mode (was 0.0) |

### 5.3 Validation results

| Gate | Result |
|---|---|
| `cp_sampler_vs_delta_pct` | **0.01%** (sampler = direct counter) |
| `cp_delta_sec` vs sampler CP | 2.607 ≈ 2.61 s (exact) |
| `physical_plausible` | true on all 5 runs |
| Reproduction | 2nd full run: delta 0.01%, share 30.32% (Δ < 0.3 pp) |

### 5.4 Absolute per-invocation overhead (model-based)

| Quantity | Per invocation |
|---|---|
| Control-plane CPU | 2.61 s / 3000 = **0.87 ms CPU** |
| Control-plane dynamic energy | 9.1 J / 3000 = **3.03 mJ** |
| Control-plane carbon (dynamic) | ≈ **0.145 µg CO₂** |
| Total operational carbon (incl. idle base) | ≈ **7.5 µg CO₂** (idle dominates: ~94%) |
The idle-dominance is itself a result: at this light load, **~94% of operational carbon is the always-on baseline**, and the marginal cost of serving is split 30/70 orchestration/function. This is precisely the regime where autoscaling ("scale to zero") pays off — and where the orchestration tax is most visible per unit of useful work.

### 5.5 Cross-platform, RAPL-validated results (bare metal, 2026-08-05)

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
| saturated, flawed instrument | 2-vCPU shared VM, c=20 | 24.59 | 15.82 | +8.8 pp |
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
cores, close to the original flawed-instrument codespace gap (8.8 pp) and far from the clean
8-core gap. **Core count driving the gap's magnitude is therefore an earned, reproduced finding,
not an inference across mismatched instruments.**

The mechanism is *not* the symmetric "~2.3× on both platforms" pattern an earlier (unconfirmed)
reading of the flawed 2-vCPU data suggested. The controlled data shows an asymmetric effect: Fn's
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

---

## 6. Discussion

**Why the headline is `cp_dynamic_share_pct`, not `cp_share_pct`.** The static share (`cp_share_pct`) is what an operator sees on a dashboard; it hides the fact that the *marginal* cost of serving a function is dominated by orchestration — the dynamic share is ~10–14% and core-count dependent (§5.5). The dynamic share is the economically relevant quantity for per-invocation pricing, carbon-aware scheduling, and "green function" claims.

**Workload anchoring.** Ratios over near-zero denominators are noise (the no-op workload case). A CPU-anchored function makes the dynamic share measurable and reproducible (±3 pp across runs, ±0.3 pp across reproductions).

**What is measured vs estimated (honest line).** Measured directly: QoS, per-container CPU (validated to 0.01% against direct counters), physical plausibility, RAPL package Joules (bare metal). Modeled: absolute Joules and carbon (CPU-time proportionality, literature constants). The **relative** dynamic share is robust to the model constant (3.5 W/core scales both numerator and denominator), so `cp_dynamic_share_pct` is the most defensible number we produce.

---

## 7. Threats to Validity (explicit)

1. **No RAPL (closed for bare metal).** On the codespace, absolute energy/carbon remain model estimates. On the 8-core bare-metal box the model is RAPL-validated to 4.2–8.2% steady-state (idle calibrated 4.3 W, not the 30 W default). The codespace absolute numbers carry the old caveat; the relative `cp_dynamic_share_pct` is model-constant-robust everywhere.
2. **Function CPU is a lower bound.** Function containers live 2–5 s and are only partially captured by the sparse nested-mount sampler (`sampling_covered_s` ranged 13–100% across runs; totals stay exact for *sampled* containers because of cumulative differencing). CP is exact (proven); treat `cpu_sec.function` as ≥ the reported value. Improves on bare metal.
3. **Co-tenanted shared VM (codespace only).** Host-level metrics on the 2-vCPU codespace include neighbor noise; excluded from claims (definition unchanged: `orchestration_cpu_sec` is a host-wide residual, never presented as pure orchestration). The bare-metal 8-core box is dedicated, so host metrics there are clean; the cgroup-exact control-plane container share (`cp_dynamic_share_pct`) is the claim on both machines.
4. **Contention-contaminated QoS (closed for bare-metal c=4).** On the codespace, `host_saturation_pct` ≈ 100% made latency percentiles reflect scheduler contention, not intrinsic platform overhead. On the 8-core box at c=4 (host_sat 74–77%) QoS is citable for the first time: Fn p50 6.5 / p99 8.9 ms @ 597 rps; OF p50 7.2 / p99 12.1 ms @ 532 rps; SLO 1.0. The c=8 quick runs (sat 91–93%) carry the `host_saturated` flag and their latency is NOT citable — consistent with the discipline that a saturated box measures reproducibly wrong.
5. **Two platforms, two machines.** Fn and OpenFaaS are measured on both the 2-vCPU codespace and the 8-core bare-metal box. The discriminator's magnitude is machine-dependent (RQ3 answer, §5.5) — a bounded threat that is now quantified rather than unknown. OpenWhisk remains **[CANDID]**.
6. **Control plane measured as one container** (`fnserver`), not decomposed into gateway/scheduler/queue sub-components. **[CANDID]**: profiling inside the control plane.
7. **Model constants** (idle 30 W, 3.5 W/core, PUE 1.15, CI 150) are literature defaults; CI in particular is regional/temporal.
8. **Two machines only; the machine-dependence is itself the new threat.** The share scales with machine capacity (~2.3× between 2-vCPU and 8-core; flat with load on a fixed machine). A third machine (e.g., a different core count / NUMA) would bound the trend; the paper must present the per-machine-pair gate so reviewers can apply the framework to their own hardware.

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
| G6 | cross-session repeatability | multi-session median discipline; `cv_pct`/`iqr`/`bootstrap_ci` within a session, session medians across sessions | `cp_dynamic_share_pct` = 23.88 / 24.38 / 24.59 / 24.07 / 23.59 across five clean sessions (1.0 pp total spread), covering py and hey loadgens |
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

1. ~~**OpenFaaS** (same function image, same protocol)~~ — **done (2026-08-05)**: gap 8.8 pp on the codespace, 2.8 pp on the 8-core box (per-machine-pair gate, §5.5).
2. **OpenWhisk** (and optionally Fission) — cross-platform comparison.
3. ~~**Bare metal** (dual-boot Ubuntu) — RAPL ground truth~~ — **done (2026-08-05)**: RAPL-validated 4.2–8.2%, idle 4.3 W. Remaining: a *third* machine to bound the machine-dependence trend.
4. **Control-plane decomposition** — which fnserver subcomponent (gateway, scheduler, freeze manager, watchers) costs what.
5. **Cold-start vs warm** — `--interarrival-ms 1000` isolation experiment: the "carbon cost of elasticity."
6. **Freeze-policy ablation** — `FN_FREEZE_IDLE_MSECS=0` vs default: quantify Fn's pause/unpause churn in energy terms.
7. **Realistic mixed workloads** — CPU/IO mixture, not just pure spin.

---

## 11. Research impact & how the results can be used

- **Methodology reuse:** the harness + delta-check pattern is a drop-in instrument for anyone measuring FaaS energy on any platform (container-level, cgroup-validated, honest about estimation).
- **Informing models:** Kepler-style CPU-time proportionality gains a real orchestration-overhead term (Fn-class platforms ≈10–30% of dynamic energy depending on machine capacity; OpenFaaS-class ≈8–16%), so serverless energy models stop treating orchestration as ~0.
- **Carbon-aware scheduling:** a per-invocation orchestration cost (bare metal: Fn 0.66 ms CPU ≈ 2.3 mJ dynamic CP-only; OF 0.56 ms ≈ 2.0 mJ) makes it possible to route work to the cheapest control plane — and to price "green functions" correctly.
- **Design guidance:** autoscaling/scale-to-zero economics quantified — the ~94% idle-baseline share (codespace) quantifies how much idle waste elasticity can reclaim, and the machine-dependence result says orchestration overhead is *capacity-bound*: it buys back fast when functions get more cores, so co-locating functions on fewer, larger boxes (or vice-versa) directly tunes the orchestration tax.
- **Framework portability (new):** the discriminator is a per-machine-pair quantity with a stable *ranking*; the paper provides the recipe (protocol + gates) so a third platform or machine can be ranked without re-deriving the methodology.

---

## Appendix A — Consolidated results table

| Metric | Fn (v9.1, median) | Spread (min–max) | Units |
|---|---|---|---|
| throughput | 205.96 | 202–223 | rps |
| SLO compliance | 0.9997 | 0.990–1.0 | frac |
| latency p50 | 83.6 | 54.5–84.4 | ms |
| latency p99 | 308.6 | 307–507 | ms |
| `cp_dynamic_share_pct` | 30.3 | 30.2–44.4 | % |
| `cp_share_pct` | 1.94 | 1.86–1.95 | % |
| dynamic energy | 30.1 | 18.4–30.6 | J |
| `cp_peak_mem_mb` | pending v9.2 | — | MB |
| KPI (op. gCO₂ per SLO-compliant invocation, incl. idle base) | ≈7.5 | — | µg CO₂ |
| control-plane carbon per invocation (dynamic only) | ≈0.145 | — | µg CO₂ |
| `cp_sampler_vs_delta_pct` | 0.01 | 0.01–0.15 | % |
| `physical_plausible` | true | true | bool |

*Table values are v9.1 medians; KPI and `cp_peak_mem_mb` reflect the v9.2 formulas and await the v9.2 re-run to be confirmed against fresh samples.*

### Appendix B — Consolidated cross-platform table (bare metal + codespace, 2026-08-05)

| Metric (median of 5) | Fn (bare c=4) | OF (bare c=4) | Fn (codespace c=20) | OF (codespace c=20) |
|---|---|---|---|---|
| `cp_dynamic_share_pct` | 10.46 | 7.67 | 24.59 | 15.82 |
| gap (pp) | **+2.79 (gate fails)** | | **+8.77 (gate passes)** | |
| per-request CP cost | 0.66 ms | 0.56 ms | 1.22 ms | 0.56 ms |
| QoS p50 / p99 | 6.5 / 8.9 ms | 7.2 / 12.1 ms | 83.6 / 308.6 ms* | — |
| throughput | 597 rps | 532 rps | 206 rps* | — |
| SLO compliance | 1.0 | 1.0 | 0.9997* | 1.0 |
| host_sat | 74–77% | 74–78% | ~100% | ~99% |
| RAPL validation err | 4.2–5.5% | 4.2–8.2% | n/a (no RAPL) | n/a |

*Fn codespace QoS rows are the saturated-regime v9.1 single-platform dataset (not contention-free);
keep the `host_saturated` caveat when quoting. Bare-metal rows are RAPL-validated and
contention-free. Full per-run tables: `SAQEF_TECHNICAL_REPORT.md` §30.1.

---

*Everything in this draft is traceable to `SAQEF_TECHNICAL_REPORT.md` and executable via `run_saqef.sh all`. Update this document as each [CANDID] gap closes.*
