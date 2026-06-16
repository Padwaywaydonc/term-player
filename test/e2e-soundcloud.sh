#!/usr/bin/env bash
# E2E smoke test for the SoundCloud provider (no credentials required).
# Exercises the real pipeline: scsearch -> provider_target -> stream resolve -> mpv decode.
#
#   bash test/e2e-soundcloud.sh
#
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PLAY="$HERE/play"
COUNT=10
QUERY="lofi hip hop"
pass=0; fail=0
ok()  { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }

# Resolve a Linux mpv for the decode check (same preference order the script uses).
detect_linux_mpv() {
    local c
    for c in /usr/bin/mpv /usr/local/bin/mpv /home/linuxbrew/.linuxbrew/bin/mpv /opt/homebrew/bin/mpv; do
        [ -x "$c" ] && { printf '%s' "$c"; return; }
    done
    command -v mpv 2>/dev/null
}
MPV="$(detect_linux_mpv)"

echo "== 1. provider_target() maps each provider correctly =="
eval "$(sed -n '/^provider_target() {/,/^}/p' "$PLAY")"
PLAY_PROVIDER=soundcloud
[ "$(provider_target https://soundcloud.com/a/b)" = "https://soundcloud.com/a/b" ] \
    && ok "soundcloud -> URL as-is" || bad "soundcloud mapping wrong"
PLAY_PROVIDER=youtube
[ "$(provider_target xyz)" = "https://www.youtube.com/watch?v=xyz" ] \
    && ok "youtube -> watch URL" || bad "youtube mapping wrong"
PLAY_PROVIDER=spotify
[ "$(provider_target 'song artist')" = "ytsearch1:song artist" ] \
    && ok "spotify -> ytsearch1:" || bad "spotify mapping wrong"

echo "== 2. scsearch returns SoundCloud tracks =="
SC=$(timeout 60 yt-dlp --flat-playlist --print "%(webpage_url)s|%(title)s|%(duration_string)s" \
        "scsearch${COUNT}:${QUERY}" 2>/dev/null)
n=$(printf '%s\n' "$SC" | grep -c '^https://soundcloud.com/')
[ "$n" -ge 1 ] && ok "scsearch returned $n tracks" || { bad "no tracks"; printf '%s\n' "$SC" | head; }

echo "== 3. stream resolves and mpv decodes it =="
URL=$(printf '%s\n' "$SC" | grep '^https://soundcloud.com/' | head -1 | cut -d'|' -f1)
STREAM=$(timeout 60 yt-dlp -f bestaudio --get-url "$URL" 2>/dev/null | grep '^https' | head -1)
if [ -n "$STREAM" ] && [ -n "$MPV" ]; then
    OUT=$(timeout 45 "$MPV" --no-video --ao=null --length=3 --msg-level=all=info "$STREAM" 2>&1); rc=$?
    if printf '%s' "$OUT" | grep -qiE 'AO: \[null\]|A: |Audio ' && [ $rc -le 1 ]; then
        ok "mpv decoded 3s of audio (rc=$rc)"
    else
        bad "mpv could not decode (rc=$rc)"; printf '%s\n' "$OUT" | tail -10
    fi
elif [ -z "$MPV" ]; then
    bad "no Linux mpv found for decode check"
else
    bad "no stream URL resolved"
fi

echo
echo "== $pass passed, $fail failed =="
[ "$fail" -eq 0 ]
