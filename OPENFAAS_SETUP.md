# OpenFaaS Setup & Measurement Guide (Codespaces)

Target: run the **same** function image and the **same** SAQEF protocol against OpenFaaS as we ran against Fn, so `cp_dynamic_share_pct` and QoS are directly comparable.

## Why swarm mode (not faasd, not kind/k8s)

| Option | Verdict |
|---|---|
| **Docker Swarm (`deploy_stack.sh`)** | **Recommended.** Mirrors Fn's single-dockerd architecture, one command to deploy, a *small, identifiable control-plane container set* (perfect for `--cp-containers`), light enough for a 2-vCPU Codespace. |
| faasd | ❌ Needs `systemd` — not present in a Codespace container. |
| kind / arkade → Kubernetes | ❌ Works in principle but heavy on 2 vCPU; control plane = many pods across namespaces (harder to attribute). Revisit only for Knative later (K8s is unavoidable there). |

## 1. Preflight

```bash
docker info --format '{{.ServerVersion}}'     # expect 29.x
docker ps                                     # must work (no DinD)
grep -c processor /proc/cpuinfo                # record the core count for the report
```

## 2. Install faas-cli

```bash
curl -sSL https://cli.openfaas.com | sudo sh
faas-cli version
```

## 3. Deploy OpenFaaS to Swarm

```bash
git clone https://github.com/openfaas/faas /tmp/faas
cd /tmp/faas
git checkout <latest stable release tag>   # e.g. 0.31.x — pin this for the report
docker swarm init
./deploy_stack.sh
```
`deploy_stack.sh` prints the generated **admin password** — copy it into the report/env now.

Wait for readiness:
```bash
watch docker service ls
# gateway should reach 1/1 replicas
curl -s -u admin:<password> http://127.0.0.1:8080/system/functions | head
```

## 4. Same function image (the 5 ms busy-loop Python handler)

Goal: byte-for-byte the same workload we used on Fn (pure `time.sleep(0.005)`, no FDK coupling beyond the platform's handler contract).

```bash
mkdir -p ~/of-hello && cd ~/of-hello
faas-cli new hello --lang python3   # creates hello/handler.py + hello.yml
```

Edit `hello/handler.py`:
```python
import time

def handle(req):
    time.sleep(0.005)
    return "Hello"
```

`hello.yml`:
```yaml
version: 1.0
provider:
  name: openfaas
  gateway: http://127.0.0.1:8080
functions:
  hello:
    lang: python3
    handler: ./hello
    image: hello:latest
    environment:
      read_timeout: "60s"
      write_timeout: "60s"
```

Build + deploy:
```bash
faas-cli build -f hello.yml
faas-cli deploy -f hello.yml --gateway http://127.0.0.1:8080
# note: faas-cli may prompt for auth -> admin:<password>; use OPENFAAS_URL env:
export OPENFAAS_URL=http://127.0.0.1:8080
faas-cli login -u admin -p <password>
```

Verify:
```bash
faas-cli list
echo -n test | faas-cli invoke hello
# synchronous URL used by the harness:
curl -s -u admin:<password> http://127.0.0.1:8080/function/hello
```

> Auth note: the OpenFaaS gateway enforces HTTP Basic auth by default. The harness now supports it:
> `python3 saqef_harness.py ... --auth admin:<password>`

## 5. Control-plane container set for `--cp-containers`

Swarm deploy creates these control-plane containers (record the exact `docker service ls` list; the set is stable):
`gateway`, `faas-swarm` (provider), `prometheus`, `nats`, `queue-worker`, `alertmanager`.

```bash
python3 saqef_harness.py --check
docker ps --format '{{.Names}}'   # confirm names seen by the sampler
```

Run with:
```
--cp-containers gateway,faas-swarm,prometheus,nats,queue-worker,alertmanager
```

## 6. Measurement protocol (identical to Fn)

Step 0 — verify the deployed workload actually ships the 5 ms of work:
```bash
python3 saqef_harness.py --verify \
  --url http://127.0.0.1:8080/function/hello \
  --platform openfaas \
  --cp-containers gateway,faas-swarm,prometheus,nats,queue-worker,alertmanager \
  --auth admin:<password> \
  --verify-n 100 --verify-budget-ms 5
# expect function_cpu_ms_per_inv ≈ 5 and budget_check == "MATCHES"
```

Step 1 — full protocol (recommended: `--sampler cgroup --delta-check`, and `--loadgen hey` if installed):
```bash
python3 saqef_harness.py \
  --url http://127.0.0.1:8080/function/hello \
  --platform openfaas \
  --cp-containers gateway,faas-swarm,prometheus,nats,queue-worker,alertmanager \
  --auth admin:<password> \
  --total 3000 --concurrency 20 --duration 60 \
  --warmup 20 --repeat 5 \
  --sampler cgroup --delta-check --loadgen hey \
  --outdir results/openfaas_cpubound
```

## 7. Expected output + comparison gate

- `summary.json` median + `spread_min_max` per run.
- **Gate:** if `cp_dynamic_share_pct` differs from Fn's ~27% by **>5pp** → the metric discriminates → proceed to bare metal (RAPL) for the definitive numbers. If within noise → redesign the metric *before* paying for bare-metal experiments.

## 8. Known pitfalls

- `docker swarm init` fails if another swarm is active → `docker swarm leave --force` then re-init.
- Swarm services do not appear in `docker ps` until their tasks start; wait for `docker service ls` replicas to be 1/1 before `--check`.
- Port 8080 conflict with Fn's fnserver → stop fnserver first (`docker rm -f fnserver`) or run OpenFaaS on another published port and set `--url` accordingly.
- First `faas-cli build` pulls the python3 template → needs network + a couple of minutes on cold cache.

## 9. What to record in the paper/report

- OpenFaaS + faas-cli + gateway image versions (pinned tags).
- The exact `docker service ls` replica list and container names (control-plane attribution).
- The generated admin password in the env log (never in the repo).
