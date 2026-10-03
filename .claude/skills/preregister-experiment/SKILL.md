---
name: preregister-experiment
description: Design, pre-register and wire up a new SAQEF measurement (workload, arm or amendment) so it runs as one go.sh command. Use when starting W2/W3/W4 or any new comparison, before any data for it exists.
---

# Pre-register a SAQEF experiment

1. **Question first.** One sentence: what the experiment decides, and which paper claim depends on
   it. Check the roadmap (runbook §26 and the latest sections) and the runbook index: has it been
   answered, rejected, or marked "do not repeat"? Can data already on disk answer it? If yes, stop.
2. **Simplest design that answers it.** Prefer a within-session A/B (both arms in one session,
   alternating order) over comparing against an old session. Reuse Part A's protocol unless the
   question needs a change: TOTAL = 3000, light `--repeat 5`, OW `--repeat 6 --discard-warmup 1`,
   `--cpu-probe 60`, in-session idle-w calibration, one `_r2` retry, settle after verify.
3. **Anchors.** Pull the existing numbers the predictions are judged against with the tools
   (`cp_anatomy.py`, `idle_crosscheck.py`, VERIFIED_RESULTS tables). Quote them in the text.
4. **Write the runbook section before any data:** question, fixed design (legs, order, prefix,
   expected duration, watchdog), anchors, numbered predictions with numeric thresholds, decision
   rules (failed prediction = finding, failed-twice leg = missing, no rescue re-runs, what is
   pooled and what is not, where results go). Include the exact marker line the pre-flight checks:
   `Arm <id>: pre-registered` or `Amendment <id>: pre-registered`. Gates are frozen from here on.
5. **Wire it.** Add the legs to `tools/run_final.sh` (`--workload`, `--arm` or `--amend`) and the
   flag/session name to `tools/go.sh` (`--status` glob too). Make sure an arm never falls through
   into the full corpus. New platform behaviour goes behind an env var or flag, default unchanged.
6. **Check.** `bash -n`, `python3 -m unittest discover -s tests -q`,
   `sudo bash tools/run_final.sh --dry-run <flags>`: the plan must list exactly the intended legs,
   and the only pre-flight problems allowed are "graphical session" and "agent running".
   Smoke-test any new platform setting unmeasured (deploy, ~500 requests, teardown).
7. **Commit and push** (the pre-flight refuses uncommitted measurement code). Tell the user the
   single command, the expected duration, and what the result will decide.
