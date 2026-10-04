"""W4 cold start analysis (runbook §32): C1-C5 per platform block, and Fn's W3 B1/B2/B4.

Implements §32's metrics as registered, over each leg's usable runs (acceptance.json):
  first burst = hey.csv `burst` 0, later bursts = 1..; availability = 2xx / attempted
  (attempted per burst = the burst size: hey drops failed transports from its CSV, §31.6);
  p50 per burst group per run, then the median over runs;
  containers created (cold run) = pool.at_end - pool.at_start;
  cp / function CPU-s per run, idle-subtracted = CPU-s - leg probe rate x host window.

  C1  first-burst availability, cold: Fn < 0.99; Knative, OpenWhisk >= 0.999
  C2  first-burst p50 cold / warm >= 2 on Knative and OpenWhisk (Fn reported)
  C3  (cold cp CPU-s - median warm cp CPU-s) / containers created > 0, ms per container;
      C3b OpenWhisk's is the highest
  C3u (descriptive) the same difference for untracked host CPU (host - cp - fn, idle-subtracted),
      with the instrument's own CPU difference (instrument_cpu_s) beside it
  C4  later-burst p50, cold vs warm, within +-15 %
  C5  (cold fn CPU-s - median warm fn CPU-s) / containers created, s per container (descriptive)
  Fn  B1 b500 availability >= 0.999; B2 b500 drain in [0.67, 1.5] x 500 / steady rps;
      B4 b500 p99 >= 5 x steady p99; B0 steady availability 1.0

Usage: python3 tools/cold_analysis.py [--prefix cold_] [--res DIR] [--json OUT]
A later part (cold_p2_) is read with its own --prefix; blocks are never mixed across parts.
"""

import argparse
import csv
import glob
import json
import os
import statistics

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LONG = {"fn": "fn", "kn": "knative", "ow": "openwhisk"}


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def leg(res, prefix, p, arm):
    """Usable runs of one leg with the per-run quantities §32 needs, or None if absent."""
    d = os.path.join(res, "%s_cpubound_lock_%s%s_%s" % (LONG[p], prefix, p, arm))
    acc_f = os.path.join(d, "acceptance.json")
    if not os.path.exists(acc_f):
        return None
    acc = json.load(open(acc_f))
    probe = glob.glob(os.path.join(res, "idle_probe_%s" % acc["stamp"], "*", "summary.json"))
    pr = json.load(open(probe[0])) if probe else None
    p_cp = pr["cpu_sec"]["control_plane"] / pr["wall_s"] if pr else None
    p_fn = pr["cpu_sec"]["function"] / pr["wall_s"] if pr else None
    p_un = ((pr["host_cpu_sec"] - pr["cpu_sec"]["control_plane"] - pr["cpu_sec"]["function"])
            / pr["wall_s"]) if pr else None
    runs = []
    for name in acc["usable_runs"]:
        s = json.load(open(os.path.join(d, name, "summary.json")))
        size = (s.get("env") or {}).get("burst_size")
        by = {}
        for r in csv.DictReader(open(os.path.join(d, name, "hey.csv"))):
            b = int(r["burst"]) if r.get("burst") not in (None, "") else 0
            by.setdefault(b, []).append(r)
        first, later = by.get(0, []), [r for b, rs in by.items() if b > 0 for r in rs]
        ok = lambda rs: [float(r["response-time"]) * 1000 for r in rs if r["status-code"].startswith("2")]
        n_later_bursts = max(len(by) - 1, 1)
        w = s["host_window_s"]
        pool = s.get("pool") or {}
        runs.append({
            "run": name,
            "first_avail": (len(ok(first)) / size) if size else None,
            "first_p50": med(ok(first)),
            "later_avail": (len(ok(later)) / (size * n_later_bursts)) if size else None,
            "later_p50": med(ok(later)),
            "avail": s.get("availability"),
            "p99": s["latency_ms"]["p99"],
            "rps": s.get("throughput_rps"),
            "drain": (s.get("burst") or {}).get("drain_s_median"),
            "cp_s": s["cpu_sec"]["control_plane"] - (p_cp * w if p_cp is not None else 0),
            "fn_s": s["cpu_sec"]["function"] - (p_fn * w if p_fn is not None else 0),
            "untr_s": (s["host_cpu_sec"] - s["cpu_sec"]["control_plane"] - s["cpu_sec"]["function"]
                       - (p_un * w if p_un is not None else 0)) if s.get("host_cpu_sec") is not None else None,
            "instr_s": (sum((s.get("instrument_cpu_s") or {}).values())
                        if s.get("instrument_cpu_s") else None),
            "created": (pool["at_end"] - pool["at_start"])
                       if pool.get("at_end") is not None and pool.get("at_start") is not None else None,
            "pool_at_start": pool.get("at_start"),
            "reset_s": pool.get("reset_s"),
        })
    return {"stamp": acc["stamp"], "gates_ok": acc.get("leg_gates_ok"), "n": len(runs),
            "probe": bool(pr), "runs": runs}


