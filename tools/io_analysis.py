#!/usr/bin/env python3
"""W5 I/O and mixed functions: verdicts of runbook §36.3 (D1, P1-P4) and the plain-Docker fee
(§36.4), written before any W5 data so the analysis cannot be fitted to the numbers.

Per leg (stamp <prefix>c<c>_<p>_<arm>, or its _r2 retry if that is the one that passed), over
the usable runs of acceptance.json (runbook §23), per run then the median:
  cp_idle   = (cp CPU-s - probe cp cores x host window) / successes x 1000      paper Eq. 3
  fn        =  function CPU-s / successes x 1000
  share     = 100 x cp / (cp + fn)                                              paper Eq. 4
  untr_idle = (host - cp - fn - probe untracked cores x host window) / successes x 1000   Eq. 5
  mj        = (RAPL J - probe W x wall) / successes x 1000                      Eq. 6
The probe is the leg's own 60 s idle probe (idle_probe_<stamp>), as tools/untracked_dyn.py and
tools/idle_crosscheck.py. Ratios across arms are ratios of leg medians (§36.5 rule 4).

Usage: python3 tools/io_analysis.py --prefix io_a_ [--res DIR] [--json OUT] [--md OUT]
"""
import argparse
import glob
import json
import os
import statistics

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATS = (("of", "openfaas"), ("fn", "fn"), ("kn", "knative"), ("ow", "openwhisk"))
ARMS = ("spin", "sleep", "disk", "mixed")
CONC = (1, 4, 8)


def leg(res, stamp):
    """Medians over usable runs of the passing attempt of `stamp`, or None (missing)."""
    for st in (stamp, stamp + "_r2"):
        acc_f = glob.glob(os.path.join(res, "*_cpubound_lock_%s" % st, "acceptance.json"))
        if not acc_f:
            continue
        acc = json.load(open(acc_f[0]))
        if not acc.get("leg_gates_ok") or not acc.get("usable_runs"):
            continue
        probe = glob.glob(os.path.join(res, "idle_probe_%s" % st, "*", "summary.json"))
        if not probe:
            continue
        p = json.load(open(probe[0]))
        pw = p["wall_s"]
        p_cp = p["cpu_sec"]["control_plane"] / pw
        p_untr = (p["host_cpu_sec"] - p["cpu_sec"]["control_plane"] - p["cpu_sec"]["function"]) / pw
        p_w = p["e_rapl_j"] / pw if p.get("e_rapl_j") is not None else None
        keep = set(acc["usable_runs"])
        runs = [r for i, r in enumerate(json.load(open(os.path.join(os.path.dirname(acc_f[0]), "runs.json"))))
                if "run_%d" % (i + 1) in keep and r.get("successes")]
        if not runs:
            continue

        def med(f):
            v = [f(r) for r in runs]
            v = [x for x in v if x is not None]
            return statistics.median(v) if v else None

        cp = lambda r: r["cpu_sec"]["control_plane"]
        fn = lambda r: r["cpu_sec"]["function"]
        return {
            "stamp": st, "usable_runs": len(runs),
            "cp_idle": med(lambda r: (cp(r) - p_cp * r["host_window_s"]) / r["successes"] * 1000),
            "cp_raw": med(lambda r: cp(r) / r["successes"] * 1000),
            "fn": med(lambda r: fn(r) / r["successes"] * 1000),
            "share": med(lambda r: 100.0 * cp(r) / (cp(r) + fn(r)) if cp(r) + fn(r) > 0 else None),
            "untr_idle": med(lambda r: (r["host_cpu_sec"] - cp(r) - fn(r) - p_untr * r["host_window_s"])
                             / r["successes"] * 1000 if r.get("host_cpu_sec") is not None else None),
            "mj": med(lambda r: (r["e_rapl_j"] - p_w * r["wall_s"]) / r["successes"] * 1000
                      if p_w is not None and r.get("e_rapl_j") is not None else None),
            "rps": med(lambda r: r.get("throughput_rps")),
            "p50": med(lambda r: (r.get("latency_ms") or {}).get("p50")),
            "p99": med(lambda r: (r.get("latency_ms") or {}).get("p99")),
        }
    return None


