# SAQEF — authors' internal known-fix verification checklist

**NOT for a fresh reviewer.** This file enumerates every fix we know about and the expected
answers, so it would defeat a cold pass. Use it as the authors' pre-publication checklist to
confirm each known fix is present, correct, and not regressed. For the independent review,
use `EXPERT_REVIEW_PROMPT_COLD.md` (no prior findings leaked) and hand the reviewer a clean
clone at the pinned commit with no other context.

---

**KNOWN-FIX CHECKLIST — SAQEF serverless overhead/carbon study**

Each item names a fix we made and what we believe the correct behavior is. Confirm each is
present, correct, and not regressed. Everything must trace to a file.

**Repo:** `https://github.com/bathork1391/saqef` (branch `main`). Work from a clean clone at
the pinned commit so you see exactly what a reviewer would see. **The pinned commit must be
set by the study authors after their pending box-task commit lands — do not review a dirty
working tree.** The headline numbers below are the values those results must reproduce.

**The study in one paragraph:** Fn, OpenFaaS, Knative, and OpenWhisk each serve an identical
~5 ms CPU-bound handler (3000–10000 requests, concurrency 4, 5 runs/session). Headline metric
`cp_dynamic_share_pct` = control-plane container CPU / dynamic (load-created) CPU — a
contention-robust discriminator. Key claims: (1) the Fn-vs-OpenFaaS control-plane gap is
machine/core-count dependent (2.8 pp at 8 cores, ~7 pp at 2 pinned cores), not a platform
constant; (2) four-platform ordering on 8 cores is OpenFaaS ≈7.4 < Fn ≈ Knative ≈10.6–12.4 <
OpenWhisk ≈82.4; (3) OpenWhisk's control-plane heaviness is structural, not a measurement
artifact (survived four sessions with different box conditions).

**Verify these headline numbers from raw data only.** For each of the four platform result
dirs under `results/`, independently recompute the median `cp_dynamic_share_pct`, the CI/CV,
and the per-invocation control-plane CPU cost directly from `runs.json` / `summary.json`
(do NOT read any prose first). Current claimed values: OpenFaaS **7.40** (2026-08-09
regression leg, ref 7.61), Fn **11.60** (3-session median; 2026-08-09 session value 11.27),
Knative **12.44** (2026-08-08 quiet rerun), OpenWhisk **82.36** (2026-08-08 evening rerun);
per-inv CP cost 0.53 / 0.75 / 0.96 / 26.82 ms. Also verify Fn's 2-core claim (13.91/14.08,
OF 6.82/7.17, gap ~7 pp) from `results/{fn,openfaas}_cpubound_2core{,,_session2}`.

**Specifically investigate — this is where previous review passes found bugs:**

1. **Container-name and image-basename substring collisions (the class of bug that already
   bit twice).** OpenFaaS's control-plane matcher previously used a bare `"gateway"`
   substring that collided with Knative's `kourier-gateway` pod (k3s/Knative-serving stays
   resident on this box across every session) — confirmed contamination existed in the
   pre-fix committed regression results (`k8s_kourier-gateway_*` inside the
   `delta_check_map`). A second latent collision: Fn's `fn_images=("hello",)` substring
   matched Knative's `kn-hello` image (masked only by a docker-vs-containerd digest quirk).
   **Fixed in `platforms/openfaas.py` + `run_openfaas.sh` (swarm-stack-prefixed names
   `openfaas_gateway`, ...) and by exact repo-basename matching in `saqef_harness.py`
   `_class_matches()` / `_image_repo_basename()`** (strip registry/path + tag, then require
   equality) for `fn_images`/`cp_images`. Check: (a) is every consumer of the CP matcher
   using the fixed names? (b) does the *current*
   `results/regression/openfaas/*/samples.csv` show any `k8s_` container counted as
   control-plane, or only sampled-and-unclassified? (c) does `_class_matches` behave for
   every platform's allowlists (name, image, label keys)? (d) quantify the magnitude of the
   pre-fix leak from git history.

