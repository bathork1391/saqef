# Next steps (kept current; last updated 2026-10-22)

The one place that says what is still to run or do. Details live in `TROUBLESHOOTING_RUNBOOK.md`
(section numbers below). Results go to `../saqef-paper/VERIFIED_RESULTS.md`.

## Before any measurement night
- Laptop on its charger, battery at 80 % or more and **charging**. The pre-flight refuses a capped
  CPU (§31.14); a power cut stops the session cleanly (§32).
- Quit editors and other interactive applications; the session leaves the desktop itself.
- Progress at any time: `sudo bash tools/go.sh --status`

## Resume here (2026-10-22: W6 done, §38.10, and in paper v10 as Section 4.10, Tables 16–17, A3; next: supervisor decisions)
**Supervisor meeting decision (recorded 2026-10-19):** the next measurements are, in order,
I/O-bound and mixed functions (W5, done), then failures and losses as in real traffic (W6).
- Paper: `../saqef-paper/versions/v10/` revised 2026-10-22 after review R1
  (`../saqef-paper/reviews/v10-2026-10/`): shortened, corrected, Appendix A (every prediction and
  its outcome). About 31 pages in Word, 33 in the PDF, with W6 (Section 4.10, Tables 16–17, A3).
- Meeting brief for the supervisor: `../saqef-paper/versions/v10/meeting/MEETING_BRIEF.md`.
- Open question for the next meeting: is a second machine coming? It decides what follows W6.

