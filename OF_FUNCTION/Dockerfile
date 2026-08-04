# Hand-written OpenFaaS function (no template-store dependency; the store no longer ships python3).
# Mirrors the classic openfaas python3 template: of-watchdog streaming mode -> `python3 index.py`.
FROM ghcr.io/openfaas/of-watchdog:0.9.12 AS watchdog
FROM python:3.11-alpine

COPY --from=watchdog /fwatchdog /usr/bin/fwatchdog
RUN apk add --no-cache ca-certificates

WORKDIR /home/app

COPY index.py handler.py requirements.txt ./

ENV fprocess="python3 index.py"
ENV mode="streaming"
ENV exec_timeout="10s"
ENV read_timeout="60s"
ENV write_timeout="60s"

RUN chmod +x /usr/bin/fwatchdog

CMD ["fwatchdog"]
