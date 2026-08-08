"""Adapter contract for the SAQEF measurement harness.

One Adapter per platform. The adapter owns EVERYTHING that is platform-specific:
URL/auth, container classifiers (cp + fn), the isolation policy, and the
deploy/teardown/scale orchestration. The measurement path itself lives in
saqef_harness.py and is invoked as a subprocess with the exact argv this repo's
proven shell runners (run_saqef.sh / run_openfaas.sh) construct -- so the
measurement is identical by construction and this layer can never silently
change the numbers.

The "do not regress" manifest is encoded as MANDATORY adapter fields so a fixed
bug cannot silently vanish (see AGENTS.md):
  1. fn-images allowlist overlap taint      -> fn_images REQUIRED non-empty
  2. leftover-service taint                 -> isolation policy REQUIRED
  3. GIL concurrency parity (static replicas) -> default_replicas where applicable
  4. calibrated idle-w (not the 30 W default) -> enforced by the CLI/recipe
  5. host-window alignment / host_plausible -> gates table always prints it
  6. delta-check 6/6 ok, coverage <=100%    -> gates table always prints them
  7. count-bound runs                       -> total/duration semantics live in the metric recipe
"""

import os
import subprocess
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def repo_script(name):
    """Absolute path to a top-level repo file (e.g. a shell runner). The
    adapters must NOT spawn 'bash run_saqef.sh' with a bare relative path: the
    CLI can be invoked from any CWD (tools/run_contamination_ab.sh calls
    '$REPO/saqef' while the user sits in tools/), and the subprocess would then
    inherit a CWD where the script does not exist. Resolve against the repo
    root from this file's own location instead."""
    return os.path.join(REPO_ROOT, name)


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def bash(*args):
    """Run a shell command list; return CompletedProcess (raise-on-fail=False)."""
    return subprocess.run(args, capture_output=True, text=True)


# --------------------------------------------------------- deployment probing
# The shell runners swallow failures (no 'set -e'; 'docker run fnserver' is
# unchecked and 'fn deploy' ends in '|| true'), so an adapter MUST prove the
# platform is actually serving after deploy() instead of trusting the exit
# code. These helpers are orchestration-only; they never touch measurement.
def http_get(url, timeout=5.0):
    """Return (status, bytes) or (None, None) on any connection/HTTP error."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(64)
    except Exception:
        return None, None


def wait_for_url(url, timeout=90.0, interval=1.0):
    """Poll GET url until it returns HTTP 200. True on success, False on timeout."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        status, _ = http_get(url)
        if status == 200:
            return True
        time.sleep(interval)
    return False


def container_names():
    out = run("docker ps --format '{{.Names}}'")
    if out.returncode != 0:
        return []
    return [l.strip() for l in out.stdout.splitlines() if l.strip()]