## To run, in this order (each one command, all pre-registered, code committed)
1. **W6 failures, overload, half load and mixed traffic (runbook §38, pre-registered 2026-10-22):**
   two slots, any order, any days; charger plugged in and charging for the whole slot.
   - Slot A, OpenFaaS + Fn: **done 2026-10-21** (`w6_a_`, outcome §38.6). 10 of 12 legs passed; both
     over legs (c = 64) failed the count gate only (hey sent 2944 of 3000), fixed in §38.7.
     So far F1–F3, H1, H3, H4, A1, A2 hold on both; H2 fails on OpenFaaS (half-load cp 1.151 × base).
   - Slot A2, OpenFaaS + Fn base and over at 3008 requests: **done 2026-10-22** (`w6_a2_`, outcome
     §38.8). 4 of 4 legs on attempt 1. O1, O2, O3, O4a hold on both; O4b fails (OpenFaaS cp at c = 64
     is 1.119 × base, not ≥ 1.15), final since O4b is OpenFaaS only.
   - Slot B first launch 2026-10-22: stopped at the Knative fail20 probe (16 % / 24 % against 20 ± 3),
     nothing measured. Cause: per-pod counters over 16 pods round off at 200 calls; the handler is
     fine. Amendment §38.9: Knative probe now 2000 GETs per phase. Record kept as
     `results/aborted_20261022T1227Z_w6_b_session/`.
   - Slot B, Knative + OpenWhisk: **done 2026-10-22** (`w6_b_`, outcome §38.10). 15 of 15 legs on
     attempt 1. Final 4-platform verdicts: F1–F3, O1–O3, O4a, H1, H3, H4, A1, A2 hold; H2 fails
     (half-load cp OpenFaaS 1.15 ×, Knative 0.79 ×); O4b fails. OpenWhisk 16 containers: K0 holds
     (10 containers), K1 fails (cp 1.15 × at c = 8), **K2 fails upward: 2.36 × the throughput at c = 8,
     1.64 × at c = 4**. So the 2-container pool is what caps OpenWhisk standalone (~315 rps).
   Per platform: base (5 ms spin, c = 8), half (fixed rate at half of base's throughput), over
   (c = 64), fail5 and fail20 (5 % / 20 % of calls answer HTTP 500 after their work), alt (calls
   alternate spin / sleep). Each slot first probes the new handlers on its platforms and stops
   before measuring if a platform does not pass the 500s through as designed.
   Adjudicate with `python3 tools/w6_analysis.py --prefix w6_a_` (and `w6_a2_`, `w6_b_`); outcome §38.x,
   VERIFIED_RESULTS Part J.
   **Paper pass done 2026-10-22** (v10 now 33 PDF pages; meeting brief updated). It was: W6 as v10 Section 4.10 and Appendix A Table A3
   (F1–F3, O1–O4, H1–H4, A1–A2, K0–K2); rewrite Limitations "Load is limited ... not tested" and
   "OpenWhisk standalone only, with 2 action containers" (K1/K2), and drop Future Work item 1
   (failures and overload); refresh `versions/v10/meeting/MEETING_BRIEF.md` (Sections 1, 4.10, 6).
   **K2 changes the paper's OpenWhisk story (§38.10):** say in §4.3, Limitations and every OW
   throughput/latency/energy comparison that the ~315 rps ceiling is the standalone's 2-container
   pool, measured: 16-container pool (10 started by the 10-concurrent pre-leg check, §38.10) gives 745 rps at c = 8, cp per request 1.15 ×.
   Closed: no re-measure of OW's main legs. W6 K1 answered the question: cp per request is
   0.95 x / 1.15 x with 10 containers, so the main metric does not depend on the pool; the pool's
   effect on throughput, latency and energy is stated in the paper (§3.1, §4.10, §6).
2. After W6: optional W5b (disk and sleep arms of all four platforms in one session, to separate
   platform from session for W5's P4b, §36.9); needs its own pre-registration.

**W5 (done):** slots `io_a_` (2026-10-19) and `io_b2_` (2026-10-20; `io_b_` void after a power cut).
12-cell verdicts (§36.9, corrected §36.10): P1 mixed, P2, P3, P4a hold; P1 sleep (7 of 12), P1 disk
(8 of 12) and P4b (8 of 12) fail. In the paper: v10 Section 4.9.
**Done:** W4 cold start ran 2026-10-05 (`cold_`), 8 of 8 legs passed on attempt 1; outcome §32.1,
VERIFIED_RESULTS Part G (G-T0–G-T2) and F-T6. 33.1 and 33.2 ran 2026-10-04; outcomes §33.1 / §33.2.

## After each run (adjudication checklist)
- Outcome in the runbook, tables in VERIFIED_RESULTS, both repos backed up and pushed.

## Offline work still open (no machine time)
- W4: the within-run power-vs-load check (§25.6), now possible because `energy_trace.csv` records
  `host_busy_ticks` (§34.2); the `cold_` legs are the first data with it.
- W4 Knative C4 (cold later bursts 17 % faster than warm at equal pods): cause not examined (§32.1).
- **Done 2026-10-14 (§34.2 B, VERIFIED_RESULTS H-T2b; paper and MEETING_PREP synced):** W per busy core (noted 2026-10-14; paper §5.3, `tools/energy_linearity.py`, §34): the numerator
  is idle-subtracted power, the denominator is total host busy cores, which include the stacks' own
  ~0.5–0.6 core idle floor. At light load this pulls the 4.4–5.8 W values down. Recompute with the
  probe's busy cores subtracted from the denominator (offline, existing legs) and report α and the
  W per busy core range both ways; "no constant fits" should hold, the magnitudes may move.
- **Ideas from related work (noted 2026-10-14). Author's rule: do first what is simple, feasible,
  related to our results and adds value; among experiments, a new direction comes before an
  extension of an existing one (C processor speed, E trace replay and the failures item are new
  directions; D keep-alive extends W4; more function types extend W1/W2).** A and B are post hoc
  and labelled so.
  A. **Done 2026-10-14** (runbook §34.3, VERIFIED_RESULTS H-T3, `tools/latency_overhead.py`).
  Platform latency per request (after Ilúvatar [24], which reports control-plane overhead as
  added latency). Part A legs: p50 − 5 ms handler time, c = 1, legs without `host_saturated` only;
  set beside cp ms/inv. Does the latency view rank the platforms as the CPU view does?
  B. **Done 2026-10-22** (runbook §39, VERIFIED_RESULTS H-T4, `tools/energy_split.py`): B1 holds, B2
  fails (19.0 % vs 17.5 % for one W per core), so no platform-only energy; one §4.4 sentence optional.
  Splitting package energy between platform and function (after FaasMeter [23], which
  disaggregates system power statistically and validates against marginal energy). Fit
  dynamic W = a · cp cores + b · function cores + c over the existing legs; judge by prediction on
  held-out legs. Fits → energy per request for the platform alone; fails → stronger evidence for
  §5.3's "no constant W per core". Rule 4: existing legs only.
  **Framing (author, 2026-10-15): per-function footprint.** Each leg runs one function per
  platform, so its energy and CPU per request already are that function's per-request footprint,
  split into function and platform with no statistical disaggregation needed. Paper wording:
  "per-request footprint, function part and platform part"; the study's centre stays the platform
  share compared across four platforms (FaasMeter [23] allocates that share among functions; we
  measure its size per platform). B, after W5, is the step that makes the link to FaasMeter direct.

## Git housekeeping (done 2026-10-05)
- GitHub: `saqef` has only `main` (+ tags v9.14-remeasure, v9.14.1-remeasure); `saqef-paper` only `master`.
- Removed locally, after a verified full backup: the merged branches `lock-summary-rapl-joules` and
  `refactor/adapters`, the pre-strip backup branches/tags, tag `archive/old-remote-main-20260817`.
- Everything removed is in `../git-backups/saqef_all_refs_20261004T2059Z.bundle` and
  `../git-backups/saqef-paper_all_refs_20261004T2059Z.bundle` (`git bundle verify` OK). Restore a
  ref with `git fetch <bundle> <ref>:<ref>`. The local stash was kept.

## Possible later sessions (not pre-registered)
**Superseded 2026-10-19 by the meeting decision under "Resume here" (W5 → W6 → OpenWhisk-16).
Kept for the reasons it records.** Which one runs next is decided once, at the supervisor meeting
(paper Future Work §10 and MEETING_PREP §10 carry the same list). No machine night before that meeting. If a second machine is
coming: the OpenWhisk 16-container pool (defends the existing OW numbers; needs the smoke test that
the pool reaches 16, and the other three platforms re-measured in the same session). If not:
processor speed (C below; new direction, partly answers host-specificity, needs the power gate
behind a flag). If the supervisor picks failures and faults, that comes first. Knative cold vs a
pre-grown 16-pod warm pool rides along with whichever session runs (a cold and a warm-16 arm in the
same session, about 1 h).
- **Related-work experiments (noted 2026-10-14; order after offline A and B above).**
  C. Processor speed (**draft pre-registration: runbook §40, 2026-10-22**; after EcoFaaS [26], which sets core frequency
  per invocation). Our own data already points at it: with the clock capped at ~2.5 GHz by the
  power state, cp ms/inv rose 20–35 % while function CPU stayed flat (§31.14 C). Design idea: one
  session, 5 ms spin, all four platforms, c = 4, the CPU pinned at 3 fixed frequencies (e.g. 1.6 /
  2.4 / 3.2 GHz), order alternated. Questions: is cp CPU per request more clock-sensitive than
  function CPU; which frequency gives the lowest energy per request. Step 1: check that fixed
  frequencies hold on this laptop (the power gate, pre-flight check 8, must allow a deliberate cap
  behind a flag, default off).
  D. Keep-alive cost (after Kondrashov et al. [25]: instance churn = 10–40 % of request-handling
  CPU; memory allocated 2–10× what is used). Vary each platform's keep-alive (Fn idle timeout,
  Knative scale-to-zero window, OpenWhisk keep-alive) under arrivals with gaps shorter and longer
  than it. Measures: cp CPU per request, container start-ups, untracked host CPU (W4 method),
  memory reserved vs used. Reuses W4's resets; more set-up than C. OpenFaaS excluded as in W4.
  E. Trace replay (Kondrashov et al., and possibly Ilúvatar, drive with real workload patterns):
  a scaled slice of the public Azure Functions trace, open loop. Overlaps the "Failures and faults"
  item below (requests can fail; needs its own gates). Most work of the three.
- **OpenWhisk with a 16-container pool (noted 2026-10-12).** OpenWhisk standalone
  has served with 2 action containers in every run (§29.2 D): `container-pool.user-memory` is
  1024 MB, the 2 nodejs prewarm containers take 512 MB, and each python action takes 256 MB, so 2
  fit. Every OpenWhisk throughput, latency and energy-per-invocation comparison is therefore
  "2 containers vs 16", and the ~305 rps driver-store plateau is that pool, not the platform.
  Question: with an equal pool, where does OpenWhisk land on throughput, latency and energy per
  invocation, and does its control-plane ms/inv stay at ~3 (driver store) as the load rises?
  Design idea: one session, driver store, 5 ms spin, 3000 requests per run, c = 1, 4, 8.
  (a) Cross-platform at equal footing: OpenWhisk with 16 action containers next to OpenFaaS and
  Knative at 16 replicas and Fn, all in the same session (energy is within-session only, so the
  others must be re-measured alongside, not taken from closed sessions). Fn grows its own pool:
  cap it at 16 if a setting exists, otherwise record its container count and say so. Every run
  records its function-container count; a run not at the intended count is not equal footing.
  (b) Within OpenWhisk, pool 2 vs pool 16: the size of the 2-container penalty.
  Measures per run, as in every session: throughput, latency p50/p99, control-plane and function
  CPU ms/inv, RAPL energy per invocation, plus the function-container count. Settings to try (unverified on this image): raise
  `whisk.container-pool.user-memory` to ~4.6 GB (16 × 256 MB + 512 MB prewarm; box has 30 GB) via
  `JVM_EXTRA_ARGS`, or set the action memory limit to 128 MB. Step 1 is an unmeasured smoke test
  that the container count actually reaches 16; then pre-register. Paper use: turns the stated
  "2 vs 16" caveat into a measured result; closed sessions and their claims stay unchanged.
- **OpenWhisk on Kubernetes (Helm chart `openwhisk-deploy-kube`, noted 2026-10-13; future
  reference).** Free, Apache 2.0, the usual production layout (controller, invoker, Kafka, CouchDB,
  nginx as pods), on the same k3s as Knative. It answers what the standalone results cannot: how a
  production OpenWhisk compares (§28.9; standalone-only properties: per-activation `docker logs`,
  2-container pool, in-memory activation store). Box has 32 GB RAM. Step 1: unmeasured install
  smoke test (memory/CPU footprint at idle, set the invoker pool to 16 containers, raise the
  invocation limits); then pre-register. Label separately from "OpenWhisk standalone".
- **Function types beyond CPU and memory (noted 2026-10-14; author re-opened the choice made in
  §27.14 / §28.1).** The final study has CPU-bound (Part A), payload echo (W1) and memory-bound (W2)
  handlers only. W1 moves data only through the platform's own request path (load generator →
  gateway or proxy → function → back, over the host's virtual network); no final handler reads a
  disk or calls another service. Missing types, each a handler swap under `--workload`, matched to 5 ms of wall time,
  c = 1, 4, 8, all four platforms in one session (energy is within-session only):
  (a) I/O-wait: `time.sleep(0.005)`, standing in for a downstream call. The 2026-08-15 quick-tier
  pass (VERIFIED_RESULTS Part B §10, 3 runs, old sampler) found cp ms/inv about unchanged and the
  share 25–47 % on the light platforms; never under the final gates, so not in the paper.
  (b) Real downstream call: the handler GETs a small object from one HTTP service run by plain
  Docker outside all four platforms (nginx or MinIO); that service's CPU is its own "backend"
  bucket, neither cp nor function (design already written in §27.14).
  (c) Disk I/O: write and fsync, then read, a fixed-size file inside the function container.
  (d) Mixed: 2.5 ms spin + 2.5 ms sleep, and spin + DRAM copy.
  Question: is cp ms/inv a property of the platform or of the function type, and how does the
  share move? Step 1: handlers in `workloads/<type>/`, `tools/workload.sh` check; then pre-register.
  **I/O-bound gap (author concern, 2026-10-15).** The study's plan had an I/O-bound workload. On
  2026-10-03 S3-style I/O (MinIO) was chosen (§27.14) and replaced the same day by payload echo
  (§28.1), because external I/O was expected to leave cp ms/inv almost unchanged. W1 answers "does
  the platform's cost grow with request data" (yes: light platforms ×3–7 from 1 KiB to 512 KiB;
  OpenWhisk standalone holds payloads in memory and fails at 512 KiB). It does **not** answer
  "what does the platform cost when the function waits on I/O"; the expected "cp unchanged, share
  up" was only a prediction, measured once in the 2026-08-15 quick-tier sleep pass (Part B §10, old
  sampler, not citable). Paper wording until W5 runs: W1 = "request-payload (data-path) I/O",
  never "I/O-bound"; Limitations names the gap.
  **W5 design, decided 2026-10-15** (author: disk, not MinIO; four expert reviews weighed, see
  below). Question: does the cp share rise when the function waits instead of computing, and does
  a real filesystem wait behave like a plain wait? One session, all four platforms, c = 1, 4, 8,
  three arms, order alternated per platform:
  - **spin 5 ms**, the in-session baseline (not taken from Part A: cross-session comparisons with n = 5 cannot size a day shift);
  - **sleep 5 ms**, pure wait, no kernel work;
  - **disk**: write a fixed 4 KiB to one file per container in its writable layer, fsync,
    overwrite each call. Same operation on every platform; its wall time is reported, not tuned
    to 5 ms (tuning would tie the result to this SSD).
  OpenWhisk with the driver log store (§29.3), labelled standalone with 2 action containers.
  36 legs, about one W1-length night. Reported per usable run: cp and function ms/inv, share with
  its denominator, untracked host CPU separately (jbd2/kworker land there; expected higher for
  disk than sleep, a finding not a defect), wall time, success. Claims on CPU only: latency at
  c = 8 is journal queueing, and SSD power is outside RAPL package, so no disk-energy claim.
  Paper wording: "waiting versus filesystem I/O", not I/O-bound functions in general.
  **Step 1, unmeasured probe: done 2026-10-15 (runbook §35.1).** All four platforms serve both
  arms; disk write + fsync ~0.75 ms at c = 1, 1.0–1.4 ms at c = 8, on overlay everywhere;
  `/var/lib/docker` and `/var/lib/rancher` are the same ext4 partition. Fn's function containers
  have a read-only root and a tmpfs `/tmp` (fsync there took 6 µs, never reaching the disk), so the
  handler writes `/var/tmp` and **Fn runs the whole W5 session with `FN_DISABLE_READONLY_ROOTFS=1`**
  (all arms, so Fn's setup is constant within the session; a non-default setting, stated in the
  paper). With the default the deploy fails, so it cannot produce silent no-op data.
  **Step 2, pre-registration**, fixing in advance (from the 2026-10-15 review):
  - disk is not a pure wait: predict fn ms/inv ≈ sleep + a few µs, the disk's cost in untracked
    host CPU (journal, writeback) and wall time, cp ms/inv unchanged;
  - arms differ in wall time (spin/sleep ~5 ms, disk ~0.8 ms) and so in rps: per-invocation
    comparisons across arms use idle-subtracted values (each leg's idle probe);
  - tmpfs rule: a platform whose disk reply shows tmpfs or a median wait under 50 µs in-session
    is excluded from the disk arm, not re-run (the in-session probe checks it before any leg);
  - Fn's 500s while its pool grows at c = 8 are counted, as in W3.
  **Proposed addition (open, 2026-10-15): a plain-Docker target**, borrowed from Alhindi et al.
  [27], who compare OpenFaaS with the same app in plain Docker (their method: VirtualBox VMs,
  battery-discharge power via Powerstat at 1 s, average watts; not borrowed). The same handler in
  one plain container (no platform), called directly by hey, spin and sleep (disk too), in the W5
  session: (1) platform − plain = the platform fee with no cgroup classification, an end-to-end
  check of the cp bucket; (2) shows how much "function" CPU is in-container proxies (of-watchdog,
  queue-proxy, §27.12); (3) answers "what does a FaaS platform cost over a container" per request
  and across four platforms; (4) latency baseline: the plain container's client p50 minus the
  handler time is hey plus loopback with no platform, so subtracting it turns the paper's c = 1
  latency figures (0.8 / 0.7 / 1.2 / 3.8 ms, now upper bounds) into each platform's added
  latency. Valid only within the W5 session (platform and plain legs the same night, rule 6);
  until then the paper keeps "upper bounds". About 1 h of the night plus a small adapter (no cp containers); if
  the adapter needs more than a day, it becomes its own session and W5 runs without it.
  **Considered and deferred (revisit later, reasons kept):**
  - Read-back arm: a read after the write is served from the page cache, i.e. a memory copy;
    W2 already covers memory work. Adds nothing to the cp question.
  - Sleep-length sweep (5 / 50 / 200 ms): longer waits mean fewer rps in a closed loop, so the
    platform's idle CPU is spread over fewer requests. That part is predictable from data on disk:
    idle cp cores in `final_` are OpenFaaS 0.0025, Fn 0.0018, Knative 0.009, OpenWhisk 0.036,
    i.e. at 200 ms, c = 1 (~5 rps) ~1.8 ms/inv for Knative and ~7 ms/inv for OpenWhisk, more than
    their per-request cp. A sweep would mostly re-measure that arithmetic, OpenWhisk's 2
    containers saturate early at long waits (unequal footing worsens), and 3000 requests at
    200 ms, c = 1 take 10 min per run. Worth it only if a reviewer asks whether open requests cost
    the platform CPU over time; then pre-register with idle-subtracted values and a smaller TOTAL.
  - MinIO / network storage: Future Work. Needs a backend CPU bucket and networking into four
    platforms; the cp share question does not need it.
  - O_DIRECT writes as a disk fallback: OverlayFS support depends on the kernel and may differ
    across platforms; the fallback is sleep only.
  - A sentence on checking the trend on commercial FaaS: outside this study's scope.
  Precedent for local disk as the I/O case: FunctionBench's disk microbenchmarks (verify the
  citation before use).
