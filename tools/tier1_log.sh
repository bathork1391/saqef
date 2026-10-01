# Sourced by the tier1 drivers. Defines:
#   tier1_log_new  <repo>   -> sets $TIER1_LOG and $TIER1_LOG_LATEST
#
# Every session gets its OWN timestamped log. A fixed filename means the
# second session silently destroys the first one's record, which is exactly
# what happened: the OpenWhisk tier1ow8 run overwrote the log of the session
# that produced the earlier concurrency data, so the provenance of those
# numbers is no longer recoverable from the log at all.
#
# The well-known results/tier1_session.log path is kept as a SYMLINK to the
# newest session, so anything that follows it still works. An existing regular
# file at that path is dated and moved aside first, never clobbered.
tier1_log_new() {
  local repo="$1" stamp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  TIER1_LOG="$repo/results/tier1_session_${stamp}.log"
  TIER1_LOG_LATEST="$repo/results/tier1_session.log"

  if [ -f "$TIER1_LOG_LATEST" ] && [ ! -L "$TIER1_LOG_LATEST" ]; then
    # Preserve the previous session's log under its own name.
    mv -n "$TIER1_LOG_LATEST" \
       "$repo/results/tier1_session_pre_${stamp}.log" 2>/dev/null || true
  fi
}

# Call after the driver has run: point the stable path at this session.
tier1_log_publish() {
  local repo="$1"
  ln -sfn "$(basename "$TIER1_LOG")" "$TIER1_LOG_LATEST"
}