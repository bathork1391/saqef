# SAQEF troubleshooting & runbook

Everything that bit us during the 2026-08-07 overnight session, the root cause,
and the fix — so the same problem is a five-minute check next time instead of a
night. **Read this before any measurement session.**

## 1. Noisy-neighbor contamination from background processes (incl. this agent)

**Symptom:** `host_saturation_pct` reads much higher than expected for the same
protocol on the same box (e.g. 91.5% on one run vs 83% on the identical run an
hour earlier), and Fn's `cp_dynamic_share_pct` drifts up ~0.3–1 pp between days
with no code change.

**Root cause:** cgroup CPU-time is *wall-time-on-core*, and that is sensitive to
host contention through three second-order channels:
1. **Cache pollution** — a big background process (opencode was at ~276% CPU,
   1.1 GB RSS, 532 min CPU) evicts the platform's working set, so the control
   plane refetches data → more real CPU cycles per request → higher `cp_cpu_s`.
2. **Context switches / scheduling** — same work, more CPU-time on-core.
3. **DVFS** — powersave governor on a busier host keeps cores at lower clocks;
   a request that needs N cycles then consumes more CPU-*time*, inflating the
   share even though the cgroup counter is nominally "frequency-invariant".

**Which metrics are affected:**
- `host_saturation_pct`, `host_plausible`, latency/QoS percentiles: **directly
  corrupted** (background CPU is part of the host busy ticks).
- `cp_dynamic_share_pct`: **robust but not bit-exact** — a ratio of the
  platform's own cgroup CPU-times, so a background spinner cannot move it
  wildly (it cannot turn 12 into 25), but the second-order channels can move it
  ~0.5–1 pp.
- Energy/carbon + idle-w calibration: need a quiet box.

**Fix / protocol:**
- **Automated (added 2026-08-09):** the harness now runs an **ambient-load quiet
  gate** before every bench — it samples whole-host busy CPU over a 20 s window
  (`--ambient-window-s`) and **refuses to start** above `--max-ambient-cpu-pct`
  (default 15%, ~1.2 cores on 8 — a 2.8-core agent reads ~35%). The reading and a
  top-CPU `ps` snapshot are written into `summary.json` → `ambient`, so "quiet box"
  is a *measured, self-certifying precondition*, not a manual assertion. Override
  with `--no-quiet-gate` only for exploratory runs and the contamination A/B tool
  (a citable run must never need it). Manual fallback for a bare-shell eyeball:
  `uptime` load < ~0.5 and `ps aux --sort=-%cpu | head` shows nothing > 5% CPU.
- **Measure the contamination bound (not just assume it):**
  `python3 tools/contamination_ab.py --platform fn` runs the same bench with the
  quiet gate active (clean leg) vs an emulated 3-core + 1.1 GB agent signature
  (dirty leg, gate disabled) and reports the measured delta on
  `cp_dynamic_share_pct`, `host_saturation_pct`, p50/p99, throughput → the honest
  "how much does an agent-style load actually move our numbers on THIS box"
  figure for §7. Run it from a bare shell with agents quit; REPEAT=3 default is a
  `_quick` outdir (bump `--repeat 5` if it becomes a citable methodology figure).
- Treat `host_saturation_pct` from any agent-attended session as unreliable.
- When day-over-day Fn drift is observed, run a **same-day A/B** against the old
  runner (`run_saqef.sh all`) before trusting any reference or gate verdict.

## 2. Fn share drifts day-to-day (10.46 → 11.60 → 12.27)

**Symptom:** same protocol, same box, `cp_dynamic_share_pct` rises across days.

