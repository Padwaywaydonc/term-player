#!/usr/bin/env bash
# E2E test for `play`: volume changes must (1) show on screen and (2) persist
# across a track skip, since every track skip spawns a brand-new mpv process.
#
# Regression guard for two bugs found 2026-07-16:
#   - volume had NO on-screen indicator at all, so a working ↑/↓ press looked
#     like it did nothing
#   - VOLUME was never threaded into the next mpv invocation, so it silently
#     reset to 100 on every n/p track change
#
# Strategy: drive the real script inside a tmux pane (real PTY) with a mocked
# yt-dlp and a fake mpv running a minimal real JSON-IPC server over its unix
# socket. It logs every "set volume" command it receives, so we can assert
# the NEXT track's freshly-started mpv was immediately set to the persisted
# level (the fix applies volume via an initial IPC command, not a CLI flag).
#
# Usage: PLAY_BIN=/path/to/play ./e2e-volume-persist.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PLAY_BIN="${PLAY_BIN:-$HERE/play}"
[ -x "$PLAY_BIN" ] || { echo "FAIL: play binary not found/executable: $PLAY_BIN" >&2; exit 2; }
command -v tmux >/dev/null || { echo "FAIL: tmux required for this test" >&2; exit 2; }

WORK=$(mktemp -d)
SESS="playvoltest_$$"

cleanup() {
    tmux kill-session -t "$SESS" 2>/dev/null
    pkill -f "$WORK/bin/mpv" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK/bin" "$WORK/home/.config" "$WORK/home/.cache"

# ── Fake yt-dlp: two distinct tracks ──────────────────────────────────────────
cat > "$WORK/bin/yt-dlp" <<'YTEOF'
#!/usr/bin/env bash
args="$*"
last="${@: -1}"
if [[ "$args" == *"--get-url"* ]]; then
    id="${last##*v=}"
    printf 'TRACK %s\n' "$id"
    printf 'https://stream.test/%s\n' "$id"
    printf '3:00\n'
    exit 0
fi
for i in 1 2; do
    printf 'ID%d|TRACK %d|3:00\n' "$i" "$i"
done
YTEOF
chmod +x "$WORK/bin/yt-dlp"

# ── Fake mpv (native): minimal real JSON-IPC server, logs volume changes ─────
cat > "$WORK/bin/mpv" <<'MPVEOF'
#!/usr/bin/env python3
import sys, os, socket, json, threading

sock = None
for a in sys.argv:
    if a.startswith('--input-ipc-server='):
        sock = a.split('=', 1)[1]

state = {"volume": 100.0, "time-pos": 0.0, "pause": False}
LOG = os.path.join(os.path.dirname(sys.argv[0]), "..", "argv.log")

def handle(conn):
    buf = b""
    with conn:
        while True:
            data = conn.recv(4096)
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    req = json.loads(line)
                except Exception:
                    continue
                cmd = req.get("command", [])
                reply = {"error": "success"}
                if cmd[:1] == ["set_property"] or cmd[:1] == ["set"]:
                    state[cmd[1]] = cmd[2]
                    if cmd[1] == "volume":
                        # "vol N" shape (no key=value adjacency) so it isn't
                        # mistaken for a credential-shaped string.
                        with open(LOG, "a") as f:
                            f.write("vol %d\n" % int(cmd[2]))
                elif cmd[:1] == ["add"]:
                    state[cmd[1]] = state.get(cmd[1], 0) + cmd[2]
                elif cmd[:1] == ["get_property"]:
                    reply["data"] = state.get(cmd[1])
                elif cmd[:1] == ["seek"]:
                    state["time-pos"] = state.get("time-pos", 0) + cmd[1]
                elif cmd[:1] == ["cycle"] and cmd[1] == "pause":
                    state["pause"] = not state.get("pause", False)
                try:
                    conn.sendall((json.dumps(reply) + "\n").encode())
                except Exception:
                    return

if sock:
    try:
        if os.path.exists(sock):
            os.unlink(sock)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(sock); s.listen(5)
        def accept_loop():
            while True:
                conn, _ = s.accept()
                threading.Thread(target=handle, args=(conn,), daemon=True).start()
        threading.Thread(target=accept_loop, daemon=True).start()
    except Exception:
        pass

import time
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

tmux new-session -d -s "$SESS" -x 100 -y 30 "$WORK/run.sh"

wait_for "l:list" 150 || fail "player never reached ready state"
wait_for "♪100%" 50   || fail "no volume indicator shown at startup (expected ♪100%)"
echo "ok: startup shows volume indicator ♪100%"

# The player picks its starting track at random (CURR_IDX=$((RANDOM % N))),
# so 'n' can land on either track depending on where it started — assert by
# title CHANGE, not by a hardcoded "TRACK 2" target.
initial_title=$(pane | grep -F '♪ TRACK')

tmux send-keys -t "$SESS" Up
tmux send-keys -t "$SESS" Up
wait_for "♪110%" 50 || fail "volume indicator did not update to ♪110% after two Up presses"
echo "ok: volume indicator updates on Up press (100% -> 110%)"

sleep 0.3
tmux send-keys -t "$SESS" n
t=0
while [ "$(pane | grep -F '♪ TRACK')" = "$initial_title" ] && [ "$t" -lt 200 ]; do
    sleep 0.1; t=$((t+1))
done
[ "$t" -lt 200 ] || fail "did not advance to the other track"
wait_for "♪110%" 50 || fail "volume indicator reset after track skip (persistence bug)"
echo "ok: volume indicator still shows 110% after track skip"

# Wait for the second mpv instance to log its initial volume set (racy right
# after launch). Track 1 logs one "vol" line per volume change (initial +
# each Up press); track 2 must log exactly one more, at the persisted level.
t=0
while [ "$(wc -l < "$WORK/argv.log" 2>/dev/null || echo 0)" -lt 4 ] && [ "$t" -lt 50 ]; do
    sleep 0.1; t=$((t+1))
done
last_line=$(tail -n 1 "$WORK/argv.log")
[ "$last_line" = "vol 110" ] || fail "second mpv instance was not started at the persisted volume (fix not applied)"
echo "ok: second track's mpv process was launched at the persisted volume"

echo "PASS: volume shows on screen and persists across track skips"
