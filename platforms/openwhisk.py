"""OpenWhisk adapter (Apache OpenWhisk STANDALONE, single-node).

Deployment model: the official `openwhisk/standalone:nightly` image runs the
whole control plane (HTTP front, controller, invoker, scheduler) in ONE
container -- architecturally like Fn's fnserver -- and the invoker spawns
per-invocation ACTION containers from the action's runtime image. This is the
Apache-recommended local deployment (memory store; no external Kafka/CouchDB).

Measurement mapping (honest, documented in the summary's container inventory):
  - cp_containers = ("openwhisk",)          -- the standalone container IS the CP.
  - fn_images     = ("action-python-v3.11",) -- the python action runtime image the
    invoker instantiates per activation (containers named wsk0_*_guest_hello).
  - The standalone pre-warms two idle nodejs20 containers (wsk0_*_prewarm_*,
    image action-nodejs-v20). They are invoker warmup capacity, NOT the function
    under test, so they are deliberately excluded from fn_images; they idle at
    ~0% CPU during a bench and land in unclassified_cpu_s (fail-open, as
    designed in the manifest #6 semantics).

Frozen-harness constraint: the measurement path is GET-only and byte-identical,
while OpenWhisk's native action invoke is POST. The action is therefore deployed
as a WEB ACTION: GET http://127.0.0.1:3233/api/v1/web/guest/default/hello
performs a blocking invocation and returns HTTP 204 (a 2xx). The harness counts
any 2xx as a success, and the 5 ms spin provably runs on every call (verified:
activation durations 6-8 ms per invocation).

Deployment gotcha handled by deploy(): the standalone image ships a 2018 docker
client (API 1.38) which host dockerd >= 29 (min API 1.44) rejects and kills the
JVM. The fix is to shadow /usr/bin/docker in the container with a modern STATIC
docker CLI (vendor/docker, fetched from download.docker.com on demand), so the
invoker's pull/run calls work against the modern daemon.

Isolation: OpenWhisk never uses swarm (like Fn), so any swarm service is
contamination; Fn's fnserver must also be down.
"""

import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

from platforms.base import Adapter, IsolationPolicy, container_names, http_get, wait_containers

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OW_IMAGE = "openwhisk/standalone:nightly"
PY_RUNTIME_IMAGE = "openwhisk/action-python-v3.11:nightly"
VENDOR_DOCKER = os.path.join(REPO, "vendor", "docker")
DOCKER_STATIC_URL = "https://download.docker.com/linux/static/stable/x86_64/docker-29.1.3.tgz"

API_HOST = "http://127.0.0.1:3233"
# The well-known OpenWhisk dev default credential (printed by the standalone on
# startup; web actions are public, this is only needed for the action deploy PUT).
OW_AUTH = "23bc46b1-71f6-4ed5-8c54-816aa4f8c502:123zO3xZCLrMN6v2BKK1dXYFpXlPkccOFqm12CdAsMgRU4VrNZ9lyGVCGuMDGIwP"

# The standalone's default per-namespace throttle is 60 invocations/minute per
# action (standalone.conf: limits-actions-invokes-perMinute = 60); a real bench
# at ~hundreds of rps would be 429-throttled into uselessness (measured: 40% of
# calls at concurrency 4 came back HTTP 429 'Too many requests'). The throttle
# is enforced from whisk-config.limits.actions.invokes.perMinute (WhiskConfig
# reads the `whisk-config.`-prefixed dotted path; standalone.conf's kebab keys
# under whisk.config are a separate, unread path -- both are overridden here for
# safety). /init passes $JVM_EXTRA_ARGS straight into `java`, so we raise the
# window limits via system properties. A bench at concurrency c <= 4 and
# TOTAL <= 10000 stays well under 1000 concurrent / 1e9 per minute.
OW_JVM_ARGS = ("-Dwhisk-config.limits.actions.invokes.perMinute=1000000000"
               " -Dwhisk.config.limits-actions-invokes-perMinute=1000000000"
               " -Dwhisk-config.limits.actions.invokes.concurrent=1000"
               " -Dwhisk.config.limits-actions-invokes-concurrent=1000"
               " -Dwhisk-config.limits.triggers.fires.perMinute=1000000000"
               " -Dwhisk.config.limits-triggers-fires-perMinute=1000000000")


def _auth_header():
    return "Basic " + base64.b64encode(OW_AUTH.encode()).decode()


