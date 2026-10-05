# Next steps (kept current; last updated 2026-10-05, after W4)

The one place that says what is still to run or do. Details live in `TROUBLESHOOTING_RUNBOOK.md`
(section numbers below). Results go to `../saqef-paper/VERIFIED_RESULTS.md`.

## Before any measurement night
- Laptop on its charger, battery at 80 % or more and **charging**. The pre-flight refuses a capped
  CPU (§31.14); a power cut stops the session cleanly (§32).
- Quit Claude Code / opencode; the session leaves the desktop itself.
- Progress at any time: `sudo bash tools/go.sh --status`

## Resume here (paused 2026-10-05, after W4 was adjudicated; both repos pushed, nothing running)
Recommended order, all offline (no machine time):
0. Two wording fixes from the review of §32.1 (runbook, "Review of §32.1", items 1–2): B3 is not
   predicted ("B0, B1, B2, B4 hold"); label OpenWhisk's C3 + C3u failure with §32's "not resolvable
   above noise" clause in §32.1 and Part G. Minutes.
1. Test that key runbook numbers match `results/*_analysis/*.json` (§31.18 D). Short.
2. Final paper draft from V5, now that every workload is in (VERIFIED_RESULTS Parts A–H).
3. Within-run power-vs-load check (§25.6) on the `cold_` legs (first data with `host_busy_ticks`).
Optional loose ends: Knative C4 (cold later bursts 17 % faster than warm at equal pods) and
OpenWhisk run_2/run_3 high cp in both W4 arms (§32.1); both causes not examined.

## To run, in this order (each one command, all pre-registered, code committed)
Nothing is queued. Every pre-registered workload (Part A, W1, W2, W3, W4) and amendment has run.
A new session needs a pre-registration first (preregister-experiment skill).

**Done:** W4 cold start ran 2026-10-05 (`cold_`), 8 of 8 legs passed on attempt 1; outcome §32.1,
VERIFIED_RESULTS Part G (G-T0–G-T2) and F-T6. 33.1 and 33.2 ran 2026-10-04; outcomes §33.1 / §33.2.

## After each run (done by Claude with the adjudicate-session skill: "check the last run")
- Outcome in the runbook, tables in VERIFIED_RESULTS, both repos backed up and pushed.

## Offline work still open (no machine time)
- A test that checks key numbers in the runbook against `results/*_analysis/*.json` (§31.18 D).
- W4: the within-run power-vs-load check (§25.6), now possible because `energy_trace.csv` records
  `host_busy_ticks` (§34.2); the `cold_` legs are the first data with it.
- W4 Knative C4 (cold later bursts 17 % faster than warm at equal pods): cause not examined (§32.1).

## Git housekeeping (done 2026-10-05)
- GitHub: `saqef` has only `main` (+ tags v9.14-remeasure, v9.14.1-remeasure); `saqef-paper` only `master`.
  No commit reachable on GitHub carries a Claude co-author line (checked with GitHub's API).
- Removed locally, after a verified full backup: the merged branches `lock-summary-rapl-joules` and
  `refactor/adapters`, the pre-strip backup branches/tags, tag `archive/old-remote-main-20260817`.
- Everything removed is in `../git-backups/saqef_all_refs_20261004T2059Z.bundle` and
  `../git-backups/saqef-paper_all_refs_20261004T2059Z.bundle` (`git bundle verify` OK). Restore a
  ref with `git fetch <bundle> <ref>:<ref>`. The local stash was kept.

## Possible later sessions (not pre-registered)
- Knative cold vs warm with the warm pool pre-grown to 16 pods (W4's warm arm was 9–16, §32.1).
- Burst-size ladder at a fixed total and fixed pool: separates per-request from per-burst cost.
- OpenFaaS CE on Kubernetes (W4b): only after written licence confirmation from OpenFaaS.
- RAPL against a wall-plug meter: needs the meter.
