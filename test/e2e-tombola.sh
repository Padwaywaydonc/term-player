#!/usr/bin/env bash
# E2E test for `play --tombola`: the generative synth must start without the
# music stack (no yt-dlp on PATH), draw the polygon + parameter panel, stream
# non-silent PCM into the audio sink, react to keys, and on quit restore the
# terminal, close the sink, and persist its settings.
#
# Strategy: point PLAY_MPV_WIN at a fake "mpv.exe" that records its argv and
# stdin, run `play --tombola --audio wsl` in a detached tmux pane, then inspect
# the pane, the recorded PCM, and the saved config.
#
# Usage: PLAY_BIN=/path/to/play ./e2e-tombola.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PLAY_BIN="${PLAY_BIN:-$HERE/play}"
[ -x "$PLAY_BIN" ] || { echo "FAIL: play binary not found/executable: $PLAY_BIN" >&2; exit 2; }
command -v tmux >/dev/null || { echo "FAIL: tmux required for this test" >&2; exit 2; }

WORK=$(mktemp -d)
SESS="playtombolatest_$$"

cleanup() {
    tmux kill-session -t "$SESS" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK/bin" "$WORK/home/.config"

# ── Fake mpv.exe: record argv + PCM from stdin ───────────────────────────────
cat > "$WORK/bin/mpv.exe" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$@" > "$WORK/sink.args"
cat > "$WORK/sink.pcm"
echo closed > "$WORK/sink.done"
EOF
chmod +x "$WORK/bin/mpv.exe"

# PATH without yt-dlp proves tombola doesn't depend on the music stack.
ln -s "$(command -v python3)" "$WORK/bin/python3"
for t in bash cat dirname readlink grep stty sleep env; do
    p=$(command -v "$t") && ln -sf "$p" "$WORK/bin/$t"
done

tmux new-session -d -s "$SESS" -x 110 -y 34 \
    "env -i HOME=$WORK/home TERM=xterm-256color PATH=$WORK/bin PLAY_MPV_WIN=$WORK/bin/mpv.exe \
     bash -c '$PLAY_BIN --tombola --audio wsl; echo EXIT=\$? > $WORK/exit; stty -a > $WORK/stty; sleep 30'"

FAIL=0
fail() { echo "FAIL: $*"; FAIL=1; }

sleep 3
SCREEN=$(tmux capture-pane -p -t "$SESS")
for label in TOMBOLA HITS RINGS KEY SCALE INSTR ATTACK RELEASE SHAPE VISUAL BALLS GRAV ROT BOUNCE OCTAVE REVERB GRAIN VOL; do
    grep -q "$label" <<<"$SCREEN" || fail "screen is missing label $label"
done
grep -q '[▀▄█]' <<<"$SCREEN" || fail "no pixel canvas on screen"

# Select GRAV (right of BALLS) and raise it twice: 30 -> 40; hotkeys: triangle, next scale.
tmux send-keys -t "$SESS" Right Up Up 3 c
sleep 0.5
SCREEN2=$(tmux capture-pane -p -t "$SESS")
grep -q 'TRI' <<<"$SCREEN2" || fail "hotkey 3 did not switch shape to TRI"
grep -q 'MAJOR' <<<"$SCREEN2" || fail "hotkey c did not advance scale MINOR -> MAJOR"

# Tab to the RINGS page, raise RINGS 3 -> 4.
tmux send-keys -t "$SESS" Tab Up
sleep 0.5
SCREEN3=$(tmux capture-pane -p -t "$SESS")
for label in MOONS SPEED TILT PAD PING CHORD CHANGE RATIO TONE; do
    grep -q "$label" <<<"$SCREEN3" || fail "RINGS page is missing label $label"
done
sleep 3
tmux send-keys -t "$SESS" q
for _ in $(seq 1 20); do [ -f "$WORK/exit" ] && break; sleep 0.25; done

[ "$(cat "$WORK/exit" 2>/dev/null)" = "EXIT=0" ] || fail "tombola did not exit cleanly: $(cat "$WORK/exit" 2>/dev/null)"
grep -q -- '-icanon' "$WORK/stty" && fail "terminal left in cbreak mode after quit"
[ -f "$WORK/sink.done" ] || fail "audio sink was not closed on quit"
grep -qx -- '--demuxer-rawaudio-format=s16le' "$WORK/sink.args" 2>/dev/null || fail "sink not started as raw PCM player"

BYTES=$(stat -c %s "$WORK/sink.pcm" 2>/dev/null || echo 0)
# ~6 s at 22050 Hz s16 mono ≈ 264 kB; paced output must be near real time, not a burst.
[ "$BYTES" -gt 150000 ] && [ "$BYTES" -lt 600000 ] || fail "unexpected PCM size: $BYTES bytes"
PEAK=$(python3 -c "
import sys
from array import array
raw = open(sys.argv[1], 'rb').read()
print(max(map(abs, array('h', raw[:len(raw) // 2 * 2])), default=0))" "$WORK/sink.pcm")
[ "$PEAK" -gt 500 ] || fail "PCM is silent (peak $PEAK)"

grep -q '"grav": 40' "$WORK/home/.config/play-tombola.json" 2>/dev/null || fail "GRAV change not persisted"
grep -q '"shape": "TRI"' "$WORK/home/.config/play-tombola.json" 2>/dev/null || fail "shape hotkey not persisted"
grep -q '"rings": 4' "$WORK/home/.config/play-tombola.json" 2>/dev/null || fail "RINGS change not persisted"

[ $FAIL -eq 0 ] && echo "PASS: tombola e2e (pcm=${BYTES}B peak=$PEAK)"
exit $FAIL
