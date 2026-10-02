#!/usr/bin/env python3
"""Attribute the OpenWhisk host-side CPU decay to a process (runbook 24.2 action item 1).

WHY THIS EXISTS. Every OW tier-1 leg decays monotonically run over run -- 58.5 -> 35.1 rps at c=1,
and worse at c=4 and c=8, on two different days -- while the measured stack does constant work:
cpu_sec.control_plane settles at ~59 CPU-s and cpu_sec.function at ~16.9 CPU-s and stays there, and
latency is flat WITHIN each run but steps up BETWEEN runs. What grows is host CPU that belongs to no
container the harness tracks: host_overhead_cpu_sec climbs 48 -> 129 CPU-s across a leg while total
host busy cores FALL, because wall grows faster than host CPU-s.

The harness already cleared itself and the leftover Knative stack by measurement (the cgroup sampler
costs 0.038 cores; Knative idles at 0.013 cores against a 0.167-core idle host), and the container
set is flat at 26 with unclassified_cpu_s at 0.53-0.84 s, so every container process is already
charged to cp or fn. Standalone OpenWhisk is a single Java process, so nginx/etcd are not in play.
That leaves host-side consumers: dockerd, containerd, or the kernel.

The leading suspect is dockerd's json-file driver. A json-file append is O(1) per write, so it does
not slow down as a file grows on its own -- which means the decay requires CUMULATIVE log volume,
i.e. the wsk0_* action containers persisting across repeats and stacking 3000 activations of logs
per repeat. The container IDs are byte-identical in all five runs, which is consistent. But the
2026-10-01 legs' overhead RATE plateaus at 3.25-3.31 cores, and pure log growth does not obviously
produce a plateau, so this is a hypothesis and not a conclusion.

Two arms, one deploy each:
  baseline   untouched. Reproduces the decay; gives the per-process CPU attribution.
  truncate   a daemon truncates the wsk0_* LogPaths every TRUNCATE_S. Isolates log VOLUME without
             recreating the action containers -- recreating them would reset the container cache and
             change cold-start latency, confounding the very thing being measured. In this arm the
             claim is not "faster", it is "no longer decays"; a sparse-file side effect could make
             appends cheaper, so only the absence of decay is informative.

Whatever the arm, the sampler attributes host CPU per interval to dockerd / containerd / k3s-server /
kernel-softirq / unaccounted, so the mechanism is identified even when the A/B is ambiguous.

USAGE
  python3 tools/ow_host_attrib_ab.py --dry-run
  sudo python3 tools/ow_host_attrib_ab.py --stamp owab1 --arm baseline
  sudo python3 tools/ow_host_attrib_ab.py --stamp owab1 --arm truncate
"""
import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

CLK = os.sysconf("SC_CLK_TCK")
NCPU = os.cpu_count() or 8
TRACK_PROCS = ("dockerd", "containerd", "k3s-server")

# Set by a background thread (Truncator or sampler) that died. A dead truncator turns the
# truncate arm into an untruncated run with the wrong label, so the leg must not complete:
# the bench is killed and the arm is marked invalid instead of reporting a result.
ABORT = threading.Event()
ABORT_REASON = []


def abort(reason):
    ABORT_REASON.append(reason)
    ABORT.set()
    print("\n!!! ABORT: %s" % reason.strip().splitlines()[0], flush=True)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def run_abortable(cmd, grace_s=30):
    """subprocess.run that kills the child's whole process group when ABORT is set."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    out, err = [], []
    readers = [threading.Thread(target=lambda s, b: b.append(s.read()), args=(s, b), daemon=True)
               for s, b in ((p.stdout, out), (p.stderr, err))]
    for t in readers:
        t.start()
    try:
        while p.poll() is None:
            if ABORT.wait(1.0):
                print("!!! killing bench (pgid %d)" % p.pid, flush=True)
                _killpg(p, signal.SIGTERM)
                try:
                    p.wait(grace_s)
                except subprocess.TimeoutExpired:
                    _killpg(p, signal.SIGKILL)
                    p.wait()
                break
    except BaseException:
        _killpg(p, signal.SIGTERM)
        raise
    for t in readers:
        t.join(5)
    return subprocess.CompletedProcess(cmd, p.returncode, "".join(out), "".join(err))


def _killpg(p, sig):
    try:
        os.killpg(p.pid, sig)
    except ProcessLookupError:
        pass


def pidof(name):
    """pidof(1) is not in every minimal image; scan /proc instead."""
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/comm" % pid) as f:
                if f.read().strip() == name:
                    out.append(int(pid))
        except OSError:
            pass
    return out


def proc_cpu_jiffies(names=None):
    """utime+stime in jiffies, summed over all instances of each tracked process.

    `names` defaults to TRACK_PROCS; pass an explicit list to attribute the
    operator's own footprint separately from the daemon set."""
    totals = {}
    for name in (TRACK_PROCS if names is None else names):
        s = 0
        for pid in pidof(name):
            try:
                with open("/proc/%d/stat" % pid) as f:
                    fld = f.read().rsplit(")", 1)[1].split()
                s += int(fld[11]) + int(fld[12])
            except (OSError, IndexError, ValueError):
                pass
        totals[name] = s
    return totals


