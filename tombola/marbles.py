"""Random chord loop with a deja-vu control, after Emilie Gillet's random-loop design.

A loop of LENGTH scale degrees is played step by step. DEJA VU decides how much
of it is remembered, as on Marbles:

  0.0        every step draws a new random chord (nothing repeats)
  0 .. 0.5   each step replays the stored chord with probability deja / 0.5,
             otherwise draws a new one and stores it: the loop slowly evolves
  0.5        locked: the same sequence forever
  0.5 .. 1   chords still come from the loop, but steps jump to a random slot
             more and more often: the loop is shuffled, never rewritten

New chords are drawn relative to the current one: SPREAD sets how far a move
may jump round the scale, BIAS tilts the odds toward the stable degrees (I, IV,
V, VI) or the colourful ones (II, III, VII).
"""
import math
import random

DEGREES = 7
STABLE = {0, 3, 4, 5}                    # I, IV, V, VI
MAX_LENGTH = 16
FRESH_STEPS = 4                          # how long a regenerated slot stays marked as new


class ChordSequencer:
    def __init__(self, length=8, deja=0.25, spread=0.4, bias=0.6, rng=None):
        self.rng = rng or random.Random()
        self.length, self.deja, self.spread, self.bias = length, deja, spread, bias
        self.pos = 0
        self.current = 0
        self.steps = 0
        self.fresh = {}                  # slot -> step at which it was (re)generated
        self.loop = []
        self.regenerate()

    def _weight(self, frm, to):
        d = min((to - frm) % DEGREES, (frm - to) % DEGREES)   # distance round the scale, 0..3
        reach = 0.35 + 3.0 * self.spread
        w = math.exp(-max(0, d - 1) / reach) * (0.25 if d == 0 else 1.0)   # neighbours easiest
        stable = 0.3 + 1.4 * self.bias
        return w * (stable if to in STABLE else 2.0 - stable)   # bias 0.5: no preference

    def draw(self, frm):
        """A new degree, relative to frm."""
        weights = [self._weight(frm, to) for to in range(DEGREES)]
        return self.rng.choices(range(DEGREES), weights)[0]

    def regenerate(self):
        """A brand-new loop, starting from the tonic."""
        self.loop, prev = [], 0
        for i in range(self.length):
            prev = 0 if i == 0 else self.draw(prev)
            self.loop.append(prev)
        self.fresh = {i: self.steps for i in range(self.length)}
        self.pos = 0

    def set_length(self, length):
        """Grow by drawing new chords at the end, or drop the tail."""
        while len(self.loop) < length:
            self.loop.append(self.draw(self.loop[-1] if self.loop else 0))
            self.fresh[len(self.loop) - 1] = self.steps
        del self.loop[length:]
        self.length = length
        self.pos %= length

    def next(self):
        """Advance one step; returns the new current degree."""
        self.steps += 1
        slot = self.pos
        if self.deja > 0.5 and self.rng.random() < (self.deja - 0.5) / 0.5:
            slot = self.rng.randrange(self.length)                       # shuffle
        elif self.deja < 0.5 and self.rng.random() >= self.deja / 0.5:
            self.loop[slot] = self.draw(self.current)                    # regenerate this slot
            self.fresh[slot] = self.steps
        self.current = self.loop[slot]
        self.pos = (slot + 1) % self.length
        return self.current

    def is_fresh(self, slot):
        return self.steps - self.fresh.get(slot, -FRESH_STEPS - 1) <= FRESH_STEPS
