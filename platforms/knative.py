"""Knative Serving adapter (single-node k3s with the docker runtime).

Deployment model: Knative Serving v1.23 + Kourier on k3s v1.36 (k3s uses the
DOCKER runtime, so every workload pod is a plain docker container the harness
can see and classify). The function is a python ThreadingHTTPServer that busy-
spins ~5 ms per GET, deployed as a Knative Service with STATIC scaling
(minScale = maxScale = 16, containerConcurrency 4) for GIL concurrency parity
with OpenFaaS's 16 static replicas.

Measurement mapping (honest, documented in the summary's container inventory):
  - cp_containers = activator, controller, autoscaler, webhook,
    net-kourier-controller, kourier-gateway, svclb-kourier  (the Knative+Kourier
    control/data plane -- the kourier gateway and the activator are on the
    request path). The k3s EMBEDDED control plane (apiserver/etcd/scheduler/
    controller-manager) runs INSIDE the `k3s server` process, NOT as docker
    containers, so its CPU is invisible to the container sampler and lands in
    the host_cpu_sec residual -- a documented measurement boundary, not a gap
    in the platform (a production cluster treats the k8s plane as shared infra).
  - fn_containers = user-container, queue-proxy  (the function pod's two
    containers). Knative injects a queue-proxy SIDECAR into every function pod;
    it is per-replica and processes every request, so it is honest "dynamic
    (load-created)" function-side overhead -- counted as fn CPU, same bucket as
    the function itself. Document this in the paper: Knative's per-invocation
    cost includes the queue-proxy where Fn/OpenFaaS/OpenWhisk use a platform
    proxy on the CP side.
  - The local registry (localhost:5000, needed to give Knative a resolvable
    image) and k3s system pods (coredns, metrics-server, local-path) idle at
    ~0% and land in unclassified_cpu_s (fail-open).

Isolation: Knative never uses swarm; any swarm service is contamination, and
fnserver / openwhisk / openfaas containers must be down. k3s itself is a
systemd service and stays up (it is the substrate); only the hello service is
torn down between platforms so its 16 function pods cannot taint a later run.
"""

import json
import os
import subprocess
import time

from platforms.base import Adapter, IsolationPolicy, http_get, run, wait_containers

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "http://hello.default.127.0.0.1.sslip.io"
SERVICE_YAML = os.path.join(REPO, "KNATIVE_FUNCTION", "service.yaml")
REGISTRY = "registry"          # local image registry container (deploy support)


def k3s(*args):
    """sudo k3s kubectl ... (k3s config is root-readable only)."""
    return subprocess.run(["sudo", "k3s", "kubectl", *args],
                          capture_output=True, text=True)


