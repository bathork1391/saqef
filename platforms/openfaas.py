"""OpenFaaS adapter (Docker Swarm, hand-written stack in OPENFAAS_DEPLOY/).

Stack deploy/scale delegate to the proven run_openfaas.sh functions. The
FUNCTION service ('hello') is deployed OUTSIDE the stack and is a manual
prerequisite -- exactly as in the pre-refactor tooling; 'docker stack rm
openfaas' does NOT remove it, which is why teardown() must rm it explicitly
(the ordering in AGENTS.md: stack rm, THEN service rm hello).

deploy_function() automates the documented manual prerequisite (OPENFAAS_SETUP.md
§4): it creates the 'hello' swarm service from the cached hello:latest image on
the openfaas_functions overlay network, labeled `com.openfaas.function=hello`
so the faas-swarm provider registers it with the gateway. (Prior sessions used
'faas-cli deploy' for this, but that requires the faas-cli template store --
'stat ./template/python3/template.yml' -- which is exactly the build machinery
'docker service create' skips; the resulting service is equivalent.)

Isolation policy: OpenFaaS sessions forbid Fn's 'fnserver' container.
"""

import os
import subprocess
import time

from platforms.base import (Adapter, IsolationPolicy, container_names,
                            repo_script, wait_containers, wait_for_url)


class OpenFaaSAdapter(Adapter):
    name = "openfaas"
    label = "OpenFaaS"
    url = "http://127.0.0.1:8080/function/hello"
    auth = ""                        # gateway 0.8.3 predates HTTP basic auth
    # Prefixed with the swarm stack name ("openfaas_"), NOT bare service names:
    # a bare "gateway" substring collides with Knative's "kourier-gateway" /
    # "3scale-kourier-gateway" pod containers. k3s stays resident on this box
    # across every platform's session (it is "the substrate", see
    # platforms/knative.py), so a bare "gateway" match silently folds leftover
    # Kourier CPU into OpenFaaS's control-plane bucket whenever a Knative
    # deployment happens to still be up -- confirmed happening in
    # results/regression/openfaas/*/samples.csv (k8s_kourier-gateway_* present
    # and CPU-mapped into cp_cpu_s every run). `docker stack deploy openfaas`
    # always names swarm tasks "openfaas_<service>.<slot>.<id>" (hardcoded
    # stack name in run_openfaas.sh), so this prefix is exact, not a guess.
    cp_containers = ("openfaas_gateway", "openfaas_faas-swarm", "openfaas_prometheus",
                     "openfaas_nats", "openfaas_queue-worker", "openfaas_alertmanager")
    fn_images = ("hello",)           # deployed function image: hello:latest
    fn_containers = ()
    cp_images = ()
    cp_labels = ()
    fn_labels = ()
    sampler = "cgroup"
    loadgen = "hey"
    delta_check = True
    verify_n = 100
    verify_budget_ms = 5.0
    default_replicas = 16            # static replica parity (GIL-bound handler; manifest #3)
    isolation = IsolationPolicy(
        forbidden_services=(),
        # "user-container"/"queue-proxy" catch a leftover Knative 'hello'
        # ksvc specifically (its two per-replica pod containers) -- NOT a
        # bare "k8s_" prefix, which would also match k3s/Knative-serving's
        # own control plane (activator, kourier-gateway, coredns, ...) that
        # is DESIGNED to stay resident on this box across every platform's
        # session (see platforms/knative.py: "k3s stays up, it is the
        # substrate"). Forbidding all "k8s_" would permanently block this
        # adapter even with 'hello' properly torn down -- confirmed live
        # 2026-08-08 while validating this fix. Fail loud on the actual
        # function-serving leftover, not on the substrate's idle presence.
        forbidden_containers=("fnserver", "user-container", "queue-proxy"),
        expected_containers=("openfaas_gateway", "openfaas_faas-swarm", "openfaas_prometheus",
                             "openfaas_nats", "openfaas_queue-worker", "openfaas_alertmanager"),
    )

    def _hello_service_exists(self):
        out = subprocess.run(["docker", "service", "ls", "--format", "{{.Name}}"],
                             capture_output=True, text=True)
        if out.returncode != 0:
            return False
        return any(l.strip() == "hello" for l in out.stdout.splitlines())

    def deploy(self):
        # Mirrors run_openfaas.sh 'stack': swarm init + stack deploy + wait for
        # gateway. Returns once the whole 6-container control plane is visible
        # (run_openfaas.sh's own curl wait only proves the gateway is up).
        r = subprocess.run(["bash", repo_script("run_openfaas.sh"), "stack"], text=True)
        if r.returncode != 0:
            raise RuntimeError("openfaas deploy: stack deploy failed (rc=%d)" % r.returncode)
        ok, names = wait_containers(expected=self.cp_containers,
                                    absent=("fnserver",), timeout=90.0)
        if not ok:
            raise RuntimeError("openfaas deploy FAILED: control plane not up after stack deploy; "
                               "containers: %s" % ", ".join(names or ["none"]))
        print("openfaas deploy OK: control plane up (%d containers)"
              % len([n for n in names if any(c in n for c in self.cp_containers)]))
        return r

    def deploy_function(self):
        # The documented manual prerequisite, automated: create the 'hello'
        # function service OUTSIDE the stack so the gateway can route
        # /function/hello (faas-swarm discovers it via com.openfaas.function).
        if self._hello_service_exists():
            print("openfaas deploy_function: hello service already present; skipping")
            return
        r = subprocess.run([
            "docker", "service", "create",
            "--name", "hello",
            "--label", "com.openfaas.function=hello",
            "--label", "com.openfaas.uid=" + str(int(time.time())),
            "--network", "openfaas_functions",
            "--detach=false",
            "hello:latest",
        ], text=True)
        if r.returncode != 0:
            raise RuntimeError("openfaas deploy_function FAILED: docker service create rc=%d"
                               % r.returncode)
        if not wait_for_url(self.url, timeout=60.0):
            raise RuntimeError("openfaas deploy_function FAILED: hello not serving %s" % self.url)
        print("openfaas deploy_function OK: hello service serving %s" % self.url)

    def teardown(self):
        # Taint-critical ordering: the 'hello' function service lives OUTSIDE the
        # stack and would otherwise contaminate a subsequent Fn run. rm both, then
        # WAIT until the CP containers and the hello service are really gone so the
        # next platform's deploy does not race a dying gateway still holding :8080.
        subprocess.run(["docker", "stack", "rm", "openfaas"], text=True)
        if self._hello_service_exists():
            subprocess.run(["docker", "service", "rm", "hello"], text=True)
        ok, names = wait_containers(absent=self.cp_containers + ("hello",), timeout=90.0)
        if not ok:
            print("WARNING: containers still present after OpenFaaS teardown: %s"
                  % ", ".join(names or ["?"]))

    def scale(self, replicas):
        env = dict(os.environ)
        env["SAQEF_REPLICAS"] = str(replicas)
        r = subprocess.run(["bash", repo_script("run_openfaas.sh"), "scale"], text=True, env=env)
        if r.returncode != 0:
            raise RuntimeError("openfaas scale FAILED: is the 'hello' function service "
                               "deployed? run 'deploy_function' first (rc=%d)" % r.returncode)
        return r


adapter = OpenFaaSAdapter()
