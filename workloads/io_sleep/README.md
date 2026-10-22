# W5 I/O handlers, sleep arm (runbook §36)

SAQEF_IO = "sleep": each call waits 5 ms (`time.sleep`), the pure-wait control. Identical to the other three W5 arms except that one line (tests/test_saqef_cli.py checks it). `tools/workload.sh swap io_sleep` copies the four files over the CPU-bound handlers; `restore` puts those back. Requests are bare GETs, as in the CPU workload. Unmeasured check: `tools/workload.sh probe-io sleep`.
