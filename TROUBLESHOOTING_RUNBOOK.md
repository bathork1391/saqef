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
