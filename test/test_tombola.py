#!/usr/bin/env python3
"""Unit tests for the tombola module. Run: python3 test/test_tombola.py"""
import json
import math
import re
import subprocess
import threading
import time
import os
import random
import sys
import tempfile
import unittest
from array import array

os.environ["TOMBOLA_ENGINE"] = "python"    # these tests exercise the Python engine; native tests opt in
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tombola"))

import app  # noqa: E402
import backdrop  # noqa: E402
import pixels  # noqa: E402
import native  # noqa: E402
from marbles import STABLE, ChordSequencer  # noqa: E402
import resonator  # noqa: E402
import ui  # noqa: E402
from music import SCALES, pad_note  # noqa: E402
from physics import World  # noqa: E402
from rings import TAU, Rings  # noqa: E402
from music import KEYS  # noqa: E402
from synth import BLOCK, INSTRUMENTS, RING_TIMBRES, SAMPLE_RATE, Mixer, render_note  # noqa: E402


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

    def test_super_elastic_bounce_stays_inside_and_capped(self):
        w = World(6, 8)
        w.spin, w.gravity, w.bounce = 1.0, 2.0, 1.5
        for _ in range(6000):
            w.step(1 / 120)
            self.assertLess(max_overshoot(w), 0.02)
            self.assertTrue(all(math.hypot(b.vx, b.vy) <= 6.0 + 1e-9 for b in w.balls))

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
        self.assertGreaterEqual(len(INSTRUMENTS), 10)
        for ins in INSTRUMENTS:
            for midi in (40, 64, 96):                         # low, middle, very high (aliasing guard)
                buf = render_note(midi, ins, 2, 0.6)
                peak = max(map(abs, buf))
                self.assertGreater(peak, 0.1, (ins, midi))
                self.assertLessEqual(peak, 1.0, (ins, midi))

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


    def test_ring_voice_ramps_in_and_glides_without_clicks(self):
        m = Mixer()
        m.wet, m.bed = 0.0, 0.6
        m.set_rings([(57, 1.0)])
        first = array("h", m.render())
        self.assertLess(max(map(abs, first[:16])), 300)       # gain ramps up, no jump at the start
        for _ in range(30):
            pcm = array("h", m.render())
        steady = max(abs(pcm[i + 1] - pcm[i]) for i in range(len(pcm) - 1))
        m.set_rings([(64, 1.0)])                                # chord change: glide, not a click
        jumps = []
        for _ in range(8):
            pcm = array("h", m.render())
            jumps += [abs(pcm[i + 1] - pcm[i]) for i in range(len(pcm) - 1)]
        for _ in range(30):
            pcm = array("h", m.render())
        steady_after = max(abs(pcm[i + 1] - pcm[i]) for i in range(len(pcm) - 1))
        self.assertLessEqual(max(jumps), 1.2 * max(steady, steady_after))   # a click would spike far above

    def _cloud_mixer(self, **kw):
        m = Mixer()
        m.wet, m.grain_source = 0.0, "BALLS"
        for k, v in kw.items():
            setattr(m.cloud, k, v)
        return m

    def test_grains_follow_notes_and_do_not_self_sustain(self):
        random.seed(3)
        m = self._cloud_mixer(level=1.0, density=0.8)
        for _ in range(40):
            m.render()
        self.assertEqual(m.cloud.count, 0)                   # nothing played yet: no grains
        m.trigger(m.note(60, "GLASS", 2, 0.4), 0.5)
        for _ in range(60):
            m.render()
        self.assertGreater(m.cloud.count, 0)
        for _ in range(int(3 * SAMPLE_RATE / BLOCK)):        # notes over, history ages out
            pcm = m.render()
        self.assertEqual(max(map(abs, array("h", pcm))), 0)

    def test_density_sign_steady_random_or_none(self):
        for density, check in ((-0.5, lambda c: c in (39, 40)), (0.0, lambda c: c == 0), (0.5, lambda c: 25 <= c <= 55)):
            random.seed(1)
            m = self._cloud_mixer(level=1.0, density=density, size=0.05)
            m.trigger(m.note(60, "ORGAN", 2, 4.0), 0.5)
            for _ in range(int(2 * SAMPLE_RATE / BLOCK)):    # 2 s at 20 grains/s
                m.render()
            self.assertTrue(check(m.cloud.count), (density, m.cloud.count))

    def test_freeze_keeps_granulating_after_the_source_stops(self):
        random.seed(2)
        m = self._cloud_mixer(level=1.0, density=0.6)
        m.trigger(m.note(60, "GLASS", 2, 0.5), 0.5)
        for _ in range(30):
            m.render()
        m.cloud.freeze = True
        for _ in range(int(4 * SAMPLE_RATE / BLOCK)):        # long after the note ended
            pcm = m.render()
        self.assertGreater(max(map(abs, array("h", pcm))), 200)

    def test_grains_are_clearly_audible(self):
        random.seed(4)
        m = self._cloud_mixer(level=0.4, density=0.3)         # the defaults
        m.trigger(m.note(57, "ORGAN", 2, 4.0), 0.4)
        src = grains = 0.0
        for _ in range(int(2 * SAMPLE_RATE / BLOCK)):
            before = m.cloud.count
            out = [0.0] * BLOCK
            from synth import _mix
            _mix(m.voices, out)
            g = m.cloud.process(out)
            src += sum(x * x for x in out)
            grains += sum(x * x for x in g)
        self.assertGreater(math.sqrt(grains / src), 0.3)     # within ~10 dB of the dry notes

    def test_texture_morphs_the_window(self):
        from synth import grain_window
        box, tri, hann = (grain_window(1000, t) for t in (0.0, 0.5, 1.0))
        self.assertGreater(box[100], 0.99)                    # flat top, fast edges
        self.assertAlmostEqual(tri[250], 0.5, places=2)
        self.assertAlmostEqual(hann[250], 0.5, places=2)
        self.assertLess(hann[100], tri[100])                 # hann is softer near the edges
        self.assertLess(box[0], 0.01)

    def test_feedback_cannot_run_away(self):
        """Regression: FEEDBACK 85%, 500 ms grains, density +40 used to lock the output at full scale."""
        random.seed(5)
        m = self._cloud_mixer(level=0.7, density=0.4, feedback=0.85, size=0.5, position=0.1,
                              spread=0.1, texture=0.3, pitch=-1)
        m.wet = 0.5
        loud = []
        for sec in range(40):
            if sec < 30 and sec % 2 == 0:                    # a ball hit every 2 s, then silence
                m.trigger(m.note(57, "GLASS", 5, 2.0), 0.3)
            acc = 0.0
            for _ in range(SAMPLE_RATE // BLOCK):
                pcm = array("h", m.render())
                acc += sum(x * x for x in pcm) / len(pcm)
            loud.append(math.sqrt(acc / (SAMPLE_RATE // BLOCK)) / 32768)
        self.assertLess(max(loud[:30]), 0.3)                 # never pinned near full scale
        self.assertLess(loud[-1], 0.25 * loud[29])           # source stopped: the echo dies away
        self.assertLessEqual(max(map(abs, m.cloud.history)), 1.0)

    def test_pad_level_scales_ring_tones_and_mutes(self):
        m = Mixer()
        m.wet = 0.0
        m.cloud.level = 0.0                                  # rings only (grains would echo them)
        m.set_rings([(45, 1.0), (52, 1.0), (57, 1.0)])
        m.bed = 0.5
        for _ in range(20):
            loud = max(map(abs, array("h", m.render())))
        m.bed = 0.0
        quiet = max(map(abs, array("h", m.render())))
        self.assertGreater(loud, 1000)
        self.assertEqual(quiet, 0)

    def test_removed_rings_fade_out_and_are_dropped(self):
        m = Mixer()
        m.bed = 0.5
        m.set_rings([(45, 1.0), (52, 1.0)])
        for _ in range(20):
            m.render()
        m.set_rings([(45, 1.0)])
        for _ in range(30):
            m.render()
        self.assertEqual(len(m.ring_voices), 1)

    def test_ring_timbres_have_their_harmonics(self):
        from synth import _harmonic_table, PAD_TABLE_SIZE
        def harmonic(table, k):
            return abs(sum(x * math.sin(2 * math.pi * k * i / PAD_TABLE_SIZE) for i, x in enumerate(table)))
        warm = _harmonic_table("WARM", 99, RING_TIMBRES)
        sine = _harmonic_table("SINE", 99, RING_TIMBRES)
        self.assertGreater(harmonic(warm, 2), 0.3 * harmonic(warm, 1))
        self.assertLess(harmonic(sine, 2), 0.01 * harmonic(sine, 1))
        saw_high = _harmonic_table("SAW", 2, RING_TIMBRES)                # high note: only 2 harmonics
        self.assertLess(harmonic(saw_high, 5), 0.01 * harmonic(saw_high, 1))


class RingsTest(unittest.TestCase):
    def test_moon_envelope_follows_height(self):
        r = Rings()
        r.count = 1
        ys = [(r.point(0, math.radians(d))[1], math.radians(d)) for d in range(360)]
        top, bottom = max(ys)[1], min(ys)[1]
        self.assertAlmostEqual(r.moon_level(0, top), 1.0, places=3)
        self.assertAlmostEqual(r.moon_level(0, bottom), 0.0, places=3)
        for (y1, a1), (y2, a2) in zip(ys, ys[1:]):           # higher on screen = louder
            self.assertEqual(y1 < y2, r.moon_level(0, a1) < r.moon_level(0, a2))

    def test_tilt_moves_the_loudest_point(self):
        r = Rings()
        r.tilt = 0.0
        flat = max(range(360), key=lambda d: r.moon_level(0, math.radians(d)))
        r.tilt = math.radians(30)
        tilted = max(range(360), key=lambda d: r.moon_level(0, math.radians(d)))
        self.assertEqual(flat, 180)                          # level ring: top centre
        self.assertGreater(abs(tilted - 180), 30)            # tilted: peak slides round the ring
        r.tilt = 0.0
        r.count, r.moons = 1, 2                              # two moons opposite: one always high
        for a in (0.0, 0.7, 2.0, 3.0):
            r.phase[0] = a
            self.assertGreaterEqual(r.level(0), 0.35)

    def test_kepler_outer_rings_are_slower(self):
        r = Rings()
        speeds = [r.omega(k) for k in range(4)]
        self.assertEqual(speeds, sorted(speeds, reverse=True))
        r.ratio = "HARM"
        self.assertAlmostEqual(r.omega(1), r.omega(0) / 2)

    def test_lower_half_in_front_upper_half_behind(self):
        r = Rings()
        r.tilt = 0.0
        x, y = r.point(0, 0.0)
        self.assertAlmostEqual(x, 0.0)
        self.assertLess(y, 0)                                # bottom centre ...
        self.assertTrue(r.in_front(0.0))                     # ... is the near side, in front of the planet
        self.assertFalse(r.in_front(math.pi))                # the top is the far side, behind it

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


class ResonatorTest(unittest.TestCase):
    @staticmethod
    def mag(x, f, start=2205, n=4410):
        seg = x[start:start + n]
        re_ = sum(v * math.cos(2 * math.pi * f * i / SAMPLE_RATE) for i, v in enumerate(seg))
        im = sum(v * math.sin(2 * math.pi * f * i / SAMPLE_RATE) for i, v in enumerate(seg))
        return math.hypot(re_, im)

    def render(self, model="MODAL", midi=57, structure=0.0, bright=0.6, damp=0.5, pos=0.3, strength=0.75, chord=()):
        return resonator.render_resonator(midi, model, structure, bright, damp, pos, strength, chord)

    def test_models_are_bounded_and_in_tune(self):
        from synth import midi_to_freq
        for model in resonator.MODELS:
            for midi in (45, 69, 81):
                x = self.render(model, midi)
                self.assertLessEqual(max(map(abs, x)), 0.91, (model, midi))
                f0 = midi_to_freq(midi)
                best = max((f0 * 2 ** (c / 1200) for c in range(-50, 51, 5)), key=lambda f: self.mag(x, f))
                self.assertLess(abs(1200 * math.log2(best / f0)), 12, (model, midi))   # within ~a tenth of a semitone

    def test_damping_sets_ring_time(self):
        short, long_ = self.render(damp=0.2), self.render(damp=0.8)
        self.assertGreater(len(long_), 3 * len(short))
        tail = lambda x: max(map(abs, x[int(0.4 * SAMPLE_RATE):int(0.5 * SAMPLE_RATE)]), default=0)
        self.assertLess(tail(short), 0.5 * tail(long_))

    def test_centre_strike_cancels_even_harmonics(self):
        from synth import midi_to_freq
        f = midi_to_freq(57)
        end, centre = self.render(pos=0.0, bright=0.8), self.render(pos=1.0, bright=0.8)
        self.assertGreater(self.mag(end, 2 * f) / self.mag(end, f), 0.3)
        self.assertLess(self.mag(centre, 2 * f) / self.mag(centre, f), 0.02)

    def test_brightness_moves_energy_up(self):
        from synth import midi_to_freq
        fs = [midi_to_freq(57) * k for k in range(1, 9)]
        def centroid(x):
            m = [self.mag(x, f) for f in fs]
            return sum(f * v for f, v in zip(fs, m)) / sum(m)
        for model in ("MODAL", "STRING"):
            self.assertGreater(centroid(self.render(model, bright=0.9)), 1.5 * centroid(self.render(model, bright=0.1)))

    def test_sympathetic_strings_sound_the_chord(self):
        from synth import midi_to_freq
        chord = (45, 52, 57, 61)                                  # A major-ish: C#4 is not in a plain A3 string
        plain = self.render("STRING", 57, structure=0.8, chord=chord)
        symp = self.render("SYMPATH", 57, structure=0.8, chord=chord)
        csharp = midi_to_freq(61)
        self.assertGreater(self.mag(symp, csharp, start=6615), 4 * self.mag(plain, csharp, start=6615))

    def test_same_hit_renders_identically(self):
        self.assertEqual(self.render("STRING"), self.render("STRING"))


class ChokeTest(unittest.TestCase):
    def test_poly_one_chokes_the_previous_ring_without_a_click(self):
        m = Mixer()
        m.wet, m.cloud.level = 0.0, 0.0
        long_note = resonator.render_resonator(57, "MODAL", 0.0, 0.6, 0.9, 0.3, 0.75)
        m.trigger(long_note, 0.5, group="balls", poly=1)
        for _ in range(10):
            m.render()
        m.trigger(long_note, 0.5, group="balls", poly=1)
        pcm = []
        for _ in range(6):
            pcm += array("h", m.render())
        self.assertEqual(sum(1 for v in m.voices if v[3] == "balls"), 1)   # the old ring is gone
        jumps = max(abs(pcm[i + 1] - pcm[i]) for i in range(len(pcm) - 1))
        self.assertLess(jumps, 6000)

    def test_poly_four_lets_four_ring(self):
        m = Mixer()
        note = resonator.render_resonator(57, "MODAL", 0.0, 0.6, 0.9, 0.3, 0.75)
        for _ in range(6):
            m.trigger(note, 0.2, group="balls", poly=4)
        ringing = [v for v in m.voices if v[3] == "balls" and v[4] is None]
        self.assertEqual(len(ringing), 4)


class ResonatorEngineTest(unittest.TestCase):
    def test_rings_instrument_routes_hits_through_the_resonator(self):
        e = app.Engine(app.make_params(), pool=resonator.RenderPool(inline=True))
        e.params["instr"].index = e.params["instr"].values.index("RESONATOR")
        e.apply()
        from physics import Hit
        e.world.step = lambda dt: [Hit(0, 0, 2.0, 0)]
        e.step(1 / 120)
        self.assertTrue(e.pool.done)
        self.assertEqual(e.mixer.voices[-1][3], "balls")

    def test_pool_renders_in_another_process_and_falls_back_meanwhile(self):
        pool = resonator.RenderPool(workers=1)
        try:
            args = (57, "STRING", 0.0, 0.6, 0.5, 0.3, 0.75, ())
            self.assertIsNone(pool.get(args))                    # first render still running: no stall
            deadline = time.time() + 30
            while args not in pool.done and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(list(pool.get(args)), list(resonator.render_resonator(*args)))
            newer = (57, "STRING", 0.0, 0.6, 0.9, 0.3, 0.75, ())    # DAMPING changed
            self.assertEqual(list(pool.get(newer)), list(pool.done[args]))   # old sound meanwhile
        finally:
            pool.close()

    def test_balls_page_swaps_in_resonator_knobs(self):
        ps = app.make_params()
        e = app.Engine(ps)
        menu = app.Menu(ps)
        for _ in range(7):
            app.handle_key("right", e, menu)                      # cursor far along the normal page
        keys = [p.key for p in menu.items()]
        self.assertIn("attack", keys)
        self.assertNotIn("rmodel", keys)
        e.params["instr"].index = e.params["instr"].values.index("RESONATOR")
        keys = [p.key for p in menu.items()]
        for k in ("rmodel", "rstruct", "rbright", "rdamp", "rpos", "rpoly"):
            self.assertIn(k, keys)
        self.assertNotIn("attack", keys)
        menu.selected()                                           # cursor stays valid after the swap
        frame = ui.render(app.ui_state(e, menu), 140, 40)
        for label in ("MODEL", "STRUCTURE", "BRIGHTNESS", "DAMPING", "POLY"):
            self.assertIn(label, frame)


class MarblesTest(unittest.TestCase):
    def seq(self, **kw):
        return ChordSequencer(rng=random.Random(kw.pop("seed", 1)), **kw)

    def test_deja_vu_modes(self):
        new = self.seq(deja=0.0)
        start = list(new.loop)
        out = [new.next() for _ in range(64)]
        self.assertNotEqual(new.loop, start)                       # 0%: keeps rewriting
        locked = self.seq(deja=0.5)
        out = [locked.next() for _ in range(64)]
        self.assertTrue(all(out[i] == out[i + 8] for i in range(56)))   # 50%: one loop, forever
        shuffled = self.seq(deja=0.9)
        start = list(shuffled.loop)
        out = [shuffled.next() for _ in range(64)]
        self.assertEqual(shuffled.loop, start)                     # >50%: never rewritten ...
        self.assertTrue(set(out) <= set(start))
        self.assertFalse(all(out[i] == out[i + 8] for i in range(56)))  # ... but reordered

    def test_partial_deja_vu_drifts_slowly(self):
        q = self.seq(deja=0.4)                                     # replays 80% of steps
        start = list(q.loop)
        for _ in range(8):
            q.next()
        changed = sum(a != b for a, b in zip(start, q.loop))
        self.assertLessEqual(changed, 4)

    def test_spread_and_bias(self):
        def moves(spread):
            q = self.seq(deja=0.0, spread=spread, seed=2)
            prev, out = q.current, []
            for _ in range(400):
                d = q.next()
                out.append(min((d - prev) % 7, (prev - d) % 7))
                prev = d
            return out
        narrow, wide = moves(0.0), moves(1.0)
        self.assertGreater(narrow.count(1), 0.7 * len(narrow))
        self.assertGreater(wide.count(2) + wide.count(3), 3 * (narrow.count(2) + narrow.count(3)))
        def stable_share(bias):
            q = self.seq(deja=0.0, spread=0.7, bias=bias, seed=3)
            return sum(q.next() in STABLE for _ in range(400)) / 400
        self.assertGreater(stable_share(1.0), 0.75)
        self.assertLess(stable_share(0.0), 0.4)

    def test_regenerate_and_resize(self):
        q = self.seq(length=8)
        old = list(q.loop)
        q.regenerate()
        self.assertEqual(q.loop[0], 0)                             # a new loop starts on the tonic
        self.assertNotEqual(q.loop, old)
        self.assertTrue(all(q.is_fresh(i) for i in range(8)))
        q.set_length(12)
        self.assertEqual(len(q.loop), 12)
        q.set_length(3)
        self.assertEqual(len(q.loop), 3)
        self.assertLess(q.pos, 3)


class ChordClockTest(unittest.TestCase):
    def engine(self, **kw):
        e = app.Engine(app.make_params())
        settings = {"chord": "RANDOM", "deja": 0, "balls": 1, "grav": 0, **kw}
        for k, v in settings.items():
            e.params[k].index = e.params[k].values.index(v)
        e.apply()
        e.rng.seed(4)
        return e

    def run_for(self, e, seconds):
        changes = 0
        for _ in range(int(seconds * 120)):
            before = e.sequencer.steps + e.chord_step
            e.step(1 / 120)
            changes += e.sequencer.steps + e.chord_step - before
        return changes

    def test_top_angle_is_the_loudest_point(self):
        r = Rings()
        for tilt in (0.0, 0.2, 0.5):
            r.tilt = tilt
            self.assertAlmostEqual(r.moon_level(0, r.top_angle()), 1.0, places=6)

    def test_moon_clock_ticks_once_per_outer_moon_pass(self):
        e = self.engine(clock="MOON", rings=2, moons=1, speed=100, ratio="SYNC", rsync="OFF")
        omega = e.rings.omega(1)                                   # outer ring
        expected = 30 * omega / TAU
        self.assertLessEqual(abs(self.run_for(e, 30) - expected), 1.0)

    def test_synced_moons_and_bars_clock_land_on_the_bar(self):
        e = self.engine(clock="MOON", rings=2, moons=1, ratio="SYNC", rsync="ON", rbars=1, bpm=120)
        self.run_for(e, 4)                                         # the rings settle onto the grid
        self.assertEqual(self.run_for(e, 20), 10)                 # 1-bar orbit at 120 BPM: every 2 s
        e = self.engine(clock="BARS", cbars=2, bpm=120)
        self.assertEqual(self.run_for(e, 20.01), 5)               # every 2 bars = 4 s
        e = self.engine(clock="BARS", cbars=2, bpm=120, clk_chords="x2")
        self.assertEqual(self.run_for(e, 20.01), 10)              # its clock twice the master

    def test_synced_ring_never_jumps_when_the_tempo_changes(self):
        e = self.engine(rsync="ON", rbars=1, bpm=120)
        for _ in range(300):
            e.step(1 / 120)
        before = list(e.rings.phase)
        e.params["bpm"].index = e.params["bpm"].values.index(60)
        e.apply()
        e.step(1 / 120)
        self.assertLess(max(abs(a - b) for a, b in zip(e.rings.phase, before)), 0.2)

    def test_time_clock_with_jitter(self):
        e = self.engine(clock="TIME", change=2, jitter=0)
        self.assertEqual(self.run_for(e, 20.01), 10)
        e = self.engine(clock="TIME", change=2, jitter=100)
        intervals = [e._time_interval() for _ in range(200)]
        self.assertGreater(max(intervals) - min(intervals), 2.0)   # varied ...
        self.assertTrue(all(0.4 <= i <= 3.6 for i in intervals))   # ... within +-75%

    def test_chance_is_a_coin_toss(self):
        self.assertEqual(self.run_for(self.engine(clock="TIME", change=2, chance=0), 40), 0)
        half = self.run_for(self.engine(clock="TIME", change=2, chance=50), 400)
        self.assertTrue(60 <= half <= 140, half)                  # ~100 of 200 ticks

    def test_hold_freezes_and_g_regenerates(self):
        e = self.engine(clock="TIME", change=2)
        menu = app.Menu(list(e.params.values()))
        app.handle_key("hold", e, menu)
        self.assertEqual(self.run_for(e, 10), 0)
        app.handle_key("hold", e, menu)
        self.assertGreater(self.run_for(e, 10), 0)
        old = list(e.sequencer.loop)
        app.handle_key("regenerate", e, menu)
        self.assertNotEqual(e.sequencer.loop, old)
        self.assertEqual(app.parse_keys(b"gx"), ["regenerate", "hold"])

    def test_digits_on_chords_page_hold_a_degree(self):
        ps = app.make_params()
        e = app.Engine(ps)
        menu = app.Menu(ps)
        while menu.page != "CHORDS":
            menu.next_page()
        app.handle_key(("digit", 4), e, menu)
        self.assertEqual(e.v("chord"), "IV")

    def test_chords_page_shows_the_loop(self):
        e = self.engine(clock="TIME", change=2)
        menu = app.Menu(list(e.params.values()))
        while menu.page != "CHORDS":
            menu.next_page()
        self.run_for(e, 5)
        for cols, rows in ((140, 40), (150, 5)):
            frame = ui.render(app.ui_state(e, menu), cols, rows)
            self.assertIn("LOOP", frame, cols)
            self.assertIn(ui.SELECT, frame)                        # the playing chord is lit


LIB = native.load()


@unittest.skipUnless(LIB, "native core not built (cd native && make)")
class NativeCoreTest(unittest.TestCase):
    SR = native.SAMPLE_RATE

    def mixer(self, **cloud):
        m = native.NativeMixer(LIB)
        m.wet, m.cloud.level = 0.0, 0.0
        for k, v in cloud.items():
            setattr(m.cloud, k, v)
        return m

    def run_for(self, m, seconds, each=None):
        left, right = [], []
        for b in range(int(seconds * self.SR / native.BLOCK)):
            if each:
                each(m, b)
            pcm = array("h", m.render())
            left += pcm[0::2]
            right += pcm[1::2]
        return left, right

    def mag(self, x, f, start=9600, n=19200):
        seg = x[start:start + n]
        return math.hypot(sum(v * math.cos(2 * math.pi * f * i / self.SR) for i, v in enumerate(seg)),
                          sum(v * math.sin(2 * math.pi * f * i / self.SR) for i, v in enumerate(seg)))

    @staticmethod
    def rms(x):
        return math.sqrt(sum(v * v for v in x) / max(1, len(x)))

    def test_every_resonator_model_rings_in_tune_and_in_stereo(self):
        for model in range(len(app.RESONATOR_MODELS)):
            m = self.mixer()
            m.res.update(model=model, poly=4)
            m.strike(57, 1.0)
            left, right = self.run_for(m, 1.0)
            mono = [a + b for a, b in zip(left, right)]
            self.assertGreater(self.mag(mono, 220.0), 5 * self.mag(mono, 220.0 * 2 ** (-1 / 12)), model)
            self.assertGreater(self.rms([a - b for a, b in zip(left, right)]), 0.2 * self.rms(left), model)

    def test_harder_strikes_are_louder(self):
        soft, hard = self.mixer(), self.mixer()
        soft.strike(57, 0.2)
        hard.strike(57, 1.0)
        self.assertGreater(self.rms(self.run_for(hard, 0.3)[0]), 3 * self.rms(self.run_for(soft, 0.3)[0]))

    def test_engine_strikes_the_resonator_in_real_time(self):
        e = app.Engine(app.make_params(), mixer=native.NativeMixer(LIB))
        e.params["instr"].index = e.params["instr"].values.index("RESONATOR")
        e.params["rmodel"].index = e.params["rmodel"].values.index("STRING")
        e.apply()
        self.assertEqual(e.mixer.res["model"], app.RESONATOR_MODELS.index("STRING"))
        from physics import Hit
        struck = []
        e.mixer.strike = lambda midi, strength: struck.append((midi, strength))
        e.world.step = lambda dt: [Hit(0, 0, 2.0, 0)]
        e.step(1 / 120)
        self.assertEqual(len(struck), 1)
        self.assertEqual(e.hits, 1)

    def test_ring_chord_and_grains_sound(self):
        m = self.mixer()
        m.bed = 0.4
        m.set_rings([(57, 1.0), (64, 1.0)])
        self.assertGreater(self.rms(self.run_for(m, 0.5)[0][12000:]), 300)

        def chord(level, freeze_at=None):
            g = self.mixer(level=level, density=0.5, size=0.2, position=0.3)
            g.grain_source, g.bed = "RINGS", 0.4

            def each(mm, b):
                t = b * native.BLOCK / self.SR
                mm.set_rings([(57, 1.0 if t < 3 else 0.0)])
                if freeze_at is not None and t >= freeze_at:
                    mm.cloud.freeze = True
            return self.run_for(g, 6, each)[0]
        dry, wet, frozen = chord(0.0), chord(1.0), chord(1.0, freeze_at=2.5)
        self.assertGreater(self.rms(wet[48000:144000]), 1.2 * self.rms(dry[48000:144000]))
        self.assertLess(self.rms(wet[200000:]), 50)            # source gone, not frozen: fades
        self.assertGreater(self.rms(frozen[200000:]), 300)      # frozen: keeps granulating

    def test_feedback_top_sustains_a_bed_that_can_be_stopped(self):
        def run(feedback, lower_at=None):
            m = self.mixer(level=1.0, density=0.8, size=0.4, feedback=feedback)
            m.grain_source, m.bed = "RINGS", 0.5

            def each(mm, b):
                t = b * native.BLOCK / self.SR
                mm.set_rings([(57, 1.0 if t < 5 else 0.0)])
                if lower_at is not None and t >= lower_at:
                    mm.cloud.feedback = 0.3
            return self.run_for(m, 20, each)[0]
        top = run(1.0)
        self.assertGreater(self.rms(top[-96000:]), 1000)       # source gone 15 s ago: the bed goes on
        self.assertLess(max(map(abs, top)), 32000)              # ... without clipping
        self.assertLess(self.rms(run(0.5)[-96000:]), 200)       # mid FEEDBACK still fades
        self.assertLess(self.rms(run(1.0, lower_at=10)[-96000:]), 200)   # turning it down ends the bed

    def test_all_grain_modes_play(self):
        def run(mode, level):
            m = self.mixer(level=level, density=0.7, size=0.2, mode=mode)
            m.grain_source, m.bed = "RINGS", 0.4
            return self.run_for(m, 2, lambda mm, b: mm.set_rings([(57, 1.0)]))[0][48000:]
        for mode in native.GRAIN_MODES:              # the grains alone: with them minus without
            wet = [a - b for a, b in zip(run(mode, 1.0), run(mode, 0.0))]
            self.assertGreater(self.rms(wet), 300, mode)

    def test_renders_far_faster_than_real_time(self):
        m = self.mixer(level=1.0, density=0.8, size=0.4)
        m.bed, m.wet = 0.5, 0.5
        m.set_rings([(45, 1), (52, 1), (57, 1), (61, 1)])
        start = time.perf_counter()
        self.run_for(m, 5, lambda mm, b: mm.strike(57 + b % 12, 0.8) if b % 20 == 0 else None)
        self.assertLess(time.perf_counter() - start, 1.5)        # 5 s of audio, typically ~0.15 s

    def test_every_synth_engine_sounds(self):
        for i, name in enumerate(native.ENGINES):
            m = self.mixer()
            m.synth["engine"] = i
            m.synth_hit(57, 1.0)
            self.assertGreater(self.rms(self.run_for(m, 0.5)[0]), 150, name)

    def test_ring_synth_gate_follows_the_moon_level(self):
        def ring(level):
            m = self.mixer()
            m.bed = 0.5
            m.ring_synth["engine"] = native.ENGINES.index("CHORDS")
            left = self.run_for(m, 1.0, lambda mm, b: mm.set_rings([(57, level), (64, level)]))[0][24000:]
            hi = sum(self.mag(left, 220 * k, 0, 9600) for k in (4, 5, 6))
            lo = self.mag(left, 220, 0, 9600) + self.mag(left, 329.6, 0, 9600)
            return self.rms(left), hi / lo
        (loud, bright_loud), (quiet, bright_quiet) = ring(1.0), ring(0.1)
        self.assertGreater(loud, 3 * quiet)                      # quieter ...
        self.assertGreater(bright_loud, 1.5 * bright_quiet)      # ... and darker, like a low-pass gate

    def test_tab_clock_ratios_and_quantized_hits(self):
        def steps(ratio):
            m = self.mixer()
            m.clock.update(bpm=120.0, drum_ratio=ratio)
            m.drums["play"] = 1
            for _ in range(100):                                          # 1 s = 2 beats
                m.render()
            return m.drum_step()
        self.assertEqual(steps(1.0), 16)                                 # 8 steps a beat
        self.assertEqual(steps(2.0), 0)                                  # twice as fast: 32 = a full bar
        self.assertEqual(steps(0.5), 8)
        m = self.mixer()                                                 # 1/4 grid at 60 BPM: hits wait for the beat
        m.synth["engine"] = 8
        m.clock.update(bpm=60.0, quant=1)
        for _ in range(30):
            m.render()
        m.synth_hit(69, 1.0)
        out = [self.rms(array("h", m.render())) for _ in range(100)]
        first = next(i for i, v in enumerate(out) if v > 50)
        self.assertGreaterEqual(first, 60)                               # ... held until 1.0 s, not played at 0.3 s
        self.assertLessEqual(first, 72)

    def test_tabs_off_fade_out_their_sound(self):
        def level(**buses):
            m = self.mixer()
            m.drums.update(play=1, density=(0.6, 0.5, 0.7))
            m.buses.update(buses)
            return self.rms(self.run_for(m, 1.0)[0][24000:])
        self.assertGreater(level(), 300)
        self.assertEqual(level(drums=0.0), 0)

    def test_radio_plays_what_is_pushed(self):
        m = self.mixer()
        tone = array("h", [int(8000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(48000) for _ in (0, 1)])
        self.assertEqual(m.radio_push(tone.tobytes()[:4 * 24000]), 4 * 24000)
        self.assertAlmostEqual(m.radio_queued(), 0.5, places=2)
        left = self.run_for(m, 0.4)[0]
        self.assertGreater(self.mag(left, 440.0, 0, 9600), 10 * self.mag(left, 466.2, 0, 9600))
        m.radio_level = 0.0
        self.assertEqual(self.rms(self.run_for(m, 0.05)[0]), 0)          # VOL 0: silent
        m.radio_clear()
        self.assertEqual(m.radio_queued(), 0)

    def test_drums_play_only_when_on_and_follow_density(self):
        def level(**drums):
            m = self.mixer()
            m.drums.update(**drums)
            return self.rms(self.run_for(m, 1.0)[0])
        self.assertEqual(level(play=0), 0)
        self.assertGreater(level(play=1, density=(0.6, 0.5, 0.7)), 300)
        self.assertEqual(level(play=1, density=(0.0, 0.0, 0.0)), 0)       # density 0: that part never plays

    def test_drum_tempo_spans_10_to_200_bpm(self):
        bpm = next(p for p in app.make_params() if p.key == "bpm")
        self.assertEqual((bpm.values[0], bpm.values[-1], bpm.value), (10, 200, 112))
        for tempo in (10, 200):
            m = self.mixer()
            m.drums["play"] = 1
            m.clock["bpm"] = float(tempo)
            for _ in range(100):                                       # 1 s
                m.render()
            self.assertEqual(m.drum_step(), (tempo * 8 // 60) % 32, tempo)   # 8 steps per beat

    def test_drum_map_moves_from_straight_to_busy(self):
        m = self.mixer()
        hits = lambda x, y: sum(v > 255 - 0.6 * 255 for part in m.drum_levels(x, y) for v in part)
        straight = m.drum_levels(0.0, 0.0)
        self.assertTrue(all(straight[0][s] > 192 for s in (0, 8, 16, 24)))   # four on the floor, accented
        self.assertTrue(straight[1][8] > 192 and straight[1][24] > 192)     # backbeat
        self.assertGreater(hits(1.0, 1.0), 2 * hits(0.0, 0.0))

    def test_warp_is_off_at_mix_zero_and_level_matched_when_on(self):
        def run(**warp):
            m = self.mixer()
            m.bed = 0.3
            m.synth["engine"] = 8
            m.warp.update(**warp)
            return self.run_for(m, 1.5, lambda mm, b: (mm.set_rings([(48, .8), (55, .7)]),
                                                     b % 25 == 0 and mm.synth_hit(60, .8)))[0]
        dry = run(mix=0.0)
        self.assertEqual(dry, run(mix=0.0, algo=0.5))                     # bypassed entirely
        for algo in (0.1, 0.4, 0.6, 0.9):                                # cross-mods ... vocoder
            wet = run(mix=1.0, algo=algo, drive=0.8)
            self.assertNotEqual(wet, dry)
            self.assertLess(abs(math.log(self.rms(wet[36000:]) / self.rms(dry[36000:]))), math.log(3), algo)

    def test_scatter_mode_adds_grains(self):
        def run(level):
            m = self.mixer(mode="SCATTER", level=level, density=0.6)
            m.bed = 0.3
            return self.rms(self.run_for(m, 2.0, lambda mm, b: mm.set_rings([(57, .8)]))[0][48000:])
        self.assertGreater(run(1.0), 1.1 * run(0.0))

    def test_mods_sweep_their_targets(self):
        m = self.mixer()
        values = set()
        for _ in range(100):
            m.render()
            values.add(tuple(m.mod_values()))
        self.assertEqual(values, {(0.0, 0.0, 0.0, 0.0)})                 # no targets: not running
        m.mods.update(targets=(1, 6, 0, 0), depths=(1.0, 0.5, 0.0, 0.0), rate=5.0)
        seen = []
        for _ in range(100):
            m.render()
            seen.append(m.mod_values()[0])
        self.assertLess(min(seen), -0.8)
        self.assertGreater(max(seen), 0.8)

    def test_native_engine_tilt_lane_swings_the_rings(self):
        e = app.Engine(app.make_params(), mixer=native.NativeMixer(LIB))
        e.params["mto1"].index = e.params["mto1"].values.index("TILT")
        e.params["mdep1"].index = e.params["mdep1"].values.index(100)
        e.params["mrate"].index = e.params["mrate"].values.index(5)
        e.params["msync"].index = 0                                   # free-running at RATE
        e.apply()
        tilts = set()
        for _ in range(60):
            e.mixer.render()
            e.step(1 / 120)
            tilts.add(round(math.degrees(e.rings.tilt)))
        self.assertGreater(max(tilts) - min(tilts), 15)
        state = app.ui_state(e, app.Menu(list(e.params.values())))
        menu = app.Menu(list(e.params.values()))
        for page, title in (("DRUMS", "STOP"), ("WARP", "OFF"), ("MODS", "MODS")):
            menu.page = page
            banner = app.ui_state(e, menu)["banner"]
            self.assertEqual(banner[0], title)
            self.assertIn(title, ui.render(app.ui_state(e, menu), 120, 40))
        menu.page = "DRUMS"
        self.assertEqual(len(app.ui_state(e, menu)["banner"][1]), 16)

    def test_every_exciter_sounds_pitched(self):
        for i, name in enumerate(native.EXCITERS):
            m = self.mixer()
            m.physical["exciter"] = i
            m.physical_hit(57, 1.0)
            left, right = self.run_for(m, 1.0)
            mono = [a + b for a, b in zip(left, right)]
            self.assertGreater(self.rms(mono), 300, name)
            self.assertGreater(self.mag(mono, 220.0), 3 * self.mag(mono, 220.0 * 2 ** (-1 / 12)), name)

    def test_native_random_loop_deja_vu(self):
        from marbles import LoopSequencer
        mk = lambda **kw: LoopSequencer(lambda seed: native.RandomSource(LIB, seed), **kw)
        locked = mk(deja=0.5, length=4, spread=1.0)
        out = [locked.next() for _ in range(24)]
        self.assertTrue(all(out[i] == out[i + 4] for i in range(20)))      # 50%: exact loop
        free = mk(deja=0.0, length=4, spread=1.0)
        out = [free.next() for _ in range(40)]
        self.assertFalse(all(out[i] == out[i + 4] for i in range(36)))
        one = mk(deja=0.0, spread=0.0, bias=1.0)
        self.assertEqual({one.next() for _ in range(30)}, {0})             # spread 0, bias stable: I only
        q = mk()
        q.next()
        q.regenerate()
        self.assertEqual((q.steps, q.current), (0, 0))

    def test_engine_routes_synth_and_physical_hits(self):
        from physics import Hit
        e = app.Engine(app.make_params(), mixer=native.NativeMixer(LIB))
        self.assertIsInstance(e.sequencer, __import__("marbles").LoopSequencer)
        for instr, method in (("SYNTH", "synth_hit"), ("PHYSICAL", "physical_hit")):
            e.params["instr"].index = e.params["instr"].values.index(instr)
            e.apply()
            calls = []
            setattr(e.mixer, method, lambda midi, strength: calls.append(midi))
            e.world.step = lambda dt: [Hit(0, 0, 2.0, 0)]
            e.step(1 / 120)
            self.assertEqual(len(calls), 1, instr)
        e.params["sengine"].index = e.params["sengine"].values.index("STRINGS")
        e.params["tone"].index = e.params["tone"].values.index("CHORDS")
        e.apply()
        self.assertEqual(e.mixer.synth["engine"], native.ENGINES.index("STRINGS"))
        self.assertEqual(e.mixer.ring_synth["engine"], native.ENGINES.index("CHORDS"))

    def test_pages_show_the_voice_controls(self):
        ps = app.make_params()
        by = {p.key: p for p in ps}
        for instr, keys in (("SYNTH", ("sengine", "sharm", "stimb", "smorph", "sdecay", "scolour")),
                            ("PHYSICAL", ("pexc", "pgeo", "pbright", "pdamp", "ppos", "pspace"))):
            by["instr"].index = by["instr"].values.index(instr)
            side, knobs = app.page_params(ps, "BALLS")
            got = {p.key for p in side + knobs}
            self.assertTrue(set(keys) <= got, instr)
            self.assertNotIn("attack", got)
        by["tone"].index = by["tone"].values.index("STRINGS")
        self.assertTrue({"rharm", "rtimb", "rmorph"} <= {p.key for p in sum(app.page_params(ps, "RINGS"), [])})
        by["tone"].index = by["tone"].values.index("WARM")
        self.assertFalse({"rharm"} & {p.key for p in sum(app.page_params(ps, "RINGS"), [])})

    def test_python_engine_can_be_forced(self):
        self.assertEqual(os.environ.get("TOMBOLA_ENGINE"), "python")
        self.assertEqual(app.make_mixer().name, "python")
        os.environ.pop("TOMBOLA_ENGINE")
        try:
            self.assertEqual(app.make_mixer().name, "native")
        finally:
            os.environ["TOMBOLA_ENGINE"] = "python"


class AudioTest(unittest.TestCase):
    def test_mixer_error_is_logged_and_audio_keeps_going(self):
        import audio
        with tempfile.TemporaryDirectory() as d:
            audio.ERROR_LOG = os.path.join(d, "errors.log")
            m = Mixer()
            calls = []
            def flaky(n=BLOCK):
                calls.append(1)
                if len(calls) == 3:
                    raise IndexError("boom")
                return bytes(2 * n)
            m.render = flaky
            out = audio.Output(m, None)
            blocks = [out._render() for _ in range(6)]
            self.assertEqual(len(calls), 6)                        # kept rendering after the error
            self.assertTrue(all(len(b) == 2 * BLOCK for b in blocks))
            self.assertIn("audio error: IndexError", out.error)
            with open(audio.ERROR_LOG) as f:
                self.assertIn("IndexError: boom", f.read())


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


class BackdropTest(unittest.TestCase):
    def _engine(self):
        e = app.Engine(app.make_params())
        e.params["rings"].index = 4
        e.apply()
        return e

    def test_frame_draws_back_to_front(self):
        e = self._engine()
        cmds = backdrop.scene_commands(e.world, e.rings)
        self.assertTrue(cmds[0].startswith("S "))
        self.assertEqual(cmds[-1], "E")
        kinds = [c.split()[0] for c in cmds]
        fill = kinds.index("F")                              # planet fill hides the far ring halves
        self.assertIn("L", kinds[1:fill])                    # far ring halves before it
        self.assertIn("L", kinds[fill + 1:])                 # edges / near halves after it
        for c in cmds[1:-1]:
            nums = c.split()[1:]
            self.assertTrue(all(re.fullmatch(r"-?\d+(\.\d+)?", n) for n in nums), c)

    def test_moons_behind_the_planet_are_not_drawn(self):
        e = self._engine()
        r = e.rings
        r.count, r.moons, r.tilt = 1, 1, 0.0
        r.phase[0] = math.pi                                 # top: the far side, behind the planet
        moon = r.point(0, math.pi)
        cmds = backdrop.scene_commands(e.world, r)
        self.assertFalse(any(c.startswith("C") and f"{moon[0]:.3f} {moon[1]:.3f}" in c for c in cmds))
        r.phase[0] = 0.0                                     # bottom: in front, loudest — amber, not ball-white
        top = r.point(0, 0.0)
        cmds = backdrop.scene_commands(e.world, r)
        moon_cmds = [c for c in cmds if c.startswith("C") and f"{top[0]:.3f} {top[1]:.3f}" in c]
        self.assertTrue(moon_cmds)
        self.assertTrue(all(c.startswith("C 255 175 0") for c in moon_cmds))

    def test_sender_streams_newest_frame_only(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "frames.txt")
            w = backdrop.Backdrop()
            w.proc = subprocess.Popen(["sh", "-c", f"cat > {out}"], stdin=subprocess.PIPE, bufsize=0)
            for n in range(5):                               # burst before the sender runs: only #4 survives
                w.submit([f"S {n} 1", "E"])
            w._thread = threading.Thread(target=w._send_loop, daemon=True)
            w._thread.start()
            time.sleep(0.3)
            w.stop()
            self.assertEqual(open(out).read(), "S 4 1\nE\n")
            w.stop()                                         # idempotent

    def test_window_off_never_starts_powershell(self):
        e = self._engine()
        w = backdrop.Backdrop()
        w.start = lambda: self.fail("started with WINDOW OFF")
        app.sync_window(w, e, None)
        self.assertFalse(w.running)


class AppTest(unittest.TestCase):
    def test_ring_notes_outer_is_bass(self):
        e = app.Engine(app.make_params())
        for n in range(1, 5):
            e.params["rings"].index = n
            e.apply()
            notes = e.ring_notes()
            self.assertEqual(len(notes), n)
            self.assertEqual(notes[-1], min(notes))             # outer ring plays the lowest tone
            self.assertEqual(notes, sorted(notes, reverse=True))

    def test_pad_never_drops_below_audible_range(self):
        e = app.Engine(app.make_params())
        for key in ("C", "F#", "B"):
            for octv in (-2, -1, 0, 1, 2):
                e.params["key"].index = e.params["key"].values.index(key)
                e.params["octave"].index = e.params["octave"].values.index(octv)
                e.params["roct"].index = e.params["roct"].values.index(0)
                for step in range(4):
                    notes = e.chord(step)
                    self.assertGreaterEqual(min(notes), app.PAD_LOWEST, (key, octv, notes))
                    self.assertLess(max(notes) - min(notes), 24)          # one compact voicing

    def test_rings_octave_moves_pad_and_moons_not_the_balls(self):
        e = app.Engine(app.make_params())
        base, moons = e.chord(0), e.ring_notes()
        e.params["roct"].index = e.params["roct"].values.index(2)
        e.apply()
        self.assertEqual(e.chord(0), [m + 24 for m in base])
        self.assertEqual(e.ring_notes(), [m + 24 for m in moons])
        e.params["octave"].index = e.params["octave"].values.index(-2)     # balls' octave
        self.assertEqual(e.chord(0), [m + 24 for m in base])
        menu = app.Menu(list(e.params.values()))
        menu.next_page()
        self.assertIn("roct", [p.key for p in menu.items()])

    def test_rings_octave_goes_down_too(self):
        e = app.Engine(app.make_params())
        base = e.chord(0)
        e.params["roct"].index = e.params["roct"].values.index(-3)
        self.assertEqual(e.chord(0), [m - 36 for m in base])
        self.assertEqual(e.params["roct"].values, tuple(range(-3, 4)))

    def test_harder_hits_are_louder(self):
        from physics import Hit
        e = app.Engine(app.make_params())
        gains = []
        e.mixer.trigger = lambda buf, g, **_: gains.append(g)
        e.world.step = lambda dt, s=iter((0.3, 3.0)): [Hit(0, 0, next(s), 0)]
        e.step(1 / 120)
        e.step(1 / 120)
        self.assertGreater(gains[1], 3 * gains[0])

    def test_ring_gains_follow_the_moons(self):
        e = app.Engine(app.make_params())
        e.params["floor"].index = e.params["floor"].values.index(0)
        e.params["rings"].index = 1
        e.params["moons"].index = 0
        e.apply()
        e.params["tilt"].index = 0                           # level ring: top = pi, bottom = 0
        e.apply()
        e.rings.phase[0] = math.pi                           # highest point: loud
        e.step(1 / 120)
        (_, loud), = e.mixer.ring_targets
        e.rings.phase[0] = 0.0                               # lowest point: silent
        e.step(1 / 120)
        (_, quiet), = e.mixer.ring_targets
        self.assertGreater(loud, 0.95)
        self.assertLess(quiet, 0.05)
        e.params["floor"].index = e.params["floor"].values.index(100)      # FLOOR 100: steady drone
        e.step(1 / 120)
        self.assertEqual(e.mixer.ring_targets[0][1], 1.0)

    def test_rings_count_picks_chord_tones(self):
        e = app.Engine(app.make_params())
        bass, root, third, fifth = e.chord(0)
        expect = {1: [root], 2: [fifth, root], 3: [fifth, third, root], 4: [fifth, third, root, bass]}
        for n, notes in expect.items():
            e.params["rings"].index = e.params["rings"].values.index(n)
            e.apply()
            self.assertEqual(e.ring_notes(), notes)

    def test_held_chord_stays_progression_moves(self):
        e = app.Engine(app.make_params())
        self.assertEqual(e.v("chord"), "I")
        first = e.chord(0)
        for _ in range(120 * 20):
            e.step(1 / 120)
        self.assertEqual(e.chord(e.chord_step), first)       # held: never moves by itself
        e.params["chord"].index = e.params["chord"].values.index("I-IV-V-I")
        e.params["change"].index = e.params["change"].values.index(2)
        e.params["clock"].index = e.params["clock"].values.index("TIME")
        e.apply()
        for _ in range(120 * 5):
            e.step(1 / 120)
        self.assertEqual(e.chord_step, 2)                    # moves every CHANGE seconds

    def test_digits_pick_shape_on_balls_and_chord_on_rings(self):
        ps = app.make_params()
        e = app.Engine(ps)
        menu = app.Menu(ps)
        app.handle_key(("digit", 5), e, menu)
        self.assertEqual(e.world.sides, 5)
        menu.next_page()
        app.handle_key(("digit", 5), e, menu)
        self.assertEqual(e.v("chord"), "V")
        self.assertEqual(e.world.sides, 5)                   # shape untouched on RINGS
        app.handle_key(("digit", 9), e, menu)                # no 9th degree: ignored
        self.assertEqual(e.v("chord"), "V")

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
        self.assertEqual(menu.page, "CHORDS")
        app.handle_key("page", e, menu)
        self.assertEqual(menu.page, "GRAINS")
        for page in ("DRUMS", "WARP", "MODS", "RADIO"):
            app.handle_key("page", e, menu)
            self.assertEqual(menu.page, page)
        app.handle_key("page", e, menu)                     # eight pages, then back round
        self.assertIs(menu.selected(), balls_sel)
        self.assertEqual(app.parse_keys(b"\t"), ["page"])

    def test_undo_brings_back_the_settings_before_r(self):
        e = app.Engine(app.make_params())
        before = app.snapshot(e.params.values())
        rng = random.Random(9)
        e.randomize(rng=rng)
        e.randomize(rng=rng)
        self.assertNotEqual(app.snapshot(e.params.values()), before)
        self.assertTrue(e.undo())
        self.assertTrue(e.undo())
        self.assertEqual(app.snapshot(e.params.values()), before)
        self.assertEqual(e.world.sides, app.SHAPES[before["shape"]])   # applied, not just stored
        self.assertFalse(e.undo())                                       # nothing left: harmless
        self.assertEqual(app.parse_keys(b"u"), ["undo"])

    def test_history_persists_capped_and_validated(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cfg.json")
            e = app.Engine(app.make_params())
            rng = random.Random(3)
            for _ in range(25):
                e.randomize(rng=rng)
            app.save_params(list(e.params.values()), path, e.history)
            loaded = app.load_history(path)
            self.assertEqual(len(loaded), app.HISTORY_MAX)
            self.assertEqual(loaded, e.history)
            with open(path) as f:
                raw = json.load(f)
            raw["history"] = ["junk", {"grav": 9999, "key": "C"}, 5]
            with open(path, "w") as f:
                json.dump(raw, f)
            e2 = app.Engine(app.load_params(path))
            e2.history = app.load_history(path)
            self.assertEqual(len(e2.history), 1)
            g = e2.v("grav")
            e2.undo()
            self.assertEqual((e2.v("key"), e2.v("grav")), ("C", g))          # bad value skipped

    def test_instr_hotkey_follows_the_page(self):
        ps = app.make_params()
        e = app.Engine(ps)
        menu = app.Menu(ps)
        before_tone = e.v("tone")
        app.handle_key(("instr", 1), e, menu)               # BALLS page: ball instrument
        self.assertEqual(e.v("instr"), "BELL")
        self.assertEqual(e.v("tone"), before_tone)
        menu.next_page()
        app.handle_key(("instr", 1), e, menu)               # RINGS page: ring timbre
        self.assertEqual(e.v("tone"), "GLASS")
        self.assertEqual(menu.selected().key, "tone")
        self.assertEqual(e.v("instr"), "BELL")
        ring_labels = [p.label for p in app.page_params(ps, "RINGS")[0]]
        self.assertIn("INSTR", ring_labels)

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

    def test_old_value_names_migrate(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cfg.json")
            with open(path, "w") as f:
                json.dump({"version": 2, "instr": "RINGS", "chord": "MARBLES", "grav": 15,
                           "history": [{"instr": "RINGS", "chord": "MARBLES"}]}, f)
            loaded = {p.key: p.value for p in app.load_params(path)}
            self.assertEqual((loaded["instr"], loaded["chord"], loaded["grav"]), ("RESONATOR", "RANDOM", 15))
            e = app.Engine(app.load_params(path))
            e.history = app.load_history(path)
            e.params["instr"].index = 0
            e.undo()                                             # history entries migrate too
            self.assertEqual((e.v("instr"), e.v("chord")), ("RESONATOR", "RANDOM"))

    def test_every_setting_has_plain_help(self):
        import explain
        for p in app.make_params():
            self.assertIn(p.key, explain.HELP, p.key)
            for i in range(len(p.values)):
                p.index = i
                long, short = explain.explain(p)
                self.assertLessEqual(len(short), 72, (p.key, p.value, short))
        self.assertLessEqual(set(explain.VALUES), {p.key for p in app.make_params()})

    def test_question_mark_shows_help_for_the_selected_setting(self):
        e = app.Engine(app.make_params())
        menu = app.Menu(list(e.params.values()))
        self.assertEqual(app.parse_keys(b"?"), ["help"])
        self.assertIsNone(app.ui_state(e, menu)["help"])
        app.handle_key("help", e, menu)
        strip = lambda t: re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", t)
        for cols, rows in ((120, 40), (60, 6)):
            frame = strip(ui.render(app.ui_state(e, menu), cols, rows))
            self.assertIn("BALLS:", frame)                               # first knob: BALLS
            self.assertIn("How many balls", frame)
        app.handle_key("right", e, menu)
        self.assertIn("Gravity", app.ui_state(e, menu)["help"][0])       # follows the cursor
        app.handle_key("help", e, menu)
        self.assertIsNone(app.ui_state(e, menu)["help"])

    def test_digits_pick_mods_movers_and_warp_carriers(self):
        e = app.Engine(app.make_params())
        menu = app.Menu(list(e.params.values()))
        menu.page = "MODS"
        for n in (3, 1, 4):
            app.handle_key(("digit", n), e, menu)
            self.assertEqual(menu.selected().key, f"mto{n}")
        app.handle_key(("digit", 7), e, menu)                            # no mover 7: stays put
        self.assertEqual(menu.selected().key, "mto4")
        menu.page = "WARP"
        for n, carrier in ((3, "SINE"), (2, "DRUMS"), (5, "SAW")):
            app.handle_key(("digit", n), e, menu)
            self.assertEqual(e.v("wcar"), carrier)

    def test_arpeggiator_port_walks_like_the_original(self):
        from arp import Arpeggiator
        def walk(mode, n=9, notes=3, range_=2):
            a = Arpeggiator(mode, range_)
            out = []
            for _ in range(n):
                a.clock(notes)
                out.append((a.octave, a.note))
            return out
        self.assertEqual(walk("UP", 7), [(0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (0, 0), (0, 1)])
        self.assertEqual(walk("DOWN", 4), [(1, 2), (1, 1), (1, 0), (0, 2)])
        up_down = walk("UP-DN", 12)
        self.assertIn((1, 2), up_down)
        self.assertEqual(up_down[up_down.index((1, 2)) + 1], (1, 1))      # turns round at the top
        rnd = walk("RANDOM", 50)
        self.assertTrue(all(a != b for a, b in zip(rnd, rnd[1:])))       # never the same twice in a row

    def test_arp_plays_the_chord_on_every_instrument(self):
        e = app.Engine(app.make_params())
        e.params["arp"].index = e.params["arp"].values.index("UP")
        e.params["chord"].index = e.params["chord"].values.index("IV")
        e.apply()
        k, sc = e.v("key"), e.v("scale")
        tones = {pad_note(k, sc, 3 + 2 * i, o) % 12 for i in range(4) for o in (0, 1)}
        notes = [e.hit_midi(edge, 0) for edge in (0, 0, 5, 1, 2, 0, 3, 4)]
        self.assertEqual({n % 12 for n in notes}, tones)                 # walls don't matter, the chord does
        self.assertLessEqual(set(notes), set(e.playable_notes()))
        e.params["arp"].index = 0
        self.assertEqual(e.hit_midi(2, 0), pad_note(k, sc, 2, e.v("octave")))

    def test_python_engine_ignores_native_only_pages(self):
        e = app.Engine(app.make_params())
        menu = app.Menu(list(e.params.values()))
        e.params["mto1"].index = e.params["mto1"].values.index("TILT")
        e.params["on_drums"].index = 1
        e.apply()
        self.assertEqual(e.tilt_lanes, [])
        for page in ("DRUMS", "MODS"):
            menu.page = page
            self.assertIn("48K ONLY", app.ui_state(e, menu)["banner"][0])
        e.step(0.1)

    def test_radio_searches_downloads_decodes_and_moves_on(self):
        import io
        import radio as radio_mod
        calls = []

        class Proc:
            def __init__(self, args, **kw):
                calls.append(args)
                if args[0] == "yt-dlp" and "--flat-playlist" in args:
                    out = b"id1|First song|3:00\nid2|Second song|2:00\n"
                elif args[0] == "yt-dlp":
                    path = os.path.join(args[args.index("-o") + 1].rsplit("/", 1)[0], args[-1][-3:] + ".m4a")
                    open(path, "wb").close()
                    out = path.encode() + b"\n"
                else:
                    out = b"\x01\x00\x02\x00" * 4800                       # 0.1 s of PCM
                self.stdout = io.BytesIO(out)
            def communicate(self):
                return self.stdout.read(), b""
            def poll(self):
                return 0
            def kill(self):
                pass

        class Mixer:
            def __init__(self):
                self.got = b""
            def radio_push(self, pcm):
                self.got += pcm
                return len(pcm)
            def radio_clear(self):
                pass
            def radio_queued(self):
                return 0.0

        mixer = Mixer()
        r = radio_mod.Radio(mixer, run=Proc, which=lambda tool: "/bin/" + tool)
        self.assertEqual(r.missing(), "")
        r.search("lofi", "YOUTUBE")
        deadline = time.time() + 5
        while len([c for c in calls if c[0] == "ffmpeg"]) < 2 and time.time() < deadline:
            time.sleep(0.01)                                             # first track ends: on to the second
        r.stop()
        self.assertEqual(calls[0][-1], "ytsearch10:lofi")
        self.assertIn("https://www.youtube.com/watch?v=id1", calls[1])
        self.assertIn("-ar", calls[2])
        self.assertGreaterEqual(len(mixer.got), 4 * 4800)
        self.assertEqual([t for _, t, _ in r.results], ["First song", "Second song"])
        self.assertEqual(r.status, "IDLE")
        r.close()
        self.assertIn("NEEDS", radio_mod.Radio(mixer, which=lambda tool: None).missing())
        self.assertEqual(radio_mod.Radio(object()).missing(), "48K ONLY")

    def radio_engine(self):
        """An engine on the RADIO page whose radio records what it's asked to do."""
        e = app.Engine(app.make_params())
        menu = app.Menu(list(e.params.values()))
        menu.page = "RADIO"
        calls = []
        r = e.radio
        r.results = [("a", "One", "3:00"), ("b", "Two", "2:00"), ("c", "Three", "1:00")]
        for name in ("seek", "pause", "skip", "play"):
            setattr(r, name, lambda *a, name=name: calls.append((name, *a)))
        e.search_radio = lambda q: calls.append(("search", q))
        return e, menu, calls

    def test_radio_page_has_plays_player_keys(self):
        e, menu, calls = self.radio_engine()
        vol = e.v("radvol")
        app.handle_radio_input(b"\x1b[C\x1b[D\x1b[A\x1b[A\x1b[B np", e, menu)
        self.assertEqual(calls, [("seek", 10), ("seek", -10), ("play",), ("skip", 1), ("skip", -1)])   # space: start
        self.assertEqual(e.v("radvol"), vol + 5)                         # up up down: +5
        self.assertTrue(e.tab_on("RADIO"))                                # skipping switched it on
        e.radio.status = "PLAYING"
        app.handle_radio_input(b" ", e, menu)
        self.assertEqual(calls[-1], ("pause",))
        source = e.v("rsrc")
        app.handle_radio_input(b"s", e, menu)                            # s: next source
        self.assertNotEqual(e.v("rsrc"), source)
        app.handle_radio_input(b"\t", e, menu)                           # tab still changes page
        self.assertEqual(menu.page, "BALLS")

    def test_radio_list_navigates_searches_and_plays(self):
        e, menu, calls = self.radio_engine()
        e.radio.index = 1
        app.handle_radio_input(b"l", e, menu)
        self.assertEqual(menu.radio_list, {"sel": 1, "sbuf": ""})         # opens on the playing track
        app.handle_radio_input(b"\x1b[B\x1b[B\x1b[A", e, menu)
        self.assertEqual(menu.radio_list["sel"], 1)                        # down, down (stops at 2), up
        app.handle_radio_input(b"\r", e, menu)
        self.assertEqual(calls[-1], ("play", 1))
        self.assertIsNone(menu.radio_list)                                 # Enter plays and closes
        app.handle_radio_input(b"/queenx\x7f\r", e, menu)                  # q types a q, backspace edits
        self.assertEqual(calls[-1], ("search", "queen"))
        self.assertEqual(menu.radio_list, {"sel": 0, "sbuf": ""})          # list stays open for the results
        app.handle_radio_input(b"\x1b", e, menu)
        self.assertIsNone(menu.radio_list)
        view = app.ui_state(e, menu)["radio"]
        self.assertEqual(view["tracks"][2], ("Three", "1:00"))
        frame = ui.render(app.ui_state(e, menu), 120, 40)
        self.assertIn("Three", frame)
        self.assertIn("48K ONLY", frame)                                  # the Python engine has no radio

    def test_radio_seek_and_pause_on_a_track(self):
        import radio as radio_mod

        class Mixer:
            radio_paused = False
            def radio_push(self, pcm):
                return len(pcm)
            def radio_clear(self):
                pass
            def radio_queued(self):
                return 0.0
        started = []
        r = radio_mod.Radio(Mixer(), which=lambda tool: "/bin/" + tool)
        r._decode = lambda gen, path, start, index: started.append(start)
        r.results, r.path, r.status, r.played = [("a", "One", "3:00")], "/x.m4a", "PLAYING", 192000 * 60
        r.seek(10)
        time.sleep(0.05)
        self.assertAlmostEqual(started[-1], 70.0)
        r.status, r.start, r.played = "PLAYING", 170.0, 0
        r.seek(30)
        time.sleep(0.05)
        self.assertAlmostEqual(started[-1], 179.0)                         # never past the end
        r.pause()
        self.assertEqual(r.status, "PAUSED")
        self.assertTrue(r.mixer.radio_paused)
        r.pause()
        self.assertFalse(r.mixer.radio_paused)
        self.assertEqual(radio_mod.seconds("1:02:03"), 3723)
        self.assertIn("KEYS", radio_mod.Radio(Mixer(), which=lambda t: t, keys=lambda: ("", "")).missing("SPOTIFY"))

    def test_o_switches_the_tab_and_undo_leaves_it(self):
        e = app.Engine(app.make_params())
        menu = app.Menu(list(e.params.values()))
        self.assertEqual(app.parse_keys(b"o"), ["tab"])
        self.assertTrue(e.tab_on("BALLS"))
        app.handle_key("tab", e, menu)
        self.assertFalse(e.tab_on("BALLS"))
        self.assertIn("BALLS", app.ui_state(e, menu)["off_pages"])
        self.assertIn("\x1b[9mBALLS", ui.render(app.ui_state(e, menu), 120, 40))   # struck through
        hits = e.hits
        for _ in range(600):
            e.step(1 / 120)
        self.assertEqual(e.hits, hits)                                  # balls bounce, silently
        e.randomize("BALLS", random.Random(1))
        app.handle_key("tab", e, menu)                                   # back on, then undo the roll
        e.undo()
        self.assertTrue(e.tab_on("BALLS"))

    def test_old_drum_settings_migrate_to_the_clock_and_switch(self):
        ps = app.make_params()
        app.apply_snapshot(ps, {"dbpm": 140, "dplay": "ON"})
        values = {p.key: p.value for p in ps}
        self.assertEqual((values["bpm"], values["on_drums"]), (140, "ON"))
        app.apply_snapshot(ps, {"dbpm": 60, "bpm": 100})                 # the new key wins
        self.assertEqual(next(p.value for p in ps if p.key == "bpm"), 100)

    def test_native_only_voices_have_python_stand_ins(self):
        e = app.Engine(app.make_params())                      # Python engine (forced in this module)
        for instr in ("SYNTH", "PHYSICAL"):
            e.params["instr"].index = e.params["instr"].values.index(instr)
            e.params["tone"].index = e.params["tone"].values.index("STRINGS")
            e.apply()
            self.assertIn(e.note_args(60)[1], ("BELL", "MARIMBA"))
            self.assertEqual(e.mixer.ring_timbre, "WARM")
            for _ in range(10):
                e.mixer.render()

    def test_param_nudge_clamps(self):
        p = next(p for p in app.make_params() if p.key == "octave")
        for _ in range(10):
            p.nudge(1)
        self.assertEqual(p.value, 2)

    def test_parse_keys(self):
        self.assertEqual(app.parse_keys(b"\x1b[C\x1b[Akq x"),
                         ["right", "up", "up", "quit", "toggle", "hold"])

    def test_render_frame_both_pages(self):
        ps = app.make_params()
        e = app.Engine(ps)
        menu = app.Menu(ps)
        visual = e.params["visual"]
        octants = set(ui.OCTANT[1:]) - {"█"}
        page_labels = {"BALLS": ("GRAV", "BOUNCE", "SHAPE"), "RINGS": ("RINGS", "MOONS", "ORBIT", "PAD", "FLOOR", "BPM", "CLK"),
                       "GRAINS": ("DENSITY", "SIZE", "POSITION", "TEXTURE", "FEEDBACK", "SOURCE"),
                       "CHORDS": ("CLOCK", "CHANCE", "LENGTH", "DEJA VU", "BIAS"),
                       "DRUMS": ("BPM", "MAP X", "CHAOS", "KICK", "HATS"), "WARP": ("CARRIER", "ALGO", "MIX"),
                       "MODS": ("CYCLE", "SHAPE", "SHIFT", "TO 1", "DEPTH", "SYNC"), "RADIO": ("VOL", "YOUTUBE")}
        for mode, is_glyph in (("PIXEL", lambda c: c in "▀▄█"),
                               ("BEHIND", lambda c: c in "▀▄█"),
                               ("BLOCK", lambda c: c in octants),
                               ("BRAILLE", lambda c: 0x2800 < ord(c) <= 0x28FF)):
            visual.index = visual.values.index(mode)
            for page in menu.pages:
                menu.page = page
                for cols, rows in ((120, 40), (60, 16)):
                    frame = ui.render(app.ui_state(e, menu), cols, rows)
                    if not (page == "RADIO" and mode != "BEHIND"):   # RADIO: the player, not the scene
                        self.assertTrue(any(map(is_glyph, frame)), (mode, page, cols))
                    for label in (("SCALE",) if page != "RADIO" else ()) + (page, *page_labels[page]):
                        self.assertIn(label, frame, (page, cols))
                    if cols >= 100:                            # narrow strips drop the title, then other tabs
                        for label in ("HITS", "TOMBOLA", *menu.pages):
                            self.assertIn(label, frame)
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

    def test_respawn_randomizes_ball_settings_but_not_volume_or_shared(self):
        e = app.Engine(app.make_params())
        e.params["vol"].index = 3
        shared = {k: e.v(k) for k in app.SHARED}
        seen = set()
        rng = random.Random(5)
        for _ in range(20):
            e.randomize("BALLS", rng)
            seen.add(tuple(e.v(k) for k in ("instr", "shape", "balls", "grav")))
            self.assertEqual(e.v("vol"), 15)
            self.assertEqual({k: e.v(k) for k in shared}, shared)       # key, scale, reverb, ...
            self.assertTrue(2 <= e.v("balls") <= 6 and 60 <= e.v("bounce") <= 110)
            self.assertEqual(len(e.world.balls), e.v("balls"))
            self.assertEqual(e.world.sides, app.SHAPES[e.v("shape")])
        self.assertGreater(len(seen), 15)

    def test_random_only_touches_the_current_page(self):
        for page in (p for p in app.PAGES if app.random_keys(p)):    # RADIO has nothing to roll
            e = app.Engine(app.make_params())
            own = set(app.random_keys(page))
            self.assertTrue(own)
            self.assertFalse(own & app.SHARED)
            before = app.snapshot(e.params.values())
            balls = list(e.world.balls)
            rng = random.Random(11)
            for _ in range(8):
                e.randomize(page, rng)
            after = app.snapshot(e.params.values())
            changed = {k for k in before if before[k] != after[k]}
            self.assertTrue(changed, page)
            self.assertLessEqual(changed, own, page)                     # nothing from other pages
            if page != "BALLS":
                self.assertEqual(e.world.balls, balls)                   # balls keep bouncing
            self.assertTrue(e.undo())

    def test_r_key_rolls_the_page_shown(self):
        e = app.Engine(app.make_params())
        menu = app.Menu(list(e.params.values()))
        while menu.page != "GRAINS":
            app.handle_key("page", e, menu)
        before = app.snapshot(e.params.values())
        app.handle_key("respawn", e, menu)
        after = app.snapshot(e.params.values())
        self.assertLessEqual({k for k in before if before[k] != after[k]}, set(app.random_keys("GRAINS")))

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
        # far side (the upper half) hidden: no ring pixels in the polygon's upper interior, centre column
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
        m.cloud.count = 50
        fx.update(m, 0.0)
        self.assertEqual(len(fx.sparks), ui.Effects.MAX_NEW_SPARKS)
        self.assertTrue(all(math.hypot(x, y) <= 0.75 for x, y, _ in fx.sparks))
        fx.update(m, 0.1)
        self.assertEqual(len(fx.sparks), ui.Effects.MAX_NEW_SPARKS)   # no new grains, still alive
        fx.update(m, 0.5)
        self.assertEqual(fx.sparks, [])

    def test_hotkeys(self):
        self.assertEqual(app.parse_keys(b"3s8ncI"),
                         [("digit", 3), ("shape", 1), ("digit", 8),
                          ("key", 1), ("scale", 1), ("instr", -1)])
        shapes = next(p for p in app.make_params() if p.key == "shape").values
        self.assertEqual(shapes[0], "TRI")
        self.assertEqual(shapes[5], "OCT")


class PresetTest(unittest.TestCase):
    def engine(self, mixer=None):
        e = app.Engine(app.make_params(), mixer=mixer)
        e.preset_dir = tempfile.mkdtemp()
        return e

    def press(self, e, m, *keys):
        for k in keys:
            app.handle_key(k, e, m)

    def test_save_then_load_brings_settings_and_loop_back(self):
        e = self.engine()
        m = app.Menu(list(e.params.values()))
        e.params["scale"].nudge(2)
        e.params["bpm"].nudge(5)
        scale, bpm, seed, loop = e.v("scale"), e.v("bpm"), e.loop_seed, list(e.sequencer.loop)
        self.press(e, m, "preset_save", ("digit", 3))
        self.assertEqual(app.preset_prompt(e, m), "SAVED PRESET 3")
        e.params["scale"].nudge(1)
        e.params["bpm"].nudge(-3)
        e.regenerate()
        self.assertNotEqual(e.loop_seed, seed)
        self.press(e, m, "preset_load", ("digit", 3))
        self.assertEqual((e.v("scale"), e.v("bpm"), e.loop_seed), (scale, bpm, seed))
        self.assertEqual(e.sequencer.loop, loop)

    def test_load_is_undoable_seed_included(self):
        e = self.engine()
        m = app.Menu(list(e.params.values()))
        self.press(e, m, "preset_save", ("digit", 1))
        e.params["key"].nudge(1)
        e.regenerate()
        key, seed = e.v("key"), e.loop_seed
        self.press(e, m, "preset_load", ("digit", 1))
        self.assertNotEqual(e.v("key"), key)
        self.assertTrue(e.undo())
        self.assertEqual((e.v("key"), e.loop_seed), (key, seed))

    def test_other_key_cancels_and_empty_slot_says_so(self):
        e = self.engine()
        m = app.Menu(list(e.params.values()))
        page = m.page
        self.press(e, m, "preset_save")
        self.assertIn("SAVE PRESET", app.preset_prompt(e, m))
        self.press(e, m, "page")                   # cancels, doesn't switch page
        self.assertEqual((m.page, m.pending, app.preset_prompt(e, m)), (page, None, "CANCELLED"))
        self.assertEqual(os.listdir(e.preset_dir), [])
        self.press(e, m, "preset_load")
        self.assertIn("NO PRESETS", app.preset_prompt(e, m))
        self.press(e, m, ("digit", 7))
        self.assertEqual(app.preset_prompt(e, m), "PRESET 7 IS EMPTY")
        self.assertEqual(e.history, [])

    def test_keys_reach_presets_on_every_page(self):
        self.assertEqual(app.parse_keys(b"P4L4"), ["preset_save", ("digit", 4), "preset_load", ("digit", 4)])
        e = self.engine()
        m = app.Menu(list(e.params.values()))
        m.page = "RADIO"
        app.handle_radio_input(b"P2", e, m)        # P, then 2: not RADIO's player keys
        self.assertEqual(app.presets.used(e.preset_dir), [2])
        app.handle_radio_input(b"Ln", e, m)        # n cancels the load, doesn't skip a track
        self.assertIsNone(m.pending)

    def test_window_is_not_part_of_a_preset(self):
        e = self.engine()
        e.save_preset(5)
        with open(app.presets.path(5, e.preset_dir)) as f:
            saved = json.load(f)
        self.assertNotIn("window", saved)
        self.assertEqual(saved["loop_seed"], e.loop_seed)

    def test_bad_preset_files_are_ignored(self):
        d = tempfile.mkdtemp()
        with open(app.presets.path(1, d), "w") as f:
            f.write("{broken")
        with open(app.presets.path(2, d), "w") as f:
            json.dump({"scale": "NOPE", "loop_seed": "x", "radio_query": 5}, f)
        self.assertIsNone(app.presets.load(1, d))
        self.assertEqual(app.presets.load(2, d), ({"scale": "NOPE"}, None, ""))

    def test_same_seed_same_chords(self):
        a, b = ChordSequencer(), ChordSequencer()
        a.reseed(42)
        b.reseed(42)
        for _ in range(40):
            self.assertEqual(a.next(), b.next())
        b.reseed(43)
        a.reseed(42)
        self.assertNotEqual([a.next() for _ in range(16)], [b.next() for _ in range(16)])

    def test_seed_survives_a_restart(self):
        path = os.path.join(tempfile.mkdtemp(), "t.json")
        e = self.engine()
        app.save_params(e.params.values(), path, loop_seed=e.loop_seed)
        self.assertEqual(app.load_seed(path), e.loop_seed)
        app.save_params(e.params.values(), path)
        self.assertIsNone(app.load_seed(path))

    @unittest.skipUnless(LIB, "native core not built (cd native && make)")
    def test_same_seed_same_chords_native(self):
        runs = []
        for _ in range(2):
            e = self.engine(native.NativeMixer(LIB))
            e.reseed_loop(1234)
            runs.append([e.sequencer.next() for _ in range(24)])
            e.close()
        self.assertEqual(runs[0], runs[1])


class WindowLayoutTest(unittest.TestCase):
    def test_window_on_every_tab_gets_knobs_and_no_second_scene(self):
        ps = app.make_params()
        e, m = app.Engine(ps), app.Menu(ps)
        for window in (True, False):
            for page in m.pages:
                m.page = page
                st = app.ui_state(e, m)
                st["window"] = window
                plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", ui.render(st, 186, 15))
                if window:
                    self.assertTrue(ui.knob_layout_fits(st, 186, 15), page)
                    self.assertGreaterEqual(plain.count("╭"), len(st["side"] + st["knobs"]), page)
                    if page != "MODS":                   # MODS' mover bars use block glyphs
                        self.assertNotRegex(plain, "[▀▄█]", page)
            m.page = "BALLS"
            st = app.ui_state(e, m)
            st["window"] = False
            self.assertFalse(ui.knob_layout_fits(st, 186, 15))   # scene shown here: needs its rows


if __name__ == "__main__":
    unittest.main()
