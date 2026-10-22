"""Post hoc (runbook §34): how Fn's hot-container pool grows inside a W3 burst run.

For every run of the Fn burst legs on disk: function containers alive before the window, born
inside it, the delay from the window start to the first birth, and when the failed requests
(non-2xx) completed relative to that first birth, per burst.

Timing sources, both independent of the cgroup sampler's ~100 ms resolution (§31.18 B):
  container birth = docker's own creation time (`born_epoch` in samples_raw.csv, from one
  `docker inspect` per container); request times = hey's offset (from the run start, which is
  the window start, `attribution.window_start_epoch`) + response time.
Function containers are Fn's ULID-named containers (26 characters, [0-9A-Z]); fnserver and the
resident k3s/Knative substrate (k8s_*) are excluded.

Usage: python3 tools/fn_pool_growth.py [--res DIR] [--legs GLOB] [--json OUT]
"""

import argparse
import csv
import glob
import json
import os
import re
import statistics

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ULID = re.compile(r"^[0-9A-Z]{26}$")


def run_row(d, run):
    rd = os.path.join(d, run)
    s = json.load(open(os.path.join(rd, "summary.json")))
    t0 = s["attribution"]["window_start_epoch"]
    t1 = s["attribution"]["window_end_epoch"]
    born = {}
    for r in csv.DictReader(open(os.path.join(rd, "samples_raw.csv"))):
        n = r["container"]
        if not ULID.match(n) or not r.get("born_epoch"):
            continue
        born.setdefault(n, float(r["born_epoch"]))
    before = sum(1 for b in born.values() if b < t0)
    inside = sorted(b - t0 for b in born.values() if t0 <= b <= t1)
    reqs = list(csv.DictReader(open(os.path.join(rd, "hey.csv"))))
    starts = {}
    for r in reqs:
        b = int(r["burst"])
        starts[b] = min(starts.get(b, 1e9), float(r["offset"]))
    bad = [r for r in reqs if not r["status-code"].startswith("2")]
    per_burst = {}
    for r in bad:
        per_burst[int(r["burst"])] = per_burst.get(int(r["burst"]), 0) + 1
    first = inside[0] if inside else None
    done = [float(r["offset"]) + float(r["response-time"]) for r in bad]
    ok_rt = [float(r["response-time"]) for r in reqs if r["status-code"].startswith("2")]
    return {
        "run": run,
        "pool_before_window": before,
        "born_in_window": len(inside),
        "first_birth_s": round(first, 3) if first is not None else None,
        "last_birth_s": round(inside[-1], 3) if inside else None,
        "births_by_burst": _by_burst(inside, starts),
        "errors": len(bad),
        "error_codes": sorted({r["status-code"] for r in bad}),
        "errors_per_burst": [per_burst.get(b, 0) for b in range(max(per_burst) + 1)] if per_burst else [],
        "errors_done_before_first_birth": (sum(1 for x in done if x < first) if first is not None else None),
        "error_median_rt_ms": round(statistics.median(float(r["response-time"]) for r in bad) * 1000, 1) if bad else None,
        "success_median_rt_ms": round(statistics.median(ok_rt) * 1000, 1) if ok_rt else None,
    }


def _by_burst(inside, starts):
    """Births counted against the burst whose start most recently preceded them."""
    order = sorted(starts.items(), key=lambda kv: kv[1])
    out = {}
    for t in inside:
        b = max((k for k, v in order if v <= t), default=0)
        out[b] = out.get(b, 0) + 1
    return [out.get(b, 0) for b in range(max(out) + 1)] if out else []


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--res", default=os.path.join(REPO, "results"))
    ap.add_argument("--legs", default="fn_cpubound_lock_burst*_fn_b*")
    ap.add_argument("--json")
    a = ap.parse_args()
    out = []
    for d in sorted(glob.glob(os.path.join(a.res, a.legs))):
        acc_f = os.path.join(d, "acceptance.json")
        usable = set(json.load(open(acc_f)).get("usable_runs", [])) if os.path.exists(acc_f) else set()
        for rd in sorted(glob.glob(os.path.join(d, "run_*")), key=lambda p: int(p.rsplit("_", 1)[1])):
            run = os.path.basename(rd)
            if not os.path.exists(os.path.join(rd, "samples_raw.csv")):
                continue
            row = run_row(d, run)
            row["leg"] = os.path.basename(d).replace("fn_cpubound_lock_", "")
            row["usable"] = run in usable
            out.append(row)
    for r in out:
        print("%-28s %-6s %-6s before %3d born %3d first %6s s errors %3d %s before-first-birth %s"
              % (r["leg"], r["run"], "use" if r["usable"] else "-", r["pool_before_window"],
                 r["born_in_window"], r["first_birth_s"], r["errors"], r["errors_per_burst"],
                 r["errors_done_before_first_birth"]))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
