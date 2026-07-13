# ASCII Music Player

A terminal music player with a nested library browser, a progress bar, and a real-time frequency visualizer, all rendered in ASCII / Unicode block characters.

```
♪ Library                        │  ▶  Kid A
├── Radiohead                    │      Radiohead  ·  Kid A
│   ├── Kid A  (2000)            │  ─────────────────────────
│   │   ├── 01. Everything...    │
│   │   ├── 02. Kid A            │       █
│   │   └── ...                  │     █ █   █
│   └── OK Computer  (1997)      │   █ █ █ █ █ █
└── ...                          │   █ █ █ █ █ █ █
                                 │   1:23 ████────── 4:12
```

## Setup

Ubuntu / Debian:

```bash
sudo apt install ffmpeg python3-venv
```

Then, in the project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python run.py                    # scans ~/Music
python run.py /path/to/music     # scans a specific directory
```

## Controls

| Key         | Action           |
|-------------|------------------|
| ↑ / ↓       | Move in library  |
| ← / →       | Collapse / expand folders |
| Enter       | Play track (or first track of album) |
| Space       | Pause / resume   |
| `n`         | Next track       |
| `p`         | Previous track   |
| `+` / `-`   | Volume up / down |
| `q`         | Quit             |

## How it works

- **Library scan** (`library.py`): walks the music directory, reads ID3 / Vorbis / FLAC tags with `mutagen`, groups tracks into Artist → Album → Track.
- **Playback** (`audio.py`): `pygame.mixer.music` streams the file from disk.
- **Visualizer**: on load, `pydub` decodes the file to raw samples. NumPy computes a windowed FFT every 50 ms and bins the result into 32 log-spaced frequency bands (~40 Hz to 16 kHz). During playback the widget indexes into this precomputed spectrogram by `get_pos_ms()`.
- **UI** (`app.py`, `widgets.py`): Textual handles the layout and key bindings. The visualizer widget uses `▄` and `█` half-block characters for double vertical resolution.

## Supported formats

MP3, OGG, FLAC, WAV, M4A, Opus. Visualizer works for any format ffmpeg can decode.

## Project layout

```
ascii-music-player/
├── run.py                    # entry point
├── requirements.txt
├── README.md
└── music_player/
    ├── __init__.py
    ├── app.py                # Textual app
    ├── app.tcss              # styling
    ├── audio.py              # pygame + FFT
    ├── library.py            # directory scan + tags
    └── widgets.py            # Visualizer, ProgressBar, NowPlaying
```

## Notes

- FFmpeg is required for the visualizer to analyze MP3 files. Playback works without it.
- The spectrogram is computed in a background thread, so playback starts immediately and the visualizer catches up a moment later.
