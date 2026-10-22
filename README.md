# SAQEF: a Sustainability-Aware QoS Evaluation Framework for serverless platforms

SAQEF measures what a serverless (FaaS) platform itself costs per request: the CPU time, energy and
carbon used by its **control plane** (gateway, controller, autoscaler, invoker, log collector),
separated from the CPU time of the **function** it runs. *Sustainability-aware*: CPU time, energy and
carbon per request. *QoS*: the same runs record latency (median and p99), throughput and the share of
requests answered, so cost is never read without the service it bought. It was built for a pre-registered study of
four open-source platforms on one machine:

| Platform | Deployment | Control plane | Function |
|---|---|---|---|
| OpenFaaS | Docker Swarm stack, gateway 0.8.3, 16 static replicas | gateway, faas-swarm, queue-worker, NATS, Prometheus, Alertmanager | function containers incl. of-watchdog |
| Fn | `fnserver` | fnserver | Fn function containers |
| Knative | Knative Serving 1.23 + Kourier on k3s 1.36 (Docker runtime), 16 static pods | activator, controller, autoscaler, webhook, Kourier | function pods incl. queue-proxy |
| OpenWhisk standalone | `openwhisk/standalone` image, single container | the standalone container | action containers |

## What it does

- **Splits container CPU by role.** A sampler reads every container's cgroup CPU counters during a
  run and assigns each container to the control plane or the function from the platform's container
  inventory. Containers it cannot classify are reported, never dropped.
- **Checks itself on every run.** The sampled totals are compared with direct before/after cgroup
  reads (the delta check), and host CPU from `/proc/stat` bounds both. A run that disagrees fails its
  gates.
- **Reads energy directly.** Package energy comes from Intel RAPL. Each leg has its own idle probe,
  so energy per request is reported as dynamic energy above the stack's own idle power.
- **Refuses bad conditions.** Before a session: a quiet-machine gate, a CPU clock check (the laptop
  firmware can cap the clock), a check that measurement code is committed, and a pinned load-generator
  build. During a session: per-run gates on request counts, errors, sampling gaps, delta agreement
  and host saturation; a failed leg is retried once.
- **Pre-registers every experiment.** Each experiment's design, numeric predictions and decision
  rules are written in `TROUBLESHOOTING_RUNBOOK.md` before any of its data exists. The session
  driver refuses to run an experiment whose pre-registration is not committed.

## Experiments

All use a closed loop: `c` requests in flight (c = 1, 2, 4, 8), each sent as soon as the previous
reply arrives, 3000 requests per run, 5 usable runs per leg (OpenWhisk 6 with the first discarded).

| Id | Question | Function (`workloads/`) | Runbook |
|---|---|---|---|
| Main corpus | Control-plane CPU and energy per request under steady load | spin the CPU for 5 ms (root handlers) | §24–§27 |
| W1 | Does the cost grow with request size? (1 KiB, 64 KiB, 512 KiB) | `payload` (echo the body) | §28 |
| Log store | What does OpenWhisk standalone's log collector cost? | spin 5 ms; `SAQEF_OW_LOGSTORE` | §29 |
| W2 | A memory-heavy function (cache-resident vs DRAM) | `mem_cache`, `mem_dram` | §30 |
| W3 | Bursts of requests | spin 5 ms; `SAQEF_BURST` | §31 |
| W4 | Cold start: a burst reaching a platform with no containers | spin 5 ms | §32 |
| W5 | Functions that wait: CPU, sleep, disk write + fsync, mixed | `io_spin`, `io_sleep`, `io_disk`, `io_mixed` | §36 |
| W6 | Failures, overload (c = 64), half load, mixed traffic; OpenWhisk with a larger container pool | `w6_base`, `w6_fail5`, `w6_fail20`, `w6_alt` | §38 |
| Offline | Analyses of stored data: latency overhead, watts per busy core, energy split by CPU role | none | §34, §39 |

Each workload folder mirrors the four handler paths and has its own README; `tools/workload.sh`
swaps it in and restores the CPU-bound handlers afterwards.

## Repository layout

