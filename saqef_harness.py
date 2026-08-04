#!/usr/bin/env python3
"""
SAQEF Harness - Sustainability-Aware QoS Evaluation Framework.

Unified measurement window: QoS (load generator) + per-container CPU/mem
(docker stats) + optional RAPL ground truth, collected synchronously.

Stdlib only. Works on: bare-metal Linux, WSL2, Killercoda Ubuntu, Docker Desktop.

Usage:
  python3 saqef_harness.py --check
  python3 saqef_harness.py --verify --url http://localhost:8080/t/app1/hello \
      --platform fn --cp-containers fnserver --verify-n 100 --verify-budget-ms 5
  python3 saqef_harness.py --url http://localhost:8080/t/app1/hello \
      --platform fn --cp-containers fnserver \
      --total 3000 --concurrency 20 --duration 60 --repeat 5 \
      --sampler cgroup --loadgen py --delta-check \
      --outdir results/fn_default

Outputs (into --outdir):
  summary.json   - all KPIs, energy/carbon, validation, QoS
  samples.csv    - per-sample per-container CPU%/mem
  requests.csv   - per-request latency/status (python loadgen only)
  hey.csv        - hey raw CSV output (--loadgen hey only)
  verify.json    - --verify report
"""

import argparse
import base64
import csv
import io
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------------------
# Power / carbon model constants (Caribou, SOSP'24; Hidden Carbon Footprint, SoCC'24)
P_BUSY_CORE_W = 3.5          # W per fully-busy core (dynamic portion)
P_IDLE_BASE_W = 30.0         # machine idle baseline W (override with --idle-w)
PUE = 1.15                   # power usage effectiveness
CI_GCO2_PER_KWH = 150.0      # grid carbon intensity, gCO2/kWh (--ci)
DRAM_EMBODIED_G_PER_GB = 1390.0  # gCO2 per GB DRAM, embodied (1.39 kg/GB)
CPU_EMBODIED_G_PER_CORE = 653.0  # gCO2 per CPU core, embodied
LIFESPAN_YEARS = 5
SAMPLE_S = 1.0               # nominal sampling interval
RAPL_DIR = "/sys/class/powercap"


# ---------------------------------------------------------------------------
def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def docker_stats_once():
    """Return {name: (cpu_percent, mem_mb)} from one `docker stats` snapshot."""
    out = run("docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}'")
    if out.returncode != 0:
        return None
    res = {}
    for line in out.stdout.strip().splitlines():
        parts = line.split("|")
        if len(parts) < 4:
            continue
        name, cpu = parts[0], parts[1].strip().rstrip("%")
        mem = parts[2].strip().split()[0]
        try:
            cpu = float(cpu)
        except ValueError:
            cpu = 0.0
        res[name] = (cpu, mem_to_mb(mem))
    return res


def mem_to_mb(s):
    s = s.strip()
    mult = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "TiB": 1024 * 1024,
            "kB": 1 / 1000, "MB": 1, "GB": 1000, "TB": 1_000_000}
    for suf, m in mult.items():
        if s.endswith(suf):
            try:
                return float(s[: -len(suf)]) * m
            except ValueError:
                return 0.0
    return 0.0


def rapl_energy():
    """Return package energy in J, or None if RAPL unavailable."""
    p = os.path.join(RAPL_DIR, "intel-rapl:0", "energy_uj")
    try:
        with open(p) as f:
            return int(f.read().strip()) / 1e6  # uJ -> J
    except Exception:
        return None


def env_frequency():
    """Return (freq_mhz, scaling_governor) or (None, None)."""
    mhz, gov = None, None
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq") as f:
            mhz = int(f.read().strip()) / 1000.0  # kHz -> MHz
    except Exception:
        pass
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor") as f:
            gov = f.read().strip()
    except Exception:
        pass
    return mhz, gov


def host_cpu_ticks():
    """BUSY CPU ticks across all cores from /proc/stat, or None.
    Busy = user+nice+system+irq+softirq+steal (total minus idle and iowait);
    excludes guest/guest_nice which double-count user/system ticks."""
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        if len(parts) < 9:
            return None
        vals = [int(v) for v in parts[1:9]]
        return sum(vals) - vals[3] - vals[4]  # total - idle - iowait
    except Exception:
        return None


def host_saturated_flag(sat_pct):
    """QoS-contamination flag for a host_saturation_pct value.

    Enforces the documented rule (report §17, v9.4 item h): a run at >=85% host
    saturation is contention-contaminated -- its latency/throughput reflect
    scheduler competition, not platform overhead. host_plausible only checks the
    physical ceiling (<=105%), so this flag is what actually attaches the QoS
    caveat to the run's numbers in the summary JSON."""
    return sat_pct is not None and sat_pct >= 85.0


def steal_ticks():
    """Steal-time ticks from /proc/stat (noisy-neighbor visibility), or None."""
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        if len(parts) > 9:
            return int(parts[8])  # user nice system idle iowait irq softirq steal guest
    except Exception:
        pass
    return None


def cpu_count():
    try:
        with open("/proc/cpuinfo") as f:
            return sum(1 for line in f if line.startswith("processor"))
    except Exception:
        return None


