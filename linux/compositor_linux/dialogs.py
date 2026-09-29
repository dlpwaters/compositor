"""Native editor dialogs, with cancellable previews and editable curves."""

import colorsys
from copy import deepcopy
from io import BytesIO

import numpy as np
from PIL import Image, ImageCms
from PySide6.QtCore import QEventLoop, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import engine, hue, levels
from .model import Transform
from .tasks import run_task


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
        return widget.value()


class SizeDialog(ValuesDialog):
    def __init__(self, title, width, height, resolution=72, parent=None):
        super().__init__(title, parent)
        self.add_number("Width", width, 1, 30_000)
        self.add_number("Height", height, 1, 30_000)
        self.add_number("Resolution (ppi)", resolution, 1, 9600, 2)
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

    def __init__(self, kind, settings=None, parent=None, histogram=None):
        super().__init__(kind, parent)
        self.kind = kind
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
        elif kind == "Gaussian Blur":
            self.add_number("Radius", self.settings.get("radius", 1), 0.1, 250, 1)
        elif kind == "Motion Blur":
            self.add_number("Angle", self.settings.get("angle", 0), -90, 90, 1)
            self.add_number("Distance", self.settings.get("distance", 10), 1, 2000, 1)
        elif kind == "Add Noise":
            self.add_number("Amount", self.settings.get("amount", 10), 0.1, 400, 1)
            self.add_check("Gaussian", self.settings.get("gaussian", False))
            self.add_check("Monochromatic", self.settings.get("monochromatic", False))
        elif kind == "Lens Correction":
            self.add_number("Distortion", self.settings.get("distortion", 0), -100, 100, 1)
        self.preview_check = self.add_check("Preview", True)
        if kind in ("Levels", "Hue/Saturation"):
            reset = QPushButton("Reset")
            reset.clicked.connect(self.reset_controls)
            self.form.addRow(reset)
        for widget in self.fields.values():
            signal = (
                widget.valueChanged
                if isinstance(widget, (QSpinBox, QDoubleSpinBox))
                else widget.currentTextChanged
                if isinstance(widget, QComboBox)
                else widget.toggled
            )
            signal.connect(self.schedule)

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
