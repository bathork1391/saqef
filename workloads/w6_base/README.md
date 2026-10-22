# W6 handlers, base arm (runbook §38)

SAQEF_W6 = "base": every call spins the CPU for 5 ms and succeeds: the in-session baseline (the overload and half-load legs use it too). Identical to the other `w6_*` arms except that one line (tests/test_saqef_cli.py checks it). OpenFaaS also gets `index.py`, whose upstream server sends the handler's HTTP status. Unmeasured check: `tools/workload.sh probe-w6 base`.
