"""Resonator voice for ball hits, modelled on Mutable Instruments Rings.

Each hit is a strum: a short exciter (pulse or filtered noise burst, brighter for
harder hits) sets a resonating structure ringing.

  MODAL    tuned partials with their own decays; STRUCTURE morphs the partial
           ratios string -> bar -> plate
  STRING   extended Karplus-Strong: noise-burst excitation, loop low-pass for
           damping, fractional-delay tuning, a stiffness all-pass (STRUCTURE)
           for piano-like / inharmonic tones
  SYMPATH  the plucked string plus sympathetic strings that bloom along with
           it: octaves and fifths, or the rings' current chord (STRUCTURE)

BRIGHTNESS goes from wood/nylon to glass/steel, DAMPING sets the ring time
(0.08-10 s), POSITION where the strike lands (the middle cancels even harmonics).
"""
import cmath
import math
import operator
import random
from array import array
from itertools import accumulate, repeat

from synth import SAMPLE_RATE, midi_to_freq

MODELS = ("MODAL", "STRING", "SYMPATH")
STRENGTHS = (0.25, 0.5, 0.75, 1.0)      # hits are rendered at the nearest of these (cached)
MAX_SECONDS = 4.0
STRING_RATIOS = tuple(float(k) for k in range(1, 9))
BAR_RATIOS = (1.0, 2.756, 5.404, 8.933, 13.34, 18.64, 24.89, 32.0)      # free-free beam
PLATE_RATIOS = (1.0, 1.594, 2.136, 2.296, 2.653, 2.918, 3.156, 3.501)   # plate / bell-like


def t60(damping):
    """DAMPING 0..1 -> ring time in seconds, 0.08 .. 10 (log scale)."""
    return 0.08 * (10 / 0.08) ** damping


def strength_step(strength):
    return min(STRENGTHS, key=lambda s: abs(s - strength))


def _ratios(structure):
    a, b, t = ((STRING_RATIOS, BAR_RATIOS, structure * 2) if structure <= 0.5
               else (BAR_RATIOS, PLATE_RATIOS, structure * 2 - 1))
    return [x + (y - x) * t for x, y in zip(a, b)]


def _strike(position):
    """POSITION 0..1 -> strike point along the string/bar, 0.05 (end) .. 0.5 (centre)."""
    return 0.05 + 0.45 * position


def _decaying(n, freq, amp, seconds, sr, phase=0.0):
    """amp * exp-decay(t60 = seconds) * sin(2 pi freq t + phase), built at C speed."""
    w = math.exp(math.log(0.001) / (seconds * sr)) * cmath.exp(2j * math.pi * freq / sr)
    z = amp * cmath.exp(1j * phase)
    return list(map(operator.attrgetter("imag"), accumulate(repeat(w, n - 1), operator.mul, initial=z)))


def _modal(f, n, structure, brightness, damping, position, strength, sr):
    ring, pos = t60(damping), _strike(position)
    tilt = 2.2 - 1.8 * brightness * (0.6 + 0.4 * strength)     # harder hits ring brighter
    out = [0.0] * n
    for i, ratio in enumerate(_ratios(structure)):
        fi = f * ratio
        if fi >= 0.45 * sr:
            break
        amp = abs(math.sin(math.pi * (i + 1) * pos)) * (i + 1) ** -tilt
        if amp < 1e-4:
            continue
        decay = ring / (1 + i * (1.2 - brightness))             # dark: the highs die first
        out = list(map(operator.add, out, _decaying(n, fi, amp, decay, sr, phase=0.3 * i)))
    return out


def _string(f, n, structure, brightness, damping, position, strength, sr, rng):
    loop_c = 0.25 + 0.7 * brightness                            # loop low-pass: dark loses highs fast
    a_stiff = -0.7 * structure                                  # stiffness all-pass: inharmonicity
    lp_delay = (1 - loop_c) / loop_c
    stiff_delay = (1 - a_stiff) / (1 + a_stiff)
    period = sr / f - lp_delay - stiff_delay                    # tune for the delay the filters add
    whole = max(2, int(period))
    frac = min(0.999, max(0.001, period - whole))
    a_tune = (1 - frac) / (1 + frac)                            # fractional-delay all-pass
    gain = 0.001 ** (1 / (t60(damping) * f))                    # loss per round trip

    # Exciter: one period of noise, low-passed by brightness and strength, then
    # comb-filtered by the strike position.
    c = 0.15 + 0.8 * brightness * (0.5 + 0.5 * strength)
    burst, lp = [], 0.0
    for _ in range(whole):
        lp += c * (rng.uniform(-1, 1) - lp)
        burst.append(lp)
    d = max(1, int(_strike(position) * whole))
    exc = [burst[i] - (burst[i - d] if i >= d else 0.0) for i in range(whole)]

    out = [0.0] * n
    lp = x1 = y1 = x2 = y2 = 0.0
    for i in range(n):
        fb = out[i - whole] if i >= whole else 0.0
        lp += loop_c * (fb - lp)
        t = a_tune * lp + x1 - a_tune * y1
        x1, y1 = lp, t
        s = a_stiff * t + x2 - a_stiff * y2
        x2, y2 = t, s
        out[i] = (exc[i] if i < whole else 0.0) + gain * s
    return out


