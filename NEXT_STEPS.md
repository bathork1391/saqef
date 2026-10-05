# Next steps (kept current; last updated 2026-10-05)

The one place that says what is still to run or do. Details live in `TROUBLESHOOTING_RUNBOOK.md`
(section numbers below). Results go to `../saqef-paper/VERIFIED_RESULTS.md`.

## Before any measurement night
- Laptop on its charger, battery at 80 % or more and **charging**. The pre-flight refuses a capped
  CPU (§31.14); a power cut stops the session cleanly (§32).
- Quit Claude Code / opencode; the session leaves the desktop itself.
- Progress at any time: `sudo bash tools/go.sh --status`

## To run, in this order (each one command, all pre-registered, code committed)
| # | command | what it answers | time | section |
|---|---|---|---|---|
| 1 | `sudo bash tools/go.sh --workload cold` | W4: cost of a burst into an empty pool (Fn, OpenWhisk, Knative), plus Fn's W3 B2/B4 | ~2.3 h | §32 |
| 1b | `sudo bash tools/go.sh --workload cold --part 2` | only if a power cut stopped #1: runs the unfinished platform blocks | ≤ 2 h | §32 rule 3 |

**Done:** 33.1 and 33.2 ran 2026-10-04 (`go.sh --revisit 33`), both finished, all legs passed on
attempt 1; outcomes §33.1 / §33.2, tables W1-T12 and D-T4. A finished amendment cannot be rerun.

## After each run (done by Claude with the adjudicate-session skill: "check the last run")
- W4: `tools/cold_analysis.py`, outcome §32.1, VERIFIED_RESULTS Part G.

## Offline work still open (no machine time)
- A test that checks key numbers in the runbook against `results/*_analysis/*.json` (§31.18 D).
- W4: the within-run power-vs-load check (§25.6), now possible because `energy_trace.csv` records
  `host_busy_ticks` (§34.2).

## Git housekeeping (done 2026-10-05)
- GitHub: `saqef` has only `main` (+ tags v9.14-remeasure, v9.14.1-remeasure); `saqef-paper` only `master`.
  No commit reachable on GitHub carries a Claude co-author line (checked with GitHub's API).
- Removed locally, after a verified full backup: the merged branches `lock-summary-rapl-joules` and
  `refactor/adapters`, the pre-strip backup branches/tags, tag `archive/old-remote-main-20260817`.
- Everything removed is in `../git-backups/saqef_all_refs_20261004T2059Z.bundle` and
  `../git-backups/saqef-paper_all_refs_20261004T2059Z.bundle` (`git bundle verify` OK). Restore a
  ref with `git fetch <bundle> <ref>:<ref>`. The local stash was kept.

## Possible later sessions (not pre-registered)
- Burst-size ladder at a fixed total and fixed pool: separates per-request from per-burst cost.
- OpenFaaS CE on Kubernetes (W4b): only after written licence confirmation from OpenFaaS.
- RAPL against a wall-plug meter: needs the meter.
