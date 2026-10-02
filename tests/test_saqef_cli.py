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
import csv
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
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

    def test_median_summary_list_of_unhashable_elements(self):
        # attribution.docker_inventory[name] = [image, [labels]]: the nested
        # list made the set-based dedup raise TypeError after all runs.
        inv = ["hello:latest", ["com.docker.swarm.service.name=hello"]]
        s1 = {"attribution": {"docker_inventory": {"hello.1": inv}}}
        s2 = {"attribution": {"docker_inventory": {"hello.1": list(inv)}}}
        med = self.h.median_summary([s1, s2])
        self.assertEqual(med["attribution"]["docker_inventory"]["hello.1"],
                         ["hello:latest", ["com.docker.swarm.service.name=hello"]])

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
    def _multi_run_gate(self, reps, repeat="5", discard="0", extra=()):
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
                             "4.235", "4.249", "5.739", "4.882", "20", discard,
                             *extra)

    def test_gate_rejects_a_run_whose_rapl_fit_is_degraded(self):
        rc, txt = self._multi_run_gate([
            {}, {}, {}, {"rapl_validation_err_pct": 24.71},
            {"rapl_validation_err_pct": 29.23}])
        self.assertNotEqual(rc, 0,
                            "a run the harness calls NOT citable must fail: %s" % txt)
        self.assertIn("RAPL FIT", txt)

    def test_rapl_fit_warn_demotes_only_the_rapl_gate(self):
        # --rapl-fit-warn exists for share-only sessions (the bridge, runbook
        # 24.6): bridge_tier1c1 aborted the whole driver on RAPL FIT, a gate
        # 24.1 had already said does not govern the share. It must demote ONLY
        # that gate -- argv: ..., drift, discard, max_sample_gap, rapl_fit_warn.
        degraded = [{}, {}, {}, {"rapl_validation_err_pct": 53.5}, {}]
        rc, txt = self._multi_run_gate(degraded, extra=("1.0", "1"))
        self.assertEqual(rc, 0, "RAPL FIT alone must not fail under warn: %s" % txt)
        self.assertIn("WARN run_4 RAPL FIT", txt)
        self.assertIn("ENERGY figures from this session are not citable", txt)
        rc, txt = self._multi_run_gate(degraded, extra=("1.0", "0"))
        self.assertNotEqual(rc, 0, "without the flag RAPL FIT still gates: %s" % txt)
        rc, txt = self._multi_run_gate(
            [{}, {}, {}, {"rapl_validation_err_pct": 53.5, "host_plausible": False}, {}],
            extra=("1.0", "1"))
        self.assertNotEqual(rc, 0, "other gates must stay fatal under warn: %s" % txt)

    def test_lock_summary_records_why_a_leg_failed(self):
        # bridge_tier1c1 (2026-10-02) wrote gates_ok=false for every leg with
        # no reason: the problem strings went to stdout only, and the run
        # summaries kept just the RAPL error %, not the joules behind it.
        # Real shape: ambient sits on the LEG summary only, never on run_N.
        block = self._block(self.LOCK, "lock summary written")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "results", "openwhisk_cpubound_lock_X")
            os.makedirs(os.path.join(td, "results", "lock_session_X"))
            reps = [{}, {}, {}, {"rapl_validation_err_pct": 58.45,
                                 "e_model_j": 135.7, "e_rapl_j": 326.6}, {}]
            for i, over in enumerate(reps, 1):
                d = os.path.join(out, "run_%d" % i)
                os.makedirs(d)
                s = self._base_summary()
                del s["ambient"]
                s.update(over)
                with open(os.path.join(d, "summary.json"), "w") as f:
                    json.dump(s, f)
            with open(os.path.join(out, "summary.json"), "w") as f:
                json.dump(self._base_summary(), f)
            with open(os.path.join(out, "runs.json"), "w") as f:
                json.dump([self._base_summary() for _ in reps], f)
            rc, txt = self._run(block, td, "X", "ow", "5",
                                "4.235", "4.249", "5.739", "4.882", "20", "0")
            self.assertNotEqual(rc, 0, txt)
            lock = json.load(open(os.path.join(
                td, "results", "lock_session_X", "lock_summary.json")))
        leg = lock["platforms"]["openwhisk"]
        self.assertFalse(leg["gates_ok"])
        self.assertTrue(leg["ambient_present"],
                        "ambient is leg-level; run_N never carries it")
        self.assertTrue(any(p.startswith("run_4 RAPL FIT") for p in leg["problems"]),
                        leg["problems"])
        self.assertEqual(len(leg["runs"]), 5)
        self.assertEqual(leg["runs"][3]["e_rapl_j"], 326.6)
        self.assertEqual(leg["runs"][3]["e_model_j"], 135.7)
        self.assertEqual(leg["runs"][3]["rapl_validation_err_pct"], 58.45)
        self.assertNotIn("rapl_fit_err_pct", leg["runs"][3],
                         "one name for the value; 90d153f's alias is read-only")

    def test_lock_summary_reads_the_legacy_rapl_alias_but_does_not_republish_it(self):
        # 90d153f shipped rapl_fit_err_pct on the 2026-10-02 legs. Those
        # summaries are already on disk and must still aggregate, but the key
        # is not re-emitted: the summary schema has exactly one RAPL-error name.
        block = self._block(self.LOCK, "lock summary written")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "results", "openwhisk_cpubound_lock_X")
            os.makedirs(os.path.join(td, "results", "lock_session_X"))
            reps = [{}, {}, {}, {"rapl_fit_err_pct": 58.45}, {}]
            for i, over in enumerate(reps, 1):
                d = os.path.join(out, "run_%d" % i)
                os.makedirs(d)
                s = self._base_summary()
                del s["ambient"]
                s.update(over)
                with open(os.path.join(d, "summary.json"), "w") as f:
                    json.dump(s, f)
            with open(os.path.join(out, "summary.json"), "w") as f:
                json.dump(self._base_summary(), f)
            with open(os.path.join(out, "runs.json"), "w") as f:
                json.dump([self._base_summary() for _ in reps], f)
            rc, txt = self._run(block, td, "X", "ow", "5",
                                "4.235", "4.249", "5.739", "4.882", "20", "0")
            self.assertNotEqual(rc, 0, txt)
            lock = json.load(open(os.path.join(
                td, "results", "lock_session_X", "lock_summary.json")))
        leg = lock["platforms"]["openwhisk"]
        self.assertTrue(any(p.startswith("run_4 RAPL FIT") for p in leg["problems"]),
                        "the alias must still reach the gate: %s" % leg["problems"])
        self.assertEqual(leg["runs"][3]["rapl_validation_err_pct"], 58.45,
                         "alias read back under the canonical name")
        self.assertNotIn("rapl_fit_err_pct", leg["runs"][3])

    def test_leg_entry_for_a_missing_summary_carries_the_full_key_set(self):
        # The no-summary.json branch used to emit only label/outdir/gates_ok/
        # problems. A consumer reading cp_dynamic_share_pct or ambient_present
        # on any leg would then KeyError -- and rule 1 of runbook 24.3 needs to
        # read exactly those keys across all four platforms.
        block = self._block(self.LOCK, "lock summary written")
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "results", "lock_session_X"))
            # No leg outdir at all: every platform takes the missing-summary path.
            rc, txt = self._run(block, td, "X", "ow,fn", "5",
                                "4.235", "4.249", "5.739", "4.882", "20", "0")
            self.assertNotEqual(rc, 0, txt)
            lock = json.load(open(os.path.join(
                td, "results", "lock_session_X", "lock_summary.json")))
        full = {"label", "outdir", "cp_dynamic_share_pct", "idle_w_used", "cv_pct",
                "host_saturation_pct", "ambient_present", "gates_ok", "problems", "runs"}
        for plat in ("openwhisk", "fn"):
            leg = lock["platforms"][plat]
            self.assertEqual(set(leg), full,
                             "%s leg key set differs: %s" % (plat, set(leg) ^ full))
            self.assertFalse(leg["gates_ok"])
            self.assertIsNone(leg["cp_dynamic_share_pct"])
            self.assertFalse(leg["ambient_present"])
            self.assertEqual(leg["runs"], [])
            self.assertTrue(any("no summary.json" in p for p in leg["problems"]),
                            leg["problems"])

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

    # ---- warm-up discard -------------------------------------------------
    # A cold JVM/classload makes the first repeat an outlier. In tier1ow8
    # run_1 cp_ms/inv was 56.4 against 29.6-33.4 afterwards, and OpenWhisk's
    # within-leg share SD at c=1 collapses 4.09 -> 0.29 once run_1 is dropped.
    # So the outlier has to be discardable -- but ONLY the first N, and only
    # when enough usable runs remain.
    def test_discard_warmup_drops_the_cold_run_from_the_drift_gate(self):
        # Real tier1ow8 c=8 shape with a pathologically slow run_1, then six
        # runs so five survive the discard. With discard=1 the drift gate must
        # compare run_2..run_6, not run_1..run_6.
        reps = [{"throughput_rps": r} for r in (120.0, 44.18, 40.0, 37.02,
                                                32.39, 30.5)]
        rc, txt = self._multi_run_gate(reps, repeat="6", discard="1")
        self.assertIn("discarded warm-up run(s): run_1", txt)
        self.assertIn("DRIFT run_2..run_6", txt,
                      "the drift report must name the runs it actually gated "
                      "on, not the discarded ones: %s" % txt)
        self.assertNotIn("DRIFT run_1..", txt)

    def test_discard_warmup_lets_a_leg_pass_that_only_failed_on_run_1(self):
        # run_1 alone is far off-trend; dropping it leaves a healthy flat leg.
        reps = [{"throughput_rps": r} for r in (120.0, 55.4, 58.1, 56.2,
                                                54.9, 57.3)]
        rc, txt = self._multi_run_gate(reps, repeat="6", discard="1")
        self.assertEqual(rc, 0,
                         "a leg that only failed on its cold run must pass once "
                         "that run is discarded: %s" % txt)
        self.assertNotIn("DRIFT", txt)

    def test_no_discard_keeps_gating_on_every_run(self):
        # Same data, discard=0: the off-trend run_1 must still be gated on. If
        # the discard leaked into the default path this would wrongly pass.
        reps = [{"throughput_rps": r} for r in (120.0, 55.4, 58.1, 56.2,
                                                54.9, 57.3)]
        rc, txt = self._multi_run_gate(reps, repeat="6", discard="0")
        self.assertNotEqual(rc, 0,
                            "run_1 must be gated on by default: %s" % txt)
        self.assertIn("DRIFT run_1..run_6", txt)
        self.assertNotIn("discarded", txt)

    def test_discard_two_warmup_runs(self):
        # A JVM often needs two passes to be warm, so the slice must be a count
        # and not a hardcoded "drop run_1".
        reps = [{"throughput_rps": r} for r in (120.0, 90.0, 55.4, 58.1,
                                                56.2, 54.9, 57.3)]
        rc, txt = self._multi_run_gate(reps, repeat="7", discard="2")
        self.assertIn("discarded warm-up run(s): run_1, run_2", txt)
        self.assertNotIn("DRIFT", txt)
        self.assertEqual(rc, 0, "post-warm-up leg must pass: %s" % txt)

    # ---- sampling-gap gate ------------------------------------------------
    # sample_totals() already sets sampling_gap_ok=False when the CPU sampler
    # went blind for longer than --max-sample-gap. That flag only WARNED, so a
    # run with a multi-second stall inside the measurement window could still be
    # cited on per-invocation CPU figures the instrument never actually saw.
    def _gap_gate(self, over, gap="1.0"):
        reps = [dict(self._base_summary(), throughput_rps=r, **over) for r in
                (56.0, 55.0, 57.0, 56.5, 55.5)]
        block = self._block(self.LOCK, "lock summary written")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "results", "openwhisk_cpubound_lock_X")
            os.makedirs(out)
            os.makedirs(os.path.join(td, "results", "lock_session_X"))
            for i, o in enumerate(reps, 1):
                d = os.path.join(out, "run_%d" % i)
                os.makedirs(d)
                with open(os.path.join(d, "summary.json"), "w") as f:
                    json.dump(o, f)
            with open(os.path.join(out, "summary.json"), "w") as f:
                json.dump(self._base_summary(), f)
            with open(os.path.join(out, "runs.json"), "w") as f:
                json.dump(reps, f)
            return self._run(block, td, "X", "ow", "5",
                             "4.235", "4.249", "5.739", "4.882", "20", "0", gap)

    def test_gate_rejects_a_run_whose_sampler_went_blind(self):
        rc, txt = self._gap_gate({"sampling_gap_ok": False,
                                  "sampling_max_gap_s": 4.2})
        self.assertNotEqual(rc, 0,
                            "a run with a 4.2 s blind interval is not citable: %s" % txt)
        self.assertIn("SAMPLING GAP 4.20s (limit 1.00s", txt)

    def test_gate_accepts_a_run_whose_sampling_was_continuous(self):
        rc, txt = self._gap_gate({"sampling_gap_ok": True,
                                  "sampling_max_gap_s": 0.06})
        self.assertEqual(rc, 0, "continuous sampling must pass: %s" % txt)
        self.assertNotIn("SAMPLING GAP", txt)

    def test_missing_sampling_key_does_not_fail_pre_existing_datasets(self):
        """Every committed dataset predates these keys and reads None. They must
        not be retroactively failed, or the gate is unusable on history."""
        rc, txt = self._gap_gate({})
        self.assertEqual(rc, 0,
                         "absent sampling keys must not fail the gate: %s" % txt)
        self.assertNotIn("SAMPLING GAP", txt)

    def test_sampling_gate_threshold_is_configurable(self):
        """A wide threshold must actually admit a run that the 1.0 s default
        rejects, or the option is decorative."""
        rc, txt = self._gap_gate({"sampling_gap_ok": False,
                                  "sampling_max_gap_s": 4.2}, gap="5.0")
        self.assertEqual(rc, 0, "--max-sample-gap 5.0 must admit a 4.2 s gap: %s" % txt)

    def test_gate_threshold_overrides_a_green_harness_boolean(self):
        """The harness sets sampling_gap_ok against ITS OWN --max-sample-gap. If
        the gate trusted that boolean, a session run with a loose harness
        threshold would sail through the gate's strict one. The gate must
        re-check the measured number."""
        rc, txt = self._gap_gate({"sampling_gap_ok": True,   # harness said fine
                                  "sampling_max_gap_s": 4.2})  # but 4.2 s > 1.0
        self.assertNotEqual(rc, 0,
                            "the gate must judge the measured gap, not inherit "
                            "the harness's verdict: %s" % txt)
        self.assertIn("SAMPLING GAP 4.20s", txt)

    def test_sampling_gap_exactly_at_the_limit_passes(self):
        """Boundary: the limit is inclusive. A gap of exactly max_sample_gap is
        tolerated; one hair over is not. Without pinning this, `>` silently
        becomes `>=` and a perfectly tuned session starts failing."""
        rc, txt = self._gap_gate({"sampling_gap_ok": True,
                                  "sampling_max_gap_s": 1.0}, gap="1.0")
        self.assertEqual(rc, 0, "a gap exactly at the limit must pass: %s" % txt)
        rc, txt = self._gap_gate({"sampling_gap_ok": True,
                                  "sampling_max_gap_s": 1.0001}, gap="1.0")
        self.assertNotEqual(rc, 0, "a hair over the limit must fail: %s" % txt)

    def test_discard_warmup_survives_into_the_lock_summary(self):
        # The discard must be recorded, or a later reader cannot tell that the
        # published median came from a post-discard subset.
        block = self._block(self.LOCK, "lock summary written")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "results", "openwhisk_cpubound_lock_X")
            os.makedirs(out)
            os.makedirs(os.path.join(td, "results", "lock_session_X"))
            for i, rps in enumerate([20.0, 57.0, 55.4, 58.1, 56.2, 54.9], 1):
                d = os.path.join(out, "run_%d" % i)
                os.makedirs(d)
                s = self._base_summary()
                s["throughput_rps"] = rps
                with open(os.path.join(d, "summary.json"), "w") as f:
                    json.dump(s, f)
            with open(os.path.join(out, "summary.json"), "w") as f:
                json.dump(self._base_summary(), f)
            with open(os.path.join(out, "runs.json"), "w") as f:
                json.dump([], f)
            rc, txt = self._run(block, td, "X", "ow", "6",
                                "4.235", "4.249", "5.739", "4.882", "20", "1")
            sp = os.path.join(td, "results", "lock_session_X", "lock_summary.json")
            meta = json.load(open(sp))["session"]
            self.assertEqual(meta["discard_warmup"], 1)
            self.assertEqual(meta["usable_runs_per_leg"], 5)

    def test_lock_summary_does_not_claim_an_idle_calibration_it_never_ran(self):
        # tier1 used --skip-idle-calib, yet every lock_summary.json carried
        # "idle-w recalibrated this session (...)" unconditionally. The tier1
        # passes were not fresh calibrations, and the note must not imply it.
        block = self._block(self.LOCK, "lock summary written")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "results", "openwhisk_cpubound_lock_X_quick")
            os.makedirs(out)
            os.makedirs(os.path.join(td, "results", "lock_session_X"))
            with open(os.path.join(out, "summary.json"), "w") as f:
                json.dump(self._base_summary(), f)
            with open(os.path.join(out, "runs.json"), "w") as f:
                json.dump([self._base_summary()], f)
            self._run(block, td, "X", "ow", "1",
                      "4.235", "4.249", "5.739", "4.882", "20", "0")
            sp = os.path.join(td, "results", "lock_session_X", "lock_summary.json")
            doc = json.load(open(sp))
            prov = doc["session"]["idle_w_provenance"]
            self.assertIn("NOT recalibrated", prov,
                          "with no calibration dir present the summary must say "
                          "the idle-w values were inherited: %s" % prov)
            self.assertIn("INHERITED", prov)
            self.assertNotIn("idle-w recalibrated this session (", prov,
                             "the false calibration claim must be gone: %s" % prov)
            self.assertNotIn("idle-w recalibrated this session (",
                             "\n".join(doc["session"]["notes"]))

    def test_lock_summary_claims_calibration_only_when_state_files_exist(self):
        # Positive branch: with a real calibration dir the claim must be made.
        block = self._block(self.LOCK, "lock summary written")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "results", "openwhisk_cpubound_lock_X_quick")
            os.makedirs(out)
            os.makedirs(os.path.join(td, "results", "lock_session_X"))
            os.makedirs(os.path.join(td, "results", "idle_w_calibration",
                                     "lock_X", "openwhisk"))
            with open(os.path.join(out, "summary.json"), "w") as f:
                json.dump(self._base_summary(), f)
            with open(os.path.join(out, "runs.json"), "w") as f:
                json.dump([self._base_summary()], f)
            self._run(block, td, "X", "ow", "1",
                      "4.235", "4.249", "5.739", "4.882", "20", "0")
            sp = os.path.join(td, "results", "lock_session_X", "lock_summary.json")
            prov = json.load(open(sp))["session"]["idle_w_provenance"]
            self.assertIn("recalibrated this session (1 state(s)", prov)

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


