# SAQEF troubleshooting & runbook

Everything that bit us during the 2026-08-07 overnight session, the root cause,
and the fix — so the same problem is a five-minute check next time instead of a
night. **Read this before any measurement session.**

**This file is also the canonical bug ledger for the whole project** (retrofitted
2026-08-14): every distinct bug found across this project's review history belongs here,
in one place, ordered roughly by discovery — not scattered across `AGENTS.md`'s dated
session narratives, and not duplicated into a separate ledger file, which would just be a
second thing to keep in sync with this one. Items 1–11 predate this convention and stayed
scoped to measurement-session operational gotchas; items 12 onward cover the full range
(carbon math, container-matching logic, isolation policy, tooling), each with an explicit
**Verification** note distinguishing *re-derived directly from the current source or diff*
from *reported by a prior review pass and not independently re-checked here* — the
discipline every future entry should follow. A description of a fix is not the fix; when
in doubt, trace to the file, not to a summary of the file.

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

## 11. `run_lock_session.sh` regression: item #6's `--duration 300` fix for OpenWhisk didn't
   survive the move to the one-file four-platform driver

**Symptom (hit 2026-08-13, lock session stamp `lock2`):** every one of OpenWhisk's 5 bench runs
printed `hey: subprocess failed (... timed out after 180 seconds); falling back` and
`WARNING: hey unavailable/failed -> python load generator`. Downstream: `requests: 1993` against a
`--total 10000` target (protocol never completed the count-bound run); `env.loadgen: "py"` /
`env.loadgen_requested: "hey"` / `env.loadgen_fallback: true` on all 5 runs; median throughput
collapsed to **8.3 rps** against this platform's established ~65–70 rps baseline; wall time per
run stretched to ~240 s; `container_inventory` showed 4 `wsk0_*_prewarm_nodejs20` containers and 3
`guest_hello` action containers (documented normal steady-state is exactly 2 prewarm containers,
see item 4's adapter note) — consistent with the invoker being repeatedly re-provisioned across
five timeout/retry cycles; every run also logged `WARNING: N.N CPU-s fell outside both cp and fn
containers (stray container?)` (3.4–4.3 CPU-s/run — two orders of magnitude above the ~0.1–0.3
CPU-s `unclassified_cpu_s` seen on clean runs of any other platform in the same session).
`saqef gates`' coded checks (delta%, CPmapped, host_plausible, coverage%) all still passed and
printed `OK` — none of them look at `requests` vs `total` or `loadgen` vs `loadgen_requested`, so
the degraded run was not flagged automatically.

**Root cause:** item 6 (above) already diagnosed and fixed this exact failure mode for the
standalone per-platform protocol — `saqef`'s loadgen subprocess kill-switch is
`deadline_s + 120`, so with the CLI's default `--duration 60` OpenWhisk's kill-switch sits at
180 s while its own low throughput needs ~150 s to clear 10000 requests at c=4, leaving only ~26 s
margin; the runbook's own reproduction commands (Quiet-box runbook section below) have used
`--duration 300` for OpenWhisk since that fix. `tools/run_lock_session.sh` — a newer, one-file
driver that runs all four platforms back-to-back for the same-day/same-box/quiet-gate discipline
(cold-review issue #2) — consolidates each platform's `deploy`/`verify`/`run`/`gates`/`teardown`
calls into one `run_leg()` function, but that function's `$SAQEF run ...` invocation never passes
`--duration` at all, for any platform, so every leg silently falls back to the CLI's hardcoded 60 s
default. The platform-specific override that existed in the pre-consolidation manual protocol was
not carried forward. (This is the second bug found in this script since it was introduced — see
commit `bcf32ac`, a gate/summary outdir-and-platform-key bug — the script is still shaking out.)

**What is and isn't tainted by this:** `env.ambient` (the quiet gate) and the freshly-calibrated
`idle_w` for the OpenWhisk leg are both unaffected — those complete before the loadgen phase and
were fine (`ambient.load_pct: 11.7` < 15% threshold, `idle_w: 3.960` from a clean N=5×60s
calibration). Only the loadgen-dependent numbers are corrupted: throughput, latency percentiles,
`requests`/`successes`, and the RAPL-derived energy/carbon figures (already separately flagged
structurally non-citable for OpenWhisk regardless, per item 6's neighbor notes and
`AGENTS.md`'s confidence tiering). `cp_dynamic_share_pct` (82.36% this run) is a pure cgroup
CPU-time ratio and is plausible/consistent with prior citable OpenWhisk sessions (82.36–82.54%
historically) — but given how much else about this leg is anomalous, treat it as an unconfirmed
data point, not a clean fourth reproduction, until it's reproduced on a rerun that completes
10000/10000 on `hey`.

**Fix (applied 2026-08-14):**
1. `run_leg()` now sets `local duration=60; [ "$platform" = "openwhisk" ] && duration=300` and
   passes `--duration "$duration"` to both the `--dry-run` preview line and the real `$SAQEF run`
   invocation — 60 s remains fine for OF/Fn/Kn given their throughput.
2. `saqef`'s `gates_for()` now prints two more per-run flags, computed from fields that already
   existed in every run's `summary.json` (no new instrumentation needed): `INCOMPLETE RUN` when
   `requests != total_requested` (the harness now also records `total_requested` — it wasn't
   captured anywhere before this fix), and `LOADGEN FALLBACK` when `env.loadgen_fallback` is true.
   Either would have caught this run automatically instead of requiring a manual read of the log.
   Covered by `tests.test_saqef_cli.TestGatesFlagsIncompleteAndFallback` (also asserts a legacy
   summary missing `total_requested` degrades to "no flag", not a crash).
3. `tests.test_saqef_cli.TestLockSessionDurationOverride` statically asserts `run_leg()`'s
   OpenWhisk branch sets `duration >= 300` and that `--duration` reaches both the dry-run echo and
   the real invocation — zero-cost, no docker/k3s dependency — so a future refactor of this script
   cannot silently drop the override a third time without failing the test suite.
4. **Still open:** rerun the OpenWhisk leg (fresh teardown/redeploy, not the churned deployment
   from the failed session) under the same day/box/quiet-gate discipline as the other three legs
   already captured — no need to rerun OpenFaaS/Fn/Knative, which completed cleanly. This is the
   one step the fix above doesn't do for you; `python3 -m unittest tests.test_saqef_cli` (55/55
   passing after this fix) proves the *code* is right, not that OpenWhisk's numbers are refreshed.

## 12. Carbon computation — 1000× unit inflation (fixed 2026-08-06)

**Symptom:** every `carbon_gCO2` figure (op_total, idle_band, per-invocation KPIs) was exactly
1000× too large — a 9-second, 600-request session reported ~18 grams of CO2, roughly what a car
emits driving 100–150 meters, for nine seconds of laptop-class compute.

**Root cause:** the carbon formula converted Joules to watt-hours correctly (`e_total / 3600.0`)
but then multiplied that Wh figure directly by a **per-kilowatt-hour** carbon-intensity constant,
without dividing by 1000 to go from Wh to kWh first.

**Fix:** every carbon call-site now divides by `3.6e6` (J → kWh directly) instead of `3600.0`
(J → Wh) — `op_gco2`, `cp_gco2`, `kpi_dynamic`, `idle_band`, `op_carbon_gCO2_by_busy_w`, and the
per-invocation KPI figures.

**Follow-on bug this fix exposed:** `kpi_gco2_per_slo_compliant_inv` was rounded to 4 decimal
places. Post-fix, the true magnitude is ~1e-5–1e-4 g, which rounds to `0.0` — the field went
silently useless at the corrected scale. Fixed by widening to `round(kpi, 8)`.

**Does NOT affect:** `energy_J.*` (plain Joules), `cp_dynamic_share_pct` (a CPU-time ratio,
carbon-formula-independent), `rapl_validation_err_pct` (Joules-only comparison).

**Verification:** the current `saqef_harness.py` carbon block still carries the comment recording
this exactly: *"J -> kWh is J / 3.6e6 (NOT the old Wh conversion -- divide by 3600 then multiply
by a per-kWh intensity leaves a spurious 1000x in every gCO2 figure, the historical unit bug,
fixed 2026-08-06)."* Re-derived independently rather than taken on the comment's word — recomputed
both formulas by hand with the model's own constants (ci=150 gCO2/kWh, PUE=1.15) at
e_total=382.2 J: correct = `382.2/3.6e6 * 150 * 1.15` = **0.0183 gCO2**; buggy =
`382.2/3600 * 150 * 1.15` = **18.314 gCO2** — a clean 1000× ratio.

## 13. OpenFaaS/Knative container-name collision — `"gateway"` substring (fixed 2026-08-08)

**Symptom:** OpenFaaS's control-plane CPU was silently inflated by Knative's `kourier-gateway`
pod whenever both platforms' substrates were resident on the box — the normal state, since
k3s/Knative stays up as shared infrastructure across every platform's session (see items 16/17).

**Root cause:** OpenFaaS's `cp_containers` matcher used a bare substring, `"gateway"`, which also
matches `kourier-gateway` / `3scale-kourier-gateway`.

**Fix:** matcher tightened to the swarm-stack-prefixed names Docker Swarm actually assigns
(`openfaas_gateway`, `openfaas_faas-swarm`, `openfaas_prometheus`, `openfaas_nats`,
`openfaas_queue-worker`, `openfaas_alertmanager`) — exact stack-prefixed names, not a substring
guess, since `docker stack deploy` deterministically prefixes every task name with the stack name.

**Verification:** confirmed directly — `tests/test_saqef_cli.py`'s `OF_CP` constant carries this
exact comment today: *"Prefixed with the swarm stack name (fixed 2026-08-08): a bare 'gateway'
substring collided with Knative's 'kourier-gateway' pod containers, since k3s/Knative stays
resident on this box across every platform's session."* Every OpenFaaS leg in the 2026-08-13
`lock2` session showed `CPmapped: 6/6` with the six real `openfaas_*` containers and zero Kourier
entries.

## 14. Fn/OpenFaaS `fn_images` collision with Knative's `kn-hello`

**Symptom:** none observed in production data — caught and fixed proactively.

**Root cause:** Fn and OpenFaaS both matched `fn_images=("hello",)` by substring containment.
Knative's function image is `kn-hello`, which also contains the substring `hello`. The only
reason this hadn't fired in practice was an incidental docker/containerd quirk on this box
(k3s-managed containers showing a bare image digest rather than a resolved tag) — not a real
guarantee.

**Fix:** `_image_repo_basename()` strips a digest suffix, registry/path, and tag, then requires
**exact** basename equality instead of substring containment. Every `Adapter` subclass also
hard-requires a non-empty `fn_images` allowlist at construction time now.

**Verification:** read the full function directly (`saqef_harness.py:394`), not just a
description of it. It does exactly what's claimed: `image.split("@",1)[0]` (drop digest) →
`.rsplit("/",1)[-1]` (drop registry/path) → `.split(":",1)[0]` (drop tag) → `.lower()`. Its own
docstring example (`'localhost:5000/saqef/kn-hello:0.0.1' -> 'kn-hello'`) confirms the
port-in-registry-host case doesn't get mistaken for a tag. `platforms/base.py`'s `Adapter.__init__`
confirms the allowlist requirement: *"fn_images allowlist must be non-empty (manifest #1:
hello-image overlap taint)."*

## 15. RAPL `energy_uj` wraparound — false single-wrap correction

**Symptom:** none observed in practice on this box (RAPL's counter range vastly exceeds what a
17–320 s run at realistic power draw could consume) — caught by review before it could bite.

**Root cause:** the original wraparound handling added one counter range (`rapl_max_range_j()`)
to any negative raw delta and reported the result as corrected, with no check that the correction
actually landed in valid (non-negative) territory. A double-wrap (or worse) would silently produce
a plausible-looking but wrong number.

**Fix:** `rapl_correct_wrap()` now checks whether the corrected value is still negative after a
single-wrap correction; if so, it returns `(None, "uncertain_double")` instead of a fabricated
value. Four distinguishable states — `"none"` / `"corrected_single"` / `"uncertain_double"` /
`"uncertain_no_range"` — so a discarded reading is now distinguishable from "RAPL unavailable" (a
separate `rapl_available` field), which the original version conflated.

**Residual, honestly-documented limitation (not fixed, physically unreachable on this box):** from
a single before/after sample there is no way to determine the true wrap count in general — a
genuine double-or-more wrap whose raw delta happens to land ≥0 after adding one range is silently
mislabeled `"corrected_single"` rather than caught. The function's own docstring says so directly:
*"This is a mathematical limitation of two-point sampling, not a bug... It is inconsequential on
the machine this study runs on -- max_energy_range_uj is ~262 kJ, several orders of magnitude
above what a 17-320s run consumes at realistic power draw."*

**Verification:** read the full docstring and the `corrected < 0` branch directly in
`saqef_harness.py`'s `rapl_correct_wrap()` — confirmed the check is real, not a blind correction.
Covered by five dedicated unit tests in `tests/test_saqef_cli.py` (`TestHarnessAggregation`):
`test_rapl_correct_wrap_single`, `test_rapl_correct_wrap_double_is_uncertain`,
`test_rapl_correct_wrap_no_range_is_uncertain`,
`test_rapl_correct_wrap_double_can_be_mislabeled_single` (exercises the residual limitation above
directly), `test_rapl_correct_wrap_none_passthrough`.

## 16. Isolation guard hardcoded to two platforms

**Symptom:** the harness's own internal `assert_platform_isolation()` — a second, independent
check beyond the adapter-level `check_isolation()` — only recognized `"fn"` and `"openfaas"`; for
any other platform (Knative, OpenWhisk) it silently returned `(True, "")`, providing zero
protection.

**Root cause:** a hardcoded `if/elif` chain that was never updated when the Knative and OpenWhisk
adapters were added.

**Fix:** ported to a data-driven check — every adapter now owns an `IsolationPolicy`
(`platforms/base.py`), and the CLI passes it through as `--forbidden-services` /
`--forbidden-containers` to `assert_platform_isolation()`, so every platform (OpenWhisk included)
gets the same defense-in-depth check at measurement time. The old hardcoded chain is kept only as
a fallback for legacy shell runners that don't yet pass those flags.

**Live-caught follow-on bug (2026-08-08):** the first version of this fix forbade any
`k8s_`-prefixed container — which correctly blocked Fn/OpenFaaS/OpenWhisk while Knative's `hello`
was deployed, but also *permanently* blocked them afterward, because k3s/Knative's own control
plane (activator, kourier-gateway, coredns, ...) is designed to stay resident on this box as
shared substrate across every platform's session. Fixed by narrowing the legacy fallback's
leftover-check to the two containers that only exist when Knative's `hello` is actually deployed
(`user-container`, `queue-proxy`), not the substrate itself. The current
`assert_platform_isolation()` docstring records this precisely: *"checking for ANY 'k8s_'-prefixed
container would permanently block Fn/OpenFaaS even with 'hello' properly torn down (confirmed live
2026-08-08)."*

**Verification:** read `IsolationPolicy`, `Adapter.check_isolation()`, and
`assert_platform_isolation()` directly (`platforms/base.py`, `saqef_harness.py:904`) — the
data-driven path, the legacy fallback, and the k8s_-prefix-was-too-broad fix are all present in
the current source exactly as described. Every leg of the 2026-08-13 `lock2` session's
precondition check reported `"k3s/Knative substrate: resident"` without falsely blocking any of
the four platforms.

