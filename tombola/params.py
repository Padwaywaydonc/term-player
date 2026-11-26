"""Every user-facing setting, the two menu pages, and their persistence."""
import json
import os
from dataclasses import dataclass

from music import KEYS, SCALES, SHAPES
from rings import RATIOS
from arp import MODES as ARP_MODES
from clock import QUANTS, RATIOS as CLOCK_RATIOS
from radio import SOURCES as RADIO_SOURCES
from native import ENGINES, EXCITERS, GRAIN_MODES, MOD_MODES, WARP_CARRIERS
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
RANDOM = "RANDOM"                # a random chord loop (see marbles.py)
CHORDS = DEGREES + tuple(PROGRESSIONS) + (RANDOM,)
RESONATOR = "RESONATOR"          # balls strike a modal / string resonator instead of playing a note
SYNTH = "SYNTH"                  # balls trigger a synth voice (24 engines) through its low-pass gate
PHYSICAL = "PHYSICAL"            # balls strike, bow or blow a physically modelled instrument
BALL_INSTRUMENTS = INSTRUMENTS + (RESONATOR, SYNTH, PHYSICAL)
RING_TONES = tuple(RING_TIMBRES) + ENGINES                   # built-in timbres, then the synth engines
RESONATOR_MODELS = ("MODAL", "SYMPATH", "STRING", "FM", "SYMP-Q", "STR+REV")
# Values renamed since earlier versions: saved settings and undo history are migrated on load.
RENAMED = {"instr": {"RINGS": RESONATOR}, "chord": {"MARBLES": RANDOM}}
# Settings renamed since earlier versions (old key -> new key), migrated the same way.
RENAMED_KEYS = {"dplay": "on_drums", "dbpm": "bpm"}
TIMED = ("BALLS", "RINGS", "CHORDS", "DRUMS", "MODS")       # tabs that follow the master clock
SYNC_PAGE_SWAPS = {"RINGS": ("rsync", "speed", "rbars"), "MODS": ("msync", "mrate", "mbars")}
# With INSTR on RESONATOR the BALLS page swaps ATTACK/RELEASE for the resonator's controls.
# MODS lanes can sweep these (OFF first; the order is the native core's mod_slot()).
MOD_TARGETS = ("OFF", "GRAIN POS", "GRAIN SIZE", "DENSITY", "TEXTURE", "GRAIN LVL",
               "SYN TIMBRE", "SYN HARM", "SYN MORPH", "RES BRIGHT", "RES STRUCT", "RES POS",
               "PHY GEOM", "PHY BRIGHT", "RING TIMB", "RING MORPH", "PAD", "REVERB",
               "WARP ALGO", "WARP TIMB", "DRUM X", "DRUM Y", "CHAOS", "TILT")
# WARP's ALGO knob sweeps through these (the last stretch is the vocoder).
WARP_ALGOS = ("XFADE", "FOLD", "RING-A", "RING-D", "XOR", "COMPARE", "VOCODER")
RESONATOR_PAGE = (("key", "scale", "instr", "rmodel", "arp", "quant", "shape", "visual", "window"),
                  ("balls", "grav", "rot", "bounce", "octave", "rstruct", "rbright", "rdamp", "rpos", "rpoly",
                   "reverb", "grain", "vol", "bpm", "clk_balls"))
SYNTH_PAGE = (("key", "scale", "instr", "sengine", "arp", "quant", "shape", "visual", "window"),
              ("balls", "grav", "rot", "bounce", "octave", "sharm", "stimb", "smorph", "sdecay", "scolour",
               "reverb", "grain", "vol", "bpm", "clk_balls"))
PHYSICAL_PAGE = (("key", "scale", "instr", "pexc", "arp", "quant", "shape", "visual", "window"),
                 ("balls", "grav", "rot", "bounce", "octave", "pgeo", "pbright", "pdamp", "ppos", "pspace",
                  "reverb", "grain", "vol", "bpm", "clk_balls"))
