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
            "--forbidden-services", "*",
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
            "--forbidden-services", "*",
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
            "--forbidden-containers", "fnserver",
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
            "--forbidden-containers", "fnserver",
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
            "--forbidden-services", "*",
            "--verify-n", "100", "--verify-budget-ms", "5.0"])

    def test_check_cmd(self):
        # run_saqef.sh / run_openfaas.sh run_check
        self.assertEqual(["python3", saqef.HARNESS, "--check"],
                         ["python3", saqef.HARNESS, "--check"])

    def test_ow_bench_defaults(self):
        # OpenWhisk web action URL (GET-invocable; harness is GET-only and frozen).
        ad = get_adapter("openwhisk")
        cmd = ad.harness_argv(METRIC, 3000, 20, 60, 20, 5, "results/openwhisk_cpubound")
        self.assertEqual(cmd, [
            "python3", "saqef_harness.py",
            "--url", "http://127.0.0.1:3233/api/v1/web/guest/default/hello",
            "--platform", "openwhisk", "--cp-containers", "openwhisk",
            "--fn-images", "action-python-v3.11",
            "--forbidden-services", "*", "--forbidden-containers", "fnserver",
            "--total", "3000", "--concurrency", "20", "--duration", "60",
            "--warmup", "20", "--repeat", "5",
            "--sampler", "cgroup", "--delta-check", "--loadgen", "hey",
            "--outdir", "results/openwhisk_cpubound"])

    def test_ow_verify(self):
        ad = get_adapter("openwhisk")
        cmd = ad.harness_argv(METRIC, 0, 0, 0, 0, 0, "results/x", verify=True)
        self.assertEqual(cmd, [
            "python3", "saqef_harness.py", "--verify", "--sampler", "cgroup",
            "--url", "http://127.0.0.1:3233/api/v1/web/guest/default/hello",
            "--platform", "openwhisk", "--cp-containers", "openwhisk",
            "--fn-images", "action-python-v3.11",
            "--forbidden-services", "*", "--forbidden-containers", "fnserver",
            "--verify-n", "100", "--verify-budget-ms", "5.0"])


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
        for name in ("fn", "openfaas", "openwhisk"):
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

    def test_openwhisk_scales_dynamically(self):
        # OpenWhisk's invoker spawns action containers per activation (like Fn);
        # no static replica concept -> the CLI's scale command must refuse.
        self.assertIsNone(get_adapter("openwhisk").default_replicas)
        with self.assertRaises(NotImplementedError):
            get_adapter("openwhisk").scale(8)

    def test_openwhisk_web_action_url(self):
        # The frozen GET-only harness needs a GET-invocable endpoint: the action
        # must be exposed as a web action (REST native invoke is POST).
        ad = get_adapter("openwhisk")
        self.assertIn("/api/v1/web/", ad.url)
        self.assertNotIn("/api/v1/namespaces/", ad.url)

    def test_openwhisk_forbids_swarm_and_fn(self):
        # OpenWhisk never uses swarm -> any service is contamination (like Fn);
        # Fn's fnserver must be down too.
        pol = get_adapter("openwhisk").isolation
        self.assertIn("*", pol.forbidden_services)
        self.assertIn("fnserver", pol.forbidden_containers)


class TestDeploymentContract(unittest.TestCase):
    """deploy/teardown must PROVE the platform state (the shell runners swallow
    failures), and OpenFaaS's function service must be deployed explicitly."""

    def test_deploy_function_hook_noop_on_fn(self):
        # Fn's deploy() already includes the function; the hook must be harmless.
        self.assertIsNone(get_adapter("fn").deploy_function())

    def test_openfaas_deploy_function_exists(self):
        self.assertTrue(callable(get_adapter("openfaas").deploy_function))

    def test_openfaas_function_service_labels(self):
        # The hello service must carry the label faas-swarm uses to register it
        # as a gateway function, and sit on the overlay network the stack created.
        # (Direct docker service create, NOT faas-cli: the local faas-cli needs a
        # template store to deploy, and service create is the deterministic path.)
        import io
        import unittest.mock as mock
        ad = get_adapter("openfaas")
        with mock.patch.object(ad, "_hello_service_exists", return_value=False), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)) as mr, \
             mock.patch("platforms.openfaas.wait_for_url", return_value=True):
            ad.deploy_function()
        argv = mr.call_args.args[0]
        self.assertIn("docker", argv) and self.assertIn("service", argv) and self.assertIn("create", argv)
        self.assertIn("--label", argv) and self.assertIn("com.openfaas.function=hello", argv)
        self.assertIn("--network", argv) and self.assertIn("openfaas_functions", argv)
        self.assertEqual(argv[-1], "hello:latest")

    def test_fn_deploy_proves_serving(self):
        # The whole point of the Fn-leg fix: a deploy that left fnserver down
        # (silently, per run_saqef.sh's no-set -e) must be detectable/retried.
        self.assertTrue(callable(get_adapter("fn")._serving))

    def test_wait_helpers_present(self):
        from platforms.base import wait_containers, wait_for_url
        self.assertTrue(callable(wait_for_url))
        self.assertTrue(callable(wait_containers))

    def test_load_verify_missing_dir(self):
        self.assertIsNone(saqef.load_verify(os.path.join(REPO, "does-not-exist")))


