#!/usr/bin/env bash
# PostToolUse hook (Write|Edit): after a change to measurement-path code, run the unit tests.
# Exit 2 + stderr feeds a failure back to Claude; anything else is silent.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
f=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
case "$f" in
    "$REPO"/tools/*|"$REPO"/platforms/*|"$REPO"/saqef|"$REPO"/saqef_harness.py) ;;
    *) exit 0 ;;
esac
if ! out=$(cd "$REPO" && timeout 120 python3 -m unittest discover -s tests -q 2>&1); then
    { echo "Unit tests FAILED after editing ${f#$REPO/}:"; echo "$out" | tail -40; } >&2
    exit 2
fi
exit 0
