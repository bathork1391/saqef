# W2 memory-bound handlers, mem_dram arm (runbook §30)

SAQEF_MEM_KIB = 65536. Identical to the other arm except that one line (tests/test_saqef_cli.py checks it). `tools/workload.sh swap mem_dram` copies the four files over the CPU-bound handlers; `restore` puts those back. Requests are bare GETs, as in the CPU workload.
