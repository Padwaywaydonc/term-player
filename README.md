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
  With `INSTR` set to `RESONATOR` each hit instead strikes a resonator: `MODEL`
  `MODAL` (tuned modes; `STRUCTURE` morphs string → bar → plate), `SYMPATH`
  (sympathetic strings), `STRING` (inharmonic / modulated string), `FM`,
  `SYMP-Q` (sympathetic strings on chord tunings) or `STR+REV` (string with its
  own reverb), with `BRIGHTNESS` (wood/nylon → glass/steel), `DAMPING`, `POSITION`
  (where the strike lands; the centre cancels even harmonics) and `POLY` (1, 2 or
  4 voices). Harder hits strike harder.
- **Rings** are the chord. They circle the polygon as tilted ellipses (the
  near, lower half passes in front of it, the far half behind). Each ring sounds
  one chord note continuously — `RINGS` sets how many: root, + fifth, + third,
  + bass, with the outer ring the lowest. The moons are each note's volume
  envelope by height: a moon swells its note as it rises to the highest point of
  its ring and lets it fade toward the lowest — `TILT` moves those points, so it
  reshapes the swell (`FLOOR` is the volume left at the lowest point: 0% silent,
  100% a steady drone). Moons
  grow and glow as they get louder. You choose the chord: `CHORD` holds a scale
  degree (`I`…`VII`, also keys `1`–`7` on the RINGS and CHORDS pages), runs a
  progression, or `RANDOM` — a random chord loop (see the CHORDS page below).
- **Grains** — a granular processor on its own `GRAINS` page. It records the
  balls, the rings or both (`SOURCE`) and replays grains from it: `MODE`
  (`GRANULAR`, `STRETCH` time-stretching, `DELAY` looping delay, `SPECTRAL`),
  `DENSITY` (negative = steady rate, 0 = none, positive = random), `SIZE`,
  `POSITION` (how far back), `PITCH` (±12 semitones), `SPREAD` (stereo spread),
  `TEXTURE` (grain window: square → triangle → smooth, then a smearing diffuser),
  `FEEDBACK` (grains re-recorded; capped so it always dies away), `FREEZE` (stop
  recording and keep granulating what's in the buffer; also `f`) and `GRAIN`
  (level). Each grain shows as a brief spark.

**Audio engine.** `./install.sh` builds a small native core (`native/`, needs
`make` and `g++`): 48 kHz stereo, with the resonator, granular processor and
reverb DSP from Emilie Gillet's open-source eurorack code (MIT — see `NOTICE`),
plus the ring-chord voices and the mix. If it isn't built, tombola falls back to
its pure-Python engine (22.05 kHz mono, simplified resonator and grains); the
status line shows `48K` or `PY`, and `TOMBOLA_ENGINE=python` forces the fallback.
Ball instruments `PLUCK` (Karplus-Strong), `BELL` (FM), `MARIMBA`, `GLASS`,
`PAD`, `KALIMBA`, `VIBES`, `ORGAN`, `CHIME`, `SAW` are rendered in Python and
played by either engine, with an attack/release envelope. Audio goes to Windows
`mpv.exe` on WSL, or to `pacat` / Linux `mpv` natively. Graphics default to `PIXEL`:
half-block characters, two square pixels per cell with their own colour, which
every terminal and font draws as solid blocks. `v` also offers `BEHIND` (the
same scene, muted, filling the whole pane behind the text), `BLOCK` (octant
characters, finer but they need a font/terminal that has them) and `BRAILLE`.

**Live backdrop behind the whole terminal (Windows Terminal on WSL):** press `w`
(setting `WINDOW`). tombola opens a borderless window that follows your Windows
Terminal window and sits right behind it, drawing the scene smoothly at full
window resolution — every hit, flash and knob change, live. To see it through
the terminal, lower the profile's background opacity (Settings → your profile
→ Appearance → Background opacity, e.g. 60%, acrylic off). With `WINDOW` on, the
small strip drops its own picture and shows just the controls.

