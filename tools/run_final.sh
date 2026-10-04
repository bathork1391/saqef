#!/usr/bin/env bash
# Final corpus driver (runbook 24.8). ONE headless session, all four platforms,
# one frozen box state, per-leg checkpointing. Replaces the remeasure_shares_
# protocol; does not modify run_tier1_conc.sh or the frozen remeasure tag.
#
# What is different from remeasure_shares_ (all deliberate, all in 24.8):
#   * dockerd json-file logs capped at max-size=64k / max-file=1 (24.8.1); the
#     cap applies to EVERY platform because k3s here runs `k3s server --docker`.
#   * headless: no graphical session, no agent, checked as a hard gate.
#   * idle_w recalibrated in THIS session state (5 stack states x 3 x 60 s),
#     never inherited.
#   * OpenWhisk legs: --repeat 6 --discard-warmup 1 (run_1 is a post-deploy
#     outlier: cp CPU-s 2.1-2.4x steady in every OW leg).
#   * psys recorded next to package RAPL; per-thread JVM CPU recorded on OW legs.
#   * each leg is its own run_lock_session invocation: a failed leg is retried
#     ONCE (stamp suffix _r2) and the session continues; every verdict is
#     appended to results/final_session/checkpoint.tsv as it happens.
#
# Run it (as root, from a system unit so it survives leaving the desktop):
#   sudo bash tools/run_final.sh --check      # pre-flight only, prints every problem
#   sudo bash tools/run_final.sh --dry-run    # pre-flight + the plan
#   see runbook 24.8.4 for the systemd-run launch command.
#
# --workload payload (runbook §28, W1): same session machinery, but the four handlers are
# swapped for the payload echo (workloads/payload/), every request is a text/plain POST of
# 1 KiB / 64 KiB / 512 KiB, concurrency is 1, 4, 8 on all platforms, and results go under
# the payload_ prefix. The handlers are restored when the session exits, however it exits.
#
# --workload memory (runbook §30, W2): every platform at c = 1/4/8 with two handler arms in the
# same session, mem_cache (256 KiB buffers, cache-resident) and mem_dram (64 MiB buffers, DRAM-
# bound), each a 5 ms copy loop. Both arms of a platform/c run back to back, order alternating.
# The handlers are swapped (and OF/Kn images rebuilt) before each leg whose arm differs from
# the last; OW runs with SAQEF_OW_LOGSTORE=driver (§29.3). Prefix mem_. Handlers restored on exit.
#
# --workload burst (runbook §31, W3): Part A's CPU-bound handler (no swap), each platform under
# three arrival patterns in the same session: steady (closed loop, c = 8, as Part A) and bursts
# of 100 or 500 simultaneous requests with 1 s idle after each (SAQEF_BURST). Failed requests are
# measured, not gated (only a run with no success is unusable). OW with the driver log store.
# Always with --hygiene (§30.11). Prefix burst_.
#
# --arm owlog29 (runbook §29): OpenWhisk only, CPU-bound handler, c = 1/4/8, each c measured
# twice in the same session: the standalone's default log collector (one `docker logs` per
# activation) and SAQEF_OW_LOGSTORE=driver (none). Order alternates per c. Prefix owlog29_.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Root's PATH, pinned. It resolves /usr/local/bin/hey -> /root/go/bin/hey, the build every
# earlier `sudo` leg used. ~/go/bin/hey is a DIFFERENT build and must not be picked up.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
HEY_SHA256="952be8d731a8fd264a75cd40c08fde38a0737c5be7a8e00a2b512b210484b3f3"   # /root/go/bin/hey
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

PFX="final_"
TOTAL=3000
LIGHT_REPEAT=5
OW_REPEAT=6
OW_DISCARD=1
LOG_MAX_SIZE="64k"
WAIT_HEADLESS_S=1200     # how long to wait for the desktop/agents to go away
WAIT_KNATIVE_S=900       # how long to wait for knative-serving to be Ready after a docker restart
RESTORE_GUI=1

CHECK_ONLY=0 DRY_RUN=0 WORKLOAD=cpu AMEND="" ARM="" RERUN="" HYGIENE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK_ONLY=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --no-restore-gui) RESTORE_GUI=0 ;;
        --workload) WORKLOAD="${2:-}"; shift ;;
        --workload=*) WORKLOAD="${1#*=}" ;;
        --amend) AMEND="${2:-}"; shift ;;
        --arm) ARM="${2:-}"; shift ;;
        --rerun) RERUN="${2:-}"; shift ;;
        --hygiene) HYGIENE=1 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
case "$WORKLOAD" in
    cpu) ;;
    payload) PFX="payload_" ;;
    memory) PFX="mem_" ;;
    burst) PFX="burst_"; HYGIENE=1 ;;   # §30.11: every session from W3 on prunes docker leftovers
    *) echo "unknown --workload '$WORKLOAD' (cpu|payload|memory|burst)" >&2; exit 2 ;;
esac
# W3 legs (runbook §31.2), in run order: "platform arm". Arms: steady = closed loop c = 8;
# b100 / b500 = bursts of 100 / 500 simultaneous requests, BURST_GAP_S idle after each.
# Order alternates by platform so no arm is always first.
BURST_GAP_S=1
BURST_LEGS=()
if [ "$WORKLOAD" = burst ]; then
    BURST_LEGS=("of steady" "of b100" "of b500" "fn b500" "fn b100" "fn steady"
                "kn steady" "kn b100" "kn b500" "ow b500" "ow b100" "ow steady")
