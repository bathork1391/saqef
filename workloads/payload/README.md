# W1 payload-echo handlers (runbook §28)

Drop-in replacements for the four CPU-bound handlers. `tools/workload.sh swap payload`
copies each file over the path it mirrors (`hello/func.py`, `OF_FUNCTION/handler.py` and `index.py`,
`KNATIVE_FUNCTION/app.py`, `OW_FUNCTION/hello.py`); `restore` git-checks them out again.

Every handler reads the whole request body and returns it unchanged, with no other work.
A GET (no body) returns an empty body, so the adapters' readiness probes still pass.

`OF_FUNCTION/index.py` is swapped too: of-watchdog forwards POST bodies chunked (no
Content-Length), which the CPU-bound wrapper read as empty. Found by the probe, 2026-10-03.
