# term-player

A single-file terminal music player (the installed command is `play`). Search
YouTube, Spotify, or SoundCloud and
stream the audio straight into your terminal — with a progress bar, an
arrow-key track list, an in-player settings menu, and background prefetch so the
next track starts instantly.

Its party trick is **high-quality audio on WSL2**. Running native Linux `mpv`
inside WSL routes through PulseAudio's RDP sink, which crackles and degrades.
`play` instead drives the **Windows** `mpv.exe` over a named-pipe IPC bridge, so
you get clean WASAPI output while still controlling everything from your Linux
terminal. On a real Linux box it falls back to native `mpv` over a Unix socket.

```
  ♪ Tycho - A Walk
  3:58  [2/10]
  ←→:seek ↑↓:vol Space:pause n/p:trk l:list s:settings q:quit

  01:24/3:58 [████████████░░░░░░░░░░░░░░░░] 35%
```

## Features

- **Three providers** — `youtube` (default, no setup), `soundcloud` (no setup),
  `spotify` (free API key; discovery via Spotify, audio streamed from YouTube).
- **Instant next track** — the upcoming song's stream URL is prefetched in the
  background while the current one plays. Search results are disk-cached.
- **Arrow-key track list** (`l`) with type-to-search, while audio keeps playing.
- **In-player settings menu** (`s`) — change provider, audio backend, window
  size, and position live; saved to your config.
- **tmux split UI** — opens in a small pane (configurable size/position) and
  leaves you a working shell next to it. Falls back to inline if tmux is absent.
- **Quality audio on WSL2** via the Windows `mpv.exe` WASAPI bridge.

## Dependencies

| Tool      | Needed for                                   | Install                         |
|-----------|----------------------------------------------|---------------------------------|
| `yt-dlp`  | search + stream resolution (all providers)   | `pip install yt-dlp`            |
| `mpv`     | playback — Windows `mpv.exe` on WSL, else Linux `mpv` | [mpv.io](https://mpv.io) / `apt install mpv` / `scoop install mpv` |
| `python3` | Spotify search + native-audio IPC bridge     | usually preinstalled            |
| `tmux`    | split-pane UI (optional — degrades to inline)| `apt install tmux`              |

`play` checks these on startup and tells you exactly what's missing.

## Install

```bash
git clone https://github.com/Padwaywaydonc/term-player.git
cd term-player
./install.sh        # symlinks ./play into ~/.local/bin
```

Make sure `~/.local/bin` is on your `PATH`. Or just copy `play` anywhere on your
`PATH` and `chmod +x` it.

## Usage

```bash
play lofi hip hop                       # search YouTube, start playing
play --provider soundcloud ambient      # use SoundCloud
play --provider spotify --pos left tycho
play --size 25 --pos bottom synthwave   # 25%-tall pane along the bottom
```

### Flags (persisted to `~/.config/play.conf` on each run)

| Flag                  | Values                          | Default            |
|-----------------------|---------------------------------|--------------------|
| `--provider`          | `youtube` `spotify` `soundcloud`| `youtube`          |
| `--audio`             | `wsl` `native`                  | auto (WSL→`wsl`)   |
| `--size`              | integer % of terminal           | `20`               |
| `--pos`               | `top` `bottom` `left` `right`   | `top`              |

### Controls

| Key       | Action            |
|-----------|-------------------|
| `←` / `→` | seek ∓10s         |
| `↑` / `↓` | volume ±5         |
| `Space`   | pause / resume    |
| `n` / `p` | next / previous   |
| `l`       | track list (arrows to navigate, type to search) |
| `s`       | settings menu     |
| `q`       | quit              |

## Tombola — generative synth

```bash
play --tombola                 # opens in the same tmux split as the player
play --tombola --size 40       # a taller pane for the full knob layout
play --tombola --audio native  # force PulseAudio/pacat instead of Windows mpv.exe
```

It uses `play`'s `--size` / `--pos` split (default: a small strip along the
top). In short panes it switches to a compact layout — planet and rings on the
left, the page tabs, the settings that fit (always including the selected one)
and key hints on the right; from 14 rows up you get the full layout with knobs.

A physics sequencer in the spirit of the OP-1 Tombola, drawn like a planet with
Saturn rings:

- **Balls** fall under gravity inside a spinning polygon. Every edge is a pad
  tuned to a step of the chosen scale; a ball that hits a pad hard enough plays
  it — small balls an octave up, large balls an octave down, harder hits louder.
- **Rings** circle the polygon as tilted ellipses (the far side passes behind
  it). Each ring is one note of the current chord — the outer ring the bass —
  and its moons play that note each time they pass the gate, the bright mark at
  the front centre. Different orbit speeds turn the chord into a slow,
  polyrhythmic arpeggio. Under it a sustained `PAD` plays the same chord,
  moving through the chosen progression every `CHANGE` seconds.
