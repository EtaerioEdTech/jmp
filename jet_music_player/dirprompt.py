"""A modal prompt for entering a new music root directory.

`d` in the browser opens this: a single-line path input at the bottom of the
screen. Enter loads the typed directory, Esc cancels. Tab completes the path
against the filesystem so deep external-media paths are quick to reach.

Kept transparent and minimal to match the rest of the UI.
"""

from __future__ import annotations

from pathlib import Path

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class DirPrompt(ModalScreen[str | None]):
    """Ask for a directory path. Dismisses with the path, or None if cancelled.

    The result is a raw string (not yet validated as a real directory) — the
    caller re-scans and reports if nothing was found there.
    """

    def __init__(self, start: str = "") -> None:
        super().__init__()
        self._start = start

    def compose(self) -> ComposeResult:
        with Vertical(id="dirprompt-box"):
            yield Label("New music directory:", id="dirprompt-label")
            yield Input(
                value=self._start,
                placeholder="/path/to/music",
                id="dirprompt-input",
            )
            yield Label("↵ load    ⇥ complete    esc cancel", id="dirprompt-hint")

    def on_mount(self) -> None:
        inp = self.query_one("#dirprompt-input", Input)
        inp.focus()
        # Put the cursor at the end so an existing path is easy to extend.
        inp.cursor_position = len(inp.value)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
        elif event.key == "tab":
            # Tab completes the path rather than moving focus.
            event.stop()
            event.prevent_default()
            self._complete()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        value = event.value.strip()
        self.dismiss(value or None)

    # ---- path completion ----

    def _complete(self) -> None:
        """Complete the current input against the filesystem.

        If the partial path uniquely matches one entry, fill it in; if several
        share a common prefix, extend to that prefix. Directories get a
        trailing "/" so completion can continue into them.
        """
        inp = self.query_one("#dirprompt-input", Input)
        text = inp.value
        matches, base = _path_matches(text)
        if not matches:
            return
        if len(matches) == 1:
            completed = str(base / matches[0])
            if (base / matches[0]).is_dir():
                completed += "/"
        else:
            common = _common_prefix(matches)
            if not common:
                return
            completed = str(base / common)
        inp.value = _expand_display(text, completed)
        inp.cursor_position = len(inp.value)


def _path_matches(text: str) -> tuple[list[str], Path]:
    """Return (matching entry names, their parent dir) for a partial path.

    Splits `text` into a parent directory and a partial final component, then
    lists the parent for entries starting with that partial component.
    """
    raw = Path(text).expanduser() if text else Path.home()
    if text.endswith("/"):
        parent, partial = raw, ""
    else:
        parent, partial = raw.parent, raw.name
    try:
        names = sorted(p.name for p in parent.iterdir())
    except (OSError, ValueError):
        return [], parent
    matches = [n for n in names if n.startswith(partial)]
    return matches, parent


def _common_prefix(names: list[str]) -> str:
    """Longest common leading substring of `names` (empty if none)."""
    if not names:
        return ""
    first, last = names[0], names[-1]  # sorted, so these bound the set
    i = 0
    while i < len(first) and i < len(last) and first[i] == last[i]:
        i += 1
    return first[:i]


def _expand_display(original: str, completed: str) -> str:
    """Preserve a leading "~" in the shown value if the user typed one."""
    if original.startswith("~"):
        home = str(Path.home())
        if completed.startswith(home):
            return "~" + completed[len(home):]
    return completed