def proc_stat_fields():
    """(busy_jiffies, softirq_jiffies) from /proc/stat's cpu aggregate line."""
    with open("/proc/stat") as f:
        fld = f.readline().split()[1:]
    v = [int(x) for x in fld]
    idle = v[3] + v[4]
    softirq = v[6] if len(v) > 6 else 0
    return sum(v) - idle, softirq


def container_cpu_s():
    """Sum of every discoverable container's cumulative cpu.stat, in seconds."""
    import saqef_harness as H
    found = H.discover_cgroup_container_dirs() or []
    total = 0.0
    for _cid, cdir in found:
        v = H.read_cpu_cumulative(cdir)
        if v:
            total += v
    return total


def wsk_logs():
    """{basename: bytes} for the wsk0_* action containers' json-file logs."""
    sizes = {}
    for line in run(["docker", "ps", "--filter", "name=wsk0_",
                     "--format", "{{.ID}} {{.Names}}"]).stdout.splitlines():
        cid, _, name = line.strip().partition(" ")
        if not cid or not name:
            continue
        r = run(["docker", "inspect", "-f", "{{.LogPath}}", cid])
        path = r.stdout.strip()
        if not path:
            continue
        try:
            sizes[name] = os.path.getsize(path)
        except OSError:
            pass
    return sizes


class Truncator(threading.Thread):
    """Keeps wsk0_* log volume bounded. Files are opened O_APPEND by dockerd, so
    truncating to 0 is safe: subsequent writes land at offset 0 again."""

    daemon = True

    def __init__(self, every_s):
        super().__init__()
        self.every_s = every_s
        self._stop = threading.Event()
        self.ticks = 0
        self.truncations = 0

    def run(self):
        try:
            while not self._stop.wait(self.every_s):
                for cid in _wsk_container_ids():
                    path = _log_path(cid)
                    if path:
                        try:
                            with open(path, "w"):
                                pass
                            self.truncations += 1
                        except OSError:
                            pass
                self.ticks += 1
        except BaseException:
            abort("truncator died:\n" + traceback.format_exc())

    def stop(self):
        self._stop.set()


def _wsk_container_ids():
    out = []
    for line in run(["docker", "ps", "--filter", "name=wsk0_", "--format", "{{.ID}}"]).stdout.split():
        if line.strip():
            out.append(line.strip())
    return out


def _log_path(cid):
    r = run(["docker", "inspect", "-f", "{{.LogPath}}", cid])
    return r.stdout.strip() or None


def sampler(outdir, every_s, arm):
    try:
        return _sampler(outdir, every_s, arm)
    except BaseException:
        abort("sampler died:\n" + traceback.format_exc())


