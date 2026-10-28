#!/usr/bin/env bash
# E2E smoke test for the SoundCloud provider (no credentials required).
# Exercises the real pipeline: scsearch -> provider_target -> download_track -> mpv decode,
# plus a YouTube download with a mid-track seek.
#
#   bash test/e2e-soundcloud.sh
#
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PLAY="$HERE/play"
COUNT=10
QUERY="radiohead"
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

eval "$(sed -n '/^download_track() {/,/^}/p' "$PLAY")"
AUDIO_DIR=$(mktemp -d); trap 'rm -rf "$AUDIO_DIR"' EXIT

# Download via the script's own download_track, then have mpv decode 3s
# starting at $2 seconds (a mid-track seek is what broke on raw YouTube URLs).
check_decode() {
    local label="$1" start="$2" target="$3" file OUT rc
    file=$(download_track "$target" | grep -F "$AUDIO_DIR/" | tail -1)
    [ -s "$file" ] || { bad "$label: download failed"; return; }
    [ -n "$MPV" ] || { bad "no Linux mpv found for decode check"; return; }
    OUT=$(timeout 45 "$MPV" --no-video --ao=null --start="$start" --length=3 --msg-level=all=info "$file" 2>&1); rc=$?
    if printf '%s' "$OUT" | grep -qiE 'AO: \[null\]|A: |Audio ' && [ $rc -le 1 ]; then
        ok "$label: downloaded and decoded 3s from ${start}s (rc=$rc)"
    else
        bad "$label: mpv could not decode (rc=$rc)"; printf '%s\n' "$OUT" | tail -10
    fi
}

echo "== 3. SoundCloud track downloads and mpv decodes it =="
URL=$(printf '%s\n' "$SC" | grep '^https://soundcloud.com/' | head -1 | cut -d'|' -f1)
check_decode soundcloud 0 "$URL"

echo "== 4. YouTube track downloads and mpv decodes mid-track =="
YT_ID=$(timeout 60 yt-dlp --flat-playlist --print "%(id)s" "ytsearch1:${QUERY}" 2>/dev/null | head -1)
check_decode youtube 60 "https://www.youtube.com/watch?v=${YT_ID}"
echo
echo "== $pass passed, $fail failed =="
[ "$fail" -eq 0 ]
