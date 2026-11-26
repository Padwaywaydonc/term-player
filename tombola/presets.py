"""Preset slots 1-9: P then a digit saves, L then a digit loads.

A preset is every setting (except WINDOW, which belongs to the machine) plus
the chord loop's seed and the radio search, so loading one brings back the
same sound and the same RANDOM chord loop. One small JSON file per slot.
"""
import json
import os

PRESET_DIR = os.path.expanduser("~/.config/play-tombola-presets")
SLOTS = range(1, 10)
SKIP = ("window",)                       # per machine, not part of a sound
MAX_SEED = 2 ** 31 - 1


def path(slot, folder=PRESET_DIR):
    return os.path.join(folder, f"slot-{slot}.json")


def save(slot, settings, seed, radio_query="", folder=PRESET_DIR):
    """Write atomically: a kill mid-save leaves the previous preset, never a broken one."""
    os.makedirs(folder, exist_ok=True)
    target = path(slot, folder)
    tmp = f"{target}.tmp"
    with open(tmp, "w") as f:
        json.dump({**{k: v for k, v in settings.items() if k not in SKIP},
                   "loop_seed": seed, "radio_query": radio_query}, f, indent=1)
    os.replace(tmp, target)


def load(slot, folder=PRESET_DIR):
    """(settings, seed or None, radio query) from a slot, or None when it's empty or unreadable."""
    try:
        with open(path(slot, folder)) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict):
        return None
    seed = saved.pop("loop_seed", None)
    query = saved.pop("radio_query", "")
    settings = {k: v for k, v in saved.items() if k not in SKIP}
    seed = seed if isinstance(seed, int) and 0 < seed <= MAX_SEED else None
    return settings, seed, query[:120] if isinstance(query, str) else ""


def used(folder=PRESET_DIR):
    """Slots that hold a preset."""
    return [s for s in SLOTS if os.path.isfile(path(s, folder))]
