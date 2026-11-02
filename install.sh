#!/usr/bin/env bash
# Symlink `play` into ~/.local/bin (or $PREFIX/bin if set).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)/play"
BIN="${PREFIX:-$HOME/.local}/bin"

mkdir -p "$BIN"
ln -sf "$SRC" "$BIN/play"
chmod +x "$SRC"

echo "Linked: $BIN/play -> $SRC"

# Optional: the native audio core for play --tombola (48 kHz stereo resonator,
# granular processor and reverb). Without it tombola uses its Python engine.
if command -v make >/dev/null && command -v g++ >/dev/null; then
    if make -s -C "$(dirname "$SRC")/native" >/dev/null 2>&1; then
        echo "Built: tombola native audio core"
    else
        echo "Note: tombola native audio core failed to build; the Python engine will be used"
    fi
else
    echo "Note: install make and g++ for tombola's native audio core (optional)"
fi
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) printf '\nNote: %s is not on your PATH. Add this to your shell rc:\n  export PATH="%s:$PATH"\n' "$BIN" "$BIN" ;;
esac

echo
echo "Try:  play lofi hip hop"
