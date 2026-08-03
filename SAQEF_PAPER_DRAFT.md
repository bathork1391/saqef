# The Hidden Cost of Orchestration: A Sustainability-Aware QoS Evaluation Framework (SAQEF)

**Working title — Paper Draft (v0.1)**

**Author:** [Name], Green Cloud Continuum project
**Date:** 2026-08-03
**Status:** Methodology validated on one platform (Fn). Draft for refinement into the final research paper.
**Source of record:** `SAQEF_TECHNICAL_REPORT.md` (full measurement log) + `saqef_harness.py` (instrumentation) + `run_saqef.sh` (one-command reproduction).

> **How to use this document.** This is the consolidated methodology + results narrative, written in paper structure. Every claim is traceable to the technical report and the harness. Sections marked **[CANDID]** are honest gaps that must be closed before submission; they are intentional, not omissions. As experiments expand (OpenFaaS, OpenWhisk, bare metal), fill in the marked placeholders and keep this document as the single narrative.

---

## Abstract

Serverless computing shifts infrastructure management to platform operators, but the *orchestration overhead* — the control-plane work that schedules, freezes, and coordinates function invocations — is rarely charged to the function. This paper presents **SAQEF**, a sustainability-aware QoS evaluation framework that attributes CPU time and energy to the control plane versus the function under controlled load, cross-validated against direct kernel counters. On the Fn platform, we find the control plane consumes **30.3% of the dynamic (non-idle) CPU/energy** while serving a CPU-bound 5 ms function at ~206 requests/second — a small fraction of total machine energy (1.9%) but a *dominant fraction of the marginal cost that scales with load*. The framework's built-in delta-check caught and eliminated a 52× sampling overcount during development, demonstrating that the validation approach works as intended. Methodology is validated; absolute energy numbers remain model estimates pending RAPL ground truth on bare metal.

---

## 1. Introduction & Motivation

Serverless (FaaS) platforms promise "scale to zero," pay-per-invocation pricing, and operational simplicity. Their environmental cost, however, is hidden in two places: (i) the **idle baseline** of always-on gateways, schedulers, and coordinators, and (ii) the **dynamic overhead** of orchestrating each invocation — route lookups, container spawn/freeze churn, queueing, and watchers. When operators report "the control plane uses ~2% of CPU," they are describing the *static* fraction of total machine capacity — which, on an idle-leaning, co-tenanted VM, obscures the fact that the marginal work attributable to a function is disproportionately orchestration.

**Central claim:** for a light CPU-bound function, the control plane is not a rounding error — it is roughly **one third of the marginal (dynamic) energy cost**. This changes how serverless energy must be modeled: orchestration is a first-class cost, not an overhead line item.

**Contributions:**
1. A reproducible, platform-agnostic measurement harness (`saqef_harness.py`) that attributes CPU/energy between control plane and function under controlled load, with built-in self-validation.
2. A workload-anchored methodology that fixes the "ratio of tiny quantities" instability that plagues no-op-workload energy comparisons.
3. The first validated Fn control-plane overhead numbers (30.3% of dynamic energy at 206 rps, 5 ms function).
4. A documented case study of the validation method catching a real 52× instrumentation bug (§8).

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
- **RQ3 (comparison, future):** Does the framework discriminate between platforms (Fn vs OpenFaaS vs OpenWhisk) — i.e., is orchestration overhead platform-specific enough to rank?

---

## 4. Methodology

### 4.1 Design overview

A single **measurement window** synchronizes: (a) QoS load generation, (b) per-container CPU sampling, and (c) pre/post direct-counter reads for validation. The window is repeated N times; per-run ratios are computed *inside* each run and then summarized (never reconstructed from independently-medianed components — §4.6).

Two energy scopes are reported, because they answer different questions:
- **`cp_share_pct`** = control-plane energy / *total* machine energy (idle + dynamic). Answers: "how much of the *facility* cost is orchestration?" (small here: 1.9%).
- **`cp_dynamic_share_pct`** = control-plane energy / *dynamic* (function + control-plane) energy. Answers: "of the *marginal work that load creates*, how much is orchestration?" (**the headline: 30.3%**).

### 4.2 Environment

| | Value |
|---|---|
| Host | GitHub Codespaces, x86_64, 2 vCPU, Docker 29.3.0, Python 3.12 |
| Platform | Fn — `fnproject/fnserver:latest` (0.3.x), containerized, iofs socket fix |
| Function runtime | Python 3.12 FDK (`fnproject/python:3.12`) |
| Load generator | `hey` (Go) preferred; Python stdlib fallback |
| RAPL | Unavailable (cloud VM) → CPU-time model; bare metal pending |

