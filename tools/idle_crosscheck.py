#!/usr/bin/env python3
"""Idle cross-check (runbook 25.6) and RAPL dynamic energy on two idle bases (27.8).

Offline, from data on disk; measures nothing. For each leg of a session prefix:
  * probe W  = the leg's post-bench 60 s idle probe (e_rapl_j / wall_s), same stack
               state and the same harness instrumentation as the measured runs
  * calib W  = that state's median in the session calibration (idle_w_<state>.txt)
  * flag     = |probe - calib| > max - min of the calibration's own reads (25.6)
  * dynamic energy per run = e_rapl_j - idle_w * wall_s, median over usable runs,
    on the probe basis (primary) and the calibration basis (upper bound)

    python3 tools/idle_crosscheck.py                 # prefix final_, calib lock_final_calib
    python3 tools/idle_crosscheck.py --prefix final_ --calib lock_final_calib --json out.json
"""
import argparse
import glob
import json
import os
import statistics

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(REPO, "results")


def calib_state(calib, plat):
    c = json.load(open(os.path.join(RES, "idle_w_calibration", calib, "idle_w_%s.txt" % plat)))
    return float(c["median_w"]), max(c["reads_w"]) - min(c["reads_w"])


def legs(prefix, calib):
    for f in sorted(glob.glob(os.path.join(RES, "lock_session_%stier1*" % prefix, "lock_summary.json"))):
        d = json.load(open(f))
        stamp, disc = d["session"]["stamp"], int(d["session"]["discard_warmup"])
        probe_f = glob.glob(os.path.join(RES, "idle_probe_%s" % stamp, "*", "summary.json"))
        if not probe_f:
            continue
        p = json.load(open(probe_f[0]))
        probe_w = p["e_rapl_j"] / p["wall_s"]
        for plat, v in d["platforms"].items():
            cal_w, spread = calib_state(calib, plat)
            runs = [json.load(open(r)) for r in
                    sorted(glob.glob(os.path.join(REPO, v["outdir"], "run_*", "summary.json")))][disc:]
            e_cal = statistics.median(r["e_rapl_j"] - cal_w * r["wall_s"] for r in runs)
            e_prb = statistics.median(r["e_rapl_j"] - probe_w * r["wall_s"] for r in runs)
            ok = statistics.median(r["successes"] for r in runs)
            yield {"stamp": stamp, "platform": plat,
                   "probe_w": round(probe_w, 3), "calib_w": cal_w, "calib_spread_w": round(spread, 3),
                   "diff_w": round(probe_w - cal_w, 3), "flagged": abs(probe_w - cal_w) > spread,
                   "probe_host_cores": round(p["host_cpu_sec"] / p["wall_s"], 2),
                   "e_dyn_j_probe": round(e_prb, 1), "e_dyn_j_calib": round(e_cal, 1),
                   "mj_per_inv_probe": round(e_prb / ok * 1000, 2),
                   "mj_per_inv_calib": round(e_cal / ok * 1000, 2),
                   "calib_overstates_pct": round((e_cal / e_prb - 1) * 100, 1),
                   "usable_runs": len(runs)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prefix", default="final_")
    ap.add_argument("--calib", default="lock_final_calib")
    ap.add_argument("--json", help="also write the rows here")
    a = ap.parse_args()
    rows = list(legs(a.prefix, a.calib))
    print("%-20s %-9s %6s %6s %6s %5s %4s %5s | %8s %8s %8s %8s %6s" % (
        "stamp", "platform", "probe", "calib", "diff", "sprd", "flag", "hostc",
        "Edyn_prb", "Edyn_cal", "mJ/i_prb", "mJ/i_cal", "over%"))
    for r in rows:
        print("%-20s %-9s %6.2f %6.2f %+6.2f %5.2f %4s %5.2f | %8.1f %8.1f %8.2f %8.2f %6.1f" % (
            r["stamp"], r["platform"], r["probe_w"], r["calib_w"], r["diff_w"], r["calib_spread_w"],
            "FLAG" if r["flagged"] else "ok", r["probe_host_cores"], r["e_dyn_j_probe"],
            r["e_dyn_j_calib"], r["mj_per_inv_probe"], r["mj_per_inv_calib"], r["calib_overstates_pct"]))
    print("flagged: %d/%d" % (sum(r["flagged"] for r in rows), len(rows)))
    if a.json:
        json.dump(rows, open(a.json, "w"), indent=2)


if __name__ == "__main__":
    main()