## 17. Isolation-failure advice hardcoded regardless of the actual offender

**Symptom:** any isolation failure — regardless of which container/service actually tripped it —
printed the same fixed remediation advice (`docker rm -f fnserver`), which was actively wrong for
a Knative-leftover or OpenWhisk-leftover failure.

**Fix:** `Adapter._CONTAINER_ADVICE` — a lookup table matched by substring against the offending
container name — plus `_advice_for_container()` / `_advice_for_service()` resolve advice from the
specific matched offender, with a generic fallback (`docker rm -f <name>`) for anything
unrecognized.

**Verification:** read the table directly (`platforms/base.py`, `_CONTAINER_ADVICE`) — five
entries, each keyed to a specific remediation (`fnserver` → *"docker rm -f fnserver"*;
`user-container`/`queue-proxy` → *"saqef teardown --platform knative"*; `openwhisk` →
*"saqef teardown --platform openwhisk"*; `openfaas` → *"saqef teardown --platform openfaas"*). The
class's own comment states the motivation exactly: *"Fn/OpenFaaS/OpenWhisk all forbid Knative's
per-replica pod containers now... so a single hardcoded message was wrong for 3 of 4 adapters --
it told the operator to remove fnserver when the actual offender was a leftover Knative ksvc,
sending them down the wrong troubleshooting path."*

## 18. Contamination A/B tool — profile-mismatch enforcement

**Not a bug fix — a methodology upgrade worth recording**, since it replaced a guess with a real
measurement, and its own history is a good example of a doc going briefly stale (see the
verification note below).

Earlier sessions *inferred* that agent-style background load might move `cp_dynamic_share_pct` by
"0.3 to 1 percentage point" (item 1, above). A dedicated tool, `tools/contamination_ab.py`,
actually measures it: a clean leg under the enforced quiet gate vs. a dirty leg reproducing the
documented incident profile (N busy cores + emulated agent RSS), N=5 each.

**Result (2026-08-07/08 measurement):** Fn +2.16 pp, OpenFaaS +0.34 pp under the documented
incident profile — a ~6× asymmetry, consistent with a central-orchestrator-vs-per-replica-proxy
design difference. The dirty-leg gap (4.92 pp) sits only 0.08 pp under the 5 pp discrimination
threshold used elsewhere in this study — thin enough to state explicitly as a live risk, not a
comfortable margin.

**Fixed (commit `66f8517`, 2026-08-13):** the tool used to measure the achieved background-CPU
profile and print it without checking it matched the target — a dirty leg that undershot or
overshot the documented incident profile would silently be reported as if it were that profile.
It now **aborts** (`raise SystemExit`) if achieved host-busy% deviates from target by more than
`--profile-tolerance-pct` (default 10 pp), unless `--allow-profile-mismatch` is explicitly passed
for exploratory (non-bound) runs. The achieved/target profile is saved into
`contamination_ab.json`'s `"achieved_profile"` key for every run either way.

**Verification:** read `tools/contamination_ab.py` directly, twice — once via targeted grep, once
as a full un-grepped read of the relevant section — and confirmed the `raise SystemExit` abort
path is live in the current working tree. Then went one step further and read the actual
historical diff (`git show 66f8517 -- tools/contamination_ab.py`), not just the commit message,
confirming this exact abort block was what that commit added (36 insertions). This item briefly
existed in a stale intermediate summary claiming it was "not yet fixed" despite `66f8517` already
being merged — recorded here as a caution: even a description of a fix, freshly written, can
already be out of date by the time it's read. Trace to the file.

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

## 19. CPU was attributed over the sampler's whole span, not over the load (fixed 2026-10-01)

**Symptoms.** `cp_cpu_s` / `fn_cpu_s` / `cp_dynamic_share_pct` read high, and the error
scales with how far the sampler's span sticks out past the load. Worst on the
scale-to-zero platforms, where the CP container is running before the first request
and after the last one.

**Cause.** `sample_totals()` accumulated cumulative counter deltas across the whole
sampled span and only checked coverage. The sampler deliberately starts *before* the
load and stops *after* it (so the load is bracketed by real observations), which means
the first interval straddles `t0` and the last straddles `t1`. Those overhang slices
were credited to the load in full. A second, smaller defect sat next to it: for a
cumulative counter the delta `cum[i] - cum[i-1]` accrued over `(t_prev, t]`, but the
per-container overlap factor was computed from the *forward* span `(t, t_next]`.
Applying a forward-span factor to a backward-span delta shifts every delta one sample
later, which is what dragged the entire pre-load stretch into the first in-window
interval.

**Fix (`c22dff9`).** `sample_totals()` takes an optional `window` (the runner passes
the real load window). Each interval is prorated against *its own* span, and coverage
and the sampling-gap metric are only claimed for intervals that overlap the window —
a gap lying wholly outside the load says nothing about coverage of the load. Backward
compatible: `window=None` keeps the old whole-span behaviour, and old `samples.csv`
replays still work.

**Consequences to carry forward.**
- Every dataset measured before `c22dff9` is non-citable for CPU attribution. Protocol,
  gates, classification and the per-invocation framing survive; the CPU numbers do not.
- Because the bias inflates CP time, it biased `cp_dynamic_share_pct` **upward**, and
  by a leg-dependent amount — i.e. it moved exactly the quantity the concurrency sweep
  exists to compare. Do not "correct for it" post hoc: the overhang is not recoverable
  from a `summary.json`, so those legs must be re-measured.

## 20. A container's CPU burned before its first sample was discarded (fixed 2026-10-01)

**Symptoms.** `fn_cpu_s` too low, so `cp_dynamic_share_pct` biased **upward**. The error
is largest for the container that appears last in the run.

**Cause.** A container discovered mid-run arrives with a `cpu.stat` counter that already
contains everything it has burned since creation. `sample_totals()` took the first
sighting of any container as delta 0 (`prev is None`), throwing that slice away. On a
platform that creates function containers seconds into the run, the last one to appear
can be carrying seconds of real CPU. The sampler rescans cgroup directories every
`--rescan-s` (0.25 s), so this is not a rare edge case — it is the common case for a
short run.

**Fix (`34b4f26`).** `container_name()` returns `(name, birth_epoch_s)` from the **same
single inspect** it already performed (`{{.Name}}|{{.Created}}`), so this costs no
extra subprocess spawn and the sampler's zero-spawn steady state is preserved. Snapshots
carry the birth time as an optional third element and `sample_totals()` credits the
counter over `(born, t]`, keeping only the part inside the window.

Two traps worth knowing before touching this code again:
- The birth slice needs its **own** overlap factor. The existing per-sample factor
  describes a different interval; applying both prorates the slice twice. There is a
  mutation test for this specific double-proration.
- Unknown birth times must keep the old drop-the-slice behaviour. A guessed
  attribution is worse than an honest undercount.

**Blind spot that remains (not fixed, by design).** A container that is born *and*
fully removed between two cgroup rescans is never observed and its CPU is
unrecoverable. Closing that would need event-driven sampling, which costs the
sub-millisecond cadence the whole design rests on. State it as a limitation; do not
paper over it.

## 21. Tier-1 session logs overwrote each other (fixed 2026-10-01)

**Symptoms.** The provenance of an earlier session's numbers cannot be read back — the
transcript that produced them no longer exists anywhere.

**Cause.** `tier1_go.sh` and `run_tier1_quiet.sh` both teed to the fixed path
`results/tier1_session.log`, so the second session silently destroyed the first one's
record. This already happened: the OpenWhisk `tier1ow8` leg overwrote the log of the
session that produced the earlier concurrency data.

**Fix (`d1fb198`).** `tools/tier1_log.sh` gives each session a UTC-stamped log
(`tier1_session_<stamp>.log`). The well-known `tier1_session.log` path is kept as a
symlink to the newest session so existing references still resolve, and anything
regular already sitting at that path is dated and moved aside rather than
overwritten. No measurement semantics change.

## 22. Two different clocks in the attribution window (fixed 2026-10-01)

**Symptoms.** `cp_cpu_s` = 0, `fn_cpu_s` = 0, `covered_s` = 0 — from a run that
clearly produced samples and clearly served requests. The lock session's sampling-gap
gate **passes anyway**, because it only inspects gaps *inside* a window and an empty
window contains no gaps. So the one gate that was supposed to catch a broken sampler
is structurally incapable of catching this particular breakage.

**Cause.** The sampler stamps every sample with `time.time()` — epoch seconds
(`saqef_harness.py:962`, and `:771` for the pct-mode sampler). `run_once()` built the
attribution window from `time.perf_counter()` — seconds since boot. The two differ by
~1.76e9 on this box, so `window=(t0, t0 + wall)` shared no number with any sample
timestamp and every interval tested as "entirely outside the load".

**Fix (`908bece`).** Keep both clocks and give each the job it is good at:

```python
t0       = time.perf_counter()   # wall only -- monotonic, so a mid-run NTP
                                  # step cannot corrupt the duration
t0_epoch = time.time()           # the window -- same base as the samples
...
window=(t0_epoch, t0_epoch + wall)
```

Both are read together before the load starts, so the window begins at or before the
first sample and the pre-load overhang remains clippable.

**Why the test suite could not see it — the general lesson.** Every window test
constructed its window in the same numeric space as the timestamps it handed to
`sample_totals()`. Nothing exercised the real `run_once()` → `sample_totals()` path
with raw clocks. A test that supplies both sides of an interface cannot detect a
mismatch between them; only a test that takes its values from the *producers* can.
Two tests now pin this: one asserts `run_once()` pairs the epoch base with
`time.time()`, and one drives `sample_totals()` with raw `time.time()` and
`time.perf_counter()` values to show that a boot-based window attributes exactly
nothing.

**Scope of the damage.** Introduced with the window clip (`c22dff9`), so the clip
itself had never run against real data — it could only ever have returned zero. No
measurement was taken while the bug was live.

## 23. Two silent analysis errors that nearly cost a full re-measurement campaign (2026-10-01)

Both errors were mine, made while triaging §21/§22, and both produced a confident
wrong answer. They are recorded because the failure mode is general, not because
the numbers mattered.

### 23.1 A field that is a total was read as a rate — 60× error

`idle_probe_*/*/summary.json` → `cpu_sec` is a **total over the probe's `wall_s`
(60 s)**, not a per-second rate. It was read as cpu-s/s.

```
Knative fn : read "0.40 cpu-s/s"  ->  actually 0.40/60 = 0.0067 cores
OpenWhisk  : read "2.37 cpu-s/s"  ->  actually 2.18/60 = 0.036 cores
```

Every bias estimate that multiplied an overhang duration by this number was
inflated ~60×. The tell was internal: the analysis produced "a 12.3 pp bias on a
12.10 % share", which requires a 2 s overhang to have burned more CPU than the
entire 3.8 s load on an 8-core box. **A bias larger than the signal it corrects
is a units bug, not a finding.**

Related trap in the same field family: `orchestration_cpu_sec` is
`host_cpu_sec − fn_cpu_s` (harness:1403) and includes kernel, dockerd and the
sampler — it is *not* `cp + fn`. The correct denominator for a dynamic share is
`cpu_sec.control_plane + cpu_sec.function`. Using the wrong one put the
reconstruction ratio at 1.75× and made a working method look broken.

**Rule adopted:** before trusting any derived quantity, print the field next to
the run's own `wall_s` and ask whether the magnitude is physically possible. A
total and a rate in the same JSON are the most common source of this.

### 23.2 A salvage triage was decided by a 3-sample MAD

Biases were compared against the median absolute deviation of n=3 runs. n=3 MAD
is dominated by sampling noise; judging a systematic bias against it is
noise-on-noise and produced a verdict ("247× noise", "redo everything") that the
re-attribution later disproved.

**Rule adopted:** compare the bias to **the contrast the claim actually rests
on** — e.g. a c=1→c=16 share difference of ~2 pp — not to a noise estimate. If the
bias is small against the contrast, the claim survives.

### 23.3 The re-attribution that settled it (`tools/legacy_reattribute.py`)

No re-measurement campaign was needed. `samples.csv` retains, per sample instant
and per container, the interval CPU rate the old code computed
(`pct = Δcum/(t[i+1]−t[i]) × 100`), which integrates back to the stored totals:
264 of 269 committed legs rebuild to a median error of **0.018 %** (max 0.42 %).
The tool refuses to report a number it cannot reproduce (`--verify` exits
non-zero) and solves the fn allowlist against each run's own stored total rather
than assuming one.

Result: **the window clip moves the numbers by essentially nothing.** Median
**0.003 pp**, max **0.074 pp**, and only 4 of 264 legs exceed 0.05 pp. Every stored
headline figure is unchanged to within 0.02 pp, and per table cell the clip is
≤0.014 pp.

#### 23.3.1 The bug that produced the opposite conclusion (commit `05d07f5`)

An earlier version of this runbook reported the clip shifting the share upward on
222/257 legs at median **+0.36 pp / max +1.85 pp**, varying with concurrency, and
drew four conclusions from it (OpenFaaS c=1→c=16 collapsing 0.75 → 0.11 pp, Fn's
minimum moving c=2 → c=8, OF c=2 "explained", and per-platform ranges of
1.67/2.42/3.68 pp). **All four were artifacts of a bug in the tool, not
properties of the platforms.**

`_integrate()` classified into two buckets — `if name in fn_set: fn else: cp` —
so every container that was neither function nor control plane landed in the CP
**numerator**, while the caller subtracted `unclassified_cpu_s` from the
**denominator** only. OpenFaaS c=1 run_1 stored 6.93 % and reported 8.03 %:
(1.41 + 0.23)/(1.41 + 18.96) = 8.05 %. Because the unclassified bucket is a
roughly *constant* absolute quantity while the share's denominator varies with
concurrency, the leak produced a shift that grew as concurrency fell — precisely
the pattern the old text read as a real bias.

**The `--verify` gate could not detect it.** It checked `cp + fn` against the
stored `cp + fn`, and that sum is correct under a mis-assigned bucket. So the
gate passed, 160 tests passed, and every reported share was inflated. *A gate on
the total cannot detect a wrong split.* The gate now checks each bucket
independently: cp and fn at 1 % (corpus maxima 0.59 % / 0.44 %), and unclassified
on **absolute** error at 0.01 cpu-s — never on its ratio, which reaches 46.9 % on
a rounding-sized absolute difference.

Two further defects surfaced while fixing it, both worth keeping:

- **The clip was being gated.** Comparing a clipped figure against the
  *unclipped* stored total reported 9 correct legs as `verify_failed` at 1.0–2.3 %
  error — purely because clipping worked. A deliberate correction must not be
  gated against the uncorrected value. The gate now always judges the unclipped
  integration; the clip is reported separately.
- **A hand-kept allowlist drifts.** `CP_CONTAINER_HINTS` had already fallen out
  of sync with the adapters' own `cp_containers` (missing
  `openfaas_nats`/`openfaas_queue-worker`/`openfaas_alertmanager` and Knative's
  `kourier-gateway`) — the same class of bug as the OpenFaaS "gateway" substring
  incident. It is now test-locked to `platforms/*.py`.
