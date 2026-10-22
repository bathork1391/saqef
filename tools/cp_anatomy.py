#!/usr/bin/env python3
"""Control-plane anatomy (runbook 26 step 3): where each platform's cp CPU goes.

Offline, from data on disk; measures nothing. Two granularities, as 25.5 requires:
  * inter-container (all platforms): CPU-s per component container over each usable
    run's attribution window, from samples_raw.csv cumulative counters, per invocation
  * intra-process (OpenWhisk): CPU-s per JVM thread subsystem over the same windows,
    from results/final_session/jvm_threads_<stamp>.csv (5 s snapshots, interpolated)

The JVM map is reported twice: the PRE-REGISTERED map (25.5) and, labelled post hoc,
an OpenJ9 map, because the JVM turned out to be OpenJ9 and the 25.5 patterns are
HotSpot names.

    python3 tools/cp_anatomy.py [--prefix final_] [--json out.json]
    python3 tools/cp_anatomy.py --prefix payload_ --legs '*' --jvm-dir payload_session

Runs are the leg's acceptance.json "usable_runs" when it exists (runbook 28.7), else every
run after the warm-up discard.
"""
import argparse
import bisect
import collections
import csv
import fnmatch
import glob
import json
import os
import re
import statistics

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(REPO, "results")

# Container name -> component. Order matters: first match wins.
COMPONENTS = [
    ("openfaas", r"^openfaas_gateway\.", "gateway"),
    ("openfaas", r"^openfaas_faas-swarm\.", "provider (faas-swarm)"),
    ("openfaas", r"^openfaas_(nats|queue-worker)\.", "async (nats + queue-worker)"),
    ("openfaas", r"^openfaas_(prometheus|alertmanager)\.", "metrics (prometheus + alertmanager)"),
    ("fn", r"^fnserver$", "fnserver"),
    ("knative", r"^k8s_activator_", "activator"),
    ("knative", r"^k8s_kourier-gateway_", "kourier gateway (envoy)"),
    ("knative", r"^k8s_lb-tcp-", "svclb (klipper LB)"),
    ("knative", r"^k8s_autoscaler_", "autoscaler"),
    ("knative", r"^k8s_controller_", "controllers (serving + net-kourier)"),
    ("knative", r"^k8s_webhook_", "webhook"),
    ("knative", r"^k8s_queue-proxy_", "queue-proxy [counted as fn]"),
    ("openwhisk", r"^openwhisk$", "standalone JVM"),
]

# 25.5, verbatim (HotSpot thread names), first match wins.
PREREG = [
    ("JIT", ["C1 CompilerThre*", "C2 CompilerThre*"]),
    ("GC / VM", ["GC Thread*", "G1 *", "VM Thread", "VM Periodic*"]),
    ("actor system", ["*akka*", "*dispatcher*"]),
    ("thread pools", ["pool-*", "ForkJoinPool*"]),
    ("docker CLI children", ["<children>"]),
]
# Post hoc: the same subsystems under OpenJ9 names. "standalone-acto" is the
# 15-char truncation of the standalone actor system's dispatcher threads.
OPENJ9 = [
    ("JIT", ["JIT Compilation*", "JIT Sampler*", "JIT IProfiler*"]),
    ("GC / VM", ["GC Worker*", "Concurrent Mark*", "Finalizer*", "VM Runtime Stat*",
                 "Common-Cleaner*", "Signal Reporter*", "Attach API*", "DestroyJavaVM*",
                 "MemoryMXBean*"]),
    ("actor system", ["standalone-acto*", "*akka*", "*dispatcher*"]),
    ("thread pools", ["pool-*", "ForkJoinPool*", "process reaper*", "Thread-*", "Keep-Alive*"]),
    ("telemetry (kamon / prometheus)", ["kamon-*", "prometheus*", "JVM Metrics*",
                                        "Process Metrics*", "hiccup-monitor*"]),
    ("docker CLI children", ["<children>"]),
]


