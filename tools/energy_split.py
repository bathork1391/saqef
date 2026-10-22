"""Offline B (runbook §39, plan written before any fit): split dynamic package power by CPU role.

Per usable run of every passing leg in the sessions the paper uses, with the leg's own idle probe:
    dyn_w = e_rapl_j / wall_s - probe W
    x_cp  = (cp_sec  - probe cp rate  * host_window_s) / wall_s     idle-subtracted busy cores
    x_fn  = (fn_sec  - probe fn rate  * host_window_s) / wall_s
    x_out = (out_sec - probe out rate * host_window_s) / wall_s     out = host - cp - fn
M1: dyn_w = a*x_cp + b*x_fn + d*x_out + c        M0: dyn_w = k*(x_cp + x_fn + x_out) + c
B1: a > 0 and b > 0, 95 % bootstrap interval over legs (1000 resamples, seed 39) excludes 0.
B2: M1's median leave-one-session-out error of energy per request over legs <= 10 %, and below M0's.
B3: if B1 and B2 hold, platform-only energy per request a * x_cp * wall_s / requests for Part A.

Usage: python3 tools/energy_split.py [--res DIR] [--json OUT] [--md OUT]
"""

import argparse
import glob
import json
import os
import re
import statistics

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# (session, stamp regex), first match wins; the "yes" groups of saqef-paper/scripts/index_results.py
SESSIONS = (("w6_b_", r"^w6_b_"), ("w6_a2_", r"^w6_a2_"), ("w6_a_", r"^w6_a_"),
            ("io_b2_", r"^io_b2_"), ("io_a_", r"^io_a_"),
            ("burst_amend31_15", r"^burst_amend31_15"), ("burst_", r"^burst_(?!amend)"),
            ("cold_", r"^cold_"),
            ("owlog29_amend33_2", r"^owlog29_amend33_2"), ("owlog29_", r"^owlog29_(?!amend)"),
            ("payload_amend28_8", r"^payload_amend28_8"), ("payload_amend33_1", r"^payload_amend33_1"),
            ("payload_", r"^payload_(?!amend)"), ("mem2_", r"^mem2_"), ("final_", r"^final_"))
PLATFORM = {"openfaas": "openfaas", "fn": "fn", "knative": "knative", "openwhisk": "openwhisk"}


def session_of(stamp):
    for name, rx in SESSIONS:
        if re.match(rx, stamp):
            return name
    return None


def usable(res, d, stamp):
    """Usable run names of a passing leg, or None."""
    acc = os.path.join(d, "acceptance.json")
    if os.path.exists(acc):
        a = json.load(open(acc))
        return a["usable_runs"] if a.get("leg_gates_ok") and a.get("usable_runs") else None
    ls = os.path.join(res, "lock_session_%s" % stamp, "lock_summary.json")
    if not os.path.exists(ls):   # final_ predates acceptance.json: runs after the discard (§39.2)
        return None
    disc = int(json.load(open(ls))["session"]["discard_warmup"])
    n = len(glob.glob(os.path.join(d, "run_*")))
    return ["run_%d" % i for i in range(disc + 1, n + 1)]


