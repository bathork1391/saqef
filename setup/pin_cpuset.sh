#!/usr/bin/env bash
# pin_cpuset.sh - live cpuset pinning daemon for the SAQEF core-count-restriction
# protocol (bare-metal box emulating an N-vCPU machine via docker/cgroup pinning
# instead of a kernel nr_cpus=/maxcpus= reboot).
#
# Why this exists: Swarm's `deploy.resources` in compose is a CFS quota, not
# core-affinity, and has no declarative cpuset field, so pinning requires
# `docker update --cpuset-cpus` applied per-container after the fact. A ONE-SHOT
# pin is not enough: Fn's function containers are ephemeral (fnserver spins new
# ones under concurrent load) and any container created after a one-shot pin
# runs unpinned on the full machine, silently breaking the core-count
# restriction mid-benchmark. This loops for the life of the benchmark, pinning
# every running container (this box is dedicated to the study, so "every
# running container" is always in-scope - no image/name filtering needed) the
# moment it appears.
#
# Usage: sudo bash pin_cpuset.sh <cpuset e.g. "0,1"> &
#        PIN_PID=$!
#        <run the benchmark>
#        kill "$PIN_PID"; wait "$PIN_PID" 2>/dev/null
#
# Must run as the same user/sudo context that can call `docker update` (root).
# Companion knobs the harness needs so its OWN accounting agrees with the pin:
#   SAQEF_CPU_COUNT_OVERRIDE=<N>   (saturation ceiling denominator)
#   SAQEF_HOST_CPU_LIST=<cpuset>   (host_cpu_ticks numerator - same core list)
# and wrap the harness invocation itself in `taskset -c <cpuset>` so the load
# generator (hey) and the harness process are pinned too, not just containers.
set -uo pipefail

CPUSET="${1:?usage: pin_cpuset.sh <cpuset, e.g. 0,1>}"

echo "pin_cpuset: pinning all running containers to cpuset=$CPUSET (Ctrl-C / SIGTERM to stop)"
trap 'echo "pin_cpuset: stopping"; exit 0' TERM INT

while true; do
  for id in $(docker ps -q 2>/dev/null); do
    cs="$(docker inspect --format '{{.HostConfig.CpusetCpus}}' "$id" 2>/dev/null)"
    if [ "$cs" != "$CPUSET" ]; then
      if docker update --cpuset-cpus="$CPUSET" "$id" >/dev/null 2>&1; then
        name="$(docker inspect --format '{{.Name}}' "$id" 2>/dev/null | sed 's#^/##')"
        echo "pin_cpuset: pinned $name ($id) -> $CPUSET"
      fi
    fi
  done
  sleep 0.5
done
