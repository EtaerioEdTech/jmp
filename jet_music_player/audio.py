"""Audio playback and spectrogram analysis.

Playback: pygame.mixer.music (streams from disk, handles mp3/ogg/wav/flac).
Formats SDL_mixer can't decode (m4a/aac, opus) are transcoded to a temporary
WAV via ffmpeg first — otherwise SDL_mixer misroutes the unknown file to its
tracker-module loader and raises "ModPlug_Load failed".
Visualizer data: pydub decodes the file to samples, numpy computes a
log-frequency spectrogram once per track. During playback we look up the
current position in the spectrogram to get the bars for this instant.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading

# Hide pygame's stdout banner before importing it. Textual would render it as garbage.
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import numpy as np
import pygame


# Extensions SDL_mixer can decode directly. Anything else needs ffmpeg to be
# transcoded before playback (see AudioEngine._resolve_playable).
NATIVE_EXTS = {".mp3", ".ogg", ".wav", ".flac"}


def ffmpeg_available() -> bool:
    """True if an `ffmpeg` binary is on PATH.

    ffmpeg isn't a pip dependency — it's a system package. Without it,
    non-native formats (m4a, opus) can't be played and the visualizer can't
    analyze compressed audio. Callers use this to warn the user up front.
    """
    return shutil.which("ffmpeg") is not None


class AudioEngine:
    """Wraps pygame playback and precomputes a spectrogram for the visualizer."""

    HOP_MS = 50           # spectrogram frame every 50 ms (20 fps of data)
    N_BARS = 32           # number of frequency bars
    FREQ_MIN = 40.0       # Hz
    FREQ_MAX = 16000.0    # Hz

    def __init__(self) -> None:
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
        self.current_path: str | None = None
        self._temp_wav: str | None = None  # transcoded file to clean up, if any
        self.spectrogram: np.ndarray | None = None  # shape (frames, N_BARS)
        self.waveform: np.ndarray | None = None      # downsampled mono samples, [-1, 1]
        self.wave_rate: int = 0                       # samples/sec of `waveform`
        self._analysis_thread: threading.Thread | None = None
        self._paused = False
        self._pause_offset_ms = 0  # ms already played when paused
        self._volume = 0.7
        pygame.mixer.music.set_volume(self._volume)

    # ---- playback control ----

    def play(self, path: str) -> None:
        """Load and start playing a file.

        Transcodes formats SDL_mixer can't decode (m4a, opus, ...) to a temp
        WAV first. Playback position and the visualizer both stay keyed to the
        original file's timeline, since the transcode preserves duration.
        """
        playable = self._resolve_playable(path)
        pygame.mixer.music.load(playable)
        pygame.mixer.music.play()
        self.current_path = path
        self.spectrogram = None
        self.waveform = None
        self.wave_rate = 0
        self._paused = False
        self._pause_offset_ms = 0
        # Analyze the audio in a background thread so the UI stays responsive.
        self._analysis_thread = threading.Thread(
            target=self._analyze, args=(path,), daemon=True
        )
        self._analysis_thread.start()

    def toggle_pause(self) -> None:
        """Pause if playing, resume if paused."""
        if self.current_path is None:
            return
        if self._paused:
            pygame.mixer.music.unpause()
            self._paused = False
        else:
            pygame.mixer.music.pause()
            self._paused = True

    def stop(self) -> None:
        pygame.mixer.music.stop()
        pygame.mixer.music.unload()
        self._cleanup_temp()
        self.current_path = None
        self.spectrogram = None
        self.waveform = None
        self.wave_rate = 0
        self._paused = False

    def _resolve_playable(self, path: str) -> str:
        """Return a path SDL_mixer can load.

        Natively supported formats pass through unchanged. Others are
        transcoded to a temporary WAV via ffmpeg. Any temp file from a previous
        track is removed here (we're about to load a new file, so the old one is
        no longer streaming). If ffmpeg is unavailable or fails, falls back to
        the original path so playback raises a clear error rather than silently
        doing nothing.
        """
        # Release the previous file's handle and drop its temp WAV, if any.
        pygame.mixer.music.unload()
        self._cleanup_temp()

        if os.path.splitext(path)[1].lower() in NATIVE_EXTS:
            return path

        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="tt-")
        os.close(fd)
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", path, tmp],
                check=True,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            # ffmpeg missing or failed — clean up and let SDL_mixer try the
            # original (it will raise, surfacing the real problem).
            try:
                os.remove(tmp)
            except OSError:
                pass
            return path
        self._temp_wav = tmp
        return tmp

    def _cleanup_temp(self) -> None:
        """Remove the transcoded temp WAV for the previous track, if any."""
        if self._temp_wav is not None:
            try:
                os.remove(self._temp_wav)
            except OSError:
                pass
            self._temp_wav = None

    def is_playing(self) -> bool:
        return pygame.mixer.music.get_busy() and not self._paused

    def is_paused(self) -> bool:
        return self._paused

    def is_finished(self) -> bool:
        """True when a track was loaded and has finished playing."""
        return self.current_path is not None and not pygame.mixer.music.get_busy() and not self._paused

    def get_pos_ms(self) -> int:
        """Playback position in ms since the current track started.

        pygame.mixer.music.get_pos() returns -1 when nothing is playing.
        """
        pos = pygame.mixer.music.get_pos()
        return pos if pos >= 0 else 0

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, volume))
        pygame.mixer.music.set_volume(self._volume)

    def get_volume(self) -> float:
        return self._volume

    # ---- visualizer data ----

    def get_current_bars(self) -> np.ndarray | None:
        """Return the current frequency bars (0..1) for the visualizer, or None.

        While only the fast head-slice spectrogram is available (pass 1), the
        playback position can run past its end before the full spectrogram
        (pass 2) lands. In that brief window we hold the last available frame
        rather than returning None, so the visualizer keeps moving instead of
        blanking out for a second at the ~head-length mark of every track.
        """
        spec = self.spectrogram
        if spec is None or len(spec) == 0:
            return None
        frame_idx = int(self.get_pos_ms() / self.HOP_MS)
        if frame_idx < 0:
            return None
        if frame_idx >= len(spec):
            frame_idx = len(spec) - 1  # hold the last frame until more data lands
        return spec[frame_idx]

    def get_current_wave(self, n: int = 512) -> np.ndarray | None:
        """Return ~`n` raw samples around the current playback position, in
        [-1, 1], for the oscilloscope. None if the waveform isn't ready."""
        if self.waveform is None or self.wave_rate <= 0:
            return None
        center = int(self.get_pos_ms() / 1000.0 * self.wave_rate)
        half = n // 2
        start = max(0, center - half)
        end = start + n
        if end > len(self.waveform):
            end = len(self.waveform)
            start = max(0, end - n)
        window = self.waveform[start:end]
        return window if len(window) > 0 else None

    # ---- analysis ----

    # Seconds of audio to decode-and-analyze in the fast first pass. Decoding
    # just the head (ffmpeg -t) takes ~150 ms regardless of track length, so the
    # visualizer lights up in step with playback; the full track follows.
    _HEAD_SECONDS = 4.0

    def _analyze(self, path: str) -> None:
        """Decode `path` and compute a log-frequency spectrogram.

        Runs in a background thread in two passes so the visualizer starts in
        step with playback rather than after the whole track is analyzed:

          1. Decode only the opening `_HEAD_SECONDS` and publish its spectrogram
             immediately (~150 ms — bounded, independent of track length).
          2. Decode the full track and publish the complete spectrogram.

        Silently fails if pydub or ffmpeg aren't available; the visualizer will
        show a "no signal" state.
        """
        try:
            # Local import so pydub/ffmpeg missing doesn't break playback.
            from pydub import AudioSegment

            # Pass 1: fast head slice so the opening frames appear right away.
            head = AudioSegment.from_file(path, duration=self._HEAD_SECONDS)
            self._process_segment(path, head, publish_wave=False)
            if self.current_path != path:
                return  # track changed while we were decoding the head

            # Pass 2: the whole track, replacing the head-only spectrogram.
            full = AudioSegment.from_file(path)
            self._process_segment(path, full, publish_wave=True)
        except Exception:
            # Playback still works. Visualizer just shows "no signal".
            return

    def _process_segment(self, path: str, segment, publish_wave: bool) -> None:
        """Compute the spectrogram for one decoded `segment` and publish it.

        Publishes only if `path` is still the current track, so a stale pass
        (the user skipped tracks mid-decode) never overwrites newer data.
        """
        samples = np.array(segment.get_array_of_samples(), dtype=np.float32)
        if segment.channels == 2:
            samples = samples.reshape((-1, 2)).mean(axis=1)
        sample_rate = segment.frame_rate

        # Normalize 16-bit samples to [-1, 1].
        samples /= float(1 << (8 * segment.sample_width - 1))

        hop_samples = int(sample_rate * self.HOP_MS / 1000)
        win_samples = hop_samples * 2
        if win_samples <= 0 or len(samples) < win_samples:
            return

        window = np.hanning(win_samples).astype(np.float32)
        freqs = np.fft.rfftfreq(win_samples, d=1.0 / sample_rate)

        # Precompute a (n_bins × N_BARS) averaging matrix: each column averages
        # the FFT bins that fall into one log-spaced band. One matmul then does
        # all the band-averaging for a whole batch of frames at once.
        edges = np.logspace(
            np.log10(self.FREQ_MIN),
            np.log10(min(self.FREQ_MAX, sample_rate / 2)),
            self.N_BARS + 1,
        )
        n_bins = len(freqs)
        band_matrix = np.zeros((n_bins, self.N_BARS), dtype=np.float32)
        for j in range(self.N_BARS):
            mask = (freqs >= edges[j]) & (freqs < edges[j + 1])
            count = int(mask.sum())
            if count:
                band_matrix[mask, j] = 1.0 / count

        n_frames = (len(samples) - win_samples) // hop_samples
        if n_frames <= 0:
            return
        spec = np.empty((n_frames, self.N_BARS), dtype=np.float32)

        # Process frames in batches: build a (batch × win) matrix of windowed
        # frames via stride indexing, one rfft over the batch, one matmul to
        # band-average. Vectorized like this the whole track takes a fraction of
        # a second instead of ~seconds of per-frame Python — so pass 2 lands
        # before playback reaches the end of the fast head window and the
        # visualizer never stalls. Batching caps the temporary matrix's memory.
        frame_offsets = np.arange(win_samples)
        BATCH = 1024
        for base in range(0, n_frames, BATCH):
            n = min(BATCH, n_frames - base)
            starts = (np.arange(base, base + n) * hop_samples)[:, None]
            frames = samples[starts + frame_offsets[None, :]] * window
            magnitude = np.abs(np.fft.rfft(frames, axis=1))
            spec[base:base + n] = magnitude @ band_matrix

        # Normalize per-segment so bars fill the display nicely.
        peak = spec.max()
        if peak > 0:
            spec = spec / peak
            # Log-compress: emphasize quieter frequencies so the viz feels alive.
            spec = np.log1p(spec * 20) / np.log1p(20)

        if publish_wave:
            # Downsample the raw signal for the oscilloscope (~4 kHz is plenty
            # for a scope and keeps memory small).
            target_rate = 4000
            step = max(1, sample_rate // target_rate)
            wave = samples[::step]
            wave_rate = sample_rate // step
        else:
            wave = wave_rate = None

        # Only assign if this is still the current track, so a stale pass never
        # clobbers newer data.
        if self.current_path == path:
            self.spectrogram = spec
            if publish_wave:
                self.waveform = wave
                self.wave_rate = wave_rate