def analyse(res, prefix):
    L = {}
    for s, plat in PLATS:
        for c in CONC:
            for arm in ARMS:
                v = leg(res, "%sc%d_%s_%s" % (prefix, c, s, arm))
                if v:
                    L[(plat, c, arm)] = v
    excluded = set()
    ex_f = os.path.join(res, "%ssession" % prefix, "disk_excluded.txt")
    if os.path.exists(ex_f):
        excluded = set(open(ex_f).read().split()) - {"none"}

    cells = sorted({(p, c) for (p, c, _a) in L}, key=lambda x: ([q for _s, q in PLATS].index(x[0]), x[1]))
    rows, d1_ok = [], {}
    exact = {}   # unrounded ratios for the counts; the JSON keeps 3 dp (§36.10: 0.850 sat on the float boundary)
    for p, c in cells:
        g = {a: L.get((p, c, a)) for a in ARMS}
        sp, sl, mx = g["spin"], g["sleep"], g["mixed"]
        ok = bool(sp and sl and mx and sp["fn"] >= 4.5 and sl["fn"] <= sp["fn"] - 3.5
                  and sl["fn"] < mx["fn"] < sp["fn"])
        d1_ok[(p, c)] = ok
        r = {"platform": p, "c": c, "D1": ok, "legs": {a: g[a] for a in ARMS if g[a]}}
        if sp:
            e1 = {a: (g[a]["cp_idle"] / sp["cp_idle"] if g[a] and sp["cp_idle"] else None)
                  for a in ("sleep", "disk", "mixed")}
            exact[(p, c, "P1")] = e1
            r["P1"] = {a: (None if x is None else round(x, 3)) for a, x in e1.items()}
        if sp and sl and mx:
            r["P2"] = sl["share"] > mx["share"] > sp["share"]
            mid = (sp["fn"] + sl["fn"]) / 2
            exact[(p, c, "P3")] = mx["fn"] / mid
            r["P3_ratio"] = round(mx["fn"] / mid, 3)
            r["mixed_share_predicted"] = round(100 * mx["cp_raw"] / (mx["cp_raw"] + mid), 2)
        if sl and g["disk"]:
            r["P4a_fn_diff_ms"] = round(g["disk"]["fn"] - sl["fn"], 3)
            r["P4b_untr_higher"] = (g["disk"]["untr_idle"] is not None and sl["untr_idle"] is not None
                                    and g["disk"]["untr_idle"] > sl["untr_idle"])
        rows.append(r)

    counted = [r for r in rows if r["D1"]]
    verdict = {"cells": len(rows), "cells_D1_ok": len(counted), "disk_excluded": sorted(excluded)}
    for a in ("sleep", "disk", "mixed"):
        vals = [exact[(r["platform"], r["c"], "P1")][a] for r in counted
                if r.get("P1", {}).get(a) is not None]
        hold = sum(1 for x in vals if abs(x - 1) <= 0.15)
        need = 10 if len(vals) == 12 else -(-5 * len(vals) // 6)   # >= 10/12, else >= 5/6 of them
        verdict["P1_" + a] = {"cells": len(vals), "within_15pct": hold, "need": need,
                              "holds": bool(vals) and hold >= need}
    v2 = [r["P2"] for r in counted if "P2" in r]
    verdict["P2"] = {"cells": len(v2), "ordered": sum(v2), "need": 11 if len(v2) == 12 else -(-11 * len(v2) // 12),
                     "holds": bool(v2) and sum(v2) >= (11 if len(v2) == 12 else -(-11 * len(v2) // 12))}
    v3 = [exact[(r["platform"], r["c"], "P3")] for r in counted if "P3_ratio" in r]
    h3 = sum(1 for x in v3 if abs(x - 1) <= 0.15)
    verdict["P3"] = {"cells": len(v3), "within_15pct": h3, "need": 10 if len(v3) == 12 else -(-5 * len(v3) // 6),
                     "holds": bool(v3) and h3 >= (10 if len(v3) == 12 else -(-5 * len(v3) // 6))}
    v4a = [r["P4a_fn_diff_ms"] for r in rows if "P4a_fn_diff_ms" in r]
    h4a = sum(1 for x in v4a if x <= 0.3)
    verdict["P4a"] = {"cells": len(v4a), "within_0p3ms": h4a, "need": 10 if len(v4a) == 12 else -(-5 * len(v4a) // 6),
                      "holds": bool(v4a) and h4a >= (10 if len(v4a) == 12 else -(-5 * len(v4a) // 6))}
    v4b = [r["P4b_untr_higher"] for r in rows if "P4b_untr_higher" in r]
    verdict["P4b"] = {"cells": len(v4b), "higher": sum(v4b), "need": 9 if len(v4b) == 12 else -(-3 * len(v4b) // 4),
                      "holds": bool(v4b) and sum(v4b) >= (9 if len(v4b) == 12 else -(-3 * len(v4b) // 4))}

    plain = {}
    for arm in ("spin", "sleep"):
        f = os.path.join(res, "%splain_%s_c1" % (prefix, arm), "summary.json")
        if not os.path.exists(f):
            continue
        pm = json.load(open(f))["median"]
        plain[arm] = {"plain": pm, "fee": {}}
        for _s, p in PLATS:
            v = L.get((p, 1, arm))
            if v:
                plain[arm]["fee"][p] = {
                    "cpu_ms_inv": round(v["cp_idle"] + v["fn"] - pm["fn_ms_inv_idle_sub"], 3),
                    "latency_p50_ms": round(v["p50"] - pm["p50_ms"], 3) if v["p50"] is not None else None}
    return {"prefix": prefix, "verdict": verdict, "cells": rows, "plain": plain}


def word(x):
    return "no data" if not x["cells"] else ("holds" if x["holds"] else "fails")


def markdown(out):
    v = out["verdict"]
    lines = ["# W5 verdicts, prefix `%s` (runbook §36.3)" % out["prefix"], "",
             "Cells: %d, passing D1: %d. Disk arm excluded on: %s." % (
                 v["cells"], v["cells_D1_ok"], ", ".join(v["disk_excluded"]) or "none"), "",
             "| prediction | result | verdict |", "|---|---|---|"]
    for a in ("sleep", "disk", "mixed"):
        x = v["P1_" + a]
        lines.append("| P1 %s: cp within ±15 %% of spin | %d of %d cells (need %d) | %s |"
                     % (a, x["within_15pct"], x["cells"], x["need"], word(x)))
    for k, lab, n in (("P2", "share sleep > mixed > spin", "ordered"), ("P3", "mixed fn within ±15 % of midpoint", "within_15pct"),
                      ("P4a", "disk fn ≤ sleep + 0.3 ms", "within_0p3ms"), ("P4b", "disk host-outside > sleep", "higher")):
        x = v[k]
        lines.append("| %s %s | %d of %d cells (need %d) | %s |" % (k, lab, x[n], x["cells"], x["need"], word(x)))
    lines += ["", "| platform | c | D1 | arm | cp idle-sub (ms) | cp raw | fn (ms) | share % | host outside (ms) | mJ | rps | p50 | p99 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    f = lambda x, d=3: "—" if x is None else ("%.*f" % (d, x))
    for r in out["cells"]:
        for a, g in r["legs"].items():
            lines.append("| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r["platform"], r["c"], "ok" if r["D1"] else "**fails**", a, f(g["cp_idle"]), f(g["cp_raw"]),
                f(g["fn"]), f(g["share"], 1), f(g["untr_idle"]), f(g["mj"], 1), f(g["rps"], 0), f(g["p50"], 2), f(g["p99"], 2)))
    if out["plain"]:
        lines += ["", "Plain-Docker fee at c = 1 (descriptive, §36.4): platform (cp idle-sub + fn) − plain fn; p50 − plain p50.", "",
                  "| arm | platform | CPU fee (ms/req) | latency fee (ms) |", "|---|---|---|---|"]
        for arm, d in out["plain"].items():
            for p, fee in d["fee"].items():
                lines.append("| %s | %s | %s | %s |" % (arm, p, f(fee["cpu_ms_inv"]), f(fee["latency_p50_ms"], 2)))
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--res", default=os.path.join(REPO, "results"))
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args()
    out = analyse(a.res, a.prefix)
    md = markdown(out)
    print(md)
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
    if a.md:
        open(a.md, "w").write(md)


if __name__ == "__main__":
    main()
