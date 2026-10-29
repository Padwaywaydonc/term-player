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
from params import (CONFIG, PAGES, PROGRESSIONS, RANDOM_RANGES, load_params, make_params,  # noqa: F401
                    page_params, save_params)
from physics import BALL_SIZES, World
from rings import Rings
from synth import Mixer, render_chord
import ui

PHYS_DT = 1 / 120
FRAME_DT = 1 / 30
BED_FADE_S = 1.5
PING_ATTACK_MS = 5
PREWARM_KEYS = {"key", "scale", "instr", "attack", "release", "octave", "shape", "tone", "chord", "rings"}


class Engine:
    """Everything except the terminal: usable headless from tests."""

    def __init__(self, params, mixer=None):
        self.params = {p.key: p for p in params}
        self.world = World()
        self.rings = Rings()
        self.mixer = mixer or Mixer()
        self.hits = 0
        self.on = True
        self.chord_step = 0
        self._bed_thread = None
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
        self.mixer.grain = self.v("grain") / 100
        self.mixer.bed = self.v("bed") / 100 * 0.45 * self.ring_vol() if self.on else 0.0

    def ball_vol(self):
        return self.v("vol") / 100 * 1.2

    def ring_vol(self):
        return self.v("rvol") / 100 * 1.2

    def set_on(self, on):
        self.on = on
        self.apply()

    def chord(self, step):
        """Chord for a progression step: bass note + scale triad, below the balls."""
        prog = PROGRESSIONS[self.v("chord")]
        degree = prog[step % len(prog)]
        k, sc, octv = self.v("key"), self.v("scale"), self.v("octave")
        return [pad_note(k, sc, degree, octv - 2)] + [pad_note(k, sc, degree + i, octv - 1) for i in (0, 2, 4)]

    def ring_notes(self):
        """Note each ring's moons play, innermost first: outer rings take the low chord tones."""
        n = self.rings.count
        chord = sorted(self.chord(self.chord_step))[:n]
        return [m + 12 for m in reversed(chord)]

    def ping_args(self, midi):
        return midi, self.v("tone"), PING_ATTACK_MS, self.v("release")

    def start_bed(self):
        """Background worker: advance the chord every CHANGE seconds, crossfading the pad."""
        if self._bed_thread is None:
            self._bed_thread = threading.Thread(target=self._bed_loop, daemon=True)
            self._bed_thread.start()

    def _bed_loop(self):
        while True:
            change = self.v("change")
            if self.on and self.v("bed") > 0:
                notes = self.chord(self.chord_step)
                fade = min(BED_FADE_S, change / 2)
                self.mixer.trigger_bed(render_chord(notes, change + fade, fade))
            for job in map(self.ping_args, self.ring_notes()):
                self.mixer.note(*job)           # pre-render this chord's moon notes
            start = time.monotonic()
            while time.monotonic() - start < change:   # short naps: a new CHANGE applies quickly
                time.sleep(0.1)
                change = self.v("change")
            if self.on:
                self.chord_step += 1

    def randomize(self, rng=random):
        """New random settings (within RANDOM_RANGES) and freshly dropped balls."""
        for key, bounds in RANDOM_RANGES.items():
            p = self.params[key]
            ok = [i for i, v in enumerate(p.values) if bounds is None or bounds[0] <= v <= bounds[1]]
            p.index = rng.choice(ok)
        self.apply()
        self.world.respawn()
        self.prewarm()

    def note_args(self, edge, ball_octave):
        midi = pad_note(self.v("key"), self.v("scale"), edge, self.v("octave"), ball_octave)
        return midi, self.v("instr"), self.v("attack"), self.v("release")

    def prewarm(self):
        """Render every note the current setup can play, off the UI thread.

        One worker; a newer request supersedes the one in progress, so holding
        a key down costs one render pass, not a thread per key repeat.
        """
        self._warm_jobs = ([self.note_args(e, o) for e in range(self.world.sides) for _, o in BALL_SIZES]
                           + [self.ping_args(m) for m in self.ring_notes()])
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
            self.mixer.trigger(buf, 0.35 * min(1.0, 0.25 + hit.speed / 2.5) * self.ball_vol())
            self.hits += 1
        pings = self.rings.step(dt)
        if pings and self.v("ping"):
            notes = self.ring_notes()
            for k in pings:
                self.mixer.trigger(self.mixer.note(*self.ping_args(notes[k])),
                                   0.3 * self.v("ping") / 100 * self.ring_vol())


# Direct hotkeys: (param, step) cycles a param, (param, "=", index) jumps to a value.
KEYMAP = {b"\x1b[C": "right", b"\x1b[D": "left", b"\x1b[A": "up", b"\x1b[B": "down",
          b"l": "right", b"h": "left", b"k": "up", b"j": "down", b"\t": "page",
          b" ": "toggle", b"r": "respawn", b"q": "quit", b"Q": "quit",
          b"s": ("shape", 1), b"S": ("shape", -1), b"n": ("key", 1), b"N": ("key", -1),
          b"c": ("scale", 1), b"C": ("scale", -1), b"i": ("instr", 1), b"I": ("instr", -1),
          b"v": ("visual", 1),
          **{str(n).encode(): ("shape", "=", n - 3) for n in range(3, 9)}}


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
    if isinstance(k, tuple):                      # hotkey: change that setting directly
        changed = engine.params[k[0]]
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
    if changed:
        engine.apply()
        if changed.key in PREWARM_KEYS:
            engine.prewarm()
    return True


def run(output_factory, config=CONFIG):
    params = load_params(config)
    engine = Engine(params)
    engine.prewarm()
    engine.start_bed()
    output = output_factory(engine.mixer)
    output.start()
    menu = Menu(params)
    fx = ui.Effects()

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
                save_params(params, config)     # every change sticks, even if the pane is killed
            while acc >= PHYS_DT:
                if engine.on:
                    engine.step(PHYS_DT)
                acc -= PHYS_DT
            cols, rows = shutil.get_terminal_size()
            fx.update(engine.mixer, now)
            out.write(ui.render(ui_state(engine, menu, output, fx), cols, rows))
            out.flush()
            time.sleep(max(0.0, FRAME_DT - (time.monotonic() - now)))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved_tty)
        out.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        out.flush()
        output.stop()
        save_params(params, config)


def ui_state(engine, menu, output=None, fx=None):
    side, knobs = page_params(menu.params, menu.page)
    return {"side": side, "knobs": knobs, "selected": menu.selected(), "pages": menu.pages,
            "page": menu.page, "world": engine.world, "rings": engine.rings,
            "bed_level": engine.v("bed") / 100, "hits": engine.hits,
            "dsp": output.dsp if output else 0.0, "on": engine.on,
            "error": output.error if output else "", "fx": fx}