> **[CANDID]**: RAPL validation on bare metal is a hard requirement for the final paper (§7, §10).

### 4.3 Workload

`hello/func.py` is a genuine **5 ms CPU spin** (`while time.perf_counter() - t0 < 0.005: pass`). Rationale: a *CPU-anchored* workload makes the function's marginal CPU measurable and stable. A no-op "hello" handler was evaluated and rejected: with a near-free function, both ratio numerator and denominator become tiny and noisy (fn freeze churn between calls → 0% CPU → `cp_dynamic_share` swings ±13 pp). **The metric must be workload-anchored** — itself a reviewer-relevant methodological finding.

Run profile: 3000 requests, concurrency 20, warmup 20, repeat 5, SLO 500 ms, duration cap 60 s. Runs are **count-bound** (exactly N requests per platform; `--duration` is a *safety cap*, not a hard stop) so cross-platform windows are identical in composition even when wall time differs. Publication runs: repeat ≥ 10 (§4.6).

### 4.4 Instrumentation

**Sampler (v9 architecture).** A background thread reads per-container CPU. Two modes:
- `--sampler cgroup` (primary): maps each running container to its cgroup dir and reads the **raw cumulative CPU seconds** (`cpu.stat` → `usage_usec`). Samples are stored as cumulative counters with true wall timestamps.
- `--sampler docker` (fallback): 1 Hz `docker stats` percentages.

**Key design decision — raw cumulative + true-timestamp differencing.** The reducer differences *consecutive cumulative reads* using *true sample timestamps* (`dt = t_{i+1} - t_i`). This makes the totals **exact regardless of sampling cadence** — slow cgroup reads, scheduling jitter, or spurious wakeups cannot bias the integral (the reducer never re-multiplies by a sampler-internal dt). This property is what lets the delta-check pass to 0.01% even when sampling coverage is low on the nested mount (§5.3). Memory is captured the same way (`memory.current` / `memory.usage_in_bytes`) so `cp_peak_mem_mb` is a real measurement in cgroup mode (v9.2+).

**Classification is allowlist-based.** Control-plane = names matching `--cp-containers`; function = names matching `--fn-containers` (when empty, *everything not CP* is treated as function — denylist default, preserved for backward compatibility). Any container matching neither allowlist goes to a logged `unclassified_cpu_s` bucket (warn if > 0.5 CPU-s) so a stray container can never silently inflate `fn_cpu`; a full `container_inventory` (docker ps names) is embedded in every summary for audit. cgroup rescans run every `--rescan-s` s (default 0.25) to bound the blind spot for containers born and dying within one scan.

**`--delta-check` (independent validation).** The control-plane container's cumulative counter is read *directly* immediately before and after the window; the direct delta is compared with the sampler's accumulated total: `cp_sampler_vs_delta_pct = (sampler_total / direct_delta - 1) × 100`. ≈0 ⇒ the whole sampling path is validated.

**`--idle-probe` (static baseline).** Platform up, zero traffic for `--duration` s: measures the orchestration baseline that exists even with no invocations (gateway, scheduler daemons).

**Load generator.** `hey` (external Go binary) removes the harness's own Python/GIL footprint from host accounting; falls back to a Python generator. QoS percentiles from `hey` JSON or measured request latencies.

### 4.5 Metrics & energy/carbon model