def runs(res):
    """Per-run points and a count of skipped legs."""
    pts, skipped = [], {"no_probe": 0, "no_energy": 0, "failed": 0}
    for d in sorted(glob.glob(os.path.join(res, "*_cpubound_lock_*"))):
        plat, stamp = os.path.basename(d).split("_cpubound_lock_", 1)
        sess = session_of(stamp)
        if sess is None or plat not in PLATFORM:
            continue
        keep = usable(res, d, stamp)
        if not keep:
            skipped["failed"] += 1
            continue
        probe = glob.glob(os.path.join(res, "idle_probe_%s" % stamp, "*", "summary.json"))
        if not probe:
            skipped["no_probe"] += 1
            continue
        p = json.load(open(probe[0]))
        if p.get("e_rapl_j") is None or p.get("host_cpu_sec") is None:
            skipped["no_energy"] += 1
            continue
        pw = p["wall_s"]
        r_w = p["e_rapl_j"] / pw
        r_cp = p["cpu_sec"]["control_plane"] / pw
        r_fn = p["cpu_sec"]["function"] / pw
        r_out = (p["host_cpu_sec"] - p["cpu_sec"]["control_plane"] - p["cpu_sec"]["function"]) / pw
        rs = json.load(open(os.path.join(d, "runs.json")))
        got = 0
        for i, r in enumerate(rs):
            if "run_%d" % (i + 1) not in keep or not r.get("requests"):
                continue
            if r.get("e_rapl_j") is None or r.get("host_cpu_sec") is None:
                continue
            w, hw = r["wall_s"], r["host_window_s"]
            cp, fn = r["cpu_sec"]["control_plane"], r["cpu_sec"]["function"]
            out = r["host_cpu_sec"] - cp - fn
            pts.append({"session": sess, "leg": stamp, "platform": plat, "run": i + 1,
                        "requests": r["requests"], "wall_s": w,
                        "dyn_w": r["e_rapl_j"] / w - r_w,
                        "x_cp": (cp - r_cp * hw) / w, "x_fn": (fn - r_fn * hw) / w,
                        "x_out": (out - r_out * hw) / w})
            got += 1
        if not got:
            skipped["no_energy"] += 1
    return pts, skipped


def design(pts, model):
    if model == "M1":
        return np.array([[p["x_cp"], p["x_fn"], p["x_out"], 1.0] for p in pts])
    return np.array([[p["x_cp"] + p["x_fn"] + p["x_out"], 1.0] for p in pts])


def fit(pts, model):
    coef, *_ = np.linalg.lstsq(design(pts, model), np.array([p["dyn_w"] for p in pts]), rcond=None)
    return coef


def leg_mj(pts, dyn):
    """Median over runs of energy per request (mJ) for each leg, from dyn_w values `dyn`."""
    by = {}
    for p, w in zip(pts, dyn):
        by.setdefault(p["leg"], []).append(w * p["wall_s"] / p["requests"] * 1000)
    return {k: statistics.median(v) for k, v in by.items()}


def loso(pts, model):
    """Leave one session out: per-leg |predicted - measured| / measured."""
    meas = leg_mj(pts, [p["dyn_w"] for p in pts])
    err = {}
    for s in sorted({p["session"] for p in pts}):
        train = [p for p in pts if p["session"] != s]
        test = [p for p in pts if p["session"] == s]
        pred = leg_mj(test, design(test, model) @ fit(train, model))
        for k, v in pred.items():
            err[k] = abs(v - meas[k]) / abs(meas[k]) if meas[k] else None
    return err


def bootstrap(pts, n=1000, seed=39):
    legs = sorted({p["leg"] for p in pts})
    by = {k: [p for p in pts if p["leg"] == k] for k in legs}
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        sample = [q for k in rng.choice(legs, size=len(legs), replace=True) for q in by[k]]
        out.append(fit(sample, "M1"))
    out = np.array(out)
    return {name: [float(np.percentile(out[:, i], 2.5)), float(np.percentile(out[:, i], 97.5))]
            for i, name in enumerate(("a", "b", "d", "c"))}