- **Grains**: `GRAIN` adds a granular cloud — 60–180 ms grains cut from the last
  two seconds of notes, replayed at ½×, 1× and 2× pitch — shown as brief sparks.

Everything is pure Python (no extra packages): instruments `PLUCK`
(Karplus-Strong), `BELL` (FM), `MARIMBA`, `GLASS`, `PAD`, an attack/release
envelope and a reverb. Mono 22.05 kHz PCM is streamed to Windows `mpv.exe` on
WSL, or to `pacat` / Linux `mpv` natively. Graphics default to `PIXEL`:
half-block characters, two square pixels per cell with their own colour, which
every terminal and font draws as solid blocks. `v` also offers `BEHIND` (the
same scene, muted, filling the whole pane behind the text), `BLOCK` (octant
characters, finer but they need a font/terminal that has them) and `BRAILLE`.

Settings are split over two pages — `Tab` switches:

| Page    | Settings |
|---------|----------|
| `BALLS` | `KEY` `SCALE` `INSTR` `ATTACK` `RELEASE` `SHAPE` `VISUAL` `BALLS` `GRAV` `ROT` `BOUNCE` `OCTAVE` `REVERB` `GRAIN` `VOL` (ball volume) |
| `RINGS` | `KEY` `SCALE` `TONE` (moon instrument) `CHORD` (progression) `CHANGE` (seconds per chord) `RATIO` (orbit speeds: `KEPLER` outer-slower, `HARM`, `PRIME`, `GOLD`, `SYNC`) `VISUAL` `RINGS` (0–4) `MOONS` (per ring) `SPEED` `TILT` `PAD` `PING` (moons vs pad mix) `REVERB` `GRAIN` `VOL` (ring volume: moons + pad) |

In a full-size pane every setting of the page is a knob along the bottom.

| Key            | Action                                    |
|----------------|-------------------------------------------|
| `Tab`          | switch page: BALLS / RINGS                |
| `←` / `→`      | select setting (also `h` / `l`)           |
| `↑` / `↓`      | change it (also `k` / `j`)                |
| `3` … `8`      | shape: triangle, square, pentagon … octagon |
| `s` / `S`      | next / previous shape                     |
| `n` / `N`      | next / previous key                       |
| `c` / `C`      | next / previous scale                     |
| `i` / `I`      | next / previous instrument                |
| `v`            | visual: blocks / braille                  |
| `Space`        | on / off (freezes everything, mutes the pad) |
| `r`            | random: new random settings + fresh balls (keeps VOL and VISUAL) |
| `q`            | quit                                      |

Settings are saved to `~/.config/play-tombola.json` after every change and come
back the next time you open it, so a sound you like is still there — even if
the pane was closed without `q`. The code lives in
`tombola/` (physics, rings, synth, audio output, UI, params). Tests:
`python3 test/test_tombola.py` and `test/e2e-tombola.sh`.

## Configuration

Settings live in `~/.config/play.conf` and are rewritten whenever you launch or
change something in the settings menu:

```ini
PLAY_SIZE=20
PLAY_POS=top
PLAY_AUDIO=wsl
PLAY_PROVIDER=youtube
```

### Spotify (optional)

Spotify has no unauthenticated search API, so you need a free key (no Premium,
no username/password):

1. Go to <https://developer.spotify.com/dashboard> and create an app.
2. Copy the **Client ID** and **Client Secret**.
3. Add them in the in-player settings menu (press `s` → switch provider to
   `spotify` → fill the fields), or put them in `~/.config/play.conf`:

   ```ini
   SPOTIFY_CLIENT_ID=your_id
   SPOTIFY_CLIENT_SECRET=your_secret
   ```

Spotify is used for *discovery* (it has the best metadata); the actual audio is
resolved from YouTube.

### Path overrides

Auto-detection covers the common cases. To force a binary or temp dir:

```bash
export PLAY_MPV_LINUX=/usr/bin/mpv
export PLAY_MPV_WIN="/mnt/c/Program Files/mpv/mpv.exe"
export PLAY_WIN_TEMP='C:\Users\you\AppData\Local\Temp'
```

## How the WSL audio bridge works

1. `yt-dlp` resolves a direct stream URL.
2. The URL is written to an `.m3u` in the Windows `%TEMP%` dir (so the Windows
   process can read it).
3. `mpv.exe` plays it with `--input-ipc-server=\\.\pipe\<name>` (a Windows named
   pipe).
4. A small PowerShell bridge pipes your keypress commands from a Linux FIFO into
   that named pipe, so seeking/volume/pause work across the WSL↔Windows boundary.

On native Linux the same control flow uses an `mpv` Unix socket and a tiny
Python bridge instead.

## Legal

For personal use. `play` builds on `yt-dlp`; respect the terms of service of
whichever provider you query, and don't redistribute copyrighted audio. No
credentials are bundled — your Spotify key (if any) stays in your local config.

## License

MIT — see [LICENSE](LICENSE).
