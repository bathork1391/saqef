# OpenFaaS Setup & Measurement Guide (Codespaces)

Target: run the **same** function image and the **same** SAQEF protocol against OpenFaaS as we ran against Fn, so `cp_dynamic_share_pct` and QoS are directly comparable.

## Why swarm mode (not faasd, not kind/k8s)

| Option | Verdict |
|---|---|
| **Docker Swarm (hand-written stack, this file)** | **Recommended.** Mirrors Fn's single-dockerd architecture, one command to deploy, a *small, identifiable control-plane container set* (perfect for `--cp-containers`), light enough for a 2-vCPU Codespace. |
| faasd | ❌ Needs `systemd` — not present in a Codespace container (`ps -p 1` shows `docker-init`). |
| kind / arkade → Kubernetes | ❌ Works in principle but heavy on 2 vCPU; control plane = many pods across namespaces (harder to attribute). Revisit only for Knative later (K8s is unavoidable there). |

> ⚠️ **Image-set constraint (verified 2026-08-05):** OpenFaaS dropped Docker Swarm support. No faas tag (0.25.4, 0.27.10–0.27.14) ships `deploy_stack.sh`; the 0.27.14 README documents only k8s/OpenShift/faasd; the 0.17-era `openfaas/*` images the archived `faas-swarm` repo referenced are **deleted from Docker Hub**. The last era-coherent, still-pullable swarm set is the 2019 `functions/*` line (`gateway:0.8.3`, `faas-swarm:0.3.3`, `queue-worker:0.4.6`). The stack in `OPENFAAS_DEPLOY/docker-compose.yml` pins exactly these (all verified pullable). gateway 0.8.3 predates HTTP basic auth → **no login, no secrets, no `--auth`** in the harness commands.

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

The deploy files live in this repo under `OPENFAAS_DEPLOY/` (`docker-compose.yml` + `prometheus/*.yml`). Copy them onto the Codespace (or recreate them from this repo), then:

```bash
cd OPENFAAS_DEPLOY
docker swarm init                          # one-time; node becomes a manager
docker stack deploy openfaas -c docker-compose.yml
```

Wait for readiness:
```bash
watch docker service ls
# gateway should reach 1/1 replicas; stack name = openfaas, services = openfaas_gateway, openfaas_faas-swarm, openfaas_nats, openfaas_queue-worker, openfaas_prometheus, openfaas_alertmanager
curl -s http://127.0.0.1:8080/version     # 0.8.3 gateway has NO basic auth
```

## 4. Same function workload (the 5 ms CPU busy-spin, identical to Fn)

Goal: byte-for-byte the **same CPU workload** we deployed on Fn (`hello/func.py` — a 5 ms **busy-spin** loop). ⚠️ Do **NOT** use `time.sleep(0.005)`: a sleep burns ~0 CPU (measured locally: 0.09 ms/call vs 4.2 ms/call for the spin) and would zero out `fn_cpu`, faking a platform "discrimination". The spin is what makes the per-invocation CPU budget real.

```bash
mkdir -p ~/of-hello && cd ~/of-hello
faas-cli template store pull python3       # one-time; needed for `faas-cli new` (and first build)
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

Build + deploy (no auth on the 0.8.3 gateway):
```bash
faas-cli build -f hello.yml
faas-cli deploy -f hello.yml --gateway http://127.0.0.1:8080
```

Verify:
```bash
faas-cli list
echo -n test | faas-cli invoke hello
# synchronous URL used by the harness:
curl -s http://127.0.0.1:8080/function/hello
```

> Auth note: gateway 0.8.3 predates HTTP basic auth → **no** `faas-cli login`, no `--auth`, no `-u admin:<password>` anywhere.

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
  --sampler cgroup --delta-check \
  --cp-containers gateway,faas-swarm,prometheus,nats,queue-worker,alertmanager \
  --fn-images hello \
  --verify-n 100 --verify-budget-ms 5
# expect function_cpu_ms_per_inv ≈ 5 and budget_check == "MATCHES"
```

Step 1 — full protocol (recommended: `--sampler cgroup --delta-check`, and `--loadgen hey` if installed):
```bash
python3 saqef_harness.py \
  --url http://127.0.0.1:8080/function/hello \
  --platform openfaas \
  --cp-containers gateway,faas-swarm,prometheus,nats,queue-worker,alertmanager \
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
- **`deploy_stack.sh` no longer exists in any faas tag** (swarm support removed); the stack in `OPENFAAS_DEPLOY/` is the replacement. Do not checkout `0.27.14` expecting a swarm script — it is k8s-only.
- Swarm services do not appear in `docker ps` until their tasks start; wait for `docker service ls` replicas to be 1/1 before `--check`.
- Port 8080 conflict with Fn's fnserver → stop fnserver first (`docker rm -f fnserver`) or run OpenFaaS on another published port and set `--url` accordingly.
- **0.8.3 gateway has no basic auth** → do not add `--auth`, `faas-cli login`, or `-u admin:<password>`; they will fail or be ignored.
- First `faas-cli build` pulls the python3 template + of-watchdog base → needs network + a couple of minutes on cold cache.
- **Sleep ≠ spin (§4):** a `time.sleep(0.005)` handler burns ~0 CPU and will make OpenFaaS look "cheaper" — that is a workload difference, not a platform result. Verify `function_cpu_ms_per_inv` matches Fn's band before comparing `cp_dynamic_share_pct`.

## 9. What to record in the paper/report

- OpenFaaS + faas-cli versions: gateway `functions/gateway:0.8.3`, `functions/faas-swarm:0.3.3`, `functions/queue-worker:0.4.6`, `nats-streaming:0.25.6`, `prom/prometheus:v2.11.0`, `prom/alertmanager:v0.18.0` (the last pullable swarm-era image set; no basic auth).
- Why not a newer release: OpenFaaS removed Docker Swarm support; the 0.27.14 line is k8s/faasd-only (documented in the README, verified 2026-08-05).
- The exact `docker service ls` replica list and container names (control-plane attribution).
- No admin password exists on 0.8.3 — nothing to protect in the env log.
