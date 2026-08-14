#!/usr/bin/env python3
"""Publication figures for the SAQEF control-plane overhead study.

Data-driven: every figure is derived from committed result sets
(results/<dir>/summary.json + runs.json). To add a platform (e.g. a future
one) or a regime, add one entry to REGIMES below and rerun — no script edits.

Single-story figures (no two-panel confusion, no dates on axes — provenance
lives in the paper captions):
  Figure 1 — core-count effect (Fn vs OpenFaaS): the controlled same-instrument
             experiment, 8 cores vs the same box cpuset-pinned to 2 cores.
             This is the machine-dependence contribution.
   Figure 2 — per-run shares, four platforms (8-core box, same instrument):
              every per-run value shown honestly (n=5 per platform, one
              matched session set -- the lock4 session (2026-08-14): all four
              platforms back-to-back the same day under the quiet gate; see
              the data-source note in the paper's Appendix A / figure caption).
  Figure 3 — attribution split (CP / fn / unclassified CPU-time) for the
             same four platforms.
  Figure 4 — control-plane CPU per invocation (ms), all four platforms.

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
    "openwhisk": {"label": "OpenWhisk", "color": "#2ca02c"},
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

# Figure 1 (core-count): the controlled Fn-vs-OF experiment
CORE_COUNT_REGS = ("8core_quiet", "2core")
CORE_COUNT_PLAT = ("fn", "openfaas")
# Figures 2-4 (four-platform, 8-core box, same instrument): the matched lock4
# session -- all four platforms back-to-back on 2026-08-14 under the quiet gate,
# each leg with a freshly calibrated idle-w (see the paper's data-source note in
# Appendix A). figure1's 8core_quiet/2core dirs are the separate controlled
# core-count experiment and are intentionally unchanged.
FOURPLAT_REGS = ("fourplat",)
FOURPLAT_PLAT = ("openfaas", "fn", "knative", "openwhisk")


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


def figure1_core_count(agg):
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
        fig.savefig(os.path.join(OUTDIR, f"figure1_core_count.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure1_core_count.{png,pdf}")


def figure2_four_platform_scatter(agg):
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
        fig.savefig(os.path.join(OUTDIR, f"figure2_four_platform_scatter.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure2_four_platform_scatter.{png,pdf}")


def figure3_attribution_split(agg):
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
        fig.savefig(os.path.join(OUTDIR, f"figure3_attribution_split.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure3_attribution_split.{png,pdf}")


def figure4_cp_cost_per_inv(agg):
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
             "log-store); Kn includes the kourier gateway + activator on the request path — see §5.6.",
             fontsize=7, color="0.35", ha="left")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUTDIR, f"figure4_cp_cost_per_inv.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure4_cp_cost_per_inv.{png,pdf}")


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    agg = data()
    for rk in REGIMES:
        for pk in REGIMES[rk]["dirs"]:
            a = agg[(rk, pk)]
            print(f"{PLATFORMS[pk]['label']:9s} {REGIMES[rk]['label']:26s} "
                  f"reported={a['reported']:.2f} range={a['spread_min']:.2f}-{a['spread_max']:.2f} "
                  f"n={len(a['per_run'])} cp={a['cp_sec']:.2f}s fn={a['fn_sec']:.2f}s")
    figure1_core_count(agg)
    figure2_four_platform_scatter(agg)
    figure3_attribution_split(agg)
    figure4_cp_cost_per_inv(agg)


if __name__ == "__main__":
    main()
