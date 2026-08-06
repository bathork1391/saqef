"""Fn adapter. Deploy/teardown delegate to the proven run_saqef.sh functions so
the orchestration stays byte-identical to the pre-refactor tooling.

Isolation policy (the taint bug, report 31.9): Fn NEVER uses Docker Swarm, so
ANY running swarm service during an Fn session is contamination -- in practice
the OpenFaaS 'hello' function service (deployed OUTSIDE the openfaas stack, so
'docker stack rm openfaas' does not remove it). Its replicas carry the same
'hello' image allowlist as Fn's ULID-named function containers and would be
silently folded into fn_cpu.
"""

import subprocess

from platforms.base import Adapter, IsolationPolicy, run


class FnAdapter(Adapter):
    name = "fn"
    label = "Fn"
    url = "http://localhost:8080/t/app1/hello"
    auth = ""
    cp_containers = ("fnserver",)
    fn_images = ("hello",)          # deployed image, NOT the base fnproject/python:*
    fn_containers = ()
    cp_images = ()
    cp_labels = ()
    fn_labels = ()
    sampler = "cgroup"
    loadgen = "hey"
    delta_check = True
    verify_n = 100
    verify_budget_ms = 5.0
    default_replicas = None         # Fn function containers are ephemeral (no static scale)
    isolation = IsolationPolicy(
        forbidden_services=("*",),   # ANY swarm service contaminates an Fn session
        forbidden_containers=(),
        expected_containers=("fnserver",),
    )

    def deploy(self):
        # Mirrors 'all' ordering minus measurement: reset then setup.
        self.teardown()
        return subprocess.run(["bash", "run_saqef.sh", "setup"], text=True)

    def teardown(self):
        # run_saqef.sh reset = fresh-session protocol: remove fnserver, orphaned
        # hello:* function containers, and stale /tmp/iofs /tmp/data.
        return subprocess.run(["bash", "run_saqef.sh", "reset"], text=True)

    def scale(self, replicas):
        raise NotImplementedError("Fn function containers are ephemeral; no static replica scale")


adapter = FnAdapter()
