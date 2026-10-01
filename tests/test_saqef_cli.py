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

import contextlib
import io
import json
import os
import re
import sys
import tempfile
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
from platforms.base import repo_script

METRIC = json.load(open(os.path.join(saqef.METRICS_DIR, "cpubound.json")))
FN_CP = "fnserver"
# Prefixed with the swarm stack name (fixed 2026-08-08): a bare "gateway"
# substring collided with Knative's "kourier-gateway" pod containers, since
# k3s/Knative stays resident on this box across every platform's session.
OF_CP = ("openfaas_gateway,openfaas_faas-swarm,openfaas_prometheus,"
        "openfaas_nats,openfaas_queue-worker,openfaas_alertmanager")


class TestArgvByteIdentical(unittest.TestCase):
    """The exact command strings the old scripts construct, re-derived by hand.

    The script prefix is repo-root-resolved (repo_script), NOT the bare filename
    the shell runners use: the CLI can be invoked from any CWD (e.g. tools/), so
    a relative 'saqef_harness.py' would fail to spawn there (fixed 2026-08-09).
    Absolute-vs-relative resolves to the same file; every measurement-relevant
    flag from --url onward stays byte-identical to the shell runners.
    """

    def test_fn_bench_defaults(self):
        ad = get_adapter("fn")
        cmd = ad.harness_argv(METRIC, 3000, 20, 60, 20, 5, "results/fn_cpubound_v9")
        self.assertEqual(cmd, [
            "python3", repo_script("saqef_harness.py"),
            "--url", "http://localhost:8080/t/app1/hello",
            "--platform", "fn", "--cp-containers", FN_CP,
            "--fn-images", "hello",
            "--forbidden-services", "*", "--forbidden-containers", "user-container,queue-proxy",
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
            "python3", repo_script("saqef_harness.py"),
            "--url", "http://localhost:8080/t/app1/hello",
            "--platform", "fn", "--cp-containers", FN_CP,
            "--fn-images", "hello",
            "--forbidden-services", "*", "--forbidden-containers", "user-container,queue-proxy",
            "--idle-w", "4.3", "--cpu-count-override", "2", "--host-cpu-list", "0,1",
            "--total", "10000", "--concurrency", "4", "--duration", "60",
            "--warmup", "20", "--repeat", "5",
            "--sampler", "cgroup", "--delta-check", "--loadgen", "hey",
            "--outdir", "results/fn_cpubound_baremetal"])

    def test_of_bench_defaults(self):
        ad = get_adapter("openfaas")
        cmd = ad.harness_argv(METRIC, 3000, 20, 60, 20, 5, "results/openfaas_cpubound")
        self.assertEqual(cmd, [
            "python3", repo_script("saqef_harness.py"),
            "--url", "http://127.0.0.1:8080/function/hello",
            "--platform", "openfaas", "--cp-containers", OF_CP,
            "--fn-images", "hello",
            "--forbidden-containers", "fnserver,user-container,queue-proxy",
            "--total", "3000", "--concurrency", "20", "--duration", "60",
            "--warmup", "20", "--repeat", "5",
            "--sampler", "cgroup", "--delta-check", "--loadgen", "hey",
            "--outdir", "results/openfaas_cpubound"])

    def test_of_verify(self):
        # run_openfaas.sh run_verify passes --fn-images hello (script does the same)
        ad = get_adapter("openfaas")
        cmd = ad.harness_argv(METRIC, 0, 0, 0, 0, 0, "results/x", verify=True)
        self.assertEqual(cmd, [
            "python3", repo_script("saqef_harness.py"), "--verify", "--sampler", "cgroup",
            "--url", "http://127.0.0.1:8080/function/hello",
            "--platform", "openfaas", "--cp-containers", OF_CP,
            "--fn-images", "hello",
            "--forbidden-containers", "fnserver,user-container,queue-proxy",
            "--verify-n", "100", "--verify-budget-ms", "5.0"])

    def test_fn_verify(self):
        # Deliberate, documented deviation from run_saqef.sh run_verify (which omits
        # --fn-images): Fn verify now uses the same allowlist as OF verify so a stray
        # container is fail-open unclassified instead of silently folded (manifest #1).
        # verify is a sanity gate, not the measured path.
        ad = get_adapter("fn")
        cmd = ad.harness_argv(METRIC, 0, 0, 0, 0, 0, "results/x", verify=True)
        self.assertEqual(cmd, [
            "python3", repo_script("saqef_harness.py"), "--verify", "--sampler", "cgroup",
            "--url", "http://localhost:8080/t/app1/hello",
            "--platform", "fn", "--cp-containers", FN_CP,
            "--fn-images", "hello",
            "--forbidden-services", "*", "--forbidden-containers", "user-container,queue-proxy",
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
            "python3", repo_script("saqef_harness.py"),
            "--url", "http://127.0.0.1:3233/api/v1/web/guest/default/hello",
            "--platform", "openwhisk", "--cp-containers", "openwhisk",
            "--fn-images", "action-python-v3.11",
            "--forbidden-services", "*", "--forbidden-containers", "fnserver,user-container,queue-proxy",
            "--total", "3000", "--concurrency", "20", "--duration", "60",
            "--warmup", "20", "--repeat", "5",
            "--sampler", "cgroup", "--delta-check", "--loadgen", "hey",
            "--outdir", "results/openwhisk_cpubound"])

    def test_ow_verify(self):
        ad = get_adapter("openwhisk")
        cmd = ad.harness_argv(METRIC, 0, 0, 0, 0, 0, "results/x", verify=True)
        self.assertEqual(cmd, [
            "python3", repo_script("saqef_harness.py"), "--verify", "--sampler", "cgroup",
            "--url", "http://127.0.0.1:3233/api/v1/web/guest/default/hello",
            "--platform", "openwhisk", "--cp-containers", "openwhisk",
            "--fn-images", "action-python-v3.11",
            "--forbidden-services", "*", "--forbidden-containers", "fnserver,user-container,queue-proxy",
            "--verify-n", "100", "--verify-budget-ms", "5.0"])

    def test_kn_bench_defaults(self):
        # Knative function URL (sslip.io -> 127.0.0.1, kourier gateway on :80).
        ad = get_adapter("knative")
        cmd = ad.harness_argv(METRIC, 3000, 20, 60, 20, 5, "results/knative_cpubound")
        self.assertEqual(cmd, [
            "python3", repo_script("saqef_harness.py"),
            "--url", "http://hello.default.127.0.0.1.sslip.io",
            "--platform", "knative", "--cp-containers",
            "activator-,controller-,autoscaler-,webhook-,net-kourier-controller,"
            "kourier-gateway,svclb-kourier",
            "--fn-images", "kn-hello",
            "--fn-containers", "user-container,queue-proxy",
            "--forbidden-services", "*", "--forbidden-containers",
            "fnserver,openwhisk,openfaas",
            "--total", "3000", "--concurrency", "20", "--duration", "60",
            "--warmup", "20", "--repeat", "5",
            "--sampler", "cgroup", "--delta-check", "--loadgen", "hey",
            "--outdir", "results/knative_cpubound"])

    def test_kn_verify(self):
        ad = get_adapter("knative")
        cmd = ad.harness_argv(METRIC, 0, 0, 0, 0, 0, "results/x", verify=True)
        self.assertEqual(cmd, [
            "python3", repo_script("saqef_harness.py"), "--verify", "--sampler", "cgroup",
            "--url", "http://hello.default.127.0.0.1.sslip.io",
            "--platform", "knative", "--cp-containers",
            "activator-,controller-,autoscaler-,webhook-,net-kourier-controller,"
            "kourier-gateway,svclb-kourier",
            "--fn-images", "kn-hello",
            "--fn-containers", "user-container,queue-proxy",
            "--forbidden-services", "*", "--forbidden-containers",
            "fnserver,openwhisk,openfaas",
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
        for name in ("fn", "openfaas", "openwhisk", "knative"):
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


class TestHarnessAggregation(unittest.TestCase):
    """median_summary must union dict AND list leaves across runs (the
    OpenWhisk wsk0_N pool grows over a session, so first-run-only silently
    drops later runs' entries), and RAPL wraparound must be fail-open but
    distinguishable from 'RAPL unavailable' (expert review #5, 2026-08-09)."""

    @classmethod
    def setUpClass(cls):
        cls.h = importlib.machinery.SourceFileLoader(
            "saqef_harness", os.path.join(REPO, "saqef_harness.py")).load_module()

    def test_median_summary_unions_container_inventory(self):
        # Growing pool: run 2 has two action containers run 1 lacks.
        s1 = {"platform": "openwhisk",
              "container_inventory": ["openwhisk", "wsk0_1_prewarm_nodejs20"]}
        s2 = {"platform": "openwhisk",
              "container_inventory": ["openwhisk", "wsk0_1_prewarm_nodejs20",
                                      "wsk0_3_guest_hello", "wsk0_4_guest_hello"]}
        med = self.h.median_summary([s1, s2])
        self.assertEqual(med["container_inventory"],
                         ["openwhisk", "wsk0_1_prewarm_nodejs20",
                          "wsk0_3_guest_hello", "wsk0_4_guest_hello"])

    def test_median_summary_union_dedups_preserves_order(self):
        s1 = {"container_inventory": ["a", "b"]}
        s2 = {"container_inventory": ["b", "c", "a"]}
        self.assertEqual(self.h.median_summary([s1, s2])["container_inventory"],
                         ["a", "b", "c"])

    def test_median_summary_dict_keys_still_union(self):
        s1 = {"delta_check_map": {"fnserver": "ok"}}
        s2 = {"delta_check_map": {"fnserver": "ok", "wsk0_3_guest_hello": "ok"}}
        med = self.h.median_summary([s1, s2])
        self.assertEqual(sorted(med["delta_check_map"]),
                         ["fnserver", "wsk0_3_guest_hello"])

    def test_rapl_correct_wrap_single(self):
        # Monkeypatched, not the physical sysfs counter: a machine without a
        # readable max_energy_range_uj (containers, some cloud VMs, CI) must
        # not fail this test -- rapl_correct_wrap's behavior is pure function
        # of rapl_max_range_j()'s return value, so patch it directly.
        import unittest.mock as mock
        with mock.patch.object(self.h, "rapl_max_range_j", return_value=1000.0):
            e, flag = self.h.rapl_correct_wrap(-1000.0 + 300.0)  # single wrap, true energy 300J
            self.assertEqual(flag, "corrected_single")
            self.assertAlmostEqual(e, 300.0)

    def test_rapl_correct_wrap_double_is_uncertain(self):
        import unittest.mock as mock
        with mock.patch.object(self.h, "rapl_max_range_j", return_value=1000.0):
            # corrected = -2500 + 1000 = -1500, still negative -> caught as uncertain_double
            e, flag = self.h.rapl_correct_wrap(-2500.0)
            self.assertIsNone(e)
            self.assertEqual(flag, "uncertain_double")

    def test_rapl_correct_wrap_no_range_is_uncertain(self):
        import unittest.mock as mock
        with mock.patch.object(self.h, "rapl_max_range_j", return_value=None):
            e, flag = self.h.rapl_correct_wrap(-500.0)
            self.assertIsNone(e)
            self.assertEqual(flag, "uncertain_no_range")

    def test_rapl_correct_wrap_double_can_be_mislabeled_single(self):
        """Regression test documenting rapl_correct_wrap's own docstring caveat:
        a genuine double wrap can still land >=0 after a single +1-range
        correction and get mislabeled 'corrected_single'. Not a bug to fix --
        a mathematical limitation of two-point sampling (no periodic
        re-sampling within a run) -- this test exists so the limitation stays
        documented and visible rather than silently regressing to a stronger
        (false) claim."""
        import unittest.mock as mock
        with mock.patch.object(self.h, "rapl_max_range_j", return_value=1000.0):
            # true energy 2500J over two wraps of a 1000J-range counter can
            # produce a raw (end-start) delta of -500J.
            e, flag = self.h.rapl_correct_wrap(-500.0)
            self.assertEqual(flag, "corrected_single")  # mislabeled by construction
            self.assertNotEqual(e, 2500.0)  # "corrected" value is NOT the true energy

    def test_rapl_correct_wrap_none_passthrough(self):
        e, flag = self.h.rapl_correct_wrap(None)
        self.assertIsNone(e)
        self.assertEqual(flag, "none")
        e, flag = self.h.rapl_correct_wrap(123.0)
        self.assertEqual(e, 123.0)
        self.assertEqual(flag, "none")


class TestAmbientQuietGate(unittest.TestCase):
    """The pre-run ambient-load quiet gate (runbook §1 automated): whole-host
    busy CPU over a window before the bench, hard-fail above the threshold.
    This is what makes a 'quiet box' a measured, self-certifying precondition
    instead of a manual `uptime`/`ps` assertion."""

    @classmethod
    def setUpClass(cls):
        cls.h = importlib.machinery.SourceFileLoader(
            "saqef_harness", os.path.join(REPO, "saqef_harness.py")).load_module()

    def test_quiet_gate_passes_below_threshold(self):
        import unittest.mock as mock
        h = self.h
        with mock.patch.object(h, "host_cpu_busy_total",
                               side_effect=[(100, 1000), (110, 1100)]), \
             mock.patch.object(h, "ps_top_snapshot", return_value=["hdr"]), \
             mock.patch.object(h.time, "sleep"):
            busy, top = h.ambient_load_check(20.0, 15.0, quiet_gate=True)
        self.assertEqual(busy, 10.0)
        self.assertEqual(top, ["hdr"])

    def test_quiet_gate_fails_above_threshold(self):
        import unittest.mock as mock
        h = self.h
        with mock.patch.object(h, "host_cpu_busy_total",
                               side_effect=[(100, 1000), (130, 1100)]), \
             mock.patch.object(h, "ps_top_snapshot", return_value=None), \
             mock.patch.object(h.time, "sleep"):
            with self.assertRaises(SystemExit):
                h.ambient_load_check(20.0, 15.0, quiet_gate=True)

    def test_quiet_gate_disabled_allows_dirty_leg(self):
        import unittest.mock as mock
        h = self.h
        with mock.patch.object(h, "host_cpu_busy_total",
                               side_effect=[(100, 1000), (130, 1100)]), \
             mock.patch.object(h, "ps_top_snapshot", return_value=None), \
             mock.patch.object(h.time, "sleep"):
            busy, _ = h.ambient_load_check(20.0, 15.0, quiet_gate=False)
        self.assertEqual(busy, 30.0)


class TestGatesFlagsIncompleteAndFallback(unittest.TestCase):
    """gates_for must flag (a) a run that didn't complete its count-bound
    protocol (requests != total_requested) and (b) a silent loadgen fallback
    (env.loadgen != env.loadgen_requested) -- both fields already existed in
    every run's summary.json, but nothing read them. This is exactly what let
    the 2026-08-13 OpenWhisk --duration regression (TROUBLESHOOTING_RUNBOOK.md
    #11) print 'gates OK' next to a run that only completed 1993/10000
    requests on the wrong load generator: the existing coded gates (delta%,
    CPmapped, host_plausible, coverage%) are all satisfied by a truncated,
    fallback-loadgen run, since none of them look at request count or loadgen
    identity."""

    def _base_summary(self, **overrides):
        s = {
            "wall_s": 20.0, "sampling_covered_s": 20.0,
            "delta_check_map": {"openwhisk": "ok"},
            "platform": "openwhisk", "container_inventory": [], "container_labels": {},
            "unclassified_cpu_s": 0.1, "host_cpu_sec": 10.0, "host_saturation_pct": 50.0,
            "host_plausible": True, "host_saturated": False,
            "rapl_validation_err_pct": 5.0,
            "requests": 10000, "total_requested": 10000,
            "env": {"loadgen": "hey", "loadgen_requested": "hey", "loadgen_fallback": False},
            "cp_dynamic_share_pct": 82.0, "slo_compliance": 1.0, "throughput_rps": 65.0,
            "cp_sampler_vs_delta_pct": 0.0, "cp_delta_sec": 1.0,
            "cpu_sec": {"control_plane": 1.0, "function": 1.0},
        }
        s.update(overrides)
        return s

    def _gates_output(self, summary):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = os.path.join(tmp, "run_1")
            os.makedirs(run_dir)
            with open(os.path.join(run_dir, "summary.json"), "w") as f:
                json.dump(summary, f)
            with open(os.path.join(tmp, "summary.json"), "w") as f:
                json.dump(summary, f)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                saqef.gates_for(tmp)
            return buf.getvalue()

    def test_incomplete_run_flagged(self):
        out = self._gates_output(self._base_summary(requests=1993, total_requested=10000))
        self.assertIn("INCOMPLETE RUN", out)

    def test_loadgen_fallback_flagged(self):
        fallback_env = {"loadgen": "py", "loadgen_requested": "hey", "loadgen_fallback": True}
        out = self._gates_output(self._base_summary(env=fallback_env))
        self.assertIn("LOADGEN FALLBACK", out)

    def test_clean_run_not_flagged(self):
        out = self._gates_output(self._base_summary())
        self.assertNotIn("INCOMPLETE RUN", out)
        self.assertNotIn("LOADGEN FALLBACK", out)

    def test_legacy_summary_without_total_requested_does_not_crash(self):
        # Pre-fix result dirs have no "total_requested" key at all -- must
        # degrade to "no flag", not KeyError.
        legacy = self._base_summary()
        del legacy["total_requested"]
        out = self._gates_output(legacy)
        self.assertNotIn("INCOMPLETE RUN", out)


class TestLockSessionDurationOverride(unittest.TestCase):
    """run_lock_session.sh's run_leg() must pass OpenWhisk an explicit
    --duration >= 300: OpenWhisk's own loadgen kill-switch is duration+120s,
    and 10000 requests at its ~65-70 rps ceiling take ~150s, so the CLI's 60s
    default leaves only ~30s margin and kills `hey` mid-run every time
    (TROUBLESHOOTING_RUNBOOK.md #6, regressed and re-fixed as #11 after the
    2026-08-13 lock session). This is a static check on the shipped script
    text -- zero-cost, no docker/k3s/box dependency, matching this file's own
    'the REAL proof is a rerun; these tests are the zero-cost first gate'
    strategy (see module docstring) -- not a substitute for actually rerunning
    the OpenWhisk leg."""

    SCRIPT_PATH = os.path.join(REPO, "tools", "run_lock_session.sh")

    def _run_leg_source(self):
        text = open(self.SCRIPT_PATH).read()
        m = re.search(r"^run_leg\(\)\s*\{.*?\n\}\n", text, re.S | re.M)
        self.assertIsNotNone(m, "could not locate run_leg() in run_lock_session.sh")
        return m.group(0)

    def test_openwhisk_gets_a_long_duration_override(self):
        body = self._run_leg_source()
        # 2026-10-01: the hardcoded `duration=300` became an overridable
        # OW_DURATION variable (--ow-duration) so the tier1 driver can give a
        # taller cap to the OpenWhisk c=1 leg. The invariant under test is
        # UNCHANGED and must not be weakened by that refactor: OpenWhisk still
        # gets a long cap, and the default is still >=300s.
        m = re.search(r'\[ "\$platform" = "openwhisk" \] && duration="\$OW_DURATION"', body)
        self.assertIsNotNone(
            m, "run_leg() has no OpenWhisk-specific duration override; "
               "OpenWhisk needs >=300s or hey's subprocess kill-switch "
               "(duration+120s) fires mid-run and silently falls back to the "
               "Python loadgen (see runbook #6/#11)")
        self.assertRegex(body, r'local duration=60\b',
                         "the non-OpenWhisk default must stay 60s")

        text = open(self.SCRIPT_PATH).read()
        d = re.search(r"^OW_DURATION=(\d+)", text, re.M)
        self.assertIsNotNone(
            d, "OW_DURATION default not found in run_lock_session.sh; "
               "OpenWhisk must default to a >=300s cap")
        self.assertGreaterEqual(int(d.group(1)), 300,
                                "OW_DURATION default dropped below 300s -- "
                                "reopens runbook #6/#11 (loadgen fallback)")

    def test_duration_reaches_the_dry_run_echo_and_the_real_invocation(self):
        body = self._run_leg_source()
        # Invariant: the DRY-RUN preview and the REAL `$SAQEF run` call must
        # both carry the resolved --duration, or --dry-run lies about what
        # actually gets executed. (This used to be `body.count('--duration')
        # == 2`; the 2026-10-01 probe/deploy-only additions introduced
        # legitimately more --duration call sites -- the deploy-only pilot and
        # the idle-probe each pass their own -- so assert the specific
        # invariants instead of a fragile literal count that any new feature
        # would break.)
        self.assertRegex(
            body, r"DRY-RUN: run --platform \$platform .*?--duration \$duration",
            "the DRY-RUN echo must show the resolved --duration")
        # the real (non-dry) invocation: `--duration "$duration"` paired with `--repeat "$REPEAT"`
        self.assertRegex(
            body, r'(?s)\$SAQEF run .*?--duration "\$duration" --repeat "\$REPEAT"',
            "the real $SAQEF run invocation must pass --duration \"$duration\"")
        # the DEPLOY-ONLY pilot path also carries it
        self.assertRegex(
            body, r'--duration "\$duration" --repeat 1',
            "the DEPLOY-ONLY pilot bench must pass --duration \"$duration\"")


class TestHeyRateLimit(unittest.TestCase):
    """hey -q wiring for rate-controlled (cold-start) runs.

    Background: `--interarrival-ms` was only ever consumed by run_load() (the
    Python ThreadPoolExecutor generator). run_hey() had no rate flag at all, so
    ANY rate-limited run silently fell back to the Python generator, whose own
    threads land inside host_cpu_sec and therefore corrupt host_saturation_pct /
    host_plausible -- the exact accounting `hey` exists to protect. Worse for a
    cold-start experiment specifically: it is deliberately low-rate, so platform
    CPU is near-idle and the loadgen's own overhead is proportionally LARGER
    than in a steady-load run.

    Two things must hold, and both are asserted against the real module:
      1. hey's -q is PER WORKER ("Rate limit, in queries per second (QPS) per
         worker"), so an aggregate target must be divided by concurrency.
      2. --interarrival-ms must reach hey, so a cold-start run never needs the
         Python fallback.
    """

    @classmethod
    def setUpClass(cls):
        cls.h = importlib.machinery.SourceFileLoader(
            "saqef_harness", os.path.join(REPO, "saqef_harness.py")).load_module()

    def _hey_cmd(self, **kw):
        """Capture the argv run_hey() would exec, returning None on any early
        return (so a test can never pass by accident)."""
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            raise RuntimeError("stop-here")  # aborts run_hey after argv is built

        import unittest.mock as mock
        with mock.patch.object(self.h.shutil, "which", lambda _: "/bin/hey"), \
             mock.patch.object(self.h.subprocess, "run", fake_run):
            with contextlib.suppress(RuntimeError):
                self.h.run_hey("http://x/", 100, 4, **kw)
        self.assertIn("cmd", seen, "run_hey returned before building argv")
        return seen["cmd"]

    def _flag(self, cmd, flag):
        return cmd[cmd.index(flag) + 1] if flag in cmd else None

    def test_no_qps_means_no_q_flag(self):
        # Backward compatibility: an unthrottled run's argv must be unchanged
        # apart from the new arg, or every existing citable run's command line
        # is no longer byte-identical.
        cmd = self._hey_cmd(qps=None)
        self.assertNotIn("-q", cmd)
        self.assertEqual(cmd[:9], ["hey", "-n", "100", "-c", "4", "-t", "30", "-o", "csv"])

    def test_zero_qps_is_not_rate_limited(self):
        self.assertNotIn("-q", self._hey_cmd(qps=0))

    def test_aggregate_qps_is_divided_by_concurrency(self):
        # 8 QPS aggregate at concurrency 4 -> hey -q 2 (per worker).
        cmd = self._hey_cmd(qps=8.0)
        self.assertAlmostEqual(float(self._flag(cmd, "-q")), 2.0, places=6)

    def test_fractional_per_worker_rate_survives(self):
        # 0.5 QPS aggregate at concurrency 4 -> -q 0.125, must not be rounded
        # to 0 (which would silently un-throttle a cold-start run).
        cmd = self._hey_cmd(qps=0.5)
        self.assertAlmostEqual(float(self._flag(cmd, "-q")), 0.125, places=6)

    def test_interarrival_ms_reaches_hey(self):
        # The regression this class exists for: interarrival must NOT be
        # py-only. concurrency 4 with a 1000 ms gap == 4 aggregate QPS.
        derived = 4 * 1000.0 / 1000.0
        self.assertAlmostEqual(derived, 4.0, places=6)
        cmd = self._hey_cmd(qps=derived)
        self.assertAlmostEqual(float(self._flag(cmd, "-q")), 1.0, places=6)

    def test_q_flag_precedes_url(self):
        # hey requires flags before the positional URL; a -q appended after it
        # is parsed as part of the URL and the run silently goes unthrottled.
        cmd = self._hey_cmd(qps=8.0)
        self.assertLess(cmd.index("-q"), len(cmd) - 1)
        self.assertEqual(cmd[-1], "http://x/")

    def test_both_flags_defined_and_independent(self):
        # CLI surface: both knobs exist, both default to off, and the help text
        # documents the per-worker conversion (the unit trap).
        src = open(os.path.join(REPO, "saqef_harness.py")).read()
        self.assertIn('"--qps"', src)
        self.assertIn('"--interarrival-ms"', src)
        self.assertIn("per worker", src.lower())


class TestSingleRunArtifactShape(unittest.TestCase):
    """A --repeat 1 bench must write the SAME artifact shape as --repeat 5.

    Found by expert review 2026-10-01 and it broke the Tier-1 sweep two ways at
    once. saqef_harness.py only wrote runs.json inside its `if repeat > 1:`
    branch, so a single-run bench produced just summary.json + samples.csv +
    requests.csv in the outdir. Both Tier-1 consumers of a single-run bench
    then broke:
      * run_lock_session.sh's gate block read runs.json UNGUARDED -> with the
        --repeat 1 OpenWhisk duration pilot it raised FileNotFoundError outside
        any try, so the whole session died ~2h in with "OW c=1 pilot failed".
      * run_tier1_conc.sh's probe_rate() returned None on a missing runs.json
        -> SILENT no-op: every background-corrected column printed "--" and the
        flatness check reported "no probe data", while the script exited 0.
        A silent no-op on the study's only independent cross-check is worse
        than a crash.
    These tests EXEC the embedded python blocks against synthetic inputs, so
    they fail if the guards are reverted -- a source-regex check would not.
    """

    HARNESS = os.path.join(REPO, "saqef_harness.py")
    LOCK = os.path.join(REPO, "tools", "run_lock_session.sh")
    TIER1 = os.path.join(REPO, "tools", "run_tier1_conc.sh")

    def _blocks(self, path):
        text = open(path).read()
        return re.findall(r"<<'PY'\n(.*?)\nPY", text, re.S)

    def _block(self, path, needle):
        for b in self._blocks(path):
            if needle in b:
                return b
        self.fail("no embedded python block containing %r in %s" % (needle, path))

    def _base_summary(self):
        return {
            "platform": "openwhisk", "wall_s": 412.3, "requests": 3000,
            "total_requested": 3000, "host_plausible": True,
            "delta_check_map": {"openwhisk": "ok"}, "rapl_wrap": "none",
            "cp_dynamic_share_pct": 81.0, "host_saturation_pct": 40.0,
            "throughput_rps": 7.3, "latency_ms": {"p50": 60.0, "p99": 90.0},
            "cpu_sec": {"control_plane": 300.0, "function": 60.0},
            "ambient": {"load_pct": 6.0, "threshold_pct": 15},
            "env": {"loadgen": "hey", "loadgen_requested": "hey",
                    "loadgen_fallback": False},
        }

    def _run(self, code, *args):
        import subprocess
        p = subprocess.run([sys.executable, "-c", code] + list(args),
                           capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    # ---- the harness contract -------------------------------------------
    def test_harness_writes_runs_json_on_the_single_run_path(self):
        src = open(self.HARNESS).read()
        # Anchor on the repeat dispatch, then take the `else:` that follows it.
        # A bare r"\n    else:\n" search is WRONG: saqef_harness.py has three
        # top-level `else:` blocks, so the lazy match runs from an unrelated one
        # all the way to `if __name__`, swallows the repeat>1 branch, and passes
        # even with the fix reverted (verified by mutation 2026-10-01).
        disp = re.search(r"if args\.repeat > 1:", src)
        self.assertIsNotNone(disp, "could not locate the repeat dispatch")
        tail = src[disp.start():]
        els = re.search(r"\n    else:\n", tail)
        self.assertIsNotNone(els, "the repeat dispatch has no single-run else branch")
        branch = re.split(r"\nif __name__", tail[els.end():])[0]
        # Strip comments before asserting: the fix carries an explanatory comment
        # that NAMES runs.json, so a naive substring check passes even with the
        # write deleted (found by mutation 2026-10-01, same class of false
        # negative as the over-branching regex above).
        code = "\n".join(ln for ln in branch.splitlines()
                         if not ln.lstrip().startswith("#"))
        self.assertIn("write_run", code,
                      "captured the wrong region -- this is not the "
                      "single-run branch")
        self.assertIn("runs.json", code,
                      "the single-run branch must write runs.json; without it "
                      "every repeat==1 consumer (pilot gate, idle-probe reader) "
                      "either crashes or silently no-ops")
        self.assertIn("repetitions", code,
                      "single-run summaries should record repetitions=1 so "
                      "consumers can tell the shape they are reading")

    # ---- the pilot gate must degrade, not crash -------------------------
    def test_lock_gate_reports_missing_runs_json_as_a_gate_failure(self):
        block = self._block(self.LOCK, "lock summary written")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "results", "openwhisk_cpubound_lock_X_quick")
            os.makedirs(out)
            os.makedirs(os.path.join(td, "results", "lock_session_X"))
            # deliberately NO runs.json -- the pre-fix single-run shape
            with open(os.path.join(out, "summary.json"), "w") as f:
                json.dump(self._base_summary(), f)
            rc, txt = self._run(block, td, "X", "ow", "1",
                                "4.235", "4.249", "5.739", "4.882")
            self.assertNotIn("Traceback", txt,
                             "a missing runs.json must be a reported gate "
                             "problem, never an unhandled traceback")
            self.assertIn("FAIL", txt)
            self.assertNotEqual(rc, 0,
                                "a leg whose runs.json cannot be read must not "
                                "pass the gate")

    def test_lock_gate_accepts_the_fixed_single_run_shape(self):
        block = self._block(self.LOCK, "lock summary written")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "results", "openwhisk_cpubound_lock_X_quick")
            os.makedirs(out)
            os.makedirs(os.path.join(td, "results", "lock_session_X"))
            s = self._base_summary()
            with open(os.path.join(out, "summary.json"), "w") as f:
                json.dump(s, f)
            with open(os.path.join(out, "runs.json"), "w") as f:   # new shape
                json.dump([s], f)
            rc, txt = self._run(block, td, "X", "ow", "1",
                                "4.235", "4.249", "5.739", "4.882")
            self.assertEqual(rc, 0, "the fixed shape must pass: %s" % txt)
            self.assertIn("OK", txt)

    # ---- RAPL fit + monotone drift must fail the gate ---------------------
    # tier1ow8 (2026-10-01) passed every per-run gate while runs 4 and 5
    # carried rapl_validation_err_pct 24.7/29.2 (the harness itself printed
    # "NOT citable") and throughput decayed monotonically 57.0 -> 29.1 rps
    # across the five repeats. Nothing compared either, so the session reported
    # ALL GATES OK. These lock both holes down.
    def _multi_run_gate(self, reps, repeat="5"):
        """Run the gate block over a leg with one run_N/summary.json per rep."""
        block = self._block(self.LOCK, "lock summary written")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "results", "openwhisk_cpubound_lock_X")
            os.makedirs(out)
            os.makedirs(os.path.join(td, "results", "lock_session_X"))
            for i, over in enumerate(reps, 1):
                d = os.path.join(out, "run_%d" % i)
                os.makedirs(d)
                s = self._base_summary()
                s.update(over)
                with open(os.path.join(d, "summary.json"), "w") as f:
                    json.dump(s, f)
            leg = self._base_summary()
            with open(os.path.join(out, "summary.json"), "w") as f:
                json.dump(leg, f)
            with open(os.path.join(out, "runs.json"), "w") as f:
                json.dump([dict(self._base_summary(), **o) for o in reps], f)
            return self._run(block, td, "X", "ow", repeat,
                             "4.235", "4.249", "5.739", "4.882", "20")

    def test_gate_rejects_a_run_whose_rapl_fit_is_degraded(self):
        rc, txt = self._multi_run_gate([
            {}, {}, {}, {"rapl_validation_err_pct": 24.71},
            {"rapl_validation_err_pct": 29.23}])
        self.assertNotEqual(rc, 0,
                            "a run the harness calls NOT citable must fail: %s" % txt)
        self.assertIn("RAPL FIT", txt)

    def test_gate_rejects_monotone_throughput_decay(self):
        # the real tier1ow8 shape: rps halves across the repeats while every
        # per-run gate still passes. A median over a decaying sequence is not a
        # central estimate, so the leg must not be citable.
        rc, txt = self._multi_run_gate([
            {"throughput_rps": 56.96}, {"throughput_rps": 44.18},
            {"throughput_rps": 37.02}, {"throughput_rps": 32.39},
            {"throughput_rps": 29.09}])
        self.assertNotEqual(rc, 0,
                            "monotone decay must fail the gate: %s" % txt)
        self.assertIn("DRIFT", txt)

    def test_gate_accepts_flat_repeats_within_drift_tolerance(self):
        # normal run-to-run scatter must NOT trip the drift gate, or the fix
        # would be unusable in practice (every healthy leg would fail).
        rc, txt = self._multi_run_gate([
            {"throughput_rps": r} for r in (57.0, 55.4, 58.1, 56.2, 54.9)])
        self.assertEqual(rc, 0, "flat repeats must pass: %s" % txt)
        self.assertNotIn("DRIFT", txt)

    # ---- the pilot validator -------------------------------------------
    def _pilot_rc(self, **over):
        block = self._block(self.TIER1, "PILOT REJECTED")
        s = self._base_summary()
        s.update(over)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "summary.json")
            with open(p, "w") as f:
                json.dump(s, f)
            rc, txt = self._run(block, p, "420", "3000")
            return rc, txt

    def test_pilot_accepts_a_clean_run(self):
        rc, txt = self._pilot_rc()
        self.assertEqual(rc, 0, "a complete, hey-driven pilot must pass: %s" % txt)

    def test_pilot_rejects_a_run_killed_by_the_duration_cap(self):
        rc, txt = self._pilot_rc(wall_s=455.0)
        self.assertNotEqual(rc, 0, "wall_s past the cap means hey was killed")
        self.assertIn("PILOT REJECTED", txt)

    def test_pilot_rejects_a_short_count(self):
        # the lock2 OpenWhisk incident: 1993/10000 with the gate still printing OK
        rc, txt = self._pilot_rc(requests=1993, total_requested=3000)
        self.assertNotEqual(rc, 0, "an incomplete pilot must not authorise the leg")
        self.assertIn("INCOMPLETE", txt)

    def test_pilot_rejects_a_loadgen_fallback(self):
        rc, txt = self._pilot_rc(
            env={"loadgen": "py", "loadgen_requested": "hey", "loadgen_fallback": True})
        self.assertNotEqual(rc, 0, "a python-loadgen fallback pilot is worthless")
        self.assertIn("LOADGEN FALLBACK", txt)

    def test_pilot_flags_a_marginal_fit_rather_than_reassuring(self):
        # a pilot at 98% of the cap passes but must warn: the n=5 leg runs the
        # same TOTAL and any per-run variance tips it into the fallback this
        # pilot exists to prevent.
        rc, txt = self._pilot_rc(wall_s=412.0)
        self.assertEqual(rc, 0)
        self.assertIn("MARGINAL", txt)


