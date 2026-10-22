"""Runbook 31.18 D: key numbers quoted in the runbook must match the analysis JSON they come from.

Each claim is (text quoted in the runbook, [(json value, format), ...]). The test checks that the
quote is still in the runbook (whitespace collapsed, so line wraps do not matter) and that every
JSON value, formatted as the runbook prints it, appears in that quote. A retyped number that drifts
from the tool output, or an edit that drops the claim, fails here. JSON lives in the paper repo's
committed backup (../saqef-paper/results/*_analysis/); the test skips if that repo is absent.
"""
import glob
import json
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(os.path.dirname(REPO), "saqef-paper", "results")
RUNBOOK = os.path.join(REPO, "TROUBLESHOOTING_RUNBOOK.md")


def _flat(s):
    return re.sub(r"\s+", " ", s)


def _load(path):
    with open(path) as f:
        return json.load(f)


def _cp_rows():
    rows = {}
    for f in glob.glob(os.path.join(RES, "*_analysis", "cp_anatomy*.json")):
        for r in _load(f):
            rows[r["stamp"]] = r
    return rows


@unittest.skipUnless(os.path.isdir(RES), "paper repo results not found")
class TestRunbookNumbersMatchJson(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(RUNBOOK) as f:
            cls.runbook = _flat(f.read())
        cls.cp = _cp_rows()
        cold = _load(os.path.join(RES, "cold_analysis", "cold_.json"))
        cls.cold = {b["platform"]: b for b in cold["blocks"]}
        cls.w3 = cold["fn_w3"]

    def check(self, quote, values):
        quote = _flat(quote)
        self.assertTrue(quote in self.runbook, "claim no longer in the runbook: %r" % quote)
        for v, fmt in values:
            s = fmt % v
            self.assertRegex(quote, r"(?<![\d.])%s(?![\d])" % re.escape(s),
                             "JSON gives %s, runbook says: %r" % (s, quote))

    # §29.1 / §29.2: OpenWhisk log collector, cli -> driver at c = 1 and 4 (owlog29)
    def test_owlog29_cp_and_child_row(self):
        cp = self.cp
        for c, q in (("1", "| 1 | 21.11 → 3.11 | 14.09 → 0.04 | 4.49 → 2.37 | 5.69 → 5.62 |"),
                     ("4", "| 4 | 20.87 → 2.94 | 13.91 → 0.02 | 4.21 → 2.18 | 5.60 → 5.65 |")):
            cli, drv = cp["owlog29_tier1ow%s_cli" % c], cp["owlog29_tier1ow%s_driver" % c]
            child = lambda r: r["jvm_openj9_ms_inv"]["docker CLI children"]
            actor = lambda r: r["jvm_openj9_ms_inv"]["actor system"]
            self.check(q, [(cli["cp_ms_inv_recorded"], "%.2f"), (drv["cp_ms_inv_recorded"], "%.2f"),
                           (child(cli), "%.2f"), (child(drv), "%.2f"),
                           (actor(cli), "%.2f"), (actor(drv), "%.2f"),
                           (cli["fn_ms_inv"], "%.2f"), (drv["fn_ms_inv"], "%.2f")])

    def test_owlog29_driver_range(self):
        lo = min(self.cp["owlog29_tier1ow%s_driver" % c]["cp_ms_inv_recorded"] for c in "14")
        hi = max(self.cp["owlog29_tier1ow%s_driver" % c]["cp_ms_inv_recorded"] for c in "14")
        self.check("owlog29 driver cp 2.94–3.11 ms/inv", [(lo, "%.2f"), (hi, "%.2f")])

    # §33.2: the c = 8 pair
    def test_amend33_2_c8(self):
        cli = self.cp["owlog29_amend33_2_tier1ow8_cli"]["cp_ms_inv_recorded"]
        drv = self.cp["owlog29_amend33_2_tier1ow8_driver"]["cp_ms_inv_recorded"]
        old = self.cp["owlog29_tier1ow8_driver"]["cp_ms_inv_recorded"]
        self.check("cp 18.99 → 2.87 ms/inv", [(cli, "%.2f"), (drv, "%.2f")])
        self.check("Q2 **holds** (18.99 − 2.87 = 16.12 ≥ 9.0)", [(cli, "%.2f"), (drv, "%.2f")])
        self.assertAlmostEqual(round(cli, 2) - round(drv, 2), 16.12, places=6)
        self.check("cp 2.87 vs 3.03", [(drv, "%.2f"), (old, "%.2f")])

    # §32.1: W4 verdict table
    def test_w4_table(self):
        fn, ow, kn = self.cold["fn"], self.cold["openwhisk"], self.cold["knative"]
        self.check("| containers created per cold run | 71 (68–72) | 2 | 16 |",
                   [(fn["containers_created"], "%d"), (ow["containers_created"], "%d"),
                    (kn["containers_created"], "%d")])
        self.check("| C1 first-burst availability, cold | 0.68 (every run) — holds (< 0.99) | 1.00 — holds"
                   " | 1.00 — holds |",
                   [(fn["cold_first_avail"], "%.2f"), (ow["cold_first_avail"], "%.2f"),
                    (kn["cold_first_avail"], "%.2f")])
        self.check("| C2 first-burst p50 cold / warm | 1286 / 99 ms, 13× (no prediction) | 607 / 174 ms, "
                   "3.5× — holds | 2066 / 73 ms, 28× — holds |",
                   [(fn["cold_first_p50_ms"], "%.0f"), (fn["warm_first_p50_ms"], "%.0f"),
                    (fn["first_p50_ratio"], "%.0f"),
                    (ow["cold_first_p50_ms"], "%.0f"), (ow["warm_first_p50_ms"], "%.0f"),
                    (ow["first_p50_ratio"], "%.1f"),
                    (kn["cold_first_p50_ms"], "%.0f"), (kn["warm_first_p50_ms"], "%.0f"),
                    (kn["first_p50_ratio"], "%.0f")])
        self.check("| C3 cp per container; separation | 5.5 ms; holds | 436 ms; **fails** | 10.9 ms; **fails** |",
                   [(fn["cp_ms_per_container"], "%.1f"), (ow["cp_ms_per_container"], "%.0f"),
                    (kn["cp_ms_per_container"], "%.1f")])
        self.assertEqual((fn["C3"], ow["C3"], kn["C3"]), (True, False, False))
        self.check("| C3u (untracked − instrument) per container; separation | 234 ms; holds | −961 ms; "
                   "**fails** | 724 ms; holds |",
                   [(fn["untracked_net_ms_per_container"], "%.0f"),
                    (-ow["untracked_net_ms_per_container"], "%.0f"),
                    (kn["untracked_net_ms_per_container"], "%.0f")])
        self.assertEqual((fn["C3u"], ow["C3u"], kn["C3u"]), (True, False, True))
        self.check("| C4 later-burst p50 cold vs warm | +6.3 % — holds | −0.3 % — holds | **−17.5 % — fails** |",
                   [(100 * fn["later_p50_rel"], "%.1f"), (-100 * ow["later_p50_rel"], "%.1f"),
                    (-100 * kn["later_p50_rel"], "%.1f")])
        self.assertEqual((fn["C4"], ow["C4"], kn["C4"]), (True, True, False))
        self.check("| C5 function CPU per container | 0.131 s (anchor 0.14) | 0.165 s | 0.224 s |",
                   [(fn["fn_s_per_container"], "%.3f"), (ow["fn_s_per_container"], "%.3f"),
                    (kn["fn_s_per_container"], "%.3f")])
        self.check("later bursts are 17.5 % *faster* than warm (79.0 vs 95.8 ms)",
                   [(-100 * kn["later_p50_rel"], "%.1f"), (kn["cold_later_p50_ms"], "%.1f"),
                    (kn["warm_later_p50_ms"], "%.1f")])

    # §32.1: Fn W3 within one session (F-T6)
    def test_w4_fn_w3(self):
        w = self.w3
        self.assertTrue(all(w[k] for k in ("B0", "B1", "B2", "B4")))
        self.check("B2 drain 0.474 s vs 500 / 1033 rps = 0.484 s (0.98×)",
                   [(w["b500_drain_s"], "%.3f"), (w["steady_rps"], "%.0f"), (w["drain_ratio"], "%.2f")])
        self.check("B4 p99 502.6 vs 14.2 ms (35×)",
                   [(w["b500_p99_ms"], "%.1f"), (w["steady_p99_ms"], "%.1f"), (w["p99_ratio"], "%.0f")])
        self.check("OpenWhisk cp 3.1–3.4 ms/inv (driver store",
                   [(self.cp["cold_ow_warm"]["cp_ms_inv_recorded"], "%.1f"),
                    (self.cp["cold_ow_cold"]["cp_ms_inv_recorded"], "%.1f")])


if __name__ == "__main__":
    unittest.main()
