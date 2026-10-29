"""Every user-facing setting, the two menu pages, and their persistence."""
import json
import os
from dataclasses import dataclass

from music import KEYS, SCALES, SHAPES
from rings import RATIOS
from synth import INSTRUMENTS, RING_TIMBRES, Mixer
from ui import VISUALS

CONFIG = os.path.expanduser("~/.config/play-tombola.json")
CONFIG_VERSION = 2
HISTORY_MAX = 20                 # settings remembered before each r, for undo (u)
DEGREES = ("I", "II", "III", "IV", "V", "VI", "VII")     # a held chord on this scale degree
PROGRESSIONS = {                 # or one that moves by itself every CHANGE seconds
    "I-VI-IV-V": (0, 5, 3, 4),
    "I-IV-V-I": (0, 3, 4, 0),
    "I-V-VI-IV": (0, 4, 5, 3),
    "I-III-VI-IV": (0, 2, 5, 3),
}
CHORDS = DEGREES + tuple(PROGRESSIONS)
# Each page lists (side settings, knobs). Shared settings sit on both pages.
PAGES = {
    "BALLS": (("key", "scale", "instr", "attack", "release", "shape", "visual", "window"),
              ("balls", "grav", "rot", "bounce", "octave", "reverb", "grain", "vol")),
    "RINGS": (("key", "scale", "tone", "chord", "change", "ratio", "visual", "window"),
              ("rings", "moons", "speed", "tilt", "roct", "bed", "floor", "reverb", "grain", "rvol")),
    "GRAINS": (("key", "scale", "source", "freeze", "visual", "window"),
               ("grain", "density", "size", "position", "pitch", "spread", "texture", "feedback", "reverb")),
}
# r = respawn with fresh random settings, kept in ranges that stay musical.
# Both VOLs and VISUAL are left alone so a reroll never blasts or changes the look.
RANDOM_RANGES = {
    "key": None, "scale": None, "instr": None, "shape": None,
    "attack": (1, 20), "release": (0.8, 3.0), "balls": (2, 6), "grav": (10, 60),
    "rot": (-60, 60), "bounce": (60, 110), "octave": (-1, 1), "reverb": (20, 70),
    "grain": (0, 60), "bed": (0, 70),
    "tone": None, "chord": None, "ratio": None, "change": (3, 12), "rings": (2, 4),
    "moons": (1, 3), "speed": (15, 70), "tilt": (4, 24), "floor": (0, 50),
    "density": (-60, 80), "size": (40, 300), "position": (0, 80), "pitch": (-12, 12), "spread": (0, 70),
    "texture": (20, 100), "feedback": (0, 40), "source": None, "roct": (0, 2),
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
        _p("visual", "VISUAL", VISUALS, "PIXEL"),
        _p("window", "WINDOW", ("OFF", "ON"), "OFF"),           # live scene behind Windows Terminal
        _p("balls", "BALLS", range(1, 9), 4),
        _p("grav", "GRAV", range(0, 101, 5), 30),
        _p("rot", "ROT", range(-100, 101, 4), 20, "{:+d}"),
        _p("bounce", "BOUNCE", range(0, 151, 2), 86, "{}%"),   # >100%: every hit adds energy
        _p("octave", "OCTAVE", range(-2, 3), 0, "{:+d}"),
        _p("reverb", "REVERB", range(0, 101, 5), 40, "{}%"),
        _p("grain", "GRAIN", range(0, 101, 5), 40, "{}%"),      # grain volume
        _p("vol", "VOL", range(0, 101, 5), 70, "{}%"),          # balls
        _p("rvol", "VOL", range(0, 101, 5), 70, "{}%"),         # rings: moons + pad
        _p("roct", "OCTAVE", range(-3, 4), 0, "{:+d}"),         # rings: pad + moons; 0 = lowest clearly audible, below = sub-bass
        _p("tone", "INSTR", RING_TIMBRES, "WARM"),              # ring tones' timbre (RINGS page)
        _p("chord", "CHORD", CHORDS, "I"),
        _p("change", "CHANGE", (2, 3, 4, 6, 8, 12, 16), 6, "{} S"),
        _p("ratio", "RATIO", RATIOS, "KEPLER"),
        _p("rings", "RINGS", range(0, 5), 3),
        _p("moons", "MOONS", range(1, 5), 2),
        _p("speed", "SPEED", range(0, 101, 5), 50, "{}%"),
        _p("tilt", "TILT", range(0, 31, 2), 12, "{}°"),
        _p("bed", "PAD", range(0, 101, 5), 30, "{}%"),
        # GRAINS page (modelled on Mutable Instruments Clouds)
        _p("source", "SOURCE", Mixer.SOURCES, "ALL"),
        _p("freeze", "FREEZE", ("OFF", "ON"), "OFF"),
        _p("density", "DENSITY", range(-100, 101, 5), 30, "{:+d}"),   # <0 steady, 0 none, >0 random
        _p("size", "SIZE", (20, 40, 60, 80, 120, 160, 200, 300, 400, 500), 120, "{}MS"),
        _p("position", "POSITION", range(0, 101, 5), 30, "{}%"),    # how far back in the 2 s buffer
        _p("pitch", "PITCH", range(-12, 13), 0, "{:+d}"),
        _p("spread", "SPREAD", range(0, 101, 5), 30, "{}%"),
        _p("texture", "TEXTURE", range(0, 101, 5), 70, "{}%"),      # square -> triangle -> smooth
        _p("feedback", "FEEDBACK", range(0, 91, 5), 0, "{}%"),
        _p("floor", "FLOOR", range(0, 101, 5), 15, "{}%"),      # ring volume with its moon at its lowest: 0 silent .. 100 drone
    ]


def page_params(params, page):
    """(side, knobs) Param lists for a page."""
    by_key = {p.key: p for p in params}
    side, knobs = PAGES[page]
    return [by_key[k] for k in side], [by_key[k] for k in knobs]


def snapshot(params):
    return {p.key: p.value for p in params}


def apply_snapshot(params, snap):
    """Set params from a saved dict, skipping anything unknown or out of range."""
    for p in params:
        if snap.get(p.key) in p.values:        # external input: ignore anything unknown
            p.index = p.values.index(snap[p.key])


def _read(path):
    try:
        with open(path) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def load_params(path=CONFIG):
    params = make_params()
    saved = _read(path)
    if saved.get("version") != CONFIG_VERSION:
        saved.pop("visual", None)              # v1 defaulted to BLOCK glyphs; start on PIXEL instead
    apply_snapshot(params, saved)
    return params


def load_history(path=CONFIG):
    history = _read(path).get("history")
    if not isinstance(history, list):
        return []
    return [h for h in history if isinstance(h, dict)][-HISTORY_MAX:]


def save_params(params, path=CONFIG, history=()):
    """Write atomically: a kill mid-save leaves the previous file, never a broken one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump({"version": CONFIG_VERSION, **snapshot(params), "history": list(history)[-HISTORY_MAX:]}, f)
    os.replace(tmp, path)
