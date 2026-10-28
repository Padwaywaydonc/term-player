"""Balls under gravity inside a spinning regular polygon.

Units: the polygon's circumradius is 1. Each edge is a pad; a ball hitting an
edge hard enough produces a Hit, which the app turns into a note.
"""
import math
import random
from dataclasses import dataclass

BALL_SIZES = ((0.035, 1), (0.045, 0), (0.06, -1))   # (radius, octave offset): small = high
HIT_SPEED = 0.18        # minimum normal impact speed that plays a note
HIT_COOLDOWN = 0.06     # seconds before the same ball can retrigger the same pad
MAX_SPEED = 6.0
FRICTION = 0.04         # tangential grip: lets the spinning walls drag balls along
AIR = 0.9995


@dataclass
class Ball:
    x: float
    y: float
    vx: float
    vy: float
    r: float
    octave: int
    cool: dict


@dataclass
class Hit:
    ball: int
    edge: int
    speed: float
    octave: int


def make_ball(i):
    r, octave = BALL_SIZES[i % len(BALL_SIZES)]
    a = random.uniform(0, 2 * math.pi)
    d = random.uniform(0, 0.3)
    return Ball(d * math.cos(a), d * math.sin(a), random.uniform(-0.6, 0.6),
                random.uniform(-0.3, 0.3), r, octave, {})


class World:
    def __init__(self, sides=6, balls=4):
        self.sides = sides
        self.angle = 0.0
        self.spin = 0.6         # rad/s
        self.gravity = 1.2      # R/s^2, pulls toward screen bottom (-y)
        self.bounce = 0.86
        self.time = 0.0
        self.balls = [make_ball(i) for i in range(balls)]
        self.flash = {}         # edge -> time of last hit, for the visuals

    def set_ball_count(self, n):
        while len(self.balls) < n:
            self.balls.append(make_ball(len(self.balls)))
        del self.balls[n:]

    def respawn(self):
        self.balls = [make_ball(i) for i in range(len(self.balls))]

    def vertices(self):
        n = self.sides
        return [(math.cos(self.angle + 2 * math.pi * k / n),
                 math.sin(self.angle + 2 * math.pi * k / n)) for k in range(n)]

    def step(self, dt):
        """Advance by dt seconds; returns the Hits that happened."""
        fastest = max((math.hypot(b.vx, b.vy) for b in self.balls), default=0)
        sub = max(1, math.ceil(fastest * dt / 0.02))
        hits = []
        h = dt / sub
        for _ in range(sub):
            self._substep(h, hits)
        return hits

    def _substep(self, h, hits):
        self.time += h
        self.angle = (self.angle + self.spin * h) % (2 * math.pi)
        n = self.sides
        apothem = math.cos(math.pi / n)
        for i, b in enumerate(self.balls):
            b.vy -= self.gravity * h
            b.vx *= AIR
            b.vy *= AIR
            b.x += b.vx * h
            b.y += b.vy * h
            for k in range(n):
                m = self.angle + 2 * math.pi * (k + 0.5) / n
                cx, cy = math.cos(m), math.sin(m)          # outward normal of edge k
                d = apothem - (b.x * cx + b.y * cy)        # distance inside the edge
                if d >= b.r:
                    continue
                # Rigid rotation: wall velocity at the contact point = spin x r
                px, py = b.x + cx * d, b.y + cy * d
                wx, wy = -self.spin * py, self.spin * px
                rx, ry = b.vx - wx, b.vy - wy
                vn = rx * -cx + ry * -cy                   # along the inward normal
                if vn < 0:
                    tx, ty = rx - vn * -cx, ry - vn * -cy  # tangential part
                    nx, ny = -self.bounce * vn * -cx, -self.bounce * vn * -cy
                    b.vx = nx + tx * (1 - FRICTION) + wx
                    b.vy = ny + ty * (1 - FRICTION) + wy
                    if -vn > HIT_SPEED and self.time - b.cool.get(k, -1) > HIT_COOLDOWN:
                        b.cool[k] = self.time
                        self.flash[k] = self.time
                        hits.append(Hit(i, k, -vn, b.octave))
                b.x -= cx * (b.r - d)
                b.y -= cy * (b.r - d)
            sp = math.hypot(b.vx, b.vy)
            if sp > MAX_SPEED:
                b.vx *= MAX_SPEED / sp
                b.vy *= MAX_SPEED / sp
            if math.hypot(b.x, b.y) > 1.0:                 # tunnelled out: put it back
                b.x, b.y, b.vx, b.vy = b.x * 0.5, b.y * 0.5, 0.0, 0.0
        self._collide_balls()

    def _collide_balls(self):
        bs = self.balls
        for i in range(len(bs)):
            a = bs[i]
            for j in range(i + 1, len(bs)):
                c = bs[j]
                dx, dy = c.x - a.x, c.y - a.y
                dist = math.hypot(dx, dy)
                gap = a.r + c.r - dist
                if gap <= 0 or dist == 0:
                    continue
                nx, ny = dx / dist, dy / dist
                ma, mc = a.r * a.r, c.r * c.r              # mass ~ area
                rel = (c.vx - a.vx) * nx + (c.vy - a.vy) * ny
                if rel < 0:
                    j_imp = -(1 + self.bounce) * rel / (1 / ma + 1 / mc)
                    a.vx -= j_imp / ma * nx
                    a.vy -= j_imp / ma * ny
                    c.vx += j_imp / mc * nx
                    c.vy += j_imp / mc * ny
                push = gap / (ma + mc)
                a.x -= nx * push * mc
                a.y -= ny * push * mc
                c.x += nx * push * ma
                c.y += ny * push * ma
