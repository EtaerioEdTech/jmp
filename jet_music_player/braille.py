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


# ---------------------------------------------------------------------------
# Bitmap font for big Braille banner text.
#
# Each glyph is 5 dot-rows tall × 5 dot-cols wide, drawn with "#" and " " so the
# shapes are legible in source. Uppercase only; lowercase is folded to upper.
# ---------------------------------------------------------------------------

FONT_H = 5
FONT_W = 5

_GLYPHS = {
    "A": ["  #  ", " # # ", "#   #", "#####", "#   #"],
    "B": ["#### ", "#   #", "#### ", "#   #", "#### "],
    "C": [" ####", "#    ", "#    ", "#    ", " ####"],
    "D": ["#### ", "#   #", "#   #", "#   #", "#### "],
    "E": ["#####", "#    ", "###  ", "#    ", "#####"],
    "F": ["#####", "#    ", "###  ", "#    ", "#    "],
    "G": [" ####", "#    ", "#  ##", "#   #", " ####"],
    "H": ["#   #", "#   #", "#####", "#   #", "#   #"],
    "I": ["#####", "  #  ", "  #  ", "  #  ", "#####"],
    "J": ["#####", "   # ", "   # ", "#  # ", " ##  "],
    "K": ["#   #", "#  # ", "###  ", "#  # ", "#   #"],
    "L": ["#    ", "#    ", "#    ", "#    ", "#####"],
    "M": ["#   #", "## ##", "# # #", "#   #", "#   #"],
    "N": ["#   #", "##  #", "# # #", "#  ##", "#   #"],
    "O": [" ### ", "#   #", "#   #", "#   #", " ### "],
    "P": ["#### ", "#   #", "#### ", "#    ", "#    "],
    "Q": [" ### ", "#   #", "# # #", "#  # ", " ## #"],
    "R": ["#### ", "#   #", "#### ", "#  # ", "#   #"],
    "S": [" ####", "#    ", " ### ", "    #", "#### "],
    "T": ["#####", "  #  ", "  #  ", "  #  ", "  #  "],
    "U": ["#   #", "#   #", "#   #", "#   #", " ### "],
    "V": ["#   #", "#   #", "#   #", " # # ", "  #  "],
    "W": ["#   #", "#   #", "# # #", "## ##", "#   #"],
    "X": ["#   #", " # # ", "  #  ", " # # ", "#   #"],
    "Y": ["#   #", " # # ", "  #  ", "  #  ", "  #  "],
    "Z": ["#####", "   # ", "  #  ", " #   ", "#####"],
    "0": [" ### ", "#  ##", "# # #", "##  #", " ### "],
    "1": ["  #  ", " ##  ", "  #  ", "  #  ", "#####"],
    "2": [" ### ", "#   #", "  ## ", " #   ", "#####"],
    "3": ["#### ", "    #", " ### ", "    #", "#### "],
    "4": ["#  # ", "#  # ", "#####", "   # ", "   # "],
    "5": ["#####", "#    ", "#### ", "    #", "#### "],
    "6": [" ####", "#    ", "#### ", "#   #", " ### "],
    "7": ["#####", "   # ", "  #  ", " #   ", " #   "],
    "8": [" ### ", "#   #", " ### ", "#   #", " ### "],
    "9": [" ### ", "#   #", " ####", "    #", "#### "],
    " ": ["     ", "     ", "     ", "     ", "     "],
    "-": ["     ", "     ", "#####", "     ", "     "],
    ".": ["     ", "     ", "     ", "     ", "  #  "],
    "'": ["  #  ", "  #  ", "     ", "     ", "     "],
    "&": [" ##  ", "#  # ", " ##  ", "#  ##", " ## #"],
    "!": ["  #  ", "  #  ", "  #  ", "     ", "  #  "],
    "?": [" ### ", "#   #", "  ## ", "     ", "  #  "],
    "/": ["    #", "   # ", "  #  ", " #   ", "#    "],
    ",": ["     ", "     ", "     ", "  #  ", " #   "],
    ":": ["     ", "  #  ", "     ", "  #  ", "     "],
    "(": ["   # ", "  #  ", "  #  ", "  #  ", "   # "],
    ")": [" #   ", "  #  ", "  #  ", "  #  ", " #   "],
}
_FALLBACK = ["#####", "#   #", "#   #", "#   #", "#####"]  # box for unknown chars

# Bump of blank dot-columns between glyphs.
_GAP = 1


def text_width_dots(text: str, scale: int = 1) -> int:
    """Total dot-width the string occupies when rendered by draw_text."""
    if not text:
        return 0
    return (len(text) * (FONT_W + _GAP) - _GAP) * scale


def text_height_dots(scale: int = 1) -> int:
    return FONT_H * scale


def draw_text(canvas: Canvas, text: str, x0: int, y0: int, scale: int = 1) -> None:
    """Stamp `text` into `canvas` as bitmap-font dots, top-left at dot (x0, y0),
    enlarged by an integer `scale` (each font pixel → scale×scale dots). Dots
    outside the canvas are skipped, so this works with horizontal scrolling."""
    text = text.upper()
    cx = x0
    xs: list[int] = []
    ys: list[int] = []
    for ch in text:
        glyph = _GLYPHS.get(ch, _FALLBACK)
        for ry, rowpat in enumerate(glyph):
            for rx, cell in enumerate(rowpat):
                if cell != " ":
                    # Expand this font pixel into a scale×scale block of dots.
                    for sy in range(scale):
                        for sx in range(scale):
                            xs.append(cx + rx * scale + sx)
                            ys.append(y0 + ry * scale + sy)
        cx += (FONT_W + _GAP) * scale
    if not xs:
        return
    xa = np.array(xs)
    ya = np.array(ys)
    m = (xa >= 0) & (xa < canvas.gw) & (ya >= 0) & (ya < canvas.gh)
    if m.any():
        np.add.at(canvas.buf, (ya[m], xa[m]), 1.0)
