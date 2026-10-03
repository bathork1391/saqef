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

CHECK_ONLY=0 DRY_RUN=0 WORKLOAD=cpu AMEND=""
while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK_ONLY=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --no-restore-gui) RESTORE_GUI=0 ;;
        --workload) WORKLOAD="${2:-}"; shift ;;
        --workload=*) WORKLOAD="${1#*=}" ;;
        --amend) AMEND="${2:-}"; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
case "$WORKLOAD" in
    cpu) ;;
    payload) PFX="payload_" ;;
    *) echo "unknown --workload '$WORKLOAD' (cpu|payload)" >&2; exit 2 ;;
esac
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

    # 7. an amendment runs only if its pre-registration is committed
    if [ -n "$AMEND" ] && ! git -c safe.directory="$REPO" -C "$REPO" show HEAD:TROUBLESHOOTING_RUNBOOK.md 2>/dev/null \
            | grep -qF "Amendment $AMEND: pre-registered"; then
        bad "amendment $AMEND is not pre-registered in the committed runbook (need the line 'Amendment $AMEND: pre-registered')"
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
        echo "sessions:"; loginctl list-sessions --no-legend
        echo "temps:"; for z in /sys/class/thermal/thermal_zone*; do echo "  $(cat $z/type) $(cat $z/temp)"; done
    } > "$BOX/box_state_$1.txt" 2>&1
    cp /etc/docker/daemon.json "$BOX/daemon.json" 2>/dev/null || true
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
        else
            echo "  rebuild OF/Kn images from the working-tree handlers"
        fi
        echo "  ${PFX}calib: idle_w for bare, of, fn, kn, ow (3 x 60 s each), no leg"
        if [ -n "$AMEND" ]; then
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
    [ "$WORKLOAD" = cpu ] && return 0
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
if [ "$WORKLOAD" = payload ]; then
    BODIES="$SESS/bodies"
    bash "$REPO/tools/workload.sh" bodies "$BODIES"
    say ">>> payload probe: every platform must echo every body (catches size limits before any leg)"
    if ! bash "$REPO/tools/workload.sh" probe "$BODIES" >> "$SESS/probe.log" 2>&1; then
        say "ABORT: payload probe failed -- see $SESS/probe.log; nothing measured"
        exit 7
    fi
fi
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
if [ -n "$AMEND" ]; then
    say "=== amendment $AMEND: ${#AMEND_LEGS[@]} leg(s), same protocol as the main session"
    for leg in "${AMEND_LEGS[@]}"; do
        read -r sz c p <<< "$leg"
        export SAQEF_BODY_FILE="$BODIES/body_${sz}.txt"
        run_one "${PFX}${sz}c${c}_${p}" "$p" "${LONG[$p]}" --concurrency "$c" --repeat "$LIGHT_REPEAT" "${IW[@]}" \
            || failed=$((failed + 1))
    done
fi
if [ "$WORKLOAD" = payload ]; then
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
        timeout 90 bpftrace -e 'tracepoint:syscalls:sys_enter_execve { time("%H:%M:%S "); printf("%d %d %s %s\n", pid, curtask->real_parent->tgid, comm, str(args->filename)); }' \
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
else
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