def component(plat, name):
    for p, rx, comp in COMPONENTS:
        if p == plat and re.search(rx, name):
            return comp
    return None


def windows(outdir, disc):
    runs = json.load(open(os.path.join(outdir, "runs.json")))
    dirs = sorted(glob.glob(os.path.join(outdir, "run_*")),
                  key=lambda p: int(p.rsplit("_", 1)[1]))
    pairs = list(zip(dirs, runs))
    acc = os.path.join(outdir, "acceptance.json")
    if os.path.exists(acc):
        usable = set(json.load(open(acc))["usable_runs"])
        return [(d, r) for d, r in pairs if os.path.basename(d) in usable]
    return pairs[disc:]


def container_cpu(run_dir, t0, t1):
    """CPU-s per container over [t0, t1] from cumulative cgroup counters."""
    first, last, born = {}, {}, {}
    with open(os.path.join(run_dir, "samples_raw.csv")) as f:
        for row in csv.DictReader(f):
            if row["mode"] != "cum" or not row["cpu_cum_s"]:
                continue
            t, c, v = float(row["t"]), row["container"], float(row["cpu_cum_s"])
            if t < t0 or t > t1:
                continue
            first.setdefault(c, v)
            last[c] = v
            born[c] = float(row["born_epoch"] or 0)
    # A container born inside the window burned all of its CPU in it (runbook 20).
    return {c: last[c] - (0.0 if born[c] >= t0 else first[c]) for c in last}


def jvm_cpu(csv_path, t0, t1):
    """CPU-s per thread name over [t0, t1], linear interpolation between snapshots."""
    series = collections.defaultdict(list)  # (tid, comm) -> [(t, cpu)]
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            series[(row["tid"], row["comm"])].append(
                (float(row["ts_unix"]), float(row["utime_s"]) + float(row["stime_s"])))

    def at(pts, t):
        ts = [p[0] for p in pts]
        i = bisect.bisect_left(ts, t)
        if i == 0:
            return 0.0 if t < ts[0] else pts[0][1]   # thread not born yet -> 0
        if i == len(pts):
            return pts[-1][1]
        (ta, va), (tb, vb) = pts[i - 1], pts[i]
        return va + (vb - va) * (t - ta) / (tb - ta)

    out = collections.Counter()
    for (tid, comm), pts in series.items():
        out[comm] += at(pts, t1) - at(pts, t0)
    return out


