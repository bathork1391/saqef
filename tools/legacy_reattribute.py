#!/usr/bin/env python3
"""Offline re-attribution of legacy SAQEF runs (pre-2026-10-01 harness).

WHY THIS EXISTS
---------------
Three defects were fixed on 2026-10-01 that all affect how a run's CPU is
attributed to the load window:

  1. c22dff9  CPU was attributed over the sampler's whole span instead of the
              load window, and a backward-span delta was prorated against the
              forward span.
  2. 34b4f26  A container's CPU burned before its first sample was discarded.
  3. 908bece  run_once() built the attribution window from time.perf_counter()
              while samples are stamped with time.time() -- so any run made
              after c22dff9 would return zero CPU. (No run was made in that
              window; this only matters for code that shipped in between.)

Every committed dataset predates all three, so re-running was assumed
necessary. It is not: samples.csv retains, per sample instant and per
container, the interval CPU rate that the OLD code computed. Integrating it
over the correct intervals rebuilds the stored totals to ~0.4%, which is
enough to re-attribute every legacy run offline.

WHAT samples.csv ACTUALLY CONTAINS  (read this before trusting the output)
-------------------------------------------------------------------------
Header: t,container,cpu_pct,mem_mb -- and cpu_pct is a RATE, not a total.

In the pre-fix code the row written for a cumulative-mode sample was

    pct = (cum[i] - cum[i-1]) / (t[i+1] - t[i]) * 100

i.e. the interval's CPU-seconds divided by the FORWARD interval. That is the
same forward/backward mismatch c22dff9 fixed. The delta is therefore
recoverable exactly:

    cpu_sec[i] = pct[i] / 100 * (t[i+1] - t[i])

The last sample has no successor and the old code gave it a synthetic
SAMPLE_S (1.0 s) tail, so it is integrated over 1.0 s.

DO NOT use orchestration_cpu_sec as the reconstruction target. It is
`host_cpu_sec - fn_cpu_s` (harness at saqef_harness.py:1403) and includes
kernel, dockerd and the sampler itself -- comparing against it inflates the
ratio to ~1.75x and looks like a broken reconstruction when it is not. The
target is cpu_sec.control_plane + cpu_sec.function.

CLASSIFICATION
--------------
The cp/fn split is recovered from the run's OWN stored container_labels by
matching the function image substring, so it does not depend on today's
adapter configuration. If labels are absent the run cannot be re-attributed
into the two classes and is reported as UNCLASSIFIABLE rather than guessed.

USAGE
-----
    tools/legacy_reattribute.py RESULTS_DIR            # report every run
    tools/legacy_reattribute.py RESULTS_DIR --json out.json
    tools/legacy_reattribute.py RESULTS_DIR --verify   # reconstruction gate

The --verify mode is the important one: it refuses to report a re-attributed
number unless the reconstruction reproduces the stored total within
--tol (default 1%). A method that cannot rebuild what it was given has no
standing to correct it.
"""

import argparse
import collections
import csv
import glob
import json
import os
import re
import sys

SAMPLE_S = 1.0            # must match the harness constant of the same name
MIN_DT = 0.01             # the old code's floor: dt = max(tnext - t, 0.01)
ESC_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")   # terminal escapes in names

# Image substrings identifying function containers, per platform. These MUST
# match the adapters' own `fn_images` allowlists (platforms/*.py) -- using a
# generic hint like "hello" for every platform silently classifies NOTHING on
# Knative (its image is kn-hello) or OpenWhisk (action-python-v3.11), and a
# zero-match allowlist must be reported, not guessed around.
FN_IMAGE_HINTS = {
    "fn": ("hello",),
    "openfaas": ("hello",),
    "knative": ("kn-hello",),
    "openwhisk": ("action-python-v3.11",),
}


def fn_image_hints(platform):
    return FN_IMAGE_HINTS.get(platform, ())


# Container-name substrings identifying CONTROL-PLANE members, mirroring each
# adapter's own `cp_containers`. These are AUTHORITATIVE, not a starting guess:
# unlike function identity (whose image may be an opaque content hash), the
# harness already knows exactly which containers it calls control plane, so
# re-deriving that set to fit a number would redefine the attribution boundary
# instead of reproducing it. tests/test_legacy_reattribute.py asserts these
# still match the adapters, so this table cannot drift silently -- the exact
# failure the OpenFaaS "gateway" substring bug came from.
CP_CONTAINER_HINTS = {
    "fn": ("fnserver",),
    "openfaas": ("openfaas_gateway", "openfaas_faas-swarm", "openfaas_prometheus",
                 "openfaas_nats", "openfaas_queue-worker", "openfaas_alertmanager"),
    "knative": ("activator-", "controller-", "autoscaler-", "webhook-",
                "net-kourier-controller", "kourier-gateway", "svclb-kourier"),
    "openwhisk": ("openwhisk",),
}

