# SAQEF — working rules for Claude (and any agent)

Research code for a pre-registered measurement study: control-plane (cp) CPU, energy and carbon
overhead of four FaaS platforms (OpenFaaS, Fn, Knative, OpenWhisk standalone) on one 8-core box.
Results are only as good as the discipline below. Every rule here was paid for with a lost night.

## Where things are
- `NEXT_STEPS.md`: what is still to run or do, with the exact commands. Keep it current.
- `TROUBLESHOOTING_RUNBOOK.md`: bug ledger, every pre-registration and outcome. **Start at its
  "Index: symptom → section" table.** Append a row there whenever you record a new bug or gotcha.
- `../saqef-paper/VERIFIED_RESULTS.md`: the ONE results file (Part A CPU corpus, Part C W1, ...),
  emitted by `../saqef-paper/tools/emit_verified_results.py`. No side tables anywhere else.
- Measurement nights: one command, `sudo bash tools/go.sh [--workload X | --arm Y]`;
  progress `sudo bash tools/go.sh --status`. Never hand the user multi-step command lists.
- `results/` is git-ignored here; `../saqef-paper/results/` is the committed backup. Back up
  every session there, then commit and push both repos.

## Before interpreting any run
1. Read the run's pre-registration (predictions + decision rules) in the runbook first.
   Judge against it, not against raw numbers. A failed prediction is a finding, never a re-run.
2. Aggregate runs only through each leg's `acceptance.json` `usable_runs` (runbook §23, §28.7).
3. Recompute claims per usable run, not over the whole leg.
4. Before claiming a cause, test it on the legs already on disk (~50 legs). Propose a new
   experiment only if existing data cannot answer it (§28.9 B).
5. Current load: `/proc/stat` deltas or `pidstat`. Never `ps %CPU` (lifetime average).
6. A bridge/comparison across sessions with n = 5 short runs cannot size a "day shift".
   Prefer within-session A/B designs.
7. RAPL FIT warnings are expected (retired 3.5 W/core model, §25.1); energy is RAPL, probe basis.
8. Label OpenWhisk results "OpenWhisk standalone": its ~12 ms/inv child-process cost is the
   standalone's `docker logs` log collector (§28.9 D). Turning it off (`SAQEF_OW_LOGSTORE=driver`,
   §29.1) cuts OW cp ~21 → ~3 ms/inv and lifts throughput from ~100 to ~305 rps, where OW's
   2 action containers saturate (§29.2 D; light platforms run 16 replicas). Say both when
   comparing OW throughput, latency or energy per invocation.
9. The quiet gate's floor is the stacks' own idle CPU (~7–8 % of 8 cores, §29.2 A). A gate
   failure after a deploy is start-up noise, not an outside process; read the gate's window
   process list (own vs reaped columns), never `ps`.

## Changing code
- Measurement-path code must be committed before a night (go.sh pre-flight enforces it).
- Run `python3 -m unittest discover -s tests -q` after any change to `tools/`, `platforms/`,
  `saqef`, `saqef_harness.py`. Dry-run: `sudo bash tools/run_final.sh --dry-run [flags]`.
- A changed analysis tool must reproduce the committed outputs byte-for-byte (diff against
  `../saqef-paper/results/*_analysis/*.json`) before its new output is trusted.
- New behaviour that would change a closed session's gates goes behind a flag, default off.
- Pre-registered protocols are frozen: never change a gate after seeing that workload's data.

## Writing results
- Lead with what the data shows, then caveats. Mark post hoc analyses as post hoc.
- Commits: plain messages, no Claude/Anthropic co-author or attribution anywhere.
- A project hook (`.claude/settings.json`) runs the unit tests after every Write/Edit to
  `tools/`, `platforms/`, `saqef` or `saqef_harness.py` and reports failures back.
