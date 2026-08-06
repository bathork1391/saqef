#!/usr/bin/env python3
"""Publication figures for the SAQEF control-plane overhead study.

Data-driven: every figure is derived from committed result sets
(results/<dir>/summary.json + runs.json). To add a platform (e.g. a future
one) or a regime, add one entry to REGIMES below and rerun — no script edits.

Panels: figures 1-3 each have two panels.
  (a) core-count effect — Fn vs OpenFaaS, 8-core (quiet 2026-08-05) vs 2-core
      cpuset-pinned (2026-08-06). Only Fn/OpenFaaS have 2-core data.
  (b) four platforms, same day (2026-08-07), 8-core box — OpenFaaS, Fn,
      Knative, OpenWhisk. OpenWhisk dwarfs the others (82.5 vs 7-14), so it
      gets its own y-scale.
Figure 4: per-invocation control-plane CPU cost (ms CPU / invocation), all
four platforms — the orchestration-cost-per-invocation comparison.

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
    "fn":        {"label": "Fn",       "color": "#1f77b4"},
    "openfaas":  {"label": "OpenFaaS", "color": "#ff7f0e"},
    "openwhisk": {"label": "OpenWhisk", "color": "#2ca02c"},
    "knative":   {"label": "Knative",  "color": "#9467bd"},
}

# regime -> label + per-platform list of result dirs (each dir is one independent
# session; a session contributes REPEAT per-run values). Headline number for a
# regime x platform = median of per-session medians (matches the paper).
REGIMES = {
    "8core": {
        "label": "8-core (quiet,\n2026-08-05)",
        "dirs": {
            "fn":       ["results/fn_cpubound_baremetal"],
            "openfaas": ["results/openfaas_cpubound_baremetal"],
        },
    },
    "2core": {
        "label": "2-core pinned\n(2026-08-06)",
        "dirs": {
            "fn":       ["results/fn_cpubound_2core", "results/fn_cpubound_2core_session2"],
            "openfaas": ["results/openfaas_cpubound_2core", "results/openfaas_cpubound_2core_session2"],
        },
    },
    "4p": {
        "label": "8-core, 4 platforms\n(2026-08-07)",
        "dirs": {
            "openfaas":  ["results/regression/openfaas"],
            "fn":        ["results/fn_cpubound_crosscheck", "results/regression/fn",
                          "results/fn_cpubound_crosscheck2"],
            "knative":   ["results/knative_cpubound_baremetal"],
            "openwhisk": ["results/openwhisk_cpubound_baremetal"],
        },
    },
}

CORE_COUNT_REGS = ("8core", "2core")   # panel (a): the controlled Fn-vs-OF experiment
CORE_COUNT_PLAT = ("fn", "openfaas")
ALL4_REGS = ("4p",)                    # panel (b): four platforms, same day
ALL4_PLAT = ("openfaas", "fn", "knative", "openwhisk")


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


def positions(regimes, platforms):
    """Return (xs, width, xtick_pos, xtick_labels) for one panel's grid."""
    n_plat = len(platforms)
    width = 0.8 / n_plat
    xs = {}
    xtick_pos = []
    for i, rk in enumerate(regimes):
        center = i
        for j, pk in enumerate(platforms):
            xs[(rk, pk)] = center + (j - (n_plat - 1) / 2) * width
        xtick_pos.append(center)
    xtick_labels = [REGIMES[rk]["label"] for rk in regimes]
    return xs, width, xtick_pos, xtick_labels


def panel_share(ax, agg, regimes, platforms, ymax, gate=True):
    xs, width, xtick_pos, xtick_labels = positions(regimes, platforms)
    for pk in platforms:
        ys = [xs[(rk, pk)] for rk in regimes if (rk, pk) in agg]
        vals = [agg[(rk, pk)]["reported"] for rk in regimes if (rk, pk) in agg]
        los = [agg[(rk, pk)]["reported"] - agg[(rk, pk)]["spread_min"] for rk in regimes if (rk, pk) in agg]
        his = [agg[(rk, pk)]["spread_max"] - agg[(rk, pk)]["reported"] for rk in regimes if (rk, pk) in agg]
        ax.bar(ys, vals, width=width, yerr=[los, his], capsize=4,
               label=PLATFORMS[pk]["label"], color=PLATFORMS[pk]["color"],
               error_kw={"elinewidth": 1.0, "capthick": 1.0}, zorder=3)
        for x, v in zip(ys, vals):
            ax.text(x, v + 0.25, f"{v:.2f}", ha="center", va="bottom", fontsize=9)
    if gate:
        ax.axhline(5.0, color="0.6", ls="--", lw=0.9)
        ax.text(xtick_pos[-1], 5.1, "5 pp decision gate", fontsize=8, color="0.4",
                va="bottom", ha="right")
    if "fn" in platforms and "openfaas" in platforms:
        for rk in regimes:
            if (rk, "fn") in agg and (rk, "openfaas") in agg:
                fa, fb = agg[(rk, "fn")], agg[(rk, "openfaas")]
                gap = fa["reported"] - fb["reported"]
                mid = (xs[(rk, "fn")] + xs[(rk, "openfaas")]) / 2
                top = max(fa["reported"], fb["reported"]) + 1.0
                ax.annotate("", xy=(xs[(rk, "fn")], top), xytext=(mid, top),
                            arrowprops=dict(arrowstyle="->", lw=0.9, color="0.35"))
                ax.text(mid, top + 0.15, f"gap {gap:.2f} pp", ha="center",
                        va="bottom", fontsize=8.5, color="0.25")
    ax.set_xticks(xtick_pos)
    ax.set_xticklabels(xtick_labels, fontsize=8.5)
    ax.set_ylabel("CP dynamic share of CPU time (%)", fontsize=10)
    ax.set_ylim(0, ymax)
    ax.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.0f"))
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", ls=":", alpha=0.4, zorder=0)


