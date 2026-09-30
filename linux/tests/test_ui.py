import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from compositor_linux.app import MainWindow
from compositor_linux.dialogs import FilterDialog, NewLayerDialog, TextLayerDialog
from compositor_linux.model import Document, Layer
from PIL import Image
from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QColorDialog, QTabBar


@pytest.fixture(scope="session")
def application(tmp_path_factory):
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(tmp_path_factory.mktemp("settings")),
    )
    return app


@pytest.fixture
def window(application):
    window = MainWindow()
    window.show()
    application.processEvents()
    yield window
    for h in window.projects:
        h.saved_revision = h.revision
    window.close()
    application.processEvents()


def document():
    d = Document(128, 128)
    layer = Layer.blank(128, 128)
    d.layers.append(layer)
    d.active = layer.id
    return d


def test_native_brush_event_path_and_undo(window, application):
    window.add_project(document())
    window.set_tool("brush")
    window.canvas.fit()
    window.options["diameter"] = 12
    window.foreground = (255, 0, 0, 255)
    point = window.canvas.screen((64, 64)).toPoint()
    QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(window.canvas, point + QPoint(30, 0), delay=20)
    QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=point + QPoint(30, 0))
    application.processEvents()
    image = window.history.document.layer().image
    assert image is not None and image.getpixel((64, 64)) == (255, 0, 0, 255)
    assert len(window.history.undo_stack) == 1
    window.actions["Undo"].trigger()
    assert window.history.document.layer().image is None
    window.actions["Redo"].trigger()
    assert window.history.document.layer().image is not None


def test_accessible_tool_toggle_selects_tool(window, application):
    window.add_project(document())
    window.tool_buttons["brush"].setChecked(True)
    application.processEvents()
    assert window.tool == "brush"
    assert not window.tool_buttons["move"].isChecked()


@pytest.mark.parametrize("solid", [False, True])
def test_new_layer_dialog_contents_and_active_selection(window, application, solid):
    from compositor_linux import engine

    window.add_project(document())

    def configure():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, NewLayerDialog)
        dialog.name.setText("Backdrop")
        dialog.fill.setCurrentText("Solid color" if solid else "Transparent")
        dialog.color.set_color((20, 50, 80))
        dialog.accept()

    QTimer.singleShot(10, configure)
    window.actions["New Layer…"].trigger()
    layer = window.history.document.layer()
    assert layer.name == "Backdrop" and window.selected == {layer.id}
    if solid:
        assert engine.render(window.history.document).getpixel((64, 64)) == (20, 50, 80, 255)
        assert layer.shape["kind"] == "Rectangle"
    else:
        assert layer.image is None and layer.shape is None
    assert len(window.history.undo_stack) == 1


def test_cancelled_layer_and_text_dialogs_leave_no_edit(window, application):
    window.add_project(document())
    revision = window.history.revision
    QTimer.singleShot(10, lambda: QApplication.activeModalWidget().reject())
    window.actions["New Layer…"].trigger()
    QTimer.singleShot(10, lambda: QApplication.activeModalWidget().reject())
    window.actions["New Text Layer…"].trigger()
    application.processEvents()
    assert window.history.revision == revision and len(window.history.document.layers) == 1
    assert not window.history.undo_stack and window.preview_document is None
    assert window.content_dialog is None


def test_closing_owner_cancels_text_preview_and_keeps_project(window, application):
    window.add_project(document())

    def close_owner():
        dialog = QApplication.activeModalWidget()
        dialog.editor.setPlainText("Unsaved preview")
        dialog.render_preview()
        assert window.preview_document is not None
        window.close()

    QTimer.singleShot(10, close_owner)
    window.actions["New Text Layer…"].trigger()
    assert window.isVisible() and len(window.projects) == 1
    assert window.preview_document is None and window.content_dialog is None
    assert not window.history.undo_stack


def test_text_rendering_is_plain_and_rejects_oversized_raster(application):
    from compositor_linux.text import render_text
    from test_core import text_settings

    settings = text_settings()
    settings["text"] = '<img src="https://example.com/private.png">'
    assert render_text(settings).getchannel("A").getbbox() is not None
    settings.update(text="W" * 1000, size=2048)
    with pytest.raises(ValueError):
        render_text(settings)


