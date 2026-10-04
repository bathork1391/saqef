"""Post hoc (runbook §34): does dynamic package power rise linearly with host load? Run level.

§25.6 pre-registered a WITHIN-run check (1 Hz power against host busy cores per interval). The
traces up to 2026-10-04 have no host CPU per interval, so that check cannot be done on them
(energy_trace.csv carries host_busy_ticks from 2026-10-05 on). This tool does the run-level
substitute, post hoc: per usable run,
    dynamic W   = e_rapl_j / wall_s - leg's idle-probe W (probe basis, §27.8)
    busy cores  = host_cpu_sec / host_window_s
then fits dynamic W = k * busy^alpha (log-log least squares) WITHIN each platform, across its
concurrency legs: the platform is held constant, so alpha is a load response, not a between-platform
correlation (review 2026-10-05). alpha = 1 is linear; alpha < 1 means each extra busy core adds
less power (shared power budget, lower clock, §25.1). A platform whose legs span < 1 busy core is
not fitted (no load range). The pooled fit over all runs is kept as a descriptive line only.

Usage: python3 tools/energy_linearity.py --res DIR --session final_ [--json OUT]
"""

import argparse
import glob
import json
import math
import os
import statistics

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def points(res, session):
    ic = {r["stamp"]: r for r in json.load(open(os.path.join(res, "%ssession" % session, "idle_crosscheck.json")))}
    out = []
    for d in sorted(glob.glob(os.path.join(res, "*_cpubound_lock_%s*" % session))):
        stamp = d.split("_lock_", 1)[1]
        if stamp not in ic:
            continue
        pw = ic[stamp]["probe_w"]
        acc = os.path.join(d, "acceptance.json")
        if os.path.exists(acc):
            usable = json.load(open(acc))["usable_runs"]
        else:   # Part A predates acceptance.json: usable = runs after the leg's discard_warmup
            disc = int(json.load(open(os.path.join(res, "lock_session_%s" % stamp, "lock_summary.json")))
                       ["session"]["discard_warmup"])
            n = len(glob.glob(os.path.join(d, "run_*")))
            usable = ["run_%d" % i for i in range(disc + 1, n + 1)]
        for r in usable:
            s = json.load(open(os.path.join(d, r, "summary.json")))
            e, w, h, hw = s.get("e_rapl_j"), s.get("wall_s"), s.get("host_cpu_sec"), s.get("host_window_s")
            if not (e and w and h and hw):
                continue
            dyn, busy = e / w - pw, h / hw
            out.append({"stamp": stamp, "run": r, "busy_cores": round(busy, 3), "dyn_w": round(dyn, 3),
                        "w_per_busy_core": round(dyn / busy, 3)})
    return out


PLATFORM = {"of": "openfaas", "fn": "fn", "kn": "knative"}


def platform(stamp):
    return PLATFORM.get(stamp.rsplit("_", 1)[-1], "openwhisk")


def fit(pts):
    xs = [math.log(p["busy_cores"]) for p in pts if p["dyn_w"] > 0]
    ys = [math.log(p["dyn_w"]) for p in pts if p["dyn_w"] > 0]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return {"alpha": round(sxy / sxx, 3), "r": round(sxy / math.sqrt(sxx * syy), 3), "n": len(xs)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--res", default=os.path.join(REPO, "results"))
    ap.add_argument("--session", default="final_")
    ap.add_argument("--json")
    a = ap.parse_args()
    pts = points(a.res, a.session)
    f = fit(pts)
    per = {}
    for pl in sorted({platform(p["stamp"]) for p in pts}):
        sel = [p for p in pts if platform(p["stamp"]) == pl]
        legs = {}
        for p in sel:
            legs.setdefault(p["stamp"], []).append(p)
        leg_rows = sorted(({"stamp": k, "busy_cores": round(statistics.median(q["busy_cores"] for q in v), 2),
                            "w_per_busy_core": round(statistics.median(q["w_per_busy_core"] for q in v), 2)}
                           for k, v in legs.items()), key=lambda r: r["busy_cores"])
        span = leg_rows[-1]["busy_cores"] - leg_rows[0]["busy_cores"]
        per[pl] = {"legs": leg_rows, "busy_span": round(span, 2),
                   "fit": fit(sel) if span >= 1.0 else None}
    bands = []
    for lo, hi in ((0, 3), (3, 5), (5, 9)):
        sel = [p for p in pts if lo <= p["busy_cores"] < hi]
        if sel:
            bands.append({"busy_cores": [lo, hi], "n": len(sel),
                          "w_per_busy_core_median": round(statistics.median(p["w_per_busy_core"] for p in sel), 2),
                          "w_per_busy_core_range": [min(p["w_per_busy_core"] for p in sel),
                                                    max(p["w_per_busy_core"] for p in sel)],
                          "dyn_w_median": round(statistics.median(p["dyn_w"] for p in sel), 1)})
    out = {"session": a.session, "per_platform": per, "pooled_fit_descriptive": f, "bands": bands, "points": pts}
    for pl, v in per.items():
        print("%-9s %s | %s" % (pl, ("alpha %.2f (r %.2f, n %d)" % (v["fit"]["alpha"], v["fit"]["r"], v["fit"]["n"]))
                                if v["fit"] else "not fitted (legs span %.2f busy cores)" % v["busy_span"],
                                "; ".join("%.2f cores %.2f W/core" % (r["busy_cores"], r["w_per_busy_core"]) for r in v["legs"])))
    print("pooled (descriptive): dynamic W ~ busy_cores^%.2f (r = %.3f, n = %d runs)" % (f["alpha"], f["r"], f["n"]))
    for b in bands:
        print("  busy %s cores: n=%d, W per busy core %.2f (%.2f-%.2f), dynamic W %.1f" % (
            b["busy_cores"], b["n"], b["w_per_busy_core_median"], *b["w_per_busy_core_range"], b["dyn_w_median"]))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(out, fh, indent=1)


if __name__ == "__main__":
    main()
