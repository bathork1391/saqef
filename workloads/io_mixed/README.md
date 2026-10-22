# W5 I/O handlers, mixed arm (runbook §36)

SAQEF_IO = "mixed": each call spins the CPU for 2.5 ms, then waits 2.5 ms (`time.sleep`): half CPU, half wait, at the same 5 ms as the spin and sleep arms. Identical to `io_spin`, `io_sleep` and `io_disk` except that one line (tests/test_saqef_cli.py checks it). Unmeasured check: `tools/workload.sh probe-io mixed`.
