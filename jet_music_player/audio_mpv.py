"""mpv-based audio backend for Android/Termux.

pygame's SDL_mixer audio doesn't initialize in a headless Termux process, so on
Android we drive `mpv` instead. mpv plays every format we care about
(mp3/ogg/wav/flac/m4a/opus) with OpenSL ES output, and exposes a JSON IPC socket
that lets us read the exact playback position — which the progress bar and the
visualizer's spectrogram lookup both depend on.

Only playback changes. The visualizer analysis (spectrogram + waveform) is
inherited unchanged from AudioEngine: it decodes files off disk with pydub/numpy
and never touched pygame, so get_current_bars()/get_current_wave() keep working
as long as get_pos_ms() reports mpv's real position.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time

from .audio import AudioEngine


class MpvAudioEngine(AudioEngine):
    """Drop-in AudioEngine that plays through an mpv subprocess over JSON IPC.

    Reuses AudioEngine's analysis pipeline (the two-pass spectrogram + waveform)
    verbatim; only the ~10 playback methods are overridden to talk to mpv.
    """

    def __init__(self) -> None:
        # Deliberately do NOT call super().__init__(): that boots pygame.mixer,
        # which is exactly what fails on Termux. Reproduce the analysis-side
        # state AudioEngine sets up, then start mpv instead.
        self.current_path = None
        self._temp_wav = None
        self.spectrogram = None
        self.waveform = None
        self.wave_rate = 0
        self._analysis_thread = None
        self._paused = False
        self._volume = 0.7

        # mpv IPC state.
        self._sock_path = os.path.join(
            tempfile.gettempdir(), f"jmp-mpv-{os.getpid()}.sock"
        )
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()  # serialize IPC requests
        self._req_id = 0
        self._start_mpv()

    # ---- mpv process + IPC plumbing ----

    def _start_mpv(self) -> None:
        """Launch an idle mpv and connect to its JSON IPC socket."""
        try:
            os.remove(self._sock_path)
        except OSError:
            pass
        self._proc = subprocess.Popen(
            [
                "mpv",
                "--idle=yes",
                "--no-video",
                "--no-terminal",
                "--really-quiet",
                f"--input-ipc-server={self._sock_path}",
                f"--volume={int(self._volume * 100)}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # mpv creates the socket a moment after starting; wait briefly for it.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if os.path.exists(self._sock_path):
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.connect(self._sock_path)
                    s.settimeout(1.0)
                    self._sock = s
                    return
                except OSError:
                    pass
            time.sleep(0.05)
        # Couldn't connect. Leave _sock None; commands become no-ops and
        # playback silently fails rather than crashing the UI.

    def _command(self, *args) -> None:
        """Fire an mpv command, ignoring the reply. Best-effort."""
        self._request({"command": list(args)})

    def _request(self, payload: dict) -> dict | None:
        """Send one JSON-IPC request and return mpv's matching reply, or None.

        mpv replies are newline-delimited JSON. We tag each request with a
        request_id and read until we see the reply carrying it, so an
        interleaved async event message doesn't get mistaken for our answer.
        """
        if self._sock is None:
            return None
        with self._lock:
            self._req_id += 1
            rid = self._req_id
            payload = {**payload, "request_id": rid}
            try:
                self._sock.sendall((json.dumps(payload) + "\n").encode())
            except OSError:
                return None
            buf = b""
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                try:
                    chunk = self._sock.recv(4096)
                except (socket.timeout, OSError):
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("request_id") == rid:
                        return msg
            return None

    def _get_property(self, name: str):
        """Read one mpv property; None if unavailable."""
        reply = self._request({"command": ["get_property", name]})
        if reply and reply.get("error") == "success":
            return reply.get("data")
        return None

    # ---- playback control (overrides) ----

    def play(self, path: str) -> None:
        """Load and start playing `path`, and kick off background analysis.

        mpv decodes every format we support natively, so unlike the pygame
        backend there's no ffmpeg transcode-to-WAV step for playback. The
        visualizer analysis still uses pydub/ffmpeg on the original file.
        """
        self._command("loadfile", path, "replace")
        self._command("set_property", "pause", False)
        self.current_path = path
        self.spectrogram = None
        self.waveform = None
        self.wave_rate = 0
        self._paused = False
        self._analysis_thread = threading.Thread(
            target=self._analyze, args=(path,), daemon=True
        )
        self._analysis_thread.start()

    def toggle_pause(self) -> None:
        if self.current_path is None:
            return
        self._paused = not self._paused
        self._command("set_property", "pause", self._paused)

    def stop(self) -> None:
        self._command("stop")
        self._cleanup_temp()
        self.current_path = None
        self.spectrogram = None
        self.waveform = None
        self.wave_rate = 0
        self._paused = False

    def is_playing(self) -> bool:
        return self.current_path is not None and not self._paused and not self._eof()

    def is_paused(self) -> bool:
        return self._paused

    def is_finished(self) -> bool:
        """True when a track was loaded and mpv has reached end-of-file."""
        return self.current_path is not None and not self._paused and self._eof()

    def _eof(self) -> bool:
        """Whether mpv is sitting idle at end-of-file (nothing left to play)."""
        # eof-reached is True once the current file finishes; idle-active is
        # True whenever no file is loaded. Either means "not playing anymore".
        if self._get_property("eof-reached") is True:
            return True
        return self._get_property("idle-active") is True

    def get_pos_ms(self) -> int:
        """Playback position in ms, from mpv's time-pos property.

        This is what keeps the progress bar and the visualizer's spectrogram
        lookup keyed to real playback. Returns 0 before the first position is
        available.
        """
        pos = self._get_property("time-pos")
        if pos is None or pos < 0:
            return 0
        return int(pos * 1000)

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, volume))
        self._command("set_property", "volume", int(self._volume * 100))

    def get_volume(self) -> float:
        return self._volume

    # ---- teardown ----

    def close(self) -> None:
        """Quit mpv and clean up the socket. Safe to call more than once."""
        try:
            self._command("quit")
        except Exception:
            pass
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        try:
            os.remove(self._sock_path)
        except OSError:
            pass
        self._cleanup_temp()