class OpenWhiskAdapter(Adapter):
    name = "openwhisk"
    label = "OpenWhisk"
    url = API_HOST + "/api/v1/web/guest/default/hello"
    auth = ""                        # web actions are public (web-export=true)
    cp_containers = ("openwhisk",)
    fn_images = ("action-python-v3.11",)   # action runtime image (wsk0_*_guest_hello containers)
    fn_containers = ()
    cp_images = ()
    cp_labels = ()
    fn_labels = ()
    sampler = "cgroup"
    loadgen = "hey"
    delta_check = True
    verify_n = 100
    verify_budget_ms = 5.0
    default_replicas = None          # invoker scales action containers dynamically (no static scale)
    isolation = IsolationPolicy(
        forbidden_services=("*",),   # OpenWhisk never uses swarm; any service contaminates
        forbidden_containers=("fnserver",),
        expected_containers=("openwhisk",),
    )

    # ------------------------------------------------------------------ deploy
    @staticmethod
    def _ensure_static_docker():
        """Download the static docker CLI into vendor/ (shadow-mount for the
        standalone's obsolete embedded client). Returns the binary path."""
        if os.path.isfile(VENDOR_DOCKER):
            return VENDOR_DOCKER
        # A stale *directory* from a pre-fix extraction must not satisfy the check
        # (it would be mounted as a dir onto /usr/bin/docker and docker run fails).
        if os.path.isdir(VENDOR_DOCKER):
            os.rmdir(VENDOR_DOCKER)   # empty dir left by an interrupted extraction
        if os.path.isdir(VENDOR_DOCKER):
            raise OSError("%s exists and is a non-empty directory; remove it manually"
                          % VENDOR_DOCKER)
        os.makedirs(os.path.dirname(VENDOR_DOCKER), exist_ok=True)
        print("openwhisk deploy: fetching static docker CLI for the standalone mount ...")
        staging = os.path.join(os.path.dirname(VENDOR_DOCKER), "_docker_extract")
        tarball = os.path.join(os.path.dirname(VENDOR_DOCKER), "docker-static.tgz")
        urllib.request.urlretrieve(DOCKER_STATIC_URL, tarball)
        os.makedirs(staging, exist_ok=True)
        try:
            subprocess.run(["tar", "xzf", tarball, "-C", staging, "docker/docker"], check=True)
            os.replace(os.path.join(staging, "docker", "docker"), VENDOR_DOCKER)
        finally:
            for leftover in (tarball, staging):
                subprocess.run(["rm", "-rf", leftover], check=False)
        os.chmod(VENDOR_DOCKER, 0o755)
        return VENDOR_DOCKER

    def deploy(self):
        # The standalone is the CP; the whole deployment is one container with
        # the docker socket so its invoker can spawn action containers.
        subprocess.run(["docker", "rm", "-f", "openwhisk"],
                       capture_output=True, text=True)
        static = self._ensure_static_docker()
        r = subprocess.run(["docker", "run", "--rm", "-d", "--name", "openwhisk",
                            "-h", "openwhisk", "-p", "3233:3233", "-p", "3232:3232",
                            "-e", "JVM_EXTRA_ARGS=" + OW_JVM_ARGS,
                            "-v", "/var/run/docker.sock:/var/run/docker.sock",
                            "-v", "%s:/usr/bin/docker" % static,
                            OW_IMAGE], text=True)
        if r.returncode != 0:
            raise RuntimeError("openwhisk deploy: docker run failed (rc=%d): %s"
                               % (r.returncode, (r.stderr or "").strip()[-300:]))
        if not wait_containers(expected=("openwhisk",), timeout=30.0)[0]:
            raise RuntimeError("openwhisk deploy: standalone container did not appear")
        if not self._ping(timeout=180.0):
            raise RuntimeError("openwhisk deploy: API not serving %s/ping" % API_HOST)
        print("openwhisk deploy OK: %s serving %s" % (OW_IMAGE, API_HOST))
        return r

    def _ping(self, timeout=180.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            status, _ = http_get(API_HOST + "/ping", timeout=5.0)
            if status == 200:
                return True
            time.sleep(2.0)
        return False

    # -------------------------------------------------------- function deploy
    def deploy_function(self):
        # Pre-pull the python runtime so the bench never hits a cold pull inside
        # an activation (would surface as 202 'not ready' during verify).
        subprocess.run(["docker", "pull", PY_RUNTIME_IMAGE], text=True)
        code = open(os.path.join(REPO, "OW_FUNCTION", "hello.py")).read()
        body = json.dumps({
            "namespace": "guest", "name": "hello",
            "exec": {"kind": "python:3.11", "code": code},
            "annotations": [{"key": "web-export", "value": True}],
        }).encode()
        req = urllib.request.Request(
            API_HOST + "/api/v1/namespaces/guest/actions/hello?overwrite=true",
            data=body, method="PUT",
            headers={"Authorization": _auth_header(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError("openwhisk deploy_function: action PUT failed (%d): %s"
                               % (e.code, e.read()[:200]))
        if not self._serving():
            raise RuntimeError("openwhisk deploy_function FAILED: hello web action not "
                               "serving %s" % self.url)
        print("openwhisk deploy_function OK: hello action serving %s" % self.url)

    def _serving(self):
        # Wait until the web action returns a 2xx (the standalone returns 204 for
        # web GETs; wait_for_url's ==200 check is too strict here). One call warms
        # the action container so the very first bench/verify request is fast.
        t0 = time.monotonic()
        while time.monotonic() - t0 < 90.0:
            status, _ = http_get(self.url, timeout=10.0)
            if status is not None and 200 <= status < 300:
                return True
            time.sleep(2.0)
        return False

    # --------------------------------------------------------------- teardown
    def teardown(self):
        subprocess.run(["docker", "rm", "-f", "openwhisk"], capture_output=True, text=True)
        # The invoker's action containers (wsk0_*) and its prewarm pool are
        # children of the standalone; remove any that survived the parent kill so
        # the next platform's deploy starts clean.
        for n in container_names():
            if n.startswith("wsk0_"):
                subprocess.run(["docker", "rm", "-f", n], capture_output=True, text=True)
        ok, names = wait_containers(absent=("openwhisk", "wsk0_"), timeout=30.0)
        if not ok:
            print("WARNING: containers still present after OpenWhisk teardown: %s"
                  % ", ".join(names or ["?"]))

    def scale(self, replicas):
        raise NotImplementedError("OpenWhisk's invoker scales action containers dynamically; "
                                  "no static replica scale")


adapter = OpenWhiskAdapter()
