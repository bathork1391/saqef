#!/usr/bin/env python3
"""Publication figures for the SAQEF control-plane overhead study.

Data-driven: every figure is derived from committed result sets
(results/<dir>/summary.json + runs.json). To add a platform (e.g. OpenWhisk)
or a regime, add one entry to the CONFIG below and rerun — no script edits.

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
import matplotlib.ticker as mtick
from matplotlib.patches import Patch

OUTDIR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__))

PLATFORMS = {
    "fn":       {"label": "Fn",       "color": "#1f77b4"},
    "openfaas": {"label": "OpenFaaS", "color": "#ff7f0e"},
    "openwhisk": {"label": "OpenWhisk", "color": "#2ca02c"},
    "knative":  {"label": "Knative",  "color": "#9467bd"},
}

# regime -> label + per-platform list of result dirs (each dir is one independent
# session; a session contributes REPEAT per-run values). Headline number for a
# regime x platform = median of per-session medians (matches the paper: 8-core
# Fn 10.46 / OF 7.67; 2-core Fn 14.00 / OF 7.00 across two sessions).
REGIMES = {
    "8core": {
        "label": "8-core bare metal",
        "dirs": {
            "fn":       ["results/fn_cpubound_baremetal"],
            "openfaas": ["results/openfaas_cpubound_baremetal"],
        },
    },
    "2core": {
        "label": "2-core cpuset-pinned",
        "dirs": {
            "fn":       ["results/fn_cpubound_2core", "results/fn_cpubound_2core_session2"],
            "openfaas": ["results/openfaas_cpubound_2core", "results/openfaas_cpubound_2core_session2"],
        },
    },
}

# Same-day (2026-08-07) four-platform comparison on the same 8-core box, all
# full REPEAT=5. Fn gets BOTH same-day 8-core sessions so the figure honestly
# shows its bounded box drift (12.27 new-CLI regression vs 12.92 old-runner
# A/B). OpenWhisk + Knative ran under a non-quiet box (agent ~2.8 cores):
# shares are contention-robust but latency from those sessions is not citable.
FOURPLATFORM = {
    "label": "8-core bare metal, 2026-08-07",
    "dirs": {
        "openfaas":  ["results/regression/openfaas"],
        "fn":        ["results/fn_cpubound_crosscheck", "results/regression/fn", "results/fn_cpubound_crosscheck2"],
        "knative":   ["results/knative_cpubound_baremetal"],
        "openwhisk": ["results/openwhisk_cpubound_baremetal"],
    },
}


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


def cell_positions():
    """Return (xs, bar_width, xtick_pos, xtick_labels) for the 2-platform grid."""
    platform_keys = [p for p in PLATFORMS if any(p in REGIMES[r]["dirs"] for r in REGIMES)]
    regime_keys = list(REGIMES)
    n_plat = len(platform_keys)
    width = 0.8 / n_plat
    xs = {}
    xtick_pos = []
    for i, rk in enumerate(regime_keys):
        center = i
        for j, pk in enumerate(platform_keys):
            offset = (j - (n_plat - 1) / 2) * width
            xs[(rk, pk)] = center + offset
        xtick_pos.append(center)
    xtick_labels = [REGIMES[rk]["label"] for rk in regime_keys]
    return xs, width, xtick_pos, xtick_labels


def data():
    agg = {}
    for rk in REGIMES:
        for pk in REGIMES[rk]["dirs"]:
            agg[(rk, pk)] = collect(REGIMES[rk], pk)
    return agg


def figure1_share_by_regime(agg):
    xs, width, xtick_pos, xtick_labels = cell_positions()
    fig, ax = plt.subplots(figsize=(6.2, 4.0), dpi=150)
    for pk in PLATFORMS:
        if not any((rk, pk) in agg for rk in REGIMES):
            continue
        vals = []
        los, his = [], []
        for rk in REGIMES:
            if (rk, pk) not in agg:
                continue
            a = agg[(rk, pk)]
            vals.append(a["reported"])
            los.append(a["reported"] - a["spread_min"])
            his.append(a["spread_max"] - a["reported"])
        ys = [xs[(rk, pk)] for rk in REGIMES if (rk, pk) in agg]
        ax.bar(ys, vals, width=width, yerr=[los, his], capsize=4,
               label=PLATFORMS[pk]["label"], color=PLATFORMS[pk]["color"],
               error_kw={"elinewidth": 1.0, "capthick": 1.0}, zorder=3)
        for x, v in zip(ys, vals):
            ax.text(x, v + 0.25, f"{v:.2f}", ha="center", va="bottom", fontsize=9)
    for rk in REGIMES:
        if all((rk, pk) in agg for pk in PLATFORMS):
            fa, fb = agg[(rk, "fn")], agg[(rk, "openfaas")]
            gap = fa["reported"] - fb["reported"]
            mid = (xs[(rk, "fn")] + xs[(rk, "openfaas")]) / 2
            ax.annotate("", xy=(xs[(rk, "fn")], max(fa["reported"], fb["reported"]) + 1.0),
                        xytext=(mid, max(fa["reported"], fb["reported"]) + 1.0),
                        arrowprops=dict(arrowstyle="->", lw=0.9, color="0.35"))
            ax.text(mid, max(fa["reported"], fb["reported"]) + 1.15, f"gap {gap:.2f} pp",
                    ha="center", va="bottom", fontsize=8.5, color="0.25")
    ax.axhline(5.0, color="0.6", ls="--", lw=0.9)
    ax.text(xtick_pos[-1], 5.1, "5 pp decision gate", fontsize=8, color="0.4", va="bottom", ha="right")
    ax.set_xticks(xtick_pos)
    ax.set_xticklabels(xtick_labels, fontsize=9)
    ax.set_ylabel("CP dynamic share of CPU time (%)", fontsize=10)
    ax.set_ylim(0, 18)
    ax.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.0f"))
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", ls=":", alpha=0.4, zorder=0)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUTDIR, f"figure1_share_by_regime.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure1_share_by_regime.{png,pdf}")


def figure2_per_run_scatter(agg):
    xs, width, xtick_pos, xtick_labels = cell_positions()
    fig, ax = plt.subplots(figsize=(6.2, 4.0), dpi=150)
    rng = __import__("random").Random(0)
    for pk in PLATFORMS:
        if not any((rk, pk) in agg for rk in REGIMES):
            continue
        for rk in REGIMES:
            if (rk, pk) not in agg:
                continue
            a = agg[(rk, pk)]
            base = xs[(rk, pk)]
            for si, s in enumerate(a["sessions"]):
                jit = (rng.random() - 0.5) * width * 0.5
                style = dict(marker="o" if si == 0 else "s", s=26, alpha=0.75,
                             color=PLATFORMS[pk]["color"], edgecolor="white", lw=0.6)
                ax.scatter([base + jit] * len(s["per_run"]), s["per_run"], **style, zorder=4)
            ax.plot([base - width * 0.28, base + width * 0.28], [a["reported"]] * 2,
                    color="black", lw=1.4, zorder=5)
    for rk in REGIMES:
        if all((rk, pk) in agg for pk in PLATFORMS):
            fa, fb = agg[(rk, "fn")], agg[(rk, "openfaas")]
            gap = fa["reported"] - fb["reported"]
            mid = (xs[(rk, "fn")] + xs[(rk, "openfaas")]) / 2
            ax.text(mid, max(fa["reported"], fb["reported"]) + 0.7, f"gap {gap:.2f} pp",
                    ha="center", fontsize=8.5, color="0.25")
    ax.axhline(5.0, color="0.6", ls="--", lw=0.9)
    ax.set_xticks(xtick_pos)
    ax.set_xticklabels(xtick_labels, fontsize=9)
    ax.set_ylabel("CP dynamic share of CPU time (%)", fontsize=10)
    ax.set_ylim(4.5, 16.5)
    handles = [Patch(color=PLATFORMS[pk]["color"]) for pk in PLATFORMS if any((rk, pk) in agg for rk in REGIMES)]
    labels = [PLATFORMS[pk]["label"] for pk in PLATFORMS if any((rk, pk) in agg for rk in REGIMES)]
    handles.append(plt.Line2D([0], [0], color="black", lw=1.4))
    labels.append("reported median")
    ax.legend(handles, labels, loc="upper right", frameon=False, fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", ls=":", alpha=0.4, zorder=0)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUTDIR, f"figure2_per_run_scatter.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure2_per_run_scatter.{png,pdf}")


def figure3_attribution_split(agg):
    xs, width, xtick_pos, xtick_labels = cell_positions()
    fig, ax = plt.subplots(figsize=(6.2, 4.0), dpi=150)
    for pk in PLATFORMS:
        if not any((rk, pk) in agg for rk in REGIMES):
            continue
        ys = [xs[(rk, pk)] for rk in REGIMES if (rk, pk) in agg]
        cp = [agg[(rk, pk)]["cp_sec"] for rk in REGIMES if (rk, pk) in agg]
        fn = [agg[(rk, pk)]["fn_sec"] for rk in REGIMES if (rk, pk) in agg]
        unc = [agg[(rk, pk)]["unc_sec"] for rk in REGIMES if (rk, pk) in agg]
        ax.bar(ys, cp, width=width, color=PLATFORMS[pk]["color"], alpha=0.85,
               label=PLATFORMS[pk]["label"] + " — control plane", zorder=3)
        ax.bar(ys, fn, width=width, bottom=cp, color=PLATFORMS[pk]["color"], alpha=0.35,
               label=PLATFORMS[pk]["label"] + " — function", zorder=3)
        ax.bar(ys, unc, width=width, bottom=[c + f for c, f in zip(cp, fn)],
               color="0.85", label="unclassified", zorder=3)
        for x, a, c, f in zip(ys, [agg[(rk, pk)] for rk in REGIMES if (rk, pk) in agg], cp, fn):
            share = a["reported"]
            ax.text(x, c + f + 1.2, f"CP {share:.1f}%", ha="center", va="bottom", fontsize=8.5)
    ax.set_xticks(xtick_pos)
    ax.set_xticklabels(xtick_labels, fontsize=9)
    ax.set_ylabel("dynamic CPU time (s, per-run median)", fontsize=10)
    ax.set_ylim(0, 80)
    handles = [Patch(color=PLATFORMS[pk]["color"], alpha=0.85) for pk in PLATFORMS
               if any((rk, pk) in agg for rk in REGIMES)]
    labels = [PLATFORMS[pk]["label"] + " — control plane" for pk in PLATFORMS
              if any((rk, pk) in agg for rk in REGIMES)]
    handles += [Patch(color=PLATFORMS[pk]["color"], alpha=0.35) for pk in PLATFORMS
                if any((rk, pk) in agg for rk in REGIMES)]
    labels += [PLATFORMS[pk]["label"] + " — function" for pk in PLATFORMS
               if any((rk, pk) in agg for rk in REGIMES)]
    handles.append(Patch(color="0.85"))
    labels.append("unclassified")
    ax.legend(handles, labels, loc="upper left", frameon=False, fontsize=8.5, ncol=2)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", ls=":", alpha=0.4, zorder=0)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUTDIR, f"figure3_attribution_split.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure3_attribution_split.{png,pdf}")


def figure4_four_platforms():
    """Same-day 8-core ordering: OF 7.5 < Fn approx Knative 12-14 < OW 82.5.

    Horizontal bars sorted ascending; error bars span the full per-run spread
    (all sessions flattened). Separate figure because OpenWhisk's 82.5 dwarfs
    the 0-18 y-scale of figures 1-3. Attribution conventions differ per
    platform (see the paper §5.6 map) -- stated as a footnote, not buried.
    """
    rows = []
    for pk in ("openfaas", "fn", "knative", "openwhisk"):
        a = collect(FOURPLATFORM, pk)
        rows.append((PLATFORMS[pk]["label"], a["reported"], a["spread_min"],
                     a["spread_max"], PLATFORMS[pk]["color"], a))
    rows.sort(key=lambda r: r[1])
    fig, ax = plt.subplots(figsize=(6.4, 3.7), dpi=150)
    ys = list(range(len(rows)))
    for i, (label, med, lo, hi, color, a) in enumerate(rows):
        ax.barh(ys[i], med, height=0.6, color=color, alpha=0.85, zorder=3)
        ax.errorbar(med, ys[i], xerr=[[med - lo], [hi - med]], fmt="none",
                    ecolor="0.2", elinewidth=1.0, capsize=3, zorder=4)
        ax.text(med + 1.4, ys[i], f"{med:.2f}%", va="center", fontsize=9.5)
    ax.set_yticks(ys)
    ax.set_yticklabels([r[0] for r in rows], fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("CP dynamic share of CPU time (%)", fontsize=10)
    ax.set_xlim(0, 95)
    ax.axvline(5.0, color="0.6", ls="--", lw=0.9)
    ax.text(5.3, len(rows) - 0.35, "5 pp gate", fontsize=8, color="0.4")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", ls=":", alpha=0.4, zorder=0)
    fig.text(0.01, 0.01,
             "* attribution conventions differ per platform (of-watchdog/queue-proxy in fn; "
             "kourier gateway + activator in CP; OW standalone log-store in CP) -- see paper §5.6.",
             fontsize=7, color="0.35", ha="left")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUTDIR, f"figure4_four_platforms.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure4_four_platforms.{png,pdf}")


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    agg = data()
    for rk in REGIMES:
        for pk in REGIMES[rk]["dirs"]:
            a = agg[(rk, pk)]
            print(f"{PLATFORMS[pk]['label']:9s} {REGIMES[rk]['label']:22s} "
                  f"reported={a['reported']:.2f} range={a['spread_min']:.2f}-{a['spread_max']:.2f} "
                  f"n={len(a['per_run'])} cp={a['cp_sec']:.2f}s fn={a['fn_sec']:.2f}s")
    for pk in FOURPLATFORM["dirs"]:
        a = collect(FOURPLATFORM, pk)
        print(f"{PLATFORMS[pk]['label']:9s} {FOURPLATFORM['label']:22s} "
              f"reported={a['reported']:.2f} range={a['spread_min']:.2f}-{a['spread_max']:.2f} "
              f"n={len(a['per_run'])} cp={a['cp_sec']:.2f}s fn={a['fn_sec']:.2f}s")
    figure1_share_by_regime(agg)
    figure2_per_run_scatter(agg)
    figure3_attribution_split(agg)
    figure4_four_platforms()


if __name__ == "__main__":
    main()
