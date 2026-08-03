# SAQEF — Technical Progress Report & Measurement Log

**Project:** Green Cloud Continuum — Sustainability-Aware QoS Evaluation Framework (SAQEF)
**Paper (working title):** *The Hidden Cost of Orchestration: A Sustainability-Aware QoS Evaluation Framework for Serverless Platforms*
**Repo:** `github.com/bathork1391/saqef` (Codespaces-backed)
**Date range:** 2026-08-03 (Day 1 of the 20-day sprint)

---

## 1. Executive summary (candid)

- We have a **working, reproducible measurement pipeline** (harness + repeat protocol + median/spread reporting) and a **working Fn deployment** on GitHub Codespaces.
- We have **one platform measured (Fn)** with preliminary numbers. **No cross-platform comparison yet.**
- **QoS results are directly measured and defensible** (throughput, latency distribution, SLO compliance — reproducible to a few percent).
- **Energy/carbon numbers are MODEL ESTIMATES, not measurements.** No RAPL in cloud VMs. They are defensible only as transparent model outputs and need bare-metal RAPL validation for the final paper.
- **Control plane measured as a component (the `fnserver` container), but not "in isolation"** in the experimental-design sense (co-tenant measurement under load).
- The headline ratio `cp_dynamic_share_pct` is **stable only with a CPU-bound workload** (±3pp). With a no-op "hello" function it swings ±13pp and is unusable.
- **The picture is still blurry but the foundation is solid.** The paper becomes publishable after: (a) ≥2 more platforms, (b) RAPL validation on bare metal, (c) realistic workloads.

---

## 2. Environment & why

| | Chosen | Why |
|---|---|---|
| Test host | **GitHub Codespaces** (blank template, x86_64, Docker 29.3.0, Python 3.12, 2 vCPU) | Persisted via git, real dockerd, reproducible. Replaced Killercoda (ephemeral 1-h sessions, Docker-in-Docker broke Fn). |
| Fn CLI | 0.6.62, direct from GitHub release (`cli.fnproject.io` is dead) | Dead domain → direct asset download |
| Fn server | `fnproject/fnserver:latest` (0.3.x), containerized | Official image; needs the iofs wiring (see Issues) |
| Function runtime | Python 3.12, `fnproject/python:3.12` FDK base | Matches harness / benchmarks |
| Energy source | **CPU-time proportional model** (Kepler/Caribou-style) | RAPL unavailable on all cloud VMs. Bare metal needed for RAPL. |

---

## 3. Full command log (reproducible setup)

### 3.1 Fn CLI (from GitHub, since cli.fnproject.io is dead)
```bash
curl -L -o fn https://github.com/fnproject/cli/releases/download/0.6.62/fn_linux
chmod +x fn && sudo mv fn /usr/local/bin/fn
fn version        # Client 0.6.62
```

