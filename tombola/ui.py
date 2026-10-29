"""Pixel-canvas rendering of the tombola plus the parameter panel.

Each terminal cell holds a 2x4 pixel block, drawn either with octant block
characters (solid pixels; Windows Terminal and most modern terminals draw them
pixel-exact) or braille (dots; works in any font with braille).
"""
import math
import random
import re
import unicodedata

import pixels

ORANGE, AMBER, DIM, BALL, FLASH, INK = 208, 214, 244, 230, 229, 16
RING = (94, 130, 166)      # ring tracks, dim -> bright; drawn under everything else
SPARK = 223
MOON = 214                 # amber moons
FLASH_S = 0.12
SPARK_S = 0.15
_PRIO = {ORANGE: 2, FLASH: 3, SPARK: 1, BALL: 4, MOON: 4}
VISUALS = ("PIXEL", "BEHIND", "BLOCK", "BRAILLE")


def _bit(dx, dy):
    """Canvas mask bit for the pixel at (dx, dy) in a cell: octant order 1..8."""
    return 1 << (dy * 2 + dx)


_BRAILLE_DOT = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))   # [dy][dx]
BRAILLE = [chr(0x2800 + sum(_BRAILLE_DOT[dy][dx] for dy in range(4) for dx in range(2)
                            if m & _bit(dx, dy))) for m in range(256)]


def _octant_table():
    """mask -> glyph. 230 patterns are BLOCK OCTANT-n; the other 26 are older blocks."""
    t = {}
    for cp in range(0x1CD00, 0x1CDE6):
        m = re.fullmatch(r"BLOCK OCTANT-(\d+)", unicodedata.name(chr(cp), ""))
        if m:
            t[sum(1 << (int(d) - 1) for d in m.group(1))] = chr(cp)
    named = {
        (): " ", (1,): "\U0001CEA8", (2,): "\U0001CEAB", (7,): "\U0001CEA3", (8,): "\U0001CEA0",
        (1, 2): "\U0001FB82", (7, 8): "▂", (3, 5): "\U0001FBE6", (4, 6): "\U0001FBE7",
        (1, 3): "▘", (2, 4): "▝", (5, 7): "▖", (6, 8): "▗",
        (1, 2, 3, 4): "▀", (5, 6, 7, 8): "▄", (1, 3, 5, 7): "▌", (2, 4, 6, 8): "▐",
        (1, 2, 3, 4, 5, 6): "\U0001FB85", (3, 4, 5, 6, 7, 8): "▆",
        (1, 3, 6, 8): "▚", (2, 4, 5, 7): "▞", (1, 2, 3, 4, 5, 7): "▛", (1, 2, 3, 4, 6, 8): "▜",
        (1, 3, 5, 6, 7, 8): "▙", (2, 4, 5, 6, 7, 8): "▟", tuple(range(1, 9)): "█",
    }
    for cells, ch in named.items():
        t[sum(1 << (c - 1) for c in cells)] = ch
    return [t.get(m) for m in range(256)]


OCTANT = _octant_table()
if None in OCTANT:            # Python's Unicode data predates octants (Unicode 16)
    OCTANT = BRAILLE


def fg(c):
    return f"\x1b[38;5;{c}m"


RESET = "\x1b[0m"
SELECT = f"\x1b[48;5;{ORANGE}m\x1b[38;5;{INK}m"


class Canvas:
    """cols x rows terminal cells = (2*cols) x (4*rows) square pixels."""

    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.bits = [0] * (cols * rows)
        self.color = [0] * (cols * rows)

    def dot(self, x, y, color, under=False):
        """Set a pixel. under=True only fills cells nothing else has drawn in, so
        background layers never recolour a ball or edge sharing their cell."""
        if 0 <= x < self.cols * 2 and 0 <= y < self.rows * 4:
            i = (y // 4) * self.cols + x // 2
            if under and self.color[i] not in (0, color) and self.color[i] not in RING:
                return
            self.bits[i] |= _bit(x % 2, y % 4)
            if _PRIO.get(color, 0) >= _PRIO.get(self.color[i], 0):
                self.color[i] = color

    def line(self, x0, y0, x1, y1, color):
        """Bresenham between rounded endpoints: one pixel per step, no doubling."""
        x0, y0, x1, y1 = round(x0), round(y0), round(x1), round(y1)
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x1 > x0 else -1), (1 if y1 > y0 else -1)
        err = dx + dy
        while True:
            self.dot(x0, y0, color)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def disc(self, cx, cy, r, color):
        r = max(r, 1.0)
        for y in range(int(cy - r), int(cy + r) + 1):
            for x in range(int(cx - r), int(cx + r) + 1):
                if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r * r:
                    self.dot(x, y, color)

    def rows_text(self, glyphs=OCTANT):
        out = []
        for r in range(self.rows):
            parts, cur = [], None
            for c in range(self.cols):
                i = r * self.cols + c
                b = self.bits[i]
                if not b:
                    parts.append(" ")
                    continue
                if self.color[i] != cur:
                    cur = self.color[i]
                    parts.append(fg(cur))
                parts.append(glyphs[b])
            out.append("".join(parts) + RESET)
        return out