def block(res, prefix, p):
    cold, warm = leg(res, prefix, p, "cold"), leg(res, prefix, p, "warm")
    out = {"platform": LONG[p], "prefix": prefix, "cold": cold and cold["stamp"], "warm": warm and warm["stamp"],
           "n_cold": cold and cold["n"], "n_warm": warm and warm["n"]}
    if not cold or not warm or not cold["n"] or not warm["n"]:
        out["evaluable"] = False
        return out
    out["evaluable"] = True
    C, W = cold["runs"], warm["runs"]
    g = lambda rs, k: med(r[k] for r in rs)
    warm_cp, warm_fn, warm_un, warm_in = g(W, "cp_s"), g(W, "fn_s"), g(W, "untr_s"), g(W, "instr_s")
    per = lambda key, base: (None if base is None else
                             med((r[key] - base) / r["created"] for r in C if r["created"] and r[key] is not None))
    out.update({
        "cold_first_avail": g(C, "first_avail"), "warm_first_avail": g(W, "first_avail"),
        "cold_first_p50_ms": g(C, "first_p50"), "warm_first_p50_ms": g(W, "first_p50"),
        "cold_later_p50_ms": g(C, "later_p50"), "warm_later_p50_ms": g(W, "later_p50"),
        "containers_created": g(C, "created"), "reset_s": g(C, "reset_s"),
        "cp_ms_per_container": (lambda v: v * 1000 if v is not None else None)(per("cp_s", warm_cp)),
        "fn_s_per_container": per("fn_s", warm_fn),
        "untracked_ms_per_container": (lambda v: v * 1000 if v is not None else None)(per("untr_s", warm_un)),
        "instrument_ms_per_container": (lambda v: v * 1000 if v is not None else None)(per("instr_s", warm_in)),
    })
    out["first_p50_ratio"] = (out["cold_first_p50_ms"] / out["warm_first_p50_ms"]
                              if out["warm_first_p50_ms"] else None)
    out["later_p50_rel"] = (out["cold_later_p50_ms"] / out["warm_later_p50_ms"] - 1
                            if out["warm_later_p50_ms"] else None)
    a = out["cold_first_avail"]
    out["C1"] = None if a is None else ((a < 0.99) if p == "fn" else (a >= 0.999))
    out["C2"] = None if p == "fn" or out["first_p50_ratio"] is None else out["first_p50_ratio"] >= 2
    out["C3"] = None if out["cp_ms_per_container"] is None else out["cp_ms_per_container"] > 0
    out["C4"] = None if out["later_p50_rel"] is None else abs(out["later_p50_rel"]) <= 0.15
    return out


def fn_w3(res, prefix):
    b5, st = leg(res, prefix, "fn", "b500"), leg(res, prefix, "fn", "steady")
    out = {"b500": b5 and b5["stamp"], "steady": st and st["stamp"]}
    if not b5 or not st or not b5["n"] or not st["n"]:
        out["evaluable"] = False
        return out
    g = lambda L, k: med(r[k] for r in L["runs"])
    rps, p99s = g(st, "rps"), g(st, "p99")
    out.update({"evaluable": True, "b500_avail": g(b5, "avail"), "b500_avail_worst": min(r["avail"] for r in b5["runs"]),
                "b500_drain_s": g(b5, "drain"), "steady_rps": rps, "drain_ratio": g(b5, "drain") / (500.0 / rps),
                "b500_p99_ms": g(b5, "p99"), "steady_p99_ms": p99s, "p99_ratio": g(b5, "p99") / p99s,
                "steady_avail": g(st, "avail")})
    out["B0"] = out["steady_avail"] == 1.0
    out["B1"] = out["b500_avail"] >= 0.999
    out["B2"] = 0.67 <= out["drain_ratio"] <= 1.5
    out["B4"] = out["p99_ratio"] >= 5
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prefix", default="cold_")
    ap.add_argument("--res", default=os.path.join(REPO, "results"))
    ap.add_argument("--json")
    a = ap.parse_args()
    blocks = [block(a.res, a.prefix, p) for p in ("fn", "ow", "kn")]
    ev = [b for b in blocks if b["evaluable"] and b.get("cp_ms_per_container") is not None]
    if len(ev) == 3:
        top = max(ev, key=lambda b: b["cp_ms_per_container"])["platform"]
        for b in blocks:
            b["C3b_highest"] = top
    out = {"prefix": a.prefix, "blocks": blocks, "fn_w3": fn_w3(a.res, a.prefix)}
    print(json.dumps(out, indent=1, default=str))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(out, f, indent=1, default=str)


if __name__ == "__main__":
    main()
