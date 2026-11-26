"""The engine (balls + rings -> notes) and the terminal main loop."""
import math
import os
import random
import select
import shutil
import signal
import sys
import termios
import threading
import time
import tty

from arp import Arpeggiator
from clock import BEATS_PER_BAR, QUANTS, RATIOS, Clock
from music import KEYS, SHAPES, pad_note
from marbles import ChordSequencer, LoopSequencer
import native
from params import (CONFIG, DEGREES, HISTORY_MAX, PAGES, PROGRESSIONS, RANDOM, RANDOM_RANGES, SHARED, random_keys,  # noqa: F401
                    MOD_TARGETS, PHYSICAL, RESONATOR, RESONATOR_MODELS, SYNTH, WARP_ALGOS,
                    apply_snapshot, load_history, load_params, load_radio, load_seed, make_params, page_params, save_params,
                    snapshot)
from physics import BALL_SIZES, World
from radio import Radio
from backdrop import FPS as BACKDROP_FPS, Backdrop, scene_commands
from resonator import STRENGTHS, RenderPool, strength_step
from rings import TAU, Rings
from native import ENGINES, EXCITERS, MOD_MODES, WARP_CARRIERS
from synth import RING_TIMBRES, Mixer
import ui
from explain import explain
import presets

PHYS_DT = 1 / 120
PY_STANDIN = {SYNTH: "BELL", PHYSICAL: "MARIMBA"}             # Python engine stand-ins
PY_MODEL = {"MODAL": "MODAL", "SYMPATH": "SYMPATH", "STRING": "STRING",       # Python fallback stand-ins
            "FM": "MODAL", "SYMP-Q": "SYMPATH", "STR+REV": "STRING"}


def eval_bars(text):
    """A CYCLE length like "1/4" or "8" as a number of bars."""
    num, _, den = text.partition("/")
    return float(num) / float(den or 1)


def make_mixer():
    """The native 48 kHz core when it's built (and TOMBOLA_ENGINE isn't 'python'), else Python."""
    if os.environ.get("TOMBOLA_ENGINE") != "python":
        lib = native.load()
        if lib:
            return native.NativeMixer(lib)
    return Mixer()
FRAME_DT = 1 / 30
PAD_LOWEST = 40                  # E2 (~82 Hz): the pad's bass never goes below this
PREWARM_KEYS = {"key", "scale", "instr", "attack", "release", "octave", "shape", "arp",
                "rmodel", "rstruct", "rbright", "rdamp", "rpos"}
RING_ORDER = (1, 3, 2, 0)        # chord tones added as RINGS grows: root, +fifth, +third, +bass
ARP_NOTES = 4                    # ARP walks the chord's 1-3-5-7
ARP_RANGE = 2                    # ... across two octaves
DRUM_ROOT = 24                   # kick tuned to the key's root in octave 1 (33..62 Hz)
TILT_SWING = 15                  # degrees a full-depth MODS lane tilts the rings either way
MOD_LANES = 4
TOAST_S = 2.5                    # how long "SAVED PRESET 3" stays on the bottom line