RING_SYNTH_KNOBS = ("rharm", "rtimb", "rmorph")              # shown on RINGS when INSTR is a synth engine
# Each page lists (side settings, knobs). Shared settings sit on both pages.
PAGES = {
    "BALLS": (("key", "scale", "instr", "arp", "quant", "attack", "release", "shape", "visual", "window"),
              ("balls", "grav", "rot", "bounce", "octave", "reverb", "grain", "vol", "bpm", "clk_balls")),
    "RINGS": (("key", "scale", "tone", "chord", "ratio", "rsync", "visual", "window"),
              ("rings", "moons", "speed", "tilt", "roct", "bed", "floor", "reverb", "grain", "rvol", "bpm", "clk_rings")),
    "CHORDS": (("key", "scale", "chord", "clock", "visual", "window"),
               ("cbars", "change", "jitter", "chance", "length", "deja", "cspread", "cbias", "roct", "bpm", "clk_chords")),
    "GRAINS": (("key", "scale", "source", "gmode", "freeze", "visual", "window"),
               ("grain", "density", "size", "position", "pitch", "spread", "texture", "feedback", "gwash", "reverb")),
    "DRUMS": (("key", "scale", "visual", "window"),
              ("dx", "dy", "dchaos", "dkick", "dsnare", "dhats", "dtone", "ddecay", "dvol", "bpm", "clk_drums")),
    "WARP": (("key", "scale", "wcar", "visual", "window"),
             ("walgo", "wtimb", "wdrive", "wmix")),
    "MODS": (("key", "scale", "mmode", "msync", "visual", "window"),
             ("mrate", "mshape", "mslope", "msmooth", "mshift", "mto1", "mdep1", "mto2", "mdep2",
              "mto3", "mdep3", "mto4", "mdep4", "bpm", "clk_mods")),
    "RADIO": (("rsrc", "visual", "window"),
              ("radvol",)),
}
# r = fresh random settings for the current page, kept in ranges that stay musical.
# Only keys listed here are rolled; VOLs, VISUAL and shared settings never are.
RANDOM_RANGES = {
    "key": None, "scale": None, "instr": None, "shape": None,
    "attack": (1, 20), "release": (0.8, 3.0), "balls": (2, 6), "grav": (10, 60),
    "rot": (-60, 60), "bounce": (60, 110), "octave": (-1, 1), "reverb": (20, 70),
    "grain": (0, 60), "bed": (0, 70),
    "tone": None, "chord": None, "ratio": None, "change": (3, 12), "rings": (2, 4),
    "moons": (1, 3), "speed": (15, 70), "tilt": (4, 24), "floor": (0, 50),
    "sengine": None, "sharm": (10, 90), "stimb": (10, 90), "smorph": (10, 90), "sdecay": (20, 80),
    "scolour": (0, 100), "pexc": None, "pgeo": (0, 100), "pbright": (20, 90), "pdamp": (20, 80),
    "ppos": (10, 90), "pspace": (10, 70), "rharm": (10, 90), "rtimb": (10, 90), "rmorph": (10, 90),
    "rmodel": None, "gmode": None, "rstruct": (0, 80), "rbright": (20, 90), "rdamp": (25, 80), "rpos": (10, 90), "rpoly": None,
    "clock": None, "jitter": (0, 40), "chance": (60, 100), "length": (4, 12), "deja": (0, 60),
    "cspread": (10, 90), "cbias": (30, 90),
    "density": (-60, 80), "size": (40, 300), "position": (0, 80), "pitch": (-12, 12), "spread": (0, 70),
    "texture": (20, 100), "feedback": (0, 40), "gwash": (0, 70), "source": None, "roct": (0, 2), "arp": None,
    "dx": None, "dy": (0, 70), "dchaos": (0, 40), "dkick": (30, 80), "dsnare": (20, 70),
    "dhats": (20, 80), "dtone": (20, 80), "ddecay": (20, 80),
    "wcar": None, "walgo": None, "wtimb": None, "wdrive": None,
    "mmode": None, "mrate": (0.05, 2), "mshape": None, "mslope": None, "msmooth": None, "mshift": None,
    "mto1": None, "mto2": None, "mto3": None, "mto4": None, "mdep1": (-60, 60), "mdep2": (-60, 60),
    "mdep3": (-60, 60), "mdep4": (-60, 60),
    "rbars": None, "mbars": None, "cbars": None,         # QUANT and SYNC stay: r never knocks a tab out of time
    **{f"clk_{page.lower()}": ("/4", "x2") for page in TIMED},
}


