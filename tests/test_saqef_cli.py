#!/usr/bin/env python3
"""Prove the refactor does not change the measurement, WITHOUT touching the box.

Strategy: the new `saqef` CLI builds the saqef_harness.py argv the same way the
proven shell runners (run_saqef.sh / run_openfaas.sh) do. This test asserts the
generated argv is byte-identical to the hand-derived expectations from those
scripts, plus the _quick guard, the adapter schema (do-not-regress manifest),
and the regression verdict math. The REAL proof is `saqef regression` (a rerun);
these tests are the zero-cost first gate.

Run: python3 tests/test_saqef_cli.py
"""

import json
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import importlib.machinery
import importlib.util

_loader = importlib.machinery.SourceFileLoader("saqef", os.path.join(REPO, "saqef"))
_spec = importlib.util.spec_from_loader("saqef", _loader)
saqef = importlib.util.module_from_spec(_spec)
_loader.exec_module(saqef)

from platforms import get_adapter

METRIC = json.load(open(os.path.join(saqef.METRICS_DIR, "cpubound.json")))
FN_CP = "fnserver"
OF_CP = "gateway,faas-swarm,prometheus,nats,queue-worker,alertmanager"


class TestArgvByteIdentical(unittest.TestCase):
    """The exact command strings the old scripts construct, re-derived by hand."""

    def test_fn_bench_defaults(self):
        ad = get_adapter("fn")
        cmd = ad.harness_argv(METRIC, 3000, 20, 60, 20, 5, "results/fn_cpubound_v9")
        self.assertEqual(cmd, [
            "python3", "saqef_harness.py",
            "--url", "http://localhost:8080/t/app1/hello",
            "--platform", "fn", "--cp-containers", FN_CP,
            "--fn-images", "hello",
            "--total", "3000", "--concurrency", "20", "--duration", "60",
            "--warmup", "20", "--repeat", "5",
            "--sampler", "cgroup", "--delta-check", "--loadgen", "hey",
            "--outdir", "results/fn_cpubound_v9"])

    def test_fn_bench_idle_cpu_host(self):
        # run_saqef.sh with SAQEF_IDLE_W / SAQEF_CPU_COUNT_OVERRIDE / SAQEF_HOST_CPU_LIST
        ad = get_adapter("fn")
        cmd = ad.harness_argv(METRIC, 10000, 4, 60, 20, 5, "results/fn_cpubound_baremetal",
                              idle_w=4.3, cpu_count_override=2, host_cpu_list="0,1")
        self.assertEqual(cmd, [
            "python3", "saqef_harness.py",
            "--url", "http://localhost:8080/t/app1/hello",
            "--platform", "fn", "--cp-containers", FN_CP,
            "--fn-images", "hello",
            "--idle-w", "4.3", "--cpu-count-override", "2", "--host-cpu-list", "0,1",
            "--total", "10000", "--concurrency", "4", "--duration", "60",
            "--warmup", "20", "--repeat", "5",
            "--sampler", "cgroup", "--delta-check", "--loadgen", "hey",
            "--outdir", "results/fn_cpubound_baremetal"])

    def test_of_bench_defaults(self):
        ad = get_adapter("openfaas")
        cmd = ad.harness_argv(METRIC, 3000, 20, 60, 20, 5, "results/openfaas_cpubound")
        self.assertEqual(cmd, [
            "python3", "saqef_harness.py",
            "--url", "http://127.0.0.1:8080/function/hello",
            "--platform", "openfaas", "--cp-containers", OF_CP,
            "--fn-images", "hello",
            "--total", "3000", "--concurrency", "20", "--duration", "60",
            "--warmup", "20", "--repeat", "5",
            "--sampler", "cgroup", "--delta-check", "--loadgen", "hey",
            "--outdir", "results/openfaas_cpubound"])

    def test_of_verify(self):
        # run_openfaas.sh run_verify passes --fn-images hello (script does the same)
        ad = get_adapter("openfaas")
        cmd = ad.harness_argv(METRIC, 0, 0, 0, 0, 0, "results/x", verify=True)
        self.assertEqual(cmd, [
            "python3", "saqef_harness.py", "--verify", "--sampler", "cgroup",
            "--url", "http://127.0.0.1:8080/function/hello",
            "--platform", "openfaas", "--cp-containers", OF_CP,
            "--fn-images", "hello",
            "--verify-n", "100", "--verify-budget-ms", "5.0"])

    def test_fn_verify(self):
        # Deliberate, documented deviation from run_saqef.sh run_verify (which omits
        # --fn-images): Fn verify now uses the same allowlist as OF verify so a stray
        # container is fail-open unclassified instead of silently folded (manifest #1).
        # verify is a sanity gate, not the measured path.
        ad = get_adapter("fn")
        cmd = ad.harness_argv(METRIC, 0, 0, 0, 0, 0, "results/x", verify=True)
        self.assertEqual(cmd, [
            "python3", "saqef_harness.py", "--verify", "--sampler", "cgroup",
            "--url", "http://localhost:8080/t/app1/hello",
            "--platform", "fn", "--cp-containers", FN_CP,
            "--fn-images", "hello",
            "--verify-n", "100", "--verify-budget-ms", "5.0"])

    def test_check_cmd(self):
        # run_saqef.sh / run_openfaas.sh run_check
        self.assertEqual(["python3", saqef.HARNESS, "--check"],
                         ["python3", saqef.HARNESS, "--check"])


