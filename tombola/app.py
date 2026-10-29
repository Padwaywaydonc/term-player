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

from music import SHAPES, pad_note
from params import (CONFIG, DEGREES, HISTORY_MAX, PAGES, PROGRESSIONS, RANDOM_RANGES,  # noqa: F401
                    apply_snapshot, load_history, load_params, make_params, page_params, save_params,
                    snapshot)
from physics import BALL_SIZES, World
from backdrop import Backdrop, scene_commands
from rings import Rings
from synth import Mixer
import ui

PHYS_DT = 1 / 120
FRAME_DT = 1 / 30
PAD_LOWEST = 40                  # E2 (~82 Hz): the pad's bass never goes below this
PREWARM_KEYS = {"key", "scale", "instr", "attack", "release", "octave", "shape"}
RING_ORDER = (1, 3, 2, 0)        # chord tones added as RINGS grows: root, +fifth, +third, +bass


class Engine:
    """Everything except the terminal: usable headless from tests."""

    def __init__(self, params, mixer=None):
        self.params = {p.key: p for p in params}
        self.world = World()
        self.rings = Rings()
        self.mixer = mixer or Mixer()
        self.hits = 0
        self.on = True
        self.history = []                        # settings before each r, newest last
        self.chord_step = 0
        self.chord_timer = 0.0
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
        self.mixer.ring_timbre = self.v("tone")

    def ball_vol(self):
        return self.v("vol") / 100 * 1.2

    def ring_vol(self):
        return self.v("rvol") / 100 * 1.2

    def set_on(self, on):
        self.on = on
        self.apply()

    def chord(self, step):
        """[bass, root, third, fifth] of the current chord.

        CHORD is either a held scale degree (I..VII) or a progression, which
        moves every CHANGE seconds (step counts those moves).

        0 on the rings' OCTAVE is the lowest voicing whose bass is at least
        PAD_LOWEST (clearly audible on any speaker); each step moves the whole
        chord, pad and moons alike, an octave — negative steps go into sub-bass.
        Independent of the balls.
        """
        name = self.v("chord")
        if name in DEGREES:
            degree = DEGREES.index(name)
        else:
            prog = PROGRESSIONS[name]
            degree = prog[step % len(prog)]
        k, sc = self.v("key"), self.v("scale")
        notes = [pad_note(k, sc, degree, -1)] + [pad_note(k, sc, degree + i, 0) for i in (0, 2, 4)]
        lift = 12 * max(0, -(-(PAD_LOWEST - notes[0]) // 12))
        return [m + lift + 12 * self.v("roct") for m in notes]

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

    def randomize(self, rng=random):
        """New random settings (within RANDOM_RANGES) and freshly dropped balls; u undoes it."""
        self.history = (self.history + [snapshot(self.params.values())])[-HISTORY_MAX:]
        for key, bounds in RANDOM_RANGES.items():
            p = self.params[key]
            ok = [i for i, v in enumerate(p.values) if bounds is None or bounds[0] <= v <= bounds[1]]
            p.index = rng.choice(ok)
        self.apply()
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

    def note_args(self, edge, ball_octave):
        midi = pad_note(self.v("key"), self.v("scale"), edge, self.v("octave"), ball_octave)
        return midi, self.v("instr"), self.v("attack"), self.v("release")

    def prewarm(self):
        """Render every note the current setup can play, off the UI thread.

        One worker; a newer request supersedes the one in progress, so holding
        a key down costs one render pass, not a thread per key repeat.
        """
        self._warm_jobs = [self.note_args(e, o) for e in range(self.world.sides) for _, o in BALL_SIZES]
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
                self.mixer.note(*job)

    def step(self, dt):
        for hit in self.world.step(dt):
            buf = self.mixer.note(*self.note_args(hit.edge, hit.octave))
            self.mixer.trigger(buf, 0.4 * min(1.0, 0.08 + hit.speed / 2.0) * self.ball_vol())   # hard hits louder
            self.hits += 1
        self.rings.step(dt)
        if self.v("chord") in PROGRESSIONS:
            self.chord_timer += dt
            if self.chord_timer >= self.v("change"):
                self.chord_timer = 0.0
                self.chord_step += 1
        self.mixer.set_rings(self.ring_targets())


# Direct hotkeys: (param, step) cycles a param, (param, "=", index) jumps to a value.
KEYMAP = {b"\x1b[C": "right", b"\x1b[D": "left", b"\x1b[A": "up", b"\x1b[B": "down",
          b"l": "right", b"h": "left", b"k": "up", b"j": "down", b"\t": "page",
          b" ": "toggle", b"r": "respawn", b"u": "undo", b"q": "quit", b"Q": "quit",
          b"s": ("shape", 1), b"S": ("shape", -1), b"n": ("key", 1), b"N": ("key", -1),
          b"c": ("scale", 1), b"C": ("scale", -1), b"i": ("instr", 1), b"I": ("instr", -1),
          b"v": ("visual", 1), b"w": ("window", 1), b"f": ("freeze", 1),
          **{str(n).encode(): ("digit", n) for n in range(1, 10)}}
# Digit keys mean different things per page: shape (sides 3-8) or chord degree (I-VII).
DIGITS = {"BALLS": ("shape", lambda n: n - 3 if 3 <= n <= 8 else None),
          "RINGS": ("chord", lambda n: n - 1 if 1 <= n <= 7 else None)}


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
        return self.items()[self.cursor[self.page]]

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
        engine.randomize()
    elif k == "undo":
        engine.undo()
    if changed:
        engine.apply()
        if changed.key in PREWARM_KEYS:
            engine.prewarm()
    return True


def run(output_factory, config=CONFIG):
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
            sync_window(window, engine, fx)
            out.write(ui.render(ui_state(engine, menu, output, fx, window), cols, rows))
            out.flush()
            time.sleep(max(0.0, FRAME_DT - (time.monotonic() - now)))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved_tty)
        out.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        out.flush()
        window.stop()
        output.stop()
        save_params(params, config, engine.history)


def sync_window(window, engine, fx):
    """Keep the Windows backdrop in step with the WINDOW setting and feed it a frame."""
    if engine.v("window") != "ON":
        window.stop()
        return
    window.start()
    if window.running:
        window.submit(scene_commands(engine.world, engine.rings, fx, engine.v("bed") / 100, ui.FLASH_S))


def ui_state(engine, menu, output=None, fx=None, window=None):
    side, knobs = page_params(menu.params, menu.page)
    return {"side": side, "knobs": knobs, "selected": menu.selected(), "pages": menu.pages,
            "page": menu.page, "world": engine.world, "rings": engine.rings,
            "bed_level": engine.v("bed") / 100, "hits": engine.hits,
            "dsp": output.dsp if output else 0.0, "on": engine.on,
            "error": " ".join(e for e in (output.error if output else "",
                                          window.error if window else "") if e),
            "fx": fx, "window": engine.v("window") == "ON"}
