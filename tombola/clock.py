"""The master clock: one tempo, and each tab running at a division or multiple of it.

Like a modular rig's clock: BPM sets the master, and every timed tab (balls,
rings, chords, drums, mods) has a CLK ratio (/8 .. x4). Ratios are whole
numbers or their inverses, so all tabs meet again on the master's downbeats.

With the native core the clock is its sample counter (sample-accurate, the
drums and the MODS lock read it there too); the Python engine adds up dt.
"""

RATIOS = {"/8": 1 / 8, "/4": 1 / 4, "/3": 1 / 3, "/2": 1 / 2, "x1": 1, "x2": 2, "x3": 3, "x4": 4}
BEATS_PER_BAR = 4
QUANTS = {"OFF": 0, "1/4": 1, "1/8": 2, "1/16": 4}     # ball hits wait for this grid (lines per beat)


class Clock:
    def __init__(self, mixer=None):
        self.mixer = mixer if mixer is not None and hasattr(mixer, "beats") else None
        self.beats = 0.0                     # master beats
        self.last = self.mixer.beats() if self.mixer else 0.0

    def advance(self, dt, bpm):
        """Move on one step; returns the master beats that passed."""
        if self.mixer:
            now = self.mixer.beats()
            delta, self.last = max(0.0, now - self.last), now
        else:
            delta = dt * bpm / 60
        self.beats += delta
        return delta
