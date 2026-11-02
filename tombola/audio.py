"""Stream the mixer's PCM into an external player process.

Both mpv.exe (via WSL interop) and pacat accept seconds of audio before a
write blocks, so pipe back-pressure can't pace us: the thread instead stays a
fixed LEAD ahead of the wall clock, which keeps hit-to-sound latency small.
"""
import os
import shlex
import shutil
import subprocess
import threading
import time
import traceback

from synth import BLOCK, SAMPLE_RATE

ERROR_LOG = os.path.expanduser("~/.cache/play/tombola-errors.log")
LEAD = 0.20   # seconds queued ahead of real time: rides out brief stalls (thread contention, host load)


def sink_command(audio, mpv_win="", linux_mpv="", sink=""):
    """argv of a process that plays mono s16le PCM from stdin, or None for silence."""
    if sink:
        return shlex.split(sink)
    if audio == "none":
        return None
    raw = ["--no-video", "--really-quiet", "--no-terminal", "--demuxer=rawaudio",
           "--demuxer-rawaudio-format=s16le", f"--demuxer-rawaudio-rate={SAMPLE_RATE}",
           "--demuxer-rawaudio-channels=1", "--cache=no", "--demuxer-readahead-secs=0",
           "--audio-buffer=0.1", "-"]
    if audio == "wsl" and mpv_win:
        return [mpv_win, *raw]
    if shutil.which("pacat"):
        return ["pacat", "--playback", "--raw", "--format=s16le", f"--rate={SAMPLE_RATE}",
                "--channels=1", "--latency-msec=60", "--client-name=play-tombola"]
    mpv = linux_mpv or shutil.which("mpv")
    if mpv:
        return [mpv, *raw]
    return None


class Output:
    def __init__(self, mixer, argv):
        self.mixer = mixer
        self.argv = argv
        self.proc = None
        self.error = "" if argv else "audio off"
        self.dsp = 0.0          # fraction of real time spent mixing
        self._failed = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        if self.argv:
            try:
                self.proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                self.error = f"audio: {exc.strerror}"
        self._thread.start()

    def _run(self):
        t0 = time.monotonic()
        written = 0
        block_s = BLOCK / SAMPLE_RATE
        while not self._stop.is_set():
            ahead = written / SAMPLE_RATE - (time.monotonic() - t0)
            if ahead > LEAD:
                time.sleep(min(ahead - LEAD, 0.02))
                continue
            if ahead < -0.25:                  # we stalled; resync instead of bursting
                t0 = time.monotonic() - written / SAMPLE_RATE
            t = time.perf_counter()
            pcm = self._render()
            self.dsp = 0.9 * self.dsp + 0.1 * (time.perf_counter() - t) / block_s
            written += BLOCK
            if self.proc and self.error != "audio sink exited":
                try:
                    self.proc.stdin.write(pcm)
                    self.proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    self.error = "audio sink exited"

    def _render(self):
        """One block from the mixer; on any error, log it, show it, and play silence instead
        of letting the audio thread die quietly."""
        try:
            return self.mixer.render(BLOCK)
        except Exception as exc:                      # noqa: BLE001 — must never kill the audio thread
            if not self._failed:                      # log the first one; later ones repeat it
                self._failed = True
                self.error = f"audio error: {type(exc).__name__} (see {ERROR_LOG})"
                os.makedirs(os.path.dirname(ERROR_LOG), exist_ok=True)
                with open(ERROR_LOG, "a") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + traceback.format_exc() + "\n")
            return bytes(2 * BLOCK)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=1)
        if self.proc:
            try:
                self.proc.stdin.close()        # mpv/pacat exit on EOF
            except OSError:
                pass
            try:
                self.proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=1)

