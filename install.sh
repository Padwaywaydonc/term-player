#!/usr/bin/env bash
# Symlink `play` into ~/.local/bin (or $PREFIX/bin if set).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)/play"
BIN="${PREFIX:-$HOME/.local}/bin"

mkdir -p "$BIN"
ln -sf "$SRC" "$BIN/play"
chmod +x "$SRC"

echo "Linked: $BIN/play -> $SRC"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) printf '\nNote: %s is not on your PATH. Add this to your shell rc:\n  export PATH="%s:$PATH"\n' "$BIN" "$BIN" ;;
esac

echo
echo "Try:  play lofi hip hop"
