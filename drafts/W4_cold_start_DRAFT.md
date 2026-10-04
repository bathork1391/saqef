# W4 cold start: DRAFT (not pre-registered)

Written 2026-10-04, while the battery charged for §31.15. **Not frozen.** It becomes runbook §32 with
the line `Arm/Workload W4: pre-registered` only after §31.15 is adjudicated, so K1/K2 can still inform
it. No W4 data exists. Open decisions are marked **[DECIDE]**.

## Question
What does it cost a platform, in control-plane CPU, function CPU, availability and latency, when a
burst arrives at an **empty** function pool instead of a warm one? Paper claim: "cp cost per cold
start" (runbook §26 step 7), plus the W3 loose end (§31.9 B, §31.14 A): Fn loses 0.4–2.3 % of
requests to fast HTTP 500s while its pool grows, and none once it has grown.

## Design idea (simplest that answers it)
**Within-session A/B per platform, both arms the same W3 b100 load** (30 bursts × 100, 1 s gap,
TOTAL 3000), so bursts 2–30 of every run are an in-run warm reference:

| arm | before each run | runs |
|---|---|---|
| `cold` | **pool reset to zero** (below), then the window starts; no verify call after the reset | `--repeat 5` |
| `warm` | nothing; pool left at the size the previous run grew ("saturated reference arm") | `--repeat 6 --discard-warmup 1` |

Order alternates per platform (cold-warm, warm-cold) so neither arm is always first.