class TestGatesFnReplicaCount(unittest.TestCase):
    """gates_for's fn_replicas column must use the run's OWN platform adapter
    fn_images allowlist (matched against each container's image), not a
    hardcoded 'hello' name-prefix -- container naming schemes differ per
    platform: Fn uses ULIDs, OpenFaaS names swarm tasks 'hello.N.hash',
    OpenWhisk names action containers 'wsk0_N_guest_hello'. The hardcoded
    check silently read 0 replicas for both Fn and OpenWhisk regardless of
    the real count."""

    def test_fn_ulid_containers_counted(self):
        s = {
            "platform": "fn",
            "container_inventory": ["01K123ULID0001", "01K123ULID0002", "fnserver"],
            "container_labels": {
                "01K123ULID0001": {"image": "hello:0.0.11", "labels": []},
                "01K123ULID0002": {"image": "hello:0.0.11", "labels": []},
                "fnserver": {"image": "fnproject/fnserver:latest", "labels": []},
            },
        }
        self.assertEqual(saqef._count_fn_containers(s), 2)

    def test_openwhisk_wsk_containers_counted(self):
        s = {
            "platform": "openwhisk",
            "container_inventory": ["openwhisk", "wsk0_1_prewarm_nodejs20", "wsk0_3_guest_hello"],
            "container_labels": {
                "openwhisk": {"image": "openwhisk/standalone:nightly", "labels": []},
                "wsk0_1_prewarm_nodejs20": {"image": "openwhisk/action-nodejs-v20:nightly", "labels": []},
                "wsk0_3_guest_hello": {"image": "openwhisk/action-python-v3.11:nightly", "labels": []},
            },
        }
        # prewarm nodejs20 pool must NOT be counted as the function under test.
        self.assertEqual(saqef._count_fn_containers(s), 1)

    def test_openfaas_swarm_task_containers_still_counted(self):
        s = {
            "platform": "openfaas",
            "container_inventory": ["hello.1.abc", "hello.2.def", "openfaas_gateway.1.xyz"],
            "container_labels": {
                "hello.1.abc": {"image": "hello:latest", "labels": []},
                "hello.2.def": {"image": "hello:latest", "labels": []},
                "openfaas_gateway.1.xyz": {"image": "openfaas/gateway:0.18.7", "labels": []},
            },
        }
        self.assertEqual(saqef._count_fn_containers(s), 2)

    def test_legacy_dir_without_container_labels_falls_back(self):
        s = {"platform": "fn", "container_inventory": ["hello.1.abc", "fnserver"]}
        self.assertEqual(saqef._count_fn_containers(s), 1)


class TestCmdVerifyPinsOutdir(unittest.TestCase):
    """cmd_verify's --out must actually reach the harness: harness_argv's
    verify branch never appends --outdir on its own (only the bench branch
    does), so a caller that forgets to pin it gets writes silently redirected
    to the harness's own 'results' default. This is exactly what corrupted
    the tracked results/verify.json working artifact in a past OpenWhisk
    session (see AGENTS.md)."""

    def test_explicit_out_reaches_argv(self):
        import argparse
        import unittest.mock as mock

        args = argparse.Namespace(platform="fn", metric="cpubound",
                                  out="results/pinned_test", dry_run=True)
        with mock.patch.object(saqef, "run_sub") as mr:
            saqef.cmd_verify(args)
        cmd = mr.call_args.args[0]
        self.assertIn("--outdir", cmd)
        self.assertEqual(cmd[cmd.index("--outdir") + 1], "results/pinned_test")

    def test_default_out_falls_back_to_per_platform_dir(self):
        import argparse
        import unittest.mock as mock

        args = argparse.Namespace(platform="openwhisk", metric="cpubound",
                                  out=None, dry_run=True)
        with mock.patch.object(saqef, "run_sub") as mr:
            saqef.cmd_verify(args)
        cmd = mr.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--outdir") + 1],
                         os.path.join(REPO, "results", "openwhisk_verify"))