class TestQuickGuard(unittest.TestCase):
    """SAQEF_REPEAT < 5 must write to *_quick (never the published outdir)."""

    def test_quick_suffix(self):
        self.assertEqual(saqef.resolve_outdir("results/fn", 1), "results/fn_quick")
        self.assertEqual(saqef.resolve_outdir("results/fn", 4), "results/fn_quick")
        self.assertEqual(saqef.resolve_outdir("results/fn", 5), "results/fn")
        self.assertEqual(saqef.resolve_outdir("results/fn", 3), "results/fn_quick")


class TestAdapterSchema(unittest.TestCase):
    """The 'do not regress' manifest encoded as mandatory adapter fields."""

    def test_all_adapters_complete(self):
        for name in ("fn", "openfaas"):
            ad = get_adapter(name)
            self.assertTrue(ad.name and ad.label and ad.url)
            self.assertTrue(ad.cp_containers)          # cp classifiers present
            self.assertTrue(ad.fn_images)              # manifest #1: allowlist REQUIRED
            self.assertIsNotNone(ad.isolation)         # manifest #1/#2: policy REQUIRED
            self.assertTrue(ad.delta_check)            # manifest #6: delta-check on

    def test_empty_allowlist_rejected(self):
        from platforms.base import Adapter, IsolationPolicy

        class Bad(Adapter):
            name = "bad"
            label = "Bad"
            url = "http://x"
            cp_containers = ("cp",)
            fn_images = ()
            isolation = IsolationPolicy(forbidden_containers=("fnserver",))

        with self.assertRaises(TypeError):
            Bad()

    def test_empty_policy_rejected(self):
        from platforms.base import IsolationPolicy

        with self.assertRaises(ValueError):
            IsolationPolicy()

    def test_openfaas_scales_statically(self):
        # manifest #3: GIL concurrency parity -> static replicas, never single-replica
        self.assertEqual(get_adapter("openfaas").default_replicas, 16)
        self.assertIsNone(get_adapter("fn").default_replicas)


class TestRegressionVerdict(unittest.TestCase):
    """The regression gate math (median deviation vs known-good references)."""

    def _med(self, share):
        return {"cp_dynamic_share_pct": share}

    def _run(self, seen):
        rg = METRIC["regression"]
        refs = rg["reference_share_pct"]
        tol = rg["tolerance_pp"]
        all_pass = True
        for platform, ref in refs.items():
            med = seen.get(platform)
            if med is None or med.get("cp_dynamic_share_pct") is None:
                return False
            all_pass = all_pass and abs(med["cp_dynamic_share_pct"] - ref) <= tol
        return all_pass

    def test_known_good_passes(self):
        self.assertTrue(self._run({"fn": self._med(10.46), "openfaas": self._med(7.67)}))

    def test_small_drift_passes(self):
        self.assertTrue(self._run({"fn": self._med(10.2), "openfaas": self._med(7.9)}))

    def test_large_drift_fails(self):
        self.assertFalse(self._run({"fn": self._med(11.2), "openfaas": self._med(7.67)}))

    def test_missing_platform_fails(self):
        self.assertFalse(self._run({"fn": self._med(10.46)}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
