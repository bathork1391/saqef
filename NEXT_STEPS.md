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
| 2 | `sudo bash tools/go.sh --workload payload --amend 33.1` | W1's missing Knative 64k c=8 cell (+ bridge) | ~1 h | §33 |
| 3 | `sudo bash tools/go.sh --arm owlog29 --amend 33.2` | OpenWhisk log-store comparison at c=8 | ~50 min | §33 |

## After each run (done by Claude with the adjudicate-session skill: "check the last run")
- W4: `tools/cold_analysis.py`, outcome §32.1, VERIFIED_RESULTS Part G.
- 33.1 / 33.2: outcome in §33, same tables as the original cells with a footnote.

## Offline work still open (no machine time)
- A test that checks key numbers in the runbook against `results/*_analysis/*.json` (§31.18 D).
- W4: the within-run power-vs-load check (§25.6), now possible because `energy_trace.csv` records
  `host_busy_ticks` (§34.2).

## Decisions waiting on the user
- Delete the merged GitHub branch `lock-summary-rapl-joules` (all its commits are in `main`)?
- Delete the local `backup/pre-strip*` branches (old commits with the Claude trailer)?

## Possible later sessions (not pre-registered)
- Burst-size ladder at a fixed total and fixed pool: separates per-request from per-burst cost.
- OpenFaaS CE on Kubernetes (W4b): only after written licence confirmation from OpenFaaS.
- RAPL against a wall-plug meter: needs the meter.
