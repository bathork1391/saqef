#!/usr/bin/env bash
#
# One-command wrapper for Tier-1 Experiment B: repair k3s if its API cert is
# clock-skewed, confirm the box is quiet enough to measure on, then run the
# sweep. Exists so the whole session is a single command with no memory of
# steps required.
#
#   bash tools/run_tier1_quiet.sh            # confirm before the 3h run
#   bash tools/run_tier1_quiet.sh --yes      # unattended
#   bash tools/run_tier1_quiet.sh --check    # preflight only, runs nothing
#
# MUST be run from a bare shell with agents quit. The harness re-checks the
# ambient gate before every single leg, so a contaminated box fails on its
# own -- this script just fails fast, before the k3s repair, instead of an
# hour later.
#
# Deliberately no `set -e`: each step is checked explicitly so a failure
# prints WHY instead of a bare non-zero exit.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRIVER="$REPO/tools/run_tier1_conc.sh"
LOG="$REPO/results/tier1_session.log"
AMBIENT_CEILING="${SAQEF_AMBIENT_CEILING:-15}"
K3S_WAIT_S="${SAQEF_K3S_WAIT_S:-180}"
ASSUME_YES=0
CHECK_ONLY=0
CERT_TAG="$(date +%Y%m%d-%H%M%S)"
CERT_BAK="/tmp/k3s-badcert-$CERT_TAG"

for a in "$@"; do
  case "$a" in
    -y|--yes)   ASSUME_YES=1 ;;
    --check)    CHECK_ONLY=1 ;;
    -h|--help)  sed -n '3,15p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (try --help)"; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
bad()  { printf '\n\033[31m!! %s\033[0m\n' "$*" >&2; }
ok()   { printf '   \033[32mok\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------- preflight
say "1/4  Preflight"

if [ ! -x "$DRIVER" ] && [ ! -f "$DRIVER" ]; then
  bad "driver not found at $DRIVER -- wrong checkout?"; exit 2
fi
ok "driver found: tools/run_tier1_conc.sh"

if command -v docker >/dev/null && docker ps >/dev/null 2>&1; then
  ok "docker reachable"
else
  bad "docker not reachable (is the docker daemon up?)"; exit 2
fi

# Advisory ambient check, same definition the harness gate uses. The harness
# still enforces this per leg; failing here just saves the k3s repair.
amb=$(python3 - <<'PY'
import time
def busy():
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:]]
    return sum(v), v[3] + v[4]          # total, idle+iowait
a = busy(); time.sleep(5); b = busy()
tot, idle = b[0] - a[0], b[1] - a[1]
print("%.1f" % (100.0 * (tot - idle) / tot) if tot else "0.0")
PY
)
if python3 -c "import sys; sys.exit(0 if float('${amb}') < ${AMBIENT_CEILING} else 1)"; then
  ok "ambient load ${amb}% (ceiling ${AMBIENT_CEILING}%)"
else
  bad "ambient load ${amb}% is OVER the ${AMBIENT_CEILING}% ceiling."
  bad "Something is busy. Top CPU consumers right now:"
  ps -eo pcpu,comm --sort=-pcpu 2>/dev/null | head -6 | sed 's/^/     /' >&2
  bad "Quit your editor/agents/terminals and re-run this script."
  bad "(A serverless box is mostly idle by design; ~16% means a bench is"
  bad " already running or an agent session is live.)"
  exit 3
fi

# Leftover platform state would fail the driver's own isolation guard. Warn
# rather than fail, because the driver is the authority here.
leftover=$(docker ps -a --format '{{.Names}}' 2>/dev/null \
           | grep -vE '^(k8s_|k3s|registry)' || true)
if [ -n "$leftover" ]; then
  info "note: containers outside the k3s substrate: $(echo $leftover)"
  info "      the driver will refuse if these overlap a platform it deploys"
