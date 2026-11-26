"""Saturn-style rings around the tombola: each ring is one note of the chord.

Ring k (0 = innermost) is an ellipse around the polygon, tilted, seen from
slightly above: its lower half passes in front of the polygon, its upper half
(the far side) behind it. Each ring sounds its chord note continuously; its
moons are the volume envelope by height on screen — loud at the ring's highest
point, silent at its lowest — so TILT reshapes the swell along with the view.

Units match physics.py: the polygon's circumradius is 1, y points up.
"""
import math

INNER, GAP, SQUASH = 1.35, 0.30, 0.36
MAX_RINGS = 4
RATIOS = {                       # orbit speed of each ring relative to the innermost
    "KEPLER": None,              # real orbits: omega ~ r^-1.5
    "HARM": (1, 1 / 2, 1 / 3, 1 / 4),
    "PRIME": (1, 2 / 3, 2 / 5, 2 / 7),
    "GOLD": (1, 0.618, 0.382, 0.236),
    "SYNC": (1, 1, 1, 1),
}
TAU = 2 * math.pi
# SYNC: each ring's orbit as a multiple of the innermost one's (in bars), per RATIO, so every
# moon reaches the top on the clock's grid.
SYNC_MULTIPLES = {"KEPLER": (1, 1.5, 2, 3), "HARM": (1, 2, 3, 4), "PRIME": (1, 1.5, 2.5, 3.5),
                  "GOLD": (1, 1.5, 2.5, 4), "SYNC": (1, 1, 1, 1)}
PULL = 0.02                      # per step: how fast a synced ring drifts back onto the grid


class Rings:
    def __init__(self):
        self.count = 3
        self.moons = 1
        self.speed = 1.0               # rad/s of the innermost ring
        self.ratio = "KEPLER"
        self.tilt = math.radians(12)
        self.phase = [0.7 * k for k in range(MAX_RINGS)]   # staggered so they don't all start together
        self.time = 0.0

    @staticmethod
    def radius(k):
        return INNER + GAP * k

    def omega(self, k):
        factors = RATIOS[self.ratio]
        f = (INNER / self.radius(k)) ** 1.5 if factors is None else factors[k]
        return self.speed * f

    def moon_angles(self, k):
        return [self.phase[k] + TAU * m / self.moons for m in range(self.moons)]

    def step(self, dt):
        self.time += dt
        for k in range(self.count):
            self.phase[k] += self.omega(k) * dt

    def step_synced(self, dt, dbeats, beats, orbit_beats):
        """SYNC: ring k orbits once per orbit_beats * its multiple (beats of the rings' clock),
        moving with the clock and pulled gently onto it, so changing BPM or ORBIT never jumps."""
        self.time += dt
        top = self.top_angle()
        for k in range(self.count):
            period = orbit_beats * SYNC_MULTIPLES[self.ratio][k]
            target = top + TAU * beats / period                 # moon 0 at the top on each period
            error = (target - self.phase[k] + math.pi) % TAU - math.pi
            self.phase[k] += TAU * dbeats / period + PULL * error

    def top_angle(self):
        """Orbit angle of every ring's highest point on screen (all rings share the shape)."""
        return math.atan2(SQUASH * math.cos(self.tilt), math.sin(self.tilt)) + math.pi / 2

    def height(self, k):
        """Half the on-screen height of ring k (its tilted ellipse)."""
        rx = self.radius(k)
        return math.hypot(rx * math.sin(self.tilt), rx * SQUASH * math.cos(self.tilt))

    def moon_level(self, k, angle):
        """Envelope of a moon on ring k: 1 at the ring's highest point on screen, 0 at its lowest."""
        y = self.point(k, angle)[1]
        return ((y / self.height(k) + 1) / 2) ** 3    # steep: the lower half of the orbit drops out

    def level(self, k):
        """Ring k's envelope: its loudest moon."""
        return max(self.moon_level(k, a) for a in self.moon_angles(k))

    def point(self, k, angle):
        """World position of angle on ring k; angle 0 is the front centre (bottom), pi the far side."""
        rx = self.radius(k)
        ex, ey = rx * math.sin(angle), -rx * SQUASH * math.cos(angle)
        c, s = math.cos(self.tilt), math.sin(self.tilt)
        return ex * c - ey * s, ex * s + ey * c

    @staticmethod
    def in_front(angle):
        """Lower half passes in front of the polygon; the upper (far) half goes behind it."""
        return math.cos(angle) > 0

    def extent(self):
        """Half-width and half-height (world units) that the rings need on screen."""
        if not self.count:
            return 1.0, 1.0
        rx = self.radius(self.count - 1)
        ry = rx * SQUASH
        c, s = math.cos(self.tilt), math.sin(self.tilt)
        return (max(1.0, math.hypot(rx * c, ry * s)),       # exact half-extents of a rotated ellipse
                max(1.0, math.hypot(rx * s, ry * c)))