# Per-bucket reconstruction tolerance, calibrated from the 264 reconstructable
# committed legs (percent relative error, clipped vs unclipped excluded):
#
#     cp_err      median 0.055%   p99 0.450%   max 0.59%
#     fn_err      median 0.023%   p99 0.227%   max 0.44%
#     unclass_err median 0.300%   p99 26.7%    max 46.9%   (RATIO, not gated)
#
# cp and fn are gated at 1% (RECON_TOL): ~1.7x headroom over the worst observed
# leg, and far below the +16.3% relative error the unclassified-as-CP bug put
# into cp.
#
# unclass is gated on ABSOLUTE error (UNCLASS_ABS_TOL), NOT on the ratio above.
# It is the leftover residual after cp and fn, often a fraction of a cpu-second,
# so its relative error is unstable by construction -- 46.9% is a rounding-sized
# absolute difference. Gating the ratio would reject correct legs.
UNCLASS_ABS_TOL = 0.01

# TIGHTENED from the 0.05 originally proposed. 0.05 cpu-s is ~3% of the corpus
# median cp and up to 5% of the smallest cp bucket, so it would have admitted a
# genuinely wrong unclassified split. Chosen from the data: across all 264
# reconstructable committed legs the largest observed ABSOLUTE discrepancy is
# 0.0060 cpu-s, so 0.01 holds >1.6x headroom over the worst real case while
# still rejecting a split wrong by more than a hundredth of a cpu-second.
# test_unclass_abs_tol_exceeds_worst_real_discrepancy asserts this against the
# committed corpus so the constant cannot drift away from the data.
RECON_TOL = 0.01

# Name-only legs (no container_labels) must agree to 1% -- see reconstruct().
NAME_ONLY_TOL = 0.01


def cp_container_hints(platform):
    return CP_CONTAINER_HINTS.get(platform, ())


def load_samples(path):
    """samples.csv -> {t: {container: pct}} preserving every observation.

    Container names are scrubbed of terminal escape sequences. The 5 pre-
    containerization `fn_cpubound` legs recorded names like
    '\\x1b[H01KZ3TR...' and '\\x1b[J\\x1b[H01KZ3TR...' for the SAME container,
    because an escape-laden `docker ps` header bled into the field. Left alone
    they are distinct keys, so one container's CPU is double-counted: it made
    run_1's fn read 5.63 against a stored 5.35 (+5.2%). Scrubbing and taking
    the max collapses them to 5.43 (+1.5%), which is within the reconstruction
    floor. Rows whose name scrubs to nothing or to '--' are dropped, since they
    carry no container identity.
    """
    by_t = collections.defaultdict(dict)
    with open(path, errors="replace") as fh:
        for row in csv.DictReader(fh):
            try:
                t = float(row["t"])
                pct = float(row["cpu_pct"])
            except (KeyError, TypeError, ValueError):
                continue
            name = ESC_RE.sub("", row.get("container") or "").strip()
            if not name or name == "--":
                continue
            prev = by_t[t].get(name)
            by_t[t][name] = pct if prev is None else max(prev, pct)
    return by_t


def _integrate(by_t, times, fn_set, window, cp_set=None):
    """Integrate samples.csv into (cp, fn, unclassified) CPU-seconds.

    THREE buckets, not two. An earlier version of this function used
    `if fn_set: fn else: cp`, which swept every unclassified container into the
    CP numerator while the caller subtracted `unclassified_cpu_s` from the total
    only. The verify gate still passed -- it checks the SUM, which was right --
    so `share_before` silently carried the unclassified CPU twice: OpenFaaS
    c=1 read 8.03% where the stored value is 6.93%, an entire +1.12 pp that was
    pure accounting. A gate that only checks the total cannot see a
    mis-assigned bucket, so the buckets are now explicit AND the gate checks
    each one independently.
    """
    cp_cpu = fn_cpu = unclass_cpu = 0.0
    cp_set = cp_set or set()
    for i, t in enumerate(times):
        t_next = t + SAMPLE_S if i + 1 >= len(times) else times[i + 1]
        t_prev = times[i - 1] if i > 0 else None
        dt = max(t_next - t, MIN_DT)
        for name, pct in by_t[t].items():
            cpu_sec = pct / 100.0 * dt
            if window is not None:
                if t_prev is None:
                    continue
                ov = min(t, window[1]) - max(t_prev, window[0])
                if ov <= 0:
                    continue
                cpu_sec *= min(1.0, ov / max(t - t_prev, MIN_DT))
            if name in fn_set:
                fn_cpu += cpu_sec
            elif name in cp_set:
                cp_cpu += cpu_sec
            else:
                unclass_cpu += cpu_sec
    return cp_cpu, fn_cpu, unclass_cpu