**Pool reset per platform** (natural expiry where the platform has one; checked "0 function
containers" before the window, else the run is gated out):
- **Fn:** wait out the hot-container idle timeout (30 s) + margin, i.e. 45 s; fnserver stays up.
- **Knative:** `minScale 0` (maxScale 16, cc 4 unchanged) for this workload only; wait for 0 pods
  (grace + stable window, ~90–120 s). Activator buffers the cold burst.
- **OpenWhisk standalone (driver log store):** idle containers live ~10 min, too long. Force cold
  with `wsk action update` (invalidates the action's containers). **[DECIDE / smoke test]**
- **OpenFaaS (swarm, gateway 0.8.3):** CE has no idler. Scale `hello` to 0 through the API and rely
  on the gateway's scale-from-zero. Unverified on this old stack. **[DECIDE: include if a smoke test
  shows 0 → N works, else drop OF from W4 with the reason stated]**

## New instrument items (the expert's W4 list)
1. **Per-run pool reset** as above, with a timeout, logged.
2. **Pool-state fields** in `summary.json`: function containers at window start, at end, created in
   window, and time of first 2xx after the window starts.
3. **Platform log harvest** per run: `docker logs --since` for fnserver / activator+autoscaler /
   openwhisk / gateway+faas-swarm, saved next to `hey.csv` (§31.11: Fn's 500s had no logs).
4. **First-burst split:** every metric for burst 1 vs bursts 2–30 (hey.csv already has `burst`).
5. **Saturated reference arm** = `warm` above.
All behind env flags, default off, so closed sessions are untouched. `SAQEF_SAMPLER_DEFER_NAMES=1`
on (container creation is the point here). Power gate (check 8) as committed.

## Anchors from disk (run_1 of W3 b100/b500 is the nearest thing to a cold pool; post hoc)
| leg (run_1 unless said) | burst 1 p50 / p99 (ms) | bursts 2–30 p50 / p99 | errors b1 / later |
|---|---|---|---|
| Fn b100 (`burst_`, 2 attempts) | 99–101 / 122–128 | 91–94 / 183–195 | 7–8 / 6–9 |
| Fn b500 (`burst_`, 2 attempts) | 248–253 / 448–454 | 334–367 / 654–783 | 27–31 / 39 |
| Fn b100 / b500 (31.13, **capped**) | 107 / 150; 563 / 819 | 114 / 243; 447 / 780 | 8 / 12; 53 / 45 |
| Kn b100 / b500 | 66 / 99; 263 / 433 | 63 / 107; 245 / 417 | 0 / 0 |
| OF b100 / b500 | 63 / 80; **1135 / 2222** | 50 / 81; 277 / 1347 | 0 / 0 |
| OW b100 / b500 | **565 / 1013; 2730 / 4630** | 231 / 684; 1388 / 3126 | 0 / 0 |
| runs 2+ (all platforms) | ≈ rest | | 0 |

None of these pools was empty: Fn started at ~20 containers (verify pre-grows it, §31.11), OF and
Kn at 16 static replicas, OW at its 2 containers. So these are lower bounds on a real cold start.
Fn's function CPU per created container: ~0.14 s (§31.11, post hoc).
Fn's errors are fast rejections, so its burst-1 latency is **not** higher; OF and OW show it as
latency instead. Different failure modes, which W4 should report per platform.

## Candidate predictions (numbers to fix at pre-registration)
- **C1 availability, burst 1, cold:** Fn < 0.99 (it rejects while creating); Kn, OW ≥ 0.999
  (they queue). OF: no prediction unless included.
- **C2 latency, burst 1, cold vs warm:** p50 ratio ≥ 2 on Kn and OW (activator / container start);
  Fn not predicted (rejections hide it).
- **C3 cp cost per cold start:** (cold − warm) cp CPU per run / containers created > 0 on every
  platform, reported in ms per container; OW expected highest (docker CLI per container, §28.9 D).
- **C4 recovery:** bursts 2–30 of cold runs within ±15 % of the warm arm's p50 (cold is a
  first-burst effect only).
- **C5 (descriptive):** function CPU per created container vs the 0.14 s Fn anchor.

## Rough cost
6 legs: calibration ~25 min + 6 legs × ~6–8 min + cold resets (5 × 45–120 s per cold leg)
≈ **1.5 h** machine time, one go.sh night. Build + tests + smoke tests ≈ 2–3 h of tooling work,
done on a separate branch so 31.15's committed code is untouched.

## Decisions (2026-10-04, user: "go with the recommended settings")
1. **OpenFaaS dropped from W4.** Our stack is gateway 0.8.3 + faas-swarm 0.3.3 (the last
   pullable Swarm provider, archived). The gateway binary has no scale-from-zero code (no
   `scale_from_zero` string), and OpenFaaS CE has no idler. Upgrading the stack would change the
   platform under every OF number in Parts A–F, so it is not done. The paper states OF's absence
   from W4 and why.
   Checked 2026-10-04: the 0.8.3 gateway binary has the known config strings
   (`functions_provider_url`, `read_timeout`, `direct_functions`) but neither `scale_from_zero` nor
   the `/system/scale-function` API. Buying Pro does not help: Pro is Kubernetes-only, not Swarm.
   Workaround considered and rejected: harness scales `hello` 0 → 16 (`docker service scale`) at
   the first burst. Swarm starts the tasks inside dockerd, so the cost lands in untracked host CPU,
   not OF's cp containers (cp per cold start ≈ 0 by construction), and the lost requests are
   timed by our trigger, not by the platform. It measures something different, so it is not
   comparable with Fn/Kn/OW. **Status (user, 2026-10-04): deferred, not rejected.** Free and
   possible on the current stack. May be added later as a separately labelled, descriptive OF arm
   (report dockerd/untracked CPU next to cp). Not part of the first W4 pre-registration.
   **W4b option (user leaning yes, later):** OpenFaaS CE on the existing k3s cluster. Free
   (CE EULA: non-commercial / experimentation; confirm academic use with supervisor). CE has
   scale-*from*-zero in the open-source gateway (verify by smoke test); scale-*to*-zero is Pro, so
   the harness scales to 0 before each cold run, as it does for the others. CE caps 5 replicas.
   Own pre-registration and session after W4; labelled "OpenFaaS CE (k8s)", never compared with
   the Swarm OF of Parts A–F (W4's own warm arm is its reference). ~1 day setup + ~25 min machine.
2. **b100 only.** Enough for cost per cold start; b500 would double the legs.
3. **OpenWhisk: `wsk action update`** to force cold, smoke-tested unmeasured first. If it does not
   empty the pool, OW is dropped from W4 the same way as OF (stated, not patched around).
4. **Knative `minScale 0` only in W4**, labelled in the paper as a W4 configuration. Parts A–F keep
   16 static replicas.
5. **Nothing old changes:** all W4 behaviour behind flags, default off; old numbers and old code
   paths untouched (tests check this).

Final shape: Fn, Kn, OW × {cold, warm} × b100 = **6 legs**.
