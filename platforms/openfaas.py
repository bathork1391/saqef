"""OpenFaaS adapter (Docker Swarm, hand-written stack in OPENFAAS_DEPLOY/).

Stack deploy/scale delegate to the proven run_openfaas.sh functions. The
FUNCTION service ('hello') is deployed OUTSIDE the stack (faas-cli) and is a
manual prerequisite -- exactly as in the pre-refactor tooling; 'docker stack rm
openfaas' does NOT remove it, which is why teardown() must rm it explicitly
(the ordering in AGENTS.md: stack rm, THEN service rm hello).

Isolation policy: OpenFaaS sessions forbid Fn's 'fnserver' container.
"""

import os
import subprocess

from platforms.base import Adapter, IsolationPolicy, run


class OpenFaaSAdapter(Adapter):
    name = "openfaas"
    label = "OpenFaaS"
    url = "http://127.0.0.1:8080/function/hello"
    auth = ""                        # gateway 0.8.3 predates HTTP basic auth
    cp_containers = ("gateway", "faas-swarm", "prometheus", "nats",
                     "queue-worker", "alertmanager")
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
        forbidden_containers=("fnserver",),   # Fn's control plane must be down
        expected_containers=("gateway", "faas-swarm", "prometheus", "nats",
                             "queue-worker", "alertmanager"),
    )

    def deploy(self):
        # Mirrors run_openfaas.sh 'stack': swarm init + stack deploy + wait for gateway.
        return subprocess.run(["bash", "run_openfaas.sh", "stack"], text=True)

    def teardown(self):
        # Taint-critical ordering: the 'hello' function service lives OUTSIDE the
        # stack and would otherwise contaminate a subsequent Fn run. rm both.
        subprocess.run(["docker", "stack", "rm", "openfaas"], text=True)
        subprocess.run(["docker", "service", "rm", "hello"], text=True)
        return None

    def scale(self, replicas):
        env = dict(os.environ)
        env["SAQEF_REPLICAS"] = str(replicas)
        return subprocess.run(["bash", "run_openfaas.sh", "scale"], text=True, env=env)


adapter = OpenFaaSAdapter()