2. **Cross-session citability borrowing (the other class of bug).** `saqef regression` uses
   `metrics/cpubound.json`'s `idle_w=4.3` and never recalibrates it. The 2026-08-09
   regression session's RAPL validation error is Fn 16.6/12.1/0.6/1.5/0.65% and
   OF 18.9/14.8/2.3/0.6/5.8% across the 5 runs (only run_1 exceeds the 15% degraded flag on
   each), while the paper's "energy citable at 4.2–8.2%" figure belongs to a *different,
   older* session (`results/{fn,openfaas}_cpubound_baremetal`). Check the energy/carbon
   citability claims in `SAQEF_PAPER_DRAFT.md` §5.6/Appendix A/B are correctly scoped to the
   session that produced each number, and that the `RAPL FIT DEGRADED >15%` warning in
   `saqef gates` fires correctly. Also test the paper's "idle-term-dominance" one-line
   observation: longer runs (TOTAL=10000, ~17–19 s) degrade less from a stale `idle_w` than
   short A/B legs (TOTAL=3000, ~5 s) under the same constant — direction only, not proof.

3. **Ambient-load quiet gate (new).** `saqef run` refuses to start a bench above
   `--max-ambient-cpu-pct` (default 15%), sampled over `--ambient-window-s` (20 s) by
   `ambient_load_check()`; the reading + a top-CPU `ps` snapshot land in `summary.json` →
   `ambient`. The 2026-08-09 regression legs self-certified at 9.9% / 11.7% of that ceiling.
   Check: (a) the gate measures whole-host busy CPU over the stated window and cannot be
   gamed by a load that starts after the probe; (b) `--no-quiet-gate` is the only bypass and
   is reserved for exploratory/contamination-AB use; (c) every citable run's `summary.json`
   actually carries the `ambient` field.

4. **Contamination A/B tool (new).** `tools/contamination_ab.py` runs the same bench clean vs
   with an emulated agent signature (3 spinners pinned to distinct cores + 1.1 GB bytearray,
   matching the documented 2026-08-07 incident), N=5/leg default, and reports the delta on
   `cp_dynamic_share_pct` / `host_saturation_pct` / p50/p99 / throughput in
   `contamination_ab.json`. Claimed result: Fn +2.16 pp, OpenFaaS +0.34 pp
   (`results/{fn,openfaas}_contamination_ab`). Check: (a) the dirty leg truly achieves the
   target profile (aggregate host busy% ≈ cores/cpu_count); (b) the A/B verdict is computed
   from within-session clean-vs-dirty summary.jsons, not reused across sessions; (c) the
   script's outdir handling is cwd-independent (this exact bug crashed it once).

5. **Isolation policy soundness.** `platforms/{fn,openfaas,openwhisk}.py` forbid
   `("user-container","queue-proxy")` (the two containers of a leftover Knative `hello`
   deployment) rather than any `k8s_` prefix (which would wrongly block when only the
   permanent k3s substrate is up — that over-broad version was found and narrowed during
   live testing). Check both the data-driven path (`saqef_harness.py`
   `assert_platform_isolation`) and the legacy shell-runner fallback, and judge whether any
   *legitimate* container name could false-positive or any *real* leftover could slip
   through. Also check the remediation advice (`platforms/base.py`
   `_advice_for_container`/`_advice_for_service`) is substring-keyed per-platform, not
   hardcoded to `docker rm -f fnserver`.

6. **Knative adapter (cold-read — newest code path).** `platforms/knative.py` deploys a
   Knative Serving Service on k3s (static minScale=maxScale=16, containerConcurrency 4),
   classifies per-replica `user-container` + `queue-proxy` as **fn** and
   kourier-gateway/svclb-kourier/activator/controller/autoscaler/webhook/net-kourier-controller
   as **CP**. Check: (a) `deploy()`'s rollout wait is revision-agnostic (a hardcoded
   `hello-00002-deployment` bug was found and removed); (b) the attribution map is honestly
   documented (queue-proxy counts as fn, unlike OpenFaaS which puts its proxy inside the
   function cgroup — a convention asymmetry the paper must disclose); (c) the k3s embedded
   control plane (apiserver/etcd inside the `k3s server` process) is invisible to the
   container sampler — is that boundary declared loudly enough in the paper? (d) the
   `--idle-w` recommendation (≈4.56 W, condition B below) is applied as documented.