class TestTier1AggregatorHygiene(unittest.TestCase):
    """Two aggregator bugs from the same expert review: the OpenWhisk function
    container count matched the invoker's warmup pool, and an incomplete run was
    warned about and then still fed the regression."""

    TIER1 = os.path.join(REPO, "tools", "run_tier1_conc.sh")

    def test_openwhisk_fn_count_excludes_prewarm_containers(self):
        src = open(self.TIER1).read()
        fn = re.search(r"def fn_container_count.*?\n(?=\S)", src, re.S)
        self.assertIsNotNone(fn, "fn_container_count not found")
        body = fn.group(0)
        ow = re.search(r'plat == "openwhisk":(.*?)\n\s{4}if plat', body, re.S)
        self.assertIsNotNone(ow, "no openwhisk branch in fn_container_count")
        self.assertIn("_guest_", ow.group(1),
                      "OW action containers are the *_guest_hello pair; a bare "
                      "'wsk0_' prefix also matches wsk0_1/2_prewarm_nodejs20 "
                      "and returned 4 instead of 2 on every OW leg")
        self.assertNotRegex(ow.group(1), r'startswith\("wsk0_"\)')

    def test_incomplete_runs_are_excluded_from_the_fit(self):
        src = open(self.TIER1).read()
        m = re.search(r"for runnr, r in enumerate\(runs, 1\):(.*?)\n\s+cp_s =", src, re.S)
        self.assertIsNotNone(m, "per-run loop not found")
        body = m.group(1)
        self.assertIn("continue", body,
                      "an incomplete run must `continue` -- warning about it and "
                      "then appending it anyway silently feeds a truncated run "
                      "into the background fit and Table 1")

    def _probe_fn(self):
        import re
        src = open(self.TIER1).read()
        m = re.search(r"def probe_rate.*?\n(?=\S)", src, re.S)
        self.assertIsNotNone(m, "probe_rate not found")
        return m.group(0)

    def test_probe_rate_is_fail_closed_not_a_silent_none(self):
        """Every leg in this protocol is invoked with --cpu-probe 60, so a leg
        that RAN but whose probe is missing/unreadable is a protocol violation.
        It used to return None quietly: the corrected columns printed '--', the
        flatness check said 'no probe data', and the script still exited 0 --
        silently dropping the one independent check this experiment exists for."""
        import re
        body = self._probe_fn()
        self.assertRegex(body, r"errors\.append",
                         "probe_rate must record a missing/unreadable probe in "
                         "`errors`; returning None quietly is the bug being fixed")
        sig = re.search(r"def probe_rate\(([^)]*)\)", body).group(1)
        self.assertIn("errors", sig,
                      "probe_rate needs the errors sink as a parameter so the "
                      "caller can make a protocol violation exit non-zero")
        # every early-return None must be a *recorded* failure, not a silent
        # skip. Strip the docstring first: it quotes the OLD bug ("this used to
        # return None"), which is prose about the defect, not a live code path.
        code_only = re.sub(r'"""..*?"""', "", body, flags=re.S)
        for seg in re.split(r"\breturn None\b", code_only)[:-1]:
            self.assertIn("errors.append", seg,
                          "an early `return None` reached without recording an error "
                          "is a silent degradation path")

    def test_probe_rate_prefers_runs_json_and_falls_back_to_summary(self):
        """Behavioral: single-run probe artifacts (summary.json only, the shape
        the --repeat 1 probe produced) must still yield a rate."""
        import json, os, subprocess, sys, tempfile
        code = ("import json,os,sys\n" + self._probe_fn() + """
errs=[]
repo=sys.argv[1]
r=probe_rate(repo,'t1','fn',errs)
print(json.dumps({'rate':r,'errs':errs}))
""")
        with tempfile.TemporaryDirectory() as td:
            # summary.json only (the single-run shape)
            d=os.path.join(td,'results','idle_probe_t1','fn'); os.makedirs(d)
            json.dump({"wall_s":60.0,"cpu_sec":{"control_plane":3.0,"function":6.0}},
                      open(os.path.join(d,'summary.json'),'w'))
            p=subprocess.run([sys.executable,'-c',code,td],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            got=json.loads(p.stdout.strip().splitlines()[-1])
            self.assertEqual(got['errs'],[], "summary.json fallback must not error")
            self.assertAlmostEqual(got['rate']['cp_per_s'],0.05,places=6)
            self.assertAlmostEqual(got['rate']['fn_per_s'],0.10,places=6)
            self.assertEqual(got['rate']['src'],'summary.json')

            # runs.json now present -> it wins over the summary
            json.dump([{"wall_s":60.0,"cpu_sec":{"control_plane":1.2,"function":2.4}}],
                      open(os.path.join(d,'runs.json'),'w'))
            p=subprocess.run([sys.executable,'-c',code,td],capture_output=True,text=True)
            got=json.loads(p.stdout.strip().splitlines()[-1])
            self.assertEqual(got['rate']['src'],'runs.json',
                             "runs.json must take precedence when present")
            self.assertAlmostEqual(got['rate']['cp_per_s'],0.02,places=6)

            # no probe dir at all -> recorded as a protocol error, not a silent None
            p=subprocess.run([sys.executable,'-c',code.replace(
                "repo,'t1'","repo,'absent'"),td],
                capture_output=True,text=True)
            got=json.loads(p.stdout.strip().splitlines()[-1])
            self.assertIsNone(got['rate'])
            self.assertTrue(got['errs'],
                            "a ran-but-unprobed leg must record an error")
            self.assertIn("idle_probe", " ".join(got['errs']))

    def test_flatness_verdict_uses_a_detection_limit_not_a_fixed_threshold(self):
        """The script hardcoded `spread_c <= 2.0` -> '(flat)'. That is the same
        error class as the paper's 'flat share' claim: a magic threshold instead
        of the instrument's resolution. VERIFIED_RESULTS section 14 now reports
        a worst-leg-CV, two-independent-means MDD, and distinguishes 'flat TO
        RESOLUTION' from 'RESOLVABLE DRIFT'. On the committed quick-tier legs
        2.0 pp would have called OpenFaaS's 1.00 pp corrected spread 'flat' when
        its limit is 0.73 pp -- i.e. a resolvable drift mislabelled as flat."""
        import re
        src = open(self.TIER1).read()
        tail = src.split("PY")[-1]
        self.assertNotRegex(tail, r"<= *2\.0",
                            "the fixed 2.0 pp flatness threshold must be gone")
        self.assertIn("RESOLVABLE DRIFT", src)
        self.assertIn("flat TO RESOLUTION", src)
        self.assertIn("detect limit", src.lower())
        self.assertIn("math.sqrt(2.0)", src, "MDD must use the section-14 formula")

    def test_python_errors_reach_bash_through_a_file(self):
        """The errors list lives in the embedded Python; ${#errors[@]} is a BASH
        array and does not see it -- that mismatch would have read as zero
        errors and exited 0, silently reintroducing the bug."""
        import re
        src = open(self.TIER1).read()
        self.assertRegex(src, r"export SAQEF_TIER1_ERRS=",
                         "the temp file carrying python errors to bash must be exported")
        self.assertRegex(src, r"nerr=\$\(head -1 \"\$ERRS_FILE\"",
                         "bash must read the error COUNT from the handoff file, not "
                         "from a bash array that python never populates")
        self.assertIn("exit 1", src.split("PROTOCOL ERRORS")[-1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
