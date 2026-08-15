#!/usr/bin/env python3
"""Publication figures for the SAQEF control-plane overhead study.

Data-driven: every figure is derived from committed result sets
(results/<dir>/summary.json + runs.json). To add a platform (e.g. a future
one) or a regime, add one entry to REGIMES below and rerun — no script edits.

Single-story figures (no two-panel confusion, no dates on axes — provenance
lives in the paper captions). Numbered in order of appearance in §5:
  Figure 1 — per-run shares, four platforms (8-core box, same instrument):
              every per-run value shown honestly (n=5 per platform, one
              matched session set -- the lock4 session (2026-08-14): all four
              platforms back-to-back the same day under the quiet gate; see
              the data-source note in the paper's Appendix A / figure caption).
   Figure 2 — attribution split (CP / fn / unclassified CPU-time) for the
              same four platforms.
  Figure 3 — control-plane CPU per invocation (ms), all four platforms.
  Figure 4 — core-count effect (Fn vs OpenFaaS): the controlled same-instrument
              experiment, 8 cores vs the same box cpuset-pinned to 2 cores.
              This is the machine-dependence contribution.
  Figure 5 — concurrency invariance: CP share vs load concurrency c=1/2/4/8/16
              (OF/Fn/Kn), the same-day quick-tier sweep (2026-08-15,
              REPEAT=3/TOTAL=3000, quiet-gated) with the c=4 point anchored by
              the lock4 N=5 session; OpenWhisk annotated flat at c=4/8.
              Data source = the committed lock_session_*/lock_summary.json files
              (the gitignored _quick outdirs hold the per-run detail).

Requires: matplotlib (figures only; the measurement harness stays stdlib-only).
Usage:    python3 figures/make_figures.py [outdir]   (default: figures/)
"""

import json
import os
import statistics
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

OUTDIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__))

PLATFORMS = {
    "fn":        {"label": "Fn",       "color": "#1f77b4"},
    "openfaas":  {"label": "OpenFaaS", "color": "#ff7f0e"},
    "openwhisk": {"label": "OpenWhisk (standalone)", "color": "#2ca02c"},
    "knative":   {"label": "Knative",  "color": "#9467bd"},
}

# regime -> label + per-platform list of result dirs (each dir is one independent
# session; a session contributes REPEAT per-run values). Headline number for a
# regime x platform = median of per-session medians (matches the paper).
REGIMES = {
    "8core_quiet": {
        "label": "8 cores",
        "dirs": {
            "fn":       ["results/fn_cpubound_baremetal"],
            "openfaas": ["results/openfaas_cpubound_baremetal"],
        },
    },
    "2core": {
        "label": "2 cores\n(cpuset-pinned)",
        "dirs": {
            "fn":       ["results/fn_cpubound_2core", "results/fn_cpubound_2core_session2"],
            "openfaas": ["results/openfaas_cpubound_2core", "results/openfaas_cpubound_2core_session2"],
        },
    },
    "fourplat": {
        "label": "8 cores, four platforms",
        "dirs": {
            "openfaas":  ["results/openfaas_cpubound_lock_lock4"],
            "fn":        ["results/fn_cpubound_lock_lock4"],
            "knative":   ["results/knative_cpubound_lock_lock4"],
            "openwhisk": ["results/openwhisk_cpubound_lock_lock4"],
        },
    },
}

# Figure 4 (core-count): the controlled Fn-vs-OF experiment
CORE_COUNT_REGS = ("8core_quiet", "2core")
CORE_COUNT_PLAT = ("fn", "openfaas")
# Figures 1-3 (four-platform, 8-core box, same instrument): the matched lock4
# session -- all four platforms back-to-back on 2026-08-14 under the quiet gate,
# each leg with a freshly calibrated idle-w (see the paper's data-source note in
# Appendix A). figure4's 8core_quiet/2core dirs are the separate controlled
# core-count experiment and are intentionally unchanged.
FOURPLAT_REGS = ("fourplat",)
FOURPLAT_PLAT = ("openfaas", "fn", "knative", "openwhisk")
# Figure 5 (concurrency invariance): the c=1/2/4/8/16 quick-tier sweep
# (2026-08-15, REPEAT=3/TOTAL=3000, quiet-gated) with the c=4 point anchored by
# the lock4 N=5 session. Provenance = committed lock_session_*/lock_summary.json
# (the gitignored _quick outdirs hold per-run detail); do not point at those.
CONC_STAMPS = ("conc1", "conc2", "conc8", "conc16")  # OF/Fn/Kn, quick-tier
CONC_OW_STAMPS = {"ow4": 4, "ow8": 8}                # OW spot-check
CONC_ANCHOR = "lock4"                                # c=4, N=5
CONC_C = (1, 2, 4, 8, 16)
CONC_PLAT = ("openfaas", "fn", "knative")


