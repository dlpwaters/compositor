"""Explicit, finite GUI exercise for live Wayland validation and visual QA."""

import json
import shutil
import subprocess
import traceback
from io import BytesIO

from PIL import Image, ImageDraw
from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from . import editing, engine, store
from .tasks import run_task


def press_key(widget, key):
    QTest.keyClick(widget, key)
    # Fcitx forwards native keys asynchronously through the desktop session bus.
    QTest.qWait(250)


def exercise(application, window, output):
    output.mkdir(parents=True, exist_ok=True)
    try:
        image = Image.new("RGBA", (720, 480), (37, 52, 78, 255))
        draw = ImageDraw.Draw(image)
        for y in range(480):
            draw.line(
                (0, y, 720, y),
                fill=(35 + round(y / 8), 65 + round(y / 5), 110 + round(y / 6), 255),
            )
        draw.ellipse((390, 50, 590, 250), fill=(244, 193, 84, 255))
        draw.polygon([(0, 480), (200, 220), (370, 480)], fill=(25, 47, 73, 255))
        draw.polygon([(230, 480), (480, 280), (720, 480)], fill=(32, 65, 85, 255))

        def create():
            dialog = QApplication.activeModalWidget()
            dialog.fields["Width"].setValue(720)
            dialog.fields["Height"].setValue(480)
            dialog.accept()

        QTimer.singleShot(10, create)
        window.actions["New Canvas…"].trigger()
        history = window.history
        assert history.document.layer() is not None
        editing.import_layers(history, [("Landscape", image)])
        id = editing.new_layer(history)
        window.selected = {id}
        window.foreground = (248, 126, 102, 255)
        window.set_tool("brush")
        window.options["diameter"] = 28
        window.refresh()
        window.canvas.fit()
        application.processEvents()
        window.activateWindow()
        window.canvas.setFocus()
        assert QTest.qWaitForWindowActive(window, 3000), (
            "The native editor did not receive window focus"
        )
        start = window.canvas.screen((100, 360)).toPoint()
        end = window.canvas.screen((320, 350)).toPoint()
        QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(window.canvas, end, delay=30)
        QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=end)
        application.processEvents()
        assert history.document.layer().image is not None
        assert history.document.layer().image.getpixel((100, 360))[3] == 255
        window.actions["Undo"].trigger()
        assert history.document.layer().image is None
        window.actions["Redo"].trigger()
        assert history.document.layer().image is not None
        reference = engine.render(history.document).tobytes()
        window.set_tool("gradient")
        QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(window.canvas, end)
        QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=end)
        assert window.canvas.pending and window.canvas.pending["kind"] == "gradient"
        assert engine.render(history.document).tobytes() == reference
        window.canvas.setFocus()
        application.processEvents()
        assert window.canvas.hasFocus(), "Native canvas focus was lost"
        press_key(window.canvas, Qt.Key.Key_Escape)
        assert window.canvas.pending is None, "Escape did not reach the native canvas"
        assert engine.render(history.document).tobytes() == reference, (
            "Cancelled gradient changed the document"
        )
        window.actions["Transform…"].trigger()
        window.numeric_transform("x", 20)
        assert engine.render(history.document).tobytes() == reference
        press_key(window.canvas, Qt.Key.Key_Return)
        window.actions["Undo"].trigger()
        assert engine.render(history.document).tobytes() == reference
        window.set_tool("crop")
        corner, inside = (window.canvas.screen(point).toPoint() for point in ((0, 0), (40, 40)))
        QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=corner)
        QTest.mouseMove(window.canvas, inside)
        QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=inside)
        assert history.document.width == 720
        press_key(window.canvas, Qt.Key.Key_Return)
        assert history.document.width < 720
        window.actions["Undo"].trigger()
        assert engine.render(history.document).tobytes() == reference
        window.set_tool("move")
        targeted = []

        def hue_drag():
            dialog = window.filter_dialog
            try:
                dialog.canvas_mode.setCurrentText("Targeted adjustment")
                point = window.canvas.screen((180, 355)).toPoint()
                QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=point)
                QTest.mouseMove(window.canvas, point + QPoint(40, 0))
                QTest.mouseRelease(
                    window.canvas, Qt.MouseButton.LeftButton, pos=point + QPoint(40, 0)
                )
                targeted.append(dialog.value("Saturation"))
                dialog.preview.emit(dialog.result_settings())
            finally:
                dialog.reject()

        QTimer.singleShot(20, hue_drag)
        window.actions["Hue/Saturation…"].trigger()
        assert targeted == [20] and engine.render(history.document).tobytes() == reference
        calibrated = []

        def levels_sample():
            dialog = window.filter_dialog
            if dialog is None:
                QTimer.singleShot(10, levels_sample)
                return
            try:
                dialog.canvas_mode.setCurrentText("White")
                QTest.mouseClick(
                    window.canvas,
                    Qt.MouseButton.LeftButton,
                    pos=window.canvas.screen((180, 355)).toPoint(),
                )
                dialog.fields["Channel"].setCurrentText("Red")
                calibrated.append(dialog.value("Input white"))
            finally:
                dialog.reject()

        QTimer.singleShot(20, levels_sample)
        window.actions["Levels…"].trigger()
        assert calibrated == [248] and engine.render(history.document).tobytes() == reference
        corner, inside = (window.canvas.screen(point).toPoint() for point in ((0, 0), (40, 20)))
        QTest.mousePress(
            window.canvas,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            pos=corner,
        )
        QTest.mouseMove(window.canvas, inside)
        QTest.mouseRelease(
            window.canvas,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            pos=inside,
        )
        assert (
            window.canvas.pending["corners"]
            and engine.render(history.document).tobytes() == reference
        )
        press_key(window.canvas, Qt.Key.Key_Return)
        window.actions["Undo"].trigger()
        assert engine.render(history.document).tobytes() == reference
        clipboard = QApplication.clipboard()
        external_clipboard = False
        prior = QMimeData()
        current_mime = clipboard.mimeData()
        for format in current_mime.formats():
            prior.setData(format, current_mime.data(format))
        try:
            before_copy = engine.render(history.document)
            window.actions["Copy Merged"].trigger()
            assert not clipboard.image().isNull()
            if shutil.which("wl-paste") and QApplication.platformName() == "wayland":
                received = run_task(
                    window,
                    "Verifying Wayland clipboard…",
                    lambda: subprocess.check_output(
                        ["wl-paste", "--type", "image/png"], timeout=10
                    ),
                )
                with Image.open(BytesIO(received)) as external:
                    assert external.convert("RGBA").tobytes() == before_copy.tobytes()
                external_clipboard = True
            window.actions["Paste"].trigger()
            assert history.document.layer().image.size == before_copy.size
            assert history.document.layer().image.tobytes() == before_copy.tobytes()
            window.actions["Undo"].trigger()
        finally:
            clipboard.setMimeData(prior)
        project_path = output / "wayland-smoke.comp"
        history.path = project_path
        window.actions["Save"].trigger()
        assert not history.dirty and project_path.is_dir()
        reloaded = store.load(project_path)
        assert engine.render(reloaded).tobytes() == engine.render(history.document).tobytes()
        store.export_image(reloaded, output / "wayland-smoke.png")
        store.export_image(reloaded, output / "wayland-smoke.jpg", "JPEG", 92)
        window.set_tool("move")
        window.refresh()
        application.processEvents()
        window.grab().save(str(output / "editor.png"))
        report = dict(
            platform=QApplication.platformName(),
            native_window_visible=window.isVisible(),
            external_wayland_clipboard_verified=external_clipboard,
            checks=[
                "native New Canvas dialog and initial editable layer",
                "Qt brush pointer events",
                "menu undo/redo",
                "pending gradient preview and Escape cancellation",
                "pending transform Apply and undo",
                "native crop handle, Apply and undo",
                "Hue/Saturation targeted canvas drag and cancellation",
                "Levels original-pixel white sampling and cancellation",
                "native distortion preview, Apply and undo",
                "native clipboard Copy Merged/Paste with prior content restored",
                "menu project save",
                "v7 save/reopen render comparison",
                "PNG/JPEG export",
                "window capture",
            ],
        )
        (output / "report.json").write_text(json.dumps(report, indent=2))

        def finish():
            window.close()
            application.exit(0)

        QTimer.singleShot(800, finish)
    except BaseException:
        (output / "failure.txt").write_text(traceback.format_exc())
        for h in window.projects:
            h.saved_revision = h.revision
        window.close()
        application.exit(1)
