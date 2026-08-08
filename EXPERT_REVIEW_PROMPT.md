# SAQEF expert review prompt

Copy-paste prompt for an independent expert reviewer. Purpose: adversarial reverification
of every headline number and claimed fix before publication. Give the reviewer a clean clone
at the pinned commit and no other context beyond this file.

---

**REVIEW PROMPT — SAQEF serverless overhead/carbon study: independent reverification**

You are reviewing a cross-platform measurement study before publication. Your job is
adversarial: assume every headline number and every claimed "fix" is wrong until you have
re-derived it yourself from raw data. Do not trust any summary, AGENTS.md prose, or commit
message. Everything must trace to a file.

**Repo:** `https://github.com/bathork1391/saqef` (branch `main`, currently at `0d18f25`).
Work from a clean clone so you see exactly what a reviewer would see.

**The study in one paragraph:** Fn, OpenFaaS, Knative, and OpenWhisk each serve an identical
~5 ms CPU-bound handler (3000–10000 requests, concurrency 4, 5 runs/session). Headline metric
`cp_dynamic_share_pct` = control-plane container CPU / dynamic (load-created) CPU — a
contention-robust discriminator. Key claims: (1) the Fn-vs-OpenFaaS control-plane gap is
machine/core-count dependent (2.8 pp at 8 cores, ~7 pp at 2 pinned cores), not a platform
constant; (2) four-platform ordering on 8 cores is OpenFaaS ≈7.1 < Fn ≈ Knative ≈10.7–12.4 <
OpenWhisk ≈82.4; (3) OpenWhisk's control-plane heaviness is structural, not a measurement
artifact (survived three sessions with different box conditions).

**Verify these headline numbers from raw data only.** For each of the four platform result
dirs under `results/`, independently recompute the median `cp_dynamic_share_pct`, the CI/CV,
and the per-invocation control-plane CPU cost directly from `runs.json` / `summary.json`
(do NOT read any prose first). Current claimed values: OpenFaaS **7.14** (regression session),
Fn **11.60** (3-session median; session value 10.65), Knative **12.44**, OpenWhisk **82.36**;
per-inv CP cost 0.50 / 0.75 / 0.96 / 26.82 ms. Also verify Fn's 2-core claim (13.91/14.08,
OF 6.82/7.17, gap ~7 pp) from `results/{fn,openfaas}_cpubound_2core{,,_session2}`.

**Specifically investigate — this is where previous review passes found bugs:**

1. **Container-name substring collisions (the class of bug that already bit once).**
   OpenFaaS's control-plane matcher previously used a bare `"gateway"` substring that
   collided with Knative's `kourier-gateway` pod (k3s/Knative-serving stays resident on this
   box across every session). Confirmed contamination existed in the pre-fix committed
   regression results (`k8s_kourier-gateway_*` inside the `delta_check_map`). Check:
   (a) is the fix in `platforms/openfaas.py` + `run_openfaas.sh` (swarm-stack-prefixed names
   `openfaas_gateway`, ...) correct and complete — is every consumer of the CP matcher using
   the fixed names? (b) does the *current* `results/regression/openfaas/*/samples.csv` still
   show any `k8s_` container being counted as control-plane, or only sampled-and-unclassified?
   (c) quantify the magnitude of the pre-fix leak from git history.

2. **Cross-session citability borrowing (the other class of bug).** `saqef regression` uses
   `metrics/cpubound.json`'s hardcoded `idle_w=4.3` forever and never recalibrates it. The
   regression session's RAPL validation error is 11.5–18.9% (Fn) / 14.8–18.9% (OF), while the
   paper's "energy citable at 4.2–8.2%" figure belongs to a *different, older* session
   (`results/{fn,openfaas}_cpubound_baremetal`). Check the energy/carbon citability claims in
   `SAQEF_PAPER_DRAFT.md` §5.6/Appendix A/B are correctly scoped to the session that produced
   each number, and that the new `RAPL FIT DEGRADED >15%` warning in `saqef gates` fires
   correctly.