def panel_scatter(ax, agg, regimes, platforms, ymin, ymax):
    xs, width, xtick_pos, xtick_labels = positions(regimes, platforms)
    rng = __import__("random").Random(0)
    markers = ("o", "s", "^")
    for pk in platforms:
        for rk in regimes:
            if (rk, pk) not in agg:
                continue
            a = agg[(rk, pk)]
            base = xs[(rk, pk)]
            for si, s in enumerate(a["sessions"]):
                jit = (rng.random() - 0.5) * width * 0.5
                ax.scatter([base + jit] * len(s["per_run"]), s["per_run"],
                           marker=markers[si % len(markers)], s=26, alpha=0.75,
                           color=PLATFORMS[pk]["color"], edgecolor="white", lw=0.6, zorder=4)
            ax.plot([base - width * 0.28, base + width * 0.28], [a["reported"]] * 2,
                    color="black", lw=1.4, zorder=5)
    for rk in regimes:
        if (rk, "fn") in agg and (rk, "openfaas") in agg:
            fa, fb = agg[(rk, "fn")], agg[(rk, "openfaas")]
            mid = (xs[(rk, "fn")] + xs[(rk, "openfaas")]) / 2
            ax.text(mid, max(fa["reported"], fb["reported"]) + 0.7, f"gap {fa['reported'] - fb['reported']:.2f} pp",
                    ha="center", fontsize=8.5, color="0.25")
    ax.axhline(5.0, color="0.6", ls="--", lw=0.9)
    ax.set_xticks(xtick_pos)
    ax.set_xticklabels(xtick_labels, fontsize=8.5)
    ax.set_ylabel("CP dynamic share of CPU time (%)", fontsize=10)
    ax.set_ylim(ymin, ymax)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", ls=":", alpha=0.4, zorder=0)


def panel_split(ax, agg, regimes, platforms, ymax):
    xs, width, xtick_pos, xtick_labels = positions(regimes, platforms)
    for pk in platforms:
        ys = [xs[(rk, pk)] for rk in regimes if (rk, pk) in agg]
        cp = [agg[(rk, pk)]["cp_sec"] for rk in regimes if (rk, pk) in agg]
        fn = [agg[(rk, pk)]["fn_sec"] for rk in regimes if (rk, pk) in agg]
        unc = [agg[(rk, pk)]["unc_sec"] for rk in regimes if (rk, pk) in agg]
        ax.bar(ys, cp, width=width, color=PLATFORMS[pk]["color"], alpha=0.85, zorder=3)
        ax.bar(ys, fn, width=width, bottom=cp, color=PLATFORMS[pk]["color"], alpha=0.35, zorder=3)
        ax.bar(ys, unc, width=width, bottom=[c + f for c, f in zip(cp, fn)],
               color="0.85", zorder=3)
        for x, a, c, f in zip(ys, [agg[(rk, pk)] for rk in regimes if (rk, pk) in agg], cp, fn):
            share = a["reported"]
            ax.text(x, c + f + 1.2, f"CP {share:.1f}%", ha="center", va="bottom", fontsize=8.5)
    ax.set_xticks(xtick_pos)
    ax.set_xticklabels(xtick_labels, fontsize=8.5)
    ax.set_ylabel("dynamic CPU time (s, per-run median)", fontsize=10)
    ax.set_ylim(0, ymax)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", ls=":", alpha=0.4, zorder=0)


def figure1_share_by_regime(agg):
    fig, (axa, axb) = plt.subplots(
        1, 2, figsize=(11.0, 4.0), dpi=150, gridspec_kw={"width_ratios": [2.1, 1.0]})
    panel_share(axa, agg, CORE_COUNT_REGS, CORE_COUNT_PLAT, ymax=18, gate=True)
    axa.set_title("(a) core-count effect, same instrument (Fn vs OpenFaaS)", fontsize=9.5, loc="left")
    panel_share(axb, agg, ALL4_REGS, ALL4_PLAT, ymax=95, gate=False)
    axb.set_title("(b) four platforms, 8-core (2026-08-07)", fontsize=9.5, loc="left")
    handles = [Patch(color=PLATFORMS[pk]["color"]) for pk in
               ("fn", "openfaas", "knative", "openwhisk")]
    labels = [PLATFORMS[pk]["label"] for pk in ("fn", "openfaas", "knative", "openwhisk")]
    axa.legend(handles, labels, loc="upper left", frameon=False, fontsize=8.5, ncol=2)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUTDIR, f"figure1_share_by_regime.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure1_share_by_regime.{png,pdf}")