def test_double_click_solid_layer_recolors_as_one_undo_step(window, application):
    from compositor_linux import engine

    window.add_project(document())
    window.create_layer("Solid", (20, 50, 80))
    window.refresh()

    def recolor():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QColorDialog)
        from PySide6.QtGui import QColor

        dialog.setCurrentColor(QColor(200, 80, 40))
        dialog.accept()

    item = window.tree.topLevelItem(0)
    point = window.tree.visualRect(window.tree.indexFromItem(item, 0)).center()
    QTest.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTimer.singleShot(10, recolor)
    QTest.mouseDClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert engine.render(window.history.document).getpixel((64, 64)) == (200, 80, 40, 255)
    assert len(window.history.undo_stack) == 2
    window.history.undo()
    assert engine.render(window.history.document).getpixel((64, 64)) == (20, 50, 80, 255)


def test_edit_content_availability_follows_tree_selection(window, application):
    window.add_project(document())
    window.create_layer("Solid", (20, 50, 80))
    window.refresh()
    action = window.actions["Edit Layer Content…"]
    assert action.isEnabled()
    window.tree.setCurrentItem(window.tree.topLevelItem(1))
    assert not action.isEnabled()
    window.tree.setCurrentItem(window.tree.topLevelItem(0))
    assert action.isEnabled()


def test_text_tool_canvas_creation_edit_roundtrip_and_raster_fallback(
    window, application, tmp_path
):
    from compositor_linux import engine, store

    window.add_project(document())
    QTest.keyClick(window.canvas, Qt.Key.Key_T)
    assert window.tool == "text"
    previews = []

    def configure():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, TextLayerDialog)
        dialog.editor.setPlainText("Hello Ω\nWorld")
        dialog.fields["Size (px)"].setValue(18)
        dialog.fields["Bold"].setChecked(True)
        dialog.fields["Alignment"].setCurrentText("Center")
        dialog.color.set_color((60, 160, 240))
        dialog.render_preview()
        previews.append(window.preview_document is not None and not window.history.undo_stack)
        dialog.accept()

    QTimer.singleShot(10, configure)
    QTest.mouseClick(
        window.canvas, Qt.MouseButton.LeftButton, pos=window.canvas.screen((16, 16)).toPoint()
    )
    layer = window.history.document.layer()
    assert previews == [True]
    assert layer.live_text.settings["text"] == "Hello Ω\nWorld"
    assert layer.live_text.settings["color"] == [60, 160, 240]
    assert layer.image.getchannel("A").getbbox() is not None
    assert len(window.history.undo_stack) == 1
    assert window.preview_document is None and window.content_dialog is None
    path = tmp_path / "text.comp"
    store.save(window.history.document, path)
    loaded = store.load(path)
    assert loaded.layer().live_text.settings == layer.live_text.settings
    expected = engine.render(loaded).tobytes()
    import json

    manifest_path = path / "manifest.json"
    metadata = json.loads(manifest_path.read_text())
    del next(r for r in metadata["layers"] if r["id"] == layer.id)["linuxText"]
    manifest_path.write_text(json.dumps(metadata))
    fallback = store.load(path)
    assert fallback.layer().live_text is None and engine.render(fallback).tobytes() == expected

    def edit():
        dialog = QApplication.activeModalWidget()
        dialog.editor.setPlainText("Changed")
        dialog.accept()

    QTimer.singleShot(10, edit)
    window.actions["Edit Layer Content…"].trigger()
    assert window.history.document.layer().id == layer.id
    assert window.history.document.layer().live_text.settings["text"] == "Changed"
    window.history.undo()
    assert window.history.document.layer().live_text.settings["text"] == "Hello Ω\nWorld"
    window.history.redo()
    assert window.history.document.layer().live_text.settings["text"] == "Changed"


def test_text_edit_preserves_rotated_flipped_anchor_and_scale(application):
    from dataclasses import replace

    from compositor_linux import editing
    from compositor_linux.model import History
    from compositor_linux.text import render_text
    from test_core import text_settings

    history = History(document())
    settings = text_settings()
    with history.edit("Text") as d:
        layer = editing.put_text(d, settings, render_text(settings), (20, 30))
        layer.transform = replace(
            layer.transform,
            width=layer.transform.width * 2,
            height=layer.transform.height * 1.5,
            rotation=37,
            flip_x=True,
        )
    layer = history.document.layer()
    old = layer.transform
    anchor = old.point(1, 0)
    scale = old.width / layer.image.width, old.height / layer.image.height
    with history.edit("Edit Text") as d:
        changed = dict(settings, text="Longer Ω text")
        editing.put_text(d, changed, render_text(changed), layer_id=layer.id)
    layer = history.document.layer()
    assert layer.transform.point(1, 0) == pytest.approx(anchor, abs=1e-8)
    assert (
        layer.transform.width / layer.image.width,
        layer.transform.height / layer.image.height,
    ) == pytest.approx(scale)
    assert layer.transform.rotation == 37 and layer.transform.flip_x