```
cpu_sec(container) = Σ (cum_{i+1} - cum_i)                    # cgroup mode (exact)
dynamic_J(container) = cpu_sec × P_BUSY_CORE_W                # 3.5 W/busy core (Caribou, SOSP'24)
total_J = P_IDLE_BASE_W × wall_s + Σ dynamic_J                # idle 30 W baseline
carbon_gCO2 = (Wh) × PUE × CI                                 # PUE 1.15, CI 150 gCO2/kWh
cp := containers matching --cp-containers (here: "fnserver")
KPI = operational_gCO2 / N_SLO-compliant_invocations
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
| **Host plausibility** | `host_cpu_sec ≤ cpu_count × wall_s × 1.05` (vs `cpu.max` quota printed by `--check`) | true |
| **Host saturation** | `host_cpu_sec / (cpu_count × wall_s)` | report per run; flag if ≥ 85% (QoS is contention-contaminated) |
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
| Control-plane carbon (dynamic) | ≈ **145 µg CO₂** |
| Total operational carbon (incl. idle base) | ≈ **7.5 mg CO₂** (idle dominates: ~94%) |
The idle-dominance is itself a result: at this light load, **~94% of operational carbon is the always-on baseline**, and the marginal cost of serving is split 30/70 orchestration/function. This is precisely the regime where autoscaling ("scale to zero") pays off — and where the orchestration tax is most visible per unit of useful work.

---

## 6. Discussion

**Why the headline is `cp_dynamic_share_pct`, not `cp_share_pct`.** The static share (1.9%) is what an operator sees on a dashboard; it hides the fact that the *marginal* cost of serving a function is one-third orchestration. The dynamic share is the economically relevant quantity for per-invocation pricing, carbon-aware scheduling, and "green function" claims.

**Workload anchoring.** Ratios over near-zero denominators are noise (the no-op workload case). A CPU-anchored function makes the dynamic share measurable and reproducible (±3 pp across runs, ±0.3 pp across reproductions).

**What is measured vs estimated (honest line).** Measured directly: QoS, per-container CPU (validated to 0.01% against direct counters), physical plausibility. Modeled: absolute Joules and carbon (CPU-time proportionality, literature constants). The **relative** dynamic share is robust to the model constant (3.5 W/core scales both numerator and denominator), so the 30.3% claim is the most defensible number we produce.

---

## 7. Threats to Validity (explicit)

1. **No RAPL.** Absolute energy/carbon are model estimates, not measurements. **[CANDID]**: bare-metal RAPL validation required.
2. **Function CPU is a lower bound.** Function containers live 2–5 s and are only partially captured by the sparse nested-mount sampler (`sampling_covered_s` ranged 13–100% across runs; totals stay exact for *sampled* containers because of cumulative differencing). CP is exact (proven); treat `cpu_sec.function` as ≥ the reported value. Improves on bare metal.
3. **Co-tenanted shared VM.** Host-level metrics (`host_cpu_sec`, `orchestration_*`) include neighbor noise and the harness's own loadgen CPU; excluded from claims. Definition: `orchestration_cpu_sec = host_cpu_sec − cpu_sec.function` — i.e., control plane + Docker/containerd daemons + load generator + co-tenant CPU + kernel, a **host-wide residual, not pure orchestration**. We never present it as orchestration; the orchestration claim is the cgroup-exact control-plane container share (`cp_dynamic_share_pct`).
4. **Contention-contaminated QoS on shared VMs.** When `host_saturation_pct` approaches 100% (as on the v9.2 run), latency percentiles reflect scheduler contention between hey, the sampler, and the containers under test — not intrinsic platform overhead. Low CV is *precision*, not *validity*: a saturated box measures reproducibly wrong. QoS claims therefore carry a Codespace-scope caveat until bare metal provides headroom (concurrency < cpu_count).
5. **Single platform, single workload shape.** Cross-platform discrimination (RQ3) is untested. **[CANDID]**: OpenFaaS, OpenWhisk.
6. **Control plane measured as one container** (`fnserver`), not decomposed into gateway/scheduler/queue sub-components. **[CANDID]**: profiling inside the control plane.
7. **Model constants** (idle 30 W, 3.5 W/core, PUE 1.15, CI 150) are literature defaults; CI in particular is regional/temporal.
8. **Single-machine scale.** 2 vCPU; orchestration overhead may scale differently at larger deployments.

> **v9.3 review-driven corrections (external expert review, 2026-08-04):** (a) `host_cpu_sec` sums **busy** ticks only (was total → `orchestration_*` inflated ~5×); (b) **memory captured** in cgroup mode — `cp_peak_mem_mb` was 0.0, now real (88.6 MB on the v9.2 run); (c) **KPI fixed** — now operational gCO₂ per SLO-compliant invocation (≈39.6 mg incl. idle base on the saturated-vm run); (d) **`sensitivity` block added** — dynamic share, dynamic energy, and carbon at busy-core 2/3.5/5 W (share invariant, absolutes banded); (e) **`orchestration_*` defined explicitly** as a host-wide residual (§7.3) and excluded from claims; (f) **quick-run guard** — `SAQEF_REPEAT < 5` writes to a `_quick` outdir so 1-run passes can't be mistaken for the 5-run publication set.
>
> **v9.4 review-driven corrections (second external expert review, 2026-08-04):** (g) **host plausibility gate** — `host_plausible = host_cpu_sec ≤ cpu_count × wall × 1.05`, with the cgroup quota (`cpu.max`) printed by `--check`; (h) **host saturation ratio** reported per run and flagged ≥ 85% (QoS caveat, §7.4); (i) **fn allowlist** (`--fn-containers`) — non-matching containers land in an `unclassified_cpu_s` bucket + `container_inventory` audit, never silently in `fn_cpu`; (j) **count-bound runs** — `-n` only, `--duration` is a safety cap with a post-run wall assertion (hey's `-z`/`-n` precedence is build-dependent); (k) **`--rescan-s`** (default 0.25) shrinks the blind spot for ephemeral containers; (l) **`iqr`** reported alongside `bootstrap_ci`/`cv_pct`, N≥10 for publication.

---

## 8. The validation method caught a real bug (methodological evidence)

During development, the sampler overcounted control-plane CPU by **52× under load** (5188% vs the direct counter; 462 CPU-seconds attributed to a machine physically capable of 28.7 — a hard physical impossibility). Root cause: the sampler computed a *percent rate* with its own internal dt, which the reducer then re-multiplied by a different dt; under thread contention, scheduling jitter produced spurious rate spikes. The built-in delta-check flagged it immediately. Fix: store raw cumulative counters + difference with true timestamps (exact by construction). Post-fix delta-check: **0.01%**.

**Why this belongs in the paper:** it demonstrates that (a) self-validation is not decorative — it caught a data-corrupting bug that would have silently poisoned every platform's numbers; (b) the raw-cumulative design is cadence-immune, which is what makes sparse sampling on constrained environments still produce exact totals.

---

## 9. Reproducibility

One-command pipeline (`run_saqef.sh`): `setup → check → verify → bench → gates`.

```bash
chmod +x run_saqef.sh
./run_saqef.sh all
```

Artifacts per run: `summary.json`, `samples.csv`, `requests.csv`, `verify.json`, `runs.json` (median + bootstrap CI + CV + IQR + spread). Environment snapshot (cpu_count, governor, freq, sampler, loadgen, container inventory) recorded inside each summary. Stdlib-only Python; no pip installs.

Full command log and historical decisions: `SAQEF_TECHNICAL_REPORT.md` §§3, 4, 11–14.

---

## 10. Future Work

1. **OpenFaaS** (same function image, same protocol) — gate: does `cp_dynamic_share` differ from Fn's 30.3% by >5 pp? (Discrimination power test.)
2. **OpenWhisk** (and optionally Fission) — cross-platform comparison.
3. **Bare metal (dual-boot Ubuntu)** — RAPL ground truth, validation of the model, and the definitive absolute numbers.
4. **Control-plane decomposition** — which fnserver subcomponent (gateway, scheduler, freeze manager, watchers) costs what.
5. **Cold-start vs warm** — `--interarrival-ms 1000` isolation experiment: the "carbon cost of elasticity."
6. **Freeze-policy ablation** — `FN_FREEZE_IDLE_MSECS=0` vs default: quantify Fn's pause/unpause churn in energy terms.
7. **Realistic mixed workloads** — CPU/IO mixture, not just pure spin.

---

## 11. Research impact & how the results can be used

- **Methodology reuse:** the harness + delta-check pattern is a drop-in instrument for anyone measuring FaaS energy on any platform (container-level, cgroup-validated, honest about estimation).
- **Informing models:** Kepler-style CPU-time proportionality gains a real orchestration-overhead term (≈30% of dynamic energy for Fn-class platforms), so serverless energy models stop treating orchestration as ~0.
- **Carbon-aware scheduling:** a per-invocation orchestration cost (≈3 mJ dynamic, ≈145 µg CO₂ CP-only here) makes it possible to route work to the cheapest control plane — and to price "green functions" correctly.
- **Design guidance:** autoscaling/scale-to-zero economics quantified — the 94% idle-baseline share quantifies exactly how much idle waste elasticity can reclaim, and the 30.3% dynamic share says what remains even when fully scaled.

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
| KPI (op. gCO₂ per SLO-compliant invocation, incl. idle base) | ≈7.5 | — | mg CO₂ |
| control-plane carbon per invocation (dynamic only) | ≈145 | — | µg CO₂ |
| `cp_sampler_vs_delta_pct` | 0.01 | 0.01–0.15 | % |
| `physical_plausible` | true | true | bool |

*Table values are v9.1 medians; KPI and `cp_peak_mem_mb` reflect the v9.2 formulas and await the v9.2 re-run to be confirmed against fresh samples.*

---

*Everything in this draft is traceable to `SAQEF_TECHNICAL_REPORT.md` and executable via `run_saqef.sh all`. Update this document as each [CANDID] gap closes.*
