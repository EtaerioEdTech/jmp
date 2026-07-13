# ASCII Music Player

A terminal music player with a pure-text, SSH-menu-style library browser, a
progress bar, and a real-time frequency visualizer, all rendered in ASCII /
Unicode block characters.

Two full-screen modes, shown one at a time. Browse the library with the arrow
keys; pick a track and the browser gives way to the player. Press `b` to go
back and pick another.

```
BROWSER                            PLAYER

ARTISTS                              ▶  Kid A
                                         Radiohead  ·  Kid A
› Radiohead                    →       : ! | ┃ █ ┃ | ! :
  Aphex Twin                           ━ ═ █ ═ █ ═ █ ═ ━
  Boards of Canada                     : ! | ┃ █ ┃ | ! :
                                       1:23 ████────── 4:12
↑↓ move   → open   q quit
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

**Browser** (Artist → Album → Track drill-down):

| Key           | Action                              |
|---------------|-------------------------------------|
| ↑ / ↓         | Move up / down the list             |
| →             | Advance — drill in / play the track |
| ← / Backspace | Retreat — back up a level           |

**Player:**

| Key           | Action              |
|---------------|---------------------|
| `b` / Escape  | Back to the browser |
| Space         | Pause / resume      |
| `n`           | Next track          |
| `p`           | Previous track      |
| `+` / `-`     | Volume up / down    |
| `q`           | Quit                |

## How it works

- **Library scan** (`library.py`): walks the music directory, reads ID3 / Vorbis / FLAC tags with `mutagen`, groups tracks into Artist → Album → Track.
- **Browser** (`widgets.py`): a pure-text `Browser` widget renders a single-column list per level with a `›` cursor — no tree widget, no boxes. Enter drills Artists → Albums → Tracks; ← goes back up. Picking a track posts a message to the app.
- **Modes** (`app.py`): the browser and player are two full-screen views toggled by `display`; only the active one is shown. Choosing a track hides the browser and shows the player; `b` returns.
- **Playback** (`audio.py`): `pygame.mixer.music` streams the file from disk.
- **Visualizer**: on load, `pydub` decodes the file to raw samples. NumPy computes a windowed FFT every 50 ms and bins the result into 32 log-spaced frequency bands (~40 Hz to 16 kHz). During playback the widget indexes into this precomputed spectrogram by `get_pos_ms()`. It's drawn as a **radial / mirrored spectrum**: each band grows symmetrically above and below a center spine, with a density ramp of ASCII glyphs (`·:!|┃█`) — faint at the fringes, solid at the core. No color fills, so it stays fully transparent.

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
