# Hand-written OpenFaaS function (no template-store dependency; the store no longer ships python3).
# of-watchdog HTTP mode: python stays resident (index.py serves 8081), fwatchdog proxies 8080.
# This matches Fn's model (long-lived handler, one handle() call per invocation) so per-invocation
# CPU is the 5 ms busy-spin only - NOT python interpreter startup (streaming mode would add that).
FROM ghcr.io/openfaas/of-watchdog:0.9.12 AS watchdog
FROM python:3.11-alpine

COPY --from=watchdog /fwatchdog /usr/bin/fwatchdog
RUN apk add --no-cache ca-certificates

WORKDIR /home/app

COPY index.py handler.py requirements.txt ./

ENV fprocess="python3 index.py"
ENV mode="http"
ENV upstream_url="http://127.0.0.1:8081"
ENV exec_timeout="10s"
ENV read_timeout="60s"
ENV write_timeout="60s"

RUN chmod +x /usr/bin/fwatchdog

CMD ["fwatchdog"]