3. **Isolation policy soundness.** `platforms/{fn,openfaas,openwhisk}.py` now forbid
   `("user-container","queue-proxy")` (the two containers of a leftover Knative `hello`
   deployment) rather than any `k8s_` prefix (which would wrongly block when only the
   permanent k3s substrate is up — that over-broad version was found and narrowed during
   live testing). Check both the data-driven path (`saqef_harness.py`
   `assert_platform_isolation`) and the legacy shell-runner fallback, and judge whether any
   *legitimate* container name could false-positive or any *real* leftover could slip through.

4. **Measurement-path changes in `saqef_harness.py`** (the code that produces every number):
   RAPL `energy_uj` wraparound guard (`rapl_max_range_j` + single-wrap correction),
   `median_summary()` key-union across all runs, pre+post `docker_inventory()` union in
   `run_once()`, and the `is not None` fix in `platforms/base.py` `harness_argv()`. Verify
   each against synthetic and real data; confirm none changes any previously-citable share.
   Check the math, edge cases (what if a key is present in only some runs; what if the counter
   wraps twice), and that `median_summary()` still behaves correctly for nested dicts.

5. **Regression integrity.** The current Fn/OF regression runs FAIL the stale 11.60/7.67
   reference (dev 0.95/0.53 pp) — claimed to be documented day-to-day box drift, not a
   refactor break. Verify from the gate tables (delta ≈ 0, CPmapped 6/6 and 1/1, coverage
   100%, host_plausible true) that the runs themselves are clean, and judge whether the drift
   explanation is credible or a fig-leaf. Check whether the Fn drift
   (10.46 → 11.60 → 12.27 → 12.92 → 10.65/11.01) has any plausible mechanism or is purely
   box-state.

6. **No data corruption.** Confirm `git status`/`git show` shows the citable baselines
   (`results/{fn,openfaas}_cpubound_baremetal`, `*_2core{,,_session2}`, `*_crosscheck{,2}`,
   `freqcheck_evidence/`) are byte-identical to their pre-audit commits. The overwritten dirs
   (`regression/*`, OW/Kn baremetal) are protocol-designated reruns — fine. Confirm
   `results/verify.json` (root) was not clobbered.

7. **Tests are honest.** `tests/test_saqef_cli.py` (38 tests): do the expected-argv
   assertions reflect actual adapter behavior, or were they bent to pass? Run
   `python3 -m unittest tests.test_saqef_cli`.

8. **Paper internal consistency.** Read `SAQEF_PAPER_DRAFT.md` end-to-end (abstract, §4
   methodology, §5.1–5.6, Appendix A/B) hunting specifically for: stale numbers from
   superseded sessions still cited as current; a number in the text disagreeing with its own
   table/figure; captions describing values the figure doesn't show; any "superseded, do not
   cite" claim that isn't actually retired everywhere. Two earlier passes each missed
   figure-caption drift — grep aggressively for every literal number and check the session it
   belongs to.

9. **The open items (already flagged, judge the framing).** (a) Knative idle-w calibration is
   a *single* 60-second RAPL read — three readings across sessions gave 11.14 / 7.01 / 4.91 W,
   and the "~2.7 W idle premium" claim is marked not-citable pending an N≥3 repeated-read
   methodology. Is marking it open the right call, or is the paper being too
   conservative/not conservative enough? (b) The regression reference needs a same-day
   old-runner A/B recalibration next session.

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
- For each of the 9 items: **CONFIRMED / REFUTED / PARTIALLY** + one-line evidence
  (file:line or git command output).
- Re-derived headline numbers table (your values vs claimed values, delta).
- Any new bug you found (file:line, severity, whether it changes any citable number).
- Judgment: are the four claims publishable as stated, and what (if anything) must change
  before submission?
- The top 3 risks a hostile reviewer would attack, ranked.

Be exact. Cite file:line and git SHAs. Do not accept any claim you have not reproduced.