def wait_containers(expected=(), absent=(), timeout=90.0, interval=1.0):
    """Poll docker ps until every `expected` name-substring is present and no
    `absent` substring is. Returns (ok, final snapshot of names)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        names = container_names()
        missing = [e for e in expected if not any(e in n for n in names)]
        lingering = [a for a in absent if any(a in n for n in names)]
        if not missing and not lingering:
            return True, names
        time.sleep(interval)
    return False, container_names()


class IsolationPolicy:
    """What must be absent/present on the host for a clean measurement.

    forbidden_services:    swarm services that must NOT be up. "*" = ANY service
                           (Fn never uses swarm, so any service is contamination).
    forbidden_containers:  docker container NAME substrings that must NOT be up
                           (e.g. OpenFaaS sessions forbid Fn's 'fnserver').
    expected_containers:   advisory container-name substrings that SHOULD be up
                           (fail-open warning only; a platform that uses systemd
                           or a non-container control plane simply leaves this
                           empty and relies on forbidden_* only).
    """

    def __init__(self, forbidden_services=(), forbidden_containers=(), expected_containers=()):
        self.forbidden_services = tuple(forbidden_services)
        self.forbidden_containers = tuple(forbidden_containers)
        self.expected_containers = tuple(expected_containers)
        if not (self.forbidden_services or self.forbidden_containers):
            raise ValueError("isolation policy must forbid at least one of services/containers")


class Adapter:
    """Base class. Subclasses set the platform constants; the CLI drives the rest."""

    name = None                 # REQUIRED: 'fn', 'openfaas', ...
    label = None                # REQUIRED: display label
    url = None                  # REQUIRED: synchronous function URL
    auth = ""                   # optional 'user:pass' HTTP Basic (OpenFaaS 0.8.3 needs none)
    cp_containers = ()          # REQUIRED non-empty: control-plane name substrings
    fn_images = ()              # REQUIRED non-empty: deployed function image substrings (allowlist)
    fn_containers = ()          # optional fn name substrings
    cp_images = ()              # optional cp image substrings
    cp_labels = ()              # optional cp docker-label keys
    fn_labels = ()              # optional fn docker-label keys
    sampler = "cgroup"
    rescan_s = 0.25
    loadgen = "hey"
    delta_check = True
    verify_n = 100
    verify_budget_ms = 5.0
    default_replicas = None     # static function replicas for concurrency parity (manifest #3)
    isolation = None            # REQUIRED: IsolationPolicy instance

    def __init__(self):
        missing = [k for k, v in {
            "name": self.name, "label": self.label, "url": self.url,
            "cp_containers": self.cp_containers, "fn_images": self.fn_images,
            "isolation": self.isolation,
        }.items() if not v]
        if missing:
            raise TypeError("%s adapter missing mandatory fields: %s"
                            % (type(self).__name__, ", ".join(missing)))
        if not self.cp_containers:
            raise TypeError("%s adapter: cp_containers must be non-empty" % type(self).__name__)
        if not self.fn_images:
            raise TypeError("%s adapter: fn_images allowlist must be non-empty "
                            "(manifest #1: hello-image overlap taint)" % type(self).__name__)

    # ------------------------------------------------------------------ config
    def cp_containers_arg(self):
        return ",".join(self.cp_containers)

    def fn_images_arg(self):
        return ",".join(self.fn_images)

    def fn_containers_arg(self):
        return ",".join(self.fn_containers)

    # ------------------------------------------------------------ isolation
    # Remediation advice, keyed by the specific offender so a failure on one
    # adapter never points at another adapter's fix. Fn/OpenFaaS/OpenWhisk all
    # forbid Knative's per-replica pod containers now (not just Fn's own
    # fnserver), so a single hardcoded message was wrong for 3 of 4 adapters
    # -- it told the operator to remove fnserver when the actual offender was
    # a leftover Knative ksvc, sending them down the wrong troubleshooting
    # path. Matched by substring against the offending container/service name.
    _CONTAINER_ADVICE = (
        ("fnserver", "docker rm -f fnserver   (Fn's control plane)"),
        ("user-container", "saqef teardown --platform knative   (leftover Knative 'hello' ksvc)"),
        ("queue-proxy", "saqef teardown --platform knative   (leftover Knative 'hello' ksvc)"),
        ("openwhisk", "saqef teardown --platform openwhisk"),
        ("openfaas", "saqef teardown --platform openfaas"),
    )

    def _advice_for_container(self, name):
        for sub, advice in self._CONTAINER_ADVICE:
            if sub in name:
                return advice
        return "docker rm -f %s" % name

    @staticmethod
    def _advice_for_service(name):
        if name == "hello":
            return "docker service rm hello   (OpenFaaS function service, deployed OUTSIDE the stack)"
        if name.startswith("openfaas"):
            return "docker stack rm openfaas   (OpenFaaS control-plane stack)"
        return "docker service rm %s" % name

    def check_isolation(self):
        """Data-driven isolation guard (the refactor's point). Returns (ok, message)."""
        offenders = []
        advice = []
        if self.isolation.forbidden_services:
            out = run("docker service ls --format '{{.Name}}'")
            if out.returncode == 0:
                services = [l.strip() for l in out.stdout.splitlines() if l.strip()]
                if "*" in self.isolation.forbidden_services:
                    bad = services
                else:
                    bad = [s for s in services
                           if any(f in s for f in self.isolation.forbidden_services)]
                if bad:
                    offenders.append("swarm service(s): %s" % ", ".join(bad))
                    for s in bad:
                        a = self._advice_for_service(s)
                        if a not in advice:
                            advice.append(a)
        if self.isolation.forbidden_containers:
            out = run("docker ps --format '{{.Names}}'")
            if out.returncode == 0:
                names = [l.strip() for l in out.stdout.splitlines() if l.strip()]
                bad = [n for n in names
                       if any(f in n for f in self.isolation.forbidden_containers)]
                if bad:
                    offenders.append("container(s): %s" % ", ".join(bad))
                    for n in bad:
                        a = self._advice_for_container(n)
                        if a not in advice:
                            advice.append(a)
        if offenders:
            advice_str = "; ".join("run: %s" % a for a in advice)
            return False, ("platform isolation FAILED for %s (%s): %s. %s"
                           % (self.name, self.label, "; ".join(offenders), advice_str))
        if self.isolation.expected_containers:
            out = run("docker ps --format '{{.Names}}'")
            names = [l.strip() for l in out.stdout.splitlines()] if out.returncode == 0 else []
            missing = [e for e in self.isolation.expected_containers
                       if not any(e in n for n in names)]
            if missing:
                print("WARNING: expected control-plane container(s) not seen: %s" % ", ".join(missing))
        return True, ""

    # ------------------------------------------------------- orchestration hooks
    # Thin by design: they delegate to the proven shell runners for the current
    # platforms, so deployment behavior is byte-identical to the pre-refactor
    # tooling. A NEW platform implements these from scratch.
    def deploy(self):
        raise NotImplementedError

    def deploy_function(self):
        """Optional hook: deploy the platform's FUNCTION (beyond the control
        plane), e.g. OpenFaaS's 'hello' service which faas-cli deploys OUTSIDE
        the stack. No-op for platforms whose deploy() already includes it.
        The adapter protocol explicitly allows bespoke hooks per platform."""
        return None

    def teardown(self):
        raise NotImplementedError

    def scale(self, replicas):
        raise NotImplementedError

    def verify_cmd(self):
        raise NotImplementedError

    # ------------------------------------------------------------ harness argv
    def harness_argv(self, metric, total, concurrency, duration, warmup, repeat,
                     outdir, idle_w=None, cpu_count_override=None, host_cpu_list=None,
                     verify=False, no_quiet_gate=False, ambient_window_s=None,
                     max_ambient_cpu_pct=None):
        """Build the saqef_harness.py argv exactly as the proven shell runners do.

        verify=True emits the --verify variant (no total/concurrency/repeat).
        The quiet-gate knobs default to None/False so existing callers emit
        byte-identical argv (the quiet gate then runs with its harness-side
        defaults: 20 s window, 15% ceiling); the contamination A/B tool passes
        no_quiet_gate=True for its dirty leg.
        """
        if verify:
            cmd = ["python3", repo_script("saqef_harness.py"), "--verify", "--sampler", self.sampler,
                   "--url", self.url, "--platform", self.name,
                   "--cp-containers", self.cp_containers_arg()]
            if self.fn_images:
                cmd += ["--fn-images", self.fn_images_arg()]
            if self.fn_containers:
                cmd += ["--fn-containers", self.fn_containers_arg()]
            if self.cp_images:
                cmd += ["--cp-images", self.cp_images_arg()]
            if self.cp_labels:
                cmd += ["--cp-labels", self.cp_labels_arg()]
            if self.fn_labels:
                cmd += ["--fn-labels", self.fn_labels_arg()]
            if self.isolation and self.isolation.forbidden_services:
                cmd += ["--forbidden-services", ",".join(self.isolation.forbidden_services)]
            if self.isolation and self.isolation.forbidden_containers:
                cmd += ["--forbidden-containers", ",".join(self.isolation.forbidden_containers)]
            cmd += ["--verify-n", str(metric.get("defaults", {}).get("verify_n", self.verify_n)),
                    "--verify-budget-ms",
                    str(metric.get("defaults", {}).get("verify_budget_ms", self.verify_budget_ms))]
            return cmd

        cmd = ["python3", repo_script("saqef_harness.py"),
               "--url", self.url, "--platform", self.name,
               "--cp-containers", self.cp_containers_arg()]
        if self.fn_images:
            cmd += ["--fn-images", self.fn_images_arg()]
        if self.fn_containers:
            cmd += ["--fn-containers", self.fn_containers_arg()]
        if self.cp_images:
            cmd += ["--cp-images", self.cp_images_arg()]
        if self.cp_labels:
            cmd += ["--cp-labels", self.cp_labels_arg()]
        if self.fn_labels:
            cmd += ["--fn-labels", self.fn_labels_arg()]
        if self.isolation and self.isolation.forbidden_services:
            cmd += ["--forbidden-services", ",".join(self.isolation.forbidden_services)]
        if self.isolation and self.isolation.forbidden_containers:
            cmd += ["--forbidden-containers", ",".join(self.isolation.forbidden_containers)]
        # `is not None`, NOT truthy: an explicitly-passed 0 (e.g. --idle-w 0
        # for a machine that truly draws no idle package power, or a future
        # 0-based override) must still be forwarded. A bare `if idle_w:`
        # silently drops a real 0 and falls back to the harness's hardcoded
        # (wrong) 30 W default with no warning -- the opposite of this
        # project's fail-loud discipline.
        if idle_w is not None:
            cmd += ["--idle-w", str(idle_w)]
        if cpu_count_override is not None:
            cmd += ["--cpu-count-override", str(cpu_count_override)]
        if host_cpu_list:
            cmd += ["--host-cpu-list", str(host_cpu_list)]
        if no_quiet_gate:
            cmd += ["--no-quiet-gate"]
        if ambient_window_s is not None:
            cmd += ["--ambient-window-s", str(ambient_window_s)]
        if max_ambient_cpu_pct is not None:
            cmd += ["--max-ambient-cpu-pct", str(max_ambient_cpu_pct)]
        cmd += ["--total", str(total), "--concurrency", str(concurrency),
                "--duration", str(duration), "--warmup", str(warmup),
                "--repeat", str(repeat)]
        cmd += ["--sampler", self.sampler]
        if self.delta_check:
            cmd += ["--delta-check"]
        cmd += ["--loadgen", self.loadgen]
        cmd += ["--outdir", outdir]
        return cmd

    def cp_images_arg(self):
        return ",".join(self.cp_images)

    def cp_labels_arg(self):
        return ",".join(self.cp_labels)

    def fn_labels_arg(self):
        return ",".join(self.fn_labels)


def load_adapter(name):
    """Registry: import platforms/<name>.py and return its Adapter instance."""
    import importlib
    mod = importlib.import_module("platforms.%s" % name)
    return mod.adapter
