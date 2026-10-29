#!/usr/bin/env python3
"""Unit tests for the tombola module. Run: python3 test/test_tombola.py"""
import json
import math
import re
import os
import random
import sys
import tempfile
import unittest
from array import array

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tombola"))

import app  # noqa: E402
import pixels  # noqa: E402
import ui  # noqa: E402
from music import SCALES, pad_note  # noqa: E402
from physics import World  # noqa: E402
from rings import Rings  # noqa: E402
from music import KEYS  # noqa: E402
from synth import BLOCK, INSTRUMENTS, SAMPLE_RATE, Mixer, render_chord, render_note  # noqa: E402


def max_overshoot(w):
    ap = math.cos(math.pi / w.sides)
    worst = -1.0
    for b in w.balls:
        for k in range(w.sides):
            m = w.angle + 2 * math.pi * (k + 0.5) / w.sides
            worst = max(worst, b.x * math.cos(m) + b.y * math.sin(m) + b.r - ap)
    return worst


class PhysicsTest(unittest.TestCase):
    def setUp(self):
        random.seed(1)

    def test_balls_stay_inside_under_extreme_settings(self):
        for sides in (3, 6, 8):
            w = World(sides, 8)
            w.spin, w.gravity, w.bounce = 3.0, 4.0, 1.0
            for _ in range(10_000):
                w.step(1 / 120)
                self.assertLess(max_overshoot(w), 0.02, f"{sides} sides")

    def test_resting_ball_does_not_retrigger(self):
        w = World(6, 1)
        w.spin, w.gravity, w.bounce = 0.0, 2.0, 0.0
        for _ in range(360):                    # 3 s to settle
            w.step(1 / 120)
        hits = sum(len(w.step(1 / 120)) for _ in range(600))
        self.assertEqual(hits, 0)

    def test_falling_ball_hits_floor_pad(self):
        w = World(4, 1)
        w.spin, w.gravity, w.bounce = 0.0, 3.0, 0.8
        b = w.balls[0]
        b.x, b.y, b.vx, b.vy = 0.0, 0.0, 0.0, 0.0
        hits = []
        for _ in range(240):
            hits += w.step(1 / 120)
        self.assertTrue(hits)
        self.assertGreater(hits[0].speed, 0.5)

    def test_ball_count_change(self):
        w = World(6, 2)
        w.set_ball_count(5)
        self.assertEqual(len(w.balls), 5)
        w.set_ball_count(1)
        self.assertEqual(len(w.balls), 1)


class MusicTest(unittest.TestCase):
    def test_pads_walk_the_scale(self):
        self.assertEqual(pad_note("A", "MINOR", 0), 57)           # A3
        self.assertEqual(pad_note("A", "MINOR", 2), 57 + 3)       # minor third
        self.assertEqual(pad_note("C", "PENTA", 5), 48 + 12)      # wraps an octave
        self.assertEqual(pad_note("C", "MAJOR", 0, octave=1, ball_octave=-1), 48)

    def test_every_scale_maps_all_pads(self):
        for scale in SCALES:
            notes = [pad_note("D#", scale, e) for e in range(8)]
            self.assertEqual(notes, sorted(notes))
            self.assertEqual(len(set(notes)), 8)


class SynthTest(unittest.TestCase):
    def test_instruments_are_audible_and_bounded(self):
        for ins in INSTRUMENTS:
            buf = render_note(64, ins, 2, 0.6)
            peak = max(map(abs, buf))
            self.assertGreater(peak, 0.1, ins)
            self.assertLessEqual(peak, 1.0, ins)

    def test_mixer_outputs_pcm_and_retires_voices(self):
        m = Mixer()
        self.assertEqual(max(map(abs, array("h", m.render()))), 0)
        m.trigger(m.note(60, "PLUCK", 2, 0.2), 0.5)
        pcm = array("h", m.render())
        self.assertEqual(len(pcm), BLOCK)
        self.assertGreater(max(map(abs, pcm)), 100)
        for _ in range(30):
            m.render()
        self.assertEqual(m.active(), 0)

    def test_voice_stealing_caps_polyphony(self):
        m = Mixer()
        note = m.note(60, "GLASS", 2, 1.0)
        for _ in range(100):
            m.trigger(note, 0.1)
        self.assertLessEqual(m.active(), 24)


    def test_chord_fades_in_and_out(self):
        c = render_chord([48, 52, 55], 2.0, 0.5)
        self.assertEqual(len(c), 2 * SAMPLE_RATE)
        self.assertLess(abs(c[0]), 1e-6)
        self.assertLess(max(map(abs, c[-50:])), 0.01)
        self.assertGreater(max(map(abs, c[SAMPLE_RATE - 500:SAMPLE_RATE + 500])), 0.2)

    def test_grains_follow_notes_and_do_not_self_sustain(self):
        random.seed(3)
        m = Mixer()
        m.grain, m.wet = 1.0, 0.0
        for _ in range(40):
            m.render()
        self.assertEqual(m.grains, [])                       # nothing played yet: no grains
        m.trigger(m.note(60, "GLASS", 2, 0.4), 0.5)
        for _ in range(60):
            m.render()
        self.assertTrue(m.grains or m.history)
        for _ in range(int(3 * SAMPLE_RATE / BLOCK)):        # notes over, history ages out
            pcm = m.render()
        self.assertEqual(max(map(abs, array("h", pcm))), 0)

    def test_bed_level_scales_live_and_mutes(self):
        m = Mixer()
        m.wet = 0.0
        m.trigger_bed(render_chord([48, 52, 55], 2.0, 0.1))
        m.bed = 0.5
        for _ in range(20):
            loud = max(map(abs, array("h", m.render())))
        m.bed = 0.0
        quiet = max(map(abs, array("h", m.render())))
        self.assertGreater(loud, 1000)
        self.assertEqual(quiet, 0)