fi
if ls -d "$REPO"/results/*tier1* >/dev/null 2>&1; then
  info "note: tier1* result dirs already exist -- the driver refuses to"
  info "      clobber a stamp, so a re-run needs fresh stamps or a cleanup"
fi

# ------------------------------------------------------------- k3s repair
say "2/4  k3s API health"

k3s_ok() { sudo k3s kubectl get node >/dev/null 2>&1; }
sudo -v >/dev/null 2>&1 || true          # cache creds so we never stall mid-run

# `systemctl is-active` PRINTS the state but EXITS NON-ZERO for states like
# 'activating', so `x=$(... ) || x=unknown` yields "activating\nunknown". Take
# stdout and fall back only when it is genuinely empty.
k3s_state=$(systemctl is-active k3s 2>/dev/null)
[ -n "$k3s_state" ] || k3s_state="unknown"

if k3s_ok; then
  ok "k3s API healthy (service: $k3s_state) -- no repair needed"
elif [ "$k3s_state" = "inactive" ] || [ "$k3s_state" = "failed" ]; then
  info "k3s is $k3s_state (not running) -- starting it"
  sudo systemctl start k3s
elif [ "$k3s_state" = "activating" ]; then
  # Could be a healthy slow start. Give it a moment before concluding the
  # worst, so we don't needlessly bounce a stack that was about to come up.
  info "k3s is 'activating' and the API is not answering yet -- waiting 20s"
  for _ in 1 2 3 4; do sleep 5; k3s_ok && break; done
  if k3s_ok; then
    ok "k3s API came up on its own after 20s (no repair needed)"
    k3s_state="active"
  else
    k3s_state="activating (still not answering)"
  fi
fi

if [ "$k3s_state" = "activating (still not answering)" ]; then
  # A serving API cert whose notBefore is in the future (pre-NTP clock at boot,
  # corrected backwards afterwards). k3s then rejects its OWN handshake and sits
  # in 'activating' forever. Only the short-lived leaf is affected: server-ca.crt
  # stays valid for years, so no cluster data is at risk. Moving the leaf (and
  # dynamic-cert.json, which drives regeneration) aside forces a fresh cert
  # stamped from the corrected clock.
  info "k3s service state: $k3s_state"
  info "reading the leaf cert to confirm the clock-skew signature"
  nb=$(sudo openssl x509 -in /var/lib/rancher/k3s/server/tls/serving-kube-apiserver.crt \
       -noout -startdate 2>/dev/null | cut -d= -f2)
  if [ -n "$nb" ]; then
    info "  leaf notBefore: $nb"
    info "  now (UTC)     : $(date -u +'%b %e %H:%M:%S %Y GMT')"
    if [ "$(date -d "$nb" +%s 2>/dev/null || echo 0)" -gt "$(date -u +%s)" ]; then
      info "  -> notBefore is in the FUTURE: this is the clock-skew cert bug"
    else
      info "  -> notBefore is in the past, so cert skew is NOT the cause."
      info "     The repair below is still safe (leaf certs only) but expect"
      info "     it not to help; check 'journalctl -u k3s' instead."
    fi
  else
    info "  -> could not read the leaf cert (k3s may not have written one)"
  fi
  if [ "$CHECK_ONLY" = 1 ]; then
    bad "--check only: not repairing. Re-run without --check to fix."
    exit 4
  fi
  info "repairing: moving the stale leaf aside to $CERT_BAK"
  sudo mkdir -p "$CERT_BAK"
  sudo systemctl stop k3s
  for f in serving-kube-apiserver.crt serving-kube-apiserver.key dynamic-cert.json; do
    src="/var/lib/rancher/k3s/server/tls/$f"
    [ -e "$src" ] && sudo mv "$src" "$CERT_BAK/" && info "  moved $f"
  done
  sudo systemctl start k3s
  info "backups kept at $CERT_BAK (delete once the run succeeds)"
fi

if [ "$CHECK_ONLY" = 1 ]; then
  say "--check only: stopping before the run"
  exit 0
fi

info "waiting up to ${K3S_WAIT_S}s for the k3s API"
deadline=$(( $(date +%s) + K3S_WAIT_S ))
until sudo k3s kubectl get node >/dev/null 2>&1; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    bad "k3s API still unreachable after ${K3S_WAIT_S}s -- do NOT benchmark."
    bad "Inspect: sudo journalctl -u k3s -n 40 | tail -20"
    exit 4
  fi
  sleep 3
done
ok "k3s API reachable (kubectl get node returns)"
ok "Knative legs can run"

say "3/4  Confirm"
if [ "$ASSUME_YES" = 0 ]; then
  info "This runs ~2.5-3 h and will occupy the box. Do not start other work."
  printf '   Start it now? [y/N] '
  read -r a
  case "$a" in
    [yY]|[yY][eE][sS]) ;;
    *) info "aborted at your request"; exit 0 ;;
  esac
fi

say "4/4  Running Tier-1 Experiment B (~2.5-3 h)"
info "log: $LOG"
info ""
bash "$DRIVER" 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}

echo
if [ "$rc" -eq 0 ]; then
  ok "driver exited 0 -- aggregation complete"
else
  bad "driver exited $rc -- READ THE LOG before trusting any table."
  case "$rc" in
    1) bad "exit 1 usually means a PROTOCOL ERROR (a leg ran without a usable"
       bad "idle probe), not a measurement failure." ;;
    *) bad "a leg's bench or gate failed; see the log tail." ;;
  esac
fi
info "log kept at: $LOG"

# ------------------------------------------------- post-run contamination audit
# The harness records a ps snapshot in every summary.json (ambient.top_cpu), so
# "did I actually quit my agents?" is answerable from the data rather than from
# memory. The 15% ceiling is necessary but NOT sufficient: this box read 15.8%
# with an agent session live and 11.9% moments later while the SAME process sat
# at 69% CPU, so a leg can pass the gate with a session still attached. The
# contamination A/B measured +2.2 pp on Fn under an agent profile, so an
# unnoticed contaminated leg is a real threat to the headline.
say "Post-run audit -- was the box actually quiet?"
python3 - "$REPO" <<'PY'
import glob, json, os, re, sys
repo = sys.argv[1]
# Matching is on the EXECUTABLE, not on the whole ps line. The previous version
# substring-matched the raw line and so matched its own harness:
#   "tor"          -> "--working-directory" in ptyxis's cmdline
#   "gnome-shell"  -> the desktop compositor, resident in every run by design
#   "ptyxis"       -> the terminal that is running THIS driver
# All three are present identically in the contamination A/B baseline, so every
# leg was flagged CONTAMINATED unconditionally and the audit could never
# discriminate a real agent session from a clean one. Desktop/compositor jitter
# is what the per-leg 15% load ceiling is for; process-name bans are for
# software that can actually be quit.
#
# Two tiers, because agents do not always exec under their own name: a node
# wrapper is basename "node", so high-signal names are matched anywhere in the
# command line, while short/ambiguous ones must match the executable itself.
# Word-boundary anchored: without \b, "code " also fires inside "opencode".
SUBSTR = (r"\bopencode\b", r"\bclaude\b", r"\bcode\b", r"\bpycharm\b",
          r"\bintellij\b", r"\blibreoffice\b", r"\bchromium\b",
          r"\bfirefox\b", r"\bxdg-open\b")
EXE = {"code", "code-insiders", "codium", "cursor", "nvim", "vim", "vi",
       "emacs", "nano", "gimp", "inkscape", "slack", "zoom", "teams", "obs",
       "tor", "ffmpeg", "chrome", "google-chrome", "brave"}


def suspects(line):
    """Return the suspect tokens this ps line trips, else []."""
    f = line.split(None, 10)
    cmd = f[10].lower() if len(f) > 10 else line.lower()
    exe = cmd.split()[0] if cmd.split() else ""
    base = exe.rsplit("/", 1)[-1]
    hits = [s.strip("\\b") for s in SUBSTR if re.search(s, cmd)]
    if base in EXE and base not in hits:
        hits.append(base)
    return hits
dirs = sorted(glob.glob(os.path.join(repo, "results", "*tier1*")))
summ = [d for d in dirs if os.path.isfile(os.path.join(d, "summary.json"))]
if not summ:
    print("   no tier1 summary.json found -- nothing to audit")
    raise SystemExit(0)
flagged = 0
print("   %-42s %7s %s" % ("leg", "amb%", "verdict"))
for d in summ:
    try:
        a = json.load(open(os.path.join(d, "summary.json"))).get("ambient") or {}
    except Exception as e:
        print("   %-42s  ??    UNREADABLE (%s)" % (os.path.basename(d), type(e).__name__))
        flagged += 1
        continue
    lp = a.get("load_pct")
    hits = []
    for l in (a.get("top_cpu") or []):
        for tok in suspects(l):
            if tok not in hits:
                hits.append(tok)
    if hits:
        verdict = "CONTAMINATED: " + ",".join(hits[:4])
        flagged += 1
    elif lp is not None and lp > (a.get("threshold_pct") or 15.0):
        verdict = "OVER threshold"
        flagged += 1
    else:
        verdict = "clean"
    print("   %-42s %7s %s" % (os.path.basename(d),
                              ("%.1f" % lp) if lp is not None else "n/a", verdict))
print()
if flagged:
    print("   !! %d leg(s) flagged. Treat their numbers as CONTAMINATED:" % flagged)
    print("      do not quote them, and re-run those legs with agents quit.")
    print("      (runbook §1: an agent profile drifted Fn's share ~+2.2 pp.)")
else:
    print("   ok -- every audited leg is free of agent/editor/browser processes")
    print("      and under the ambient ceiling. Numbers are citable.")
PY

if [ "$rc" -eq 0 ]; then
  say "Promoting the data into the paper repo (committed data lives there)"
  info "runs write to saqef/results/, but committed results are saqef-paper/results/"
  info ""
  info "  cp -r $REPO/results/*tier1* $REPO/results/idle_probe_* \\"
  info "      $REPO/../saqef-paper/results/"
  info ""
  info "Then: git add -A results/ && git status   (review before committing)"
  info "The idle probes are raw reads -- commit them, they are the provenance."
fi

info ""
info "Then say 'tier1 done' and I'll read the log and the data myself."
exit "$rc"
