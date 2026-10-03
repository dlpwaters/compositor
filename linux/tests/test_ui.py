import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from compositor_linux.app import MainWindow
from compositor_linux.dialogs import (
    EffectDialog,
    FilterDialog,
    NewLayerDialog,
    SizeDialog,
    TextLayerDialog,
    TrimDialog,
    ValuesDialog,
)
from compositor_linux.model import Document, Layer, TextContent
from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QColorDialog, QDialog, QMessageBox, QTabBar, QWidget


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
    QSettings("Compositor", "CompositorLinux").clear()
    window = MainWindow()
    window.show()
    application.processEvents()
    yield window
    for h in window.projects:
        h.saved_revision = h.revision
    window.close()
    application.processEvents()
    window.deleteLater()
    application.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def drive_dialog(application, dialog_type, callback, open_dialog):
    """Run a real dialog callback after it appears and report timer failures."""
    errors = []
    handled = False
    deadline = time.monotonic() + 10
    timer = QTimer()
    timer.setInterval(10)

    def tick():
        nonlocal handled
        active = dialog = None
        try:
            active = application.activeModalWidget()
            dialog = (
                active
                if isinstance(active, dialog_type)
                else next(
                    (
                        widget
                        for widget in application.topLevelWidgets()
                        if isinstance(widget, dialog_type) and widget.isVisible()
                    ),
                    None,
                )
            )
            if dialog is None:
                if time.monotonic() >= deadline:
                    raise AssertionError(f"Timed out waiting for {dialog_type.__name__}")
                return
            handled = True
            timer.stop()
            callback(dialog)
        except Exception as exc:
            errors.append(exc)
            timer.stop()
            current = (
                dialog
                or active
                or next(
                    (
                        widget
                        for widget in application.topLevelWidgets()
                        if isinstance(widget, QDialog) and widget.isVisible()
                    ),
                    None,
                )
            )
            if isinstance(current, QDialog):
                current.done(QDialog.DialogCode.Rejected)

    timer.timeout.connect(tick)
    timer.start()
    try:
        result = open_dialog()
    finally:
        timer.stop()
    if errors:
        raise errors[0]
    assert handled, f"{dialog_type.__name__} did not open"
    return result


def document():
    d = Document(128, 128)
    layer = Layer.blank(128, 128)
    d.layers.append(layer)
    d.active = layer.id
    return d


def test_dialog_callback_exception_rejects_real_dialog(application):
    dialog = ValuesDialog("Callback failure")

    def fail(_):
        raise RuntimeError("callback failure")

    with pytest.raises(RuntimeError, match="callback failure"):
        drive_dialog(application, ValuesDialog, fail, dialog.exec)
    assert not dialog.isVisible()
    assert dialog.result() == QDialog.DialogCode.Rejected
    dialog.deleteLater()
    application.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_new_canvas_presets_match_upstream_screens_and_social_sizes(application):
    dialog = SizeDialog("New Canvas", 1920, 1080)
    assert dialog.value("Preset") == "1080p"
    dialog.fields["Preset"].setCurrentText("Instagram Story")
    assert (dialog.value("Width"), dialog.value("Height")) == (1080, 1920)
    dialog.fields["Width"].setValue(1111)
    assert dialog.value("Preset") == "Custom"
    dialog.close()


