"""Keys, scales, and the pad -> pitch mapping."""

KEYS = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
SCALES = {
    "MINOR": (0, 2, 3, 5, 7, 8, 10),
    "MAJOR": (0, 2, 4, 5, 7, 9, 11),
    "PENTA": (0, 2, 4, 7, 9),
    "MIN PENTA": (0, 3, 5, 7, 10),
    "DORIAN": (0, 2, 3, 5, 7, 9, 10),
    "LYDIAN": (0, 2, 4, 6, 7, 9, 11),
    "HIRAJOSHI": (0, 2, 3, 7, 8),
    "WHOLE": (0, 2, 4, 6, 8, 10),
}
SHAPES = {"TRI": 3, "SQR": 4, "PENT": 5, "HEX": 6, "HEPT": 7, "OCT": 8}


def pad_note(key, scale, edge, octave=0, ball_octave=0):
    """MIDI note for a ball (octave offset) hitting pad `edge`.

    Pads walk up the scale from the root, wrapping into the next octave, so a
    hexagon in a 5-note scale spans root .. root+octave.
    """
    steps = SCALES[scale]
    degree = steps[edge % len(steps)] + 12 * (edge // len(steps))
    return 48 + KEYS.index(key) + degree + 12 * (octave + ball_octave)