def _page_keys(page):
    """Every key a page can show, whatever INSTR or TONE is set to."""
    layouts = [PAGES[page]] + {"BALLS": [RESONATOR_PAGE, SYNTH_PAGE, PHYSICAL_PAGE],
                               "RINGS": [((), RING_SYNTH_KNOBS + ("rbars",))],
                               "MODS": [((), ("mbars",))]}.get(page, [])
    return {k for side, knobs in layouts for k in side + knobs}


# Settings on more than one page (KEY, SCALE, VISUAL, REVERB, ...) are global: r never rolls them.
SHARED = {k for k in set().union(*map(_page_keys, PAGES))
          if sum(k in _page_keys(page) for page in PAGES) > 1}


def random_keys(page):
    """The RANDOM_RANGES keys r rolls on this page: its own settings only."""
    own = _page_keys(page) - SHARED
    return [k for k in RANDOM_RANGES if k in own]


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
        _p("instr", "INSTR", BALL_INSTRUMENTS, "PLUCK"),
        # Resonator (INSTR = RESONATOR)
        _p("rmodel", "MODEL", RESONATOR_MODELS, "MODAL"),
        _p("rstruct", "STRUCTURE", range(0, 101, 5), 25, "{}%"),  # partial ratios / stiffness / tuning
        _p("rbright", "BRIGHTNESS", range(0, 101, 5), 55, "{}%"), # wood/nylon .. glass/steel
        _p("rdamp", "DAMPING", range(0, 101, 5), 50, "{}%"),      # ring time 0.08 .. 10 s
        _p("rpos", "POSITION", range(0, 101, 5), 30, "{}%"),      # strike point: 100% = centre
        _p("rpoly", "POLY", (1, 2, 4), 4),                        # voices; 1 = each hit chokes the last
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
        _p("tone", "INSTR", RING_TONES, "WARM"),                # ring tones' timbre (RINGS page)
        _p("rharm", "HARMONICS", range(0, 101, 5), 50, "{}%"),  # ring synth-engine controls
        _p("rtimb", "TIMBRE", range(0, 101, 5), 50, "{}%"),
        _p("rmorph", "MORPH", range(0, 101, 5), 50, "{}%"),
        # Synth ball voice (INSTR = SYNTH)
        _p("sengine", "ENGINE", ENGINES, "VA"),
        _p("sharm", "HARMONICS", range(0, 101, 5), 50, "{}%"),
        _p("stimb", "TIMBRE", range(0, 101, 5), 50, "{}%"),
        _p("smorph", "MORPH", range(0, 101, 5), 50, "{}%"),
        _p("sdecay", "DECAY", range(0, 101, 5), 50, "{}%"),
        _p("scolour", "COLOUR", range(0, 101, 5), 50, "{}%"),    # low-pass gate: VCA-like .. filter-like
        # Physical-modelling ball voice (INSTR = PHYSICAL)
        _p("pexc", "EXCITER", EXCITERS, "STRIKE"),
        _p("pgeo", "GEOMETRY", range(0, 101, 5), 50, "{}%"),     # string .. bar .. plate .. bell
        _p("pbright", "BRIGHTNESS", range(0, 101, 5), 50, "{}%"),
        _p("pdamp", "DAMPING", range(0, 101, 5), 50, "{}%"),
        _p("ppos", "POSITION", range(0, 101, 5), 30, "{}%"),
        _p("pspace", "SPACE", range(0, 101, 5), 30, "{}%"),      # the voice's own stereo reverb
        _p("chord", "CHORD", CHORDS, "I"),
        _p("change", "CHANGE", (2, 3, 4, 6, 8, 12, 16), 6, "{} S"),
        # CHORDS page: when the chord moves (progressions and RANDOM) and the random loop
        _p("clock", "CLOCK", ("MOON", "TIME", "BARS"), "MOON"),          # outer moon at its peak / every CHANGE s
        _p("jitter", "JITTER", range(0, 101, 5), 0, "{}%"),     # TIME clock: random variation
        _p("chance", "CHANCE", range(0, 101, 5), 100, "{}%"),   # coin toss per tick: change or stay
        _p("length", "LENGTH", range(1, 17), 8),
        _p("deja", "DEJA VU", range(0, 101, 5), 25, "{}%"),     # 0 new, 50 locked, 100 shuffled
        _p("cspread", "SPREAD", range(0, 101, 5), 40, "{}%"),   # how far a new chord may jump
        _p("cbias", "BIAS", range(0, 101, 5), 60, "{}%"),       # colourful .. stable degrees
        _p("ratio", "RATIO", RATIOS, "KEPLER"),
        _p("rings", "RINGS", range(0, 5), 3),
        _p("moons", "MOONS", range(1, 5), 2),
        _p("speed", "SPEED", range(0, 101, 5), 50, "{}%"),
        _p("tilt", "TILT", range(0, 31, 2), 12, "{}°"),
        _p("bed", "PAD", range(0, 101, 5), 30, "{}%"),
        # GRAINS page: the granular processor
        _p("source", "SOURCE", Mixer.SOURCES, "ALL"),
        _p("gmode", "MODE", GRAIN_MODES, "GRANULAR"),
        _p("freeze", "FREEZE", ("OFF", "ON"), "OFF"),
        _p("density", "DENSITY", range(-100, 101, 5), 30, "{:+d}"),   # <0 steady, 0 none, >0 random
        _p("size", "SIZE", (20, 40, 60, 80, 120, 160, 200, 300, 400, 500), 120, "{}MS"),
        _p("position", "POSITION", range(0, 101, 5), 30, "{}%"),    # how far back in the 2 s buffer
        _p("pitch", "PITCH", range(-12, 13), 0, "{:+d}"),
        _p("spread", "SPREAD", range(0, 101, 5), 30, "{}%"),
        _p("texture", "TEXTURE", range(0, 101, 5), 70, "{}%"),      # square -> triangle -> smooth
        _p("feedback", "FEEDBACK", range(0, 101, 5), 0, "{}%"),       # top quarter: sustains forever
        _p("gwash", "WASH", range(0, 101, 5), 0, "{}%"),              # the grain engine's own reverb
        _p("floor", "FLOOR", range(0, 101, 5), 15, "{}%"),      # ring volume with its moon at its lowest: 0 silent .. 100 drone
        _p("arp", "ARP", ARP_MODES, "OFF"),                     # hits walk the chord (1-3-5-7, two octaves)
        # DRUMS page: a drum-pattern map (X/Y) and three drum voices
        # Master clock and each timed tab's division / multiple of it
        _p("bpm", "BPM", (*range(10, 60, 5), *range(60, 201, 4)), 112),    # 10..200; fine steps up top
        *(_p(f"clk_{page.lower()}", "CLK", tuple(CLOCK_RATIOS), "x1") for page in TIMED),
        _p("quant", "QUANT", tuple(QUANTS), "1/16"),             # ball hits wait for the clock's grid
        _p("rsync", "SYNC", ("OFF", "ON"), "ON"),                # rings orbit in bars of their clock
        _p("rbars", "ORBIT", (1, 2, 4, 8, 16), 4, "{} BAR"),     # innermost ring's orbit (SYNC on)
        _p("cbars", "BARS", (1, 2, 4, 8), 2, "{} BAR"),          # BARS clock: change every N bars
        _p("msync", "SYNC", ("OFF", "ON"), "ON"),                # mods cycle in bars of their clock
        _p("mbars", "CYCLE", ("1/4", "1/2", "1", "2", "4", "8", "16"), "4", "{} BAR"),
        # Each tab's on/off (o); not on any page, kept out of r and u
        *(_p(f"on_{page.lower()}", "ON", ("OFF", "ON"), "OFF" if page in ("DRUMS", "RADIO") else "ON")
          for page in ("BALLS", "RINGS", "CHORDS", "GRAINS", "DRUMS", "WARP", "MODS", "RADIO")),
        # RADIO page
        _p("rsrc", "SOURCE", RADIO_SOURCES, "YOUTUBE"),
        _p("radvol", "VOL", range(0, 101, 5), 50, "{}%"),
        _p("dx", "MAP X", range(0, 101, 5), 20, "{}%"),          # straight .. broken
        _p("dy", "MAP Y", range(0, 101, 5), 30, "{}%"),          # sparse .. busy
        _p("dchaos", "CHAOS", range(0, 101, 5), 10, "{}%"),      # random extra hits per bar
        _p("dkick", "KICK", range(0, 101, 5), 60, "{}%"),        # density per part: 0 silent
        _p("dsnare", "SNARE", range(0, 101, 5), 50, "{}%"),
        _p("dhats", "HATS", range(0, 101, 5), 60, "{}%"),
        _p("dtone", "TONE", range(0, 101, 5), 50, "{}%"),
        _p("ddecay", "DECAY", range(0, 101, 5), 50, "{}%"),
        _p("dvol", "VOL", range(0, 101, 5), 60, "{}%"),
        # WARP page: the balls cross-modulate a carrier
        _p("wcar", "CARRIER", WARP_CARRIERS, "RINGS"),           # rings, drums, or an oscillator at the chord root
        _p("walgo", "ALGO", range(0, 96, 5), 25, "{}%"),         # see WARP_ALGOS; the top is the vocoder
        _p("wtimb", "TIMBRE", range(0, 101, 5), 50, "{}%"),
        _p("wdrive", "DRIVE", range(0, 101, 5), 50, "{}%"),      # input saturation (character, not level)
        _p("wmix", "MIX", range(0, 101, 5), 0, "{}%"),           # 0 = off
        # MODS page: four slow modulation lanes
        _p("mmode", "MODE", MOD_MODES, "PHASE"),                 # lanes phase-shifted / at other rates / panned
        _p("mrate", "RATE", (0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 5, 8), 0.2, "{} HZ"),
        _p("mshape", "SHAPE", range(0, 101, 5), 50, "{}%"),
        _p("mslope", "SLOPE", range(0, 101, 5), 50, "{}%"),      # rise vs fall
        _p("msmooth", "SMOOTH", range(0, 101, 5), 50, "{}%"),
        _p("mshift", "SHIFT", range(0, 101, 5), 50, "{}%"),      # how the four lanes differ
        *(_p(f"mto{k}", f"TO {k}", MOD_TARGETS, "OFF") for k in range(1, 5)),
        *(_p(f"mdep{k}", "DEPTH", range(-100, 101, 10), 50, "{:+d}%") for k in range(1, 5)),
    ]