**Root cause (verified, not cached values):** `fnserver` per-request CPU cost
measured 0.61 ms (08-05) → 0.75 ms (08-06) → 0.79 ms (08-07) while `fn_cpu_s`
stayed flat at ~56 s every day. Not result-caching (every session deploys a
fresh fnserver) and not saturation from stored results (JSON files are inert).
It is host-state + noisy-neighbor drift (see #1): fnserver does the same work
but the host around it is more contended each day.

**Fix:** the regression gate's fixed 0.5 pp tolerance is tighter than Fn's
day-to-day envelope on this box. The gate still catches refactor-scale breaks
(0%/100% success bugs, argv drift); it cannot adjudicate ~0.5 pp box noise.
Recalibrate the Fn reference with a **same-day old-runner A/B** on a quiet box
(the established 2026-08-06 procedure), never by loosening the tolerance.

## 3. Regression gate FAIL on Fn after the refactor

**Symptom:** `saqef regression` reports Fn dev 0.67 pp > 0.5 pp tolerance.

**Diagnosis:** check the per-run gate table first — if delta% ~0, CPmapped 1/1,
host_plausible true, coverage 100%, and the 5 runs are flat, the run itself is
clean and the deviation is the reference/box, not the refactor. Then run the
old-runner A/B (`SAQEF_OUT=results/fn_cpubound_crosscheck2 ... run_saqef.sh all`):
today old = 12.92 vs refactored = 12.27, same noise envelope as 2026-08-06
(11.60 vs 11.96). A refactor that changes the harness argv cannot produce this
(the tests pin byte-identical argv; the measurement is the same harness as a
subprocess).

**Fix:** recalibrate the Fn reference from the quiet-box old-runner value with
full provenance in `metrics/cpubound.json` → `regression.reference_notes`, then
re-run `saqef regression`. OpenFaaS's reference (7.67) is stable and has never
required this.

## 4. OpenWhisk 60/min throttle (the 429 wall)

**Symptom:** ~40% HTTP 429 "Too many requests" at bench speeds; verify and bench
availability silently corrupt.

**Root cause:** Apache OpenWhisk standalone default is
`limits-actions-invokes-perMinute = 60` per action. The key is enforced by
WhiskConfig from the **`whisk-config.`-prefixed dotted path**
(`whisk-config.limits.actions.invokes.perMinute`); the kebab-case keys in
`standalone.conf` under `whisk.config` are a separate, unread path. `JVM_EXTRA_ARGS`
flows through `/init` into `java`, so raise the limits via system properties
(both spellings, for safety): per-minute 1e9, concurrent 1000, trigger-fire 1e9.

**Fix:** already in `platforms/openwhisk.py` (`OW_JVM_ARGS`). Verify 300/300 →
204 after the fix.

## 5. OpenWhisk standalone's obsolete docker client

**Symptom:** the standalone JVM's embedded 2018 docker client (API 1.38) is
rejected by host dockerd ≥ 29 → invoker can't spawn action containers.

**Root cause:** API version mismatch kills the pull/run path.

**Fix:** `deploy()` shadow-mounts a modern STATIC docker CLI at `/usr/bin/docker`
in the container (`vendor/docker`, fetched from download.docker.com; gitignored).
Stale extract-leftover directories must not satisfy the existence check.

## 6. OpenWhisk is slow → the 60 s duration cap truncates runs

**Symptom (anticipated):** OW does ~65 rps at c=4, so 10000 requests ≈ 154 s/run.
The default `--duration 60` safety cap makes the loadgen subprocess kill-switch
`deadline_s + 120 = 180 s` — margin of only ~26 s over the expected run length.

**Fix:** run OW with `--duration 300` (or larger). Runs stay count-bound
(`-n total`, exactly N requests); `--duration` is only the kill-switch + the
`wall > duration*1.1` warning threshold.

## 7. `docker stack rm openfaas` leaves the `hello` function service

**Symptom:** OpenFaaS's `hello` service (deployed outside the stack) survives
`docker stack rm openfaas`; its replicas fold into a later Fn run's `fn_cpu`
via the shared `hello` image name and silently taint the headline number.

**Root cause:** `hello` is a `docker service create`, not part of the stack.

**Fix:** always `docker service rm hello` before Fn. Enforced automatically by
the data-driven isolation guard (`--forbidden-services *` for Fn/OpenWhisk) at
the top of every bench run.

## 8. `results/verify.json` clobbering

**Symptom:** an OpenWhisk verify overwrote the tracked `results/verify.json`
working artifact.

**Root cause:** `cmd_verify` passed `--out` to `harness_argv`, but the verify
branch never emits `--outdir`, so writes went to the harness's default.

**Fix:** `cmd_verify` now pins `--outdir` explicitly, defaulting to
`results/<platform>_verify`. Cross-platform verifies can no longer collide.

## 9. Replica defaults

- `run_openfaas.sh` default was 4; protocol is 16 static replicas (GIL
  concurrency parity). Fixed → 16; set `SAQEF_REPLICAS` explicitly otherwise.
- The reviewer's "10" was Fn's *dynamic* ephemeral function-container count
  (`fn_replicas: 10,10,10,10,8`), not an OpenFaaS under-replication.

## 10. k3s stuck "activating" forever after a reboot — TLS cert issued with a future notBefore

**Symptom:** `sudo systemctl status k3s` shows `Active: activating (start)` indefinitely (never
reaches `active (running)`); `kubectl`/`k3s kubectl` fails with `x509: certificate has expired or
is not yet valid: current time ... is before <some time a few hours in the future>`;
`platforms/knative.py deploy()` fails at the `k3s get node` precondition check.

**Root cause:** `k3s`/`docker` are systemd services with `Restart=always`, so they restart on every
reboot of this box. k3s's dynamiclistener auto-rotates its short-lived apiserver serving cert
(`serving-kube-apiserver.crt` + `dynamic-cert.json`) on certain restarts. If the reboot's RTC/system
clock is briefly wrong (ahead of real time) before NTP finishes syncing, the newly-issued cert gets
stamped with a `notBefore` in that wrong future window. Once NTP corrects the clock backward, the
apiserver's own TLS handshake to itself rejects the cert as "not yet valid" and the server process
never completes startup — a self-inflicted deadlock. The root CA (`server-ca.crt`, long-lived, only
generated at cluster init) is unaffected; only the short-lived leaf cert is bad. Confirm with:
```bash
sudo openssl x509 -in /var/lib/rancher/k3s/server/tls/serving-kube-apiserver.crt -noout -dates
timedatectl   # confirm System clock synchronized: yes, i.e. current time is now trustworthy
```

**Fix (~15s, no cluster data lost):**
```bash
sudo systemctl stop k3s
sudo mkdir -p /var/lib/rancher/k3s/server/tls/_badcert_backup
sudo mv /var/lib/rancher/k3s/server/tls/{serving-kube-apiserver.crt,serving-kube-apiserver.key,dynamic-cert.json} \
  /var/lib/rancher/k3s/server/tls/_badcert_backup/
sudo systemctl start k3s
sleep 15 && sudo systemctl status k3s --no-pager   # expect: active (running)
```
k3s regenerates the deleted files from the (now-correct) clock on next start.

**Downstream gotcha:** after this fix, if a Knative `hello` ksvc predates the outage, kubelet may
be stuck retrying `KillContainer` on stale pods with `DeadlineExceeded` (dockerd itself was also
mid-restart) — `kubectl get pods` shows nothing but `docker ps` still shows the containers Up.
These are orphaned (API objects already deleted); safe to force-remove directly:
```bash
docker ps -a --format '{{.Names}}' | grep 'hello-[0-9]*-deployment' | xargs -r -n1 -P8 docker rm -f
```
Then `python3 saqef teardown --platform knative && python3 saqef deploy --platform knative` for a
clean redeploy. Note the redeploy lands on a FRESH revision number (`hello-00001-...`, not
`-00002`) since deleting+recreating the ksvc resets Knative's revision counter — this is normal,
not a bug (fixed 2026-08-08: `deploy()` no longer hardcodes the old revision number).

## Quiet-box runbook (final citable numbers)

From a bare bash shell, **with opencode/agent stopped and desktop apps idle**:

**Before step 1 or 2, also confirm no leftover Knative deployment is up**
(`docker ps --format '{{.Names}}' | grep k8s_` should be empty, or run
`python3 saqef teardown --platform knative` first) — k3s itself stays
resident as the substrate, but a leftover `hello` ksvc's pods can silently
misclassify into another platform's CPU accounting (fixed 2026-08-08: OpenFaaS's
`cp_containers` no longer collides with `kourier-gateway`, and Fn/OpenFaaS/
OpenWhisk's isolation policies now forbid any `k8s_`-prefixed container; but
an empty box is still the point of a "quiet-box" run, not just a passing gate).

