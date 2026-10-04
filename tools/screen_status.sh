#!/usr/bin/env bash
# Text-mode status screen for an unattended session (started by go.sh on tty8).
# Stopping gdm leaves the Ubuntu/Dell boot splash, which looks like a hang; this
# keeps a short "running, don't power off" note plus progress on the console.
# go.sh starts this unit BEFORE stopping gdm (its own terminal dies with the
# desktop), so the switch to tty8 happens here once gdm is gone.
# Redraws once a minute, so its own CPU cost is negligible next to the legs.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARG1="${1:-}"
SESS="$REPO/results/${1:-final}_session"   # go.sh passes "payload" for a W1 night
# "latest" (chained sessions, go.sh --revisit): follow whichever session directory is newest
pick() { [ "${1:-}" = latest ] && SESS=$(ls -td "$REPO"/results/*_session 2>/dev/null | head -1); }
pick "${1:-}"
TTY=/dev/tty8
START=$(date '+%H:%M')
while systemctl is-active --quiet display-manager; do sleep 1; done   # never steal the screen from a live desktop
# 3 Oct: a bare "chvt 8" right after gdm stopped left the screen on gdm's emptied VT
# (black, blinking cursor) for the whole session -- chvt can block or lose the race
# while logind is still tearing the session VT down. So: retry with a timeout on
# every redraw, and also draw on whichever VT is in front, so the note is visible
# even if the switch never happens.
active() { cat /sys/class/tty/tty0/active 2>/dev/null; }
draw() {
    pick "${ARG1:-}"
    printf '\033[2J\033[H'
    echo "  SAQEF measurement session RUNNING   (started $START, now $(date '+%H:%M'))"
    echo
    echo "  The black screen / text mode is intentional. Do NOT press the power button."
    echo "  The desktop comes back by itself when the session ends (CPU ~2 h, payload ~4 h; watchdog 4 h / 6 h + 15 min)."
    echo "  To abort: Ctrl+Alt+F3 (Latitude: Ctrl+Alt+Fn+F3), log in, then  sudo bash $REPO/tools/go.sh --stop"
    echo
    echo "  == legs done"
    if [ -f "$SESS/checkpoint.tsv" ]; then
        tail -n +2 "$SESS/checkpoint.tsv" | awk -F'\t' '{printf "  %-28s %-6s rc=%s gates=%s\n", $2, $3, $5, $6}' | tail -n 12
    fi
    echo
    echo "  == last log lines"
    tail -n 6 "$SESS/session.log" 2>/dev/null | cut -c1-110 | sed 's/^/  /'
}
setterm --blank 0 --powersave off --cursor off > "$TTY" 2>/dev/null
# Force tty8 only until the switch has worked once. After that the user owns the
# console: forcing it on every redraw yanked them off tty3 mid-login when they
# tried to run go.sh --stop.
switched=
while :; do
    if [ -z "$switched" ]; then
        [ "$(active)" = tty8 ] || timeout 5 chvt 8 || echo "chvt 8 failed (active: $(active))"
        [ "$(active)" = tty8 ] && switched=1
    fi
    draw > "$TTY" 2>/dev/null
    a=$(active)
    if [ -z "$switched" ] && [ -n "$a" ] && [ "$a" != tty8 ]; then
        setterm --blank 0 --powersave off --cursor off > "/dev/$a" 2>/dev/null
        draw > "/dev/$a" 2>/dev/null
    fi
    sleep 30
done
