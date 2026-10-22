"""Runbook 29.4: fixes from the owlog29 external review."""
import importlib.machinery
import json
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import unittest.mock as mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def harness():
    return importlib.machinery.SourceFileLoader(
        "saqef_harness_294", os.path.join(REPO, "saqef_harness.py")).load_module()


class TestGitProvenance(unittest.TestCase):
    def test_rev_matches_git_and_uses_safe_directory(self):
        h = harness()
        want = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(h.harness_git_rev(), want)
        src = open(os.path.join(REPO, "saqef_harness.py")).read()
        self.assertEqual(src.count('"safe.directory=" + repo'), 2)

    def test_git_failure_is_unknown_not_clean(self):
        h = harness()
        bad = types.SimpleNamespace(returncode=128, stdout="", stderr="dubious ownership")
        with mock.patch.object(h.subprocess, "run", return_value=bad):
            self.assertEqual(h.harness_git_rev(), "unknown")
            self.assertIsNone(h.harness_git_dirty())


class TestVerifyClassifiesLikeRun(unittest.TestCase):
    """verify used to count every non-cp container from birth as function
    (9.77 s/inv for a ~6 ms handler); it must pass members and the load window."""

    def test_verify_passes_members_and_window(self):
        h = harness()
        seen = {}

        def fake_totals(samples, cp_sub, fn_sub="", cp_members=None, fn_members=None,
                        fn_allow_configured=False, window=None):
            seen.update(fn_members=fn_members, allow=fn_allow_configured, window=window)
            return (0.1, 0.6, 0, 0, [], 0, 0, 0, 0)

        inv = {"wsk0_3_guest_hello": ("openwhisk/action-python-v3.11:nightly", []),
               "k8s_activator_x": ("e5ab", [])}
        args = types.SimpleNamespace(
            auth=None, outdir=tempfile.mkdtemp(), sampler="cgroup", rescan_s=0.25, sample_s=0.05,
            url="http://x", verify_n=100, concurrency=1, interarrival_ms=0.0,
            fn_containers=[], fn_images=["action-python-v3.11"], fn_labels=[],
            cp_images=[], cp_labels=[], verify_budget_ms=5.0, platform="openwhisk")
        stop = threading.Event()
        th = threading.Thread(target=lambda: None)
        th.start()
        with mock.patch.object(h, "docker_inventory", return_value=inv), \
             mock.patch.object(h, "start_sampler", return_value=([], stop, None, th)), \
             mock.patch.object(h, "run_load", return_value=[(True, 0.01)] * 100), \
             mock.patch.object(h, "sample_totals", side_effect=fake_totals), \
             mock.patch("builtins.print"):
            try:
                h.verify(args, "openwhisk")
            except SystemExit:
                pass
        self.assertEqual(seen["fn_members"], {"wsk0_3_guest_hello"})
        self.assertTrue(seen["allow"])
        self.assertIsNotNone(seen["window"])
        self.assertLessEqual(seen["window"][0], seen["window"][1])


class TestUntrackedDyn(unittest.TestCase):
    def test_idle_floor_times_window_is_removed(self):
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import untracked_dyn
        res = tempfile.mkdtemp()
        leg = os.path.join(res, "openwhisk_cpubound_lock_t_x")
        os.makedirs(leg)
        json.dump({"stamp": "t_x", "platform": "openwhisk", "leg_gates_ok": True,
                   "usable_runs": ["run_1", "run_2"]}, open(os.path.join(leg, "acceptance.json"), "w"))
        # idle floor 0.7 core outside containers; one run 10 s, one 20 s, same work (1.0 cpu-s)
        runs = [{"host_cpu_sec": 3 + 1 + 0.7 * w + 1.0, "host_window_s": w, "successes": 1000,
                 "cpu_sec": {"control_plane": 3.0, "function": 1.0}} for w in (10.0, 20.0)]
        json.dump(runs, open(os.path.join(leg, "runs.json"), "w"))
        pd = os.path.join(res, "idle_probe_t_x", "openwhisk_quick")
        os.makedirs(pd)
        json.dump({"wall_s": 60.0, "host_cpu_sec": 0.7 * 60 + 0.6, "cpu_sec": {"control_plane": 0.6, "function": 0.0}},
                  open(os.path.join(pd, "summary.json"), "w"))
        (r,) = list(untracked_dyn.rows(res, "t_"))
        self.assertAlmostEqual(r["untracked_dyn_ms_inv"], 1.0, places=3)   # the work, not the wall
        self.assertAlmostEqual(r["untracked_raw_ms_inv"], 11.5, places=3)  # median of 8 and 15
        self.assertAlmostEqual(r["cp_dyn_ms_inv"], 2.85, places=3)         # 3.0 − 0.01 × 15 s / 1000


if __name__ == "__main__":
    unittest.main()