def test_tabs_keep_documents_and_viewports(window, application):
    first = window.add_project(document())
    window.canvas.zoom_to(2)
    second = window.add_project(Document(256, 256))
    window.switch_project(0)
    assert window.history is first and window.canvas.zoom == 2
    window.switch_project(1)
    assert window.history is second


def test_tab_close_button_tracks_its_project_after_indices_shift(window, application):
    projects = [window.add_project(document()) for _ in range(3)]
    for project in projects:
        project.saved_revision = project.revision
    side = QTabBar.ButtonPosition.RightSide
    window.tabs.tabButton(0, side).click()
    assert window.projects == projects[1:]
    window.tabs.tabButton(1, side).click()
    assert window.projects == [projects[1]]
    assert window.history is projects[1]


def test_filter_cancel_does_not_modify_document(window, application):
    d = document()
    d.layer().image = Image.new("RGBA", (128, 128), (100, 120, 140, 255))
    window.add_project(d)
    from PySide6.QtCore import QTimer

    def cancel():
        for widget in application.topLevelWidgets():
            if isinstance(widget, FilterDialog):
                widget.fields["Exposure"].setValue(2)
                widget.preview.emit(widget.result_settings())
                widget.reject()

    QTimer.singleShot(10, cancel)
    before = d.layer().image.tobytes()
    window.filter("Exposure")
    assert d.layer().image.tobytes() == before
    assert not window.history.undo_stack and window.preview_document is None


def test_hue_range_settings_roundtrip(application):
    dialog = FilterDialog("Hue/Saturation")
    dialog.fields["Range"].setCurrentText("Reds")
    dialog.fields["Hue"].setValue(45)
    first = dialog.result_settings()
    second = dialog.result_settings()
    assert first == second
    assert isinstance(first["hsvSettings"]["adjustments"], list)
    reload = FilterDialog("Hue/Saturation", first)
    assert reload.value("Hue") == 45 and reload.value("Range") == "Reds"


def test_layer_mask_target_is_native_selection(window, application):
    d = document()
    d.layer().mask = Image.new("L", (1, 1), 255)
    window.add_project(d)
    window.layer_clicked(window.tree.topLevelItem(0), 1)
    assert window.mask_target
    window.set_tool("brush")
    window.options["mask_white"] = False
    center = window.canvas.screen((64, 64)).toPoint()
    QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=center)
    assert window.history.document.layer().mask.getpixel((64, 64)) == 0


def test_cancel_new_adjustment_leaves_no_layer_or_undo(window, application):
    from PySide6.QtCore import QTimer

    window.add_project(document())
    QTimer.singleShot(
        10,
        lambda: next(
            widget
            for widget in application.topLevelWidgets()
            if isinstance(widget, FilterDialog) and widget.isVisible()
        ).reject(),
    )
    window.new_adjustment("Exposure")
    assert len(window.history.document.layers) == 1
    assert not window.history.undo_stack


def test_worker_result_and_exception_use_native_event_loop(window):
    from compositor_linux.tasks import run_task

    assert run_task(window, "Test job", lambda: 42) == 42
    with pytest.raises(ValueError, match="job error"):
        run_task(window, "Test error", lambda: (_ for _ in ()).throw(ValueError("job error")))


def test_jpeg_preview_encodes_selected_transparency_matte(application):
    from io import BytesIO

    from compositor_linux.dialogs import JPEGDialog

    dialog = JPEGDialog(Image.new("RGBA", (16, 16)), 144)
    dialog.matte = (30, 100, 220)
    dialog.timer.stop()
    dialog.update_preview()
    with Image.open(BytesIO(dialog.data)) as image:
        color = image.getpixel((8, 8))
        assert all(abs(a - b) <= 2 for a, b in zip(color, dialog.matte))
        assert image.info["dpi"] == (144, 144)
    dialog.close()


