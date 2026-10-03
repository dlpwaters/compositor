"""Native editor dialogs, with cancellable previews and editable curves."""

import colorsys
import json
from copy import deepcopy
from io import BytesIO

import numpy as np
from PIL import Image, ImageCms
from PySide6.QtCore import QEvent, QEventLoop, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import dither, engine, hue, levels
from .icons import icon
from .model import Transform
from .tasks import run_task
from .text import render_text


def number(value, minimum, maximum, decimals=0):
    box = QDoubleSpinBox() if decimals else QSpinBox()
    box.setRange(minimum, maximum)
    if decimals:
        box.setDecimals(decimals)
        box.setSingleStep(10 ** -min(2, decimals))
    box.setValue(value)
    box.setKeyboardTracking(False)
    return box


class ValuesDialog(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.layout_box = QVBoxLayout(self)
        self.form = QFormLayout()
        self.layout_box.addLayout(self.form)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setIcon(icon("apply"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setIcon(icon("cancel"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.layout_box.addWidget(self.buttons)
        self.fields = {}

    def add_number(self, label, value, minimum, maximum, decimals=0):
        box = number(value, minimum, maximum, decimals)
        self.form.addRow(label, box)
        self.fields[label] = box
        return box

    def add_choice(self, label, choices, current=None):
        box = QComboBox()
        box.addItems(choices)
        if current in choices:
            box.setCurrentText(current)
        self.form.addRow(label, box)
        self.fields[label] = box
        return box

    def add_check(self, label, value=False):
        box = QCheckBox(label)
        box.setChecked(value)
        self.form.addRow(box)
        self.fields[label] = box
        return box

    def value(self, label):
        widget = self.fields[label]
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QLineEdit):
            return widget.text()
        return widget.value()


class ScrubLabel(QLabel):
    """Drag an option label horizontally to change its adjacent numeric control."""

    def __init__(self, label, box, parent=None):
        super().__init__(label, parent)
        self.box = box
        self.start = None
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip("Drag horizontally to adjust")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = (event.position().x(), self.box.value())
            event.accept()

    def mouseMoveEvent(self, event):
        if self.start is not None:
            value = self.start[1] + (event.position().x() - self.start[0]) * 0.5
            self.box.setValue(value)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.start = None
        event.accept()


class ColorButton(QPushButton):
    changed = Signal()

    def __init__(self, color, label, parent=None):
        super().__init__(parent)
        self.color = QColor(*color[:3])
        self.setAccessibleName(label)
        self.clicked.connect(self.choose)
        self.update_swatch()

    def update_swatch(self):
        swatch = QPixmap(18, 18)
        swatch.fill(self.color)
        from PySide6.QtGui import QIcon

        self.setIcon(QIcon(swatch))
        self.setText(self.color.name().upper())
        self.setToolTip(self.color.name().upper())

    def choose(self):
        selected = QColorDialog.getColor(self.color, self, self.accessibleName())
        if selected.isValid():
            self.set_color((selected.red(), selected.green(), selected.blue()))

    def set_color(self, color):
        self.color = QColor(*color[:3])
        self.update_swatch()
        self.changed.emit()

    def rgb(self):
        return self.color.red(), self.color.green(), self.color.blue()


class NewLayerDialog(ValuesDialog):
    def __init__(self, color, solid=False, parent=None):
        super().__init__("New Layer", parent)
        self.name = QLineEdit("Solid Color" if solid else "Layer")
        self.name.setMaxLength(256)
        self.name.setAccessibleName("Layer name")
        self.form.addRow("Name", self.name)
        self.fill = self.add_choice(
            "Fill", ["Transparent", "Solid color"], "Solid color" if solid else "Transparent"
        )
        self.fill.setAccessibleName("Layer fill")
        self.color = ColorButton(color, "Layer fill color")
        self.form.addRow("Color", self.color)
        self.fill.currentTextChanged.connect(self.update_fill)
        self.update_fill()
        self.setMinimumWidth(350)

    def update_fill(self):
        self.color.setEnabled(self.fill.currentText() == "Solid color")


class EffectDialog(ValuesDialog):
    preview = Signal(dict)
    KINDS = {
        "Stroke": "stroke",
        "Drop Shadow": "shadow",
        "Color Overlay": "colorOverlay",
        "Inner Shadow": "innerShadow",
        "Outer Glow": "outerGlow",
        "Inner Glow": "innerGlow",
    }

    def __init__(self, kind, initial=None, parent=None):
        super().__init__(kind, parent)
        self.kind = self.KINDS[kind]
        self.original = deepcopy(initial or {})
        self.add_check("Enabled", self.original.get("enabled", True))
        if self.kind in ("stroke", "outerGlow", "innerGlow"):
            self.add_number(
                "Size", self.original.get("size", 4 if self.kind == "stroke" else 20), 0, 500
            )
        if self.kind in ("shadow", "innerShadow"):
            self.add_number("Angle", self.original.get("angle", 90), -360, 360)
            self.add_number("Distance", self.original.get("distance", 20), 0, 5000)
            self.add_number("Blur", self.original.get("blur", 20), 0, 500)
        if self.kind == "stroke":
            self.add_check("Inside", self.original.get("inside", False))
        self.add_number("Opacity", round(self.original.get("opacity", 1) * 100), 0, 100)
        color = tuple(round(self.original.get(key, 0) * 255) for key in ("red", "green", "blue"))
        self.color = ColorButton(color, "Effect color")
        self.form.addRow("Color", self.color)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(80)
        self.timer.timeout.connect(lambda: self.preview.emit(self.result_settings()))
        for field in self.fields.values():
            if isinstance(field, QCheckBox):
                field.toggled.connect(lambda _: self.timer.start())
            else:
                field.valueChanged.connect(lambda _: self.timer.start())
        self.color.changed.connect(lambda: self.timer.start())

    def result_settings(self):
        result = dict(self.original)
        result["enabled"] = self.value("Enabled")
        for label, key in (
            ("Size", "size"),
            ("Angle", "angle"),
            ("Distance", "distance"),
            ("Blur", "blur"),
            ("Inside", "inside"),
        ):
            if label in self.fields:
                result[key] = self.value(label)
        result["opacity"] = self.value("Opacity") / 100
        result.update(
            zip(("red", "green", "blue"), (channel / 255 for channel in self.color.rgb()))
        )
        return result


class ColorRangeDialog(ValuesDialog):
    preview = Signal(dict)

    def __init__(self, color, parent=None):
        super().__init__("Color Range", parent)
        self.include = ColorButton(color, "Include sampled color")
        self.exclude = ColorButton((0, 0, 0), "Exclude sampled color")
        self.form.addRow("Include", self.include)
        self.form.addRow("Exclude", self.exclude)
        self.add_check("Use exclusion", False)
        self.add_number("Fuzziness", 40, 0, 200)
        self.add_check("Invert", False)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(80)
        self.timer.timeout.connect(lambda: self.preview.emit(self.result_settings()))
        for widget in self.fields.values():
            (widget.toggled if isinstance(widget, QCheckBox) else widget.valueChanged).connect(
                lambda *_: self.timer.start()
            )
        self.include.changed.connect(self.timer.start)
        self.exclude.changed.connect(self.timer.start)

    def result_settings(self):
        return dict(
            include=[self.include.rgb()],
            exclude=[self.exclude.rgb()] if self.value("Use exclusion") else [],
            fuzziness=self.value("Fuzziness"),
            invert=self.value("Invert"),
        )


def text_editor_settings(editor, result, old_mac):
    """Serialize Qt rich text runs into the shared editable text representation."""
    result = dict(result)
    result["text"] = editor.toPlainText()
    mac = deepcopy(old_mac)
    mac["content"] = result["text"]
    runs = {"colorRuns": [], "fontRuns": []}
    cursor = QTextCursor(editor.document())
    for index in range(editor.document().characterCount() - 1):
        cursor.setPosition(index)
        cursor.movePosition(
            QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor
        )
        fmt = cursor.charFormat()
        color = (
            fmt.foreground().color()
            if fmt.hasProperty(QTextCharFormat.Property.ForegroundBrush)
            else None
        )
        rgb = (color.red(), color.green(), color.blue()) if color else None
        family = (
            fmt.font().family() if fmt.hasProperty(QTextCharFormat.Property.FontFamily) else None
        )
        for key, value in (("colorRuns", rgb), ("fontRuns", family)):
            if value is None or value == (
                tuple(result["color"]) if key == "colorRuns" else result["family"]
            ):
                continue
            last = runs[key][-1] if runs[key] else None
            if (
                last
                and last["location"] + last["length"] == index
                and (
                    (key == "fontRuns" and last["fontName"] == value)
                    or (
                        key == "colorRuns"
                        and tuple(round(last[c] * 255) for c in ("red", "green", "blue")) == value
                    )
                )
            ):
                last["length"] += 1
            elif key == "fontRuns":
                runs[key].append(dict(location=index, length=1, fontName=value))
            else:
                runs[key].append(
                    dict(
                        location=index,
                        length=1,
                        red=value[0] / 255,
                        green=value[1] / 255,
                        blue=value[2] / 255,
                    )
                )
    for key, values in runs.items():
        if values:
            mac[key] = values
        else:
            mac.pop(key, None)
    if old_mac or any(runs.values()):
        result["macText"] = mac
    return result


class TextLayerDialog(ValuesDialog):
    preview_changed = Signal(dict, object)

    def __init__(self, settings, parent=None):
        super().__init__("Text Layer", parent)
        self.setMinimumWidth(460)
        self.mac = deepcopy(settings.get("macText") or {})
        self.editor = QTextEdit()
        self.editor.setPlainText(settings.get("text", ""))
        self.editor.setAccessibleName("Text content")
        self.editor.setPlaceholderText("Type your text…")
        self.editor.setMinimumHeight(110)
        self.form.addRow(self.editor)
        for field_name in ("fontRuns", "colorRuns"):
            for run in self.mac.get(field_name) or []:
                cursor = self.editor.textCursor()
                cursor.setPosition(run["location"])
                cursor.setPosition(run["location"] + run["length"], QTextCursor.MoveMode.KeepAnchor)
                style = QTextCharFormat()
                if field_name == "fontRuns":
                    style.setFontFamily(run["fontName"])
                else:
                    style.setForeground(
                        QColor(*(round(run[key] * 255) for key in ("red", "green", "blue")))
                    )
                cursor.mergeCharFormat(style)
        self.editor.moveCursor(QTextCursor.MoveOperation.End)
        self.family = QFontComboBox()
        self.family.setAccessibleName("Text font")
        font = self.family.currentFont()
        font.setFamily(settings["family"])
        self.family.setCurrentFont(font)
        self.base_family = settings["family"]
        self.form.addRow("Font", self.family)
        self.add_number("Size (px)", settings["size"], 1, 2048)
        self.add_check("Bold", settings["bold"])
        self.add_check("Italic", settings["italic"])
        self.add_check("Underline", settings["underline"])
        self.add_choice("Alignment", ["Left", "Center", "Right"], settings["alignment"])
        self.color = ColorButton(settings["color"], "Text color")
        self.base_color = tuple(settings["color"])
        self.form.addRow("Color", self.color)
        self.preview = QLabel()
        self.preview.setMinimumHeight(90)
        self.preview.setFixedHeight(122)
        self.preview.setStyleSheet("background:#858585;border:1px solid #555;padding:8px;")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.form.addRow("Preview", self.preview)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.form.addRow(self.error)
        self.pixels = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.render_preview)
        self.editor.textChanged.connect(self.timer.start)
        self.family.currentFontChanged.connect(self.apply_font)
        self.color.changed.connect(self.apply_color)
        for widget in self.fields.values():
            if isinstance(widget, QSpinBox):
                widget.valueChanged.connect(lambda _: self.timer.start())
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(lambda _: self.timer.start())
            else:
                widget.currentTextChanged.connect(lambda _: self.timer.start())
        QTimer.singleShot(0, self.render_preview)
        self.editor.setFocus()

    def settings(self):
        result = dict(
            version=1,
            text=self.editor.toPlainText(),
            family=self.base_family,
            size=self.value("Size (px)"),
            bold=self.value("Bold"),
            italic=self.value("Italic"),
            underline=self.value("Underline"),
            alignment=self.value("Alignment"),
            color=list(self.base_color),
        )
        return text_editor_settings(self.editor, result, self.mac)

    def apply_font(self, font):
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            style = QTextCharFormat()
            style.setFontFamily(font.family())
            cursor.mergeCharFormat(style)
            self.family.blockSignals(True)
            base = self.family.currentFont()
            base.setFamily(self.base_family)
            self.family.setCurrentFont(base)
            self.family.blockSignals(False)
        else:
            self.base_family = font.family()
        self.timer.start()

    def apply_color(self):
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            style = QTextCharFormat()
            style.setForeground(self.color.color)
            cursor.mergeCharFormat(style)
            self.color.color = QColor(*self.base_color)
            self.color.update_swatch()
        else:
            self.base_color = self.color.rgb()
        self.timer.start()

    def set_error(self, message):
        self.error.setText(message)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not message)

    def render_preview(self):
        settings = self.settings()
        try:
            self.pixels = render_text(settings)
            from .canvas import qimage

            preview = QPixmap.fromImage(qimage(self.pixels))
            if preview.width() > 340 or preview.height() > 100:
                preview = preview.scaled(
                    QSize(340, 100),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            self.preview.setPixmap(preview)
            self.set_error("")
        except ValueError as error:
            self.pixels = None
            self.preview.clear()
            self.set_error("Enter some text." if not settings["text"].strip() else str(error))
        self.preview_changed.emit(settings, self.pixels)

    def accept(self):
        self.timer.stop()
        self.render_preview()
        if (
            self.pixels is not None
            and self.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        ):
            super().accept()


class InlineTextEditor(QWidget):
    """A resizable Qt rich text box placed directly over the canvas."""

    finished = Signal(bool)

    def __init__(self, settings, zoom, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Inline text editor")
        self.setStyleSheet("background:#30343c;border:1px solid #8793a3;border-radius:4px;")
        self.base = deepcopy(settings)
        self.mac = deepcopy(settings.get("macText") or {})
        self.zoom = zoom
        self.user_box = False
        self.track_resize = False
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.family = QFontComboBox()
        font = self.family.currentFont()
        font.setFamily(settings["family"])
        self.family.setCurrentFont(font)
        self.size = number(settings["size"], 1, 2048)
        self.size.setFixedWidth(70)
        self.color = ColorButton(settings["color"], "Text color")
        self.alignment = QComboBox()
        self.alignment.addItems(["Left", "Center", "Right"])
        self.alignment.setCurrentText(settings["alignment"])
        controls.addWidget(self.family)
        controls.addWidget(self.size)
        controls.addWidget(self.color)
        controls.addWidget(self.alignment)
        layout.addLayout(controls)
        self.editor = QTextEdit()
        self.editor.setAccessibleName("Inline text content")
        self.editor.setPlaceholderText("Type text…")
        font = self.family.currentFont()
        font.setPixelSize(max(1, round(settings["size"] * zoom)))
        self.editor.setFont(font)
        self.editor.setTextColor(QColor(*settings["color"]))
        self.editor.setPlainText(settings["text"])
        self.editor.setAlignment(
            {
                "Left": Qt.AlignmentFlag.AlignLeft,
                "Center": Qt.AlignmentFlag.AlignHCenter,
                "Right": Qt.AlignmentFlag.AlignRight,
            }[settings["alignment"]]
        )
        for field_name in ("fontRuns", "colorRuns"):
            for run in self.mac.get(field_name) or []:
                cursor = self.editor.textCursor()
                cursor.setPosition(run["location"])
                cursor.setPosition(run["location"] + run["length"], QTextCursor.MoveMode.KeepAnchor)
                style = QTextCharFormat()
                if field_name == "fontRuns":
                    style.setFontFamily(run["fontName"])
                else:
                    style.setForeground(
                        QColor(*(round(run[key] * 255) for key in ("red", "green", "blue")))
                    )
                cursor.mergeCharFormat(style)
        self.editor.moveCursor(QTextCursor.MoveOperation.End)
        self.editor.installEventFilter(self)
        layout.addWidget(self.editor, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(QLabel("Ctrl+Enter to apply · Esc to cancel"))
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        apply = QPushButton("Apply")
        cancel.clicked.connect(self.reject)
        apply.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        buttons.addWidget(QSizeGrip(self))
        layout.addLayout(buttons)
        self.family.currentFontChanged.connect(self.change_font)
        self.color.changed.connect(self.change_color)
        self.size.valueChanged.connect(self.change_size)
        self.alignment.currentTextChanged.connect(self.change_alignment)

    def resizeEvent(self, event):
        if self.track_resize:
            self.user_box = True
        super().resizeEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.editor and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.reject()
                return True
            if (
                event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier
            ):
                self.accept()
                return True
        return super().eventFilter(watched, event)

    def change_font(self, font):
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            style = QTextCharFormat()
            style.setFontFamily(font.family())
            cursor.mergeCharFormat(style)
        else:
            self.base["family"] = font.family()

    def change_color(self):
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            style = QTextCharFormat()
            style.setForeground(self.color.color)
            cursor.mergeCharFormat(style)
        else:
            self.base["color"] = list(self.color.rgb())

    def change_size(self, value):
        self.base["size"] = value
        font = self.editor.font()
        font.setPixelSize(max(1, round(value * self.zoom)))
        self.editor.setFont(font)

    def change_alignment(self, value):
        self.base["alignment"] = value
        self.editor.setAlignment(
            {
                "Left": Qt.AlignmentFlag.AlignLeft,
                "Center": Qt.AlignmentFlag.AlignHCenter,
                "Right": Qt.AlignmentFlag.AlignRight,
            }[value]
        )

    def settings(self):
        mac = deepcopy(self.mac)
        if self.user_box:
            mac["boxSize"] = [
                max(16, round(self.editor.width() / self.zoom)),
                max(16, round(self.editor.height() / self.zoom)),
            ]
        return text_editor_settings(self.editor, self.base, mac)

    def accept(self):
        self.finished.emit(True)

    def reject(self):
        self.finished.emit(False)


class SizeDialog(ValuesDialog):
    CANVAS_PRESETS = (
        ("4K", 3840, 2160),
        ("1440p", 2560, 1440),
        ("1080p", 1920, 1080),
        ("iPhone 18 Pro", 1206, 2622),
        ("iPhone 18 Pro Max", 1320, 2868),
        ('MacBook Pro 14"', 3024, 1964),
        ('MacBook Pro 16"', 3456, 2234),
        ("Studio Display", 5120, 2880),
        ("Instagram Square", 1080, 1080),
        ("Instagram Portrait", 1080, 1350),
        ("Instagram Story", 1080, 1920),
        ("YouTube Thumb", 1080, 608),
    )

    def __init__(self, title, width, height, resolution=72, parent=None):
        super().__init__(title, parent)
        self.add_number("Width", width, 1, 30_000)
        self.add_number("Height", height, 1, 30_000)
        self.add_number("Resolution (ppi)", resolution, 1, 9600, 2)
        if title == "New Canvas":
            names = ["Custom"] + [name for name, _, _ in self.CANVAS_PRESETS]
            preset = self.add_choice("Preset", names)
            sizes = {name: (w, h) for name, w, h in self.CANVAS_PRESETS}

            def sync_preset():
                size = (self.value("Width"), self.value("Height"))
                name = next(
                    (name for name, dimensions in sizes.items() if dimensions == size), "Custom"
                )
                preset.blockSignals(True)
                preset.setCurrentText(name)
                preset.blockSignals(False)

            def choose_preset(name):
                if name in sizes:
                    self.fields["Width"].setValue(sizes[name][0])
                    self.fields["Height"].setValue(sizes[name][1])

            self.fields["Width"].valueChanged.connect(sync_preset)
            self.fields["Height"].valueChanged.connect(sync_preset)
            preset.currentTextChanged.connect(choose_preset)
            sync_preset()
        if title == "Canvas Size":
            self.add_choice(
                "Anchor",
                [
                    "Center",
                    "Top left",
                    "Top",
                    "Top right",
                    "Left",
                    "Right",
                    "Bottom left",
                    "Bottom",
                    "Bottom right",
                ],
            )
        if title == "Image Size":
            self.add_check("Resample", True)
        self.setMinimumWidth(330)


class TrimDialog(ValuesDialog):
    def __init__(self, parent=None):
        super().__init__("Trim", parent)
        self.add_choice(
            "Based On", ["Transparent Pixels", "Top Left Pixel Color", "Bottom Right Pixel Color"]
        )
        for side in ("Top", "Bottom", "Left", "Right"):
            self.add_check(side, True)
        self.add_number("Tolerance", 0, 0, 255)

    def options(self):
        return (
            self.value("Based On"),
            tuple(self.value(side) for side in ("Top", "Bottom", "Left", "Right")),
            self.value("Tolerance"),
        )


class TransformDialog(ValuesDialog):
    def __init__(self, t, parent=None):
        super().__init__("Transform Layer", parent)
        for label, value, lo, hi in (
            ("X", t.x, -1_000_000, 1_000_000),
            ("Y", t.y, -1_000_000, 1_000_000),
            ("Width", t.width, 1, 300_000),
            ("Height", t.height, 1, 300_000),
            ("Angle", t.rotation, -360_000, 360_000),
        ):
            self.add_number(label, value, lo, hi, 2)
        self.add_check("Flip horizontal", t.flip_x)
        self.add_check("Flip vertical", t.flip_y)
        self.add_choice("Sampling", ["Nearest", "Smooth", "High quality"], t.sampling)

    def transform(self):
        return Transform(
            *(
                self.value(k)
                for k in (
                    "X",
                    "Y",
                    "Width",
                    "Height",
                    "Angle",
                    "Flip horizontal",
                    "Flip vertical",
                    "Sampling",
                )
            )
        )


class CurvesGraph(QWidget):
    changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings, self.channel, self.dragging = settings, 0, None
        self.setMinimumSize(280, 240)
        self.setAccessibleName("Curves graph")

    def point(self, event):
        return max(0, min(255, event.position().x() / self.width() * 255)), max(
            0, min(255, (1 - event.position().y() / self.height()) * 255)
        )

    def mousePressEvent(self, event):
        x, y = self.point(event)
        points = self.settings["channels"][self.channel]
        nearest = min(
            range(len(points)),
            key=lambda i: abs(points[i]["x"] - x) + abs(points[i]["y"] - y),
        )
        if event.button() == Qt.MouseButton.RightButton:
            if nearest not in (0, len(points) - 1):
                points.pop(nearest)
                self.changed.emit()
                self.update()
            return
        if (
            abs(points[nearest]["x"] - x) + abs(points[nearest]["y"] - y) > 16
            and len(points) < 32
            and 0 < x < 255
        ):
            points.append(dict(x=x, y=y))
            points.sort(key=lambda p: p["x"])
            nearest = next(i for i, p in enumerate(points) if p["x"] == x)
        self.dragging = nearest
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event):
        if self.dragging is None:
            return
        x, y = self.point(event)
        points = self.settings["channels"][self.channel]
        i = self.dragging
        x = (
            0
            if i == 0
            else 255
            if i == len(points) - 1
            else max(points[i - 1]["x"] + 0.01, min(points[i + 1]["x"] - 0.01, x))
        )
        points[i] = dict(x=x, y=y)
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, event):
        self.dragging = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#22252a"))
        painter.setPen(QPen(QColor("#454950"), 1))
        for i in range(1, 4):
            painter.drawLine(
                round(self.width() * i / 4),
                0,
                round(self.width() * i / 4),
                self.height(),
            )
            painter.drawLine(
                0,
                round(self.height() * i / 4),
                self.width(),
                round(self.height() * i / 4),
            )
        colors = ["#e6e7e9", "#ff6c6c", "#75d895", "#72abff"]
        painter.setPen(QPen(QColor(colors[self.channel]), 2))
        table = engine.curve_table(self.settings["channels"][self.channel])
        path = QPainterPath()
        for x, y in enumerate(table):
            point = QPointF(x / 255 * self.width(), (1 - y) * self.height())
            path.moveTo(point) if x == 0 else path.lineTo(point)
        painter.drawPath(path)
        painter.setBrush(QColor(colors[self.channel]))
        for point in self.settings["channels"][self.channel]:
            painter.drawEllipse(
                QPointF(
                    point["x"] / 255 * self.width(),
                    (1 - point["y"] / 255) * self.height(),
                ),
                4,
                4,
            )


class LevelsHistogram(QWidget):
    def __init__(self, dialog, histogram):
        super().__init__(dialog)
        self.dialog, self.histogram, self.dragging = dialog, histogram, None
        self.setMinimumWidth(350)
        self.setFixedHeight(180)
        self.setAccessibleName("Original Levels histogram and tonal handles")

    def positions(self, output=False):
        if output:
            return [self.dialog.value("Output black"), self.dialog.value("Output white")]
        low, high, gamma = (
            self.dialog.value(key) for key in ("Input black", "Input white", "Gamma")
        )
        return [low, low + (high - low) * 0.5**gamma, high]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#202226"))
        if self.histogram is not None:
            bins = self.histogram[self.dialog.levels_channel]
            positive = np.sort(bins[1:-1][bins[1:-1] > 0])
            peak = (
                min(float(max(bins)), float(positive[int((len(positive) - 1) * 0.95)]) * 4)
                if len(positive)
                else float(max(bins))
            )
            color = ["#a0a0a0", "#f46565", "#6cd28a", "#72a9ef"][self.dialog.levels_channel]
            if peak:
                for i, value in enumerate(bins):
                    height = min(120, value / peak * 120)
                    painter.fillRect(
                        QRectF(
                            i / 256 * self.width(), 120 - height, self.width() / 256 + 0.1, height
                        ),
                        QColor(color),
                    )
        for x in range(self.width()):
            tone = round(x / self.width() * 255)
            painter.setPen(QColor(tone, tone, tone))
            painter.drawLine(x, 142, x, 155)
        for output, y in ((False, 128), (True, 165)):
            positions = self.positions(output)
            for i, value in enumerate(positions):
                x = value / 255 * self.width()
                painter.setPen(QColor("#cccccc"))
                painter.setBrush(
                    QColor(
                        "#111111" if i == 0 else "#ffffff" if i == len(positions) - 1 else "#888888"
                    )
                )
                painter.drawEllipse(QPointF(x, y), 5, 5)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        output = event.position().y() > 145
        positions = self.positions(output)
        self.dragging = (
            output,
            min(
                range(len(positions)),
                key=lambda i: abs(positions[i] / 255 * self.width() - event.position().x()),
            ),
        )
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event):
        if self.dragging is None:
            return
        output, i = self.dragging
        value = max(0, min(255, event.position().x() / self.width() * 255))
        names = (
            ["Output black", "Output white"] if output else ["Input black", "Gamma", "Input white"]
        )
        low, high = self.dialog.value("Input black"), self.dialog.value("Input white")
        if not output:
            if i == 0:
                value = min(value, high - 1)
            elif i == 2:
                value = max(value, low + 1)
            else:
                fraction = max(0.001, min(0.999, (value - low) / max(1, high - low)))
                value = np.log(fraction) / np.log(0.5)
        self.dialog.fields[names[i]].setValue(value)

    def mouseReleaseEvent(self, event):
        self.dragging = None


class HueSpectrum(QWidget):
    """Before/after colors with the original four constrained band handles."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog, self.dragging = dialog, None
        self.setFixedHeight(68)
        self.setMinimumWidth(350)
        self.setAccessibleName("Hue range spectrum")

    def paintEvent(self, event):
        painter = QPainter(self)
        settings = self.dialog.settings["hsvSettings"]
        before = np.array(
            [colorsys.hls_to_rgb(x / self.width(), 0.5, 1) for x in range(self.width())]
        )
        after = engine.hue_values(before, settings)
        for x, (original, adjusted) in enumerate(zip(before, after)):
            for y, color in ((0, original), (42, adjusted)):
                painter.setPen(QColor.fromRgbF(*map(float, color)))
                painter.drawLine(x, y, x, y + 17)
        painter.setPen(QColor("#f5f5f5"))
        painter.setBrush(QColor("#111111"))
        band = hue.band_for(settings, self.dialog.hue_range)
        for i, key in enumerate(hue.KEYS):
            x = band[key] % 360 / 360 * self.width()
            painter.drawLine(QPointF(x, 18), QPointF(x, 41))
            painter.drawEllipse(QPointF(x, 25 if i in (0, 3) else 35), 4, 4)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        band = hue.band_for(self.dialog.settings["hsvSettings"], self.dialog.hue_range)
        self.dragging = min(
            range(4),
            key=lambda i: abs(band[hue.KEYS[i]] % 360 / 360 * self.width() - event.position().x()),
        )
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event):
        if self.dragging is None:
            return
        self.dialog.store_hue()
        settings = self.dialog.settings["hsvSettings"]
        band = hue.moved_handle(
            hue.band_for(settings, self.dialog.hue_range),
            self.dragging,
            event.position().x() / self.width() * 360,
        )
        self.dialog.set_hue_band(band)

    def mouseReleaseEvent(self, event):
        self.dragging = None


class FilterDialog(ValuesDialog):
    preview = Signal(dict)

    def exec_canvas(self, owner):
        """Keep the canvas available for sampling while protecting the document."""
        widgets = [
            owner.toolbar,
            owner.option_widget,
            owner.menuBar(),
            owner.splitter.widget(0),
            owner.splitter.widget(2),
        ]
        states = [(widget, widget.isEnabled()) for widget in widgets + list(owner.actions.values())]
        loop = QEventLoop()
        self.finished.connect(loop.quit)
        owner.filter_dialog = self
        try:
            for widget, _ in states:
                widget.setEnabled(False)
            self.setWindowModality(Qt.WindowModality.NonModal)
            self.show()
            loop.exec()
            return self.result()
        finally:
            owner.filter_dialog = None
            owner.canvas.drag = None
            for widget, enabled in states:
                widget.setEnabled(enabled)
            self.hide()
            self.timer.stop()

    def __init__(self, kind, settings=None, parent=None, histogram=None, adjustment=False):
        super().__init__(kind, parent)
        self.kind = kind
        self.adjustment = adjustment
        self.settings = deepcopy(settings or {})
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(80)
        self.timer.timeout.connect(lambda: self.preview.emit(self.result_settings()))
        self.setMinimumWidth(420)
        if kind == "Levels":
            self.settings.setdefault("levels", dict(channel="RGB", ranges=[{} for _ in range(4)]))
            self.add_choice(
                "Channel",
                ["RGB", "Red", "Green", "Blue"],
                self.settings["levels"].get("channel", "RGB"),
            )
            for label, key, value, low, high, decimals in (
                ("Input black", "black", 0, 0, 254, 0),
                ("Gamma", "gamma", 1, 0.1, 9.99, 2),
                ("Input white", "white", 255, 1, 255, 0),
                ("Output black", "outputBlack", 0, 0, 255, 0),
                ("Output white", "outputWhite", 255, 0, 255, 0),
            ):
                self.add_number(label, value, low, high, decimals)
            self.fields["Channel"].currentTextChanged.connect(self.switch_levels)
            self.switch_levels(self.value("Channel"))
            auto = QPushButton("Auto")
            auto.clicked.connect(lambda: self.auto_levels(histogram))
            self.form.addRow(auto)
            self.add_choice("Auto mode", ["Contrast", "Color", "Color + neutral midtones"])
            self.canvas_mode = QComboBox()
            self.canvas_mode.addItems(["Panel controls", "Black", "Gray", "White"])
            self.form.addRow("Sample original", self.canvas_mode)
            self.histogram_view = LevelsHistogram(self, histogram)
            self.form.insertRow(1, self.histogram_view)
        elif kind == "Curves":
            self.settings.setdefault(
                "curves",
                dict(
                    channel="RGB",
                    channels=[[dict(x=0, y=0), dict(x=255, y=255)] for _ in range(4)],
                ),
            )
            choice = self.add_choice(
                "Channel",
                ["RGB", "Red", "Green", "Blue"],
                self.settings["curves"].get("channel", "RGB"),
            )
            self.graph = CurvesGraph(self.settings["curves"])
            self.form.addRow(self.graph)
            choice.currentIndexChanged.connect(self.curve_channel)
            self.graph.changed.connect(self.schedule)
            self.form.addRow(QLabel("Click to add a point; drag to edit; right-click to remove."))
        elif kind == "Hue/Saturation":
            hsv = self.settings.setdefault(
                "hsvSettings",
                dict(range="Master", colorize=False, adjustments={}, bands={}),
            )
            hsv["adjustments"] = engine.dict_pairs(hsv.get("adjustments", {}))
            hsv["bands"] = engine.dict_pairs(hsv.get("bands", {}))
            self.add_choice(
                "Range",
                ["Master", "Reds", "Yellows", "Greens", "Cyans", "Blues", "Magentas"],
                hsv.get("range", "Master"),
            )
            self.add_number("Hue", 0, -180, 360)
            self.add_number("Saturation", 0, -100, 100)
            self.add_number("Lightness", 0, -100, 100)
            self.add_check("Colorize", hsv.get("colorize", False))
            self.add_check("Invert range", hsv.get("invertRange", False))
            for label in ("Falloff start", "Range start", "Range end", "Falloff end"):
                self.add_number(label, 0, 0, 360, 1)
            self.fields["Range"].currentTextChanged.connect(self.switch_hue)
            self.switch_hue(self.value("Range"))
            self.canvas_mode = QComboBox()
            self.canvas_mode.addItems(
                ["Panel controls", "Sample", "Add", "Remove", "Targeted adjustment"]
            )
            self.form.addRow("Canvas", self.canvas_mode)
            self.form.addRow(QLabel("Targeted drag: saturation · Ctrl-drag: hue"))
            self.spectrum = HueSpectrum(self)
            self.form.addRow(self.spectrum)
            self.fields["Colorize"].toggled.connect(self.colorize_changed)
            self.update_hue_controls()
        elif kind == "Exposure":
            s = self.settings.get("exposureSettings") or {}
            self.add_number("Exposure", s.get("exposure", 0), -20, 20, 2)
            self.add_number("Offset", s.get("offset", 0), -0.5, 0.5, 3)
            self.add_number("Gamma", s.get("gamma", 1), 0.01, 9.99, 2)
        elif kind == "Gradient Map":
            self.settings.setdefault("gradientMapSettings", {})
            for label, name, default in (
                ("Shadows", "shadows", 0),
                ("Highlights", "highlights", 1),
            ):
                button = QPushButton(label)
                color = self.settings["gradientMapSettings"].setdefault(
                    name, dict(red=default, green=default, blue=default)
                )
                self.color_button(button, color)
                button.clicked.connect(lambda _, b=button, n=name: self.choose_color(b, n))
                self.form.addRow(label, button)
            self.add_check("Reverse", self.settings["gradientMapSettings"].get("reversed", False))
        elif kind == "Grain":
            s = self.settings.get("grainSettings") or {}
            self.add_number("Amount", s.get("amount", 25), 0, 100, 1)
            self.add_number("Size", s.get("size", 1.5), 0.5, 20, 2)
            self.add_number("Roughness", s.get("roughness", 50), 0, 100, 1)
        elif kind == "Black & White":
            s = self.settings.get("blackWhiteSettings") or {}
            for key, default in (
                ("reds", 40),
                ("yellows", 60),
                ("greens", 40),
                ("cyans", 60),
                ("blues", 20),
                ("magentas", 80),
            ):
                self.add_number(key.capitalize(), s.get(key, default), -200, 300)
            self.add_check("Tint", s.get("tint", False))
            self.add_number("Tint hue", s.get("tintHue", 40), 0, 360)
            self.add_number("Tint saturation", s.get("tintSaturation", 20), 0, 100)
        elif kind == "Color Balance":
            s = self.settings.get("colorBalanceSettings") or {}
            for label, key in (("Shadows", "shadow"), ("Mid", "mid"), ("Highlights", "highlight")):
                for title, axis in (
                    ("cyan/red", "CyanRed"),
                    ("magenta/green", "MagentaGreen"),
                    ("yellow/blue", "YellowBlue"),
                ):
                    self.add_number(f"{label} {title}", s.get(key + axis, 0), -100, 100)
            self.add_check("Preserve luminosity", s.get("preserveLuminosity", True))
        elif kind == "Gaussian Blur":
            self.add_number(
                "Radius",
                self.settings.get("blurRadius" if adjustment else "radius", 10),
                0.1,
                250,
                1,
            )
        elif kind == "Motion Blur":
            self.add_number(
                "Angle", self.settings.get("motionAngle" if adjustment else "angle", 0), -90, 90, 1
            )
            self.add_number(
                "Distance",
                self.settings.get("motionDistance" if adjustment else "distance", 10),
                1,
                2000,
                1,
            )
        elif kind == "Add Noise":
            self.add_number(
                "Amount",
                self.settings.get("noiseAmount" if adjustment else "amount", 10),
                0.1,
                400,
                1,
            )
            self.add_check(
                "Gaussian", self.settings.get("noiseGaussian" if adjustment else "gaussian", False)
            )
            self.add_check(
                "Monochromatic",
                self.settings.get("noiseMonochromatic" if adjustment else "monochromatic", False),
            )
        elif kind == "Dither":
            self.add_choice("Style", dither.STYLES, self.settings.get("style", dither.STYLES[0]))
            self.add_number("Pixel size", self.settings.get("pixelSize", 2), 1, 32)
            self.add_choice(
                "Pixel shape", ["Square", "Dot"], self.settings.get("pixelShape", "Square")
            )
            self.add_number("Cell size", self.settings.get("cellSize", 8), 4, 64)
            self.add_number("Text size", self.settings.get("textSize", 14), 6, 64)
            self.add_number("Line spacing", self.settings.get("lineSpacing", 4), 2, 32)
            for label, key, default, low, high in (
                ("Glow", "glow", 35, 0, 100),
                ("Dots", "dots", 0, 0, 100),
                ("Wobble", "wobble", 0, 0, 64),
                ("Angle", "angle", 45, -90, 90),
                ("Levels", "levels", 2, 2, 8),
                ("Diffusion", "diffusion", 100, 0, 100),
                ("Density", "density", 0, -100, 100),
                ("Contrast", "contrast", 0, -100, 100),
            ):
                self.add_number(label, self.settings.get(key, default), low, high)
            self.add_choice(
                "Colors",
                ["Black & White", "Two Colors", "Original"],
                self.settings.get("colors", "Black & White"),
            )
            self.add_check("Light on dark", self.settings.get("lightOnDark", True))
            self.dark_button = ColorButton(
                self.settings.get("dark", [0, 0, 0]), "Dark dither color"
            )
            self.light_button = ColorButton(
                self.settings.get("light", [255, 255, 255]), "Light dither color"
            )
            self.form.addRow("Dark color", self.dark_button)
            self.form.addRow("Light color", self.light_button)
            self.dark_button.changed.connect(self.schedule)
            self.light_button.changed.connect(self.schedule)
            field = QLineEdit(self.settings.get("characters", " .:-=+*#%@"))
            field.setMaxLength(64)
            self.fields["Characters"] = field
            self.form.addRow("ASCII characters", field)
        elif kind == "Camera Raw Filter":
            self.camera_fields = []
            self.camera_raw_fields()
        elif kind == "Vignette":
            for label, key, default, low, high in (
                ("Amount", "vignetteAmount", 35, 0, 100),
                ("Midpoint", "vignetteMidpoint", 50, 0, 100),
                ("Roundness", "vignetteRoundness", 100, -100, 100),
                ("Feather", "vignetteFeather", 60, 0, 100),
                ("Highlights", "vignetteHighlights", 25, 0, 100),
            ):
                self.add_number(label, self.settings.get(key, default), low, high)
            self.vignette_color = ColorButton(
                self.settings.get("vignetteColor", [0, 0, 0]), "Vignette edge color"
            )
            self.form.addRow("Edge color", self.vignette_color)
            self.vignette_color.changed.connect(self.schedule)
        elif kind == "Bloom / Glow":
            self.add_number("Amount", self.settings.get("bloomAmount", 40), 0, 100)
            self.add_number("Radius", self.settings.get("bloomRadius", 24), 1, 150)
        elif kind == "Tonal Contrast":
            for label, key, default, low, high in (
                ("Amount", "tonalAmount", 50, 0, 100),
                ("Radius", "tonalRadius", 16, 1, 100),
                ("Shadows", "tonalShadows", 40, -100, 100),
                ("Midtones", "tonalMidtones", 60, -100, 100),
                ("Highlights", "tonalHighlights", 30, -100, 100),
            ):
                self.add_number(label, self.settings.get(key, default), low, high)
        elif kind == "Lens Correction":
            self.add_number("Distortion", self.settings.get("distortion", 0), -100, 100, 1)
        self.preview_check = self.add_check("Preview", True)
        if kind in ("Levels", "Hue/Saturation"):
            reset = QPushButton("Reset")
            reset.setIcon(icon("reset"))
            reset.clicked.connect(self.reset_controls)
            self.form.addRow(reset)
        for widget in self.fields.values():
            signal = (
                widget.valueChanged
                if isinstance(widget, (QSpinBox, QDoubleSpinBox))
                else widget.currentTextChanged
                if isinstance(widget, QComboBox)
                else widget.textChanged
                if isinstance(widget, QLineEdit)
                else widget.toggled
            )
            signal.connect(self.schedule)

    def camera_raw_fields(self):
        body = QWidget(self)
        self.layout_box.removeItem(self.form)
        body.setLayout(self.form)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        self.layout_box.insertWidget(0, scroll)
        self.resize(560, 700)

        def add(label, group, key, default=0, low=-100, high=100, kind="number"):
            source = self.settings.get(group, {}) if group else self.settings
            if group == "mixer" and isinstance(key, tuple):
                value = source.get(key[0], [0] * 8)[key[1]]
            elif group == "grading" and isinstance(key, tuple):
                value = source.get(key[0], {}).get(key[1], default)
            else:
                value = source.get(key, default)
            if kind == "check":
                self.add_check(label, value)
            elif kind == "choice":
                self.add_choice(label, low, value)
            elif kind == "text":
                points = value if isinstance(value, list) else []
                field = QLineEdit(",".join(f"{p['x']}:{p['y']}" for p in points) or "0:0,1:1")
                self.fields[label] = field
                self.form.addRow(label, field)
            elif kind == "json":
                field = QLineEdit(json.dumps(value, separators=(",", ":")))
                field.setMaxLength(8192)
                self.fields[label] = field
                self.form.addRow(label, field)
            else:
                self.add_number(label, value, low, high, 2 if key == "exposure" else 0)
            self.camera_fields.append((label, group, key))

        sections = (
            (
                "Light and Color",
                "",
                (
                    (
                        "temperature",
                        "tint",
                        "contrast",
                        "highlights",
                        "shadows",
                        "whites",
                        "blacks",
                        "vibrance",
                        "saturation",
                    ),
                    -100,
                    100,
                ),
            ),
            (
                "Effects",
                "",
                (
                    (
                        "texture",
                        "clarity",
                        "dehaze",
                        "glowRange",
                        "glowSpread",
                        "glowWarmth",
                        "vignetteAmount",
                        "vignetteRoundness",
                    ),
                    -100,
                    100,
                ),
            ),
            (
                "Curve",
                "curve",
                (("shadows", "darks", "lights", "highlights", "refineSaturation"), -100, 100),
            ),
            (
                "Detail",
                "detail",
                (
                    (
                        "sharpenAmount",
                        "sharpenRadius",
                        "sharpenDetail",
                        "sharpenMasking",
                        "noiseLuminance",
                        "noiseLuminanceDetail",
                        "noiseLuminanceContrast",
                        "noiseColor",
                        "noiseColorDetail",
                        "noiseColorSmoothness",
                    ),
                    0,
                    150,
                ),
            ),
            (
                "Optics",
                "optics",
                (("distortion", "vignetteAmount", "purpleAmount", "greenAmount"), -100, 100),
            ),
            (
                "Geometry",
                "geometry",
                (
                    ("vertical", "horizontal", "rotate", "aspect", "scale", "offsetX", "offsetY"),
                    -100,
                    100,
                ),
            ),
            (
                "Calibration",
                "calibration",
                (
                    (
                        "shadowTint",
                        "redHue",
                        "redSaturation",
                        "greenHue",
                        "greenSaturation",
                        "blueHue",
                        "blueSaturation",
                    ),
                    -100,
                    100,
                ),
            ),
        )
        for title, group, (keys, low, high) in sections:
            self.form.addRow(QLabel(title))
            for key in keys:
                add(
                    f"{title} {key}",
                    group,
                    key,
                    0,
                    -45 if key == "rotate" else low,
                    45 if key == "rotate" else high,
                )
        add("Exposure", "", "exposure", 0, -5, 5)
        add("White Balance", "", "whiteBalance", "Custom", ["Custom", "Auto"], kind="choice")
        for key, default in (
            ("glow", 0),
            ("vignetteMidpoint", 50),
            ("vignetteFeather", 50),
            ("vignetteHighlights", 0),
            ("grainAmount", 0),
            ("grainSize", 25),
            ("grainRoughness", 50),
        ):
            add(f"Effects {key}", "", key, default, 0, 100)
        add(
            "Glow style",
            "",
            "glowStyle",
            "Diffusion",
            ["Diffusion", "Bloom", "Halation"],
            kind="choice",
        )
        add(
            "Vignette style",
            "",
            "vignetteStyle",
            "Highlight Priority",
            ["Highlight Priority", "Color Priority", "Paint Overlay"],
            kind="choice",
        )
        for key in ("rgb", "red", "green", "blue"):
            add(f"Curve {key}", "curve", key, kind="text")
        for key, default in (("shadowSplit", 25), ("darkSplit", 50), ("lightSplit", 75)):
            add(f"Curve {key}", "curve", key, default, 0, 100)
        for title, field in (
            ("Hue", "hue"),
            ("Saturation", "saturation"),
            ("Luminance", "luminance"),
        ):
            for index, color in enumerate(
                ("Reds", "Oranges", "Yellows", "Greens", "Aquas", "Blues", "Purples", "Magentas")
            ):
                add(f"Mixer {title} {color}", "mixer", (field, index))
        add("Mixer point colors JSON", "mixer", "points", [], kind="json")
        for wheel in ("shadows", "midtones", "highlights", "global"):
            for field in ("hue", "saturation", "luminance"):
                add(
                    f"Grading {wheel} {field}",
                    "grading",
                    (wheel, field),
                    0,
                    -100 if field == "luminance" else 0,
                    360 if field == "hue" else 100,
                )
        add("Grading blending", "grading", "blending", 50, 0, 100)
        add("Grading balance", "grading", "balance", 0, -100, 100)
        for key, default, low, high in (
            ("profileDistortion", 100, 0, 100),
            ("profileVignetting", 100, 0, 100),
            ("purpleHueLow", 270, 0, 360),
            ("purpleHueHigh", 310, 0, 360),
            ("greenHueLow", 60, 0, 360),
            ("greenHueHigh", 120, 0, 360),
            ("vignetteMidpoint", 50, 0, 100),
        ):
            add(f"Optics {key}", "optics", key, default, low, high)
        add("Optics chromatic aberration", "optics", "removeChromaticAberration", kind="check")
        add("Optics lens profile", "optics", "enableLensProfile", kind="check")
        add("Geometry upright", "geometry", "upright", "Off", ["Off", "Guided"], kind="choice")
        add(
            "Geometry projection",
            "geometry",
            "projection",
            "Perspective",
            ["Perspective", "Rectilinear"],
            kind="choice",
        )
        add("Geometry guides JSON", "geometry", "guides", [], kind="json")
        add("Geometry constrain crop", "geometry", "constrainCrop", kind="check")
        add(
            "Calibration process",
            "calibration",
            "process",
            "Version 6",
            [f"Version {i}" for i in range(1, 7)],
            kind="choice",
        )

    def reset_controls(self):
        self.canvas_mode.setCurrentIndex(0)
        if self.kind == "Levels":
            self.settings["levels"] = dict(channel="RGB", ranges=[{} for _ in range(4)])
            del self.levels_channel
            field, value = self.fields["Channel"], "RGB"
            switch = self.switch_levels
        else:
            colorize = self.value("Colorize")
            self.settings["hsvSettings"] = dict(
                range="Master",
                colorize=colorize,
                adjustments={"Master": dict(hue=0, saturation=25 if colorize else 0, lightness=0)},
                bands={},
            )
            del self.hue_range
            field, value = self.fields["Range"], "Master"
            switch = self.switch_hue
            self.fields["Invert range"].blockSignals(True)
            self.fields["Invert range"].setChecked(False)
            self.fields["Invert range"].blockSignals(False)
        field.blockSignals(True)
        field.setCurrentText(value)
        field.blockSignals(False)
        switch(value)
        self.schedule()

    def schedule(self, *args):
        if hasattr(self, "histogram_view"):
            self.histogram_view.update()
        if hasattr(self, "spectrum"):
            self.store_hue()
            self.settings["hsvSettings"].update(
                range=self.hue_range,
                colorize=self.value("Colorize"),
                invertRange=self.value("Invert range"),
            )
            self.spectrum.update()
            self.update_hue_controls()
        self.timer.start()

    def update_hue_controls(self):
        colorize = self.value("Colorize")
        self.fields["Range"].setEnabled(not colorize)
        self.fields["Hue"].setRange(0 if colorize else -180, 360 if colorize else 180)
        self.fields["Saturation"].setRange(0 if colorize else -100, 100)
        self.canvas_mode.setEnabled(not colorize)
        self.spectrum.setEnabled(not colorize and self.hue_range != "Master")
        for label in ("Falloff start", "Range start", "Range end", "Falloff end"):
            self.fields[label].setEnabled(not colorize and self.hue_range != "Master")

    def colorize_changed(self, checked):
        self.settings["hsvSettings"].update(adjustments={}, bands={})
        self.fields["Range"].setCurrentText("Master")
        for label, value in (("Hue", 0), ("Saturation", 25 if checked else 0), ("Lightness", 0)):
            self.fields[label].setValue(value)
        self.canvas_mode.setCurrentIndex(0)
        self.schedule()

    def set_hue_band(self, band):
        for label, key in zip(
            ("Falloff start", "Range start", "Range end", "Falloff end"), hue.KEYS
        ):
            self.fields[label].blockSignals(True)
            self.fields[label].setValue(band[key])
            self.fields[label].blockSignals(False)
        self.schedule()

    def begin_hue_canvas(self, color, x):
        self.hue_drag = None
        value = hue.sampled_hue(color)
        mode = self.canvas_mode.currentText()
        if self.value("Colorize") or value is None or mode == "Panel controls":
            return
        self.store_hue()
        settings = self.settings["hsvSettings"]
        if mode == "Targeted adjustment":
            settings["range"] = self.hue_range
            self.fields["Range"].setCurrentText(hue.owning_range(settings, value))
            self.hue_drag = (x, self.value("Hue"), self.value("Saturation"))
        elif self.hue_range != "Master":
            self.set_hue_band(hue.sample_band(hue.band_for(settings, self.hue_range), value, mode))

    def drag_hue_canvas(self, x, adjusts_hue):
        if self.hue_drag is None:
            return
        start, initial_hue, initial_saturation = self.hue_drag
        label, initial, limit = (
            ("Hue", initial_hue, 180) if adjusts_hue else ("Saturation", initial_saturation, 100)
        )
        self.fields[label].setValue(max(-limit, min(limit, initial + (x - start) / 2)))

    def color_button(self, button, color):
        c = QColor.fromRgbF(color["red"], color["green"], color["blue"])
        button.setStyleSheet(
            f"background:{c.name()};color:{'#111' if c.lightnessF() > 0.5 else '#fff'}"
        )

    def choose_color(self, button, name):
        s = self.settings["gradientMapSettings"][name]
        color = QColorDialog.getColor(QColor.fromRgbF(s["red"], s["green"], s["blue"]), self)
        if color.isValid():
            self.settings["gradientMapSettings"][name] = dict(
                red=color.redF(), green=color.greenF(), blue=color.blueF()
            )
            self.color_button(button, self.settings["gradientMapSettings"][name])
            self.schedule()

    def switch_levels(self, name):
        if hasattr(self, "levels_channel"):
            self.store_levels()
        self.levels_channel = ["RGB", "Red", "Green", "Blue"].index(name)
        r = self.settings["levels"]["ranges"][self.levels_channel]
        for label, key, default in (
            ("Input black", "black", 0),
            ("Gamma", "gamma", 1),
            ("Input white", "white", 255),
            ("Output black", "outputBlack", 0),
            ("Output white", "outputWhite", 255),
        ):
            widget = self.fields[label]
            widget.blockSignals(True)
            widget.setValue(r.get(key, default))
            widget.blockSignals(False)

    def store_levels(self):
        self.settings["levels"]["ranges"][self.levels_channel] = {
            key: self.value(label)
            for label, key in (
                ("Input black", "black"),
                ("Gamma", "gamma"),
                ("Input white", "white"),
                ("Output black", "outputBlack"),
                ("Output white", "outputWhite"),
            )
        }

    def auto_levels(self, histogram):
        if histogram is None:
            return
        self.settings["levels"] = levels.automatic(histogram, self.value("Auto mode"))
        del self.levels_channel
        self.fields["Channel"].blockSignals(True)
        self.fields["Channel"].setCurrentText("RGB")
        self.fields["Channel"].blockSignals(False)
        self.switch_levels("RGB")
        self.canvas_mode.setCurrentIndex(0)
        self.schedule()

    def sample_levels(self, color):
        mode = self.canvas_mode.currentText()
        if mode == "Panel controls" or color is None or color[3] == 0:
            return
        self.store_levels()
        self.settings["levels"] = levels.sampled(self.settings["levels"], color, mode)
        del self.levels_channel
        self.switch_levels(self.value("Channel"))
        self.schedule()

    def curve_channel(self, index):
        self.graph.channel = index
        self.settings["curves"]["channel"] = self.value("Channel")
        self.graph.update()

    def switch_hue(self, name):
        if hasattr(self, "hue_range"):
            self.store_hue()
        self.hue_range = name
        hsv = self.settings["hsvSettings"]
        values = hsv["adjustments"].get(name, {})
        for label, key in (
            ("Hue", "hue"),
            ("Saturation", "saturation"),
            ("Lightness", "lightness"),
        ):
            self.fields[label].blockSignals(True)
            self.fields[label].setValue(values.get(key, 0))
            self.fields[label].blockSignals(False)
        defaults = {
            "Master": (0, 0, 360, 360),
            "Reds": (315, 345, 15, 45),
            "Yellows": (15, 45, 75, 105),
            "Greens": (75, 105, 135, 165),
            "Cyans": (135, 165, 195, 225),
            "Blues": (195, 225, 255, 285),
            "Magentas": (255, 285, 315, 345),
        }
        band = hsv["bands"].get(
            name,
            dict(
                zip(
                    ("falloffStart", "rangeStart", "rangeEnd", "falloffEnd"),
                    defaults[name],
                )
            ),
        )
        for label, key in (
            ("Falloff start", "falloffStart"),
            ("Range start", "rangeStart"),
            ("Range end", "rangeEnd"),
            ("Falloff end", "falloffEnd"),
        ):
            self.fields[label].blockSignals(True)
            self.fields[label].setValue(band[key])
            self.fields[label].setEnabled(name != "Master")
            self.fields[label].blockSignals(False)

    def store_hue(self):
        hsv = self.settings["hsvSettings"]
        hsv["adjustments"][self.hue_range] = {
            key: self.value(label)
            for label, key in (
                ("Hue", "hue"),
                ("Saturation", "saturation"),
                ("Lightness", "lightness"),
            )
        }
        hsv["bands"][self.hue_range] = {
            key: self.value(label)
            for label, key in (
                ("Falloff start", "falloffStart"),
                ("Range start", "rangeStart"),
                ("Range end", "rangeEnd"),
                ("Falloff end", "falloffEnd"),
            )
        }

    def result_settings(self):
        kind = self.kind
        if kind == "Levels":
            self.store_levels()
            self.settings["levels"]["channel"] = self.value("Channel")
        elif kind == "Hue/Saturation":
            self.store_hue()
            s = self.settings["hsvSettings"]
            s.update(
                range=self.hue_range,
                colorize=self.value("Colorize"),
                invertRange=self.value("Invert range"),
            )
            # Preserve Swift's enum dictionary wire format for macOS decoding.
            s = deepcopy(s)
            for key in ("adjustments", "bands"):
                s[key] = [item for pair in s[key].items() for item in pair]
            self.settings["hsvSettings"] = s
        elif kind == "Exposure":
            self.settings["exposureSettings"] = {
                k: self.value(item)
                for k, item in (
                    ("exposure", "Exposure"),
                    ("offset", "Offset"),
                    ("gamma", "Gamma"),
                )
            }
        elif kind == "Gradient Map":
            self.settings["gradientMapSettings"]["reversed"] = self.value("Reverse")
        elif kind == "Grain":
            self.settings["grainSettings"] = dict(
                amount=self.value("Amount"),
                size=self.value("Size"),
                roughness=self.value("Roughness"),
                seed=(self.settings.get("grainSettings") or {}).get("seed", 0),
            )
        elif kind == "Black & White":
            self.settings["blackWhiteSettings"] = {
                **{
                    key: self.value(key.capitalize())
                    for key in ("reds", "yellows", "greens", "cyans", "blues", "magentas")
                },
                "tint": self.value("Tint"),
                "tintHue": self.value("Tint hue"),
                "tintSaturation": self.value("Tint saturation"),
            }
        elif kind == "Color Balance":
            self.settings["colorBalanceSettings"] = {
                **{
                    key + axis: self.value(f"{label} {title}")
                    for label, key in (
                        ("Shadows", "shadow"),
                        ("Mid", "mid"),
                        ("Highlights", "highlight"),
                    )
                    for title, axis in (
                        ("cyan/red", "CyanRed"),
                        ("magenta/green", "MagentaGreen"),
                        ("yellow/blue", "YellowBlue"),
                    )
                },
                "preserveLuminosity": self.value("Preserve luminosity"),
            }
        elif kind in ("Gaussian Blur", "Motion Blur", "Add Noise") and self.adjustment:
            for label, key in (
                ("Radius", "blurRadius"),
                ("Angle", "motionAngle"),
                ("Distance", "motionDistance"),
                ("Amount", "noiseAmount"),
                ("Gaussian", "noiseGaussian"),
                ("Monochromatic", "noiseMonochromatic"),
            ):
                if label in self.fields:
                    self.settings[key] = self.value(label)
        elif kind == "Dither":
            for label, key in (
                ("Style", "style"),
                ("Pixel size", "pixelSize"),
                ("Pixel shape", "pixelShape"),
                ("Cell size", "cellSize"),
                ("Text size", "textSize"),
                ("Line spacing", "lineSpacing"),
                ("Glow", "glow"),
                ("Dots", "dots"),
                ("Wobble", "wobble"),
                ("Angle", "angle"),
                ("Levels", "levels"),
                ("Diffusion", "diffusion"),
                ("Density", "density"),
                ("Contrast", "contrast"),
                ("Colors", "colors"),
                ("Light on dark", "lightOnDark"),
                ("Characters", "characters"),
            ):
                self.settings[key] = self.value(label)
            self.settings["dark"] = list(self.dark_button.rgb())
            self.settings["light"] = list(self.light_button.rgb())
        elif kind == "Camera Raw Filter":
            for label, group, key in self.camera_fields:
                target = self.settings.setdefault(group, {}) if group else self.settings
                if isinstance(key, tuple) and group == "mixer":
                    field, index = key
                    values = list(target.get(field, [0] * 8))
                    values[index] = self.value(label)
                    target[field] = values
                elif isinstance(key, tuple) and group == "grading":
                    wheel, field = key
                    target.setdefault(wheel, {})[field] = self.value(label)
                elif group == "curve" and key in ("rgb", "red", "green", "blue"):
                    try:
                        target[key] = [
                            dict(zip(("x", "y"), (float(x), float(y))))
                            for x, y in (part.split(":") for part in self.value(label).split(","))
                        ]
                    except (ValueError, TypeError) as exc:
                        raise ValueError(
                            "Curve points must use x:y pairs separated by commas."
                        ) from exc
                elif (group, key) in (("mixer", "points"), ("geometry", "guides")):
                    try:
                        target[key] = json.loads(self.value(label))
                    except (ValueError, TypeError) as exc:
                        raise ValueError(
                            "Point colors and geometry guides must be JSON arrays."
                        ) from exc
                else:
                    target[key] = self.value(label)
        elif kind in ("Vignette", "Bloom / Glow", "Tonal Contrast"):
            for label, key in (
                (
                    "Amount",
                    "vignetteAmount"
                    if kind == "Vignette"
                    else "bloomAmount"
                    if kind == "Bloom / Glow"
                    else "tonalAmount",
                ),
                ("Radius", "bloomRadius" if kind == "Bloom / Glow" else "tonalRadius"),
                ("Midpoint", "vignetteMidpoint"),
                ("Roundness", "vignetteRoundness"),
                ("Feather", "vignetteFeather"),
                ("Highlights", "vignetteHighlights" if kind == "Vignette" else "tonalHighlights"),
                ("Shadows", "tonalShadows"),
                ("Midtones", "tonalMidtones"),
            ):
                if label in self.fields:
                    self.settings[key] = self.value(label)
            if kind == "Vignette":
                self.settings["vignetteColor"] = list(self.vignette_color.rgb())
        else:
            for label, widget in self.fields.items():
                if label != "Preview":
                    self.settings[label[0].lower() + label[1:]] = self.value(label)
        result = deepcopy(self.settings)
        # Keep editable dictionaries internally after the serialized copy is made.
        if kind == "Hue/Saturation":
            for key in ("adjustments", "bands"):
                self.settings["hsvSettings"][key] = engine.dict_pairs(
                    self.settings["hsvSettings"][key]
                )
        result["kind"] = kind
        result["preview"] = self.value("Preview")
        return result


class JPEGDialog(ValuesDialog):
    def __init__(self, image, resolution, quality=90, parent=None):
        super().__init__("Export JPEG", parent)
        self.image, self.resolution, self.data = image, resolution, None
        self.matte = (255, 255, 255)
        self.preview_image = QLabel()
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.setFixedSize(560, 330)
        self.layout_box.insertWidget(0, self.preview_image)
        self.quality = self.add_number("Quality (%)", quality, 1, 100)
        self.color_button = QPushButton("White")
        self.form.addRow("Background for transparency", self.color_button)
        self.color_button.clicked.connect(self.choose_matte)
        self.details = QLabel(f"{image.width} × {image.height} px · sRGB")
        self.layout_box.insertWidget(self.layout_box.count() - 1, self.details)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.update_preview)
        self.quality.valueChanged.connect(self.schedule)
        self.schedule()

    def schedule(self):
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.timer.start(200)

    def choose_matte(self):
        color = QColorDialog.getColor(QColor(*self.matte), self, "JPEG background")
        if color.isValid():
            self.matte = color.red(), color.green(), color.blue()
            self.color_button.setText(color.name())
            self.schedule()

    def update_preview(self):
        quality, matte = self.value("Quality (%)"), self.matte

        def encode():
            buffer = BytesIO()
            image = Image.alpha_composite(
                Image.new("RGBA", self.image.size, (*matte, 255)), self.image
            ).convert("RGB")
            image.save(
                buffer,
                format="JPEG",
                quality=quality,
                subsampling=0,
                dpi=(self.resolution, self.resolution),
                icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes(),
            )
            return buffer.getvalue()

        self.data = run_task(self, "Encoding JPEG preview…", encode)
        pixmap = QPixmap()
        pixmap.loadFromData(self.data, "JPEG")
        self.preview_image.setPixmap(
            pixmap.scaled(
                self.preview_image.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.details.setText(
            f"{self.image.width} × {self.image.height} px · sRGB · {len(self.data):,} bytes · encoded preview"
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

    def done(self, result):
        self.timer.stop()
        super().done(result)