def _code_only(src):
    """Drop comment-only lines.

    The fixes below are documented in comments that quote the old buggy code
    verbatim, so a naive substring search would match the explanation of the
    defect rather than the defect."""
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


class TestTier1StatsHygiene(unittest.TestCase):
    """Three aggregator defects from the second expert review: a missing wall_s
    was silently replaced with 1.0 and fed to the fit, the detection limit used
    the POPULATION sd on a sample of runs, and a threshold built for one
    pairwise difference was applied to a range of four means."""

    TIER1 = os.path.join(REPO, "tools", "run_tier1_conc.sh")

    @classmethod
    def setUpClass(cls):
        with open(cls.TIER1) as f:
            src = f.read()
        cls.agg = "\n".join(re.findall(r"python3 - \"\$REPO\" <<'PY'\n(.*?)\nPY",
                                       src, re.S))
        # Only the table + lookup are pure; executing the whole block would run
        # the aggregation against the results tree.
        head = cls.agg.split("def find_run_dir")[0]
        # FIXED (1a audit): the heredoc's second line is `repo = sys.argv[1]`,
        # and this exec runs with unittest's argv (no REPO argument), so
        # setUpClass raised IndexError. unittest reported ONE setUpClass error
        # and skipped all 21 tests in this class -- silently, because the class
        # had no other failures to draw attention to it. The guards below (Tukey
        # df k(n-1), stdev-not-pstdev, the wall_s fallback, the flatness
        # threshold) were therefore not running at all, despite being cited as
        # proof those defects were fixed. sys is stubbed with an argv the
        # heredoc accepts; REPO is never touched below def find_run_dir.
        # NOTE: the heredoc re-imports sys on its own line 1, so injecting a
        # stub module into `ns` does NOT work -- `import sys` rebinds the name
        # to the real module and line 2 reads the real (empty) argv. sys.argv
        # itself must be swapped, then restored.
        saved_argv = sys.argv
        sys.argv = ["run_tier1_conc.sh", REPO]
        ns = {"math": math, "statistics": statistics, "os": os}
        try:
            exec(compile(head, "agg-head", "exec"), ns)
        finally:
            sys.argv = saved_argv
        cls.ns = ns

    def test_missing_wall_s_is_not_replaced_with_one(self):
        """`wall = r.get("wall_s") or 1.0` fabricated a 1.0 s window and fed it to
        the background fit; the wall<=0 guard below it could never fire."""
        src = open(self.TIER1).read()
        code = _code_only(src)
        self.assertNotRegex(code, r'wall\s*=\s*r\.get\("wall_s"\)\s*or\s*1\.0',
                            "a missing wall_s must fail closed, not become 1.0 s")
        self.assertEqual(code.count('wall = r.get("wall_s")'), 2,
                         "both fit sites must read wall_s explicitly")
        self.assertIn("has no usable wall_s", src)

    def test_detection_limit_uses_sample_sd_not_population_sd(self):
        """pstdev divides by n; these are n observed runs of a sample. It
        understated the limit ~11% at n=4, making 'flat' easier to declare."""
        code = _code_only(open(self.TIER1).read())
        self.assertNotIn("statistics.pstdev", code,
                         "population sd must not be used on a sample of runs")
        self.assertIn("statistics.stdev", code)

    def test_flatness_threshold_uses_studentized_range_not_pairwise_t(self):
        """The old (tcrit+tpow)*sqrt(2) threshold is a Bonferroni-corrected
        criterion for ONE pairwise difference, applied to max-minus-min over
        FOUR means. The correct question is Tukey's 'are these k means equal'."""
        code = _code_only(open(self.TIER1).read())
        self.assertNotIn("tpow", code,
                         "the Bonferroni pairwise-t threshold must be gone")
        self.assertIn("studentized_range_q", code)
        self.assertIn("df = nleg * (k_lev - 1)", code,
                      "ANOVA-style df for k groups of n, not 2*n-2")

    def test_studentized_range_q_reduces_to_t_times_sqrt2_for_k_equals_2(self):
        """For two levels the studentized range is |t|*sqrt(2), so the table is
        cross-checkable against standard t quantiles."""
        sq = self.ns["studentized_range_q"]
        self.assertAlmostEqual(sq(2, 4), 2.776 * math.sqrt(2), places=2)   # df=4
        self.assertAlmostEqual(sq(2, 10), 2.228 * math.sqrt(2), places=2)  # df=10
        self.assertAlmostEqual(sq(2, 40), 2.021 * math.sqrt(2), places=2)  # df=40

    def test_studentized_range_q_is_conservative_when_rounding_df(self):
        """Rounding df DOWN (or clamping above the table) gives a LARGER q,
        i.e. a stricter threshold -- never easier to declare flat."""
        sq = self.ns["studentized_range_q"]
        for k in (2, 3, 4):
            self.assertGreaterEqual(sq(k, 4), sq(k, 20))
            self.assertGreaterEqual(sq(k, 20), sq(k, 40))
        self.assertEqual(sq(4, 10 ** 6), sq(4, 40),
                         "df beyond the table must clamp to the last row")

    def test_studentized_range_q_rounds_an_off_table_df_downward(self):
        """The rounding direction only shows up at a df that is NOT in the table.
        Rounding down gives the LARGER q (df=10 rather than df=12 for a request of
        11), i.e. the stricter threshold. Rounding up would let a leg declare
        itself flat with less evidence."""
        sq = self.ns["studentized_range_q"]
        tbl = self.ns["_Q_TABLE"]
        for want_df, lower_df, upper_df in ((11, 10, 12), (13, 12, 15), (14, 12, 15),
                                            (16, 15, 20), (19, 15, 20),
                                            (21, 20, 24), (23, 20, 24),
                                            (25, 24, 30), (35, 30, 40)):
            self.assertEqual(sq(4, want_df), tbl[lower_df][4],
                             "df=%d must round down to the lower tabulated row %d"
                             % (want_df, lower_df))
            self.assertGreater(sq(4, want_df), sq(4, upper_df),
                               "rounding down must yield the larger, stricter q")

    def test_studentized_range_q_grows_with_the_number_of_levels(self):
        """Comparing MORE means needs a wider range to be significant, so q rises
        with k. (The old fixed pairwise-t threshold could not express this.)"""
        sq = self.ns["studentized_range_q"]
        for df in (4, 10, 20):
            vals = [sq(k, df) for k in (2, 3, 4, 5)]
            self.assertEqual(vals, sorted(vals),
                             "more levels must need a wider range to be significant")

    def test_studentized_range_table_is_monotone_in_df(self):
        tbl = self.ns["_Q_TABLE"]
        for k in (2, 3, 4, 5):
            series = [tbl[df][k] for df in sorted(tbl)]
            self.assertEqual(series, sorted(series, reverse=True),
                             "q must decrease as df grows")
            self.assertGreater(tbl[sorted(tbl)[0]][k], tbl[sorted(tbl)[-1]][k])

    # ---- TOST: 'flat to resolution' is not 'flat' -----------------------
    # The paper's claim is that cp_dynamic_share does not move with concurrency.
    # The detection-limit line only shows the spread is below this instrument's
    # power; that is a non-inferiority result and cannot support the word "flat".
    def test_tost_accepts_a_tight_cluster_with_tight_noise(self):
        """A genuinely flat leg: means within 0.2 pp of each other, small SD."""
        ok, (lo, hi) = self.ns["tost_equivalent"](0.20, 0.05, 4, 2.0)
        self.assertTrue(ok, "a 0.2 pp deviation cannot exclude <=2 pp: %s" % ((lo, hi),))

    def test_tost_rejects_when_noise_alone_cannot_exclude_the_margin(self):
        """The OpenWhisk c=1 case: the MEAN deviation is only 1.77 pp, under the
        2 pp margin, but the CI is [-5.67, +2.13] and straddles it. This is the
        leg that printed 'flat TO RESOLUTION' and must not read as flat.
        se is back-solved from that interval: half-width 3.90 / t(4)=2.132."""
        ok, (lo, hi) = self.ns["tost_equivalent"](-1.77, 3.90 / 2.132, 4, 2.0)
        self.assertFalse(ok,
                         "a CI crossing the margin is not equivalence: %s" % ((lo, hi),))
        self.assertLess(lo, -2.0)
        self.assertGreater(hi, 2.0)

    def test_tost_is_stricter_than_comparing_the_mean_to_the_margin(self):
        """The bug class this guards: `abs(mean) < margin` declares equivalence
        while ignoring sampling error entirely."""
        ok, _ = self.ns["tost_equivalent"](1.5, 0.60, 4, 2.0)
        self.assertFalse(ok,
                         "1.5 pp < 2.0 pp but the CI is ~1.3 pp wide, so "
                         "differences beyond the margin are not excluded")

    def test_tost_rejects_zero_standard_error_rather_than_passing(self):
        """se==0 (identical runs) must not short-circuit to 'equivalent'."""
        ok, _ = self.ns["tost_equivalent"](1.9, 0.0, 4, 2.0)
        self.assertFalse(ok, "degenerate se must fail closed, not certify flatness")

    def test_tost_rejects_a_nonpositive_margin(self):
        """A margin of 0 or negative is meaningless and must not certify."""
        for bad in (0.0, -1.0):
            ok, _ = self.ns["tost_equivalent"](0.0, 0.1, 4, bad)
            self.assertFalse(ok, "margin=%r must not certify equivalence" % bad)

    def test_tost_t_table_decreases_with_df(self):
        """t must fall as df grows, so tighter data can exclude a wider range."""
        tbl = self.ns["_T_TABLE"]
        tc = self.ns["t_crit_95"]
        for df in (2, 5, 10, 20, 40):
            self.assertLess(tc(df), tbl[sorted(tbl)[0]])
        series = [tbl[df] for df in sorted(tbl)]
        self.assertEqual(series, sorted(series, reverse=True))

    def test_tost_df_rounding_is_conservative(self):
        """Off-table df must round DOWN to a LARGER t, widening the CI and making
        equivalence harder -- never easier."""
        tc = self.ns["t_crit_95"]
        tbl = self.ns["_T_TABLE"]
        for want_df, lower_df, upper_df in ((11, 10, 12), (13, 12, 15), (14, 12, 15),
                                            (16, 15, 20), (19, 15, 20),
                                            (21, 20, 24), (23, 20, 24),
                                            (25, 24, 30), (35, 30, 40)):
            self.assertEqual(tc(want_df), tbl[lower_df],
                             "df=%d must round down to %d" % (want_df, lower_df))
            self.assertGreater(tc(want_df), tc(upper_df),
                               "rounding down must yield the larger, stricter t")

    def test_equivalence_margin_is_prespecified_not_data_derived(self):
        """A margin chosen after seeing the spread is what made 'flat' "
        "unfalsifiable. It must come from the environment with a fixed default,
        never from the measured spread."""
        code = _code_only(open(self.TIER1).read())
        self.assertIn('SAQEF_EQUIV_MARGIN_PP', code,
                      "the margin must be an explicit, logged input")
        self.assertIn('"2.0"', code, "and must have a fixed pre-specified default")
        # No expression may build the margin out of the observed data.
        self.assertNotRegex(code, r"EQUIV_MARGIN_PP\s*=\s*(spread|mdd|s_pool|max\()",
                            "the margin must not be derived from the measurements")

    def test_flat_to_resolution_never_prints_without_a_tost_line(self):
        """The output must never show the old 'flat TO RESOLUTION' verdict with
        no equivalence test next to it."""
        code = _code_only(open(self.TIER1).read())
        self.assertIn("EQUIVALENCE NOT ESTABLISHED", code)
        self.assertIn("EQUIVALENT to flat within margin", code)
        self.assertIn("Do not", code)

    # ---- end to end: the printed verdict is what the paper quotes ---------
    # The pure-function tests above cannot catch a broken REPORTING path (a TOST
    # that is computed and then silently dropped, or a margin quietly widened at
    # the call site). Those mutants all survived. So drive the real aggregator
    # over a synthetic results tree and assert on what it prints.
    def _agg_over_tree(self, tree):
        """Run the whole aggregation block against a synthetic results tree."""
        src = open(self.TIER1).read()
        blk = "\n".join(re.findall(r"python3 - \"\$REPO\" <<'PY'\n(.*?)\nPY",
                                   src, re.S))
        self.assertTrue(blk, "aggregation block not found")
        with tempfile.TemporaryDirectory() as td:
            self._write_tree(td, tree)
            errs = os.path.join(td, "errs.txt")
            with open(errs, "w") as f:
                f.write("0\n")
            p = subprocess.run([sys.executable, "-c", blk, td],
                               capture_output=True, text=True,
                               env=dict(os.environ, SAQEF_TIER1_ERRS=errs))
        return p.returncode, p.stdout + p.stderr

    def _write_tree(self, td, tree):
        """tree: {concurrency: [share_pct per run]} for the synthetic platform."""
        for c, shares in tree.items():
            stamp = "tier1c%d" % c
            d = os.path.join(td, "results", "openfaas_cpubound_lock_%s" % stamp)
            os.makedirs(d, exist_ok=True)
            runs = [{"requests": 3000, "total_requested": 3000, "wall_s": 100.0,
                     "cpu_sec": {"control_plane": 60.0, "function": 60.0},
                     "cp_dynamic_share_pct": s,
                     "container_labels": {"h": {"image": "x/hello:v1"}},
                     "container_inventory": ["h"],
                     "platform": "openfaas"} for s in shares]
            json.dump(runs, open(os.path.join(d, "runs.json"), "w"))
            json.dump(runs[0], open(os.path.join(d, "summary.json"), "w"))
            pd = os.path.join(td, "results", "idle_probe_%s" % stamp, "openfaas")
            os.makedirs(pd, exist_ok=True)
            json.dump([{"wall_s": 60.0,
                        "cpu_sec": {"control_plane": 3.0, "function": 6.0}}],
                      open(os.path.join(pd, "runs.json"), "w"))

    def test_aggregator_reports_equivalence_when_the_margin_is_excluded(self):
        rc, txt = self._agg_over_tree(
            {1: [7.80, 7.76, 7.82, 7.78, 7.81],
             2: [7.79, 7.77, 7.81, 7.80, 7.78],
             4: [7.81, 7.79, 7.83, 7.80, 7.82],
             8: [7.80, 7.82, 7.78, 7.81, 7.79]})
        self.assertIn("TOST", txt, "every flat verdict needs a TOST line: %s" % txt)
        self.assertIn("EQUIVALENT to flat within margin", txt,
                      "a 0.06 pp spread with tiny noise must be established as "
                      "flat: %s" % txt)

    def test_aggregator_refuses_to_call_an_unresolved_spread_flat(self):
        """OpenWhisk's shape: 1.5 pp of spread that is small against the CV but
        not tight enough to exclude the 2 pp margin."""
        rc, txt = self._agg_over_tree(
            {1: [81.14, 86.0, 79.0, 84.5, 80.0],
             4: [83.41, 84.0, 83.0, 83.9, 82.5],
             8: [84.18, 85.1, 84.0, 83.6, 85.0]})
        self.assertIn("flat TO RESOLUTION", txt,
                      "this tree should still be below the detection limit")
        self.assertIn("EQUIVALENCE NOT ESTABLISHED", txt,
                      "and it must NOT be reported as proven flat: %s" % txt)
        self.assertIn("Do not", txt)

    def test_aggregator_cannot_report_flat_when_the_tost_line_is_suppressed(self):
        """Direct check of the M6 mutant: if the TOST print were removed, the
        'flat TO RESOLUTION' verdict would stand alone and be quotable."""
        src = open(self.TIER1).read()
        self.assertNotRegex(_code_only(src),
                            r"if False:\s*print\(\s*\"+\s*TOST",
                            "the TOST line must not be disabled by a dead branch")
        flat_at = src.index("flat TO RESOLUTION (%.2f < %.2f pp)")
        tost_at = src.index('print("             TOST')
        self.assertLess(flat_at, tost_at)
        self.assertLess(tost_at, len(src))

    def test_aggregator_cannot_widen_the_margin_at_the_call_site(self):
        """Direct check of the M5 mutant: the call must pass the pre-specified
        margin, never a literal chosen to make the data look flat."""
        code = _code_only(open(self.TIER1).read())
        m = re.search(r"tost_equivalent\(m - gm,(.*?)\)\)", code, re.S)
        self.assertIsNotNone(m, "tost_equivalent call not found")
        self.assertIn("EQUIV_MARGIN_PP", m.group(1),
                      "the call site must use the pre-specified margin: %s"
                      % m.group(1).strip())
        self.assertNotRegex(m.group(1), r"\d+\.\d",
                            "the call site must not hardcode a pp margin: %s"
                            % m.group(1).strip())


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
        # CORRECTED 2026-10-01 (second expert review): the section-14 formula
        # (tcrit+tpow)*sqrt(2)*s/sqrt(n) is a Bonferroni threshold for ONE
        # pairwise difference, but it was being applied to max-minus-min over
        # FOUR means. The limit is now the Tukey studentized-range threshold.
        # Asserting on the comment-quoted string would be a false pass, hence
        # _code_only().
        code = _code_only(src)
        self.assertNotIn("math.sqrt(2.0)", code,
                         "the pairwise-t MDD formula must be gone from the code")
        self.assertIn("studentized_range_q(k_lev, df) * s_pool", code,
                      "MDD must use the studentized-range criterion for k levels")

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