class RingsTest(unittest.TestCase):
    def test_moons_ping_once_per_orbit(self):
        r = Rings()
        r.count, r.moons, r.ratio, r.speed = 1, 2, "SYNC", 2 * math.pi   # one orbit per second
        r.phase[0] = 0.1
        pings = sum(len(r.step(1 / 120)) for _ in range(120 * 3))
        self.assertEqual(pings, 6)                          # 2 moons x 3 orbits

    def test_kepler_outer_rings_are_slower(self):
        r = Rings()
        speeds = [r.omega(k) for k in range(4)]
        self.assertEqual(speeds, sorted(speeds, reverse=True))
        r.ratio = "HARM"
        self.assertAlmostEqual(r.omega(1), r.omega(0) / 2)

    def test_gate_is_front_centre_below_the_polygon(self):
        r = Rings()
        r.tilt = 0.0
        x, y = r.point(0, 0.0)
        self.assertAlmostEqual(x, 0.0)
        self.assertLess(y, 0)
        self.assertTrue(r.in_front(0.0))
        self.assertFalse(r.in_front(math.pi))

    def test_extent_covers_every_ring_point(self):
        r = Rings()
        r.count, r.tilt = 4, math.radians(24)
        ex, ey = r.extent()
        for i in range(360):
            x, y = r.point(3, math.radians(i))
            self.assertLessEqual(abs(x), ex + 1e-9)
            self.assertLessEqual(abs(y), ey + 1e-9)