**CHORDS page — when and how the chord moves.** `CLOCK` `MOON` changes chord each
time the outer ring's moon passes its highest point (so the harmony moves with
the orbit you see); `TIME` every `CHANGE` seconds, varied by `JITTER`. `CHANCE`
is a coin toss on every tick: change, or stay. With `CHORD`
on `RANDOM` the chords come from a loop of `LENGTH` (1–16) steps: `DEJA VU` 0%
draws a new chord every step, up to 50% replays the loop with growing
probability (it slowly evolves), 50% locks it, above 50% shuffles it. `SPREAD`
sets how far a new chord may jump, `BIAS` favours colourful (II, III, VII) or
stable (I, IV, V, VI) chords. The row at the top shows the loop, the playing
chord lit and freshly regenerated ones marked with a dot. `g` regenerates the
whole loop, `x` holds the current chord.

Settings are split over four pages — `Tab` switches:

| Page    | Settings |
|---------|----------|
| `BALLS` | `KEY` `SCALE` `INSTR` `ATTACK` `RELEASE` `SHAPE` `VISUAL` `BALLS` `GRAV` `ROT` `BOUNCE` `OCTAVE` `REVERB` `GRAIN` `VOL` (ball volume); with `INSTR` `RESONATOR`: `MODEL` `STRUCTURE` `BRIGHTNESS` `DAMPING` `POSITION` `POLY` in place of `ATTACK` `RELEASE` |
| `CHORDS` | `KEY` `SCALE` `CHORD` `CLOCK` (`MOON` `TIME`) `VISUAL` `WINDOW` `CHANGE` `JITTER` `CHANCE` `LENGTH` `DEJA VU` `SPREAD` `BIAS` `OCTAVE` |
| `GRAINS` | `KEY` `SCALE` `SOURCE` (`ALL` `BALLS` `RINGS`) `MODE` `FREEZE` `VISUAL` `WINDOW` `GRAIN` (level) `DENSITY` `SIZE` `POSITION` `PITCH` `SPREAD` `TEXTURE` `FEEDBACK` `REVERB` |
| `RINGS` | `KEY` `SCALE` `INSTR` (ring timbre: `WARM` `GLASS` `SINE` `ORGAN` `SAW`) `CHORD` (held degree `I`…`VII`, or a progression) `RATIO` (orbit speeds: `KEPLER` outer-slower, `HARM`, `PRIME`, `GOLD`, `SYNC`) `VISUAL` `WINDOW` `RINGS` (chord notes, 0–4) `MOONS` (swells per orbit) `SPEED` `TILT` `OCTAVE` (−3…+3; 0 = lowest clearly audible, below = sub-bass) `PAD` (ring level) `FLOOR` (volume with the moon at its lowest: 0% silent … 100% drone) `REVERB` `GRAIN` `VOL` (ring volume) |

In a full-size pane every setting of the page is a knob along the bottom.

| Key            | Action                                    |
|----------------|-------------------------------------------|
| `Tab`          | switch page: BALLS / RINGS / CHORDS / GRAINS |
| `←` / `→`      | select setting (also `h` / `l`)           |
| `↑` / `↓`      | change it (also `k` / `j`)                |
| `3` … `8`      | BALLS page: shape — triangle, square, pentagon … octagon |
| `1` … `7`      | RINGS / CHORDS page: hold chord I … VII   |
| `g`            | regenerate the RANDOM chord loop          |
| `x`            | hold the current chord (no changes)       |
| `s` / `S`      | next / previous shape                     |
| `n` / `N`      | next / previous key                       |
| `c` / `C`      | next / previous scale                     |
| `i` / `I`      | next / previous instrument (balls, or ring timbre on the RINGS page) |
| `v`            | visual: pixel / behind / blocks / braille |
| `w`            | live backdrop window behind Windows Terminal |
| `f`            | freeze the grain buffer                   |
| `Space`        | on / off (freezes everything, mutes the pad) |
| `r`            | random: new random settings + fresh balls (keeps VOL and VISUAL) |
| `u`            | undo the last `r` (remembers the last 20 rolls, across restarts) |
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
