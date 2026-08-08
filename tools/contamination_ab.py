#!/usr/bin/env python3
"""Contamination A/B: how much does an 'opencode-style' background load move
the headline metric on THIS box?

Runbook §1 documents the 2026-08-07 incident: opencode at ~276% CPU (~2.8
cores), 1.1 GB RSS, 532 min CPU drifted Fn's cp_dynamic_share_pct ~0.3-1 pp
and directly corrupted host_saturation_pct / QoS. That magnitude has never
been MEASURED as a controlled experiment -- this tool does it:

  clean leg  -> bench with the quiet gate active (refuses if the box is not
                quiet, so the clean leg self-certifies)
  dirty leg  -> same bench with an emulated agent signature running
                (--cores busy spinners pinned to distinct cores + --mem-gb
                RSS), quiet gate disabled
  verdict    -> measured delta on cp_dynamic_share_pct, host_saturation_pct,
                p50/p99, throughput; written to contamination_ab.json

Run from a BARE bash shell with opencode/agents quit (the clean leg enforces
this). REPEAT < 5 writes to a `_quick` outdir (never publish as headline);
bump --repeat 5 if this becomes a §7 methodology figure.

Usage:
  python3 tools/contamination_ab.py --platform fn
  python3 tools/contamination_ab.py --platform openfaas --repeat 5
"""

import argparse
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAQEF = os.path.join(REPO, "saqef")


def read_summary(path):
    with open(path) as f:
        return json.load(f)


def host_busy_total():
    try:
        with open("/proc/stat") as f:
            lines = f.readlines()
        parts = lines[0].split()
        vals = [int(v) for v in parts[1:9]]
        return (sum(vals) - vals[3] - vals[4], sum(vals))
    except Exception:
        return None


def measure_busy_pct(window_s=10):
    t0 = host_busy_total()
    time.sleep(window_s)
    t1 = host_busy_total()
    if not t0 or not t1:
        return None
    d_b = t1[0] - t0[0]
    d_t = t1[1] - t0[1]
    return d_b / d_t * 100.0 if d_t > 0 else None


