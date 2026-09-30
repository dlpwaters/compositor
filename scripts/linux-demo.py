"""Replay a synthetic poster demo through native Qt controls and save its editable project."""

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

from compositor_linux import editing, engine, store
from compositor_linux.app import STYLE, MainWindow
from compositor_linux.dialogs import NewLayerDialog, TextLayerDialog
from compositor_linux.model import Document, Layer, Transform
from compositor_linux.text import render_text
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QColorDialog, QDialogButtonBox


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output", type=Path, help="New output directory for project, PNGs and report"
    )
    parser.add_argument("--fast", action="store_true", help="Run without presentation pauses")
    parser.add_argument(
        "--start-file", type=Path, help="Wait up to 60 seconds for a recording trigger"
    )
    parser.add_argument("--hold", type=int, default=10, choices=range(31), metavar="0..30")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    app = QApplication([])
    app.setApplicationName("Compositor Linux")
    app.setDesktopFileName("compositor")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(output / "settings")
    )
    window = MainWindow()
    window.showFullScreen()
    checks, phases = [], []
    started = time.monotonic()

    def wait(milliseconds):
        QTest.qWait(20 if args.fast else milliseconds)

    def phase(label):
        phases.append({"seconds": round(time.monotonic() - started, 2), "label": label})
        window.statusBar().showMessage(label)
        print(label, flush=True)
        app.processEvents()

    def capture(name):
        app.processEvents()
        window.grab().save(str(output / name))

    def guard(callback):
        def wrapped():
            try:
                callback()
            except Exception:
                traceback.print_exc()
                if QApplication.activeModalWidget():
                    QApplication.activeModalWidget().reject()
                app.exit(1)

        return wrapped

    def accept(dialog):
        buttons = dialog.findChild(QDialogButtonBox)
        QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.LeftButton)

    def select(layer_id):
        window.history.document.active = layer_id
        window.selected = {layer_id}
        window.refresh()

    def exercise():
        phase("Start with a canvas")
        window.add_project(Document(1200, 800))
        window.canvas.fit()
        wait(1500)

        def backdrop():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, NewLayerDialog)
            dialog.name.setText("Warm paper")
            wait(600)
            dialog.fill.setCurrentText("Solid color")
            dialog.color.set_color((245, 240, 228))
            wait(2200)
            dialog.grab().save(str(output / "solid-layer.png"))
            capture("solid-layer-context.png")
            accept(dialog)

        phase("Create a solid-color layer")
        QTimer.singleShot(150, guard(backdrop))
        window.actions["New Layer…"].trigger()
        backdrop_id = window.history.document.active
        assert window.history.document.layer().image.getpixel((0, 0)) == (245, 240, 228, 255)
        checks.append("native New Layer solid-color fill")
        wait(1000)

        phase("Load the sample artwork")
        with window.history.edit("Sample artwork") as document:
            for name, bounds, color, kind in (
                ("Terracotta circle", (750, 130, 350, 350), (206, 91, 65), "Ellipse"),
                ("Midnight circle", (650, 460, 270, 270), (28, 51, 64), "Ellipse"),
                ("Fine rule", (80, 101, 1020, 3), (28, 51, 64), "Rectangle"),
            ):
                style = dict(editing.solid_style(color), kind=kind)
                editing.inserted(
                    document,
                    Layer(
                        name,
                        Transform(*bounds),
                        shape=style,
                        image=engine.shape_pixels(tuple(bounds[2:]), style),
                    ),
                )
            for value, size, location, color in (
                ("COMPOSITOR LINUX / ARCH + OMARCHY", 24, (80, 57), (28, 51, 64)),
                ("Layers. Pixels. Possibilities.", 35, (80, 458), (28, 51, 64)),
                (
                    "A little less friction.\nA little more room to create.",
                    26,
                    (80, 528),
                    (80, 94, 94),
                ),
                ("01 / NATIVE WAYLAND", 20, (80, 720), (80, 94, 94)),
            ):
                settings = dict(
                    window.text_options,
                    text=value,
                    size=size,
                    family="Liberation Sans",
                    bold=False,
                    color=list(color),
                )
                editing.put_text(document, settings, render_text(settings), location)
        window.refresh()
        wait(1500)

        def headline():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, TextLayerDialog)
            dialog.family.setCurrentFont(QFont("Liberation Sans"))
            dialog.fields["Size (px)"].setValue(104)
            dialog.fields["Bold"].setChecked(True)
            dialog.color.set_color((28, 51, 64))
            wait(700)
            QTest.keyClicks(dialog.editor, "MAKE", delay=100 if not args.fast else 0)
            QTest.keyClick(dialog.editor, Qt.Key_Return)
            QTest.keyClicks(dialog.editor, "SOMETHING.", delay=100 if not args.fast else 0)
            dialog.render_preview()
            wait(2300)
            dialog.grab().save(str(output / "text-layer.png"))
            capture("text-layer-context.png")
            accept(dialog)

        phase("Add editable text with the Text tool (T)")
        window.foreground = (28, 51, 64, 255)
        QTest.mouseClick(window.tool_buttons["text"], Qt.LeftButton)
        QTimer.singleShot(150, guard(headline))
        QTest.mouseClick(
            window.canvas, Qt.LeftButton, pos=window.canvas.screen((80, 160)).toPoint()
        )
        headline_id = window.history.document.active
        assert window.history.document.layer().live_text.settings["text"] == "MAKE\nSOMETHING."
        checks.append("native Text tool, typing, preview and commit")
        wait(2500)

        def edit_text():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, TextLayerDialog)
            dialog.editor.selectAll()
            QTest.keyClicks(dialog.editor, "EDITABLE", delay=90 if not args.fast else 0)
            QTest.keyClick(dialog.editor, Qt.Key_Return)
            QTest.keyClicks(dialog.editor, "TYPE.", delay=90 if not args.fast else 0)
            dialog.render_preview()
            wait(1800)
            accept(dialog)

        phase("Edit the text again")
        QTimer.singleShot(150, guard(edit_text))
        window.actions["Edit Layer Content…"].trigger()
        assert window.history.document.layer().live_text.settings["text"] == "EDITABLE\nTYPE."
        wait(2200)
        phase("Undo restores editable text")
        window.actions["Undo"].trigger()
        assert window.history.document.layer().live_text.settings["text"] == "MAKE\nSOMETHING."
        checks.append("text edit and undo")
        wait(1800)

        def recolor():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, QColorDialog)
            dialog.setCurrentColor(QColor(221, 231, 221))
            wait(1800)
            dialog.accept()

        phase("Recolor a solid layer")
        select(backdrop_id)
        QTimer.singleShot(150, guard(recolor))
        window.actions["Edit Layer Content…"].trigger()
        assert window.history.document.layer().image.getpixel((0, 0)) == (221, 231, 221, 255)
        wait(1500)
        window.actions["Undo"].trigger()
        assert window.history.document.layer().image.getpixel((0, 0)) == (245, 240, 228, 255)
        checks.append("native solid-layer recoloring and undo")
        select(headline_id)
        QTest.mouseClick(window.tool_buttons["move"], Qt.LeftButton)
        wait(1000)

        phase("Save the editable project and export a PNG")
        path = output / "Studio.comp"
        store.save(window.history.document, path)
        expected = engine.render(window.history.document)
        reopened = store.load(path)
        assert engine.render(reopened).tobytes() == expected.tobytes()
        assert reopened.layer(headline_id).live_text.settings["text"] == "MAKE\nSOMETHING."
        store.export_image(reopened, output / "poster.png")
        window.history.saved(path)
        window.refresh()
        capture("editor.png")
        checks.append("project reopen identical pixels and editable text; PNG export")
        wait(2500)
        phase("Compositor Linux — Arch + Omarchy")
        (output / "report.json").write_text(
            json.dumps(
                {"platform": app.platformName(), "checks": checks, "phases": phases}, indent=2
            )
        )
        # Leave the final canvas visible long enough to stop an external recorder.
        wait(args.hold * 1000)
        for history in window.projects:
            history.saved_revision = history.revision
        window.close()
        app.quit()

    def start():
        nonlocal started
        if args.start_file and not args.start_file.exists():
            if time.monotonic() - started > 60:
                print("Timed out waiting for the recording trigger.", file=sys.stderr)
                app.exit(1)
            else:
                QTimer.singleShot(100, start)
            return
        started = time.monotonic()
        guard(exercise)()

    QTimer.singleShot(500, start)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
