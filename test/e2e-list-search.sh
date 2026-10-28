#!/usr/bin/env bash
# E2E test for `play`: pressing `l`, typing a NEW search, then selecting a
# result must load a song from the NEW search — not the old one.
#
# Regression guard for the bug where the in-list search ran `do_search` in a
# `&` subshell, so the SEARCH_IDS/TITLES/DURS arrays never updated in the
# parent shell and the list (and the selection) stayed on the old results.
#
# Strategy: drive the real script inside a tmux pane (real PTY) with a mocked
# `yt-dlp` and a fake `mpv` that only creates the IPC socket and stays alive.
# Distinct titles per query (OLDSONG vs NEWSONG) make the regression visible.
#
# Usage: PLAY_BIN=/path/to/play ./play-list-search.e2e.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PLAY_BIN="${PLAY_BIN:-$HERE/play}"
[ -x "$PLAY_BIN" ] || { echo "FAIL: play binary not found/executable: $PLAY_BIN" >&2; exit 2; }
command -v tmux >/dev/null || { echo "FAIL: tmux required for this test" >&2; exit 2; }

WORK=$(mktemp -d)
SESS="playtest_$$"

cleanup() {
    tmux kill-session -t "$SESS" 2>/dev/null
    pkill -f "$WORK/bin/mpv" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK/bin" "$WORK/home/.config" "$WORK/home/.cache"

# ── Fake yt-dlp ───────────────────────────────────────────────────────────────
# Search form: --print ... ytsearchN:QUERY  → 10 lines  id|title|dur
# Download form: --print ... -o <tpl> <youtube url>  → title / duration / file path
cat > "$WORK/bin/yt-dlp" <<'YTEOF'
#!/usr/bin/env bash
args="$*"
last="${@: -1}"

# Map a query substring to a deterministic id-prefix + display name.
name_for() {
    case "$1" in
        *new*) echo "NEW NEWSONG" ;;
        *old*) echo "OLD OLDSONG" ;;
        *)     echo "DEF DEFSONG" ;;
    esac
}

if [[ "$args" == *"after_move:filepath"* ]]; then
    # last arg looks like https://www.youtube.com/watch?v=OLD3
    id="${last##*v=}"
    pfx="${id%%[0-9]*}"      # OLD / NEW / DEF
    num="${id#$pfx}"         # trailing digits
    case "$pfx" in
        NEW) disp="NEWSONG" ;;
        OLD) disp="OLDSONG" ;;
        *)   disp="DEFSONG" ;;
    esac
    printf '%s %s\n' "$disp" "$num"   # title
    tpl=""; prev=""; for a in "$@"; do [ "$prev" = "-o" ] && tpl="$a"; prev="$a"; done
    f="${tpl%/*}/$id.webm"; echo audio > "$f"
    printf '3:00\n'                    # duration
    printf '%s\n' "$f"                 # downloaded file
    exit 0
fi

# search form
q="${last#*:}"                        # strip ytsearchN:
read pfx disp < <(name_for "$q")
for i in $(seq 1 10); do
    printf '%s%d|%s %d|3:00\n' "$pfx" "$i" "$disp" "$i"
done
YTEOF
chmod +x "$WORK/bin/yt-dlp"

# ── Fake mpv (native) ─────────────────────────────────────────────────────────
# Only needs to create the unix IPC socket (so the script's socket-wait returns
# fast) and stay alive until killed. No real IPC handling required.
cat > "$WORK/bin/mpv" <<'MPVEOF'
#!/usr/bin/env python3
import sys, os, socket, time
sock = None
for a in sys.argv:
    if a.startswith('--input-ipc-server='):
        sock = a.split('=', 1)[1]
if sock:
    try:
        if os.path.exists(sock):
            os.unlink(sock)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(sock); s.listen(1)
    except Exception:
        pass
time.sleep(99999)
MPVEOF
chmod +x "$WORK/bin/mpv"

# ── Isolated config: force native audio (check_deps runs before flag parse) ───
cat > "$WORK/home/.config/play.conf" <<EOF
PLAY_PROVIDER=youtube
PLAY_AUDIO=native
PLAY_SIZE=20
PLAY_POS=top
EOF

# ── Launcher (bakes env so tmux pane is self-contained) ───────────────────────
cat > "$WORK/run.sh" <<EOF
#!/usr/bin/env bash
export PATH="$WORK/bin:\$PATH"
export HOME="$WORK/home"
export PLAY_MPV_LINUX="$WORK/bin/mpv"
export _PLAY_TMUX=1
exec "$PLAY_BIN" --audio native oldquery
EOF
chmod +x "$WORK/run.sh"

# ── Helpers ───────────────────────────────────────────────────────────────────
pane() { tmux capture-pane -pt "$SESS" 2>/dev/null; }
wait_for() {  # wait_for <substring> [max_ticks=100]  (tick=0.1s)
    local pat="$1" max="${2:-100}" t=0
    while [ "$t" -lt "$max" ]; do
        pane | grep -qF "$pat" && return 0
        sleep 0.1; t=$((t+1))
    done
    return 1
}

fail() { echo "FAIL: $1"; echo "----- pane -----"; pane; echo "----------------"; exit 1; }

# ── Drive the scenario ────────────────────────────────────────────────────────
tmux new-session -d -s "$SESS" -x 120 -y 40 "$WORK/run.sh"

wait_for "l:list" 150     || fail "player never reached ready state (no 'l:list' hint)"
wait_for "OLDSONG"        || fail "initial track is not an OLDSONG result"
echo "ok: player started on an OLDSONG result"

tmux send-keys -t "$SESS" l
wait_for "type to search" || fail "list view did not open after pressing 'l'"
echo "ok: list view opened"

tmux send-keys -t "$SESS" -l "newquery"
wait_for "Search: newquery" || fail "typed query not reflected in search buffer"
tmux send-keys -t "$SESS" Enter
echo "ok: submitted search 'newquery'"

# THE regression assertion: after the search, the list must show NEW results.
# With the subshell bug, arrays stay stale and NEWSONG never appears here.
wait_for "NEWSONG" 100    || fail "list did not refresh to NEW search results (stale arrays — the bug)"
echo "ok: list refreshed to NEWSONG results"

tmux send-keys -t "$SESS" Enter   # select first new result (sel=0)
wait_for "NEWSONG" 100    || fail "did not load a NEWSONG track after selecting"

# Final player screen is cleared to just the now-playing line; it must be NEW.
sleep 0.5
out=$(pane)
grep -qF "NEWSONG" <<<"$out" || fail "player not showing a NEWSONG track"
if grep -qF "♪" <<<"$out" && grep -F "♪" <<<"$out" | grep -qF "OLDSONG"; then
    fail "now-playing line still shows an OLDSONG track"
fi
echo "ok: now playing a NEWSONG track"

echo "PASS: list search reloads the correct (newly searched) song"
