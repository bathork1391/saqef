# W6 handlers, alt arm (runbook §38)

SAQEF_W6 = "alt": calls alternate per process: odd calls spin 5 ms, even calls wait 5 ms (time.sleep); all succeed (mixed traffic). Identical to the other `w6_*` arms except that one line (tests/test_saqef_cli.py checks it). OpenFaaS also gets `index.py`, whose upstream server sends the handler's HTTP status. Unmeasured check: `tools/workload.sh probe-w6 alt`.
