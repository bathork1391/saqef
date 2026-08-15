# SAQEF — Technical Progress Report & Measurement Log

**Project:** Green Cloud Continuum — Sustainability-Aware QoS Evaluation Framework (SAQEF)
**Paper (working title):** *The Hidden Cost of Orchestration: A Sustainability-Aware QoS Evaluation Framework for Serverless Platforms*
**Repo:** `github.com/bathork1391/saqef` (Codespaces-backed)
**Date range:** 2026-08-03 (Day 1 of the 20-day sprint)

> **Status (2026-08-15): this file is a chronological session log — the CURRENT authoritative
> state, the citable four-platform publication-lock session (2026-08-14 lock4: OF 7.58 / Fn 11.29 /
> Kn 11.47 / OW 81.78) and what remains before submission live in `AGENTS.md` (read it first),
> `SAQEF_PAPER_DRAFT.md`, and `results/*_cpubound_lock_lock4`. Sections below are historical
> and may be superseded by those sources. Recent quick-tier sessions (concurrency sweep, Fn freeze
> ablation, I/O-bound variant — all 2026-08-15) are logged in §34–§35.

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
# freeze ablation (NOTE: docker update has NO --env-add flag -- env is fixed at container
# creation. Use the run_saqef.sh hook instead: FN_FREEZE_IDLE_MSECS=-1 bash run_saqef.sh all;
# fnproject semantics: NEGATIVE disables freeze, 0 = freeze WITHOUT delay (max churn, not "off")):
FN_FREEZE_IDLE_MSECS=-1 bash run_saqef.sh all   # then rerun with the var unset; record difference
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

# 4. freeze ablation: FN_FREEZE_IDLE_MSECS=-1 bash run_saqef.sh all  (NEGATIVE disables freeze; 0 = freeze immediately; env set at container creation; see line 153)
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

## 11. Session log (2026-08-03, Codespace retest)

**Environment confirmed:** Codespace, 2 vCPU, no RAPL (CPU-time model only), `cgroup map: OK` (fnserver maps → 100 Hz sampler + `--delta-check` usable), `hey` installed. `cpu_count` recorded per run in `env`.

**Workload confirmed REAL:** `hello/func.py` is a genuine 5 ms spin (`while time.perf_counter() - t0 < 0.005: pass`) — CPU-bound anchor is valid.

**New discovery — Fn container lifecycle (measured via `docker ps` watch during load):**
Fn creates function containers **per call-batch** (10 at concurrency 10, opaque `01KZ...` names), each lives **~2–5 s**, then **pauses** (FN_FREEZE_IDLE_MSECS=50 default), then is **GC'd within ~1–2 min**. `docker ps -a` minutes after a run shows only `fnserver`.

