"""ARP: ball hits walk the current chord instead of playing the wall's note.

A port of the arpeggiator in Emilie Gillet's macro-oscillator firmware
(plaits/dsp/engine2/arpeggiator.h, MIT, see NOTICE): UP, DOWN, UP-DOWN or
RANDOM over the chord's notes, across RANGE octaves.
"""
import random

MODES = ("OFF", "UP", "DOWN", "UP-DN", "RANDOM")


class Arpeggiator:
    def __init__(self, mode="UP", range_=2, rng=None):
        self.mode, self.range = mode, range_
        self.rng = rng or random.Random()
        self.reset()

    def reset(self):
        self.note, self.octave, self.direction = 0, 0, 1

    def clock(self, num_notes):
        """Advance one step over num_notes chord notes; read .note and .octave after."""
        if num_notes == 1 and self.range == 1:
            self.note = self.octave = 0
            return
        if self.mode == "RANDOM":
            while True:
                octave, note = self.rng.randrange(self.range), self.rng.randrange(num_notes)
                if (octave, note) != (self.octave, self.note):
                    self.octave, self.note = octave, note
                    return
        if self.mode == "UP":
            self.direction = 1
        if self.mode == "DOWN":
            self.direction = -1
        self.note += self.direction
        done = False
        while not done:
            done = True
            if self.note >= num_notes or self.note < 0:
                self.octave += self.direction
                self.note = 0 if self.direction > 0 else num_notes - 1
            if self.octave >= self.range or self.octave < 0:
                self.octave = 0 if self.direction > 0 else self.range - 1
                if self.mode == "UP-DN":
                    self.direction = -self.direction
                    self.note = 1 if self.direction > 0 else num_notes - 2
                    self.octave = 0 if self.direction > 0 else self.range - 1
                    done = False
