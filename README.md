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
