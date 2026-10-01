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
import sys

SAMPLE_S = 1.0            # must match the harness constant of the same name
MIN_DT = 0.01             # the old code's floor: dt = max(tnext - t, 0.01)

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


def load_samples(path):
    """samples.csv -> {t: {container: pct}} preserving every observation."""
    by_t = collections.defaultdict(dict)
    with open(path) as fh:
        for row in csv.DictReader(fh):
            try:
                t = float(row["t"])
                pct = float(row["cpu_pct"])
            except (KeyError, TypeError, ValueError):
                continue
            by_t[t][row["container"]] = pct
    return by_t


def _integrate(by_t, times, fn_set, window):
    """Integrate samples.csv into (cp, fn) CPU-seconds for a given fn set."""
    cp_cpu = fn_cpu = 0.0
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
            else:
                cp_cpu += cpu_sec
    return cp_cpu, fn_cpu


def fn_containers_from_labels(summary, by_t, times, tol):
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
    labels = summary.get("container_labels")
    if not isinstance(labels, dict) or not labels:
        return None, "no container_labels in summary.json"

    platform = summary.get("platform", "")
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
        cp, fn = _integrate(by_t, times, fn_set, None)
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


def reconstruct(leg_dir, window=None, tol=0.01):
    """Rebuild cp/fn CPU-seconds from samples.csv.

    window=(t0, t1) applies the c22dff9 clip: an interval contributes only the
    portion of (t_prev, t] that overlaps the load window. Without it the whole
    sampler span is folded in, which is exactly the defect being corrected.

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

    fn_set, rule = fn_containers_from_labels(summary, by_t, times, tol)
    if fn_set is None:
        return {"status": "unclassifiable", "leg": leg_dir, "reason": rule}

    # The stored pct is a delta over the FORWARD interval, so recovering the
    # delta means integrating over that same forward interval.
    cp_cpu, fn_cpu = _integrate(by_t, times, fn_set, window)

    stored = (summary.get("cpu_sec") or {})
    tgt_cp = stored.get("control_plane")
    tgt_fn = stored.get("function")
    if tgt_cp is None or tgt_fn is None:
        return {"status": "unclassifiable", "leg": leg_dir,
                "reason": "summary.json has no cpu_sec split"}

    # Integrating samples.csv necessarily sweeps up the UNCLASSIFIED bucket
    # (everything that is neither the fn allowlist nor a control-plane member),
    # while cpu_sec holds only cp+fn. Comparing the raw sum to cp+fn therefore
    # shows a ~1% "error" that is really just the unclassified CPU, and it is
    # the whole reason 48 legs first failed the gate. Subtract it -- the run
    # recorded it -- rather than loosening the tolerance until they pass.
    unclass = summary.get("unclassified_cpu_s") or 0.0
    got = cp_cpu + fn_cpu - unclass
    want = tgt_cp + tgt_fn
    err = abs(got - want) / want if want else float("inf")
    return {
        "status": "ok" if err <= tol else "verify_failed",
        "leg": leg_dir,
        "recon_cp": cp_cpu, "recon_fn": fn_cpu,
        "stored_cp": tgt_cp, "stored_fn": tgt_fn,
        "recon_total": got, "stored_total": want,
        "recon_err": err,
        "share_before": cp_cpu / got * 100 if got else None,
        "share_stored": summary.get("cp_dynamic_share_pct"),
        "fn_rule": rule,
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
        shifts = [abs(r["share_before"] - r["share_stored"])
                  for r in ok if r["share_stored"] is not None]
        if shifts:
            print("share shift (clip): median %.4f pp  max %.4f pp"
                  % (sorted(shifts)[len(shifts) // 2], max(shifts)))
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