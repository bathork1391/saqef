# W5 I/O handlers, spin arm (runbook §36)

SAQEF_IO = "spin": each call spins the CPU for 5 ms, the same loop as the CPU-bound handlers, so W5 has its own CPU-bound baseline in the same session. Identical to `io_sleep`, `io_disk` and `io_mixed` except that one line (tests/test_saqef_cli.py checks it). Unmeasured check: `tools/workload.sh probe-io spin`.
