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

Run profile: 3000 requests, concurrency 20, warmup 20, repeat 5, SLO 500 ms, duration cap 60 s. Runs are **count-bound** (exactly N requests per platform; `--duration` is a *safety cap*, not a hard stop) so cross-platform windows are identical in composition even when wall time differs. Publication runs: repeat ≥ 10 (§4.6). **Every benchmark session starts from a freshly-restarted fnserver** (`reset` in `run_saqef.sh`): leftover warm/zombie function containers from a prior session were found to fold into `fn_cpu` under the old denylist and inflate it (report §18), so a reused server is no longer measurable via the runner.

### 4.4 Instrumentation

**Sampler (v9 architecture).** A background thread reads per-container CPU. Two modes:
- `--sampler cgroup` (primary): maps each running container to its cgroup dir and reads the **raw cumulative CPU seconds** (`cpu.stat` → `usage_usec`). Samples are stored as cumulative counters with true wall timestamps.
- `--sampler docker` (fallback): 1 Hz `docker stats` percentages.

**Key design decision — raw cumulative + true-timestamp differencing.** The reducer differences *consecutive cumulative reads* using *true sample timestamps* (`dt = t_{i+1} - t_i`). This makes the totals **exact regardless of sampling cadence** — slow cgroup reads, scheduling jitter, or spurious wakeups cannot bias the integral (the reducer never re-multiplies by a sampler-internal dt). This property is what lets the delta-check pass to 0.01% even when sampling coverage is low on the nested mount (§5.3). Memory is captured the same way (`memory.current` / `memory.usage_in_bytes`) so `cp_peak_mem_mb` is a real measurement in cgroup mode (v9.2+).

**Classification is allowlist-based.** Control-plane = containers matching `--cp-containers`/`--cp-images`/`--cp-labels`; function = containers matching `--fn-containers`/`--fn-images`/`--fn-labels`. When no function allowlist is given, *everything not CP* is function (denylist default, back-compat). When an allowlist IS given, any container matching neither CP nor function goes to a logged `unclassified_cpu_s` bucket (warn if > 0.5 CPU-s) — **so a stray container can never silently inflate `fn_cpu`**; and if the allowlist matches *nothing*, the harness warns instead of silently falling back (a wrong image is loud). `run_saqef.sh` passes `--fn-images hello` by default — the **deployed** function image name, not the base runtime `fnproject/python:*` (Fn names function containers with opaque ULIDs, and the running containers carry the built image, so the deployed image is the only reliable signal). `container_inventory` (names) and `container_labels` (name → image + labels) are embedded in every summary for audit. cgroup rescans run every `--rescan-s` s (default 0.25) to bound the blind spot for containers born and dying within one scan.

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
| **Host plausibility** | `host_cpu_sec ≤ cpu_count × wall_s × 1.05` (vs `cpu.max` quota printed by `--check`) | true |
| **Host saturation** | `host_cpu_sec / (cpu_count × wall_s)` | report per run; `host_saturated` flag (v9.8, enforced in code) if ≥ 85% — QoS is contention-contaminated |
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
>
> **v9.5 corrections (between-session finding, 2026-08-04):** (m) **fresh-session protocol** — `run_saqef.sh reset` + `setup` always restart fnserver and remove orphaned function containers, so leftover warm containers can't inflate `fn_cpu` (root cause candidate for the 3.2× session swing); (n) **allowlist now exercised** — image/label signals (`--fn-images` etc.) added, making `unclassified_cpu_s` informative instead of a guaranteed 0.0; (o) **marginal KPI** — `kpi_gco2_per_inv_dynamic` (load-created carbon only) is wall-independent, unlike the ~93%-idle-dominated operational KPI; (p) neither session's absolute numbers are quoted in isolation — bare-metal multi-session medians are required.
>
> **v9.7 corrections (image-handle bug, 2026-08-04):** (q) the v9.5 allowlist default `fnproject/python:3.12` and the `reset_fn` `ancestor=` filter both referenced the **base runtime** image, but Fn's running function containers carry the **deployed** image (`hello:0.0.14`) — BuildKit does not preserve the FROM lineage. Net effect: the allowlist silently matched nothing and reverted to the denylist, and leftover warm `hello:*` containers were not cleaned. Fixed by defaulting `--fn-images` to the deployed image name (`hello`) and cleaning `hello:*` containers by image-name pattern; (r) **fail-open classification** — when an fn allowlist is configured but matches no container, the harness now WARNs and routes strays to `unclassified_cpu_s` instead of silently folding them into `fn_cpu`. The v9.5 numbers are unaffected (`container_labels` proved a genuine two-bucket world), but the protection is now actually enforced.