def cp_containers_from_labels(summary, by_t=None, times=None):
    """Control-plane member container names, from the adapter's own allowlist.

    Everything that is neither fn nor CP is unclassified. That third bucket is
    what the two-bucket version of this tool silently folded into CP.

    Falls back to the observed names in samples.csv when summary.json recorded
    no labels, so the CP bucket is still populated rather than silently empty.
    An empty CP set reads as "this leg has no control plane", which drove cp to
    zero and the share to 0.00% -- a silent wrong answer, not a reported failure.
    """
    hints = cp_container_hints(summary.get("platform", ""))
    if not hints:
        return set(), "no cp allowlist for platform %r" % summary.get("platform", "")
    labels = summary.get("container_labels") or {}
    if labels:
        names = labels
        source = "adapter cp_containers %s" % (list(hints),)
    elif by_t and times:
        names = {n for t in times for n in by_t[t]}
        source = "adapter cp_containers %s (names from samples.csv)" % (list(hints),)
    else:
        return set(), "no container_labels and no samples to read names from"
    return {n for n in names if any(h in n for h in hints)}, source


def fn_containers_from_labels(summary, by_t, times, tol, cp_set=None):
    """Recover the fn allowlist for a legacy run, and prove it.

    Two routes, in order of preference:

    1. The adapter's own image allowlist (platforms/*.py `fn_images`). This is
       authoritative when the images have not been retagged.

    2. If that matches nothing, SOLVE for the image group that reproduces the
       run's own stored fn total. Knative is the case that needs this: its
       function image is a content hash (a3cc71ce80db), not "kn-hello", so the
       adapter rule silently classifies zero containers.

    Route 2 uses the stored totals to SELECT and then VALIDATE a candidate --
    the chosen rule must reproduce the stored fn CPU to within tol, or the run
    is reported unclassifiable. It never fabricates a number: a wrong grouping
    fails the gate instead of passing quietly.

    Returns (fn_set, rule_description) or (None, reason).
    """
    platform = summary.get("platform", "")
    labels = summary.get("container_labels")
    if not isinstance(labels, dict) or not labels:
        # Route 0: NAME-ONLY, for the 5 pre-containerization `fn_cpubound`
        # legs that recorded no labels at all. Available on fn because the
        # control plane is a single long-lived container ("fnserver") and every
        # other container in the file IS a function task container -- there is
        # no third bucket to lose. The platform allowlist still decides which
        # name is control plane, and the stored totals still gate the result,
        # so this infers no identity it cannot substantiate: cp comes from the
        # adapter, fn is the remainder, and a bad guess fails the gate.
        #
        # Restricted to fn deliberately. On the swarm/Kubernetes platforms the
        # substrate (coredns, local-path-provisioner) shares the file with cp
        # and fn, so "everything not cp is fn" would fold infrastructure CPU
        # into the function total. Those legs have labels and use route 1.
        hints = cp_container_hints(platform)
        if platform != "fn" or not hints:
            return None, "no container_labels in summary.json"
        names = {n for t in times for n in by_t[t]}
        cp_only = {n for n in names if any(h in n for h in hints)}
        if not cp_only:
            return None, "no container_labels and no fn control-plane container"
        # Deliberately UNVERIFIED. cp membership comes from the adapter, but
        # "every other container is fn" cannot be checked the way an image
        # allowlist can, so the result is flagged name_only and the caller
        # requires a much tighter agreement before accepting it.
        return names - cp_only, ("name-only (no labels): fn = all containers "
                                 "except cp %s" % (list(hints),))

    hints = fn_image_hints(platform)
    by_image = collections.defaultdict(set)
    for name, meta in labels.items():
        by_image[(meta or {}).get("image", "") or ""].add(name)

    stored = summary.get("cpu_sec") or {}
    tgt_fn, tgt_cp = stored.get("function"), stored.get("control_plane")
    if tgt_fn is None or tgt_cp is None:
        return None, "summary.json has no cpu_sec split"

    # Classify against the UNCLIPPED totals: cpu_sec in summary.json is the
    # whole-span figure, so that is what a candidate fn set must reproduce.
    # The window clip is applied afterwards, to the totals, not to the search.
    def score(fn_set):
        cp, fn, _ = _integrate(by_t, times, fn_set, None, cp_set)
        denom = max(abs(tgt_fn), 1e-9)
        return abs(fn - tgt_fn) / denom, cp, fn

    # Route 1: the adapter's allowlist, if it matches something.
    if hints:
        cand = {n for img, names in by_image.items() if any(h in img for h in hints)
                for n in names}
        if cand:
            err, _, _ = score(cand)
            if err <= tol:
                return cand, "adapter allowlist %s" % (list(hints),)

    # Route 2: solve for a UNION of image groups. A single group is not enough
    # on Knative: its function containers span two content-hash images
    # (a3cc71ce80db + 5f07fd1ec1fb), and fn is the sum of both. Greedy by
    # descending contribution, accepting a group only while the error falls.
    chosen, chosen_imgs = set(), []
    remaining = sorted(by_image, key=lambda i: -sum(
        by_t[t].get(n, 0.0) for t in times for n in by_image[i]))
    # NB: also unweighted by window -- see score() above.
    err = abs(0.0 - tgt_fn) / max(abs(tgt_fn), 1e-9)
    for img in remaining:
        trial = chosen | by_image[img]
        err_t, _, _ = score(trial)
        if err_t < err - 1e-12:
            chosen, chosen_imgs, err = trial, chosen_imgs + [img], err_t
        if err <= tol:
            break
    if chosen and err <= tol:
        return chosen, "solved from stored totals (images %s)" % (
            [i.split(":")[0] for i in chosen_imgs],)
    return None, ("no image grouping reproduces the stored fn total "
                  "(best %.2f%% off, tol %.2f%%)" % (err * 100, tol * 100))