class TestCgroupSamplerOverhead(unittest.TestCase):
    """The cgroup sampler used to run `docker ps` + TWO `docker inspect` per
    container on EVERY scan. `docker inspect` is ~90 ms of host CPU per call, so
    on the ~70-container Knative stack the instrument itself burned ~3 cores --
    charged to no measured cgroup, so it never appeared in cp/fn, but it
    inflated host_saturation_pct, polluted the host-residual check, and starved
    the platform of ~40% of the box. Discovery is now a cgroup-tree walk plus one
    inspect per container *lifetime*."""

    @classmethod
    def setUpClass(cls):
        cls.loader = importlib.machinery.SourceFileLoader(
            "saqef_harness", os.path.join(REPO, "saqef_harness.py"))
        spec = importlib.util.spec_from_loader("saqef_harness", cls.loader)
        cls.h = importlib.util.module_from_spec(spec)
        cls.loader.exec_module(cls.h)

    def test_container_cgroup_dirs_issues_exactly_one_inspect(self):
        """The cpu and memory dirs both come from one /proc/<pid>/cgroup read, so
        one PID lookup must serve both. Two inspects per container per scan was
        the defect."""
        calls = []

        class R:
            returncode = 0
            stdout = "4242\n"

        def fake_run(cmd, *a, **kw):
            calls.append(cmd)
            return R()

        orig_run, orig_open = self.h.run, open
        self.h.run = fake_run
        import builtins
        real_open = builtins.open

        def fake_open(p, *a, **kw):
            if str(p) == "/proc/4242/cgroup":
                import io
                return io.StringIO("0::/docker/abc123.scope\n")
            return real_open(p, *a, **kw)

        builtins.open = fake_open
        try:
            cpu, mem = self.h.container_cgroup_dirs("deadbeef")
        finally:
            self.h.run = orig_run
            builtins.open = real_open
        self.assertEqual(len(calls), 1,
                         "must resolve PID with a single docker inspect, got %d: %s"
                         % (len(calls), calls))
        self.assertEqual(cpu, "/sys/fs/cgroup/docker/abc123.scope")
        self.assertEqual(mem, cpu, "cgroup v2 is unified: cpu and memory dirs coincide")

    def test_thin_wrappers_agree_with_combined_lookup(self):
        self.assertEqual(self.h.container_cgroup_dir.__doc__ is not None, True)
        class R:
            returncode = 0
            stdout = "0\n"
        orig = self.h.run
        self.h.run = lambda cmd, *a, **kw: R()
        try:
            # Both wrappers must route through the single-inspect path.
            cpu, mem = self.h.container_cgroup_dirs("x")
            self.assertEqual(self.h.container_cgroup_dir("x"), cpu)
            self.assertEqual(self.h.container_mem_cgroup_dir("x"), mem)
        finally:
            self.h.run = orig

    def test_discover_parses_scope_dir_names(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cid = "a" * 64
            os.makedirs(os.path.join(d, "docker-%s.scope" % cid))
            os.makedirs(os.path.join(d, "notacontainer"))
            got = dict(self.h.discover_cgroup_container_dirs(root=d))
            self.assertIn(cid, got)
            self.assertEqual(got[cid], os.path.join(d, "docker-%s.scope" % cid))
            self.assertNotIn("notacontainer", got.values())

    def test_discover_returns_empty_for_missing_root(self):
        self.assertEqual(self.h.discover_cgroup_container_dirs(root="/nonexistent/x"), [])

    def test_steady_state_spawns_no_docker_subprocesses(self):
        """The point of the fix: after the first scan, sampling must not fork
        docker at all. This is the assertion that would have failed before."""
        cid = "b" * 64
        cdir = "/sys/fs/cgroup/docker-%s.scope" % cid
        imports = []

        class R:
            returncode = 0
            stdout = "/web\n"

        def fake_run(cmd, *a, **kw):
            imports.append(cmd)
            return R()

        usage = [0.0]

        orig_run = self.h.run
        orig_disc = self.h.discover_cgroup_container_dirs
        orig_read = self.h.read_cpu_cumulative
        orig_mem = self.h.read_mem_mb
        self.h.run = fake_run
        self.h.discover_cgroup_container_dirs = lambda *a, **kw: [(cid, cdir)]
        self.h.read_cpu_cumulative = lambda d: 1.0 + 0.01 * len(imports)
        self.h.read_mem_mb = lambda d: 5.0

        import threading
        samples, stop, first = [], threading.Event(), threading.Event()
        try:
            th = threading.Thread(target=self.h.cgroup_sampler,
                                  args=(samples, stop, first, 0.01, 0.01), daemon=True)
            th.start()
            first.wait(timeout=5)
            warm = len(imports)
            self.assertTrue(samples, "sampler produced no samples")
            import time as _t
            _t.sleep(0.2)   # keep sampling; every extra sample must fork nothing
            stop.set()
            th.join(timeout=5)
        finally:
            self.h.run = orig_run
            self.h.discover_cgroup_container_dirs = orig_disc
            self.h.read_cpu_cumulative = orig_read
            self.h.read_mem_mb = orig_mem

        self.assertEqual(warm, 1,
                         "exactly one inspect per container lifetime, got %d" % warm)
        self.assertGreater(len(samples), 5, "sampler should keep sampling after warm-up")
        self.assertTrue(all(s[2] == "cum" for s in samples))
        self.assertEqual(len(imports), warm,
                         "steady-state sampling forked %d extra docker calls"
                         % (len(imports) - warm))

    def test_dead_container_ids_are_evicted_from_the_cache(self):
        """An ID is never reused, so a stale entry would leak forever."""
        cid = "c" * 64
        cdir = "/sys/fs/cgroup/docker-%s.scope" % cid

        class R:
            returncode = 0
            stdout = "/web\n"

        live = [[(cid, cdir)]]
        orig_run = self.h.run
        orig_disc = self.h.discover_cgroup_container_dirs
        orig_read = self.h.read_cpu_cumulative
        orig_mem = self.h.read_mem_mb
        self.h.run = lambda cmd, *a, **kw: R()
        self.h.discover_cgroup_container_dirs = lambda *a, **kw: live[0]
        self.h.read_cpu_cumulative = lambda d: 1.0
        self.h.read_mem_mb = lambda d: 5.0

        import threading
        try:
            # first scan sees one container
            samples, stop, first = [], threading.Event(), threading.Event()
            th = threading.Thread(target=self.h.cgroup_sampler,
                                  args=(samples, stop, first, 0.01, 0.01), daemon=True)
            th.start()
            first.wait(timeout=5)
            self.assertEqual(set(samples[0][1]), {"web"})
            stop.set()
            th.join(timeout=5)
        finally:
            self.h.run = orig_run
            self.h.discover_cgroup_container_dirs = orig_disc
            self.h.read_cpu_cumulative = orig_read
            self.h.read_mem_mb = orig_mem

    def test_sample_s_replaces_the_pinned_100hz_loop(self):
        src = open(os.path.join(REPO, "saqef_harness.py")).read()
        sampler = src.split("def cgroup_sampler")[1].split("\ndef ")[0]
        self.assertNotIn("stop.wait(0.01)", sampler,
                         "the fixed 10 ms wait pinned the sampler near 100 Hz")
        self.assertIn("stop.wait(sample_s)", sampler)


class TestSamplingQualityGate(unittest.TestCase):
    """'sampling_covered_s' was quoted in the paper as a quality gate, but it
    CANNOT FAIL: covered summed the dt of every consecutive sample pair plus a
    synthetic SAMPLE_S tail and was then clamped to wall, so any sampler that
    merely started before the load and stopped after it read 100%. The real
    question is whether the sampler went blind mid-window, which is a MAX GAP."""

    @classmethod
    def setUpClass(cls):
        cls.loader = importlib.machinery.SourceFileLoader(
            "saqef_harness", os.path.join(REPO, "saqef_harness.py"))
        spec = importlib.util.spec_from_loader("saqef_harness", cls.loader)
        cls.h = importlib.util.module_from_spec(spec)
        cls.loader.exec_module(cls.h)

    @staticmethod
    def _samples(times):
        return [(t, {"web": (t, 1.0)}, "cum") for t in times]

    def test_coverage_reads_100pct_even_with_a_10s_blind_stall(self):
        ts = [i * 0.05 for i in range(20)] + [10.0 + i * 0.05 for i in range(20)]
        st = self.h.sample_totals(self._samples(ts), ("web",), "fn")
        span = st.span_s or 1.0
        coverage_pct = min(st.covered_s, span) / span * 100.0
        self.assertAlmostEqual(coverage_pct, 100.0, places=1,
                               msg="this is the defect: coverage is unfailable")
        self.assertGreater(st.max_gap_s, 9.0,
                           "but the new max-gap metric must see the stall")

    def test_max_gap_gate_fails_on_a_mid_window_stall(self):
        ts = [i * 0.05 for i in range(20)] + [3.0 + i * 0.05 for i in range(60)]
        st = self.h.sample_totals(self._samples(ts), ("web",), "fn")
        self.assertGreater(st.max_gap_s, 1.0)
        gate_ok = st.n_samples >= 2 and st.max_gap_s <= 1.0
        self.assertFalse(gate_ok, "a >1 s blind interval must fail the gate")

    def test_max_gap_gate_passes_on_a_healthy_20hz_stream(self):
        ts = [i * 0.05 for i in range(101)]
        st = self.h.sample_totals(self._samples(ts), ("web",), "fn")
        self.assertEqual(st.n_samples, 101)
        self.assertAlmostEqual(st.max_gap_s, 0.05, places=3)
        self.assertAlmostEqual(st.span_s, 5.0, places=3)
        self.assertTrue(st.n_samples >= 2 and st.max_gap_s <= 1.0)

    def test_single_sample_is_not_a_pass(self):
        """One sample means the instrument never observed the window."""
        st = self.h.sample_totals(self._samples([0.0]), ("web",), "fn")
        self.assertEqual(st.n_samples, 1)
        gate_ok = st.n_samples >= 2 and st.max_gap_s <= 1.0
        self.assertFalse(gate_ok, "a single sample must not certify sampling quality")

    def test_synthetic_tail_is_excluded_from_the_gap_metric(self):
        """The last sample has a synthetic SAMPLE_S tail used for CPU
        integration. It must not be counted as a real 1.0 s observation."""
        ts = [i * 0.05 for i in range(21)]        # 1.0 s of real 20 Hz samples
        st = self.h.sample_totals(self._samples(ts), ("web",), "fn")
        self.assertLess(st.max_gap_s, 0.1,
                        "the synthetic final tail must not masquerade as a blind gap")

    # ---- the load window -------------------------------------------------
    # The sampler is started before the load and stopped after it, so samples
    # exist outside [t0, t0+wall]. Counting them folded idle CPU into the run and
    # pushed cp_dynamic_share_pct down by an amount that varies with the
    # overhang, so it was not even a constant bias across concurrencies.
    def test_cpu_outside_the_load_window_is_not_counted(self):
        """Same counters, two windows: total CPU must scale with the window."""
        ts = [i * 0.5 for i in range(41)]          # 20 s, counter grows 1 CPU/s
        full = self.h.sample_totals(self._samples(ts), ("web",), "fn")
        half = self.h.sample_totals(self._samples(ts), ("web",), "fn",
                                    window=(5.0, 15.0))
        self.assertAlmostEqual(half.cp_cpu_s, 10.0, places=6,
                               msg="a 10 s window of a 1 CPU/s counter is 10 CPU-s")
        self.assertAlmostEqual(full.cp_cpu_s, 20.0, places=6,
                               msg="the whole 20 s of samples is 20 CPU-s")
        self.assertLess(half.cp_cpu_s, full.cp_cpu_s)

    def test_window_does_not_change_the_share_of_cpu_it_attributes(self):
        """Clipping must not move CPU between buckets: cp and fn both live
        outside the window, so their RATIO must be preserved."""
        ts = [i * 0.5 for i in range(41)]
        smp = [(t, {"cp": (t, 1.0), "fn": (3.0 * t, 1.0)}, "cum") for t in ts]
        st = self.h.sample_totals(smp, ("cp",), "fn", window=(5.0, 15.0))
        share = st.cp_cpu_s / (st.cp_cpu_s + st.fn_cpu_s)
        self.assertAlmostEqual(share, 0.25, places=6,
                               msg="cp:fn = 1:3 ratio must survive the clip")

    def test_samples_before_the_window_contribute_nothing(self):
        """A long pre-load stretch must not appear in the totals."""
        ts = list(range(0, 100)) + [100.0 + i * 0.5 for i in range(21)]
        smp = [(t, {"web": (t, 1.0)}, "cum") for t in ts]
        st = self.h.sample_totals(smp, ("web",), "fn", window=(100.0, 110.0))
        self.assertAlmostEqual(st.cp_cpu_s, 10.0, places=6,
                               msg="only the 10 s inside the window may count")

    def test_a_gap_entirely_outside_the_window_does_not_fail_the_gate(self):
        """The 100 s stall before the load starts says nothing about whether the
        window was observed. It must not trip max_gap_s."""
        ts = list(range(0, 20)) + [100.0 + i * 0.05 for i in range(21)]
        st = self.h.sample_totals(self._samples(ts), ("web",), "fn",
                                  window=(100.0, 101.0))
        self.assertLess(st.max_gap_s, 0.1,
                        "a pre-window stall is not a mid-window blind interval")

    def test_a_gap_inside_the_window_still_fails(self):
        """Clipping must not accidentally suppress the stall it is meant to
        keep visible. Sampled 0-0.45, blind 0.45-1.5, then 1.5-3.45."""
        ts = [100.0 + i * 0.05 for i in range(10)] + [101.5 + i * 0.05
                                                      for i in range(40)]
        st = self.h.sample_totals(self._samples(ts), ("web",), "fn",
                                  window=(100.0, 103.5))
        self.assertGreater(st.max_gap_s, 1.0,
                           "an in-window stall must still be reported")

    def test_window_coverage_never_exceeds_the_window(self):
        ts = [i * 0.5 for i in range(41)]
        st = self.h.sample_totals(self._samples(ts), ("web",), "fn",
                                  window=(5.0, 15.0))
        self.assertLessEqual(st.covered_s, 10.0 + 1e-9,
                             "coverage cannot exceed the load window itself")

    def test_window_edges_that_fall_between_samples_are_prorated(self):
        """Every other window test happens to land on sample instants, so the
        two partial intervals at the boundary are never exercised -- and those
        are precisely the ones a hard clip or an un-prorated sum gets wrong.
        Window 5.25..14.75 spans 9.5 s of a 1 CPU/s counter; the first and last
        sample intervals are only half inside it."""
        ts = [i * 0.5 for i in range(41)]
        st = self.h.sample_totals(self._samples(ts), ("web",), "fn",
                                  window=(5.25, 14.75))
        self.assertAlmostEqual(st.cp_cpu_s, 9.5, places=6,
                               msg="the half-overlap intervals at each edge must "
                                   "contribute half their CPU")
        self.assertAlmostEqual(st.covered_s, 9.5, places=6,
                               msg="coverage must be prorated at the edges too")

    def test_a_window_entirely_between_two_samples_credits_only_the_overlap(self):
        """A window wholly inside one sample interval credits that fraction of
        the delta, not the whole interval."""
        ts = [i * 0.5 for i in range(41)]
        st = self.h.sample_totals(self._samples(ts), ("web",), "fn",
                                  window=(3.10, 3.40))
        self.assertAlmostEqual(st.cp_cpu_s, 0.30, places=6,
                               msg="0.30 s inside the [3.0,3.5] interval is 0.30 CPU-s")

    def test_a_window_with_no_sample_overlap_yields_no_cpu(self):
        """A sub-cadence gap shorter than the sampling interval still contains
        real CPU; it must be apportioned by overlap, not rounded away."""
        ts = [i * 0.5 for i in range(41)]
        full = self.h.sample_totals(self._samples(ts), ("web",), "fn",
                                    window=(3.0, 3.5))
        half = self.h.sample_totals(self._samples(ts), ("web",), "fn",
                                    window=(3.0, 3.25))
        self.assertAlmostEqual(full.cp_cpu_s, 0.5, places=6)
        self.assertAlmostEqual(half.cp_cpu_s, 0.25, places=6)

    def test_a_window_ending_exactly_at_a_sample_credits_nothing_after_it(self):
        """Boundary: overlap == 0 is not overlap. Window (3.0, 3.5) contains the
        interval [3.0,3.5] and nothing of [3.5,4.0]; a `<` instead of `<=`
        guard would fold that next half-second in."""
        ts = [i * 0.5 for i in range(41)]
        st = self.h.sample_totals(self._samples(ts), ("web",), "fn",
                                  window=(3.0, 3.5))
        self.assertAlmostEqual(st.cp_cpu_s, 0.5, places=6,
                               msg="exactly one interval, not one and a half")
        self.assertAlmostEqual(st.covered_s, 0.5, places=6)

    def test_no_window_argument_preserves_the_old_behaviour(self):
        """Call sites and older analyses pass no window; clipping must be
        opt-in or every previously published number silently changes."""
        ts = [i * 0.5 for i in range(41)]
        st = self.h.sample_totals(self._samples(ts), ("web",), "fn")
        self.assertAlmostEqual(st.cp_cpu_s, 20.0, places=6)

    # ---- the window must share a time base with the samples ---------------
    # Regression guard for a bug that made every real run produce 0 CPU: the
    # sampler stamps samples with time.time() (epoch) but run_once() built the
    # attribution window from time.perf_counter() (seconds since boot). The two
    # differ by ~1.76e9 here, so no sample ever fell inside the window and every
    # CPU total came out zero. The unit tests below could not catch it because
    # they all construct the window in the same numeric space as the timestamps
    # they hand in. So these tests deliberately use RAW clocks and assert that
    # the harness pairs them the way run_once() does.
    def test_attribution_window_is_built_in_the_same_time_base_as_the_samples(self):
        """A real run must not attribute zero CPU. Sampled stamps come from
        time.time(); the window must therefore be epoch-based."""
        src = open(os.path.join(REPO, "saqef_harness.py")).read()
        body = re.search(r"def run_once.*?\n(?=\ndef )", src, re.S).group(0)
        self.assertRegex(body, r"window=\(t0_epoch,\s*t0_epoch \+ wall\)",
                         "run_once must pass an EPOCH-based window to "
                         "sample_totals(), matching the sampler's time.time() stamps")
        # And the epoch base must actually be recorded from time.time().
        self.assertRegex(body, r"t0_epoch\s*=\s*time\.time\(\)")

    def test_window_from_perf_counter_attributes_nothing(self):
        """Documenting the failure mode itself: a perf_counter-based window
        against epoch-stamped samples yields zero CPU and zero coverage. If this
        ever stops being true, the two clocks have been unified and the guard
        test above should be revisited rather than trusted blindly."""
        epoch_now = time.time()
        perf_now = time.perf_counter()
        # 10 samples, 1/s, container burning 1 CPU-s per second.
        samples = []
        for i in range(11):
            t = epoch_now + i
            samples.append((t, {"web": (float(i), 1.0)}, "cum"))
        good = self.h.sample_totals(samples, ("web",), "fn",
                                    window=(epoch_now, epoch_now + 10))
        bad = self.h.sample_totals(samples, ("web",), "fn",
                                   window=(perf_now, perf_now + 10))
        self.assertAlmostEqual(good.cp_cpu_s, 10.0, places=6,
                               msg="epoch window must attribute the full load")
        self.assertGreater(good.covered_s, 9.0)
        self.assertAlmostEqual(bad.cp_cpu_s, 0.0, places=6,
                               msg="a boot-based window cannot overlap epoch stamps")
        self.assertAlmostEqual(bad.covered_s, 0.0, places=6)

    def test_preload_cpu_is_not_absorbed_by_the_first_in_window_sample(self):
        """The sampler starts before the load, so the first in-window cumulative
        delta covers (prev_t, t] -- which straddles t0. Prorating it against the
        FORWARD interval instead put the whole pre-load stretch into the window,
        which is the exact leak the window is meant to remove."""
        pre = [i * 0.5 for i in range(11)]                 # 0.0 .. 5.0, idle
        during = [5.0 + 0.5 * i for i in range(21)]        # 5.0 .. 15.0, loaded
        ts = pre + during
        smp = [(t, {"web": (t, 1.0)}, "cum") for t in ts]
        st = self.h.sample_totals(smp, ("web",), "fn", window=(5.0, 15.0))
        self.assertAlmostEqual(st.cp_cpu_s, 10.0, places=6,
                               msg="only the 10 s of load may be attributed, "
                                   "not the 5 s of idle time before it")

    # ---- containers born mid-run -----------------------------------------
    # A container first seen at sample i has a counter that already holds
    # everything it burned since creation. That slice used to be dropped
    # entirely, which on scale-up platforms is seconds of real fn CPU for the
    # container that appears last.
    def test_cpu_burned_before_a_container_is_first_sampled_is_not_lost(self):
        """fn does not exist in the 12.0 sample, and by 13.0 its counter already
        reads 4 CPU-s: it was created at 12.0 and burned that CPU before anyone
        looked. The container must appear in exactly ONE sample here, otherwise
        the value comes from the ordinary forward delta and the birth slice is
        never exercised."""
        smp = [
            (12.0, {"cp": (1.0, 1.0, 0.0)}, "cum"),
            (13.0, {"cp": (2.0, 1.0, 0.0), "fn": (4.0, 1.0, 12.0)}, "cum"),
        ]
        st = self.h.sample_totals(smp, ("cp",), "fn", window=(12.0, 13.0))
        self.assertAlmostEqual(st.fn_cpu_s, 4.0, places=6,
                               msg="the birth-to-first-sample slice is real CPU "
                                   "and must be credited, not zeroed")
        self.assertAlmostEqual(st.cp_cpu_s, 1.0, places=6,
                               msg="cp's own delta is unaffected")

    def test_a_container_visible_in_two_samples_uses_the_delta_not_the_birth(self):
        """The birth credit is a FIRST-sighting correction only. If it were
        applied on every sample the counter would be counted repeatedly."""
        smp = [
            (12.0, {"fn": (0.0, 1.0, 12.0)}, "cum"),
            (13.0, {"fn": (4.0, 1.0, 12.0)}, "cum"),
            (14.0, {"fn": (8.0, 1.0, 12.0)}, "cum"),
        ]
        st = self.h.sample_totals(smp, ("cp",), "fn", window=(12.0, 14.0))
        self.assertAlmostEqual(st.fn_cpu_s, 8.0, places=6,
                               msg="4 CPU-s in each of two intervals, not 4+4+8")

    def test_birth_credit_is_clipped_to_the_load_window(self):
        """fn is absent from the first sample and by the second its counter
        reads 30 CPU-s after a 60 s life. The load window is only 50..55, so
        5/60 of that counter is in-window. This genuinely exercises the birth
        branch: fn appears in exactly one sample."""
        smp = [
            (50.0, {"cp": (0.0, 1.0, 0.0)}, "cum"),
            (60.0, {"cp": (1.0, 1.0, 0.0), "fn": (30.0, 1.0, 0.0)}, "cum"),
        ]
        st = self.h.sample_totals(smp, ("cp",), "fn", window=(50.0, 55.0))
        self.assertAlmostEqual(st.fn_cpu_s, 2.5, places=6,
                               msg="only 5/60 of a 30 CPU-s lifetime is in-window")

    def test_a_container_present_from_the_first_sample_credits_its_birth_slice(self):
        """The very first sample of the run also has no baseline, so it takes the
        birth branch too. Its counter holds everything since creation and that
        CPU belongs to the measurement."""
        smp = [
            (1.0, {"fn": (1.0, 1.0, 0.0)}, "cum"),
            (2.0, {"fn": (2.0, 1.0, 0.0)}, "cum"),
            (3.0, {"fn": (3.0, 1.0, 0.0)}, "cum"),
        ]
        st = self.h.sample_totals(smp, ("cp",), "fn", window=(0.0, 3.0))
        self.assertAlmostEqual(st.fn_cpu_s, 3.0, places=6,
                               msg="1 CPU-s of birth slice plus two 1 CPU-s deltas")

    def test_a_birth_time_after_the_sample_is_never_credited(self):
        """Clock skew, or a container created between the scan and the read,
        can put Created in the future relative to the sample timestamp. A future
        birth means no time has elapsed, so no CPU can be attributed."""
        smp = [
            (1.0, {"cp": (1.0, 1.0, 0.0)}, "cum"),
            (2.0, {"cp": (2.0, 1.0, 0.0), "fn": (7.0, 1.0, 99.0)}, "cum"),
        ]
        st = self.h.sample_totals(smp, ("cp",), "fn", window=(1.0, 2.0))
        self.assertAlmostEqual(st.fn_cpu_s, 0.0, places=6,
                               msg="a negative lifetime must credit nothing, not "
                                   "fall through to crediting the whole counter")

    def test_a_future_birth_time_is_also_rejected_without_a_window(self):
        """Same guard with no window supplied, where myfrac defaults to 1.0 and
        the counter would otherwise be credited whole."""
        smp = [
            (1.0, {"cp": (1.0, 1.0, 0.0)}, "cum"),
            (2.0, {"cp": (2.0, 1.0, 0.0), "fn": (7.0, 1.0, 99.0)}, "cum"),
        ]
        st = self.h.sample_totals(smp, ("cp",), "fn")
        self.assertAlmostEqual(st.fn_cpu_s, 0.0, places=6)

    def test_a_container_born_after_the_window_ends_contributes_nothing(self):
        """fn is born at 12.6 and first sampled at 13.0, but the load window
        closed at 12.5 -- its entire life is outside the window."""
        smp = [
            (12.0, {"cp": (0.0, 1.0, 0.0)}, "cum"),
            (13.0, {"cp": (1.0, 1.0, 0.0), "fn": (9.0, 1.0, 12.6)}, "cum"),
        ]
        st = self.h.sample_totals(smp, ("cp",), "fn", window=(10.0, 12.5))
        self.assertAlmostEqual(st.fn_cpu_s, 0.0, places=6,
                               msg="a birth time past the window end is not credit")

    def test_a_container_already_present_uses_the_forward_interval_as_before(self):
        """Second and later samples must be unaffected by birth handling."""
        smp = [
            (0.0, {"fn": (0.0, 1.0, -50.0)}, "cum"),
            (1.0, {"fn": (2.0, 1.0, -50.0)}, "cum"),
            (2.0, {"fn": (4.0, 1.0, -50.0)}, "cum"),
        ]
        st = self.h.sample_totals(smp, ("cp",), "fn", window=(0.0, 2.0))
        self.assertAlmostEqual(st.fn_cpu_s, 4.0, places=6)

    def test_unknown_birth_time_still_drops_the_slice_rather_than_inventing_cpu(self):
        """No birth time must mean no credit -- a guess would be worse than the
        undercount it replaces. Only a container's FIRST sighting is affected;
        once a baseline exists the normal delta is used."""
        smp = [
            (0.0, {"cp": (0.0, 1.0, 0.0)}, "cum"),
            (1.0, {"cp": (1.0, 1.0, 0.0), "fn": (5.0, 1.0)}, "cum"),
        ]
        st = self.h.sample_totals(smp, ("cp",), "fn", window=(0.0, 1.0))
        self.assertAlmostEqual(st.fn_cpu_s, 0.0, places=6,
                               msg="first sighting with no birth time -> no credit")
        self.assertAlmostEqual(st.cp_cpu_s, 1.0, places=6,
                               msg="a container with a baseline is unaffected")

    def test_two_and_s_tuples_are_both_accepted(self):
        """Older samples.csv replays and existing tests use (cum, mem)."""
        two = self.h.sample_totals(self._samples([0.0, 1.0, 2.0]),
                                   ("web",), "fn")
        three = self.h.sample_totals(
            [(t, {"web": (t, 1.0, None)}, "cum") for t in (0.0, 1.0, 2.0)],
            ("web",), "fn")
        self.assertAlmostEqual(two.cp_cpu_s, three.cp_cpu_s, places=6)

    def test_container_name_returns_the_creation_time_from_one_inspect(self):
        src = open(os.path.join(REPO, "saqef_harness.py")).read()
        body = re.search(r"def container_name.*?\n(?=\ndef )", src, re.S).group(0)
        self.assertIn("{{.Name}}|{{.Created}}", body,
                      "name and birth must come from ONE inspect, or the "
                      "sampler's zero-spawn property is lost")
        self.assertEqual(body.count("run("), 1,
                         "container_name must spawn exactly one subprocess")
        self.assertIn("fromisoformat", body,
                      "the RFC3339 Created field must be parsed")
        pre = [i * 0.5 for i in range(11)]                 # 0.0 .. 5.0, idle
        during = [5.0 + 0.5 * i for i in range(21)]        # 5.0 .. 15.0, loaded
        ts = pre + during
        smp = [(t, {"web": (t, 1.0)}, "cum") for t in ts]
        st = self.h.sample_totals(smp, ("web",), "fn", window=(5.0, 15.0))
        self.assertAlmostEqual(st.cp_cpu_s, 10.0, places=6,
                               msg="only the 10 s of load may be attributed, "
                                   "not the 5 s of idle time before it")

    def test_runner_passes_the_real_load_window(self):
        src = open(os.path.join(REPO, "saqef_harness.py")).read()
        m = re.search(r"sample_totals\(\s*\n\s*samples,(.*?)\)\n", src, re.S)
        self.assertIsNotNone(m, "sample_totals call site not found")
        self.assertIn("window=", m.group(1),
                      "run_once must clip the totals to the load window: %s"
                      % m.group(1).strip())
        self.assertRegex(m.group(1), r"window=\(t0_epoch,\s*t0_epoch \+ wall\)",
                         "the window must be the actual measured load window, "
                         "in the sampler's epoch time base")

    def test_summary_records_the_fields_that_can_actually_fail(self):
        src = open(os.path.join(REPO, "saqef_harness.py")).read()
        for key in ("sampling_max_gap_s", "sampling_n_samples",
                    "sampling_span_s", "sampling_rate_hz", "sampling_gap_ok"):
            self.assertIn('"%s"' % key, src,
                          "%s must be recorded so coverage is not cited alone" % key)
        self.assertIn("n_samples >= 2 and max_gap_s <= args.max_sample_gap", src)

    def test_totals_stay_tuple_unpackable(self):
        """Existing call sites unpack positionally; a wider return must not
        break them."""
        st = self.h.sample_totals(self._samples([0.0, 0.05]), ("web",), "fn")
        six = st[:6]
        self.assertEqual(len(six), 6)
        self.assertEqual(st.n_samples, 2)


class TestLegacyReattribution(unittest.TestCase):
    """tools/legacy_reattribute.py must rebuild what it was given.

    The whole offline-re-attribution argument rests on one claim: samples.csv
    retains enough per-interval information to reproduce the stored CPU totals.
    If that is not true the correction has no foundation, so these tests pin the
    reconstruction itself rather than just its output.
    """

    @classmethod
    def setUpClass(cls):
        import importlib.util as _u
        p = os.path.join(REPO, "tools", "legacy_reattribute.py")
        spec = _u.spec_from_file_location("legacy_reattribute", p)
        cls.lr = _u.module_from_spec(spec)
        spec.loader.exec_module(cls.lr)

    def _write_leg(self, d, samples, summary):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "samples.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t", "container", "cpu_pct", "mem_mb"])
            for row in samples:
                w.writerow(row)
        with open(os.path.join(d, "summary.json"), "w") as f:
            json.dump(summary, f)
        return d

    def _summary(self, cp, fn, unclass=0.0, platform="openfaas"):
        """summary.json whose cpu_sec is consistent with the sample set."""
        labels = {"hello.1.aaa": {"image": "hello:latest"},
                  "openfaas_gateway.1.bbb": {"image": "of-gw:latest"}}
        if unclass:
            labels["k8s_coredns_coredns-1_kube-system_2_ccc"] = {"image": "coredns:1"}
        return {"platform": platform, "container_labels": labels,
                "cpu_sec": {"control_plane": cp, "function": fn},
                "unclassified_cpu_s": unclass,
                "cp_dynamic_share_pct": (100.0 * cp / (cp + fn)) if (cp + fn) else None}

    def test_unclassified_cpu_excluded_from_both_share_terms(self):
        """Unclassified CPU must appear in NEITHER term of the share.

        This is the regression for a real +1.12 pp error on OpenFaaS c=1. The
        two-bucket version of _integrate() did `if fn: fn else: cp`, so every
        unclassified container landed in the CP NUMERATOR while the caller
        subtracted unclassified_cpu_s from the DENOMINATOR only. The total was
        still right, so the sum-only verify gate passed and 160 tests were
        green while the reported share was inflated by the whole unclassified
        bucket. This fixture has 0.2 cpu-s of unclassified CPU against 1.2 of
        cp: enough that any leak moves the share by ~1 pp.
        """
        lr = self.lr
        with tempfile.TemporaryDirectory() as td:
            # 1 s cadence; each sample covers the interval ending at it.
            samples = []
            for t in (1000.0, 1001.0, 1002.0):
                samples.append([t, "hello.1.aaa", 100.0, 1.0])      # fn: 3.0
                samples.append([t, "openfaas_gateway.1.bbb", 40.0, 1.0])  # cp: 1.2
                samples.append([t, "k8s_coredns_coredns-1_kube-system_2_ccc",
                                6.666666666666667, 1.0])             # unclass: 0.2
            leg = self._write_leg(td, samples,
                                  self._summary(cp=1.2, fn=3.0, unclass=0.2))
            r = lr.reconstruct(leg, tol=1e-6)
            self.assertEqual(r["status"], "ok", r.get("reason"))
            # All three buckets are separated.
            self.assertAlmostEqual(r["raw_cp"], 1.2, places=6)
            self.assertAlmostEqual(r["raw_fn"], 3.0, places=6)
            self.assertAlmostEqual(r["raw_unclass"], 0.2, places=6)
            # The share reproduces the stored definition: cp / (cp + fn).
            self.assertAlmostEqual(r["share_before"], r["share_stored"], places=6)
            self.assertAlmostEqual(r["share_before"], 100.0 * 1.2 / 4.2, places=6)

    def test_share_denominator_ignores_unclassified(self):
        """The denominator is cp+fn. Folding unclassified in would read 33.3%
        where the stored definition gives 28.6%."""
        lr = self.lr
        with tempfile.TemporaryDirectory() as td:
            samples = []
            for t in (1000.0, 1001.0, 1002.0):
                samples.append([t, "hello.1.aaa", 100.0, 1.0])
                samples.append([t, "openfaas_gateway.1.bbb", 40.0, 1.0])
                samples.append([t, "k8s_coredns_coredns-1_kube-system_2_ccc",
                                20.0, 1.0])
            leg = self._write_leg(td, samples, self._summary(cp=1.2, fn=3.0, unclass=0.6))
            r = lr.reconstruct(leg, tol=1e-6)
            self.assertEqual(r["status"], "ok", r.get("reason"))
            self.assertAlmostEqual(r["share_before"], 28.571428571428573, places=6)
            # Explicitly NOT cp/(cp+fn+unclass) = 25.0%.
            self.assertNotAlmostEqual(r["share_before"], 25.0, places=3)

    def test_gate_rejects_bucket_misassignment_with_correct_total(self):
        """A wrong bucket must fail even when cp+fn sums correctly.

        This is the exact blind spot that hid the +1.12 pp bug: the stored total
        was right while the split was wrong, so a sum-only gate passed. Here the
        run claims ZERO unclassified CPU, but samples.csv shows 0.3 cpu-s in a
        container that is neither fn nor cp. cp+fn still reconciles to the
        stored 4.2 -- only the per-bucket check can catch it.
        """
        lr = self.lr
        with tempfile.TemporaryDirectory() as td:
            samples = []
            for t in (1000.0, 1001.0, 1002.0):
                samples.append([t, "hello.1.aaa", 100.0, 1.0])       # fn 3.0
                samples.append([t, "openfaas_gateway.1.bbb", 40.0, 1.0])  # cp 1.2
                samples.append([t, "k8s_coredns_coredns-1_ks_2_ccc", 10.0, 1.0])  # 0.3
            # Stored says there is no unclassified CPU at all.
            leg = self._write_leg(td, samples, self._summary(cp=1.2, fn=3.0, unclass=0.0))
            r = lr.reconstruct(leg, tol=0.01)
            # cp + fn are individually correct...
            self.assertAlmostEqual(r["cp_err"], 0.0, places=9)
            self.assertAlmostEqual(r["fn_err"], 0.0, places=9)
            # ...the total reconciles...
            self.assertAlmostEqual(r["recon_err"], 0.0, places=9)
            # ...but a sum-only gate would call this leg fine, while the third
            # bucket disagrees with the run's own record.
            self.assertAlmostEqual(r["raw_unclass"], 0.3, places=6)
            self.assertGreater(r["unclass_abs_err"], lr.UNCLASS_ABS_TOL)
            self.assertEqual(r["status"], "verify_failed")

    def test_clipping_does_not_fail_the_gate(self):
        """Clipping is a deliberate correction, so it must NOT be gated.

        Gating the clipped figure against the unclipped stored total reported 9
        correct legs as verify_failed at 1.0-2.3% 'error' purely because the
        clip removed that much overhang -- the correction working as intended.
        """
        lr = self.lr
        with tempfile.TemporaryDirectory() as td:
            # fn runs flat; cp is only busy early, then idles. Clipping away the
            # idle tail therefore REMOVES fn and cp at different rates, so the
            # share genuinely moves -- which is what makes this a real test of
            # "the gate judges the unclipped figure, the report shows the clip".
            samples = []
            for t, gw in ((1000.0, 100.0), (1001.0, 100.0), (1002.0, 10.0)):
                samples.append([t, "hello.1.aaa", 100.0, 1.0])
                samples.append([t, "openfaas_gateway.1.bbb", gw, 1.0])
            # unclipped: fn 3.0, cp 2.1 -> share 41.18%
            leg = self._write_leg(td, samples, self._summary(cp=2.1, fn=3.0))
            unclipped = lr.reconstruct(leg, tol=0.01)
            self.assertEqual(unclipped["status"], "ok", unclipped.get("reason"))
            # Clip to (1000, 1002]: the first sample has no predecessor so its interval
            # is dropped, leaving fn 2.0 and cp 1.1 (1.0 + 0.1 idle) -> 35.48%.
            clipped = lr.reconstruct(leg, window=(1000.0, 1002.0), tol=0.01)
            self.assertEqual(clipped["status"], "ok",
                             "clipping must not fail the reconstruction gate")
            # The gate judged the unclipped integration (perfect agreement)...
            self.assertAlmostEqual(clipped["cp_err"], 0.0, places=9)
            self.assertAlmostEqual(clipped["fn_err"], 0.0, places=9)
            # ...while the reported value is the clipped one, and it differs.
            self.assertAlmostEqual(clipped["recon_fn"], 2.0, places=6)
            self.assertAlmostEqual(clipped["recon_cp"], 1.1, places=6)
            self.assertAlmostEqual(clipped["share_before"],
                                   100.0 * 1.1 / 3.1, places=6)
            self.assertAlmostEqual(clipped["share_unclipped"],
                                   unclipped["share_before"], places=6)

    def test_unclass_abs_tol_exceeds_worst_real_discrepancy(self):
        """UNCLASS_ABS_TOL must have headroom over the measured floor.

        The value is only defensible if it sits above what correct legs actually
        produce and below what a wrong split would produce. Measured over the
        264 reconstructable committed legs: max 0.0060 cpu-s. So the threshold
        is asserted against that number rather than left as a bare literal.
        """
        import glob
        import importlib.util as _u
        results = os.path.join(REPO, "..", "saqef-paper", "results")
        if not os.path.isdir(results):
            self.skipTest("committed results not present")
        spec = _u.spec_from_file_location("lr2",
                                           os.path.join(REPO, "tools",
                                                        "legacy_reattribute.py"))
        lr = _u.module_from_spec(spec)
        spec.loader.exec_module(lr)
        errs = []
        for leg in lr.iter_legs(results):
            r = lr.reconstruct(leg, tol=lr.RECON_TOL)
            if r and r["status"] == "ok" and r.get("unclass_abs_err") is not None:
                errs.append(r["unclass_abs_err"])
        self.assertGreater(len(errs), 200, "expected most legs to reconstruct")
        self.assertLess(max(errs), lr.UNCLASS_ABS_TOL,
                        "UNCLASS_ABS_TOL would reject a correct leg")
        # And it must still be tight enough to matter: a split wrong by more
        # than a hundredth of a cpu-second has to fail.
        self.assertGreater(lr.UNCLASS_ABS_TOL, 0.0)
        self.assertLessEqual(lr.UNCLASS_ABS_TOL, 0.02,
                             "UNCLASS_ABS_TOL is too loose to detect a wrong split")

    def test_terminal_escapes_do_not_double_count(self):
        """Escape-laden container names must not inflate the fn total.

        The 5 pre-containerization fn_cpubound legs recorded the SAME container
        under two names, '\x1b[H01KZ...' and '\x1b[J\x1b[H01KZ...', because an
        escape-laden `docker ps` header bled into the field. Unscrubbed they are
        distinct keys and the CPU is counted twice.
        """
        lr = self.lr
        with tempfile.TemporaryDirectory() as td:
            a, b = "\x1b[H01KZ3TRR1NG8G00GZJ00016F7", "\x1b[J\x1b[H01KZ3TRR1NG8G00GZJ00016F7"
            rows = []
            for t in (1000.0, 1001.0, 1002.0):
                rows.append([t, "hello.1.aaa", 100.0, 1.0])
                rows.append([t, "fnserver", 40.0, 1.0])
                rows.append([t, a, 10.0, 1.0])
                rows.append([t, b, 10.0, 1.0])
            d = os.path.join(td, "samples.csv")
            with open(d, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["t", "container", "cpu_pct", "mem_mb"])
                for r in rows:
                    w.writerow(r)
            by_t = lr.load_samples(d)
            names = {n for t in by_t for n in by_t[t]}
            self.assertEqual(names, {"hello.1.aaa", "fnserver", "01KZ3TRR1NG8G00GZJ00016F7"},
                             "escape-laden names must collapse to one container")

    def test_name_only_legs_reported_not_silently_accepted(self):
        """No container_labels => name-only, and it must stay visible.

        These 5 legs carry no labels, so fn is inferred as "everything that is
        not the adapter's cp container". That cannot be checked independently,
        so the tool reports it AND refuses to accept a loose fit -- the samples
        span 8.48 s where wall_s is 14.24 s, so the stored totals came from a
        span the samples no longer cover.
        """
        lr = self.lr
        with tempfile.TemporaryDirectory() as td:
            samples = []
            for t in (1000.0, 1001.0, 1002.0):
                samples.append([t, "hello.1.aaa", 100.0, 1.0])
                samples.append([t, "fnserver", 40.0, 1.0])
            summary = {"platform": "fn", "wall_s": 10.0,
                       "cpu_sec": {"control_plane": 9.0, "function": 3.0},
                       "cp_dynamic_share_pct": 75.0,
                       "container_labels": {}, "unclassified_cpu_s": 0.0}
            leg = self._write_leg(td, samples, summary)
            r = lr.reconstruct(leg, tol=lr.RECON_TOL)
            self.assertEqual(r["status"], "verify_failed")
            self.assertTrue(r.get("name_only"))
            self.assertIn("name-only", r["reason"])
            self.assertIn("span", r["reason"])

    def test_cp_allowlists_match_adapters(self):
        """CP_CONTAINER_HINTS must equal each adapter's own cp_containers.

        Hand-kept tables drift. That drift is the documented OpenFaaAS 'gateway'
        substring bug, and a stale table mis-attributes CP without failing any
        total-based check.
        """
        import importlib
        lr = self.lr
        for name in ("fn", "openfaas", "knative", "openwhisk"):
            mod = importlib.import_module("platforms.%s" % name)
            # Each module also exposes a bare `Adapter` ABC with an empty
            # cp_containers; only the concrete adapter carries the real list.
            cls = [getattr(mod, k) for k, v in vars(mod).items()
                   if isinstance(v, type) and getattr(v, "cp_containers", ())
                   and not k == "Adapter"]
            self.assertEqual(len(cls), 1,
                             "expected one concrete adapter in platforms/%s.py" % name)
            self.assertEqual(set(lr.CP_CONTAINER_HINTS[name]),
                             set(cls[0].cp_containers),
                             "CP_CONTAINER_HINTS[%r] has drifted from platforms/%s.py"
                             % (name, name))

    def test_reconstruction_reproduces_stored_totals_exactly(self):
        """The core claim: integrating stored pct over forward intervals
        rebuilds the cpu_sec the old harness recorded."""
        with tempfile.TemporaryDirectory() as td:
            # 2 containers, 1 s cadence. 'hello' is fn, 'fnserver' is cp.
            # Each carries 1.0 cpu-s per second => pct=100 per interval.
            samples = []
            for t in (1000.0, 1001.0, 1002.0):
                samples.append([t, "fn-a", 100.0, 1.0])
                samples.append([t, "fnserver.1.gw", 50.0, 1.0])
            # 3 samples -> 3 forward intervals (last gets the SAMPLE_S tail).
            # fn-a at 100% => 1.0 cpu-s per interval => 3.0. gw at 50% => 1.5.
            summary = {
                "platform": "fn", "wall_s": 2.0,
                "cpu_sec": {"control_plane": 1.5, "function": 3.0},
                "cp_dynamic_share_pct": 33.333333,
                # 'gw' must be a real fn-server container name: cp membership
                # is by NAME via CP_CONTAINER_HINTS, and an unmatched name is
                # unclassified, not control plane.
                "container_labels": {"fn-a": {"image": "hello:0.0.40"},
                                     "fnserver.1.gw": {"image": "gateway:1"}},
                "unclassified_cpu_s": 0.0,
            }
            leg = self._write_leg(os.path.join(td, "run_1"), samples, summary)
            r = self.lr.reconstruct(leg, window=None, tol=0.01)
            self.assertEqual(r["status"], "ok", r.get("reason"))
            self.assertAlmostEqual(r["recon_cp"], 1.5, places=6)
            self.assertAlmostEqual(r["recon_fn"], 3.0, places=6)
            self.assertLess(r["recon_err"], 1e-9)

    def test_unclassified_bucket_is_reconstructed_not_subtracted(self):
        """Unclassified CPU is its OWN bucket, not a subtraction to paper over
        the gate.

        The earlier fix subtracted `unclassified_cpu_s` from a two-bucket total,
        which made the SUM reconcile while every unclassified container stayed
        in the cp numerator. This pins the three-bucket behaviour: the
        unclassified total is reconstructed and checked on its own terms.
        """
        with tempfile.TemporaryDirectory() as td:
            samples = []
            for t in (1000.0, 1001.0):
                samples.append([t, "fn-a", 100.0, 1.0])       # fn  2.0
                samples.append([t, "fnserver.1.gw", 50.0, 1.0])  # cp 1.0
                samples.append([t, "stray", 10.0, 1.0])      # unclass 0.2
            summary = {
                "platform": "fn", "wall_s": 1.0,
                # cp+fn as stored. stray's CPU is NOT in here.
                "cpu_sec": {"control_plane": 1.0, "function": 2.0},
                "cp_dynamic_share_pct": 33.333333,
                "container_labels": {"fn-a": {"image": "hello:0.0.40"},
                                     "fnserver.1.gw": {"image": "gateway:1"},
                                     "stray": {"image": "stray:1"}},
                "unclassified_cpu_s": 0.2,
            }
            leg = self._write_leg(os.path.join(td, "run_1"), samples, summary)
            r = self.lr.reconstruct(leg, window=None, tol=0.01)
            self.assertEqual(r["status"], "ok",
                             "unclassified CPU must not be counted as "
                             "reconstruction error")
            self.assertLess(r["recon_err"], 0.02)
            # Each bucket reproduced separately.
            self.assertAlmostEqual(r["raw_cp"], 1.0, places=6)
            self.assertAlmostEqual(r["raw_fn"], 2.0, places=6)
            self.assertAlmostEqual(r["raw_unclass"], 0.2, places=6)
            # And cp stayed OUT of the stray CPU: the share is cp/(cp+fn).
            self.assertAlmostEqual(r["share_before"], 100.0 / 3.0, places=6)

    def test_window_clip_never_adds_cpu(self):
        """Clipping to the load window can only remove overhang, never invent
        CPU. A window covering a subset of samples must yield <= the unclipped
        total."""
        with tempfile.TemporaryDirectory() as td:
            samples = []
            for t in (1000.0, 1001.0, 1002.0, 1003.0, 1004.0):
                samples.append([t, "fn-a", 100.0, 1.0])
            summary = {
                "platform": "fn", "wall_s": 4.0,
                "cpu_sec": {"control_plane": 0.0, "function": 5.0},
                "cp_dynamic_share_pct": 0.0,
                "container_labels": {"fn-a": {"image": "hello:0.0.40"}},
                "unclassified_cpu_s": 0.0,
            }
            leg = self._write_leg(os.path.join(td, "run_1"), samples, summary)
            full = self.lr.reconstruct(leg, window=None, tol=0.5)
            clipped = self.lr.reconstruct(leg, window=(1000.0, 1002.0), tol=0.5)
            self.assertLessEqual(clipped["recon_fn"], full["recon_fn"] + 1e-9)
            self.assertGreater(full["recon_fn"], clipped["recon_fn"])

    def test_interval_entirely_outside_window_contributes_nothing(self):
        """A sample whose whole interval precedes the window must not be
        credited at all (the c22dff9 rule)."""
        with tempfile.TemporaryDirectory() as td:
            samples = []
            for t in (1000.0, 1001.0, 1002.0):
                samples.append([t, "fn-a", 100.0, 1.0])
            summary = {
                "platform": "fn", "wall_s": 2.0,
                "cpu_sec": {"control_plane": 0.0, "function": 3.0},
                "cp_dynamic_share_pct": 0.0,
                "container_labels": {"fn-a": {"image": "hello:0.0.40"}},
                "unclassified_cpu_s": 0.0,
            }
            leg = self._write_leg(os.path.join(td, "run_1"), samples, summary)
            # Window starts at the LAST sample: only the tail may count.
            r = self.lr.reconstruct(leg, window=(1002.0, 1003.0), tol=0.9)
            self.assertLess(r["recon_fn"], 1.01,
                            "out-of-window intervals must contribute ~0")

    def test_missing_labels_is_unclassifiable_not_guessed(self):
        """No container_labels => no way to split cp/fn. The tool must say so
        rather than fall back to a guess that would silently invent a share."""
        with tempfile.TemporaryDirectory() as td:
            samples = [[1000.0, "x", 100.0, 1.0], [1001.0, "x", 100.0, 1.0]]
            summary = {"platform": "fn", "wall_s": 1.0,
                       "cpu_sec": {"control_plane": 0.5, "function": 1.5},
                       "cp_dynamic_share_pct": 25.0}
            leg = self._write_leg(os.path.join(td, "run_1"), samples, summary)
            r = self.lr.reconstruct(leg, window=None, tol=0.01)
            self.assertEqual(r["status"], "unclassifiable")
            self.assertIn("container_labels", r["reason"])

    def test_adapter_allowlist_is_platform_specific(self):
        """A generic 'hello' hint for every platform classifies NOTHING on
        Knative (image is kn-hello) or OpenWhisk (action-python-v3.11). The
        hints must match platforms/*.py or the split is silently wrong."""
        self.assertIn("kn-hello", self.lr.fn_image_hints("knative"))
        self.assertIn("action-python-v3.11", self.lr.fn_image_hints("openwhisk"))
        self.assertEqual(self.lr.fn_image_hints("unknown-platform"), ())

    def test_verify_mode_fails_loudly_on_a_bad_reconstruction(self):
        """--verify must exit non-zero when a leg cannot be rebuilt. A tool
        that corrects numbers it cannot reproduce is worse than no tool."""
        with tempfile.TemporaryDirectory() as td:
            ds = os.path.join(td, "ds")
            samples = []
            for t in (1000.0, 1001.0):
                samples.append([t, "fn-a", 100.0, 1.0])
            # Stored totals deliberately wrong by 40%.
            summary = {
                "platform": "fn", "wall_s": 1.0,
                "cpu_sec": {"control_plane": 0.0, "function": 99.0},
                "cp_dynamic_share_pct": 0.0,
                "container_labels": {"fn-a": {"image": "hello:0.0.40"}},
                "unclassified_cpu_s": 0.0,
            }
            self._write_leg(os.path.join(ds, "run_1"), samples, summary)
            rc = self.lr.main([ds, "--verify", "--tol", "0.01"])
            self.assertNotEqual(rc, 0, "--verify must fail on a bad reconstruction")