fi
burst_size() { case "$1" in b100) echo 100 ;; b500) echo 500 ;; *) echo "" ;; esac; }
# W2 legs (runbook §30.2), in run order: "c platform arm". For each c, each platform's two arms
# run back to back; which arm goes first alternates with the platform and with c, so neither
# arm is always first (a slow drift cannot line up with one arm).
MEM_LEGS=()
if [ "$WORKLOAD" = memory ]; then
    ci=0
    for c in 1 4 8; do
        pi=0
        for p in of fn kn ow; do
            if [ $(( (ci + pi) % 2 )) = 0 ]; then MEM_LEGS+=("$c $p cache" "$c $p dram")
            else MEM_LEGS+=("$c $p dram" "$c $p cache"); fi
            pi=$((pi + 1))
        done
        ci=$((ci + 1))
    done
fi
# Rerun of a whole W2 session under a new prefix (runbook §30.8): mem<N>_, never mem_, so the
# closed session can not be overwritten or pooled by a `--prefix mem_` glob. Implies --hygiene.
if [ -n "$RERUN" ]; then
    [ "$WORKLOAD" = memory ] || { echo "--rerun needs --workload memory" >&2; exit 2; }
    [[ "$RERUN" =~ ^[2-9]$ ]] || { echo "--rerun takes 2-9" >&2; exit 2; }
    PFX="mem${RERUN}_"; HYGIENE=1
fi
mem_kib() { case "$1" in cache) echo 256 ;; dram) echo 65536 ;; esac; }
PAYLOAD_SIZES=(1k 64k 512k)
# Amendments: a fixed, named set of legs, run under their own prefix so they can never be
# mistaken for the main session. Each must be pre-registered in the COMMITTED runbook
# (pre-flight check 7). Not a generic leg picker: §28.4 rule 2 forbids ad hoc re-runs.
AMEND_LEGS=()
if [ -n "$AMEND" ]; then
    [ "$WORKLOAD" = payload ] || { echo "--amend needs --workload payload" >&2; exit 2; }
    case "$AMEND" in
        # Kn c=8 at 1k/64k (no citable leg on 2026-10-03) + Kn 512k c=8 as the bridge cell
        28.8) AMEND_LEGS=("1k 8 kn" "64k 8 kn" "512k 8 kn") ;;
        *) echo "unknown amendment '$AMEND' (known: 28.8)" >&2; exit 2 ;;
    esac
    PFX="payload_amend${AMEND/./_}_"
fi
# Arms: a fixed, named comparison under its own prefix, pre-registered in the committed
# runbook (pre-flight check 7). ARM_LEGS entries: "c logstore".
ARM_LEGS=()
if [ -n "$ARM" ]; then
    [ "$WORKLOAD" = cpu ] && [ -z "$AMEND" ] || { echo "--arm needs --workload cpu and no --amend" >&2; exit 2; }
    case "$ARM" in
        # §29: alternating order per c, so a slow drift cannot line up with one log store
        owlog29) ARM_LEGS=("1 cli" "1 driver" "4 driver" "4 cli" "8 cli" "8 driver") ;;
        *) echo "unknown arm '$ARM' (known: owlog29)" >&2; exit 2 ;;
    esac
    PFX="${ARM}_"
fi

SESS="$REPO/results/${PFX}session"
BOX="$REPO/results/${PFX}box_state"
CKPT="$SESS/checkpoint.tsv"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
say() { echo "[$(ts)] $*"; }
problems=()
bad() { problems+=("$1"); echo "  PROBLEM: $1"; }

# ----------------------------------------------------------------- pre-flight
graphical_sessions() {
    local s t out=""
    for s in $(loginctl list-sessions --no-legend 2>/dev/null | awk '{print $1}'); do
        t=$(loginctl show-session "$s" -p Type --value 2>/dev/null)
        case "$t" in x11|wayland|mir) out="$out $s($t)" ;; esac
    done
    systemctl is-active --quiet display-manager 2>/dev/null && out="$out display-manager"
    echo "$out"
}
agents_running() {
    pgrep -a -f '(^|/)(claude|opencode)( |$)' 2>/dev/null | grep -v -e pgrep -e run_final || true
}
knative_ready() {
    local notready
    notready=$(kubectl get pods -n knative-serving --no-headers 2>/dev/null \
        | awk '{split($2,a,"/"); if (a[1]!=a[2] || $3!="Running") print $1}')
    [ -n "$(kubectl get pods -n knative-serving --no-headers 2>/dev/null)" ] && [ -z "$notready" ]
}
epp_values() {
    cat /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference 2>/dev/null | sort | uniq -c | tr '\n' ' '
}