def classify(counter, mapping):
    out = collections.Counter()
    for comm, v in counter.items():
        for group, pats in mapping:
            if any(fnmatch.fnmatch(comm, p) for p in pats):
                out[group] += v
                break
        else:
            out["other"] += v
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prefix", default="final_")
    ap.add_argument("--legs", default="tier1*", help="stamp pattern after the prefix")
    ap.add_argument("--jvm-dir", default="final_session", help="results/ subdir with jvm_threads_<stamp>.csv")
    ap.add_argument("--json")
    a = ap.parse_args()
    result = []
    for f in sorted(glob.glob(os.path.join(RES, "lock_session_%s%s" % (a.prefix, a.legs), "lock_summary.json"))):
        d = json.load(open(f))
        stamp, disc = d["session"]["stamp"], int(d["session"]["discard_warmup"])
        for plat, v in d["platforms"].items():
            outdir = os.path.join(REPO, v["outdir"])
            comp_ms = collections.defaultdict(list)
            jvm_pre, jvm_j9, jvm_tot, cp_rec, ow_cont = [], [], [], [], []
            fn_rec, untracked = [], []
            jcsv = os.path.join(RES, a.jvm_dir, "jvm_threads_%s.csv" % stamp)
            for run_dir, r in windows(outdir, disc):
                at = r["attribution"]
                t0, t1, ok = at["window_start_epoch"], at["window_end_epoch"], r["successes"]
                per = collections.Counter()
                for c, cpu in container_cpu(run_dir, t0, t1).items():
                    comp = component(plat, c)
                    if comp:
                        per[comp] += cpu
                for comp in {x[2] for x in COMPONENTS if x[0] == plat}:
                    comp_ms[comp].append(per[comp] / ok * 1000)
                cp_rec.append(r["cpu_sec"]["control_plane"] / ok * 1000)
                fn_rec.append(r["cpu_sec"]["function"] / ok * 1000)
                # Host CPU no container accounts for (kernel networking etc., P5 in 28.3).
                if r.get("host_cpu_sec") is not None:
                    untracked.append((r["host_cpu_sec"] - r["cpu_sec"]["control_plane"]
                                      - r["cpu_sec"]["function"]) / ok * 1000)
                if plat == "openwhisk" and os.path.exists(jcsv):
                    threads = jvm_cpu(jcsv, t0, t1)
                    jvm_pre.append({k: x / ok * 1000 for k, x in classify(threads, PREREG).items()})
                    jvm_j9.append({k: x / ok * 1000 for k, x in classify(threads, OPENJ9).items()})
                    jvm_tot.append(sum(threads.values()) / ok * 1000)
                    ow_cont.append(per["standalone JVM"] / ok * 1000)
            if not cp_rec:
                result.append({"stamp": stamp, "platform": plat, "usable_runs": 0})
                continue
            row = {"stamp": stamp, "platform": plat, "usable_runs": len(cp_rec),
                   "gates_ok": v.get("gates_ok"),
                   "cp_ms_inv_recorded": round(statistics.median(cp_rec), 3),
                   "fn_ms_inv": round(statistics.median(fn_rec), 3),
                   "untracked_host_ms_inv": round(statistics.median(untracked), 3) if untracked else None,
                   "components_ms_inv": {k: round(statistics.median(x), 3)
                                         for k, x in sorted(comp_ms.items(), key=lambda kv: -statistics.median(kv[1]))}}
            if jvm_tot:
                med = lambda rows: {k: round(statistics.median(x.get(k, 0.0) for x in rows), 3)
                                    for k in sorted({k for x in rows for k in x})}
                row["jvm_ms_inv_total"] = round(statistics.median(jvm_tot), 3)
                row["jvm_vs_container_pct"] = round(statistics.median(
                    j / c * 100 for j, c in zip(jvm_tot, ow_cont)), 1)
                row["jvm_prereg_ms_inv"] = med(jvm_pre)
                row["jvm_openj9_ms_inv"] = med(jvm_j9)
            result.append(row)

    for row in result:
        if not row["usable_runs"]:
            print("\n== %s  (%s, 0 usable runs)" % (row["stamp"], row["platform"]))
            continue
        print("\n== %s  (%s, %d usable runs, gates_ok=%s)  cp recorded %.3f  fn %.3f  untracked host %s ms/inv" % (
            row["stamp"], row["platform"], row["usable_runs"], row["gates_ok"], row["cp_ms_inv_recorded"],
            row["fn_ms_inv"], row["untracked_host_ms_inv"]))
        for k, x in row["components_ms_inv"].items():
            print("   %-40s %8.3f ms/inv" % (k, x))
        if "jvm_ms_inv_total" in row:
            print("   JVM threads + children: %.3f ms/inv = %.1f%% of the openwhisk container"
                  % (row["jvm_ms_inv_total"], row["jvm_vs_container_pct"]))
            for label, key in (("pre-registered map (25.5)", "jvm_prereg_ms_inv"),
                               ("OpenJ9 map (post hoc)", "jvm_openj9_ms_inv")):
                tot = sum(row[key].values())
                print("   -- %s" % label)
                for k, x in sorted(row[key].items(), key=lambda kv: -kv[1]):
                    print("      %-36s %8.3f ms/inv  %5.1f%%" % (k, x, x / tot * 100))
    if a.json:
        json.dump(result, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