- **Failures and faults (noted 2026-10-14).** Every steady leg is closed-loop over loopback with no
  faults, so availability is 1.0 by construction (§25.3); the only failures measured are Fn's HTTP
  500s while its pool grows (W3 post hoc, W4 first burst). Real deployments see failed calls.
  Arms, one at a time against the steady 5 ms spin baseline in the same session:
  (a) function errors: the handler fails a fixed fraction of calls (e.g. 5 %, 20 %) with an
  exception or HTTP 500; (b) function timeouts: a fraction of calls exceed the platform's timeout;
  (c) network faults: `tc netem` loss and delay on the path between the load generator and the
  platform entry point; (d) open-loop overload: `hey -q` above measured capacity (429 / 503 /
  timeouts). Measures: cp CPU per attempted and per successful call, availability, latency, and
  platform retries (OW and Knative retry and queue inside the control plane; OpenFaaS sync calls fail
  fast). The current gates require ≥ 99 % success, so these legs need their own gate set behind a
  flag, default off, fixed in the pre-registration.
- Knative cold vs warm with the warm pool pre-grown to 16 pods (W4's warm arm was 9–16, §32.1).
- Burst-size ladder at a fixed total and fixed pool: separates per-request from per-burst cost.
- OpenFaaS CE on Kubernetes (W4b): only after written licence confirmation from OpenFaaS.
- RAPL against a wall-plug meter: needs the meter.
- Several functions on one platform at once (noted 2026-10-15; FaasMeter's setting [23]): two or
  more different handlers (e.g. spin and sleep) deployed together under mixed load; split package
  energy and CPU per function, including each one's share of the control plane, and check the
  split against each function measured alone in the same session. Future Work, not a change of
  focus.
