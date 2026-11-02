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
PAD_TABLE_SIZE = 2048           # one waveform cycle for table-lookup instruments

INSTRUMENTS = ("PLUCK", "BELL", "MARIMBA", "GLASS", "PAD", "KALIMBA", "VIBES", "ORGAN", "CHIME", "SAW")


def midi_to_freq(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _envelope(n, attack_n, release_s, sr):
    decay = math.exp(math.log(0.001) / max(1.0, release_s * sr))   # -60 dB over release
    a = max(1, attack_n)
    return [i / a if i < a else decay ** (i - a) for i in range(n)]


_HARMONIC_TABLES = {}
HARMONIC_SETS = {"ORGAN": ((1, 1.0), (2, 0.6), (3, 0.4), (4, 0.25), (6, 0.15)),
                 "SAW": tuple((k, 1 / k) for k in range(1, 9))}
# Sustained timbres for the ring tones (the chord). Harmonic (ratio, amplitude) sets.
RING_TIMBRES = {"WARM": ((1, 1.0), (2, 0.45), (3, 0.2)),
                "GLASS": ((1, 1.0), (2, 0.2)),
                "SINE": ((1, 1.0),),
                "ORGAN": HARMONIC_SETS["ORGAN"],
                "SAW": HARMONIC_SETS["SAW"]}


def _harmonic_table(name, max_ratio, sets=HARMONIC_SETS):
    """One normalised cycle of a harmonic set up to max_ratio (fewer for high notes: no aliasing)."""
    key = (id(sets), name, max_ratio)
    if key not in _HARMONIC_TABLES:
        parts = [(r, a) for r, a in sets[name] if r <= max_ratio]
        cycle = [sum(a * math.sin(r * 2 * math.pi * i / PAD_TABLE_SIZE) for r, a in parts)
                 for i in range(PAD_TABLE_SIZE)]
        peak = max(map(abs, cycle))
        _HARMONIC_TABLES[key] = [x / peak for x in cycle]
    return _HARMONIC_TABLES[key]


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

    def partials(pairs):
        """(ratio, amp) -> (angular step, amp), dropping any partial that would alias."""
        return [(w * r, a) for r, a in pairs if f * r < 0.45 * sr]

    def additive(parts, decays=None):
        """Sum of a * sin(s * i) [* k ** i] over parts, one whole list per partial (fast)."""
        acc = [0.0] * n
        for j, (s, a) in enumerate(parts):
            k = decays[j] if decays else 1.0
            if k == 1.0:
                wave = [a * sin(s * i) for i in range(n)]
            else:
                wave = [a * k ** i * sin(s * i) for i in range(n)]
            acc = list(map(operator.add, acc, wave))
        return acc

    if instr == "KALIMBA":                     # tine: fundamental + bright inharmonic overtone, gone fast
        k = math.exp(math.log(0.001) / (0.05 * sr))
        parts = partials(((1, 0.68), (5.94, 0.3)))
        return [e * x for e, x in zip(env, additive(parts, [1.0, k][:len(parts)]))]
    if instr == "VIBES":                       # vibraphone: sine + 4th partial under motor tremolo
        trem = 2 * math.pi * 5.5 / sr
        return [e * (0.72 * sin(w * i) + 0.18 * sin(4 * w * i)) * (0.8 + 0.2 * sin(trem * i))
                for i, e in enumerate(env)]
    if instr in HARMONIC_SETS:                 # ORGAN drawbars 1-2-3-4-6, SAW harmonics 1..8 at 1/k
        table = _harmonic_table(instr, max(1, int(0.45 * sr / f)))
        step, size = PAD_TABLE_SIZE * f / sr, PAD_TABLE_SIZE
        level = 0.85 if instr == "ORGAN" else 0.7
        return [level * e * table[int(step * i) % size] for i, e in enumerate(env)]
    if instr == "CHIME":                       # tubular bell: inharmonic partials, high ones fade first
        parts = partials(((1, 1.0), (2.76, 0.5), (5.40, 0.3), (8.93, 0.18)))
        decays = [math.exp(math.log(0.001) / (release_s * sr / (1 + r))) for r in (0.0, 1.5, 3.0, 5.0)]
        a_n = max(1, attack_n)
        return [0.5 * min(1.0, i / a_n) * x for i, x in enumerate(additive(parts, decays))]
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


RING_GLIDE = 0.3                 # per block: a chord change slides ~40 ms instead of clicking
RING_GAIN_STEP = 0.08            # max gain change per block: fades never crackle
RING_SCALE = 0.3                 # four rings at full swell stay clear of the clip


class _RingVoice:
    """One ring's continuous tone: two detuned table oscillators, phase never jumps."""

    __slots__ = ("freq", "target", "p1", "p2", "gain", "goal")

    def __init__(self, freq):
        self.freq = self.target = freq
        self.p1 = self.p2 = 0.0
        self.gain = self.goal = 0.0

    def render(self, n, timbre, out, scale):
        f = self.freq + (self.target - self.freq) * RING_GLIDE
        self.freq = self.target if abs(self.target - f) < 0.01 else f
        table = _harmonic_table(timbre, max(1, int(0.45 * SAMPLE_RATE / self.freq)), RING_TIMBRES)
        size = PAD_TABLE_SIZE
        s1 = size * self.freq * 0.997 / SAMPLE_RATE
        s2 = size * self.freq * 1.003 / SAMPLE_RATE
        g0 = self.gain
        g1 = g0 + max(-RING_GAIN_STEP, min(RING_GAIN_STEP, self.goal - g0))
        dg = (g1 - g0) / n
        p1, p2 = self.p1, self.p2
        if g0 > 0 or g1 > 0:
            seg = [scale * (g0 + dg * i) * (table[int(p1 + s1 * i) % size] + table[int(p2 + s2 * i) % size])
                   for i in range(n)]
            out[:] = map(operator.add, out, seg)
        self.p1, self.p2 = (p1 + s1 * n) % size, (p2 + s2 * n) % size
        self.gain = g1


CHOKE = 256                      # samples to fade a choked voice out (no click)


def _mix(voices, out):
    """Add each voice's next len(out) samples; return finished voices.

    A voice is [samples, pos, gain] or [samples, pos, gain, group, fade]; fade
    counts down the samples left of a choke fade-out (None when not choked).
    """
    n, done = len(out), []
    for v in voices:
        samples, pos, g = v[0], v[1], v[2]
        seg = samples[pos:pos + n]
        fade = v[4] if len(v) > 4 else None
        if fade is None:
            scaled = [x * g for x in seg]
        else:
            scaled = [x * g * max(0.0, (fade - j) / CHOKE) for j, x in enumerate(seg)]
            v[4] = fade - n
        out[:len(seg)] = map(operator.add, out[:len(seg)], scaled)
        v[1] = pos + n
        if v[1] >= len(samples) or (fade is not None and v[4] <= 0):
            done.append(v)
    return done


_WINDOWS = {}


def cached_window(length, texture):
    """grain_window, memoised: lengths are already rounded to 64 samples by the caller."""
    key = (length, round(texture, 2))
    if key not in _WINDOWS:
        if len(_WINDOWS) > 512:
            _WINDOWS.clear()
        _WINDOWS[key] = grain_window(length, texture)
    return _WINDOWS[key]


def grain_window(length, texture):
    """Grain envelope: texture 0 = square (tiny edge fades), 0.5 = triangle, 1 = smooth Hann."""
    last = max(1, length - 1)
    edge = max(1, int(0.004 * SAMPLE_RATE))
    cos, tau = math.cos, 2 * math.pi
    box = [min(1.0, i / edge, (last - i) / edge) for i in range(length)]
    tri = [1 - abs(2 * i / last - 1) for i in range(length)]
    if texture <= 0.5:
        a = texture / 0.5
        return [b * (1 - a) + t * a for b, t in zip(box, tri)]
    a = (texture - 0.5) / 0.5
    return [t * (1 - a) + (0.5 - 0.5 * cos(tau * i / last)) * a for i, t in enumerate(tri)]


class GrainCloud:
    """Granular processor for the Python fallback engine (the native core uses Emilie
    Gillet's open-source granular processor instead, see NOTICE).

    Records its source into a 2 s buffer (not while frozen) and replays grains
    from it. density < 0: grains at a steady rate, > 0: at random times, 0: none.
    Feedback mixes the cloud's own output back into what it records.
    """

    HISTORY = SAMPLE_RATE * 2
    MAX_RATE = 40                  # grains per second at |density| = 1
    MAX_GRAINS = 48
    SPREAD_STEPS = (12, -12, 7, 5, -5)   # spread jumps stay in key: octaves, fifths, fourths

    def __init__(self):
        self.level, self.density, self.size, self.position = 0.4, 0.3, 0.12, 0.3
        self.pitch, self.texture, self.spread, self.feedback = 0, 0.7, 0.3, 0.0
        self.freeze = False
        self.buf = [0.0] * self.HISTORY   # circular recording buffer
        self.wpos = 0
        self.filled = 0
        self.grains = []           # [samples, pos, gain]
        self.count = 0             # grains spawned so far (the visuals turn them into sparks)
        self.silent = self.HISTORY # samples since the source last made sound
        self._clock = 0.0
        self._prev = None

    def process(self, src, rng=random):
        """Record src (unless frozen), spawn grains, return this block of grain output."""
        n = len(src)
        if not self.freeze:
            if self.feedback and self._prev:
                # Overlapping grains are louder than what they read; divide that cloud gain out
                # so one trip round the loop is at most FEEDBACK (< 1): it echoes and dies away
                # instead of locking up at full scale. tanh is only a last-resort limiter.
                fb = self.feedback / max(1.0, self._cloud_gain())
                tanh = math.tanh
                block = [tanh(x + fb * g) for x, g in zip(src, self._prev)]
            else:
                block = src
            self.silent = 0 if any(block) else self.silent + n
            if self.silent < self.HISTORY:
                self._record(block)
        live = self.filled and (self.freeze or self.silent < self.HISTORY)
        if live and self.level > 0 and self.density:
            self._spawn(n, rng)
        out = [0.0] * n
        if self.grains:
            finished = set(map(id, _mix(self.grains, out)))
            self.grains = [g for g in self.grains if id(g) not in finished]
        self._prev = out
        return out

    @property
    def history(self):
        """The recording, oldest first."""
        return self._read(0, self.filled)

    def _record(self, block):
        buf, i, n, size = self.buf, self.wpos, len(block), self.HISTORY
        if i + n <= size:
            buf[i:i + n] = block
        else:
            k = size - i
            buf[i:] = block[:k]
            buf[:n - k] = block[k:]
        self.wpos = (i + n) % size
        self.filled = min(size, self.filled + n)

    def _read(self, start, length):
        """length samples from start (0 = oldest recorded sample)."""
        size = self.HISTORY
        i = (self.wpos - self.filled + start) % size
        end = i + length
        return self.buf[i:end] if end <= size else self.buf[i:] + self.buf[:end - size]

    def _cloud_gain(self):
        """Worst-case gain from the recording to the cloud's output (all grains in phase)."""
        overlap = max(1.0, abs(self.density) * self.MAX_RATE * self.size)
        return 1.4 * self.level * math.sqrt(overlap)

    def _spawn(self, n, rng):
        rate = abs(self.density) * self.MAX_RATE
        if self.density < 0:                               # steady
            self._clock += rate * n / SAMPLE_RATE
            count = int(self._clock)
            self._clock -= count
        else:                                              # random
            count = int(rate * n / SAMPLE_RATE + rng.random())
        overlap = max(1.0, rate * self.size)
        gain = 1.4 * self.level / math.sqrt(overlap)       # dense clouds don't just get louder
        filled = self.filled
        for _ in range(count):
            length = int(self.size * SAMPLE_RATE * (1 + self.spread * rng.uniform(-0.3, 0.3)))
            length = max(64, (length + 63) // 64 * 64)        # rounded so windows can be cached
            semis = self.pitch + (rng.choice(self.SPREAD_STEPS) if rng.random() < 0.6 * self.spread else 0)
            ratio = 2 ** (semis / 12)
            span = int(length * ratio) + 1
            if filled <= span + 1:
                return
            room = filled - span - 1
            back = self.position * room + self.spread * rng.uniform(0, 0.25) * filled
            seg = self._read(int(max(0, min(room, room - back))), span)
            window = cached_window(length, self.texture)
            if semis == 0:                                     # common cases at C speed
                samples = list(map(operator.mul, seg, window))
            elif semis == 12:
                samples = list(map(operator.mul, seg[::2], window))
            else:
                samples = [seg[int(i * ratio)] * w for i, w in enumerate(window)]
            self.grains.append([samples, 0, gain])
            self.count += 1
            if len(self.grains) > self.MAX_GRAINS:
                self.grains.pop(0)


class Mixer:
    """Thread-safe voice pool. trigger() from the UI thread, render() from audio.

    Three layers feed the reverb: note voices (balls), the ring tones — one
    continuous voice per chord note, its gain set live by set_rings() from the
    moons — and a GrainCloud fed from the balls, the rings or both.
    """

    SOURCES = ("ALL", "BALLS", "RINGS")
    sample_rate, channels, block = SAMPLE_RATE, 1, BLOCK
    name = "python"

    def __init__(self):
        self.voices = []           # [samples, pos, gain]
        self.ring_voices = []      # audio thread only
        self.ring_targets = []     # [(midi, gain)] per ring, set by set_rings()
        self.ring_timbre = "WARM"
        self.cloud = GrainCloud()  # audio thread only
        self.grain_source = "ALL"
        self.lock = threading.Lock()
        self.reverb = Reverb()
        self.wet = 0.4
        self.volume = 0.7
        self.bed = 0.0
        self.cache = {}

    def note(self, midi, instr, attack_ms, release_s):
        key = (midi, instr, attack_ms, release_s)
        buf = self.cache.get(key)
        if buf is None:
            if len(self.cache) >= CACHE_LIMIT:
                self.cache.clear()
            buf = self.cache[key] = render_note(midi, instr, attack_ms, release_s)
        return buf

    def trigger(self, samples, gain, group=None, poly=None):
        """Start a voice. With a group and poly, at most poly voices of that group ring:
        the oldest is choked with a short fade (like re-plucking a string)."""
        with self.lock:
            if group is not None and poly:
                ringing = [v for v in self.voices if v[3] == group and v[4] is None]
                for v in ringing[:max(0, len(ringing) - poly + 1)]:
                    v[4] = CHOKE
            if len(self.voices) >= MAX_VOICES:
                self.voices.pop(0)             # steal the oldest
            self.voices.append([samples, 0, gain, group, None])


    def set_rings(self, targets):
        """[(midi, gain 0..1)] for each ring; voices glide and ramp to them."""
        with self.lock:
            self.ring_targets = list(targets)

    def _render_rings(self, out):
        with self.lock:
            targets, timbre = self.ring_targets, self.ring_timbre
        voices = self.ring_voices
        for i, (midi, gain) in enumerate(targets):
            if i == len(voices):
                voices.append(_RingVoice(midi_to_freq(midi)))
            voices[i].target, voices[i].goal = midi_to_freq(midi), gain
        for v in voices[len(targets):]:
            v.goal = 0.0                       # rings removed: fade out, then drop
        for v in voices:
            v.render(len(out), timbre, out, RING_SCALE * self.bed)
        while len(voices) > len(targets) and voices[-1].gain <= 0:
            voices.pop()

    @property
    def grain_count(self):
        """Grains spawned so far; the visuals turn new ones into sparks."""
        return self.cloud.count

    def render(self, n=BLOCK):
        """Mix the next n samples; returns s16le bytes."""
        out = [0.0] * n
        with self.lock:
            voices = list(self.voices)
        done = _mix(voices, out)                # balls
        rings = [0.0] * n
        self._render_rings(rings)
        src = {"BALLS": out, "RINGS": rings}.get(self.grain_source) or list(map(operator.add, out, rings))
        grains = self.cloud.process(src)
        out = [b + r + g for b, r, g in zip(out, rings, grains)]
        if done:
            gone = set(map(id, done))
            with self.lock:
                self.voices = [v for v in self.voices if id(v) not in gone]
        wet = self.reverb.process(out)
        dry, w, vol, tanh = 1.0 - 0.5 * self.wet, self.wet * 1.6, self.volume, math.tanh
        return array("h", [int(32000 * tanh(vol * (d * dry + r * w))) for d, r in zip(out, wet)]).tobytes()

    def active(self):
        return len(self.voices)