class Effects:
    """Grain sparks: each grain the mixer spawns flashes briefly inside the polygon."""

    MAX_NEW_SPARKS = 6

    def __init__(self, rng=random):
        self.rng = rng
        self.sparks = []       # (x, y, born) in world units
        self.seen = 0

    def update(self, mixer, now):
        new, self.seen = mixer.grain_count - self.seen, mixer.grain_count
        for _ in range(min(new, self.MAX_NEW_SPARKS)):
            a, r = self.rng.uniform(0, 2 * math.pi), 0.75 * math.sqrt(self.rng.random())
            self.sparks.append((r * math.cos(a), r * math.sin(a), now))
        self.sparks = [s for s in self.sparks if now - s[2] < SPARK_S]


def _inside(world):
    """Point-in-polygon test (world units) for the current rotation."""
    n, ap = world.sides, math.cos(math.pi / world.sides)
    normals = [(math.cos(m), math.sin(m)) for m in
               (world.angle + 2 * math.pi * (k + 0.5) / n for k in range(n))]
    return lambda x, y: all(x * cx + y * cy <= ap for cx, cy in normals)


def draw_rings(cv, rings, world, to, scale, bed_level):
    """Static elliptical ring tracks; the far half hides behind the polygon. PAD sets brightness."""
    inside = _inside(world)
    hidden = lambda a, x, y: not rings.in_front(a) and inside(x, y)
    for k in range(rings.count):
        loud = rings.level(k) * (0.5 + 0.5 * min(1.0, bed_level))
        level = 2 if loud > 0.6 else 1 if loud > 0.25 else 0
        n = max(16, int(2 * math.pi * rings.radius(k) * scale))
        for i in range(0, n, 3 - level):
            a = 2 * math.pi * i / n
            x, y = rings.point(k, a)
            if not hidden(a, x, y):
                px, py = to(x, y)
                cv.dot(round(px), round(py), RING[level], under=True)
    for k in range(rings.count):
        for a in rings.moon_angles(k):
            x, y = rings.point(k, a)
            if not hidden(a, x, y):
                px, py = to(x, y)
                ml = rings.moon_level(k, a)
                cv.disc(px, py, (0.025 + 0.03 * ml) * scale, MOON)


def draw_world(world, rings, cols, rows, visual="BLOCK", fx=None, bed_level=0.0):
    cv = Canvas(cols, rows)
    w, h = cols * 2, rows * 4
    ex, ey = rings.extent()
    scale = min((w / 2 - 2) / ex, (h / 2 - 2) / ey)
    cx, cy = w / 2, h / 2                      # canvas centre in pixels
    to = lambda x, y: (cx + x * scale, cy - y * scale)
    verts = [to(x, y) for x, y in world.vertices()]
    n = len(verts)
    for k in range(n):
        lit = world.time - world.flash.get(k, -9) < FLASH_S
        (x0, y0), (x1, y1) = verts[k], verts[(k + 1) % n]
        cv.line(x0, y0, x1, y1, FLASH if lit else ORANGE)
    for b in world.balls:
        x, y = to(b.x, b.y)
        cv.disc(x, y, b.r * scale, BALL)
    if fx:
        for x, y, _ in fx.sparks:
            px, py = to(x, y)
            cv.dot(round(px), round(py), SPARK, under=True)
    draw_rings(cv, rings, world, to, scale, bed_level)
    return cv.rows_text(BRAILLE if visual == "BRAILLE" else OCTANT)


def _label(text, value, selected, width):
    v = f"{SELECT} {value} {RESET}" if selected else f"{fg(AMBER)} {value} {RESET}"
    return f"{fg(DIM)}{text:<{width}}{RESET}{v}"


def _knob(label, value, selected, inner=6):
    """Four lines: rounded box with the value, label underneath. Width = inner + 2."""
    edge = fg(AMBER if selected else ORANGE)
    val = f"{SELECT}{value:^{inner}}{RESET}" if selected else f"{fg(BALL)}{value:^{inner}}{RESET}"
    lab = f"{fg(AMBER if selected else DIM)}{label:^{inner + 2}}{RESET}"
    bar = "─" * inner
    return (f"{edge}╭{bar}╮{RESET}", f"{edge}│{RESET}{val}{edge}│{RESET}",
            f"{edge}╰{bar}╯{RESET}", lab)


def _knob_inner(p):
    """Box width that fits the param's longest value and its label."""
    return max(6, len(p.label) - 2, max(len(p.fmt.format(v)) for v in p.values) + 2)