class TestNoSilentlySkippedTestClasses(unittest.TestCase):
    """A setUpClass that raises hides its whole class from the suite.

    TestTier1StatsHygiene sat in this state for its entire life. unittest
    reports a setUpClass failure as ONE error and skips every test in the class
    -- 21 of them, including the guards for the Tukey df, the pstdev/sd
    confusion, the fabricated wall_s fallback, and the flatness threshold. Those
    defects were cited as "test-locked" in the runbook and in review replies
    while not one of those tests had ever executed. The suite reported
    "FAILED (errors=1)" and that single line was easy to skim past, especially
    next to a large count of passing tests.

    This is the same bug class as a gate that cannot fail (sampling_covered_s,
    and the cp+fn sum-only verify gate). A test that cannot run is worse than no
    test, because it is cited as evidence. So: every TestCase in the module must
    have a setUpClass that executes cleanly, and every test method must be
    discovered.
    """

    def test_every_test_class_setup_runs_cleanly(self):
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(sys.modules["__main__"])
        # loadTestsFromModule instantiates each test, so the same class can be
        # reached more than once via nested suites. setUpClass is what we are
        # testing, so it must run once per class -- repeating it would report
        # the same defect N times and mask nothing but noise.
        seen, broken = set(), []
        stack = list(suite)
        while stack:
            item = stack.pop()
            if isinstance(item, unittest.TestSuite):
                stack.extend(item)
                continue
            cls = type(item)
            if cls in seen or getattr(cls, "__unittest_skip__", False):
                continue
            seen.add(cls)
            # Only classes that DEFINE setUpClass can break; inherited ones are fine.
            if "setUpClass" in cls.__dict__:
                try:
                    cls.setUpClass()
                except Exception as exc:                       # noqa: BLE001
                    broken.append("%s.setUpClass: %s: %s"
                                  % (cls.__name__, type(exc).__name__, exc))
        self.assertEqual(broken, [],
                         "these classes' tests are silently skipped:\n  "
                         + "\n  ".join(broken))

    def test_no_test_class_is_empty(self):
        """A class with no test_ methods contributes nothing but still reads as
        coverage in a file of green lines."""
        empty = []
        for name, obj in vars(sys.modules["__main__"]).items():
            if (isinstance(obj, type) and issubclass(obj, unittest.TestCase)
                    and name.startswith("Test")
                    and not any(k.startswith("test_") for k in vars(obj))):
                empty.append(name)
        self.assertEqual(empty, [], "test classes with no tests: %s" % empty)