- **Terminal escapes in container names.** The 5 pre-`container_labels` legs
  recorded one container under two names, `\x1b[H01KZ…` and `\x1b[J\x1b[H01KZ…`,
  because an escape-laden `docker ps` header bled into the field. Its CPU was
  counted twice (+5.2 % on run_1's fn total).

**The 5 remaining legs.** `fn_cpubound` predates `container_labels`, so fn is
inferred name-only as "everything that is not the adapter's cp container". That
cannot be checked independently, so it is held to a **tighter** 1 % bound rather
than a looser one — and it fails, at 3.1–16.6 %, because `samples.csv` retains
8.48 s where `wall_s` is 14.24 s. The stored totals came from a span the samples
no longer cover; recovering them needs cgroup files that do not exist. Final
state: **264 ok, 5 `verify_failed`, 0 `unclassifiable`.**

#### 23.3.2 What survives

- **Paper §5.3's "flat within ~1–2 pp on every platform" is false as written** —
  but because of the **stored** spread, not the clip: 1.48 / 2.13 / 2.74 pp across
  c=1/2/8/16 (OF / Kn / Fn) and 1.52 / 2.85 / 3.41 pp across tier-1 c=1/2/4/8.
  Restate per platform.
- **The sweep minimum is per platform, and only OpenFaaS's is at c=2.** Recomputed
  from the per-run summaries (medians; ranges are max−min of medians — the quick
  sweep is `REPEAT=3`, tier-1 is `REPEAT=5`):

  | platform | quick c=1/2/8/16 | range | tier-1 c=1/2/4/8 | range | min |
  |---|---|---|---|---|---|
  | OpenFaaS | 7.00 / 5.82 / 6.51 / 7.75 | 1.93 | 7.78 / 6.26 / 7.16 / 7.15 | 1.52 | **c=2** |
  | Fn | 12.66 / 11.06 / 9.92 / 11.01 | 2.74 | 13.97 / 12.03 / 10.57 / 10.56 | 3.41 | **c=8** quick; **c=4≈c=8** tier-1 |
  | Knative | 14.08 / 11.97 / 12.10 / 12.08 | 2.11 | 14.49 / 12.23 / 11.64 / 12.68 | 2.85 | **c=4** tier-1 |

  Knative's quick-sweep c=2/8/16 sit within **0.13 pp** of each other, so there is
  effectively no minimum there. OpenFaaS c=2 remains **unexplained** — it is *not*
  explained by the correction, which moves it by −0.004 pp.

  **How the wrong version got written.** Two edits were made from memory instead of
  from the per-run files, and both failed silently: the OF range came out as
  1.48 (matching neither the median nor the mean range) and Knative as 2.13 (2.11
  by median, 2.16 by mean). And the "c=2 minimum on all three" sentence was
  written *with its own numbers beside it* — "Fn 12.03 → 10.57 at c=4" states that
  c=4 is below c=2, so the sentence refuted itself one clause earlier. Nobody
  checked it against the table it was derived from.

  **Two rules from this, and both are the §23 lesson again:**
  1. **A derived figure must be recomputed from the committed per-run files in the
     same edit that introduces it** — never carried in a summary, never written
     from a remembered table. `python3 -c` over `results/*/run_*/summary.json`
     takes seconds and is the only thing that catches this.
  2. **No sentence may state a superlative (min/max) that its own adjacent numbers
     contradict.** The self-contradicting clause survived review because both the
     table and the prose read as plausible in isolation; only placing them side by
     side exposes it.

  One cell genuinely needs the statistic named: **Fn c=16** has runs
  17.43 / 10.66 / 11.01 — one warm-up-contaminated leg pulling the mean to 13.03
  while the median holds at 11.01 (CV 23.9 %). Under the mean, Fn's quick-sweep
  range reads 3.15 rather than 2.74. The median is the quoted statistic and the
  text now says so.
- **The cross-platform ordering OF < Fn < Knative holds at every concurrency**,
  which is the paper's central claim and is untouched — the correction is ≤0.02 pp
  on every platform, and a bias that moved platforms *differently* is the only
  thing that could have threatened it.

**Rule adopted from this:** a total-summed verification gate cannot certify a
per-bucket claim, and a per-platform conclusion drawn from a correction whose
magnitude is smaller than the reporting precision is a finding about the tool,
not the system. Re-derive any figure that moves when the attribution code moves.

### 23.4 A third class of the same bug: 21 tests that had never run

The §23.3 bug and this one share a shape — **a check that cannot fail was cited
as evidence that something was verified.**

`TestTier1StatsHygiene` has been in the suite since it was written, and its 21
tests had **never executed**. Its `setUpClass` extracts the aggregation heredoc
from `tools/run_tier1_conc.sh` and `exec`s the head of it, and that head begins:

```python
import json, math, os, statistics, sys
repo = sys.argv[1]
```

Under `python3 tests/test_saqef_cli.py` there is no `argv[1]`, so `setUpClass`
raised `IndexError` — **on the first day it ran.** unittest reports a
`setUpClass` failure as a single error and skips every test in the class, so
the suite showed `FAILED (errors=1)` with 147 passing and the whole thing was
easy to skim past.

What was silently not being checked:

- Tukey df must be `k(n-1)`, not the Bonferroni `2n-2`
- `statistics.stdev`, never `statistics.pstdev`, for a sample of runs
- a missing `wall_s` must fail closed, never become `1.0`
- the flatness threshold must be `studentized_range_q`, not `(tcrit+tpow)*sqrt(2)`
- the TOST margin must be pre-specified, and `se == 0` must not pass

Those are exactly the fixes §22 and §23 cite as "test-locked". They were locked
to nothing. This is the same failure mode as the `cp + fn` sum-only verify gate:
the aggregate was right, so the check passed, and the per-bucket claim it was
supposed to certify was never examined. The Tukey table itself is in the
heredoc and is correct — but *nothing was verifying it.*

**Fixed** by swapping `sys.argv` around the `exec` (a stub module in the exec
namespace does **not** work: the heredoc's own `import sys` on line 1 rebinds
the name to the real module). Suite now reports **180 passing, 0 errors**.

**New guard, because "the suite is green" is not the same as "the suite ran":**
`TestNoSilentlySkippedTestClasses` re-executes every `setUpClass` in the module
and fails if any raises, and fails on any `TestCase` with no `test_` methods.
Verified it fires by deliberately re-breaking the `sys.argv` fix — it reports
`TestTier1StatsHygiene.setUpClass: IndexError` and exits non-zero.

**Lesson, and it is the general one:** *a test that cannot run is worse than no
test, because it gets cited.* Both this and §23.3 were invisible to review,
because a green run and a hidden skip look identical in the output. When
verifying that something is checked, confirm the check **executes** — count the
tests, don't read the pass line.

### 23.5 New runs were strictly LESS re-analysable than the corpus they replace (2026-10-01)

The salvage worked because of an accident of history. The 2026-08-14/15 harness
wrote **full-span, unclipped** percent-rate rows, and `pct = Δcum/dt` integrates
back to the stored CPU-s totals — which is the entire reason `legacy_reattribute.py`
can reconstruct 264/269 legs. The current harness **breaks that property**:

- `sample_totals()` drops samples lying entirely outside the window (line ~1063)
  and scales partial intervals by their overlap fraction (line ~1114)
- `samples.csv` is written **downstream** of that clip, so the discarded CPU is
  gone from disk
- `summary.json` recorded **no** `t0_epoch`, no window, no git revision (49 keys,
  none of them any of those)

So new runs could not have been re-attributed at all. The clip itself is correct
and the measured effect is ≤0.074 pp — but correctness of the *number* was never
the issue. **If a window or birth bug appeared tomorrow, those runs would have
been unrecoverable exactly as the pre-2026-08-08 data is**, and box time would
have bought nothing. That is the worst outcome available: a run that passes every
gate and cannot be audited.

Fixed (1a):

- **`samples_raw.csv`** — the sampler's unclipped output, raw cumulative `cpu.stat`
  counters (`cum` mode, exact and cadence-independent) or instantaneous rates
  (`pct` mode, docker fallback), plus `mem_mb` and **`born_epoch`**. Written
  unconditionally alongside `samples.csv`, never instead of it: `samples.csv`
  stays the citable read that the figures and emitter consume.
- **`born_epoch` matters more than it looks.** At first sight the sampler sees a
  counter that already contains everything since creation; difference it away and
  that slice is gone. Knative creates fn containers seconds into a run, so this is
  not a rounding detail there — it is the same class of loss as the overhang.
- **`summary.json.attribution`** — `t0_epoch`, `window_start_epoch`,
  `window_end_epoch`, sampler and cadences, the resolved `cp_members`/`fn_members`,
  every allowlist, and the full `container_inventory`. The allowlists go in
  because `CP_CONTAINER_HINTS` had already drifted out of sync with the adapters'
  own `cp_containers` (§23.3) — a hand-kept list cannot be trusted alone.
- **`summary.json.harness`** — `git_rev` and `git_dirty`. A result is reproducible
  only if you know which code produced it; the 2026-08 corpus predates `c22dff9`
  and `34b4f26` and says nothing about it in its own JSON. `git_dirty` is there
  because an uncommitted edit is precisely the case where the commit hash
  misrepresents the code that ran.

The round trip is test-locked: `samples_raw.csv` is parsed back into the structure
`sample_totals()` consumes and must reproduce the in-memory `cp_cpu_s`/`fn_cpu_s`
to 1e-6, with the unclipped totals asserted strictly larger so the test cannot pass
if the clip were removed. That is the property the old corpus had and new runs
would have lost.

## 24. Bridge experiment — pre-registered decision rule (written 2026-10-02, before any bridge data)

**Why this exists.** Every committed share predates `c05a9df`. The old sampler forked
`docker ps` plus two `docker inspect` per container per scan (~3 cores, ~40 % of the box) and
stamped `t` *before* each multi-second scan. Offline re-attribution (§23) settles the window clip
(≤0.074 pp) but **cannot** bound how much that sampler disturbed the system under test. The bridge
measures that disturbance. The rule is written down and committed **before** the first leg so it
cannot be tuned to the outcome. Do not edit this section after bridge data exists. If the rule
turns out to be wrong, add a §24.x amendment that says what changed and why, and report results
under both versions.

### 24.1 Design

- **Harness:** **`v9.13.1-bridge-prereg`** — the tag under which the bridge legs run. Every leg will
  record `git_rev` = whatever `HEAD` is when the bridge starts, so this section must name that
  revision or it reads as a protocol deviation. The only change from `cec0bd9` (`v9.12-reanalysable`)
  is the driver's `--stamp-prefix` flag and this section itself: **no measurement code is touched**,
  so the two reference values and the bridge values are computed by identical `saqef_harness.py`.
  Confirm at run time:
  ```bash
  git rev-parse --short HEAD && git describe --tags --exact-match 2>/dev/null
  git status --porcelain      # must be empty
  ```
  The two known runtime review items (**transient-inspect fallback**, **RAPL gate semantics**) were
  **NOT** fixed before this section was written and are **not** fixed as of `cec0bd9` or
  `v9.13.1-bridge-prereg`. Neither
  changes attribution: the fallback only fires on a docker-inspect failure (it would abandon a leg
  rather than mis-measure it) and the RAPL gate governs energy, not the CP/fn share this bridge
  compares. Both therefore belong *after* the bridge, where changing them cannot move a
  reference value. If either is fixed first anyway, re-tag and record the new tag here, and say
  which.
- **Protocol:** identical to the existing tier-1 data, via `tools/run_tier1_conc.sh`: TOTAL=3000,
  REPEAT=5, OF/Fn/Kn at c = 1, 2, 4, 8, OW at c = 1, 4, 8, same `idle_w`, per-leg idle probe.
  **c = 16 is excluded** for the same reason the driver gives (oversubscribed 8-core box, different
  regime); the quick-tier c=16 cells stay trend-only.
- **Conditions matched to the reference:** governor `powersave` (all 169 old lock legs), same
  pinning, same function images, bare shell with agents quit, ambient quiet gate on.
- **Fresh stamps are required.** `run_lock_session.sh` refuses to clobber, and the 2026-10-01
  reference datasets occupy the bare `tier1c$c` / `tier1ow$c` names, so the bridge must run with
  the prefix flag added for this purpose (2026-10-02):

  ```bash
  bash tools/run_tier1_conc.sh --stamp-prefix bridge_        # ~2.5-3 h, all four platforms
  bash tools/run_tier1_conc.sh --stamp-prefix bridge_ --skip-ow   # ~1.5 h, lightweight only
  ```

  This writes `results/<plat>_cpubound_lock_bridge_tier1c<N>/` and
  `results/idle_probe_bridge_tier1c<N>/`, and the driver prints a `CARE` line if the flag is
  omitted. A prefix containing `/`, `tier1c` or `tier1ow` is rejected. Do not move or rename the
  reference datasets. **Verify before the first leg that the printed stamps carry the prefix** —
  the driver aborts mid-session if a stamp collides, which would waste the quiet window.
- **Reference:** the tier-1 datasets below were taken on 2026-10-01 **before** `c05a9df` (their
  summaries have no `sampling_max_gap_s`), with the same protocol. So reference vs bridge isolates
  the sampler change, confounded only by day-to-day drift (see 24.3 step 4).

### 24.2 Reference values (frozen)

Median and MAD of `cp_dynamic_share_pct` over the reference runs. Tolerance = max(2 × MAD, 0.50 pp).
The 0.50 pp floor exists because a 5-run MAD can be implausibly small (Fn c=4: 0.02 pp), and §23.2
records what happens when a bias is judged against noise-on-noise.

| platform | c | reference dataset | n | median | MAD | tolerance |
|---|---|---|---|---|---|---|
| openfaas | 1 | `openfaas_cpubound_lock_tier1c1` | 5 | 7.78 | 0.13 | ±0.50 |
| openfaas | 2 | `openfaas_cpubound_lock_tier1c2` | 5 | 6.26 | 0.21 | ±0.50 |
| openfaas | 4 | `openfaas_cpubound_lock_tier1c4` | 5 | 7.16 | 0.12 | ±0.50 |
| openfaas | 8 | `openfaas_cpubound_lock_tier1c8` | 5 | 7.15 | 0.15 | ±0.50 |
| fn | 1 | `fn_cpubound_lock_tier1c1` | 5 | 13.97 | 0.15 | ±0.50 |
| fn | 2 | `fn_cpubound_lock_tier1c2` | 5 | 12.03 | 0.22 | ±0.50 |
| fn | 4 | `fn_cpubound_lock_tier1c4` | 5 | 10.57 | 0.02 | ±0.50 |
| fn | 8 | `fn_cpubound_lock_tier1c8` | 5 | 10.56 | 0.11 | ±0.50 |
| knative | 1 | `knative_cpubound_lock_tier1c1` | 5 | 14.49 | 0.15 | ±0.50 |
| knative | 2 | `knative_cpubound_lock_tier1c2` | 5 | 12.23 | 0.28 | ±0.56 |
| knative | 4 | `knative_cpubound_lock_tier1c4` | 5 | 11.64 | 0.38 | ±0.76 |
| knative | 8 | `knative_cpubound_lock_tier1c8` | 5 | 12.68 | 0.15 | ±0.50 |
| openwhisk | 1 | `openwhisk_cpubound_lock_tier1ow1` | 5 | 81.14 | 0.22 | ±0.50 |
| openwhisk | 4 | `openwhisk_cpubound_lock_tier1ow4` | 5 | 83.41 | 0.64 | ±1.28 |
| openwhisk | 8 | `openwhisk_cpubound_lock_tier1ow8` | 5 | 84.18 | 0.70 | ±1.40 |

**The three OW reference rows are not committed — CORRECTED 2026-10-02, this was 2/3 out of date.**
They live in `saqef/results/`, which `.gitignore` excludes in that repo — unlike
`saqef-paper/results/`, which is fully tracked (1435 files, 316 of them tier-1). On re-checking:
`tier1ow1` and `tier1ow4` (all 5 runs each) and `idle_probe_tier1ow{1,4}` were **already** tracked in
`saqef-paper/results/`. What was genuinely unbacked was the **c=8 pair** —
`openwhisk_cpubound_lock_tier1ow8/` and `idle_probe_tier1ow8/` — now copied and byte-verified with
`diff -r`, committed as `8349eb9` in `saqef-paper`. All three OW cells are now recoverable from git.
The OF/Fn/Kn rows never had this problem.

**Remaining risk, and it is not about these six directories.** `saqef-paper` has **no git remote** —
1435 tracked result files live on this one disk. Tracking is not durability. Until a private remote
exists, a disk failure loses the entire paper-side corpus, not just the OW legs. The medians in the
table above are reproducible from git as of `8349eb9`; that is the only durability they now have.

**Resolved 2026-10-02:** `saqef-paper` now has a private remote (`github.com/bathork1391/saqef-paper`,
branch `master`). Every `saqef/results/` directory up to that date was copied in and pushed (`2bc7f26`).
After each measurement night, copy the new `results/` directories across, commit and push.

**Read the OW rows before quoting them.** Each of the three OW cells has a first leg at
89.7–90.8 % and four later legs within ~1 pp of each other — the known OW post-deploy transient
already recorded in the 2026-08-15 sweep. The medians above are unaffected (that is what a median
is for) and the tolerances are computed from the MAD of all five legs, but **any per-leg
comparison against these rows will fail**, so rule 1 must be applied to medians only. The reference
medians also sit above the lock4 OW anchor (81.14 / 83.41 / 84.18 here vs 81.78 at lock4 c=4), so
a bridge OW leg landing near 81.8 is not a failure against these numbers; it is the transient's
absence plus the different day, and is reported as such.

**But the share is the *only* OW quantity that survives. OW throughput, latency and energy are not
citable.** Added 2026-10-02 after `remeasure_shares_tier1ow1` failed the drift gate at 73.2 → 45.4 rps
(−38.0 % against a 20 % limit), and the decay is systematic, not a bad day. All four OW legs show it,
run over run, at every concurrency, across two days:

| leg | rps run_1 → run_5 | loss | untracked host CPU |
|---|---|---|---|
| `tier1ow1` (2026-10-01) | 58.5 → 35.1 | −40 % | 114 → 278 CPU-s |
| `tier1ow4` (2026-10-01) | 61.8 → 29.7 | −52 % | 109 → 333 |
| `tier1ow8` (2026-10-01) | 57.0 → 29.1 | −49 % | 121 → 339 |
| `remeasure_shares_tier1ow1` (2026-10-02) | 73.2 → 45.4 | −38 % | 48 → 129 |

The signature, measured rather than inferred. Latency is flat *within* each run and steps up *between*
runs, so this is cumulative state, not noise. `cpu_sec.control_plane` settles at ~59 CPU-s and
`cpu_sec.function` at ~16.9 CPU-s and stays there — the stack does the **same work** over 41 s → 66 s.
What grows is host CPU that belongs to no tracked container: `host_overhead_cpu_sec` climbs
monotonically (1.17 → 1.95 cores of 8) while *total* host busy cores *fall* (4.77 → 3.11), because
wall grows 61 % while host CPU-s grows 46 %. Per request that is 16 → 43 ms of extra untracked host
CPU, which at c=1 lands directly in latency.

Ruled out by measurement, not argument: container set is flat at 26 across all five runs;
`unclassified_cpu_s` is 0.53–0.84 s and flat, so every container process is already charged to cp or
fn; keep-alive is on (no `-disable-keepalive` at `saqef_harness.py:573`) and TIME-WAIT is flat at 21;
RAPL J/CPU-s is stable at ~5.9 so frequency and thermal are not implicated. The harness's own sampler
costs 0.038 cores, and the still-running Knative stack 0.013 cores idle, against a 0.167-core idle
host — so neither the instrumentation nor the leftover stack explains 1–2 cores. Standalone OpenWhisk
is a single Java process, so nginx/etcd are not in play; the missing CPU is host-side (dockerd,
containerd or kernel).

**Corrected 2026-10-02 by direct idle measurement (`tools/ow_host_attrib_ab.py`, 17 × 2 s intervals,
nothing deployed).** The host baseline is *already* 1.078 cores with zero containers, and it decomposes:

| | mean cores |
|---|---|
| host busy | 1.078 |
| containerd | 0.240 |
| dockerd | 0.167 |
| k3s-server | 0.058 |
| softirq | 0.012 |
| containers | 0.012 |
| **unaccounted** | **0.601** |

Three consequences, and one of them undercuts the drift story above:

1. **softirq is ruled out.** The "if logs stay flat, read `/proc/stat` softirq" branch is dead —
   0.012 cores. Do not spend a cycle there.
2. **`unaccounted` is the operator, not a hidden daemon.** Per-process `/proc` deltas over the same
   window give opencode 0.49 + ptyxis 0.11 + gnome-shell 0.04 + chrome 0.01 = **0.65 cores**, which
   is the 0.601 to within a few percent. There is no unexplained host consumer; "unaccounted" on this
   box means *this GUI session and this agent*.
3. **The 15 % quiet gate cannot pass on this box with a desktop logged in.** 1.2-core ceiling against
   a 1.078-core baseline of which ~0.65 cores is the desktop itself. A quiet-gate failure on this
   machine is therefore expected and is **not** evidence about the platform. Citable legs need a
   headless box or a logged-out session; see action item (1).

Because "unaccounted" means operator, the *absolute* untracked figure in the decaying legs is not a
platform measurement, and the quiet-gate failure on this box proves nothing about OpenWhisk. But the
operator does **not** explain the drift shape on its own: untracked host CPU *grows* run over run
(1.17 → 1.95 cores), and a steady operator load would hold that flat. So platform-side growth stays
the leading explanation, with operator load as an additive offset on top of it. The two are cleanly
separable, and the way to separate them is one headless leg with zero polling compared against the
existing arms: if untracked stays ~0.6 cores and drift persists, it is the platform; if untracked
grows again, it is the measurement.

**Why the three OW rows above still say `gates_ok`.** They were written 16:54, 17:21 and 18:30 on
2026-10-01; the drift gate landed in `1b6b306` ("lock: reject runs with degraded RAPL fit or
throughput decay") at 19:15 that evening. They passed because each repeat was an independent
snapshot and nothing compared them. As **share** references they stand — the share denominator is
cp + fn container CPU only, and the growing host overhead is in neither — but rule 1 must never be
applied to OW throughput, and no OW throughput/latency/energy number from these datasets may be
quoted.

**Two action items.** (1) Establish whether the decaying-leg overhead is platform-side at all, before
looking for an OpenWhisk defect. On this box "unaccounted" *is* the desktop/agent session, so the
cleanest test is one headless leg with zero agent polling and no GUI session, compared against the
existing arms. If the drift persists headless, the platform hypothesis stands and the next probes are
the cumulative `dockerd` json-file log state (the `wsk0_*` action container IDs are byte-identical in
all five runs, so their `LogPath` files grow monotonically across repeats) and
`pidstat -u -p $(pidof dockerd),$(pidof containerd) 5` during the leg. If it vanishes, the earlier
legs are operator-contaminated and no OpenWhisk defect was ever demonstrated.
(2) The cold-JIT first leg is a separate, already-known defect that `--discard-warmup` exists for;
the re-measure session ran with `--discard-warmup 0`, so `run_1` at 88.38 % sat inside the median.
New OW legs should use `--discard-warmup 1 --repeat 6`. That fixes the share but **not** citability:
the drift gate compares first to last surviving run, so it would compare `run_2` to `run_6` and
still fail.

### 24.3 Decision rule

Use bridge medians over 5 runs, computed by the same code that computed the reference.

1. **Cell validated:** |bridge median − reference median| ≤ that cell's tolerance.
2. **Central claim confirmed:** OF < Fn < Kn at every c in the bridge data, and OW above all three.
   Note this is **stricter than the paper's claim**, which reports Fn ≈ Kn as a
   convention- and condition-sensitive pair (§5.6) and never ranks them. If `Fn < Kn` fails at some
   c while `OF < Fn` and `OW` above all three both hold, the paper's headline is *not* refuted — the
   bridge has shown the Fn/Knative near-tie is not stable, which the paper already says. Report that
   outcome as "pair ordering not stable", do not patch prose, and do not treat it as rule 2 failing
   the central claim. Reserve "stop and re-plan" for `OF` losing to `Fn` or `Kn`, or OW dropping
   below any of the three.
3. **Shapes confirmed** (operational definitions, using each cell's tolerance as the noise scale):
   - **Fn falls and stays down:** c1 > c2 > c4, and |c8 − c4| ≤ tolerance(c4). The reference c=4
     and c=8 medians differ by 0.01 pp (10.57 / 10.56), i.e. a tie, which is why the second clause
     is a tolerance band and not a strict inequality.
   - **OpenFaaS dips at c=2:** c2 is lower than *both* c1 and c4 by more than 0.50 pp. If not, §5.3's
     "unexplained c=2 minimum" becomes "did not reproduce; session state," and is reported that way.
   - **Knative turns upward at c=8:** c8 − c4 > 0.50 pp.
4. **If any cell fails rule 1:** run an interleaved A/B on that platform in **one** session,
   alternating the pre-`c05a9df` sampler and the current one (`tools/contamination_ab.py` is the
   template). If A ≠ B, the sampler disturbance is real: redo only that platform's affected legs on
   the current harness and supersede them. If A = B, the difference is day-to-day drift: report it
   as session variance, and the old data stand.
5. **Report everything.** Every bridge cell goes into `VERIFIED_RESULTS.md` through the emitter,
   pass or fail. A failed shape criterion changes the paper text; it is not grounds for a re-run.

### 24.4 Self-check: the rule against the reference itself

Run before the bridge, and recorded here so the criterion's own margins are known rather than
discovered afterwards. Every rule passes on the reference data (2026-10-02):

| criterion | reference outcome | margin |
|---|---|---|
| rule 2, `OF < Fn < Kn` at c=1/2/4/8 | holds at all four | smallest Fn→Kn gap **0.20 pp at c=2** |
| rule 2, OW above all three | OW min 81.14 > Kn max 14.49 | 66.65 pp |
| rule 3, Fn c1 > c2 > c4 | holds | \|c8 − c4\| = 0.01 pp, band ±0.50 |
| rule 3, OF c=2 dip | c1 − c2 = 1.52, c4 − c2 = 0.90 | both exceed 0.50, by 1.02 / 0.40 |
| rule 3, Kn turns up at c=8 | c8 − c4 = 1.04 | exceeds 0.50 by 0.54 |

Two of those margins are thinner than they look, and both were true before any bridge data existed:

- **The Fn→Kn gap at c=2 is 0.20 pp against a per-cell tolerance of ~0.50 pp.** The ordering is
  real in the reference but is *not* resolvable at that concurrency — which is exactly why the
  paper reports the pair as `≈` and §24.3 rule 2 is qualified above. If the bridge flips Fn and Kn at
  c=2, that is the criterion behaving as designed on a gap that was always inside the noise, not a
  regression.
- **The OF dip margin is 0.90 vs a 0.50 threshold, and Knative's is 1.04 vs 0.50.** Both clear, but
  both would be overturned by roughly half their current size. If either fails, §5.3's "unexplained
  c=2 minimum" becomes "did not reproduce" — already the stated consequence — rather than evidence
  that the sampler changed the shape.

### 24.5 What the outcomes mean for the paper

- **All of rules 1–3 pass:** the old corpus is validated against the sampler change. Cite the
  bridge as the tier-1 replication; keep lock4 / baremetal / 2-core as they are. No more box time.
- **Rule 1 or 3 fails on some cells:** follow step 4; the scope of any redo is those cells only.
- **Rule 2 fails on the grouping:** OpenFaaS losing to Fn or Knative at any c, or OpenWhisk dropping
  below any of the three, puts the central claim in question. Stop and re-plan; do not patch it in
  prose.
- **Rule 2 fails only on the Fn/Knative pair:** Fn and Knative flipping order while `OF < Fn`,
  `OF < Kn` and `OW` above all three still hold means the **pair ordering is not stable**, which the
  paper already states (§5.6). Report it that way. The paper's claim is the grouping, and the
  relevant reference numbers are the 0.18 pp lock4 gap and the 0.20 pp tier-1 c=2 gap, both inside
  their own spread — so a flip at either is the criterion working on an unresolved gap, not a
  refutation. No prose change and no re-run.

### 24.6 Amendment (2026-10-02, after the c=1 legs): RAPL FIT demoted for the bridge; resume plan

**What happened.** The c=1 legs (`bridge_tier1c1`, 11:07–11:22) ran at `425c490`, one commit past
`v9.13.1-bridge-prereg`. That commit only fixes `median_summary`'s list union (aggregation, not
attribution), and every median below is recomputed from `runs.json`, so it does not touch the
compared quantity. All gates passed on all 15 runs (worst sampling gap 0.073 s, 3000/3000
requests, no loadgen fallback, delta checks ok, no RAPL wrap, ambient 5.4–7.2 %) **except RAPL
FIT: 42.6–60.5 % on 15/15 runs**. `run_lock_session.sh` exited non-zero, the driver's `|| die`
stopped the session, and **c = 2, 4, 8 never ran**.

**Why RAPL FIT is demoted here.** 24.1 already says the RAPL gate "governs energy, not the CP/fn
share this bridge compares". The share is a ratio of CPU-seconds and RAPL never enters it. The
large errors are also not new: tier1c4 (24–43 %) and tier1c8 (11–40 %) were just as far out and
passed only because the gate was added on 2026-10-01. Likely cause, **unconfirmed**: the model
counts only fn+CP CPU, while RAPL meters the whole package, which includes dockerd, containerd,
k3s and `hey`. In the c=1 bridge runs, 27–54 % of host CPU lies outside the model. The sign was
not recoverable, because runs saved only the error %. That is fixed in `616812c`: runs now record
`e_model_j` and `e_rapl_j`, and `lock_summary.json` now records each leg's `problems`.

**Changes since the tag (none to attribution):**
- `616812c`: harness emits `e_model_j` / `e_rapl_j` / `rapl_fit_err_pct` (output fields only).
  `lock_summary.json` gains `problems` and per-run energy details.
- This amendment's commit: `run_lock_session.sh --rapl-fit-warn` moves RAPL FIT from `problems` to
  `warnings`. Every other gate stays fatal, and the session meta records
  `"rapl_fit_gate": "warn"`. `run_tier1_conc.sh` passes the flag through and adds
  `--light-from-c N` to resume without re-running (or clobbering) legs that already exist.

`git diff cec0bd9 HEAD -- saqef_harness.py` is limited to those output fields and the
`median_summary` fix. The c = 2/4/8 legs record the new `git_rev`; cite it next to this section.
**Bridge energy figures are not citable.** Only the shares are compared.

**Preliminary c=1 observation. Not adjudicated: the rule is applied after all cells exist.**

| platform | reference median | bridge c=1 median (runs) | Δ | tolerance |
|---|---|---|---|---|
| openfaas | 7.78 | 4.94 (4.69 4.62 4.94 4.99 5.40) | −2.84 | ±0.50 |
| fn | 13.97 | 9.32 (9.32 8.84 9.46 8.94 9.40) | −4.65 | ±0.50 |
| knative | 14.49 | 10.77 (10.02 9.89 11.13 10.77 10.84) | −3.72 | ±0.50 |

At c=1, rule 2's grouping holds (OF < Fn < Kn), and all three cells are far outside rule 1. They are
lower, which is the direction expected if the old sampler's own CPU inflated the CP bucket. Rule 1
failing means step 4 (the interleaved A/B) applies. Do not update paper numbers from this table.

**Resume command — SUPERSEDED by §24.7, do not run.** The adjudication below changed the plan;
this command is kept verbatim so the record shows what was decided before the rule was applied.
See §24.7 for what runs instead.

```bash
bash tools/run_tier1_conc.sh --stamp-prefix bridge_ --skip-ow --light-from-c 2 --rapl-fit-warn --dry-run   # check stamps
bash tools/run_tier1_conc.sh --stamp-prefix bridge_ --skip-ow --light-from-c 2 --rapl-fit-warn             # ~1 h (c=1 took ~20 min)
```

The final aggregation reads `bridge_tier1c1` from disk, so the table covers c = 1/2/4/8.

**Next steps, in order — SUPERSEDED by §24.7. Kept for the record, NOT the plan:**
1. ~~Run the resume command above. Then apply rules 1–3 to all twelve OF/Fn/Kn cells.~~
2. ~~For every cell that fails rule 1, run the interleaved A/B from 24.3 step 4 in **one** session~~
   ~~(`tools/contamination_ab.py` as template): old sampler vs current. A ≠ B means the old sampler~~
   ~~inflated the shares; supersede those cells. A = B means day drift; report both.~~
3. OpenWhisk bridge cells (c = 1/4/8): only after step 2, and only if its outcome makes them
   necessary.
4. Energy: using the new `e_model_j` / `e_rapl_j`, decide what RAPL FIT should compare (fn+CP
   model vs. a host-CPU model) before any energy figure is cited again. tier1c4/c8 also fail the
   current gate. Until this is settled, energy is model-only.
5. Then new experiments instead of more replication. Candidates: an I/O-bound or memory-heavy
   function (the I/O variant exists but is undeveloped), cold-start / scale-from-zero
   (Knative/OpenWhisk), and bursty arrivals instead of a steady rate.

### 24.7 Amendment (2026-10-02, after adjudicating c=1): rule 1 fails, bridge STOPPED, re-measure instead

24.6 left the c=1 cells "not adjudicated" and proposed finishing the bridge. Applying the rule to
the cells that exist reverses that plan. **This section supersedes 24.6's resume command and its
"Next steps" list.** 24.6's factual record of what the legs did is unchanged and still stands.

#### 24.7.1 Rule 1 is adjudicated: it fails on all three c=1 cells

The rule does not require all cells to exist. Rule 1 is a per-cell predicate — "cell validated: |
bridge median − reference median| ≤ that cell's tolerance" — and each cell is decidable on its own
five runs. The twelve unrun cells do not make the three decided ones undecidable; they only add
more of the same verdict.

| cell | reference median | bridge median | Δ | tolerance | verdict | miss |
|---|---|---|---|---|---|---|
| openfaas c=1 | 7.78 | 4.94 | −2.84 | ±0.50 | **FAIL** | 5.7× |
| fn c=1 | 13.97 | 9.32 | −4.65 | ±0.50 | **FAIL** | 9.3× |
| knative c=1 | 14.49 | 10.77 | −3.72 | ±0.50 | **FAIL** | 7.4× |

The run distributions do not touch: openfaas 7.09–7.92 vs 4.62–5.40; fn 13.78–14.21 vs 8.84–9.46;
knative 14.04–14.64 vs 9.89–11.13. **15 of 15 bridge runs sit below their reference cell's minimum.**
This is not drift. Drift at the observed scale would need ~5–9× the cell's own noise.

**Rule 2 holds.** openfaas 4.94 < fn 9.32 < knative 10.77 — the grouping, which is the paper's
actual claim, survives. What moved is the *level* of every share, not the order. That distinction
governs everything below: the paper's central claim is not in question; its absolute numbers are.

#### 24.7.2 The gap is in the data, not in the attribution code

This closes off the cheapest possible explanation before acting on it. `c22dff9` / `34b4f26` /
`908bece` (§19–§22) changed how CPU is attributed to the load window, so the reference datasets
might simply be mis-attributed rather than differently measured. They are not:

```
$ python3 tools/legacy_reattribute.py results/openfaas_cpubound_lock_tier1c1 --verify
legs scanned      : 5
reconstruction ok : 5
verify failed     : 0
reconstruction err: median 0.0305%  max 0.0697%
share shift unclipped : median 0.0012 pp  max 0.0035 pp
```

Re-attributing the old data with today's `sample_totals()` reproduces its stored totals to 0.03%
and moves the share by **0.0012 pp**. A 2.84–4.65 pp difference cannot come from a 0.001 pp
code change. The sampler produced different measurements.

Corroborating, from the same legs: `host_overhead_cpu_sec` fell ~36.0 → ~15.0 CPU-s over a ~18 s
window (~1.5 cores of host CPU, not the ~3 cores `c05a9df` estimated for Knative — the honest
figure is the one measured), and `host_saturation_pct` fell 35.9 → 23.5. The control-plane bucket
is the term that moved (1.62 → 0.92 CPU-s for openfaas c=1, −43%) while the function bucket barely
moved (19.41 → 17.76, −8.5%), which is exactly the shape a shared-host contention removal predicts.
For scale: runbook §1 records a ~2.8-core background agent shifting this metric by 0.3–1 pp. The old
sampler burned comparable CPU *continuously, for the whole window*, and moved it 5–9× further.

#### 24.7.3 Decision: stop the bridge, re-measure tier-1 on the current harness

The bridge is **stopped after c=1**. The remaining twelve OF/Fn/Kn cells and the three OW cells are
**not** run under the `bridge_` prefix. Reasoning:

1. **The bridge has answered its question.** It existed to detect whether the old sampler perturbed
   the system. It did, by 5–9× the pre-registered tolerance, with no distribution overlap. Running
   twelve more cells re-confirms a known verdict at ~1 h of box time.
2. **24.3 step 4's contingency has no tool.** It prescribes an interleaved old-sampler-vs-current
   A/B "in one session", naming `tools/contamination_ab.py` as a template. That tool exists but
   A/Bs *background load*, not *sampler versions*. Building it is unbudgeted work, and it would
   be needed for a verdict 24.7.2 has already reached by a cheaper route.
3. **Re-measurement yields citable data; bridge completion does not.** 24.3 step 4 says a
   confirmed sampler effect means "redo only that platform's affected legs on the current harness and
   supersede them" — i.e. a full re-measurement anyway, *after* the bridge. Skipping the bridge
   spends ~3 h on data that is superseded by construction rather than ~3 h on data that can be
   cited.
4. **OW was to be run last, and is the cell most at risk.** OW's 81–84% share is the most
   control-plane-dominated number in the paper, so it is the most exposed to exactly the mechanism
   above. It has never been measured on the current sampler. It should be in the re-measurement,
   not deferred behind an experiment whose outcome no longer branches.

#### 24.7.4 Supersession scope is the whole corpus, not tier-1

24.5 assumed the failure would be scoped to "some cells". It is not. **Every dataset measured before
`c05a9df` shares the contaminated sampler**, so every absolute share in the paper is provisional:
the lock4 headline table (openfaas 7.58 / fn 11.29 / knative 11.47 / openwhisk 81.78), the
core-count experiment, the contamination A/B and the ablations. The three OW reference rows in
24.2 were taken under it too.

This is stated now, before the re-measurement, so the scope cannot be argued afterwards in either
direction. Two consequences:

- **The grouping claim is safe; the levels are not.** Every pre-`c05a9df` set has the same sign and
  roughly the same relative size of shift (openfaas ×0.63, fn ×0.67, knative ×0.74 at c=1), so any
  ordering asserted across the corpus is preserved. OW's level is the open question.
- **`cp_dynamic_share_pct` is unaffected by idle-w, but energy and carbon are not.** The share is a
  ratio of CPU-seconds; `energy_J` multiplies by `idle_w`, and idle watts are a property of the box
  state that the sampler change perturbed (host overhead fell ~21 CPU-s). **Every energy, carbon
  and gCO2/invocation figure must be treated as void**, independently of the share outcome. Idle-w
  must be re-calibrated rather than carried over **in any session whose energy is cited**; §24.7.5
  records one deliberate, scoped exception — a shares-only re-measurement that inherits the lock4
  medians and inherits exactly the same voidness. Recalibration stops being optional the moment
  energy is on the page.

The old corpus is **retained, not deleted**, and relabelled pre-`c05a9df`/instrument-contaminated so
a reviewer can see both series. Superseded numbers are struck in `VERIFIED_RESULTS.md`, never
silently overwritten.

#### 24.7.5 Protocol for the re-measurement (freeze before running)

The mistake that cost `bridge_tier1c1` was running first and adjudicating later. Freeze these
before the first leg:

- **Revision.** **`v9.14.1-remeasure`** — the tag the re-measurement runs under, cited by name and never
  by SHA. **One command runs the whole session**, and its step 1 is this gate:
  ```bash
  bash tools/remeasure_shares.sh            # pre-flight, dry run, confirm, then ~2.5-3 h
  bash tools/remeasure_shares.sh --check    # pre-flight only, measures nothing
  ```
  Underneath, two conditions, because either failure alone makes the whole session uncitable:
  ```bash
  git status --porcelain                  # must be empty
  git diff --name-only v9.14.1-remeasure..HEAD   # must be empty, or list ONLY:
  #   TROUBLESHOOTING_RUNBOOK.md     -- where the protocol is written
  #   tools/remeasure_shares.sh      -- the pre-flight wrapper doing this check
  ```
  Neither is on the measurement path, so neither can move a number. Anything else appearing in that
  diff — `tools/run_tier1_conc.sh`, `tools/run_lock_session.sh`, `saqef_harness.py`, `tests/` — is a
  **hard fail with the offending files listed**, not a warning: that is measurement code drifting past
  the freeze, which is the exact failure that made the last campaign uncitable.

  **A SHA quoted here goes stale** the moment a runbook-only commit lands — which happened twice while
  writing this section (a trailer strip rewrote 7 commits, and the hash remap that followed was itself
  a commit). Hence the tag name, and hence the second check.

  Note the deliberate absence of `git describe --tags --exact-match`: it passes only while the tag sits
  *on* the tip, so a single documentation commit would break the pre-flight for a session that is in
  fact perfectly citable. `v9.14.1-remeasure` is therefore already behind the tip by exactly the
  documentation and pre-flight commits that follow it, and that is the state this protocol is written
  for. **The invariant is not "tag equals tip", it is "nothing between the tag and HEAD touches
  anything a measurement depends on"** — which is what the `diff --name-only` allowlist asserts, and
  what the empty `git status` guarantees for the working tree.

  The driver also refuses to start on a dirty tree independently (provenance gate, `--allow-dirty` to
  override for exploratory work). All 15 `bridge_tier1c1` legs recorded `git_dirty=true` and that
  alone made them uncitable, with no measured number wrong.

  `v9.14.1` exists because this section was amended **after** `v9.14-remeasure` was frozen — the
  idle-w rule, the acceptance rule and the run command all changed — and amending a pre-registration
  in place would destroy the only thing the tag was for. The old tag stays where it is, so a reader
  can see what was frozen when and what was corrected before any leg ran. The same rule applies to the
  corrections after it: documentation and pre-flight only, so the tag is left alone and the gap is
  verified rather than erased.
- **Stamps.** `remeasure_shares_` — distinct, as it must be (never bare `tier1c<N>`, or pre- and
  post-`c05a9df` datasets can be confused or overwritten), and it *names the scope*, so nobody can
  mistake a void-energy run for a fully citable one without opening it. **One command**, which does
  the pre-flight, the box-state snapshot, a dry run and the session, in that order:
  ```bash
  bash tools/remeasure_shares.sh
  ```
  The underlying invocation, for the record and for reproducing a single leg by hand:
  ```bash
  bash tools/run_tier1_conc.sh --stamp-prefix remeasure_shares_ --rapl-fit-warn --dry-run
  bash tools/run_tier1_conc.sh --stamp-prefix remeasure_shares_ --rapl-fit-warn
  ```
  Both flags are load-bearing. **`tools/run_tier1_quiet.sh` is not a substitute** — it invokes the
  driver as bare `bash "$DRIVER"` with no arguments, i.e. bare `tier1c<N>` stamps and no
  `--rapl-fit-warn`, which is a different protocol from this one: stamps that can collide with the
  pre-`c05a9df` datasets, and RAPL FIT gating rather than warning.

  The wrapper snapshots box state because §24.7.7's whole point is that `governor` is recorded per run
  but EPP is recorded nowhere — so the one setting that cannot be recovered from the data is the one
  worth writing down *before* the run. It also pins the power profile to `performance`, refuses to
  start on battery, and diffs the thermal zones afterwards.

  Output lands in `results/<platform>_cpubound_lock_remeasure_shares_tier1c<N>/`, the medians in
  `results/lock_session_remeasure_shares_tier1c<N>/lock_summary.json`, and the per-leg background
  probe in `results/idle_probe_remeasure_shares_tier1c<N>/<platform>/`. `run_lock_session` refuses
  to clobber, so a re-run needs a fresh prefix rather than an overwrite — and because a new prefix is
  a protocol change, it needs a new tag.
- **Acceptance rule, decided in advance.** This campaign's job is to replace uncitable bridge data
  with citable data, **not** to pass a test. So there is exactly one pass/fail in the protocol, and it
  is mechanical:
  1. **Is the cell citable?** Clean tree (`git_dirty=false`), revision named, no `LOADGEN FALLBACK`,
     ambient < 15 %, `rapl_fit_gate=warn` recorded. A cell failing any of these is void however good
     its numbers look. That single rule is what made all 15 `bridge_tier1c1` legs uncitable, with no
     measured number wrong.
  2. **Frozen questions for the new data.** Report the answers; do not score them:
     - Does the grouping hold at every concurrency (OpenFaaS < {Fn, Knative})?
     - What is OpenWhisk's level on the current sampler? It has never been measured there, and
       §24.7.4 makes it the one genuinely open number in the paper.
     - Do the three §24.3 shape claims still describe what is seen — Fn falling and staying down,
       OpenFaaS dipping at c=2, Knative turning up at c=8? Recorded as descriptions **of this
       corpus**, not as pass/fail. They are the claims the bridge could not confirm, so re-asking
       "do they hold" would re-import exactly the post-hoc judgement §24.7 exists to remove.
  3. **No practical-equivalence margin is stated here, deliberately.** TOST equivalence is in the
     repo (`saqef_harness.py`, `tests/TestTier1StatsHygiene`) and OW has already failed it once
     (`test_tost_rejects_the_openwhisk_c1_case`) — but a margin chosen at this moment, with the
     pre-`c05a9df` corpus already visible, is chosen knowing the answer. That is precisely the
     failure mode §23.2 already records (a 3-sample MAD deciding a bias). If an equivalence claim is
     wanted, its margin must be fixed from an independent basis — §24.2's QoS floor is the obvious
     candidate — **before** the data it judges, in its own pre-registration.
  4. **Old-vs-new is descriptive.** §24.7 has already adjudicated c=1, so scoring new data against
     old and calling it pass or fail is circular: the comparison is already decided. Both series are
     reported side by side, and superseded numbers are struck in `VERIFIED_RESULTS.md`, never
     overwritten (§24.7.4).
- **Idle-w is inherited here, on purpose, and energy is therefore void.** This campaign's driver
  passes `--skip-idle-calib` with the lock4 N=5 medians (OF 4.235 / Fn 4.249 / Kn 5.739 / OW 4.882)
  instead of recalibrating per leg as §19 requires. That is a **recorded deviation with a stated
  scope**, not an oversight, and it is the only point where this protocol departs from §19:
  - `cp_dynamic_share_pct` and CP/fn per-invocation are ratios of CPU-seconds. They do not consume
    idle-w, so every citable output of this campaign is unaffected by the inheritance.
  - **Every `energy_J`, carbon and gCO2/invocation figure this session produces is void**, struck on
    sight, exactly as §24.7.4 rules for the pre-`c05a9df` corpus. The stamp prefix records the scope
    (`remeasure_shares_`) and the driver prints `shares only; energy NOT citable` on every leg, so the
    constraint travels with the data instead of depending on someone reading this section first.
  - Recalibration is deferred to one separate §19 idle-w calibration, run with the platform stack
    up, before any energy figure is cited again. Dropping `--skip-idle-calib` here would cost
    ~25–30 min of box time and buy no citable number, because §24.7.6 has not settled what energy
    should even be compared against yet.
  - **No mid-session re-anchoring.** Within the session the medians are fixed. Re-anchoring partway
    through would make the per-leg background probe incomparable across legs, which is the one
    cross-check this campaign actually retains.
- **RAPL FIT stays demoted to a warning** (`--rapl-fit-warn`) for share sessions. It is uninformative
  here: it fails the *reference* data too (tier1c4 24–43 %, tier1c8 11–40 %), so it cannot
  discriminate. Energy stays model-only until 24.7.6 settles what it should compare.

#### 24.7.6 RAPL FIT: what to do with the 42–60 % error

Not a regression — the same magnitude is present in the reference corpus — but it must be explained
before any energy figure is cited again. 24.6's hypothesis stands and is now testable rather than
speculative: the model counts fn+CP CPU, RAPL meters the whole package, and 27–54 % of host CPU
falls outside the model. `616812c` now records `e_model_j` and `e_rapl_j` per run, so **the sign and
the ratio are recoverable from the first five runs of the re-measurement** — settle this from
`run_1`, not after the campaign. Until then energy is model-only, as 24.6 already states.

#### 24.7.7 Provenance gaps found while adjudicating

- **The reference datasets carry no `harness.git_rev` at all** (the field predates them). So 24.1's
  "the two reference values and the bridge values are computed by identical `saqef_harness.py`"
  cannot be *verified* for the reference side, only asserted. It is also imprecise: the bridge
  isolates `c05a9df` *plus* §19–§22, which 24.7.2 bounds at 0.0012 pp. New runs are stamped; these
  are not.
- **CPU frequency is uncontrolled in both corpora — and partly recorded.** `governor=powersave` is
  recorded in every run, and the delivered clock swings from 517 to 3800 MHz across runs *and within
  them*: one `bridge_tier1c1` leg recorded `env.freq_mhz_before` 3299.8 → `env.freq_mhz_after` 707.8.
  No gate reads it. It is *not* the explanation for 24.7.1 — it is equally variable on both sides — but
  it is an uncontrolled variable that inflates the noise floor against which every tolerance in 24.2
  is judged.

  The finer-grained setting is a **blind spot, and one this section previously got wrong by asserting
  it as fact.** Under the `intel_pstate` driver the `governor` string is only a passive hint; the real
  bias lives in `cpufreq/energy_performance_preference` (EPP). **`governor` is the only frequency
  control any run records** — checked across all 127 `summary.json` files in `results/`, whose `env`
  blocks contain `cpu_count`, `freq_mhz_after`, `freq_mhz_before`, `governor`, `interarrival_ms`,
  `loadgen`, `loadgen_fallback`, `loadgen_requested`, `sampler`, `target_qps` and nothing else. No EPP
  value is recorded in either corpus or in the bridge, and the file name for it is not even a distinct
  grep hit. So any statement about what EPP was during the reference runs is an **assumption, not a
  record**. This box reads `balance_performance` today under the `balanced` profile; that is today's
  value and nothing more — it is **not** evidence about the reference runs, and the fact that it did
  not even hold steady within one working session (it also read `balance_power`) is the reason not to
  read anything into it. §24.7.7 previously wrote that the reference corpus "was collected under
  `balance_power`"; that was an assumption dressed as a measurement, and it is corrected here.

  Two corrections to what this section previously recommended. First, **"recording per run" is partly
  done**: `saqef_harness.py` writes `env.freq_mhz_before` / `env.freq_mhz_after` / `env.governor`
  into every `summary.json`, verified in `results/fn_cpubound_lock_bridge_tier1c1/`. What is missing is
  EPP and a gate — not the frequency fields. Second, **pinning is deliberately not done for this
  campaign.** This box runs the `intel_pstate` driver, where the real knobs (`intel_pstate/no_turbo`,
  `min_perf_pct`, `cpufreq/energy_performance_preference`) are root-owned, so pinning needs `sudo`.
  The move is also questionable *here* even with root, though the honest reason is weaker than the one
  previously given: we cannot show the reference corpus ran under this EPP, so we cannot show that
  changing it would break comparability — only that we do not know either way. What *is* certain is
  that the reference corpus and this campaign were **never recorded as matching**, and that switching
  a root-owned machine setting between the two series would introduce an undeclared protocol
  deviation — the exact failure that made `bridge_tier1c1` uncitable. Pins belong in a dedicated
  like-for-like session that re-measures a reference leg *and* a new leg under the same pinned state,
  so both sides of any comparison demonstrably share it. For this campaign the discipline is narrower
  and free: **snapshot the box state before the first leg** (recorded in
  `results/remeasure_shares_box_state/cpufreq.txt`) and **report each cell's `freq_mhz_before`/`after`
  swing beside its numbers**, so a swing-driven outlier is visible rather than silently absorbed into
  a median.

- **The box is a laptop, and two daemons own the frequency policy.** This is the part that actually
  governs whether the re-measurement is repeatable, and it was not written down until now. The host is
  a **Dell Latitude 3420** (`chassis_type=10`, notebook), running on AC, with the
  **`power-profiles-daemon`** (currently profile `balanced`, EPP `balance_performance`) and
  **`thermald --adaptive`** both active.
  - `power-profiles-daemon` is the thing that *writes* `energy_performance_preference`. Nothing in
    this repo records it, and it is not stable over the life of the box: it read `balance_power` and
    `balance_performance` within a single working session. Leaving the profile at whatever the daemon
    last chose means a variable the protocol needs fixed is owned by software the protocol never
    records. **Set the profile deliberately before the run** (`powerprofilesctl set performance`) so
    the value is known and matches the snapshot, rather than inherited by accident. This needs no
    `sudo`, unlike the `intel_pstate` knobs.
  - `thermald --adaptive` throttles on a laptop chassis with no sustained-load cooling budget.
    `x86_pkg_temp` was already at **67 °C at idle** before the session began, with `TCPU` 63 °C and
    `TSKN` (skin) 59 °C. A 2.5–3 h run with a 420 s × 5 OpenWhisk leg will very plausibly cross the
    throttle threshold partway through, and delivered frequency will then fall *within* the campaign —
    which is the same uncontrolled variable as above, arriving from the thermal side and recorded
    nowhere.
  - What follows for the numbers: throttling does not obviously damage `cp_dynamic_share_pct`, since
    that is a ratio of CPU-seconds and both platforms are throttled together — but it degrades
    *absolute* figures and widens the noise floor, so it is one more reason §24.7.4's energy void
    stands and one more reason the freq swing must be reported per cell. It is **not** a reason to
    skip the campaign: the shares are the citable output and they are the robust one.
  - The honest summary is that this box is a laptop being used as a bench, and no amount of runbook
    text makes it a server. A machine-state pin plus the per-cell swing report is the best available
    mitigation; a proper fix is a dedicated, thermally stable, pinned host, which is a hardware
    decision rather than a protocol one.


### 24.8 Final corpus — pre-registered (written 2026-10-02, before any `final_` data)

Supersedes 24.7.5 as the protocol for citable numbers. `remeasure_shares_*` stays on disk as the
record of what the desktop-session, unbounded-log condition produced. It is never mixed with `final_*`.

#### 24.8.1 What changes, and why each change is justified by data already in hand

| change | justification |
|---|---|
| dockerd `json-file` `max-size: 64k`, `max-file: 1` | `owhead1` A/B, same session: unbounded logs give 67.9 → 35.9 rps over 6 repeats with dockerd at 1.24 cores. Truncated logs give 90.6–93.7 rps flat with dockerd at 0.46 cores. Log volume is ~219 B/activation, ~320 KB per run per action container. A 1 MB cap would bind only ~3.2 runs into a leg, so runs 2–4 would stay in the ramp. 64 KB binds after ~0.2 runs, which reproduces the truncate arm's condition natively. |
| applies to **all** platforms | `k3s server --docker`: Knative/k3s pods are dockerd containers too. A daemon-level cap cannot be scoped to OW, so all four platforms are re-measured under it. |
| headless (`multi-user.target`), no agents | §24.2: ~0.65 of the 1.078-core idle baseline is the desktop + agent. Contention of ~1.5 cores moved shares 3–5 pp (24.7.2). |
| idle_w recalibrated in-session (5 states × 3 × 60 s) | idle_w is measured per stack state and enters every energy figure as `idle_w × wall`. Leaving the desktop changes it, so inherited values would be wrong by an unknown offset. |
| OW `--repeat 6 --discard-warmup 1` | OW run_1 cp CPU-s is 2.1–2.4× steady in every OW leg (post-deploy JIT/classload), and the light platforms do not show it. The drift gate then compares run_2 to run_6 (`run_lock_session.sh` drift block uses the post-discard list). |
| `psys` recorded (`e_psys_j`, `psys_wrap`, `psys_status`), 1 Hz `energy_trace.csv` per run, JVM per-thread CPU on OW legs | Reporting only; none enters a gate or the share. The trace makes the within-run power-vs-load check (25.6) possible offline. |

This is a deliberate, non-default deployment configuration and the paper's setup section must state it
as such, with the A/B as the reason. Stock dockerd has no rotation. With it, OW throughput is not
stationary, so it is not measurable as a single number.

The same applies to the OW throttle: `platforms/openwhisk.py:57-72` raises the standalone's default
60 invocations/min/namespace limit (which 429s 40 % of calls at c=4) via JVM system properties. That is
also a stated deviation from default.

#### 24.8.2 Acceptance (per leg; evaluated mechanically from `lock_summary.json`)

1. All existing gates pass (sampling gap, completeness, no loadgen fallback, host_plausible, delta
   check, quiet gate per run).
2. Drift (first usable run → last) ≤ 20 % throughput loss. For OW that is run_2 → run_6.
3. RAPL FIT is **warn-only** (`--rapl-fit-warn`). It measures |e_model − e_rapl| / e_rapl
   (`saqef_harness.py`, the `rapl_validation` line), i.e. the disagreement between RAPL and the
   **retired** 3.5 W/core model (§25.1). It says nothing about RAPL's own quality, so it cannot gate a
   RAPL-based energy figure.

Predictions, stated now:
- **P1.** OW c=1 runs 2–6 are flat (drift < 20 %) at ~90 rps (owhead1 truncate: 90.6–93.7).
- **P2.** OW cp share stays in its previous 81–84 % band. The share never contained dockerd CPU
  (§24.2), so bounding logs should not move it. A move > 3 pp would mean the logs were reaching cp
  CPU, and that must be explained before citing.
- **P3.** Ordering OpenFaaS < {Fn, Knative} < OpenWhisk holds at every concurrency. The Fn-vs-Knative
  order is **not** predicted: in `remeasure_shares_` it flips at c=1 (12.73 vs 11.12).

A failed prediction is reported, not tuned away.

#### 24.8.3 Stop rule

A leg that fails acceptance is retried once (`_r2`) inside the same session, automatically. A leg that
fails twice is reported as failed with its reason. It is investigated further **only** if it breaks
P3's OpenFaaS-lowest / OpenWhisk-highest claim. After this corpus, no further re-measurement on this
machine. Open questions become stated limitations or new, separately pre-registered experiments.

Comparing `final_*` to `remeasure_shares_*` is **descriptive only**. The two differ in three factors at
once (log cap, headless, in-session idle_w), so no difference between them may be attributed to any
one factor. The cap's effect on OW is isolated by `owhead1` (same session, one factor), not by this
comparison.

#### 24.8.4 How it is run

`tools/run_final.sh` drives it. Pre-flight hard gates (abort, nothing measured):
- root
- `hey` resolves to the corpus build, checked by sha256 (`/usr/local/bin/hey -> /root/go/bin/hey`,
  `952be8d7…`). PATH is pinned to root's. `~/go/bin/hey` is a different build (`6666d178…`) and must
  not be picked up. See 25.6.
- daemon.json cap present, dockerd restarted after it, and a live `k8s_*` container carrying
  `max-size=64k`
- knative-serving Ready
- power profile `performance` with uniform EPP
- no `final_*` results present
- measurement-path code committed
- no graphical session, no agent

The job waits up to 20 min for the operator to leave the desktop and quit agents. It does not race a
countdown. Then it calibrates (`final_calib`), runs 12 light legs and 3 OW legs, appends each verdict
to `results/final_session/checkpoint.tsv` as it lands, snapshots box state before and after, and
restores the desktop. Commands are in §24.8.5.

#### 24.8.5 Operator commands

```bash
# 0. commit first (pre-flight refuses uncommitted measurement-path code)
# 1. log cap (create; the file does not exist today)
sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "64k", "max-file": "1" } }
JSON
sudo systemctl restart docker          # restarts k3s pods too (k3s --docker); they come back on their own
# 2. wait until Ready, then the pre-flight (expect ONLY desktop/agent problems)
sudo k3s kubectl get pods -n knative-serving
sudo bash tools/run_final.sh --check
# 3. launch as a system unit (survives leaving the desktop), quit every agent/app, drop the desktop
sudo systemd-run --unit saqef-final \
  systemd-inhibit --what=sleep:idle:handle-lid-switch --why="SAQEF final corpus" \
  bash /home/imran/faas-work/SAQEF/saqef/tools/run_final.sh
sudo systemctl stop display-manager   # NOT isolate multi-user.target: that also kills saqef-final
# 4. ~2 h later the desktop comes back by itself. Then:
systemctl status saqef-final
column -t -s $'\t' results/final_session/checkpoint.tsv
```

## 25. Supervisor review questions (2026-10-02) — what the data already answers

### 25.1 Is 3.5 W per busy core trustworthy? No — measured, it is not a constant

`P_BUSY_CORE_W = 3.5` (`saqef_harness.py:50`) is an assumed literature value, never calibrated on this
box. The data to calibrate it already exists: marginal W per busy core-second =
`(e_rapl_j − idle_w × wall_s) / host_cpu_sec`, per run, from the `remeasure_shares_*` and `owhead1_*`
legs (all RAPL package-0):

| platform | c=1 | c=2 | c=4 | c=8 |
|---|---|---|---|---|
| openfaas | 6.17 | 5.27 | 5.17 | 3.75 |
| fn | 6.08 | 6.49 | 4.57 | 3.21 |
| knative | 4.98 | 6.25 | 4.60 | 3.61 |
| openwhisk (c=1, owhead1 baseline / truncate) | 2.75 / 1.96 | | | |

So the effective figure spans **1.7–7.7 W**. It falls with concurrency (more busy cores share one
turbo/power budget, so each runs at a lower frequency and voltage) and differs between legs (OW ~2 W, the others 5–6 W at c=1; per 25.5 this is clock state, not a
per-platform efficiency). To the supervisor's sub-questions:
it **does** change with workload type (IPC, memory stalls, vector units), with core type and SMT (a
hyperthread is not a core; P- vs E-cores differ several-fold), with hardware generation, process node,
TDP and temperature (leakage), and with DVFS state. It does **not** change with the energy *source*:
that changes carbon per joule (`ci`, gCO2/kWh), not watts. `PUE = 1.15` is a data-centre facility
factor and has no meaning on a laptop.

Consequence: `e_model_j` is CPU-seconds × an uncalibrated constant, contributes no evidence beyond
CPU time, and is not cited. Energy claims come from RAPL directly (25.2).

### 25.2 What energy *can* be claimed

- **Per-platform dynamic energy, measured.** Legs run one platform at a time, so
  `e_rapl − idle_w × wall` is the package energy that platform's leg added. No W/core model needed.
- **Standing (idle) control-plane power, measured.** The per-platform idle calibration is already
  model-free RAPL: knative 5.74 W vs openfaas 4.24 / fn 4.25 / openwhisk 4.30 W
  (`lock_summary.json idle_w_by_platform`). Knative's deployed-but-idle control plane costs ~1.5 W
  more. This is a real result.
- **CP vs function split of dynamic energy** needs an attribution rule (proportional to CPU time).
  Report it as attributed, never as measured.
- **`psys`** (`/sys/class/powercap/intel-rapl:1`) is the platform-level domain. It cannot be broken
  down by itself any more than package can; record it next to package as a bound on what package
  misses (DRAM, PCH, etc.), not as a separate attribution.

### 25.3 Success rate: measured, not assumed — but never stressed

Not assumed. Every request is checked: `hey` rows count as successes only on an HTTP `2xx` status
(`saqef_harness.py:631`), the Python fallback only on a completed response within the 10 s timeout
(`:525-528`). `availability = successes / requests` is recorded per run, and non-2xx responses print a
warning. In every leg so far it is **1.0 (3000/3000)**.

That 100 % is a property of the test conditions, not of the platforms. Load comes over loopback (no
packet loss), it is closed-loop (at most `c` requests in flight, so the platform is never pushed past
capacity), and no faults are injected. The paper must state this as a limitation: the results describe
the control-plane cost of *successful* steady-state invocations below saturation. They say nothing
about how each platform behaves on drops, timeouts, retries or overload, and that behaviour is
platform-specific (OW and Knative queue and retry inside the control plane; OpenFaaS sync calls fail
fast). The way to test it later is fault injection (`tc netem loss` on the docker bridge) or open-loop
overload. Both are new experiments, not fixes to this one.

### 25.4 Desktop session vs headless

All earlier legs ran with the GNOME desktop logged in (~0.65 cores idle, §24.2) and no agents. A
headless final session changes that condition. The share is cp/(cp+fn) container CPU, so the desktop
enters only through contention, and §24.7.2 showed contention of ~1.5 cores moved shares by 3–5 pp.
So headless *can* move numbers. It is acceptable only because the final corpus re-measures **all four
platforms** under the same condition: nothing headless is ever compared with anything desktop. It is
also the more representative condition, since production hosts are headless.

### 25.5 Corrections and additions after review (2026-10-02)

- **Retracted: "OpenWhisk's Java control plane draws ~2 W per core".** The W/core figure falls
  monotonically with concurrency on all three light platforms. That is DVFS (more busy cores sharing
  one package power budget at a lower clock), not a platform property. The per-cell numbers in 25.1
  show that 3.5 W is not a constant. They are **not** per-platform efficiencies and do not go in the
  paper as such.
- **Why OW's energy looks low per core-second but is high per request.** RAPL dynamic energy per
  request at c=1: OpenFaaS 0.065 J, Fn 0.064 J, Knative 0.065 J, OW 0.084 J (truncate) / 0.186 J
  (baseline, decaying). OW burns 128–216 host CPU-s per 3000 requests against 31–38 for the others.
  Much of that is low-intensity work at a low clock, so each core-second is cheap, but there are 4–7×
  more of them. **Per request, OW costs ~1.3× (bounded logs) to ~2.9× (unbounded) the energy of the
  others.** Per request is the figure the paper reports.
- **psys vs package.** Both are RAPL domains read the same way. `intel-rapl:0` (package) covers CPU
  cores + uncore + iGPU. `intel-rapl:1` (psys) is the platform domain reported by the platform power
  controller, so it includes package plus more of the SoC/board. It still excludes the display and
  PSU losses. Neither can be broken down by itself. Per-platform figures come from differencing
  (legs run one platform at a time, minus that state's idle_w), and the CP/fn split is an attribution.
  psys is recorded as the wider bound, not attributed.
- **CP-vs-fn energy "in proportion to CPU time"** is a CPU-time ratio expressed in joules. It must be
  labelled as such, never as a measured energy split.
- **RAPL is not "failing a third of runs".** The >15 % FIT flag is model-vs-RAPL disagreement (24.8.2,
  item 3), not a RAPL defect.
- **Success-rate regime (adds to 25.3).** Every citable leg is closed-loop: `hey -n -c` with no `-q`
  (the rate flag exists at `saqef_harness.py` `run_hey` and is never passed by any driver). Offered
  load is at most `c` outstanding requests and self-limits to what the platform retires, so overload
  is unreachable by construction. 100 % success holds even at 90–92 % host saturation. That is real,
  but only for this regime. The open-loop arm (`--qps` above measured capacity, drops/429s/timeouts
  as the measurement) is a separate experiment and is pre-registered only as a stated limitation here.
- **JVM thread → subsystem map (pre-registered before any `final_tier1ow*` data).** Thread names
  are the 15-char pthread names in `results/final_session/jvm_threads_*.csv`:

  | subsystem | thread-name pattern |
  |---|---|
  | JIT | `C1 CompilerThre*`, `C2 CompilerThre*` |
  | GC / VM | `GC Thread*`, `G1 *`, `VM Thread`, `VM Periodic*` |
  | actor system (controller + invoker logic, HTTP) | contains `akka` or `dispatcher` |
  | thread pools (blocking I/O, clients) | `pool-*`, `ForkJoinPool*` |
  | docker CLI spawned by the invoker | the `<children>` row (reaped-child CPU) |
  | other | everything else |

  If "other" exceeds 20 % of JVM CPU over a leg, the breakdown is reported as incomplete rather than
  re-mapped after the fact. Inter-container breakdowns (Knative activator / queue-proxy / autoscaler;
  OpenFaaS gateway / provider; Fn fnserver) come from the per-container data already recorded. The
  paper must state that OW's breakdown is intra-process and the others' are inter-container: same
  axis, different granularity.

### 25.6 Load-generator provenance, and the energy checks that replace the model gate

**Two `hey` builds exist on this box.** `/usr/local/bin/hey -> /root/go/bin/hey` (sha256 `952be8d7…`,
2026-08-08) is what `sudo` resolves, so it is the build behind every leg in the corpus.
`/home/imran/go/bin/hey` (`6666d178…`, 2026-08-06) is what an interactive shell resolves. Nothing
recorded which build ran until now. From 2026-10-02 every run records `env.loadgen_bin` and
`env.loadgen_sha256`, and `run_final.sh` refuses any build but `952be8d7…`. For the existing corpus,
the build is inferred from `sudo` resolution, not recorded. State it as inferred.

**A requested-but-missing `hey` is now fatal before the window opens.** Previously `run_hey` returned
`None` and the run silently used the Python loadgen, flagged only by `loadgen_fallback` afterwards.
`run_lock_session` gated on that flag, so a leg would fail rather than be cited. But a whole
unattended session could still burn its time producing nothing but failed legs. A `hey` that exists
but fails mid-run still falls back and is still gated.

**The ">15 %" flag is the model's residual, and its message now says so.** `rapl_validation_err_pct`
keeps its name: the repo removed an alias of this field once already ("one name for this value, not
two"), and every summary and reader uses it. But `saqef` now prints "MODEL RESIDUAL", not "RAPL FIT
DEGRADED". Note that its value depends on `idle_w`, so it shifts between desktop and headless sessions
for reasons unrelated to RAPL.

**Model-free energy checks (offline, from the new per-run files; no global constant):**
- *Within-run linearity.* `energy_trace.csv` (1 Hz package + psys) is aligned against host busy cores
  from the same interval. A power-vs-busy-cores slope that is stable across the run's intervals means
  energy scales linearly with load inside that run. A slope that grows with load is the superlinearity
  §24.6 suspected. The per-interval CPU in `samples_raw.csv` is container-only, so host busy cores
  come from the same 1 Hz windows via `/proc/stat` in the analysis, or from `host_cpu_sec` at
  run granularity.
- *Idle cross-check.* The 60 s idle probe after each leg is RAPL at zero traffic in the same stack
  state as that leg's `idle_w`. |probe W − calibrated idle_w| is a within-session drift detector for
  the baseline every energy figure subtracts.

Neither gates tonight's session. Both are pre-registered here so their thresholds are not picked
after seeing the data. Reported as-is, they are:
- linearity: slope CV across intervals, reported per leg
- idle cross-check: absolute difference, flagged if it exceeds the max − min spread of that state's
  own three calibration reads in `final_calib`. No fixed wattage: no per-read calibration file
  survives from any earlier session (every `results/idle_w_calibration/lock_*` dir is empty, because
  each session reused inherited values), so there is no measured spread to anchor a number on.

## 26. Roadmap from here (agreed 2026-10-02; corrected the same day)

Rule: nothing measured in `final_*` is re-measured. Every later step is either analysis or a new
**workload**: the plan already set in §24.6 step 5 (I/O-bound, memory-bound, bursty, cold start).
An "overload" arm proposed earlier the same day is **dropped**. Bursty arrivals answer the
supervisor's success-rate question in a realistic form, because bursts are where requests queue,
get rejected (429) or time out.

Every measurement night is one command: `sudo bash tools/go.sh`. Progress afterwards:
`sudo bash tools/go.sh --status`.

| # | step | type | answers |
|---|---|---|---|
| 1 | **CPU-bound final corpus** (`go.sh`, §24.8) | measurement, ~2 h | citable baseline for all 4 platforms |
| 2 | Adjudicate P1–P3; paper tables/figures; close §24.2 action item (1) | analysis | main results |
| 3 | Control-plane anatomy (OW JVM threads; Knative activator/queue-proxy/autoscaler; OpenFaaS gateway/provider; Fn fnserver) + energy per request, idle CP power, §25.6 checks | analysis of step-1 data | *why* OW costs 21–45× more CP CPU; whether the energy is trustworthy |
| 4 | **W1 I/O-bound**: handler waits 5 ms (`time.sleep`, simulating a downstream call) instead of spinning | measurement, ~2 h | is CP cost a property of the platform or of the workload? An earlier quick-tier pass (old sampler) suggested CP ms/inv is workload-invariant but the share ordering is not. W1 settles that under the final protocol. |
| 5 | **W2 memory-bound**: handler sweeps a resident buffer larger than cache (sized to stay under every platform's memory limit) | measurement, ~2 h | does memory pressure in the function inflate CP cost (cache/bandwidth contention on a shared host)? |
| 6 | **W3 bursty**: on/off arrivals (bursts at high concurrency separated by idle gaps) instead of a steady closed loop | measurement, ~2 h; needs a burst mode in the load generator | success rate, latency tails and CP cost under realistic bursts; autoscaler reaction (Knative), container creation (OW) |
| 7 | **W4 cold start / scale-from-zero** (Knative, OpenWhisk; Fn idle timeout) | measurement | CP cost per cold start |
| — | fifth platform | only if supervisor/reviewer asks | |

Build order for the tooling. Each piece is pre-registered (design + predictions) before its night:
- `go.sh --workload cpu|io|mem`: generalise the existing handler swap in `tools/run_io_bound.sh`
  (swap → rebuild images → measure → restore) into `run_final.sh`, so W1/W2 are the same one command
  with a different flag.
- W3: a burst arrival mode in the harness's load generator.
- W4: scale-to-zero settings per platform.

Decision point: after step 2, if P2 fails (OW share moves > 3 pp), explain it before step 3.

## 27. Final corpus — P1–P3 adjudicated (2026-10-03)

Session `final_` (§24.8), headless via `tools/go.sh`, 2026-10-02 19:11–20:53 UTC. 15/15 legs
`rc=0 gates_ok=True` on attempt 1, `failed_legs=0`. idle_w calibrated in-session 24 min before the
first leg (`results/idle_w_calibration/lock_final_calib/`, 5 states: bare 6.548 / of 6.857 /
fn 6.984 / kn 8.767 / ow 8.19 W). Log cap checked on a live `k8s_*` container, EPP performance ×8,
psys ok, 1 Hz `energy_trace.csv` in every run, OW JVM thread CSVs written. Drift ≤ 7.6 % everywhere.

| cp share % | c=1 | c=2 | c=4 | c=8 |
|---|---|---|---|---|
| OpenFaaS | 4.74 | 4.92 | 5.91 | 6.09 |
| Fn | 8.97 | 8.53 | 8.71 | 8.78 |
| Knative | 9.95 | 9.52 | 9.26 | 10.38 |
| OpenWhisk (ow1 / ow4 / ow8) | 76.57 | — | 76.16 | 76.29 |

As pre-registered, failed predictions are reported, not tuned away.

### 27.1 P3 — holds
OpenFaaS < {Fn, Knative} < OpenWhisk at every concurrency. Fn < Knative also at c=1
(8.97 < 9.95), so the `remeasure_shares_` c=1 flip (12.73 vs 11.12) does not reproduce. Citable.

### 27.2 P2 — fails; the pre-registered mechanism was wrong
OW share 76.57 / 76.16 / 76.29 (§27.11) against the predicted 81–84 % band: −4.4 to −7.8 pp. §24.8.2 said a
move > 3 pp "would mean the logs were reaching cp CPU". They were not counted in cp CPU. Instead, cp
CPU per invocation itself fell:

| OW c=1 (median usable run) | fn ms/inv | cp ms/inv | share % | untracked host CPU, last run (CPU-s) |
|---|---|---|---|---|
| `tier1ow1` (2026-10-01, ref) | 5.77 | 24.7 | 81.1 | 278 (run_5) |
| `remeasure_shares_tier1ow1` (2026-10-02) | 5.64 | 19.8 | 77.8 | 129 (run_5) |
| `final_tier1ow1` (this session) | 5.64 | 18.4 | 76.5 | 24.6–31.2 (runs 2–6, flat) |

Function CPU per invocation does not change. The whole move is cp CPU per invocation, −26 %, while
untracked host CPU (`host_overhead_cpu_sec`) collapses. The light platforms move the same way at
c=1 against `tier1` (OpenFaaS −3.0 pp, Fn −5.0, Knative −4.5). So this is a **box-state** effect, not
an OpenWhisk change: the log cap plus headless remove the dockerd and desktop/agent contention (§24.2,
~0.65 cores of desktop + agent; dockerd 1.24 → 0.46 cores in `owhead1`), and that contention was
inflating cp CPU per invocation. §25.4 had already said headless could move shares 3–5 pp through
contention. P2 was written without that and assumed only *direct* accounting of dockerd CPU could
move the share. That assumption was wrong.

Consequence: every pre-`final_` share was measured under contention and reads 3–8 pp high. Only
`final_` shares are cited. The ordering (P3) does not depend on this.

### 27.3 P1 — half fails
Flatness holds: OW c=1 runs 2→6 are 104.4 → 107.1 rps (+2.6 %; §24.2 had −38 to −52 %). The level
is 104–110 rps, not the predicted ~90. The ~90 came from the `owhead1` truncate arm, which ran with
the desktop up; headless removes that contention too. Reported, not fixed.

### 27.4 §24.2 action item (1) — closed
The OW throughput decay was operator contention plus unbounded docker log volume, not an OpenWhisk
defect. With logs capped and the box headless it is gone (flat throughput, flat untracked host CPU).

### 27.5 Energy — RAPL-based energy IS citable from this session
Every leg warns ">15 %" (OW 16–20 %, light legs up to 44 %; model/RAPL ≈ 0.6 at c=1, ≈ 0.9–1.0 at
c=8). That figure is the **model's residual** against RAPL (§25.6), not a RAPL quality check. The
3.5 W/core constant is retired (§25.1), energy claims come from RAPL directly (§25.2), and §24.8.2
pre-registered the residual as warn-only because it "cannot gate a RAPL-based energy figure".
`--rapl-fit-warn` is therefore what makes `final_` the citable **energy** session. Cite
`e_rapl_j − idle_w × wall_s` (basis: §27.8). Never cite `energy_J` (the model's figure). The model
undershoots at low load because marginal W per busy core falls with load (§25.1: OpenFaaS
6.17 → 3.75 W). No extra experiment is needed for this.

`run_lock_session.sh` used to say the opposite (comment at `RAPL_FIT_WARN`, the `rapl_fit_gate`
comment, and the end-of-session NOTE: "ENERGY figures from this session are not citable"). Fixed
2026-10-03. The 15 `final_` leg logs still carry the old NOTE line. This section supersedes it.

The share is not energy-weighted. `cp_dynamic_share_pct` = cp_cpu_s / (cp_cpu_s + fn_cpu_s) exactly,
because the model applies one constant to both terms (`saqef_harness.py`
`sensitivity.cp_dynamic_share_pct_by_busy_w` is identical at 2.0 / 3.5 / 5.0 W). idle_w does not
enter the share.

### 27.6 Provenance fix
All 15 original `lock_summary.json` files said "idle-w NOT recalibrated this session — INHERITED".
That was false. `run_lock_session.sh` only looked under the leg's own stamp, but `run_final.sh`
calibrates once under `lock_final_calib` and then runs each leg under its own stamp. Fixed:
`run_lock_session.sh --idle-w-source DIR` names the calibration the `--idle-w-*` values came from.
The summary now cites that directory and checks that the values used equal its medians (otherwise it
says "idle-w NOT from …"). `run_final.sh` passes `--idle-w-source "$CAL"`. The 15 summaries were
regenerated by re-running the gate block on the on-disk runs. Apart from `idle_w_provenance` and
`notes`, they are byte-for-byte identical in content to the originals.

### 27.7 Variability (corrected figures)
Run-to-run CV of throughput: light legs 0.64–4.78 %. OW is 7.9–9.0 % over all six runs, but that
comes entirely from the discarded warm-up run_1. Post-discard it is 1.5–2.0 % (104.4–109.7 rps at
ow1). Quote the post-discard figure. Share CV over usable runs: 1.3–6.0 %, except Knative c=4 at
8.54 %. The 7.6 % quoted elsewhere was the worst throughput **drift**, not a CV.

### 27.8 Idle cross-check (§25.6) — fails 12/15; per-leg probe is the idle_w basis
`tools/idle_crosscheck.py` (offline; rows in `results/final_session/idle_crosscheck.json`). Every
post-leg 60 s probe reads above the session calibration: +0.51 to +1.91 W. That exceeds the
calibration's own read spread (of 0.45, fn 0.42, kn 0.54 W) on all 12 light legs. The 3 OW legs pass
only because OW's spread is 1.24 W. The probe direction is uniform: of 7.63–8.02 vs 6.86, fn
7.50–7.93 vs 6.98, kn 9.62–10.68 vs 8.77, ow 8.89–9.35 vs 8.19.

Cause, not separable from data on disk. Power is flat within each probe (no decaying tail after the
bench). Two candidates:
1. Leakage at higher package temperature. Calibration ran 19:13–19:37 before any load. pkg temp
   during legs ran 62–85 °C.
2. Instrumentation. The probe runs the full harness (CPU sampler etc.) and shows 0.44–0.88 host cores
   busy outside the containers. The calibration was RAPL reads only, with no host CPU recorded. The
   excess tracks host cores loosely (Knative highest on both).

**Decision: idle_w basis = the leg's own probe.** It shares the leg's stack state, thermal epoch and
instrumentation load, so `e_rapl − probe_W × wall` removes exactly what was present without traffic.
The calibration-basis figure is kept as an upper bound. With the calibration basis, dynamic energy
is overstated by 2.8 % (fn c=4) to 10.8 % (of c=1). Restated (median over usable runs, J per run of
3000 invocations):

| leg | E_dyn probe basis | E_dyn calib basis | over % | mJ/inv (probe) |
|---|---|---|---|---|
| of c1 / c2 / c4 / c8 | 174.1 / 121.5 / 93.9 / 71.3 | 193.0 / 132.1 / 99.4 / 73.9 | 10.8 / 8.7 / 5.8 / 3.6 | 58.0 / 40.5 / 31.3 / 23.8 |
| fn c1 / c2 / c4 / c8 | 141.3 / 98.4 / 82.6 / 60.6 | 154.6 / 106.4 / 84.9 / 63.2 | 9.5 / 8.1 / 2.8 / 4.4 | 47.1 / 32.8 / 27.5 / 20.2 |
| kn c1 / c2 / c4 / c8 | 180.7 / 116.1 / 98.6 / 77.0 | 197.2 / 127.4 / 104.5 / 83.2 | 9.2 / 9.8 / 6.0 / 8.1 | 60.2 / 38.7 / 32.9 / 25.7 |
| ow1 / ow4 / ow8 | 339.6 / 319.6 / 324.5 | 372.1 / 342.9 / 343.6 | 9.6 / 7.3 / 5.9 | 113.2 / 106.5 / 108.2 |

Each probe is a single 60 s read (N=1), so the probe basis carries probe-to-probe noise that the
N=3 calibration does not. The table reports both bases rather than hiding that trade-off. No
re-measurement (§26). `tools/reanchor_and_kn_idle.sh` is **not** run: task (b) re-measures and task
(c) recalibrates Knative idle-w, which `final_calib` supersedes.

### 27.9 cp CPU per invocation — the cleanest result in the corpus
Median over usable runs, ms of CPU per successful invocation:

| | c=1 | c=2 | c=4 | c=8 | fn ms/inv |
|---|---|---|---|---|---|
| OpenFaaS | 0.29 | 0.31 | 0.39 | 0.40 | 5.9–6.2 |
| Fn | 0.52 | 0.50 | 0.52 | 0.52 | 5.3–5.5 |
| Knative | 0.70 | 0.67 | 0.66 | 0.72 | 6.2–6.5 |
| OpenWhisk (ow1 / ow4 / ow8) | 18.43 | — | 17.70 | 17.85 | 5.5–5.6 |

Both terms are per-invocation constants against concurrency, which is why the share is flat and why
it can be interpreted. OpenFaaS is the exception: cp ms/inv rises 38 % (0.29 → 0.40) with fn flat,
so its share creep 4.74 → 6.09 % is real. At c=1, OW costs **26–64×** more cp CPU per invocation
than the others (18.43 vs Knative 0.70 / Fn 0.52 / OpenFaaS 0.29). §26 step 3 predicted 21–45×.
The 8× share ratio understates this, because OW's function cost is the same as everyone else's.
(§27.2 uses the median run, 18.4 ms. A mean over runs 2–6 gives 18.2.)

### 27.10 Next
1. Copy `results/` → `saqef-paper`, commit, push.
2. `figures/make_figures.py` REGIMES and paper numbers from `final_` (shares §27, cp ms/inv §27.9,
   energy §27.8 probe basis with the calibration band).
3. §26 step 3 (control-plane anatomy from the JVM CSVs and traces). Then W1.

Status 2026-10-03: (1) done, `saqef-paper` pushed. (2) done as a separate script,
`saqef-paper/figures/make_final_figures.py` → `figures/final/` (F1 share vs c, F2 cp ms/inv,
F3 energy/inv, `final_tables.md`, `final_legs.csv`). `make_figures.py` is left as is so V5 still
rebuilds. The final draft starts from V5 and is rewritten only once the workload results are in.

### 27.11 OW headline shares included the discarded warm-up (fixed 2026-10-03)
`run_lock_session.sh` applied `--discard-warmup` to the gates but took the headline
`cp_dynamic_share_pct` (and `cv_pct`) from the leg's `summary.json`, which is the median over **all**
runs in `runs.json`. The OW legs keep run_1 in there (87.7–87.9 %). Cited vs usable-run medians:
ow1 76.715 → **76.57**, ow4 76.28 → **76.16**, ow8 76.315 → **76.29**; CV 5.2–5.5 % → 1.2–1.5 %.
Light legs ran without a warm-up discard and are unaffected, as are §27.8 energy and §27.9 cp ms/inv
(both already sliced). P2/P3 conclusions are unchanged (−4.4 to −7.8 pp below the band). The §27 table
now carries the corrected values. The three OW `lock_summary.json` files are recomputed in
`saqef-paper` with a note. The gate block now computes share and CV over usable runs only.

A side observation from the tables, not pre-registered: OW throughput is pinned at 107–111 rps for
c = 1, 4, 8 while p50 latency rises 8.8 → 33.9 → 68.3 ms. The light platforms scale 5.5–6.1× from
c=1 to c=8. OW is serialising requests at about 9 ms each. This is why OW's energy per invocation stays flat
(113 / 107 / 108 mJ) while the others fall 2.3–2.4×. It needs explaining in §26 step 3.

### 27.12 Control-plane anatomy (§26 step 3) — where cp CPU goes
`tools/cp_anatomy.py` (offline). Output is in `saqef-paper/results/final_session/cp_anatomy.json` and the
figure is `figures/final/F4_cp_anatomy_c1`. Medians over usable runs, ms of CPU per invocation. For
every leg the components sum to the recorded cp CPU within 1 %, so the attribution closes.

**Light platforms: one component carries the cost (inter-container).**

| c=1 | component | ms/inv | rest of cp |
|---|---|---|---|
| OpenFaaS | gateway | 0.28 (95 %) | provider, nats, queue-worker, prometheus ≤ 0.01 each |
| Fn | fnserver | 0.52 (100 %) | — |
| Knative | activator 0.34 + kourier gateway 0.33 | 0.67 (96 %) | autoscaler, controllers, webhook ≈ 0.01 each |

OpenFaaS's rise 0.29 → 0.40 from c=1 to c=8 (§27.9) is all gateway (0.28 → 0.40). The provider, the
autoscaler and the controllers are near zero under steady load: on a warm platform, cp cost is
request-path proxying, not orchestration.

**Knative classification sensitivity.** `queue-proxy` is counted as function by the V5 convention
(co-located request-path proxies → function; see V5 §5.6). It costs 0.73–0.84 ms/inv, more than the
whole cp. Moving it to cp would double Knative's share: 9.98 / 9.53 / 9.25 / 10.40 % →
20.4 / 20.5 / 20.5 / 22.5 % (c = 1/2/4/8). The ordering P3 still holds (OpenFaaS < Fn < Knative ≪ OW).
OpenFaaS's of-watchdog runs inside the function container, so it cannot be separated the same way.

**OpenWhisk: process spawning, not orchestration logic (intra-process).** JVM threads plus reaped
children account for 91.7–92.9 % of the `openwhisk` container's CPU. The remaining ~1.5 ms/inv is
other processes in the container and the 5 s sampling grain.

- Under the **pre-registered map (§25.5)** the breakdown is **incomplete**: "other" is 27.1–27.9 %,
  above the 20 % limit. The map was written for HotSpot thread names (`C1/C2 CompilerThread`,
  `GC Thread`, `akka`/`dispatcher`). This JVM is OpenJ9 (`JIT Compilation`, `GC Worker`,
  `Concurrent Mark`), and the actor-system threads are truncated to 15 chars as `standalone-acto`,
  so "akka" never appears. As pre-registered, this is reported, not re-mapped.
- One component needs no thread-name mapping, because it is the pre-registered `<children>` row:
  **reaped child processes = 12.2 / 11.9 / 11.9 ms/inv = 72 % of JVM CPU** at ow1/ow4/ow8. These are
  the processes the invoker forks (§25.5 calls this row "docker CLI spawned by the invoker"). That
  alone is 17–42× the entire cp cost of each light platform.
- **Post hoc, labelled as such:** with an OpenJ9 name map ("other" = 0 %), the actor system
  (controller + invoker logic, HTTP) is 3.8–4.0 ms/inv (23 %), and JIT, GC, telemetry and pools
  together are ~0.7 ms (4 %).

So OW's 26–64× cp premium (§27.9) is mostly **per-activation process spawning** (≈ 12 ms CPU per
invocation in child processes). Even OW's actor-system logic alone (≈ 4 ms) is 6–14× a light
platform's whole cp. The CPU the docker CLI triggers inside dockerd is not in cp at all. It lands in
untracked host CPU, so this is a lower bound on OW's real cost.

**Open, not resolved from data on disk.**
(a) Which docker commands run per activation. The children row has no argv. A one-leg strace/execsnoop
during W1 would settle it.
(b) The throughput cap (§27.11). Only 2 `guest_hello` action containers exist at c = 1, 4 and 8, and
throughput is pinned at ~110 rps ≈ 9 ms per request serialised. Candidates: the standalone invoker's
concurrency/container limits, or serialised docker CLI calls. Cap and spawn cost may share one cause.
Both are W4-adjacent (container lifecycle) and are noted for that pre-registration.

§27.10 status: step 3 done. Next is W1 (`go.sh --workload io`, pre-registered first).
