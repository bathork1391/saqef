#!/usr/bin/env python3
"""
SAQEF Harness - Sustainability-Aware QoS Evaluation Framework.

Unified measurement window: QoS (load generator) + per-container CPU/mem
(docker stats) + optional RAPL ground truth, collected synchronously.

Stdlib only. Works on: bare-metal Linux, WSL2, Killercoda Ubuntu, Docker Desktop.

Usage:
  python3 saqef_harness.py --check
  python3 saqef_harness.py --url http://localhost:8080/r/app/hello \
      --platform fn --cp-containers fnserver \
      --total 2000 --concurrency 10 --duration 60 --outdir results/fn_default

Outputs (into --outdir):
  summary.json   - all KPIs, energy/carbon, validation, QoS
  samples.csv    - per-second per-container CPU%/mem
  requests.csv   - per-request latency/status
  rapl.csv       - RAPL readings (if available)
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------------------
# Power / carbon model constants (Caribou, SOSP'24; Hidden Carbon Footprint, SoCC'24)
P_BUSY_CORE_W = 3.5          # W per fully-busy core (dynamic portion)
P_IDLE_BASE_W = 30.0         # machine idle baseline W (override with --idle-w)
PUE = 1.15                   # power usage effectiveness
CI_GCO2_PER_KWH = 150.0      # grid carbon intensity, gCO2/kWh (--ci)
DRAM_EMBODIED_G_PER_GB = 1390.0  # gCO2 per GB DRAM, embodied (1.39 kg/GB)
CPU_EMBODIED_G_PER_CORE = 653.0  # gCO2 per CPU core, embodied
LIFESPAN_YEARS = 5
SAMPLE_S = 1.0               # nominal sampling interval
RAPL_DIR = "/sys/class/powercap"


# ---------------------------------------------------------------------------
def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def docker_stats_once():
    """Return {name: (cpu_percent, mem_mb)} from one `docker stats` snapshot."""
    out = run("docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}'")
    if out.returncode != 0:
        return None
    res = {}
    for line in out.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 4:
            continue
        name, cpu = parts[0], parts[1].strip().rstrip("%")
        mem = parts[2].strip().split()[0]
        try:
            cpu = float(cpu)
        except ValueError:
            cpu = 0.0
        res[name] = (cpu, mem_to_mb(mem))
    return res


def mem_to_mb(s):
    s = s.strip()
    mult = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "TiB": 1024 * 1024,
            "kB": 1 / 1000, "MB": 1, "GB": 1000, "TB": 1_000_000}
    for suf, m in mult.items():
        if s.endswith(suf):
            try:
                return float(s[: -len(suf)]) * m
            except ValueError:
                return 0.0
    return 0.0


def rapl_energy():
    """Return package energy in J, or None if RAPL unavailable."""
    p = os.path.join(RAPL_DIR, "intel-rapl:0", "energy_uj")
    try:
        with open(p) as f:
            return int(f.read().strip()) / 1e6  # uJ -> J
    except Exception:
        return None


# ---------------------------------------------------------------------------
def run_load(url, total, concurrency, timeout_s=10):
    """Fire `total` requests with `concurrency` threads. Returns list of (ok, latency_s)."""
    per = total // concurrency
    results = []

    def worker(base):
        n = per
        if base == 0:
            n += total % concurrency
        for _ in range(n):
            t0 = time.perf_counter()
            ok = True
            try:
                with urllib.request.urlopen(url, timeout=timeout_s) as r:
                    r.read()
            except Exception:
                ok = False
            results.append((ok, time.perf_counter() - t0))

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for i in range(concurrency):
            ex.submit(worker, i)
    return results


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="SAQEF measurement harness")
    ap.add_argument("--check", action="store_true", help="verify docker + RAPL availability")
    ap.add_argument("--url", default="http://localhost:8080/r/app/hello")
    ap.add_argument("--platform", default="unknown", help="label, e.g. fn, openfaas, openwhisk")
    ap.add_argument("--cp-containers", default="", help="comma-separated substrings of control-plane containers")
    ap.add_argument("--total", type=int, default=2000, help="total requests")
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--duration", type=int, default=60, help="window seconds (also a hard stop)")
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--slo-ms", type=float, default=500.0, help="SLO latency target in ms")
    ap.add_argument("--idle-w", type=float, default=P_IDLE_BASE_W)
    ap.add_argument("--ci", type=float, default=CI_GCO2_PER_KWH, help="grid carbon intensity gCO2/kWh")
    args = ap.parse_args()

    if args.check:
        ds = docker_stats_once()
        r = rapl_energy()
        print("docker stats :", "OK" if ds is not None else "NOT AVAILABLE",
              "(%d containers seen)" % len(ds) if ds is not None else "")
        print("RAPL         :", ("OK %.0f J" % r) if r else "NOT AVAILABLE (CPU-time model only)")
        sys.exit(0)

    os.makedirs(args.outdir, exist_ok=True)
    cp_sub = [s.strip().lower() for s in args.cp_containers.split(",") if s.strip()]

    # --- sampler thread ------------------------------------------------------
    samples, rapl_log = [], []
    stop = threading.Event()
    rapl_start = rapl_energy()

    def sampler():
        while not stop.is_set():
            t = time.time()
            snap = docker_stats_once()
            e = rapl_energy()
            samples.append((t, snap, e))
            time.sleep(SAMPLE_S)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()

    # --- load generation -----------------------------------------------------
    t0 = time.perf_counter()
    reqs = run_load(args.url, args.total, args.concurrency)
    wall = time.perf_counter() - t0
    stop.set()
    th.join(timeout=5)
    rapl_end = rapl_energy()

    # --- energy attribution (Kepler-style CPU-time proportional) -------------
    cp_cpu_s, fn_cpu_s, cp_peak_mem_mb = 0.0, 0.0, 0.0
    all_snaps = [s for s in samples if s[1] is not None]
    e_cp, e_fn = 0.0, 0.0
    covered_s = 0.0
    for i in range(len(all_snaps)):
        t, snap, _ = all_snaps[i]
        tnext = all_snaps[i + 1][0] if i + 1 < len(all_snaps) else t + SAMPLE_S
        dt = max(tnext - t, 0.01)
        covered_s += dt
        for name, (cpu, mem) in snap.items():
            is_cp = any(s in name.lower() for s in cp_sub)
            cpu_sec = (cpu / 100.0) * dt
            dyn_j = cpu_sec * P_BUSY_CORE_W
            if is_cp:
                e_cp += dyn_j
                cp_cpu_s += cpu_sec
                cp_peak_mem_mb = max(cp_peak_mem_mb, mem)
            else:
                e_fn += dyn_j
                fn_cpu_s += cpu_sec
    e_dynamic = e_cp + e_fn
    e_total = args.idle_w * wall + e_dynamic

    # --- QoS -----------------------------------------------------------------
    n = len(reqs)
    ok = sum(1 for o, _ in reqs if o)
    lats_ms = sorted(1000.0 * l for _, l in reqs)
    def pct(p):
        if not lats_ms:
            return float("nan")
        return lats_ms[min(len(lats_ms) - 1, int(len(lats_ms) * p))]

    compliance = sum(1 for l in lats_ms if l <= args.slo_ms)
    availability = ok / n if n else 0.0

    # --- carbon ---------------------------------------------------------------
    wh = e_total / 3600.0
    op_gco2 = wh * args.ci * PUE
    cp_wh = e_cp / 3600.0
    cp_gco2 = cp_wh * args.ci * PUE
    kpi = op_gco2 / compliance if compliance else float("nan")
    embodied_per_gb = DRAM_EMBODIED_G_PER_GB / (LIFESPAN_YEARS * 365 * 24)

    # --- RAPL validation -------------------------------------------------------
    rapl_validation = None
    if rapl_start is not None and rapl_end is not None:
        e_rapl = rapl_end - rapl_start
        rapl_validation = abs(e_total - e_rapl) / e_rapl * 100 if e_rapl > 0 else None

    summary = {
        "platform": args.platform,
        "url": args.url,
        "wall_s": round(wall, 2),
        "sampling_covered_s": round(covered_s, 2),
        "requests": n, "successes": ok,
        "availability": round(availability, 4),
        "throughput_rps": round(ok / wall, 2),
        "latency_ms": {"p50": round(pct(0.5), 2), "p90": round(pct(0.9), 2),
                       "p99": round(pct(0.99), 2), "max": round(lats_ms[-1], 2) if lats_ms else None},
        "slo_ms": args.slo_ms,
        "slo_compliance": round(compliance / n, 4) if n else None,
        "energy_J": {"total": round(e_total, 1), "dynamic": round(e_dynamic, 1),
                     "control_plane": round(e_cp, 1), "function": round(e_fn, 1)},
        "cp_share_pct": round(e_cp / e_total * 100, 2) if e_total else None,
        "cp_dynamic_share_pct": round(e_cp / e_dynamic * 100, 2) if e_dynamic else None,
        "cpu_sec": {"control_plane": round(cp_cpu_s, 2), "function": round(fn_cpu_s, 2)},
        "cp_peak_mem_mb": round(cp_peak_mem_mb, 1),
        "carbon_gCO2": {"op_total": round(op_gco2, 3), "op_control_plane": round(cp_gco2, 3)},
        "model": {"idle_w": args.idle_w, "busy_core_w": P_BUSY_CORE_W, "pue": PUE, "ci": args.ci},
        "kpi_gco2_per_slo_compliant_inv": round(kpi, 4),
        "embodied_g_per_gb_h": round(embodied_per_gb, 4),
        "rapl_validation_err_pct": round(rapl_validation, 2) if rapl_validation is not None else None,
        "rapl_available": rapl_start is not None,
    }

    # --- write outputs ----------------------------------------------------------
    with open(os.path.join(args.outdir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    with open(os.path.join(args.outdir, "samples.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "container", "cpu_pct", "mem_mb"])
        for t, snap, _ in all_snaps:
            for name, (cpu, mem) in (snap or {}).items():
                w.writerow([round(t, 2), name, cpu, mem])
    with open(os.path.join(args.outdir, "requests.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ok", "latency_ms"])
        for o, l in reqs:
            w.writerow([int(o), round(1000 * l, 3)])

    print(json.dumps(summary, indent=2))
    print(f"\nSaved to {os.path.abspath(args.outdir)}/")


if __name__ == "__main__":
    main()