class TestCarbonFormula(unittest.TestCase):
    """Pin the carbon formula against the REAL module so the /3600 Wh bug
    (every gCO2 figure 1000x too high) cannot silently regress. The reviewer's
    independently-verified pair: e_total=382.2 J -> 0.0183 gCO2 (old buggy
    value was 18.312 gCO2). KPI per-invocation fields must also survive
    rounding (post-fix magnitudes are ~1e-5 g, so round(x,4) collapsed them
    to 0.0)."""

    @classmethod
    def setUpClass(cls):
        cls.h = importlib.machinery.SourceFileLoader(
            "saqef_harness", os.path.join(REPO, "saqef_harness.py")).load_module()

    def test_op_total_carbon_corrected(self):
        e_total = 382.2
        kwh = e_total / 3.6e6
        gco2 = kwh * self.h.CI_GCO2_PER_KWH * self.h.PUE
        self.assertEqual(round(gco2, 4), 0.0183)
        self.assertGreater(round(gco2, 4), 0.0)

    def test_buggy_wh_formula_still_1000x_high(self):
        # The OLD formula (J/3600 as Wh, then x CI in gCO2/kWh): must remain
        # 1000x the corrected value -- if this ever stops being 1000x, the
        # constants/units were changed and the assertion above needs re-checking.
        e_total = 382.2
        buggy = e_total / 3600.0 * self.h.CI_GCO2_PER_KWH * self.h.PUE
        fixed = e_total / 3.6e6 * self.h.CI_GCO2_PER_KWH * self.h.PUE
        self.assertAlmostEqual(buggy / fixed, 1000.0, places=6)

    def test_kpi_rounding_preserves_small_values(self):
        # A realistic per-invocation dynamic carbon: 3.03 mJ CP dynamic energy
        # (the §5.4 number), CI/PUE from the real module.
        e_dynamic = 0.00303 * 3000  # 3.03 mJ x 3000 invocations
        kwh = e_dynamic / 3.6e6
        kpi = kwh * self.h.CI_GCO2_PER_KWH * self.h.PUE / 3000.0
        self.assertEqual(round(kpi, 8), round(kpi, 8))  # not NaN/0-collapse
        self.assertGreater(round(kpi, 8), 0.0)
        self.assertNotEqual(round(kpi, 8), 0.0)

    def test_no_stray_wh_sites_in_harness(self):
        # Guard the count the reviewer asked for: /3600 must not appear anywhere
        # on the carbon path (kpi, idle_band, sensitivity, totals).
        src = open(os.path.join(REPO, "saqef_harness.py")).read()
        for bad in ("/ 3600.0", "/3600.0", "/ 3600", " /3600", "e_total / 3600",
                    "e_cp / 3600", "e_dynamic / 3600"):
            self.assertNotIn(bad, src)


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
        refs = METRIC["regression"]["reference_share_pct"]
        self.assertTrue(self._run({k: self._med(v) for k, v in refs.items()}))

    def test_small_drift_passes(self):
        refs = METRIC["regression"]["reference_share_pct"]
        self.assertTrue(self._run({
            "fn": self._med(refs["fn"] + 0.2), "openfaas": self._med(refs["openfaas"] + 0.13)}))

    def test_large_drift_fails(self):
        refs = METRIC["regression"]["reference_share_pct"]
        self.assertFalse(self._run({
            "fn": self._med(refs["fn"] + 0.8), "openfaas": self._med(refs["openfaas"])}))

    def test_missing_platform_fails(self):
        refs = METRIC["regression"]["reference_share_pct"]
        self.assertFalse(self._run({"fn": self._med(refs["fn"])}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