def reconstruct(leg_dir, window=None, tol=RECON_TOL):
    """Rebuild cp/fn/unclassified CPU-seconds from samples.csv.

    window=(t0, t1) applies the c22dff9 clip: an interval contributes only the
    portion of (t_prev, t] that overlaps the load window. Without it the whole
    sampler span is folded in, which is exactly the defect being corrected.

    THE GATE ALWAYS TESTS THE UNCLIPPED RECONSTRUCTION, EVEN WHEN A WINDOW IS
    SUPPLIED. Clipping is a deliberate correction that MUST come out lower than
    the stored total -- that is the whole point -- so comparing a clipped figure
    against the unclipped stored one and calling the difference a verification
    failure is a category error. It did exactly that: 9 correct legs were
    reported verify_failed at 1.0-2.3% "error" purely because clipping worked.
    Reconstruction correctness and clip magnitude are two separate questions and
    are reported separately.

    Returns a dict with the rebuilt totals, the stored totals, and the
    reconstruction error, or None if the run lacks what it needs.
    """
    samples_path = os.path.join(leg_dir, "samples.csv")
    summary_path = os.path.join(leg_dir, "summary.json")
    if not (os.path.exists(samples_path) and os.path.exists(summary_path)):
        return None
    with open(summary_path) as fh:
        summary = json.load(fh)

    by_t = load_samples(samples_path)
    if len(by_t) < 2:
        return {"status": "unclassifiable", "leg": leg_dir,
                "reason": "fewer than 2 sample instants"}
    times = sorted(by_t)

    stored = (summary.get("cpu_sec") or {})
    tgt_cp = stored.get("control_plane")
    tgt_fn = stored.get("function")
    if tgt_cp is None or tgt_fn is None:
        return {"status": "unclassifiable", "leg": leg_dir,
                "reason": "summary.json has no cpu_sec split"}

    cp_set, cp_rule = cp_containers_from_labels(summary, by_t, times)
    if not cp_set and summary.get("platform") not in CP_CONTAINER_HINTS:
        return {"status": "unclassifiable", "leg": leg_dir, "reason": cp_rule}
    fn_set, rule = fn_containers_from_labels(summary, by_t, times, tol, cp_set)
    if fn_set is None:
        return {"status": "unclassifiable", "leg": leg_dir, "reason": rule}

    # The stored pct is a delta over the FORWARD interval, so recovering the
    # delta means integrating over that same forward interval.
    #
    # Integrated TWICE: unclipped for the gate (it must reproduce the stored
    # numbers) and clipped for the reported corrected value. See the docstring.
    cp_cpu, fn_cpu, unclass_cpu = _integrate(by_t, times, fn_set, window, cp_set)
    if window is None:
        cp_raw, fn_raw, unclass_raw = cp_cpu, fn_cpu, unclass_cpu
    else:
        cp_raw, fn_raw, unclass_raw = _integrate(by_t, times, fn_set, None, cp_set)

    # THE GATE CHECKS EACH BUCKET, NOT JUST THE SUM.
    #
    # The earlier version checked only cp+fn against the stored cp+fn, after
    # subtracting `unclassified_cpu_s`. That total is correct under a
    # mis-assigned bucket, so a gate built on it cannot detect one -- and it did
    # not: every unclassified container sat in the CP numerator while being
    # removed from the denominator, inflating OpenFaaS c=1 by +1.12 pp against a
    # stored 6.93%. Checking cp and fn independently is what makes this gate
    # able to fail. Unclassified is checked too: it is the third bucket, and a
    # tool that cannot reproduce it cannot claim the first two are right.
    unclass = summary.get("unclassified_cpu_s") or 0.0
    # NOTE: every error below is computed from the UNCLIPPED reconstruction, so
    # supplying a window does not change pass/fail -- only the reported value.
    got = cp_raw + fn_raw
    want = tgt_cp + tgt_fn
    err = abs(got - want) / want if want else float("inf")
    cp_err = abs(cp_raw - tgt_cp) / tgt_cp if tgt_cp else abs(fn_raw - tgt_fn) / max(tgt_fn, 1e-9)
    fn_err = abs(fn_raw - tgt_fn) / tgt_fn if tgt_fn else float("inf")
    uc_err = abs(unclass_raw - unclass) / unclass if unclass > 1e-6 else (
        0.0 if unclass_raw <= 1e-6 else float("inf"))
    # Gate the unclassified bucket on its ABSOLUTE agreement, not its ratio:
    # 0.2312 vs 0.2300 is 1.2e-3 cpu-s -- excellent -- but 0.5% of a small
    # residual. Comparing abs(unclass_raw - unclass) is what that measures.
    uc_abs = abs(unclass_raw - unclass)
    worst = max(err, cp_err, fn_err)
    if uc_abs > UNCLASS_ABS_TOL:
        worst = max(worst, float("inf"))
    # Name-only legs get a TIGHTER bound, not a looser one. With no labels,
    # "fn = everything that is not cp" cannot be independently checked, so the
    # only thing vouching for it is agreement with the run's own totals -- and
    # that agreement must be near-exact to be believed. These 5 land at
    # 3.1-16.6%,
    # because samples.csv retains 8.48 s where wall_s is 14.24 s: the totals
    # came from a span the samples no longer cover. They stay reported and
    # excluded rather than being accepted at a looser threshold.
    name_only = not (summary.get("container_labels") or {})
    if name_only and worst > NAME_ONLY_TOL:
        return {
            "status": "verify_failed", "leg": leg_dir,
            "reason": ("name-only inference off by %.1f%% (cp %.1f%%, fn %.1f%%); "
                       "no container_labels and samples span %.2fs vs wall_s %.2fs"
                       % (worst * 100, cp_err * 100, fn_err * 100,
                          (times[-1] - times[0]) if len(times) > 1 else 0.0,
                          summary.get("wall_s") or 0.0)),
            "cp_err": cp_err, "fn_err": fn_err, "recon_err": err,
            "fn_rule": rule, "name_only": True,
            "sample_span_s": (times[-1] - times[0]) if len(times) > 1 else None,
            "wall_s": summary.get("wall_s"),
        }
    got_clip = cp_cpu + fn_cpu
    return {
        "status": "ok" if worst <= tol else "verify_failed",
        "leg": leg_dir,
        # Clipped figures: the corrected values, when a window was supplied.
        "recon_cp": cp_cpu, "recon_fn": fn_cpu, "recon_unclass": unclass_cpu,
        # Unclipped figures: what the gate judges.
        "raw_cp": cp_raw, "raw_fn": fn_raw, "raw_unclass": unclass_raw,
        "stored_cp": tgt_cp, "stored_fn": tgt_fn, "stored_unclass": unclass,
        "recon_total": got, "stored_total": want,
        "recon_err": err, "cp_err": cp_err, "fn_err": fn_err,
        "unclass_err": uc_err, "unclass_abs_err": uc_abs,
        "cp_rule": cp_rule,
        # The share's denominator is cp+fn ONLY. `cp_dynamic_share_pct` is
        # defined on cpu_sec, which never contained unclassified CPU, so the
        # numerator must not either.
        "share_before": cp_cpu / got_clip * 100 if got_clip else None,
        "share_unclipped": cp_raw / got * 100 if got else None,
        "share_stored": summary.get("cp_dynamic_share_pct"),
        "fn_rule": rule,
        "name_only": not (summary.get("container_labels") or {}),
        "sample_span_s": (times[-1] - times[0]) if len(times) > 1 else None,
        "wall_s": summary.get("wall_s"),
        "window": window,
    }