def _sampler(outdir, every_s, arm):
    rows = []
    prev = {"t": time.time(), "proc": proc_cpu_jiffies(),
            "stat": proc_stat_fields(), "cont": container_cpu_s()}
    trunc = Truncator(every_s) if arm == "truncate" else None
    sampler._trunc = trunc
    if trunc:
        trunc.start()
    while not getattr(sampler, "_stop", threading.Event()).is_set():
        time.sleep(every_s)
        # Backstop for a truncator that stopped without raising.
        if trunc and not trunc.is_alive() and not ABORT.is_set():
            abort("truncator thread is no longer alive (ticks=%d)" % trunc.ticks)
        now = {"t": time.time(), "proc": proc_cpu_jiffies(),
               "stat": proc_stat_fields(), "cont": container_cpu_s()}
        dt = now["t"] - prev["t"]
        if dt <= 0:
            prev = now
            continue
        r = {"t": round(now["t"], 3), "dt_s": round(dt, 3)}
        for k in TRACK_PROCS:
            r[k + "_cores"] = round(
                (now["proc"][k] - prev["proc"][k]) / CLK / dt, 4)
        r["host_busy_cores"] = round((now["stat"][0] - prev["stat"][0]) / CLK / dt, 4)
        r["softirq_cores"] = round(
            (now["stat"][1] - prev["stat"][1]) / CLK / dt, 4)
        cont = now["cont"] - prev["cont"]
        r["container_cores"] = round(cont / dt, 4)
        tracked = sum(r[k + "_cores"] for k in TRACK_PROCS)
        # host - tracked procs - containers = kernel + anything else not named here.
        r["unaccounted_cores"] = round(r["host_busy_cores"] - tracked - r["container_cores"], 4)
        rows.append(r)
        prev = now
    if trunc:
        trunc.stop()
    path = os.path.join(outdir, "host_attrib.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


def own_footprint(window=10.0):
    """Measure the CPU this diagnostic's own operator costs, in cores.

    Running the A/B from an agent session puts ~0.5 core outside every tracked
    container, which lands straight in the unaccounted_cores column -- the exact
    signal under investigation. So measure it instead of ignoring it, and record it
    next to the attribution CSV. It is an additive offset common to both arms, so
    the baseline-vs-truncate *contrast* survives it even when the absolute
    unaccounted number does not."""
    names = ["opencode", "claude", "ptyxis", "chrome", "gnome-shell"]
    j0 = proc_cpu_jiffies(names)
    busy0 = proc_stat_fields()[0]
    t0 = time.time()
    time.sleep(window)
    busy1 = proc_stat_fields()[0]
    dt = time.time() - t0
    j1 = proc_cpu_jiffies(names)
    hz = float(CLK)
    out = {"window_s": round(dt, 1),
           "host_busy_cores": round((busy1 - busy0) / hz / dt, 3),
           "by_name_cores": {}}
    for n in names:
        a, b = j0.get(n), j1.get(n)
        if a and b:
            out["by_name_cores"][n] = round((b - a) / hz / dt, 4)
    out["operator_cores"] = round(
        sum(v for k, v in out["by_name_cores"].items() if k in ("opencode", "claude")), 4)
    return out


def run_leg(args, arm):
    stamp = "%s_%s" % (args.stamp, arm)
    out = os.path.join(REPO, "results", "openwhisk_cpubound_lock_%s" % stamp)
    instdir = os.path.join(REPO, "results", "ow_host_attrib_%s" % stamp)
    os.makedirs(instdir, exist_ok=True)
    saqef = [sys.executable, os.path.join(REPO, "saqef")]

    if os.path.exists(out):
        sys.exit("outdir exists, refusing to clobber: %s" % out)

    def step(name, cmd, abortable=False):
        """Run a saqef subcommand, persist its output, and surface a failure loudly.

        The first version of this script swallowed every return code, so a bench that
        died in 20s reported as a completed leg and the real error was lost. Any step
        that is not teardown aborts the arm. An abortable step is killed (whole process
        group, so the load generator goes too) as soon as ABORT is set."""
        r = run_abortable(cmd) if abortable else run(cmd)
        with open(os.path.join(instdir, "%s.log" % name), "w") as f:
            f.write(r.stdout or "")
            f.write("\n--- stderr ---\n")
            f.write(r.stderr or "")
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-12:]
            print("!!! [%s] %s FAILED rc=%d" % (arm, name, r.returncode), flush=True)
            for line in tail:
                print("    | %s" % line, flush=True)
            print("    full output: %s/%s.log" % (instdir, name), flush=True)
        return r.returncode

    t0 = time.time()
    print(">>> [%s] deploy" % arm, flush=True)
    if step("deploy", saqef + ["deploy", "--platform", "openwhisk"]) != 0:
        sys.exit("deploy failed for arm %s" % arm)
    try:
        print(">>> [%s] verify" % arm, flush=True)
        if step("verify", saqef + ["verify", "--platform", "openwhisk"]) != 0:
            sys.exit("verify failed for arm %s" % arm)

        print(">>> [%s] own footprint (idle window)" % arm, flush=True)
        with open(os.path.join(instdir, "own_footprint.json"), "w") as f:
            json.dump(own_footprint(), f, indent=2)

        sampler._stop = threading.Event()
        th = threading.Thread(target=sampler,
                              args=(instdir, args.interval, arm), daemon=True)
        th.start()
        print(">>> [%s] bench: total=%d concurrency=%d duration=%d repeat=%d arm=%s"
              % (arm, args.total, args.concurrency, args.duration, args.repeat, arm),
              flush=True)
        rc = step("bench", saqef + [
            "run", "--platform", "openwhisk", "--total", str(args.total),
            "--concurrency", str(args.concurrency), "--duration", str(args.duration),
            "--repeat", str(args.repeat), "--idle-w", args.idle_w, "--out", out]
            + (["--no-quiet-gate"] if args.no_quiet_gate else []), abortable=True)
        sampler._stop.set()
        th.join(timeout=30)
        with open(os.path.join(instdir, "bench_rc"), "w") as f:
            f.write("%d\n" % rc)

        trunc = getattr(sampler, "_trunc", None)
        if trunc is not None:
            trunc.stop()
            with open(os.path.join(instdir, "truncator.json"), "w") as f:
                json.dump({"ticks": trunc.ticks, "truncations": trunc.truncations,
                           "alive_at_end": trunc.is_alive()}, f, indent=2)
            if trunc.ticks == 0 and not ABORT.is_set():
                abort("truncator never completed a tick")
        if ABORT.is_set():
            reason = "\n\n".join(ABORT_REASON)
            with open(os.path.join(instdir, "ARM_INVALID"), "w") as f:
                f.write(reason + "\n")
            if os.path.exists(out):
                with open(os.path.join(out, "ARM_INVALID"), "w") as f:
                    f.write(reason + "\n")
            print("!!! [%s] ARM INVALID -- do not use %s" % (arm, out), flush=True)
            print(reason, flush=True)
            sys.exit("arm %s aborted; see %s/ARM_INVALID" % (arm, instdir))

        print(">>> [%s] log sizes at end of leg" % arm)
        with open(os.path.join(instdir, "wsk_log_sizes.json"), "w") as f:
            json.dump(wsk_logs(), f, indent=2)
        if rc != 0:
            sys.exit("bench failed for arm %s (rc=%d); see %s/bench.log" % (arm, rc, instdir))
    finally:
        print(">>> [%s] teardown" % arm, flush=True)
        step("teardown", saqef + ["teardown", "--platform", "openwhisk"])
    print("[%s] done in %.1f min -> %s" % (arm, (time.time() - t0) / 60.0, instdir))


