"""WINDOW backdrop: the live scene in a Windows window behind the terminal.

backdrop.ps1 opens a borderless window that follows the Windows Terminal
window and sits just below it; with the terminal's background opacity turned
down the scene shows behind every pane. This module turns the world into the
script's drawing commands and streams them, always sending only the newest
frame so a slow window can never stall the main loop.
"""
import math
import os
import shutil
import subprocess
import threading

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backdrop.ps1")
RING_STEPS = 120

EDGE, FLASH = (255, 135, 0), (255, 235, 160)
BALL, MOON, RING, SPARK = (255, 250, 220), (255, 175, 0), (175, 95, 0), (255, 215, 175)
BG = (12, 12, 12)


def _xy(points):
    return " ".join(f"{x:.3f} {y:.3f}" for x, y in points)


def _line(colour, alpha, width, points):
    r, g, b = colour
    return f"L {r} {g} {b} {alpha} {width} {_xy(points)}"


def _circle(colour, alpha, x, y, radius):
    r, g, b = colour
    return f"C {r} {g} {b} {alpha} {x:.3f} {y:.3f} {radius:.3f}"


def _ring_runs(rings, k):
    """Ring k split into (front, points) runs along the ellipse."""
    runs, cur, front = [], [], None
    for i in range(RING_STEPS + 1):
        a = 2 * math.pi * i / RING_STEPS
        f = rings.in_front(a)
        if f != front and cur:
            runs.append((front, cur + [rings.point(k, a)]))
            cur = []
        front = f
        cur.append(rings.point(k, a))
    runs.append((front, cur))
    return runs


def scene_commands(world, rings, fx=None, bed_level=0.0, flash_s=0.12):
    """One frame of drawing commands, back to front."""
    ex, ey = rings.extent()
    out = [f"S {ex:.3f} {ey:.3f}"]
    pad = 0.5 + 0.5 * min(1.0, bed_level)
    runs = {k: _ring_runs(rings, k) for k in range(rings.count)}

    def rings_pass(front):
        for k, kruns in runs.items():
            loud = rings.level(k)                           # louder ring, brighter and thicker track
            for f, pts in kruns:
                if f == front and len(pts) > 1:
                    if loud > 0.3:
                        out.append(_line(RING, int(60 * loud * pad), 7, pts))
                    out.append(_line(RING, int((90 + 165 * loud) * pad), 2, pts))

    rings_pass(False)                                       # far side, hidden by the planet below
    verts = world.vertices()
    out.append("F {} {} {} 255 {}".format(*BG, _xy(verts)))
    closed = verts + verts[:1]
    out.append(_line(EDGE, 60, 9, closed))                  # glow
    out.append(_line(EDGE, 255, 3, closed))
    for k in range(len(verts)):
        if world.time - world.flash.get(k, -9) < flash_s:
            seg = [verts[k], verts[(k + 1) % len(verts)]]
            out.append(_line(FLASH, 90, 14, seg))
            out.append(_line(FLASH, 255, 4, seg))
    if fx:
        for x, y, _ in fx.sparks:
            out.append(_circle(SPARK, 200, x, y, 0.012))
    for b in world.balls:
        out.append(_circle(BALL, 45, b.x, b.y, b.r * 2.2))
        out.append(_circle(BALL, 255, b.x, b.y, b.r))
    rings_pass(True)                                        # near side passes in front
    for k in range(rings.count):
        for a in rings.moon_angles(k):
            x, y = rings.point(k, a)
            if not rings.in_front(a) and _inside(world, x, y):
                continue
            ml = rings.moon_level(k, a)                        # moons swell as they rise; always amber,
            out.append(_circle(MOON, int(30 + 70 * ml), x, y, 0.05 + 0.08 * ml))   # never ball-white
            out.append(_circle(MOON, int(170 + 85 * ml), x, y, 0.022 + 0.026 * ml))
    out.append("E")
    return out


def _inside(world, x, y):
    n, ap = world.sides, math.cos(math.pi / world.sides)
    return all(x * math.cos(m) + y * math.sin(m) <= ap
               for m in (world.angle + 2 * math.pi * (k + 0.5) / n for k in range(n)))


def available():
    return bool(shutil.which("powershell.exe") and shutil.which("cmd.exe"))


def _windows_copy(path):
    """Copy the script to Windows %TEMP% (PowerShell won't run scripts off \\\\wsl$ shares reliably)."""
    temp = subprocess.run(["cmd.exe", "/c", "echo %TEMP%"], capture_output=True, text=True,
                          timeout=10).stdout.strip()
    linux_temp = subprocess.run(["wslpath", temp], capture_output=True, text=True, timeout=10).stdout.strip()
    dest = os.path.join(linux_temp, "play-tombola-backdrop.ps1")
    shutil.copyfile(path, dest)
    return temp + "\\play-tombola-backdrop.ps1"


class Backdrop:
    def __init__(self):
        self.proc = None
        self.error = ""
        self._latest = None
        self._wake = threading.Event()
        self._thread = None

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.running:
            return
        if not available():
            self.error = "WINDOW needs Windows (WSL)"
            return
        try:
            script = _windows_copy(SCRIPT)
            self.proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        except (OSError, subprocess.SubprocessError) as exc:
            self.error = f"WINDOW: {exc}"
            return
        self.error = ""
        self._thread = threading.Thread(target=self._send_loop, daemon=True)
        self._thread.start()

    def submit(self, commands):
        self._latest = commands
        self._wake.set()

    def _send_loop(self):
        proc = self.proc
        while proc.poll() is None:
            self._wake.wait(0.5)
            self._wake.clear()
            frame, self._latest = self._latest, None
            if frame is None:
                continue
            try:
                proc.stdin.write("\n".join(frame) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return

    def stop(self):
        proc, self.proc = self.proc, None
        if not proc:
            return
        try:
            proc.stdin.close()                      # EOF closes the window
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
