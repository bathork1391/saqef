# SAQEF — serverless platform overhead & carbon study

Durable memory for this project. Any opencode/agent session should start here, then read the
report sections referenced below. **The git repo IS the memory** — not any chat or account.

## What this is
Cross-platform measurement study: Fn vs OpenFaaS serving an identical CPU-bound function
(handler busy-spins ~5 ms per invocation; 3000 requests, concurrency 20, 5 runs). Headline
metric: `cp_dynamic_share_pct` = control-plane container CPU / dynamic (load-created) CPU —
a contention-robust discriminator. Decision gate: platform gap in the share must exceed 5 pp.

## How to run
- Fn: `./run_saqef.sh all` -> `results/fn_cpubound*`. See header of run_saqef.sh.
- OpenFaaS: `./run_openfaas.sh [stack|scale|check|verify|bench|gates|all]` -> `results/openfaas_cpubound*`.
- **Never commit a run until `gates` passes.** `SAQEF_REPEAT < 5` writes to a `_quick` outdir
  (never publish). The gates table shows delta%, CPmapped (must be 6/6 `ok`), coverage% (100),
  `host_plausible` (must be true), `physical_plausible`, `host_sat%` (~100 on the codespace is
  the honest saturated-box flag, not a failure).

## Expert review #5 (2026-08-09) — disposition

External expert (reviewer #5) ran the audit in `EXPERT_REVIEW_PROMPT.md`. Verdict: **not yet
publishable as-is, but close — "close two self-identified loose ends and tighten one claim's
wording."** Disposition of each point:

- **Praise (confirmed, no action):** gates-in-measurement-path, self-critical changelog, the
  three-session OW stability (82.54/80.23/82.36 in a ~2 pp band) as a reproducibility argument,
  honesty about Kn's 13.99→11.40→12.44 swing.
- **median_summary list-union gap — CONFIRMED + FIXED.** `container_inventory` (a list) fell
  through to first-run-only while dict siblings were unioned; OpenWhisk's growing `wsk0_N` pool
  was the exact case. `median_summary()` now unions lists (dedup, first-seen order) the same way
  it unions dicts. Diagnostic impact only (no headline metric reads `container_inventory`), but
  the inconsistency was real. 3 new tests.
- **RAPL double-wrap indistinguishable from "unavailable" — CONFIRMED + FIXED.** Extracted
  `rapl_correct_wrap()` (single-wrap exact correction; double/no-range → fail-open None) and added
  a `rapl_wrap` summary field (`none|corrected_single|uncertain_double|uncertain_no_range`),
  distinct from `rapl_available`. OW's 150–320 s runs are the platform most exposed. Defensive on
  this box (~262 kJ counter range, no wrap at realistic power). 3 new tests.
- **Convention-normalized comparison — CONFIRMED + ADDRESSED WITH DATA.** Re-derived Kn's
  queue-proxy magnitude from `samples.csv` (integrated CPU-time reproduces the harness's 12.44 to
  0.02 pp): queue-proxy ≈ 10.3 CPU-s/run vs fn 57.6 / CP 9.4, so classifying the sidecar as CP
  raises Kn to **25.7%**. Added a §5.6 "convention-normalized view" table + abstract caveat:
  Fn/OF/Kn already use the consistent co-located-proxy-in-fn convention; the Fn–Kn tie is
  convention-sensitive (report as a cluster), but OF < {Fn,Kn} << OW survives every plausible
  reclassification.
- **Knative idle-premium "dangling" — PUSHED BACK.** The expert's own prescribed fix ("honestly
  scoped absence") is already implemented: §5.6 labels it "OPEN, not citable at any specific W
  figure" and the abstract cites the three readings only as "some premium, not yet pinned down"
  (also §12). No paper edit needed; the remaining action is the N≥3 recalibration (box task,
  pending a quiet session — see finding #13 below).
- **n=1 machine (blocking B) — AGREED + REWORDED.** Contribution #3 and T5V #8 now say explicitly:
  core-count effect demonstrated on **one physical host via cpuset restriction** (instrument held
  fixed — that is the validity argument), a second physical machine is future work. **Reviewer's
  lean (agreed):** machine-pair dependence is the paper's central contribution, so spend the
  extra days on a genuinely distinct second box if the timeline has any give — "demonstrated X"
  beats "demonstrated X, with one caveat, in our central claim"; shipping the honestly-scoped n=1
  is a legitimate fallback, not the preferred path. (§4.2 already carried the CANDID.)
- **Cold review pass #6 on the four newest code paths (Kn adapter, OW adapter, narrowed isolation
  policy, median_summary rewrite) — OPEN, recommended before submission.** Track record: every
  prior pass found something real. **Must be a GENUINELY fresh reviewer** — a different person or
  at minimum a fresh model session with no prior context, NOT a continuation of this thread; a
  reviewer who already knows the codebase pattern-matches instead of re-deriving, which defeats
  the purpose (this session counts as "reviewer #5, continued" from here on).

## Current state (2026-08-09 — quiet-gate automation + contamination A/B, reviewer #5 follow-up)

Reviewer #5 confirmed the runbook's own documentation: opencode at ~276% CPU / 1.1 GB RSS
(2026-08-07) contaminated `host_saturation_pct` and drifted Fn's `cp_dynamic_share_pct`
~0.3–1 pp via cache pollution / context switching / DVFS. "Quiet box" was a manual `uptime`/`ps`
assertion — a hope, not a gate. Addressed:

1. **Ambient-load quiet gate baked into the harness** (`ambient_load_check()`): samples whole-host
   busy CPU over a 20 s window before every bench and **refuses to start** above
   `--max-ambient-cpu-pct` (default 15%); reading + top-CPU `ps` snapshot land in `summary.json`
   → `ambient`, so a citable run self-certifies a quiet box. Live-verified: with this session's
   opencode + dockerd + k3s + chrome it read 19.5% and refused. `--no-quiet-gate` = exploratory /
   contamination-AB only. Idle-probe (idle-w calibration) is exempt (the platform stack itself is
   the subject). 3 new tests (47/47 pass).
2. **Contamination A/B tool** (`tools/contamination_ab.py`): runs the same bench clean (gate
   active) vs an emulated agent signature (gate disabled) and reports the measured delta on
   `cp_dynamic_share_pct` / `host_saturation_pct` / p50/p99 / throughput → the honest "how
   much does an agent-style load move our numbers on THIS box" figure for §7. **Profile
   matched to the documented 2026-08-07 incident** (per reviewer #5 follow-up — a generic
   light stressor would understate the real bound): `--cores 3` spinners pinned to distinct
   cores (≈300% ≈ the real 2.76-core load) + `--mem-gb 1.1` bytearray (the real 1.1 GB RSS);
   the dirty leg prints the achieved aggregate host busy% (target = cores / cpu_count) and the
   emulated load profile lands in `contamination_ab.json`. **N≥5 by default (`--repeat 5`) —
   the same discipline as every citable number in this study; a single A/B pair is not a
   bound.** **Must be run from a bare shell with agents quit** (the clean leg enforces it).
3. **Flags threaded through the adapter layer** (`harness_argv` keyword args, default-preserving
   → byte-identical argv tests still pass) and `saqef run --no-quiet-gate/--ambient-window-s/
   --max-ambient-cpu-pct`.

**Box task, next quiet session (bare shell, opencode quit) — SEQUENCED, in this order:**
(a) `tools/contamination_ab.py --platform fn` then `--platform openfaas` (REPEAT=5 defaults)
→ §7 contamination bound; (b) **only after (a)**: re-anchor the Fn/OF regression references
(11.60/7.67) via same-day old-runner A/B under the now-self-certifying gate (runbook §2/§3);
(c) Knative idle-w N≥3 recalibration (finding #13). Do NOT run (b) concurrently with (a): the
A/B first establishes what "quiet" actually means on this box (a measured bound, not a
load-average check), otherwise the re-anchor is anchored against a session whose quietness is
still being established — a subtle circularity. All three produce results whose quiet
provenance is in the data, not the prose.

**Reviewer #5 follow-up on execution quality (2026-08-09) — AGREED, incorporated above:**
(1) the A/B synthetic load must reproduce the documented incident profile (2.76 cores + 1.1 GB
RSS), not a generic light stressor — now the tool default; (2) sequence the A/B BEFORE the
regression re-anchor to avoid circular quietness — now the box-task order above; (3) review
pass #6 must be a genuinely fresh reviewer (different person, or at minimum a fresh model
session with no prior context), NOT a continuation of this thread — a reviewer who already
knows the codebase pattern-matches instead of re-deriving, defeating the whole point (four
passes → four real bugs is the argument FOR independence); (4) n=1 machine — reviewer leans
toward a genuinely distinct second box if the timeline has any give, since machine-pair
dependence is the paper's central contribution; shipping the honestly-scoped n=1 is a
legitimate fallback, not the preferred path. User's call.

### Contamination A/B — BOTH LEGS DONE (2026-08-08 ~23:00 + rerun 2026-08-08, box quiet, N=5/leg)

First real execution of the new tool (Fn leg), then a full rerun of Fn + the OpenFaaS leg in the
same session (this one). **Verdict: the documented agent profile moves Fn's share ~+2.2 pp but
OpenFaaS's only ~+0.3 pp** — a ~6× asymmetry. The ~0.3–1 pp drift estimate the paper previously
inferred from the 2026-08-07 incident is superseded by these direct measurements; §7 wording must
cite: Fn ~+2.2 pp, OpenFaaS ~+0.3 pp (both N=5/leg, profile-matched). Data in
`results/{fn,openfaas}_contamination_ab/{clean,dirty}/summary.json` +
`contamination_ab.json`.

- **Fn rerun (confirms the +2.2 pp is stable):** clean **10.0** (CV 2.8%, CI 9.81–10.55),
  host_sat 69.7, p50 6.4 / p99 9.3 ms @ 597.8 rps → dirty **12.16** (CV 4.2%, CI 12.07–13.3),
  host_sat 93.6, p50 7.2 / p99 14.8 ms @ 503.0 rps. **delta +2.16 pp** (first run: 9.91 → 12.11,
  +2.20 pp — the two sessions agree within ~0.05 pp).
- **OpenFaaS leg (new):** clean **6.9** (CV 1.6%, CI 6.76–7.01), host_sat 71.6, p50 6.8 / p99 12.1 ms
  @ 549.2 rps → dirty **7.24** (CV 0.6%, CI 7.22–7.33), host_sat 92.7, p50 7.9 / p99 17.5 ms @ 450.1
  rps. **delta +0.34 pp.**
- **Mechanism story (supports Design Principle C1):** the share is contaminated only where the
  control plane is a *central orchestrator on the request path* (fnserver +2.2 pp); OpenFaaS's
  per-replica of-watchdog model is nearly immune (+0.34 pp). Same direction as the core-scarcity
  result (Fn +33% at 2 cores, OF flat-to-lower).
- **QoS is the larger contamination effect on both platforms:** p99 +5.5/+5.4 ms, throughput
  −16/−18%. Both dirty legs correctly flagged `host_saturated=true` → dirty-leg latency percentiles
  NOT citable; the clean-vs-dirty latency CONTRAST is the citable output.
- RAPL not citable this session (clean rapl_err 35–49% Fn, 38–54% OF; dirty 22–28%) — Fn/OF
  energy/carbon flags stay quiet here. Share unaffected (cgroup CPU-time ratio).
- Clean legs 10.0 (Fn) / 6.9 (OF) are the lowest values ever recorded on this box (previous lows
  9.91 / 7.14) — the quietest session yet, consistent with the documented day-to-day drift; does
  not affect the within-session A/B deltas.
- Minor: `fn_replicas` grew 5→6 in Fn dirty runs 1–5 (autoscaler under contention); OF held 16/16
  everywhere.
- Box left clean: `docker service ls` empty, 21 containers = k3s/knative substrate only (the
  openfaas teardown's "network in use by service xypwiyk…" warning resolved in the final cleanup
  pass — `hello` WAS removed, no leftover to taint a future Fn session).

**Bug found + fixed (2026-08-08):** `tools/contamination_ab.py` wrote benches via `cwd=REPO`
(so results landed in the repo-root `results/…`) but read them back relative to the caller's
cwd — run from `tools/` it crashed at `read_summary()` with `FileNotFoundError` and never wrote
`contamination_ab.json`. Fixed: outdir is now resolved to absolute against `REPO`
(`contamination_ab.py:140-144`). Verdict computation re-verified by reading the existing
summary.jsons directly. **Box-task step (a) is now COMPLETE** — next (b): re-anchor the Fn/OF
regression references (11.60/7.67) via same-day old-runner A/B under the self-certifying gate;
then (c) Knative idle-w N≥3 recalibration.

## Current state (2026-08-08 independent reverification + 11 bugs found/fixed — READ THIS FIRST)

A follow-up audit (independent of the agent that did the quiet-box reruns below) re-derived every
headline number directly from `runs.json`/`summary.json` and re-read the harness/adapters/scripts
line by line, specifically hunting for anything that could contaminate results. **Verdict: the
headline numbers hold** (OW 80.23, Kn 11.40 reproduce exactly from raw data; CIs, CVs match) —
but 11 real bugs were found, all fixed except where noted:

1. **[confirmed active, ~0.1 pp impact]** OpenFaaS's `cp_containers` included a bare `"gateway"`
   substring that collides with Knative's `kourier-gateway`/`3scale-kourier-gateway` pod
   containers. Since k3s/Knative stays resident on this box across every platform's session, this
   was silently folding leftover Kourier CPU into OpenFaaS's control-plane bucket — confirmed
   present in all 5 `results/regression/openfaas` runs (`k8s_kourier-gateway_*` in the
   `delta_check_map`, ~1.5% of `cp_cpu_s`). **Fixed:** `cp_containers` now prefixed
   `"openfaas_gateway"` etc. (`platforms/openfaas.py`, `run_openfaas.sh`) — matches the same real
   containers, no longer matches Kourier's.
2. **[isolation gap, root cause of #1 and #3]** Fn/OpenFaaS/OpenWhisk's `IsolationPolicy` never
   accounted for a leftover Knative/k3s deployment (Knative's own policy already forbids
   fnserver/openwhisk/openfaas, but the reverse was never added when Knative became the 4th
   platform). **Fixed:** all three now forbid any `k8s_`-prefixed container, both in the
   data-driven adapter path and the harness's legacy shell-runner fallback.
3. **[latent, currently masked by luck]** Fn's `fn_images=("hello",)` substring would match
   Knative's function image `kn-hello` if a leftover Knative `hello` ksvc were up during an Fn
   session. Currently harmless only because k3s-managed containers show a bare image digest in
   `docker ps`, not the resolved tag — an incidental quirk, not a defense. Primary fix is #2 (a
   leftover Knative deployment now fails loud before this could matter).
4. **[docs, confirmed]** `TROUBLESHOOTING_RUNBOOK.md`'s quiet-box runbook calibrated idle-w live,
   then the very next command hardcoded the OLD value (`--idle-w 5.294`) instead of the fresh
   reading — following it literally would have reproduced the contaminated baseline. Also had zero
   Knative-specific steps despite a cited result needing them. **Fixed:** placeholder + explicit
   warning, plus the missing Knative section added.
5. **[defensive, not currently triggered]** `rapl_energy()` did a naive two-point read with no
   protection against the RAPL `energy_uj` counter's wraparound (a documented Linux powercap
   hazard). This box's range is ~262 kJ (no wraparound at realistic power over even OW's 320 s
   runs), but the code had zero defense for a smaller-range machine. **Fixed:** added
   `rapl_max_range_j()` + single-wrap correction in `saqef_harness.py`.
