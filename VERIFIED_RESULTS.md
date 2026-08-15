# SAQEF — Verified results master document (single source of truth)

Machine-generated; do not edit by hand. Regenerate with:

```bash
python3 tools/emit_verified_results.py
```

Every number below is computed at emit time from the committed result files. Figures are built from these numbers: `figures/make_figures.py` reads the same committed result sets. A figure or table that disagrees with this document is wrong by construction.

Provenance: matched lock4 session 2026-08-14 (`results/*_cpubound_lock_lock4/`), all four platforms back-to-back same day under the self-certifying quiet gate (ambient 5.9-7.4% of a 15% ceiling); core-count experiment 2026-08-05/06 (`results/*_cpubound_baremetal`, `results/*_cpubound_2core{,_session2}`); idle-w N=5 calibration `results/idle_w_calibration/lock_lock4/*.txt`; contamination A/B `results/{fn,openfaas}_contamination_ab/contamination_ab.json`; concurrency sweep 2026-08-15 (quick-tier, `results/lock_session_conc*/` + `results/lock_session_ow*/`; c=4 anchored by lock4); Fn freeze ablation 2026-08-15 (quick-tier diagnostic, `results/fn_freeze_{baseline,off}_quick_quick/`).

## 1. Matched lock4 session — headline numbers (all medians of N=5)

| platform | CP share % | per-run range | CI (bootstrap) | CV % | IQR | CP ms/inv | fn ms/inv | per-inv CP mJ | per-inv CP µg | energy J/run | carbon g/run | p50/p99 ms | rps | SLO | host_sat % | idle_w W | RAPL err % (median; run range) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| OpenFaaS | **7.58** | 6.78–7.66 | 6.78–7.66 | 5.4 | 0.56 | 0.54 | 6.62 | 1.90 | 0.09 | 331.3 | 0.016 | 7.0/13.1 | 532.8 | 1 | 70.8 | 4.235 | 2.3 (1.0–34.5) |
| Fn | **11.29** | 9.82–11.50 | 9.82–11.50 | 6.4 | 0.72 | 0.72 | 5.66 | 2.52 | 0.12 | 295.3 | 0.014 | 6.5/9.4 | 590.5 | 1 | 71.9 | 4.249 | 3.4 (0.6–32.0) |
| Knative | **11.47** | 10.94–12.31 | 10.94–12.31 | 4.4 | 0.36 | 0.88 | 6.82 | 3.09 | 0.15 | 383.0 | 0.018 | 7.6/11.0 | 505.8 | 1 | 74.1 | 5.739 | 5.9 (2.5–27.2) |
| OpenWhisk (standalone) | **81.78** | 80.65–84.45 | 80.65–84.45 | 1.7 | 0.18 | 25.66 | 5.72 | 89.80 | 4.30 | 2447.4 | 0.117 | 110.7/189.2 | 35.0 | 1 | 60.7 | 4.882 | 42.0 (27.4–46.8) |

Per-inv CP energy/carbon are model-based (CP CPU per invocation x 3.5 W/busy-core; carbon = kWh x PUE 1.15 x CI 150 gCO2/kWh). fn cost includes the request-path sidecar/proxy in the fn bucket for OpenFaaS/Knative. OpenWhisk CP is the standalone JVM incl. per-activation docker-log log-store; its RAPL fit is structurally poor (energy reported as model-estimated only).

## 2. lock4 per-run values (raw, from committed runs.json)

- **OpenFaaS**: share % = 6.78, 7.06, 7.66, 7.58, 7.62 | CP s = 4.65, 4.91, 5.51, 5.44, 5.46 | fn s = 64.03, 64.61, 66.42, 66.32, 66.21 | RAPL err % = 34.55, 28.55, 2.33, 1.01, 1.06 | unclassified s = 0.26, 0.25, 0.32, 0.32, 0.30
- **Fn**: share % = 9.82, 10.58, 11.29, 11.50, 11.30 | CP s = 6.09, 6.64, 7.21, 7.35, 7.21 | fn s = 55.93, 56.08, 56.61, 56.56, 56.62 | RAPL err % = 32.00, 19.42, 3.36, 1.53, 0.55 | unclassified s = 0.22, 0.25, 0.28, 0.27, 0.27
- **Knative**: share % = 10.94, 11.32, 11.68, 11.47, 12.31 | CP s = 8.09, 8.66, 9.02, 8.83, 9.57 | fn s = 65.84, 67.84, 68.16, 68.15, 68.16 | RAPL err % = 27.18, 2.45, 5.88, 4.11, 8.80 | unclassified s = 0.11, 0.13, 0.13, 0.15, 0.14
- **OpenWhisk (standalone)**: share % = 84.45, 80.65, 81.78, 81.75, 81.93 | CP s = 309.09, 238.59, 255.57, 256.58, 259.94 | fn s = 56.89, 57.25, 56.95, 57.27, 57.31 | RAPL err % = 27.36, 43.80, 38.61, 42.02, 46.77 | unclassified s = 2.49, 4.02, 3.55, 4.03, 5.04

