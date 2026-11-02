"""play --tombola: generative music from balls bouncing in a spinning polygon."""
import argparse
import sys

import app
from audio import Output, sink_command


def main():
    ap = argparse.ArgumentParser(prog="play --tombola", description=__doc__)
    ap.add_argument("--audio", choices=("wsl", "native", "none"), default="native")
    ap.add_argument("--mpv", default="", help="Windows mpv.exe (used with --audio wsl)")
    ap.add_argument("--linux-mpv", default="")
    ap.add_argument("--sink", default="", help="custom command that plays s16le PCM from stdin "
                    "(48 kHz stereo with the native core, 22.05 kHz mono with the Python engine)")
    ap.add_argument("--config", default=app.CONFIG)
    args = ap.parse_args()
    if not sys.stdin.isatty():
        sys.exit("play --tombola needs an interactive terminal")
    app.run(lambda mixer: Output(mixer, sink_command(args.audio, args.mpv, args.linux_mpv, args.sink,
                                                     mixer.sample_rate, mixer.channels)), args.config)


if __name__ == "__main__":
    main()
