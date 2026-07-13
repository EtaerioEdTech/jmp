# ASCII Music Player

A terminal music player rendered in **Braille glyphs** for a fine, high-res,
futuristic look — a pure-text, SSH-menu-style library browser and a real-time
Braille visualizer with several switchable modes. Everything is monochrome
(brightness only), so it stays fully transparent and the terminal shows through.

Two full-screen modes, shown one at a time. Browse the library with the arrow
keys; pick a track and the browser gives way to the player. Press `b` (or
Escape) to go back and pick another; press `v` to cycle the visualizer.

```
BROWSER                            PLAYER

ARTISTS                              ⣿⣿⣿  RADIOHEAD
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿                                everything in its right place
⣿ ⤮  Shuffle All                     ⣀⡠⠔⠒⠉⠉⠑⠢⢄⡀
  Radiohead                    →   ⣀⠔⠊         ⠈⠢⣀   ⡀
  Aphex Twin                     ⠊                 ⠑⠊ ⠈
  Boards of Canada                 1:23 ⣿⣿⣿⣿⣿⠉⠉⠉⠉⠉ 4:12
↑↓ move  → open  s shuffle  d dir  q quit
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

Installed as a `ttunes` launcher on your `PATH`:

```bash
ttunes                    # scans ~/Music
ttunes /path/to/music     # scans a specific directory
```

Or directly from the project directory:

```bash
python run.py [/path/to/music]
```

## Controls

**Browser** (Artist → Album → Track drill-down):

| Key           | Action                              |
|---------------|-------------------------------------|
| ↑ / ↓         | Move up / down the list             |
| →             | Advance — drill in / play the track |
| ← / Backspace | Retreat — back up a level           |
| `s`           | Shuffle the highlighted folder (all of an artist's songs, or an album). On the **Shuffle All** row at the top of the artist list, shuffles the whole library |
| `d`           | Change the music directory (type a new root path) |

**Player:**

| Key           | Action                                        |
|---------------|-----------------------------------------------|
| `b` / Escape  | Back to the browser                           |
| `v`           | Cycle visualizer (spectrum bars / mirror) |
| Space         | Pause / resume                                |
| `n`           | Next track                                    |
| `p`           | Previous track                                |
| `+` / `-`     | Volume up / down                              |
| `q`           | Quit                                          |

## How it works

- **Library scan** (`library.py`): walks the music directory, reads ID3 / Vorbis / FLAC tags with `mutagen`, groups tracks into Artist → Album → Track.
- **Browser** (`widgets.py`): a pure-text `Browser` widget renders a single-column list per level with a Braille `⣿` cursor and a Braille rule under the heading — no tree widget, no boxes. → drills Artists → Albums → Tracks; ← goes back up. Picking a track posts a message to the app.
- **Modes** (`app.py`): the browser and player are two full-screen views toggled by `display`; only the active one is shown. Choosing a track hides the browser and shows the player; `b`/Escape returns. Pressing `s` in the browser shuffles the highlighted folder (an artist's whole catalogue, or a single album) into a playlist; a **Shuffle All** row at the top of the artist list shuffles the whole library. `d` re-scans a new music directory in place.
- **Banner** (`widgets.py`): the player's title is two stacked lines — the artist big, the track name half-size below it — rendered in a Braille bitmap font (`braille.py`). Each line is centered when it fits and scrolls as a marquee when it's wider than the screen.
- **Braille rendering** (`braille.py`): a small `Canvas` rasterizes points/lines/bars into a 2×4-dots-per-cell sub-pixel grid (8× resolution) and packs each block into one Braille glyph, fully vectorized in NumPy. The visualizer, progress bar, browser cursor and play icons all use it, so the whole UI reads as one fine, futuristic, monochrome (transparent) system.
- **Playback** (`audio.py`): `pygame.mixer.music` streams the file from disk.
- **Visualizer**: on load, `pydub` decodes the file to raw samples and NumPy computes a windowed FFT every 50 ms into 32 log-spaced frequency bands (~40 Hz to 16 kHz). Analysis runs in a background thread in two passes — the opening seconds first, so the visualizer starts in step with playback — and the widget indexes into the spectrogram by `get_pos_ms()`. Press `v` to switch between two modes: **spectrum** (32-band frequency bars) and **mirror** (the spectrum reflected above and below a center line). Both are Braille-rendered and hold 30 fps, scaling their resolution to the terminal size.

## Supported formats

MP3, OGG, FLAC, WAV, M4A, Opus. Visualizer works for any format ffmpeg can decode.

## Project layout

```
terminaltunes/
├── run.py                    # entry point
├── requirements.txt
├── README.md
└── music_player/
    ├── __init__.py
    ├── app.py                # Textual app
    ├── app.tcss              # styling
    ├── audio.py              # pygame + FFT + waveform
    ├── braille.py            # Braille sub-pixel canvas
    ├── library.py            # directory scan + tags
    └── widgets.py            # Browser, Visualizer, ProgressBar, Banner
```

## Notes

- FFmpeg is required for the visualizer to analyze MP3 files. Playback works without it.
- The spectrogram is computed in a background thread, opening seconds first, so the visualizer starts in step with playback rather than catching up after the whole track is analyzed.