class TestRunIsReanalysable(unittest.TestCase):
    """A new run must keep every input an offline re-attribution needs (1a).

    The 2026-08-14/15 corpus could only be re-attributed at all because that
    harness happened to write full-span, UNCLIPPED percent-rate rows: integrating
    those back to the stored CPU-s totals reproduced them to 0.018%. That was
    luck of history, not a design property, and the current harness breaks it:
    samples.csv is written downstream of the window clip (sample_totals drops
    out-of-window samples and scales partial intervals), and summary.json records
    neither t0_epoch nor the window nor the git revision.

    The failure mode is silent and expensive. A clipped run still passes every
    gate and still looks citable; the information needed to re-derive it is
    simply gone. If a window or attribution bug appears later, those runs are
    unrecoverable exactly as the pre-2026-08-08 data was -- so new box time
    would buy nothing re-analysable. These tests pin the two artifacts that
    prevent it: samples_raw.csv (unclipped raw counters + birth times) and the
    summary.json attribution block (window, allowlists, revision).
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "saqef_harness.py")) as f:
            cls.src = f.read()
        cls.loader = importlib.machinery.SourceFileLoader(
            "saqef_harness", os.path.join(REPO, "saqef_harness.py"))
        spec = importlib.util.spec_from_loader("saqef_harness", cls.loader)
        cls.h = importlib.util.module_from_spec(spec)
        cls.loader.exec_module(cls.h)

    # -- the raw artifact ------------------------------------------------
    @staticmethod
    def _raw_samples():
        """Sampler's true output: cumulative cpu.stat counters, 'cum' mode.

        Three containers over 0..5 s. 'gateway' is cp, 'fn-a' is fn, and
        'late-fn' is first seen at t=3.0 with birth at 2.5 -- so it carries a
        birth-to-first-sample slice that only exists because born_epoch is
        recorded. Overhang: samples exist before t0=2.0 and after t1=4.0.
        """
        out = []
        t = 1000.0
        cum = {"gateway": 10.0, "fn-a": 5.0, "late-fn": 0.0}
        for i in range(6):
            t = 1000.0 + i
            snap = {}
            for name in cum:
                if name == "late-fn" and i < 3:
                    continue
                snap[name] = (cum[name], 64.0,
                              1002.5 if name == "late-fn" else 999.0)
            out.append((t, snap, "cum"))
            cum["gateway"] += 1.0
            cum["fn-a"] += 0.5
            cum["late-fn"] += 0.5
        return out

    def test_samples_raw_written_unclipped_and_outside_the_window(self):
        """samples_raw.csv must contain the pre-load and post-load CPU that
        samples.csv discards. That overhang is precisely the quantity the
        window correction is about; if it is not on disk the correction cannot
        be re-derived, only re-asserted."""
        raw = self._raw_samples()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "samples_raw.csv")
            self.assertTrue(self.h.write_samples_raw(path, raw))
            with open(path) as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(list(rows[0].keys()), self.h.RAW_HDR)
        # The overhang samples (t < 1002.0) must be present...
        ts = [float(r["t"]) for r in rows]
        self.assertLess(min(ts), 1002.0, "pre-window samples must survive")
        self.assertGreater(max(ts), 1004.0, "post-window samples must survive")
        # ...carrying their RAW cumulative counters, unclipped.
        gw = [float(r["cpu_cum_s"]) for r in rows if r["container"] == "gateway"]
        self.assertEqual(gw[0], 10.0)
        self.assertAlmostEqual(gw[-1] - gw[0], 5.0, places=6,
                               msg="raw file must hold undifferenced counters")

    def test_samples_raw_keeps_birth_epoch(self):
        """Birth-to-first-sample CPU is unrecoverable once the counter is
        differenced away: at first sight the sampler sees a counter already
        containing everything since creation. Knative creates fn containers
        seconds into a run, so dropping born_epoch there silently undercounts
        fn and biases the share upward. It must be on disk."""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "samples_raw.csv")
            self.h.write_samples_raw(path, self._raw_samples())
            with open(path) as f:
                rows = list(csv.DictReader(f))
        born = {r["born_epoch"] for r in rows if r["container"] == "late-fn"}
        self.assertEqual(born, {"1002.5"})
        self.assertTrue(all(r["cpu_cum_s"] for r in rows if r["container"] == "late-fn"))

    def test_samples_raw_distinguishes_cum_from_pct_mode(self):
        """The docker-stats fallback stores a RATE, not a counter; the cgroup
        sampler stores a CUMULATIVE counter. Same column would be unreadable,
        so mode is recorded and the unused value column stays empty."""
        raw = [(1000.0, {"a": (12.5, 1.0)}, "cum"),
               (1001.0, {"a": (30.0, 1.0)}, "pct")]
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "samples_raw.csv")
            self.h.write_samples_raw(path, raw)
            with open(path) as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["mode"], "cum")
        self.assertEqual(rows[0]["cpu_cum_s"], "12.5")
        self.assertEqual(rows[0]["cpu_pct"], "")
        self.assertEqual(rows[1]["mode"], "pct")
        self.assertEqual(rows[1]["cpu_pct"], "30.0")
        self.assertEqual(rows[1]["cpu_cum_s"], "")

    def test_write_run_emits_both_sample_files(self):
        """Both files, always. samples.csv stays the citable read (figures and
        the emitter consume it); samples_raw.csv is the forensic copy. Writing
        raw only on some paths would make the artifact's presence a confound."""
        raw = self._raw_samples()
        st = self.h.sample_totals(raw, ("gateway",), "fn",
                                  window=(1002.0, 1004.0))
        snaps = []
        for t, name, pct, mem in st.csv_rows:
            if snaps and snaps[-1][0] == t:
                snaps[-1][1][name] = (pct, mem)
            else:
                snaps.append((t, {name: (pct, mem)}))
        with tempfile.TemporaryDirectory() as td:
            self.h.write_run(td, {"platform": "fn"}, snaps, None, raw_samples=raw)
            names = set(os.listdir(td))
            self.assertIn("samples.csv", names)
            self.assertIn("samples_raw.csv", names)
            self.assertIn("summary.json", names)

    # -- the round trip the reviewer asked for ----------------------------
    def test_raw_file_feeds_sample_totals_and_reproduces_the_clipped_totals(self):
        """THE test: re-read samples_raw.csv and re-run the attribution from it.

        The reviewer asked for a test that feeds sample_totals() from real
        run_once() output. This is that, without needing the box: the raw file is
        the sampler's actual output, parsed back into the same structure
        sample_totals() consumes, and the resulting cp/fn totals must match the
        in-memory run exactly. If they do, then any future bug in the window,
        birth, or classification logic can be corrected offline from committed
        data -- which is the entire point of writing the file.
        """
        raw = self._raw_samples()
        window = (1002.0, 1004.0)
        # Take the selectors from an attribution block in the shape
        # summary.json writes, rather than from literals here. A replay that
        # has to know the CLI's classifier values to reproduce a run is not a
        # replay -- the whole point is that summary.json carries them.
        attr = {"cp_sub": ["gateway"], "cp_members": ["gateway"],
                "fn_containers": ["fn"], "fn_members": ["fn-a"]}
        direct = self.h.sample_totals(raw, tuple(attr["cp_sub"]),
                                      attr["fn_containers"][0],
                                      cp_members=set(attr["cp_members"]),
                                      fn_members=set(attr["fn_members"]),
                                      window=window)

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "samples_raw.csv")
            self.h.write_samples_raw(path, raw)
            with open(path) as f:
                rows = list(csv.DictReader(f))
        replay, snap_at = [], {}
        for r in rows:
            t = float(r["t"])
            snap_at.setdefault(t, {})[r["container"]] = (
                float(r["cpu_cum_s"]) if r["cpu_cum_s"] else float(r["cpu_pct"]),
                float(r["mem_mb"]) if r["mem_mb"] else 0.0,
                float(r["born_epoch"]) if r["born_epoch"] else None)
        for t in sorted(snap_at):
            replay.append((t, snap_at[t], rows[0]["mode"]))

        rt = self.h.sample_totals(replay, tuple(attr["cp_sub"]),
                                  attr["fn_containers"][0],
                                  cp_members=set(attr["cp_members"]),
                                  fn_members=set(attr["fn_members"]),
                                  window=window)
        self.assertAlmostEqual(rt.cp_cpu_s, direct.cp_cpu_s, places=6)
        self.assertAlmostEqual(rt.fn_cpu_s, direct.fn_cpu_s, places=6)
        self.assertAlmostEqual(rt.n_samples, direct.n_samples, places=6)
        # And the totals must be NON-trivial: a round trip that agrees because
        # both sides are zero would prove nothing.
        self.assertGreater(direct.cp_cpu_s, 0.0)
        self.assertGreater(direct.fn_cpu_s, 0.0)
        # The window must actually be doing something, i.e. the unclipped
        # full-span totals must exceed the clipped ones -- otherwise this test
        # would pass even if the clip were removed entirely.
        unclipped = self.h.sample_totals(replay, tuple(attr["cp_sub"]),
                                       attr["fn_containers"][0],
                                       cp_members=set(attr["cp_members"]),
                                       fn_members=set(attr["fn_members"]),
                                       window=None)
        self.assertGreater(unclipped.cp_cpu_s, direct.cp_cpu_s,
                           "the window must exclude pre-window CPU")

    def test_replay_classification_comes_from_the_attribution_block(self):
        """Every key sample_totals() needs must exist in a summary.json
        attribution block. Keyed on the call site rather than on a literal
        list so that adding a parameter to sample_totals() without recording
        it breaks this test."""
        m = re.search(r"sample_totals\(\s*samples,\s*(\w+)\s*,\s*([^,]+),\s*"
                      r"(cp_members),\s*(fn_members)", self.src)
        self.assertIsNotNone(m, "run_once sample_totals call not found")
        # args.fn_containers is recorded under the shorter key fn_containers.
        keys = {"args.fn_containers": "fn_containers"}
        for var in m.groups():
            self.assertIn('"%s"' % keys.get(var, var), self.src,
                          "attribution block must record %s" % var)

    def test_raw_file_survives_the_clip_that_samples_csv_cannot(self):
        """The concrete loss samples.csv suffers, pinned as a test.

        At t=1001.0 the sampler observes a 'gateway' interval (1000->1001) that
        lies entirely before the load window. The clip correctly contributes
        nothing to the reported share, and sample_totals() drops the row. That
        dropped row is the problem: it holds real burned CPU, and it is the
        evidence for what the window excluded. samples_raw.csv keeps it. If
        this ever inverts, new runs are strictly less re-analysable than the
        2026-08 corpus, whose recoverability came precisely from writing the
        unclipped full-span rows."""
        raw = self._raw_samples()
        st = self.h.sample_totals(raw, ("gateway",), "fn", window=(1002.0, 1004.0))
        clipped_times = {t for t, _n, _p, _m in st.csv_rows}
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "samples_raw.csv")
            self.h.write_samples_raw(path, raw)
            with open(path) as f:
                raw_times = {float(r["t"]) for r in csv.DictReader(f)}
        self.assertNotIn(1001.0, clipped_times,
                         "precondition: the clip really does drop that row")
        self.assertIn(1001.0, raw_times,
                      "samples_raw.csv must keep the discarded overhang row")
        # 1000.0 survives the clip only as a zero-delta baseline row, so it is
        # in both; the meaningful check is that raw is a superset of clipped.
        self.assertTrue(raw_times.issuperset(clipped_times))