6. **[confirmed, cosmetic-but-real]** `median_summary()`'s dict-merge only kept keys present in
   *every* run, silently dropping `delta_check_map`/`container_labels` entries for platforms whose
   container names change run-to-run (OpenWhisk's `wsk0_N` pool grows over a session). **Fixed:**
   now unions keys across all runs.
7. **[structural, ~0 current impact]** OpenWhisk's fn/cp classification depends entirely on a
   *single* post-run `docker_inventory()` snapshot (it has no `fn_containers` name fallback);
   any action container recycled before that snapshot would misclassify as unclassified instead of
   fn. **Fixed:** `run_once()` now unions a pre-run and post-run inventory snapshot.
8. **[paper hygiene, high impact]** `SAQEF_PAPER_DRAFT.md` (§5.6, Appendix A/B, abstract, roadmap)
   still cited the CONTAMINATED 2026-08-07 numbers (OW 82.54, Kn 13.99, Fn 12.27, OF 7.53)
   throughout, even though this file's "Current state" already had the corrected quiet-box values.
   **Fixed:** every table/prose reference synced to OF 7.62 / Fn 11.60 (today 11.01) / Kn 11.40 /
   OW 80.23, with an explicit "superseded, do not cite" note on the old snapshot.
9. **[methodology note]** `bootstrap_ci()` at N=5 is numerically close to raw min/max — CI-overlap
   arguments (e.g. "OW's old and new CIs overlap, so it wasn't contamination") are weaker than they
   read. The paper already had an IQR caveat for this; strengthened it with an explicit warning
   about CI-overlap-only reasoning (§4.6).
10. **[confirmed active, real]** The regression re-anchor session that produced the headline
    **OF 7.62 / Fn 11.01** numbers has its OWN RAPL validation at **14–32% (OF) / 26–29% (Fn)** —
    NOT the 4.2–8.2%/4.2–5.5% "citable" figure the paper attributes to Fn/OpenFaaS energy (which
    is real, but comes from the separate `fn_cpubound_baremetal`/`openfaas_cpubound_baremetal`
    sessions). Root cause: `saqef regression` reuses `metrics/cpubound.json`'s `idle_w=4.3`
    forever and never recalibrates it — by 2026-08-08 that constant was stale for this box's
    actual idle draw, exactly the same class of drift already documented for OW/Kn idle-w. **Fixed:**
    `saqef gates` now prints a loud "RAPL FIT DEGRADED (>15%)" warning; the paper's energy-citable
    flags no longer imply the regression session's energy is trustworthy.
11. `platforms/base.py`'s `harness_argv()` used `if idle_w:`/`if cpu_count_override:` (truthy)
    instead of `is not None` — an explicitly-passed `0` would silently be dropped and fall back to
    the harness's wrong hardcoded default with no warning. **Fixed.**

**What this does NOT change:** none of these bugs are large enough to move any headline
conclusion (ordering, the 5 pp gate finding, OW's structural CP-heaviness, Kn's energy-citability
reversal all stand).

### Live rerun executed the same day (2026-08-08, immediately after the fixes above)

All four platforms rerun on the actual box with the fixed code, back-to-back, same session:
`saqef regression` (OF + Fn), then OpenWhisk full protocol, then Knative full protocol. Two bugs
caught DURING this rerun that the static code review above missed:

12. **[caught live, fixed immediately]** The isolation fix (bug #2 above) was initially written as
    "forbid any `k8s_`-prefixed container." Live-tested against the box's actual leftover Knative
    deployment: it correctly blocked Fn/OpenFaaS/OpenWhisk while `hello` was up — but ALSO
    permanently blocked them even after `hello` was torn down, because k3s/Knative-serving's own
    control plane (activator, kourier-gateway, coredns, ...) is *designed* to stay resident on this
    box as "the substrate" (see the Knative adapter docstring). **Fixed:** narrowed
    `forbidden_containers` to `("user-container", "queue-proxy")` — the two containers that only
    exist when `hello` is actually deployed — in `platforms/fn.py`, `openfaas.py`, `openwhisk.py`,
    and the harness's legacy fallback. Verified both directions live: blocks correctly with `hello`
    up, passes correctly with only the idle substrate up.
13. **[discovered live, not yet fixed — flag for next session]** Knative's "idle premium" claim
    does not reproduce. The paper (post-fix-#8) said Knative carries a ~2.7 W always-on idle
    premium over bare metal (11.14 W contaminated-session reading → 7.007 W "quiet" reading, both
    vs Fn/OpenFaaS's 4.3 W). Today's live recalibration measured: bare k3s+Knative-serving+Kourier
    substrate with NO `hello` deployed = **4.15 W and 4.23 W** (two repeated 60 s reads, consistent
    with each other, statistically indistinguishable from Fn/OpenFaaS's 4.3 W bare baseline); WITH
    `hello` deployed at 16 replicas = **4.906 W**. That is a real but much smaller premium (~0.5–0.8 W)
    than the previously-reported ~2.7 W, and neither of today's readings resembles the earlier
    7.007 W or 11.138 W figures. **Root cause (methodological, not a code bug):** `idle_w`
    calibration is a *single* 60-second point-in-time RAPL read with no repeats, no median, no
    CI/CV — unlike every other metric in this study (N=5 with bootstrap CI). A metric this small
    (single-digit watts) is exactly the kind of measurement where a single point sample is fragile.
    **Recommendation, not yet implemented:** upgrade idle-w calibration to N≥3 repeated 60s reads
    with median + spread reported, for every platform, before trusting any "always-on idle premium"
    number in the paper. Until then, treat the "Knative idle premium" narrative as **open, not
    citable at the ~2.7 W figure** — today's data argues for something smaller, but a single day's
    single readings aren't enough to replace one shaky number with another.

**Final same-day, same-fixed-code numbers (2026-08-08, supersede everything above pending the
idle-w methodology fix in #13):**
- OpenFaaS (regression session): **7.14** (CI 7.07–7.25, CV 0.93%), p50 6.9 / p99 12.8 ms @ 537 rps.
  Gate table clean: `CPmapped 6/6`, delta_check_map = exactly the 6 real OpenFaaS containers, zero
  Kourier/k8s entries (bug #1 fix confirmed live).
- Fn (regression session): **10.65** (CI 10.51–10.74, CV 0.85%), p50 6.4 / p99 9.2 ms @ 593 rps;
  3-session aggregate (unchanged by today's leg) stays **11.60**.
- Both Fn/OF **FAIL** `saqef regression`'s stale 11.60/7.67 reference (dev 0.95/0.53 pp) — expected,
  documented day-to-day box drift (TROUBLESHOOTING_RUNBOOK.md §2/§3), not a refactor break: every
  gate is green (delta≈0, host_plausible true, coverage 100%). Reference needs a same-day
  old-runner A/B recalibration before the next session, per the existing (pre-dating this audit)
  protocol — not done here to keep this session's scope bounded.
- Knative: **12.44** (CI 12.12–12.97, CV 2.73%), p50 7.6 / p99 11.8 ms @ 494 rps, RAPL err 1.3–4.7%
  (all 5 runs steady-state, no run_1 transient this time) — energy/carbon citable, cleanly.
- OpenWhisk: **82.36** (CI 81.94–86.86, CV 2.44%), p50 113.6 / p99 182.0 ms @ 34.2 rps. Box was
  measurably less quiet during this run (host_sat 56.6–69.7%, vs 42–56% two days ago — `dockerd`
  itself ran at 45–64% CPU during the bench, consistent with the "per-activation `docker logs`"
  hypothesis already in the paper) — still within the non-contamination gate (<85%) and the share
  is contention-robust by construction, but flagged for transparency. RAPL err still 31–50%
  (steady-state runs) — structural, confirmed again — except run_1 at an outlier 0.19%, a curiosity
  not investigated further.
- Figures regenerated (`figures/make_figures.py`) from this fresh data.

Full test suite (38/38, updated for the isolation-policy changes, including the #12 narrowing) passes:
`python3 -m unittest tests.test_saqef_cli`.

## Current state (2026-08-08 quiet-box reruns + 2 bugs fixed — READ THIS FIRST)
- **Two real bugs found and fixed** while investigating why OW/Kn looked "fishy":
  1. **k3s dynamic serving cert can be issued with a `notBefore` in the future**
     (operational, not a measurement-path bug, but it BLOCKS every Knative
     session until fixed). Root cause: on this box, `k3s`/`docker` restart on
     every reboot (systemd `Restart=always`); if the reboot's RTC/clock is off
     before NTP finishes syncing, k3s's dynamiclistener regenerates
     `serving-kube-apiserver.crt` stamped with that wrong (future) time, and
     once NTP corrects the clock backward, the apiserver's own TLS handshake
     rejects the cert as "not yet valid" — `k3s` sits in
     `Active: activating (start)` forever, `kubectl` fails with
     `x509: certificate has expired or is not yet valid`. The root CA
     (`server-ca.crt`) is unaffected (long-lived, only regenerated at cluster
     init); only the short-lived leaf cert is bad. **Fix:** `sudo systemctl
     stop k3s`, move aside `/var/lib/rancher/k3s/server/tls/{serving-kube-apiserver.crt,.key,dynamic-cert.json}`,
     `sudo systemctl start k3s` — it regenerates them from the (now-correct)
     clock. Takes ~15s, no cluster data lost. **Add this as TROUBLESHOOTING
     §10** if it recurs (it will, on every reboot with slow NTP).
  2. **`platforms/knative.py` `deploy()` hardcoded `deploy/hello-00002-deployment`**
     in its rollout-status check. The revision number is NOT stable: a full
     `teardown` + fresh `deploy` (delete the ksvc, recreate it) resets
     Knative's revision counter to 1, so the very first fresh redeploy lands
     on `hello-00001-deployment` and the hardcoded check always fails
     (silently papered over by the `_pod_ready()` label-selector fallback,
     which IS revision-agnostic). Fixed: removed the hardcoded rollout-status
     attempt entirely, deploy() now polls `_pod_ready()` directly. No
     measurement was ever wrong from this (the fallback always worked), but
     every fresh-redeploy session printed a confusing failed kubectl command.
     38/38 tests still pass.
- **OpenWhisk + Knative quiet-box reruns — DONE, supersede the 2026-08-07
  agent-contaminated numbers** (box confirmed quiet: `uptime` load 0.5-0.7/8,
  no other agent; this session's own tool calls are the only load, same as
  every other citable run in this study).
  - **OpenWhisk: median `cp_dynamic_share_pct` = 80.23** (CI 78.45-82.71,
    CV 1.89%, IQR 0.49) — barely moved from the contaminated 82.54 (well
    within the CI overlap), which is itself informative: **the "OW is
    CP-heavy" finding was NOT a contamination artifact.** All gates green
    (delta 0.0% every run, CPmapped 1/1, coverage 100%, host_plausible true).
    **`host_saturation_pct` is now 42-56%** (was 64-87%) — **QoS is citable
    for OpenWhisk for the first time**: p50 97.4 / p99 136.5 ms @ 40.83 rps,
    slo_compliance 1.0. Energy/carbon **still NOT citable**
    (`rapl_validation_err_pct` 45-58% across all 5 runs, stable — this is
    the structural JVM/linear-busy-core-model mismatch, unrelated to box
    noise, confirmed by being unchanged between contaminated and quiet runs).
    idle_w recalibrated 3.889 W (was 5.294; box-state, not a citable delta).
  - **Knative: median `cp_dynamic_share_pct` = 11.40** (CI 10.83-11.94,
    CV 4.17%) — a REAL correction down from the contaminated 13.99 (outside
    the old value's own CI). This changes the four-platform story: Knative is
    no longer "tied with Fn at 12-14", it is now essentially tied with Fn's
    same-day quiet number (11.01, see below) and both sit just above OpenFaaS
    (7.62), clearly below OpenWhisk (80.23). All gates green (delta ≤0.05%,
    CPmapped 15/15, coverage 100%, host_plausible true). `host_saturation_pct`
    now 75-79% (was 84-87%) — **QoS citable**: p50 7.6 / p99 11.5 ms @ 503
    rps. **Energy/carbon verdict REVERSED: now plausibly citable** —
    `rapl_validation_err_pct` settles to 2.5-7% in steady state (runs 3-5;
    run_1 20.1%, run_2 8.9% — the same warm-up transient pattern documented
    elsewhere), a dramatic improvement on the contaminated run's 22-32%
    (which was itself partly a box-noise artifact, not purely the
    always-on-idle-baseline structural issue previously assumed — the k8s
    stack's idle draw is real (7.0 W vs 4.3 W bare, recalibrated today, was
    11.14 W under the contaminated session) but does NOT break the linear
    model as badly as first thought once the box is quiet).
  - **Same-day Fn/OpenFaaS regression re-anchor** (`saqef regression`, run
    right after, same quiet box): OpenFaaS **7.62 PASS** (ref 7.67, dev
    0.05 pp). Fn **11.01 FAIL** (ref 11.60, dev 0.59 pp > 0.5 pp tolerance) —
    this is the ALREADY-DOCUMENTED day-to-day box-state drift (see "Fn share
    drifts day-to-day" below and TROUBLESHOOTING_RUNBOOK.md §2), not a new
    issue; 11.01 is the lowest Fn value since the 2026-08-06 reference
    recalibration (11.60/11.96/12.27/12.92 era; the older 2026-08-05 value 10.46
    is still the all-time low on this box, so "lowest ever" is not accurate),
    consistent with this being the quietest session yet. Do not re-tighten or
    loosen the regression tolerance over this — it is doing its documented
    job (flagging box noise, not refactor breaks).
  - **Updated four-platform ordering (all figures/tables regenerated
    2026-08-08):** OpenFaaS 7.62 < Knative 11.40 ≈ Fn 11.60 (3-session
    aggregate incl. today's 11.01) < OpenWhisk 80.23. Per-inv CP cost:
    OpenFaaS 0.54 ms < Fn 0.75 ms < Knative 0.86 ms < OpenWhisk 23.16 ms.
  - **Confidence tiering update:** OpenWhisk and Knative move from "Fishy —
    needs quiet rerun" to the solid tier for `cp_dynamic_share_pct` and QoS.
    Knative's energy/carbon moves from "not citable by design" to
    provisionally citable (cite the steady-state runs 3-5 range, flag run_1
    as a transient, same discipline as every other multi-run metric in this
    study). OpenWhisk's energy/carbon remains not citable (structural).
  - Result dirs overwritten in place (git-tracked, prior contaminated numbers
    recoverable via `git log -- results/openwhisk_cpubound_baremetal` /
    `results/knative_cpubound_baremetal` if ever needed for an appendix note
    on the contamination itself).

## Current state (2026-08-05 / 06)
- **Paper/figure cleanup (2026-08-07, post-knative):** the paper and figures were refocused
  **bare-metal-only** per user direction. Codespace results (the 2-vCPU shared-VM origin
  instrument) were REMOVED from all results sections — RQ3, the §5.5 cross-regime table, §7
  threats, §10, §11, and Appendix B's codespace columns — and remain only as a one-paragraph
  origin story in §4.2 (hardware table: "origin instrument, no results cited"). Figures were
  redesigned from two-panel to **four single-story figures with NO dates on axes** (provenance
  in paper captions), legends at the bottom, OW handled on shared axes with labels:
  figure1_core_count (Fn-vs-OF core-count), figure2_four_platform_scatter, figure3_attribution_split,
  figure4_cp_cost_per_inv. §5.1–5.4 re-anchored to the bare-metal Fn run (was codespace-era).
  §5.4 carbon values hand-recomputed with the fixed kWh formula (summary.json files from
  2026-08-05 predate the 2026-08-06 carbon fix and still carry the 1000× bug — do not read
  carbon from them directly). G6 gate row updated to bare-metal cross-session numbers.
- **Bare-metal milestone COMPLETE** (8-core Ubuntu, RAPL available). Results in
  `results/fn_cpubound_baremetal` + `results/openfaas_cpubound_baremetal` (protocol-conformant,
  all gates green). Old saturated-invalid runs preserved in `*_sat_invalid/` (do not cite).
- **RAPL validated.** Machine idle package power measured **4.3 W** (60 s RAPL read, stack up).
  The hardcoded `idle_w=30` gave `rapl_validation_err_pct` ~45% everywhere; with
  `SAQEF_IDLE_W=4.3` validation settles to **4.2–5.5% on Fn** (runs 4–5) and **4.2–8.2% on
  OpenFaaS**, steady-state (OF run_1/2 transient 34.6/25.9; Fn run_1–3 transient 36→19→19 — both
  platforms show the same ~2-run stack/frequency settling). The earlier "0.9–6.1% on Fn" figure
  belongs to the DISCARDED tainted run and must NOT be cited. `SAQEF_IDLE_W` was added to both
  runner scripts (knob only, no measurement-path change).
- **Protocol-conformant bare-metal results** (`c=4 < cpu_count`, `TOTAL=10000`, 5 runs,
  host_sat ~74–77% -> `host_saturated=false`, coverage 100%, delta-map 6/6, delta%~0):
  Fn median `cp_dynamic_share_pct` **10.46** (9.80–11.74, CV 8.4% — run-order drift on fnserver,
  runs 4–5 ~11.7) vs OpenFaaS **7.67** (6.80–7.83, CV 6.4%) -> **gap ≈ 2.8 pp < 5 pp GATE FAILS**.
  (Drift is a BOUNDED warm-up/settling transient, not accumulation: fnserver RSS flat 34→33 MB
  across runs 2–5, CPU% plateaus then dips at run_5 — §31.9.)
- **Regime-dependence is now the finding.** The gap is ~8 pp on the saturated 2-core codespace
  but ~2.8 pp on an 8-core box with headroom; the direction is stable (Fn's share higher) but
  the magnitude collapses. Cause: Fn's per-request CP cost is 1.22 ms under saturation vs
  0.66 ms here (0.56 ms for OpenFaaS both regimes). OpenFaaS's share is conservative (of-watchdog
  proxies inside the function cgroup), so the true gap is even smaller. The 5 pp decision gate
  is therefore NOT machine-invariant; paper must be reframed (per-machine-pair gate + the
  machine-dependence as a contribution).
- **Concurrency sensitivity (c=8, REPEAT=2, `*_c8_quick`): flat within the box, but NOT YET
  citable as a trend line.** Fn 10.47, OpenFaaS 7.62 at c=8 (host_sat ~91–93%) vs c=4 values
  10.46/7.67 — share and gap look invariant to concurrency/saturation on this 8-core box.
  REPEAT=2 is fine for confirming flatness against the already-validated c=4 baseline, but if
  this becomes a headline/citable number, bump to REPEAT=5 for consistency with everything else
  reported.
- **CPU-count effect — CONFIRMED with a controlled, same-instrument experiment (2026-08-06).**
  (Resolves the expert-review catch above.) THIS bare-metal box was cpuset-pinned to 2 logical
  cores via `sudo bash pin_cpuset.sh 0,1 &` (repo script, formalized 2026-08-06 — loops
  `docker update --cpuset-cpus` over every running container every 0.5s for the life of the
  benchmark; a one-shot pin is not enough because Fn's function containers are ephemeral and new
  ones keep appearing under load, unlike OpenFaaS's static swarm replicas), plus `hey` + the
  harness process wrapped in `taskset -c 0,1`. Then Fn + OpenFaaS were rerun with
  today's corrected protocol (fixed spin, `SAQEF_IDLE_W=4.3`, REPEAT=5,
  `SAQEF_CPU_COUNT_OVERRIDE=2`, `SAQEF_HOST_CPU_LIST=0,1` — see pitfall below on why BOTH knobs
  are required, not just one). Result: **Fn 13.91 (13.34–14.07, CV 2.6%) vs OpenFaaS 6.82
  (6.28–6.96, CV 4.1%) → gap 7.09 pp** — up sharply from 2.79/2.85 pp at 4/8 cores on this same
  box, and now much closer to the original (flawed-instrument) codespace gap of 8.77 pp than to
  the clean 8-core gap. Direction confirmed: fewer cores → bigger gap, on the same corrected
  instrument. All gates pass on the citable metric (delta 6/6 ~0%, host_plausible true, coverage
  100%); `host_saturated=true` (98.5–98.7%, expected at concurrency=4 > 2 cores) means latency/QoS
  from this pair is NOT citable, same discipline as the c=8 sensitivity rows.
  - **Reproduced in an independent second session, same day.** Full teardown/redeploy/re-pin
    (Fn's function image tag even changed, 0.0.10→0.0.11, on rebuild — irrelevant, since
    `pin_cpuset.sh` pins by "every running container," not by image tag): session 2 gave Fn 14.08,
    OpenFaaS 7.17, gap 6.91 pp. Across the two independent sessions: Fn 13.91/14.08 (median 14.00),
    OpenFaaS 6.82/7.17 (median 7.00), **gap 7.09/6.91 pp across sessions** — reproduced to within
    0.2 pp. Note: BOTH sessions ran the full REPEAT=5 protocol with per-run gate tables (delta 6/6,
    host_plausible, coverage%) — the n=2 is the count of independent SESSIONS, not runs; each
    session already matches the reproducibility standard applied to every other citable number.
    Do NOT write "CV across sessions" (n=2 is not a distribution). This is the number to cite in
    the paper for the 2-core row.
    Results: `results/{fn,openfaas}_cpubound_2core` (session 1) and
    `results/{fn,openfaas}_cpubound_2core_session2` (session 2).
  - **Correction to the mechanism — it is NOT symmetric.** The retracted hypothesis said "both
    platforms' shares are ~2.3× higher on the 2-vCPU VM." The controlled data says otherwise: Fn's
    share rose sharply (10.46 → 13.91, +33%) while OpenFaaS's share was flat-to-lower (7.67 →
    6.82, −11%). So core-count scarcity inflates Fn's control-plane overhead specifically; it does
    not inflate OpenFaaS's proportionally. **[CANDID for the paper]** the mechanism (fnserver
    scheduling contention under thread starvation vs of-watchdog's per-replica model) is not yet
    explained, only observed — do not assert a cause beyond "asymmetric, platform-specific
    core-scarcity sensitivity" without a follow-up micro-benchmark.
  - **Energy/carbon NOT citable from this experiment.** `rapl_validation_err_pct` ran **43–60%**
    on both platforms at 2 pinned cores — the same magnitude of error the box had under the OLD,
    wrong `idle_w=30` calibration, despite using the correct `SAQEF_IDLE_W=4.3` here. Likely cause:
    the 4.3 W idle baseline was calibrated with all 8 cores idle; under 2-core pinning the 2 active
    cores may run at different turbo/frequency behavior than the flat `busy_core_w` model assumes,
    and/or the 6 un-pinned-but-present idle cores' contribution to package power no longer matches
    the whole-box idle baseline. `cp_dynamic_share_pct` is unaffected (pure cgroup CPU-time ratio,
    no energy model involved) and remains the citable number; do not report absolute J/gCO2 from
    the `*_2core` result sets without re-deriving idle_w for the pinned configuration specifically.
  - **Expert-review disposition (2026-08-06; full review recorded in report §31.7).** (a) The
    core-count effect — 2.79 → 7.0 pp, same instrument, both sessions at full REPEAT=5 with per-run
    gates — is judged an **earned, publishable result**, and the machine-dependence of the 5 pp
    gate is a methodological contribution on its own. (b) **Frequency parity — VERIFIED
    (2026-08-06 evening; §31.8, evidence in `results/freqcheck_evidence/`).** Direct
    `scaling_cur_freq` sampling during a short pinned 2-core run vs a c=4 run: loaded cores 0,1 sit
    at **3.60/3.60 GHz (identical — no per-core DVFS asymmetry)** vs **3.30 GHz** on the c=4 loaded
    cores. The reviewer's turbo-headroom mechanism is REAL (pinned cores ~+9% higher; this is the
    measured cause of the 43–60% RAPL error, plus the un-pinned idle cores downclocking to 400
    MHz), but it does NOT confound the share: Linux cgroup CPU-time accrues via sched_clock /
    invariant TSC — wall-time-on-core, not cycles — so a ratio of CPU-times is frequency-invariant
    by construction, and with cores 0/1 at identical clocks there is no CP-on-faster-core channel
    either. **Original 2-core results stand as final.** (c) Mechanism stays observed-not-explained;
    concrete future work: `perf stat -e context-switches,migrations` on the fnserver process (or
    /proc/<pid>/status voluntary_ctxt_switches) at 2 vs 8 cores.
  - **The 8/2-core mechanism fix, if reusing this method:** `cpu_count()` and `host_cpu_ticks()`
    both default to whole-machine values from `/proc/cpuinfo`/`/proc/stat`. `--cpu-count-override`
    (`SAQEF_CPU_COUNT_OVERRIDE`) alone is NOT sufficient — the first pass at this experiment used
    only that knob and got `host_sat%` of 111–121% (impossible >105%, correctly caught by the
    `host_plausible` gate) because `host_cpu_ticks()` was still summing the aggregate `/proc/stat`
    line across all 8 cores, including background activity (dockerd, kworkers, this very shell) on
    the 6 un-pinned cores. Fix: also set `--host-cpu-list`/`SAQEF_HOST_CPU_LIST` (e.g. `0,1`) so
    the numerator sums only the pinned cores' own `cpuN` lines. Both knobs now exist in
    `saqef_harness.py`/`run_saqef.sh`/`run_openfaas.sh`; the first (invalid) OpenFaaS attempt is
    preserved at `results/openfaas_cpubound_2core_hostmetric_invalid` (its `cp_dynamic_share_pct`
    was fine — only host_sat/host_plausible were wrong — but don't cite its gates table).
- QoS now citable for the first time (host_sat <85%, from the c=4 bare-metal run): Fn p50 6.5 /
  p99 8.9 ms @ 597 rps; OpenFaaS p50 7.2 / p99 12.1 ms @ 532 rps; SLO compliance 1.0 both.
- Codespace numbers (Fn 23.59–24.59 vs OpenFaaS 15.82, gap ~8 pp) remain the saturated-regime,
  flawed-instrument dataset — no longer needed to establish the core-count effect (the 2-core
  bare-metal result above does that on a clean instrument) but still usable as the original
  "regime differs" observation. The expert-review Findings A/B resolutions (GIL concurrency
  parity) still stand independently.
- **OpenWhisk adapter PROVEN end-to-end (2026-08-06 evening; commit after saqef-v2.1).**
  `platforms/openwhisk.py` + `OW_FUNCTION/hello.py` pass deploy/verify/bench/gates and 26/26 tests.
  Deployment = the Apache `openwhisk/standalone:nightly` image: the whole control plane is ONE
  container (`cp=openwhisk`); the invoker spawns per-activation action containers from
  `action-python-v3.11` (`fn_images`). Isolation: any swarm service or `fnserver` forbidden
  (data-driven contract). Standalone's two `wsk0_*_prewarm_nodejs20` containers are invoker
  warmup (excluded from fn_images, idle ~0%, land in unclassified fail-open).
  - **Frozen-GET constraint:** the measurement harness is GET-only, OpenWhisk's native invoke is
    POST → the action is a WEB ACTION: `GET http://127.0.0.1:3233/api/v1/web/guest/default/hello`
    → HTTP 204 (blocking). Verify 100/100, `function_cpu_ms_per_inv=5.5` (budget MATCHES).
  - **Rate-limit fix (expensive to learn):** the standalone defaults to **60 invokes/minute per
    action** (`standalone.conf: limits-actions-invokes-perMinute=60`) → ~40% HTTP 429 at
    concurrency 4, which silently corrupts verify AND bench availability. The key is enforced by
    WhiskConfig from `whisk-config.limits.actions.invokes.perMinute` (the `whisk-config.`-prefix
    dotted path — standalone.conf's kebab keys under `whisk.config` are a separate UNREAD path).
    Override via `JVM_EXTRA_ARGS` (`/init` passes it into `java`): raised per-minute + concurrent
    + trigger-fire limits to 1e9/1000. After the fix: 300/300 → 204.
  - **Static docker CLI shadow-mount:** the standalone ships a 2018 docker client (API 1.38) that
    host dockerd ≥29 rejects; `deploy()` mounts a modern STATIC CLI at `/usr/bin/docker`
    (`vendor/docker`, gitignored, fetched from download.docker.com, cleaned up after extraction).
  - **Proof bench** (quick, c=4, repeat=2, total=2000): `cp_dynamic_share_pct` **87.5 / 81.1**
    (run_1/run_2), delta 0.0%, CPmapped 1/1, coverage 100%, availability 1.0, unclassified 0.0.
    OpenWhisk's CP uses ~4–5× the function CPU steady-state (per-activation `docker logs` in the
    standalone's DockerCliLogStoreProvider) → a real, defensible contrast vs Fn ~11.6 / OF ~7.7
    shares. NOT yet a citable paper number: needs a full REPEAT=5 run + gates before citation, and
    decide in the paper how much of the standalone's log-store overhead is "control plane" vs
    deployment-mode artifact.
  - **Full REPEAT=5 run (2026-08-07, `results/openwhisk_cpubound_baremetal`):** `cp_dynamic_share_pct`
    **82.54** (median; runs 2–5 flat 82.0–82.6, CV 2.1%, IQR 0.26; run_1 transient 86.24), slo_compliance
    1.0, availability 1.0, fn_cpu_s rock-solid 57.3–57.5 s, all gates green (delta ~0, CPmapped 1/1,
    coverage 100%, host_plausible true, host_saturated false ~64–71%). The standalone's CP burns
    262–365 CPU-s/run vs ~57 s function CPU — **~4.6× the function CPU, i.e. share 82.5 vs Fn 12.3 /
    OF 7.5**. OpenWhisk is the "heavy control-plane" pole of the study. **Caveats:** (a) box was
    NOT quiet (opencode agent at ~2.8 cores — see TROUBLESHOOTING_RUNBOOK.md §1): the share is
    contention-robust and flat across runs even as host load rose, but a quiet-box rerun is the
    clean provenance for citation; (b) `rapl_validation_err_pct` 36.3% — the standalone JVM's power
    draw does not fit the linear busy-core energy model, so OW **energy/carbon is NOT citable**
    (share is, being a pure cgroup CPU-time ratio); (c) throughput declined across runs (47→25 rps)
    as host load accumulated — QoS percentiles from this session are agent-contaminated, rerun for
    latency. Per-invocation CP cost ≈ 262 CPU-s / 10000 inv ≈ **26 ms CPU per invocation** (vs
    fnserver 0.79 ms / of-watchdog 0.56 ms) — the standalone's `docker logs` log-store and JVM
    orchestration dominate. Deployment-mode decision for the paper: how much is "control plane" vs
    standalone-emulator artifact (same § as the OW adapter notes).
  - `.gitignore` had CRLF line endings (broke the `vendor/docker` pattern); rewritten LF +
    `vendor/` + `results/*_quick*/` guard. `results/verify.json` is a tracked working artifact —
    revert before committing (OpenWhisk verify overwrote it — now fixed: verify outdir defaults to
    `results/<platform>_verify`, so cross-platform verify can no longer collide).
- **Knative adapter DONE + full REPEAT=5 run (2026-08-07, `results/knative_cpubound_baremetal`).**
  `platforms/knative.py` + `KNATIVE_FUNCTION/` (the identical ~5 ms spin handler) deploy/verify/
  bench/gates clean; 38/38 tests (adapter schema now includes knative; byte-identical argv tests
  for bench + verify). Deployment = Knative Serving v1.23 + Kourier on k3s v1.36.3 (docker
  runtime, so every pod is a docker container the harness can classify), function = the hello
  Service with **static scaling** minScale=maxScale=16, containerConcurrency 4 (GIL parity).
  - **Result: median `cp_dynamic_share_pct` = 13.99 (13.22–14.24, CV ~2.8%)** — statistically
    tied with Fn's 8-core value (11.6–12.9 box-drift range, today's crosschecks 12.27–12.92) and
    ~1.9× OpenFaaS (7.53). So the ordering on this box is now OF 7.5 < Fn ≈ Knative 12–14 <
    OpenWhisk 82.5. All gates pass on the citable share (delta ~0, CPmapped 15–17/15–17 ok,
    coverage 100%, host_plausible true); `fn_replicas` shows 32 (16 user-container + 16
    queue-proxy — the `_count_fn_containers` gate display now matches fn_containers by name for
    platforms whose function image shows only a bare digest).
  - **Attribution map (must be documented in the paper, asymmetric vs OF):** Knative's *fn*
    bucket includes the per-replica **queue-proxy sidecar** (on the request path, same honesty
    caveat OF solves by putting of-watchdog inside the function cgroup) AND the function; its *CP*
    bucket includes **kourier-gateway + svclb-kourier** (the data-plane gateway) + activator +
    controller/autoscaler/webhook/net-kourier-controller. OpenFaaS counts its proxy in fn, so the
    true OF-vs-Kn control-plane gap is even smaller than the raw 7.5-vs-14 share suggests. Per-
    invocation CP cost ≈ 10.5–11.4 CPU-s / 10000 inv ≈ **~1.1 ms CPU per invocation** (fnserver
    0.79 / of-watchdog 0.56 / OW 26).
  - **k3s embedded control plane is measurement-invisible:** apiserver/etcd/scheduler/
    controller-manager run INSIDE the `k3s server` process, not as docker containers → their CPU
    lands in `host_cpu_sec` residual, NOT in the CP bucket. Document as a boundary (a production
    cluster would bill it as shared infra); the container-visible CP (knative-serving + kourier)
    is what the share measures.
  - **Idle baseline finding:** calibrated idle package watts with the knative stack up (60 s RAPL,
    zero traffic) = **11.14 W** vs 4.3 W bare — the k8s-native platform carries a ~7 W always-on
    baseline (16 warm replicas + 32 proxies + knative-serving + kourier). This is itself a citable
    sustainability observation, but it is why `rapl_validation_err_pct` is 22.6–32.2% (the linear
    busy-core energy model does not fit a stack whose idle draw already exceeds the flat model) →
    knative **energy/carbon NOT citable** (share is).
  - **QoS NOT citable from this session:** host_sat 84.3–87.2% (3/5 runs ≥85, flagged) — box not
    quiet (opencode agent ~2.8 cores). slo_compliance 1.0, p50 8.3–8.7 / p99 13.8–14.8 ms @
    431–452 rps, but those percentiles are agent-contaminated; quiet-box rerun before citing
    latency (TROUBLESHOOTING_RUNBOOK.md §1 + §10).
  - Run knative (bench only) with: `python3 saqef run --platform knative --total 10000
    --concurrency 4 --duration 300 --warmup 20 --repeat 5 --idle-w <calibrated> --out
    results/<name>`; deploy/verify first. Calibrate `--idle-w` with the stack up — it is ~2.6× the
    bare-box value.
- **External-review findings fixed (2026-08-06; committed 2026-08-07 as `57f8a6f`).**
  - **🔴 Carbon ×1000 unit bug — FIXED in `saqef_harness.py`.** `carbon_gCO2` used `J/3600` (Wh)
    with `CI` in gCO2/kWh → every gCO₂ figure was 1000× too high. Formula now `J/3.6e6` (kWh).
    `energy_J`, `cp_dynamic_share_pct` (unit-free), `rapl_validation_err_pct` NEVER affected. Paper
    values corrected (§5.4 145 µg → 0.145 µg; 7.5 mg → 7.5 µg; v9.3-era 39.6 mg → ≈39.6 µg).
  - **🟠 Isolation guard is now data-driven.** `assert_platform_isolation()` in the harness consumes
    `--forbidden-services`/`--forbidden-containers` argv (OpenWhisk was silently falling through
    the old hardcoded fn/openfaas elif to `(True,"")`). Adapter `isolation` fields now enforced at
    measurement time on all three platforms.
  - **🟡 `cmd_verify` default outdir** → `results/<platform>_verify` (was shared `results/`).
  - **🟡 `saqef` deploy comment** corrected (OpenFaaS = `docker service create`, not faas-cli).
  - **🟡 `run_openfaas.sh` replica default 4 → 16** (protocol is 16 static replicas).
  - **Replica audit (reviewer's "10" was a misread):** OpenFaaS runs are protocol-clean — baremetal
    and regression = 16 replicas (16/16/16/16/16 across runs 1–5), 2-core = 4 (2×N protocol),
    legacy codespace = 4/1 (pre-citable). "10" was Fn's *dynamic function-container count*.
  - **⚠️ `saqef_harness.py` NOW DIVERGES from `saqef-v2.0-frozen`** (carbon fix + isolation port).
    Fixed + committed 2026-08-07 (`57f8a6f`). **Regression rerun executed live 2026-08-07** (c=4,
    10000, REPEAT=5, idle_w=4.3): OpenFaaS **7.53 PASS** (ref 7.67, dev 0.14 pp); Fn **12.27 vs ref
    11.60 → dev 0.67 pp > 0.5 pp FAIL**, but proven box drift by same-day old-runner A/B
    (`results/fn_cpubound_crosscheck2` = **12.92**) — the refactor is faithful (see
    TROUBLESHOOTING_RUNBOOK.md §2/§3). The 11.60 reference is stale for this box; recalibrate it
    from a quiet-box old-runner A/B before trusting the Fn gate (OF's 7.67 stays). Measurement-path
    bytes beyond the two edits are untouched.

## Bare-metal protocol (proven on this box)
1. `sudo bash setup_baremetal.sh` (docker + CLIs + RAPL readable by the user).
2. Calibrate idle watts: 60 s RAPL read with the platform up, zero traffic -> `SAQEF_IDLE_W`.
3. Run OpenFaaS FIRST (its function service must be gone before Fn — see pitfall below):
   `SAQEF_REPLICAS=16 SAQEF_CONCURRENCY=4 SAQEF_TOTAL=10000 SAQEF_REPEAT=5
    SAQEF_IDLE_W=<W> SAQEF_OUT=results/openfaas_cpubound_baremetal sudo bash run_openfaas.sh all`
4. `sudo docker stack rm openfaas` **and** `sudo docker service rm hello`, then Fn:
   `SAQEF_CONCURRENCY=4 SAQEF_TOTAL=10000 SAQEF_REPEAT=5
    SAQEF_IDLE_W=<W> SAQEF_OUT=results/fn_cpubound_baremetal sudo bash run_saqef.sh all`
5. `gates` must show `host_saturated=false` (else latency not citable) and the share comparison
   decides the paper question.

## Core-restricted variant (emulate an N-vCPU box on this bare-metal machine)
Confirms whether an effect is core-count-driven by holding the instrument fixed and varying only
core count — used 2026-08-06 to confirm the CPU-count effect (see Current state). Steps, for N=2
cores (repeat per-platform, OpenFaaS first, same teardown discipline as above):
1. Deploy + scale normally (`SAQEF_REPLICAS=2×N`), then start the pin daemon:
   `sudo bash pin_cpuset.sh 0,1 &` (pick N adjacent **physical** cores via `lscpu -e` — CORE
   column, not just CPU id, or you pin to two hyperthreads of one physical core by accident).
2. Run the bench wrapped in `taskset`, with BOTH override knobs set so the harness's own
   saturation ceiling and busy-tick numerator agree with the pin (see pitfall below — one knob
   alone silently mis-measures host saturation):
   `sudo env SAQEF_CPU_COUNT_OVERRIDE=N SAQEF_HOST_CPU_LIST=0,1 SAQEF_IDLE_W=<W>
    SAQEF_CONCURRENCY=4 SAQEF_TOTAL=10000 SAQEF_REPEAT=5 SAQEF_OUT=results/<name>_Ncore
    taskset -c 0,1 bash run_openfaas.sh bench` (or `run_saqef.sh bench`).
3. Kill the pin daemon (`kill` the backgrounded PID) once both platforms' bench runs are done.
4. `SAQEF_OUT=results/<name>_Ncore bash run_openfaas.sh gates` (gates ignores `SAQEF_CPU_COUNT_OVERRIDE`/
   `SAQEF_HOST_CPU_LIST` — they're baked into the already-written summary.json, not re-read).
5. Energy/carbon from a pinned run are NOT citable without re-deriving idle-w for that specific
   pin (see Current state caveat) — `cp_dynamic_share_pct` (cgroup CPU-time ratio) is unaffected
   and is the only citable number.

## Key pitfalls (learned, expensive)
- GIL concurrency parity: static replicas, never single-replica for the headline.
- Allowlist: `--fn-images hello` (the DEPLOYED image, not the base runtime image).
- Host-window alignment: `host_saturation_pct`/`host_plausible` are measured over the host's own
  sampling window (`host_window_s`, v9.10); v9.11 reads the host counter at `t0` so it equals
  `wall_s`. A 112%-style break is a window artifact, not a real anomaly.
- Delta-check: verify 6/6 `ok` AND delta% ~0 in the gates table before committing results.
- OpenFaaS autoscaler lag: scale statically before the run.
- **`docker stack rm openfaas` does NOT remove the `hello` function service** (it is deployed
  outside the stack). Its replicas fold into Fn's `fn_cpu` via the `hello` image allowlist and
  taint the Fn run — always `docker service rm hello` before Fn. **Enforced since 2026-08-06
  (§31.9):** `saqef_harness.py` now asserts platform isolation at the top of every bench run —
  Fn sessions fail loud if ANY swarm service is up, OpenFaaS sessions fail loud if `fnserver` is
  up. The rule is a precondition check, not a measurement-path change; no citable run invalidated.
- **Calibrate `SAQEF_IDLE_W`** to the machine's measured idle package watts (60 s RAPL read,
  stack up, zero traffic) or `rapl_validation_err_pct` stays ~45% on bare metal.
- Codespace git push needs device-flow `gh auth login` after `unset GITHUB_TOKEN`.

## Durable knowledge (read these for depth)
- `SAQEF_TECHNICAL_REPORT.md` — full session log, numbered sections (24–29 = OpenFaaS saga).
- `SAQEF_PAPER_DRAFT.md` — paper structure + corrections log (v9.3…v9.11, methodology fixes).
- `OPENFAAS_SETUP.md` — OpenFaaS deployment + protocol steps (incl. §6 Step 1.5 concurrency parity).
- `pin_cpuset.sh` — live cpuset-pinning daemon for the core-restricted variant (see that section).

## Next-session plan (agreed 2026-08-06, user + external expert greenlighted)
**Sequencing — do in this order:**
1. ~~**Figures from committed data**~~ — **DONE (2026-08-07), redesigned (2026-08-07).**
   `figures/make_figures.py` (data-driven, matplotlib, harness stays stdlib-only): **four
   single-story figures, no dates on axes, legends at the bottom** (provenance lives in the
   paper captions, not axis labels):
   - figure1_core_count — the Fn-vs-OF core-count effect (8-core quiet vs 2-core pinned, same
     instrument), 5 pp gate line + gap arrows (2.79 → 7.00 pp).
   - figure2_four_platform_scatter — per-run shares, all four platforms, 8-core same-day
     (OF 7.53 < Fn 12.27 [11.52–12.96] ≈ Knative 13.99 < OW 82.54); every per-run value shown
     (n=5/session, Fn 3 sessions = 15 points), black tick = reported median.
   - figure3_attribution_split — CP / fn / unclassified CPU-time, four platforms.
   - figure4_cp_cost_per_inv — control-plane CPU per invocation (of-watchdog 0.56, fnserver
     0.79, Kn ~1.1, OW ~27 ms).
   Adding a platform/regime = one dict entry in REGIMES + rerun.
   Regenerate: `python3 figures/make_figures.py`.
2. **Adapter refactor** (fn + openfaas) WITH an automated **regression gate** before the new
   plumbing is trusted: `saqef regression` reruns c=4 8-core both platforms and FAILS if median
   `cp_dynamic_share_pct` deviates > ~0.5 pp from known-good **11.60 / 7.67**. Do NOT trust by code
   review alone; prove by rerun.
   - **fn reference recalibrated 2026-08-06 (was 10.46).** The refactored CLI's first full
     `saqef regression` passed OpenFaaS (7.55, dev 0.12 pp) but "failed" Fn at 11.96. A same-day
     A/B with the OLD runner (`run_saqef.sh all`, fresh setup, results/fn_cpubound_crosscheck)
     measured **11.60** — same flat plateau, so the drift is the box, not the refactor (argv is
     byte-identical). Cause: fnserver per-request CP cost 0.61→0.75 ms vs the 2026-08-05 runs
     (which ramped 9.8→11.7). fn_cpu_s identical (~56.7 s) both days. Full context in
     `metrics/cpubound.json` → `regression.reference_notes`. The gate is day-sensitive for Fn;
     it catches refactor-scale breaks (100%/0-success bugs), not ~0.5 pp box noise.
3. **OpenWhisk adapter — DONE (proven in isolation on 2026-08-06 after the framework passed
   regression; see Current state).** If reused for a citable number: full REPEAT=5 run + gates, and
   decide how much of the standalone's per-activation log-store overhead is "control plane".

**Architecture decisions (agreed):**
- Adapter-per-platform (`platforms/<name>.py`), metric-as-config (JSON recipes under `metrics/`,
  NOT scripts — metrics are modes of the existing harness), single CLI entrypoint:
  `saqef run --platform X --metric Y --iterations N --total ... --concurrency ...`.
- Adapter protocol must allow **bespoke sampling/hooks**, not just different flag values — a future
  platform may not express its control plane as container names (systemd service, non-container CP,
  dynamic function count). Design the adapter interface for that now.
- **`assert_platform_isolation` becomes data-driven — THE point of the refactor.** Each adapter
  declares `expected_containers` / `forbidden_containers` / `forbidden_services`; the guard
  consumes them. Today it is hardcoded elif for fn/openfaas (`saqef_harness.py:747`) — the exact
  copy-paste-drift surface that caused the original taint bug.
- Do NOT let the future-GUI idea shape the design. Build for 3 platforms, not imagined N. A clean
  scriptable CLI is good practice regardless; if a real GUI requirement appears, its shape will be
  known then. (GUI would only ever call the same `saqef` CLI.)
- **Harness measurement path stays byte-identical** during refactor; adapters are thin config +
  deploy/teardown orchestration. Old `run_saqef.sh`/`run_openfaas.sh` stay working until the new
  path passes regression — no capability loss.

**"Do not regress" manifest — each previously-fixed bug must still be enforced after refactor:**
1. hello-allowlist overlap → isolation guard (data-driven, mandatory per-adapter fields).
2. `docker service rm hello` ordering / OpenFaaS-leftover taint.
3. GIL concurrency parity: static replicas, never single-replica.
4. `SAQEF_IDLE_W` calibrated per platform (idle-wats, not the 30 W default).
5. host-window alignment (v9.10/v9.11) + `host_plausible` gate.
6. delta-check 6/6 `ok`, delta% ~0; coverage ≤100%; `unclassified_cpu_s` informative (fail-open).
7. Count-bound runs (TOTAL), `--duration` only a safety cap.
Make each of these a *mandatory* field/assertion in the adapter contract so a fixed bug cannot
silently vanish. Existing pitfalls list above remains authoritative.

## Handoff log — user out (2026-08-07 evening; box NOT quiet — an agent was burning ~2.8 cores)

**Do NOT start any benchmark while an agent is running.** This list is the accepted plan for the
next human/quiet session. Order matters; the two reruns below are the only new measurements the
paper still needs.

### Confidence tiering (updated 2026-08-08 after the quiet-box OW/Kn reruns)
- **Defensible as-is (measurement-path + multiple sessions):** OpenFaaS 8-core share ~7.5–7.7%
  (four independent measurements now, incl. today's 7.62, within ~0.15 pp); the **2-core pinned
  gap ≈ 7.0 pp** (Fn 14.00 vs OF 7.00, two independent sessions, frequency-parity verified,
  invariant-TSC ratio → DVFS-safe); Fn-vs-OF 8-core direction (Fn higher, below the 5 pp gate);
  the fixed carbon values (0.145 µg/inv etc., hand-recomputed with J/3.6e6); OF per-inv CP 0.54 ms
  / Fn 0.66–0.79 ms. **NEW: OpenWhisk 80.23 and Knative 11.40 share + QoS**, quiet-box, all gates
  green (see "Current state" above) — promoted out of "fishy" 2026-08-08.
- **Still open:** how much of OpenWhisk's ~80% is "control plane" vs standalone-emulator
  `docker logs` artifact is unchanged by quieting the box (the share barely moved, 82.54→80.23,
  which argues AGAINST it being mostly a deployment artifact — a true `docker-logs`-per-activation
  cost should scale with wall-clock/scheduling noise more than this did). Kn's queue-proxy-in-fn /
  kourier-in-CP attribution asymmetry + invisible k3s apiserver/etcd remain real boundaries to
  document, not defects to fix.
- **NOT citable by design:** energy/carbon from 2-core runs and OpenWhisk (RAPL error 45–58%,
  stable across contaminated AND quiet sessions — structural JVM/linear-busy-core-model mismatch,
  confirmed NOT a noise artifact). **Knative energy/carbon is now provisionally citable** (rapl err
  2.5–7% quiet steady-state, was thought structural but wasn't — see "Current state").
- **Biggest scientific risk to pre-empt:** the 8-core result *failed* the 5 pp gate; the 2-core
  rescue came after. Framed honestly as regime-dependence, but the paper must lead with the
  controlled same-instrument defense (and the Fn box drift 10.46→12.92→11.01 needs a stated cause
  or an explicit box-state caveat, not a footnote — today's 11.01 is the widest low end yet).

### 1. Quiet-box rerun runbook for OW + Kn — DONE 2026-08-08, see "Current state" above.
Findings: OW share barely moved (82.54→80.23, contamination was NOT the story), Kn share moved
meaningfully (13.99→11.40, contamination partly WAS the story there), Kn energy/carbon verdict
reversed to citable, OW energy/carbon confirmed still not citable. Two real bugs found+fixed along
the way: a k3s TLS-cert clock-skew bug that blocks Knative after every reboot (see TROUBLESHOOTING
runbook), and a hardcoded Knative revision name in `platforms/knative.py` `deploy()` (harmless —
masked by an existing fallback — but fixed for robustness). `results/verify.json` was NOT clobbered
this time (the earlier fix holds: verify now defaults to `results/<platform>_verify`).

### 2. Fn-drift investigation (mechanism; share values themselves are fine)
The 11.60-vs-12.92 range is a *box-state* drift (RSS flat, fnserver per-request CP 0.61→0.75 ms),
observed-not-explained. Concrete plan: `perf stat -e context-switches,migrations` on the fnserver
process (or `/proc/<pid>/status` voluntary_ctxt_switches) at 8 cores vs 2 pinned cores, plus the
recalibration A/B: rerun OLD runner `run_saqef.sh all` on a quiet box same-day to re-anchor the
regression reference (AGENTS.md §"fn reference recalibrated" — do NOT trust 11.60 or 12.92 as a
constant; pick the fresh quiet-box value and note it is day-sensitive). The regression gate's job
is to catch refactor-scale breaks, not ~0.5 pp noise.

### 3. Codespace disposition — DECISION (user asked 2026-08-07; agreed)
- **Remove ALL codespace results/findings from tables/figures.** Already done 2026-08-07 — the
  only remaining mentions are (a) the §4.2 hardware-table origin row ("origin instrument, no
  results cited") and (b) the §7.4 contention-contamination lesson (what a saturated co-tenanted
  box does to QoS) — both narrative, zero codespace numbers.
- **The ONE thing worth keeping is the story, not the numbers:** the origin instrument's
  saturated-regime *direction* (gap larger under core scarcity) was later confirmed by the clean
  2-core pinned experiment — worth one sentence in §4.2 as narrative arc ("the direction observed
  on the origin instrument was confirmed on a clean instrument"), NEVER with codespace figures.
  The flawed-instrument lesson (co-tenancy + no RAPL + saturation ⇒ reproducible-but-worthless
  QoS) is a methodology contribution for §7, not a result.
- **Do not re-add codespace columns** to any table, and do not cite Fn 23.59–24.59 / OF 15.82 / gap
  ~8 pp anywhere. The clean 2-core result supersedes them on a defensible instrument.

### 4. Methodology rewrite — IN PROGRESS (2026-08-07, partially applied, uncommitted)
Session applied: §4.1 design overview (clean two-scope framing, removed stale 1.9%), §4.2 kept
origin table + hardware-dependence note, §4.3 workload (removed N≥10 contradiction; added the
"count-bound + fresh CP" rationale), §4.4 instrumentation (stripped v9.x fix-chatter, kept the
design rationales), §4.5 model (removed historical-bug comment + "pending" RAPL + stale 30 W;
calibrated idle-w now per platform), §4.6 (N=5 + session medians + GIL parity), §4.7 gates table
(stripped v9.x markers). **REMAINING:** §5.5/§5.6 numbers vs prose pass; a "how to read this
table" note for §5.1–5.4 QoS/attribution tables and Appendix A/B self-sufficiency pass; verify no
codespace-era protocol numbers anywhere in §4–§5 (grep clean as of this session: only the §4.2
origin row). Then update this file's "Current state" block, commit, push.

## Future-work proposals (2026-08-07, user + agent agreed; design-only, no measurements yet)

**Ordering rule:** all of these need a QUIET box and come AFTER the OW/Kn quiet reruns (§1 of the
handoff) and the Fn-drift `perf stat` check. Nothing here is a priority over finishing the paper's
remaining prose/tables.

### A. Workload variation (highest new-science value)
**Question it answers:** is the share / platform ordering a property of the *platform*, or of the
5 ms CPU-bound spin specifically? A reviewer's first "so what" is "does this generalize?"
**Design:** same protocol (c=4, TOTAL=10000, REPEAT=5, 16 static replicas, idle-w per platform),
four platforms, three variants:
- spin 1 ms and 20 ms (test the workload-anchoring sensitivity; the 5 ms spin was chosen because
  a near-free function makes both ratio terms tiny — does the share hold at 1 ms or 20 ms?),
- an I/O-bound function (e.g. `time.sleep(0.005)` — same wall duration, no busy CPU) to separate
  "orchestration for the request" from "CPU anchoring", and
- optionally a mixed 50/50 split to simulate a realistic service mix.
**Success criterion:** ordering (OF < Fn ≈ Kn < OW) and the 2-core gap direction survive across
variants; if the share collapses at 1 ms the workload-anchoring claim in §4.3 needs strengthening.
**Cost:** pure function-image swap + rerun; no harness changes (the adapter recipe takes the new
image via config). Keep old `hello` variants under `functions/` or a `WORKLOADS` dict.

### B. Minimal strawman control plane (the "floor")
**Purpose:** anchor the platform shares to a lower bound. Fn ≈ 12%, OW ≈ 82% are meaningless
until we know the *minimum possible* share for the same handler on the same box. OpenFaaS (7.5%,
of-watchdog proxy inside the function cgroup) already approximates it, so expect the floor to sit
**below or near OF**; then "real orchestration tax" = platform − floor (e.g. Fn 12 − floor ≈ 4–5
pp) becomes a citable number, and OW − floor is the emulator/deployment-mode overhead.
**Design (dumbest possible):** nginx or haproxy in front of 16 *static* function replicas (the
same 5 ms handler, plain HTTP server, no FaaS runtime). No scheduling, no spawn/freeze, no logs.
CP = the proxy container only. Same protocol + gates. Caveat to record: a dumb reverse proxy is
NOT a "serverless control plane" (no scale-to-zero, no scheduling) — its value is strictly as a
reference floor, and it must preserve GIL parity (16 replicas, same handler). If the proxy floor
lands within noise of OF, that *strengthens* OF's honesty claim; if it lands far below, it shows
how much headroom a "lean" serverless CP has.
**Deliverable:** one `platforms/proxy.py` adapter (deploy nginx + static replicas) + a
`strawman` metric recipe. This is the natural seed for the carbon-aware CP in §C.

### C. Carbon-aware control plane — the future-paper direction (user's ask)
**Yes — we found concrete, fixable things, all measurable with this framework:**
1. **Central-orchestrator contention under core scarcity (Fn).** Fn's share rose 10.46 → 13.91
   (+33%) at 2 pinned cores while OF's per-replica model stayed flat (7.67 → 6.82). Mechanism
   observed-not-explained (fnserver thread starvation), but the *design* lesson is concrete:
   co-locating orchestration in per-replica proxies (of-watchdog style) is both cheaper
   (0.56 vs 0.79 ms/inv) AND immune to the core-scarcity amplification. → **Design principle 1:
   keep control-plane work per-replica/co-located, avoid a single central orchestrator on the
   request path.**
2. **Per-activation log-store reads (OpenWhisk).** OW's CP burns ~26 ms CPU *per invocation* and
   the share is 82.5% — dominated by the standalone's `docker logs` read per activation
   (DockerCliLogStoreProvider). Reading full logs per activation is O(container-spawn + log I/O)
   per request — pure waste, zero QoS value. → **Design principle 2: structured/streaming log
   collection (OTel-style), never per-activation `docker logs`; log volume is a first-class
   carbon metric.**
3. **Always-on idle baseline (Knative) — direction confirmed, magnitude UNRESOLVED (see 2026-08-08
   evening rerun above, finding #13).** Kn's idle draw with 16 warm replicas up has read 11.14 W,
   7.01 W, and 4.91 W across three single-sample calibrations (vs 4.3 W / 4.15-4.23 W bare, the
   latter also single/double-sampled) — direction is consistently "some premium over bare metal"
   but the size of that premium is NOT yet pinned down by a properly repeated measurement. Before
   citing a specific W figure for this design principle, calibrate idle-w with N≥3 repeated reads
   per condition. A scale-to-zero / low-idle policy is still a real carbon lever in direction, but
   it trades against cold-start energy+latency. → **Design principle 3: an autoscaler with an
   explicit idle-watts budget and a carbon-aware cold-start policy, evaluated with a properly
   repeated version of the idle-w measurement methodology this
   study built (§4.5).**
**Proposed future paper:** design + implement a minimal carbon-aware control plane embodying
principles 1–3, then measure it against Fn/OF/Kn/OW with THIS harness (same box, same gates).
The measurement framework — not the control plane — is the transferable artifact. It also gives
the "improves QoS" angle: principle 1 removes contention-spike latency, principle 3 can be tuned
to a QoS SLO budget.

### D. Universal adapter — clarify the scope before starting
The **measurement** side is already universal (metric-as-config recipes in `metrics/` + the
data-driven isolation contract); deployment/verify will always be platform-bespoke (swarm vs k8s
vs standalone are structurally different). A "universal adapter" therefore means: *one* adapter
that can measure *any containerized control plane* from a JSON recipe (CP/fn image allowlists +
isolation fields) without new Python code. All four current platforms are containerized, so this
is achievable and would make platform N a 30-minute config job instead of a coding task. Do NOT
start until the paper is out; this is refactor-for-refactor's-sake otherwise. If the strawman
(§B) is built as `platforms/proxy.py`, that's already a 5th test of the adapter contract.
