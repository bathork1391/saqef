#!/usr/bin/env bash
# Text-mode status screen for an unattended session (started by go.sh on tty8).
# Stopping gdm leaves the Ubuntu/Dell boot splash, which looks like a hang; this
# keeps a short "running, don't power off" note plus progress on the console.
# go.sh starts this unit BEFORE stopping gdm (its own terminal dies with the
# desktop), so the switch to tty8 happens here once gdm is gone.
# Redraws once a minute, so its own CPU cost is negligible next to the legs.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESS="$REPO/results/final_session"
TTY=/dev/tty8
START=$(date '+%H:%M')
while systemctl is-active --quiet display-manager; do sleep 1; done   # never steal the screen from a live desktop
setterm --blank 0 --powersave off --cursor off > "$TTY" 2>/dev/null
chvt 8
while :; do
    {
        printf '\033[2J\033[H'
        echo "  SAQEF measurement session RUNNING   (started $START, now $(date '+%H:%M'))"
        echo
        echo "  The black screen / text mode is intentional. Do NOT press the power button."
        echo "  The desktop comes back by itself when the session ends (~2 h, at most 4 h 15 min)."
        echo "  To abort: Ctrl+Alt+F3, log in, then  sudo bash $REPO/tools/go.sh --stop"
        echo
        echo "  == legs done"
        if [ -f "$SESS/checkpoint.tsv" ]; then
            tail -n +2 "$SESS/checkpoint.tsv" | awk -F'\t' '{printf "  %-28s %-6s rc=%s gates=%s\n", $2, $3, $5, $6}' | tail -n 12
        fi
        echo
        echo "  == last log lines"
        tail -n 6 "$SESS/session.log" 2>/dev/null | cut -c1-110 | sed 's/^/  /'
    } > "$TTY" 2>/dev/null
    sleep 30
done