### 3.2 Fn server (the WORKING invocation — includes the iofs fix)
```bash
mkdir -p /tmp/iofs /tmp/data
docker run -d --rm --name fnserver \
  -v /tmp/iofs:/iofs \
  -e FN_IOFS_DOCKER_PATH=/tmp/iofs \
  -e FN_IOFS_PATH=/iofs \
  -v /tmp/data:/app/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --privileged \
  -p 8080:8080 \
  --entrypoint ./fnserver \
  -e FN_LOG_LEVEL=DEBUG \
  fnproject/fnserver
```
> This mounts a **shared Unix-socket filesystem** (`/iofs`) so function containers can complete the FDK handshake with fnserver. Without it, every invoke fails with `502 Container failed to initialize` (community-confirmed fix: fnproject/fn#1577).

### 3.3 Function create + deploy + HTTP trigger
```bash
export FN_API_URL=http://localhost:8080
fn init --runtime python hello          # creates hello/func.py, func.yaml
cd hello
fn deploy --create-app --app app1 --local   # builds + registers (bumps 0.0.x)
cd ~
fn invoke app1 hello                     # CLI invoke (bypasses HTTP triggers)
```
**HTTP trigger must be created explicitly** in this Fn version (deploy does not auto-create one):
```bash
fn create trigger -s /hello -t http app1 hello http-hello
# -> Trigger Endpoint: http://localhost:8080/t/app1/hello
```
Sanity check:
```bash
curl -s http://localhost:8080/t/app1/hello
# -> {"message": "Hello World"}
```

### 3.4 Harness setup
`saqef_harness.py` is dragged into the Codespace from the host machine. Stdlib-only Python (no pip installs).

### 3.5 Environment sanity check
```bash
python3 saqef_harness.py --check
# docker stats : OK (N containers seen)
# RAPL         : NOT AVAILABLE (CPU-time model only)   <- expected on cloud VM
```

### 3.6 Measurement protocol (the DEFINITIVE form)
```bash
python3 saqef_harness.py \
  --url http://localhost:8080/t/app1/hello \
  --platform fn --cp-containers fnserver \
  --total 3000 --concurrency 20 --duration 60 \
  --warmup 20 --repeat 5 \
  --outdir results/<experiment>
```
Outputs:
- `summary.json` — median of the runs + `spread_min_max` per key metric
- `runs.json` — every per-run summary
- `run_N/` — per-run `summary.json`, `samples.csv` (1 Hz per-container CPU%/mem), `requests.csv` (per-request latency/status)

---

## 4. Harness evolution & bugs found (v1 → v4)

| # | Bug / issue | Symptom | Fix | Where |
|---|---|---|---|---|
| 1 | `--check` false negative with zero running containers | `docker stats : NOT AVAILABLE` on a healthy box | Only fail on non-zero exit code; report `(N containers seen)` | `docker_stats_once` |
| 2 | Energy idle-baseline undercount | v1 total energy ≈ 70% of expected | `e_total = idle_w × wall_s + dynamic` (was: idle accumulated only over sampled intervals) | energy attribution |
| 3 | Missing metric | `cp_share_pct` diluted by idle baseline | Added `cp_dynamic_share_pct = e_cp/(e_cp+e_fn)` — the "hidden cost of orchestration" ratio | summary |
| 4 | Sampling coverage only ~62% | per-sample `docker stats` subprocess too slow (~2 s/sample) | **Streaming** `docker stats` in a background thread → ~1 Hz snapshots | sampler |
| 5 | Sampling coverage <100% (`sampling_covered_s` < `wall_s`) | `docker stats` needs ~1–2 s to emit its first cycle; load started before it | **FIXED (v5):** sampler sets `first_sample` event on its first snapshot; load window `t0` starts only after that (10 s timeout guard). Also `covered_s = min(covered_s, wall)` so coverage can never exceed the window. Expect `sampling_covered_s ≈ wall_s` now. | sampler / `run_once` |
| 6 | `duration` flag was decorative | runs not time-bounded | hard stop `deadline_s` in the load generator | `run_load` |
| 7 | Run-to-run instability of the ratio | hello workload: `cp_dynamic_share` 48–61% | Warmup + repeats + median; **CPU-bound workload** (see §6) | protocol |
| 8 | First-run cold-start outlier | run 1 wall + function CPU much higher | Median is robust to it; report spread so it is visible | protocol |

**v5 additions (for OpenFaaS):**
- `--auth user:pass` → adds an `Authorization: Basic ...` header to every request (OpenFaaS gateway is Basic-auth-gated). Same harness works for both platforms untouched.
- Sampler-start gate (row 5) now guarantees `sampling_covered_s ≈ wall_s` on all future runs.

**v6 additions (review response — P0/P1):**
| Feature | What it does | Review item addressed |
|---|---|---|
| `--verify` / `--verify-n` / `--verify-budget-ms` | Fires N calls (default 100), reports per-invocation **function container CPU** (ms/inv) + p50/p99 + a budget verdict (UNDER/MATCHES/OVER). | "CPU-bound workload unverified" — proves the deployed handler does the claimed work |
| `--sampler cgroup` | Direct `cpu.stat` / `cpuacct.usage` cumulative-counter reads at ~10 Hz (exact integral, no docker CLI). Auto-falls back to docker sampler if cgroup mapping fails. Mem not captured (0.0). | "Sampling rate too low" — best-effort, guarded |
| `/proc/stat` host CPU | `host_cpu_sec` + `host_overhead_cpu_sec = host − Σcontainer` per run. | "Control-plane contamination" — quantifies kernel/dockerd/containerd orchestration work invisible to per-container counters |
| Frequency/governor logging | `env.governor`, `env.freq_mhz_before/after`, `env.cpu_count` recorded per run; also printed in `--check`. | "Frequency state not controlled" — at least measured/reported; try `cpupower -g performance` on bare metal |
| `bootstrap_ci` + `cv_pct` | Added to repeat summaries alongside median + spread (stdlib, no numpy). | "No confidence intervals" |
| `carbon_gCO2.idle_band` | `op_total` recomputed at idle 15/30/45 W → carbon reported as a band. | "Idle power assumed" — makes the most suspect constant visible |
| `embodied_dram_g_per_gb_h` | Renamed from `embodied_g_per_gb_h` — explicit scope (DRAM-only; operational-carbon focus declared in paper). | "Embodied carbon incomplete" |
| `median_summary` fix | Even-count medians now = mean of two middle values (statistics.median semantics). | correctness bug found in smoke test |

**v6 return-runbook (Codespace):**
```bash
python3 saqef_harness.py --check                          # env snapshot: cpu_count, governor, freq
python3 saqef_harness.py --verify --url http://localhost:8080/t/app1/hello \
    --platform fn --cp-containers fnserver \
    --verify-n 100 --verify-budget-ms 5                  # confirm the 5ms busy loop shipped
python3 saqef_harness.py --sampler cgroup --check        # probe cgroup sampler (falls back if it fails)
python3 saqef_harness.py \
    --url http://localhost:8080/t/app1/hello --platform fn \
    --cp-containers fnserver --total 3000 --concurrency 20 --duration 60 \
    --warmup 20 --repeat 5 --sampler cgroup \
    --outdir results/fn_cpubound_v6
# freeze ablation:
docker update --env-add FN_FREEZE_IDLE_MSECS=0 fnserver   # then rerun; record difference
```
Then follow `OPENFAAS_SETUP.md` with `--auth admin:<pw>` and `--verify` before measuring.

**v7 additions (second expert review — accuracy-first):**
| Feature | What it does | Review item addressed |
|---|---|---|
| cgroup sampler → **~100 Hz + re-enumeration** | Direct `cpu.stat` counter reads at 100 Hz; container set re-scanned every 1 s so short-lived/ephemeral function containers are accounted (a 50 ms container ≈ 5 samples) instead of aliased away. The real fix for the 5×-low function-CPU suspect (freeze/unfreeze bursts + ephemeral containers falling between 1 Hz samples). | "Sampling gap vs serverless granularity" |
| `--delta-check` | Reads the control-plane container's cgroup counter directly **before and after** the window and compares with the sampler's accumulated total → `cp_sampler_vs_delta_pct` (≈0 = sampler validated). Independent cross-check of the whole sampling path. | "Read cumulative usec before/after" |
| `--loadgen hey` | Runs the Go `hey` binary as a subprocess → harness's own CPU (GIL/threads) is removed from host accounting. `slo_compliance` then derived by linear interpolation of hey's percentile points (labeled `compliance_source: hey_interp`). Falls back to python generator if hey missing. | "Load generator bottleneck" |
| `steal_sec` / `steal_pct` | `/proc/stat` `steal` tick delta across the window → quantifies noisy-neighbor/VM steal time that can inflate p99. | "Noisy neighbor / steal time" |
| `orchestration_cpu_sec` / `orchestration_share_pct` | `= host_cpu − Σfunction CPU` — control plane + dockerd + kernel + harness. The reviewer's definition of the "hidden cost". Reported **alongside** the narrower `host_overhead_cpu_sec` (= host − CP − function). | "Control plane definition too narrow" |
| `--interarrival-ms` | Gap between requests; enables the **cold-start experiment** (`--total 1000 --concurrency 1 --interarrival-ms 1000`) to isolate the carbon cost of elasticity vs warm runs. | "Cold start isolation" |
| `--check` additions | Reports `steal`, `hey` availability, plus existing freq/governor/cpu_count. | reproducibility |

**Skipped / already addressed (documented, no code needed):** hello-workload "idle tax" framing (we already anchor on CPU-bound); RAPL validation plan (already in harness + bare-metal gate); memory-bound workload (bare-metal item); Codespaces-as-CI vs bare-metal gold standard (already the two-tier plan).

**v8 additions (third review — ablation/decomposition):**
| Feature | What it does | Review item addressed |
|---|---|---|
| `--idle-probe` | Platform up, **zero traffic** for `--duration` s → static orchestration baseline (CP container + host CPU with no requests). Summary gets `"mode": "idle"`. | "Delta method / orchestration tax" |

**Orchestration-tax decomposition (run this pair per platform):**
```
# A. static baseline (platform up, no traffic)
python3 saqef_harness.py --url <ep> --platform <p> --cp-containers <cps> \
    --idle-probe --duration 60 --sampler cgroup --delta-check --outdir results/<p>_idle
# B. loaded (the normal protocol)
python3 saqef_harness.py --url <ep> --platform <p> --cp-containers <cps> \
    --total 3000 --concurrency 20 --duration 60 --warmup 20 --repeat 5 \
    --sampler cgroup --delta-check --outdir results/<p>_load

# Then:
#   static orchestration   = summary_A.cpu_sec.control_plane / orchestration_cpu_sec
#   incremental orchestration = summary_B.orchestration_cpu_sec − summary_A.orchestration_cpu_sec
#   marginal energy of traffic  = summary_B.energy_J.dynamic (idle dynamic ≈ 0)
#   carbon per request = (energy_B − energy_A) / requests   <- "carbon cost of serving"
```

> Note: the reviewer's sampling-coverage critique (9.19/13.55 s = 32% gap) was computed from the **superseded v4 hello run**; since v5 the sampler-start gate and `covered_s = min(covered_s, wall)` clamp make `sampling_covered_s ≈ wall_s`. Re-verify on every live run: coverage gap should be <5% or the run is suspect.

**Cold-start KPI** (already enabled by `--interarrival-ms`, v7):
```
warm:  --total 1000 --concurrency 20                       -> gCO2/1000 warm
cold:  --total 1000 --concurrency 1 --interarrival-ms 1000 -> gCO2/1000 cold
difference / 1000 = "gCO2 per cold start" (carbon cost of elasticity)
```

**v7 runbook additions (Codespace):**
```bash
# 1. install hey (low host footprint load generator)
curl -OL https://hey-release.s3.us-east-2.amazonaws.com/hey_linux_amd64
chmod +x hey_linux_amd64 && sudo mv hey_linux_amd64 /usr/local/bin/hey
python3 saqef_harness.py --check       # confirm hey: available + cgroup map: OK
#   `cgroup map: OK` is required for --sampler cgroup (100 Hz + --delta-check).
#   If FAIL (container PIDs not visible from this host), use --sampler docker
#   here and reserve cgroup mode for bare metal; the delta-check will be absent.

# 2. gold-standard Fn run: cgroup sampler + delta-check + hey loadgen
python3 saqef_harness.py \
    --url http://localhost:8080/t/app1/hello --platform fn \
    --cp-containers fnserver --total 3000 --concurrency 20 --duration 60 \
    --warmup 20 --repeat 5 --sampler cgroup --delta-check --loadgen hey \
    --outdir results/fn_cpubound_v7

# 3. cold-start experiment (1000 isolated starts vs warm):
python3 saqef_harness.py --url http://localhost:8080/t/app1/hello --platform fn \
    --cp-containers fnserver \
    --total 1000 --concurrency 1 --interarrival-ms 1000 --repeat 3 \
    --outdir results/fn_coldstart
#   ...then compare against the warm run above -> "carbon cost of elasticity"

# 4. freeze ablation (unchanged): docker update --env-add FN_FREEZE_IDLE_MSECS=0 fnserver
```

---

## 5. Issues faced on Fn (and exact fixes)

1. **`502 Container failed to initialize`** (Killercoda AND Codespaces)
   - Cause: FDK ↔ fnserver handshake over the shared socket filesystem. Containerized fnserver's `/tmp/iofs` is private; function containers mount the host's, so the socket is never seen.
   - Fix: the §3.2 invocation (shared `/iofs`, `FN_IOFS_*`, `--privileged`). Confirmed via fnproject/fn#1577.
   - Debug path: `docker logs fnserver` at `FN_LOG_LEVEL=DEBUG` (shows FDK stderr tagged `tag=stderr`).

2. **`cli.fnproject.io` unresolvable**
   - Fix: fetch the `fn_linux` asset directly from GitHub releases.

3. **`fn triggers` / `fn list triggers` structure changes in CLI 0.6.x**
   - Fix: verb-first commands; trigger creation is positional: `fn create trigger -s <src> -t http <app> <fn> <trigger>`.

4. **`handler() got an unexpected keyword argument 'data'`** (all invokes 502 after our func.py rewrite)
   - Cause: Fn Python FDK calls `handler(ctx, data=...)`; our signature was `def handler(ctx):`.
   - Fix: `def handler(ctx, data=None):`.

5. **git/gh cannot push**
   - Codespaces GitHub token is scoped to its origin repo and cannot create/write others (`gh repo create` → GraphQL 403; push → 403/404).
   - Fix: **classic PAT with `repo` scope** (prefilled URL: `github.com/settings/tokens/new?scopes=repo&description=saqef`), embedded in the remote URL:
     `git remote set-url origin https://<user>:<PAT>@github.com/bathork1391/saqef.git`

---

## 6. Measurement results so far (preliminary — Fn only)

Load: 3000 requests, concurrency 20, warmup 20, repeat 5, SLO 500 ms, model: idle 30 W, 3.5 W/core, PUE 1.15, CI 150 gCO₂/kWh.

### 6.1 Workload A — "hello" (no-op handler): QoS clean, energy ratio UNSTABLE
| metric | median | spread |
|---|---|---|
| throughput (rps) | 221 | 190–236 |
| p50 / p99 (ms) | 75 / 295 | 72–77 / 275–566 |
| SLO compliance | 99.93% | 98.8–100% |
| **cp_dynamic_share** | **57.9%** | **47.8–61.1%** ⚠️ |
| cp_share (of total energy) | 0.91% | 0.35–1.33% |
| dynamic energy (J) | 6.0 | 2.8–13.6 |
| KPI (gCO₂/SLO-compliant inv) | 0.0066 | 0.0062–0.0079 |

> ⚠️ Ratio swings because both numerator and denominator are tiny: no-op function is nearly free AND Fn **freezes the hot container between calls** (observed: `docker pause`/`docker unpause` churn in fnserver logs, `FN_FREEZE_IDLE_MSECS=50` default). Frozen ⇒ 0% CPU ⇒ noisy function-side denominator.

### 6.2 Workload B — CPU-bound (5 ms spin per call): STABLE, the usable baseline
| metric | median | spread (runs 2–5) |
|---|---|---|
| throughput (rps) | 210 | 161–221 (run 1 = cold-start outlier) |
| p50 / p99 (ms) | 83 / 306 | 75–84 / 270–596 |
| SLO compliance | 99.97% | 98.6–100% |
| **cp_dynamic_share** | **27.4%** | **26.6–32.5%** (±3pp) |
| cp_share (of total energy) | 0.90% | 0.8–1.0% |
| dynamic energy (J) | 14.0 | 12.5–24.3 |
| KPI (gCO₂/SLO-compliant inv) | 0.0070 | 0.0067–0.0094 |

**Absolute control-plane overhead (the cross-platform comparator):**
- 1.08 cpu-sec over 3000 invocations → **0.36 ms CPU per invocation**
- 3.8 J dynamic → **1.3 mJ per invocation**
- 0.182 gCO₂ op → **~61 µg CO₂ per invocation** (at CI 150, PUE 1.15)

> Open question: measured function CPU (≈2.7 cpu-sec) is ~5× below the naive `3000 × 5 ms` estimate. Verify the deployed func.py (did the busy loop ship?) and quantify freeze-induced CPU loss before trusting the function-side absolute.

---

## 7. Energy & carbon model (transparent, literature-based)

```
cpu_sec(container) = Σ (CPUPerc/100 × dt)      # from cgroup via docker stats, 1 Hz
dynamic_J(container) = cpu_sec × 3.5 W          # P_BUSY_CORE_W (Caribou, SOSP'24)
total_J = idle_W × wall_s + Σ dynamic_J         # idle 30 W (P_IDLE_BASE_W)
carbon = Wh × PUE(1.15) × CI(150 gCO₂/kWh)
control plane := containers matching --cp-containers (here: "fnserver")
```

**This is estimation, not measurement.** Uncertainty is large on absolute Joules (±50% typical). It is defensible as: (a) a transparent, reproducible model; (b) a *relative* cross-platform comparator on identical hardware; (c) validated only once RAPL is available (bare metal).

---

## 8. Are the results defensible / publishable? (candid)

| Claim | Status |
|---|---|
| "Fn achieves p50 75–83 ms, p99 ~300 ms, 99.9% SLO compliance at 210 rps on this box" | **Defensible now** — direct measurement, reproducible |
| "Control plane = ~27% of dynamic energy for a CPU-bound function" | **Preliminary** — stable to ±3pp on this box; needs repeat across platforms + RAPL validation |
| "Control plane = 0.36 ms CPU / 1.3 mJ / 61 µg CO₂ per invocation" | **Preliminary absolute** — model-based; good cross-platform comparator |
| "The hidden cost of orchestration is X" | **Not yet** — needs ≥2 more platforms and a clear isolation experiment |
| Any absolute carbon number in gCO₂ | **Model only** — report as estimates; validate on bare metal |

**What is NOT defensible yet:** any claim that rests on absolute Joules/carbon as if measured; any cross-platform ranking (only Fn exists); the hello-workload ratio.

---

## 9. What we can present so far

1. A **reproducible measurement methodology + open tooling** (this is itself a workshop-paper contribution): streaming 1 Hz container sampling, coverage gate, warmup, N-repeats, median + spread, transparent model.
2. **Fn system behavior findings:** hot-container freeze/pause churn between calls (visible orchestration overhead); control-plane energy is a small fraction of total machine energy (~0.9%) but a significant fraction of *dynamic* energy (~27%) — i.e., orchestration overhead matters most when the workload is light.
3. **Preliminary Fn QoS envelope** (above).
4. The honest methodological lesson: **ratio metrics of tiny quantities are noise; the metric must be workload-anchored** (CPU-bound) — a reviewer-relevant insight.

---

## 10. Next steps (in order)

1. Verify deployed `func.py` (busy loop present?) — resolve the function-CPU discrepancy.
2. Set up **OpenFaaS** in Codespaces (same function image, same protocol) — follow `OPENFAAS_SETUP.md` (in this folder); note the new `--auth` flag. **Gate:** if `cp_dynamic_share` differs from Fn's ~27% by >5pp, the methodology discriminates → proceed. If not, redesign the metric first.
3. **OpenWhisk** (and optionally Fission) similarly.
4. Decide bare metal (dual-boot Ubuntu on the physical PC) for **RAPL validation** + the definitive numbers. Do this *after* step 2 confirms discrimination power.
5. Write methodology section + results tables into the paper scaffold.

---

*Everything in this report is reproducible from the command log in §3 and the committed artifacts in the repo.*