class Engine:
    """Everything except the terminal: usable headless from tests."""

    def __init__(self, params, mixer=None, pool=None):
        self.params = {p.key: p for p in params}
        mixer = mixer or make_mixer()
        self._pool = pool                        # resonator renders; started on first RINGS use
        self.world = World()
        self.rings = Rings()
        self.mixer = mixer or Mixer()
        self.hits = 0
        self.on = True
        self.history = []                        # settings before each r, newest last
        self.chord_step = 0                      # progression position
        self.chord_timer = 0.0
        self.next_change = None                  # TIME clock: seconds until the next tick
        self.hold = False                        # x: freeze the moving chord
        self.rng = random.Random()
        self.arp = Arpeggiator(range_=ARP_RANGE, rng=self.rng)
        self.tilt_lanes = []                     # (lane, depth) MODS lanes aimed at TILT
        self.clock = Clock(self.mixer if isinstance(self.mixer, native.NativeMixer) else None)
        self.last_bar = None                     # BARS chord clock: bar of the last check
        self.radio = Radio(self.mixer)
        self.radio_query = ""                    # the RADIO tab's last search
        lib = native.load() if isinstance(self.mixer, native.NativeMixer) else None
        self.sequencer = (LoopSequencer(lambda seed: native.RandomSource(lib, seed)) if lib
                          else ChordSequencer(rng=self.rng))
        self.preset_dir = presets.PRESET_DIR
        self.loop_seed = None
        self.reseed_loop(new_seed())
        self._warm_jobs = []
        self._warm_wake = threading.Event()
        self._warm_thread = None
        self.apply()

    def v(self, key):
        return self.params[key].value

    def apply(self):
        w, r = self.world, self.rings
        w.sides = SHAPES[self.v("shape")]
        w.gravity = self.v("grav") / 100 * 4.0
        w.spin = self.v("rot") / 100 * 3.0
        w.bounce = self.v("bounce") / 100
        w.set_ball_count(self.v("balls"))
        r.count, r.moons, r.ratio = self.v("rings"), self.v("moons"), self.v("ratio")
        r.speed = self.v("speed") / 100 * 2.5
        r.tilt = math.radians(self.v("tilt"))
        self.mixer.wet = self.v("reverb") / 100
        self.mixer.volume = 1.0                  # balls and rings each have their own VOL
        c = self.mixer.cloud
        c.level = self.v("grain") / 100 if self.native or self.tab_on("GRAINS") else 0.0   # native fades it
        c.density = self.v("density") / 100
        c.size, c.position, c.pitch = self.v("size") / 1000, self.v("position") / 100, self.v("pitch")
        c.spread, c.texture, c.feedback = self.v("spread") / 100, self.v("texture") / 100, self.v("feedback") / 100
        c.wash = self.v("gwash") / 100              # 48K only: the grain engine's own reverb
        c.freeze = self.v("freeze") == "ON"
        self.mixer.grain_source = self.v("source")
        self.mixer.bed = self.v("bed") / 100 * 0.45 * self.ring_vol() if self.tab_on("RINGS") else 0.0
        tone = self.v("tone")
        self.mixer.ring_timbre = tone if tone in RING_TIMBRES else "WARM"   # engines: native only
        c.mode = self.v("gmode")
        if self.native:
            pc = lambda key: self.v(key) / 100
            self.mixer.synth.update(engine=ENGINES.index(self.v("sengine")), harmonics=pc("sharm"),
                                    timbre=pc("stimb"), morph=pc("smorph"), decay=pc("sdecay"),
                                    colour=pc("scolour"))
            self.mixer.ring_synth.update(engine=ENGINES.index(tone) if tone in ENGINES else -1,
                                         harmonics=pc("rharm"), timbre=pc("rtimb"), morph=pc("rmorph"))
            self.mixer.physical.update(exciter=EXCITERS.index(self.v("pexc")), geometry=pc("pgeo"),
                                       brightness=pc("pbright"), damping=pc("pdamp"), position=pc("ppos"),
                                       space=pc("pspace"))
            self.mixer.res.update(model=RESONATOR_MODELS.index(self.v("rmodel")), poly=self.v("rpoly"),
                                  structure=self.v("rstruct") / 100, brightness=self.v("rbright") / 100,
                                  damping=self.v("rdamp") / 100, position=self.v("rpos") / 100)
        if self.native:
            self.apply_native_extras()
        self.tilt_lanes = [(k, self.v(f"mdep{k + 1}") / 100) for k in range(MOD_LANES)
                           if self.v(f"mto{k + 1}") == "TILT"] if self.native and self.tab_on("MODS") else []
        self.sync_radio()
        q = self.sequencer
        q.deja, q.spread, q.bias = self.v("deja") / 100, self.v("cspread") / 100, self.v("cbias") / 100
        if q.length != self.v("length"):
            q.set_length(self.v("length"))

    def apply_native_extras(self):
        """DRUMS, WARP and MODS: native only (the Python engine has none of them)."""
        pc = lambda key: self.v(key) / 100
        m = self.mixer
        m.drums.update(play=int(self.tab_on("DRUMS")),
                       x=pc("dx"), y=pc("dy"), chaos=pc("dchaos"),
                       density=(pc("dkick"), pc("dsnare"), pc("dhats")), level=pc("dvol") * 1.2,
                       tone=pc("dtone"), decay=pc("ddecay"), note=float(DRUM_ROOT + KEYS.index(self.v("key"))))
        m.warp.update(carrier=WARP_CARRIERS.index(self.v("wcar")), algo=pc("walgo"), timbre=pc("wtimb"),
                      drive=0.6 + 0.4 * pc("wdrive"), mix=pc("wmix"))     # below 0.6 the vocoder goes silent
        mods_on = self.tab_on("MODS")
        m.mods.update(mode=MOD_MODES.index(self.v("mmode")), rate=float(self.v("mrate")),
                      shape=pc("mshape"), slope=pc("mslope"), smooth=pc("msmooth"), shift=pc("mshift"),
                      targets=tuple(MOD_TARGETS.index(self.v(f"mto{k}")) if mods_on else 0
                                    for k in range(1, MOD_LANES + 1)),
                      depths=tuple(pc(f"mdep{k}") for k in range(1, MOD_LANES + 1)))
        m.clock.update(bpm=float(self.v("bpm")), ball_ratio=self.ratio("BALLS"), drum_ratio=self.ratio("DRUMS"),
                       mod_ratio=self.ratio("MODS"), quant=QUANTS[self.v("quant")],
                       mod_cycle=(BEATS_PER_BAR * eval_bars(self.v("mbars")) if self.v("msync") == "ON" else 0.0))
        m.buses.update({bus: float(self.tab_on(page)) for bus, page in
                        (("balls", "BALLS"), ("rings", "RINGS"), ("grains", "GRAINS"), ("drums", "DRUMS"),
                         ("warp", "WARP"), ("radio", "RADIO"))})
        m.radio_level = pc("radvol")

    def sync_radio(self):
        """Start the radio when its tab is on (last search, or the track it was on); stop it when off."""
        radio = getattr(self, "radio", None)
        if radio is None:
            return
        if not self.tab_on("RADIO"):
            if radio.playing:
                radio.stop()
        elif radio.status == "IDLE" and not getattr(self, "_radio_manual", False):
            if radio.results:
                radio.play()
            elif self.radio_query:
                radio.search(self.radio_query, self.v("rsrc"))

    def search_radio(self, query):
        """A new RADIO search; switches the tab on."""
        self.radio_query = query.strip()[:120]
        self.radio.results = []
        self.radio.stop()
        self.radio_on()

    def radio_on(self):
        """Playing something on the radio switches its tab on."""
        if self.v("on_radio") != "ON":
            self.params["on_radio"].index = 1
            self._radio_manual = True            # the caller starts the track: don't autostart one too
            try:
                self.apply()
            finally:
                self._radio_manual = False

    def tab_on(self, page):
        """Is this tab sounding: its own switch (o), and the master on/off (space)."""
        return self.on and self.v(f"on_{page.lower()}") == "ON"

    def ratio(self, page):
        """The tab's clock as a multiple of the master (CLK)."""
        return RATIOS[self.v(f"clk_{page.lower()}")]

    def tab_beats(self, page):
        return self.clock.beats * self.ratio(page)

    def ball_vol(self):
        return self.v("vol") / 100 * 1.2

    def ring_vol(self):
        return self.v("rvol") / 100 * 1.2

    def set_on(self, on):
        self.on = on
        self.apply()

    def chord(self, step):
        """[bass, root, third, fifth] of the current chord.

        CHORD is a held scale degree (I..VII), a progression (step counts its
        moves) or RANDOM, the random loop's current chord; the CHORDS page sets
        when moving chords move.

        0 on the rings' OCTAVE is the lowest voicing whose bass is at least
        PAD_LOWEST (clearly audible on any speaker); each step moves the whole
        chord, pad and moons alike, an octave — negative steps go into sub-bass.
        Independent of the balls.
        """
        degree = self.chord_degree(step)
        k, sc = self.v("key"), self.v("scale")
        notes = [pad_note(k, sc, degree, -1)] + [pad_note(k, sc, degree + i, 0) for i in (0, 2, 4)]
        lift = 12 * max(0, -(-(PAD_LOWEST - notes[0]) // 12))
        return [m + lift + 12 * self.v("roct") for m in notes]

    def chord_degree(self, step):
        """The current chord's scale degree (0 = I)."""
        name = self.v("chord")
        if name in DEGREES:
            return DEGREES.index(name)
        if name == RANDOM:
            return self.sequencer.current
        prog = PROGRESSIONS[name]
        return prog[step % len(prog)]

    def hit_midi(self, edge, ball_octave):
        """A ball's note: the wall's pad, or with ARP the chord's next note (every instrument)."""
        k, sc, octave = self.v("key"), self.v("scale"), self.v("octave")
        if self.v("arp") == "OFF":
            return pad_note(k, sc, edge, octave, ball_octave)
        self.arp.mode = self.v("arp")
        self.arp.clock(ARP_NOTES)
        degree = self.chord_degree(self.chord_step) + 2 * self.arp.note
        return pad_note(k, sc, degree, octave + self.arp.octave, ball_octave)

    def ring_notes(self):
        """Each ring's note, innermost first: RINGS picks how many chord tones sound
        (root, +fifth, +third, +bass) and the outer rings take the lowest."""
        chord = self.chord(self.chord_step)
        notes = sorted(chord[i] for i in RING_ORDER[:self.rings.count])
        return list(reversed(notes))

    def ring_targets(self):
        """(midi, gain) per ring: full with its moon at the ring's highest point, FLOOR at its lowest."""
        floor = self.v("floor") / 100
        return [(m, floor + (1 - floor) * self.rings.level(k)) for k, m in enumerate(self.ring_notes())]

    def randomize(self, page="BALLS", rng=random):
        """New random settings for one page only (within RANDOM_RANGES); u undoes it.

        Settings shared by several pages (key, scale, reverb, ...) are left alone.
        On BALLS the balls are dropped fresh too.
        """
        self.history = (self.history + [snapshot(self.params.values())])[-HISTORY_MAX:]
        for key in random_keys(page):
            p, bounds = self.params[key], RANDOM_RANGES[key]
            ok = [i for i, v in enumerate(p.values) if bounds is None or bounds[0] <= v <= bounds[1]]
            p.index = rng.choice(ok)
        self.apply()
        if page == "BALLS":
            self.world.respawn()
        self.prewarm()

    def undo(self):
        """Back to the settings before the last r (or preset load). Returns False when there is
        nothing to undo. The tabs' on/off switches stay as they are."""
        if not self.history:
            return False
        snap = self.history.pop()
        apply_snapshot(self.params.values(), {k: v for k, v in snap.items() if not k.startswith("on_")})
        if isinstance(snap.get("loop_seed"), int) and snap["loop_seed"] != self.loop_seed:
            self.reseed_loop(snap["loop_seed"])
        self.apply()
        self.prewarm()
        return True

    def note_args(self, midi):
        instr = PY_STANDIN.get(self.v("instr"), self.v("instr"))     # native-only voices on Python
        return midi, instr, self.v("attack"), self.v("release")

    def resonator_args(self, midi, strength):
        """Arguments for render_resonator: the hit's note, the resonator settings, the ring chord."""
        model = PY_MODEL.get(self.v("rmodel"), "MODAL")       # the Python fallback has three
        chord = tuple(self.chord(self.chord_step)) if model == "SYMPATH" else ()
        return (midi, model, self.v("rstruct") / 100, self.v("rbright") / 100, self.v("rdamp") / 100,
                self.v("rpos") / 100, strength_step(strength), chord)

    @property
    def pool(self):
        if self._pool is None:
            self._pool = RenderPool()
        return self._pool

    def close(self):
        if self._pool:
            self._pool.close()
        self.radio.close()

    @property
    def native(self):
        return isinstance(self.mixer, native.NativeMixer)

    def ball_sound(self, midi, strength=0.75):
        """The hit's buffer; with RESONATOR possibly None for a moment after a setting change."""
        if self.v("instr") == RESONATOR:
            return self.pool.get(self.resonator_args(midi, strength))
        return self.mixer.note(*self.note_args(midi))

    def playable_notes(self):
        """Every note a hit can play now: the walls' pads, or with ARP every chord's 1-3-5-7."""
        k, sc, octave = self.v("key"), self.v("scale"), self.v("octave")
        if self.v("arp") == "OFF":
            spots = [(e, 0) for e in range(self.world.sides)]
        else:
            spots = [(d, a) for d in range(len(DEGREES) + 2 * (ARP_NOTES - 1)) for a in range(ARP_RANGE)]
        return sorted({pad_note(k, sc, d, octave + a, o) for d, a in spots for _, o in BALL_SIZES})

    def prewarm(self):
        """Render every note the current setup can play, off the UI thread.

        One worker; a newer request supersedes the one in progress, so holding
        a key down costs one render pass, not a thread per key repeat.
        """
        jobs = [(m, s) for m in self.playable_notes()
                for s in (STRENGTHS if self.v("instr") == RESONATOR else (0.75,))]
        if self.v("instr") == RESONATOR:
            if self.native:                          # the native core resonates in real time
                return
            # the Python fallback renders resonator hits in other processes
            for m, s in jobs:
                self.pool.request(self.resonator_args(m, s))
            return
        self._warm_jobs = jobs
        self._warm_wake.set()
        if self._warm_thread is None:
            self._warm_thread = threading.Thread(target=self._warm_loop, daemon=True)
            self._warm_thread.start()

    def _warm_loop(self):
        while True:
            self._warm_wake.wait()
            self._warm_wake.clear()
            for job in self._warm_jobs:
                if self._warm_wake.is_set():
                    break
                self.ball_sound(*job)

    def step(self, dt):
        dbeats = self.clock.advance(dt, self.v("bpm")) if self.on else 0.0
        balls_on = self.tab_on("BALLS")
        for hit in self.world.step(dt):
            if not balls_on:                     # off: the balls still bounce, silently
                continue
            strength = min(1.0, 0.08 + hit.speed / 2.0)               # hard hits louder (and brighter)
            midi = self.hit_midi(hit.edge, hit.octave)
            if self.native and self.v("instr") in (RESONATOR, SYNTH, PHYSICAL):
                play = {RESONATOR: self.mixer.strike, SYNTH: self.mixer.synth_hit,
                        PHYSICAL: self.mixer.physical_hit}[self.v("instr")]
                play(midi, strength * self.ball_vol())
                self.hits += 1
                continue
            buf = self.ball_sound(midi, strength)
            if buf is None:                          # first-ever render of this note still running
                continue
            poly = self.v("rpoly") if self.v("instr") == RESONATOR else None
            self.mixer.trigger(buf, 0.4 * strength * self.ball_vol(), group="balls", poly=poly)
            self.hits += 1
        if self.tilt_lanes:                      # MODS lanes on TILT swing the rings
            lanes = self.mixer.mod_values()
            swing = sum(depth * lanes[k] for k, depth in self.tilt_lanes)
            self.rings.tilt = math.radians(max(0.0, self.v("tilt") + TILT_SWING * swing))
        if self.native:
            self.mixer.warp["note"] = float(self.chord(self.chord_step)[1])   # the oscillator plays the root
        before = self.outer_moons()
        if self.v("rsync") == "ON":
            r = self.ratio("RINGS")
            self.rings.step_synced(dt, dbeats * r, self.clock.beats * r, BEATS_PER_BAR * self.v("rbars"))
        else:
            self.rings.step(dt)
        if (self.clock_tick(dt, before) and not self.hold and self.tab_on("CHORDS")
                and self.rng.random() < self.v("chance") / 100):
            self.advance_chord()
        rings_on = self.tab_on("RINGS")
        self.mixer.set_rings([(m, g if rings_on else 0.0) for m, g in self.ring_targets()])

    def moving(self):
        return self.v("chord") in PROGRESSIONS or self.v("chord") == RANDOM

    def outer_moons(self):
        k = self.rings.count - 1
        return self.rings.moon_angles(k) if k >= 0 else []

    def clock_tick(self, dt, before):
        """Is it time to (maybe) change chord? MOON: the outer ring's moon passed its
        highest point. TIME: CHANGE seconds have passed, varied by JITTER."""
        if not self.moving():
            return False
        if self.v("clock") == "BARS":
            bar = int(self.tab_beats("CHORDS") // (BEATS_PER_BAR * self.v("cbars")))
            ticked = self.last_bar is not None and bar != self.last_bar
            self.last_bar = bar
            return ticked
        if self.v("clock") == "MOON" and before:
            top = self.rings.top_angle()
            after = self.outer_moons()
            return any((b - top) // TAU != (a - top) // TAU for b, a in zip(before, after))
        if self.next_change is None:
            self.next_change = self._time_interval()
        self.chord_timer += dt
        if self.chord_timer < self.next_change:
            return False
        self.chord_timer -= self.next_change          # keep the overshoot: no drift over time
        self.next_change = self._time_interval()
        return True

    def _time_interval(self):
        return self.v("change") * (1 + 0.75 * self.v("jitter") / 100 * self.rng.uniform(-1, 1))

    def advance_chord(self):
        if self.v("chord") == RANDOM:
            self.sequencer.next()
        else:
            self.chord_step += 1

    def regenerate(self):
        """g: a brand-new random chord loop (with its own seed, so a preset can bring it back)."""
        self.reseed_loop(new_seed())

    def reseed_loop(self, seed):
        """The RANDOM chord loop drawn from seed, and the coin tosses (CHANCE, JITTER, ARP
        RANDOM) restarted from it: the same seed always plays the same loop."""
        self.loop_seed = seed
        self.rng.seed(seed)
        self.sequencer.reseed(seed)
        self.sequencer.current = self.sequencer.loop[0]
        self.chord_step = 0

    def save_preset(self, slot):
        presets.save(slot, snapshot(self.params.values()), self.loop_seed, self.radio_query,
                     self.preset_dir)

    def load_preset(self, slot):
        """Settings and chord loop from a slot; u undoes it. Returns False when the slot is empty."""
        saved = presets.load(slot, self.preset_dir)
        if saved is None:
            return False
        settings, seed, query = saved
        self.history = (self.history + [{**snapshot(self.params.values()),
                                         "loop_seed": self.loop_seed}])[-HISTORY_MAX:]
        apply_snapshot(self.params.values(), settings)
        if seed:
            self.reseed_loop(seed)
        if query and query != self.radio_query:     # a different station: search it when RADIO is on
            self.radio_query = query
            self.radio.results = []
            self.radio.stop()
        self.apply()
        self.prewarm()
        return True


def new_seed():
    return random.randint(1, presets.MAX_SEED)


# Direct hotkeys: (param, step) cycles a param, (param, "=", index) jumps to a value.
KEYMAP = {b"\x1b[C": "right", b"\x1b[D": "left", b"\x1b[A": "up", b"\x1b[B": "down",
          b"l": "right", b"h": "left", b"k": "up", b"j": "down", b"\t": "page",
          b" ": "toggle", b"r": "respawn", b"u": "undo", b"q": "quit", b"Q": "quit",
          b"s": ("shape", 1), b"S": ("shape", -1), b"n": ("key", 1), b"N": ("key", -1),
          b"c": ("scale", 1), b"C": ("scale", -1), b"i": ("instr", 1), b"I": ("instr", -1),
          b"v": ("visual", 1), b"w": ("window", 1), b"f": ("freeze", 1),
          b"g": "regenerate", b"x": "hold", b"?": "help", b"o": "tab",
          b"P": "preset_save", b"L": "preset_load",
          **{str(n).encode(): ("digit", n) for n in range(1, 10)}}
# Digit keys mean different things per page: shape (sides 3-8) or chord degree (I-VII).
DIGITS = {"BALLS": ("shape", lambda n: n - 3 if 3 <= n <= 8 else None),
          "RINGS": ("chord", lambda n: n - 1 if 1 <= n <= 7 else None),
          "CHORDS": ("chord", lambda n: n - 1 if 1 <= n <= 7 else None),
          "WARP": ("wcar", lambda n: n - 1 if 1 <= n <= len(WARP_CARRIERS) else None)}
# ... or jump the cursor: 1-4 on MODS selects that mover's TO setting.
FOCUS_DIGITS = {"MODS": lambda n: f"mto{n}" if 1 <= n <= MOD_LANES else None}


def parse_keys(data):
    keys, i = [], 0
    while i < len(data):
        for seq, name in KEYMAP.items():
            if data.startswith(seq, i):
                keys.append(name)
                i += len(seq)
                break
        else:
            i += 1
    return keys


# Hotkeys that mean "this page's version" of a setting: i on RINGS changes the moons.
PAGE_ALIASES = {"RINGS": {"instr": "tone"}}


class Menu:
    """Which page is shown and which setting is selected on each page."""

    def __init__(self, params):
        self.params = params
        self.pages = list(PAGES)
        self.page = self.pages[0]
        self.cursor = {p: len(PAGES[p][0]) for p in self.pages}   # start on the first knob
        self.help = False                        # ?: explain the selected setting
        self.radio_list = None                   # RADIO track list when open: {"sel", "sbuf"}
        self.pending = None                      # "save" / "load": waiting for a slot digit (P / L)
        self.toast = None                        # (text, until): a short note on the bottom line

    def say(self, text, seconds=TOAST_S):
        self.toast = (text, time.monotonic() + seconds)

    def items(self):
        side, knobs = page_params(self.params, self.page)
        return side + knobs

    def selected(self):
        items = self.items()                     # the BALLS page changes size with INSTR
        self.cursor[self.page] %= len(items)
        return items[self.cursor[self.page]]

    def move(self, d):
        self.cursor[self.page] = (self.cursor[self.page] + d) % len(self.items())

    def next_page(self):
        self.page = self.pages[(self.pages.index(self.page) + 1) % len(self.pages)]

    def focus(self, param):
        """Select param if the current page shows it."""
        items = self.items()
        if param in items:
            self.cursor[self.page] = items.index(param)


def handle_key(k, engine, menu):
    """Apply one parsed key. Returns False to quit."""
    changed = None
    if k == "quit":
        return False
    if menu.pending:
        preset_key(k, engine, menu)
        return True
    if isinstance(k, tuple) and k[0] == "digit" and menu.page in FOCUS_DIGITS:
        key = FOCUS_DIGITS[menu.page](k[1])
        if key:
            menu.focus(engine.params[key])
        return True
    if isinstance(k, tuple) and k[0] == "digit":
        if menu.page not in DIGITS:
            return True
        key, index = DIGITS[menu.page]
        if index(k[1]) is not None:
            k = (key, "=", index(k[1]))
        else:
            return True
    if isinstance(k, tuple):                      # hotkey: change that setting directly
        changed = engine.params[PAGE_ALIASES.get(menu.page, {}).get(k[0], k[0])]
        if k[1] == "=":
            changed.index = k[2]
        else:
            changed.nudge(k[1])
        menu.focus(changed)
    elif k in ("left", "right"):
        menu.move(1 if k == "right" else -1)
    elif k in ("up", "down"):
        changed = menu.selected()
        changed.nudge(1 if k == "up" else -1)
    elif k == "page":
        menu.next_page()
    elif k == "toggle":
        engine.set_on(not engine.on)
    elif k == "respawn":
        engine.randomize(menu.page)
    elif k == "undo":
        engine.undo()
    elif k == "regenerate":
        engine.regenerate()
    elif k == "hold":
        engine.hold = not engine.hold
    elif k == "help":
        menu.help = not menu.help
    elif k in ("preset_save", "preset_load"):
        menu.pending = k[7:]
        menu.toast = None
    elif k == "tab":                              # this tab on / off
        changed = engine.params[f"on_{menu.page.lower()}"]
        changed.nudge(1)
    if changed:
        engine.apply()
        if changed.key in PREWARM_KEYS:
            engine.prewarm()
    return True


def preset_key(k, engine, menu):
    """The key after P or L: a digit picks the slot, anything else cancels."""
    action, menu.pending = menu.pending, None
    if not (isinstance(k, tuple) and k[0] == "digit"):
        menu.say("CANCELLED")
        return
    slot = k[1]
    if action == "save":
        engine.save_preset(slot)
        menu.say(f"SAVED PRESET {slot}")
    elif engine.load_preset(slot):
        menu.say(f"LOADED PRESET {slot}  (u undoes)")
    else:
        menu.say(f"PRESET {slot} IS EMPTY")


# RADIO page: play's player keys, before tombola's own (tab, o, ?, q, v, w still work).
RADIO_KEYS = {b"\x1b[C": ("seek", 10), b"\x1b[D": ("seek", -10), b"\x1b[A": ("vol", 1), b"\x1b[B": ("vol", -1),
              b" ": ("pause",), b"n": ("skip", 1), b"p": ("skip", -1), b"l": ("list",), b"\r": ("list",),
              b"/": ("list",), b"s": ("source",)}


def radio_action(act, engine, menu):
    radio = engine.radio
    name = act[0]
    if name == "seek":
        radio.seek(act[1])
    elif name == "vol":
        engine.params["radvol"].nudge(act[1])
        engine.apply()
    elif name == "pause":
        if radio.status in ("PLAYING", "PAUSED"):
            radio.pause()
        elif radio.results:
            engine.radio_on()
            radio.play()
    elif name == "skip" and radio.results:
        engine.radio_on()
        radio.skip(act[1])
    elif name == "list":
        menu.radio_list = {"sel": radio.index if radio.results else 0, "sbuf": ""}
    elif name == "source":                        # like play's settings: a new source searches again
        engine.params["rsrc"].nudge(1)
        engine.apply()
        if engine.radio_query:
            engine.search_radio(engine.radio_query)


def radio_list_key(data, i, engine, menu):
    """One key in the open track list (play's l): arrows move, typing searches,
    Enter plays (or searches), Esc closes. Returns the index after the key."""
    lst, results = menu.radio_list, engine.radio.results
    if data.startswith(b"\x1b[A", i) or data.startswith(b"\x1b[B", i):
        step = -1 if data[i + 2:i + 3] == b"A" else 1
        lst["sel"] = max(0, min(len(results) - 1, lst["sel"] + step)) if results else 0
        lst["sbuf"] = ""
        return i + 3
    if data.startswith(b"\x1b[", i):                # other arrows / keys: ignored here
        return i + 3
    byte = data[i:i + 1]
    if byte == b"\x1b":
        menu.radio_list = None
        return i + 1
    if byte in (b"\r", b"\n"):
        if lst["sbuf"].strip():
            engine.search_radio(lst["sbuf"])
            lst.update(sel=0, sbuf="")
        elif results:
            engine.radio_on()
            engine.radio.play(lst["sel"])
            menu.radio_list = None
        return i + 1
    if byte in (b"\x7f", b"\x08"):
        lst["sbuf"] = lst["sbuf"][:-1]
        return i + 1
    size = 1 if data[i] < 0xC0 else 2 if data[i] < 0xE0 else 3 if data[i] < 0xF0 else 4
    ch = data[i:i + size].decode("utf-8", "ignore")
    if ch.isprintable() and len(lst["sbuf"]) < 120:
        lst["sbuf"] += ch
    return i + size


def handle_radio_input(data, engine, menu):
    """Keys on the RADIO page. Returns False to quit."""
    i = 0
    while i < len(data):
        if menu.page != "RADIO":                   # tab moved on: the rest is ordinary keys
            return all(handle_key(k, engine, menu) for k in parse_keys(data[i:]))
        if menu.radio_list is not None:
            i = radio_list_key(data, i, engine, menu)
            continue
        seq = None if menu.pending else next((s for s in RADIO_KEYS if data.startswith(s, i)), None)
        if seq:
            radio_action(RADIO_KEYS[seq], engine, menu)
            i += len(seq)
            continue
        seq = next((s for s in KEYMAP if data.startswith(s, i)), None)
        if seq is None:
            i += 1
            continue
        if not handle_key(KEYMAP[seq], engine, menu):
            return False
        i += len(seq)
    return True


def run(output_factory, config=CONFIG):
    sys.setswitchinterval(0.002)                 # hand the interpreter to the audio thread sooner
    params = load_params(config)
    engine = Engine(params)
    engine.history = load_history(config)
    engine.radio_query = load_radio(config)
    seed = load_seed(config)
    if seed:
        engine.reseed_loop(seed)                 # the same chord loop as last time
    engine.apply()
    engine.prewarm()
    output = output_factory(engine.mixer)
    output.start()
    menu = Menu(params)
    fx = ui.Effects()
    window = Backdrop()

    fd = sys.stdin.fileno()
    saved_tty = termios.tcgetattr(fd)

    def bail(*_):
        raise SystemExit(0)
    for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, bail)

    out = sys.stdout
    try:
        tty.setcbreak(fd)
        out.write("\x1b[?1049h\x1b[?25l\x1b[2J")
        last = time.monotonic()
        acc = 0.0
        while True:
            now = time.monotonic()
            acc = min(acc + now - last, 0.25)
            last = now
            if select.select([fd], [], [], 0)[0]:
                data = os.read(fd, 64)
                if menu.page == "RADIO":
                    if not handle_radio_input(data, engine, menu):
                        return
                elif not all(handle_key(k, engine, menu) for k in parse_keys(data)):
                    return
                save_params(params, config, engine.history, engine.radio_query, engine.loop_seed)   # every change sticks
            while acc >= PHYS_DT:
                if engine.on:
                    engine.step(PHYS_DT)
                acc -= PHYS_DT
            cols, rows = shutil.get_terminal_size()
            fx.update(engine.mixer, now)
            sync_window(window, engine, fx, now)
            out.write(ui.render(ui_state(engine, menu, output, fx, window), cols, rows))
            out.flush()
            time.sleep(max(0.0, FRAME_DT - (time.monotonic() - now)))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved_tty)
        out.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        out.flush()
        window.stop()
        output.stop()
        engine.close()
        save_params(params, config, engine.history, engine.radio_query, engine.loop_seed)


def sync_window(window, engine, fx, now=None):
    """Keep the Windows backdrop in step with the WINDOW setting and feed it frames (at FPS)."""
    if engine.v("window") != "ON":
        window.stop()
        return
    window.start()
    if window.running and (now is None or now - window.last_frame >= 1 / BACKDROP_FPS):
        window.last_frame = now or 0.0
        window.submit(scene_commands(engine.world, engine.rings, fx, engine.v("bed") / 100, ui.FLASH_S))


def chord_row(engine):
    """(title, [(label, current, fresh)]) for the CHORDS page: the loop, progression or held chord."""
    name = engine.v("chord")
    if name == RANDOM:
        q = engine.sequencer
        playing = (q.pos - 1) % q.length
        return "LOOP", [(DEGREES[d], i == playing, q.is_fresh(i)) for i, d in enumerate(q.loop)]
    if name in PROGRESSIONS:
        prog = PROGRESSIONS[name]
        return "PROG", [(DEGREES[d], i == engine.chord_step % len(prog), False) for i, d in enumerate(prog)]
    return "CHORD", [(name, True, False)]


def drum_glyph(hits):
    """A 16th on the DRUMS bar: K kick, S snare, X both, H hats only, · nothing."""
    if 0 in hits and 1 in hits:
        return "X"
    return "KSH"[hits[0]] if hits else "·"


def drum_row(engine):
    """(title, cells, tight) for DRUMS: the bar as 16 sixteenths, playhead lit, accents marked."""
    if not engine.native:
        return "DRUMS 48K ONLY", [], True
    levels = engine.mixer.drum_levels(engine.v("dx") / 100, engine.v("dy") / 100)
    density = [engine.v(k) / 100 for k in ("dkick", "dsnare", "dhats")]
    playing = engine.mixer.drum_step() // 2 if engine.tab_on("DRUMS") else -1
    cells = []
    for s in range(0, 32, 2):
        hits = [p for p in range(3) if density[p] > 0 and levels[p][s] > 255 - 255 * density[p]]
        accent = any(levels[p][s] > 192 for p in hits)
        cells.append((drum_glyph(hits), s // 2 == playing, accent))
    return ("PLAY" if engine.tab_on("DRUMS") else "STOP"), cells, True


def warp_row(engine):
    """(title, cells) for WARP: the algorithm ALGO sits on."""
    a = engine.v("walgo") / 100
    at = len(WARP_ALGOS) - 1 if a >= 0.725 else min(len(WARP_ALGOS) - 2, round(a * 8))
    title = "WARP" if engine.v("wmix") else "OFF"
    return title, [(name, i == at, False) for i, name in enumerate(WARP_ALGOS)]


BARS = " ▁▂▃▄▅▆▇█"


def mods_row(engine):
    """(title, cells) for MODS: each lane's live value as a bar, lit when it has a target."""
    values = engine.mixer.mod_values() if engine.native else [0.0] * MOD_LANES
    cells = []
    for k, v in enumerate(values):
        bar = BARS[min(len(BARS) - 1, int((v + 1) / 2 * len(BARS)))]
        cells.append((f"{k + 1}{bar}", engine.v(f"mto{k + 1}") != "OFF", False))
    return "MODS" if engine.native else "MODS 48K ONLY", cells


def radio_view(engine, menu):
    """What the RADIO page's player panel shows."""
    radio = engine.radio
    lst = menu.radio_list
    return {"status": radio.missing(engine.v("rsrc")) or radio.status, "title": radio.title,
            "index": radio.index, "count": len(radio.results), "elapsed": radio.elapsed(),
            "duration": radio.duration(), "volume": engine.v("radvol"), "source": engine.v("rsrc"),
            "on": engine.tab_on("RADIO"), "query": engine.radio_query,
            "tracks": [(title, dur) for _, title, dur in radio.results],
            "sel": lst["sel"] if lst else None, "sbuf": lst["sbuf"] if lst else None}


BANNERS = {"CHORDS": chord_row, "DRUMS": drum_row, "WARP": warp_row, "MODS": mods_row}


def ui_state(engine, menu, output=None, fx=None, window=None):
    side, knobs = page_params(menu.params, menu.page)
    return {"side": side, "knobs": knobs, "selected": menu.selected(), "pages": menu.pages,
            "page": menu.page, "world": engine.world, "rings": engine.rings,
            "bed_level": engine.v("bed") / 100, "hits": engine.hits,
            "dsp": output.dsp if output else 0.0, "on": engine.on,
            "engine": engine.mixer.name, "error": " ".join(e for e in (output.error if output else "",
                                          window.error if window else "") if e),
            "fx": fx, "window": engine.v("window") == "ON",
            "banner": BANNERS[menu.page](engine) if menu.page in BANNERS else None,
            "radio": radio_view(engine, menu) if menu.page == "RADIO" else None,
            "hold": engine.hold and menu.page == "CHORDS",
            "help": explain(menu.selected()) if menu.help else None,
            "off_pages": {p for p in menu.pages if not engine.tab_on(p)},
            "prompt": preset_prompt(engine, menu)}


def preset_prompt(engine, menu):
    """Bottom-line text while P / L waits for a slot, or a fresh toast; None otherwise."""
    if menu.pending == "save":
        return "SAVE PRESET TO 1-9 (other key cancels)"
    if menu.pending == "load":
        slots = presets.used(engine.preset_dir)
        return f"LOAD PRESET: {' '.join(map(str, slots))}" if slots else "NO PRESETS YET (P saves one)"
    if menu.toast and time.monotonic() < menu.toast[1]:
        return menu.toast[0]
    return None
