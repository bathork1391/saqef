# W2 memory-bound handlers, mem_cache arm (runbook §30)

SAQEF_MEM_KIB = 256. Identical to the other arm except that one line (tests/test_saqef_cli.py checks it). `tools/workload.sh swap mem_cache` copies the four files over the CPU-bound handlers; `restore` puts those back. Requests are bare GETs, as in the CPU workload.