| Path | Contents |
|---|---|
| `saqef` | Command-line entry point: `check`, `deploy`, `teardown`, `scale`, `verify`, `run`, `gates`, `regression` |
| `saqef_harness.py` | Measurement harness: sampler, container classification, delta check, RAPL, gates, run records |
| `platforms/` | One adapter per platform (`openfaas.py`, `fn.py`, `knative.py`, `openwhisk.py`) on a shared `base.py` |
| `hello/`, `OF_FUNCTION/`, `KNATIVE_FUNCTION/`, `OW_FUNCTION/` | The deployed function per platform (Fn, OpenFaaS, Knative, OpenWhisk). The CPU-bound handler lives here; workloads are swapped in over it |
| `workloads/` | Handlers for W1, W2, W5 and W6, one folder per arm |
| `OPENFAAS_DEPLOY/` | OpenFaaS Swarm stack (docker-compose, Prometheus) |
| `run_saqef.sh`, `run_openfaas.sh` | Fn and OpenFaaS stack runners the adapters call |
| `metrics/cpubound.json` | Metric recipe the CLI loads |
| `tools/` | Session drivers and analysis tools (below) |
| `tests/` | Unit tests |
| `setup/` | Host provisioning (`setup_baremetal.sh`) and CPU pinning (`pin_cpuset.sh`) |
| `docs/` | `OPENFAAS_SETUP.md` (the OpenFaaS Swarm image set), `MEASUREMENT_RULES.md` (rules for running and interpreting sessions) |
| `TROUBLESHOOTING_RUNBOOK.md` | Bug ledger, every pre-registration and every outcome. Start at its symptom index |
| `NEXT_STEPS.md` | What is still to run or do |

### tools/

| Script | Role |
|---|---|
| `go.sh` | One command per measurement session: `sudo bash tools/go.sh [--workload X \| --arm Y]`; progress `--status`, stop `--stop` |
| `run_final.sh` | Session driver go.sh starts: pre-flight checks, legs in order, retries; `--dry-run` prints the plan |
| `run_lock_session.sh` | One quiet-gated leg: deploy, verify, idle probe, runs, gates, teardown |
| `run_tier1_conc.sh` | Earlier concurrency driver; its gate code is still used and tested |
| `workload.sh` | Swap a workload's handlers in or out, rebuild images, probe each platform before a session |
| `pool.sh` | Count, reset or log a platform's function containers (W4, W6) |
| `jvm_thread_sampler.py` | OpenWhisk JVM CPU per thread during a run |
| `plain_docker.py` | The same function in plain Docker, with no platform (W5 reference) |
| `screen_status.sh` | Progress on the console during a session |
| `cp_anatomy.py` | Control-plane CPU by component, per leg |
| `idle_crosscheck.py` | Energy per request on both idle bases (leg probe, session calibration) |
| `untracked_dyn.py` | Host CPU outside all containers, idle floor subtracted |
| `cold_analysis.py`, `io_analysis.py`, `w6_analysis.py` | Per-experiment analysis for W4, W5 and W6 |
| `fn_pool_growth.py`, `latency_overhead.py`, `energy_linearity.py`, `energy_split.py` | Offline analyses of stored data |
| `ow_host_attrib_ab.py` | Attributes OpenWhisk host-side CPU to a process (runbook §24.2) |
| `legacy_reattribute.py` | Re-attributes early runs from their raw samples (runbook §23) |

## Requirements

- Linux on bare metal, an Intel CPU with readable RAPL (`/sys/class/powercap/intel-rapl:0`), root
  for measurement sessions. The study used one laptop with 8 logical CPUs (Intel Core i5-1145G7).
- Docker; k3s with Knative Serving and Kourier for Knative; the Fn CLI; `faas-cli`; the OpenWhisk
  standalone image; the `hey` load generator (the session driver checks its build by hash).
- Python 3 with NumPy.

`sudo bash setup/setup_baremetal.sh` installs Docker, Go, the Fn CLI, `faas-cli` and `hey`, and makes
RAPL readable by the harness user. `docs/OPENFAAS_SETUP.md` explains the OpenFaaS image set.

## Usage

```bash
# unit tests (no platform needed)
python3 -m unittest discover -s tests -q

# what a session would run, and whether the machine passes the pre-flight
sudo bash tools/run_final.sh --dry-run

# one measured session (the main corpus); W1-W6 take --workload / --arm, see tools/go.sh
sudo bash tools/go.sh
sudo bash tools/go.sh --status

# single platform by hand
python3 saqef check
python3 saqef deploy --platform fn
python3 saqef run --platform fn --concurrency 4
python3 saqef teardown --platform fn
```

A session takes the machine over: it waits until interactive applications are closed, switches to
text mode, runs every leg, and brings the desktop back when it finishes. Results go to `results/`
(git-ignored), one folder per leg, with a `summary.json` per run, the raw samples and the leg's
`acceptance.json` (which runs are usable).

## Results and paper

The result corpus, the generated results file and the paper are kept in a separate repository and
will be released with the published paper; they are available from the author on request. Commit
IDs quoted in the runbook refer to the development history in which each session ran.

## License

[CC BY-NC 4.0](LICENSE).
