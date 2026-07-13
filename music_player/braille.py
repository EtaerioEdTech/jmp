"""Braille sub-pixel rendering helpers.

Braille characters (U+2800…U+28FF) encode a 2×4 grid of dots per glyph, so a
character cell becomes 8 addressable pixels. Rasterizing into this grid and
packing each 2×4 block into one glyph gives 8× the resolution of one glyph per
cell — fine, light lines that still live in a normal text grid.

Everything here is vectorized with NumPy so it holds a high frame rate.
"""

from __future__ import annotations

import numpy as np

DOT_W = 2
DOT_H = 4

# Bit weight of each (dx, dy) dot within a Braille cell, per the Unicode layout:
#   (0,0)=0x01 (1,0)=0x08
#   (0,1)=0x02 (1,1)=0x10
#   (0,2)=0x04 (1,2)=0x20
#   (0,3)=0x40 (1,3)=0x80
_BITS = np.array(
    [[0x01, 0x08],
     [0x02, 0x10],
     [0x04, 0x20],
     [0x40, 0x80]],
    dtype=np.int32,
)  # shape (DOT_H, DOT_W)


class Canvas:
    """A Braille pixel canvas `width` × `height` characters.

    Coordinates for plotting are in *dot* space: 0..(width*2) by 0..(height*4).
    """

    def __init__(self, width: int, height: int) -> None:
        self.width = max(1, width)
        self.height = max(1, height)
        self.gw = self.width * DOT_W
        self.gh = self.height * DOT_H
        # Accumulate hit counts so overlapping strokes can read as "brighter".
        self.buf = np.zeros((self.gh, self.gw), dtype=np.float32)

    def plot(self, xs: np.ndarray, ys: np.ndarray) -> None:
        """Set dots at integer coordinate arrays (clamped to the canvas)."""
        xi = np.clip(np.asarray(xs, dtype=int), 0, self.gw - 1)
        yi = np.clip(np.asarray(ys, dtype=int), 0, self.gh - 1)
        np.add.at(self.buf, (yi, xi), 1.0)

    def line(self, xs: np.ndarray, ys: np.ndarray, oversample: int = 4) -> None:
        """Plot a connected polyline through the given points, filling the gaps
        between consecutive points so the stroke is continuous."""
        xs = np.asarray(xs, dtype=float)
        ys = np.asarray(ys, dtype=float)
        if len(xs) < 2:
            self.plot(xs, ys)
            return
        t = (np.arange(oversample) / oversample)[None, :]
        px = (xs[:-1, None] + np.diff(xs)[:, None] * t).ravel()
        py = (ys[:-1, None] + np.diff(ys)[:, None] * t).ravel()
        self.plot(px, py)

    def vbar(self, col_dot: int, top_dot: int) -> None:
        """Fill a 1-dot-wide vertical bar in column `col_dot` from the bottom
        of the canvas up to `top_dot` (dot rows). Used for spectrum bars."""
        col_dot = int(np.clip(col_dot, 0, self.gw - 1))
        top_dot = int(np.clip(top_dot, 0, self.gh))
        if top_dot <= 0:
            return
        self.buf[self.gh - top_dot:self.gh, col_dot] += 1.0

    def codes(self) -> np.ndarray:
        """Return an (height, width) int array of Braille codepoints offsets
        (0..255); 0 means an empty cell."""
        hit = self.buf > 0
        blocks = hit.reshape(self.height, DOT_H, self.width, DOT_W)
        return (blocks * _BITS[None, :, None, :]).sum(axis=(1, 3)).astype(np.int32)

    def density(self) -> np.ndarray:
        """Return an (height, width) float array of per-cell hit totals, for
        deciding brightness (how many strokes crossed each cell)."""
        return self.buf.reshape(self.height, DOT_H, self.width, DOT_W).sum(axis=(1, 3))


def line_glyphs(width: int, filled: float) -> str:
    """A horizontal Braille bar `width` chars wide, `filled` in 0..1 of it
    solid (all 8 dots) and the remainder faint (top+bottom dots only). Handy
    for progress bars and rules."""
    if width <= 0:
        return ""
    solid = chr(0x28FF)  # all 8 dots
    faint = chr(0x2809)  # ⠉ two top-ish dots — a light track
    n_full = int(round(max(0.0, min(1.0, filled)) * width))
    return solid * n_full + faint * (width - n_full)
