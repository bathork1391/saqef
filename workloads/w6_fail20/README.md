# W6 handlers, fail20 arm (runbook §38)

SAQEF_W6 = "fail20": as base, but every 5th call of each handler process answers HTTP 500 after doing its 5 ms of work (20 % designed failures). Identical to the other `w6_*` arms except that one line (tests/test_saqef_cli.py checks it). OpenFaaS also gets `index.py`, whose upstream server sends the handler's HTTP status. Unmeasured check: `tools/workload.sh probe-w6 fail20`.
