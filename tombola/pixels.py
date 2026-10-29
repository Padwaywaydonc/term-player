"""Half-block pixel renderer: crisp in any font, one colour per pixel.

Each terminal cell holds two square pixels (upper/lower half block), drawn as
solid blocks by every terminal and font. Lines are crisp one-pixel strokes;
a layer's brightness picks a step on a single-hue ramp of 256-colour indices,
which tmux passes through unchanged (it reduces true colour to that palette).
"""
import math

from rings import GAP, SQUASH

# layer: (priority, ramp of xterm-256 colours from faint to full)
RING, SPARK, EDGE, FLASH, MOON, BALL = range(6)
LAYERS = {                     # every ramp stays in one hue: grey -> brown -> orange
    RING: (0, (237, 239, 94, 130)),
    SPARK: (1, (240, 245, 223)),
    EDGE: (2, (94, 130, 166, 202, 208)),
    FLASH: (3, (136, 178, 220, 229)),
    MOON: (4, (94, 130, 172, 214)),
    BALL: (5, (242, 247, 252, 230)),
}
# Backdrop palette (scene behind text): one muted colour per layer, same hues.
DIM = {RING: 237, SPARK: 239, EDGE: 94, FLASH: 136, MOON: 130, BALL: 243}
MIN_COVER = 0.08


class PixelCanvas:
    """cols x rows cells = cols x (2*rows) square pixels."""

    def __init__(self, cols, rows, dim=False):
        self.w, self.h = cols, rows * 2
        self.layer = [-1] * (self.w * self.h)
        self.cover = [0.0] * (self.w * self.h)
        self.dim = dim

    def plot(self, x, y, layer, a):
        """Blend coverage a (0..1) of layer into pixel (x, y)."""
        if a < MIN_COVER or not (0 <= x < self.w and 0 <= y < self.h):
            return
        i = y * self.w + x
        cur = self.layer[i]
        if cur == layer:
            self.cover[i] = min(1.0, max(self.cover[i], a))
        elif cur < 0 or (LAYERS[layer][0] > LAYERS[cur][0] and a >= 0.35) or self.cover[i] < 0.2:
            self.layer[i], self.cover[i] = layer, a

    def point(self, x, y, layer, a=1.0):
        """The pixel containing fractional (x, y)."""
        self.plot(math.floor(x), math.floor(y), layer, a)

    def line(self, x0, y0, x1, y1, layer, a=1.0):
        """Crisp one-pixel line between fractional endpoints."""
        n = max(1, int(math.ceil(max(abs(x1 - x0), abs(y1 - y0)))))
        for s in range(n + 1):
            t = s / n
            self.point(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, layer, a)

    def disc(self, cx, cy, r, layer):
        """Filled circle; anything smaller than a pixel still shows as one."""
        self.point(cx, cy, layer)
        for y in range(int(cy - r), int(cy + r) + 1):
            for x in range(int(cx - r), int(cx + r) + 1):
                if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r * r:
                    self.plot(x, y, layer, 1.0)

    def colour(self, i):
        layer = self.layer[i]
        if layer < 0:
            return None
        if self.dim:
            return DIM[layer]
        ramp = LAYERS[layer][1]
        return ramp[min(len(ramp) - 1, int(self.cover[i] * len(ramp)))]

    def rows_text(self):
        out = []
        for r in range(self.h // 2):
            parts, cur = [], None
            for c in range(self.w):
                top = self.colour(2 * r * self.w + c)
                bot = self.colour((2 * r + 1) * self.w + c)
                if top is None and bot is None:
                    style, ch = "\x1b[0m", " "
                elif bot is None:
                    style, ch = f"\x1b[0;38;5;{top}m", "▀"
                elif top is None:
                    style, ch = f"\x1b[0;38;5;{bot}m", "▄"
                elif top == bot:
                    style, ch = f"\x1b[0;38;5;{top}m", "█"
                else:
                    style, ch = f"\x1b[0;38;5;{top};48;5;{bot}m", "▀"
                if style != cur:
                    parts.append(style)
                    cur = style
                parts.append(ch)
            out.append("".join(parts) + "\x1b[0m")
        return out


def _inside(world):
    n, ap = world.sides, math.cos(math.pi / world.sides)
    normals = [(math.cos(m), math.sin(m)) for m in
               (world.angle + 2 * math.pi * (k + 0.5) / n for k in range(n))]
    return lambda x, y: all(x * cx + y * cy <= ap for cx, cy in normals)


def draw_scene(world, rings, cols, rows, fx=None, bed_level=0.0, flash_s=0.12, dim=False):
    """Render polygon, balls, rings and moons, centred and scaled to fit.

    dim=True uses the muted backdrop palette, for drawing behind text.
    """
    cv = PixelCanvas(cols, rows, dim)
    w, h = cv.w, cv.h
    ex, ey = rings.extent()
    scale = min((w / 2 - 1) / ex, (h / 2 - 0.5) / ey)
    cx, cy = w / 2, h / 2
    to = lambda x, y: (cx + x * scale, cy - y * scale)

    inside = _inside(world)
    hidden = lambda a, x, y: not rings.in_front(a) and inside(x, y)
    ring_a = 0.5 + 0.5 * min(1.0, bed_level)
    # Tiny canvases can't separate adjacent tracks: draw every stride-th (always the outer one).
    spacing = GAP * SQUASH * scale
    stride = max(1, math.ceil(2 / max(spacing, 0.01)))
    for k in range(rings.count):
        if (rings.count - 1 - k) % stride:
            continue
        glow = ring_a * (0.45 + 0.55 * rings.level(k))     # louder ring, brighter track
        n = max(24, int(3 * math.pi * rings.radius(k) * scale))
        for i in range(n):
            a = 2 * math.pi * i / n
            x, y = rings.point(k, a)
            if not hidden(a, x, y):
                cv.point(*to(x, y), RING, glow)

    verts = [to(x, y) for x, y in world.vertices()]
    for k in range(len(verts)):
        lit = world.time - world.flash.get(k, -9) < flash_s
        (x0, y0), (x1, y1) = verts[k], verts[(k + 1) % len(verts)]
        cv.line(x0, y0, x1, y1, FLASH if lit else EDGE)
    if fx:
        for x, y, _ in fx.sparks:
            cv.point(*to(x, y), SPARK)
    for b in world.balls:
        cv.disc(*to(b.x, b.y), b.r * scale, BALL)
    for k in range(rings.count):
        for a in rings.moon_angles(k):
            x, y = rings.point(k, a)
            if not hidden(a, x, y):
                ml = rings.moon_level(k, a)                          # moons swell as they climb
                cv.disc(*to(x, y), (0.025 + 0.035 * ml) * scale, MOON)   # amber, never ball-white
    return cv.rows_text()