```bash
# sanity: box quiet
uptime                                  # load < ~0.5
ps aux --sort=-%cpu | head              # nothing > 5% CPU
docker ps --format '{{.Names}}'         # empty (incl. no leftover k8s_* / Knative pods)
```

Every `saqef run` below additionally enforces the **ambient-load quiet gate**
(20 s window / 15% ceiling, runbook §1) and records the reading + top-CPU snapshot
in the result dir's `summary.json` → `ambient` — so each citable result
self-certifies that it was measured on a quiet box.

# 1) regression gate (OF first, then Fn; calibrates nothing, uses idle_w=4.3)
python3 saqef regression

# 2) OpenWhisk full run: calibrate idle-w WITH the OW stack up, zero traffic
python3 saqef deploy --platform openwhisk
python3 saqef verify --platform openwhisk     # expect 100/100, ~5.3 ms/inv
python3 -c "import time;p='/sys/class/powercap/intel-rapl:0/energy_uj';\
 e0=int(open(p).read())/1e6;time.sleep(60);e1=int(open(p).read())/1e6;\
 print('idle_w=%.3f'%((e1-e0)/60.0))"
# ^ USE THE PRINTED VALUE, do not copy a number from this file: idle watts are
# box-state, not a constant (this run measured 5.294 on 2026-08-07, 3.889 on
# the 2026-08-08 quiet rerun -- same box, different day). Pasting an old
# number here defeats the calibration step and silently reproduces a stale
# baseline (this bug existed in this exact runbook until 2026-08-08 -- the
# example below had literally hardcoded 5.294 right after telling you to
# recalibrate it). Substitute <IDLE_W> with whatever just printed.
python3 saqef run --platform openwhisk --metric cpubound \
  --total 10000 --concurrency 4 --duration 300 --warmup 20 --repeat 5 \
  --idle-w <IDLE_W> --out results/openwhisk_cpubound_baremetal