def load_result_dir(d):
    """Return (per_run_values, summary_dict) for one committed result set."""
    runs = json.load(open(os.path.join(d, "runs.json")))
    summary = json.load(open(os.path.join(d, "summary.json")))
    return runs, summary


def collect(regime, platform):
    """Aggregate all sessions of one regime x platform.

    Returns dict with:
      sessions:      list of dicts {dir, per_run: [..], summary}
      per_run:       flattened list of cp_dynamic_share_pct across all sessions
      reported:      median of per-session medians (the citable number)
      spread_min/max: min/max across all per-run values
      cp_sec/fn_sec/unc_sec: per-run medians of the CPU-time attribution fields
    """
    sessions = []
    for d in regime["dirs"][platform]:
        runs, summary = load_result_dir(d)
        sessions.append({"dir": d, "per_run": [r["cp_dynamic_share_pct"] for r in runs],
                         "summary": summary})
    per_run = [v for s in sessions for v in s["per_run"]]
    session_medians = [statistics.median(s["per_run"]) for s in sessions]
    med = lambda key: statistics.median(
        [r["cpu_sec"][key] for s in sessions for r in json.load(open(os.path.join(s["dir"], "runs.json")))])
    unc_med = statistics.median(
        [r.get("unclassified_cpu_s", 0.0) for s in sessions
         for r in json.load(open(os.path.join(s["dir"], "runs.json")))])
    return {
        "sessions": sessions,
        "per_run": per_run,
        "reported": statistics.median(session_medians),
        "spread_min": min(per_run),
        "spread_max": max(per_run),
        "cp_sec": med("control_plane"),
        "fn_sec": med("function"),
        "unc_sec": unc_med,
        "session_labels": [s["dir"] for s in sessions],
    }


def data():
    agg = {}
    for rk in REGIMES:
        for pk in REGIMES[rk]["dirs"]:
            agg[(rk, pk)] = collect(REGIMES[rk], pk)
    return agg


def style_ax(ax, ymax, ylabel):
    ax.set_ylim(0, ymax)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", ls=":", alpha=0.4, zorder=0)


def figure4_core_count(agg):
    """The machine-dependence contribution: Fn vs OF, 8 cores vs 2 cores (pinned)."""
    fig, ax = plt.subplots(figsize=(6.6, 4.2), dpi=150)
    n_plat = len(CORE_COUNT_PLAT)
    width = 0.32
    centers = []
    for i, rk in enumerate(CORE_COUNT_REGS):
        center = i * 1.0
        for j, pk in enumerate(CORE_COUNT_PLAT):
            x = center + (j - (n_plat - 1) / 2) * (width + 0.06)
            a = agg[(rk, pk)]
            lo = a["reported"] - a["spread_min"]
            hi = a["spread_max"] - a["reported"]
            ax.bar(x, a["reported"], width=width, yerr=[[lo], [hi]], capsize=3.5,
                   color=PLATFORMS[pk]["color"], edgecolor="white", zorder=3,
                   error_kw={"elinewidth": 1.0, "capthick": 1.0})
            ax.text(x, a["spread_max"] + 0.35, f"{a['reported']:.2f}", ha="center",
                    va="bottom", fontsize=9)
        centers.append(center)
    gate = 5.0
    ax.axhline(gate, color="0.6", ls="--", lw=0.9, zorder=1)
    ax.text(centers[0], gate + 0.25, "5 pp decision gate", fontsize=8, color="0.4",
            va="bottom", ha="left")
    # gap annotations
    top0 = max(agg[("8core_quiet", p)]["spread_max"] for p in CORE_COUNT_PLAT)
    top1 = max(agg[("2core", p)]["spread_max"] for p in CORE_COUNT_PLAT)
    for i, (rk, top) in enumerate(zip(CORE_COUNT_REGS, (top0, top1))):
        fa, fb = agg[(rk, "fn")], agg[(rk, "openfaas")]
        gap = fa["reported"] - fb["reported"]
        xa = centers[i] + (1 - (n_plat - 1) / 2) * (width + 0.06)
        xb = centers[i] + (0 - (n_plat - 1) / 2) * (width + 0.06)
        mid = (xa + xb) / 2
        top = top + 0.9
        ax.annotate("", xy=(xa, top), xytext=(xb, top),
                    arrowprops=dict(arrowstyle="<->", lw=1.0, color="0.35"))
        ax.text(mid, top + 0.2, f"gap {gap:.2f} pp", ha="center", va="bottom",
                fontsize=8.5, color="0.2")
    ax.set_xticks(centers)
    ax.set_xticklabels([REGIMES[rk]["label"] for rk in CORE_COUNT_REGS], fontsize=9.5)
    ax.set_ylabel("control-plane share of dynamic CPU (%)", fontsize=10)
    style_ax(ax, 19, None)
    handles = [Patch(facecolor=PLATFORMS[pk]["color"]) for pk in CORE_COUNT_PLAT]
    labels = [PLATFORMS[pk]["label"] for pk in CORE_COUNT_PLAT]
    ax.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.14),
              frameon=False, fontsize=9.5, ncol=2, handlelength=1.4, columnspacing=1.2)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUTDIR, f"figure4_core_count.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure4_core_count.{png,pdf}")