def report(stamp, arms):
    print("\n=== host attribution (%s) ===" % stamp)
    for arm in arms:
        p = os.path.join(REPO, "results", "ow_host_attrib_%s_%s" % (stamp, arm),
                         "host_attrib.csv")
        if not os.path.exists(p):
            continue
        rows = list(csv.DictReader(open(p)))
        f = lambda k: sum(float(r[k]) for r in rows) / len(rows)
        print("\n[%s] mean cores over %d intervals" % (arm, len(rows)))
        for k in ("host_busy_cores", "container_cores", "dockerd_cores",
                  "containerd_cores", "k3s-server_cores", "softirq_cores",
                  "unaccounted_cores"):
            print("   %-22s %6.3f" % (k, f(k)))
        print("   -> per-repeat throughput:")
        out = os.path.join(REPO, "results",
                           "openwhisk_cpubound_lock_%s_%s" % (stamp, arm))
        rps = []
        for i in range(1, args_repeat + 1):
            fp = os.path.join(out, "run_%d" % i, "summary.json")
            if os.path.exists(fp):
                d = json.load(open(fp))
                rps.append((i, d["throughput_rps"],
                            d.get("host_overhead_cpu_sec", 0.0),
                            d["cpu_sec"]["control_plane"],
                            d["cpu_sec"]["function"]))
        for i, r_, ho, cp, fn in rps:
            print("      run_%-2d rps=%6.2f  host_ovh=%7.2f  cp=%6.2f  fn=%5.2f"
                  % (i, r_, ho, cp, fn))
        if len(rps) >= 3:
            drop = (rps[0][1] - rps[-1][1]) / rps[0][1] * 100.0
            print("      drift run_1..run_%d = %.1f%%" % (rps[-1][0], drop))


args_repeat = 5

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stamp", default="owab1")
    ap.add_argument("--arm", default="both", choices=["baseline", "truncate", "both"])
    ap.add_argument("--total", type=int, default=3000)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--duration", type=int, default=420)
    ap.add_argument("--repeat", type=int, default=6)
    ap.add_argument("--idle-w", default="4.882")
    ap.add_argument("--interval", type=float, default=2.0)
    # saqef_harness.py:2046 reserves --no-quiet-gate for "exploratory runs and the
    # contamination A/B tool only". This *is* that tool: you cannot have a quiet box
    # while running a diagnostic that attributes the box's own host CPU. The offset
    # is measured, not assumed -- see own_footprint().
    ap.add_argument("--no-quiet-gate", action="store_true",
                    help="pass --no-quiet-gate to the bench (exploratory A/B only)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    args_repeat = args.repeat
    arms = ["baseline", "truncate"] if args.arm == "both" else [args.arm]
    if args.dry_run:
        print("dry-run: would run arms %s, repeat=%d, concurrency=%d, total=%d"
              % (arms, args.repeat, args.concurrency, args.total))
        for a in arms:
            print("  results/openwhisk_cpubound_lock_%s_%s" % (args.stamp, a))
            print("  results/ow_host_attrib_%s_%s/host_attrib.csv" % (args.stamp, a))
        sys.exit(0)
    for a in arms:
        run_leg(args, a)
    report(args.stamp, arms)