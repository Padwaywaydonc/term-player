"""Pure-stdlib synth: cached per-note renders, a voice mixer, a cheap reverb.

Notes are rendered once per (pitch, instrument, envelope) and cached, so the
audio thread only has to sum slices and run the reverb — fast enough in plain
Python at SAMPLE_RATE with a couple of dozen voices.
"""
import math
import operator
import random
import threading
from array import array

SAMPLE_RATE = 22050
BLOCK = 256                 # every reverb delay below is longer than this,
                            # which lets each filter process a whole block at once
MAX_VOICES = 24
CACHE_LIMIT = 256

INSTRUMENTS = ("PLUCK", "BELL", "MARIMBA", "GLASS", "PAD")


def midi_to_freq(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _envelope(n, attack_n, release_s, sr):
    decay = math.exp(math.log(0.001) / max(1.0, release_s * sr))   # -60 dB over release
    a = max(1, attack_n)
    return [i / a if i < a else decay ** (i - a) for i in range(n)]


def render_note(midi, instr, attack_ms, release_s, sr=SAMPLE_RATE):
    """Return one note as a list of floats in [-1, 1]."""
    f = midi_to_freq(midi)
    attack_n = int(attack_ms / 1000 * sr)
    n = attack_n + int(release_s * sr)
    env = _envelope(n, attack_n, release_s, sr)
    w = 2 * math.pi * f / sr
    sin = math.sin

    if instr == "PLUCK":                       # Karplus-Strong
        period = max(2, int(sr / f))
        line = [random.uniform(-1, 1) for _ in range(period)]
        out = [0.0] * n
        prev = 0.0
        damp = 0.5 * (0.996 if f < 400 else 0.99)
        for i in range(n):
            j = i % period
            cur = line[j]
            line[j] = damp * (cur + prev)
            prev = cur
            out[i] = cur * env[i]
        return out
    if instr == "BELL":                        # 2-op FM, index decays with the note
        wm = w * 3.5
        return [0.8 * e * sin(w * i + 2.5 * e * sin(wm * i)) for i, e in enumerate(env)]
    if instr == "MARIMBA":                     # fundamental + fast-decaying 4th partial
        k = math.exp(math.log(0.001) / (0.08 * sr))
        return [e * (0.72 * sin(w * i) + 0.26 * k ** i * sin(4 * w * i)) for i, e in enumerate(env)]
    if instr == "GLASS":                       # soft sine with a quiet octave
        return [e * (0.75 * sin(w * i) + 0.15 * sin(2 * w * i)) for i, e in enumerate(env)]
    # PAD: two detuned triangles with a slow swell regardless of attack
    swell = int(0.25 * sr)
    w2 = w * 1.006
    tri = lambda ph: 2 / math.pi * math.asin(sin(ph))
    return [1.5 * e * min(1.0, i / swell) * (tri(w * i) + tri(w2 * i)) for i, e in enumerate(env)]


class _Delay:
    """Circular delay line that reads/writes a whole block per call."""

    def __init__(self, length):
        self.buf = [0.0] * length
        self.idx = 0

    def read(self, n):
        b, i, L = self.buf, self.idx, len(self.buf)
        return b[i:i + n] if i + n <= L else b[i:] + b[:i + n - L]

    def write(self, data):
        b, i, L, n = self.buf, self.idx, len(self.buf), len(data)
        if i + n <= L:
            b[i:i + n] = data
        else:
            k = L - i
            b[i:] = data[:k]
            b[:n - k] = data[k:]
        self.idx = (i + n) % L


class Reverb:
    """Schroeder reverb: 4 parallel combs into 2 series allpasses."""

    COMBS = (557, 593, 641, 677)
    ALLPASS = (347, 263)

    def __init__(self):
        self.combs = [_Delay(d) for d in self.COMBS]
        self.allpass = [_Delay(d) for d in self.ALLPASS]
        self.lp = 0.0

    def process(self, x, feedback=0.82, g=0.5):
        # One-pole lowpass on the input darkens the tail (stands in for comb damping).
        lp = self.lp
        damped = []
        for s in x:
            lp += 0.45 * (s - lp)
            damped.append(lp)
        self.lp = lp
        n = len(x)
        acc = [0.0] * n
        for c in self.combs:
            y = [d + feedback * h for d, h in zip(damped, c.read(n))]
            c.write(y)
            acc = list(map(operator.add, acc, y))
        sig = [a * 0.25 for a in acc]
        for ap in self.allpass:
            hist = ap.read(n)                  # holds the delayed v[n-D]
            v = [s + g * h for s, h in zip(sig, hist)]
            ap.write(v)
            sig = [h - g * vv for vv, h in zip(v, hist)]
        return sig


def render_chord(midis, seconds, fade, sr=SAMPLE_RATE):
    """Sustained chord for the bed: detuned sine pairs with fade-in/out for crossfading."""
    n, f = int(seconds * sr), max(1, int(fade * sr))
    ws = [2 * math.pi * midi_to_freq(m) * d / sr for m in midis for d in (0.997, 1.003)]
    norm = 1.0 / len(ws)
    sin = math.sin
    return [norm * min(1.0, i / f, (n - i) / f) * sum(sin(w * i) for w in ws) for i in range(n)]


def _mix(voices, out, gain_scale=1.0):
    """Add each [samples, pos, gain] voice's next len(out) samples; return finished voices."""
    n, done = len(out), []
    for v in voices:
        samples, pos, gain = v
        seg = samples[pos:pos + n]
        g = gain * gain_scale
        out[:len(seg)] = map(operator.add, out[:len(seg)], [x * g for x in seg])
        v[1] = pos + n
        if v[1] >= len(samples):
            done.append(v)
    return done


class Mixer:
    """Thread-safe voice pool. trigger() from the UI thread, render() from audio.

    Three layers feed the reverb: note voices, the chord bed, and grains cut
    from the recent note output (never from grains, so the cloud can't feed
    back into itself).
    """

    GRAIN_HISTORY = SAMPLE_RATE * 2
    GRAIN_RATE = 30                # grains per second at grain = 1.0

    def __init__(self):
        self.voices = []           # [samples, pos, gain]
        self.beds = []             # chord-bed voices, scaled live by self.bed
        self.grains = []           # audio thread only
        self.history = []          # recent note output the grains are cut from
        self.silent = self.GRAIN_HISTORY   # samples since the notes last made sound
        self.lock = threading.Lock()
        self.reverb = Reverb()
        self.wet = 0.4
        self.volume = 0.7
        self.grain = 0.0
        self.bed = 0.0
        self.grain_count = 0       # grains spawned so far; the visuals turn new ones into sparks
        self.cache = {}

    def note(self, midi, instr, attack_ms, release_s):
        key = (midi, instr, attack_ms, release_s)
        buf = self.cache.get(key)
        if buf is None:
            if len(self.cache) >= CACHE_LIMIT:
                self.cache.clear()
            buf = self.cache[key] = render_note(midi, instr, attack_ms, release_s)
        return buf

    def trigger(self, samples, gain):
        with self.lock:
            if len(self.voices) >= MAX_VOICES:
                self.voices.pop(0)             # steal the oldest
            self.voices.append([samples, 0, gain])

    def trigger_bed(self, samples):
        with self.lock:   # keep the previous chord so it fades out under the new one
            self.beds = self.beds[-1:] + [[samples, 0, 1.0]]

    def _spawn_grains(self, n, rng=random):
        hist = self.history
        count = int(self.grain * self.GRAIN_RATE * n / SAMPLE_RATE + rng.random())
        self.grain_count += count
        for _ in range(count):
            length = rng.randint(int(0.06 * SAMPLE_RATE), int(0.18 * SAMPLE_RATE))
            pitch = rng.choice((0.5, 1.0, 1.0, 2.0))
            span = int(length * pitch) + 1
            if len(hist) <= span:
                return
            start = rng.randrange(len(hist) - span)
            k = 2 * math.pi / length
            cos = math.cos
            grain = [hist[start + int(i * pitch)] * 0.5 * (1 - cos(k * i)) for i in range(length)]
            self.grains.append([grain, 0, 0.9])

    def render(self, n=BLOCK):
        """Mix the next n samples; returns s16le bytes."""
        out = [0.0] * n
        with self.lock:
            voices, beds = list(self.voices), list(self.beds)
        done = _mix(voices, out)
        self.silent = 0 if any(out) else self.silent + n
        if self.grain > 0 and self.silent < self.GRAIN_HISTORY:   # only while there is sound to cut
            self.history = (self.history + out)[-self.GRAIN_HISTORY:]
            self._spawn_grains(n)
        if self.grains:
            finished = set(map(id, _mix(self.grains, out)))
            self.grains = [g for g in self.grains if id(g) not in finished]
        done += _mix(beds, out, self.bed)
        if done:
            gone = set(map(id, done))
            with self.lock:
                self.voices = [v for v in self.voices if id(v) not in gone]
                self.beds = [v for v in self.beds if id(v) not in gone]
        wet = self.reverb.process(out)
        dry, w, vol, tanh = 1.0 - 0.5 * self.wet, self.wet * 1.6, self.volume, math.tanh
        return array("h", [int(32000 * tanh(vol * (d * dry + r * w))) for d, r in zip(out, wet)]).tobytes()

    def active(self):
        return len(self.voices)