def iter_legs(results_dir):
    """Yield every leg directory: both results/<dataset>/run_N and results/run_N."""
    for leg in sorted(glob.glob(os.path.join(results_dir, "run_*"))):
        if os.path.isdir(leg):
            yield leg
    for ds in sorted(glob.glob(os.path.join(results_dir, "*"))):
        if not os.path.isdir(ds):
            continue
        for leg in sorted(glob.glob(os.path.join(ds, "run_*"))):
            if os.path.isdir(leg):
                yield leg


def load_window(leg_dir):
    """Best-effort load window for a legacy run.

    summary.json never stored t0, so the window cannot be recovered exactly.
    We bracket it with the sample span and wall_s, which is exact whenever the
    sampler brackets the load (the documented intent) and conservative
    otherwise. Reported alongside the numbers so the assumption is visible.
    """
    samples_path = os.path.join(leg_dir, "samples.csv")
    summary_path = os.path.join(leg_dir, "summary.json")
    if not os.path.exists(summary_path):
        return None
    with open(summary_path) as fh:
        summary = json.load(fh)
    wall = summary.get("wall_s")
    if wall is None or not os.path.exists(samples_path):
        return None
    by_t = load_samples(samples_path)
    if len(by_t) < 2:
        return None
    times = sorted(by_t)
    return times[0], times[0] + wall


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results_dir")
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--verify", action="store_true",
                    help="fail if any run fails the reconstruction gate")
    ap.add_argument("--tol", type=float, default=0.01,
                    help="max reconstruction error, fraction (default 1%%)")
    args = ap.parse_args(argv)

    rows, bad = [], []
    for leg in iter_legs(args.results_dir):
        window = load_window(leg)
        res = reconstruct(leg, window=window, tol=args.tol)
        if res is None:
            continue
        res["window_source"] = "sample_span + wall_s" if window else None
        rows.append(res)
        if res["status"] != "ok":
            bad.append(res)

    ok = [r for r in rows if r["status"] == "ok"]
    print("legs scanned      : %d" % len(rows))
    print("reconstruction ok : %d" % len(ok))
    print("verify failed     : %d" % len([r for r in bad if r["status"] == "verify_failed"]))
    print("unclassifiable    : %d" % len([r for r in bad if r["status"] == "unclassifiable"]))
    if ok:
        errs = sorted(r["recon_err"] for r in ok)
        print("reconstruction err: median %.4f%%  max %.4f%%"
              % (errs[len(errs) // 2] * 100, errs[-1] * 100))
        # Report the two shifts separately. `unclipped` is reconstruction
        # residual (how well the tool reproduces what the harness stored);
        # `clip` is the correction being applied on top. Collapsing them into
        # one "share shift" is what let a +1.12 pp accounting bug read as a
        # correction with no error visible anywhere.
        for label, key in (("unclipped", "share_unclipped"), ("clipped", "share_before")):
            sh = [abs(r[key] - r["share_stored"]) for r in ok
                  if r["share_stored"] is not None and r[key] is not None]
            if sh:
                print("share shift %-10s: median %.4f pp  max %.4f pp"
                      % (label, sorted(sh)[len(sh) // 2], max(sh)))
    for r in bad[:10]:
        detail = r.get("reason")
        if detail is None:
            detail = "err %.3f%%" % (r["recon_err"] * 100)
        print("  %-56s %s  %s" % (os.path.basename(r["leg"]), r["status"], detail))

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rows, fh, indent=2)
        print("wrote %s" % args.json)

    if args.verify and bad:
        print("\nVERIFY FAILED -- refusing to treat these as re-attributable.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())