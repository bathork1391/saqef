#!/usr/bin/env python3
"""W6 failures, overload, half load and mixed traffic: verdicts of runbook §38.3 (F1-F3, O1-O4,
H1-H4, A1-A2, K0-K2), written before any W6 data so the analysis cannot be fitted to the numbers.

Per leg (stamp <prefix><p>_<arm>, or its _r2 retry if that is the one that passed), over the usable
runs of acceptance.json (runbook §23), per run then the median. "Attempted" is every request sent
(successes + errors); a failed request costs the platform work too, so W6 divides by it:
  err       = 1 - successes / attempted
  cp_idle   = (cp CPU-s - probe cp cores x host window) / attempted x 1000        paper Eq. 3
  fn        =  function CPU-s / attempted x 1000
  untr_idle = (host - cp - fn - probe untracked cores x host window) / attempted x 1000   Eq. 5
  mj        = (RAPL J - probe W x wall) / attempted x 1000                         Eq. 6
  rps       = successes / wall (the harness's throughput); p50, p99 from the load generator
  owc       = OpenWhisk action containers (wsk*_guest_hello) seen in the run's inventory
The probe is the leg's own 60 s idle probe. Ratios between arms are ratios of leg medians, always
against the base leg of the same platform in the same slot (§38.5 rule 3).

Usage: python3 tools/w6_analysis.py --prefix w6_a_ [--res DIR] [--json OUT] [--md OUT]
"""
import argparse
import glob
import json
import os
import statistics

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATS = (("of", "openfaas"), ("fn", "fn"), ("kn", "knative"), ("ow", "openwhisk"))
ARMS = ("base", "half", "over", "fail5", "fail20", "alt")
POOL = ("pool16_c8", "pool16_c4", "pool2_c4")
DESIGN_ERR = {"fail5": 0.05, "fail20": 0.20}


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
                if "run_%d" % (i + 1) in keep and r.get("requests")]
        if not runs:
            continue

        def med(f):
            v = [f(r) for r in runs]
            v = [x for x in v if x is not None]
            return statistics.median(v) if v else None

        cp = lambda r: r["cpu_sec"]["control_plane"]
        fn = lambda r: r["cpu_sec"]["function"]
        att = lambda r: r["requests"]
        return {
            "stamp": st, "usable_runs": len(runs),
            "attempted": med(att), "errors": sum(att(r) - r["successes"] for r in runs),
            "err": med(lambda r: 1.0 - r["successes"] / att(r)),
            "cp_idle": med(lambda r: (cp(r) - p_cp * r["host_window_s"]) / att(r) * 1000),
            "cp_raw": med(lambda r: cp(r) / att(r) * 1000),
            "fn": med(lambda r: fn(r) / att(r) * 1000),
            "untr_idle": med(lambda r: (r["host_cpu_sec"] - cp(r) - fn(r) - p_untr * r["host_window_s"])
                             / att(r) * 1000 if r.get("host_cpu_sec") is not None else None),
            "mj": med(lambda r: (r["e_rapl_j"] - p_w * r["wall_s"]) / att(r) * 1000
                      if p_w is not None and r.get("e_rapl_j") is not None else None),
            "rps": med(lambda r: r.get("throughput_rps")),
            "target_qps": med(lambda r: (r.get("env") or {}).get("target_qps") or None),
            "p50": med(lambda r: (r.get("latency_ms") or {}).get("p50")),
            "p99": med(lambda r: (r.get("latency_ms") or {}).get("p99")),
            "owc": med(lambda r: sum(1 for n in r.get("container_inventory") or [] if "guest_hello" in n)),
        }
    return None


def ratio(a, b, k):
    return a[k] / b[k] if a and b and a.get(k) is not None and b.get(k) else None


def within(x, tol):
    return x is not None and abs(x - 1) <= tol


