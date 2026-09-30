"""Finite installed-package check; no display, downloads, or persistent writes."""

import argparse
import os
import tempfile
from pathlib import Path

from compositor_linux import __version__, editing, engine, kernels, store
from compositor_linux.icons import icon
from compositor_linux.model import Document
from compositor_linux.text import render_text
from PySide6.QtWidgets import QApplication


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--background", action="store_true")
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_QPA_PLATFORMTHEME"] = ""
    app = QApplication([])
    kernels.library()  # Resolves all original C symbols and their checked signatures.
    if icon("text").pixmap(24, 24).isNull():
        raise RuntimeError("Bundled SVG icons could not render.")
    document = Document(256, 128)
    settings = {
        "version": 1,
        "text": "Compositor Linux",
        "family": "Liberation Sans",
        "size": 20,
        "bold": False,
        "italic": False,
        "underline": False,
        "alignment": "Left",
        "color": [20, 40, 60],
    }
    raster = render_text(settings)
    if raster.getbbox() is None:
        raise RuntimeError("Text rendering produced no pixels.")
    editing.put_text(document, settings, raster, (8, 8))
    expected = engine.render(document)
    with tempfile.TemporaryDirectory(prefix="compositor-check-") as directory:
        project = Path(directory) / "Check.comp"
        store.save(document, project)
        if engine.render(store.load(project)).tobytes() != expected.tobytes():
            raise RuntimeError("Saved project does not match the rendered image.")
        store.export_image(document, Path(directory) / "Check.png")
    if args.background:
        import onnxruntime

        if "CPUExecutionProvider" not in onnxruntime.get_available_providers():
            raise RuntimeError("ONNX Runtime has no CPU inference provider.")
    print(f"Compositor Linux {__version__}: kernels, SVG, text, project round-trip and PNG OK")
    app.quit()


if __name__ == "__main__":
    main()
