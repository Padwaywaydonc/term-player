#!/usr/bin/env bash
# E2E test for `play --tombola` inside tmux: like the music player it must open
# in a small split (play's default is 10% along the top), draw the compact
# layout there — canvas, both page tabs, the selected setting, key hints —
# react to keys, and close its pane on quit.
#
# Runs on a private tmux server (-L) so it never touches the user's sessions.
#
# Usage: PLAY_BIN=/path/to/play ./e2e-tombola-split.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PLAY_BIN="${PLAY_BIN:-$HERE/play}"
[ -x "$PLAY_BIN" ] || { echo "FAIL: play binary not found/executable: $PLAY_BIN" >&2; exit 2; }
command -v tmux >/dev/null || { echo "FAIL: tmux required for this test" >&2; exit 2; }

WORK=$(mktemp -d)
SOCK="playtombolasplit_$$"
# The server's environment is what split panes inherit, so it must already be
# sandboxed (HOME, fake mpv.exe) — not just the first pane's command.
T() { env HOME="$WORK/home" PLAY_MPV_WIN="$WORK/bin/mpv.exe" tmux -L "$SOCK" -f /dev/null "$@"; }
REAL_CFG="$HOME/.config/play-tombola.json"
REAL_SUM=$(sha256sum "$REAL_CFG" 2>/dev/null)
# A tombola the user is playing saves to the real config on every key, so the
# "test didn't touch it" check is only meaningful when none is running.
USER_TOMBOLA=$(pgrep -f '^python3 .*/tombola( |$)' | tr '\n' ' ')

cleanup() {
    T kill-server 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK/bin" "$WORK/home/.config"
cat > "$WORK/bin/mpv.exe" <<'EOF'
#!/usr/bin/env bash
cat > /dev/null
EOF
chmod +x "$WORK/bin/mpv.exe"

FAIL=0
fail() { echo "FAIL: $*"; FAIL=1; }

T new-session -d -s t -x 160 -y 50 "bash -c '$PLAY_BIN --tombola --size 10 --pos top --audio wsl; sleep 60'"
sleep 3

PANES=$(T list-panes -t t -F '#{pane_index} #{pane_height}')
[ "$(wc -l <<<"$PANES")" -eq 2 ] || fail "expected a split with 2 panes, got: $PANES"
TOP_H=$(awk '$1 == 0 {print $2}' <<<"$PANES")
[ "${TOP_H:-0}" -ge 4 ] && [ "${TOP_H:-0}" -le 6 ] || fail "tombola pane should be ~10% (5 rows) tall, got ${TOP_H:-?}"

SCREEN=$(T capture-pane -p -t t:0.0)
for label in TOMBOLA BALLS RINGS "tab page"; do
    grep -q "$label" <<<"$SCREEN" || fail "small pane is missing '$label'"
done
grep -q '[▀▄█]' <<<"$SCREEN" || fail "no canvas drawn in the small pane"
# The selected setting must be on screen even when the panel can't show them all.
T send-keys -t t:0.0 Right                  # BALLS -> GRAV
sleep 0.5
SCREEN=$(T capture-pane -p -e -t t:0.0)
grep -q $'\x1b\\[48;5;208m' <<<"$SCREEN" || fail "selected setting not visible in the small pane"

# A setting changed and then a hard close (pane killed, no q) must still be remembered.
T send-keys -t t:0.0 Up
sleep 0.5
T kill-pane -t t:0.0
sleep 1
grep -q '"grav": 35' "$WORK/home/.config/play-tombola.json" 2>/dev/null \
    || fail "change not remembered after the pane was killed: $(cat "$WORK/home/.config/play-tombola.json" 2>/dev/null)"

# Reopen: the remembered sound comes back, then quit with q.
T split-window -v -b -l 10% -t t:0.0 "env _PLAY_TMUX=1 $PLAY_BIN --tombola --audio wsl"
sleep 3
T capture-pane -p -t t:0.0 | grep -q 'GRAV 35' || fail "reopened tombola did not restore GRAV 35"
T send-keys -t t:0.0 q
sleep 2
[ "$(T list-panes -t t 2>/dev/null | wc -l)" -eq 1 ] || fail "tombola pane did not close on quit"
grep -q '"grav"' "$WORK/home/.config/play-tombola.json" 2>/dev/null || fail "settings not saved on quit"

if [ -n "$USER_TOMBOLA" ]; then
    echo "note: real-config check skipped — your own tombola is running (pid $USER_TOMBOLA)"
else
    [ "$(sha256sum "$REAL_CFG" 2>/dev/null)" = "$REAL_SUM" ] || fail "the real $REAL_CFG was modified by the test"
fi

[ $FAIL -eq 0 ] && echo "PASS: tombola opens in play's small split and closes cleanly (top pane ${TOP_H} rows)"
exit $FAIL