OW unclassified (median 4.02 s) is dominated by the always-resident k3s/Knative substrate (kourier-gateway, metrics-server, webhook, coredns, controllers), not the standalone's prewarm `wsk0` containers (~0.04 s).

## 3. Core-count experiment (Fn vs OpenFaaS, same instrument)

| regime | platform | median share % | per-run range | per-run values |
|---|---|---|---|---|
| 8core | fn | 10.46 | 9.80–11.74 | 9.80, 10.34, 10.46, 11.74, 11.72 |
| 8core | openfaas | 7.67 | 6.80–7.83 | 6.80, 6.96, 7.83, 7.80, 7.67 |
| 2core_s1 | fn | 13.91 | 13.34–14.07 | 13.34, 13.40, 14.07, 13.91, 14.06 |
| 2core_s1 | openfaas | 6.82 | 6.28–6.96 | 6.73, 6.95, 6.82, 6.28, 6.96 |
| 2core_s2 | fn | 14.08 | 13.69–14.38 | 13.69, 14.08, 14.38, 14.28, 14.07 |
| 2core_s2 | openfaas | 7.17 | 6.90–7.27 | 6.90, 7.17, 7.20, 7.11, 7.27 |

Fn−OF gap: 8-core bare metal 2.79 pp (below the 5 pp gate); 2-core pinned session 1 7.09 pp, session 2 6.91 pp (both above the gate; reproduced to ~0.2 pp). 2-core host_saturated=true (98.5–98.7%), so latency from that pair is not citable; energy not citable without a pin-specific idle-w (RAPL err 43–60%).

## 4. Idle-w calibration (lock4, N=5 repeated 60 s RAPL reads)

| stack state | median idle W | N reads W | source |
|---|---|---|---|
| bare | 4.084 | 4.084 / 4.515 / 3.864 / 4.152 / 3.903 | `results/idle_w_calibration/lock_lock4/idle_w_bare.txt` |
| openfaas@16 | 4.235 | 4.362 / 4.017 / 4.200 / 4.418 / 4.235 | `results/idle_w_calibration/lock_lock4/idle_w_openfaas.txt` |
| fn | 4.249 | 4.261 / 3.994 / 4.243 / 4.339 / 4.249 | `results/idle_w_calibration/lock_lock4/idle_w_fn.txt` |
| knative hello@16 | 5.739 | 5.768 / 5.477 / 6.272 / 5.739 / 5.435 | `results/idle_w_calibration/lock_lock4/idle_w_knative.txt` |
| openwhisk | 4.882 | 5.907 / 4.828 / 5.249 / 4.464 / 4.882 | `results/idle_w_calibration/lock_lock4/idle_w_openwhisk.txt` |

Knative warm-replica idle premium (hello @ 16 replicas vs bare): 0.690 W on 2026-08-09 (3.871 bare / 4.561 loaded) and ~1.66 W on 2026-08-14 (4.084 / 5.739) — direction confirmed both N=5 days, magnitude day-state dependent; paper cites '~0.7–1.7 W', not a fixed number.

## 5. Contamination A/B (agent-style load profile-matched to the 2026-08-07 incident)

| platform | clean median % | dirty median % | delta pp | clean host_sat | dirty host_sat |
|---|---|---|---|---|---|
| fn | 10.0 | 12.16 | +2.16 | 69.7 | 93.6 |
| openfaas | 6.9 | 7.24 | +0.34 | 71.6 | 92.7 |

