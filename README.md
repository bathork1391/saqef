# SAQEF — Serverless Platform Overhead & Carbon Measurement

A cross-platform measurement framework for quantifying control-plane overhead and operational carbon in serverless (FaaS) platforms.

## Platforms

- **Fn Project** (fnproject/fn)
- **OpenFaaS** (openfaas/faas)
- **Knative Serving** (k8s + Kourier)
- **Apache OpenWhisk** (standalone)

## What it measures

SAQEF runs identical CPU-bound functions across platforms under controlled conditions and reports:

- `cp_dynamic_share_pct` — control-plane container CPU as a percentage of dynamic (load-created) CPU
- Per-invocation control-plane CPU cost (ms)
- Energy consumption via RAPL (Intel)
- Operational carbon (gCO₂)
- Quality-of-service (p50/p99 latency, SLO compliance)

## Structure

```
saqef                  CLI entrypoint
saqef_harness.py       Measurement harness
platforms/             Per-platform adapters (fn, openfaas, knative, openwhisk)
metrics/               Metric recipes (JSON configs)
tools/                 Experiment drivers, sweep scripts, result emission
figures/               Figure generation (matplotlib)
results/               Committed measurement data (gated, citable)
hello/                 Function handler (CPU-bound ~5 ms spin)
```

## Usage

```bash
# Deploy + verify + bench (single platform)
sudo bash run_saqef.sh all         # Fn
sudo bash run_openfaas.sh all      # OpenFaaS

# Unified CLI
sudo python3 saqef run --platform fn --total 10000 --concurrency 4 --repeat 5
sudo python3 saqef run --platform openfaas --total 10000 --concurrency 4 --repeat 5
sudo python3 saqef run --platform knative --total 10000 --concurrency 4 --repeat 5
sudo python3 saqef run --platform openwhisk --total 10000 --concurrency 4 --repeat 5

# Regression gate (catches measurement-path regressions)
sudo python3 saqef regression
```

## Requirements

- Linux (Ubuntu 22.04+), Docker, RAPL-readable Intel CPU
- For Knative: k3s + Knative Serving + Kourier
- For OpenWhisk: Docker (standalone mode)

## Data governance

- `VERIFIED_RESULTS.md` is the single authoritative reference for all verified results
- Results in `results/` are gated (delta-check, host-plausibility, ambient-load) before commitment
- See `AGENTS.md` for the full project state and session log

## License

This work is licensed under [CC BY-NC 4.0](LICENSE).

## Citation

If you use this framework, please cite the associated paper.