class PixelTest(unittest.TestCase):
    def test_half_blocks_encode_top_and_bottom_pixels(self):
        cv = pixels.PixelCanvas(4, 1)
        cv.plot(0, 0, pixels.EDGE, 1.0)                       # top only
        cv.plot(1, 1, pixels.BALL, 1.0)                       # bottom only
        cv.plot(2, 0, pixels.EDGE, 1.0)
        cv.plot(2, 1, pixels.BALL, 1.0)                       # both, different colours
        row = cv.rows_text()[0]
        edge, ball = pixels.LAYERS[pixels.EDGE][1][-1], pixels.LAYERS[pixels.BALL][1][-1]
        self.assertIn(f"38;5;{edge}m▀", row)
        self.assertIn(f"38;5;{ball}m▄", row)
        self.assertIn(f"38;5;{edge};48;5;{ball}m▀", row)
        self.assertTrue(row.replace("\x1b[0m", "").endswith(" "))   # empty cell stays blank

    def test_lines_are_gapless_one_pixel_strokes(self):
        cv = pixels.PixelCanvas(40, 20)
        cv.line(2.3, 3.7, 37.6, 31.2, pixels.EDGE)
        lit = {(i % cv.w, i // cv.w) for i, l in enumerate(cv.layer) if l == pixels.EDGE}
        for y in range(4, 31):                                  # every row the line spans is hit
            self.assertTrue(any(py == y for _, py in lit), y)
        self.assertLess(len(lit), 2 * 36)                       # thin: not a smeared band

    def test_ramps_stay_in_one_hue(self):
        def rgb(c):
            if c >= 232:
                v = 8 + 10 * (c - 232)
                return v, v, v
            c -= 16
            lv = (0, 95, 135, 175, 215, 255)
            return lv[c // 36], lv[c // 6 % 6], lv[c % 6]
        for layer, (_, ramp) in pixels.LAYERS.items():
            for c in ramp:
                r, g, b = rgb(c)
                self.assertTrue(r >= g >= b, (layer, c))         # greys or warm: no olive/green/blue

    def test_scene_fits_small_strip(self):
        e = app.Engine(app.make_params())
        e.params["rings"].index = 4
        e.apply()
        for cols, rows in ((22, 3), (26, 5), (60, 20)):
            text = pixels.draw_scene(e.world, e.rings, cols, rows)
            self.assertEqual(len(text), rows)
            for line in text:
                self.assertEqual(len(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", line)), cols)


class BehindTest(unittest.TestCase):
    def test_put_words_keeps_text_and_leaves_gaps_open(self):
        placed = []
        text = f"{ui.fg(ui.DIM)}KEY{ui.RESET}{ui.SELECT} D# {ui.RESET}  {ui.fg(ui.DIM)}SCALE{ui.RESET}"
        ui._put_words(lambda r, c, t: placed.append((c, t)), 2, 10, text)
        visible = [(c, ui._ANSI.sub("", t)) for c, t in placed]
        self.assertEqual(visible, [(10, "KEY D# "), (19, "SCALE")])    # 2-space gap not drawn
        self.assertTrue(placed[1][1].startswith(ui.fg(ui.DIM)))         # colour carried into piece

    def test_behind_draws_scene_under_text_with_muted_colours(self):
        ps = app.make_params()
        e = app.Engine(ps)
        e.params["visual"].index = e.params["visual"].values.index("BEHIND")
        frame = ui.render(app.ui_state(e, app.Menu(ps)), 120, 40)
        self.assertIn("TOMBOLA", frame)
        bright = {ramp[-1] for _, ramp in pixels.LAYERS.values()}
        used = {int(c) for c in re.findall(r"38;5;(\d+)m[▀▄█]", frame)}
        self.assertTrue(used)
        self.assertTrue(used <= set(pixels.DIM.values()), used)          # only muted backdrop colours
        self.assertFalse(used & bright - set(pixels.DIM.values()))


class AppTest(unittest.TestCase):
    def test_ring_notes_outer_is_bass(self):
        e = app.Engine(app.make_params())
        for n in range(1, 5):
            e.params["rings"].index = n
            e.apply()
            notes = e.ring_notes()
            self.assertEqual(len(notes), n)
            self.assertEqual(notes[-1], min(e.chord(0)) + 12)
            self.assertEqual(notes, sorted(notes, reverse=True))

    def test_moon_pings_play_notes(self):
        e = app.Engine(app.make_params())
        e.params["balls"].index = 0
        e.params["grav"].index = 0
        e.apply()
        e.world.balls[0].x = e.world.balls[0].y = e.world.balls[0].vx = e.world.balls[0].vy = 0.0
        pings = 0
        for _ in range(120 * 8):                            # default orbits: several gate passes in 8 s
            e.step(1 / 120)
            pings += len(e.rings.flash)
        self.assertEqual(e.hits, 0)                         # the ball floats; only moons play
        self.assertGreater(e.mixer.active(), 0)

    def test_tab_switches_pages_and_keeps_each_cursor(self):
        ps = app.make_params()
        e = app.Engine(ps)
        menu = app.Menu(ps)
        self.assertEqual(menu.page, "BALLS")
        app.handle_key("right", e, menu)
        balls_sel = menu.selected()
        app.handle_key("page", e, menu)
        self.assertEqual(menu.page, "RINGS")
        self.assertEqual(menu.selected().key, "rings")
        app.handle_key("up", e, menu)
        self.assertEqual(e.rings.count, 4)
        app.handle_key("page", e, menu)
        self.assertIs(menu.selected(), balls_sel)
        self.assertEqual(app.parse_keys(b"\t"), ["page"])

    def test_hotkey_for_other_page_changes_value_without_moving_cursor(self):
        ps = app.make_params()
        e = app.Engine(ps)
        menu = app.Menu(ps)
        menu.next_page()                                    # RINGS page has no SHAPE
        before = menu.selected()
        app.handle_key(("shape", "=", 0), e, menu)
        self.assertEqual(e.world.sides, 3)
        self.assertIs(menu.selected(), before)

    def test_engine_turns_hits_into_voices(self):
        random.seed(2)
        e = app.Engine(app.make_params())
        for _ in range(600):
            e.step(1 / 120)
        self.assertGreater(e.hits, 0)
        self.assertGreater(e.mixer.active(), 0)

    def test_prewarm_bursts_use_one_worker(self):
        import threading
        e = app.Engine(app.make_params())
        before = threading.active_count()
        for _ in range(30):
            e.prewarm()
        self.assertLessEqual(threading.active_count() - before, 1)

    def test_config_roundtrip_ignores_bad_values(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cfg.json")
            ps = app.make_params()
            next(p for p in ps if p.key == "grav").nudge(2)
            app.save_params(ps, path)
            self.assertEqual(next(p for p in app.load_params(path) if p.key == "grav").value, 40)
            with open(path, "w") as f:
                json.dump({"grav": 9999, "scale": "NOPE", "balls": "4"}, f)
            loaded = {p.key: p.value for p in app.load_params(path)}
            self.assertEqual((loaded["grav"], loaded["scale"], loaded["balls"]), (30, "MINOR", 4))
            with open(path, "w") as f:
                f.write("not json")
            self.assertEqual(len(app.load_params(path)), len(ps))

    def test_save_is_atomic_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "cfg.json")
            ps = app.make_params()
            app.save_params(ps, path)
            app.save_params(ps, path)
            self.assertEqual(os.listdir(os.path.dirname(path)), ["cfg.json"])

    def test_v1_config_moves_to_pixel_but_keeps_the_sound(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cfg.json")
            with open(path, "w") as f:
                json.dump({"visual": "BLOCK", "grav": 15, "key": "E"}, f)   # no version: v1 file
            loaded = {p.key: p.value for p in app.load_params(path)}
            self.assertEqual((loaded["visual"], loaded["grav"], loaded["key"]), ("PIXEL", 15, "E"))
            ps = app.load_params(path)
            vis = next(p for p in ps if p.key == "visual")
            vis.index = vis.values.index("BLOCK")                             # user picks BLOCK again
            app.save_params(ps, path)
            self.assertEqual(next(p for p in app.load_params(path) if p.key == "visual").value, "BLOCK")

    def test_param_nudge_clamps(self):
        p = next(p for p in app.make_params() if p.key == "octave")
        for _ in range(10):
            p.nudge(1)
        self.assertEqual(p.value, 2)

    def test_parse_keys(self):
        self.assertEqual(app.parse_keys(b"\x1b[C\x1b[Akq x"),
                         ["right", "up", "up", "quit", "toggle"])

    def test_render_frame_both_pages(self):
        ps = app.make_params()
        e = app.Engine(ps)
        menu = app.Menu(ps)
        visual = e.params["visual"]
        octants = set(ui.OCTANT[1:]) - {"█"}
        page_labels = {"BALLS": ("GRAV", "BOUNCE", "SHAPE"), "RINGS": ("RINGS", "MOONS", "SPEED", "PAD", "PING")}
        for mode, is_glyph in (("PIXEL", lambda c: c in "▀▄█"),
                               ("BEHIND", lambda c: c in "▀▄█"),
                               ("BLOCK", lambda c: c in octants),
                               ("BRAILLE", lambda c: 0x2800 < ord(c) <= 0x28FF)):
            visual.index = visual.values.index(mode)
            for page in menu.pages:
                menu.page = page
                for cols, rows in ((120, 40), (60, 16)):
                    frame = ui.render(app.ui_state(e, menu), cols, rows)
                    self.assertTrue(any(map(is_glyph, frame)), mode)
                    for label in ("TOMBOLA", "SCALE", "BALLS", "RINGS", *page_labels[page]):
                        self.assertIn(label, frame, (page, cols))
                    if cols >= 100:
                        self.assertIn("HITS", frame)
                    # every cursor-positioned segment must end inside the terminal
                    for col, text in re.findall(r"\x1b\[\d+;(\d+)H((?:(?!\x1b\[\d+;\d+H).)*)", frame):
                        visible = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
                        self.assertLessEqual(int(col) - 1 + len(visible), cols, (page, cols, visible))

    def test_octant_table_covers_every_pixel_pattern(self):
        self.assertEqual(len(set(ui.OCTANT)), 256)
        self.assertEqual(ui.OCTANT[0], " ")
        self.assertEqual(ui.OCTANT[255], "█")
        self.assertEqual(ui.OCTANT[0b00001111], "▀")      # top two pixel rows

    def test_choices_wrap_numbers_clamp(self):
        ps = {p.key: p for p in app.make_params()}
        shape = ps["shape"]
        shape.index = len(shape.values) - 1
        shape.nudge(1)
        self.assertEqual(shape.value, "TRI")
        ps["grav"].index = 0
        ps["grav"].nudge(-1)
        self.assertEqual(ps["grav"].value, 0)

    def test_respawn_randomizes_settings_but_not_volume(self):
        e = app.Engine(app.make_params())
        e.params["vol"].index = 3
        seen = set()
        rng = random.Random(5)
        for _ in range(20):
            e.randomize(rng)
            seen.add(tuple(e.v(k) for k in ("key", "scale", "instr", "shape")))
            self.assertEqual(e.v("vol"), 15)
            self.assertEqual(e.v("visual"), "PIXEL")
            self.assertTrue(2 <= e.v("balls") <= 6 and 60 <= e.v("bounce") <= 96)
            self.assertEqual(len(e.world.balls), e.v("balls"))
            self.assertEqual(e.world.sides, app.SHAPES[e.v("shape")])
        self.assertGreater(len(seen), 15)

    def test_bed_chords_stay_in_scale_and_mute_when_off(self):
        e = app.Engine(app.make_params())
        root = KEYS.index(e.v("key"))
        steps = set(SCALES[e.v("scale")])
        for step in range(4):
            notes = e.chord(step)
            self.assertEqual(len(notes), 4)
            self.assertTrue(all((n - root) % 12 in steps for n in notes))
            self.assertLess(notes[0], min(notes[1:]))         # bass under the triad
        e.set_on(False)
        self.assertEqual(e.mixer.bed, 0.0)
        e.set_on(True)
        self.assertGreater(e.mixer.bed, 0.0)

    def _canvas(self, e, cols=60, rows=24):
        grab = {}
        orig = ui.Canvas.rows_text
        ui.Canvas.rows_text = lambda cv, g=ui.OCTANT: grab.setdefault("cv", cv) and orig(cv, g)
        try:
            ui.draw_world(e.world, e.rings, cols, rows, "BLOCK", None, 0.5)
        finally:
            ui.Canvas.rows_text = orig
        return grab["cv"]

    def test_rings_draw_around_the_polygon_and_hide_behind_it(self):
        e = app.Engine(app.make_params())
        e.params["balls"].index = 0
        e.params["rings"].index = 4
        e.apply()
        e.world.balls[0].x = e.world.balls[0].y = 0.0
        cv = self._canvas(e)
        ring_cells = [i for i, c in enumerate(cv.color) if c in ui.RING]
        self.assertTrue(ring_cells)
        cols = cv.cols
        xs = [i % cols for i in ring_cells]
        self.assertLess(min(xs), cols * 0.15)          # rings reach far past the polygon sideways
        self.assertGreater(max(xs), cols * 0.85)
        # far side hidden: no ring pixels in the polygon's upper interior around the centre column
        top_inside = [i for i in ring_cells if abs(i % cols - cols / 2) < 2 and i // cols < cv.rows * 0.4]
        self.assertEqual(top_inside, [])
        e.params["rings"].index = 0
        e.apply()
        self.assertFalse(any(c in ui.RING for c in self._canvas(e).color))

    def test_rings_never_recolour_ball_cells(self):
        e = app.Engine(app.make_params())
        e.params["rings"].index = 4
        e.apply()
        b = e.world.balls[0]
        b.x, b.y = e.rings.point(3, 0.0)                  # a ball sitting right on a ring's gate
        b.y = max(b.y, -0.8)
        cv = self._canvas(e)
        w, h = cv.cols * 2, cv.rows * 4
        ex, ey = e.rings.extent()
        scale = min((w / 2 - 2) / ex, (h / 2 - 2) / ey)
        px, py = w / 2 + b.x * scale, h / 2 - b.y * scale
        self.assertEqual(cv.color[int(py // 4) * cv.cols + int(px // 2)], ui.BALL)

    def test_grain_sparks_are_capped_and_expire(self):
        m = Mixer()
        fx = ui.Effects(random.Random(1))
        m.grain_count = 50
        fx.update(m, 0.0)
        self.assertEqual(len(fx.sparks), ui.Effects.MAX_NEW_SPARKS)
        self.assertTrue(all(math.hypot(x, y) <= 0.75 for x, y, _ in fx.sparks))
        fx.update(m, 0.1)
        self.assertEqual(len(fx.sparks), ui.Effects.MAX_NEW_SPARKS)   # no new grains, still alive
        fx.update(m, 0.5)
        self.assertEqual(fx.sparks, [])

    def test_hotkeys(self):
        self.assertEqual(app.parse_keys(b"3s8ncI"),
                         [("shape", "=", 0), ("shape", 1), ("shape", "=", 5),
                          ("key", 1), ("scale", 1), ("instr", -1)])
        shapes = app.make_params()[5].values
        self.assertEqual(shapes[0], "TRI")
        self.assertEqual(shapes[5], "OCT")


if __name__ == "__main__":
    unittest.main()
