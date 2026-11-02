"""Python side of the native audio core (libtombola_core.so, built from native/).

NativeMixer has the same interface as synth.Mixer, so the engine can use
either: the native core renders 48 kHz stereo with the vendored resonator and
granular processor; the pure-Python mixer is the fallback when the library
isn't built. Calls go through ctypes.CDLL, which releases the interpreter lock
while C++ renders.
"""
import ctypes
import os
from array import array

from synth import CACHE_LIMIT, render_note

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libtombola_core.so")
SAMPLE_RATE = 48000
CHANNELS = 2
BLOCK = 480                      # 10 ms; a multiple of the core's 48-frame chunk
TIMBRES = ("WARM", "GLASS", "SINE", "ORGAN", "SAW")
SOURCES = ("ALL", "BALLS", "RINGS")
GRAIN_MODES = ("GRANULAR", "STRETCH", "DELAY", "SPECTRAL")
GROUPS = {"balls": 0}


class TcParams(ctypes.Structure):     # mirrors Params in native/tombola_core.cc
    _fields_ = [("volume", ctypes.c_float), ("reverb", ctypes.c_float), ("ring_level", ctypes.c_float),
                ("ring_timbre", ctypes.c_int),
                ("res_model", ctypes.c_int), ("res_poly", ctypes.c_int),
                ("res_structure", ctypes.c_float), ("res_brightness", ctypes.c_float),
                ("res_damping", ctypes.c_float), ("res_position", ctypes.c_float),
                ("grain_level", ctypes.c_float), ("grain_density", ctypes.c_float),
                ("grain_size", ctypes.c_float), ("grain_position", ctypes.c_float),
                ("grain_pitch", ctypes.c_float), ("grain_spread", ctypes.c_float),
                ("grain_texture", ctypes.c_float), ("grain_feedback", ctypes.c_float),
                ("grain_freeze", ctypes.c_int), ("grain_source", ctypes.c_int), ("grain_mode", ctypes.c_int)]


def load():
    """The core library, or None when it isn't built (or won't load)."""
    try:
        lib = ctypes.CDLL(LIB)
    except OSError:
        return None
    lib.tc_create.restype = ctypes.c_void_p
    lib.tc_destroy.argtypes = [ctypes.c_void_p]
    lib.tc_set_params.argtypes = [ctypes.c_void_p, ctypes.POINTER(TcParams)]
    lib.tc_strike.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_float]
    lib.tc_play.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int,
                            ctypes.c_float, ctypes.c_int, ctypes.c_int]
    lib.tc_set_rings.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                 ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
    lib.tc_render.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16), ctypes.c_int]
    lib.tc_render.restype = ctypes.c_int
    lib.tc_grains.argtypes = [ctypes.c_void_p]
    lib.tc_grains.restype = ctypes.c_long
    lib.tc_voices.argtypes = [ctypes.c_void_p]
    lib.tc_voices.restype = ctypes.c_int
    return lib


class GrainSettings:
    """What the engine sets on mixer.cloud; read into TcParams every block."""

    def __init__(self):
        self.level, self.density, self.size, self.position = 0.4, 0.3, 0.12, 0.3
        self.pitch, self.texture, self.spread, self.feedback = 0, 0.7, 0.3, 0.0
        self.freeze = False
        self.mode = "GRANULAR"

    @property
    def count(self):                  # set by NativeMixer after each render
        return getattr(self, "_count", 0)


class NativeMixer:
    sample_rate, channels, block = SAMPLE_RATE, CHANNELS, BLOCK
    SOURCES = SOURCES
    name = "native"

    def __init__(self, lib):
        self.lib = lib
        self.handle = lib.tc_create()
        self.cloud = GrainSettings()
        self.wet, self.volume, self.bed = 0.4, 0.7, 0.0
        self.ring_timbre, self.grain_source = "WARM", "ALL"
        self.res = dict(model=0, poly=4, structure=0.25, brightness=0.55, damping=0.5, position=0.3)
        self.cache = {}
        self._params = TcParams()
        self._out = (ctypes.c_int16 * (BLOCK * CHANNELS))()

    def __del__(self):
        if getattr(self, "handle", None):
            self.lib.tc_destroy(self.handle)
            self.handle = None

    # --- the synth.Mixer interface -------------------------------------------------
    def note(self, midi, instr, attack_ms, release_s):
        key = (midi, instr, attack_ms, release_s)
        buf = self.cache.get(key)
        if buf is None:
            if len(self.cache) >= CACHE_LIMIT:
                self.cache.clear()
            buf = self.cache[key] = array("f", render_note(midi, instr, attack_ms, release_s, SAMPLE_RATE))
        return buf

    def trigger(self, samples, gain, group=None, poly=None):
        data = samples if isinstance(samples, array) and samples.typecode == "f" else array("f", samples)
        ptr = (ctypes.c_float * len(data)).from_buffer(data)
        self.lib.tc_play(self.handle, ptr, len(data), gain, GROUPS.get(group, -1), poly or 0)

    def strike(self, midi, strength):
        """A ball hitting the resonator: strength sets the exciter level."""
        self.lib.tc_strike(self.handle, float(midi), float(strength))

    def set_rings(self, targets):
        n = min(4, len(targets))
        midis = (ctypes.c_float * 4)(*[m for m, _ in targets[:n]], *[0.0] * (4 - n))
        gains = (ctypes.c_float * 4)(*[g for _, g in targets[:n]], *[0.0] * (4 - n))
        self.lib.tc_set_rings(self.handle, n, midis, gains)

    @property
    def grain_count(self):
        return self.lib.tc_grains(self.handle)

    def active(self):
        return self.lib.tc_voices(self.handle)

    def render(self, n=BLOCK):
        """n frames of interleaved stereo s16 (n is rounded to the core's 48-frame chunks)."""
        p, c, r = self._params, self.cloud, self.res
        p.volume, p.reverb, p.ring_level = self.volume, self.wet, self.bed
        p.ring_timbre = TIMBRES.index(self.ring_timbre) if self.ring_timbre in TIMBRES else 0
        p.res_model, p.res_poly = r["model"], r["poly"]
        p.res_structure, p.res_brightness = r["structure"], r["brightness"]
        p.res_damping, p.res_position = r["damping"], r["position"]
        p.grain_level, p.grain_density = c.level, c.density
        p.grain_size = max(0.0, min(1.0, (c.size - 0.02) / 0.48))          # 20..500 ms -> 0..1
        p.grain_position, p.grain_pitch = c.position, float(c.pitch)
        p.grain_spread, p.grain_texture, p.grain_feedback = c.spread, c.texture, c.feedback
        p.grain_freeze = int(bool(c.freeze))
        p.grain_source = SOURCES.index(self.grain_source) if self.grain_source in SOURCES else 0
        p.grain_mode = GRAIN_MODES.index(c.mode) if c.mode in GRAIN_MODES else 0
        self.lib.tc_set_params(self.handle, ctypes.byref(p))
        n = n // 48 * 48
        out = self._out if n == BLOCK else (ctypes.c_int16 * (n * CHANNELS))()
        done = self.lib.tc_render(self.handle, out, n)
        c._count = self.grain_count
        return bytes(memoryview(out).cast("B")[:done * CHANNELS * 2])
