"""Scalable, bundled monochrome icons for the dark editor chrome."""

from functools import cache
from pathlib import Path

from PySide6.QtGui import QIcon

ICON_DIRECTORY = Path(__file__).parent / "assets" / "icons"


@cache
def icon(name: str) -> QIcon:
    path = ICON_DIRECTORY / f"{name}.svg"
    if not path.is_file():
        raise ValueError(f"Unknown editor icon: {name}")
    # Qt's SVG engine renders at the requested size and device pixel ratio.
    return QIcon(str(path))
