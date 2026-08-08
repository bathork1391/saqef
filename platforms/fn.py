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

from platforms.base import Adapter, IsolationPolicy, container_names, wait_containers, wait_for_url


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
        # "user-container"/"queue-proxy" catch a leftover Knative 'hello' ksvc
        # specifically (its two per-replica pod containers) -- NOT a bare
        # "k8s_" prefix, which would also match k3s/Knative-serving's own
        # control plane (activator, kourier-gateway, coredns, ...) that is
        # DESIGNED to stay resident on this box across every session (see
        # platforms/knative.py). Forbidding all "k8s_" would permanently block
        # Fn even with 'hello' torn down -- confirmed live 2026-08-08. Fn's
        # fn_images=("hello",) substring-matches Knative's "kn-hello" image;
        # today that's masked only by an incidental docker/containerd quirk
        # (k3s-managed containers show a bare image digest, not the resolved
        # tag), not by design -- this check is the real defense.
        forbidden_containers=("user-container", "queue-proxy"),
        expected_containers=("fnserver",),
    )

    def _serving(self):
        if not any("fnserver" in n for n in container_names()):
            return False
        return wait_for_url(self.url, timeout=20.0, interval=0.5)

    def deploy(self):
        # Mirrors 'all' ordering minus measurement: reset then setup. The shell
        # runner swallows a failed `docker run fnserver` (no 'set -e'), so after
        # setup we PROVE fnserver serves the trigger URL. The most common cause
        # of a silent miss is port 8080 still held by a dying OpenFaaS gateway
        # from the previous leg -- a fresh reset+setup after the port frees up
        # recovers cleanly, so retry once before declaring failure.
        self.teardown()
        subprocess.run(["bash", "run_saqef.sh", "setup"], text=True)
        if not self._serving():
            print("WARNING: fnserver not serving %s after setup; retrying deploy once "
                  "(port 8080 may still have been held by a dying OpenFaaS gateway)"
                  % self.url)
            subprocess.run(["bash", "run_saqef.sh", "reset"], text=True)
            subprocess.run(["bash", "run_saqef.sh", "setup"], text=True)
        if not self._serving():
            raise RuntimeError("Fn deploy FAILED: fnserver did not come up to serve %s" % self.url)
        print("Fn deploy OK: fnserver serving %s" % self.url)

    def teardown(self):
        # run_saqef.sh reset = fresh-session protocol: remove fnserver, orphaned
        # hello:* function containers, and stale /tmp/iofs /tmp/data. Wait for
        # the containers to actually be gone so the NEXT platform's deploy does
        # not race a still-dying container on the same ports.
        subprocess.run(["bash", "run_saqef.sh", "reset"], text=True)
        ok, names = wait_containers(absent=("fnserver", "hello"), timeout=30.0)
        if not ok:
            print("WARNING: containers still present after Fn teardown: %s"
                  % ", ".join(names or ["?"]))

    def scale(self, replicas):
        raise NotImplementedError("Fn function containers are ephemeral; no static replica scale")


adapter = FnAdapter()
