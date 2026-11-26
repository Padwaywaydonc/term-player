"""RADIO: play's music player inside tombola's mix — YouTube, SoundCloud or Spotify.

Same approach as play's own streaming: yt-dlp searches and downloads each
track to a private temp folder (YouTube rejects mpv's range requests, a
download doesn't), ffmpeg decodes it to 48 kHz stereo, and a feeder thread
queues that into the native core's radio buffer. Spotify searches use play's
saved API keys (~/.config/play.conf) and play each hit's YouTube match, as play
does. Seeking restarts the decoder at the new spot; pausing holds the core's
buffer. At the end of a track the next one starts. Native engine only.
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request

RESULTS = 10                         # tracks per search
CHUNK = 4 * 4800                     # 0.1 s of 48 kHz stereo s16
BYTES_PER_S = 4 * 48000
SOURCES = ("YOUTUBE", "SOUNDCLOUD", "SPOTIFY")
SEARCH = {"YOUTUBE": "ytsearch{n}:{q}", "SOUNDCLOUD": "scsearch{n}:{q}"}
FIELDS = {"YOUTUBE": "%(id)s|%(title)s|%(duration_string)s",
          "SOUNDCLOUD": "%(webpage_url)s|%(title)s|%(duration_string)s"}
PLAY_CONF = os.path.expanduser("~/.config/play.conf")


def target(source, ident):
    if source == "SPOTIFY":
        return f"ytsearch1:{ident}"      # ident is "name artist": play its YouTube match
    return f"https://www.youtube.com/watch?v={ident}" if source == "YOUTUBE" else ident


def seconds(duration):
    """'3:05' or '1:02:03' -> seconds (0 when unknown)."""
    try:
        total = 0
        for part in duration.split(":"):
            total = total * 60 + int(part)
        return total
    except ValueError:
        return 0


def spotify_keys(path=PLAY_CONF):
    """(client id, secret) from play's config, or ('', '')."""
    keys = {}
    try:
        with open(path) as f:
            for line in f:
                name, _, value = line.strip().partition("=")
                if name in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"):
                    keys[name] = value.strip().strip("'\"")
    except OSError:
        pass
    return keys.get("SPOTIFY_CLIENT_ID", ""), keys.get("SPOTIFY_CLIENT_SECRET", "")