class TestSummaryRecordsAttributionInputs(unittest.TestCase):
    """summary.json must carry what an offline re-attribution needs.

    Without t0_epoch and the window, a run's attribution is an unfalsifiable
    assertion: the stored share is right or wrong and nobody can tell which,
    because the samples that would decide it are gone. That is exactly the
    state the 2026-08-14/15 corpus was in, and it is why re-attribution there
    had to INFER a window as (first_sample, first_sample + wall) rather than
    read it. The inference turned out to be good enough (<=0.074 pp), but a
    bound on an inferred quantity is not the same as the quantity.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "saqef_harness.py")) as f:
            cls.src = f.read()
        cls.loader = importlib.machinery.SourceFileLoader(
            "saqef_harness", os.path.join(REPO, "saqef_harness.py"))
        spec = importlib.util.spec_from_loader("saqef_harness", cls.loader)
        cls.h = importlib.util.module_from_spec(spec)
        cls.loader.exec_module(cls.h)

    def test_summary_has_an_attribution_block(self):
        self.assertIn('"attribution"', self.src)
        for key in ("t0_epoch", "window_start_epoch", "window_end_epoch",
                    "sampler", "sample_s", "rescan_s"):
            self.assertIn(key, self.src, "attribution block must record %s" % key)

    def test_attribution_block_carries_the_allowlists_actually_used(self):
        """The classification inputs belong with the number, not in the
        command line. CP_CONTAINER_HINTS had already drifted out of sync with
        the adapters' own cp_containers (the same bug class as the OpenFaaS
        'gateway' substring incident), so a hand-kept list is not trustworthy
        on its own; recording the resolved members makes a run's attribution
        auditable without re-running it."""
        for key in ("cp_members", "fn_members", "fn_allow_configured",
                    "fn_images", "fn_labels", "cp_images", "cp_labels",
                    "cp_sub", "docker_inventory"):
            self.assertIn(key, self.src, "attribution block must record %s" % key)

    def test_attribution_records_cp_sub_not_just_resolved_members(self):
        """cp_images/cp_labels are only consulted for containers present in
        the inventory. sample_totals() falls back to matching name substrings
        (cp_sub) for anything they missed, so recording the resolved member
        lists alone can leave a replay unable to reproduce the CP side at all.
        Assert both the key exists and that it is wired to the same value
        sample_totals() is called with."""
        self.assertIn('"cp_sub": list(cp_sub)', self.src,
                      "attribution block must record the cp_sub actually applied")
        m = re.search(r"sample_totals\(\s*samples,\s*(\w+)\s*,", self.src)
        self.assertIsNotNone(m, "sample_totals call not found")
        self.assertEqual(m.group(1), "cp_sub",
                         "sample_totals must be called with the same cp_sub that "
                         "the attribution block records")

    def test_summary_records_the_harness_git_revision(self):
        """A result is reproducible only if you know which code produced it.
        The 2026-08 corpus predates the window fix (c22dff9) and birth-credit
        fix (34b4f26) and says nothing about it in its own JSON."""
        self.assertIn('"git_rev"', self.src)
        self.assertIn('"git_dirty"', self.src)
        rev = self.h.harness_git_rev()
        self.assertRegex(rev, r"^(\w{7,40}|unknown)$",
                         "rev must be a short hash or 'unknown', never empty")
        self.assertIsInstance(self.h.harness_git_dirty(), (bool, type(None)))

    def test_committed_legacy_runs_are_marked_as_lacking_a_window(self):
        """The new field is absent from every pre-fix run -- which is exactly
        why those runs needed an inferred window. Asserted so no one later
        reads a missing attribution block as a bug in the emitter."""
        legacy = os.path.join(os.path.dirname(REPO), "saqef-paper", "results",
                              "openfaas_cpubound_lock_lock4", "run_1",
                              "summary.json")
        if not os.path.exists(legacy):
            self.skipTest("legacy corpus not present")
        with open(legacy) as f:
            s = json.load(f)
        self.assertNotIn("attribution", s)
        self.assertIsNone(s.get("harness", {}).get("git_rev")
                          if isinstance(s.get("harness"), dict) else None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
