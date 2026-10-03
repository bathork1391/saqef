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
import collections
import csv
import datetime
import hashlib
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

# sample_totals() returns these; a namedtuple keeps the existing tuple-unpacking
# call sites working while giving the new gap diagnostics real field names.
SampleTotals = collections.namedtuple(
    "SampleTotals",
    "cp_cpu_s fn_cpu_s cp_peak_mem_mb covered_s csv_rows unclass_cpu_s "
    "max_gap_s n_samples span_s")


# ---------------------------------------------------------------------------
def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def harness_git_rev():
    """The harness's own git revision, recorded in every summary.json.

    A result is only reproducible if you know which code produced it. The
    2026-08-14/15 corpus predates the window fix (c22dff9) and the birth-credit
    fix (34b4f26), and nothing in those runs' JSON says so -- the revision had to
    be recovered from runbook history. Returns "unknown" rather than raising:
    a run must not fail because git is absent."""
    try:
        # safe.directory: the harness runs as root under sudo on a user-owned repo,
        # and git then refuses ("dubious ownership") with empty stdout, so every
        # owlog29 run recorded "unknown" (runbook 29.4).
        repo = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(["git", "-c", "safe.directory=" + repo, "rev-parse", "--short", "HEAD"],
                             cwd=repo, capture_output=True, text=True, timeout=10)
        return (out.stdout.strip() if out.returncode == 0 else "") or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def harness_git_dirty():
    """True if the harness working tree had uncommitted changes at run time.

    git_rev alone is not enough: an uncommitted edit is exactly the case where
    the committed revision misrepresents the code that ran. Recorded so a
    suspicious result can be traced to a working state, not blamed on a hash."""
    try:
        repo = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(["git", "-c", "safe.directory=" + repo, "status", "--porcelain"],
                             cwd=repo, capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return None  # git refused or failed: unknown, never a false "clean"
        return bool(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None


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


def _psys_dir():
    return os.path.join(RAPL_DIR, "intel-rapl:1")


def psys_status():
    """'ok' | 'absent' (no psys domain) | 'unreadable' (present but energy_uj is
    root-only and this process is not root). Recorded so that e_psys_j=null is
    never ambiguous between "not on this box" and "not attempted"."""
    try:
        with open(os.path.join(_psys_dir(), "name")) as f:
            if f.read().strip() != "psys":
                return "absent"
    except OSError:
        return "absent"
    try:
        with open(os.path.join(_psys_dir(), "energy_uj")) as f:
            int(f.read().strip())
        return "ok"
    except (OSError, ValueError):
        return "unreadable"


def psys_energy():
    """Return platform (psys) energy in J, or None (see psys_status for why).

    psys (intel-rapl:1 on this box) is the platform-level RAPL domain: package
    plus what the platform power controller adds around it. It is recorded next
    to package as a bound on what package misses; it is never attributed."""
    if psys_status() != "ok":
        return None
    try:
        with open(os.path.join(_psys_dir(), "energy_uj")) as f:
            return int(f.read().strip()) / 1e6
    except (OSError, ValueError):
        return None


def psys_max_range_j():
    try:
        with open(os.path.join(_psys_dir(), "max_energy_range_uj")) as f:
            return int(f.read().strip()) / 1e6
    except (OSError, ValueError):
        return None


def rapl_max_range_j():
    """Return the RAPL counter's wraparound range in J, or None if unavailable.

    The kernel powercap `energy_uj` counter free-runs and wraps at
    `max_energy_range_uj` (a documented Linux powercap hazard -- some older/
    mobile Intel platforms wrap in as little as ~60-260 J, i.e. every few
    seconds under load). This harness takes only two point-in-time reads
    (before/after a run) with no periodic re-sampling, so a run whose window
    is long relative to the wrap period (OpenWhisk stretches to 150-320s, the
    longest windows in this study) would otherwise silently read back a
    tiny/negative energy delta and produce a garbage rapl_validation_err_pct.
    On THIS box max_energy_range_uj is ~262 kJ (confirmed 2026-08-08), so no
    wraparound occurs even on the longest OW run at realistic power draw --
    but the correction costs nothing and removes the silent-corruption risk
    on any other machine this harness is run on."""
    p = os.path.join(RAPL_DIR, "intel-rapl:0", "max_energy_range_uj")
    try:
        with open(p) as f:
            return int(f.read().strip()) / 1e6  # uJ -> J
    except Exception:
        return None


def rapl_correct_wrap(raw_delta, range_fn=None):
    """Return (e_rapl, wrap_flag) for a raw RAPL (end - start) energy delta.

    Corrects for the energy_uj counter's wraparound: a single wrap is corrected
    exactly by adding one counter range (rapl_max_range_j()); a negative
    counter with no known range is reported as uncertain and returns None --
    fail-open, never a garbage validation number.

    CAVEAT (not a general double-wrap detector): from a single before/after
    pair there is no way to determine the true wrap count in general -- the
    correction only ever adds ONE range, so a genuine double-or-more wrap
    whose raw delta happens to land >=0 after adding one range is silently
    mislabeled 'corrected_single' rather than caught by the 'corrected <  0'
    check below (e.g. rng=1000J, true energy=2500J: two wraps can produce a
    raw delta that becomes positive after a single +rng correction). This is
    a mathematical limitation of two-point sampling, not a bug: distinguishing
    N wraps requires periodic re-sampling within the run, which this harness
    does not do. It is inconsequential on the machine this study runs on --
    max_energy_range_uj is ~262 kJ, several orders of magnitude above what a
    17-320 s run consumes at realistic power draw, so multi-wrap is not
    physically reachable here -- but the docstring should not imply the
    wrap_flag values are a sound general-purpose classifier; they are a
    best-effort single-wrap correction with a fail-open backstop, not a proof.
    wrap_flag is one of 'none' | 'corrected_single' | 'uncertain_double' |
    'uncertain_no_range', so a discarded reading is DISTINGUISHABLE in the
    output from "RAPL not available" (rapl_available is a separate field)."""
    if raw_delta is None or raw_delta >= 0:
        return raw_delta, "none"
    # range_fn lets the psys domain reuse this exact correction with its own
    # range. Both domains wrap at ~262 kJ on this box (~2.4 h at 30 W), so a
    # wrap inside a 17-420 s window is not reachable here; the flag exists so a
    # re-analyser never has to assume that.
    rng = (range_fn or rapl_max_range_j)()
    if not rng:
        return None, "uncertain_no_range"
    corrected = raw_delta + rng
    if corrected < 0:
        return None, "uncertain_double"
    return corrected, "corrected_single"


class EnergyTrace(threading.Thread):
    """1 Hz package + psys counter trace over the measurement window.

    The window totals (e_rapl_j, e_psys_j) are two-point reads; this keeps the
    shape in between, so power-vs-load linearity and within-run drift can be
    checked offline against the per-interval CPU in samples_raw.csv without
    referencing any global W/core constant. One sysfs read per domain per
    second: negligible next to the cgroup sampler."""

    def __init__(self, interval_s=1.0):
        super().__init__(daemon=True)
        self.interval_s = interval_s
        self.rows = []
        self._stop_ev = threading.Event()

    def run(self):
        while True:
            self.rows.append((time.time(), rapl_energy(), psys_energy()))
            if self._stop_ev.wait(self.interval_s):
                break
        self.rows.append((time.time(), rapl_energy(), psys_energy()))

    def stop(self):
        self._stop_ev.set()
        self.join(timeout=5)


_LOADGEN_ID = {}


def loadgen_identity():
    """(resolved absolute path, sha256) of the hey binary this process will run.

    This box has two different hey builds: /usr/local/bin/hey -> /root/go/bin/hey
    (what every `sudo` leg resolves) and ~/go/bin/hey (an interactive shell's).
    Recording which one ran makes a mid-corpus switch visible instead of silent."""
    if "v" not in _LOADGEN_ID:
        import hashlib
        path = shutil.which("hey")
        digest = None
        if path:
            path = os.path.realpath(path)
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            digest = h.hexdigest()
        _LOADGEN_ID["v"] = (path, digest)
    return _LOADGEN_ID["v"]


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


_HOST_CPU_LIST_OVERRIDE = None  # set from --host-cpu-list / SAQEF_HOST_CPU_LIST, e.g. "0,1"


def host_cpu_ticks():
    """BUSY CPU ticks from /proc/stat, or None.
    Busy = user+nice+system+irq+softirq+steal (total minus idle and iowait);
    excludes guest/guest_nice which double-count user/system ticks.

    By default sums the aggregate "cpu " line (all cores). If the platform is
    cpuset/taskset-pinned to a subset of cores (--host-cpu-list / cpu_count
    override), that default is wrong: background activity on the OTHER,
    un-pinned cores (dockerd, kworkers, this very harness's own host) still
    counts toward the aggregate line, inflating host_saturation_pct past the
    pinned-core ceiling even past 100%/the 105% plausibility bound. In that
    case sum only the specific per-core "cpuN" lines that were actually
    pinned, matching cpu_count_override's denominator."""
    try:
        with open("/proc/stat") as f:
            lines = f.readlines()
        if _HOST_CPU_LIST_OVERRIDE is not None:
            wanted = {"cpu%d" % c for c in _HOST_CPU_LIST_OVERRIDE}
            total = 0
            found = 0
            for line in lines:
                parts = line.split()
                if parts and parts[0] in wanted:
                    if len(parts) < 9:
                        return None
                    vals = [int(v) for v in parts[1:9]]
                    total += sum(vals) - vals[3] - vals[4]
                    found += 1
            return total if found == len(wanted) else None
        parts = lines[0].split()
        if len(parts) < 9:
            return None
        vals = [int(v) for v in parts[1:9]]
        return sum(vals) - vals[3] - vals[4]  # total - idle - iowait
    except Exception:
        return None


def host_cpu_busy_total():
    """Return (busy_ticks, total_ticks) over the SAME core scope host_cpu_ticks
    uses (whole machine, or --host-cpu-list cores when pinned), or None.
    Used by the ambient-load quiet gate; kept separate from host_cpu_ticks so
    the in-run host_saturation measurement path stays byte-identical."""
    try:
        with open("/proc/stat") as f:
            lines = f.readlines()
        if _HOST_CPU_LIST_OVERRIDE is not None:
            wanted = {"cpu%d" % c for c in _HOST_CPU_LIST_OVERRIDE}
            busy = total = 0
            found = 0
            for line in lines:
                parts = line.split()
                if parts and parts[0] in wanted:
                    if len(parts) < 9:
                        return None
                    vals = [int(v) for v in parts[1:9]]
                    busy += sum(vals) - vals[3] - vals[4]
                    total += sum(vals)
                    found += 1
            return (busy, total) if found == len(wanted) else None
        parts = lines[0].split()
        if len(parts) < 9:
            return None
        vals = [int(v) for v in parts[1:9]]
        return (sum(vals) - vals[3] - vals[4], sum(vals))
    except Exception:
        return None


def ps_top_snapshot(n=8):
    """Top-N CPU processes (ps aux --sort=-%cpu): the quiet gate's fallback when
    /proc snapshots fail. %CPU here is a lifetime average, not current load.
    Returns a list of header + n rows, or None."""
    try:
        out = subprocess.run(["ps", "aux", "--sort=-%cpu"],
                             capture_output=True, text=True, timeout=15).stdout
        lines = out.strip().splitlines()
        return lines[:1 + n] if lines else None
    except Exception:
        return None


def proc_cpu_ticks():
    """{pid: (starttime, own_ticks, reaped_child_ticks, comm, cmdline)} for every
    process, from /proc/<pid>/stat: utime + stime, and cutime + cstime (CPU of
    children it has reaped, e.g. OpenWhisk's `docker logs` per activation).
    Unreadable or vanished processes are skipped. Returns {} on failure."""
    out = {}
    try:
        pids = [d for d in os.listdir("/proc") if d.isdigit()]
    except Exception:
        return out
    for pid in pids:
        try:
            with open("/proc/%s/stat" % pid) as f:
                raw = f.read()
            comm = raw[raw.index("(") + 1:raw.rindex(")")]
            fields = raw[raw.rindex(")") + 2:].split()
            try:
                with open("/proc/%s/cmdline" % pid, "rb") as f:
                    cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            except Exception:
                cmd = ""
            out[int(pid)] = (int(fields[19]), int(fields[11]) + int(fields[12]),
                             int(fields[13]) + int(fields[14]), comm, cmd)
        except Exception:
            continue
    return out


def window_top_cpu(p0, p1, window_s, n=8):
    """Top-N processes by CPU used BETWEEN two proc_cpu_ticks() snapshots, in % of
    one core over the window (runbook 29.2). Replaces the gate's old ps snapshot,
    whose %CPU is a lifetime average and named the wrong culprits (owlog29: a fresh
    JVM at '51 %', containerd at '39 %').

    'own' is the process's own CPU in the window; the own column over all processes
    sums to the host busy figure (less processes that started and exited inside the
    window). 'reaped' is CPU of children reaped in the window, over their whole
    lifetime, so it can include time before the window and is shown separately,
    never added to own. A pid seen in both snapshots with the same start time is the
    same process (comm is not used: kworkers rename themselves); otherwise it is new
    and its whole CPU so far counts. Returns header + rows, or None if a snapshot
    is empty."""
    if not p0 or not p1 or window_s <= 0:
        return None
    hz = float(os.sysconf("SC_CLK_TCK"))
    rows = []
    for pid, (start, own, reaped, comm, cmd) in p1.items():
        prev = p0.get(pid)
        if prev and prev[0] == start:
            d_own, d_reaped = own - prev[1], reaped - prev[2]
        else:
            d_own, d_reaped = own, reaped
        if d_own > 0 or d_reaped > 0:
            rows.append((d_own, d_reaped, pid, comm, cmd))
    rows.sort(key=lambda r: (r[0] + r[1], r[0]), reverse=True)
    pct = lambda t: 100.0 * t / hz / window_s
    out = ["own%1core  reaped%1core      PID  COMMAND"]
    for d_own, d_reaped, pid, comm, cmd in rows[:n]:
        out.append("%9.1f  %12.1f  %7d  %s  %s" % (pct(d_own), pct(d_reaped), pid, comm, cmd[:160]))
    return out


def ambient_load_check(window_s, max_pct, quiet_gate=True):
    """Measure whole-host background CPU over a window BEFORE any measurement
    and fail loud if the box is not quiet -- the automated replacement for the
    runbook's manual `uptime`/`ps aux` precondition.

    Why: runbook §1 documents that a ~2.8-core background agent (opencode at
    276% CPU, 1.1 GB RSS on 2026-08-07) contaminated host_saturation_pct and
    drifted Fn's cp_dynamic_share_pct ~0.3-1 pp via cache pollution, context
    switching, and DVFS -- second-order channels even the cgroup CPU-time ratio
    is not immune to. busy_pct is 0..100 (100 = every logical core in scope
    fully busy). Hard-fails above max_pct unless quiet_gate=False (exploratory
    runs and the contamination A/B tool, which needs the dirty leg to run).

    Returns (busy_pct, top_ps_snapshot) for provenance."""
    p0 = proc_cpu_ticks()
    t0 = host_cpu_busy_total()
    w0 = time.monotonic()
    time.sleep(window_s)
    t1 = host_cpu_busy_total()
    p1 = proc_cpu_ticks()
    elapsed = time.monotonic() - w0
    busy_pct = None
    if t0 and t1:
        d_busy = t1[0] - t0[0]
        d_total = t1[1] - t0[1]
        if d_total > 0:
            busy_pct = d_busy / d_total * 100.0
    top = window_top_cpu(p0, p1, elapsed if elapsed > 0 else window_s)
    if top is None:
        top = ps_top_snapshot()
    label = ("%.1f%%" % busy_pct) if busy_pct is not None else "n/a"
    print("[quiet-gate] ambient host busy over %gs window: %s (threshold %.1f%%)"
          % (window_s, label, max_pct))
    if top:
        print("[quiet-gate] top CPU processes over the window (100 = one core):\n"
              + "\n".join(top))
    if quiet_gate and busy_pct is not None and busy_pct > max_pct:
        print("FATAL: box not quiet (ambient %.1f%% > %.1f%%). Background load "
              "(agents incl. opencode, heavy apps) drifts the headline share "
              "~0.5-1 pp even through the cgroup ratio (runbook §1). Quit "
              "background processes and rerun, or pass --no-quiet-gate to "
              "override (exploratory/contamination-AB only)." % (busy_pct, max_pct))
        sys.exit(1)
    return busy_pct, top


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


_CPU_COUNT_OVERRIDE = None  # set from --cpu-count-override / SAQEF_CPU_COUNT_OVERRIDE


def cpu_count():
    """Physical/logical CPU count used for saturation ceilings.

    Reads /proc/cpuinfo (whole-machine count) unless overridden. An override is
    required for any experiment that restricts the platform to fewer cores via
    cgroup/cpuset/taskset rather than a kernel boot parameter (nr_cpus=/maxcpus=):
    without it, host_saturation_pct = busy_ticks / (cpu_count()*window) stays
    diluted by the idle, un-pinned cores and can never trip the 85% saturated
    gate even at true 100% saturation of the pinned cores."""
    if _CPU_COUNT_OVERRIDE is not None:
        return _CPU_COUNT_OVERRIDE
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


def _image_repo_basename(image):
    """Registry/path- and tag-stripped repository name, e.g.
    'localhost:5000/saqef/kn-hello:0.0.1' -> 'kn-hello'. Used for EXACT
    fn_images/cp_images allowlist matching so Fn/OpenFaaS's 'hello' allowlist
    cannot substring-match inside Knative's 'kn-hello' image (manifest #1,
    see AGENTS.md) -- raw substring containment made that collision reachable
    the moment docker/containerd resolves a tag where it currently shows a
    bare digest for k3s-managed containers; exact basename match closes it by
    construction instead of relying on that quirk."""
    repo = image.split("@", 1)[0]     # drop a digest suffix, if present
    repo = repo.rsplit("/", 1)[-1]    # drop registry/path
    repo = repo.split(":", 1)[0]      # drop tag
    return repo.lower()


def _class_matches(name, image, labels, subs, img_subs, lbl_keys):
    """True if a container matches ANY of the name/image/label signals."""
    if subs and any(s in name.lower() for s in subs):
        return True
    if img_subs and image and _image_repo_basename(image) in {s.lower() for s in img_subs}:
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
# W1 payload workload (runbook §28): when SAQEF_BODY_FILE names a file, every
# request (warm-up, verify, measured window, either load generator) is a
# text/plain POST of that file's bytes instead of a bare GET. Unset = GET, so
# the CPU-bound corpus path is unchanged.
_BODY_PATH = os.environ.get("SAQEF_BODY_FILE") or None
_BODY = open(_BODY_PATH, "rb").read() if _BODY_PATH else None


def body_identity():
    """(bytes, sha256) of the request body, or (0, None) for GET."""
    if _BODY is None:
        return 0, None
    return len(_BODY), hashlib.sha256(_BODY).hexdigest()


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
                if _BODY is not None:
                    h = dict(headers or {}, **{"Content-Type": "text/plain"})
                    req = urllib.request.Request(url, data=_BODY, headers=h, method="POST")
                else:
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


def run_hey(url, total, concurrency, deadline_s=None, headers=None, timeout_ms=30000,
            qps=None):
    """Run hey (Go load generator) as a subprocess; keeps the harness's own CPU
    out of host accounting. Returns a results dict, or None if hey is missing/fails.
    QoS is computed from hey's per-request CSV rows: rps, latency percentiles,
    status distribution.

    qps (added for the cold-start / rate-controlled experiments): AGGREGATE target
    request rate. hey's own -q flag is PER WORKER, so it is divided by
    concurrency here. Without this, any rate-limited run had no way to stay on
    hey and silently fell back to the Python ThreadPoolExecutor generator, whose
    own threads land inside host_cpu_sec and corrupt host_saturation_pct /
    host_plausible -- the exact accounting hey exists to protect.

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
        # Unreachable from run_once (it refuses to start without hey when
        # --loadgen hey is requested); kept for direct callers.
        print("hey: binary not found on PATH (wanted --loadgen hey); falling back")
        return None
    # hey's -t is SECONDS per request (default 20), not milliseconds: passing
    # the raw ms value (30000) was silently a 30,000-second timeout. Convert.
    cmd = ["hey", "-n", str(total), "-c", str(concurrency),
           "-t", str(max(1, timeout_ms // 1000)), "-o", "csv"]
    if qps and qps > 0:
        # -q is QPS PER WORKER in hey; aggregate rate = q * concurrency.
        per_worker = qps / float(concurrency)
        if per_worker <= 0:
            print("hey: qps %.3f with concurrency %d rounds to 0 per worker; "
                  "not rate-limiting" % (qps, concurrency))
        else:
            cmd += ["-q", ("%.6g" % per_worker)]
    if headers:
        for k, v in headers.items():
            cmd += ["-H", "%s: %s" % (k, v)]
    if _BODY_PATH:
        cmd += ["-m", "POST", "-T", "text/plain", "-D", _BODY_PATH]
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
    """Return a callable reading cumulative CPU seconds summed across ALL
    control-plane containers matching cp_sub, or None if none mappable.
    The container set is re-resolved on every call (so swarm task restarts
    cannot wedge the reader), and the sum matches what sample_totals adds for
    the cp buckets - the delta-check must compare like for like, and a
    multi-container control plane (OpenFaaS = 6 containers) is the norm.
    Each matched container's cgroup-mapping status is recorded on
    reader.map (name -> 'ok' / 'read-failed' / 'unmappable') and logged, so a
    partial silent mapping failure can never masquerade as a valid delta."""
    if not cp_sub:
        return None
    mapping = {}

    def read_total():
        out = run("docker ps --format '{{.Names}}'")
        if out.returncode != 0:
            return None
        total = 0.0
        mapped = 0
        for nm in (s.strip() for s in out.stdout.splitlines()):
            if any(s in nm.lower() for s in cp_sub):
                cid = run("docker inspect -f '{{.Id}}' %s" % nm).stdout.strip()
                d = container_cgroup_dir(cid) if cid else None
                status = "unmappable"
                if d:
                    v = read_cpu_cumulative(d)
                    if v is not None:
                        total += v
                        mapped += 1
                        status = "ok"
                    else:
                        status = "read-failed"
                mapping[nm] = status
        return total if mapped else None

    read_total.map = mapping
    first = read_total()
    ok = sorted(n for n, s in mapping.items() if s == "ok")
    bad = sorted(n for n, s in mapping.items() if s != "ok")
    print("[delta-check] CP cgroup mapping: %d/%d mapped (%s)%s"
          % (len(ok), len(mapping), ", ".join(ok) if ok else "NONE",
             "; UNMAPPED: " + ", ".join(bad) if bad else ""))
    if first is None:
        return None
    return read_total


# ---------------------------------------------------------------------------
def container_cgroup_dirs(cid):
    """(cpu_dir, mem_dir) for a container from ONE 'docker inspect'.

    The CPU and memory dirs both come from the same /proc/<pid>/cgroup file, so
    a single PID lookup suffices. Returns (None, None) if the PID is
    unavailable or the file cannot be parsed. cgroup v1 'cpu'/'memory'
    controller hierarchies and the v2 unified hierarchy are both handled.
    """
    pid = run("docker inspect -f '{{.State.Pid}}' %s" % cid).stdout.strip()
    if not pid or not pid.isdigit():
        return None, None
    cdir = mdir = None
    try:
        with open("/proc/%s/cgroup" % pid) as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) != 3:
                    continue
                if parts[1] == "":  # v2 unified
                    u = "/sys/fs/cgroup" + parts[2]
                    return (cdir or u), (mdir or u)
                if cdir is None and parts[1] in ("cpu", "cpu,cpuacct", "cpuacct"):
                    cdir = "/sys/fs/cgroup/" + parts[1] + parts[2]
                if mdir is None and "memory" in parts[1]:
                    mdir = "/sys/fs/cgroup/" + parts[1] + parts[2]
    except Exception:
        pass
    return cdir, mdir


def container_cgroup_dir(cid):
    """cgroup dir for a container, or None (cgroup v1 'cpu' or v2 unified)."""
    return container_cgroup_dirs(cid)[0]


def container_mem_cgroup_dir(cid):
    """cgroup dir for a container's memory controller, or None.
    v2: unified dir (same as cpu). v1: 'memory' controller hierarchy."""
    return container_cgroup_dirs(cid)[1]


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


def docker_sampler(samples, stop, first_sample, rescan_s=0.25, sample_s=0.05):
    """Streaming `docker stats` sampler (~1 Hz). Samples: (t, {name: (cpu_pct, mem_mb)}).
    rescan_s/sample_s are unused here (docker stats streams all containers
    continuously); they exist so start_sampler can pass the same arg tuple to
    either sampler."""
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


_CGROUP_SCOPE_RE = re.compile(r"^(?:docker|crio|libpod)-([0-9a-f]{64})\.scope$")


def discover_cgroup_container_dirs(root="/sys/fs/cgroup", max_depth=6):
    """Discover (container_id, cgroup_dir) pairs by walking the cgroup tree.

    A container's cgroup directory is already named after its ID, so this needs
    no subprocess at all -- 'docker ps' costs ~60 ms of host CPU per call, and
    paying that 4x/s made the instrument itself a multi-core load. Only the
    cgroup v2 unified layout is recognised here; callers fall back to
    'docker ps' when this returns nothing (e.g. cgroup v1 hierarchies, or a
    runtime whose cgroup dirs are not visible under root).
    """
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, _ in os.walk(root):
        if dirpath[len(root):].count(os.sep) >= max_depth:
            dirnames[:] = []
        keep = []
        for d in dirnames:
            m = _CGROUP_SCOPE_RE.match(d)
            if m:
                out.append((m.group(1), os.path.join(dirpath, d)))
            else:
                keep.append(d)
        dirnames[:] = keep
    return out


def container_name(cid):
    """(name, birth_epoch_s) for an ID, or (None, None).

    Name and creation time come from a SINGLE inspect: the birth time is what
    lets the consumer recover the CPU a container burned between being created
    and first being sampled, which is otherwise dropped (see sample_totals).
    Asking for it separately would double the spawn count for no new
    information, so the two templates are deliberately combined."""
    out = run("docker inspect -f '{{.Name}}|{{.Created}}' %s" % cid).stdout.strip()
    if not out:
        return None, None
    name, _, created = out.partition("|")
    born = None
    if created.strip():
        try:
            # RFC3339 -> epoch. fromisoformat handles the offset docker emits.
            born = datetime.datetime.fromisoformat(
                created.strip().replace("Z", "+00:00")).timestamp()
        except Exception:
            born = None      # unknown birth -> fall back to dropping that slice
    return (name.lstrip("/") or None), born


def cgroup_sampler(samples, stop, first_sample, rescan_s=0.25, sample_s=0.05):
    """Direct cpu.stat + memory.current sampler. Stores RAW cumulative CPU
    seconds and current mem MB per container ('cum' samples); the consumer
    differences cumulative CPU using true timestamps, so the result is exact
    regardless of sampling cadence (slow cgroup reads, docker rescans,
    spurious wakeups). Bails on the FIRST scan if any container cannot be
    mapped -> caller falls back to the docker sampler.
    rescan_s bounds the blind spot for containers that are born and die between
    two rescans (shrink for scale-to-zero platforms with ephemeral containers).
    sample_s is the cgroup-read cadence; it is a real host cost (two file reads
    per container per sample) and buys nothing above ~20 Hz, because CPU is
    recovered by differencing cumulative counters rather than by integrating a
    rate. The old fixed 0.01 s wait pinned the sampler near 100 Hz.

    Container discovery is a pure cgroup-filesystem walk, and each container's
    name is resolved with at most one 'docker inspect' for its whole lifetime
    (cached by ID). The original loop instead ran 'docker ps' plus two
    'docker inspect' subprocesses for every container on every scan, which cost
    ~3 cores of host CPU -- CPU attributed to no measured cgroup, so it never
    showed up in cp/fn, but it inflated host_saturation_pct, polluted the
    host-residual check, and stole ~40% of the box from the system under test."""
    cache = {}  # container ID -> (name, cpu_cgroup_dir, mem_cgroup_dir, born)

    def resolve(cid, cdir=None):
        """Cached name+cgroup+birth lookup. One inspect per container ID, ever."""
        hit = cache.get(cid)
        if hit is None:
            c2, m2 = container_cgroup_dirs(cid)
            if c2 is None:
                return None
            nm, born = container_name(cid)
            if not nm:
                return None
            hit = (nm, c2, m2, born)
            cache[cid] = hit
        return hit

    def scan():
        d = {}
        live = set()
        found = discover_cgroup_container_dirs()
        if found:
            for cid, cdir in found:
                hit = cache.get(cid)
                if hit is None:  # new container: one inspect, then cached
                    nm, born = container_name(cid)
                    if not nm:
                        return None
                    hit = (nm, cdir, cdir, born)  # cgroup v2: unified cpu+memory
                    cache[cid] = hit
                live.add(cid)
                d[hit[0]] = (hit[1], hit[2], hit[3])
        else:
            # cgroup v1 / non-standard layout: fall back to asking docker.
            out = run("docker ps --format '{{.ID}}|{{.Names}}'")
            if out.returncode != 0:
                return None
            for line in out.stdout.strip().splitlines():
                cid, _, cname = line.partition("|")
                cid = cid.strip()
                if not cid:
                    continue
                hit = resolve(cid)
                if hit is None:
                    return None
                live.add(cid)
                d[cname.strip()] = (hit[1], hit[2])
        if not d:
            return None
        # Bound the cache: a container ID is never reused, so anything absent
        # from this scan is dead and can be dropped outright.
        for dead in [k for k in cache if k not in live]:
            del cache[dead]
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
        for cname, (cdir, mdir, born) in dirs.items():
            cum = read_cpu_cumulative(cdir)
            if cum is None:
                continue
            snap[cname] = (cum, read_mem_mb(mdir) if mdir else 0.0, born)
        if snap:
            samples.append((t, snap, "cum"))
            first_sample.set()
        stop.wait(sample_s)
    # Flush a final sample at stop time: on a saturated host the last scheduled
    # rescan can be starved past the window end, which truncated coverage on
    # fresh-reset runs (93.6%/92.9% on runs 1-2). A final read closes the gap
    # so the sampled span reaches the window end.
    snap = {}
    for cname, (cdir, mdir, born) in dirs.items():
        cum = read_cpu_cumulative(cdir)
        if cum is None:
            continue
        snap[cname] = (cum, read_mem_mb(mdir) if mdir else 0.0, born)
    if snap:
        samples.append((time.time(), snap, "cum"))


def sample_totals(samples, cp_sub, fn_sub="", cp_members=None, fn_members=None,
                  fn_allow_configured=False, window=None):
    """Reduce raw samples to a SampleTotals namedtuple:
    (cp_cpu_s, fn_cpu_s, cp_peak_mem_mb, covered_s, csv_rows, unclass_cpu_s,
     max_gap_s, n_samples, span_s).
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
    allowlists in run_once.

    max_gap_s/n_samples/span_s exist because 'coverage' as previously defined
    CANNOT FAIL: covered summed the dt of every consecutive pair (including a
    synthetic SAMPLE_S tail for the last sample) and was then clamped to wall,
    so any sampler that merely started before the load and stopped after it read
    100%. The real question is whether the sampler went blind mid-window, which
    is a MAX GAP, not a total.

    window=(t_start, t_end) restricts the totals to the actual load window. The
    sampler is started before the load and stopped after it, so without this
    every run folded in CPU accrued while the platform was idle but the sampler
    was already running, and the stop-time flush added a tail past the end. Both
    inflate cp_cpu_s/fn_cpu_s relative to wall_s. Direction of the share bias:
    UPWARD, not downward. cp_dynamic_share_pct = cp/(cp+fn), so adding a block of
    idle CPU to both numerator and denominator pulls the ratio toward that
    block's own cp:fn mix -- and idle time is CP-dominated (measured CP idle
    0.0025-0.0036 cpu-s/s vs Fn idle 0.0012-0.0095), i.e. far more CP-heavy than
    the load phase, where the function container is doing the work. The size of
    the error scales with how far the sampler overhangs the window, so it was
    not even a constant bias across concurrencies: measured overhang/wall is
    ~2-5% on the long lock4 legs but reaches 29-37% on the short quick-tier legs,
    which is precisely where the concurrency comparison lives.
    Cumulative counters are only known at sample instants, so the in-window
    portion of each interval is apportioned by time overlap; a sample entirely
    outside the window contributes nothing and a container absent from a sample
    still gets 0 rather than a negative delta.

    The interval a sample's CPU belongs to depends on the mode and this has to be
    exact, because the sampler starts BEFORE the load:
      'cum' - the delta cum[i]-cum[i-1] accrued over (t_prev, t], so it is
              prorated against THAT span, not against the forward one.
      'pct' - the rate was read at t and applies to (t, t_next].
    Using the forward interval for 'cum' instead shifted every delta one sample
    later, which put the whole pre-load stretch into the first in-window
    interval -- the exact leak the window is meant to remove.

    Snapshots are {name: (cum, mem)} or {name: (cum, mem, born_epoch)}. The
    third element is the container's CREATION time. A container first seen at
    sample i has a counter that already contains everything it burned since
    creation, and that entire birth-to-first-sample slice used to be discarded
    (prev is None -> delta 0). That is not a rounding error on scale-up
    platforms: Knative creates fn containers seconds into the run, and the
    slice for the LAST container to appear can be seconds of real CPU, silently
    undercounting fn_cpu and biasing cp_dynamic_share_pct UPWARD.
    With a known birth time the whole counter is credited over (born, t] and
    the existing overlap proration keeps only the in-window part, under the
    uniform-rate assumption that is already used for partial intervals. If the
    birth time is unknown the old behaviour (drop the slice) is kept, which is
    conservative in the sense of not inventing CPU."""
    cp_cpu = fn_cpu = unclass = 0.0
    cp_mem = 0.0
    covered = 0.0
    max_gap = 0.0
    n_samples = len(samples)
    fn_allow_active = fn_allow_configured or bool(fn_sub or fn_members)
    prev = {}
    prev_t = None
    csv_rows = []
    for i, (t, snap, mode) in enumerate(samples):
        last = i + 1 >= len(samples)
        tnext = samples[i + 1][0] if not last else t + SAMPLE_S
        # The span this sample's measurement covers.
        if mode == "cum":
            # first sample establishes the baseline: it has no delta to prorate
            span0 = prev_t
            span1 = t
            has_delta = prev_t is not None
        else:
            span0 = t
            span1 = tnext
            has_delta = True
        dt = max(span1 - span0, 0.01) if has_delta else 0.0
        frac = 1.0
        if window is not None and has_delta:
            w0, w1 = window
            overlap = min(span1, w1) - max(span0, w0)
            if overlap <= 0:
                # Entirely outside the load: no CPU, no coverage, no gap claim.
                for name, vsnap in snap.items():
                    if mode == "cum":
                        prev[name] = vsnap[0]
                prev_t = t
                continue
            frac = min(1.0, overlap / dt)
        if has_delta:
            covered += dt * frac
        # The synthetic SAMPLE_S tail is not a real observation, so it must not
        # be able to masquerade as a healthy cadence. A gap that lies wholly
        # outside the window says nothing about coverage of the window either.
        # A gap is the interval BETWEEN two real observations, so it is measured
        # forward from t to tnext and never uses the synthetic tail.
        if not last and (window is None or
                         min(tnext, window[1]) > max(t, window[0])):
            max_gap = max(max_gap, tnext - t)
        for name, vsnap in snap.items():
            v = vsnap[0]
            mem = vsnap[1]
            born = vsnap[2] if len(vsnap) > 2 else None
            # Which window fraction applies to THIS container. Normally the
            # sample's own forward/backward interval fraction. For a first-sight
            # birth credit the relevant span is (born, t], which is a different
            # interval, so it must carry its own factor -- applying `frac` as
            # well would prorate the slice twice.
            myfrac = frac
            if mode == "cum":
                cum = v
                old = prev.get(name)
                prev[name] = cum
                if old is not None and has_delta:
                    cpu_sec = max(cum - old, 0.0)
                    pct = (cpu_sec / dt) * 100.0 if dt else 0.0
                elif born is not None and born < t:
                    # First sight: credit the whole counter over (born, t], then
                    # keep only the part inside the load window. Assumes a
                    # uniform rate across the lifetime, the same assumption the
                    # partial-interval proration already makes.
                    life = t - born
                    cpu_sec = cum
                    pct = (cpu_sec / life) * 100.0 if life else 0.0
                    if window is not None:
                        ov = min(t, window[1]) - max(born, window[0])
                        myfrac = min(1.0, max(0.0, ov) / life) if life else 0.0
                    else:
                        myfrac = 1.0
                else:
                    cpu_sec = 0.0        # no baseline and no birth time
                    pct = 0.0
            else:
                pct = v
                cpu_sec = (pct / 100.0) * dt
            cpu_sec *= myfrac
            if myfrac < 1.0:
                pct *= myfrac
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
        prev_t = t
    span_s = (samples[-1][0] - samples[0][0]) if n_samples else 0.0
    return SampleTotals(cp_cpu, fn_cpu, cp_mem, covered, csv_rows, unclass,
                        max_gap, n_samples, span_s)


def start_sampler(mode="docker", rescan_s=0.25, sample_s=0.05):
    """Start a background sampler. Returns (samples, stop, first_sample, thread).
    cgroup mode that cannot map containers returns (None,)*4 -> caller falls back."""
    samples, stop, first_sample = [], threading.Event(), threading.Event()
    target = cgroup_sampler if mode == "cgroup" else docker_sampler
    th = threading.Thread(target=target, args=(samples, stop, first_sample, rescan_s, sample_s),
                          daemon=True)
    th.start()
    first_sample.wait(timeout=6)
    if not first_sample.is_set():
        stop.set()
        th.join(timeout=2)
        if mode == "cgroup":
            return None, None, None, None
    return samples, stop, first_sample, th


def assert_platform_isolation(platform, forbidden_services="", forbidden_containers=""):
    """Fail loud if the OTHER platform is still up. Turns the documented teardown
    discipline ('docker service rm hello' before Fn; fnserver torn down before
    OpenFaaS) into an enforced precondition instead of a remembered step.
    Why this is necessary: BOTH platforms' function images are named 'hello', so
    the --fn-images allowlist substring match cannot distinguish Fn's ULID-named
    function containers from OpenFaaS's swarm replicas. A leftover OpenFaaS
    'hello' service (deployed OUTSIDE the stack -- 'docker stack rm openfaas'
    does not remove it) would have its replicas silently folded into fn_cpu and
    taint the headline number. Fn never uses swarm services, so ANY running
    service during a Fn session is contamination.

    Data-driven by default: the CLI passes the adapter's IsolationPolicy as
    --forbidden-services / --forbidden-containers, so every platform (incl.
    OpenWhisk) gets the same defense-in-depth check at measurement time. When
    those args are absent (legacy shell runners), fall back to the historical
    hardcoded fn/openfaas checks. Returns (ok, message)."""
    fsvc = [s.strip() for s in forbidden_services.split(",") if s.strip()]
    fcnt = [c.strip() for c in forbidden_containers.split(",") if c.strip()]
    if fsvc or fcnt:
        offenders = []
        if fsvc:
            out = run("docker service ls --format '{{.Name}}'")
            if out.returncode == 0:
                services = [l.strip() for l in out.stdout.splitlines() if l.strip()]
                if "*" in fsvc:
                    bad = services
                else:
                    bad = [s for s in services if any(f in s for f in fsvc)]
                if bad:
                    offenders.append("swarm service(s): %s" % ", ".join(bad))
        if fcnt:
            inv = docker_inventory()
            bad = [n for n in inv if any(f in n for f in fcnt)]
            if bad:
                offenders.append("container(s): %s" % ", ".join(bad))
        if offenders:
            return False, (
                "platform isolation check FAILED for %s: %s running during the session; "
                "tear it down first (docker service rm hello / docker rm -f fnserver)"
                % (platform, "; ".join(offenders)))
        return True, ""
    # --- legacy fallback (pre-data-driven shell runners) -----------------------
    # k3s/Knative stays resident on this box as "the substrate" across every
    # platform's session (see platforms/knative.py) -- checking for ANY
    # "k8s_"-prefixed container would permanently block Fn/OpenFaaS even with
    # 'hello' properly torn down (confirmed live 2026-08-08). Check
    # specifically for the 'hello' ksvc's two per-replica pod containers
    # instead: that is the actual leftover that can misclassify (a bare
    # "gateway" cp_containers substring matching "kourier-gateway"; Fn's
    # "hello" fn_images substring matching Knative's "kn-hello" image).
    knative_leftover = [n for n in docker_inventory()
                        if "user-container" in n or "queue-proxy" in n]
    if platform == "fn":
        out = run("docker service ls --format '{{.Name}}'")
        if out.returncode == 0:
            services = [l.strip() for l in out.stdout.splitlines() if l.strip()]
            if services:
                return False, (
                    "platform isolation check FAILED: %d swarm service(s) running during an Fn "
                    "session (Fn never uses swarm): %s. Likely offender: the OpenFaaS function "
                    "service 'hello' (outside the stack). Run: docker service rm hello"
                    % (len(services), ", ".join(services)))
        if knative_leftover:
            return False, (
                "platform isolation check FAILED: %d leftover Knative/k3s container(s) running "
                "during an Fn session: %s. Run: python3 saqef teardown --platform knative"
                % (len(knative_leftover), ", ".join(knative_leftover[:5])))
    elif platform == "openfaas":
        inv = docker_inventory()
        if "fnserver" in inv:
            return False, (
                "platform isolation check FAILED: Fn's 'fnserver' container is still running "
                "during an OpenFaaS session. Tear Fn down first (docker rm -f fnserver).")
        if knative_leftover:
            return False, (
                "platform isolation check FAILED: %d leftover Knative/k3s container(s) running "
                "during an OpenFaaS session: %s. Run: python3 saqef teardown --platform knative"
                % (len(knative_leftover), ", ".join(knative_leftover[:5])))
    return True, ""


def run_once(args, cp_sub):
    """One full measurement window (warmup + sampler + load). Returns summary dict."""
    ok, why = assert_platform_isolation(args.platform,
                                        args.forbidden_services, args.forbidden_containers)
    if not ok:
        sys.exit("ERROR: " + why)
    headers = None
    if args.auth:
        user, _, pw = args.auth.partition(":")
        headers = {"Authorization": "Basic " + base64.b64encode(
            ("%s:%s" % (user, pw)).encode()).decode()}

    if args.warmup > 0 and not args.idle_probe:
        run_load(args.url, args.warmup, min(args.concurrency, args.warmup),
                 headers=headers, interarrival_ms=args.interarrival_ms)
        time.sleep(2)  # let the hot function container register in docker stats

    # Pre-run inventory snapshot, unioned with the post-run one below (fixes:
    # image/label classification was previously derived from a SINGLE
    # post-run docker_inventory() snapshot applied retroactively to the whole
    # sampling window. Any container that started before the window but
    # exited before that final snapshot (e.g. a recycled OpenWhisk action
    # container) would silently drop into "unclassified" instead of "fn" for
    # its entire CPU history, since fn_containers is empty for platforms with
    # no stable name pattern -- classification then depends ENTIRELY on the
    # image/label allowlist. Currently ~0 measured impact (containers persist
    # for the whole window in every citable run), but structurally fragile.
    # A requested-but-missing hey used to fall back to the python loadgen INSIDE
    # the window and only flag loadgen_fallback afterwards: a whole unattended
    # session could silently switch instruments. Missing binary = refuse to start.
    # (A hey that exists but fails mid-run still falls back and is gated by
    # run_lock_session's LOADGEN FALLBACK check.)
    if args.loadgen == "hey" and not args.idle_probe and shutil.which("hey") is None:
        sys.exit("FATAL: --loadgen hey requested but no hey on PATH (%s); refusing to "
                 "measure with a different load generator" % os.environ.get("PATH", ""))
    loadgen_bin, loadgen_sha256 = loadgen_identity() if args.loadgen == "hey" else (None, None)
    inv_before = docker_inventory()
    rapl_start = rapl_energy()
    psys_start = psys_energy()
    psys_state = psys_status()
    etrace = EnergyTrace()
    etrace.start()
    samples, stop, first_sample, th = start_sampler(args.sampler, args.rescan_s, args.sample_s)
    if th is None:
        print("WARNING: %s sampler unavailable -> falling back to docker stats" % args.sampler)
        args.sampler = "docker"
        samples, stop, first_sample, th = start_sampler("docker")

    steal_before = steal_ticks()
    freq_before, governor = env_frequency()
    cp_read = cp_cgroup_reader(cp_sub) if args.delta_check else None
    cp_cum_before = cp_read() if cp_read else None
    # Host counter read as close to t0 as possible so the host window == the load
    # window (v9.11). It previously sat BEFORE the delta-check reader construction
    # (cp_cgroup_reader), which takes ~1.5 s on this box: host_window_s then came
    # out ~1.5 s longer than wall_s (11.77 vs 10.23 on the v3 rerun), so host_cpu_sec
    # included busy ticks from a non-load stretch. Self-consistent after v9.10, but
    # the headroom was exactly what inflated the old wall-based sat% to 112% on the
    # short windows. Moving the read here makes host_window_s == wall_s by ordering.
    host_before = host_cpu_ticks()
    t_host_before = time.perf_counter()

    # The sampler stamps every sample with time.time() (epoch seconds), so the
    # attribution window MUST be in that same time base or nothing overlaps it.
    # t0 stays perf_counter for measuring wall, because perf_counter is monotonic
    # and a wall-clock step (NTP) mid-run would otherwise corrupt the duration.
    # Two clocks, two jobs: t0 for the duration, t0_epoch for the window.
    t0 = time.perf_counter()
    t0_epoch = time.time()
    reqs = None
    ld = None
    wall_loadgen = None
    # Effective aggregate QPS for a rate-controlled run. --qps is explicit;
    # otherwise --interarrival-ms is translated (each of the `concurrency`
    # in-flight workers sleeps interarrival_ms per request, so the aggregate
    # rate is concurrency / interarrival_s). Translating here is what keeps a
    # cold-start experiment on hey: before this, --interarrival-ms only reached
    # run_load(), so passing it silently forced the Python generator.
    qps = args.qps or 0.0
    if args.interarrival_ms > 0:
        derived = args.concurrency * 1000.0 / args.interarrival_ms
        if qps > 0:
            print("WARNING: both --qps (%.3f) and --interarrival-ms (%.1f) given; "
                  "using --qps and ignoring --interarrival-ms" % (qps, args.interarrival_ms))
        else:
            qps = derived
    if args.loadgen == "hey" and qps > 0:
        print("rate-limited run: aggregate %.3f QPS at concurrency %d "
              "(hey -q %.4g per worker)" % (qps, args.concurrency, qps / args.concurrency))
    if args.idle_probe:
        time.sleep(args.duration)  # platform up, zero traffic -> static orchestration baseline
    elif args.loadgen == "hey":
        ld = run_hey(args.url, args.total, args.concurrency, deadline_s=args.duration,
                     headers=headers, qps=(qps or None))
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
    t_host_after = time.perf_counter()
    steal_after = steal_ticks()
    stop.set()
    th.join(timeout=10)
    etrace.stop()
    rapl_end = rapl_energy()
    psys_end = psys_energy()
    freq_after, _ = env_frequency()
    cp_cum_after = cp_read() if cp_read else None

    # --- energy attribution (Kepler-style CPU-time proportional) -------------
    # Classification members: any container matching the cp/fn name substring,
    # image, or label allowlists. Image/label signals are what make the fn
    # allowlist MEANINGFUL on platforms whose fn containers have opaque names
    # (Fn = ULIDs) - otherwise unclassified_cpu_s is guaranteed 0.0 regardless
    # of what is running. Defaults preserve denylist behavior when no fn
    # allowlist is configured (back-compat, documented).
    inv_after = docker_inventory()
    inv = {**inv_before, **inv_after}   # union: a container gone by run-end still classifies
    cp_members = {n for n, (img, lbls) in inv.items()
                  if _class_matches(n, img, lbls, (), args.cp_images, args.cp_labels)}
    fn_members = {n for n, (img, lbls) in inv.items()
                  if _class_matches(n, img, lbls, (), args.fn_images, args.fn_labels)}
    fn_allow_configured = bool(args.fn_containers or args.fn_images or args.fn_labels)
    (cp_cpu_s, fn_cpu_s, cp_peak_mem_mb, covered_s, csv_rows, unclass_cpu_s,
     max_gap_s, n_samples, span_s) = sample_totals(
        samples, cp_sub, args.fn_containers, cp_members, fn_members,
        fn_allow_configured=fn_allow_configured,
        window=(t0_epoch, t0_epoch + wall))
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
    if n_samples < 2:
        print("ERROR: only %d sample(s) collected - the sampler did not observe the window "
              "and NO cpu attribution from this run can be trusted" % n_samples)
    elif max_gap_s > args.max_sample_gap:
        print("WARNING: sampler went blind for %.2f s mid-window (limit %.2f s) at ~%.1f Hz; "
              "CPU accrued during that gap is still counted from cumulative counters, but "
              "peak memory and any rate-derived quantity are unreliable"
              % (max_gap_s, args.max_sample_gap, n_samples / span_s if span_s else 0))
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
    host_window_s = None
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
        # Saturation: busy host time relative to the physical ceiling, measured
        # over the host's OWN sampling window (t_host_before..t_host_after),
        # NOT the load wall. /proc/stat busy ticks over a window W can never
        # exceed cpu_count()*W, so this definition is structurally
        # self-consistent: host_plausible can only trip on a REAL anomaly
        # (cpu_count()/proc-stat CPU-count mismatch, counter drift), never on
        # window-edge alignment - which at short wall windows used to inflate
        # sat% past 100% because the host window is sampled a few ms before/
        # after the load window and that fixed edge is a larger fraction of a
        # fast run. cpu_sec_ceiling below keeps the container-side invariant
        # (cp+fn <= cpu_count()*wall) on the load window.
        ceiling = cpu_count() * wall          # container-side ceiling (unchanged)
        host_window_s = (t_host_after - t_host_before) if (t_host_after is not None and t_host_before is not None) else wall
        ceiling_host = cpu_count() * host_window_s
        if ceiling_host > 0:
            host_saturation_pct = round(host_cpu_sec / ceiling_host * 100.0, 1)
            host_plausible = host_cpu_sec <= ceiling_host * 1.05
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
    # args.ci is gCO2 PER KILOwatt-hour; energy here is Joules. J -> kWh is
    # J / 3.6e6 (NOT the old Wh conversion -- divide by 3600 then multiply by a
    # per-kWh intensity leaves a spurious 1000x in every gCO2 figure, the
    # historical unit bug, fixed 2026-08-06).
    kwh = e_total / 3.6e6
    op_gco2 = kwh * args.ci * PUE
    cp_kwh = e_cp / 3.6e6
    cp_gco2 = cp_kwh * args.ci * PUE
    n_compliant = int(round(compliance * n)) if compliance is not None and n else 0
    kpi = (op_gco2 / n_compliant) if n_compliant else float("nan")
    # Marginal KPI: dynamic (load-created) carbon only, NOT the idle baseline.
    # The operational KPI is ~90%+ idle-power-dominated, so it is extremely
    # sensitive to wall-clock duration (a 3x wall swing moves it 3x). The
    # dynamic-only figure is the wall-independent per-invocation cost of serving.
    kpi_dynamic = (e_dynamic / 3.6e6 * args.ci * PUE) / n_compliant if n_compliant else float("nan")
    embodied_per_gb = DRAM_EMBODIED_G_PER_GB / (LIFESPAN_YEARS * 365 * 24)

    # --- RAPL validation -------------------------------------------------------
    rapl_validation = None
    rapl_wrap = "none"
    # Bound before the branch: summary always emits e_rapl_j, and a box with no
    # RAPL (or a failed read) never enters it.
    e_rapl = None
    e_psys, psys_wrap = (rapl_correct_wrap(psys_end - psys_start, psys_max_range_j)
                         if psys_start is not None and psys_end is not None else (None, None))
    if rapl_start is not None and rapl_end is not None:
        e_rapl, rapl_wrap = rapl_correct_wrap(rapl_end - rapl_start)
        rapl_validation = (abs(e_total - e_rapl) / e_rapl * 100
                           if e_rapl is not None and e_rapl > 0 else None)

    summary = {
        "platform": args.platform,
        "url": args.url,
        "mode": "idle" if args.idle_probe else "load",
        "wall_s": round(wall, 2),
        "wall_harness_s": round(wall_harness, 2),
        "sampling_covered_s": round(covered_s, 2),
        # sampling_covered_s CANNOT FAIL and must not be cited as validation:
        # it summed the dt of every consecutive sample pair plus a synthetic
        # SAMPLE_S tail and was then clamped to wall, so any sampler that merely
        # started before the load and stopped after it reads 100%. The fields
        # below are the ones that actually say whether the instrument stayed
        # awake: max_gap_s is the longest blind interval, n_samples/span_s give
        # the achieved rate, and sampling_gap_ok is the gate that can fail.
        "sampling_max_gap_s": round(max_gap_s, 3),
        "sampling_n_samples": n_samples,
        "sampling_span_s": round(span_s, 2),
        "sampling_rate_hz": round(n_samples / span_s, 2) if span_s > 0 else None,
        "sampling_gap_ok": bool(n_samples >= 2 and max_gap_s <= args.max_sample_gap),
        # total_requested lets a gate check "did this run finish its count-bound
        # protocol" after the fact (requests can fall short of it on a loadgen
        # timeout/fallback, e.g. the 2026-08-13 OpenWhisk --duration regression,
        # TROUBLESHOOTING_RUNBOOK.md #11) -- args.total wasn't previously
        # recorded anywhere in the summary.
        "requests": n, "successes": ok, "total_requested": args.total,
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
                        "idle_band": {str(w): round((w * wall + e_dynamic) / 3.6e6 * args.ci * PUE, 3)
                                      for w in (15, 30, 45)}},
        "model": {"idle_w": args.idle_w, "busy_core_w": P_BUSY_CORE_W, "pue": PUE, "ci": args.ci},
        "sensitivity": {
            "cp_dynamic_share_pct_by_busy_w": {str(w): round(cp_cpu_s / (cp_cpu_s + fn_cpu_s) * 100.0, 2)
                                               if (cp_cpu_s + fn_cpu_s) else None
                                               for w in (2.0, 3.5, 5.0)},
            "dynamic_energy_J_by_busy_w": {str(w): round((cp_cpu_s + fn_cpu_s) * w, 1)
                                           for w in (2.0, 3.5, 5.0)},
            "op_carbon_gCO2_by_busy_w": {str(w): round((args.idle_w * wall + (cp_cpu_s + fn_cpu_s) * w)
                                                       / 3.6e6 * args.ci * PUE, 3)
                                         for w in (2.0, 3.5, 5.0)},
        },
        # Post-carbon-fix magnitudes are ~1e-5 g per invocation; round(..., 4)
        # collapsed them to 0.0 (looked "fine" at the old 1000x-inflated scale).
        "kpi_gco2_per_slo_compliant_inv": round(kpi, 8),
        "kpi_gco2_per_inv_dynamic": round(kpi_dynamic, 8),
        "embodied_dram_g_per_gb_h": round(embodied_per_gb, 4),
        "host_cpu_sec": round(host_cpu_sec, 2) if host_cpu_sec is not None else None,
        "host_window_s": round(host_window_s, 3) if host_window_s is not None else None,
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
        "delta_check_map": dict(getattr(cp_read, "map", {}) or {}) if cp_read else None,
        "env": {"cpu_count": cpu_count(), "governor": governor,
                "freq_mhz_before": round(freq_before, 1) if freq_before else None,
                "freq_mhz_after": round(freq_after, 1) if freq_after else None,
                "sampler": args.sampler,
                "loadgen": "hey" if ld is not None else "py",
                "loadgen_requested": args.loadgen,
                "loadgen_fallback": bool(args.loadgen == "hey" and ld is None),
                "loadgen_bin": loadgen_bin,
                "loadgen_sha256": loadgen_sha256,
                # Rate-control provenance: a run that INTENDED to be
                # rate-limited but silently ran unthrottled is otherwise
                # indistinguishable from a correct one in the committed JSON
                # (same bug class as the invalid freeze-ablation `=0` leg).
                # target_qps is the aggregate rate actually requested; it is 0.0
                # for every unthrottled citable run, so pre-existing runs keep
                # reading as unthrottled.
                "target_qps": round(qps, 4),
                "interarrival_ms": args.interarrival_ms,
                # W1 payload provenance (runbook §28): 0 / None = bare GET.
                "payload_bytes": body_identity()[0],
                "payload_sha256": body_identity()[1],
                # W2 provenance (runbook §30): the swapped-in arm, e.g. "mem_dram";
                # None for every workload that does not set it.
                "workload_variant": os.environ.get("SAQEF_WORKLOAD_VARIANT") or None},
        # One name for this value, not two. 90d153f added rapl_fit_err_pct as an
        # alias of rapl_validation_err_pct; the committed corpus already uses the
        # latter, so the alias bought nothing and invited the two to drift.
        # rapl_fit_err_pct is READ (never written) for the 2026-10-02 legs that
        # were produced with it; see run_lock_session.sh.
        "rapl_validation_err_pct": round(rapl_validation, 2) if rapl_validation is not None else None,
        "e_model_j": round(e_total, 3),
        "e_rapl_j": round(e_rapl, 3) if e_rapl is not None else None,
        "rapl_wrap": rapl_wrap,
        "rapl_available": rapl_start is not None,
        "e_psys_j": round(e_psys, 3) if e_psys is not None else None,
        "psys_wrap": psys_wrap,
        "psys_status": psys_state,
        "_energy_trace": etrace.rows,
        # Re-analysability (1a). The window is the single input that decides
        # which samples count, so a run whose JSON does not carry it cannot be
        # re-derived by an offline tool -- it can only be trusted. That is
        # exactly the trap of the 2026-08-14/15 corpus, whose only
        # re-attribution lever was inferring (first_sample, first_sample+wall)
        # because t0_epoch was never written. These fields make the choice
        # explicit and auditable instead of inferred.
        "attribution": {
            "t0_epoch": round(t0_epoch, 6),
            "window_start_epoch": round(t0_epoch, 6),
            "window_end_epoch": round(t0_epoch + wall, 6),
            "wall_s": round(wall, 3),
            # Every assumption the offline re-attributer needs to reproduce this
            # run, in one place. Anything that changes cp/fn totals belongs
            # here, not buried in argparse defaults.
            "sampler": args.sampler,
            "sample_s": args.sample_s,
            "rescan_s": args.rescan_s,
            "fn_containers": list(args.fn_containers),
            "fn_images": list(args.fn_images),
            "fn_labels": list(args.fn_labels),
            "cp_images": list(args.cp_images),
            "cp_labels": list(args.cp_labels),
            # cp_sub is the name-substring fallback applied in sample_totals().
            # Without it a replay cannot reproduce CP classification for
            # containers cp_images/cp_labels did not already resolve.
            "cp_sub": list(cp_sub),
            "cp_members": sorted(cp_members),
            "fn_members": sorted(fn_members),
            "fn_allow_configured": fn_allow_configured,
            # Named docker_inventory, not container_inventory: the summary
            # already has a top-level container_inventory (live names at
            # write time). This is the PRE/POST-RUN UNION with images and
            # labels -- the classification input that was actually applied.
            "docker_inventory": {n: [img, sorted(lbls)]
                                 for n, (img, lbls) in sorted(inv.items())},
        },
        "harness": {"git_rev": harness_git_rev(), "git_dirty": harness_git_dirty()},
    }
    if ld is not None:
        summary["loadgen"] = {"source": "hey", "rps": ld["rps"],
                              "avg_ms": round(ld_avg, 2) if ld_avg is not None else None,
                              "errors": ld_errors,
                              "wall_s": round(wall_loadgen, 2) if wall_loadgen else None}
    return summary, all_snaps, reqs, ld, list(samples)


RAW_HDR = ["t", "container", "mode", "cpu_cum_s", "cpu_pct", "mem_mb", "born_epoch"]


def write_samples_raw(path, raw_samples):
    """Write the UNCLIPPED sampler output next to samples.csv.

    Why this file exists. samples.csv is written downstream of the window clip:
    sample_totals() drops samples entirely outside the load window, scales
    partial intervals by their overlap fraction, and hands downstream only a
    normalized percent-rate. That is the right artifact for reading a number --
    and the wrong artifact for auditing one. Two separate problems follow:

      1. A clip bug is unrecoverable. If the window is later shown to be wrong,
         samples.csv no longer holds the CPU that was discarded, so no offline
         re-attribution can put it back. This is exactly what happened to the
         2026-08-14/15 corpus, where re-attribution worked ONLY because that
         harness wrote full-span unclipped rows; new runs must not lose the
         property that made the old ones salvageable.
      2. A window bug is unfalsifiable. With the raw counters on disk, anyone can
         re-run the attribution under a different window and see whether the
         headline moves. Without them, the window is an unfalsifiable assertion.

    So samples_raw.csv carries the sampler's actual observations -- cumulative
    CPU counters ('cum' mode, exact and cadence-independent) or instantaneous
    rates ('pct' mode, docker stats fallback) -- with NO window applied. Together
    with summary.json's attribution block (t0_epoch, window bounds, allowlists,
    git revision) this is the minimum needed to reproduce or challenge any
    number in summary.json offline.

    The birth time is kept because birth-to-first-sample CPU is unrecoverable
    once the counter is differenced away: at first sight the sampler sees a
    counter that already contains everything since creation, and only
    born_epoch lets that slice be apportioned. Knative creates function
    containers seconds into a run, so this is not a rounding detail there.

    Written unconditionally, so the artifact's presence can never itself be a
    confound (a run without it is anomalous, not silently different)."""
    if raw_samples is None:
        return False
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(RAW_HDR)
        for t, snap, mode in raw_samples:
            for name, vsnap in (snap or {}).items():
                cum = vsnap[0]
                mem = vsnap[1] if len(vsnap) > 1 else None
                born = vsnap[2] if len(vsnap) > 2 else None
                if mode == "cum":
                    w.writerow([round(t, 6), name, mode, cum, None, mem,
                                round(born, 6) if born else None])
                else:
                    w.writerow([round(t, 6), name, mode, None, cum, mem,
                                round(born, 6) if born else None])
    return True


def write_run(outdir, summary, all_snaps, reqs, raw_samples=None):
    os.makedirs(outdir, exist_ok=True)
    # Popped before summary.json/runs.json are written: the trace is a per-run
    # file, not a summary field (runs.json would otherwise carry every row).
    trace = summary.pop("_energy_trace", None)
    if trace:
        with open(os.path.join(outdir, "energy_trace.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_epoch", "pkg_energy_j", "psys_energy_j"])
            for t, pkg, ps in trace:
                w.writerow([round(t, 3), pkg, ps])
    with open(os.path.join(outdir, "summary.json"), "w") as f:
        json.dump(clean_json(summary), f, indent=2)
    with open(os.path.join(outdir, "samples.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "container", "cpu_pct", "mem_mb"])
        for t, snap in all_snaps:
            for name, (cpu, mem) in (snap or {}).items():
                w.writerow([round(t, 2), name, cpu, mem])
    write_samples_raw(os.path.join(outdir, "samples_raw.csv"), raw_samples)
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
    """Median of numeric leaves (statistics.median semantics); dict and list leaves are
    UNIONED across all runs (not first-run-only), so container pools that grow over a
    session (OpenWhisk's wsk0_N numbering) keep every run's entries; first value for
    strings/None. Non-finite values are skipped so a NaN from one run can't poison a median."""
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
            # Union of keys across ALL runs, not just items[0]'s: container
            # names (delta_check_map, container_labels) can differ run-to-run
            # for platforms whose container pool grows over a session
            # (OpenWhisk's wsk0_N action-container numbering), so restricting
            # to items[0]'s keys silently dropped later runs' entries from
            # the aggregate summary.json instead of merging them.
            keys = []
            seen = set()
            for it in items:
                if isinstance(it, dict):
                    for k in it:
                        if k not in seen:
                            seen.add(k)
                            keys.append(k)
            for k in keys:
                present = [it for it in items if isinstance(it, dict) and k in it]
                if not present:
                    continue
                sample = present[0][k]
                if isinstance(sample, dict):
                    out[k] = rec([it[k] for it in present])
                elif isinstance(sample, list):
                    # Lists must be unioned the same way dicts are: the
                    # container pool can grow run-to-run, so first-run-only
                    # would silently drop later runs' entries from the
                    # aggregate (the exact bug fixed for dicts above, applied
                    # to container_inventory). Dedup, preserve first-seen
                    # order, skip None/non-list values. Elements may themselves
                    # be lists/dicts (attribution.docker_inventory values are
                    # [image, [labels...]]), so dedup on a canonical JSON key
                    # rather than the element itself (unhashable -> TypeError
                    # after all N runs had finished, losing summary.json).
                    merged, seen = [], set()
                    for it in present:
                        v = it[k]
                        if isinstance(v, list):
                            for item in v:
                                key = json.dumps(item, sort_keys=True, default=str)
                                if key not in seen:
                                    seen.add(key)
                                    merged.append(item)
                    out[k] = merged
                elif isinstance(sample, (int, float)):
                    out[k] = med([it[k] for it in present])
                else:
                    out[k] = sample
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
    inv_before = docker_inventory()
    samples, stop, first_sample, th = start_sampler(args.sampler, args.rescan_s, args.sample_s)
    if th is None:
        print("WARNING: %s sampler unavailable -> docker" % args.sampler)
        args.sampler = "docker"
        samples, stop, first_sample, th = start_sampler("docker")
    t0_epoch = time.time()
    reqs = run_load(args.url, args.verify_n, min(args.concurrency, args.verify_n),
                    headers=headers, interarrival_ms=args.interarrival_ms)
    t1_epoch = time.time()
    stop.set()
    th.join(timeout=10)

    ok = sum(1 for o, _ in reqs if o)
    lats = sorted(1000.0 * l for _, l in reqs if _)

    def pct(p):
        return lats[min(len(lats) - 1, int(len(lats) * p))] if lats else float("nan")

    # Same classification and load window as run_once (runbook 29.4). Without them,
    # every non-cp container on the box counted as function from its cgroup's birth,
    # and verify reported ~9.8 s/inv for a ~6 ms handler (owlog29 leg logs).
    inv = {**inv_before, **docker_inventory()}
    cp_members = {n for n, (img, lbls) in inv.items()
                  if _class_matches(n, img, lbls, (), args.cp_images, args.cp_labels)}
    fn_members = {n for n, (img, lbls) in inv.items()
                  if _class_matches(n, img, lbls, (), args.fn_images, args.fn_labels)}
    cp_cpu_s, fn_cpu_s = sample_totals(
        samples, cp_sub, args.fn_containers, cp_members, fn_members,
        fn_allow_configured=bool(args.fn_containers or args.fn_images or args.fn_labels),
        window=(t0_epoch, t1_epoch))[:2]

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
    ap.add_argument("--forbidden-services", default="",
                    help="comma-separated swarm service name substrings that must NOT be up during "
                         "the run; '*' means ANY service (platforms that never use swarm). Data-driven "
                         "isolation guard; when absent the legacy hardcoded fn/openfaas checks apply.")
    ap.add_argument("--forbidden-containers", default="",
                    help="comma-separated container name substrings that must NOT be up (e.g. "
                         "'fnserver' during OpenFaaS/OpenWhisk sessions). Empty = no container check.")
    ap.add_argument("--total", type=int, default=2000, help="total requests")
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--duration", type=int, default=60, help="window seconds (safety cap; runs are count-bound)")
    ap.add_argument("--warmup", type=int, default=10, help="requests fired before the measured window")
    ap.add_argument("--repeat", type=int, default=1, help="measurement repetitions")
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--slo-ms", type=float, default=500.0, help="SLO latency target in ms")
    ap.add_argument("--auth", default="", help="HTTP Basic auth 'user:pass' (OpenFaaS admin creds)")
    ap.add_argument("--idle-w", type=float, default=P_IDLE_BASE_W)
    ap.add_argument("--cpu-count-override", type=int, default=None,
                     help="use this instead of /proc/cpuinfo's count for saturation ceilings "
                          "(required if the platform is cpuset/taskset-pinned to fewer cores "
                          "than the physical machine has; not needed for a nr_cpus=/maxcpus= "
                          "kernel-restricted box, which reports the restricted count natively)")
    ap.add_argument("--host-cpu-list", default="", help="comma-separated core ids actually "
                     "pinned (e.g. '0,1') -- sums only those /proc/stat cpuN lines for "
                     "host_cpu_ticks instead of the whole-machine aggregate line; pair with "
                     "--cpu-count-override so numerator and denominator agree, or background "
                     "activity on the un-pinned cores will still inflate host_saturation_pct")
    ap.add_argument("--ci", type=float, default=CI_GCO2_PER_KWH, help="grid carbon intensity gCO2/kWh")
    ap.add_argument("--verify", action="store_true", help="workload sanity check (N calls, per-inv function CPU)")
    ap.add_argument("--verify-n", type=int, default=100)
    ap.add_argument("--verify-budget-ms", type=float, default=None,
                    help="claimed per-call CPU budget ms (for the verify budget check)")
    ap.add_argument("--sampler", default="docker", choices=["docker", "cgroup"],
                    help="container CPU source (cgroup = direct cpu.stat via a cgroup-tree "
                         "walk, ~20 Hz, falls back)")
    ap.add_argument("--rescan-s", type=float, default=0.25,
                    help="container-set rescan interval for the cgroup sampler (smaller catches "
                         "short-lived/scale-to-zero containers; costs host CPU)")
    ap.add_argument("--sample-s", type=float, default=0.05,
                    help="cgroup read cadence for the cgroup sampler (default 20 Hz; CPU is "
                         "differenced from cumulative counters so high rates only cost host CPU)")
    ap.add_argument("--max-sample-gap", type=float, default=1.0,
                    help="fail/warn if the sampler went blind for longer than this many seconds "
                         "mid-window (the real sampling-quality gate; replaces the "
                         "cannot-fail 'coverage 100%%' figure)")
    ap.add_argument("--loadgen", default="py", choices=["py", "hey"],
                    help="load generator: py (stdlib threads) or hey (Go binary, low host footprint)")
    ap.add_argument("--interarrival-ms", type=float, default=0.0,
                    help="gap between requests, ms (cold-start experiments: total N, concurrency 1). "
                         "With --loadgen hey this is translated into a rate limit (hey -q) so the "
                         "run stays on the CPU-clean generator instead of falling back to the "
                         "Python threads (whose CPU pollutes host_saturation_pct)")
    ap.add_argument("--qps", type=float, default=0.0,
                    help="aggregate target request rate for --loadgen hey (0 = unthrottled). "
                         "Passed as hey -q (per worker). Mutually redundant with --interarrival-ms: "
                         "if both are given, --qps wins and a warning is printed")
    ap.add_argument("--delta-check", action="store_true",
                    help="cross-validate sampler vs direct before/after cgroup counter of the CP container")
    ap.add_argument("--idle-probe", action="store_true",
                    help="platform up, zero traffic for --duration s: static orchestration baseline")
    ap.add_argument("--no-quiet-gate", action="store_true",
                    help="skip the pre-run ambient-load quiet check (runbook §1): exploratory runs and "
                         "the contamination A/B tool only -- a citable run must self-certify a quiet box")
    ap.add_argument("--ambient-window-s", type=float, default=20.0,
                    help="seconds the quiet gate samples whole-host busy CPU before the bench")
    ap.add_argument("--max-ambient-cpu-pct", type=float, default=15.0,
                    help="quiet-gate ceiling: aggregate host busy%% over the window (100 = all cores) "
                         "above which the run refuses to start; ~2.8 cores of opencode reads ~35%%")
    args = ap.parse_args()

    if args.cpu_count_override is not None:
        global _CPU_COUNT_OVERRIDE
        _CPU_COUNT_OVERRIDE = args.cpu_count_override

    if args.host_cpu_list:
        global _HOST_CPU_LIST_OVERRIDE
        _HOST_CPU_LIST_OVERRIDE = [int(c) for c in args.host_cpu_list.split(",")]

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

    # Quiet-box precondition (runbook §1): measure whole-host background CPU
    # once, BEFORE any measurement, and refuse to run if the box is not quiet.
    # Idle-probe (idle-w calibration) is exempt: the platform stack itself is
    # the 'load' under study and its steady-state CPU is legitimately nonzero.
    ambient = {"window_s": args.ambient_window_s,
               "threshold_pct": args.max_ambient_cpu_pct,
               "quiet_gate_disabled": bool(args.no_quiet_gate)}
    if not args.idle_probe:
        ambient["load_pct"], ambient["top_cpu"] = ambient_load_check(
            args.ambient_window_s, args.max_ambient_cpu_pct,
            quiet_gate=not args.no_quiet_gate)

    if args.repeat > 1:
        os.makedirs(args.outdir, exist_ok=True)
        summaries = []
        for i in range(1, args.repeat + 1):
            print(f"--- run {i}/{args.repeat} ---")
            summary, all_snaps, reqs, ld, raw = run_once(args, cp_sub)
            write_run(os.path.join(args.outdir, "run_%d" % i), summary, all_snaps, reqs,
                      raw_samples=raw)
            if ld is not None:
                with open(os.path.join(args.outdir, "run_%d" % i, "hey.csv"), "w") as f:
                    f.write(ld["raw"])
            summaries.append(summary)
        with open(os.path.join(args.outdir, "runs.json"), "w") as f:
            json.dump(clean_json(summaries), f, indent=2)
        med = median_summary(summaries)
        med["ambient"] = ambient
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
        summary, all_snaps, reqs, ld, raw = run_once(args, cp_sub)
        summary["ambient"] = ambient
        # FIXED 2026-10-01 (expert review): a single-run bench must write the SAME
        # artifact shape as a repeat>1 bench. Only write_run() ran here, so
        # runs.json was never created -- the lock-session pilot gate read it
        # unguarded (FileNotFoundError -> the whole session died ~2h in) and the
        # tier-1 --idle-probe reader silently returned None, which would have
        # printed "--" for every corrected column and quietly skipped the entire
        # independent cross-check. Write runs.json = [summary] here.
        summary["repetitions"] = 1
        write_run(args.outdir, summary, all_snaps, reqs, raw_samples=raw)
        with open(os.path.join(args.outdir, "runs.json"), "w") as f:
            json.dump(clean_json([summary]), f, indent=2)
        if ld is not None:
            with open(os.path.join(args.outdir, "hey.csv"), "w") as f:
                f.write(ld["raw"])
        print(json.dumps(clean_json(summary), indent=2))
        print(f"\nSaved to {os.path.abspath(args.outdir)}/")


if __name__ == "__main__":
    main()