def spotify_search(query, keys, opener=urllib.request.urlopen):
    """Spotify track search, as play does it: [(\"name artist\", \"name - artists\", \"m:ss\")]."""
    cid, secret = keys
    data = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid,
                                   "client_secret": secret}).encode()
    req = urllib.request.Request("https://accounts.spotify.com/api/token", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    token = json.loads(opener(req, timeout=10).read())["access_token"]
    url = "https://api.spotify.com/v1/search?" + urllib.parse.urlencode({"q": query, "type": "track",
                                                                          "limit": RESULTS})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    out = []
    for t in json.loads(opener(req, timeout=10).read())["tracks"]["items"]:
        ms = t["duration_ms"]
        out.append((f"{t['name']} {t['artists'][0]['name']}",
                    f"{t['name']} - {', '.join(a['name'] for a in t['artists'])}",
                    f"{ms // 60000}:{(ms % 60000) // 1000:02d}"))
    return out


class Radio:
    def __init__(self, mixer, run=subprocess.Popen, which=shutil.which, keys=spotify_keys,
                 spotify=spotify_search):
        self.mixer = mixer
        self.run, self.which, self.keys, self.spotify = run, which, keys, spotify
        self.results = []                # (id, title, duration)
        self.index = 0
        self.source = "YOUTUBE"
        self.status = "IDLE"             # IDLE, SEARCHING, LOADING, PLAYING, PAUSED, or a short error
        self.title = ""
        self.path = None                 # the current track's downloaded file
        self.start = 0.0                 # where in the track the decoder started (seek)
        self.played = 0                  # bytes queued since then
        self._gen = 0                    # bumped on every new action: stale workers stop
        self._procs = []
        self._lock = threading.Lock()
        self._dir = None

    # --- what the UI asks ---------------------------------------------------
    def missing(self, source=None):
        """Why the radio can't run here, or '' when it can."""
        if not hasattr(self.mixer, "radio_push"):
            return "48K ONLY"
        tools = [t for t in ("yt-dlp", "ffmpeg") if not self.which(t)]
        if tools:
            return f"NEEDS {' + '.join(tools).upper()}"
        if (source or self.source) == "SPOTIFY" and not all(self.keys()):
            return "SPOTIFY NEEDS KEYS (play: s settings)"
        return ""

    def elapsed(self):
        """Seconds into the current track (what's been heard, not just queued)."""
        if self.status not in ("PLAYING", "PAUSED"):
            return 0.0
        return max(0.0, self.start + self.played / BYTES_PER_S - self.mixer.radio_queued())

    def duration(self):
        return seconds(self.results[self.index][2]) if self.results else 0

    @property
    def playing(self):
        return self.status in ("SEARCHING", "LOADING", "PLAYING", "PAUSED", "SKIPPING")

    # --- actions --------------------------------------------------------------
    def search(self, query, source):
        """New results for query, then play the first."""
        self.source = source
        if self.missing() or not query.strip():
            return
        gen = self._restart("SEARCHING")
        threading.Thread(target=self._search, args=(gen, query.strip(), source), daemon=True).start()

    def play(self, index=None):
        if self.missing() or not self.results:
            return
        self.index = (self.index if index is None else index) % len(self.results)
        gen = self._restart("LOADING")
        threading.Thread(target=self._play, args=(gen, self.index), daemon=True).start()

    def skip(self, step):
        if self.results:
            self.play(self.index + step)

    def pause(self):
        """Pause / resume (Space)."""
        if self.status == "PLAYING":
            self.status = "PAUSED"
        elif self.status == "PAUSED":
            self.status = "PLAYING"
        self.mixer.radio_paused = self.status == "PAUSED"

    def seek(self, delta):
        """Jump delta seconds (<- / ->) in the current track."""
        if self.status not in ("PLAYING", "PAUSED") or not self.path:
            return
        end = max(0, (self.duration() or 10 ** 6) - 1)
        to = min(max(0.0, self.elapsed() + delta), end)
        paused = self.status == "PAUSED"
        gen = self._restart("PAUSED" if paused else "PLAYING")
        threading.Thread(target=self._decode, args=(gen, self.path, to, self.index), daemon=True).start()

    def stop(self):
        self._restart("IDLE")

    def close(self):
        self.stop()
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None

    # --- workers ----------------------------------------------------------------
    def _restart(self, status):
        with self._lock:
            self._gen += 1
            for proc in self._procs:
                if proc.poll() is None:
                    proc.kill()
            self._procs = []
            self.status = status
            self.played = 0
        self.mixer.radio_paused = status == "PAUSED"
        if hasattr(self.mixer, "radio_clear"):
            self.mixer.radio_clear()
        return self._gen

    def _spawn(self, gen, args):
        with self._lock:
            if gen != self._gen:
                return None
            proc = self.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self._procs.append(proc)
            return proc

    def _search(self, gen, query, source):
        if source == "SPOTIFY":
            try:
                results = self.spotify(query, self.keys())
            except Exception:            # network, bad keys: say so, don't crash the synth
                results = None
            if results is None:
                if gen == self._gen:
                    self.status = "SPOTIFY SEARCH FAILED"
                return
        else:
            proc = self._spawn(gen, ["yt-dlp", "--flat-playlist", "--print", FIELDS[source],
                                     SEARCH[source].format(n=RESULTS, q=query)])
            if not proc:
                return
            out = proc.communicate()[0].decode("utf-8", "replace")
            results = [tuple((line.split("|") + ["", ""])[:3]) for line in out.splitlines() if "|" in line]
        if gen != self._gen:
            return
        if not results:
            self.status = "NOTHING FOUND"
            return
        self.results, self.index = results, 0
        self._play(gen, 0)

    def _download(self, gen, ident):
        if self._dir is None:
            self._dir = tempfile.mkdtemp(prefix="tombola-radio-")
        proc = self._spawn(gen, ["yt-dlp", "-f", "bestaudio", "--match-filters", "!is_live", "--no-simulate",
                                 "-o", os.path.join(self._dir, "%(id)s.%(ext)s"),
                                 "--print", "after_move:filepath", target(self.source, ident)])
        if not proc:
            return None
        lines = proc.communicate()[0].decode("utf-8", "replace").split("\n")
        path = next((line for line in reversed(lines) if line.startswith(self._dir)), "")
        return path if os.path.isfile(path) else None

    def _play(self, gen, index):
        ident, title, _ = self.results[index]
        self.title, self.path, self.start = title, None, 0.0
        self.status = "LOADING"
        path = self._download(gen, ident)
        if gen != self._gen:
            return
        if not path:                     # live stream, region-locked, gone: try the next one
            self.status = "SKIPPING"
            time.sleep(0.5)
            if gen == self._gen and len(self.results) > 1:
                self.index = (index + 1) % len(self.results)
                self._play(gen, self.index)
            return
        self.path = path
        self.status = "PLAYING"
        self._decode(gen, path, 0.0, index)

    def _decode(self, gen, path, start, index):
        """Decode path from start (seconds) into the core; at the end, on to the next track."""
        self.start, self.played = start, 0
        decoder = self._spawn(gen, ["ffmpeg", "-loglevel", "quiet", "-ss", f"{start:.2f}", "-i", path,
                                    "-f", "s16le", "-ac", "2", "-ar", "48000", "-"])
        if not decoder:
            return
        self._feed(gen, decoder)
        if gen != self._gen:
            return
        while gen == self._gen and (self.status == "PAUSED" or self.mixer.radio_queued() > 0.05):
            time.sleep(0.1)              # let the end play out (and wait while paused)
        if gen == self._gen:
            self.index = (index + 1) % len(self.results)
            self._play(gen, self.index)

    def _feed(self, gen, decoder):
        """Queue the decoded audio, waiting whenever the core's buffer is full."""
        while gen == self._gen:
            data = decoder.stdout.read(CHUNK)
            if not data:
                return
            while data and gen == self._gen:
                taken = self.mixer.radio_push(data)
                self.played += taken
                data = data[taken:]
                if data:
                    time.sleep(0.05)