**Problem:** with `--sampler cgroup`, `function_cpu_sec_total` is **~0 (0.0–0.092 s)** even though function containers exist for multiple seconds — a *hard capture failure* (containers never added to the sampler's set), NOT an aliasing undercount. `cp_cgroup_reader`/fnserver reads work; function-container mapping/read fails somewhere in `container_cgroup_dir` / `read_cpu_cumulative`.

**Pending probe** (replicates the sampler's scan + read against live containers — run a 20-call verify in the background first, then within seconds):
```bash
python3 - <<'EOF'
import subprocess, os
def run(cmd): return subprocess.run(cmd, shell=True, capture_output=True, text=True)
out = run("docker ps --format '{{.ID}}|{{.Names}}'")
for line in out.stdout.strip().splitlines():
    cid, _, cname = line.partition("|")
    pid = run("docker inspect -f '{{.State.Pid}}' %s" % cid).stdout.strip()
    print("== %s docker_id=%s pid=%r" % (cname, cid, pid))
    cgroup = None
    if pid.isdigit():
        try:
            for l in open("/proc/%s/cgroup" % pid):
                parts = l.strip().split(":")
                if len(parts) == 3:
                    if parts[1] in ("cpu", "cpu,cpuacct", "cpuacct"):
                        cgroup = "/sys/fs/cgroup" + parts[1] + parts[2]
                    elif parts[1] == "":
                        cgroup = "/sys/fs/cgroup" + parts[2]
        except Exception as e:
            print("   /proc read FAILED:", e)
    print("   cgroup dir:", cgroup)
    if cgroup and os.path.isdir(cgroup):
        for f in ("cpu.stat", "cpuacct.usage"):
            p = os.path.join(cgroup, f)
            if os.path.exists(p):
                print("   %s:" % f, open(p).read().strip().replace("\n", "; "))
    else:
        print("   cgroup dir MISSING / not mounted")
EOF
```

**Decision matrix from the probe:**
| Probe result | Conclusion | Fix |
|---|---|---|
| `pid=` empty / `/proc` read FAILED | function-container PIDs invisible from devcontainer (namespace) | cgroup mode cannot work here → accurate function-CPU requires bare metal |
| `cgroup dir MISSING` | container cgroup not mounted in devcontainer | same → bare metal |
| `cpu.stat` shows `usage_usec > 0`, dir exists | mapping/read fine → sampler timing/add bug | cheap fix: re-enumerate at ~100 ms instead of 1 s (containers live seconds, so this catches them) |

**Status of this session's gates:**
- `cgroup map`: OK · `--verify` docker-sampler: VOID (wrong sampler) · `--verify` cgroup: `budget_check` UNDER (0.92 ms/inv, suspect) · frozen idle/GC lifecycle: characterized · function-CPU capture: FAILING (pending probe) · gold-standard run: NOT STARTED.
- Codespace commit `3d5528f` "harness v8 + report" = everything in sync with the local machine copies.

**Resume checklist (in order):**
1. Run the pending probe; branch per decision matrix.
2. If cgroup mode is salvaged: re-run `--verify --sampler cgroup` → expect `function_cpu_ms_per_inv` ≈ 5 ms, `budget_check: MATCHES`.
3. Then the gold-standard v8 pair (idle-probe A + loaded B with `--loadgen hey --delta-check`), then freeze ablation (`FN_FREEZE_IDLE_MSECS=0`), then cold-start (`--interarrival-ms 1000`).
4. If cgroup mode is unsalvageable: the honest path is a 1 Hz docker-tier smoke run here + bare metal for the defensible dataset; do NOT publish Codespace cgroup numbers.

---

## 12. Sampler overcount bug — diagnosed and FIXED (2026-08-03)

**Diagnostic (run_1/samples.csv, 18 containers):** only **37 samples / 14.4 s** (≈2.5 Hz, not 100 Hz — cgroup reads are slow on the nested mount) and fnserver `max_cpu% = 897.9%` (impossible). The `--delta-check` for run B showed **5188%** (sampler CP 136.6 s vs direct counter 2.6 s).

**Root cause:** the cgroup sampler computed a *percent rate* `(cum-old)/dt*100` using its own internal `dt`, then `run_once` re-multiplied by a *different* dt (`tnext-t`). With irregular cadence (slow reads, 1 s docker rescans, spurious `Event.wait` wakeups), that double normalization massively overcounted — uniformly, which is why `cp_dynamic_share` stayed stable (≈29.9%) while the absolute numbers were impossible (462 s of container CPU vs 28.7 s available).

**Fix (harness, applied + smoke-tested):** the sampler now stores **raw cumulative CPU seconds** (`(t, {name: (cum_s, 0.0)}, "cum")`); `sample_totals()` differences consecutive cumulative reads (exact, immune to cadence) and re-derives percent only for the CSV. `docker_sampler` samples are tagged `"pct"` and reduced as before. `run_once`, `verify`, `write_run` updated. Local test: cum deltas exact, irregular-cadence immune, CP-mem only for CP containers.

**Environment caveat (separate, NOT a harness bug):** the idle probe showed `host_cpu_sec = 118 s / 60 s wall` with zero traffic — the shared Codespaces VM burns ~2 cores of background CPU, so **host-level metrics (`host_cpu_sec`, `orchestration_cpu_sec`, `orchestration_share_pct`) are meaningless in the Codespace**. Reserved for bare metal. `hey` download was also only 243 bytes (S3 error page) — correct URL: `https://storage.googleapis.com/hey-release/hey_linux_amd64`.

**Re-validate after fix:** `--verify --sampler cgroup` then run B again; accept only if `cp_sampler_vs_delta_pct ≈ 0` AND `function_cpu_ms_per_inv ≈ 5 ms` (function CPU / requests) AND coverage ≥ 95%.

---

## 13. External review cross-check (2026-08-03)

Third-party technical review of the v8 data; disposition of each point:

1. **Delta-check discrepancy (idle 52.73%, loaded 5188.48% = ~52x) is a real bug.** AGREE. That is the exact overcount §12 explains; the reviewer's quoted code (`dt = max(t - last_t, 0.001)` + `snap[cname] = ((cum-old)/dt*100.0, 0.0)`) was the OLD `cgroup_sampler` — it no longer exists. Their GIL/scheduling-jitter mechanism (unreliable internal dt) is precisely why that code was wrong; the fix stores raw cumulative seconds and differences with true timestamps, so it is immune to cadence jitter. (We removed `dt` from the sampler entirely rather than instrumenting it, which makes their proposed debug step unnecessary.) FIXED.
2. **Physical impossibility (462 CPU-s attributed > 28.66 ceiling).** AGREE — it's the same overcount, and it can't occur from the new cumulative path. The harness now self-reports `cpu_sec_ceiling` and `physical_plausible` per run so the check is automatic. FIXED.
3. **`median_summary()` medians each field independently — hazard.** PARTIALLY AGREED, and already compliant with their recommended fix: all ratios (`cp_dynamic_share_pct`, `cp_share_pct`, `steal_pct`, `kpi_*`, `cp_sampler_vs_delta_pct`) are computed *inside each run* in `run_once` and the median is taken of stored per-run ratios — never reconstructed from independently-medianed raw components. Residual theoretical risk (median `wall_s` from run A + median `cpu_sec` from run B) only affects absolute sanity checks, not ratios; the new `physical_plausible` flag makes any violation visible. NOTE: verify the per-run `summary.json` files for run_1–5 in the Codespace (5-min check) to confirm the impossibility existed per-run (sampler bug) rather than only in the median view — expected conclusion: per-run impossible, consistent with the overcount.
4. **`kpi_gco2_per_slo_compliant_inv: NaN` breaks strict JSON.** AGREE — `json.dump` writes bare `NaN` (invalid per spec). Fixed with a recursive `clean_json()` that maps non-finite floats → `null` before every write/print, and `median_summary()` now skips non-finite values (all-NaN → `null`, not crash). FIXED.
5. **Failure mode flipped direction (v6/v7 "5x too low" → v8 "16x too high").** AGREE, worth stating: v6/v7 undercounted because function containers were never added to the sampler set; v8 overcounted because of double normalization. Both distinct root causes, both now removed — but the flip is evidence the pipeline was not yet converged, so the gold-standard gate must pass before publication.
6. **Report ratios as "52x", not "5188%".** AGREE — cosmetic; `cp_sampler_vs_delta_pct` stays a % in JSON, this log uses both forms. Adopted.
7. **Sequencing advice (validate sampler before OpenFaaS/OpenWhisk; treat 100 Hz as earn-back; bare metal only after delta-check is single-digit).** AGREE, this is the current plan (§10, §11 step 4). `--sampler docker` remains a valid interim fallback for cross-platform smoke runs, but the fixed cgroup path should now pass the delta-check directly.

**Net:** all actionable points addressed in the harness; §12 fix + §13 hardening are both in the local copy to re-drag into the Codespace.

---

## 14. v9 re-validation on Codespace (2026-08-03) — FIX CONFIRMED

Re-ran gold-standard B (`--sampler cgroup --delta-check --loadgen hey`, 5×3000 calls) with Fn up. Median:

| Metric | v8 (buggy) | v9 | Meaning |
|---|---|---|---|
| `cp_sampler_vs_delta_pct` | 5188.48 (52x) | **0.02** | sampler now matches direct cgroup counter to 0.02% |
| `cp_delta_sec` vs `cpu_sec.control_plane` | 2.6 vs 136.6 | **2.611 = 2.61** | exact |
| `physical_plausible` | false (462 CPU-s > 28.7) | **true** (8.6 < 30.2 ceiling) | impossibility gone |
| `cp_dynamic_share_pct` | ~29.9 | **30.55** | the one stable ratio, now on real numbers |
| `slo_compliance` / rps | – | 0.9997 / 198.6 | QoS intact |

**Remaining honest caveats (not blocking CP-share claims, must be stated):**
1. **Function CPU capture still partial:** `cpu_sec.function = 6.01 s / 3000 inv = 2.0 ms/inv` vs the 5 ms busy-loop claim. Short-lived function containers are only partially caught by the sparse nested-mount sampler (known §11 issue). CP side is exact (delta-check proves it); function-side absolute CPU improves on bare metal. `cp_dynamic_share_pct` is a CP/CP+fn ratio so it is affected only mildly; treat fn CPU as lower-bound.
2. **`sampling_covered_s` is low (≈13.7% of wall)** on the nested mount — but totals remain exact because the sampler stores raw cumulative counts and the consumer differences them (this is exactly why the delta-check passes despite sparse sampling). Coverage ≥ 95% remains a bare-metal gate.
3. **`hey` still failed at runtime** despite being on PATH — root cause now identified: `storage.googleapis.com/hey-release/hey_linux_amd64` returns **403 Forbidden** (verified 2026-08-03), so every "reinstall" fetched a truncated error page. GitHub releases have no prebuilt assets; `run_saqef.sh setup` now installs via `go install github.com/rakyll/hey@latest`. Fallback to the Python generator is harmless for container-level metrics.
4. Host-level metrics (`host_cpu_sec`, `orchestration_*`) remain meaningless in the shared Codespace (noisy neighbor, §12). Reserved for bare metal.

**Reproduction (2026-08-03, second full `all` run):** identical conclusion — `cp_sampler_vs_delta_pct = 0.01`, `cp_dynamic_share_pct = 30.32`, `physical_plausible = true` on all 5 runs, `slo_compliance = 0.9997`, `throughput_rps = 206`.

## 15. External expert review — verdicts & fixes (v9.2, 2026-08-04)

An expert review of the v9 `summary.json` (carried in the peer conversation) found 3 real defects plus 2 methodology asks. Disposition and code changes below. **All v9 results remain valid for everything that does not touch the three fixed fields; the three fixed fields must be re-measured on a v9.2 run.**

| # | Review point | Disposition | Fix (in `saqef_harness.py` v9.2) |
|---|---|---|---|
| 1 | Availability/throughput/QoS/energy/carbon fine | Accepted | none |
| 2 | `host_cpu_sec` 29.97 vs CP 2.6 implausible | **Agreed — real bug** | `host_cpu_ticks()` now sums **busy** ticks only (`total − idle − iowait`, excluding guest double-count); previously it returned total machine time ≈ wall×cores, which inflated `orchestration_*` ~5×. |
| 3 | `orchestration_cpu_sec = host − fn` double-counts host incl. loadgen/idle | **Agreed — real bug** | Host metrics are now a honest *busy* integral; the residual (host − cp − fn) is genuinely attributable to loadgen + kernel/idle-checks, so it is flagged as "host incl. loadgen" rather than called pure orchestration. Bare-metal-only claim unchanged. |
| 4 | `cp_peak_mem_mb: 0.0` — sampler returns `(cum, 0.0)` | **Agreed — real bug** | `container_mem_cgroup_dir()` + `read_mem_mb()` (v2 `memory.current`, v1 `memory.usage_in_bytes`); `cgroup_sampler` now stores `(cum, mem)` and `sample_totals` returns peak CP mem. Unit-tested (v2/v1/missing→0.0). |
| 5 | `sampling_covered_s` 2.07/14.6 — "sample time" vs "coverage" confusion | Accepted (documentation) | Now documented as *cgroup-read time*, not an inclusion window; coverage% derives from first–last sample timestamps. |
| 6 | KPI label wrong: 22.39 g labeled per-invocation | **Agreed — real bug** | `kpi = op_gco2 / n_compliant` (per SLO-compliant invocation, incl. idle base) → ≈7.5 mg. CP-only per-inv is the separate ≈145 µg dynamic figure. Unit-tested. |
| 7 | CPU accounting 2.6 CP vs 6 fn "makes sense"; 467 J/30 W idle realistic | Accepted | none — confirms 94% idle dominance is a real, publishable finding. |
| 8 | Partial sampling / sampler cadence concerns | Accepted (documentation) | unchanged design; exact by construction (cumulative differencing, §12). |

**v9.2 harness changes (all unit-tested locally, `test_review_fixes.py`):** `host_cpu_ticks` busy-only; `container_mem_cgroup_dir` + `read_mem_mb`; `cgroup_sampler` emits `(cum, mem)`; `sample_totals` peak-mem; `kpi` per-invocation. Re-drag `saqef_harness.py` + `run_saqef.sh` to Codespace and re-run `all` before quoting any of the three fixed fields.

## 16. v9.3 — sensitivity band, orchestration definition, quick-run guard (2026-08-04)

1. **`sensitivity` block in `summary.json`:** recomputes `cp_dynamic_share_pct`, dynamic energy (J), and operational carbon (gCO₂) at busy-core **2.0 / 3.5 / 5.0 W** (mirrors the existing `idle_band` 15/30/45 W for carbon). Expected property: the dynamic share is *identical* across the band (the W constant cancels in the ratio) — proving the headline is model-robust; absolute energy/carbon scale linearly and are bounded by the band until RAPL.
2. **`orchestration_*` defined, not claimed:** `orchestration_cpu_sec = host_cpu_sec − cpu_sec.function` (CP + dockerd/containerd + loadgen + co-tenant + kernel). Documented in §7.3 of the draft; excluded from claims. Only the cgroup-exact control-plane container share is presented as orchestration cost.
3. **Quick-run guard in `run_saqef.sh`:** env overrides `SAQEF_TOTAL/SAQEF_CONCURRENCY/SAQEF_DURATION/SAQEF_WARMUP/SAQEF_REPEAT`. If `SAQEF_REPEAT < 5`, the bench + gates write to `<outdir>_quick` with a loud banner — a 1-run pass cannot be mistaken for (or overwrite) the 5-run publication set.
4. **v9.2→v9.3 test status:** py_compile + `test_review_fixes.py` (host_cpu busy arithmetic, mem v2/v1/missing, KPI arithmetic, sample_totals mem flow, clean_json) all green. Sensitivity-dict median path covered by `median_summary` recursion (numeric leaves only).

## 17. Independent review of `results/fn_cpubound_v9/summary.json` — v9.4 findings (2026-08-04)

A second independent expert review of the v9.2 `summary.json` (the 39.9 rps / 75.19 s run). Verdict: methodology B+, results B−, publishability B; bottom line "nothing here says throw out the harness — delta-check, sensitivity band, median-of-ratios are the right calls." Six points, all addressed in v9.4 (below); three were already handled by v9.2/v9.3 fixes (marked ✓), the rest are new gates.

| # | Review point | Disposition | Fix (in `saqef_harness.py` v9.4) |
|---|---|---|---|
| 1 | `host_cpu_sec` 150.48 > ceiling 150.37 — hard physical impossibility; `physical_plausible` checks only cp+fn, never host | **Agreed — real gap** | `--check` now prints `cgroup quota` via new `cgroup_cpu_quota()` (v2 `cpu.max`, v1 `cfs_quota/period`); every summary gains `host_saturation_pct` (host_cpu ÷ cores×wall) and `host_plausible` (host_cpu ≤ cores×wall×1.05). |
| 2 | CPU-saturated environment (~100%) undermines QoS claims (p50 85 ms not representative) | **Agreed — new gate** | `host_saturation_pct` is reported per run; a run ≥85% must be flagged as contention-contaminated (QoS claim caveat); publication runs on bare metal must keep concurrency < cpu_count. |
| 3 | Function classification is a **denylist** — anything not matching `--cp-containers` silently folds into `fn_cpu` | **Agreed — real bug** | `sample_totals(samples, cp_sub, fn_sub="")` returns a 6-tuple ending `unclass_cpu_s`; new `--fn-containers` allowlist; non-matching names go to an `unclassified_cpu_s` bucket (warning if >0.5 CPU-s) and `container_inventory` (docker ps names) is embedded in every summary. Default `fn_sub=""` preserves prior denylist behavior. |
| 4 | hey `-z`/`-n` precedence — `-n` is ignored when `-z` is given; v9.2 run hit exactly 3000 and ran 75.19 s (> its 60 s `-z`) | **Agreed — real bug** | Runs are now **count-bound** (`-n total` only; exactly N requests, identical windows across platforms); `--duration` is a safety cap only (subprocess timeout + post-run warning when `wall > duration×1.1`). Isolate-test `hey -n 10 -z 2s -c 2` documented in the draft to confirm build precedence. |
| 5 | Coverage 80.4% < 95% gate; 1 s docker rescans blind to containers born and dying within one scan | **Agreed — new parameter** | `--rescan-s` (default 0.25) threaded through `cgroup_sampler` → `start_sampler` → both callers; shrinks the blind spot for scale-to-zero churn. Coverage ≥95% remains a bare-metal gate. |
| 6 | n=5 is thin for bootstrap CI (resampling combinatorics artifact, not honest uncertainty) | **Agreed — reporting** | `summary.json` now also reports `iqr` (Q3−Q1) next to `bootstrap_ci` and `cv_pct`; n=5 is an *iteration* setting, n≥10 for the paper (report both the CI and the IQR). |

**v9.4 test status:** py_compile clean; `test_review_v94.py` green (denylist default + fn allowlist split, host saturation/plausibility math 97.5% vs 107.5%, iqr, `median_summary` recursion over the new fields). Re-drag `saqef_harness.py` + `run_saqef.sh` to the Codespace and re-run `all` before quoting any host-level or classification field.

**Honest framing carried forward into the draft (not new results):** `cp_dynamic_share_pct` is an upper bound on CP share because `cpu_sec.function` is a lower bound (sparse capture of short-lived fn containers); QoS from the shared Codespace is contention-contaminated (governor/frequency/steal uncontrolled); the KPI is window-dependent (per SLO-compliant invocation); host-level metrics are bare-metal-only claims.

## 18. v9.5 — the between-session finding, fresh-session protocol, exercised allowlist (2026-08-04)

A follow-up expert review of the v9.4 run flagged a **new, higher-priority finding** than any of the original four: two nominally identical sessions do not agree.

| Metric | prior run | v9.4 run | ratio |
|---|---|---|---|
| `wall_s` | 75.19 | 23.24 | 3.2× |
| `throughput_rps` | 39.9 | 129.07 | 3.2× |
| `cpu_sec.function` / inv | 16.05 ms | 3.83 ms | 4.2× |
| `kpi_gco2_per_slo_compliant_inv` | 0.0396 | 0.012 | 3.3× |

This is **between-session spread**, not the within-session `cv_pct`/`iqr` (which only cover one `all` invocation). Two plausible causes, both now addressed in the harness/runner:

1. **Leftover containers from the prior session.** `setup` reused a running `fnserver` and never cleaned orphaned function containers; under the old denylist those leftovers folded into `fn_cpu`, inflating the 16 ms/inv figure and host CPU. One bug, two symptoms.
2. **Genuine shared-VM contention.** Consistent with the report's own "Codespaces VM burns background CPU" note.

**Dispositions (all in v9.5):**
- **Fresh-session protocol:** new `reset` action + `setup` now always restarts `fnserver` from scratch and removes orphaned `fnproject/python:3.12` containers (image is the only reliable handle — Fn names containers with opaque ULIDs). `all` runs the full pipeline from a fresh server. *Benchmarking from a reused fnserver is no longer possible via the runner.*
- **Classification allowlist now exercised, not just wired:** the reviewer correctly noted `unclassified_cpu_s: 0.0` was guaranteed (nothing was given a chance to fail) because `run_saqef.sh` never passed `--fn-containers`. v9.5 adds **image/label** signals (`--fn-images`, `--fn-labels`, `--cp-images`, `--cp-labels`) and `run_saqef.sh` now passes `--fn-images fnproject/python:3.12` by default — so any container not running the function image and not CP lands in `unclassified_cpu_s` for real. `container_labels` (name → image + labels) is embedded in every summary for audit.
- **Wall-independent marginal KPI:** the operational KPI is ~93% idle-power-dominated (`energy_J.dynamic 54.9` of `total 752.4`), so it inherits the 3.2× wall swing. New `kpi_gco2_per_inv_dynamic` reports *load-created* carbon per SLO-compliant invocation only (idle baseline excluded) — a per-invocation cost that is stable across window durations.
- **Honest status of both runs:** per the reviewer, neither session's absolute numbers should be quoted in isolation. The path forward is bare metal (controlled environment), where the fresh-restart protocol + exercised allowlist + both KPIs will let a multi-session median (≥2 independent `all` runs) be reported.

**v9.5 test status:** py_compile + `test_review_v95.py` green (members-based allowlist folds strays to unclassified; denylist default preserved; image/label matching; `docker_inventory` parsing; marginal-KPI arithmetic). Re-drag `saqef_harness.py` + `run_saqef.sh` and re-run `all` from a fresh server before quoting any fn-CPU or KPI number.

## 19. v9.6 — reviewer green light, but a loadgen-truth bug found in the transcript (2026-08-04)

The fresh-reset v9.5 run was independently reviewed. Verdict: **internally consistent, not corrupt** — three independent measurements now triangulate on the same per-invocation function cost:

| cross-check | value |
|---|---|
| bench run-to-run (5×, fresh-reset session) | `fn` CPU/inv 3.80 ms, host_saturation 100.0–100.3% (tight) |
| independent earlier clean session | 3.83 ms/inv |
| `--verify` (100 calls, same build) | `function_cpu_ms_per_inv` 3.22 ms, budget_check "MATCHES" |

`container_labels` also confirmed a clean two-bucket world: **all 12 non-`fnserver` containers are image `hello:0.0.14`** (the deployed function), so the v9.5 allowlist classification is proven, not assumed.

**But the transcript exposed a loadgen-truth bug (fixed in v9.6):**
Every run in that session printed `WARNING: hey unavailable/failed -> python load generator` (×5), yet `summary.json` reported `env.loadgen: "hey"`. The harness recorded the **requested** loadgen, not the **actual** one. The exact hey failure is not yet confirmed (the box is gone), but `run_hey` had three **silent** `None`-return paths (binary-not-found, subprocess exception, JSON parse) that print nothing — only the `rc != 0` path was loud, and no `hey failed (rc=...)` line appeared, so hey died on a silent path. The fallback therefore ran invisibly.

Consequences and fixes (v9.6):
- **QoS numbers of the v9.5 fresh run are Python-generator measurements, not hey's.** Container-level metrics (`fn` 3.80 ms/inv, `cp_dynamic_share_pct` 23.88%) are loadgen-agnostic and remain valid — the triangulation above holds. But that run's `"loadgen": "hey"` label was wrong.
- **Every failure path is now loud.** `run_hey` prints a reason on binary-not-found, subprocess exception, non-zero rc, and JSON parse failure, so a silent fallback cannot recur and the next run will name the actual cause.
- **Defensive NaN/Inf sanitize.** Go's `encoding/json` emits bare `NaN`/`Inf` for non-finite floats; while Python tolerates them, they poison percentile math and make `hey.json` invalid for strict consumers (R/JS). Sanitized to `null` so artifacts stay spec-valid.
- `env.loadgen` now records the **actual** generator used (`"hey"` or `"py"`); `env.loadgen_requested` preserves what was asked for; `env.loadgen_fallback` is `true` only when hey was requested but unavailable.
- **Coverage tail fixed:** runs 1–2 missed 95% (93.6%/92.9%). Root cause: the sampler's last scheduled rescan can be starved past the window end on a saturated host, truncating the sampled span. `cgroup_sampler` now flushes a final sample on `stop`, and `sampling_covered_s` is clamped at `wall_s` so coverage cannot read >100%.

**Reviewer's open items (unchanged — bare-metal gates, not code bugs):**
1. **Host saturation is real and stable (100.0–100.3%)**: a genuinely saturated 2-vCPU box. QoS p50/p99 (54.9/151.75 ms) carry zero scheduling headroom → bare-metal gate applies to QoS, not just energy.
2. **Verify p99 (733 ms) ≫ bench p99 (151.75 ms)**: expected — `--verify` runs the first 100 calls immediately after `fn deploy`, so the tail is cold-start/Docker overhead. `verify.json` is a CPU-budget sanity check, **not** a QoS measurement; do not quote its tail latency.
3. **Still one platform (Fn only), zero RAPL ground truth.** Per §11 plan, next is OpenFaaS on the identical protocol + fresh-reset discipline. Discrimination gate: if `cp_dynamic_share_pct` differs from Fn's ~24% by >5 pp, the methodology discriminates → clear for bare metal.

**v9.6 test status:** py_compile + `test_review_v96.py` green (hey NaN sanitize; `env.loadgen` records actual; `loadgen_fallback` truth; coverage clamp ≤ wall).

## 20. v9.7 — image-handle bug: the allowlist and the fresh-session cleanup were inert by default (2026-08-04)

A second reviewer gave the v9.5 fresh run a green light (internal consistency 9.5/10, "no unclassified CPU … every CPU second attributed somewhere" singled out as excellent). That praise forced a re-check of *how* the zero came about — and exposed a **genuine bug**:

- The v9.5 allowlist default was `--fn-images fnproject/python:3.12` — the **base runtime** the function image is built **FROM**.
- The running function containers carry the **deployed** image `hello:0.0.14` (visible in that same run's own `container_labels`).
- `fn_allow_active = bool(fn_sub or fn_members)` → since `fn_members` matched nothing, the allowlist **silently deactivated** and classification reverted to the denylist default. `unclassified_cpu_s: 0.0` was again vacuous (nothing had a chance to be unclassified). The label audit made the *result* correct, but the *mechanism* was off.
- The same wrong image was in `reset_fn`'s `ancestor=fnproject/python:3.12` filter. BuildKit does not reliably preserve the FROM lineage, so that filter never matched the running `hello:*` containers — the fresh-session cleanup would not remove a leftover warm fn container from a prior session.

**Fixes (v9.7, bugfix-only — no new metrics, per reviewer "freeze"):**
- `run_saqef.sh` now defaults `--fn-images hello` — the **deployed** image name (`hello:*`), overridable via `SAQEF_FN_IMAGES`.
- `reset_fn` removes containers whose image name matches `hello:*` (docker-ps format loop), not an `ancestor=` guess.
- **Fail-open classification:** when an fn allowlist is configured but matches no container, the harness WARNs loudly and routes strays to `unclassified_cpu_s` — it can no longer silently fall back to the denylist. `--fn-images` help text now says "match the DEPLOYED image, not the base runtime."
- The v9.5 numbers are **unaffected** (`container_labels` proved a genuine two-bucket world), but the protection is now actually enforced, and a future wrong default is loud.

**v9.7 test status:** py_compile + `test_review_v97.py` green (allowlist-configured-but-empty is fail-open → strays unclassified, denylist not used; default denylist back-compat; warning path; `_class_matches` deployed-image semantics).

**Freeze declaration:** with v9.7 the measurement framework is frozen unless a genuine bug surfaces. Remaining work is experiments, not metrics: (1) repeatability validation — `SAQEF_REPEAT=10 ./run_saqef.sh bench` on Fn, check `cp_dynamic_share_pct` within ±2%, both KPIs stable, CP energy ∝ CP CPU; (2) cross-platform discrimination — OpenFaaS (then Knative/OpenWhisk) with the identical protocol, `cp_dynamic_share_pct` vs Fn's ~24%; (3) bare metal — RAPL ground truth + the native→Docker→containerd→FaaS decomposition the reviewer proposed.

## 21. v9.7 Fn validation run — the repeatability gate (2026-08-04)

`./run_saqef.sh all` from a fresh session, deployed image bumped to `hello:0.0.15`. Median of 5×3000 (c=20, cgroup + delta-check). **All gates pass on every run:**

| run | delta% | cp_cpu_s | fn_cpu_s | plausible | host_sat% | host_plausible | coverage% |
|---|---|---|---|---|---|---|---|
| 1 | 0.01 | 3.65 | 11.22 | True | 100.2 | True | 100.0 |
| 2 | 0.01 | 3.62 | 11.38 | True | 100.2 | True | 100.0 |
| 3 | 0.01 | 3.68 | 11.39 | True | 99.9 | True | 100.0 |
| 4 | 0.00 | 3.60 | 11.37 | True | 100.1 | True | 100.0 |
| 5 | 0.00 | 3.66 | 11.35 | True | 100.2 | True | 100.0 |

**This directly answers the second reviewer's repeatability gate** (stability across runs):
- `cp_dynamic_share_pct` median **24.38**, spread **24.07–24.54**, CV **0.83%**, IQR 0.30 → well inside ±2%.
- fn CPU/inv 11.22–11.39 s ÷ 3000 = **3.74–3.80 ms**, consistent with both prior sessions (3.83, 3.80) and `--verify` (3.56 ms, budget "MATCHES") — session-stable.
- Between-session: `cp_dynamic_share_pct` 24.38 vs 23.88 (0.5 pp); the between-session spread is now resolved.
- **Coverage 100.0% on all 5 runs** — the v9.6 stop-time flush landed; the reviewer's only earlier data concern is gone.
- **Allowlist now genuinely exercised:** all 12 fn containers are image `hello:0.0.15`, matched by the v9.7 default `--fn-images hello`; no fail-open warning fired; `unclassified_cpu_s: 0.0` is now enforced, not coincidental.

**hey diagnosis (v9.6 diagnostics paid off):** every run printed `hey: JSON parse failed (Expecting value: line 1 column 1 (char 0)): json` — i.e. the `hey` binary ran with rc=0 but its stdout was the literal string `json`, not a JSON report. `/go/bin/hey` is **not behaving as rakyll/hey** (a real hey prints a full report). `env` now records the truth (`loadgen: "py"`, `loadgen_requested: "hey"`, `loadgen_fallback: true`), so the QoS numbers (p50 55.6, p99 140.1 ms) are Python-generator measurements, container-level metrics are loadgen-agnostic and unaffected. Action before cross-platform runs: `hey -h` + `ls -la json` to identify the binary, reinstall the official release, and fix the generator once so the same generator is used across platforms.

> **CORRECTION (v9.9, see §23):** the "not behaving as rakyll/hey" conclusion was wrong. The `/go/bin/hey` binary was genuine rakyll/hey v0.1.5 all along (confirmed via `go version -m`). Mainline hey **has never had an `-o json` mode** — `-o csv` is the only alternative to its default text summary, and any other `-o` value (e.g. `json`) is parsed as a literal text/template, printing the string `json` to stdout with rc=0. The v9.6–v9.8 JSON-era smoke test was testing a mode hey never shipped; the v9.9 fix requests the documented `-o csv` and parses it, which is why `./run_saqef.sh all` (v9.9) ran with `loadgen: "hey"` for the first time. The v9.7 QoS numbers (p50 55.6 ms) are still Python-generator measurements and stand as labeled.

**KPI (median):** op 0.0093 gCO₂/inv, dynamic 0.000838 gCO₂/inv; `verify.json` function_cpu_ms_per_inv 3.56 (MATCHES). Results committed to `results/fn_cpubound_v9/`.

## 22. v9.8 third-expert corrections — hey functional gate + QoS saturation flag (2026-08-04)

Third external expert review of `run_saqef.sh` + `saqef_harness.py` surfaced two genuine gaps in the measurement discipline. Both are code, not metrics, so both are fixed within the v9.7 freeze:

**(a) hey reinstall gate was size-only.** The old check reused any `hey` on PATH with `stat -c%s ≥ 1000`. That cannot tell a real binary from a truncated 403-HTML page or a wrong binary of ≥1000 bytes. **Fix (v9.8):** `hey_smoke_ok()` runs the candidate and parses its output; fail → wipe every copy (`/go/bin`, `/usr/local/bin`) and reinstall (go install, then binary-download fallback), re-smoke-testing the replacement. A still-broken install prints a warning and the Python generator fallback carries the run (`loadgen_fallback` stays truthful).

> **CORRECTION (v9.9, §23):** the v9.8 gate tested `-n 2 -c 1 -o json` — but mainline rakyll/hey has **no `-o json` mode**, so the smoke test kept failing against a perfectly good binary. The gate is correct in *structure* (functional, not size-based); its *probe* was wrong. v9.9 probes the one mode hey documents (`-o csv`) and the gate finally passes — with the first genuine `loadgen: "hey"` run as proof.

**(b) the ≥85% saturation QoS-caveat rule was documented but not enforced.** §17 and the §7.4 draft said a run at ≥85% host saturation is contention-contaminated and must be flagged; `host_plausible` only checked the physical ceiling (≤105%), and nothing in `summary.json` carried the flag. **Fix:** new `host_saturated_flag()` (shared, unit-tested) enforces `sat_pct ≥ 85.0`; every `summary.json` now emits `host_saturated`; the `gates` table prints `host_saturated` per run with an explicit "QoS CONTENTION-CONTAMINATED — do not cite latency" marker when true.

**Re-validation:** `python -m py_compile` + `bash -n` clean; all six review test suites pass (v9.4–v9.8, incl. 8 new v9.8 assertions). Committed `ad7a063` → v9.8. The v9.7 published numbers are unaffected: both fixes are gates/audit, no measurement path changed. `host_saturated` for the v9.7 runs would be `true` (~100%) — that is precisely the §7.4 Codespace-scope caveat that already governs the QoS claims.

## 23. v9.9 hey CSV root-cause fix + first real hey run (2026-08-04)

**Root cause of the entire v9.6–v9.8 hey saga (now proven):** mainline rakyll/hey has never had a JSON output mode. `requester/print.go` `newTemplate()` switches on `""` → default text template, `"csv"` → CSV template, and **any other `-o` value is parsed as a literal text/template** — so `-o json` printed the literal string `json` to stdout (rc=0), which the JSON-era parser (correctly) rejected. Verified directly against upstream source (`requester/print.go`, README, `hey.go`). The v9.7 "not a rakyll/hey binary" theory was wrong: `go version -m /go/bin/hey` shows genuine `github.com/rakyll/hey v0.1.5` (go1.26.1).

**Fix (expert-verified, in the working tree since v9.8-era):** `run_hey` now requests `-o csv` — the one output mode hey documents and ships — and parses the per-request rows itself (header normalization `re.sub(r"[\s\-+]","",fn).lower()`, columns `responsetime`/`statuscode`/`offset`). CSV schema: `response-time,DNS+dialup,DNS,Request-write,Response-delay,Response-read,status-code,offset`. The `hey_smoke_ok()` gate probes `-o csv` (header + ≥1 data row). This also removed the last dependency on hey's built-in percentile math (we need `lat_points` for `cdf_compliance()` anyway). Parsing validated 5/5 against mocked-upstream fixtures.

**v9.9 validation run — the first `loadgen: "hey"` in the project's history** (`./run_saqef.sh all`, fresh session, `hello:0.0.20`, 5×3000, c=20, cgroup + delta-check):

- `setup`: `hey OK: /go/bin/hey (functional smoke test passed)`; `verify.json`: 100/100, `function_cpu_ms_per_inv` 3.57, budget "MATCHES".
- Every run: `loadgen: "hey"`, `loadgen_requested: "hey"`, `loadgen_fallback: false`, `compliance_source: "hey_interp"`, 0 errors.
- Median: throughput **335.4 rps** (spread 331.6–353.5, CV 2.64), p50 **47.0 ms**, p90 102.2, p99 203.7, max 315.9, `slo_compliance` 1.0; **`cp_dynamic_share_pct` 24.07** (spread 22.24–24.31, CV 3.63, IQR 0.19) — within ~0.3 pp of the Python-generator medians (23.88 / 24.38 / 24.59), confirming container-level energy attribution is loadgen-agnostic, as designed. Energy J total 304.9 / dynamic 27.7 / cp 6.7 / fn 20.9; cpu_sec cp 1.92 / fn 5.98; unclassified 0.0; cp_peak_mem 56.7 MB; KPI op 0.0049, dynamic 0.000442 gCO₂/inv.
- Gates: delta% 0.00–0.15, plausible=true, host_plausible=true, **host_saturated=true (~101%)** → the v9.8 enforcement now fires live on every run ("QoS CONTENTION-CONTAMINATED — do not cite latency"), i.e. the §7.4 Codespace-scope caveat is machine-enforced rather than stated.
- Env: cgroup quota `['max','100000']`, cpu_count 2, steal 0, RAPL unavailable (CPU-time model only).

**Two cleanups folded into the v9.9 commit (genuine bugs, both in the previously-dead hey code path):**

- **(u) Coverage/wall invariant broken by the hey branch.** `run_once` overwrote `wall` with hey's CSV wall (`max(offset)` = time of the **last request start**), a fraction of a second shorter than the harness window over which energy is attributed (`e_total = idle_w*wall + e_dynamic`). Result: `sampling_covered_s` (9.22 s) > `wall_s` (8.94 s) → coverage 103%, silently violating the G3 "coverage ≤ 100%" invariant (v9.6 clamp was capping against the *harness* window while `wall_s` reported the *hey* window). **Fix:** the hey branch no longer reassigns `wall`; it stays the harness clock and hey's own duration is exposed only as `loadgen.wall_s` (cross-check). Coverage now reads 100.0% and all window-derived fields (`wall_s`, `throughput_rps`, `cpu_sec_ceiling`, `host_saturation_pct`, `idle_band` carbon) are mutually consistent. The gates table additionally flags any `coverage% > 100` as a hard invariant break.
- **(v) hey `-t` unit bug.** hey's `-t` is **seconds** per request (default 20), but `run_hey` passed `timeout_ms` raw (30000 → a 30,000-second per-request timeout). Fix: `-t max(1, timeout_ms // 1000)`.
- Stale JSON-era text cleaned: output file renamed `hey.json` → `hey.csv`; smoke-test/report messages now say CSV; no runtime `-o json` / `errorDistribution` references remain.

**Regression coverage:** `test_review_v99.py` (28 checks) + the existing `test_hey_csv.py` (5 tests) pass, covering the `-o csv` argv, `-t` seconds, CSV parse (wall = max offset, rps, p50/p90/p99), the no-`wall`-reassignment invariant, the coverage≤100 gate formula, and absence of stale JSON references. Committed as **v9.9**. The hey issue that blocked cross-platform runs is resolved; the OpenFaaS validation (§§24+) proceeds with the same `hey -o csv` path on both platforms.

**v9.9 post-commit re-verification (fresh `git pull`, `hello:0.0.21`, 2026-08-04):** `./run_saqef.sh all` re-run from the committed tree. Gates on all 5 runs: **coverage 100.0%** (pre-fix: 103.6/102.5/102.5/102.1/103.6), `wall_s = wall_harness_s = sampling_covered_s = 8.97 s` (structurally equal now, not display-clamped), delta% 0.01–0.31, plausible=true, host_plausible=true, host_saturated=true (100.1–101.7%) → the contention caveat fires live on every run. Median: `cp_dynamic_share_pct` **23.59**, slo_compliance 1.0, throughput_rps 334.3, fn_cpu_s median 5.60 (run_1 low outlier 4.30 — warmup/container-set effect, dampened by the median). Session-median series for `cp_dynamic_share_pct` is now **23.88 / 24.38 / 24.59 / 24.07 / 23.59** — total spread 1.0 pp, G6 satisfied.

**Reviewer items from the v9.9 pass (recorded, not engineered):**

- **verify vs bench per-inv CPU gap (~2.0x, known cause, deferred).** `verify` (100 calls immediately after fresh deploy) reported 3.76 ms/inv; bench steady-state 5.60 s / 3000 = **1.87 ms/inv**. Cause: verify's window includes the first cold container boots — its own p99 of 791 ms confirms cold-start exposure — so per-inv CPU is boot-inflated, while bench's `--warmup 20` amortizes boot over 3000 warm calls. Not a correctness bug: verify is a sanity check, not a QoS/budget measurement, and its `budget_check` must not be read as the steady-state per-inv cost. Paper-time action (deferred per reviewer "a note, not another engineering cycle"): bump `VERIFY_N` (e.g. 500) and/or warm up before verify timing.
- **Coverage >100% ("clamp the display")** — the reviewer's comment referenced the pre-fix output; v9.9 removed the divergence at the source (`wall_s` = harness clock), so no display clamp was needed.
- **Deferred to paper time:** `SAQEF_REPEAT` 5 → 10; the vegeta open-loop swap is deprioritized (container-level energy attribution is loadgen-agnostic and stable across four loadgen/session combinations).

## 24. OpenFaaS deployment on Codespace (2026-08-05)

**Goal (cross-platform validation):** run the identical 5 ms busy-spin function on OpenFaaS vs Fn and test the discriminator — `cp_dynamic_share_pct` differing by more than the 5 pp gate. OpenFaaS chosen over Knative/OpenWhisk for the *small, identifiable control-plane container set* that `--cp-containers` needs.

**Deployment (hand-written swarm stack — `OPENFAAS_DEPLOY/docker-compose.yml`), because several upstream pieces were broken:**

| Bug found | Fix | Commit |
|---|---|---|
| faas tags no longer ship `deploy_stack.sh` | hand-written stack: gateway `functions/gateway:0.8.3`, faas-swarm `functions/faas-swarm:0.3.3`, queue-worker `functions/queue-worker:0.4.6`, nats `nats-streaming:0.25.6`, prometheus `prom/prometheus:v2.11.0`, alertmanager `prom/alertmanager:v0.18.0` | `dba8b34` |
| faas-swarm `DOCKER_API_VERSION=1.30` rejected by modern dockerd | bumped to 1.40 | `d23a86f` |
| template store no longer ships a python3 template | hand-written `OF_FUNCTION/` (of-watchdog 0.9.12 + python:3.11-alpine); streaming→http mode | `693204b`, `7ba5107` |
| of-watchdog binds its own metrics listener on 8081 → python bind crashed (`OSError: [Errno 98] Address in use`) | upstream moved to **8082** | `3d41ecf` |
| python `HTTPServer` serialized concurrent invocations (unlike Fn's concurrency model) | `ThreadingHTTPServer` | `fd08159` |
| harness `docker_sampler` accepted 3 args but `start_sampler` always passes 4 → sampler thread crashed under the **default** `--sampler docker`, zeroing function CPU | signature made 4-arg compatible; protocol pinned to `--sampler cgroup` | `6dfd003` |

Gateway 0.8.3 has no basic auth → `--auth`/login removed from the protocol. `OPENFAAS_SETUP.md` was rewritten around the hand-written stack (no 0.27.14 checkout).

## 25. Delta-check first-container bug — diagnosed and FIXED (2026-08-05)

**Symptom:** the OpenFaaS run reported `cp_sampler_vs_delta_pct ≈ 15061–20748%` with `cp_delta_sec 0.009`, while the sampler's control-plane total was 1.67 s.

**Root cause:** `cp_cgroup_reader` (harness) resolved only the **first** container matching `--cp-containers` and diffed that single container's cumulative counter. For Fn (`fnserver`, one container) the delta-check was like-for-like (0.01%). For OpenFaaS's 6-container set it compared the whole-CP sampler sum against one idle container (alertmanager, ~0.009 s of real work) — a meaningless 18943×.

**Fix (`b4dbbdc`):** the reader now sums cumulative CPU across **all** control-plane containers matching `--cp-containers`, re-resolving the container list on every read (swarm task restarts cannot wedge it). Paper draft `--delta-check` description updated (`cce0579`). **The attribution itself was never in doubt:** it is per-container kernel `cpu.stat` cumulative differencing with `unclassified_cpu_s 0.0` and coverage 100% — the same mechanism that passed 0.01% delta-check on Fn.

## 26. OpenFaaS measurement results (2026-08-05) — 5×3000 cgroup runs

**Protocol identical to Fn:** `--sampler cgroup --delta-check --loadgen hey`, 5×3000 @ concurrency 20, 20 s warmup, URL `http://127.0.0.1:8080/function/hello`, `--fn-images hello`, `--cp-containers gateway,faas-swarm,prometheus,nats,queue-worker,alertmanager`.

| run | cp_dynamic_share_pct | throughput_rps | slo_compliance | coverage% | unclass | host_saturated | delta% (pre-fix artifact, §25) |
|---|---|---|---|---|---|---|---|
| 1 | 10.75 | 142.32 | 0.9209 | 100 | 0.0 | true (92.9%) | 18942.88 |
| 2 | 10.90 | 143.36 | 0.9195 | 100 | 0.0 | true | 20748.24 |
| 3 | 11.17 | 139.68 | 0.9312 | 100 | 0.0 | true | 19473.09 |
| 4 | 11.23 | 139.26 | 0.9211 | 100 | 0.0 | true | 15061.03 |
| 5 | 11.10 | 140.85 | 0.9192 | 100 | 0.0 | true | 14955.10 |
| **median** | **11.10** | **140.85** | **0.9209** | 100 | 0.0 | — | — |

**Attribution audit:** `container_inventory` = exactly 7 containers per run (1 function `hello:latest` + the 6 CP at the pinned stack images); `container_labels` prove image-based matching; `unclassified_cpu_s 0.0`; `physical_plausible true`. Verify parity: `openfaas_verify.json` 100/100, `function_cpu_ms_per_inv 3.88`, `budget_check MATCHES` (Fn: 3.76) → same workload band.

**Discriminator verdict:** median `cp_dynamic_share_pct` **11.10** (spread 10.75–11.23) vs Fn **24.59** (23.59–24.59) → gap ≈ **13.5 pp > 5 pp gate** ⇒ the metric discriminates between platforms.

**Caveats (must accompany the claim):** (1) `host_saturated true` on both platforms → the QoS-contention caveat applies symmetrically; (2) OpenFaaS's of-watchdog proxies inside the *function* cgroup, so per-request routing work lands in `function_cpu`, whereas Fn's fnserver absorbs it in `control_plane` → the gap is **conservative**, not inflated; (3) RAPL unavailable → energy numbers remain model estimates until bare metal; (4) the committed summaries carry the pre-fix delta artifact (§25) — numbers are unaffected, a re-run with the fixed harness yields `cp_delta_sec ≈ 1.66` and delta ≈ 0.

## 27. Session log + bare-metal handoff (2026-08-05)

**Codespace push auth (recorded):** the Codespaces auto-token (`ghu_…`) cannot write to repos → `git push` 403'd even though `gh auth status` showed the account. Fixed with device-flow `gh auth login` (requires `unset GITHUB_TOKEN` first, otherwise gh reuses the environment token) + `gh auth setup-git`.

**Results committed from the Codespace:** `6dc2120` — `results/openfaas_cpubound/` (run_1..5, runs.json, summary.json) + `results/openfaas_verify.json` (24 files). Note: the committed set predates the §25 harness fix, so it carries the pre-fix delta artifact.

**Local directory now the git repo:** `C:\Users\MERCURY LAPTOP\Documents\Default Project\saqef` was initialized as the git repository, tracking `origin/main` at `6dc2120`; all source files verified byte-identical to the remote. The temporary push-clone under the opencode temp dir is retired (disposable). `old-working code/` remains untracked (backup decision pending).

**Bare-metal checklist (Linux):** `git clone https://github.com/bathork1391/saqef.git` → docker (swarm or single dockerd) → deploy pinned stack + function → `python3 saqef_harness.py --check` (RAPL *should* be available on real hardware → direct energy, removing the CPU-time-model caveat) → `--verify` → bench 5×3000 → `gates`. Run at `concurrency < cpu_count` to exit the host-saturated regime → removes the QoS-contention caveat. No re-engineering required.

## 28. External expert review of the OpenFaaS run — two findings (2026-08-05)

Reviewer verdict: gate passed (13 pp vs 5 pp) but **not signed off** — two findings, one is a real workload-vs-platform confound, one is a data-visibility gap. Responses:

**Finding A — OpenFaaS function is GIL-serialized, so `--concurrency 20` is ~1-way in practice (confirmed).** The deployed function is one replica, one Python process (`ThreadingHTTPServer` on 8082 behind of-watchdog), and `handler.py` is a pure CPU busy-spin that never releases the GIL — so concurrent connections time-slice instead of running in parallel. 3000 × 5 ms ≈ 15 s serial floor vs observed `wall_s` 21.3 s is consistent. Fn spawned 10–18 separate containers (processes) for the same setting. `cp_dynamic_share_pct` is *roughly* conserved (busy-spin CPU-seconds are scheduling-order-invariant), but **wall_s / throughput / KPI / QoS are not comparable** between platforms for a reason unrelated to either platform's overhead — same category as the sleep-vs-spin trap §4 warns about, at the deployment layer. **Fix in progress (§29):** run the function as multiple replicas (the platform-native concurrency mechanism) so delivery conditions match Fn's.
- *Response:* agree. The throughput gap (335 vs 141 rps) is dominated by this, not by platform overhead. The `cp_dynamic_share_pct` ratio may survive, but the fair test requires real parallelism first.

**Finding B — `cp_delta_sec 0.009` over a 21 s window is physically impossible (correct).** Reconciliation: the *committed* OpenFaaS summaries predate the `b4dbbdc` fix and carry the old **first-container** snapshot (alertmanager's real ~9 ms), not the whole-set sum. The current summing logic is correct (reviewer confirmed) and the expert's residual worry — that `container_cgroup_dir` may be silently failing to map most Swarm containers — is plausible-but-unverified. **Fix (implemented):** `cp_cgroup_reader` now records a per-container mapping (`delta_check_map`: name → `ok`/`read-failed`/`unmappable`) and logs it, so the next run shows *which* CP containers mapped instead of guessing.
- *Response:* the diagnostic is in (committed with this section); the rerun's `cp_delta_sec` must read ≈ `cpu_sec.control_plane` and the map must show 6/6 `ok`, else the delta-check is invalid on that host.

**Minor (noted, not blocking):** `openfaas_verify.json` p99 1294 ms at 100 calls (≈ serial) — likely 2019-era gateway overlay/DNS re-resolution (`direct_functions: true` + `dnsrr: true`); record as a report footnote if it persists. **Standing caveat unchanged:** `host_saturated: true` → QoS percentiles not citable; `cp_dynamic_share_pct` remains the contention-robust metric, which is exactly why Finding A matters.

## 29. Finding A fix — parallel function delivery (resolved, codespace scope, 2026-08-05)

Chosen approach (of the reviewer's two options): **static multi-replica scaling** of the function service — the platform-native concurrency mechanism, semantically closest to Fn's multiple function containers. Autoscaler lag (alert `group_wait`/`group_interval` + Prometheus `evaluation_interval` ≈ 15–20 s vs a ~21 s window) is bypassed by scaling the service *statically* before the run: `docker service scale hello=N`. With `--fn-images hello`, all replicas match the function allowlist (attribution intact). Decision: **replicas, `N = 2 × cpu_count` = 4 on this box** (the reviewer's other option, in-handler `gunicorn -w N`, was rejected: it is not how OpenFaaS is designed to run, diverges from Fn's container-per-invocation model, and adds a deployment dependency). Protocol step recorded in `OPENFAAS_SETUP.md` §6 Step 1.5. Rerun protocol unchanged otherwise (§24–26 flags), new outdir `results/openfaas_cpubound_v3`.

**v2 rerun status (committed `5bb0f7f`, 2026-08-05):** the 5×3000 run with the whole-CP delta-check **passes** — `cp_sampler_vs_delta_pct` −0.26/0.49/0.02/0.14/0.09, and `cp_delta_sec` matches `cpu_sec.control_plane` to 0.01 s (1.656≈1.65 … 1.663≈1.66). This empirically **disproves** the reviewer's partial-mapping hypothesis (if any of the 6 CP containers had failed to map, the summed delta would read below the sampler total). `delta_check_map` lands in every summary from the harness commit in §28 onward, so the next run also proves 6/6 `ok` explicitly. Median `cp_dynamic_share_pct` **11.1** (11.03–11.26), rps ~139. **Remaining: the multi-replica rerun (v3) for delivery-condition parity, then sign-off.**

**v3 status (4 function replicas, `N = 2 × cpu_count`, 2026-08-05):** three independent 5×3000 sessions at 4 replicas give medians **15.87 / 16.29 / 15.82** — `results/openfaas_cpubound` holds the final committed session, **15.82** (spread 15.56–16.20; each session's within-run spread ≤ 0.7 pp, CV ~2%); between-session drift ≈ ±0.4 pp on a saturated shared box. Throughput ~287–293 rps (≈2× v2, not 4× — consistent with the 2-core ceiling, not replica-starved). **The v2 sentence above claiming the busy-spin ratio is "scheduling-order-invariant as predicted" is SUPERSEDED — it is not.** Going 1→4 replicas moved the headline 11.1 → ~15.8–16.3, a ~45% relative shift. The reviewer's causal story for this (serialization making CP "do more work per invocation") is **contradicted by the data's direction**: it predicts the share should have *dropped* when serialization was removed, and it rose. The defensible reading: under 1 replica, 20 concurrent requests queue on one GIL-bound process and the CP components (gateway/queue-worker/nats) spend most of the window **idle** — per-wall-second CP busy went ~0.089 → ~0.27 (≈3×) from the replica fix, i.e. CP busy-time tracks *active concurrency*, not queue length. The 1-replica 11.1 therefore **understated** CP's contribution; ~15.8–16.3 is the protocol-conformant range. **Replica-insensitivity confirmed:** a single 8-replica run (`results/openfaas_cpubound_quick`) gives **16.12** — inside the 4-replica session spread, so `N = 2 × cpu_count` is settled. Final comparison: Fn **23.59–24.59** vs OpenFaaS **15.82** → gap ≈ **7.8–8.8 pp > 5 pp gate**, still discriminating; the uncorrected ~13.5 pp figure would have overstated the platform difference by >50%.

**v3 caveat — host plausibility (root-caused, fixed in v9.10/v9.11):** run_2 of the first v3 session tripped `host_plausible: false` (`host_cpu_sec` 23.23 vs ceiling 20.78 = 2 cores × 10.39 s wall, sat 112%) — the first host-ceiling break in the project, on the *shortest* run yet. Root cause, now **proven** rather than hypothesized by the v9.10 rerun: the host `/proc/stat` window is not the load window. `host_window_s` came out 11.79 vs `wall_s` 10.43 — the ~1.4 s gap being the delta-check reader construction (`cp_cgroup_reader`, ~1.5 s on this box) that sat between the host read and `t0`; on a box that is ~100% busy regardless of load, that headroom adds ~2 cores × 1.4 s ≈ 2.8 s of busy ticks, which is exactly the excess behind the old wall-based 112%. Since busy ticks over a window `W` can never exceed `cpu_count × W`, v9.10 defined `host_saturation_pct`/`host_plausible` against the host's **own** window (self-consistent; the gate can only trip on a real CPU-count/counter anomaly) and v9.11 moved the host read to `t0` so `host_window_s == wall_s` by construction. The committed dataset ran on v9.10 (so its JSONs show `host_window_s` 11.7–11.8 vs `wall_s` 10.3–10.4); a v9.11 rerun only aligns that cosmetic field — the cgroup-based headline (`cp_dynamic_share_pct`) is invariant to both changes. On v9.10 all 5 runs read `host_saturation_pct` 98.9–99.4, `host_plausible: true`, and the honest `host_saturated: true` QoS caveat fired (this Codespace is genuinely ~100% busy). The OpenFaaS `gates` table also regained the host columns with a hard "HOST IMPLAUSIBLE — do not cite host metrics" marker when false, so this class of failure cannot be committed silently again.

**Final gate status of the committed set (all pass):** delta% −0.01…0.56 (median 0.08), `delta_check_map` 6/6 `ok` on all 5 runs, `cp_delta_sec` ≈ `cpu_sec.control_plane` to 0.01 s, coverage 100.0%, `physical_plausible: true`, `unclassified_cpu_s: 0.0`, `host_plausible: true`. SLO compliance median 1.0 (one run dipped to 0.939 with p99 1037 ms — saturated-box noise under the documented QoS caveat, does not affect the CPU-share headline). **Remaining: bare-metal/RAPL handoff (§27) to lift the Codespace-scope caveats; the OpenFaaS-vs-Fn discriminator on the codespace is settled.**

## 30. Bare-metal milestone COMPLETE — RAPL validated, but the 5 pp gate FAILS on bare metal (2026-08-05)

Ran the full protocol-conformant pair on an 8-core Ubuntu box with RAPL (`setup_baremetal.sh`:
docker + CLIs + RAPL made user-readable). This section is the durable record of the milestone;
the two `*_sat_invalid` dirs (old saturated c=20 bare-metal attempts) are preserved but must not be cited.

**RAPL calibration (the fix for the ~45% validation error).** Machine idle package power measured
**4.3 W** via a 60 s RAPL read with the stack up and zero traffic. The hardcoded `idle_w=30`
(default) was a 7× overestimate and produced `rapl_validation_err_pct` ≈ 44–46% on both platforms
(e_total = idle_w*wall + dynamic ran 25.7 W × wall above the real package energy). Calibrating
`--idle-w 4.3` dropped validation to **4.2–5.5% steady-state on Fn** (runs 4–5; runs 1–3
transient 36→19→19 as the stack/frequency settles) and **4.2–8.2% steady-state on OpenFaaS**
(run_1/2 transient 34.6/25.9). Both platforms show the same ~2-run settling transient — a
property of the measurement box, not a platform difference. (The "0.9–6.1% on Fn" figure quoted
in an earlier draft belongs to the DISCARDED tainted run and must NOT be cited.) Tooling change:
`SAQEF_IDLE_W` passthrough added to both runner scripts (knob only — no measurement-path change;
the harness already supported `--idle-w`). Result: the CPU-time model now reconciles with real
RAPL, so the absolute energy/carbon numbers are citable with the busy-core 2/3.5/5 W sensitivity
band.

**Protocol-conformant runs (this is the milestone).** `c=4 < cpu_count=8`, `TOTAL=10000` (~17–19 s
windows vs the 2.7 s of the old saturated attempts), 5 runs, 20 s warmup, `--sampler cgroup
--delta-check --loadgen hey`, OpenFaaS statically scaled to 16 replicas (2 × cpu_count). All gates
green on both platforms: delta% ~0, `delta_check_map` 6/6 `ok` (OF) / 1/1 (Fn), coverage 100.0%,
`physical_plausible` + `host_plausible` true, `unclassified_cpu_s 0.0`, `host_saturation_pct`
73–77% → **`host_saturated=false`** (first time QoS is citable in the project's history).

**Results.** Fn median `cp_dynamic_share_pct` **10.46** (9.80–11.74, CV 8.08%, IQR 1.38 — run-order
drift on fnserver, runs 1–3 ~9.8–10.5, runs 4–5 ~11.7, consistent with container GC/freeze churn
accumulating in the control plane across a session) vs OpenFaaS **7.67** (6.80–7.83, CV 6.65%,
IQR 0.84). **Gap ≈ 2.8 pp — the 5 pp decision gate FAILS on bare metal.** Per-request CP cost:
Fn 0.66 ms, OpenFaaS 0.56 ms (both ~constant); per-request fn cost: Fn 5.62 ms, OpenFaaS 6.71 ms
(of-watchdog proxies inside the function cgroup, so OpenFaaS's share is conservatively *low* and
the true gap is even smaller). `cp_share_pct` (CP/total machine energy, now against the REAL
4.3 W idle): Fn 7.88, OpenFaaS 5.79. QoS (now citable): Fn p50 6.5 / p99 8.9 ms @ 597 rps;
OpenFaaS p50 7.2 / p99 12.1 ms @ 532 rps; SLO compliance 1.0 both.

**Regime-dependence is the headline finding.** The Fn-vs-OpenFaaS gap is ~7.8–8.8 pp on the
saturated 2-vCPU codespace but ~2.8 pp on an 8-core box with headroom; the direction is stable
(Fn's control-plane share is always higher) but the magnitude collapses. Root cause: Fn's
per-request CP cost is 1.22 ms under saturation vs 0.66 ms here, while OpenFaaS is ~0.56 ms in
both regimes — i.e. the saturated regime inflates fnserver's apparent overhead. **The a-priori
5 pp gate is not machine-invariant; the paper must be reframed** to a per-machine-pair gate and
to present the machine/regime-dependence as a contribution (a metric whose discriminator power
depends on the co-tenancy/CPU-count regime is itself a finding). The codespace dataset remains
the saturated-regime measurement; the Finding A/B resolutions stand.

**New isolation pitfall (this session, also tainted the old invalid Fn runs).**
`docker stack rm openfaas` removes the six stack services but **NOT** the `hello` function service
(deployed outside the stack). Its replicas match the `--fn-images hello` allowlist and fold into
`fn_cpu`; because they sat idle they contributed ~0 busy CPU (gates still passed, `unclassified`
read 0.0), but the platform was not isolated. One Fn rerun was discarded on this account
(`docker service rm hello`, then re-ran). Verified clean: the reported Fn set contains only
`fnserver` + hello:0.0.7 function containers.

**Concurrency sensitivity (minimum-iteration add-on, REPEAT=2, same box):** c=8 runs in
`results/{fn,openfaas}_cpubound_baremetal_c8_quick` give Fn **10.47** and OpenFaaS **7.62**
(host_sat 91–93%) vs c=4's 10.46 / 7.67. **The share and the gap are flat with concurrency and
saturation within the 8-core box** (gap 2.79 → 2.85 pp). Combined with the codespace point
(gap 8.77 pp on a 2-core shared VM), this proves the discriminator is machine(CPU-count)-dependent,
NOT load-dependent: both platforms' shares are ~2.3× higher on the 2-core VM and Fn inflates
proportionally more. The 5 pp gate is a machine-pair property, not a platform property.

### 30.1 Exact per-run data (all four bare-metal datasets, from `summary.json`/`runs.json`)

**Fn c=4 (protocol-conformant, `results/fn_cpubound_baremetal`, the committed clean set):**

| run | share% | cp_cpu_s | fn_cpu_s | host_sat% | rapl_err% | wall_s | rps | p50 ms | p99 ms | unclass |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 9.80 | 6.07 | 55.85 | 73.7 | 36.07 | 16.7 | 600 | 6.4 | 8.8 | 0.00 |
| 2 | 10.34 | 6.48 | 56.22 | 73.4 | 19.10 | 16.7 | 597 | 6.5 | 8.8 | 0.00 |
| 3 | 10.46 | 6.56 | 56.22 | 74.3 | 18.66 | 16.8 | 597 | 6.5 | 8.9 | 0.00 |
| 4 | 11.74 | 7.57 | 56.89 | 76.1 | 5.47 | 17.4 | 574 | 6.7 | 9.4 | 0.00 |
| 5 | 11.72 | 7.54 | 56.78 | 77.3 | 4.20 | 17.6 | 568 | 6.7 | 9.7 | 0.00 |

median share 10.46 (bootstrap CI 9.8–11.74, CV 8.08%, IQR 1.38); throughput 597 (CV 2.58%);
cp_share_pct 7.88; SLO 1.0. Per-req: CP 0.656 ms, fn 5.622 ms.

**OpenFaaS c=4 (protocol-conformant, `results/openfaas_cpubound_baremetal`):**

| run | share% | cp_cpu_s | fn_cpu_s | host_sat% | rapl_err% | wall_s | rps | p50 ms | p99 ms | unclass |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 6.80 | 4.70 | 64.47 | 75.0 | 34.55 | 18.1 | 551 | 6.9 | 11.9 | 0.00 |
| 2 | 6.96 | 4.87 | 65.02 | 74.8 | 25.94 | 18.5 | 542 | 6.9 | 12.4 | 0.00 |
| 3 | 7.83 | 5.73 | 67.49 | 77.6 | 8.20 | 19.0 | 526 | 7.2 | 12.0 | 0.00 |
| 4 | 7.80 | 5.68 | 67.17 | 76.9 | 5.16 | 18.8 | 532 | 7.2 | 12.1 | 0.00 |
| 5 | 7.67 | 5.58 | 67.14 | 77.3 | 4.17 | 19.2 | 519 | 7.2 | 12.7 | 0.00 |

median share 7.67 (bootstrap CI 6.8–7.83, CV 6.65%, IQR 0.84); throughput 532 (CV 2.35%);
cp_share_pct 5.79; SLO 1.0. Per-req: CP 0.558 ms, fn 6.714 ms. delta_check_map 6/6 ok (CP =
gateway + queue-worker + faas-netesd + nats + prometheus + alertmanager), fn_replicas 16.

**Fn c=8 sensitivity (`results/fn_cpubound_baremetal_c8_quick`, REPEAT=2 — NOT a publication set):**

| run | share% | cp_cpu_s | fn_cpu_s | host_sat% | rapl_err% | wall_s | rps | p50 ms | p99 ms |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 10.25 | 5.75 | 50.32 | 90.9 | 13.67 | 11.0 | 912 | 8.0 | 15.6 |
| 2 | 10.70 | 6.06 | 50.59 | 91.3 | 1.56 | 11.0 | 911 | 8.0 | 15.7 |

median 10.475; host_sat ≥ 85% → host_saturated=true (latency NOT citable at c=8).

**OpenFaaS c=8 sensitivity (`results/openfaas_cpubound_baremetal_c8_quick`, REPEAT=2 — NOT a
publication set):**

| run | share% | cp_cpu_s | fn_cpu_s | host_sat% | rapl_err% | wall_s | rps | p50 ms | p99 ms | unclass |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 7.40 | 4.74 | 59.25 | 93.2 | 16.78 | 12.6 | 795 | 8.8 | 21.9 | 0.01 |
| 2 | 7.85 | 5.04 | 59.17 | 92.9 | 13.26 | 13.1 | 762 | 9.2 | 22.0 | 0.01 |

median 7.625; host_saturated=true (latency NOT citable at c=8); unclassified 0.01 CPU-s (a
container outside both allowlists during deploy — see troubleshooting log).

### 30.2 Cross-regime comparison (the paper's central table)

| regime | machine | c | Fn share | OF share | gap (pp) | host_sat Fn/OF | RAPL err steady |
|---|---|---|---|---|---|---|---|
| codespace, saturated | 2-vCPU shared VM | 20 | 24.59 | 15.82 | **+8.77** | ~99–100% | no RAPL |
| bare metal, headroom | 8-core, 16 GB | 4 | 10.46 | 7.67 | **+2.79** | 74–77% | Fn 4.2–5.5, OF 4.2–8.2 |
| bare metal, saturated | 8-core, 16 GB | 8 | 10.47 | 7.62 | **+2.85** | 91–93% | single-digit |

Reading: within the 8-core box the share is **invariant to concurrency/saturation** (10.46→10.47
and 7.67→7.62 when going from ~75% to ~93% host saturation), so the ~8 pp codespace gap is a
**machine (CPU-count) effect, not a load effect**. Both platforms' shares are ~2.3× higher on the
2-vCPU VM and Fn inflates proportionally more (Fn 24.59 is 2.35× its bare-metal 10.46; OF 15.82 is
2.06× its 7.67). OpenFaaS's share is a conservative upper bound anyway (of-watchdog proxies live
inside the function cgroup), so the true gap is even smaller than reported.

### 30.3 Reproduction commands (exact, this session)

```bash
# 0. provisioning (root): docker + CLIs + RAPL user-readable
sudo bash setup_baremetal.sh

# 1. calibrate idle watts: 60 s RAPL read, stack UP, zero traffic  ->  IDLE_W = 4.3
python3 -c "import time,os
p='/sys/class/powercap/intel-rapl:0/energy_uj'
def r(): return int(open(p).read())
a,b=r(),r(); time.sleep(60); a,b=r(),r()
print((b-a)/1e6/60)"   # 4.3

# 2. OpenFaaS FIRST (c=4, TOTAL=10000, 5 runs, 16 static replicas)
sudo SAQEF_REPLICAS=16 SAQEF_CONCURRENCY=4 SAQEF_TOTAL=10000 SAQEF_REPEAT=5 \
     SAQEF_IDLE_W=4.3 SAQEF_OUT=results/openfaas_cpubound_baremetal bash run_openfaas.sh all

# 3. tear down BOTH stack and function service before Fn
sudo docker stack rm openfaas
sudo docker service rm hello

# 4. Fn (c=4, TOTAL=10000, 5 runs)
sudo SAQEF_CONCURRENCY=4 SAQEF_TOTAL=10000 SAQEF_REPEAT=5 \
     SAQEF_IDLE_W=4.3 SAQEF_OUT=results/fn_cpubound_baremetal bash run_saqef.sh all

# 5. c=8 sensitivity (REPEAT=2 -> *_quick, never publishable)
sudo SAQEF_CONCURRENCY=8 SAQEF_TOTAL=10000 SAQEF_REPEAT=2 \
     SAQEF_IDLE_W=4.3 SAQEF_OUT=results/fn_cpubound_baremetal_c8 bash run_saqef.sh all
sudo SAQEF_REPLICAS=16 SAQEF_CONCURRENCY=8 SAQEF_TOTAL=10000 SAQEF_REPEAT=2 \
     SAQEF_IDLE_W=4.3 SAQEF_OUT=results/openfaas_cpubound_baremetal_c8 bash run_openfaas.sh all
```

Notes: scripts are mode 644 and `sudo -E` is rejected ("preserving the entire environment is not
supported"), so env vars are passed *after* `sudo`, i.e. `sudo SAQEF_...=... bash run_*.sh all`.
Result dirs created under sudo are owned by root; `chown -R imran:imran results/*baremetal*`.

### 30.4 Session troubleshooting log (issue → symptom → root cause → fix → evidence)

1. **Old bare-metal attempts were protocol-invalid (carried in from prior session).** Symptom:
   runs saturated the 8-core box (host_sat 90–95%), 2.7–3.5 s windows, `host_plausible` broke.
   Root cause: c=20 = 2.5× cpu_count and TOTAL=3000 gave ~2.7 s windows (edge-alignment artifacts).
   Fix: c=4 < 8, TOTAL=10000 → ~17–19 s windows, host_sat 74–77%. Evidence: preserved in
   `results/{fn,openfaas}_cpubound_baremetal_sat_invalid` — do not cite.
2. **RAPL validation stuck ~45%.** Symptom: `rapl_validation_err_pct` ≈ 44–46% both platforms.
   Root cause: hardcoded `idle_w=30` was a 7× overestimate of this box's real 4.3 W idle package
   power. Fix: measure idle (60 s RAPL, stack up) → `SAQEF_IDLE_W=4.3` passthrough in both runner
   scripts (knob only). Evidence: err drops to 4.2–5.5% (Fn) / 4.2–8.2% (OF) steady-state.
3. **RAPL transient in run_1/2 on BOTH platforms.** Symptom: OF run_1/2 err 34.55/25.94, Fn run_1–3
   36.07/19.10/18.66 before settling to single digits. Root cause: ~2 runs of thermal/frequency
   settling after the stack boots — a property of the box, present identically on both platforms.
   Fix: none needed; document. Evidence: run_4/5 both platforms 4.2–5.5%. **Do not average across
   the transient; cite steady-state runs.**
4. **Fn run tainted by leftover OpenFaaS `hello` replicas.** Symptom: Fn rerun showed median 11.68
   (share inflated ~1.2 pp) despite all gates green. Root cause: `docker stack rm openfaas` removes
   the 6 stack services but NOT the `hello` function service (deployed outside the stack); its 16
   idle replicas matched the `--fn-images hello` allowlist and folded into `fn_cpu`. They were idle
   so contributed ~0 busy CPU (gates still passed, unclassified 0.0) but the platform was not
   isolated. Fix: `docker service rm hello` and re-run; verify inventory shows only
   `fnserver` + hello:0.0.7. Evidence: clean set median 10.46 (below). The discarded run's figures
   (rapl 0.9–6.1%, QoS 6.7/9.4 @ 577 rps, share 11.68) must NOT be cited.
5. **Fn run-order drift (CV 8.08%).** Symptom: runs 4–5 share ~11.7 vs runs 1–3 ~9.8–10.5. Root
   cause (hypothesis): container GC/freeze churn accumulating in fnserver across a session.
   Fix: none; documented and consistent with the 2-run settling seen everywhere on this box.
   Direction of the verdict (Fn > OF, gate fails) is unaffected.
6. **OpenFaaS c=8 run aborted: "no 'hello' swarm service".** Symptom: `run_openfaas.sh all` redeployed
   the stack but the `scale` step failed because the `hello` service had been removed for Fn
   isolation. Fix: re-deploy the function OUTSIDE the stack with faas-cli from a minimal manifest
   (`/tmp/of-hello/hello.yml`, image `hello:latest`, read/write timeout 60 s), then re-run `all`
   (which re-scales to 16). Evidence: second c=8 run gates green; run_1 has unclassified 0.01
   CPU-s (the transient extra containers during deploy) — immaterial at 2 decimal places.
7. **sudo/env/ownership gotchas.** (a) scripts are mode 644 and `sudo -E` is refused → pass env
   after sudo; (b) result dirs created by root → `chown` to the user; (c) single-node swarm on a
   multi-homed host fails to auto-pick an advertise addr ("could not choose an IP address to
   advertise") → `run_openfaas.sh` now does `docker swarm init --advertise-addr 127.0.0.1`.
8. **hello deploy for c=8 (faas-cli).** The hello image `hello:latest` is already built by the Fn
   allowlist; `faas-cli deploy -f /tmp/of-hello/hello.yml --gateway http://127.0.0.1:8080` recreates
   the swarm service so OpenFaaS can scale it. Timeout must be ≥ the load window (else watchdog
   recycles containers mid-run and the fn CPU budget is silently short).

### 30.5 Environment snapshot (this box)

8-core x86_64 Ubuntu (16 GB), docker + swarm, RAPL user-readable via setup_baremetal.sh
(`intel-rapl:0`, idle package 4.3 W). Fn: `fnproject/fnserver` 0.3.x containerized, hello:0.0.7
function, 5 ms busy-spin. OpenFaaS 0.8.3 gateway (no auth), 6-container control plane (gateway,
queue-worker, faas-netesd, nats, prometheus, alertmanager), of-watchdog, hello function scaled
statically to 16 replicas (2 × cpu_count). Load: hey `-o csv`, TOTAL=10000, warmup 20. Sampler:
cgroup v2 cumulative counters, `--delta-check`. Harness version: v9.11 + `SAQEF_IDLE_W` knob.

### 30.6 Verdict and what the paper must now say

- The a-priori 5 pp gate **passes on the 2-vCPU codespace (8.8 pp) and FAILS on the 8-core box
  (2.8 pp)** — with the same ranking, the same SLO, and identical load. The gate is a
  **per-machine-pair property**, and the machine-dependence (share ≈ inversely proportional to the
  cores available to the function; both platforms' shares ~2.3× higher on 2 vCPU) is itself a
  contribution: control-plane overhead as a fraction of marginal work scales with function-side
  CPU slack, and Fn is structurally less efficient than OpenFaaS in every regime tested.
- Energy is now RAPL-validated (4–8% steady-state) on bare metal; the codespace numbers remain the
  saturated-regime dataset under their existing caveats. QoS is citable for the first time (Fn p50
  6.5 / p99 8.9 ms @ 597 rps; OF p50 7.2 / p99 12.1 ms @ 532 rps; SLO 1.0, host_sat <85%).

## 31. CPU-count effect CONFIRMED — controlled same-instrument 2-core experiment (2026-08-06)

Closes the expert-review catch on §30 (the 8.8-pp codespace gap vs 2.8-pp bare-metal gap was
"observed across mismatched instruments, not proven"). This section records the controlled
experiment that resolves it and the bug it surfaced. Bare metal only; codespace appears solely as
the historical "regime differs" context row.

**Design.** THIS 8-core box was restricted to 2 logical cores *without* a kernel reboot:
`pin_cpuset.sh` (repo script, formalized this day) loops `docker update --cpuset-cpus` over every
running container every 0.5 s for the life of the benchmark. A ONE-SHOT pin is not enough — Fn's
function containers are ephemeral (new ones appear under load; OpenFaaS's swarm replicas are
static), and any container born after a one-shot pin runs unpinned, silently breaking the
restriction mid-run. `pin_cpuset.sh` pins by "every running container", deliberately NOT by image
tag: Fn's function image tag changed between the two sessions (0.0.10→0.0.11 on rebuild) and a
tag-based filter would have silently missed it. The harness + hey were also wrapped in
`taskset -c 0,1`. Physical-core selection: `lscpu -e` CORE column (0,1 = two physical cores, not
two hyperthreads of one).

**The numerator/ceiling bug caught mid-experiment (a gate working as intended).** First attempt set
only `--cpu-count-override=2` (the host_sat ceiling denominator). Result: host_sat 111–121%
(impossible >105%), correctly tripped `host_plausible=false` because `host_cpu_ticks()` was still
summing the whole-machine `/proc/stat` aggregate, including background activity (dockerd, swarm
kworkers, the harness's own shell) on the 6 un-pinned cores. Fix: new `--host-cpu-list`/`SAQEF_HOST_CPU_LIST`
knob scopes the busy-tick numerator to the pinned cores' own `cpuN` lines, so numerator and
denominator agree. Both knobs are now permanent in `saqef_harness.py` + both runner scripts. The
invalid attempt is preserved at `results/openfaas_cpubound_2core_hostmetric_invalid` for the audit
trail (its `cp_dynamic_share_pct` ~6.9 was actually fine — only host metrics were wrong — but its
gates table must not be cited).

**Protocol.** Per platform, REPEAT=5, TOTAL=10000, c=4 (> 2 pinned cores → expected saturation),
`SAQEF_IDLE_W=4.3`, `SAQEF_CPU_COUNT_OVERRIDE=2`, `SAQEF_HOST_CPU_LIST=0,1`, OpenFaaS scaled to
4 replicas (2×N), OpenFaaS run FIRST with full teardown (`docker stack rm openfaas` + `docker
service rm hello`) before Fn. Full independent reproduction session 2 same day: teardown,
redeploy, re-pin from scratch.

**Results (all runs: host_plausible=true, coverage 100%, delta% ~0, delta-check 6/6, unclass 0.0):**

Fn session 1 shares 13.34 / 13.40 / 14.07 / 13.91 / 14.06 → **median 13.91** (CV 2.6%, CI
13.34–14.07). OpenFaaS session 1: 6.73 / 6.95 / 6.82 / 6.28 / 6.96 → **median 6.82** (CV 4.1%).
Gap **7.09 pp**.

Fn session 2: 13.69 / 14.08 / 14.38 / 14.28 / 14.07 → **median 14.08** (CV 1.9%). OpenFaaS
session 2: 6.90 / 7.17 / 7.20 / 7.11 / 7.27 → **median 7.17** (CV 2.0%). Gap **6.91 pp**.

Across the two independent sessions: Fn 13.91/14.08 (median 14.00), OpenFaaS 6.82/7.17 (median
7.00), **gap 7.09/6.91 pp across sessions** — reproduced to within 0.2 pp, both sessions at full
REPEAT=5 with per-run gates. This is the
paper's 2-core row. `host_sat` 98.3–98.7% everywhere → `host_saturated=true` (c=4 > 2 pinned
cores) → latency/QoS from this pair is NOT citable, same discipline as the c=8 sensitivity rows.

**Reading.** The gap jumps from 2.79/2.85 pp (4/8 cores, same box) to ~7.0 pp at 2 pinned cores —
close to the 8.8-pp codespace value, on a clean, same-instrument, same-protocol run where ONLY
core count changed. **Core count drives the gap magnitude: earned and reproduced.**

**Mechanism is asymmetric — NOT the earlier (retracted) "both platforms scale ~2.3×" reading of
the flawed 2-vCPU data.** Controlled result: Fn's share rose +33–34% under core scarcity (10.46 →
13.91/14.00) while OpenFaaS was flat-to-lower (−9 to −11%; 7.67 → 6.82/7.17). Core scarcity
inflates Fn's control-plane overhead specifically. **[CANDID, observed-not-explained]** leading
hypothesis: fnserver scheduling contention under thread starvation vs of-watchdog's per-replica
isolation model — a follow-up micro-benchmark (e.g. perf stat context-switch/migration counts on
fnserver at 2 vs 8 cores) is required before any causal claim.

**Energy/carbon NOT citable from the pinned runs.** `rapl_validation_err_pct` ran 43–60% on both
platforms at 2 pinned cores (transient runs 1–2 ~48–60%, steady ~44–45%) — the same magnitude the
box showed under the OLD wrong `idle_w=30`, despite using the correct `SAQEF_IDLE_W=4.3`. Likely
cause: the 4.3 W idle baseline was calibrated with all 8 cores idle; under partial-core pinning the
2 active cores' turbo/frequency behavior differs from the flat `busy_core_w` model and/or the 6
un-pinned-but-present idle cores' package contribution no longer matches the whole-box baseline.
`cp_dynamic_share_pct` is a pure cgroup CPU-time ratio (no energy model) and is unaffected — the
ONLY citable number from this experiment. Closing the energy question would require re-deriving
idle-w for the pinned configuration and confirming per-core frequency parity (scaling_cur_freq /
turbostat) between the pinned and full-core runs — recorded here as open, not run.

### 31.7 External expert review (2026-08-06) — stored record + disposition

An independent reviewer audited the 2-core experiment. Full review (verbatim) was delivered in the
session; this is the substantive record and the disposition agreed here.

**What the reviewer credited (earned, publishable):**
- The core-count effect is real and properly earned: same-instrument, controlled, gate-passing
  confirmation (2.79 pp → 7.09 pp when pinning THIS box to 2 cores), reproduced in a full
  independent second session. "The strongest result in the whole thread."
- The instrument discipline — catching the numerator/ceiling pinning bug via the `host_plausible`
  gate — is itself a research contribution, and the machine-dependence of the widely-used 5 pp
  threshold is a publishable methodological point on its own.

**Two technical debts raised, and their disposition:**

1. **RAPL 43–60% under pinning = possible confound beyond energy.** The reviewer's mechanism: under
   cpuset pinning, fewer active cores → more turbo headroom per core → cores 0–1 may boost higher
   than in the c=4/c=8 configs → per-core power/frequency differs physically, not just a model bug;
   and if a boosted core does more work per cgroup-CPU-second, the reviewer argued the CPU-share
   number could be confounded too. **Disposition:** the energy point stands (hard no on J/gCO2 —
   same conclusion as §31). The share point is first-order cancelled by construction: numerator and
   denominator both accrue cgroup CPU-time on the same 2 pinned cores, and Linux CPU-time is
   frequency-normalized, so a common boost cancels in the ratio. The genuine residual is a
   second-order per-core-DVFS term (cores 0 vs 1 at different clocks; a systematic
   CP-on-faster-core split would bias the ratio). **Open verification (not run — reviewer: "not the
   paper yet" without it):** read `/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq` (or
   turbostat) during a pinned run vs a c=4 run. Cheap, decisive, recorded as the single gate
   between "defensible with caveat" and "fully closed."

2. **"CV 1.3% across sessions" reads as statistical reproducibility from n=2.** **Disposition: this
   concern is already satisfied and was a misreading of the evidence.** Both sessions ran the FULL
   REPEAT=5 protocol with per-run gate tables (delta 6/6, host_plausible, coverage%) — identical
   rigor to every other citable number in the study; the n=2 counts independent SESSIONS (each
   containing 5 runs), not runs. The "CV across sessions" phrasing has been removed everywhere
   (AGENTS.md, paper §5.5) in favor of "session gaps 7.09 and 6.91 pp, reproduced to within
   0.2 pp."

**Third item — mechanism still open (agreed).** The asymmetric sensitivity (Fn +33–34%, OpenFaaS
flat-to-lower) is an observation, not an explanation, and stays labeled as such. Concrete
investigation recorded for future work: `perf stat -e context-switches,migrations` on the fnserver
process (or `/proc/<pid>/status` voluntary_ctxt_switches) at 2 vs 8 cores; plus process/thread-model
comparison vs OpenFaaS's gateway+queue-worker+of-watchdog path.

**Disposition summary:** results are publishable as the core-count effect with the frequency-parity
verification explicitly open (and energy-not-citable flagged); the mechanism is honest future work.

### 31.8 Frequency-parity verification (2026-08-06) — expert-review item (b) CLOSED

Closes the one open verification from §31.7 (per-core CPU frequency under pinning vs c=4).
Method: a minimal REPEAT=1 Fn bench (TOTAL=4000, c=4) run twice — once cpuset-pinned to 2 cores
(pin_cpuset.sh + taskset + both override knobs, identical to the published sessions) and once
unpinned at c=4 — while sampling `/sys/devices/system/cpu/cpu{0..7}/cpufreq/scaling_cur_freq` at
10 Hz. Raw samples committed at `results/freqcheck_evidence/freq_{pinned,c4}.csv`.

Median frequency of the loaded cores over the load window:

| run | loaded cores | median | p90 | max |
|---|---|---|---|---|
| pinned 2-core | 0, 1 | 3.60 / 3.60 GHz | 3.90 / 3.81 GHz | 4.01 / 4.00 GHz |
| c=4 unpinned | 0–3 | 3.30 / 3.30 / 3.30 / 3.30 GHz | 3.60 / 3.59 / 3.50 / 3.50 GHz | 4.00 / 4.00 / 4.00 / 4.00 GHz |

(The box idles at ~4.0 GHz and its 4.4 GHz turbo ceiling was not reached in either regime.)

**Verdict.**
1. **No per-core DVFS asymmetry within the pinned run.** Cores 0 and 1 sat at identical
   frequencies (3.60/3.60 GHz median). fnserver and the function containers both run on those two
   cores, so the reviewer's "control plane on a faster core" channel cannot exist — the
   second-order term from §31.7 is empirically absent.
2. **Frequency DOES differ between regimes — the reviewer's turbo-headroom mechanism is real on
   this box.** Pinned loaded cores run ~+9% higher (3.60 vs 3.30 GHz) and the un-pinned idle cores
   downclock toward 400 MHz. This is now the measured cause of the 43–60% `rapl_validation_err_pct`
   (the flat `busy_core_w` model and the whole-box `idle_w=4.3` baseline both fail under that
   profile) — the energy-not-citable decision is evidence-backed, not hypothetical.
3. **It does NOT confound the share.** Linux cgroup CPU-time accrues via sched_clock/rq_clock on
   the invariant TSC, i.e. it measures wall-time-on-core, not cycles; a core at 3.6 vs 3.3 GHz
   accrues identical CPU-time per wall-second. `cp_dynamic_share_pct` is therefore a ratio of
   core-seconds — frequency-invariant by construction — and with cores 0/1 identical there is no
   asymmetric channel either. Both frequency paths to the share are closed.
4. **Instrument corroboration.** The REPEAT=1 runs reproduced the established shares (pinned 14.21
   within the session-1/2 range 13.34–14.38; c=4 9.56 within/near the 8-core range 9.80–11.74),
   confirming identical behavior to the published sessions.

**Final status.** The 2-core row stands as the final, correct result: Fn 14.00 / OpenFaaS 7.00,
gap +7.0 pp. Energy/carbon from pinned runs remains NOT citable, now with a measured cause.
Mechanism (§31.7(c)) remains observed-not-explained future work.

### 31.9 Structural isolation guard + drift analysis (2026-08-06) — allowlist review item CLOSED

**Latest external review (after §31.8).** Two parts, both dispositioned here:
(a) **Frequency-parity pushback — RETRACTED BY THE REVIEWER.** On the invariant-TSC argument
(§31.8 verdict 3) the reviewer withdrew the validity-gap concern: "your developer is right, and I
was wrong to press on it as a validity gap." It is downgraded to an optional cheap mechanism lead
only (whether Fn's share would move if fnserver ran on a faster core than the functions) and is
recorded as future work, not a gate.
(b) **Allowlist/isolation concern — ACCEPTED, and fixed here.** The reviewer confirmed at code
level that the allowlist is a real structural gap: `sample_totals()` (saqef_harness.py, lines
721–725) folds a container matching the fn substring (e.g. `hello`) into `fn_cpu` when
`fn_allow_active`; the `unclassified_cpu_s` bucket only catches containers matching NEITHER cp
NOR fn, so a wrong-platform-but-name-matching stray is invisible. The reviewer also flagged that
`pin_cpuset.sh` pins "every running container" with no filtering. We accepted the cheap fixes
(config changes, not measurement-methodology changes). This section records the fix + the drift
analysis.

**Code-level confirmation of the gap.** Both platforms' function images are named `hello`
(OpenFaaS's is deployed as a swarm *service* `hello`, deliberately outside the stack). The
`--fn-images hello` allowlist substring match therefore cannot distinguish Fn's ULID-named
function containers from OpenFaaS's swarm replicas, and the `unclassified_cpu_s` safety net only
catches containers matching NEITHER cp NOR fn — a wrong-platform-but-name-matching stray is
silently folded into `fn_cpu` with all gates still green. This is exactly the mechanism of the
discarded tainted Fn rerun in §30 (share 11.68 vs clean 10.46).

**Fix implemented in `saqef_harness.py`:** new `assert_platform_isolation(platform)` called at the
top of `run_once()` (the bench + idle-probe entry point) — fail loud, exit with an actionable
message, before any sampling. Fn sessions assert **zero** swarm services (`docker service ls`;
Fn never uses swarm, so any service is contamination; message names the likely `hello` offender
and the exact removal command); OpenFaaS sessions assert **no `fnserver` container** (inverse
direction). On docker-command failure (e.g. swarm not enabled) the guard is lenient — no services
can exist without swarm, so returncode!=0 is treated as clean. Both failure paths verified by
injection test; clean passes verified on the live box. This is a config/precondition change only
— no measurement-path or methodology change, and it does not invalidate any published run (every
citable run already satisfied the stronger form of this discipline). `pin_cpuset.sh` (the 
core-restricted variant) remains warning-free by design: it pins whatever is running, so the
isolation guard above is what protects pinned runs from a stray wrong-platform container.

**Drift analysis (expert's accumulation hypothesis) — REJECTED, and boundedness established from
existing data (no rerun).** Expert suggested the Fn run-order drift (CV ~8.4%, runs 4–5 climbing
to ~11.7) might be container accumulation (`docker ps -a` growth across runs 1→5). Existing
`results/fn_cpubound_baremetal` data show the opposite on both candidate mechanisms:
- **Container accumulation — rejected:** live container counts across runs 1–5 were **11, 11, 9,
  9, 9** — flat-to-shrinking.
- **In-process memory (RSS) accumulation — rejected:** fnserver `mem_mb` from the per-run
  `samples.csv` (docker-stats trace) is a run-1 warm-up spike (mean 62.9 MB, max 126.5 MB) then
  **flat 34.0 / 33.8 / 33.3 / 33.1 MB** across runs 2–5. A goroutine/FD/buffer/connection leak
  with a +24% CPU rise would show in RSS; it does not.
- **Bounded, not unbounded:** fnserver mean CPU% rises 28.9 → 30.2 → 32.6 → 33.9 then **down to
  32.7** at run_5 (cp_cpu_s 6.07 → 6.48 → 6.56 → 7.57 → 7.54). The climb is a settle-then-plateau
  warm-up/settling transient, not a monotonic session-length scaling term — so a group replicating
  at REPEAT=10 or longer sessions would land on the same plateau, and the external-validity concern
  (systematically higher share from longer sessions) is answered negatively from data already
  committed.
- **Mechanism (threads/FD counts) not measured:** `/proc/<pid>/status Threads` + `ls /proc/<pid>/fd`
  are not recoverable retroactively (fnserver is gone) and would need a ~15-min diagnostic session;
  deferred per the no-more-iterations directive, and flagged open rather than closed. The drift does
  not move the reported median and is honestly presented via the reported CV/spread; a
  micro-benchmark of fnserver's scheduler behavior remains future work per §31.7(c).

## 32. External review #5 close-out — quiet gate, contamination A/B, regression re-anchor, Knative idle-w N≥3 (2026-08-07 → 08-09)

This section records the reviewer-#5-driven measurement work that closes the two "self-identified
loose ends" (finding #13's single-sample idle-w fragility; the regression reference anchoring) and
bakes the quiet-box assertion into the harness itself. The full narrative, per-bug detail, and the
box-task sequencing live in `AGENTS.md` (Current state 2026-08-09 + the (a)/(b)/(c) blocks); here
are the numbers and the measurement provenance, traced to result dirs.

### 32.1 Ambient-load quiet gate + contamination A/B (2026-08-08)

Reviewer #5 confirmed the runbook's own documentation: opencode at ~276% CPU / 1.1 GB RSS
(2026-08-07) contaminated `host_saturation_pct` and drifted Fn's `cp_dynamic_share_pct` ~0.3–1 pp
via cache pollution / context switching / DVFS — the "quiet box" was a manual `uptime`/`ps`
assertion, not a gate. Two changes made it measured, not hoped:

1. **`ambient_load_check()`** in the harness samples whole-host busy CPU over a 20 s window before
   every bench and refuses to start above `--max-ambient-cpu-pct` (default 15%); the reading + a
   top-CPU `ps` snapshot land in `summary.json` → `ambient`. Live-verified: with this session's
   opencode + dockerd + k3s + chrome it read 19.5% and refused. `--no-quiet-gate` exists for
   exploratory / contamination-AB runs. Idle-probe calibration is exempt (the platform stack itself
   is the subject).
2. **`tools/contamination_ab.py`** runs the same bench clean (gate active) vs an emulated agent
   signature (gate disabled) and reports the delta on `cp_dynamic_share_pct` / `host_saturation_pct`
   / p50/p99 / throughput. The dirty profile is **matched to the documented 2026-08-07 incident**
   (not a generic light stressor — reviewer #5 follow-up): `--cores 3` spinners pinned to distinct
   cores (≈300% ≈ the real 2.76-core load) + `--mem-gb 1.1` bytearray (the real 1.1 GB RSS); the
   dirty leg prints the achieved aggregate host busy% (target = cores/cpu_count). N≥5 by default
   (`--repeat 5`) — the same discipline as every citable number; a single A/B pair is not a bound.

**Results (N=5/leg; `results/{fn,openfaas}_contamination_ab/{clean,dirty}/summary.json` +
`contamination_ab.json`):**

| leg | clean share | dirty share | Δ (pp) | host_sat clean/dirty | p50→p99 clean→dirty | rps clean→dirty |
|---|---|---|---|---|---|---|
| Fn | **10.0** (CV 2.8%) | **12.16** (CV 4.2%) | **+2.16** | 69.7 → 93.6 | 6.4/9.3 → 7.2/14.8 ms | 597.8 → 503.0 |
| OpenFaaS | **6.9** (CV 1.6%) | **7.24** (CV 0.6%) | **+0.34** | 71.6 → 92.7 | 6.8/12.1 → 7.9/17.5 ms | 549.2 → 450.1 |

- Fn's +2.2 pp reproduced across two independent sessions to within ~0.05 pp (first run: 9.91 →
  12.11). The earlier inferred ~0.3–1 pp contamination estimate (§7) is **superseded by this direct
  measurement**.
- Mechanism: the share is contaminated only where the control plane is a **central orchestrator on
  the request path** (fnserver, +2.2 pp); OpenFaaS's per-replica of-watchdog model is nearly immune
  (+0.34 pp) — same direction as the core-scarcity result (Fn +33% at 2 cores, OF flat-to-lower).
- QoS is the larger contamination effect on both platforms (p99 +5.5/+5.4 ms, throughput −16/−18%);
  both dirty legs flagged `host_saturated=true` → dirty-leg latency percentiles not citable; the
  clean-vs-dirty **contrast** is the citable output.
- RAPL not citable this session (clean rapl_err 35–54%; dirty 22–28%) — Fn/OF energy/carbon flags
  stay quiet here. Share unaffected (cgroup CPU-time ratio).
- Bug found + fixed: `contamination_ab.py` wrote benches via `cwd=REPO` but read them back relative
  to the caller's cwd → `FileNotFoundError` when run from `tools/`; outdir now resolved absolute
  against `REPO`. Verdicts re-verified by reading the existing summary.jsons directly.

### 32.2 Regression re-anchor + verify (2026-08-09) — refs CONFIRMED, not corrected

`saqef regression`'s references were 11.60/7.67 (2026-08-06-era). The 2026-08-08 runs read
Fn 10.65 / OF 7.14 (FAIL, box drift). Per the runbook §2/§3 protocol, the references were
re-anchored with a **same-day old-runner A/B** under the now-self-certifying quiet gate
(`tools/reanchor_and_kn_idle.sh`, sections (a)/(b)), then the refactored path was re-verified:

1. **Old-runner A/B (quiet box, ambient 5.6–5.9%):** OpenFaaS **7.61**, Fn **11.49**
   (`results/{of,fn}_cpubound_crosscheck_2026-08-09`). Both within ~0.1 pp of the old refs
   (7.67/11.60) → the 2026-08-08 regression FAILs (7.14/10.65) were **that day's box drift, not a
   refactor break**. Refs applied to `metrics/cpubound.json` (backup `.bak-2026-08-09`).
2. **Refactored-path verify (`saqef regression`, same session, ambient 9.9% / 11.7%):**
   `results/regression/{openfaas,fn}` — Fn median **11.27** (runs 10.56/10.89/11.31/11.36/11.27;
   dev vs ref 11.49 = **+0.22 pp**), OpenFaaS median **7.40** (runs 7.08/7.14/7.50/7.44/7.40; dev
   vs ref 7.61 = **+0.21 pp**) → **PASS both** (tolerance 0.50 pp). Full gate tables green: delta
   ~0, CPmapped 6/6 (OF) + 1/1 (Fn), coverage 100%, host_plausible true, host_sat 70–74%, SLO 1.0.
   QoS: Fn p50 6.5 / p99 9.9 ms @ 581 rps; OF p50 7.0 / p99 13.1 ms @ 525 rps.
3. **Script bugs found + fixed during the re-anchor** (none touched a banked measurement):
   - `reanchor_and_kn_idle.sh` ran the Fn old-runner leg (leaves `fnserver` up) then immediately
     invoked `saqef regression`, which starts with OpenFaaS whose isolation guard correctly refuses
     while `fnserver` is present → the script died at verify every full run. Fixed: Fn teardown
     before the regression verify + `--skip-legs` mode (refs already applied). Same pattern
     recurred for Knative (leftover `hello` ksvc, 16 replicas) — fixed with `saqef teardown
     --platform knative` alongside the Fn teardown.
   - `rapl_w_series()` (section (c) only) did a raw `(e1-e0)/60` RAPL delta with no wraparound
     guard, bypassing `rapl_correct_wrap()` built for exactly this hazard (finding #5) — in the
     most wrap-sensitive regime (single-digit watts). Fixed: imports `saqef_harness` directly,
     discards/retries any wrapped/uncertain read.
   - `saqef_harness._class_matches()` still substring-matched Fn's `fn_images=("hello",)` against
     Knative's `kn-hello` (bug #3 from the 2026-08-08 audit, "masked by luck"): now exact
     repo-basename matching (`_image_repo_basename()`: strip registry/path + tag, require equality).
   - `check_isolation()`'s remediation advice was hardcoded to `docker rm -f fnserver` /
     `docker service rm hello` regardless of which forbidden container/service matched → now a
     substring-keyed lookup (`platforms/base.py` `_advice_for_container()`/`_advice_for_service()`).
   - Section (b) never asserted the k3s/Knative substrate was up before the Fn/OF legs → explicit
     `docker ps | grep k8s_` precondition added. The two already-banked crosscheck runs were
     verified substrate-up and clean post-hoc.

### 32.3 Knative idle-w N≥3 — finding #13 CLOSED (2026-08-09)

Finding #13 (2026-08-08) established that Knative's idle-w calibration was a *single* 60 s RAPL
read with no repeats, producing 11.14 / 7.01 / 4.91 W across three sessions — a >2× span. The
2026-08-09 N=5 protocol (`tools/reanchor_and_kn_idle.sh` (c), 60 s reads, wraparound-guarded
`rapl_w_series()`):

- **condition A** (bare k3s + knative-serving + kourier, no `hello`): **median 3.871 W**
  (3.692–3.946, spread 0.25 W, n=5)
- **condition B** (`hello` @ 16 replicas, exact bench-time stack): **median 4.561 W**
  (4.309–4.719, spread 0.41 W, n=5)
- **premium B − A = 0.690 W** — a real but small always-on premium over the ~4.2–4.3 W
  bare/Fn/OpenFaaS baseline, matching the back-of-envelope decomposition (~4.2 W substrate + ~0.7 W
  warm replicas + proxies) to within noise. **Verdict: finding #13 CLOSED.** Use the condition-B
  median (≈4.56 W) as `--idle-w` for a Knative bench; the premium (≈0.69 W) is now the citable
  "Knative always-on idle premium" for §5.6 / design-principle C3.

**Corroborating observation (idle-term dominance, direction only):** the 2026-08-09 regression
runs (TOTAL=10000, ~17–19 s wall) show mostly clean RAPL fits — Fn 16.6/12.1/0.6/1.5/0.65, OF
18.9/14.8/2.3/0.6/5.8% (only run_1 crosses 15%) — vs every run failing at 20–55% on the earlier
short contamination A/B legs (TOTAL=3000, ~5 s wall) with the same `idle_w=4.3`. Consistent with
"light load → stale idle-w dominates the fit error"; a one-line note in the paper, not proof
(`idle_w` was not recalibrated in either case).

### 32.4 Reviewer #5 disposition — what this closes and what remains

- **Closed with data:** the median_summary list-union gap (3 new tests), the RAPL double-wrap
  ambiguity (`rapl_correct_wrap()` + `rapl_wrap` summary field, 3 new tests), the
  convention-normalized comparison (§5.6 table; Kn's queue-proxy reclassified as CP → 25.7%),
  n=1 wording (Contribution #3 / T5V #8 — "one physical host via cpuset restriction, second
  machine is future work"), the Knative idle premium (this section), and the regression reference
  anchoring (this section). 47/47 tests pass (`python3 -m unittest tests.test_saqef_cli`).
- **Still open (before submission):** (1) **cold review pass #6** on the four newest code paths
  (Kn adapter, OW adapter, narrowed isolation policy, `median_summary` rewrite) — recommended and
  must be a genuinely fresh reviewer (fresh model session with no prior context); every prior pass
  found something real. **Use `EXPERT_REVIEW_PROMPT_COLD.md`** (leaks zero prior findings);
  `EXPERT_REVIEW_PROMPT.md` is now the authors' internal known-fix checklist. (2) The **second physical machine** decision — preferred by the reviewer
  if the timeline allows, since machine-pair dependence is the paper's central contribution;
  honestly-scoped n=1 is the agreed fallback. (3) OpenWhisk's **structural** energy-model
  mismatch (31–50% RAPL err, stable across all sessions — the standalone JVM does not fit a
  linear busy-core model) is named in the paper's Future Work as a separate, larger open item, not
  the same class as the idle-w calibration gap; it was never in `saqef regression`'s scope (that
   gate proves the refactored CLI reproduces old-runner values for fn/openfaas, which have tight
   references).

## 33. lock4 — the matched single-day four-platform session (2026-08-14)

The publication-lock pair (§32 context; lock2 + lock3, 2026-08-13/14) was clean but still
stitched OpenWhisk across to a second day (its lock2 leg was corrupted by the loadgen-timeout
bug — 1,993/10,000 — fixed in `b9d0204`; lock3 re-ran OW only). **lock4 replaces it as the
paper's citable four-platform baseline**: all four platforms back-to-back the SAME day
(2026-08-14) via `tools/run_lock_session.sh` (dry-run first, then the real session), under the
self-certifying quiet gate, count-complete, zero loadgen fallbacks, all gates green. This closes
the cold-review-pass-#6 "asymmetric quiet-gate certification" criticism at the root — every leg
now carries its own `ambient` field and fresh idle-w.

**Data dirs.** `results/{openfaas,fn,knative,openwhisk}_cpubound_lock_lock4/` (5 runs each,
`runs.json` + `summary.json` + per-run `samples.csv`), `results/idle_w_calibration/lock_lock4/`
(N=5 raw RAPL reads per stack state, medians), `results/lock_session_lock4/lock_summary.json`
(session-level gate report). Driver smoke-test artifacts (`results/lock_session_smoketest/`,
`*_cpubound_lock_smoketest_quick/`) were REPEAT<5 `_quick` never-publish working artifacts; they
were removed in the 2026-08-15 scratch cleanup (nothing referenced them).

**Headline medians (`cp_dynamic_share_pct`):** OpenFaaS **7.58** (CI 6.78–7.66, CV 5.41%) < Fn
**11.29** (CI 9.82–11.50, CV 6.39%) ≈ Knative **11.47** (CI 10.94–12.31, CV 4.39%) < OpenWhisk
**81.78** (CI 80.65–84.45, CV 1.71%). Gap Fn−OF = **+3.71 pp**. Reproduces lock2/lock3
(7.29/11.16/11.82/81.88) within ≤0.35 pp per platform; ordering OF < Fn≈Kn << OW holds; the
Fn–Kn cluster tie flipped sign (lock2 Kn +0.66 pp above Fn; lock4 Fn +0.18 pp above Kn) — inside
overlapping CIs, consistent with reporting a cluster. Per-inv CP cost 0.54 / 0.72 / 0.88 /
25.66 ms; fn cost 6.62 / 5.66 / 6.82 / 5.72 ms.

**Idle-w calibration (N=5 per stack state):** bare 4.084 W, OpenFaaS@16 4.235 W, Fn 4.249 W,
Knative@16 5.739 W, OpenWhisk 4.882 W. The Knative premium (B−A) re-read **~1.66 W** vs 0.690 W
on 2026-08-09 — same direction, larger magnitude, both N=5 → the paper now cites "~0.7–1.7 W"
and each session models energy with its own fresh per-leg idle-w (energy/carbon used 4.235 /
4.249 / 5.739 / 4.882 W; §5.6, §11).

**Gates.** All four legs: runs==5, delta ~0, CPmapped 6/6 (OF) / 1/1 (Fn) / 18/18 (Kn) / 1/1
(OW), coverage 100%, host_plausible=physical_plausible=True, `rapl_wrap` none, ambient present
and under threshold (5.9–7.4% of a 15% ceiling), host_sat 70.8 / 71.9 / 74.1 / 60.7% (< 85% →
QoS citable). `saqef gates` GREEN on all legs.

**QoS (median).** OpenFaaS p50 7.0 / p99 13.1 ms @ 532.8 rps; Fn p50 6.5 / p99 9.4 ms @ 590.5
rps; Knative p50 7.6 / p99 11.0 ms @ 505.8 rps; OpenWhisk p50 110.7 / p99 189.2 ms @ 35.0 rps;
SLO compliance 1.0 all four. OW latency remains above the 2026-08-08 quiet-morning reading
(97.4/136.5 @ 40.8 rps) — box-state and run-to-run throughput variance, host_sat disclosed
alongside.

**Energy/carbon.** Fn/OpenFaaS/Knative citable from this session's own steady-state runs 3–5
(RAPL err 0.6–3.4% / 1.0–2.3% / 4.1–8.8%; runs 1–2 warm-up transient ≤32.0/34.6/27.2%).
OpenWhisk NOT citable (RAPL err 27.4–46.8%, median 42.0% — structural JVM/linear-model
mismatch, unchanged across all five sessions; §10 item 10 / §5.6). Energy totals (median):
Fn 295.3 J, OF 331.3 J, Kn 383.0 J, OW 2447.4 J; carbon op_total 0.014 / 0.016 / 0.018 / 0.117 g.

**Robustness figures re-derived from raw data.** Queue-proxy→CP for Knative: per-run integrated
CPU-time from `samples.csv` reproduces the harness's own shares exactly (10.94/11.32/11.68/11.47/
12.31), queue-proxy ~10.2 CPU-s/run vs ~57.8 s fn and ~8.8 s CP → **24.8%** (vs 24.9% in lock2).
Flat-5ms-normalized shares (share = cp_ms/(cp_ms+5)): **9.81 / 12.60 / 15.01 / 83.69%**.

**Regression refs.** lock4 Fn 11.29 (dev 0.20 pp) and OF 7.58 (dev 0.03 pp) both within the
0.5 pp tolerance of the 2026-08-09 references (11.49 / 7.61) → `metrics/cpubound.json` unchanged,
no re-anchor needed.

**Sync applied (2026-08-14, this commit):** `figures/make_figures.py` REGIMES `fourplat` →
 lock4 dirs (figure1 untouched; its PNG byte-identical, PDF metadata-only per the 2026-08-13
 wording fix); figures 2–4 regenerated; `SAQEF_PAPER_DRAFT.md` abstract, §3, §4.1/§4.5, §5.6
 (table, fig captions, convention + function-cost-normalized views, idle-baseline day-state note,
 reproducibility, QoS), §8.1 G6, §10, §11, Appendices A/B re-anchored; `AGENTS.md` current-state
 block rewritten to lock4. `SAQEF_TECHNICAL_REPORT.md` prior sections remain the durable history;
 lock2/lock3 stay retired-but-documented (Appendix evolution note).

## 34. Concurrency sweep + Fn freeze ablation (2026-08-15) — quick-tier trend + diagnostic

Ran via `tools/run_sweep_and_freeze.sh` from a bare shell (quiet gate active). Quick-tier protocol
(REPEAT=3/TOTAL=3000, outdirs `_quick`, gitignored; the durable aggregate record is the committed
`results/lock_session_*/lock_summary.json`).

**Concurrency sweep (c=1/2/8/16, OF/Fn/Kn + OW spot-check c=4/c=8).** `cp_dynamic_share_pct` is flat
within ~1–2 pp across all c on every platform — CP overhead does NOT amortize with concurrency,
consistent with per-invocation CP cost (CP ms/inv OF 0.40–0.49, Fn 0.52–0.83, Kn 0.72–1.09, OW ~30).
Ordering OF < Fn ≈ Kn << OW survives every c. Full table + flags in the paper §5.5 (Table 8a,
Figure 5). All legs `gates_ok=True`; quiet gate 11.8%/8.4% (<15%). Flags: c=8/16 host_sat 88–94%
(share citable, QoS/energy not); c=16 INCOMPLETE-RUN 2992/3000 = benign sampler truncation on ~3 s
runs (hey, no fallback — NOT the OW duration bug); Fn c=16 run_1 17.43 (CV 23.9%, noisiest point);
OF c=2 5.82 all-time low, non-monotonic (don't hang claims on it); RAPL DEGRADED on ~3–4 s runs
(idle-term dominance) → energy/carbon not citable from the sweep.

**Fn freeze ablation.** Hook `FN_FREEZE_IDLE_MSECS` set at container creation (`run_saqef.sh`
`setup_fn()`; the old `docker update --env-add` recipe was wrong). **Knob semantics: fnproject docs —
a NEGATIVE value disables freezing; `0` = "freeze without any delay" (maximum churn), NOT "off".**
The morning leg that ran `=0` was therefore invalid (fnserver log still showed continuous
`docker pause`/`unpause`, share 26.49%) and is NOT cited — only the corrected `=-1` leg is.
Result: baseline (default freeze) **10.85** (10.84–11.04, CV 1.03%, cp_cpu_s 2.05–2.09) vs freeze
disabled `=-1` **9.82** (9.69–9.82, CV 0.77%, cp_cpu_s 1.79–1.82); non-overlapping run ranges,
~1.0 pp, all gates green. Pause/unpause churn is real but modest — a footnote-closer for fnserver
per-request CP variance, never a headline. (The driver's `run_freeze` had no `|| die` and the
script no `set -e`, so a silent failure previously just moved on — both added in this session.)

**Sync applied (2026-08-15, this commit):** `figures/make_figures.py` gained `figure5_
concurrency_invariance` (data source = committed `lock_session_*/lock_summary.json`, NOT the
gitignored `_quick` outdirs; figures 1–4 content unchanged — their PDFs reverted from the working
tree to avoid metadata churn); `SAQEF_PAPER_DRAFT.md` §3 RQ3, §5.5 (Table 8a + Figure 5 +
concurrency-invariance paragraph + freeze-ablation note, quick-tier-labeled), §6 mechanism
synthesis, §10 item 7 (marked DONE), §12 Conclusion claim 2 updated; `AGENTS.md` current-state +
DECIDED-PLAN items updated. The sweep does not touch `metrics/cpubound.json` — regression refs
(11.49/7.61) remain valid (lock4 dev 0.20/0.03 pp).

## 35. I/O-bound workload variant (2026-08-15) — quick-tier trend + methodology note

Ran via `tools/run_io_bound.sh` from a bare shell (quiet gate active, self-certified each leg:
ambient 4.9–10.0% of a 15% ceiling). The driver swaps all four handlers to `time.sleep(0.005)`
(the same 5 ms wall duration as the spin, no busy CPU), rebuilds the OF/Kn images (and clears the
k3s containerd image cache so the next Knative deploy pulls the fresh build), runs one quick-tier
lock session (REPEAT=3/TOTAL=3000/c=4, lock4 idle-w medians via `--skip-idle-calib`), then
git-restores the four handler sources and rebuilds both images from the restored source so the box
returns to the spin-workload state. Durable aggregate: `results/lock_session_iobound/
lock_summary.json` (committed); the `_quick` per-platform outdirs are gitignored.

**Results (medians of 3; lock4 spin baseline in parentheses):**

| platform | share % (lock4→I/O) | CP ms/inv (lock4→I/O) | fn ms/inv (lock4→I/O) |
|---|---|---|---|
| OpenFaaS | 7.58→24.67 | 0.54→0.45 | 6.62→1.33 |
| Fn | 11.29→47.12 | 0.72→0.53 | 5.66→0.59 |
| Knative | 11.47→30.69 | 0.88→0.67 | 6.82→1.50 |
| OpenWhisk | 81.78→96.87 | 25.66→24.89 | 5.72→0.81 |

**Two findings (both quick-tier trend-only in the paper — §5.5 Table 8b + §6 + §12):**
(a) **CP ms/inv is workload-invariant** — the per-invocation control-plane cost does not scale
with the workload change. OpenWhisk's 24.89 vs 25.66 is the tightest of the four (smallest
relative deviation, within the same ~3% box-drift band as the other three — deliberately NOT
described as "byte-identical" per expert pushback); OF/Fn/Kn sit ~17–26% *lower* under I/O,
consistent with the quieter host (host_sat 33–55% vs 69–75% in lock4). (b) **The share ordering
is NOT workload-invariant** — OF < Fn ≈ Kn (7.58/11.29/11.47) becomes OF 24.67 < Kn 30.69 <
Fn 47.12. Mechanism: the §4.1 denominator caveat at its extreme. Fn's fn-side floor is the leanest
(0.59 ms/inv) because its fdk serves the request path directly with no always-on per-replica proxy
in the fn cgroup; OF's of-watchdog and Kn's queue-proxy forward each request on the function side.
**Freeze is NOT the driver** — verified against the committed §34 ablation: its whole effect is
CP-side churn (cp ms/inv 0.68→0.60), fn-side CPU invariant to freeze under spin, and a sleeping
`time.sleep` accrues ~0 CPU whether paused or not.

Third observation: OW p50 improves 110.7→65.6 ms and rps ~35→58 under I/O — its bottleneck is the
standalone control plane, not function execution. QoS citable all legs (host_sat 33–55%, p50
6.3–7.1 ms OF/Fn/Kn ≈ spin — the 5 ms wall-time match is real). **Energy/carbon NOT citable**
(rapl_err 71–77% container platforms, 25–41% OW — idle-term dominance on ~5 s runs). OW run_1
CP transient 145.4 s vs ~72 s steady (median used). The discriminating freeze-off I/O leg
(`FN_FREEZE_IDLE_MSECS=-1 bash tools/run_io_bound.sh --stamp iobound_nofreeze --platforms fn`) was
NOT run — the mechanism predicts it would confirm the proxy-floor explanation, and the paper
wording is evidence-backed either way.

**Sync applied (2026-08-15, committed `493b1ff`):** `SAQEF_PAPER_DRAFT.md` §5.5 retitled "Regime
dependence — core count, concurrency, freeze policy, and workload" + I/O-bound paragraph + Table 8b;
§4.3 notes the variant as the empirical version of the workload-anchoring rationale; §6 ordering
claim scoped to the CPU-bound regime + workload axis added to the mechanism synthesis; §12 claim 2
updated. `tools/emit_verified_results.py` gained §10 (I/O-bound, emitted live from runs.json);
`VERIFIED_RESULTS.md` regenerated. `AGENTS.md` current-state + Remaining-item-1 updated. lock4 fn
ms/inv corrected to the canonical 6.62/5.72 in the I/O tables. Regression refs
(`metrics/cpubound.json`) untouched — the I/O run shares are quick-tier trend-only, not
headline-comparable.
