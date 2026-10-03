---
name: adjudicate-session
description: Adjudicate a finished SAQEF measurement session (go.sh night) against its pre-registration, record it in the runbook and VERIFIED_RESULTS, back it up and push. Use when the user asks "how did the run go", "any results", "check the last run", or after a session's DONE file appears.
---

# Adjudicate a SAQEF session

Follow in order. Do not summarise numbers to the user before step 4.

1. **State of the run.** `sudo bash tools/go.sh --status`; read `results/<prefix>session/session.log`
   and `checkpoint.tsv`. Note every failed leg and the exact reason from its `leg_<stamp>.log`
   (quiet gate value, DRIFT numbers, INCOMPLETE, SUCCESSES). Check the runbook index for each symptom
   before diagnosing it.
2. **Pre-registration.** Find the session's section in `TROUBLESHOOTING_RUNBOOK.md` (grep the prefix
   and "pre-registered"). Copy its predictions, thresholds and decision rules into your working notes.
3. **Numbers, from the tools, never by hand:**
   - `python3 tools/cp_anatomy.py --prefix <prefix> --legs '<pattern>' [--jvm-dir <prefix>session] --json ...`
     (careful: `--prefix payload_ --legs '*'` also matches amendment legs; use a narrower pattern).
   - `python3 tools/idle_crosscheck.py --prefix <prefix> --legs '<pattern>' --calib lock_<prefix>calib --json ...`
   - per-run values and `stability` from each leg's `acceptance.json` (only `usable_runs`).
4. **Adjudicate** each prediction per platform/cell against its threshold. Failed prediction = finding.
   Apply the decision rules literally (retries, missing cells, pooling). For any cause you propose,
   check it against existing legs first; mark anything not pre-registered as post hoc.
5. **Verify your own claims** before writing: recompute every number you will quote from the files;
   for OW legs check the leg log shows the intended configuration (e.g. "activation log store = ...").
6. **Record.**
   - Runbook: new subsection "<n>.x outcome" with legs, verdicts, findings, corrections, tooling
     changes, and a "Do not repeat" line. Add rows to the symptom index for any new bug/gotcha.
   - `../saqef-paper`: copy every `results/*<prefix>*` dir (and `idle_w_calibration/lock_<prefix>calib`)
     into `../saqef-paper/results/`, write analysis JSON under `results/<name>_analysis/`, extend
     the table generator, re-run `python3 tools/emit_verified_results.py`, and confirm existing
     lines of VERIFIED_RESULTS.md are unchanged (`git diff` shows additions only).
7. **Commit and push both repos** (plain messages, no Claude attribution). Tests must pass first:
   `python3 -m unittest discover -s tests -q`.
8. **Report to the user:** what the data shows first (verdicts, key numbers), then failures and
   caveats, then the next roadmap step. Plain language; no command lists for them to run.
