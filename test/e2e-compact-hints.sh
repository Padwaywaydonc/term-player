#!/usr/bin/env bash
# E2E test for `play`: the compact key-hint line (shown when the pane is
# <=5 rows tall) must describe every key, not just the first three.
#
# Regression guard for the bug where "n/p l s:cfg q" had no description for
# n/p, l, or q — only ←→/↑↓/Space were labeled. Also guards against the
# installed ~/.local/bin/play silently drifting out of sync with the source
# (which is exactly how this slipped through once already): PLAY_BIN here
# must be a real symlink to the repo's play script, not a stale copy.
#
# Strategy: force the compact branch of show_player_display() with a short
# (<=5 row) tmux pane, and assert the hint line has a description for every
# key (colon-separated) rather than bare letters.
#
# Usage: PLAY_BIN=/path/to/play ./e2e-compact-hints.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PLAY_BIN="${PLAY_BIN:-$HERE/play}"
[ -x "$PLAY_BIN" ] || { echo "FAIL: play binary not found/executable: $PLAY_BIN" >&2; exit 2; }
command -v tmux >/dev/null || { echo "FAIL: tmux required for this test" >&2; exit 2; }

WORK=$(mktemp -d)
SESS="playhinttest_$$"

cleanup() {
    tmux kill-session -t "$SESS" 2>/dev/null
    pkill -f "$WORK/bin/mpv" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK/bin" "$WORK/home/.config" "$WORK/home/.cache"

# ── Fake yt-dlp ───────────────────────────────────────────────────────────────
cat > "$WORK/bin/yt-dlp" <<'YTEOF'
#!/usr/bin/env bash
last="${@: -1}"
if [[ "$*" == *"after_move:filepath"* ]]; then
    printf 'TRACK\n'
    tpl=""; prev=""; for a in "$@"; do [ "$prev" = "-o" ] && tpl="$a"; prev="$a"; done
    f="${tpl%/*}/1.webm"; echo audio > "$f"
    printf '3:00\n'
    printf '%s\n' "$f"
    exit 0
fi
for i in $(seq 1 10); do
    printf 'ID%d|TRACK %d|3:00\n' "$i" "$i"
done
YTEOF
chmod +x "$WORK/bin/yt-dlp"

# ── Fake mpv (native): just needs to create the IPC socket and stay alive ────
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

cat > "$WORK/home/.config/play.conf" <<EOF
PLAY_PROVIDER=youtube
PLAY_AUDIO=native
PLAY_SIZE=20
PLAY_POS=top
EOF

cat > "$WORK/run.sh" <<EOF
#!/usr/bin/env bash
export PATH="$WORK/bin:\$PATH"
export HOME="$WORK/home"
export PLAY_MPV_LINUX="$WORK/bin/mpv"
export _PLAY_TMUX=1
exec "$PLAY_BIN" --audio native track
EOF
chmod +x "$WORK/run.sh"

pane() { tmux capture-pane -pt "$SESS" 2>/dev/null; }
wait_for() {
    local pat="$1" max="${2:-100}" t=0
    while [ "$t" -lt "$max" ]; do
        pane | grep -qF "$pat" && return 0
        sleep 0.1; t=$((t+1))
    done
    return 1
}
fail() { echo "FAIL: $1"; echo "----- pane -----"; pane; echo "----------------"; exit 1; }

# A <=5 row pane forces show_player_display()'s compact branch. It's only
# drawn once per track load though, and right at startup there's a race
# between the pane settling to its final size and that first draw — so the
# very first screen can occasionally still reflect a stale, larger size.
# Advance to the next track (forcing a fresh, guaranteed-settled redraw)
# before asserting on the hint line.
tmux new-session -d -s "$SESS" -x 100 -y 5 "$WORK/run.sh"

wait_for "seek" 150 || fail "player never reached ready state (no hint line shown)"

tmux send-keys -t "$SESS" n
wait_for "seek" 150 || fail "player never redrew after advancing tracks"
sleep 0.3

hint_line=$(pane | grep -F '←→:seek')
[ -n "$hint_line" ] || fail "compact hint line not found in pane"
echo "ok: compact hint line present: $hint_line"

for entry in 'n/p:' 'l:' 's:' 'q:'; do
    case "$hint_line" in
        *"$entry"*) : ;;
        *) fail "compact hint line is missing a description for '$entry' (bare key, no colon+label): $hint_line" ;;
    esac
done
echo "ok: n/p, l, s and q all have descriptions in the compact hint line"

echo "PASS: compact key-hint line describes every key"
