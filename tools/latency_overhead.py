"""Post hoc (runbook §34.3): the platform's fee as added latency, beside its fee as CPU. Run level.

Ilúvatar (HPDC '23) reports control-plane overhead as latency added to an invocation; this study
reports it as CPU. Both can be read from the same runs. The CPU-bound handler spins for 5 ms of
wall time, so per usable run
    added latency  = client p50 latency - 5 ms      (load generator, loopback and platform path)
    cp CPU         = control-plane CPU-s / successes (ms per invocation, as Part A T2)
Added latency is an upper bound on the platform's share of the wait: it also holds the load
generator and the kernel's loopback path, which are the same for every platform, so differences
between platforms are the robust part. hey reports latency to 0.1 ms. Legs with any
host_saturated run are marked and left out of the reading (rule 7). At c > 1, added latency also
holds queueing behind other requests, which is not a per-request fee.

Usable runs: the leg's acceptance.json `usable_runs` when it exists, else the runs after the
session's discard_warmup (Part A predates acceptance.json).

Usage: python3 tools/latency_overhead.py --res DIR --session final_ [--session owlog29_] [--json OUT]
"""

import argparse
import glob
import json
import os
import statistics

HANDLER_MS = 5.0


def usable_runs(res, outdir, disc):
    d = os.path.join(os.path.dirname(res), outdir) if not os.path.isabs(outdir) else outdir
    if not os.path.isdir(d):
        d = os.path.join(res, os.path.basename(outdir))
    acc = os.path.join(d, "acceptance.json")
    if os.path.exists(acc):
        names = json.load(open(acc))["usable_runs"]
    else:
        n = len(glob.glob(os.path.join(d, "run_*")))
        names = ["run_%d" % i for i in range(disc + 1, n + 1)]
    return [json.load(open(os.path.join(d, r, "summary.json"))) for r in names]


def legs(res, session):
    out = []
    for f in sorted(glob.glob(os.path.join(res, "lock_session_%stier1*" % session, "lock_summary.json"))):
        d = json.load(open(f))
        stamp, disc = d["session"]["stamp"], int(d["session"]["discard_warmup"])
        tail = stamp.split("tier1", 1)[1]
        c = int(tail[2:].split("_")[0]) if tail.startswith("ow") else int(tail[1:].split("_")[0])
        for plat, v in d["platforms"].items():
            runs = usable_runs(res, v["outdir"], disc)
            if not runs:
                continue
            lat = [r["latency_ms"]["p50"] - HANDLER_MS for r in runs]
            cp = [r["cpu_sec"]["control_plane"] / r["successes"] * 1000 for r in runs]
            out.append({
                "stamp": stamp, "platform": plat, "c": c, "usable_runs": len(runs),
                "saturated_runs": sum(bool(r.get("host_saturated")) for r in runs),
                "added_latency_ms": round(statistics.median(lat), 2),
                "added_latency_range_ms": [round(min(lat), 2), round(max(lat), 2)],
                "cp_ms_inv": round(statistics.median(cp), 3),
                "cp_range_ms_inv": [round(min(cp), 3), round(max(cp), 3)],
            })
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--res", required=True)
    ap.add_argument("--session", action="append", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    rows = [r for s in a.session for r in legs(a.res, s)]
    for r in rows:
        print("%-28s %-9s c=%d n=%d sat=%d  added latency %.2f ms  cp %.3f ms/inv" % (
            r["stamp"], r["platform"], r["c"], r["usable_runs"], r["saturated_runs"],
            r["added_latency_ms"], r["cp_ms_inv"]))
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        json.dump({"handler_ms": HANDLER_MS, "sessions": a.session, "legs": rows},
                  open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
