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
from music import KEYS, SHAPES, pad_note
from marbles import ChordSequencer, LoopSequencer
import native
from params import (CONFIG, DEGREES, HISTORY_MAX, PAGES, PROGRESSIONS, RANDOM, RANDOM_RANGES, SHARED, random_keys,  # noqa: F401
                    MOD_TARGETS, PHYSICAL, RESONATOR, RESONATOR_MODELS, SYNTH, WARP_ALGOS,
                    apply_snapshot, load_history, load_params, make_params, page_params, save_params,
                    snapshot)
from physics import BALL_SIZES, World
from backdrop import FPS as BACKDROP_FPS, Backdrop, scene_commands
from resonator import STRENGTHS, RenderPool, strength_step
from rings import TAU, Rings
from native import ENGINES, EXCITERS, MOD_MODES, WARP_CARRIERS
from synth import RING_TIMBRES, Mixer
import ui

PHYS_DT = 1 / 120
PY_STANDIN = {SYNTH: "BELL", PHYSICAL: "MARIMBA"}             # Python engine stand-ins
PY_MODEL = {"MODAL": "MODAL", "SYMPATH": "SYMPATH", "STRING": "STRING",       # Python fallback stand-ins
            "FM": "MODAL", "SYMP-Q": "SYMPATH", "STR+REV": "STRING"}


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
        lib = native.load() if isinstance(self.mixer, native.NativeMixer) else None
        self.sequencer = (LoopSequencer(lambda seed: native.RandomSource(lib, seed)) if lib
                          else ChordSequencer(rng=self.rng))
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
        c.level, c.density = self.v("grain") / 100, self.v("density") / 100
        c.size, c.position, c.pitch = self.v("size") / 1000, self.v("position") / 100, self.v("pitch")
        c.spread, c.texture, c.feedback = self.v("spread") / 100, self.v("texture") / 100, self.v("feedback") / 100
        c.freeze = self.v("freeze") == "ON"
        self.mixer.grain_source = self.v("source")
        self.mixer.bed = self.v("bed") / 100 * 0.45 * self.ring_vol() if self.on else 0.0
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
                           if self.v(f"mto{k + 1}") == "TILT"] if self.native else []
        q = self.sequencer
        q.deja, q.spread, q.bias = self.v("deja") / 100, self.v("cspread") / 100, self.v("cbias") / 100
        if q.length != self.v("length"):
            q.set_length(self.v("length"))

    def apply_native_extras(self):
        """DRUMS, WARP and MODS: native only (the Python engine has none of them)."""
        pc = lambda key: self.v(key) / 100
        m = self.mixer
        m.drums.update(play=int(self.on and self.v("dplay") == "ON"), bpm=float(self.v("dbpm")),
                       x=pc("dx"), y=pc("dy"), chaos=pc("dchaos"),
                       density=(pc("dkick"), pc("dsnare"), pc("dhats")), level=pc("dvol") * 1.2,
                       tone=pc("dtone"), decay=pc("ddecay"), note=float(DRUM_ROOT + KEYS.index(self.v("key"))))
        m.warp.update(carrier=WARP_CARRIERS.index(self.v("wcar")), algo=pc("walgo"), timbre=pc("wtimb"),
                      drive=0.6 + 0.4 * pc("wdrive"), mix=pc("wmix"))     # below 0.6 the vocoder goes silent
        m.mods.update(mode=MOD_MODES.index(self.v("mmode")), rate=float(self.v("mrate")),
                      shape=pc("mshape"), slope=pc("mslope"), smooth=pc("msmooth"), shift=pc("mshift"),
                      targets=tuple(MOD_TARGETS.index(self.v(f"mto{k}")) for k in range(1, MOD_LANES + 1)),
                      depths=tuple(pc(f"mdep{k}") for k in range(1, MOD_LANES + 1)))

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
        """Back to the settings before the last r. Returns False when there is nothing to undo."""
        if not self.history:
            return False
        apply_snapshot(self.params.values(), self.history.pop())
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
        for hit in self.world.step(dt):
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
        self.rings.step(dt)
        if self.clock_tick(dt, before) and not self.hold and self.rng.random() < self.v("chance") / 100:
            self.advance_chord()
        self.mixer.set_rings(self.ring_targets())

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
        """g: a brand-new random chord loop."""
        self.sequencer.regenerate()
        self.sequencer.current = self.sequencer.loop[0]


# Direct hotkeys: (param, step) cycles a param, (param, "=", index) jumps to a value.
KEYMAP = {b"\x1b[C": "right", b"\x1b[D": "left", b"\x1b[A": "up", b"\x1b[B": "down",
          b"l": "right", b"h": "left", b"k": "up", b"j": "down", b"\t": "page",
          b" ": "toggle", b"r": "respawn", b"u": "undo", b"q": "quit", b"Q": "quit",
          b"s": ("shape", 1), b"S": ("shape", -1), b"n": ("key", 1), b"N": ("key", -1),
          b"c": ("scale", 1), b"C": ("scale", -1), b"i": ("instr", 1), b"I": ("instr", -1),
          b"v": ("visual", 1), b"w": ("window", 1), b"f": ("freeze", 1),
          b"g": "regenerate", b"x": "hold",
          **{str(n).encode(): ("digit", n) for n in range(1, 10)}}
# Digit keys mean different things per page: shape (sides 3-8) or chord degree (I-VII).
DIGITS = {"BALLS": ("shape", lambda n: n - 3 if 3 <= n <= 8 else None),
          "RINGS": ("chord", lambda n: n - 1 if 1 <= n <= 7 else None),
          "CHORDS": ("chord", lambda n: n - 1 if 1 <= n <= 7 else None)}


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
    if changed:
        engine.apply()
        if changed.key in PREWARM_KEYS:
            engine.prewarm()
    return True


def run(output_factory, config=CONFIG):
    sys.setswitchinterval(0.002)                 # hand the interpreter to the audio thread sooner
    params = load_params(config)
    engine = Engine(params)
    engine.history = load_history(config)
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
                if not all(handle_key(k, engine, menu) for k in parse_keys(os.read(fd, 64))):
                    return
                save_params(params, config, engine.history)   # every change sticks, even if killed
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
        save_params(params, config, engine.history)


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
    playing = engine.mixer.drum_step() // 2 if engine.v("dplay") == "ON" else -1
    cells = []
    for s in range(0, 32, 2):
        hits = [p for p in range(3) if density[p] > 0 and levels[p][s] > 255 - 255 * density[p]]
        accent = any(levels[p][s] > 192 for p in hits)
        cells.append((drum_glyph(hits), s // 2 == playing, accent))
    return ("PLAY" if engine.v("dplay") == "ON" else "STOP"), cells, True


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
            "hold": engine.hold and menu.page == "CHORDS"}