def test_subject_removal_changes_pixels_and_preserves_existing_mask(
    window, application, monkeypatch
):
    from compositor_linux import engine, models
    from compositor_linux.dialogs import ValuesDialog
    from PySide6.QtCore import QTimer

    d = document()
    d.layer().image = Image.new("RGBA", (128, 128), (100, 120, 140, 255))
    d.layer().mask = Image.new("L", (1, 1), 192)
    original_mask = d.layer().mask
    window.add_project(d)
    monkeypatch.setattr(models, "verified", lambda path: True)
    monkeypatch.setattr(engine, "subject_mask", lambda image, path: Image.new("L", image.size, 128))
    QTimer.singleShot(
        10,
        lambda: next(
            widget
            for widget in application.topLevelWidgets()
            if isinstance(widget, ValuesDialog) and widget.isVisible()
        ).accept(),
    )
    window.remove_background()
    assert window.history.document.layer().image.getpixel((50, 50))[3] == 128
    assert window.history.document.layer().mask is original_mask
    assert window.history.undo()
    assert window.history.document.layer().image.getpixel((50, 50))[3] == 255


def test_new_canvas_has_an_active_blank_layer_ready_to_paint(window, application):
    from compositor_linux.dialogs import SizeDialog
    from PySide6.QtCore import QTimer

    def create():
        dialog = next(
            widget
            for widget in application.topLevelWidgets()
            if isinstance(widget, SizeDialog) and widget.isVisible()
        )
        dialog.fields["Width"].setValue(128)
        dialog.fields["Height"].setValue(64)
        dialog.accept()

    QTimer.singleShot(10, create)
    window.new_canvas()
    assert window.history.document.layer() is not None
    assert window.history.document.layer().image is None
    assert window.history.document.layer().transform.width == 128
    assert not window.history.undo_stack


def test_alt_handle_resize_keeps_one_layer_and_its_center(window, application):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    window.add_project(document())
    window.set_tool("move")
    window.canvas.fit()
    initial = window.history.document.layer().transform
    point = window.canvas.screen(initial.point(1, 1)).toPoint()
    QTest.mousePress(
        window.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, pos=point
    )
    move = point + QPoint(20, 20)
    QApplication.sendEvent(
        window.canvas,
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(move),
            QPointF(window.canvas.mapToGlobal(move)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.AltModifier,
        ),
    )
    QTest.mouseRelease(
        window.canvas,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.AltModifier,
        pos=point + QPoint(20, 20),
    )
    assert len(window.history.document.layers) == 1
    final = window.history.document.layer().transform
    assert final.center == pytest.approx(initial.center, abs=1e-9, rel=0)
    assert final.width > initial.width


@pytest.mark.parametrize("adjustment", [False, True])
def test_levels_auto_samples_the_selection_and_live_adjustment_input(
    window, application, adjustment
):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QPushButton

    d = document()
    d.layer().image = Image.new("RGBA", (128, 128), (240, 240, 240, 255))
    d.layer().image.paste((40, 40, 40, 255), (0, 0, 64, 64))
    d.layer().image.paste((120, 120, 120, 255), (0, 32, 64, 64))
    d.selection = Image.new("L", (128, 128))
    d.selection.paste(255, (0, 0, 64, 64))
    window.add_project(d)
    sampled_white = []

    def auto():
        dialogs = [
            widget
            for widget in application.topLevelWidgets()
            if isinstance(widget, FilterDialog) and widget.isVisible()
        ]
        if not dialogs:
            QTimer.singleShot(10, auto)
            return
        dialog = dialogs[0]
        next(
            button for button in dialog.findChildren(QPushButton) if button.text() == "Auto"
        ).click()
        sampled_white.append(dialog.value("Input white"))
        dialog.accept()

    QTimer.singleShot(10, auto)
    if adjustment:
        window.new_adjustment("Levels")
        assert window.history.document.layer().adjustment["levels"]["ranges"][0]["white"] <= 120
    else:
        window.filter("Levels")
        assert window.history.document.layer().image.getpixel((20, 45))[:3] == (255, 255, 255)
    assert sampled_white and sampled_white[0] <= 120


