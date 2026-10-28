"""Saturn-style rings around the tombola, with moons that play the chord.

Ring k (0 = innermost) is an ellipse around the polygon, tilted, seen from
slightly above: its lower half passes in front of the polygon, its upper half
behind it. Each moon on a ring plays that ring's chord note when it passes the
gate, the front-centre point under the polygon.

Units match physics.py: the polygon's circumradius is 1, y points up.
"""
import math

INNER, GAP, SQUASH = 1.35, 0.27, 0.30
MAX_RINGS = 4
RATIOS = {                       # orbit speed of each ring relative to the innermost
    "KEPLER": None,              # real orbits: omega ~ r^-1.5
    "HARM": (1, 1 / 2, 1 / 3, 1 / 4),
    "PRIME": (1, 2 / 3, 2 / 5, 2 / 7),
    "GOLD": (1, 0.618, 0.382, 0.236),
    "SYNC": (1, 1, 1, 1),
}
TAU = 2 * math.pi


class Rings:
    def __init__(self):
        self.count = 3
        self.moons = 1
        self.speed = 1.0               # rad/s of the innermost ring
        self.ratio = "KEPLER"
        self.tilt = math.radians(12)
        self.phase = [0.7 * k for k in range(MAX_RINGS)]   # staggered so they don't all start together
        self.time = 0.0
        self.flash = {}                # ring -> time of its last ping

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
        """Advance the orbits; returns the rings whose moons crossed the gate."""
        self.time += dt
        pings = []
        for k in range(self.count):
            before = [a // TAU for a in self.moon_angles(k)]
            self.phase[k] += self.omega(k) * dt
            crossed = sum(a // TAU != b for a, b in zip(self.moon_angles(k), before))
            if crossed:
                self.flash[k] = self.time
                pings.extend([k] * crossed)
        return pings

    def point(self, k, angle):
        """World position of angle on ring k; angle 0 is the gate (front centre)."""
        rx = self.radius(k)
        ex, ey = rx * math.sin(angle), -rx * SQUASH * math.cos(angle)
        c, s = math.cos(self.tilt), math.sin(self.tilt)
        return ex * c - ey * s, ex * s + ey * c

    @staticmethod
    def in_front(angle):
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