def figure2_per_run_scatter(agg):
    fig, (axa, axb) = plt.subplots(
        1, 2, figsize=(11.0, 4.0), dpi=150, gridspec_kw={"width_ratios": [2.1, 1.0]})
    panel_scatter(axa, agg, CORE_COUNT_REGS, CORE_COUNT_PLAT, ymin=4.5, ymax=16.5)
    axa.set_title("(a) core-count effect (Fn vs OpenFaaS)", fontsize=9.5, loc="left")
    panel_scatter(axb, agg, ALL4_REGS, ALL4_PLAT, ymin=0, ymax=95)
    axb.set_title("(b) four platforms, 8-core (2026-08-07)", fontsize=9.5, loc="left")
    handles = [Patch(color=PLATFORMS[pk]["color"]) for pk in
               ("fn", "openfaas", "knative", "openwhisk")]
    labels = [PLATFORMS[pk]["label"] for pk in ("fn", "openfaas", "knative", "openwhisk")]
    handles.append(plt.Line2D([0], [0], color="black", lw=1.4))
    labels.append("reported median")
    axa.legend(handles, labels, loc="upper right", frameon=False, fontsize=8.5, ncol=2)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUTDIR, f"figure2_per_run_scatter.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure2_per_run_scatter.{png,pdf}")


def figure3_attribution_split(agg):
    fig, (axa, axb) = plt.subplots(
        1, 2, figsize=(11.0, 4.0), dpi=150, gridspec_kw={"width_ratios": [2.1, 1.0]})
    panel_split(axa, agg, CORE_COUNT_REGS, CORE_COUNT_PLAT, ymax=80)
    axa.set_title("(a) core-count effect (Fn vs OpenFaaS)", fontsize=9.5, loc="left")
    panel_split(axb, agg, ALL4_REGS, ALL4_PLAT, ymax=360)
    axb.set_title("(b) four platforms, 8-core (2026-08-07)", fontsize=9.5, loc="left")
    handles = [Patch(color=PLATFORMS[pk]["color"], alpha=0.85) for pk in
               ("fn", "openfaas", "knative", "openwhisk")]
    labels = [PLATFORMS[pk]["label"] + " — control plane" for pk in
              ("fn", "openfaas", "knative", "openwhisk")]
    handles += [Patch(color=PLATFORMS[pk]["color"], alpha=0.35) for pk in
                ("fn", "openfaas", "knative", "openwhisk")]
    labels += [PLATFORMS[pk]["label"] + " — function" for pk in
               ("fn", "openfaas", "knative", "openwhisk")]
    handles.append(Patch(color="0.85"))
    labels.append("unclassified")
    axa.legend(handles, labels, loc="upper left", frameon=False, fontsize=8, ncol=2)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(OUTDIR, f"figure3_attribution_split.{ext}"), bbox_inches="tight")
    plt.close(fig)
    print("wrote figure3_attribution_split.{png,pdf}")


def figure4_cp_cost_per_inv(agg):
    """Control-plane CPU cost per invocation (ms CPU / inv), all four platforms.

    cp_cpu_s median per run / 10000 inv * 1000. This is the per-request
    orchestration tax (fnserver 0.79, of-watchdog 0.56, Kn ~1.1, OW ~27) and
    does not duplicate any other figure.
    """
    rows = []
    for pk in ("openfaas", "fn", "knative", "openwhisk"):
        a = agg[("4p", pk)]
        ms = a["cp_sec"] / 10000.0 * 1000.0
        rows.append((PLATFORMS[pk]["label"], ms, PLATFORMS[pk]["color"]))
    rows.sort(key=lambda r: r[1])
    fig, ax = plt.subplots(figsize=(6.4, 3.4), dpi=150)
    ys = list(range(len(rows)))
    for i, (label, ms, color) in enumerate(rows):
        ax.barh(ys[i], ms, height=0.6, color=color, alpha=0.85, zorder=3)
        ax.text(ms + 0.4, ys[i], f"{ms:.2f} ms", va="center", fontsize=9.5)
    ax.set_yticks(ys)
    ax.set_yticklabels([r[0] for r in rows], fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("control-plane CPU per invocation (ms)", fontsize=10)
    ax.set_xlim(0, 30)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", ls=":", alpha=0.4, zorder=0)
    fig.text(0.01, 0.01,
             "* OW is the standalone emulator's single JVM (orchestrator + per-activation docker-log "
             "log-store); Kn includes the kourier gateway + activator on the request path — see paper §5.6.",
             fontsize=7, color="0.35", ha="left")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
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
    figure1_share_by_regime(agg)
    figure2_per_run_scatter(agg)
    figure3_attribution_split(agg)
    figure4_cp_cost_per_inv(agg)


if __name__ == "__main__":
    main()
