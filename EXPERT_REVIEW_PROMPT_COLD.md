# SAQEF expert review prompt — COLD PASS

Copy-paste prompt for an independent expert reviewer. Purpose: adversarial reverification of
every headline number before publication, plus a cold read of the four newest code paths.

**Give the reviewer a clean clone at the pinned commit and no other context beyond this
file.** Do NOT give them AGENTS.md, this file's history, any prior review transcripts, or any
other author notes. The reviewer must approach the code as a stranger would. The pinned
commit must be set by the study authors after their pending commit lands — never hand over a
dirty working tree.

---

**REVIEW PROMPT — SAQEF serverless overhead/carbon study: independent reverification**

You are reviewing a cross-platform measurement study before publication. Your job is
adversarial: assume every headline number and every claimed mechanism is wrong until you have
re-derived it yourself from raw data and from the source code that produced it. Do not trust
any prose — not this prompt, not READMEs, not comments. Everything must trace to a file.

**Repo:** `https://github.com/bathork1391/saqef` (branch `main`, pinned at `311301c`). Work
from a clean clone at that commit so you see exactly what a reviewer would see.

**The study in one paragraph:** Fn, OpenFaaS, Knative, and OpenWhisk each serve an identical
~5 ms CPU-bound handler (thousands of requests, concurrency 4, 5 runs/session). The headline
metric `cp_dynamic_share_pct` = control-plane container CPU / dynamic (load-created) CPU. Key
claims: (1) the Fn-vs-OpenFaaS control-plane gap is machine/core-count dependent (a few pp at
8 cores, ~7 pp when the same box is pinned to 2 cores), not a platform constant; (2) the
four-platform ordering on 8 cores is OpenFaaS < Fn ≈ Knative < OpenWhisk (OpenWhisk's share
is ~80%, far above the others); (3) OpenWhisk's control-plane heaviness is structural, not a
measurement artifact.

**Verify these headline numbers from raw data only.** For each of the four platform result
dirs under `results/`, independently recompute the median `cp_dynamic_share_pct`, the CI/CV,
and the per-invocation control-plane CPU cost directly from `runs.json` / `summary.json`
before reading any prose. Claimed values to check: OpenFaaS ≈7.4, Fn ≈11.6 (3-session
median), Knative ≈12.4, OpenWhisk ≈82.4; per-invocation control-plane CPU cost on the order
of ~0.5 / ~0.75 / ~1 / ~27 ms respectively. Also verify the 2-core claim from the
`results/*_cpubound_2core*` dirs: Fn ≈14, OpenFaaS ≈7, gap ≈7 pp.

**Read the measurement path end to end** (`saqef_harness.py` + the adapter scripts under
`platforms/`). You are specifically checking that the number any claim rests on is produced
honestly. Pay adversarial attention to:

- **How a container is classified as control-plane vs function** — matching logic, allowlists,
  defaults, and failure modes. A misclassification here silently moves every headline number.
  Consider what happens when a container name or image matches the wrong allowlist, when the
  platform leaves a leftover of another platform running, and when the allowlist matches
  nothing at all.
- **The energy/carbon model** — how RAPL counters are read (read the code for wraparound
  handling and how unusable reads are flagged), the idle-baseline term, and the conversion to
  carbon. Watch for unit errors and for whether validation error is honestly reported and
  scoped to the session that produced it.
- **The aggregate summary** — how per-run numbers are merged into a session median, and
  whether containers or keys that appear in only some runs are dropped or preserved.
- **The isolation preconditions** — whether a run refuses to start when another platform's
  leftover is present, and whether that check could falsely block a legitimate run or miss a
  real contaminant.

The **four newest code paths** — the Knative adapter, the OpenWhisk adapter, the isolation
policy, and the summary-aggregation logic — have had the least scrutiny. Read them first and
hardest. They follow different deployment models than the original two platforms; check that
each one's classification and measurement logic is correct on its own terms, not just
consistent with the others.

**Known limitations to be fair about (the authors declare these):** the machine is not a
controlled testbed — there is documented day-to-day drift in its idle power draw; Knative runs
on k3s whose own control plane runs inside the `k3s server` process and is therefore invisible
to a container-based sampler; OpenWhisk runs as the *standalone emulator*, not a production
deployment. Judge whether these boundaries are declared loudly enough in the paper and
whether any of them silently breaks a claim despite the declaration.

**Deliver a verdict like this:**
- Re-derived headline numbers table (your values vs claimed values, delta). Give the raw
  command/output that produced each.
- For each of the four newest code paths: your adversarial findings, with file:line.
- Any bug you found anywhere (file:line, severity, whether it changes any citable number).
- Judgment: are the claims publishable as stated, and what (if anything) must change before
  submission?
- The top 3 risks a hostile reviewer would attack, ranked.

Be exact. Cite file:line and git SHAs. Do not accept any claim you have not reproduced.
