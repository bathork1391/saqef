#!/usr/bin/env python3
"""Snapshot per-thread CPU of the OpenWhisk standalone JVM (runbook 24.8 / 25.5).

OpenWhisk standalone is ONE container running ONE JVM, so container-level attribution
cannot say which part of its control plane burns the CPU. Linux names JVM threads
(pthread names, 15 chars), so cumulative utime/stime per thread, snapshotted every few
seconds, is enough to rebuild a per-subsystem breakdown offline against the
thread->subsystem map pre-registered in 25.5.

Cost: one /proc read per thread per interval, no attach, no agent, nothing inside the
JVM. Runs on the host, outside every container, so it is host overhead and never enters
the cp/fn share.

  sudo python3 tools/jvm_thread_sampler.py --out results/x/jvm_threads.csv [--container openwhisk]

Waits up to --wait-s for the container to appear, samples until it is gone or SIGTERM.
"""
import argparse
import csv
import os
import signal
import subprocess
import sys
import time

CLK = os.sysconf("SC_CLK_TCK")
STOP = False


def _stop(*_):
    global STOP
    STOP = True


def container_pid(name):
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Pid}}", name],
                       capture_output=True, text=True)
    try:
        pid = int(r.stdout.strip())
    except ValueError:
        return None
    return pid or None


def children(pid):
    """All descendant pids of pid (one /proc scan)."""
    parent = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % d) as f:
                parent[int(d)] = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
    out, frontier = [], [pid]
    while frontier:
        p = frontier.pop()
        out.append(p)
        frontier.extend(c for c, pp in parent.items() if pp == p)
    return out


def java_pids(root):
    pids = []
    for p in children(root):
        try:
            with open("/proc/%d/comm" % p) as f:
                if f.read().strip() == "java":
                    pids.append(p)
        except OSError:
            continue
    return pids


def snapshot(pid):
    rows = []
    base = "/proc/%d/task" % pid
    for tid in os.listdir(base):
        try:
            with open("%s/%s/comm" % (base, tid)) as f:
                comm = f.read().strip()
            with open("%s/%s/stat" % (base, tid)) as f:
                fields = f.read().rsplit(")", 1)[1].split()
            rows.append((int(tid), comm, int(fields[11]) / CLK, int(fields[12]) / CLK))
        except (OSError, IndexError, ValueError):
            continue  # thread exited between listdir and read
    # Reaped children (cutime/cstime): the invoker shells out to the docker CLI per
    # container operation; those processes are short-lived and never appear as JVM
    # threads, but the kernel credits their CPU to the parent once they are waited for.
    try:
        with open("/proc/%d/stat" % pid) as f:
            fields = f.read().rsplit(")", 1)[1].split()
        rows.append((-1, "<children>", int(fields[13]) / CLK, int(fields[14]) / CLK))
    except (OSError, IndexError, ValueError):
        pass
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--container", default="openwhisk")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--wait-s", type=float, default=900.0)
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    t0 = time.time()
    pids = []
    while not STOP and time.time() - t0 < a.wait_s:
        root = container_pid(a.container)
        if root:
            pids = java_pids(root)
            if pids:
                break
        time.sleep(2)
    if not pids:
        print("jvm_thread_sampler: no java process in container %r after %.0fs"
              % (a.container, time.time() - t0), file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_unix", "pid", "tid", "comm", "utime_s", "stime_s"])
        while not STOP:
            ts = round(time.time(), 3)
            alive = [p for p in pids if os.path.exists("/proc/%d" % p)]
            if not alive:
                break
            for p in alive:
                for tid, comm, ut, st in snapshot(p):
                    w.writerow([ts, p, tid, comm, ut, st])
            f.flush()
            for _ in range(int(a.interval * 10)):
                if STOP:
                    break
                time.sleep(0.1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