def _sympathetic_set(midi, structure, chord):
    """Pitches of the sympathetic strings: octaves/fifths, or the chord folded near the note."""
    if structure < 0.5 or not chord:
        return [midi + 12, midi - 12, midi + 7, midi + 19]
    near = []
    for m in chord:
        while m < midi - 12:
            m += 12
        while m > midi + 12:
            m -= 12
        if m != midi:
            near.append(m)
    return near[:4] or [midi + 12, midi + 7]


def _sympathetic(midi, n, structure, damping, chord, sr):
    """Strings that ring along: slow bloom, long sustain, a little second harmonic."""
    ring = min(MAX_SECONDS, t60(damping) * 1.5)
    out = [0.0] * n
    for k, m in enumerate(_sympathetic_set(midi, structure, chord)):
        fm = midi_to_freq(m)
        if fm >= 0.22 * sr:
            continue
        out = list(map(operator.add, out, _decaying(n, fm, 0.22, ring, sr, phase=1.1 * k)))
        out = list(map(operator.add, out, _decaying(n, 2 * fm, 0.07, ring / 2, sr, phase=0.7 * k)))
    bloom = 0.05 * sr                                            # they swell in after the pluck
    return [x * (1 - math.exp(-i / bloom)) for i, x in enumerate(out)]


def render_resonator(midi, model, structure, brightness, damping, position, strength,
                     chord=(), sr=SAMPLE_RATE, rng=None):
    """One hit as a float array, peak ~0.9, faded out at its end."""
    rng = rng or random.Random(midi * 7919 + int(strength * 4))  # same hit -> same sound (cacheable)
    f = midi_to_freq(midi)
    n = int(min(MAX_SECONDS, t60(damping) * 1.1 + 0.05) * sr)
    if model == "MODAL":
        out = _modal(f, n, structure, brightness, damping, position, strength, sr)
    else:
        out = _string(f, n, structure, brightness, damping, position, strength, sr, rng)
        if model == "SYMPATH":
            peak = max(map(abs, out), default=0.0) or 1.0
            out = list(map(operator.add, [x / peak for x in out],
                           _sympathetic(midi, n, structure, damping, chord, sr)))
    tail = min(n, int(0.05 * sr))                                # no click where the buffer ends
    for i in range(tail):
        out[n - tail + i] *= 1 - i / tail
    peak = max(map(abs, out), default=0.0)
    k = 0.9 / peak if peak > 0 else 0.0
    return array("f", [x * k for x in out])


class RenderPool:
    """Renders resonator hits in worker processes, off the audio thread's interpreter.

    A hit's sound is often needed again (same note, settings and strength), so
    finished renders are cached. While a new setting is still rendering, get()
    returns the latest render of the same note and strength instead of making
    anyone wait — no stalls, no dropouts. inline=True renders synchronously
    (tests, or when processes can't be started).
    """

    LIMIT = 160

    def __init__(self, workers=2, inline=False):
        self.done, self.pending, self.latest = {}, {}, {}
        self.executor = None
        if not inline:
            try:
                from concurrent.futures import ProcessPoolExecutor
                from multiprocessing import get_context
                self.executor = ProcessPoolExecutor(workers, mp_context=get_context("spawn"))
            except (OSError, ImportError, ValueError):
                self.executor = None

    def _store(self, args, buf):
        if len(self.done) >= self.LIMIT:
            self.done.clear()
        self.done[args] = buf
        self.latest[(args[0], args[6])] = buf          # (midi, strength) -> newest sound

    def request(self, args):
        if args in self.done or args in self.pending:
            return
        if self.executor is None:
            self._store(args, render_resonator(*args))
            return
        future = self.executor.submit(render_resonator, *args)
        self.pending[args] = future

        def finished(f, a=args):
            self.pending.pop(a, None)
            if not f.cancelled() and f.exception() is None:
                self._store(a, f.result())
        future.add_done_callback(finished)

    def get(self, args):
        """The hit's sound, or the latest one for the same note while it renders (None at first)."""
        buf = self.done.get(args)
        if buf is None:
            self.request(args)
            buf = self.done.get(args) or self.latest.get((args[0], args[6]))
        return buf

    def close(self):
        if self.executor:
            self.executor.shutdown(wait=False, cancel_futures=True)