> **v9.8 corrections (third external expert review, 2026-08-04):** (s) **hey gate is functional, not size-based** — the old `stat -c%s ≥ 1000` check could reuse a stale ≥1000-byte wrong binary forever (exactly the v9.7 `hey` failure); `hey_smoke_ok()` now requires the candidate to emit a parseable JSON report or it is wiped and reinstalled (G8); (t) **the ≥85% saturation QoS-caveat is enforced, not just documented** — new `host_saturated` field in every `summary.json` (`host_saturated_flag`: sat ≥ 85%), and the `gates` table marks a saturated run "QoS CONTENTION-CONTAMINATED". Both fixes are gates/audit only; no measurement path changed, so all prior numbers stand under their existing caveats.

---

## 8. Measurement-validation discipline (methodological contribution)

The central methodological claim is not a number; it is that **every reported quantity has an independent cross-check, and the harness actively validates itself** rather than assuming its own correctness. Each measurement threat discovered during development produced a fix plus a gate that proves the fix, and the gates are now part of the output (a failed gate fails the run, not the narrative). This section is the paper's "how we know" appendix — it is what separates this framework from a script that prints plausible-looking numbers.

### 8.1 The validation gates (each number is double-checked)

| # | Reported quantity | Independent cross-check | Current status |
|---|---|---|---|
| G1 | control-plane CPU | cgroup **delta-check**: direct before/after counter of the CP container vs the sampler's sum | **0.00–0.01%** across all v9.7 runs |
| G2 | host busy accounting | `host_plausible = host_cpu_sec ≤ cpu_count × wall × 1.05`; `host_saturation_pct = host/wall` per core; v9.8 `host_saturated` flag = sat ≥ 85% | 99.9–100.3%, plausible=true (saturated but *reproducibly*) |
| G3 | sampling coverage | `sampling_covered_s / wall_s`; stop-time flush + clamp so it cannot exceed 100% | **100.0% on all 5** v9.7 runs |
| G4 | function classification | allowlist (names + image + label keys) with a logged **unclassified bucket**; fail-open (a configured-but-matching-nothing allowlist warns instead of reverting to the denylist) | `unclassified_cpu_s = 0.0` with 12/12 fn containers matched by image; no warning fired |
| G5 | load-generator identity | `env.loadgen` records the **actual** generator (`py`/`hey`), plus `loadgen_requested` and `loadgen_fallback` | v9.6+ truthful; a silent fallback is impossible |
| G6 | cross-session repeatability | multi-session median discipline; `cv_pct`/`iqr`/`bootstrap_ci` within a session, session medians across sessions | `cp_dynamic_share_pct` = 23.88 / 24.38 / 24.59 across three clean sessions (≤0.7 pp drift) |
| G7 | KPI wall-independence | marginal (idle-excluded) KPI vs operational KPI; busy-power sensitivity band (2/3.5/5 W) | dynamic KPI invariant to window; share invariant to busy power |
| G8 | instrument/toolchain identity | `hey_smoke_ok()`: a candidate `hey` must emit a parseable JSON report (`-n 2 -c 1 -o json`) or it is wiped and reinstalled | v9.8; a stale/corrupted binary ≥1000 bytes can no longer be reused; python fallback still records truthfully |

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

The framework was **frozen at v9.7**; further development stops unless a genuine bug surfaces (v9.8 applied exactly that exception: two third-expert-verified gate gaps, §8.1 G2/G8). Everything measured after the freeze is comparable by construction.

---

## 9. Reproducibility

One-command pipeline (`run_saqef.sh`): `setup → check → verify → bench → gates`. Every `all` starts from a fresh session (`reset` removes a reused `fnserver` and orphaned function containers), so leftover-container pollution cannot recur.

```bash
chmod +x run_saqef.sh
./run_saqef.sh all
```

Artifacts per run: `summary.json`, `samples.csv`, `requests.csv`, `verify.json`, `runs.json` (median + bootstrap CI + CV + IQR + spread). Environment snapshot (cpu_count, governor, freq, sampler, loadgen — including `loadgen_requested`/`loadgen_fallback`, container inventory/labels) recorded inside each summary. Stdlib-only Python; no pip installs. `verify.json` is a CPU-budget sanity check (`function_cpu_ms_per_inv` vs the deployed handler's spin), not a QoS measurement — its tail latency (cold first calls right after `fn deploy`) is not representative and must not be quoted.

Full command log and historical decisions: `SAQEF_TECHNICAL_REPORT.md` §§3, 4, 11–19.

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