def page_params(params, page):
    """(side, knobs) Param lists for a page."""
    by_key = {p.key: p for p in params}
    side, knobs = PAGES[page]
    if page == "BALLS":
        side, knobs = {RESONATOR: RESONATOR_PAGE, SYNTH: SYNTH_PAGE,
                       PHYSICAL: PHYSICAL_PAGE}.get(by_key["instr"].value, (side, knobs))
    if page in SYNC_PAGE_SWAPS:                  # synced: a length in bars replaces the free speed
        sync, free, bars = SYNC_PAGE_SWAPS[page]
        if by_key[sync].value == "ON":
            knobs = tuple(bars if k == free else k for k in knobs)
    if page == "RINGS" and by_key["tone"].value in ENGINES:
        at = knobs.index("floor") + 1
        knobs = knobs[:at] + RING_SYNTH_KNOBS + knobs[at:]
    return [by_key[k] for k in side], [by_key[k] for k in knobs]


def snapshot(params):
    return {p.key: p.value for p in params}


def apply_snapshot(params, snap):
    """Set params from a saved dict, skipping anything unknown or out of range."""
    snap = {RENAMED_KEYS.get(k, k): v for k, v in snap.items()
            if not (k in RENAMED_KEYS and RENAMED_KEYS[k] in snap)}      # a saved new key wins over its old name
    snap = {k: RENAMED.get(k, {}).get(v, v) if isinstance(v, str) else v for k, v in snap.items()}
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


def load_radio(path=CONFIG):
    """The RADIO page's last search (text)."""
    query = _read(path).get("radio_query", "")
    return query[:120] if isinstance(query, str) else ""


def load_seed(path=CONFIG):
    """The chord loop's seed from last time, or None."""
    seed = _read(path).get("loop_seed")
    return seed if isinstance(seed, int) and 0 < seed < 2 ** 31 else None


def save_params(params, path=CONFIG, history=(), radio_query="", loop_seed=None):
    """Write atomically: a kill mid-save leaves the previous file, never a broken one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump({"version": CONFIG_VERSION, **snapshot(params), "history": list(history)[-HISTORY_MAX:],
                   "radio_query": radio_query, "loop_seed": loop_seed}, f)
    os.replace(tmp, path)