def spawn_agent_load(cores, mem_gb):
    """Emulate the documented opencode signature: `cores` busy spinners pinned
    to distinct physical cores + `mem_gb` GB resident. Returns a list of PIDs."""
    procs = []
    try:
        for i in range(cores):
            spinner = subprocess.Popen(
                [sys.executable, "-c", "while True: pass"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            procs.append(spinner)
            subprocess.run(["taskset", "-pc", str(i), str(spinner.pid)],
                           capture_output=True)
        memproc = subprocess.Popen(
            [sys.executable, "-c",
             "import sys; x = bytearray(%d * 2**30); print(len(x), flush=True); "
             "import time; time.sleep(1e9)" % mem_gb],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(memproc)
    except Exception as e:
        for p in procs:
            p.kill()
        raise
    return procs


def kill_procs(procs):
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass
    for p in procs:
        try:
            p.wait(timeout=5)
        except Exception:
            pass


def run_bench(args, out, quiet_gate):
    cmd = [sys.executable, SAQEF, "run", "--platform", args.platform,
           "--metric", "cpubound", "--total", str(args.total),
           "--concurrency", str(args.concurrency), "--duration", str(args.duration),
           "--warmup", str(args.warmup), "--repeat", str(args.repeat),
           "--idle-w", str(args.idle_w), "--out", out]
    if not quiet_gate:
        cmd.append("--no-quiet-gate")
    if args.concurrency > args.total:
        raise SystemExit("--concurrency must be <= --total")
    print(">>>", " ".join(cmd))
    r = subprocess.run(cmd, cwd=REPO)
    if r.returncode != 0:
        raise SystemExit("bench failed (rc=%d): %s" % (r.returncode, " ".join(cmd)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--platform", required=True, choices=["fn", "openfaas"])
    ap.add_argument("--outdir", default=None,
                    help="base outdir (default: results/<platform>_contamination_ab); "
                         "saqef's _quick suffix is appended when --repeat < 5")
    ap.add_argument("--total", type=int, default=3000)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--duration", type=int, default=120)
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--idle-w", type=float, default=4.3)
    ap.add_argument("--cores", type=int, default=3, help="busy spinners (default 3 ~ 2.8-core opencode)")
    ap.add_argument("--mem-gb", type=float, default=1.1, help="emulated agent RSS in GB")
    args = ap.parse_args()

    outdir = args.outdir or os.path.join("results", "%s_contamination_ab" % args.platform)
    resolved = outdir + "_quick" if args.repeat < 5 else outdir  # same _quick rule as saqef
    clean_dir = os.path.join(resolved, "clean")
    dirty_dir = os.path.join(resolved, "dirty")

    print("=== CLEAN LEG (quiet gate ACTIVE -- box must be quiet) ===")
    run_bench(args, clean_dir, quiet_gate=True)

    print("=== DIRTY LEG (emulated agent load: %d cores busy + %.1f GB RSS) ===" % (args.cores, args.mem_gb))
    procs = spawn_agent_load(args.cores, args.mem_gb)
    try:
        achieved = measure_busy_pct(window_s=10)
        print("[ab] achieved aggregate host busy: %.1f%% (target: ~%.1f%%)"
              % (achieved or 0.0, args.cores * 100.0 / 8.0))
        run_bench(args, dirty_dir, quiet_gate=False)
    finally:
        kill_procs(procs)

    c = read_summary(os.path.join(clean_dir, "summary.json"))
    d = read_summary(os.path.join(dirty_dir, "summary.json"))

    def pick(s, k, default=float("nan")):
        v = s.get(k)
        return v if v is not None else default

    row = ["metric", "clean", "dirty", "delta"]
    rows = [
        ["cp_dynamic_share_pct", c["cp_dynamic_share_pct"], d["cp_dynamic_share_pct"]],
        ["host_saturation_pct", pick(c, "host_saturation_pct"), pick(d, "host_saturation_pct")],
        ["latency p50 ms", pick(c, "latency_ms", {}).get("p50") if isinstance(c.get("latency_ms"), dict) else None,
         pick(d, "latency_ms", {}).get("p50") if isinstance(d.get("latency_ms"), dict) else None],
        ["latency p99 ms", pick(c, "latency_ms", {}).get("p99") if isinstance(c.get("latency_ms"), dict) else None,
         pick(d, "latency_ms", {}).get("p99") if isinstance(d.get("latency_ms"), dict) else None],
        ["throughput_rps", pick(c, "throughput_rps"), pick(d, "throughput_rps")],
    ]
    print("\n=== CONTAMINATION A/B VERDICT ===")
    fmt = "%-24s %-10s %-10s %s"
    print(fmt % (row[0], row[1], row[2], row[3]))
    report = {}
    for name, cv, dv in rows:
        if cv is None or dv is None or cv != cv or dv != dv:
            print(fmt % (name, "-", "-", "-"))
            report[name] = None
            continue
        delta = dv - cv
        print(fmt % (name, round(cv, 3), round(dv, 3), ("%+.3f" % delta)))
        report[name] = {"clean": cv, "dirty": dv, "delta": delta}
    report["emulated_load"] = {"cores": args.cores, "mem_gb": args.mem_gb}
    report["results_dir"] = resolved
    with open(os.path.join(resolved, "contamination_ab.json"), "w") as f:
        json.dump(report, f, indent=2)
    print("\nSaved report -> %s/contamination_ab.json" % resolved)
    print("\nInterpretation: this is the measured upper bound of 'opencode-style' "
          "background-load contamination for %s on THIS box. It is a methodology "
          "figure for §7, not a headline number (REPEAT<5 quick outdir)." % args.platform)


if __name__ == "__main__":
    main()