def cgroup_cpu_quota():
    """Effective CPU quota as (quota, period) or None. v2: cpu.max; v1: quota/period."""
    for p in ("/sys/fs/cgroup/cpu.max",
              "/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "/sys/fs/cgroup/cpu/cpu.cfs_period_us"):
        try:
            with open(p) as f:
                v = f.read().strip()
                if v:
                    if p.endswith("cpu.max"):
                        parts = v.split()
                        if len(parts) == 2:
                            return parts
                    return v
        except Exception:
            pass
    return None


def docker_container_names():
    """Sorted names of running containers (audit trail), or [] if docker fails."""
    out = run("docker ps --format '{{.Names}}'")
    if out.returncode == 0:
        return sorted(x.strip() for x in out.stdout.splitlines() if x.strip())
    return []


def docker_inventory():
    """{name: (image, [labels])} for running containers (audit trail + allowlist
    classification), or {} if docker fails. Labels are parsed as a list of
    'key=value' strings from docker's {{.Labels}} (a,b=c format)."""
    out = run("docker ps --format '{{.Names}}\t{{.Image}}\t{{.Labels}}'")
    if out.returncode != 0:
        return {}
    inv = {}
    for line in out.stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 2 or not parts[0]:
            continue
        image = parts[1] if len(parts) > 1 else ""
        labels = [x.strip() for x in parts[2].split(",")] if len(parts) > 2 and parts[2] else []
        inv[parts[0]] = (image, labels)
    return inv


def _class_matches(name, image, labels, subs, img_subs, lbl_keys):
    """True if a container matches ANY of the name/image/label signals."""
    if subs and any(s in name.lower() for s in subs):
        return True
    if img_subs and image and any(s in image.lower() for s in img_subs):
        return True
    if lbl_keys:
        for k in lbl_keys:
            if any(lbl.split("=", 1)[0] == k for lbl in labels):
                return True
    return False


def iqr(values):
    """Interquartile range (Q3-Q1, linear interpolation), or None for <4 points."""
    if not values or len(values) < 4:
        return None
    s = sorted(values)

    def q(p):
        pos = (len(s) - 1) * p
        lo = int(pos)
        frac = pos - lo
        if lo + 1 < len(s):
            return s[lo] + frac * (s[lo + 1] - s[lo])
        return s[lo]

    return round(q(0.75) - q(0.25), 4)


def bootstrap_ci(values, n=1000, seed=1):
    """Bootstrap 95% CI on the median (stdlib only)."""
    if not values or n < 1:
        return None
    rng = random.Random(seed)
    medians = []
    for _ in range(n):
        res = [rng.choice(values) for _ in values]
        s = sorted(res)
        medians.append(s[len(s) // 2])
    medians.sort()
    return [round(medians[int(len(medians) * 0.025)], 4),
            round(medians[int(len(medians) * 0.975)], 4)]


def cv_pct(values):
    """Coefficient of variation in %, or None."""
    if not values or len(values) < 2:
        return None
    m = statistics.fmean(values)
    if m == 0:
        return None
    return round(statistics.stdev(values) / m * 100.0, 2)


# ---------------------------------------------------------------------------
def run_load(url, total, concurrency, timeout_s=10, deadline_s=None, headers=None, interarrival_ms=0.0):
    """Fire `total` requests with `concurrency` threads, hard-stopped at deadline_s.
    Optionally sleeps `interarrival_ms` between requests (cold-start experiments).
    Returns list of (ok, latency_s)."""
    per = total // concurrency
    results = []
    start = time.perf_counter()

    def worker(base):
        n = per
        if base == 0:
            n += total % concurrency
        for _ in range(n):
            if deadline_s is not None and time.perf_counter() - start > deadline_s:
                break
            t0 = time.perf_counter()
            ok = True
            try:
                req = urllib.request.Request(url, headers=headers) if headers else url
                with urllib.request.urlopen(req, timeout=timeout_s) as r:
                    r.read()
            except Exception:
                ok = False
            results.append((ok, time.perf_counter() - t0))
            if interarrival_ms:
                time.sleep(interarrival_ms / 1000.0)

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for i in range(concurrency):
            ex.submit(worker, i)
    return results


def run_hey(url, total, concurrency, deadline_s=None, headers=None, timeout_ms=30000):
    """Run hey (Go load generator) as a subprocess; keeps the harness's own CPU
    out of host accounting. Returns a results dict, or None if hey is missing/fails.
    QoS is computed from hey's per-request CSV rows: rps, latency percentiles,
    status distribution.

    NOTE (bug fixed here): mainline rakyll/hey has never had a JSON output mode.
    Per hey's own docs, "'csv' is the only supported alternative" to the default
    human-readable summary -- there is no -o json. Passing -o json (the previous
    behavior of this function) either falls through to the plain-text summary
    (unparseable as JSON) or, depending on build/flag-parsing quirks, produces
    other unparseable stdout -- both read to the caller as "hey is broken" even
    though the binary and the server are fine. We now request the one output
    mode hey actually documents and ships, "-o csv", and parse it ourselves.
    This also means we no longer depend on hey's built-in percentile/rps math,
    which is a wash -- we already need lat_points for cdf_compliance().

    Runs are COUNT-BOUND (-n total, exactly N requests) so windows are identical
    across platforms; deadline_s is a SAFETY cap only (subprocess timeout + a
    post-run wall assertion), because hey's -z/-n precedence varies by build and
    must not silently truncate a window."""
    if shutil.which("hey") is None:
        print("hey: binary not found on PATH (wanted --loadgen hey); falling back")
        return None
    # hey's -t is SECONDS per request (default 20), not milliseconds: passing
    # the raw ms value (30000) was silently a 30,000-second timeout. Convert.
    cmd = ["hey", "-n", str(total), "-c", str(concurrency),
           "-t", str(max(1, timeout_ms // 1000)), "-o", "csv"]
    if headers:
        for k, v in headers.items():
            cmd += ["-H", "%s: %s" % (k, v)]
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=(deadline_s or 0) + 120)
    except Exception as e:
        print("hey: subprocess failed (%s); falling back" % e)
        return None
    if proc.returncode != 0:
        print("hey failed (rc=%d): %s" % (
            proc.returncode,
            (proc.stderr or proc.stdout)[-300:].strip() or "(no output)"))
        return None
    try:
        # hey's CSV header (mainline): response-time,DNS+dialup,DNS,
        # Request-write,Response-delay,Response-read,status-code,offset
        # Normalize keys (lower, strip hyphens/spaces) so a header-casing
        # difference across hey builds/forks doesn't silently break parsing.
        reader = csv.DictReader(io.StringIO(proc.stdout))
        if reader.fieldnames is None:
            raise ValueError("no CSV header in hey output")
        norm = {fn: re.sub(r"[\s\-+]", "", fn).lower() for fn in reader.fieldnames}
        rt_key = next((fn for fn, n in norm.items() if n == "responsetime"), None)
        st_key = next((fn for fn, n in norm.items() if n == "statuscode"), None)
        off_key = next((fn for fn, n in norm.items() if n == "offset"), None)
        if rt_key is None or st_key is None:
            raise ValueError("expected columns not found; got %r" % (reader.fieldnames,))
        rows = [r for r in reader if r.get(rt_key) not in (None, "")]
        if not rows:
            raise ValueError("hey CSV had a header but zero data rows")
        lat_ms = sorted(float(r[rt_key]) * 1000.0 for r in rows)
        status = [str(r.get(st_key, "")) for r in rows]
        offsets = [float(r[off_key]) for r in rows if off_key and r.get(off_key) not in (None, "")]
    except Exception as e:
        print("hey: CSV parse failed (%s): %.200s" % (
            e, (proc.stdout or "(no output)").strip()))
        return None

    n = len(lat_ms)

    def pct(p):
        k = max(0, min(n - 1, int(round(p / 100.0 * (n - 1)))))
        return lat_ms[k]

    ok = sum(1 for s in status if s.startswith("2"))
    wall = max(offsets) if offsets else (sum(lat_ms) / 1000.0 / max(concurrency, 1))
    pct_map = {50: pct(50), 90: pct(90), 99: pct(99)}
    return {
        "ok": ok, "requests": n, "wall": wall,
        "p50": pct_map[50], "p90": pct_map[90], "p99": pct_map[99],
        "max": lat_ms[-1], "avg": sum(lat_ms) / n,
        "rps": (n / wall) if wall > 0 else 0.0,
        "errors": n - ok,
        "lat_points": sorted(pct_map.items()),
        "source": "hey",
        "raw": proc.stdout,
    }


def cdf_compliance(lat_points, slo_ms):
    """SLO compliance estimated by linear interpolation of hey's percentile points."""
    pts = sorted(lat_points)
    if not pts:
        return None
    if slo_ms <= pts[0][1]:
        return 0.0
    if slo_ms >= pts[-1][1]:
        return 1.0
    for i in range(len(pts) - 1):
        p0, l0 = pts[i]
        p1, l1 = pts[i + 1]
        if l0 <= slo_ms <= l1:
            frac = (slo_ms - l0) / (l1 - l0) if l1 > l0 else 1.0
            return (p0 + (p1 - p0) * frac) / 100.0
    return 1.0


def cp_cgroup_reader(cp_sub):
    """Return a callable reading cumulative CPU seconds of the control-plane
    container (first name matching cp_sub), or None if not mappable."""
    out = run("docker ps --format '{{.Names}}'")
    if out.returncode != 0:
        return None
    name = next((nm.strip() for nm in out.stdout.splitlines()
                 if any(s in nm.lower() for s in cp_sub)), None)
    if not name:
        return None
    cid = run("docker inspect -f '{{.Id}}' %s" % name).stdout.strip()
    d = container_cgroup_dir(cid)
    if not d:
        return None
    return lambda: read_cpu_cumulative(d)


# ---------------------------------------------------------------------------
def container_cgroup_dir(cid):
    """cgroup dir for a container, or None (cgroup v1 'cpu' or v2 unified)."""
    pid = run("docker inspect -f '{{.State.Pid}}' %s" % cid).stdout.strip()
    if not pid or not pid.isdigit():
        return None
    try:
        with open("/proc/%s/cgroup" % pid) as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) == 3:
                    if parts[1] in ("cpu", "cpu,cpuacct", "cpuacct"):
                        return "/sys/fs/cgroup/" + parts[1] + parts[2]
                    if parts[1] == "":
                        return "/sys/fs/cgroup" + parts[2]  # v2 unified
    except Exception:
        pass
    return None


def container_mem_cgroup_dir(cid):
    """cgroup dir for a container's memory controller, or None.
    v2: unified dir (same as cpu). v1: 'memory' controller hierarchy."""
    pid = run("docker inspect -f '{{.State.Pid}}' %s" % cid).stdout.strip()
    if not pid or not pid.isdigit():
        return None
    try:
        with open("/proc/%s/cgroup" % pid) as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) == 3:
                    if parts[1] == "":
                        return "/sys/fs/cgroup" + parts[2]  # v2 unified
                    if "memory" in parts[1]:
                        return "/sys/fs/cgroup/" + parts[1] + parts[2]
    except Exception:
        pass
    return None


def read_cpu_cumulative(cdir):
    """Cumulative CPU time (s) for a cgroup, or None."""
    try:
        p = os.path.join(cdir, "cpu.stat")
        if os.path.exists(p):  # cgroup v2
            for line in open(p):
                if line.startswith("usage_usec"):
                    return int(line.split()[1]) / 1e6
        p = os.path.join(cdir, "cpuacct.usage")
        if os.path.exists(p):  # cgroup v1
            return int(open(p).read().strip()) / 1e9
    except Exception:
        pass
    return None


def read_mem_mb(cdir):
    """Current memory usage (MB) for a cgroup, or 0.0 if unreadable."""
    try:
        p = os.path.join(cdir, "memory.current")
        if os.path.exists(p):  # cgroup v2
            return int(open(p).read().strip()) / (1024.0 ** 2)
        p = os.path.join(cdir, "memory.usage_in_bytes")
        if os.path.exists(p):  # cgroup v1
            return int(open(p).read().strip()) / (1024.0 ** 2)
    except Exception:
        pass
    return 0.0


def docker_sampler(samples, stop, first_sample, rescan_s=0.25):
    """Streaming `docker stats` sampler (~1 Hz). Samples: (t, {name: (cpu_pct, mem_mb)}).
    rescan_s is unused here (docker stats streams all containers continuously);
    it exists so start_sampler can pass the same arg tuple to either sampler."""
    proc = None
    try:
        proc = subprocess.Popen(
            ["docker", "stats", "--format",
             "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1)
    except Exception:
        pass
    if proc is None:
        return
    pending, seen = {}, set()

    def commit():
        nonlocal pending, seen
        if pending:
            samples.append((time.time(), dict(pending), "pct"))
            first_sample.set()
        pending, seen = {}, set()

    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        name = parts[0].strip()
        cpu = parts[1].strip().rstrip("%")
        mem = parts[2].strip().split()[0]
        try:
            cpu = float(cpu)
        except ValueError:
            cpu = 0.0
        if name in seen:
            commit()
        seen.add(name)
        pending[name] = (cpu, mem_to_mb(mem))
        if stop.is_set():
            break
    commit()
    try:
        proc.terminate()
    except Exception:
        pass


def cgroup_sampler(samples, stop, first_sample, rescan_s=0.25):
    """Direct cpu.stat + memory.current sampler. Stores RAW cumulative CPU
    seconds and current mem MB per container ('cum' samples); the consumer
    differences cumulative CPU using true timestamps, so the result is exact
    regardless of sampling cadence (slow cgroup reads, docker rescans,
    spurious wakeups). Bails on the FIRST scan if any container cannot be
    mapped -> caller falls back to the docker sampler.
    rescan_s bounds the blind spot for containers that are born and die between
    two rescans (shrink for scale-to-zero platforms with ephemeral containers)."""
    def scan():
        out = run("docker ps --format '{{.ID}}|{{.Names}}'")
        if out.returncode != 0:
            return None
        d = {}
        for line in out.stdout.strip().splitlines():
            cid, _, cname = line.partition("|")
            cdir = container_cgroup_dir(cid.strip())
            mdir = container_mem_cgroup_dir(cid.strip())
            if cdir is None:
                return None
            d[cname.strip()] = (cdir, mdir)
        return d

    dirs = scan()
    if not dirs:
        return
    last_scan = time.time()
    while not stop.is_set():
        t = time.time()
        if t - last_scan > rescan_s:
            fresh = scan()
            if fresh:  # merge new containers / drop dead ones; never bail post-start
                dirs = fresh
            last_scan = t
        snap = {}
        for cname, (cdir, mdir) in dirs.items():
            cum = read_cpu_cumulative(cdir)
            if cum is None:
                continue
            snap[cname] = (cum, read_mem_mb(mdir) if mdir else 0.0)
        if snap:
            samples.append((t, snap, "cum"))
            first_sample.set()
        stop.wait(0.01)
    # Flush a final sample at stop time: on a saturated host the last scheduled
    # rescan can be starved past the window end, which truncated coverage on
    # fresh-reset runs (93.6%/92.9% on runs 1-2). A final read closes the gap
    # so the sampled span reaches the window end.
    snap = {}
    for cname, (cdir, mdir) in dirs.items():
        cum = read_cpu_cumulative(cdir)
        if cum is None:
            continue
        snap[cname] = (cum, read_mem_mb(mdir) if mdir else 0.0)
    if snap:
        samples.append((time.time(), snap, "cum"))


def sample_totals(samples, cp_sub, fn_sub="", cp_members=None, fn_members=None,
                  fn_allow_configured=False):
    """Reduce raw samples to (cp_cpu_s, fn_cpu_s, cp_peak_mem_mb, covered_s, csv_rows, unclass_cpu_s).
    'cum' samples (cgroup): exact — consecutive cumulative deltas; the rate/dt
    normalization is irrelevant to the total, so irregular cadence cannot bias it.
    'pct' samples (docker stats): rate x elapsed as before.
    csv_rows normalize everything to percent-rate for samples.csv.
    Classification: control-plane = names matching cp_sub (or in cp_members);
    function = names matching fn_sub / fn_members; when NO function allowlist is
    configured (fn_allow_configured=False and fn_sub/fn_members empty), ALL
    non-cp names are function (denylist default, back-compat); when an fn
    allowlist IS configured (fn_allow_configured=True, or fn_sub/fn_members
    non-empty), non-matching names go to an unclassified bucket so a stray
    container can never silently inflate fn_cpu -- even if the allowlist matched
    nothing (fail-open, so a wrong --fn-images is loud, not silently ignored).
    cp_members/fn_members are sets of container names matched by image/label
    allowlists in run_once."""
    cp_cpu = fn_cpu = unclass = 0.0
    cp_mem = 0.0
    covered = 0.0
    fn_allow_active = fn_allow_configured or bool(fn_sub or fn_members)
    prev = {}
    csv_rows = []
    for i, (t, snap, mode) in enumerate(samples):
        tnext = samples[i + 1][0] if i + 1 < len(samples) else t + SAMPLE_S
        dt = max(tnext - t, 0.01)
        covered += dt
        for name, (v, mem) in snap.items():
            if mode == "cum":
                cum = v
                old = prev.get(name)
                prev[name] = cum
                d = max(cum - old, 0.0) if old is not None else 0.0
                cpu_sec = d
                pct = (d / dt) * 100.0
            else:
                pct = v
                cpu_sec = (pct / 100.0) * dt
            csv_rows.append((t, name, pct, mem))
            name_l = name.lower()
            if cp_members and name in cp_members:
                cp_cpu += cpu_sec
                cp_mem = max(cp_mem, mem)
            elif any(s in name_l for s in cp_sub):
                cp_cpu += cpu_sec
                cp_mem = max(cp_mem, mem)
            elif fn_allow_active:
                if (fn_members and name in fn_members) or any(s in name_l for s in fn_sub):
                    fn_cpu += cpu_sec
                else:
                    unclass += cpu_sec
            else:
                fn_cpu += cpu_sec
    return cp_cpu, fn_cpu, cp_mem, covered, csv_rows, unclass


def start_sampler(mode="docker", rescan_s=0.25):
    """Start a background sampler. Returns (samples, stop, first_sample, thread).
    cgroup mode that cannot map containers returns (None,)*4 -> caller falls back."""
    samples, stop, first_sample = [], threading.Event(), threading.Event()
    target = cgroup_sampler if mode == "cgroup" else docker_sampler
    th = threading.Thread(target=target, args=(samples, stop, first_sample, rescan_s), daemon=True)
    th.start()
    first_sample.wait(timeout=6)
    if not first_sample.is_set():
        stop.set()
        th.join(timeout=2)
        if mode == "cgroup":
            return None, None, None, None
    return samples, stop, first_sample, th


def run_once(args, cp_sub):
    """One full measurement window (warmup + sampler + load). Returns summary dict."""
    headers = None
    if args.auth:
        user, _, pw = args.auth.partition(":")
        headers = {"Authorization": "Basic " + base64.b64encode(
            ("%s:%s" % (user, pw)).encode()).decode()}

    if args.warmup > 0 and not args.idle_probe:
        run_load(args.url, args.warmup, min(args.concurrency, args.warmup),
                 headers=headers, interarrival_ms=args.interarrival_ms)
        time.sleep(2)  # let the hot function container register in docker stats

    rapl_start = rapl_energy()
    samples, stop, first_sample, th = start_sampler(args.sampler, args.rescan_s)
    if th is None:
        print("WARNING: %s sampler unavailable -> falling back to docker stats" % args.sampler)
        args.sampler = "docker"
        samples, stop, first_sample, th = start_sampler("docker")

    host_before = host_cpu_ticks()
    steal_before = steal_ticks()
    freq_before, governor = env_frequency()
    cp_read = cp_cgroup_reader(cp_sub) if args.delta_check else None
    cp_cum_before = cp_read() if cp_read else None

    t0 = time.perf_counter()
    reqs = None
    ld = None
    wall_loadgen = None
    if args.idle_probe:
        time.sleep(args.duration)  # platform up, zero traffic -> static orchestration baseline
    elif args.loadgen == "hey":
        ld = run_hey(args.url, args.total, args.concurrency, deadline_s=args.duration,
                     headers=headers)
        if ld is None:
            print("WARNING: hey unavailable/failed -> python load generator")
            reqs = run_load(args.url, args.total, args.concurrency, deadline_s=args.duration,
                            headers=headers, interarrival_ms=args.interarrival_ms)
    else:
        reqs = run_load(args.url, args.total, args.concurrency, deadline_s=args.duration,
                        headers=headers, interarrival_ms=args.interarrival_ms)
    wall = time.perf_counter() - t0
    wall_harness = wall
    # Read host AFTER-counter immediately at window end, BEFORE stopping the
    # sampler: on a saturated box the sampler thread can be starved for up to
    # the 10s join timeout, which would otherwise inflate host_cpu_sec past the
    # window and push host_saturation_pct spuriously over 100%.
    host_after = host_cpu_ticks()
    steal_after = steal_ticks()
    stop.set()
    th.join(timeout=10)
    rapl_end = rapl_energy()
    freq_after, _ = env_frequency()
    cp_cum_after = cp_read() if cp_read else None

    # --- energy attribution (Kepler-style CPU-time proportional) -------------
    # Classification members: any container matching the cp/fn name substring,
    # image, or label allowlists. Image/label signals are what make the fn
    # allowlist MEANINGFUL on platforms whose fn containers have opaque names
    # (Fn = ULIDs) - otherwise unclassified_cpu_s is guaranteed 0.0 regardless
    # of what is running. Defaults preserve denylist behavior when no fn
    # allowlist is configured (back-compat, documented).
    inv = docker_inventory()
    cp_members = {n for n, (img, lbls) in inv.items()
                  if _class_matches(n, img, lbls, (), args.cp_images, args.cp_labels)}
    fn_members = {n for n, (img, lbls) in inv.items()
                  if _class_matches(n, img, lbls, (), args.fn_images, args.fn_labels)}
    fn_allow_configured = bool(args.fn_containers or args.fn_images or args.fn_labels)
    cp_cpu_s, fn_cpu_s, cp_peak_mem_mb, covered_s, csv_rows, unclass_cpu_s = sample_totals(
        samples, cp_sub, args.fn_containers, cp_members, fn_members,
        fn_allow_configured=fn_allow_configured)
    if fn_allow_configured and not (args.fn_containers or fn_members):
        print("WARNING: function allowlist configured (--fn-images/--fn-labels/--fn-containers) "
              "but matched NO running container - every non-CP container is being counted as "
              "unclassified. Check the image/label against `container_labels` (Fn runs the "
              "DEPLOYED function image, e.g. hello:0.0.14, not the base fnproject/python:*).")
    # Clamp covered to the window: the final-sample tail (SAMPLE_S) plus the
    # stop-time flush can extend the sampled span just past wall; coverage must
    # not read >100%.
    covered_s = min(covered_s, wall)
    if unclass_cpu_s > 0.5:
        print("WARNING: %.1f CPU-s fell outside both cp and fn containers "
              "(stray container?) - see container_inventory / container_labels" % unclass_cpu_s)
    all_snaps = []
    for t, name, pct, mem in csv_rows:
        if all_snaps and all_snaps[-1][0] == t:
            all_snaps[-1][1][name] = (pct, mem)
        else:
            all_snaps.append((t, {name: (pct, mem)}))
    e_cp, e_fn = cp_cpu_s * P_BUSY_CORE_W, fn_cpu_s * P_BUSY_CORE_W
    e_dynamic = e_cp + e_fn
    e_total = args.idle_w * wall + e_dynamic
    covered_s = min(covered_s, wall)  # never overstate coverage beyond the window

    host_cpu_sec = None
    host_overhead_cpu_sec = None
    orchestration_cpu_sec = None
    orchestration_share_pct = None
    host_saturation_pct = None
    host_plausible = None
    host_saturated = None
    steal_sec = None
    steal_pct = None
    if host_before is not None and host_after is not None and host_after > host_before:
        host_cpu_sec = (host_after - host_before) / 100.0  # USER_HZ = 100
        host_overhead_cpu_sec = max(host_cpu_sec - (cp_cpu_s + fn_cpu_s), 0.0)
        orchestration_cpu_sec = max(host_cpu_sec - fn_cpu_s, 0.0)  # CP + kernel + dockerd + harness
        orchestration_share_pct = orchestration_cpu_sec / host_cpu_sec * 100.0
        # Saturation: busy host time relative to the physical ceiling. The host
        # window is sampled just OUTSIDE the load window, so allow 5% slack for
        # edge bleed-in before declaring the box saturated/implausible.
        ceiling = cpu_count() * wall
        if ceiling > 0:
            host_saturation_pct = round(host_cpu_sec / ceiling * 100.0, 1)
            host_plausible = host_cpu_sec <= ceiling * 1.05
            # Enforce the documented QoS-caveat rule (report §17): a run at >=85%
            # host saturation is contention-contaminated -- its latency/throughput
            # reflect scheduler competition, not platform overhead. host_plausible
            # only checks the physical ceiling, so this flag is what actually
            # attaches the caveat to the run's QoS numbers.
            host_saturated = host_saturated_flag(host_saturation_pct)
    if steal_before is not None and steal_after is not None and steal_after > steal_before:
        steal_sec = (steal_after - steal_before) / 100.0
        if host_cpu_sec:
            steal_pct = steal_sec / host_cpu_sec * 100.0

    cp_delta_sec = None
    cp_sampler_vs_delta_pct = None
    if cp_cum_before is not None and cp_cum_after is not None and cp_cum_after > cp_cum_before:
        cp_delta_sec = cp_cum_after - cp_cum_before
        if cp_delta_sec > 0 and cp_cpu_s > 0:
            cp_sampler_vs_delta_pct = round((cp_cpu_s / cp_delta_sec - 1.0) * 100.0, 2)

    # --- QoS -----------------------------------------------------------------
    compliance_source = "measured"
    if args.idle_probe:
        n = 0
        ok = 0
        p50 = p90 = p99 = max_ms = None
        compliance = None
        compliance_source = "idle_probe"
    elif reqs is not None:
        n = len(reqs)
        ok = sum(1 for o, _ in reqs if o)
        lats_ms = sorted(1000.0 * l for _, l in reqs)
        def pct(p):
            return lats_ms[min(len(lats_ms) - 1, int(len(lats_ms) * p))] if lats_ms else float("nan")
        p50, p90, p99 = pct(0.5), pct(0.9), pct(0.99)
        max_ms = lats_ms[-1] if lats_ms else None
        compliance = (sum(1 for l in lats_ms if l <= args.slo_ms) / n) if n else 0.0
    else:
        n = ld["requests"]
        ok = ld["ok"]
        # wall MUST stay the harness clock: it is the single window over which
        # energy is attributed (e_total = idle_w*wall + e_dynamic), host
        # saturation is computed, and coverage is clamped. hey's own wall
        # (max(offset) = time of the LAST REQUEST START) can be a fraction of a
        # second shorter than the true window, so reassigning wall here made
        # wall_s < the attribution window and coverage read >100% even though
        # the clamp had capped covered_s at the harness window (v9.9 fix).
        # Expose hey's own duration separately as loadgen.wall_s (cross-check).
        wall_loadgen = ld["wall"]
        if wall_harness and abs(wall_loadgen - wall_harness) > 5.0:
            print("WARNING: loadgen-reported wall (%.1fs) differs from harness clock (%.1fs) - "
                  "loadgen timing may be unreliable" % (wall_loadgen, wall_harness))
        p50, p90, p99 = ld["p50"], ld["p90"], ld["p99"]
        max_ms = ld["max"]
        compliance = cdf_compliance(ld["lat_points"], args.slo_ms)
        compliance_source = "hey_interp"
        ld_avg = ld.get("avg")
        ld_errors = ld.get("errors", 0)
        if ld_avg and p50 and ld_avg > 3.0 * p50:
            print("WARNING: heavy-tailed QoS (avg=%.0fms vs p50=%.0fms) - check hey.csv, VM contention"
                  % (ld_avg, p50))
        if ld_errors:
            print("WARNING: hey reports %d request errors - check hey.csv status codes" % ld_errors)
        if args.duration and wall > args.duration * 1.1:
            print("WARNING: window (%.1fs) exceeded the --duration safety cap (%.0fs) - "
                  "runs are count-bound; consider a shorter --total on a loaded VM"
                  % (wall, args.duration))
    availability = ok / n if n else 0.0

    # --- carbon ---------------------------------------------------------------
    wh = e_total / 3600.0
    op_gco2 = wh * args.ci * PUE
    cp_wh = e_cp / 3600.0
    cp_gco2 = cp_wh * args.ci * PUE
    n_compliant = int(round(compliance * n)) if compliance is not None and n else 0
    kpi = (op_gco2 / n_compliant) if n_compliant else float("nan")
    # Marginal KPI: dynamic (load-created) carbon only, NOT the idle baseline.
    # The operational KPI is ~90%+ idle-power-dominated, so it is extremely
    # sensitive to wall-clock duration (a 3x wall swing moves it 3x). The
    # dynamic-only figure is the wall-independent per-invocation cost of serving.
    kpi_dynamic = (e_dynamic / 3600.0 * args.ci * PUE) / n_compliant if n_compliant else float("nan")
    embodied_per_gb = DRAM_EMBODIED_G_PER_GB / (LIFESPAN_YEARS * 365 * 24)

    # --- RAPL validation -------------------------------------------------------
    rapl_validation = None
    if rapl_start is not None and rapl_end is not None:
        e_rapl = rapl_end - rapl_start
        rapl_validation = abs(e_total - e_rapl) / e_rapl * 100 if e_rapl > 0 else None

    summary = {
        "platform": args.platform,
        "url": args.url,
        "mode": "idle" if args.idle_probe else "load",
        "wall_s": round(wall, 2),
        "wall_harness_s": round(wall_harness, 2),
        "sampling_covered_s": round(covered_s, 2),
        "requests": n, "successes": ok,
        "availability": round(availability, 4),
        "throughput_rps": round(ok / wall, 2),
        "latency_ms": {"p50": round(p50, 2) if p50 is not None else None,
                       "p90": round(p90, 2) if p90 is not None else None,
                       "p99": round(p99, 2) if p99 is not None else None,
                       "max": round(max_ms, 2) if max_ms else None},
        "slo_ms": args.slo_ms,
        "slo_compliance": round(compliance, 4) if compliance is not None else None,
        "compliance_source": compliance_source,
        "energy_J": {"total": round(e_total, 1), "dynamic": round(e_dynamic, 1),
                     "control_plane": round(e_cp, 1), "function": round(e_fn, 1)},
        "cp_share_pct": round(e_cp / e_total * 100, 2) if e_total else None,
        "cp_dynamic_share_pct": round(e_cp / e_dynamic * 100, 2) if e_dynamic else None,
        "cpu_sec": {"control_plane": round(cp_cpu_s, 2), "function": round(fn_cpu_s, 2)},
        "cpu_sec_ceiling": round(cpu_count() * wall, 2),
        "physical_plausible": bool(cpu_count() * wall >= cp_cpu_s + fn_cpu_s),
        "unclassified_cpu_s": round(unclass_cpu_s, 2),
        "container_inventory": docker_container_names(),
        "container_labels": {n: {"image": img, "labels": lbls} for n, (img, lbls) in sorted(inv.items())},
        "cp_peak_mem_mb": round(cp_peak_mem_mb, 1),
        "carbon_gCO2": {"op_total": round(op_gco2, 3), "op_control_plane": round(cp_gco2, 3),
                        "idle_band": {str(w): round((w * wall + e_dynamic) / 3600.0 * args.ci * PUE, 3)
                                      for w in (15, 30, 45)}},
        "model": {"idle_w": args.idle_w, "busy_core_w": P_BUSY_CORE_W, "pue": PUE, "ci": args.ci},
        "sensitivity": {
            "cp_dynamic_share_pct_by_busy_w": {str(w): round(cp_cpu_s / (cp_cpu_s + fn_cpu_s) * 100.0, 2)
                                               if (cp_cpu_s + fn_cpu_s) else None
                                               for w in (2.0, 3.5, 5.0)},
            "dynamic_energy_J_by_busy_w": {str(w): round((cp_cpu_s + fn_cpu_s) * w, 1)
                                           for w in (2.0, 3.5, 5.0)},
            "op_carbon_gCO2_by_busy_w": {str(w): round((args.idle_w * wall + (cp_cpu_s + fn_cpu_s) * w)
                                                       / 3600.0 * args.ci * PUE, 3)
                                         for w in (2.0, 3.5, 5.0)},
        },
        "kpi_gco2_per_slo_compliant_inv": round(kpi, 4),
        "kpi_gco2_per_inv_dynamic": round(kpi_dynamic, 6),
        "embodied_dram_g_per_gb_h": round(embodied_per_gb, 4),
        "host_cpu_sec": round(host_cpu_sec, 2) if host_cpu_sec is not None else None,
        "host_saturation_pct": host_saturation_pct,
        "host_plausible": host_plausible,
        "host_saturated": host_saturated,
        "host_overhead_cpu_sec": round(host_overhead_cpu_sec, 2) if host_overhead_cpu_sec is not None else None,
        "orchestration_cpu_sec": round(orchestration_cpu_sec, 2) if orchestration_cpu_sec is not None else None,
        "orchestration_share_pct": round(orchestration_share_pct, 2) if orchestration_share_pct is not None else None,
        "steal_sec": round(steal_sec, 2) if steal_sec is not None else None,
        "steal_pct": round(steal_pct, 2) if steal_pct is not None else None,
        "cp_delta_sec": round(cp_delta_sec, 3) if cp_delta_sec is not None else None,
        "cp_sampler_vs_delta_pct": cp_sampler_vs_delta_pct,
        "env": {"cpu_count": cpu_count(), "governor": governor,
                "freq_mhz_before": round(freq_before, 1) if freq_before else None,
                "freq_mhz_after": round(freq_after, 1) if freq_after else None,
                "sampler": args.sampler,
                "loadgen": "hey" if ld is not None else "py",
                "loadgen_requested": args.loadgen,
                "loadgen_fallback": bool(args.loadgen == "hey" and ld is None)},
        "rapl_validation_err_pct": round(rapl_validation, 2) if rapl_validation is not None else None,
        "rapl_available": rapl_start is not None,
    }
    if ld is not None:
        summary["loadgen"] = {"source": "hey", "rps": ld["rps"],
                              "avg_ms": round(ld_avg, 2) if ld_avg is not None else None,
                              "errors": ld_errors,
                              "wall_s": round(wall_loadgen, 2) if wall_loadgen else None}
    return summary, all_snaps, reqs, ld


def write_run(outdir, summary, all_snaps, reqs):
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "summary.json"), "w") as f:
        json.dump(clean_json(summary), f, indent=2)
    with open(os.path.join(outdir, "samples.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "container", "cpu_pct", "mem_mb"])
        for t, snap in all_snaps:
            for name, (cpu, mem) in (snap or {}).items():
                w.writerow([round(t, 2), name, cpu, mem])
    with open(os.path.join(outdir, "requests.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ok", "latency_ms"])
        if reqs is not None:
            for o, l in reqs:
                w.writerow([int(o), round(1000 * l, 3)])
        else:
            w.writerow(["none", 0])  # hey loadgen: per-request latencies live in hey.csv


def clean_json(obj):
    """Recursively replace non-finite floats (NaN/Inf) with None so every
    JSON output stays spec-valid (bare NaN breaks strict parsers like R/JS)."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: clean_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_json(v) for v in obj]
    return obj


def median_summary(summaries):
    """Median of numeric leaves (statistics.median semantics); first value for strings/None.
    Non-finite values are skipped so a NaN from one run can't poison a median."""
    def med(vals):
        s = sorted(v for v in vals if v is not None and not (isinstance(v, float) and not math.isfinite(v)))
        n = len(s)
        if n == 0:
            return None
        if n % 2 == 1:
            return s[n // 2]
        return (s[n // 2 - 1] + s[n // 2]) / 2.0
    def rec(items):
        if isinstance(items[0], dict):
            out = {}
            for k in items[0]:
                if not all(k in it for it in items):
                    continue
                if isinstance(items[0][k], dict):
                    out[k] = rec([it[k] for it in items])
                elif isinstance(items[0][k], (int, float)):
                    out[k] = med([it[k] for it in items])
                else:
                    out[k] = items[0][k]
            return out
        return items[0]
    return rec(summaries)


def spread_of(summaries, paths):
    res = {}
    for path in paths:
        vals = []
        for s in summaries:
            cur = s
            ok = True
            for p in path:
                if isinstance(cur, dict) and p in cur:
                    cur = cur[p]
                else:
                    ok = False
                    break
            if ok and isinstance(cur, (int, float)) and cur == cur:
                vals.append(cur)
        if vals:
            res[".".join(path)] = [round(min(vals), 4), round(max(vals), 4)]
    return res


def gather(summaries, path):
    """Values of a nested path across summaries."""
    vals = []
    for s in summaries:
        cur = s
        ok = True
        for p in path:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                ok = False
                break
        if ok and isinstance(cur, (int, float)) and cur == cur:
            vals.append(cur)
    return vals


def verify(args, cp_sub):
    """Workload sanity check: fire N calls, report per-invocation function CPU.
    Confirms the deployed handler actually does the claimed work."""
    headers = None
    if args.auth:
        user, _, pw = args.auth.partition(":")
        headers = {"Authorization": "Basic " + base64.b64encode(
            ("%s:%s" % (user, pw)).encode()).decode()}
    os.makedirs(args.outdir, exist_ok=True)
    samples, stop, first_sample, th = start_sampler(args.sampler, args.rescan_s)
    if th is None:
        print("WARNING: %s sampler unavailable -> docker" % args.sampler)
        args.sampler = "docker"
        samples, stop, first_sample, th = start_sampler("docker")
    reqs = run_load(args.url, args.verify_n, min(args.concurrency, args.verify_n),
                    headers=headers, interarrival_ms=args.interarrival_ms)
    stop.set()
    th.join(timeout=10)

    ok = sum(1 for o, _ in reqs if o)
    lats = sorted(1000.0 * l for _, l in reqs if _)

    def pct(p):
        return lats[min(len(lats) - 1, int(len(lats) * p))] if lats else float("nan")

    cp_cpu_s, fn_cpu_s, _, _, _, _ = sample_totals(samples, cp_sub, args.fn_containers)

    ms_per_inv = (fn_cpu_s / ok * 1000.0) if ok else None
    result = {
        "platform": args.platform, "url": args.url,
        "calls": args.verify_n, "successes": ok,
        "availability": round(ok / args.verify_n, 4) if args.verify_n else None,
        "latency_ms": {"p50": round(pct(0.5), 2), "p99": round(pct(0.99), 2)},
        "function_cpu_sec_total": round(fn_cpu_s, 3),
        "function_cpu_ms_per_inv": round(ms_per_inv, 2) if ms_per_inv is not None else None,
        "control_plane_cpu_sec_total": round(cp_cpu_s, 3),
        "budget_ms": args.verify_budget_ms,
        "budget_check": None,
        "env": {"sampler": args.sampler},
    }
    if args.verify_budget_ms and ms_per_inv is not None:
        if ms_per_inv < args.verify_budget_ms * 0.5:
            result["budget_check"] = "UNDER budget: function CPU far below claim (sleeping / not shipped?)"
        elif ms_per_inv > args.verify_budget_ms * 1.5:
            result["budget_check"] = "OVER budget: function CPU above claim"
        else:
            result["budget_check"] = "MATCHES budget within 50-150%"
    with open(os.path.join(args.outdir, "verify.json"), "w") as f:
        json.dump(clean_json(result), f, indent=2)
    print(json.dumps(clean_json(result), indent=2))
    print("\nSaved to %s/verify.json" % os.path.abspath(args.outdir))


# ---------------------------------------------------------------------------
def cgroup_probe():
    """Live check that running containers can be mapped to cgroup dirs (as the
    ~100 Hz cgroup sampler requires). FAIL = use --sampler docker on this host."""
    out = run("docker ps --format '{{.ID}}|{{.Names}}'")
    if out.returncode != 0:
        return "FAIL: docker ps error -> use --sampler docker"
    lines = [l for l in out.stdout.strip().splitlines() if l.strip()]
    if not lines:
        return "no containers running (probe again once the platform is up)"
    for line in lines:
        cid, _, cname = line.partition("|")
        cdir = container_cgroup_dir(cid.strip())
        if cdir is None:
            return "FAIL: cannot map %s -> use --sampler docker" % cname.strip()
        if not os.path.isdir(cdir):
            return "FAIL: %s cgroup dir %s missing -> use --sampler docker" % (cname.strip(), cdir)
    return "OK: %d containers map to cgroups (100 Hz sampler usable)" % len(lines)


def main():
    ap = argparse.ArgumentParser(description="SAQEF measurement harness")
    ap.add_argument("--check", action="store_true", help="verify docker + RAPL availability")
    ap.add_argument("--url", default="http://localhost:8080/r/app/hello")
    ap.add_argument("--platform", default="unknown", help="label, e.g. fn, openfaas, openwhisk")
    ap.add_argument("--cp-containers", default="", help="comma-separated substrings of control-plane containers")
    ap.add_argument("--fn-containers", default="",
                    help="comma-separated substrings of function containers (default: all non-cp containers; "
                         "anything matching neither goes to an unclassified bucket)")
    ap.add_argument("--cp-images", default="",
                    help="comma-separated image substrings of control-plane containers")
    ap.add_argument("--fn-images", default="",
                    help="comma-separated image substrings of function containers (recommended for Fn, whose "
                         "function containers have opaque ULID names). Match the DEPLOYED image name "
                         "(e.g. hello:0.0.14 / 'hello'), NOT the base runtime fnproject/python:*.")
    ap.add_argument("--cp-labels", default="",
                    help="comma-separated docker label keys identifying control-plane containers")
    ap.add_argument("--fn-labels", default="",
                    help="comma-separated docker label keys identifying function containers")
    ap.add_argument("--total", type=int, default=2000, help="total requests")
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--duration", type=int, default=60, help="window seconds (safety cap; runs are count-bound)")
    ap.add_argument("--warmup", type=int, default=10, help="requests fired before the measured window")
    ap.add_argument("--repeat", type=int, default=1, help="measurement repetitions")
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--slo-ms", type=float, default=500.0, help="SLO latency target in ms")
    ap.add_argument("--auth", default="", help="HTTP Basic auth 'user:pass' (OpenFaaS admin creds)")
    ap.add_argument("--idle-w", type=float, default=P_IDLE_BASE_W)
    ap.add_argument("--ci", type=float, default=CI_GCO2_PER_KWH, help="grid carbon intensity gCO2/kWh")
    ap.add_argument("--verify", action="store_true", help="workload sanity check (N calls, per-inv function CPU)")
    ap.add_argument("--verify-n", type=int, default=100)
    ap.add_argument("--verify-budget-ms", type=float, default=None,
                    help="claimed per-call CPU budget ms (for the verify budget check)")
    ap.add_argument("--sampler", default="docker", choices=["docker", "cgroup"],
                    help="container CPU source (cgroup = direct cpu.stat, ~100 Hz, falls back)")
    ap.add_argument("--rescan-s", type=float, default=0.25,
                    help="container-set rescan interval for the cgroup sampler (smaller catches "
                         "short-lived/scale-to-zero containers; costs host CPU)")
    ap.add_argument("--loadgen", default="py", choices=["py", "hey"],
                    help="load generator: py (stdlib threads) or hey (Go binary, low host footprint)")
    ap.add_argument("--interarrival-ms", type=float, default=0.0,
                    help="gap between requests, ms (cold-start experiments: total N, concurrency 1)")
    ap.add_argument("--delta-check", action="store_true",
                    help="cross-validate sampler vs direct before/after cgroup counter of the CP container")
    ap.add_argument("--idle-probe", action="store_true",
                    help="platform up, zero traffic for --duration s: static orchestration baseline")
    args = ap.parse_args()

    if args.check:
        ds = docker_stats_once()
        r = rapl_energy()
        mhz, gov = env_frequency()
        ht = host_cpu_ticks()
        st = steal_ticks()
        print("docker stats :", "OK" if ds is not None else "NOT AVAILABLE",
              "(%d containers seen)" % len(ds) if ds is not None else "")
        print("RAPL         :", ("OK %.0f J" % r) if r else "NOT AVAILABLE (CPU-time model only)")
        print("cpu_count    :", cpu_count())
        print("cgroup quota :", cgroup_cpu_quota() or "n/a (not under cgroup CPU quota)")
        print("governor     :", gov or "n/a")
        print("freq_mhz     :", ("%.0f" % mhz) if mhz else "n/a")
        print("host_cpu     :", ("%d ticks" % ht) if ht is not None else "n/a (no /proc/stat)")
        print("steal        :", ("%d ticks" % st) if st is not None else "n/a (no /proc/stat)")
        print("hey          :", "available" if shutil.which("hey") else "not installed (--loadgen py only)")
        print("cgroup map   :", cgroup_probe())
        sys.exit(0)

    cp_sub = [s.strip().lower() for s in args.cp_containers.split(",") if s.strip()]
    args.fn_containers = [s.strip().lower() for s in args.fn_containers.split(",") if s.strip()]
    args.cp_images = [s.strip().lower() for s in args.cp_images.split(",") if s.strip()]
    args.fn_images = [s.strip().lower() for s in args.fn_images.split(",") if s.strip()]
    args.cp_labels = [s.strip() for s in args.cp_labels.split(",") if s.strip()]
    args.fn_labels = [s.strip() for s in args.fn_labels.split(",") if s.strip()]

    if args.verify:
        verify(args, cp_sub)
        sys.exit(0)

    if args.repeat > 1:
        os.makedirs(args.outdir, exist_ok=True)
        summaries = []
        for i in range(1, args.repeat + 1):
            print(f"--- run {i}/{args.repeat} ---")
            summary, all_snaps, reqs, ld = run_once(args, cp_sub)
            write_run(os.path.join(args.outdir, "run_%d" % i), summary, all_snaps, reqs)
            if ld is not None:
                with open(os.path.join(args.outdir, "run_%d" % i, "hey.csv"), "w") as f:
                    f.write(ld["raw"])
            summaries.append(summary)
        with open(os.path.join(args.outdir, "runs.json"), "w") as f:
            json.dump(clean_json(summaries), f, indent=2)
        med = median_summary(summaries)
        med["repetitions"] = args.repeat
        med["spread_min_max"] = spread_of(summaries, [
            ("throughput_rps",), ("slo_compliance",),
            ("latency_ms", "p50"), ("latency_ms", "p99"),
            ("cp_dynamic_share_pct",), ("cp_share_pct",),
            ("energy_J", "dynamic"), ("kpi_gco2_per_slo_compliant_inv",),
        ])
        stats_paths = [("throughput_rps",), ("slo_compliance",),
                       ("latency_ms", "p50"), ("latency_ms", "p99"),
                       ("cp_dynamic_share_pct",)]
        med["bootstrap_ci"] = {".".join(p): bootstrap_ci(gather(summaries, p)) for p in stats_paths}
        med["cv_pct"] = {".".join(p): cv_pct(gather(summaries, p)) for p in stats_paths}
        med["iqr"] = {".".join(p): iqr(gather(summaries, p)) for p in stats_paths}
        with open(os.path.join(args.outdir, "summary.json"), "w") as f:
            json.dump(clean_json(med), f, indent=2)
        print("=== MEDIAN over %d runs ===" % args.repeat)
        print(json.dumps(clean_json(med), indent=2))
        print("\nSaved runs to", os.path.abspath(args.outdir), "/")
    else:
        summary, all_snaps, reqs, ld = run_once(args, cp_sub)
        write_run(args.outdir, summary, all_snaps, reqs)
        if ld is not None:
            with open(os.path.join(args.outdir, "hey.csv"), "w") as f:
                f.write(ld["raw"])
        print(json.dumps(clean_json(summary), indent=2))
        print(f"\nSaved to {os.path.abspath(args.outdir)}/")


if __name__ == "__main__":
    main()