def figure1_four_platform_scatter(agg):
    """Per-run shares, all four platforms (8-core box, same instrument; matched
    lock4 session 2026-08-14 -- see module docstring)."""
    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=150)
    rng = __import__("random").Random(0)
    markers = ("o", "s", "^", "D")
    xs = list(range(len(FOURPLAT_PLAT)))
    for i, pk in enumerate(FOURPLAT_PLAT):
        a = agg[("fourplat", pk)]
        x = xs[i]
        for si, s in enumerate(a["sessions"]):
            jit = (rng.random() - 0.5) * 0.12
            ax.scatter([x + jit] * len(s["per_run"]), s["per_run"],
                       marker=markers[si % len(markers)], s=30, alpha=0.8,
                       color=PLATFORMS[pk]["color"], edgecolor="white", lw=0.6, zorder=4)
        ax.plot([x - 0.18, x + 0.18], [a["reported"]] * 2, color="black", lw=1.6, zorder=5)
        ax.text(x, a["spread_max"] + 2.0, f"median {a['reported']:.2f}", ha="center",
                fontsize=8.5, va="bottom")
    ax.set_xticks(xs)
    ax.set_xticklabels([PLATFORMS[pk]["label"] for pk in FOURPLAT_PLAT], fontsize=10)
    ax.set_ylabel("control-plane share of dynamic CPU (%)", fontsize=10)
    style_ax(ax, 100, None)
    ax.axhline(5.0, color="0.6", ls="--", lw=0.9, zorder=1)
    handles = [Patch(color=PLATFORMS[pk]["color"]) for pk in FOURPLAT_PLAT]
    handles.append(plt.Line2D([0], [0], color="black", lw=1.6))
    labels = [PLATFORMS[pk]["label"] for pk in FOURPLAT_PLAT] + ["reported median"]
    ax.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.10),
              frameon=False, fontsize=8.5, ncol=3, handlelength=1.2, columnspacing=1.0)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUTDIR, f"figure1_four_platform_scatter.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure1_four_platform_scatter.{png,pdf}")


def figure2_attribution_split(agg):
    """Attribution split (CP / fn / unclassified CPU-time), four platforms."""
    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=150)
    xs = list(range(len(FOURPLAT_PLAT)))
    width = 0.5
    for i, pk in enumerate(FOURPLAT_PLAT):
        a = agg[("fourplat", pk)]
        x = xs[i]
        cp, fn, unc = a["cp_sec"], a["fn_sec"], a["unc_sec"]
        ax.bar(x, cp, width=width, color=PLATFORMS[pk]["color"], alpha=0.9, zorder=3)
        ax.bar(x, fn, width=width, bottom=cp, color=PLATFORMS[pk]["color"], alpha=0.35, zorder=3)
        ax.bar(x, unc, width=width, bottom=cp + fn, color="0.85", zorder=3)
        ax.text(x, cp + fn + 2.0, f"CP {a['reported']:.1f}%", ha="center", va="bottom",
                fontsize=8.5)
    ax.set_xticks(xs)
    ax.set_xticklabels([PLATFORMS[pk]["label"] for pk in FOURPLAT_PLAT], fontsize=10)
    ax.set_ylabel("dynamic CPU time (s, per-run median)", fontsize=10)
    style_ax(ax, 350, None)
    handles = [Patch(color=PLATFORMS[pk]["color"], alpha=0.9) for pk in
               ("openfaas", "fn", "knative", "openwhisk")]
    labels = [PLATFORMS[pk]["label"] + " — control plane" for pk in
              ("openfaas", "fn", "knative", "openwhisk")]
    handles += [Patch(color=PLATFORMS[pk]["color"], alpha=0.35) for pk in
                ("openfaas", "fn", "knative", "openwhisk")]
    labels += [PLATFORMS[pk]["label"] + " — function" for pk in
               ("openfaas", "fn", "knative", "openwhisk")]
    handles.append(Patch(color="0.85"))
    labels.append("unclassified")
    ax.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.12),
              frameon=False, fontsize=8, ncol=3, handlelength=1.2, columnspacing=1.0)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUTDIR, f"figure2_attribution_split.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure2_attribution_split.{png,pdf}")


