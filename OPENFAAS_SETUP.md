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
git checkout 0.27.14   # latest stable release tag (verified 2026-08-05; there are no 0.3x tags — pin this for the report)
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

## 4. Same function workload (the 5 ms CPU busy-spin, identical to Fn)

Goal: byte-for-byte the **same CPU workload** we deployed on Fn (`hello/func.py` — a 5 ms **busy-spin** loop). ⚠️ Do **NOT** use `time.sleep(0.005)`: a sleep burns ~0 CPU (measured locally: 0.09 ms/call vs 4.2 ms/call for the spin) and would zero out `fn_cpu`, faking a platform "discrimination". The spin is what makes the per-invocation CPU budget real.

```bash
mkdir -p ~/of-hello && cd ~/of-hello
faas-cli new hello --lang python3   # creates hello/handler.py + hello.yml
```

Edit `hello/handler.py` to mirror Fn's busy-spin exactly:
```python
import time

def handle(req):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.005:
        pass
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
- **Gate:** Fn baseline = `cp_dynamic_share_pct` **23.59–24.59** (five clean sessions, report G6). If OpenFaaS differs from Fn by **>5pp** → the metric discriminates → proceed to bare metal (RAPL) for the definitive numbers. If within noise → redesign the metric *before* paying for bare-metal experiments.
- **Workload cross-check first:** `--verify` should report `function_cpu_ms_per_inv` in the same band as Fn (near-uncontended ≈3.5–4.0; the bench value will read lower, ~1.9, because host saturation dilutes per-call CPU attribution on the 2-vCPU box). If verify lands near 0, the deployed handler is sleeping, not spinning — stop and fix §4 before comparing.

## 8. Known pitfalls

- `docker swarm init` fails if another swarm is active → `docker swarm leave --force` then re-init.
- Swarm services do not appear in `docker ps` until their tasks start; wait for `docker service ls` replicas to be 1/1 before `--check`.
- Port 8080 conflict with Fn's fnserver → stop fnserver first (`docker rm -f fnserver`) or run OpenFaaS on another published port and set `--url` accordingly.
- First `faas-cli build` pulls the python3 template → needs network + a couple of minutes on cold cache.
- **Sleep ≠ spin (§4):** a `time.sleep(0.005)` handler burns ~0 CPU and will make OpenFaaS look "cheaper" — that is a workload difference, not a platform result. Verify `function_cpu_ms_per_inv` matches Fn's band before comparing `cp_dynamic_share_pct`.

## 9. What to record in the paper/report

- OpenFaaS + faas-cli + gateway image versions (pinned tags).
- The exact `docker service ls` replica list and container names (control-plane attribution).
- The generated admin password in the env log (never in the repo).
