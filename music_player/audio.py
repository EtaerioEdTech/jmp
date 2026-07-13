"""Audio playback and spectrogram analysis.

Playback: pygame.mixer.music (streams from disk, handles mp3/ogg/wav/flac).
Visualizer data: pydub decodes the file to samples, numpy computes a
log-frequency spectrogram once per track. During playback we look up the
current position in the spectrogram to get the bars for this instant.
"""

from __future__ import annotations

import os
import threading

# Hide pygame's stdout banner before importing it. Textual would render it as garbage.
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import numpy as np
import pygame


class AudioEngine:
    """Wraps pygame playback and precomputes a spectrogram for the visualizer."""

    HOP_MS = 50           # spectrogram frame every 50 ms (20 fps of data)
    N_BARS = 32           # number of frequency bars
    FREQ_MIN = 40.0       # Hz
    FREQ_MAX = 16000.0    # Hz

    def __init__(self) -> None:
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
        self.current_path: str | None = None
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
        """Load and start playing a file."""
        pygame.mixer.music.load(path)
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
        self.current_path = None
        self.spectrogram = None
        self.waveform = None
        self.wave_rate = 0
        self._paused = False

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
        """Return the current frequency bars (0..1) for the visualizer, or None."""
        if self.spectrogram is None:
            return None
        frame_idx = int(self.get_pos_ms() / self.HOP_MS)
        if 0 <= frame_idx < len(self.spectrogram):
            return self.spectrogram[frame_idx]
        return None

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

    def _analyze(self, path: str) -> None:
        """Decode `path` and compute a log-frequency spectrogram.

        Runs in a background thread. Silently fails if pydub or ffmpeg aren't
        available; the visualizer will show a "no signal" state.
        """
        try:
            # Local import so pydub/ffmpeg missing doesn't break playback.
            from pydub import AudioSegment

            segment = AudioSegment.from_file(path)
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

            # Precompute which FFT bins fall into each log-spaced band.
            edges = np.logspace(
                np.log10(self.FREQ_MIN),
                np.log10(min(self.FREQ_MAX, sample_rate / 2)),
                self.N_BARS + 1,
            )
            band_masks = [(freqs >= edges[i]) & (freqs < edges[i + 1]) for i in range(self.N_BARS)]

            n_frames = (len(samples) - win_samples) // hop_samples
            spec = np.zeros((n_frames, self.N_BARS), dtype=np.float32)

            for i in range(n_frames):
                start = i * hop_samples
                chunk = samples[start:start + win_samples] * window
                magnitude = np.abs(np.fft.rfft(chunk))
                for j, mask in enumerate(band_masks):
                    if mask.any():
                        spec[i, j] = magnitude[mask].mean()

            # Normalize per-track so bars fill the display nicely.
            peak = spec.max()
            if peak > 0:
                spec = spec / peak
                # Log-compress: emphasize quieter frequencies so the viz feels alive.
                spec = np.log1p(spec * 20) / np.log1p(20)

            # Downsample the raw signal for the oscilloscope (~4 kHz is plenty
            # for a scope and keeps memory small).
            target_rate = 4000
            step = max(1, sample_rate // target_rate)
            wave = samples[::step]
            wave_rate = sample_rate // step

            # Only assign at the end so partial writes never appear.
            if self.current_path == path:
                self.spectrogram = spec
                self.waveform = wave
                self.wave_rate = wave_rate
        except Exception:
            # Playback still works. Visualizer just shows "no signal".
            return