def analyse(res, n_boot=1000):
    pts, skipped = runs(res)
    a, b, d, c = (float(x) for x in fit(pts, "M1"))
    k, c0 = (float(x) for x in fit(pts, "M0"))
    ci = bootstrap(pts, n_boot)
    e1, e0 = loso(pts, "M1"), loso(pts, "M0")
    med1 = statistics.median(v for v in e1.values() if v is not None)
    med0 = statistics.median(v for v in e0.values() if v is not None)
    b1 = bool(a > 0 and b > 0 and ci["a"][0] > 0 and ci["b"][0] > 0)
    b2 = bool(med1 <= 0.10 and med1 < med0)
    per_session = {}
    for s in sorted({p["session"] for p in pts}):
        ks = sorted({p["leg"] for p in pts if p["session"] == s})
        per_session[s] = {"legs": len(ks), "M1_median_err": statistics.median(e1[x] for x in ks),
                          "M0_median_err": statistics.median(e0[x] for x in ks)}
    meas = leg_mj(pts, [p["dyn_w"] for p in pts])
    part_a = []
    for leg in sorted({p["leg"] for p in pts if p["session"] == "final_"}):
        lp = [p for p in pts if p["leg"] == leg]
        cp_mj = statistics.median(a * p["x_cp"] * p["wall_s"] / p["requests"] * 1000 for p in lp)
        part_a.append({"leg": leg, "platform": lp[0]["platform"], "measured_mj": meas[leg],
                       "platform_only_mj": cp_mj if (b1 and b2) else None,
                       "platform_only_mj_descriptive": cp_mj})
    return {"runs": len(pts), "legs": len(meas), "sessions": len(per_session), "skipped_legs": skipped,
            "M1": {"a": a, "b": b, "d": d, "c": c, "ci95": ci},
            "M0": {"k": k, "c": c0},
            "a_over_b": a / b if b else None,
            "corr_cp_fn": float(np.corrcoef([p["x_cp"] for p in pts], [p["x_fn"] for p in pts])[0, 1]),
            "loso_median_err": {"M1": med1, "M0": med0},
            "per_session": per_session,
            "verdict": {"B1": b1, "B2": b2, "B3_reported": b1 and b2},
            "part_a": part_a}


def markdown(out):
    m, ci = out["M1"], out["M1"]["ci95"]
    v = out["verdict"]
    lines = ["# Offline B: energy split by CPU role (runbook §39)", "",
             "%d runs, %d legs, %d sessions; skipped legs %s." % (out["runs"], out["legs"], out["sessions"],
                                                                   out["skipped_legs"]), "",
             "| term | M1 fit (W per busy core) | 95 % bootstrap interval |", "|---|---|---|"]
    for name, label in (("a", "a, control plane"), ("b", "b, function"), ("d", "d, outside containers"),
                        ("c", "c, intercept (W)")):
        lines.append("| %s | %.2f | %.2f to %.2f |" % (label, m[name], ci[name][0], ci[name][1]))
    lines += ["", "M0: k = %.2f W per busy core, c = %.2f W. a / b = %.2f. Correlation of x_cp with x_fn: %.2f."
              % (out["M0"]["k"], out["M0"]["c"], out["a_over_b"], out["corr_cp_fn"]), "",
              "| prediction | result | verdict |", "|---|---|---|",
              "| B1 a > 0 and b > 0, intervals exclude 0 | a %.2f [%.2f, %.2f], b %.2f [%.2f, %.2f] | %s |"
              % (m["a"], ci["a"][0], ci["a"][1], m["b"], ci["b"][0], ci["b"][1], "holds" if v["B1"] else "fails"),
              "| B2 M1 leave-one-session-out median error ≤ 10 %% and below M0 | M1 %.1f %%, M0 %.1f %% | %s |"
              % (100 * out["loso_median_err"]["M1"], 100 * out["loso_median_err"]["M0"],
                 "holds" if v["B2"] else "fails"), "",
              "| session | legs | M1 median error | M0 median error |", "|---|---|---|---|"]
    for s, r in out["per_session"].items():
        lines.append("| %s | %d | %.1f %% | %.1f %% |" % (s, r["legs"], 100 * r["M1_median_err"], 100 * r["M0_median_err"]))
    lines += ["", "| Part A leg | measured mJ/req | platform-only mJ/req (a × x_cp)%s |"
              % ("" if v["B3_reported"] else ", descriptive only"), "|---|---|---|"]
    for r in out["part_a"]:
        lines.append("| %s | %.1f | %.2f |" % (r["leg"], r["measured_mj"], r["platform_only_mj_descriptive"]))
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--res", default=os.path.join(REPO, "results"))
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args()
    out = analyse(a.res)
    md = markdown(out)
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1, sort_keys=True)
    if a.md:
        open(a.md, "w").write(md)
    print(md)


if __name__ == "__main__":
    main()