@pytest.mark.parametrize("accept", [False, True])
def test_hue_targeted_native_canvas_drag_stays_pending_until_accepted(window, application, accept):
    from PySide6.QtCore import QTimer

    d = document()
    d.layer().image = Image.new("RGBA", (128, 128), (160, 60, 60, 255))
    before = d.layer().image.tobytes()
    window.add_project(d)
    observed = []

    def drag():
        dialog = window.filter_dialog
        try:
            dialog.canvas_mode.setCurrentText("Targeted adjustment")
            point = window.canvas.screen((64, 64)).toPoint()
            QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseMove(window.canvas, point + QPoint(80, 0))
            QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=point + QPoint(80, 0))
            observed.append(
                (dialog.value("Range"), dialog.value("Saturation"), bool(window.history.undo_stack))
            )
            dialog.preview.emit(dialog.result_settings())
        finally:
            dialog.accept() if accept else dialog.reject()

    QTimer.singleShot(10, drag)
    window.filter("Hue/Saturation")
    assert observed == [("Reds", 40, False)]
    assert window.filter_dialog is None and window.preview_document is None
    assert window.canvas.isEnabled() and window.toolbar.isEnabled()
    assert (window.history.document.layer().image.tobytes() != before) == accept
    assert bool(window.history.undo_stack) == accept


def test_hue_colorize_starts_at_original_defaults_and_lasso_shortcut(window, application):
    dialog = FilterDialog("Hue/Saturation")
    dialog.fields["Colorize"].setChecked(True)
    assert dialog.value("Hue") == 0 and dialog.value("Saturation") == 25
    assert not dialog.fields["Range"].isEnabled() and not dialog.canvas_mode.isEnabled()
    dialog.fields["Colorize"].setChecked(False)
    assert dialog.value("Saturation") == 0 and dialog.fields["Range"].isEnabled()
    dialog.fields["Range"].setCurrentText("Reds")
    dialog.fields["Invert range"].setChecked(True)
    dialog.fields["Hue"].setValue(90)
    dialog.reset_controls()
    assert dialog.value("Range") == "Master" and dialog.value("Hue") == 0
    assert not dialog.value("Invert range")
    dialog.close()
    window.add_project(document())
    QTest.keyClick(window.canvas, Qt.Key.Key_L)
    assert window.tool == "lasso"


@pytest.mark.parametrize("accept", [False, True])
def test_crop_native_handles_keep_canvas_unchanged_until_apply(window, application, accept):
    window.add_project(document())
    window.set_tool("crop")
    point = window.canvas.screen((0, 0)).toPoint()
    end = window.canvas.screen((24, 16)).toPoint()
    QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(window.canvas, end)
    QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=end)
    assert window.history.document.width == 128 and not window.history.undo_stack
    assert round(window.canvas.pending["rect"].width) == 104
    QTest.keyClick(window.canvas, Qt.Key.Key_Return if accept else Qt.Key.Key_Escape)
    assert window.canvas.pending is None
    assert window.history.document.width == (104 if accept else 128)
    if accept:
        window.actions["Undo"].trigger()
        assert window.history.document.width == 128


@pytest.mark.parametrize("finish", ["cancel", "apply", "switch"])
def test_gradient_native_preview_options_and_single_commit(window, application, finish):
    window.add_project(document())
    window.set_tool("gradient")
    start, end = (window.canvas.screen(point).toPoint() for point in ((24, 24), (100, 24)))
    QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(window.canvas, end)
    QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=end)
    assert window.history.document.layer().image is None and not window.history.undo_stack
    before = window.canvas.document.layer().image.tobytes()
    window.set_option("gradient_reverse", True)
    assert window.canvas.document.layer().image.tobytes() != before
    if finish == "switch":
        window.set_tool("move")
    else:
        QTest.keyClick(
            window.canvas, Qt.Key.Key_Escape if finish == "cancel" else Qt.Key.Key_Return
        )
    assert window.canvas.pending is None
    assert len(window.history.undo_stack) == (0 if finish == "cancel" else 1)
    assert (window.history.document.layer().image is not None) == (finish != "cancel")


@pytest.mark.parametrize("accept", [False, True])
def test_persistent_transform_numbers_and_drag_form_one_undo(window, application, accept):
    window.add_project(document())
    window.actions["Transform…"].trigger()
    window.numeric_transform("x", 10)
    window.numeric_transform("y", 20)
    initial = window.history.document.layer().transform
    assert initial.x == 0 and initial.y == 0 and not window.history.undo_stack
    assert window.canvas.document.layer().transform.x == 10
    point = window.canvas.screen((60, 60)).toPoint()
    QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseMove(window.canvas, point + QPoint(30, 20))
    QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=point + QPoint(30, 20))
    assert not window.history.undo_stack
    QTest.keyClick(window.canvas, Qt.Key.Key_Return if accept else Qt.Key.Key_Escape)
    assert len(window.history.undo_stack) == int(accept)
    assert (window.history.document.layer().transform.x != initial.x) == accept