def figure3_cp_cost_per_inv(agg):
    """Control-plane CPU cost per invocation (ms CPU / inv), all four platforms.

    cp_cpu_s median per run / 10000 inv * 1000. This is the per-request
    orchestration tax (of-watchdog 0.54, fnserver 0.72, Kn 0.88, OW 25.66) and
    does not duplicate any other figure.
    """
    rows = []
    for pk in FOURPLAT_PLAT:
        a = agg[("fourplat", pk)]
        ms = a["cp_sec"] / 10000.0 * 1000.0
        rows.append((PLATFORMS[pk]["label"], ms, PLATFORMS[pk]["color"]))
    rows.sort(key=lambda r: r[1])
    fig, ax = plt.subplots(figsize=(6.6, 3.6), dpi=150)
    ys = list(range(len(rows)))
    for i, (label, ms, color) in enumerate(rows):
        ax.barh(ys[i], ms, height=0.6, color=color, alpha=0.9, zorder=3)
        ax.text(ms + 0.5, ys[i], f"{ms:.2f} ms", va="center", fontsize=9.5)
    ax.set_yticks(ys)
    ax.set_yticklabels([r[0] for r in rows], fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("control-plane CPU per invocation (ms)", fontsize=10)
    ax.set_xlim(0, 32)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", ls=":", alpha=0.4, zorder=0)
    fig.text(0.01, 0.01,
             "* OW is the standalone emulator's single JVM (orchestrator + per-activation docker-log "
             "log-store); Kn includes the kourier gateway + activator on the request path — see §5.1.",
             fontsize=7, color="0.35", ha="left")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUTDIR, f"figure3_cp_cost_per_inv.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure3_cp_cost_per_inv.{png,pdf}")


def load_lock_summary(stamp):
    """Return the lock-session driver's committed aggregate for one stamp."""
    p = os.path.join("results", f"lock_session_{stamp}", "lock_summary.json")
    return json.load(open(p))