python3 saqef gates --out results/openwhisk_cpubound_baremetal

# 3) Knative full run: calibrate idle-w WITH the knative+kourier+k3s stack up,
# zero traffic (this stack's idle draw is a bit above the bare/OW-standalone
# baseline -- 16 warm replicas + 32 proxies + knative-serving + kourier -- so do
# NOT reuse the OpenWhisk idle-w above). Use the N>=3 repeated-read protocol,
# not a single 60 s read: single-sample Knative idle-w reads spanned 11.14 /
# 7.01 / 4.91 W across three sessions (>2x) with no repeats -- that N=1
# fragility is finding #13, closed 2026-08-09 by the N=5 protocol in
# tools/reanchor_and_kn_idle.sh (c): bare substrate 3.871 W, with hello @ 16
# replicas 4.561 W (medians), premium 0.690 W. Run the (c) section for the
# fresh pair, or at minimum repeat the single-read calibration N>=3 and take
# the median. This section was entirely missing from the runbook until
# 2026-08-08 despite a cited result depending on it -- re-deriving it from
# scratch was the only way to reproduce Knative's cp_dynamic_share_pct=11.40 /
# energy citability verdict.
python3 saqef deploy --platform knative
python3 saqef verify --platform knative       # expect 100/100, ~5 ms/inv
python3 -c "import time;p='/sys/class/powercap/intel-rapl:0/energy_uj';\
 e0=int(open(p).read())/1e6;time.sleep(60);e1=int(open(p).read())/1e6;\
 print('idle_w=%.3f'%((e1-e0)/60.0))"
# ^ repeat 3+ times with the stack in the state you will bench (hello deployed
# at 16 replicas), take the median, use THAT as <IDLE_W> below.
python3 saqef run --platform knative --metric cpubound \
  --total 10000 --concurrency 4 --duration 60 --warmup 20 --repeat 5 \
  --idle-w <IDLE_W> --out results/knative_cpubound_baremetal
python3 saqef gates --out results/knative_cpubound_baremetal
```

Gates must show: delta% ~0, CPmapped 1/1 (OW) / 6/6 (OF) / 15+/15+ (Kn),
coverage 100%, `host_plausible=true`, `host_saturated=false` (else latency is
not citable; the share still is — it is contention-robust).
