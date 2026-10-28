"""Every user-facing setting, the two menu pages, and their persistence."""
import json
import os
from dataclasses import dataclass

from music import KEYS, SCALES, SHAPES
from rings import RATIOS
from synth import INSTRUMENTS
from ui import VISUALS

CONFIG = os.path.expanduser("~/.config/play-tombola.json")
PROGRESSIONS = {                 # scale degrees per chord
    "I-VI-IV-V": (0, 5, 3, 4),
    "I-IV-V-I": (0, 3, 4, 0),
    "I-V-VI-IV": (0, 4, 5, 3),
    "I-III-VI-IV": (0, 2, 5, 3),
    "DRONE": (0,),
}
# Each page lists (side settings, knobs). Shared settings sit on both pages.
PAGES = {
    "BALLS": (("key", "scale", "instr", "attack", "release", "shape", "visual"),
              ("balls", "grav", "rot", "bounce", "octave", "reverb", "grain", "vol")),
    "RINGS": (("key", "scale", "tone", "chord", "change", "ratio", "visual"),
              ("rings", "moons", "speed", "tilt", "bed", "ping", "reverb", "grain", "vol")),
}
# r = respawn with fresh random settings, kept in ranges that stay musical.
# VOL and VISUAL are left alone so a reroll never blasts or changes the look.
RANDOM_RANGES = {
    "key": None, "scale": None, "instr": None, "shape": None,
    "attack": (1, 20), "release": (0.8, 3.0), "balls": (2, 6), "grav": (10, 60),
    "rot": (-60, 60), "bounce": (60, 96), "octave": (-1, 1), "reverb": (20, 70),
    "grain": (0, 60), "bed": (0, 70),
    "tone": None, "chord": None, "ratio": None, "change": (3, 12), "rings": (2, 4),
    "moons": (1, 3), "speed": (15, 70), "tilt": (4, 24), "ping": (20, 70),
}


@dataclass
class Param:
    key: str
    label: str
    values: tuple
    index: int
    fmt: str = "{}"

    @property
    def value(self):
        return self.values[self.index]

    def text(self):
        return self.fmt.format(self.value)

    def nudge(self, d):
        """Numbers stop at their range ends; named choices (key, scale, ...) wrap around."""
        if isinstance(self.value, str):
            self.index = (self.index + d) % len(self.values)
        else:
            self.index = max(0, min(len(self.values) - 1, self.index + d))


def _p(key, label, values, default, fmt="{}"):
    values = tuple(values)
    return Param(key, label, values, values.index(default), fmt)


def make_params():
    return [
        _p("key", "KEY", KEYS, "D#"),
        _p("scale", "SCALE", SCALES, "MINOR"),
        _p("instr", "INSTR", INSTRUMENTS, "PLUCK"),
        _p("attack", "ATTACK", (1, 2, 5, 10, 20, 50, 100, 200, 500), 2, "{}MS"),
        _p("release", "RELEASE", [round(0.2 * i, 1) for i in range(1, 21)], 1.6, "{:.1f} S"),
        _p("shape", "SHAPE", SHAPES, "HEX"),
        _p("visual", "VISUAL", VISUALS, "BLOCK"),
        _p("balls", "BALLS", range(1, 9), 4),
        _p("grav", "GRAV", range(0, 101, 5), 30),
        _p("rot", "ROT", range(-100, 101, 4), 20, "{:+d}"),
        _p("bounce", "BOUNCE", range(0, 101, 2), 86, "{}%"),
        _p("octave", "OCTAVE", range(-2, 3), 0, "{:+d}"),
        _p("reverb", "REVERB", range(0, 101, 5), 40, "{}%"),
        _p("grain", "GRAIN", range(0, 101, 5), 25, "{}%"),
        _p("vol", "VOL", range(0, 101, 5), 70, "{}%"),
        _p("tone", "TONE", INSTRUMENTS, "BELL"),
        _p("chord", "CHORD", PROGRESSIONS, "I-VI-IV-V"),
        _p("change", "CHANGE", (2, 3, 4, 6, 8, 12, 16), 6, "{} S"),
        _p("ratio", "RATIO", RATIOS, "KEPLER"),
        _p("rings", "RINGS", range(0, 5), 3),
        _p("moons", "MOONS", range(1, 5), 2),
        _p("speed", "SPEED", range(0, 101, 5), 50, "{}%"),
        _p("tilt", "TILT", range(0, 31, 2), 12, "{}°"),
        _p("bed", "PAD", range(0, 101, 5), 30, "{}%"),
        _p("ping", "PING", range(0, 101, 5), 50, "{}%"),
    ]


def page_params(params, page):
    """(side, knobs) Param lists for a page."""
    by_key = {p.key: p for p in params}
    side, knobs = PAGES[page]
    return [by_key[k] for k in side], [by_key[k] for k in knobs]


def load_params(path=CONFIG):
    params = make_params()
    try:
        with open(path) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return params
    if isinstance(saved, dict):
        for p in params:
            if saved.get(p.key) in p.values:   # external input: ignore anything unknown
                p.index = p.values.index(saved[p.key])
    return params


def save_params(params, path=CONFIG):
    """Write atomically: a kill mid-save leaves the previous file, never a broken one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump({p.key: p.value for p in params}, f)
    os.replace(tmp, path)