Clean gaps: Fn 10.0 / OF 6.9 (gap 3.1 pp); dirty gaps: Fn 12.16 / OF 7.24 (gap 4.92 pp, just 0.08 pp below the 5 pp gate). QoS contrast is the larger contamination effect (p99 +5.5/+5.4 ms; throughput −16/−18%); dirty-leg latency not citable.

## 6. Sensitivity re-derivations (from committed raw data)

| platform | share % (as-reported) | flat-5 ms function cost → share % | convention: queue-proxy → CP → share % |
|---|---|---|---|
| OpenFaaS | 7.58 | 9.81 | n/a (convention unchanged) |
| Fn | 11.29 | 12.60 | n/a (convention unchanged) |
| Knative | 11.47 | 15.01 | 24.77 |
| OpenWhisk (standalone) | 81.78 | 83.69 | n/a (convention unchanged) |

Flat-5ms normalization keeps the denominator's function term identical across platforms (5 ms CPU/inv x 10000 inv = 50 s). The queue-proxy→CP row adds the Knative request-path sidecar CPU into the CP bucket (harness convention keeps it in fn; the paper's convention-normalized view quotes 24.8%).

## 7. Multi-session stability band (per-platform median shares)

| platform | session median shares % |
|---|---|
| OpenFaaS | 7.29, 7.58, 7.67 |
| Fn | 11.16, 11.29, 10.46, 12.92 |
| Knative | 11.82, 11.47, 12.44 |
| OpenWhisk (standalone) | 81.88, 81.78, 82.36 |

## 8. Concurrency sweep — CP share does NOT amortize with load concurrency (quick-tier trend, 2026-08-15)

`cp_dynamic_share_pct` (%) vs load concurrency c. Same-day quick-tier protocol (REPEAT=3/TOTAL=3000 per leg, quiet-gated); c=4 column is the lock4 N=5 anchor. Source: `results/lock_session_conc{1,2,8,16}/lock_summary.json`, OW spot-check `results/lock_session_ow{4,8}/lock_summary.json`, anchor `results/lock_session_lock4/lock_summary.json`.

| platform | c=1 | c=2 | c=8 | c=16 | c=4 anchor (lock4 N=5) |
|---|---|---|---|---|---|
| OpenFaaS | 7.00 | 5.82 | 6.51 | 7.75 | 7.58 |
| Fn | 12.66 | 11.06 | 9.92 | 11.01 | 11.29 |
| Knative | 14.08 | 11.97 | 12.10 | 12.08 | 11.47 |
| OpenWhisk (standalone) | — | — | 81.16 (c=8 spot) | — | 81.78 (c=4 spot 81.96) |

Flags (quick-tier trend-only framing, paper §5.5): c=8/16 host_sat 88-94% -> QoS/energy not citable there (share is); c=16 legs print INCOMPLETE-RUN (2992/3000) = benign sampler truncation on ~3 s runs, NOT the OpenWhisk duration bug; Fn c=16 run_1 = 17.43 (CV 23.9%) is the sweep's noisiest point; OF c=2 = 5.82 is an all-time low and non-monotonic (leg-level box state, not a trend). Ordering OF < Fn ≈ Kn << OW survives every c; mechanism = per-invocation CP cost (CP ms/inv OF 0.40-0.49, Fn 0.52-0.83, Kn 0.72-1.09, OW ~30) does not amortize with wall-clock concurrency.

## 9. Fn freeze ablation (quick-tier diagnostic, 2026-08-15; never a headline)

Fn's hot-container pause/unpause churn vs disabled freezing (`FN_FREEZE_IDLE_MSECS=-1`; fnproject semantics — a NEGATIVE value disables freeze, `0` = freeze without any delay, i.e. MAXIMUM churn, NOT 'off'; the morning `=0` leg is invalid, share 26.49%, never cited). Source: gitignored quick-tier outdirs `results/fn_freeze_{baseline,off}_quick_quick/` (present on the measurement box).

| leg | median share % | run values % | CP s/run |
|---|---|---|---|
| baseline (default freeze) | 10.85 | 10.85, 11.04, 10.84 | 2.05–2.09 |
| freeze disabled (-1) | 9.82 | 9.82, 9.69, 9.82 | 1.79–1.82 |

Result: baseline 10.85 (10.84-11.04) vs freeze disabled 9.82 (9.69-9.82), REPEAT=3, non-overlapping run ranges, ~1.0 pp, all gates green — pause/unpause churn is real but modest, consistent with Fn drift being scheduling-dominated.