def figure5_concurrency_invariance():
    """CP share vs load concurrency (c=1/2/4/8/16), OF/Fn/Kn + OW annotation.

    Quick-tier trend (REPEAT=3/TOTAL=3000, 2026-08-15, quiet-gated); the c=4
    point is the lock4 N=5 anchor (diamond markers), everything else the same-day
    sweep. OW is drawn as a flat reference band (81.2-82.0 across c=4/8) so the
    main axis stays readable at 0-18%. See paper §5.3 for the full table + flags.
    """
    by_plat = {p: {} for p in CONC_PLAT}
    for stamp in CONC_STAMPS:
        c = int(stamp[4:])
        s = load_lock_summary(stamp)
        for p in CONC_PLAT:
            by_plat[p][c] = s["platforms"][p]["cp_dynamic_share_pct"]
    anchor = load_lock_summary(CONC_ANCHOR)
    for p in CONC_PLAT:
        by_plat[p][4] = anchor["platforms"][p]["cp_dynamic_share_pct"]

    ow_vals = [load_lock_summary(st)["platforms"]["openwhisk"]["cp_dynamic_share_pct"]
               for st in CONC_OW_STAMPS]
    ow_vals.append(anchor["platforms"]["openwhisk"]["cp_dynamic_share_pct"])
    ow_lo, ow_hi = min(ow_vals), max(ow_vals)

    xs = list(range(len(CONC_C)))
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=150)
    for p in CONC_PLAT:
        pts = sorted(by_plat[p].items())
        cx = [xs[CONC_C.index(c)] for c, _ in pts]
        cy = [v for _, v in pts]
        ax.plot(cx, cy, color=PLATFORMS[p]["color"], lw=1.5, marker="o", ms=5.5,
                zorder=4, mfc="white", mew=1.4)
        for c, v in pts:
            if c == 4:  # lock4 N=5 anchor
                ax.plot(xs[CONC_C.index(c)], v, marker="D", ms=7.0, zorder=6,
                        color=PLATFORMS[p]["color"], mfc=PLATFORMS[p]["color"],
                        mec="black", mew=0.6)
            ax.text(xs[CONC_C.index(c)], v + 0.45, f"{v:.1f}", ha="center",
                    va="bottom", fontsize=7.5, color="0.2")
    # OpenWhisk inset (its ~81% would flatten the 0-18% main axis): zoomed strip
    # showing the spot-check flatness across c=4/8.
    ow_stamps = [CONC_ANCHOR] + list(CONC_OW_STAMPS)
    ow_c = [4] + list(CONC_OW_STAMPS.values())
    ow_s = [ow_vals[2]] + ow_vals[:2]  # lock4 anchor first, then ow4, ow8
    ow_color = PLATFORMS["openwhisk"]["color"]
    ow_med = (ow_lo + ow_hi) / 2
    ins = ax.inset_axes([0.13, 0.60, 0.30, 0.32])
    for i, (c, v) in enumerate(zip(ow_c, ow_s)):
        if c == 4:
            x = 3.9 if v == ow_s[0] else 4.1
        else:
            x = 8.0
        marker, mfc, ms = ("D", ow_color, 6.5) \
            if i == 0 else ("o", "white", 5.5)
        ins.plot(x, v, marker=marker, ms=ms, zorder=4,
                 color=ow_color, mfc=mfc, mec=ow_color, mew=1.2)
        ins.text(x, v + (0.5 if v < ow_med else 1.2), f"{v:.1f}",
                 ha="center", va="bottom", fontsize=7, color="0.2")
    ins.axhline(ow_med, color=ow_color, ls=":", lw=1.2, zorder=1)
    ins.text(8.55, ow_med, f" median {ow_med:.1f}", ha="right", va="center",
             fontsize=7, color=ow_color, style="italic")
    ins.set_xlim(3.4, 8.6)
    ins.set_ylim(ow_lo - 2.0, ow_hi + 2.0)
    ins.set_xticks([4, 8])
    ins.set_yticks([ow_lo, ow_hi])
    ins.tick_params(labelsize=7.5)
    ins.set_title("OpenWhisk (standalone) — inset: c=4/c=8 spot-check", fontsize=8.5,
                  fontweight="bold")
    ins.set_xticklabels(["4*", "8"])
    ax.set_xticks(xs)
    ax.set_xticklabels(["1", "2", "4*", "8", "16"], fontsize=10)
    ax.set_xlabel("load concurrency c (4* = lock4 N=5 anchor; others quick-tier "
                  "REPEAT=3/TOTAL=3000)", fontsize=9)
    ax.set_ylabel("control-plane share of dynamic CPU (%)", fontsize=10)
    style_ax(ax, 19, None)
    handles = [plt.Line2D([0], [0], color=PLATFORMS[p]["color"], lw=1.5, marker="o",
                          ms=5.5, mfc="white") for p in CONC_PLAT]
    handles.append(plt.Line2D([0], [0], marker="D", ls="", ms=6.5, color="black"))
    handles.append(plt.Line2D([0], [0], color=ow_color, lw=1.5, ls=":", marker="o",
                              ms=5.5, mfc="white"))
    labels = ([PLATFORMS[p]["label"] for p in CONC_PLAT]
              + ["c=4 lock4 anchor (N=5)", "OpenWhisk (standalone) — inset"])
    ax.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.15),
              frameon=False, fontsize=8.5, ncol=2, handlelength=1.4, columnspacing=1.4)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUTDIR, f"figure5_concurrency_invariance.{ext}"),
                    bbox_inches="tight")
    plt.close(fig)
    print("wrote figure5_concurrency_invariance.{png,pdf}")


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    agg = data()
    for rk in REGIMES:
        for pk in REGIMES[rk]["dirs"]:
            a = agg[(rk, pk)]
            print(f"{PLATFORMS[pk]['label']:9s} {REGIMES[rk]['label']:26s} "
                  f"reported={a['reported']:.2f} range={a['spread_min']:.2f}-{a['spread_max']:.2f} "
                  f"n={len(a['per_run'])} cp={a['cp_sec']:.2f}s fn={a['fn_sec']:.2f}s")
    figure1_four_platform_scatter(agg)
    figure2_attribution_split(agg)
    figure3_cp_cost_per_inv(agg)
    figure4_core_count(agg)
    figure5_concurrency_invariance()


if __name__ == "__main__":
    main()