def analyse(res, prefix):
    L = {}
    for s, plat in PLATS:
        for arm in ARMS + (POOL if s == "ow" else ()):
            v = leg(res, "%s%s_%s" % (prefix, s, arm))
            if v:
                L[(plat, arm)] = v
    plats = [p for _s, p in PLATS if any(k[0] == p for k in L)]
    rows, V = [], {}

    def count(key, cells, need):
        V[key] = {"cells": len(cells), "hold": sum(cells), "need": need, "holds": bool(cells) and sum(cells) >= need}

    f1, f2, f3, o1, o2, o3, o4a, h1, h2, h3, h4, a1, a2 = ([] for _ in range(13))
    o4b = None
    for p in plats:
        b = L.get((p, "base"))
        r = {"platform": p, "legs": {a: L[(p, a)] for a in ARMS + POOL if (p, a) in L}}
        for arm in ("fail5", "fail20"):
            g = L.get((p, arm))
            if g is None:
                continue
            r[arm] = {"err": g["err"], "fn_ratio": ratio(g, b, "fn"), "cp_ratio": ratio(g, b, "cp_idle")}
            f1.append(abs(g["err"] - DESIGN_ERR[arm]) <= 0.01)
            if b:
                f2.append(within(r[arm]["fn_ratio"], 0.10))
                f3.append(within(r[arm]["cp_ratio"], 0.15))
        g = L.get((p, "over"))
        if g is not None:
            r["over"] = {"err": g["err"], "rps_ratio": ratio(g, b, "rps"), "p50_ratio": ratio(g, b, "p50"),
                         "cp_ratio": ratio(g, b, "cp_idle")}
            o1.append(g["err"] <= 0.01)
            if b:
                o2.append(within(r["over"]["rps_ratio"], 0.15))
                o3.append(r["over"]["p50_ratio"] is not None and 4 <= r["over"]["p50_ratio"] <= 12)
                if p == "openfaas":
                    o4b = r["over"]["cp_ratio"] is not None and r["over"]["cp_ratio"] >= 1.15
                else:
                    o4a.append(within(r["over"]["cp_ratio"], 0.15))
        g = L.get((p, "half"))
        if g is not None and b:
            hit = g["target_qps"] and within(g["rps"] / g["target_qps"], 0.10)
            r["half"] = {"rps_vs_target": g["rps"] / g["target_qps"] if g["target_qps"] else None,
                         "cp_ratio": ratio(g, b, "cp_idle"), "mj_ratio": ratio(g, b, "mj"),
                         "p50_ratio": ratio(g, b, "p50"), "design_ok": bool(hit)}
            h1.append(bool(hit))
            if hit:   # §38.5 rule 5: a half leg off its rate is not judged on H2-H4
                h2.append(within(r["half"]["cp_ratio"], 0.15))
                h3.append(r["half"]["mj_ratio"] is not None and r["half"]["mj_ratio"] > 1)
                h4.append(r["half"]["p50_ratio"] is not None and r["half"]["p50_ratio"] <= 1)
        g = L.get((p, "alt"))
        if g is not None and b:
            r["alt"] = {"fn_ratio": ratio(g, b, "fn"), "cp_ratio": ratio(g, b, "cp_idle")}
            a1.append(r["alt"]["fn_ratio"] is not None and 0.45 <= r["alt"]["fn_ratio"] <= 0.75)
            a2.append(within(r["alt"]["cp_ratio"], 0.15))
        rows.append(r)

    count("F1", f1, 7 if len(f1) == 8 else len(f1))
    count("F2", f2, 7 if len(f2) == 8 else len(f2))
    count("F3", f3, 6 if len(f3) == 8 else -(-3 * len(f3) // 4))
    count("O1", o1, len(o1))
    count("O2", o2, 3 if len(o2) == 4 else len(o2))
    count("O3", o3, 3 if len(o3) == 4 else len(o3))
    count("O4a", o4a, 2 if len(o4a) == 3 else len(o4a))
    V["O4b"] = {"cells": 0 if o4b is None else 1, "hold": int(bool(o4b)), "need": 1, "holds": bool(o4b)}
    count("H1", h1, len(h1))
    count("H2", h2, 3 if len(h2) == 4 else len(h2))
    count("H3", h3, 3 if len(h3) == 4 else len(h3))
    count("H4", h4, 3 if len(h4) == 4 else len(h4))
    count("A1", a1, len(a1))
    count("A2", a2, 3 if len(a2) == 4 else len(a2))

    pool = {}
    if any(k[0] == "openwhisk" for k in L):
        pairs = (("c8", L.get(("openwhisk", "pool16_c8")), L.get(("openwhisk", "base"))),
                 ("c4", L.get(("openwhisk", "pool16_c4")), L.get(("openwhisk", "pool2_c4"))))
        k0, k1, k2 = [], [], []
        for c, p16, p2 in pairs:
            if not (p16 and p2):
                continue
            pool[c] = {"owc_16": p16["owc"], "owc_2": p2["owc"], "cp_ratio": ratio(p16, p2, "cp_idle"),
                       "rps_ratio": ratio(p16, p2, "rps"), "fn_ratio": ratio(p16, p2, "fn"),
                       "p50_ratio": ratio(p16, p2, "p50"), "mj_ratio": ratio(p16, p2, "mj")}
            k0.append(p16["owc"] is not None and p16["owc"] > 2)
            k1.append(within(pool[c]["cp_ratio"], 0.15))
            k2.append(within(pool[c]["rps_ratio"], 0.15))
        count("K0", k0, len(k0))
        count("K1", k1, len(k1))
        count("K2", k2, len(k2))
    return {"prefix": prefix, "verdict": V, "platforms": rows, "ow_pool": pool}


def word(x):
    return "no data" if not x["cells"] else ("holds" if x["holds"] else "fails")


LABEL = {"F1": "error share as designed (±1 point)", "F2": "fail fn within ±10 % of base",
         "F3": "fail cp within ±15 % of base", "O1": "c = 64: ≥ 99 % answered",
         "O2": "c = 64 rps within ±15 % of base", "O3": "c = 64 p50 4–12 × base",
         "O4a": "c = 64 cp within ±15 % (Fn, Knative, OpenWhisk)", "O4b": "c = 64 cp ≥ 1.15 × base (OpenFaaS)",
         "H1": "half: rps within ±10 % of target", "H2": "half cp within ±15 % of base",
         "H3": "half energy per request > base", "H4": "half p50 ≤ base", "A1": "alt fn 0.45–0.75 × base",
         "A2": "alt cp within ±15 % of base", "K0": "16-pool runs > 2 action containers",
         "K1": "16 vs 2: cp within ±15 %", "K2": "16 vs 2: rps within ±15 %"}


def markdown(out):
    f = lambda x, d=3: "—" if x is None else ("%.*f" % (d, x))
    lines = ["# W6 verdicts, prefix `%s` (runbook §38.3)" % out["prefix"], "",
             "| prediction | result | verdict |", "|---|---|---|"]
    for k, x in out["verdict"].items():
        lines.append("| %s %s | %d of %d (need %d) | %s |" % (k, LABEL[k], x["hold"], x["cells"], x["need"], word(x)))
    lines += ["", "| platform | arm | runs | attempted | errors | err % | cp idle-sub (ms) | fn (ms) | host outside (ms) | mJ | rps | p50 | p99 | OW containers |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in out["platforms"]:
        for a, g in r["legs"].items():
            lines.append("| %s | %s | %d | %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r["platform"], a, g["usable_runs"], f(g["attempted"], 0), g["errors"], f(100 * g["err"], 2),
                f(g["cp_idle"]), f(g["fn"]), f(g["untr_idle"]), f(g["mj"], 1), f(g["rps"], 0), f(g["p50"], 1),
                f(g["p99"], 1), f(g["owc"], 0)))
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