def _knob_rows(params, width):
    """Split knobs into the fewest rows that fit width, balanced (15 -> 8 + 7, not 10 + 5)."""
    for n in range(1, len(params) + 1):
        per = -(-len(params) // n)
        rows = [params[i:i + per] for i in range(0, len(params), per)]
        if all(sum(_knob_inner(p) + 4 for p in row) <= width for row in rows):
            return rows
    return [[p] for p in params]


def knob_layout_fits(state, cols, rows):
    """True when the full layout (scene + every setting as a knob) has room."""
    n = len(_knob_rows(state["side"] + state["knobs"], cols - 4))
    return rows >= 3 + MIN_SCENE_ROWS + 4 * n + 1


SHORT = {"ATTACK": "ATK", "RELEASE": "REL", "VISUAL": "VIS", "CHANGE": "CHG"}
HINTS = (("tab", "page"), ("←→", "select"), ("↑↓", "change"), ("3-8 s", "shape"), ("n", "key"),
         ("c", "scale"), ("i", "instr"), ("v", "visual"), ("w", "window"), ("space", "on/off"), ("r", "random"), ("u", "undo"),
         ("q", "quit"))


def _hints(width, page="BALLS"):
    """Key hints that fit in width columns; page/shape/key/scale keys win over the rest.

    Digits mean shape on BALLS and chord degree on RINGS."""
    swap = {"BALLS": None, "RINGS": ("1-7", "chord"), "GRAINS": ("f", "freeze")}[page]
    hints = HINTS if swap is None else tuple(swap if h[1] == "shape" else h for h in HINTS)
    order = (0, 3, 4, 5, 6, 12, 10, 11, 8, 1, 2, 9, 7)
    keep, used = set(), 0
    for i in order:
        k, label = hints[i]
        w = len(k) + len(label) + 3
        if used + w > width:
            break
        keep.add(i)
        used += w
    return "  ".join(f"{fg(AMBER)}{k}{RESET} {fg(DIM)}{label}{RESET}"
                     for i, (k, label) in enumerate(hints) if i in keep)


def _row(params, sel, width):
    """One line of label/value pairs; falls back to short labels when it won't fit."""
    for short in (False, True):
        items = [(SHORT.get(p.label, p.label) if short else p.label, p.text(), p is sel) for p in params]
        if sum(len(l) + len(v) + 4 for l, v, _ in items) <= width or short:
            return "  ".join(_label(l, v, s, 0) for l, v, s in items)


MIN_SCENE_ROWS = 6           # below this (after the knob rows) the frame goes compact


def _tabs(state, only_current=False):
    pages = [state["page"]] if only_current else state["pages"]
    return "  ".join(f"{SELECT} {p} {RESET}" if p == state["page"] else f"{fg(DIM)} {p} {RESET}"
                     for p in pages)


def _title(state, width):
    """Title + page tabs that fit width: drop the title first, then the other tabs."""
    tabs_w = sum(len(p) + 4 for p in state["pages"])
    if 9 + tabs_w <= width:
        return f"\x1b[1m{fg(ORANGE)}TOMBOLA{RESET}  {_tabs(state)}", 9 + tabs_w
    if tabs_w <= width:
        return _tabs(state), tabs_w
    return _tabs(state, only_current=True), len(state["page"]) + 2


def _status(state):
    on = f"{SELECT} ON {RESET}" if state["on"] else f"{fg(DIM)} OFF {RESET}"
    return f"{fg(DIM)}DSP {RESET}{fg(AMBER)}{min(99, int(state['dsp'] * 100)):2d}%{RESET} {on}"


def _flow(params, sel, width):
    """Wrap label/value pairs into lines of width; returns (lines, line holding sel)."""
    lines, cur, used, sel_line = [], [], 0, 0
    for p in params:
        item = (SHORT.get(p.label, p.label), p.text(), p is sel)
        w = len(item[0]) + len(item[1]) + 4
        if cur and used + w > width:
            lines.append(cur)
            cur, used = [], 0
        cur.append(item)
        used += w
        if p is sel:
            sel_line = len(lines)
    lines.append(cur)
    return ["  ".join(_label(l, v, s, 0) for l, v, s in line) for line in lines], sel_line


_TOKENS = re.compile(r"(\x1b\[[0-9;?]*[A-Za-z])|( {2,})|([^\x1b ]+| )")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _put_words(put, row, col, text):
    """put() text in pieces split at runs of 2+ spaces, so the scene behind shows between words."""
    sgr, seg, seg_col, x = "", "", col, col
    for esc, gap, run in _TOKENS.findall(text):
        if esc:
            sgr = "" if esc == RESET else sgr + esc
            seg += esc
        elif gap:
            if _ANSI.sub("", seg).strip():
                put(row, seg_col, seg + RESET)
            x += len(gap)
            seg, seg_col = sgr, x
        else:
            seg += run
            x += len(run)
    if _ANSI.sub("", seg).strip():
        put(row, seg_col, seg + RESET)


def _visual(state):
    return next((p.value for p in state["side"] + state["knobs"] if p.key == "visual"), "PIXEL")


def scene(state, cols, rows):
    """Draw the world in the chosen visual: PIXEL (half blocks) or BLOCK/BRAILLE (2x4 glyphs)."""
    if _visual(state) in ("PIXEL", "BEHIND"):
        return pixels.draw_scene(state["world"], state["rings"], cols, rows, state.get("fx"),
                                 state.get("bed_level", 0.0), flash_s=FLASH_S,
                                 dim=_visual(state) == "BEHIND")
    return draw_world(state["world"], state["rings"], cols, rows, _visual(state),
                      state.get("fx"), state.get("bed_level", 0.0))


def _render_compact(state, cols, rows, put):
    """Short panes (e.g. play's default 10% split): canvas left, controls right."""
    rings = state["rings"]
    ex, ey = rings.extent()
    side, knobs, sel = state["side"], state["knobs"], state["selected"]
    if _visual(state) == "BEHIND" or state.get("window"):
        cw = 0                                   # the scene is already behind the pane / the window
    else:
        cw = max(8, min(int(2 * rows * ex / ey), cols * 45 // 100))   # canvas cols matching ring aspect
        for r, text in enumerate(scene(state, cw, rows)):
            put(1 + r, 1, text)
    text_put = put
    x0 = cw + 3
    width = cols - x0
    hits = f"{fg(DIM)}HITS {RESET}{fg(BALL)}{state['hits'] % 10000:04d}{RESET}"
    title, title_w = _title(state, width)
    text_put(1, x0, title)
    if width - title_w >= 28:
        text_put(1, cols - 25, f"{hits}  {_status(state)}")
    lines, at = _flow(side + knobs, sel, width)
    room = rows - (2 if rows >= 3 else 1)       # header, and the hint line when there is space
    start = min(max(0, at - room // 2), max(0, len(lines) - room))
    for i, line in enumerate(lines[start:start + room]):
        text_put(2 + i, x0, line)
    if rows >= 3:
        err = f" {fg(196)}{state['error']}{RESET}" if state["error"] else ""
        text_put(rows, x0, _hints(width, state["page"]) + err)


def render(state, cols, rows):
    """Return the full frame as one string (cursor-positioned rows)."""
    side, knobs, sel = state["side"], state["knobs"], state["selected"]
    place = {}                                  # row -> [(col, text)]
    put = lambda r, c, t: place.setdefault(r, []).append((c, t))
    behind = _visual(state) == "BEHIND"
    if behind:
        for r, text in enumerate(scene(state, cols, rows)):
            put(1 + r, 1, text)
        raw_put = put
        put = lambda r, c, t: _put_words(raw_put, r, c, t)
    if not knob_layout_fits(state, cols, rows):
        _render_compact(state, cols, rows, put)
        return _compose(place, rows)

    put(1, 3, f"\x1b[1m{fg(ORANGE)}TOMBOLA{RESET}   {_tabs(state)}")
    put(2, 3, f"{fg(DIM)}GENERATIVE SYNTH{RESET}")
    put(1, max(3, cols - 12), f"{fg(DIM)}HITS {RESET}{fg(BALL)}{state['hits'] % 10000:04d}{RESET}")

    grid = _knob_rows(side + knobs, cols - 4)
    top = 4
    h = rows - top - 4 * len(grid) - 1
    if not behind:
        for r, text in enumerate(scene(state, cols - 4, h)):
            put(top + r, 3, text)

    for gi, row in enumerate(grid):                 # knob rows sit above the hint line
        y = rows - 4 * (len(grid) - gi)
        widths = [_knob_inner(p) + 2 for p in row]
        gap = (cols - 4 - sum(widths)) / len(row)  # spare room shared out evenly
        x = 3 + gap / 2
        for p, w in zip(row, widths):
            for dy, part in enumerate(_knob(p.label, p.text(), p is sel, w - 2)):
                put(y + dy, round(x), part)
            x += w + gap

    err = f" {fg(196)}{state['error']}{RESET}" if state["error"] else ""
    put(rows, 3, _hints(cols - 24, state["page"]) + err)
    put(rows, max(3, cols - 18), _status(state))
    return _compose(place, rows)


def _compose(place, rows):
    out = ["\x1b[H"]
    for r in range(1, rows + 1):
        out.append(f"\x1b[{r};1H\x1b[2K")
        for c, t in sorted(place.get(r, ())):
            out.append(f"\x1b[{r};{c}H{t}")
    return "".join(out)