def test_subtracting_entire_selection_does_not_turn_next_paint_into_select_all(window, application):
    d = document()
    d.layer().image = Image.new("RGBA", (128, 128), (60, 80, 100, 255))
    d.selection = Image.new("L", (128, 128), 255)
    window.add_project(d)
    window.options["selection_mode"] = "Subtract"
    window.canvas.set_selection(Image.new("L", (128, 128), 255))
    assert window.history.document.selection is not None
    assert window.history.document.selection.getbbox() is None
    window.set_tool("brush")
    window.foreground = (255, 0, 0, 255)
    point = window.canvas.screen((64, 64)).toPoint()
    QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=point)
    assert window.history.document.layer().image.getpixel((64, 64)) == (60, 80, 100, 255)


def test_levels_canvas_gray_samples_original_pixels_and_cancel_preserves_layer(window, application):
    from PySide6.QtCore import QTimer

    d = document()
    d.layer().image = Image.new("RGBA", (128, 128), (64, 128, 192, 255))
    before = d.layer().image.tobytes()
    window.add_project(d)
    sampled = []

    def calibrate():
        dialog = window.filter_dialog
        try:
            dialog.fields["Input white"].setValue(200)
            dialog.preview.emit(dialog.result_settings())
            dialog.canvas_mode.setCurrentText("Gray")
            QTest.mouseClick(
                window.canvas,
                Qt.MouseButton.LeftButton,
                pos=window.canvas.screen((64, 64)).toPoint(),
            )
            sampled.append(dialog.result_settings()["levels"]["ranges"])
        finally:
            dialog.reject()

    QTimer.singleShot(10, calibrate)
    window.filter("Levels")
    assert sampled[0][0]["white"] == 255
    assert sampled[0][1]["gamma"] == pytest.approx(2, abs=0.02)
    assert (
        not window.history.undo_stack and window.history.document.layer().image.tobytes() == before
    )


def test_native_distort_keeps_corners_pending_and_repeated_drags_do_not_resample_preview(
    window, application
):

    d = document()
    d.layer().image = Image.new("RGBA", (128, 128), (10, 20, 30, 255))
    window.add_project(d)
    corner = window.canvas.screen((0, 0)).toPoint()
    end = window.canvas.screen((20, 10)).toPoint()
    QTest.mousePress(
        window.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=corner
    )
    QTest.mouseMove(window.canvas, end)
    QTest.mouseRelease(
        window.canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=end
    )
    assert not window.history.undo_stack and window.history.document.layer().transform.x == 0
    assert window.canvas.pending["corners"][0][0] == pytest.approx(20, abs=1)
    first = window.canvas.document.layer().image.tobytes()
    corner = window.canvas.screen(window.canvas.pending["corners"][0]).toPoint()
    QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=corner)
    QTest.mouseMove(window.canvas, corner)
    QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=corner)
    assert window.canvas.document.layer().image.tobytes() == first
    QTest.keyClick(window.canvas, Qt.Key.Key_Return)
    assert len(window.history.undo_stack) == 1


def test_transform_ratio_toggle_and_auto_select_match_native_controls(window, application):
    from compositor_linux.model import Transform

    d = document()
    d.layer().transform = Transform(width=32, height=16)
    d.layer().image = Image.new("RGBA", (32, 16), (255, 0, 0, 255))
    second = Layer(
        "Blue",
        Transform(x=80, y=80, width=20, height=20),
        image=Image.new("RGBA", (20, 20), (0, 0, 255, 255)),
    )
    d.layers.append(second)
    window.add_project(d)
    window.numeric_transform("width", 64)
    assert window.canvas.document.layer().transform.height == 32
    window.set_option("locks_transform_ratio", False)
    window.numeric_transform("width", 80)
    assert window.canvas.document.layer().transform.height == 32
    window.cancel_pending()
    window.set_option("auto_select", True)
    QTest.mouseClick(
        window.canvas, Qt.MouseButton.LeftButton, pos=window.canvas.screen((90, 90)).toPoint()
    )
    assert window.history.document.active == second.id