def _pod_ready(selector, timeout=120.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        r = k3s("get", "pods", "-n", "default", "-l", selector,
                "-o", "jsonpath={range .items[*]}{.status.phase}{\" \"}{end}")
        if r.returncode == 0 and r.stdout.strip() and \
                all(p == "Running" for p in r.stdout.split()):
            return True
        time.sleep(3.0)
    return False


class KnativeAdapter(Adapter):
    name = "knative"
    label = "Knative"
    url = URL
    auth = ""
    cp_containers = ("activator-", "controller-", "autoscaler-", "webhook-",
                     "net-kourier-controller", "kourier-gateway", "svclb-kourier")
    fn_images = ("kn-hello",)            # mandatory allowlist (image substrings)
    fn_containers = ("user-container", "queue-proxy")   # actual match: pod containers
    cp_images = ()
    cp_labels = ()
    fn_labels = ()
    sampler = "cgroup"
    loadgen = "hey"
    delta_check = True
    verify_n = 100
    verify_budget_ms = 5.0
    default_replicas = 16                # GIL parity: static replicas (manifest #3)
    isolation = IsolationPolicy(
        forbidden_services=("*",),       # Knative never uses swarm
        forbidden_containers=("fnserver", "openwhisk", "openfaas"),
        expected_containers=("activator-", "kourier-gateway"),
    )

    # ------------------------------------------------------------------ deploy
    def _ensure_registry(self):
        out = run("docker ps -a --format '{{.Names}}'")
        if REGISTRY not in out.stdout:
            subprocess.run(["docker", "run", "-d", "--name", REGISTRY,
                            "-p", "5000:5000", "registry:2"],
                           capture_output=True, text=True)

    def deploy(self):
        r = k3s("get", "node")
        if r.returncode != 0:
            raise RuntimeError("knative deploy: k3s not ready (rc=%d): %s"
                               % (r.returncode, (r.stderr or "").strip()[-300:]))
        self._ensure_registry()
        r = k3s("get", "ksvc", "hello", "-n", "default")
        if r.returncode != 0:
            r = k3s("apply", "-f", SERVICE_YAML)
            if r.returncode != 0:
                raise RuntimeError("knative deploy: apply service failed: %s"
                                   % (r.stderr or "").strip()[-300:])
        # The Deployment name embeds the revision number (hello-NNNNN-deployment),
        # which is NOT stable: deleting and recreating the ksvc (a full teardown
        # + deploy, as opposed to reusing an existing one) resets Knative's
        # revision counter back to 1, so a hardcoded '-00002' silently fails
        # 'rollout status' after any fresh redeploy and relies entirely on the
        # _pod_ready fallback below. Poll by the revision-agnostic label
        # selector directly instead of guessing the Deployment name.
        if not _pod_ready("serving.knative.dev/service=hello"):
            raise RuntimeError("knative deploy: function pods not running")
        if not self._ping():
            raise RuntimeError("knative deploy: %s not serving" % URL)
        print("knative deploy OK: %s serving %s (k3s + knative v1.23 + kourier)" % (self.label, URL))
        return r

    def _ping(self, timeout=120.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            status, _ = http_get(URL, timeout=5.0)
            if status == 200:
                return True
            time.sleep(2.0)
        return False

    # -------------------------------------------------------- function deploy
    def deploy_function(self):
        """The function IS part of the Knative Service; ensure it is up."""
        r = k3s("apply", "-f", SERVICE_YAML)
        if r.returncode != 0:
            raise RuntimeError("knative deploy_function: %s" % (r.stderr or "").strip()[-300:])
        if not self._ping(timeout=180.0):
            raise RuntimeError("knative deploy_function: hello not serving")
        print("knative deploy_function OK: hello serving %s" % URL)

    # --------------------------------------------------------------- teardown
    def teardown(self):
        """Remove the hello service (and its 16 function pods + queue-proxies)
        so no knative fn containers can taint a later platform's run. k3s +
        knative + kourier stay up (the substrate); the registry is removed too."""
        k3s("delete", "ksvc", "hello", "-n", "default", "--ignore-not-found")
        k3s("delete", "deploy", "-n", "default",
            "-l", "serving.knative.dev/service=hello", "--ignore-not-found")
        ok, names = wait_containers(absent=("hello-0000", "user-container"),
                                    timeout=120.0)
        if not ok:
            print("WARNING: knative function containers still present: %s"
                  % ", ".join(names or ["?"]))
        subprocess.run(["docker", "rm", "-f", REGISTRY], capture_output=True, text=True)

    # ------------------------------------------------------------------ scale
    def scale(self, replicas):
        """Static replica scale via the autoscaler annotations (GIL parity).

        These annotations must live under spec.template.metadata.annotations
        (the revision template), not the ksvc's own top-level metadata --
        Knative's admission webhook rejects the latter. `kubectl annotate`
        only ever targets the object's own metadata, so a merge patch into
        the template is used instead."""
        patch = json.dumps({"spec": {"template": {"metadata": {"annotations": {
            "autoscaling.knative.dev/minScale": str(replicas),
            "autoscaling.knative.dev/maxScale": str(replicas),
        }}}}})
        r = k3s("patch", "ksvc", "hello", "-n", "default",
                "--type=merge", "-p", patch)
        if r.returncode != 0:
            raise RuntimeError("knative scale: %s" % (r.stderr or "").strip()[-300:])
        if not _pod_ready("serving.knative.dev/service=hello", timeout=300.0):
            raise RuntimeError("knative scale: pods did not reach Running")


adapter = KnativeAdapter()