def test_trim_menu_crops_rendered_canvas_as_one_edit(window, monkeypatch):
    d = Document(20, 20)
    pixels = Image.new("RGBA", (20, 20))
    pixels.paste((255, 0, 0, 255), (2, 4, 17, 15))
    d.layers = [Layer.blank(20, 20)]
    d.layers[0].image = pixels
    d.active = d.layers[0].id
    window.add_project(d)
    monkeypatch.setattr(TrimDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    window.trim_document()
    assert (window.history.document.width, window.history.document.height) == (15, 11)
    window.history.undo()
    assert window.history.document.width == 20


def test_cancellable_busy_job_returns_without_applying_result(window, application):
    import time

    from compositor_linux.tasks import BusyDialog, run_task

    def work(cancel):
        while not cancel.is_set():
            time.sleep(0.005)
        return "late result"

    assert (
        drive_dialog(
            application,
            BusyDialog,
            lambda dialog: dialog.reject(),
            lambda: run_task(window, "Scanning…", work, cancellable=True),
        )
        is None
    )


def test_mask_alone_renders_gray_and_clears_when_targeting_pixels(window):
    d = document()
    layer = d.layer()
    layer.image = Image.new("RGBA", (128, 128), (255, 0, 0, 255))
    layer.mask = Image.new("L", (128, 128), 0)
    layer.mask.paste(255, (0, 0, 64, 128))
    window.add_project(d)
    window.toggle_mask_alone(layer.id)
    assert window.mask_alone_id == layer.id
    assert window.mask_target
    view = window.canvas.mask_alone_image((0, 0, 128, 128), 1)
    assert view.getpixel((20, 20)) == (255, 255, 255, 255)
    assert view.getpixel((90, 20)) == (0, 0, 0, 255)
    window.select_layer_target(layer.id, mask=False)
    assert window.mask_alone_id is None
    item = window.tree.topLevelItem(0)
    mask_cell = window.tree.visualItemRect(item)
    mask_point = QPoint(window.tree.columnViewportPosition(1) + 10, mask_cell.center().y())
    QTest.mouseClick(
        window.tree.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.AltModifier,
        mask_point,
    )
    assert window.mask_alone_id == layer.id
    window.delete_mask()
    window.refresh()
    assert window.mask_alone_id is None


def test_layer_copy_paste_between_tabs_uses_selected_hierarchy(window):
    source = document()
    source.layer().name = "Original"
    source.layer().mask = Image.new("L", (128, 128), 120)
    original_id = source.layer().id
    window.add_project(source)
    window.copy_selected_layers()
    window.add_project(document())
    window.paste_copied_layers()
    imported = window.history.document.layer()
    assert imported.name == "Original"
    assert imported.id != original_id
    assert imported.mask.getpixel((0, 0)) == 120
    assert "Copy Layers" in window.actions and "Paste Layers" in window.actions


def test_inline_text_typing_formatting_commit_and_cancel(window):
    window.add_project(document())
    initial = len(window.history.document.layers)
    window.start_inline_text((20, 20))
    editor = window.inline_text
    assert editor is not None and editor.isVisible()
    editor.editor.setPlainText("Inline")
    editor.size.setValue(34)
    editor.accept()
    layer = window.history.document.layer()
    assert len(window.history.document.layers) == initial + 1
    assert layer.live_text.settings["text"] == "Inline"
    assert layer.live_text.settings["size"] == 34
    window.history.undo()
    assert len(window.history.document.layers) == initial
    window.start_inline_text((20, 20))
    window.inline_text.editor.setPlainText("Discard")
    window.inline_text.reject()
    assert len(window.history.document.layers) == initial
    assert window.content_dialog is None


def test_inline_text_keeps_color_runs_and_paragraph_box(window):
    from compositor_linux.text import render_text

    d = document()
    settings = dict(
        version=1,
        text="AB",
        family="DejaVu Sans",
        size=32,
        bold=False,
        italic=False,
        underline=False,
        alignment="Left",
        color=[255, 0, 0],
    )
    pixels = render_text(settings)
    d.layer().image = pixels
    d.layer().text = TextContent(settings, pixels)
    window.add_project(d)
    window.start_inline_text(layer_id=d.active)
    editor = window.inline_text
    cursor = editor.editor.textCursor()
    cursor.setPosition(1)
    cursor.setPosition(2, QTextCursor.MoveMode.KeepAnchor)
    editor.editor.setTextCursor(cursor)
    editor.color.color = QColor(0, 0, 255)
    editor.color.changed.emit()
    editor.resize(430, 270)
    QTest.keyClick(editor.editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    updated = window.history.document.layer()
    assert updated.live_text is not None
    assert updated.live_text.settings["macText"]["colorRuns"] == [
        dict(location=1, length=1, red=0, green=0, blue=1)
    ]
    assert updated.live_text.settings["macText"]["boxSize"][0] > 16


def test_recent_projects_and_tool_options_persist(window, tmp_path):
    from compositor_linux import store

    path = tmp_path / "remember.comp"
    store.save(document(), path)
    window.add_project(document(), path)
    assert str(path) in window.recent_projects()
    window.set_option("diameter", 73)
    second = MainWindow()
    try:
        assert second.options["diameter"] == 73
        assert str(path) in second.recent_projects()
    finally:
        second.close()


def test_tab_drag_reorders_projects_and_viewports(window):
    first, second = document(), document()
    window.add_project(first)
    window.add_project(second)
    window.tabs.tabMoved.emit(0, 1)
    assert [history.document.id for history in window.projects] == [second.id, first.id]


def test_shortcut_override_persists(window):
    window.set_shortcut("Save", "Ctrl+Alt+S")
    second = MainWindow()
    try:
        assert second.actions["Save"].shortcut().toString() == "Ctrl+Alt+S"
    finally:
        second.close()


def test_external_project_change_reloads_when_clean(window, tmp_path):
    from compositor_linux import store

    path = tmp_path / "external.comp"
    d = document()
    store.save(d, path)
    window.add_project(store.load(path), path)
    payload = store.manifest(d)
    payload["layers"][0]["name"] = "Changed externally"
    (path / "manifest.json").write_text(__import__("json").dumps(payload))
    window.check_external_changes()
    assert window.history.document.layer().name == "Changed externally"


def test_external_change_keeps_unsaved_edits_on_conflict(window, tmp_path, monkeypatch):
    from compositor_linux import store

    path = tmp_path / "conflict.comp"
    d = document()
    store.save(d, path)
    window.add_project(store.load(path), path)
    with window.history.edit("Local rename") as local:
        local.layer().name = "Local"
    payload = store.manifest(d)
    payload["layers"][0]["name"] = "External"
    (path / "manifest.json").write_text(__import__("json").dumps(payload))
    monkeypatch.setattr(
        "compositor_linux.app.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    window.check_external_changes()
    assert window.history.document.layer().name == "Local"


def test_async_save_uses_snapshot_and_keeps_newer_edit_dirty(window, tmp_path, monkeypatch):
    import threading

    from compositor_linux import store

    real_save = store.save
    started, release = threading.Event(), threading.Event()
    path = tmp_path / "async.comp"

    def slow_save(snapshot, destination):
        started.set()
        assert release.wait(5)
        real_save(snapshot, destination)

    monkeypatch.setattr(store, "save", slow_save)
    window.add_project(document())
    monkeypatch.setattr(
        "compositor_linux.app.QFileDialog.getSaveFileName", lambda *args: (str(path), "")
    )
    assert window.save_project()
    assert started.wait(2)
    with window.history.edit("Later edit") as d:
        d.layer().name = "Newer"
    release.set()
    window.wait_for_save()
    assert store.load(path).layer().name == "Layer"
    assert window.history.document.layer().name == "Newer"
    assert window.history.dirty


def test_color_range_dialog_commits_one_undo_and_cancel_restores(window, application):
    from compositor_linux.dialogs import ColorRangeDialog

    d = document()
    d.layer().image = Image.new("RGBA", (128, 128), (255, 0, 0, 255))
    window.add_project(d)

    def cancel(dialog):
        assert isinstance(dialog, ColorRangeDialog)
        dialog.preview.emit(dialog.result_settings())
        assert window.preview_document is not None
        dialog.reject()

    drive_dialog(application, ColorRangeDialog, cancel, window.select_color_range)
    assert window.history.document.selection is None
    drive_dialog(
        application, ColorRangeDialog, lambda dialog: dialog.accept(), window.select_color_range
    )
    assert window.history.document.selection.getbbox() == (0, 0, 128, 128)
    window.history.undo()
    assert window.history.document.selection is None


def test_psd_conversion_report_precedes_document_open(window, tmp_path, monkeypatch):
    from compositor_linux import psd

    path = tmp_path / "layers.psd"
    path.write_bytes(b"8BPS")
    monkeypatch.setattr(
        psd, "load", lambda _: psd.ImportResult(document(), ["Text rasterized as pixels"])
    )
    monkeypatch.setattr(
        "compositor_linux.app.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel,
    )
    window.open_paths([path])
    assert not window.projects
    monkeypatch.setattr(
        "compositor_linux.app.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Open,
    )
    window.open_paths([path])
    assert len(window.projects) == 1


def test_effect_dialog_preview_cancel_and_undo(window, application):
    d = document()
    d.layer().image = Image.new("RGBA", (128, 128), (255, 0, 0, 255))
    window.add_project(d)
    original = window.history.document.layer().extras.copy()

    def cancel(dialog):
        assert isinstance(dialog, EffectDialog)
        dialog.fields["Size"].setValue(7)
        dialog.preview.emit(dialog.result_settings())
        assert window.preview_document is not None
        dialog.reject()

    drive_dialog(application, EffectDialog, cancel, lambda: window.edit_effect("Stroke"))
    assert window.history.document.layer().extras == original
    assert window.preview_document is None

    def accept(dialog):
        dialog.fields["Size"].setValue(9)
        dialog.accept()

    drive_dialog(application, EffectDialog, accept, lambda: window.edit_effect("Stroke"))
    assert window.history.document.layer().extras["effects"]["stroke"]["size"] == 9
    window.history.undo()
    assert window.history.document.layer().extras == original


def test_new_adjustment_dialog_uses_saved_field_names(application):
    parent = QWidget()
    blur = FilterDialog(
        "Gaussian Blur", {"kind": "Gaussian Blur", "blurRadius": 8}, parent=parent, adjustment=True
    )
    assert blur.value("Radius") == 8
    blur.fields["Radius"].setValue(12)
    assert blur.result_settings()["blurRadius"] == 12
    balance = FilterDialog(
        "Color Balance", {"kind": "Color Balance"}, parent=parent, adjustment=True
    )
    balance.fields["Mid cyan/red"].setValue(30)
    assert balance.result_settings()["colorBalanceSettings"]["midCyanRed"] == 30


def test_dither_dialog_retains_pixel_shape_ascii_and_crt_settings(application):
    parent = QWidget()
    dialog = FilterDialog(
        "Dither",
        {"style": "ASCII", "pixelShape": "Dot", "characters": " .#@", "wobble": 4},
        parent=parent,
    )
    assert dialog.value("Style") == "ASCII"
    assert dialog.value("Characters") == " .#@"
    dialog.fields["Style"].setCurrentText("Scanlines (CRT)")
    dialog.fields["Wobble"].setValue(9)
    settings = dialog.result_settings()
    assert settings["style"] == "Scanlines (CRT)"
    assert settings["wobble"] == 9 and settings["pixelShape"] == "Dot"


def test_persistent_guide_and_grid_snap_controls(window):
    d = document()
    d.extras["guides"] = [
        {"id": "C14196E3-290F-4092-BEFA-25419D3DA46F", "axis": "vertical", "position": 43}
    ]
    window.add_project(d)
    window.canvas.zoom_to(1)
    assert window.canvas.snapped((40, 20)) == (43, 20)
    window.set_view_flag("snap_guides", False)
    assert window.canvas.snapped((40, 20)) == (40, 20)
    window.grid_spacing, window.grid_subdivisions = 40, 4
    window.set_view_flag("snap_grid", True)
    assert window.canvas.snapped((8, 22)) == (10, 20)


def test_crop_starts_at_selection_bounds(window):
    d = document()
    d.selection = Image.new("L", (128, 128))
    d.selection.paste(255, (14, 22, 48, 77))
    window.add_project(d)
    window.set_tool("crop")
    rect = window.canvas.pending["rect"]
    assert (rect.x, rect.y, rect.width, rect.height) == (14, 22, 34, 55)


def test_mac_text_runs_change_raster_and_survive_dialog(application):
    from compositor_linux.text import render_text

    base = dict(
        version=1,
        text="AB",
        family="DejaVu Sans",
        size=32,
        bold=False,
        italic=False,
        underline=False,
        alignment="Left",
        color=[255, 0, 0],
    )
    styled = dict(
        base,
        macText=dict(
            content="AB",
            fontName="DejaVu Sans",
            fontSize=32,
            red=1,
            green=0,
            blue=0,
            alignment="Left",
            tracking=0,
            leading=0,
            colorRuns=[dict(location=1, length=1, red=0, green=0, blue=1)],
        ),
    )
    assert render_text(base).tobytes() != render_text(styled).tobytes()
    dialog = TextLayerDialog(styled)
    assert dialog.settings()["macText"]["colorRuns"] == styled["macText"]["colorRuns"]
    cursor = dialog.editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(1, cursor.MoveMode.KeepAnchor)
    dialog.editor.setTextCursor(cursor)
    dialog.color.set_color((0, 255, 0))
    assert dialog.settings()["macText"]["colorRuns"] == [
        dict(location=0, length=1, red=0, green=1, blue=0),
        dict(location=1, length=1, red=0, green=0, blue=1),
    ]


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

    def configure(dialog):
        assert isinstance(dialog, NewLayerDialog)
        dialog.name.setText("Backdrop")
        dialog.fill.setCurrentText("Solid color" if solid else "Transparent")
        dialog.color.set_color((20, 50, 80))
        dialog.accept()

    drive_dialog(
        application, NewLayerDialog, configure, lambda: window.actions["New Layer…"].trigger()
    )
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
    drive_dialog(
        application,
        NewLayerDialog,
        lambda dialog: dialog.reject(),
        lambda: window.actions["New Layer…"].trigger(),
    )
    drive_dialog(
        application,
        TextLayerDialog,
        lambda dialog: dialog.reject(),
        lambda: window.actions["New Text Layer…"].trigger(),
    )
    application.processEvents()
    assert window.history.revision == revision and len(window.history.document.layers) == 1
    assert not window.history.undo_stack and window.preview_document is None
    assert window.content_dialog is None


def test_closing_owner_cancels_text_preview_and_keeps_project(window, application):
    window.add_project(document())

    def close_owner(dialog):
        dialog.editor.setPlainText("Unsaved preview")
        dialog.render_preview()
        assert window.preview_document is not None
        window.close()

    drive_dialog(
        application,
        TextLayerDialog,
        close_owner,
        lambda: window.actions["New Text Layer…"].trigger(),
    )
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

    def recolor(dialog):
        assert isinstance(dialog, QColorDialog)
        from PySide6.QtGui import QColor

        dialog.setCurrentColor(QColor(200, 80, 40))
        dialog.accept()

    item = window.tree.topLevelItem(0)
    point = window.tree.visualRect(window.tree.indexFromItem(item, 0)).center()
    QTest.mouseClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
    drive_dialog(
        application,
        QColorDialog,
        recolor,
        lambda: QTest.mouseDClick(window.tree.viewport(), Qt.MouseButton.LeftButton, pos=point),
    )
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

    def configure(dialog):
        assert isinstance(dialog, TextLayerDialog)
        dialog.editor.setPlainText("Hello Ω\nWorld")
        dialog.fields["Size (px)"].setValue(18)
        dialog.fields["Bold"].setChecked(True)
        dialog.fields["Alignment"].setCurrentText("Center")
        dialog.color.set_color((60, 160, 240))
        dialog.render_preview()
        previews.append(window.preview_document is not None and not window.history.undo_stack)
        dialog.accept()

    drive_dialog(
        application,
        TextLayerDialog,
        configure,
        lambda: QTest.mouseClick(
            window.canvas,
            Qt.MouseButton.LeftButton,
            pos=window.canvas.screen((16, 16)).toPoint(),
        ),
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

    def edit(dialog):
        dialog.editor.setPlainText("Changed")
        dialog.accept()

    drive_dialog(
        application,
        TextLayerDialog,
        edit,
        lambda: window.actions["Edit Layer Content…"].trigger(),
    )
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
    stale = FilterDialog("Hue/Saturation", parent=window)
    assert not stale.isVisible() and stale in application.topLevelWidgets()

    def cancel(dialog):
        dialog.fields["Exposure"].setValue(2)
        dialog.preview.emit(dialog.result_settings())
        dialog.reject()

    before = d.layer().image.tobytes()
    drive_dialog(application, FilterDialog, cancel, lambda: window.filter("Exposure"))
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
    window.add_project(document())
    drive_dialog(
        application,
        FilterDialog,
        lambda dialog: dialog.reject(),
        lambda: window.new_adjustment("Exposure"),
    )
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

    d = document()
    d.layer().image = Image.new("RGBA", (128, 128), (100, 120, 140, 255))
    d.layer().mask = Image.new("L", (1, 1), 192)
    original_mask = d.layer().mask
    window.add_project(d)
    monkeypatch.setattr(models, "verified", lambda path: True)
    monkeypatch.setattr(engine, "subject_mask", lambda image, path: Image.new("L", image.size, 128))
    drive_dialog(
        application,
        ValuesDialog,
        lambda dialog: dialog.accept(),
        window.remove_background,
    )
    assert window.history.document.layer().image.getpixel((50, 50))[3] == 128
    assert window.history.document.layer().mask is original_mask
    assert window.history.undo()
    assert window.history.document.layer().image.getpixel((50, 50))[3] == 255


def test_new_canvas_has_an_active_blank_layer_ready_to_paint(window, application):
    from compositor_linux.dialogs import SizeDialog

    def create(dialog):
        dialog.fields["Width"].setValue(128)
        dialog.fields["Height"].setValue(64)
        dialog.accept()

    drive_dialog(application, SizeDialog, create, window.new_canvas)
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
    from PySide6.QtWidgets import QPushButton

    d = document()
    d.layer().image = Image.new("RGBA", (128, 128), (240, 240, 240, 255))
    d.layer().image.paste((40, 40, 40, 255), (0, 0, 64, 64))
    d.layer().image.paste((120, 120, 120, 255), (0, 32, 64, 64))
    d.selection = Image.new("L", (128, 128))
    d.selection.paste(255, (0, 0, 64, 64))
    window.add_project(d)
    sampled_white = []

    def auto(dialog):
        next(
            button for button in dialog.findChildren(QPushButton) if button.text() == "Auto"
        ).click()
        sampled_white.append(dialog.value("Input white"))
        dialog.accept()

    if adjustment:
        drive_dialog(application, FilterDialog, auto, lambda: window.new_adjustment("Levels"))
        assert window.history.document.layer().adjustment["levels"]["ranges"][0]["white"] <= 120
    else:
        drive_dialog(application, FilterDialog, auto, lambda: window.filter("Levels"))
        assert window.history.document.layer().image.getpixel((20, 45))[:3] == (255, 255, 255)
    assert sampled_white and sampled_white[0] <= 120


@pytest.mark.parametrize("accept", [False, True])
def test_hue_targeted_native_canvas_drag_stays_pending_until_accepted(window, application, accept):
    d = document()
    d.layer().image = Image.new("RGBA", (128, 128), (160, 60, 60, 255))
    before = d.layer().image.tobytes()
    window.add_project(d)
    observed = []

    def drag(dialog):
        assert dialog is window.filter_dialog
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

    drive_dialog(application, FilterDialog, drag, lambda: window.filter("Hue/Saturation"))
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
    d = document()
    d.layer().image = Image.new("RGBA", (128, 128), (64, 128, 192, 255))
    before = d.layer().image.tobytes()
    window.add_project(d)
    sampled = []

    def calibrate(dialog):
        assert dialog is window.filter_dialog
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

    drive_dialog(application, FilterDialog, calibrate, lambda: window.filter("Levels"))
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