7. **OpenWhisk adapter (cold-read — newest code path).** `platforms/openwhisk.py` uses the
   `openwhisk/standalone:nightly` image (whole control plane in one container, `cp=openwhisk`),
   a web-action frozen-GET route (HTTP 204 blocking), rate-limit overrides via
   `JVM_EXTRA_ARGS` (`whisk-config.limits.actions.invokes.perMinute`), and a static docker
   CLI shadow-mount. Check: (a) the classification of prewarm containers (`wsk0_*` — excluded
   from fn_images, idle ~0%, fail-open unclassified); (b) how much of the ~26 ms/inv CP cost
   is the standalone's per-activation `docker logs` log-store vs "real" control plane, and
   whether the paper's deployment-mode caveat is honest; (c) that no stale swarm service or
   fnserver can be up during an OW run (isolation).

8. **Measurement-path changes in `saqef_harness.py`** (the code that produces every number):
   RAPL wraparound handling, `median_summary()` merging, pre+post inventory union, `is not
   None` fixes, and `_class_matches` exact-basename matching. Specifically:
   (a) `rapl_max_range_j()` + `rapl_correct_wrap()`: single-wrap exact correction; double-wrap
   or no-range → fail-open None. Check the math against synthetic counters, including the
   `rapl_wrap` summary field (`none|corrected_single|uncertain_double|uncertain_no_range`) —
   is it distinct from `rapl_available` and does `rapl_w_series()` in
   `tools/reanchor_and_kn_idle.sh` discard/retry wrapped reads instead of folding them in?
   (b) `median_summary()` unions **both** dicts (key-union) **and** lists (dedup, first-seen
   order) across all runs — verify `container_inventory` no longer falls through to
   first-run-only (OpenWhisk's growing `wsk0_N` pool was the trigger); (c) `run_once()` unions
   a pre-run and post-run `docker_inventory()` snapshot; (d) `harness_argv()` uses `is not
   None` (an explicitly-passed `0` must not silently fall back); (e) confirm none of these
   changes any previously-citable share.

9. **Regression integrity — re-anchored 2026-08-09, now PASSING.** Refs were recalibrated
   same-day via old-runner A/B under the self-certifying quiet gate:
   `results/{of,fn}_cpubound_crosscheck_2026-08-09` → OpenFaaS **7.61**, Fn **11.49**
   (`metrics/cpubound.json`, backup `.bak-2026-08-09`). The refactored path then reproduced
   them: `saqef regression` gave Fn **11.27** (dev 0.22 pp) / OF **7.40** (dev 0.21 pp),
   tolerance 0.50 pp → PASS both. Verify from the gate tables (delta ≈ 0, CPmapped 6/6 and
   1/1, coverage 100%, host_plausible true, ambient < 15%) that the runs are clean, and judge
   whether the day-to-day Fn drift (10.46 → 11.60 → 12.27 → 12.92 → 11.01/11.27) has any
   plausible mechanism or is purely box-state. The runbook also claims a script bug was found
   and fixed in `tools/reanchor_and_kn_idle.sh` (Fn old-runner leg left `fnserver` up before
   `saqef regression`'s OpenFaaS isolation guard; fixed with a teardown step + `--skip-legs`)
   and that Knative teardown was added for the same class of bug — check the script matches
   its own description.

10. **No data corruption.** Confirm `git status`/`git show` shows the citable baselines
    (`results/{fn,openfaas}_cpubound_baremetal`, `*_2core{,,_session2}`, `*_crosscheck{,2}`,
    `*_crosscheck_2026-08-09`, `*_contamination_ab`, `freqcheck_evidence/`) are byte-identical
    to their post-audit commits. The overwritten dirs (`regression/*`, OW/Kn baremetal) are
    protocol-designated reruns — fine. Confirm `results/verify.json` (root) was not clobbered.

11. **Tests are honest.** `tests/test_saqef_cli.py` (47 tests): do the expected-argv
    assertions reflect actual adapter behavior, or were they bent to pass? Run
    `python3 -m unittest tests.test_saqef_cli`.

12. **Paper internal consistency.** Read `SAQEF_PAPER_DRAFT.md` end-to-end (abstract, §4
    methodology, §5.1–5.6, §8, §11, Appendix A/B) hunting specifically for: stale numbers
    from superseded sessions still cited as current; a number in the text disagreeing with
    its own table/figure; captions describing values the figure doesn't show; any
    "superseded, do not cite" claim that isn't actually retired everywhere. Earlier passes
    missed figure-caption drift twice — grep aggressively for every literal number and check
    the session it belongs to. **Note:** figures were regenerated 2026-08-09
    (`python3 figures/make_figures.py`) after the regression rerun overwrote
    `results/regression/*`; figure1 must be byte-identical to its pre-regeneration commit
    (its sources were untouched), and figure2/3/4 must reflect the 2026-08-09 Fn/OF leg.
    Remaining `7.14`/`10.65` mentions in the paper are intentional historical-snapshot notes.

13. **The open items (already flagged, judge the framing).** (a) **Knative idle-w — CLOSED
    (2026-08-09):** N=5 repeated 60 s RAPL reads per condition give bare k3s+knative-serving+
    kourier **3.871 W** (3.692–3.946) vs `hello` @ 16 replicas **4.561 W** (4.309–4.719);
    **premium = 0.690 W**. This supersedes the three single-sample readings (11.14 / 7.01 /
    4.91 W). Verify the measurement in the script output / `results/` idle-probe artifacts
    and judge whether a ~0.7 W always-on premium at single-digit watts is honestly framed as
    a design-guidance figure rather than a headline. (b) OpenWhisk's **structural** energy
    mismatch (31–50% RAPL err, stable across four sessions — a linear busy-core model does
    not fit the standalone JVM) is named in Future Work as a separate, larger open item, not
    treated as "the same calibration gap." Judge whether that framing is fair or a dodge.
    (c) **n=1 machine** — the core-count effect was demonstrated on ONE physical host via
    cpuset restriction (instrument held fixed); the paper's Contribution #3 / T5V #8 says so
    explicitly and lists a second machine as future work. Judge whether the honesty is
    adequate or the claim needs further weakening.

**Also scrutinize the measurement path itself, not just the diffs** (`saqef_harness.py`
end-to-end + `platforms/*.py`): the delta-check (CPmapped) mechanism, host-window alignment,
the energy model (`e_total = idle_w*wall + e_dynamic`), the carbon formula
(`J/3.6e6 × PUE × CI` — a 1000× unit bug was fixed here once already), fail-open unclassified
buckets, and the `hello` image allowlist for cross-platform collisions.

**Known limitations to be fair about:** the box is not a controlled testbed — it has
documented day-to-day idle-wat drift, and Knative's own control plane (k3s apiserver/etcd) is
invisible to the container sampler (runs inside the `k3s server` process). OpenWhisk is the
*standalone emulator*, not a production deployment. These are declared boundaries; judge
whether they're declared loudly enough.

**Deliver a verdict like this:**
- For each of the 13 items: **CONFIRMED / REFUTED / PARTIALLY** + one-line evidence
  (file:line or git command output).
- Re-derived headline numbers table (your values vs claimed values, delta).
- Any new bug you found (file:line, severity, whether it changes any citable number).
- Judgment: are the four claims publishable as stated, and what (if anything) must change
  before submission?
- The top 3 risks a hostile reviewer would attack, ranked.

Be exact. Cite file:line and git SHAs. Do not accept any claim you have not reproduced.
