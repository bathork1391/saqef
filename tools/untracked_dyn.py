#!/usr/bin/env python3
"""Idle-subtracted untracked host CPU per invocation (runbook 29.4).

cp_anatomy.py's untracked_host_ms_inv = (host − cp − fn) CPU / successes. host CPU
includes the box's idle floor (the stacks' own ~0.5–0.8 core, runbook 29.2 A), so that
figure grows with wall time, not with work: two owlog29 driver legs with the same
configuration read 8.16 vs 3.59 ms/inv in the ratio of their walls (22.1 vs 9.8 s).

This tool subtracts the leg's own idle probe (same stack state, zero traffic, 60 s):

    untracked_dyn_ms_inv = (host − cp − fn − probe_untracked_cores × window_s) / successes

per usable run (acceptance.json), then the median. It also reports the same correction
for cp (cp_dyn_ms_inv) to show how little idle there is in cp. The idle probe is one
60 s reading per leg, so a leg-to-leg idle difference of ~0.1 core moves the result by
0.1 × window / successes; the output carries that sensitivity (ms/inv per 0.1 core).

Usage: python3 tools/untracked_dyn.py --prefix owlog29_ [--res DIR] [--json OUT]
"""

import argparse
import glob
import json
import os
import statistics

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rows(res, prefix):
    for d in sorted(glob.glob(os.path.join(res, "*_cpubound_lock_%s*" % prefix))):
        acc_f = os.path.join(d, "acceptance.json")
        if not os.path.exists(acc_f):
            continue
        acc = json.load(open(acc_f))
        stamp = acc["stamp"]
        probe = glob.glob(os.path.join(res, "idle_probe_%s" % stamp, "*", "summary.json"))
        if not probe or not acc["usable_runs"]:
            continue
        p = json.load(open(probe[0]))
        pw = p["wall_s"]
        p_untr = (p["host_cpu_sec"] - p["cpu_sec"]["control_plane"] - p["cpu_sec"]["function"]) / pw
        p_cp = p["cpu_sec"]["control_plane"] / pw
        keep = set(acc["usable_runs"])
        runs = [r for i, r in enumerate(json.load(open(os.path.join(d, "runs.json"))))
                if "run_%d" % (i + 1) in keep and r.get("host_cpu_sec") is not None]
        if not runs:
            continue
        med = lambda f: statistics.median(f(r) for r in runs)
        untr = lambda r: r["host_cpu_sec"] - r["cpu_sec"]["control_plane"] - r["cpu_sec"]["function"]
        yield {
            "stamp": stamp, "platform": acc["platform"], "gates_ok": acc["leg_gates_ok"],
            "usable_runs": len(runs),
            "window_s": round(med(lambda r: r["host_window_s"]), 3),
            "untracked_raw_ms_inv": round(med(lambda r: untr(r) / r["successes"] * 1000), 3),
            "untracked_rate_cores": round(med(lambda r: untr(r) / r["host_window_s"]), 3),
            "probe_untracked_cores": round(p_untr, 3),
            "untracked_dyn_ms_inv": round(med(lambda r: (untr(r) - p_untr * r["host_window_s"])
                                                    / r["successes"] * 1000), 3),
            "sens_ms_inv_per_0p1_core": round(med(lambda r: 0.1 * r["host_window_s"]
                                                  / r["successes"] * 1000), 3),
            "cp_ms_inv": round(med(lambda r: r["cpu_sec"]["control_plane"] / r["successes"] * 1000), 3),
            "probe_cp_cores": round(p_cp, 4),
            "cp_dyn_ms_inv": round(med(lambda r: (r["cpu_sec"]["control_plane"] - p_cp * r["host_window_s"])
                                             / r["successes"] * 1000), 3),
        }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--res", default=os.path.join(REPO, "results"))
    ap.add_argument("--json")
    a = ap.parse_args()
    out = list(rows(a.res, a.prefix))
    print("%-30s %5s %8s | %8s %6s %6s -> %8s (±%5s per 0.1 core) | cp %7s -> %7s" % (
        "stamp", "gates", "window", "untr raw", "rate", "idle", "untr dyn", "sens", "raw", "dyn"))
    for r in out:
        print("%-30s %5s %8.2f | %8.2f %6.2f %6.2f -> %8.2f (±%5.2f per 0.1 core) | cp %7.2f -> %7.2f" % (
            r["stamp"], "ok" if r["gates_ok"] else "FAIL", r["window_s"], r["untracked_raw_ms_inv"],
            r["untracked_rate_cores"], r["probe_untracked_cores"], r["untracked_dyn_ms_inv"],
            r["sens_ms_inv_per_0p1_core"], r["cp_ms_inv"], r["cp_dyn_ms_inv"]))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