preflight() {
    problems=()
    echo "== pre-flight ($(ts))"
    [ "$(id -u)" -eq 0 ] || bad "must run as root (sudo / systemd-run)"
    local heyp heys
    heyp=$(readlink -f "$(command -v hey 2>/dev/null)" 2>/dev/null)
    if [ -z "$heyp" ]; then bad "hey not on PATH ($PATH)"
    else
        heys=$(sha256sum "$heyp" | cut -d' ' -f1)
        [ "$heys" = "$HEY_SHA256" ] || bad "hey resolves to $heyp sha256 $heys, not the corpus build $HEY_SHA256"
        echo "  hey: $heyp (corpus build)"
    fi

    # 1. log cap: configured, loaded, and present on running containers
    if ! python3 - "$LOG_MAX_SIZE" <<'PY'
import json, sys
want = sys.argv[1]
try:
    c = json.load(open("/etc/docker/daemon.json"))
except Exception as e:
    sys.exit("daemon.json unreadable: %s" % e)
o = c.get("log-opts", {})
ok = c.get("log-driver", "json-file") == "json-file" and o.get("max-size") == want and str(o.get("max-file")) == "1"
sys.exit(0 if ok else "daemon.json log-opts %r, want max-size=%s max-file=1" % (o, want))
PY
    then bad "/etc/docker/daemon.json does not set json-file max-size=$LOG_MAX_SIZE max-file=1"
    else
        local dj_m dk_m
        dj_m=$(stat -c %Y /etc/docker/daemon.json)
        dk_m=$(systemctl show docker --timestamp=unix -p ActiveEnterTimestamp --value | tr -d @)
        [ "$dk_m" -gt "$dj_m" ] || bad "dockerd started before daemon.json was written -- restart docker"
        local cid ms
        cid=$(docker ps -q --filter name=k8s_ | head -1)
        if [ -z "$cid" ]; then bad "no running k8s_* container to verify the log cap on"
        else
            ms=$(docker inspect -f '{{index .HostConfig.LogConfig.Config "max-size"}}' "$cid" 2>/dev/null)
            [ "$ms" = "$LOG_MAX_SIZE" ] || bad "running k8s_* container has max-size='$ms' (want $LOG_MAX_SIZE) -- pods predate the restart"
        fi
        echo "  log cap: daemon.json + docker restart + live containers checked"
    fi

    # 2. Knative substrate Ready (it is recreated by the docker restart)
    if knative_ready; then echo "  knative-serving: all pods Ready"
    else bad "knative-serving pods not all Ready"; fi

    # 3. frequency policy: recorded, and uniform
    if command -v powerprofilesctl >/dev/null; then
        [ "$(powerprofilesctl get 2>/dev/null)" = performance ] || powerprofilesctl set performance 2>/dev/null || true
        [ "$(powerprofilesctl get 2>/dev/null)" = performance ] || bad "power profile is '$(powerprofilesctl get 2>/dev/null)', not performance"
    fi
    local epp; epp=$(epp_values)
    case "$epp" in
        *performance*) [ "$(echo "$epp" | wc -w)" -eq 2 ] || bad "EPP not uniform across CPUs: $epp" ;;
        *) bad "EPP is not performance: $epp" ;;
    esac
    echo "  EPP: $epp governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor) no_turbo: $(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null)"

    # 4. no previous final_ results (run_lock_session refuses to clobber anyway)
    local collide
    collide=$(ls -d "$REPO"/results/*_cpubound_lock_${PFX}* "$REPO"/results/lock_session_${PFX}* 2>/dev/null || true)
    [ -z "$collide" ] || bad "results for prefix $PFX already exist: $(echo $collide | tr ' ' ',')"

    # 5. measurement-path code committed (the run must correspond to a commit)
    local dirty
    # The handlers are part of the measured path too: a payload session swaps them in the
    # working tree, so they must start clean (go.sh restores a leftover swap itself).
    dirty=$(git -c safe.directory="$REPO" -C "$REPO" status --porcelain -- saqef saqef_harness.py platforms tools/run_lock_session.sh tools/run_final.sh tools/jvm_thread_sampler.py tools/workload.sh workloads hello OF_FUNCTION KNATIVE_FUNCTION OW_FUNCTION 2>&1) || bad "git status failed: $dirty"
    [ -z "$dirty" ] || bad "uncommitted measurement-path changes: $(echo "$dirty" | tr '\n' ';')"

    # 7. an amendment runs only if its pre-registration is committed. Read the committed
    # runbook once: piping `git show` into `grep -q` under pipefail races (grep exits on the
    # match, git gets SIGPIPE, the pipeline returns 141 and a registered line reads as missing).
    local prereg
    prereg=$(git -c safe.directory="$REPO" -C "$REPO" show HEAD:TROUBLESHOOTING_RUNBOOK.md 2>/dev/null) \
        || bad "cannot read the committed runbook (git show HEAD:TROUBLESHOOTING_RUNBOOK.md)"
    if [ -n "$AMEND" ] && ! grep -qF "Amendment $AMEND: pre-registered" <<<"$prereg"; then
        bad "amendment $AMEND is not pre-registered in the committed runbook (need the line 'Amendment $AMEND: pre-registered')"
    fi
    if [ "$WORKLOAD" = memory ] && ! grep -qF "Workload memory: pre-registered" <<<"$prereg"; then
        bad "W2 is not pre-registered in the committed runbook (need the line 'Workload memory: pre-registered')"
    fi
    if [ -n "$RERUN" ] && ! grep -qF "Workload memory rerun $RERUN: pre-registered" <<<"$prereg"; then
        bad "W2 rerun $RERUN is not pre-registered in the committed runbook (need the line 'Workload memory rerun $RERUN: pre-registered')"
    fi
    if [ "$WORKLOAD" = burst ] && ! grep -qF "Workload burst: pre-registered" <<<"$prereg"; then
        bad "W3 is not pre-registered in the committed runbook (need the line 'Workload burst: pre-registered')"
    fi
    if [ -n "$ARM" ] && ! grep -qF "Arm $ARM: pre-registered" <<<"$prereg"; then
        bad "arm $ARM is not pre-registered in the committed runbook (need the line 'Arm $ARM: pre-registered')"
    fi

    # 6. headless + no agents
    local g ag
    g=$(graphical_sessions); ag=$(agents_running)
    [ -z "$g" ] || bad "graphical session(s) still up:$g"
    [ -z "$ag" ] || bad "agent process(es) running: $(echo "$ag" | head -3 | tr '\n' ';')"
}

# Wait (bounded) for the conditions the operator creates AFTER launching:
# leaving the desktop, quitting agents, Knative coming back after the restart.
wait_for_box() {
    local waited=0
    while [ "$waited" -lt "$WAIT_HEADLESS_S" ]; do
        if [ -z "$(graphical_sessions)" ] && [ -z "$(agents_running)" ] && knative_ready; then
            say "box headless, no agents, knative Ready (after ${waited}s); settling 60 s"
            sleep 60
            return 0
        fi
        sleep 10; waited=$((waited + 10))
    done
    return 1
}

snapshot_box() {   # pre|post -- two files, so the post snapshot cannot overwrite the pre one
    mkdir -p "$BOX"
    {
        echo "ts_utc: $(ts)"
        echo "git: $(git -c safe.directory="$REPO" -C "$REPO" describe --always --dirty --tags 2>/dev/null) $(git -c safe.directory="$REPO" -C "$REPO" rev-parse HEAD)"
        echo "workload: $WORKLOAD"
        echo "handlers: $(cd "$REPO" && sha256sum hello/func.py OF_FUNCTION/handler.py OF_FUNCTION/index.py KNATIVE_FUNCTION/app.py OW_FUNCTION/hello.py | awk '{printf "%s=%s ", $2, substr($1,1,12)}')"
        echo "uname: $(uname -a)"
        echo "hey: $(readlink -f "$(command -v hey)") $(sha256sum "$(readlink -f "$(command -v hey)")" | cut -d' ' -f1)"
        echo "power_profile: $(powerprofilesctl get 2>/dev/null)"
        echo "epp: $(epp_values)"
        echo "governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
        echo "no_turbo: $(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null)"
        echo "default_target: $(systemctl get-default) active_graphical: $(systemctl is-active graphical.target)"
        echo "daemon.json: $(tr -d '\n' < /etc/docker/daemon.json)"
        echo "docker_started: $(systemctl show docker -p ActiveEnterTimestamp --value)"
        echo "k3s_exec: $(systemctl show k3s -p ExecStart --value | tr -s ' ' | head -c 300)"
        echo "docker_objects: images=$(docker images -aq | wc -l) dangling=$(docker images -qf dangling=true | wc -l) volumes=$(docker volume ls -q | wc -l) containers=$(docker ps -aq | wc -l)"
        echo "sessions:"; loginctl list-sessions --no-legend
        echo "temps:"; for z in /sys/class/thermal/thermal_zone*; do echo "  $(cat $z/type) $(cat $z/temp)"; done
    } > "$BOX/box_state_$1.txt" 2>&1
    cp /etc/docker/daemon.json "$BOX/daemon.json" 2>/dev/null || true
}

# Docker leftovers raise the daemons' idle CPU: every fnserver and OpenFaaS deploy leaves an
# anonymous volume, every handler rebuild a dangling image. 172 volumes + 153 dangling images
# put containerd + dockerd at ~1.3 cores idle and failed 16 W2 legs on the quiet gate; pruning
# them took it to ~0.2 core (runbook §30.7 B). Both prunes skip anything a container uses.
# Runs only with --hygiene (default off; closed sessions unchanged), before each leg attempt,
# so the leg's own settle + quiet gate see its aftermath.
box_hygiene() {   # label
    [ "$HYGIENE" = 1 ] || return 0
    local v i
    v=$(docker volume prune -f 2>&1 | grep -c '^[0-9a-f]\{64\}$')
    i=$(docker image prune -f 2>&1 | grep -ci '^deleted: ')
    say "    hygiene ($1): removed $v unused anonymous volume(s), $i dangling image layer(s); now $(docker volume ls -q | wc -l) volume(s), $(docker images -aq | wc -l) image(s)"
}

pkg_temp() {
    local z
    for z in /sys/class/thermal/thermal_zone*; do
        [ "$(cat $z/type)" = x86_pkg_temp ] && { echo $(( $(cat $z/temp) / 1000 )); return; }
    done
    echo NA
}

# ----------------------------------------------------------------- legs
SAQEF="python3 $REPO/saqef"
cleanup_platform() {
    $SAQEF teardown --platform "$1" >/dev/null 2>&1 || true
    if [ "$1" = knative ]; then
        local w=0
        while [ $w -lt 360 ] && docker ps --format '{{.Names}}' | grep -qE 'user-container|queue-proxy|hello-0000'; do
            sleep 5; w=$((w + 5))
        done
    fi
    sleep 5
}

leg_verdict() {   # stamp platform -> "gates_ok share rps"
    python3 - "$REPO" "$1" "$2" <<'PY'
import json, os, sys
repo, stamp, plat = sys.argv[1:]
try:
    d = json.load(open(os.path.join(repo, "results", "lock_session_%s" % stamp, "lock_summary.json")))
    p = d["platforms"][plat]
    s = json.load(open(os.path.join(repo, p["outdir"], "summary.json")))
    print(p.get("gates_ok"), p.get("cp_dynamic_share_pct"), s.get("throughput_rps"))
except Exception as e:
    print("False NA NA")
PY
}

# Poll /proc/stat every 5 s until the box is <= 10 % busy (margin under the harness's
# 15 % quiet gate), at most 180 s. On timeout carry on: the quiet gate still decides.
wait_quiet() {
    local w=0 b
    while [ $w -lt 180 ]; do
        b=$(python3 - <<'PY'
import time
def busy():
    v = list(map(int, open("/proc/stat").readline().split()[1:]))
    return sum(v) - v[3] - v[4], sum(v)
b0, t0 = busy(); time.sleep(5); b1, t1 = busy()
print(round(100.0 * (b1 - b0) / max(1, t1 - t0), 1))
PY
)
        w=$((w + 5))
        if python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) <= 10.0 else 1)" "$b"; then
            say "    box quiet before retry: ${b}% busy after ${w}s"; return 0
        fi
    done
    say "    box still ${b}% busy after 180s; retrying anyway (the quiet gate decides)"
}

# Expected OW activation-store growth: ~2 bytes retained per payload byte (JVM UTF-16
# strings), measured 2026-10-03; at 512 KiB the ~8.5 GB heap runs out near 8,000 activations.
ow_heap_note() {   # body_file repeat total
    local b
    b=$(stat -c %s "$1" 2>/dev/null) || return 0
    say "    OW expected retained: $(( 2 * b * $2 * $3 / 1024 / 1024 )) MB over $2 x $3 activations (heap dies near 8500 MB)"
}

# run_one STAMP PLATFORM(short) PLATFORM(long) ARGS...
run_one() {
    local stamp="$1" short="$2" long="$3"; shift 3
    local attempt rc v t0 t1 st sampler
    for attempt in 1 2; do
        st="$stamp"; [ "$attempt" = 2 ] && st="${stamp}_r2"
        t0=$(pkg_temp)
        box_hygiene "$st"
        say ">>> leg $st ($long) attempt $attempt"
        sampler=""
        if [ "$short" = ow ]; then
            python3 "$REPO/tools/jvm_thread_sampler.py" --out "$SESS/jvm_threads_${st}.csv" &
            sampler=$!
        fi
        bash "$REPO/tools/run_lock_session.sh" --stamp "$st" --platforms "$short" \
            --total "$TOTAL" --cpu-probe 60 --rapl-fit-warn "$@" \
            >> "$SESS/leg_${st}.log" 2>&1
        rc=$?
        [ -n "$sampler" ] && { kill "$sampler" 2>/dev/null; wait "$sampler" 2>/dev/null; }
        t1=$(pkg_temp)
        v=$(leg_verdict "$st" "$long")
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(ts)" "$st" "$long" "$attempt" "$rc" "$v" "$t0" "$t1" \
            | tr ' ' '\t' >> "$CKPT"
        say "    rc=$rc verdict(gates_ok share rps)=$v pkg_temp ${t0}->${t1}C"
        if [ "$rc" = 0 ] && [ "${v%% *}" = True ]; then return 0; fi
        cleanup_platform "$long"
        # Settle before the retry: right after a 16-pod Knative teardown containerd is still
        # busy and the _r2 leg fails the quiet gate (2026-10-03: 1kc8_kn_r2, 64kc8_kn_r2).
        [ "$attempt" = 1 ] && wait_quiet
    done
    say "    leg $stamp FAILED twice -- recorded, not retried again (24.8.3 stop rule)"
    return 1
}

read_calib() {   # calib_dir state -> median
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["median_w"])' "$1/idle_w_$2.txt"
}

# ----------------------------------------------------------------- main
if [ "$CHECK_ONLY" = 1 ] || [ "$DRY_RUN" = 1 ]; then
    preflight
    if [ "$DRY_RUN" = 1 ]; then
        echo; echo "== plan (workload: $WORKLOAD)"
        if [ "$WORKLOAD" = payload ]; then
            echo "  swap handlers -> workloads/payload, rebuild OF/Kn images, write bodies, probe all 4 platforms"
        elif [ "$WORKLOAD" = memory ]; then
            echo "  probe both arms on all 4 platforms (swap, rebuild OF/Kn, deploy, GET, reply names the arm's kib)"
        else
            echo "  rebuild OF/Kn images from the working-tree handlers"
        fi
        [ "$HYGIENE" = 1 ] && echo "  hygiene: prune unused anonymous volumes + dangling images before calibration and before every leg attempt"
        echo "  ${PFX}calib: idle_w for bare, of, fn, kn, ow (3 x 60 s each), no leg"
        if [ -n "$ARM" ]; then
            for leg in "${ARM_LEGS[@]}"; do
                read -r c ls <<< "$leg"
                echo "  ${PFX}tier1ow${c}_${ls}: SAQEF_OW_LOGSTORE=$ls --concurrency $c --repeat $OW_REPEAT --discard-warmup $OW_DISCARD + JVM thread sampler (arm $ARM)"
            done
        elif [ "$WORKLOAD" = burst ]; then
            for leg in "${BURST_LEGS[@]}"; do
                read -r p arm <<< "$leg"
                b=$(burst_size "$arm")
                if [ -n "$b" ]; then load="SAQEF_BURST=${b}:${BURST_GAP_S} --concurrency $b"; else load="closed loop --concurrency 8"; fi
                if [ "$p" = ow ]; then
                    echo "  ${PFX}${p}_${arm}: $load SAQEF_OW_LOGSTORE=driver --repeat $OW_REPEAT --discard-warmup $OW_DISCARD + JVM thread sampler"
                else
                    echo "  ${PFX}${p}_${arm}: $load --repeat $LIGHT_REPEAT"
                fi
            done
        elif [ "$WORKLOAD" = memory ]; then
            for leg in "${MEM_LEGS[@]}"; do
                read -r c p arm <<< "$leg"
                if [ "$p" = ow ]; then
                    echo "  ${PFX}c${c}_${p}_${arm}: mem_${arm} (kib=$(mem_kib "$arm")) SAQEF_OW_LOGSTORE=driver --concurrency $c --repeat $OW_REPEAT --discard-warmup $OW_DISCARD + JVM thread sampler"
                else
                    echo "  ${PFX}c${c}_${p}_${arm}: mem_${arm} (kib=$(mem_kib "$arm")) --concurrency $c --repeat $LIGHT_REPEAT"
                fi
            done
            echo "  restore handlers + rebuild OF/Kn images"
        elif [ -n "$AMEND" ]; then
            for leg in "${AMEND_LEGS[@]}"; do
                read -r sz c p <<< "$leg"
                echo "  ${PFX}${sz}c${c}_${p}: POST body_${sz} --concurrency $c --repeat $LIGHT_REPEAT (amendment $AMEND)"
            done
            echo "  unmeasured: OW execsnoop trace, 64k c=4 (runbook §27.12 a)"
            echo "  restore handlers + rebuild OF/Kn images"
        elif [ "$WORKLOAD" = payload ]; then
            for sz in "${PAYLOAD_SIZES[@]}"; do
                for c in 1 4 8; do for p in of fn kn; do
                    echo "  ${PFX}${sz}c${c}_${p}: POST body_${sz} --concurrency $c --repeat $LIGHT_REPEAT"; done; done
                for c in 1 4 8; do
                    echo "  ${PFX}${sz}ow${c}: POST body_${sz} --concurrency $c --repeat $OW_REPEAT --discard-warmup $OW_DISCARD + JVM thread sampler"; done
            done
            echo "  unmeasured: OW execsnoop trace, 64k c=4, 60 s (runbook §27.12 a)"
            echo "  restore handlers + rebuild OF/Kn images"
        else
            for c in 1 2 4 8; do for p in of fn kn; do
                echo "  ${PFX}tier1c${c}_${p}: --concurrency $c --repeat $LIGHT_REPEAT"; done; done
            for c in 1 4 8; do
                echo "  ${PFX}tier1ow${c}: --concurrency $c --repeat $OW_REPEAT --discard-warmup $OW_DISCARD + JVM thread sampler"; done
        fi
    fi
    echo
    if [ "${#problems[@]}" -eq 0 ]; then echo "PRE-FLIGHT OK"; exit 0; fi
    echo "PRE-FLIGHT: ${#problems[@]} problem(s). Graphical-session/agent problems are expected"
    echo "while you are still at the desktop: the real run WAITS for those to clear."
    exit 1
fi

mkdir -p "$SESS"
exec >> "$SESS/session.log" 2>&1
say "final session start (workload $WORKLOAD), repo $REPO"
# Safety net 1: whatever happens from here -- normal end, abort, crash, or being
# killed by the RuntimeMaxSec watchdog go.sh sets -- the desktop comes back.
restore_handlers() {
    [ "$WORKLOAD" = cpu ] || [ "$WORKLOAD" = burst ] && return 0
    # Never rebuild after a failed restore: that bakes the swapped handlers into the images.
    if bash "$REPO/tools/workload.sh" restore; then
        bash "$REPO/tools/workload.sh" build || true
    else
        say "ERROR: handler restore failed -- images NOT rebuilt; next pre-flight will refuse to start"
    fi
}
restore_gui() {
    [ "$RESTORE_GUI" = 1 ] || return 0
    systemctl stop saqef-guard.timer 2>/dev/null || true
    systemctl stop --no-block saqef-screen 2>/dev/null || true
    systemctl start --no-block display-manager || true
}
trap 'rc=$?; say "session exiting (rc=$rc); restoring handlers and desktop"; restore_handlers; restore_gui' EXIT
trap 'say "session terminated by signal"; exit 143' TERM INT HUP
if ! wait_for_box; then
    preflight
    say "ABORT: box never became headless/agent-free/Knative-Ready within ${WAIT_HEADLESS_S}s"
    exit 3
fi
preflight
if [ "${#problems[@]}" -ne 0 ]; then
    say "ABORT: pre-flight failed (${#problems[@]} problem(s)); nothing measured"
    exit 4
fi
if [ "$WORKLOAD" = payload ]; then
    say ">>> swap handlers to the payload echo"
    bash "$REPO/tools/workload.sh" swap payload || { say "ABORT: handler swap failed"; exit 6; }
fi
say ">>> rebuild OF/Kn images from the working-tree handlers"
bash "$REPO/tools/workload.sh" build || { say "ABORT: image build failed"; exit 6; }
if [ "$WORKLOAD" = memory ]; then
    # Both arms end to end on every platform before anything is measured: catches a handler
    # that does not deploy, an OOM at 128 MiB resident, or a stale image serving the other arm.
    for arm in cache dram; do
        say ">>> W2 probe: arm mem_${arm} (kib=$(mem_kib "$arm")) on all 4 platforms"
        bash "$REPO/tools/workload.sh" swap "mem_${arm}" >> "$SESS/probe.log" 2>&1 \
            && bash "$REPO/tools/workload.sh" build >> "$SESS/probe.log" 2>&1 \
            || { say "ABORT: swap/build for mem_${arm} failed -- see $SESS/probe.log"; exit 6; }
        if ! SAQEF_OW_LOGSTORE=driver bash "$REPO/tools/workload.sh" probe-mem "$(mem_kib "$arm")" >> "$SESS/probe.log" 2>&1; then
            say "ABORT: W2 probe failed for mem_${arm} -- see $SESS/probe.log; nothing measured"
            exit 7
        fi
    done
fi
if [ "$WORKLOAD" = payload ]; then
    BODIES="$SESS/bodies"
    bash "$REPO/tools/workload.sh" bodies "$BODIES"
    say ">>> payload probe: every platform must echo every body (catches size limits before any leg)"
    if ! bash "$REPO/tools/workload.sh" probe "$BODIES" >> "$SESS/probe.log" 2>&1; then
        say "ABORT: payload probe failed -- see $SESS/probe.log; nothing measured"
        exit 7
    fi
fi
box_hygiene "before calibration"
snapshot_box pre
printf 'ts_utc\tstamp\tplatform\tattempt\trc\tgates_ok\tshare_pct\trps\tpkg_temp_start_C\tpkg_temp_end_C\n' > "$CKPT"

declare -A LONG=([of]=openfaas [fn]=fn [kn]=knative [ow]=openwhisk)

# Calibrate all five stack states in the current (headless) state, with no leg:
# --platforms none runs check_preconditions + calibrate_all and measures nothing else.
say ">>> idle_w calibration (5 states x 3 x 60 s)"
bash "$REPO/tools/run_lock_session.sh" --stamp "${PFX}calib" --platforms none \
    >> "$SESS/calibration.log" 2>&1
CAL="$REPO/results/idle_w_calibration/lock_${PFX}calib"
if ! W_OF=$(read_calib "$CAL" openfaas) || ! W_FN=$(read_calib "$CAL" fn) \
   || ! W_KN=$(read_calib "$CAL" knative) || ! W_OW=$(read_calib "$CAL" openwhisk); then
    say "ABORT: idle_w calibration incomplete in $CAL -- no energy number would be valid"
    exit 5
fi
say "idle_w (this session): of=$W_OF fn=$W_FN kn=$W_KN ow=$W_OW bare=$(read_calib "$CAL" bare 2>/dev/null)"
IW=(--skip-idle-calib --idle-w-source "$CAL" --idle-w-of "$W_OF" --idle-w-fn "$W_FN" --idle-w-kn "$W_KN" --idle-w-ow "$W_OW")

failed=0
if [ -n "$ARM" ]; then
    say "=== arm $ARM: ${#ARM_LEGS[@]} OpenWhisk leg(s), CPU-bound, same protocol as final_ OW legs"
    for leg in "${ARM_LEGS[@]}"; do
        read -r c ls <<< "$leg"
        dur=300; [ "$c" = 1 ] && dur=420
        export SAQEF_OW_LOGSTORE="$ls"
        say "    OW activation log store for this leg: $ls"
        run_one "${PFX}tier1ow${c}_${ls}" ow openwhisk --concurrency "$c" --repeat "$OW_REPEAT" \
            --discard-warmup "$OW_DISCARD" --ow-duration "$dur" "${IW[@]}" \
            || failed=$((failed + 1))
        unset SAQEF_OW_LOGSTORE
    done
elif [ -n "$AMEND" ]; then
    say "=== amendment $AMEND: ${#AMEND_LEGS[@]} leg(s), same protocol as the main session"
    for leg in "${AMEND_LEGS[@]}"; do
        read -r sz c p <<< "$leg"
        export SAQEF_BODY_FILE="$BODIES/body_${sz}.txt"
        run_one "${PFX}${sz}c${c}_${p}" "$p" "${LONG[$p]}" --concurrency "$c" --repeat "$LIGHT_REPEAT" "${IW[@]}" \
            || failed=$((failed + 1))
    done
fi
if [ "$WORKLOAD" = burst ]; then
    say "=== W3 burst: ${#BURST_LEGS[@]} legs, steady / b100 / b500 per platform, gap ${BURST_GAP_S}s, OW with the driver log store"
    for leg in "${BURST_LEGS[@]}"; do
        read -r p arm <<< "$leg"
        b=$(burst_size "$arm")
        if [ -n "$b" ]; then export SAQEF_BURST="${b}:${BURST_GAP_S}"; conc="$b"
        else unset SAQEF_BURST; conc=8; fi
        say "    arrival pattern for this leg: $arm (SAQEF_BURST=${SAQEF_BURST:-unset}, concurrency $conc)"
        if [ "$p" = ow ]; then
            export SAQEF_OW_LOGSTORE=driver
            run_one "${PFX}${p}_${arm}" ow openwhisk --concurrency "$conc" --repeat "$OW_REPEAT" \
                --discard-warmup "$OW_DISCARD" --ow-duration 300 "${IW[@]}" \
                || failed=$((failed + 1))
            unset SAQEF_OW_LOGSTORE
        else
            run_one "${PFX}${p}_${arm}" "$p" "${LONG[$p]}" --concurrency "$conc" --repeat "$LIGHT_REPEAT" "${IW[@]}" \
                || failed=$((failed + 1))
        fi
        unset SAQEF_BURST
    done
elif [ "$WORKLOAD" = memory ]; then
    say "=== W2 memory: ${#MEM_LEGS[@]} legs, two arms per platform/c, OW with the driver log store"
    for leg in "${MEM_LEGS[@]}"; do
        read -r c p arm <<< "$leg"
        want=$(mem_kib "$arm")
        if [ "$(bash "$REPO/tools/workload.sh" variant 2>/dev/null)" != "$want" ]; then
            if ! { bash "$REPO/tools/workload.sh" swap "mem_${arm}" && bash "$REPO/tools/workload.sh" build; } >> "$SESS/swap.log" 2>&1; then
                say "ABORT: swap/build to mem_${arm} failed before ${PFX}c${c}_${p}_${arm} -- see $SESS/swap.log"
                exit 6
            fi
        fi
        [ "$(bash "$REPO/tools/workload.sh" variant 2>/dev/null)" = "$want" ] \
            || { say "ABORT: handlers are not the mem_${arm} arm before ${PFX}c${c}_${p}_${arm}"; exit 6; }
        export SAQEF_WORKLOAD_VARIANT="mem_${arm}" SAQEF_EXPECT_KIB="$want"
        say "    handler arm for this leg: mem_${arm} (SAQEF_MEM_KIB=$want)"
        if [ "$p" = ow ]; then
            export SAQEF_OW_LOGSTORE=driver
            say "    OW activation log store for this leg: driver (§29.3)"
            dur=300; [ "$c" = 1 ] && dur=420
            run_one "${PFX}c${c}_${p}_${arm}" ow openwhisk --concurrency "$c" --repeat "$OW_REPEAT" \
                --discard-warmup "$OW_DISCARD" --ow-duration "$dur" "${IW[@]}" \
                || failed=$((failed + 1))
            unset SAQEF_OW_LOGSTORE
        else
            run_one "${PFX}c${c}_${p}_${arm}" "$p" "${LONG[$p]}" --concurrency "$c" --repeat "$LIGHT_REPEAT" "${IW[@]}" \
                || failed=$((failed + 1))
        fi
        unset SAQEF_WORKLOAD_VARIANT SAQEF_EXPECT_KIB
    done
elif [ "$WORKLOAD" = payload ]; then
    [ -n "$AMEND" ] || for sz in "${PAYLOAD_SIZES[@]}"; do
        export SAQEF_BODY_FILE="$BODIES/body_${sz}.txt"
        say "=== payload $sz ($SAQEF_BODY_FILE)"
        for c in 1 4 8; do
            for p in of fn kn; do
                run_one "${PFX}${sz}c${c}_${p}" "$p" "${LONG[$p]}" --concurrency "$c" --repeat "$LIGHT_REPEAT" "${IW[@]}" \
                    || failed=$((failed + 1))
            done
        done
        for c in 1 4 8; do
            dur=300; [ "$c" = 1 ] && dur=420
            ow_heap_note "$SAQEF_BODY_FILE" "$OW_REPEAT" "$TOTAL"
            run_one "${PFX}${sz}ow${c}" ow openwhisk --concurrency "$c" --repeat "$OW_REPEAT" \
                --discard-warmup "$OW_DISCARD" --ow-duration "$dur" "${IW[@]}" \
                || failed=$((failed + 1))
        done
    done
    unset SAQEF_BODY_FILE
    # Unmeasured: which processes OpenWhisk spawns per activation (runbook §27.12 a).
    # Its own deploy, after every leg, so the tracer cannot touch a measured window.
    say ">>> OW execsnoop trace (unmeasured, 64k c=4)"
    if python3 "$REPO/saqef" deploy --platform openwhisk >> "$SESS/ow_execsnoop.log" 2>&1; then
        # bpftrace, not execsnoop-bpfcc: BCC cannot compile against kernel 7.0 headers.
        # join(args->argv) records the full command line (runbook 28.9: filename alone
        # showed one /usr/bin/docker per activation but not which subcommand).
        timeout 90 bpftrace -e 'tracepoint:syscalls:sys_enter_execve { time("%H:%M:%S "); printf("%d %d %s ", pid, curtask->real_parent->tgid, comm); join(args->argv); }' \
            > "$SESS/ow_execsnoop.txt" 2>> "$SESS/ow_execsnoop.log" &
        tr_pid=$!
        sleep 10
        hey -n 2000 -c 4 -m POST -T text/plain -D "$BODIES/body_64k.txt" \
            "$(python3 -c "import sys; sys.path.insert(0, '$REPO'); from platforms import get_adapter; print(get_adapter('openwhisk').url)")" \
            >> "$SESS/ow_execsnoop.log" 2>&1
        kill -INT "$tr_pid" 2>/dev/null; wait "$tr_pid" 2>/dev/null
        say "    execsnoop: $(grep -c . "$SESS/ow_execsnoop.txt" 2>/dev/null) lines for 2000 activations"
    else
        say "    OW deploy for the trace failed; skipped"
    fi
    cleanup_platform openwhisk
elif [ -z "$ARM" ]; then   # an arm runs only its own legs, never the whole CPU corpus
for c in 1 2 4 8; do
    for p in of fn kn; do
        run_one "${PFX}tier1c${c}_${p}" "$p" "${LONG[$p]}" --concurrency "$c" --repeat "$LIGHT_REPEAT" "${IW[@]}" \
            || failed=$((failed + 1))
    done
done
for c in 1 4 8; do
    dur=300; [ "$c" = 1 ] && dur=420
    run_one "${PFX}tier1ow${c}" ow openwhisk --concurrency "$c" --repeat "$OW_REPEAT" \
        --discard-warmup "$OW_DISCARD" --ow-duration "$dur" "${IW[@]}" \
        || failed=$((failed + 1))
done
fi

say "final session done: $failed leg(s) failed twice. Checkpoint: $CKPT"
echo "failed_legs=$failed" > "$SESS/DONE"
snapshot_box post
exit 0   # the EXIT trap restores the desktop
