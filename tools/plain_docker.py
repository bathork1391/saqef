#!/usr/bin/env python3
"""W5 plain-Docker target (runbook §36): the W5 handler in ONE plain container, no platform,
called directly by hey. Descriptive, not gated: it gives the in-session floor that the
platform legs are compared against (platform - plain = the platform fee per request, CPU and
latency), measured with the harness's own readers.

The container runs the Knative handler image (KNATIVE_FUNCTION/app.py is a self-contained
HTTP server), built by `tools/workload.sh build` from whichever W5 arm is swapped in. One
Python process serves one request at a time, so the target is measured at c = 1 only.

Per run: the container's cgroup CPU, host busy CPU (/proc/stat), RAPL package energy and
hey's per-request latency over exactly TOTAL requests. After the runs, a 60 s idle probe
with the container still up gives the idle rates that are subtracted, as on the platform
legs (runbook §36: Equations 3, 5 and 6 of the paper).

  python3 tools/plain_docker.py --arm spin --out results/io_plain_spin_c1
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import saqef_harness as H  # noqa: E402

NAME = "saqef-plain"
TICK = os.sysconf("SC_CLK_TCK")


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, repr(e)


def snap(cdir):
    hb = H.host_cpu_busy_total()
    return {"t": time.monotonic(), "fn": H.read_cpu_cumulative(cdir),
            "host": hb[0] / TICK if hb else None, "e": H.rapl_energy()}


def delta(a, b):
    e, flag = H.rapl_correct_wrap(None if a["e"] is None or b["e"] is None else b["e"] - a["e"])
    return {"T": b["t"] - a["t"], "fn": b["fn"] - a["fn"], "host": b["host"] - a["host"],
            "e": e, "rapl_wrap": flag}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=("spin", "sleep", "disk", "mixed"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--image", default="localhost:5000/saqef/kn-hello:0.0.1")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--total", type=int, default=3000)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=200)
    ap.add_argument("--probe", type=int, default=60)
    ap.add_argument("--quiet-window", type=float, default=20.0)
    ap.add_argument("--quiet-max", type=float, default=15.0)
    a = ap.parse_args()
    if os.path.exists(a.out):
        sys.exit("refusing to overwrite %s" % a.out)
    url = "http://127.0.0.1:%d/" % a.port

    sh("docker rm -f %s" % NAME)
    r = sh("docker run -d --name %s -p 127.0.0.1:%d:8080 %s" % (NAME, a.port, a.image))
    if r.returncode != 0:
        sys.exit("docker run failed: %s" % r.stderr.strip())
    cid = r.stdout.strip()
    try:
        for _ in range(60):
            if get(url)[0] == 200:
                break
            time.sleep(0.5)
        arms = [re.search(r"io=(\w+) us=\d+", get(url)[1] or "") for _ in range(20)]
        got = sorted({m.group(1) if m else None for m in arms}, key=str)
        if got != [a.arm]:
            sys.exit("plain container serves %r, not io=%s" % (got, a.arm))
        cdir = H.container_cgroup_dir(cid)
        if not cdir or H.read_cpu_cumulative(cdir) is None:
            sys.exit("no cgroup CPU counter for the plain container")
        H.run_hey(url, a.warmup, a.concurrency)

        runs = []
        for i in range(1, a.repeat + 1):
            busy, _ = H.ambient_load_check(a.quiet_window, a.quiet_max, quiet_gate=False)
            s0 = snap(cdir)
            q = H.run_hey(url, a.total, a.concurrency, deadline_s=600)
            s1 = snap(cdir)
            if q is None:
                runs.append({"run": i, "error": "hey failed"})
                continue
            d = delta(s0, s1)
            runs.append({"run": i, "quiet_busy_pct": busy, "window_s": d["T"], "ok": q["ok"],
                         "requests": q["requests"], "rps": q["rps"], "p50_ms": q["p50"],
                         "p99_ms": q["p99"], "fn_cpu_s": d["fn"], "host_cpu_s": d["host"],
                         "energy_j": d["e"], "rapl_wrap": d["rapl_wrap"]})
            print("run %d: ok %d/%d p50 %.2f ms fn %.3f s host %.3f s energy %s J (quiet %.1f%%)"
                  % (i, q["ok"], q["requests"], q["p50"], d["fn"], d["host"], d["e"], busy or -1))

        # idle probe: same container, no requests
        p0 = snap(cdir)
        time.sleep(a.probe)
        idle = delta(p0, snap(cdir))
        r_fn = idle["fn"] / idle["T"]
        u_idle = (idle["host"] - idle["fn"]) / idle["T"]
        p_idle = idle["e"] / idle["T"] if idle["e"] is not None else None
        for x in runs:
            if "error" in x or not x["ok"]:
                continue
            n, T = x["ok"], x["window_s"]
            x["fn_ms_inv"] = 1000.0 * x["fn_cpu_s"] / n
            x["fn_ms_inv_idle_sub"] = 1000.0 * (x["fn_cpu_s"] - r_fn * T) / n
            x["host_outside_ms_inv_idle_sub"] = 1000.0 * (x["host_cpu_s"] - x["fn_cpu_s"] - u_idle * T) / n
            x["mj_inv_probe"] = (1000.0 * (x["energy_j"] - p_idle * T) / n
                                 if x["energy_j"] is not None and p_idle is not None else None)

        def med(k):
            v = [x[k] for x in runs if x.get(k) is not None]
            return statistics.median(v) if v else None

        out = {"target": "plain-docker", "arm": a.arm, "image": a.image,
               "container_id": cid, "concurrency": a.concurrency, "total": a.total,
               "repeat": a.repeat, "idle_probe": {"window_s": idle["T"], "fn_cores": r_fn,
               "host_outside_cores": u_idle, "package_w": p_idle},
               "runs": runs,
               "median": {k: med(k) for k in ("rps", "p50_ms", "p99_ms", "fn_ms_inv",
                          "fn_ms_inv_idle_sub", "host_outside_ms_inv_idle_sub", "mj_inv_probe")},
               "note": "descriptive, not gated (runbook §36); same session as the W5 platform legs"}
        os.makedirs(a.out)
        json.dump(out, open(os.path.join(a.out, "summary.json"), "w"), indent=2)
        print("plain %s c=%d: %s" % (a.arm, a.concurrency, json.dumps(out["median"])))
    finally:
        sh("docker rm -f %s" % NAME)


if __name__ == "__main__":
    main()
