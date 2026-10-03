"""Compositor's native Linux application and desktop integration entry point."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageFilter, ImageOps
from PySide6.QtCore import (
    QByteArray,
    QEventLoop,
    QMimeData,
    QSettings,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QImage,
    QKeySequence,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabBar,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__, editing, engine, kernels, models, raw, store
from .canvas import Canvas, qimage
from .dialogs import (
    ColorRangeDialog,
    EffectDialog,
    FilterDialog,
    InlineTextEditor,
    JPEGDialog,
    NewLayerDialog,
    ScrubLabel,
    SizeDialog,
    TextLayerDialog,
    TransformDialog,
    TrimDialog,
    ValuesDialog,
    number,
)
from .icons import icon
from .model import ADJUSTMENTS, BLENDS, Document, History, Layer, new_id
from .tasks import Job, Result, run_task
from .text import render_text

STYLE = """
QMainWindow,QWidget { background:#292b30;color:#e6e7eb;font-size:13px; }
QMenuBar,QToolBar,QTabBar { background:#313339; }
QToolBar { border:0;spacing:5px;padding:5px; }
QPushButton,QToolButton { background:#3b3e45;border:1px solid #4b4e56;border-radius:5px;padding:5px 9px; }
QPushButton:hover,QToolButton:hover { background:#4b505b; }
QToolButton[iconButton="true"] { background:transparent;border:1px solid transparent;border-radius:2px;padding:0; }
QToolButton[iconButton="true"]:hover { background:#45484f;border-color:#55585f; }
QToolButton[iconButton="true"]:checked { background:#4a4d54;border-color:#73767d; }
QToolButton[iconButton="true"]:pressed { background:#555860; }
QToolButton[iconButton="true"]:focus { border-color:#8b9daf; }
QWidget#toolRail { background:#2b2d32; }
QComboBox,QSpinBox,QDoubleSpinBox,QLineEdit { background:#23252a;border:1px solid #4d515a;border-radius:4px;padding:4px; }
QTreeWidget { background:#25272c;border:0;outline:0; }
QTreeWidget::item { padding:4px; }
QTreeWidget::item:selected { background:#3c526b; }
QMenu { background:#303239;border:1px solid #555963; }
QMenu::item { padding:7px 30px 7px 14px; }
QMenu::item:selected { background:#45637f; }
QMenu::item:disabled { color:#777b84; }
QTabBar::tab { padding:8px 12px;background:#292c32;border-right:1px solid #44474e; }
QTabBar::tab:selected { background:#424750; }
QSplitter::handle { background:#191b1f;width:4px; }
QStatusBar { background:#303238; }
"""


def icon_button(name, label, parent=None):
    button = QToolButton(parent)
    button.setProperty("iconButton", True)
    button.setIcon(icon(name))
    button.setIconSize(QSize(20, 20))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setFixedSize(34, 32)
    button.setToolTip(label)
    button.setAccessibleName(label)
    return button


class LayerTree(QTreeWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setObjectName("layersPanel")
        self.setAccessibleName("Layers")
        self.setColumnCount(3)
        self.setHeaderLabels(["Layer", "Mask", "Link"])
        self.setColumnWidth(0, 170)
        self.setColumnWidth(1, 45)
        self.setColumnWidth(2, 35)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setIconSize(QPixmap(44, 32).size())
        self.itemSelectionChanged.connect(owner.layer_selection)
        self.itemChanged.connect(owner.layer_changed)
        self.itemClicked.connect(owner.layer_clicked)
        self.itemDoubleClicked.connect(owner.layer_double_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(owner.layer_context)

    def mimeTypes(self):
        return ["application/x-compositor-layer"]

    def mimeData(self, items):
        mime = QMimeData()
        payload = dict(
            project=self.owner.history.document.id,
            layers=[item.data(0, Qt.ItemDataRole.UserRole) for item in items],
        )
        mime.setData("application/x-compositor-layer", QByteArray(json.dumps(payload).encode()))
        return mime

    def dropEvent(self, event):
        try:
            payload = json.loads(bytes(event.mimeData().data("application/x-compositor-layer")))
            target = self.itemAt(event.position().toPoint())
            document = self.owner.history.document
            layer = document.layer(target.data(0, Qt.ItemDataRole.UserRole)) if target else None
            parent = layer.id if layer and layer.group else layer.parent if layer else None
            before = layer.id if layer and not layer.group else None
            ids = payload["layers"]
            if payload["project"] != document.id:
                self.owner.transfer_layers(payload["project"], ids, parent)
            else:
                for id in ids:
                    if id != before:
                        editing.move_layer(self.owner.history, id, parent, before)
            self.owner.refresh()
            event.acceptProposedAction()
        except Exception as exc:
            self.owner.fail(exc)
            event.ignore()


class ProjectTabs(QTabBar):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setAcceptDrops(True)
        self.setTabsClosable(True)
        self.setExpanding(False)
        self.setMovable(True)
        self.tabMoved.connect(owner.tab_moved)
        self.currentChanged.connect(owner.switch_project)
        self.tabCloseRequested.connect(owner.close_project)

    def tabInserted(self, index):
        super().tabInserted(index)
        button = icon_button("close", "Close project", self)
        button.setFixedSize(20, 20)
        button.setIconSize(QSize(16, 16))
        button.clicked.connect(lambda: self.close_tab(button))
        self.setTabButton(index, QTabBar.ButtonPosition.RightSide, button)

    def close_tab(self, button):
        # Indices shift when another tab closes; resolve the button's current tab.
        for index in range(self.count()):
            if self.tabButton(index, QTabBar.ButtonPosition.RightSide) is button:
                self.tabCloseRequested.emit(index)
                break

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-compositor-layer"):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if self.tabAt(event.position().toPoint()) >= 0:
            event.acceptProposedAction()

    def dropEvent(self, event):
        target = self.tabAt(event.position().toPoint())
        if target >= 0:
            try:
                payload = json.loads(bytes(event.mimeData().data("application/x-compositor-layer")))
                self.setCurrentIndex(target)
                self.owner.transfer_layers(payload["project"], payload["layers"], None)
                event.acceptProposedAction()
            except Exception as exc:
                self.owner.fail(exc)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Compositor Linux")
        self.setObjectName("compositorEditor")
        self.setMinimumSize(900, 560)
        self.resize(1280, 820)
        self.projects, self.viewports, self.selected = [], [], set()
        self.save_jobs = {}
        self.layer_clipboard = None
        self.current = -1
        self.preview_document = None
        self.filter_dialog = None
        self.content_dialog = None
        self.inline_text = None
        self.text_options = dict(
            version=1,
            text="",
            family=QApplication.font().family(),
            size=48,
            bold=False,
            italic=False,
            underline=False,
            alignment="Left",
            color=[0, 0, 0],
        )
        self.tool, self.mask_target, self.mask_alone_id = "move", False, None
        self.foreground, self.background = (0, 0, 0, 255), (255, 255, 255, 255)
        self.clone_source, self.clone_offset = None, None
        self.pixel_grid, self.transform_controls = True, True
        self.options = dict(
            diameter=40,
            hardness=1.0,
            opacity=1.0,
            brush_mode="Paint",
            heal_mode="Content-Aware",
            smear_mode="Liquify",
            blur_radius=10,
            aligned=True,
            sample_all=False,
            sample_radius=0,
            tolerance=32,
            contiguous=True,
            mask_white=True,
            marquee="Rectangle",
            lasso="Freehand",
            selection_mode="New",
            shape="Rectangle",
            corner_radius=0,
            gradient_shape="Linear",
            gradient_style="Foreground to Transparent",
            gradient_reverse=False,
            crop_ratio="Free",
            auto_select=False,
            locks_transform_ratio=True,
        )
        self.settings = QSettings("Compositor", "CompositorLinux")
        for key, default in self.options.items():
            if self.settings.contains("tools/" + key):
                self.options[key] = self.settings.value("tools/" + key, default, type=type(default))
        self.show_guides = True
        self.show_grid = False
        self.show_rulers = False
        self.snap_guides = True
        self.snap_grid = False
        self.snap_layers = True
        self.grid_spacing = max(2, min(4096, int(self.settings.value("gridSpacing", 64))))
        self.grid_subdivisions = max(1, min(64, int(self.settings.value("gridSubdivisions", 8))))
        self.updating = False
        self.document_actions = []
        self.actions = {}
        self.build_ui()
        self.build_menus()
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        self.refresh()
        self.external_timer = QTimer(self)
        self.external_timer.setInterval(2000)
        self.external_timer.timeout.connect(self.check_external_changes)
        self.external_timer.start()

    @property
    def history(self):
        return self.projects[self.current] if 0 <= self.current < len(self.projects) else None

    def run(self, function, resolve=True):
        try:
            if self.filter_dialog is not None or self.content_dialog is not None:
                return False
            if resolve:
                self.canvas.resolve_pending()
            result = function()
            self.refresh()
            return result
        except Exception as exc:
            self.fail(exc)
            return False

    def fail(self, error):
        QMessageBox.warning(self, "Compositor Linux", str(error))

    def build_ui(self):
        toolbar = QToolBar("Project")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.toolbar = toolbar
        new = QPushButton("New")
        new.setIcon(icon("new-document"))
        new.setIconSize(QSize(18, 18))
        new.setObjectName("newCanvasToolbar")
        new.clicked.connect(lambda: self.run(self.new_canvas))
        toolbar.addWidget(new)
        self.tabs = ProjectTabs(self)
        toolbar.addWidget(self.tabs)
        self.tab_overflow = QToolButton(self)
        self.tab_overflow.setText("▾")
        self.tab_overflow.setToolTip("Open project tabs")
        self.tab_overflow.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tab_overflow_menu = QMenu(self.tab_overflow)
        self.tab_overflow.setMenu(self.tab_overflow_menu)
        self.tab_overflow_menu.aboutToShow.connect(self.refresh_tab_overflow)
        toolbar.addWidget(self.tab_overflow)
        spacer = QWidget()
        spacer.setSizePolicy(
            spacer.sizePolicy().Policy.Expanding, spacer.sizePolicy().Policy.Preferred
        )
        toolbar.addWidget(spacer)
        for name, label, callback in (
            ("fit", "Fit canvas", lambda: self.canvas.fit()),
            (None, "100%", lambda: self.canvas.zoom_to(1)),
            ("zoom-in", "Zoom in", lambda: self.canvas.zoom_to(self.canvas.zoom * 1.25)),
            ("zoom-out", "Zoom out", lambda: self.canvas.zoom_to(self.canvas.zoom / 1.25)),
        ):
            button = icon_button(name, label) if name else QPushButton(label)
            button.setObjectName("view_" + (name or "actual-size"))
            button.setAccessibleName(label)
            button.setToolTip("Actual size" if name is None else label)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.option_widget = QWidget()
        self.option_layout = QHBoxLayout(self.option_widget)
        self.option_layout.setContentsMargins(12, 8, 12, 8)
        option_scroll = QScrollArea()
        option_scroll.setWidget(self.option_widget)
        option_scroll.setWidgetResizable(True)
        option_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        option_scroll.setMinimumHeight(64)
        option_scroll.setMaximumHeight(76)
        layout.addWidget(option_scroll)
        self.splitter = QSplitter()
        tools = QWidget()
        tools.setObjectName("toolRail")
        rail = QVBoxLayout(tools)
        rail.setContentsMargins(6, 8, 6, 8)
        rail.setSpacing(2)
        self.tool_buttons = {}
        definitions = [
            ("move", "Move / Transform (V)"),
            ("marquee", "Marquee (M)"),
            ("lasso", "Lasso (L)"),
            ("wand", "Magic Wand (W)"),
            ("crop", "Crop (C)"),
            ("brush", "Brush (B) / Eraser (E)"),
            ("heal", "Spot Healing (J)"),
            ("clone", "Clone Stamp (S)"),
            ("smear", "Smear (R)"),
            ("gradient", "Gradient (G)"),
            ("shape", "Shape (U)"),
            ("text", "Text (T)"),
            ("eyedropper", "Eyedropper (I)"),
            ("hand", "Hand (H)"),
            ("zoom", "Zoom (Z)"),
        ]
        for name, label in definitions:
            button = icon_button(name, label)
            button.setObjectName("tool_" + name)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.toggled.connect(
                lambda checked, n=name: self.set_tool(n) if checked and self.tool != n else None
            )
            rail.addWidget(button)
            self.tool_buttons[name] = button
        self.foreground_button, self.background_button = QPushButton(), QPushButton()
        for button, background in (
            (self.foreground_button, False),
            (self.background_button, True),
        ):
            button.setFixedSize(34, 24)
            button.setAccessibleName("Background color" if background else "Foreground color")
            button.clicked.connect(lambda _, b=background: self.choose_color(b))
            rail.addWidget(button)
        rail.addStretch()
        tools.setFixedWidth(48)
        self.canvas = Canvas(self)
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(8, 8, 8, 8)
        panel_layout.addWidget(QLabel("Layers"))
        self.blend = QComboBox()
        self.blend.addItems(BLENDS)
        self.blend.currentTextChanged.connect(lambda value: self.change_appearance("blend", value))
        panel_layout.addWidget(self.blend)
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Opacity"))
        self.opacity = number(100, 0, 100, 1)
        self.opacity.valueChanged.connect(
            lambda value: self.change_appearance("opacity", value / 100)
        )
        opacity_row.addWidget(self.opacity)
        panel_layout.addLayout(opacity_row)
        self.tree = LayerTree(self)
        panel_layout.addWidget(self.tree, 1)
        bottom = QHBoxLayout()
        bottom.setSpacing(4)
        bottom.addStretch()
        for name, label, callback in (
            ("new-layer", "New layer…", self.new_layer_dialog),
            ("mask", "Add layer mask", lambda: editing.add_mask(self.history)),
            ("folder", "New layer group", lambda: editing.new_layer(self.history, group=True)),
            ("trash", "Delete selected layers", self.delete),
        ):
            button = icon_button(name, label)
            button.setObjectName("layer_" + name)
            button.clicked.connect(lambda _, f=callback: self.run(f) if self.history else None)
            bottom.addWidget(button)
        panel_layout.addLayout(bottom)
        tool_scroll = QScrollArea()
        tool_scroll.setWidget(tools)
        tool_scroll.setWidgetResizable(True)
        tool_scroll.setFixedWidth(58)
        tool_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.splitter.addWidget(tool_scroll)
        self.splitter.addWidget(self.canvas)
        self.splitter.addWidget(panel)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([48, 930, 280])
        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self.status = QLabel("Ready")
        self.statusBar().addWidget(self.status, 1)

    def action(
        self,
        menu,
        label,
        function,
        shortcut=None,
        document=True,
        checkable=False,
        checked=False,
    ):
        action = QAction(label, self)
        if shortcut:
            action.setShortcut(QKeySequence(self.settings.value("shortcuts/" + label, shortcut)))
            action.setProperty("defaultShortcut", shortcut)
        action.setCheckable(checkable)
        action.setChecked(checked)
        action.triggered.connect(
            lambda checked=False: (
                self.run(lambda: function(checked)) if checkable else self.run(function)
            )
        )
        menu.addAction(action)
        self.actions[label] = action
        if document:
            self.document_actions.append(action)
        return action

    def set_shortcut(self, label, sequence):
        if label not in self.actions or not self.actions[label].property("defaultShortcut"):
            raise ValueError("This action has no editable shortcut.")
        shortcut = QKeySequence(sequence)
        if shortcut.isEmpty():
            raise ValueError("Choose a keyboard shortcut.")
        for name, action in self.actions.items():
            if name != label and action.shortcut() == shortcut:
                raise ValueError(f"Shortcut already assigned to {name}.")
        self.actions[label].setShortcut(shortcut)
        self.settings.setValue("shortcuts/" + label, shortcut.toString())

    def edit_shortcuts(self):
        names = sorted(
            name for name, action in self.actions.items() if action.property("defaultShortcut")
        )
        name, chosen = QInputDialog.getItem(
            self, "Keyboard Shortcuts", "Action", names, editable=False
        )
        if not chosen:
            return
        dialog = ValuesDialog("Shortcut for " + name, self)
        editor = QKeySequenceEdit(self.actions[name].shortcut())
        dialog.form.addRow("Keys", editor)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_shortcut(name, editor.keySequence().toString())

    def build_menus(self):
        file = self.menuBar().addMenu("File")
        for label, func, key in (
            ("New Canvas…", self.new_canvas, "Ctrl+N"),
            ("Open Project…", self.open_project, "Ctrl+O"),
            ("Import Images…", self.import_images, "Ctrl+Shift+O"),
            ("Save", self.save_project, "Ctrl+S"),
            ("Save As…", lambda: self.save_project(True), "Ctrl+Shift+S"),
            ("Export PNG…", lambda: self.export("PNG"), "Ctrl+Shift+E"),
            ("Export JPEG…", lambda: self.export("JPEG"), "Ctrl+Alt+Shift+S"),
            ("Close Project", lambda: self.close_project(self.current), "Ctrl+W"),
        ):
            self.action(
                file,
                label,
                func,
                key,
                document=label not in ("New Canvas…", "Open Project…", "Import Images…"),
            )
        self.recent_menu = file.addMenu("Open Recent")
        self.refresh_recent()
        file.addSeparator()
        self.action(file, "Quit", self.close, "Ctrl+Q", document=False)
        edit = self.menuBar().addMenu("Edit")
        self.action(edit, "Keyboard Shortcuts…", self.edit_shortcuts, document=False)
        for label, func, key in (
            ("Undo", lambda: self.history.undo(), "Ctrl+Z"),
            ("Redo", lambda: self.history.redo(), "Ctrl+Shift+Z"),
            ("Cut", lambda: self.copy(cut=True), "Ctrl+X"),
            ("Copy", self.copy, "Ctrl+C"),
            ("Paste", self.paste, "Ctrl+V"),
            ("Copy Merged", lambda: self.copy(merged=True), "Ctrl+Shift+C"),
            (
                "Fill Foreground",
                lambda: editing.fill_pixels(self.history, self.mask_color(False), self.mask_target),
                "Alt+Backspace",
            ),
            (
                "Fill Background",
                lambda: editing.fill_pixels(self.history, self.mask_color(True), self.mask_target),
                "Ctrl+Backspace",
            ),
            (
                "Content-Aware Fill",
                lambda: self.filter("Content-Aware Fill"),
                "Shift+Backspace",
            ),
        ):
            self.action(edit, label, func, key, document=label != "Paste")
        self.action(edit, "Copy Layers", self.copy_selected_layers, "Ctrl+Alt+Shift+C")
        self.action(
            edit, "Paste Layers", self.paste_copied_layers, "Ctrl+Alt+Shift+V", document=False
        )
        select = self.menuBar().addMenu("Select")
        for label, func, key in (
            ("All", lambda: self.selection_command("all"), "Ctrl+A"),
            ("Deselect", lambda: self.selection_command("none"), "Ctrl+D"),
            ("Inverse", lambda: self.selection_command("inverse"), "Ctrl+Shift+I"),
            ("Expand…", lambda: self.selection_command("expand"), None),
            ("Contract…", lambda: self.selection_command("contract"), None),
            ("Feather…", lambda: self.selection_command("feather"), None),
            ("Load Layer Pixels", lambda: self.load_selection(False), None),
            ("Load Layer Mask", lambda: self.load_selection(True), None),
            ("Color Range…", self.select_color_range, None),
            ("Object Selection (local model)…", self.select_object, None),
        ):
            self.action(select, label, func, key)
        image = self.menuBar().addMenu("Image")
        for kind, key in (
            ("Levels", "Ctrl+L"),
            ("Hue/Saturation", "Ctrl+U"),
            ("Curves", "Ctrl+M"),
            ("Exposure", None),
            ("Gradient Map", None),
            ("Grain", None),
            ("Black & White", None),
            ("Color Balance", None),
            ("Invert", "Ctrl+I"),
        ):
            self.action(
                image,
                kind + "…" if kind != "Invert" else kind,
                lambda k=kind: self.filter(k),
                key,
            )
        image.addSeparator()
        self.action(image, "Canvas Size…", lambda: self.size_dialog("Canvas Size"), "Ctrl+Alt+C")
        self.action(image, "Image Size…", lambda: self.size_dialog("Image Size"), "Ctrl+Alt+I")
        self.action(image, "Trim…", self.trim_document)
        self.action(
            image,
            "Flip Canvas Horizontal",
            lambda: editing.flip_canvas(self.history, True),
        )
        self.action(
            image,
            "Flip Canvas Vertical",
            lambda: editing.flip_canvas(self.history, False),
        )
        filter_menu = self.menuBar().addMenu("Filter")
        for kind in (
            "Gaussian Blur",
            "Motion Blur",
            "Add Noise",
            "Dither",
            "Vignette",
            "Bloom / Glow",
            "Tonal Contrast",
            "Camera Raw Filter",
            "Lens Correction",
            "Remove Background",
        ):
            self.action(filter_menu, kind + "…", lambda k=kind: self.filter(k))
        layer = self.menuBar().addMenu("Layer")
        adjustments = layer.addMenu("New Adjustment Layer")
        for kind in ADJUSTMENTS:
            self.action(adjustments, kind, lambda k=kind: self.new_adjustment(k))
        self.action(layer, "Edit Adjustment…", self.edit_adjustment)
        self.action(layer, "Ungroup", self.ungroup_selected, "Ctrl+Shift+G")
        effects_menu = layer.addMenu("Layer Effects")
        for kind in EffectDialog.KINDS:
            self.action(effects_menu, kind + "…", lambda k=kind: self.edit_effect(k))
        self.action(layer, "New Layer…", self.new_layer_dialog, "Ctrl+Shift+N")
        self.action(layer, "New Solid Color Layer…", lambda: self.new_layer_dialog(solid=True))
        self.action(layer, "New Text Layer…", self.text_layer_dialog)
        self.action(layer, "Edit Text on Canvas", self.start_inline_text)
        self.action(layer, "Edit Layer Content…", self.edit_layer_content)
        for label, func, key in (
            ("Transform…", self.transform_dialog, "Ctrl+T"),
            ("Duplicate / Layer via Copy", self.layer_via_copy, "Ctrl+J"),
            (
                "Create / Release Clipping Mask",
                lambda: editing.toggle_clipping(self.history),
                "Ctrl+Alt+G",
            ),
            (
                "Group Selected Layers",
                lambda: editing.group_layers(self.history, self.selected),
                "Ctrl+G",
            ),
            ("Move Out of Folder", self.out_of_folder, None),
            (
                "New Blank Layer",
                self.create_layer,
                None,
            ),
            ("Rename Layer…", self.rename_layer, "F2"),
            ("Show / Hide Layer", self.toggle_visibility, None),
            ("Move Layer Up", lambda: self.reorder(1), "Ctrl+]"),
            ("Move Layer Down", lambda: self.reorder(-1), "Ctrl+["),
            ("Merge", self.merge, "Ctrl+E"),
            ("Flip Layer Horizontal", lambda: self.flip_layer(True), None),
            ("Flip Layer Vertical", lambda: self.flip_layer(False), None),
            ("Delete Layer / Mask", self.delete, None),
        ):
            self.action(layer, label, func, key)
        mask = layer.addMenu("Layer Mask")
        for label, func in (
            ("Reveal All", lambda: editing.add_mask(self.history, True)),
            ("Hide All", lambda: editing.add_mask(self.history, False)),
            ("Enable / Disable", self.toggle_mask),
            ("Link / Unlink", self.link_mask),
            ("Apply Mask", self.apply_mask),
            ("Delete Mask", self.delete_mask),
        ):
            self.action(mask, label, func)
        view = self.menuBar().addMenu("View")
        for label, func, key in (
            ("Fit Canvas", self.canvas.fit, "Ctrl+0"),
            ("Actual Pixels", lambda: self.canvas.zoom_to(1), "Ctrl+1"),
            ("Zoom In", lambda: self.canvas.zoom_to(self.canvas.zoom * 1.25), "Ctrl+="),
            (
                "Zoom Out",
                lambda: self.canvas.zoom_to(self.canvas.zoom / 1.25),
                "Ctrl+-",
            ),
        ):
            self.action(view, label, func, key)
        self.action(
            view,
            "Pixel Grid",
            lambda checked: setattr(self, "pixel_grid", checked),
            checkable=True,
            checked=True,
        )
        for label, attr, checked in (
            ("Rulers", "show_rulers", False),
            ("Guides", "show_guides", True),
            ("Grid", "show_grid", False),
            ("Snap To Guides", "snap_guides", True),
            ("Snap To Grid", "snap_grid", False),
            ("Snap To Layers", "snap_layers", True),
        ):
            self.action(
                view,
                label,
                lambda state, name=attr: self.set_view_flag(name, state),
                checkable=True,
                checked=checked,
            )
        self.action(view, "Add Horizontal Guide…", lambda: self.add_guide("horizontal"))
        self.action(view, "Add Vertical Guide…", lambda: self.add_guide("vertical"))
        self.action(view, "Clear Guides", self.clear_guides)
        self.action(view, "Grid Settings…", self.set_grid)
        self.action(
            view,
            "Transform Controls",
            lambda checked: setattr(self, "transform_controls", checked),
            "Ctrl+H",
            checkable=True,
            checked=True,
        )
        help_menu = self.menuBar().addMenu("Help")
        self.action(
            help_menu,
            "About Compositor Linux",
            lambda: QMessageBox.information(
                self,
                "Compositor Linux",
                f"Compositor Linux {__version__}\nNative Qt/Wayland port of Robbie Tilton's Compositor.\nUpstream copyright: Wonder Assembly LLC.\nMIT licensed application; Qt libraries retain their own licenses.\nFull parity validation is in progress.",
            ),
            document=False,
        )

    def refresh(self):
        self.updating = True
        h = self.history
        for action in self.document_actions:
            action.setEnabled(h is not None)
        if h:
            valid = {item.id for item in h.document.layers}
            self.selected &= valid
            if h.document.active and not self.selected:
                self.selected = {h.document.active}
            self.actions["Undo"].setEnabled(bool(h.undo_stack))
            self.actions["Redo"].setEnabled(bool(h.redo_stack))
            self.actions["Undo"].setText("Undo " + h.undo_stack[-1][0] if h.undo_stack else "Undo")
            self.actions["Redo"].setText("Redo " + h.redo_stack[-1][0] if h.redo_stack else "Redo")
        self.tabs.blockSignals(True)
        while self.tabs.count() > len(self.projects):
            self.tabs.removeTab(self.tabs.count() - 1)
        for i, project in enumerate(self.projects):
            title = project.path.stem if project.path else "Untitled"
            if project.dirty:
                title += " •"
            if i >= self.tabs.count():
                self.tabs.addTab(title)
            else:
                self.tabs.setTabText(i, title)
        self.tabs.setCurrentIndex(self.current)
        self.tabs.blockSignals(False)
        collapsed = set()

        def remember(item):
            if not item.isExpanded():
                collapsed.add(item.data(0, Qt.ItemDataRole.UserRole))
            for i in range(item.childCount()):
                remember(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            remember(self.tree.topLevelItem(i))
        self.tree.clear()
        if h:
            items = {}
            for layer, ancestors in h.document.entries(top_first=True):
                title = ("↳ " if layer.mask_source else "") + layer.name
                item = QTreeWidgetItem(
                    [
                        title,
                        "▣" if layer.mask is not None else "",
                        "●"
                        if layer.mask is not None and layer.mask_linked
                        else "○"
                        if layer.mask is not None
                        else "",
                    ]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, layer.id)
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsUserCheckable
                    | Qt.ItemFlag.ItemIsEditable
                    | Qt.ItemFlag.ItemIsDragEnabled
                    | Qt.ItemFlag.ItemIsDropEnabled
                )
                item.setCheckState(
                    0,
                    Qt.CheckState.Checked if layer.visible else Qt.CheckState.Unchecked,
                )
                if layer.live_text is not None:
                    item.setIcon(0, icon("text"))
                    item.setToolTip(0, "Text — double-click to edit")
                elif layer.image is not None:
                    image = layer.image.copy()
                    image.thumbnail((44, 32))
                    item.setIcon(0, QIcon(QPixmap.fromImage(qimage(image))))
                elif layer.group:
                    item.setIcon(0, icon("folder"))
                if layer.mask is not None:
                    mask_image = layer.mask.copy()
                    mask_image.thumbnail((32, 24))
                    item.setIcon(1, QIcon(QPixmap.fromImage(qimage(mask_image))))
                    item.setToolTip(
                        1,
                        "Click to edit; Alt-click to view mask; Shift-click to toggle; Ctrl-click to select",
                    )
                parent = items.get(layer.parent)
                parent.addChild(item) if parent else self.tree.addTopLevelItem(item)
                item.setExpanded(layer.id not in collapsed)
                item.setSelected(layer.id in self.selected)
                items[layer.id] = item
            layer = h.document.layer()
            if layer:
                self.blend.setCurrentText(layer.blend)
                self.opacity.setValue(layer.opacity * 100)
                self.blend.setEnabled(not layer.group)
                self.opacity.setEnabled(True)
            self.mask_target = self.mask_target and layer is not None and layer.mask is not None
            if self.mask_alone_id and (
                h.document.layer(self.mask_alone_id) is None
                or h.document.layer(self.mask_alone_id).mask is None
            ):
                self.mask_alone_id = None
        self.updating = False
        self.refresh_options()
        self.canvas.invalidate()
        self.update_status()
        self.setWindowTitle(
            (h.path.stem if h and h.path else "Untitled") + " — Compositor Linux"
            if h
            else "Compositor Linux"
        )

    def refresh_options(self):
        content_action = self.actions.get("Edit Layer Content…")
        if content_action is not None:
            layer = self.history.document.layer() if self.history else None
            content_action.setEnabled(
                layer is not None and (layer.live_text is not None or layer.shape is not None)
            )
        while self.option_layout.count():
            item = self.option_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.option_layout.addWidget(
            QLabel(
                {
                    "heal": "Spot Healing",
                    "clone": "Clone Stamp",
                    "smear": "Smear",
                    "idle": "Select a tool",
                }.get(self.tool, self.tool.title())
            )
        )

        def choice(key, values, label=None):
            if label:
                self.option_layout.addWidget(QLabel(label))
            box = QComboBox()
            box.addItems(values)
            box.setCurrentText(self.options[key])
            box.currentTextChanged.connect(lambda value, k=key: self.set_option(k, value))
            self.option_layout.addWidget(box)

        def spin(key, label, lo, hi, factor=1):
            box = number(self.options[key] * factor, lo, hi, 1 if factor == 100 else 0)
            self.option_layout.addWidget(ScrubLabel(label, box))
            box.setMaximumWidth(85)
            box.valueChanged.connect(lambda value, k=key, f=factor: self.set_option(k, value / f))
            self.option_layout.addWidget(box)

        def check(key, label):
            box = QCheckBox(label)
            box.setChecked(self.options[key])
            box.toggled.connect(lambda value, k=key: self.set_option(k, value))
            self.option_layout.addWidget(box)

        if self.tool in ("brush", "heal", "clone", "smear"):
            if self.tool == "brush":
                choice("brush_mode", ["Paint", "Erase"])
            if self.tool == "heal":
                choice("heal_mode", ["Content-Aware", "Create Texture", "Proximity Match"])
            if self.tool == "smear":
                choice("smear_mode", ["Liquify", "Blur", "Smudge"])
                if self.options["smear_mode"] == "Blur":
                    spin("blur_radius", "Radius", 1, 50)
            if self.tool == "clone":
                check("aligned", "Aligned")
                check("sample_all", "Sample all layers")
            spin("diameter", "Size", 1, 2000)
            spin("hardness", "Hardness", 0, 100, 100)
            spin("opacity", "Opacity", 1, 100, 100)
            if self.mask_target:
                check("mask_white", "White reveals")
        elif self.tool in ("marquee", "lasso", "wand"):
            choice("selection_mode", ["New", "Add", "Subtract"])
            if self.tool == "marquee":
                choice("marquee", ["Rectangle", "Ellipse"])
            if self.tool == "lasso":
                choice("lasso", ["Freehand", "Polygonal"])
            if self.tool == "wand":
                spin("tolerance", "Tolerance", 0, 255)
                spin("sample_radius", "Sample radius", 0, 16)
                check("contiguous", "Contiguous")
                check("sample_all", "Sample all layers")
        elif self.tool == "gradient":
            choice("gradient_shape", ["Linear", "Radial"])
            choice(
                "gradient_style",
                ["Foreground to Transparent", "Foreground to Background"],
            )
            check("gradient_reverse", "Reverse")
            spin("opacity", "Opacity", 1, 100, 100)
        elif self.tool == "shape":
            choice("shape", ["Rectangle", "Ellipse"])
            spin("corner_radius", "Corner radius", 0, 2000)
        elif self.tool == "text":
            self.option_layout.addWidget(QLabel("Click the canvas to add or edit text"))
            for label, callback in (
                ("Add Text…", self.text_layer_dialog),
                ("Edit Selected Text…", self.edit_layer_content),
                ("Edit on Canvas", self.start_inline_text),
            ):
                button = QPushButton(label)
                button.setIcon(icon("text"))
                layer = self.history.document.layer() if self.history else None
                button.setEnabled(
                    self.history is not None
                    and (label == "Add Text…" or layer is not None and layer.live_text is not None)
                )
                button.clicked.connect(lambda _, f=callback: self.run(f))
                self.option_layout.addWidget(button)
        elif self.tool == "move" and self.history and self.history.document.layer():
            check("auto_select", "Auto select")
            check("locks_transform_ratio", "Lock ratio")
            t = editing.selection_transform(self.canvas.document, self.selected, self.mask_target)
            for label, attribute in (
                ("X", "x"),
                ("Y", "y"),
                ("W", "width"),
                ("H", "height"),
                ("°", "rotation"),
            ):
                self.option_layout.addWidget(QLabel(label))
                box = number(
                    getattr(t, attribute),
                    1 if attribute in ("width", "height") else -1_000_000,
                    300_000 if attribute in ("width", "height") else 1_000_000,
                    2,
                )
                box.setMaximumWidth(90)
                box.setEnabled(not (self.canvas.pending and self.canvas.pending.get("corners")))
                box.valueChanged.connect(
                    lambda value, a=attribute: self.run(
                        lambda: self.numeric_transform(a, value), resolve=False
                    )
                )
                self.option_layout.addWidget(box)
            layer = self.history.document.layer()
            if len(self.selected) == 1 and not layer.group and layer.image is not None:
                self.option_layout.addWidget(QLabel("Scale %"))
                scale = number(t.width / layer.image.width * 100, 0.1, 10000, 2)
                scale.setMaximumWidth(90)
                scale.setEnabled(not (self.canvas.pending and self.canvas.pending.get("corners")))
                scale.valueChanged.connect(
                    lambda value: self.run(lambda: self.scale_transform(value), resolve=False)
                )
                self.option_layout.addWidget(scale)
        elif self.tool == "crop":
            choice("crop_ratio", ["Free", "Original", "1:1", "4:3", "3:4", "16:9", "9:16"], "Ratio")
            self.option_layout.addWidget(QLabel("Alt: symmetric · Enter: apply · Escape: cancel"))
        if self.canvas.pending:
            for label, callback in (
                ("Cancel", self.cancel_pending),
                ("Apply Crop" if self.tool == "crop" else "Apply", self.canvas.apply_pending),
            ):
                button = QPushButton(label)
                button.setIcon(icon("cancel" if label == "Cancel" else "apply"))
                button.setIconSize(QSize(18, 18))
                button.clicked.connect(callback)
                self.option_layout.addWidget(button)
        self.option_layout.addStretch()
        if self.mask_target:
            self.option_layout.addWidget(QLabel("Editing mask"))
        for name, button in self.tool_buttons.items():
            button.setChecked(name == self.tool)
        self.tool_buttons["brush"].setIcon(
            icon("eraser" if self.options["brush_mode"] == "Erase" else "brush")
        )
        for button, color in (
            (self.foreground_button, self.foreground),
            (self.background_button, self.background),
        ):
            button.setStyleSheet(
                f"background:rgb({color[0]},{color[1]},{color[2]});border:1px solid #aaa"
            )

    def update_status(self, message=None):
        d = self.history.document if self.history else None
        self.status.setText(
            message
            or f"{d.width} × {d.height} px   ·   {d.resolution:g} ppi   ·   {self.canvas.zoom * 100:.1f}%   ·   {len(d.layers)} layers"
            if d
            else "Ready"
        )

    def set_tool(self, tool):
        self.canvas.resolve_pending()
        self.tool = tool
        if tool == "crop" and self.history:
            self.options["crop_ratio"] = "Free"
            self.canvas.start_crop()
        self.refresh_options()
        self.canvas.setCursor(
            Qt.CursorShape.IBeamCursor if tool == "text" else Qt.CursorShape.ArrowCursor
        )
        self.canvas.setFocus()
        self.canvas.update()

    def set_option(self, key, value):
        self.options[key] = value
        self.settings.setValue("tools/" + key, value)
        if key == "brush_mode":
            self.tool_buttons["brush"].setIcon(icon("eraser" if value == "Erase" else "brush"))
        self.canvas.refresh_pending()

    def cancel_pending(self):
        self.canvas.cancel()
        self.refresh_options()

    def choose_color(self, background=False):
        color = self.background if background else self.foreground
        chosen = QColorDialog.getColor(
            QColor(*color),
            self,
            "Background color" if background else "Foreground color",
        )
        if chosen.isValid():
            color = chosen.red(), chosen.green(), chosen.blue(), 255
            if background:
                self.background = color
            else:
                self.foreground = color
            self.refresh_options()
            self.canvas.refresh_pending()

    def add_project(self, document, path=None):
        history = History(document)
        if path:
            history.saved(path)
            self.remember_project(path)
            history.disk_digest = store.digest(path)
        self.projects.append(history)
        self.viewports.append(None)
        self.switch_project(len(self.projects) - 1)
        self.refresh()
        self.canvas.fit()
        return history

    def recent_projects(self):
        value = self.settings.value("recentProjects", [])
        return (
            [str(item) for item in value if isinstance(item, str)][:10]
            if isinstance(value, list)
            else []
        )

    def remember_project(self, path):
        item = str(Path(path).resolve())
        recent = [item] + [old for old in self.recent_projects() if old != item]
        self.settings.setValue("recentProjects", recent[:10])
        if hasattr(self, "recent_menu"):
            self.refresh_recent()

    def refresh_recent(self):
        self.recent_menu.clear()
        for path in self.recent_projects():
            item = self.recent_menu.addAction(path)
            item.setEnabled(Path(path).is_dir())
            item.triggered.connect(lambda _, p=path: self.run(lambda: self.open_paths([p])))
        if not self.recent_projects():
            self.recent_menu.addAction("No recent projects").setEnabled(False)

    def tab_moved(self, source, destination):
        if not (0 <= source < len(self.projects) and 0 <= destination < len(self.projects)):
            return
        current = self.history
        self.projects.insert(destination, self.projects.pop(source))
        self.viewports.insert(destination, self.viewports.pop(source))
        self.current = self.projects.index(current) if current else -1
        self.refresh()

    def refresh_tab_overflow(self):
        self.tab_overflow_menu.clear()
        for index, history in enumerate(self.projects):
            title = history.path.stem if history.path else f"Untitled {index + 1}"
            action = self.tab_overflow_menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(index == self.current)
            action.triggered.connect(lambda _, i=index: self.switch_project(i))

    def check_external_changes(self):
        history = self.history
        if (
            history is None
            or history.path is None
            or self.preview_document is not None
            or self.filter_dialog is not None
            or self.content_dialog is not None
            or self.canvas.pending
            or id(history) in self.save_jobs
        ):
            return
        try:
            digest = store.digest(history.path)
            if digest == getattr(history, "disk_digest", None):
                return
            replacement = store.load(history.path)
        except (OSError, ValueError):
            # A package being replaced or synchronized is retried on the next tick.
            return
        if history.dirty:
            answer = QMessageBox.question(
                self,
                "Project changed on disk",
                "Another app changed this project. Revert your unsaved changes?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                history.disk_digest = digest
                return
        history.document = replacement
        history.undo_stack.clear()
        history.redo_stack.clear()
        history.revision = new_id()
        history.saved(history.path)
        history.disk_digest = digest
        self.selected &= {layer.id for layer in replacement.layers}
        self.refresh()

    def switch_project(self, index):
        if index == self.current or not 0 <= index < len(self.projects):
            return
        if 0 <= self.current < len(self.viewports):
            self.viewports[self.current] = self.canvas.zoom, self.canvas.pan
        self.canvas.resolve_pending()
        self.preview_document = None
        self.current, self.mask_target, self.mask_alone_id = index, False, None
        self.selected = {self.history.document.active} if self.history.document.active else set()
        saved = self.viewports[index]
        if saved:
            self.canvas.zoom, self.canvas.pan = saved
        else:
            self.canvas.fit()
        self.refresh()

    def new_canvas(self):
        clipboard_image = QApplication.clipboard().image()
        width, height = (
            (clipboard_image.width(), clipboard_image.height())
            if not clipboard_image.isNull()
            else (1920, 1080)
        )
        if not 1 <= width <= 30_000 or not 1 <= height <= 30_000:
            width, height = 1920, 1080
        dialog = SizeDialog("New Canvas", width, height, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            width, height = dialog.value("Width"), dialog.value("Height")
            layer = Layer.blank(width, height)
            self.add_project(
                Document(
                    width,
                    height,
                    resolution=dialog.value("Resolution (ppi)"),
                    layers=[layer],
                    active=layer.id,
                )
            )

    def create_layer(self, name=None, color=None):
        id = editing.new_layer(self.history, name=name, color=color)
        self.selected, self.mask_target = {id}, False
        return id

    def new_layer_dialog(self, solid=False):
        dialog = NewLayerDialog(self.foreground, solid, self)
        self.content_dialog = dialog
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            name = dialog.name.text().strip() or "Layer"
            color = dialog.color.rgb() if dialog.fill.currentText() == "Solid color" else None
        finally:
            self.content_dialog = None
            dialog.deleteLater()
        if accepted:
            self.create_layer(name, color)

    def text_layer_dialog(self, position=None, layer_id=None):
        document = self.history.document
        layer = document.layer(layer_id) if layer_id else None
        if layer_id and (layer is None or layer.live_text is None):
            raise ValueError("Select an editable text layer.")
        settings = dict(layer.live_text.settings) if layer else dict(self.text_options, text="")
        if layer is None:
            settings["color"] = list(self.foreground[:3])
        dialog = TextLayerDialog(settings, self)

        def preview(settings, pixels):
            self.preview_document = None
            if pixels is not None:
                try:
                    draft = document.clone()
                    editing.put_text(draft, settings, pixels, position, layer_id)
                    draft.validate()
                    self.preview_document = draft
                except ValueError as error:
                    dialog.set_error(str(error))
            self.canvas.invalidate()

        dialog.preview_changed.connect(preview)
        self.content_dialog = dialog
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            settings, pixels = dialog.settings(), dialog.pixels
        finally:
            dialog.timer.stop()
            dialog.preview_changed.disconnect(preview)
            self.content_dialog, self.preview_document = None, None
            self.canvas.invalidate()
            dialog.deleteLater()
        if accepted:
            with self.history.edit("Edit Text" if layer_id else "New Text Layer") as d:
                result = editing.put_text(d, settings, pixels, position, layer_id)
                self.selected, self.mask_target = {result.id}, False
            self.text_options = dict(settings, text="")

    def start_inline_text(self, position=None, layer_id=None):
        if self.history is None or self.inline_text is not None:
            return
        document = self.history.document
        if layer_id is None and position is None:
            active = document.layer()
            layer_id = active.id if active and active.live_text is not None else None
        layer = document.layer(layer_id) if layer_id else None
        if layer_id and (layer is None or layer.live_text is None):
            raise ValueError("Select an editable text layer.")
        settings = dict(layer.live_text.settings) if layer else dict(self.text_options, text="")
        if layer is None:
            settings["color"] = list(self.foreground[:3])
        position = position or (
            (layer.transform.x, layer.transform.y)
            if layer
            else (document.width * 0.1, document.height * 0.1)
        )
        zoom = max(0.1, self.canvas.screen_zoom)
        editor = InlineTextEditor(settings, zoom, self.canvas)
        box = (settings.get("macText") or {}).get("boxSize")
        editor.resize(
            min(self.canvas.width(), max(310, round((box or [300])[0] * zoom))),
            min(
                self.canvas.height(),
                max(180, round(((box or [300, 140])[1] if box else 140) * zoom + 75)),
            ),
        )
        point = self.canvas.screen(position)
        editor.move(
            max(0, min(round(point.x()), self.canvas.width() - editor.width())),
            max(0, min(round(point.y()), self.canvas.height() - editor.height())),
        )
        if layer is not None:
            self.preview_document = document.clone()
            self.preview_document.layer(layer.id).visible = False
        self.inline_text = self.content_dialog = editor
        editor.finished.connect(
            lambda accepted: self.finish_inline_text(accepted, position, layer_id)
        )
        editor.show()
        editor.track_resize = True
        editor.editor.setFocus()
        self.canvas.invalidate()

    def finish_inline_text(self, accepted, position, layer_id):
        editor = self.inline_text
        if editor is None:
            return
        if accepted:
            try:
                settings = editor.settings()
                pixels = render_text(settings)
                with self.history.edit("Edit Text" if layer_id else "New Text Layer") as document:
                    layer = editing.put_text(document, settings, pixels, position, layer_id)
                    self.selected, self.mask_target = {layer.id}, False
                self.text_options = dict(settings, text="")
            except ValueError as exc:
                self.fail(exc)
                return
        self.inline_text = self.content_dialog = self.preview_document = None
        editor.hide()
        editor.deleteLater()
        self.refresh()

    def edit_layer_content(self):
        layer = self.history.document.layer()
        if layer is not None and layer.live_text is not None:
            self.text_layer_dialog(layer_id=layer.id)
        elif layer is not None and layer.shape is not None:
            color = QColor(*(round(layer.shape[key] * 255) for key in ("red", "green", "blue")))
            selected = QColorDialog.getColor(color, self, "Layer fill color")
            if selected.isValid():
                with self.history.edit("Layer Fill Color") as d:
                    target = d.layer(layer.id)
                    target.shape = dict(
                        target.shape,
                        red=selected.redF(),
                        green=selected.greenF(),
                        blue=selected.blueF(),
                    )
                    target.image = engine.shape_pixels(target.image.size, target.shape)
        else:
            raise ValueError("Select a text or shape layer to edit its content.")

    def open_project(self):
        path = QFileDialog.getExistingDirectory(
            self, "Open .comp project directory", str(Path.home())
        )
        if path:
            self.open_paths([path])

    def import_images(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import Images",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.heic *.heif *.tif *.tiff *.svg *.psd *.psb *.dng *.cr2 *.cr3 *.nef *.arw *.raf *.orf *.rw2);;All Files (*)",
        )
        if paths:
            self.open_paths(paths, True)

    def open_paths(self, paths, importing=False):
        images = []
        for path in paths:
            p = Path(path)
            if p.suffix.lower() == ".comp" and p.is_dir():
                already = next(
                    (
                        i
                        for i, h in enumerate(self.projects)
                        if h.path and h.path.resolve() == p.resolve()
                    ),
                    None,
                )
                if already is not None:
                    self.switch_project(already)
                else:
                    self.add_project(store.load(p), p)
            elif p.suffix.lower() in (".psd", ".psb"):
                from .psd import load as import_psd

                result = import_psd(p)
                if result.conversions:
                    details = "\n".join(result.conversions)
                    answer = QMessageBox.question(
                        self,
                        "Photoshop import conversions",
                        "Review conversions before opening this file:\n\n" + details,
                        QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel,
                        QMessageBox.StandardButton.Cancel,
                    )
                    if answer != QMessageBox.StandardButton.Open:
                        continue
                self.add_project(result.document)
            elif p.suffix.lower() in raw.EXTENSIONS:
                dialog = ValuesDialog("Develop Camera RAW", self)
                dialog.add_number("Exposure (stops)", 0, -5, 5, 2)
                dialog.add_number("Temperature (K)", 5000, 2000, 50000)
                dialog.add_number("Tint", 0, -100, 100)
                dialog.add_number("Boost", 1, 0, 1, 2)
                dialog.form.addRow(
                    QLabel("Develop to 8-bit sRGB pixels with the local LibRaw decoder.")
                )
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    continue
                options = dict(
                    exposure=dialog.value("Exposure (stops)"),
                    temperature=dialog.value("Temperature (K)"),
                    tint=dialog.value("Tint"),
                    boost=dialog.value("Boost"),
                )
                images.append(
                    (
                        p.stem,
                        run_task(self, "Developing camera RAW…", lambda: raw.develop(p, options)),
                    )
                )
            else:
                images.append((p.stem, store.import_image(p)))
        if images:
            if self.history is None:
                first = images[0][1]
                self.add_project(Document(first.width, first.height))
            editing.import_layers(self.history, images)
            self.selected = {self.history.document.active}
            self.refresh()
            self.canvas.fit()

    def save_project(self, as_new=False, wait=False):
        if self.history is None:
            return False
        history = self.history
        if id(history) in self.save_jobs:
            return self.wait_for_save(history) if wait else False
        path = history.path
        if as_new or path is None:
            value, _ = QFileDialog.getSaveFileName(
                self,
                "Save Project",
                str(path or Path.home() / "Untitled.comp"),
                "Compositor project (*.comp)",
            )
            if not value:
                return False
            path = Path(value)
            if path.suffix.lower() != ".comp":
                path = path.with_name(path.name + ".comp")
        snapshot, revision = history.document.frozen(), history.revision
        history.save_error = None
        result = Result()
        pool = QThreadPool(self)
        pool.setMaxThreadCount(1)

        def finished(_, error):
            self.save_jobs.pop(id(history), None)
            if error is not None:
                history.save_error = error
                self.fail(error)
            else:
                history.save_error = None
                history.path = path
                history.saved_revision = revision
                history.disk_digest = store.digest(path)
                self.remember_project(path)
            self.refresh()

        result.ready.connect(finished, Qt.ConnectionType.QueuedConnection)
        self.save_jobs[id(history)] = (pool, result)
        pool.start(Job(lambda: store.save(snapshot, path), result))
        self.update_status("Saving project…")
        return self.wait_for_save(history) if wait else True

    def wait_for_save(self, history=None):
        history = history or self.history
        if history is None:
            return True
        job = self.save_jobs.get(id(history))
        if job:
            loop = QEventLoop()
            job[1].ready.connect(loop.quit, Qt.ConnectionType.QueuedConnection)
            loop.exec()
            job[0].waitForDone()
        return getattr(history, "save_error", None) is None

    def close_project(self, index):
        if not 0 <= index < len(self.projects):
            return False
        self.switch_project(index)
        if not self.wait_for_save(self.history):
            return False
        if self.history.dirty:
            answer = QMessageBox.question(
                self,
                "Unsaved project",
                "Save changes before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if (
                answer == QMessageBox.StandardButton.Cancel
                or answer == QMessageBox.StandardButton.Save
                and not self.save_project(wait=True)
            ):
                return False
        del self.projects[index]
        del self.viewports[index]
        self.current = -1
        self.selected.clear()
        self.canvas.cancel()
        if self.projects:
            self.switch_project(min(index, len(self.projects) - 1))
        self.refresh()
        return True

    def closeEvent(self, event):
        if self.content_dialog is not None:
            self.content_dialog.reject()
            event.ignore()
            return
        if self.filter_dialog is not None:
            self.filter_dialog.reject()
            event.ignore()
            return
        try:
            while self.projects:
                if not self.close_project(len(self.projects) - 1):
                    event.ignore()
                    return
            self.settings.setValue("geometry", self.saveGeometry())
            self.external_timer.stop()
            self.canvas.timer.stop()
            event.accept()
        except Exception as exc:
            self.fail(exc)
            event.ignore()

    def export(self, format):
        encoded = None
        if format == "JPEG":
            raster = run_task(
                self, "Preparing JPEG export…", lambda: engine.render(self.history.document)
            )
            dialog = JPEGDialog(
                raster,
                self.history.document.resolution,
                int(self.settings.value("jpegQuality", 90)),
                self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            encoded = dialog.data
            self.settings.setValue("jpegQuality", dialog.value("Quality (%)"))
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export " + format,
            str(Path.home() / ("Untitled.png" if format == "PNG" else "Untitled.jpg")),
            "PNG (*.png)" if format == "PNG" else "JPEG (*.jpg *.jpeg)",
        )
        if path:
            if encoded is not None:
                store.write_export_bytes(path, encoded)
            else:
                run_task(
                    self, "Exporting PNG…", lambda: store.export_image(self.history.document, path)
                )

    def layer_selection(self):
        if self.updating or self.history is None:
            return
        self.canvas.resolve_pending()
        self.selected = {
            item.data(0, Qt.ItemDataRole.UserRole) for item in self.tree.selectedItems()
        }
        current = self.tree.currentItem()
        self.history.document.active = (
            current.data(0, Qt.ItemDataRole.UserRole)
            if current
            else next(iter(self.selected), None)
        )
        if self.mask_alone_id != self.history.document.active:
            self.mask_alone_id = None
        self.mask_target = False
        self.refresh_options()
        self.canvas.invalidate()

    def layer_changed(self, item, column):
        if self.updating or self.history is None:
            return
        id = item.data(0, Qt.ItemDataRole.UserRole)

        def change():
            with self.history.edit("Layer Properties") as d:
                layer = d.layer(id)
                layer.visible = item.checkState(0) == Qt.CheckState.Checked
                if column == 0:
                    name = item.text(0)
                    layer.name = name[2:] if name.startswith("↳ ") else name

        self.run(change)

    def layer_clicked(self, item, column):
        if self.history is None:
            return
        id = item.data(0, Qt.ItemDataRole.UserRole)
        modifiers = QApplication.keyboardModifiers()
        if column == 1 and modifiers & Qt.KeyboardModifier.AltModifier:
            self.toggle_mask_alone(id)
            return
        self.select_layer_target(id, mask=column == 1)
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            self.run(lambda: self.load_selection(self.mask_target))
        elif column == 1 and modifiers & Qt.KeyboardModifier.ShiftModifier:
            self.run(self.toggle_mask)
        elif column == 2:
            self.run(self.link_mask)

    def select_layer_target(self, id, mask=False):
        layer = self.history.document.layer(id)
        if layer is None:
            raise ValueError("Layer is unavailable.")
        self.history.document.active = id
        self.selected = {id}
        self.mask_target = bool(mask and layer.mask is not None)
        if not self.mask_target or self.mask_alone_id != id:
            self.mask_alone_id = None
        self.refresh_options()
        self.canvas.invalidate()

    def toggle_mask_alone(self, id):
        layer = self.history.document.layer(id)
        if layer is None or layer.mask is None:
            self.mask_alone_id = None
            return
        self.history.document.active = id
        self.selected = {id}
        self.mask_target = True
        self.mask_alone_id = None if self.mask_alone_id == id else id
        self.refresh_options()
        self.canvas.invalidate()

    def layer_double_clicked(self, item, column):
        if column == 0 and self.history.document.layer().adjustment:
            self.run(self.edit_adjustment)
        elif column == 0:
            layer = self.history.document.layer()
            if layer.live_text is not None or layer.shape is not None:
                self.run(self.edit_layer_content)

    def layer_context(self, point):
        menu = QMenu(self)
        for name in (
            "Edit Layer Content…",
            "Duplicate / Layer via Copy",
            "Copy Layers",
            "Paste Layers",
            "Rename Layer…",
            "Group Selected Layers",
            "Merge",
            "Create / Release Clipping Mask",
            "Delete Layer / Mask",
        ):
            menu.addAction(self.actions[name])
        menu.exec(self.tree.mapToGlobal(point))

    def change_appearance(self, key, value):
        if self.updating or not self.history or not self.history.document.layer():
            return

        def change():
            with self.history.edit("Layer Appearance") as d:
                for layer in d.layers:
                    if layer.id in self.selected and not layer.group:
                        setattr(layer, key, value)

        self.run(change)

    def numeric_transform(self, attribute, value):
        t = replace(
            editing.selection_transform(self.canvas.document, self.selected, self.mask_target)
        )
        setattr(t, attribute, value)
        if self.options["locks_transform_ratio"] and attribute in ("width", "height"):
            before = editing.selection_transform(
                self.canvas.document, self.selected, self.mask_target
            )
            other = "height" if attribute == "width" else "width"
            setattr(t, other, getattr(before, other) * value / getattr(before, attribute))
        first = self.canvas.pending is None
        self.canvas.preview_transform(t)
        if first:
            self.refresh_options()

    def scale_transform(self, percent):
        layer = self.history.document.layer()
        t = editing.selection_transform(self.canvas.document, self.selected, self.mask_target)
        width, height = layer.image.width * percent / 100, layer.image.height * percent / 100
        cx, cy = t.center
        first = self.canvas.pending is None
        self.canvas.preview_transform(
            replace(t, x=cx - width / 2, y=cy - height / 2, width=width, height=height)
        )
        if first:
            self.refresh_options()

    def transform_dialog(self):
        layer = self.history.document.layer()
        if layer is None:
            return
        selected = self.history.document.selection is not None and not self.mask_target
        if selected:
            _, initial = editing.selected_pixels(self.history.document)
        else:
            self.tool = "move"
            self.canvas.start_transform()
            self.refresh_options()
            self.canvas.setFocus()
            return
        dialog = TransformDialog(initial, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if selected:
                editing.transform_pixels(self.history, dialog.transform())
            else:
                editing.transform_layers(
                    self.history, self.selected, dialog.transform(), self.mask_target
                )

    def filter(self, kind, adjustment_id=None, create=False):
        if kind == "Remove Background":
            return self.remove_background()
        if kind in ("Invert", "Content-Aware Fill"):
            draft = self.history.document.clone()
            run_task(
                self,
                "Applying " + kind + "…",
                lambda: editing.apply_filter(draft, kind, {}, self.mask_target),
            )
            with self.history.edit(kind) as d:
                d.layers = draft.layers
            return
        d = self.history.document.clone() if create else self.history.document
        if create:
            new_layer = Layer.blank(d.width, d.height, kind)
            new_layer.adjustment = {"kind": kind}
            if kind == "Add Noise":
                import secrets

                new_layer.adjustment["noiseSeed"] = secrets.randbits(32)
            editing.inserted(d, new_layer)
            adjustment_id = new_layer.id
        layer = d.layer(adjustment_id)
        if layer is None:
            raise ValueError("Select a layer first.")
        settings = layer.adjustment if adjustment_id else {}
        if kind == "Gradient Map" and not settings.get("gradientMapSettings"):
            settings = dict(
                settings,
                gradientMapSettings={
                    name: {key: value / 255 for key, value in zip(("red", "green", "blue"), color)}
                    for name, color in (
                        ("shadows", self.foreground[:3]),
                        ("highlights", self.background[:3]),
                    )
                },
            )
        histogram = None
        if kind == "Levels":
            if adjustment_id:
                below = {entry.id for entry in d.layers[: d.layers.index(layer)]}
                histogram_image = run_task(
                    self, "Sampling adjustment input…", lambda: engine.render(d, only=below)
                )
                histogram_mask = d.selection
            else:
                _, histogram_image, t = editing.raster_target(d, self.mask_target)
                histogram_image = histogram_image.convert("RGBA")
                histogram_mask = (
                    editing.local_coverage(d, layer, histogram_image.size, t)
                    if d.selection is not None
                    else None
                )
            histogram = kernels.histogram(histogram_image, histogram_mask)
        dialog = FilterDialog(kind, settings, self, histogram, adjustment=bool(adjustment_id))
        if kind == "Levels":
            sample_image = histogram_image
            sample_matrix = (
                np.eye(3)
                if adjustment_id
                else np.linalg.inv(editing.pixel_matrix(t, sample_image.size))
            )

            def sample_original(point):
                if not 0 <= point[0] < d.width or not 0 <= point[1] < d.height:
                    return None
                x, y, _ = sample_matrix @ [*point, 1]
                if 0 <= x < sample_image.width and 0 <= y < sample_image.height:
                    return sample_image.getpixel((int(x), int(y)))
                return None

            dialog.original_sample = sample_original
        original = d.clone()

        def preview(settings):
            try:
                if not settings.pop("preview", True):
                    self.preview_document = None
                else:
                    draft = original.clone()
                    if adjustment_id:
                        draft.layer(adjustment_id).adjustment = settings
                    else:
                        target = draft.layer()
                        if target.image is not None:
                            thumbnail = target.image.copy()
                            thumbnail.thumbnail((1000, 1000))
                            target.image = thumbnail
                        editing.apply_filter(draft, kind, settings, self.mask_target)
                    self.preview_document = draft
                self.canvas.invalidate()
            except Exception as exc:
                self.update_status(str(exc))

        dialog.preview.connect(preview)
        try:
            result = (
                dialog.exec_canvas(self) if kind in ("Hue/Saturation", "Levels") else dialog.exec()
            )
            accepted = result == QDialog.DialogCode.Accepted
            if accepted:
                settings = dialog.result_settings()
                settings.pop("preview", None)
                with self.history.edit(kind) as document:
                    if create:
                        new_layer.adjustment = settings
                        editing.inserted(document, new_layer)
                        self.selected = {new_layer.id}
                    elif adjustment_id:
                        document.layer(adjustment_id).adjustment = settings
                    else:
                        draft = document.clone()
                        run_task(
                            self,
                            "Applying " + kind + "…",
                            lambda: editing.apply_filter(draft, kind, settings, self.mask_target),
                        )
                        document.layers = draft.layers
        finally:
            dialog.timer.stop()
            dialog.deleteLater()
            self.preview_document = None
            self.canvas.invalidate()

    def new_adjustment(self, kind):
        self.filter(kind, create=True)

    def set_view_flag(self, name, state):
        setattr(self, name, state)
        self.canvas.invalidate()

    def add_guide(self, axis):
        if self.history is None:
            return
        document = self.history.document
        extent = document.height if axis == "horizontal" else document.width
        position, accepted = QInputDialog.getDouble(
            self, "Add Guide", "Position (px)", extent / 2, -1_000_000, 1_000_000, 2
        )
        if accepted:
            with self.history.edit("Add Guide") as draft:
                draft.extras.setdefault("guides", []).append(
                    dict(id=new_id(), axis=axis, position=position)
                )
            self.canvas.invalidate()

    def clear_guides(self):
        if self.history and self.history.document.extras.get("guides"):
            with self.history.edit("Clear Guides") as draft:
                draft.extras.pop("guides", None)
            self.canvas.invalidate()

    def set_grid(self):
        spacing, accepted = QInputDialog.getInt(
            self, "Grid Settings", "Spacing (px)", self.grid_spacing, 2, 4096
        )
        if not accepted:
            return
        subdivisions, accepted = QInputDialog.getInt(
            self, "Grid Settings", "Subdivisions", self.grid_subdivisions, 1, min(64, spacing)
        )
        if accepted:
            self.grid_spacing, self.grid_subdivisions = spacing, subdivisions
            self.settings.setValue("gridSpacing", spacing)
            self.settings.setValue("gridSubdivisions", subdivisions)
            self.canvas.invalidate()

    def edit_effect(self, kind):
        layer = self.history.document.layer()
        if layer is None or layer.group or layer.image is None:
            raise ValueError("Select a pixel layer to edit its effects.")
        key = EffectDialog.KINDS[kind]
        existing = layer.extras.get("effects", {})
        dialog = EffectDialog(kind, existing.get(key), self)
        original = self.history.document.clone()

        def preview(value):
            draft = original.clone()
            draft.layer(layer.id).extras.setdefault("effects", {})[key] = value
            self.preview_document = draft
            self.canvas.invalidate()

        dialog.preview.connect(preview)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                with self.history.edit("Layer Effect") as document:
                    document.layer(layer.id).extras.setdefault("effects", {})[key] = (
                        dialog.result_settings()
                    )
        finally:
            dialog.timer.stop()
            dialog.deleteLater()
            self.preview_document = None
            self.canvas.invalidate()

    def ungroup_selected(self):
        editing.ungroup(self.history, self.selected)
        self.selected = {self.history.document.active} if self.history.document.active else set()
        self.refresh()

    def edit_adjustment(self):
        layer = self.history.document.layer()
        if layer and layer.adjustment:
            self.filter(layer.adjustment["kind"], layer.id)

    def size_dialog(self, title):
        d = self.history.document
        dialog = SizeDialog(title, d.width, d.height, d.resolution, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        width, height = dialog.value("Width"), dialog.value("Height")
        if title == "Canvas Size":
            anchor = {
                "Center": (0.5, 0.5),
                "Top left": (0, 0),
                "Top": (0.5, 0),
                "Top right": (1, 0),
                "Left": (0, 0.5),
                "Right": (1, 0.5),
                "Bottom left": (0, 1),
                "Bottom": (0.5, 1),
                "Bottom right": (1, 1),
            }[dialog.value("Anchor")]
            editing.resize_canvas(self.history, width, height, anchor)
        else:
            editing.resize_image(
                self.history,
                width,
                height,
                dialog.value("Resolution (ppi)"),
                dialog.value("Resample"),
            )
        self.canvas.fit()

    def trim_document(self):
        dialog = TrimDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        based_on, sides, tolerance = dialog.options()
        snapshot = self.history.document.frozen()
        box = run_task(
            self,
            "Finding trim bounds…",
            lambda cancel: editing.trim_bounds(
                snapshot, based_on, sides, tolerance, cancelled=cancel.is_set
            ),
            cancellable=True,
        )
        if box is None:
            return
        editing.trim(self.history, based_on, sides, tolerance, box=box)
        self.canvas.fit()

    def mask_color(self, background):
        if self.mask_target:
            white = self.options["mask_white"] ^ background
            return (255, 255, 255, 255) if white else (0, 0, 0, 255)
        return self.background if background else self.foreground

    def toggle_visibility(self):
        with self.history.edit("Visibility") as d:
            layer = d.layer()
            if layer:
                layer.visible = not layer.visible

    def rename_layer(self):
        layer = self.history.document.layer()
        if layer:
            name, ok = QInputDialog.getText(self, "Rename Layer", "Name", text=layer.name)
            if ok and name.strip():
                with self.history.edit("Rename Layer") as d:
                    d.layer().name = name

    def delete(self):
        if self.mask_target:
            self.delete_mask()
        elif self.history.document.selection is not None:
            editing.fill_pixels(self.history, (0, 0, 0, 0), False, True)
        else:
            editing.delete_layers(self.history, self.selected)

    def delete_mask(self):
        with self.history.edit("Delete Mask") as d:
            layer = d.layer()
            if layer:
                layer.mask, layer.mask_transform = None, None
        self.mask_target = False

    def toggle_mask(self):
        with self.history.edit("Mask Visibility") as d:
            layer = d.layer()
            if layer and layer.mask is not None:
                layer.mask_enabled = not layer.mask_enabled

    def link_mask(self):
        with self.history.edit("Link Mask") as d:
            layer = d.layer()
            if layer and layer.mask is not None:
                layer.mask_linked = not layer.mask_linked
                if not layer.mask_linked and layer.mask_transform is None:
                    layer.mask_transform = replace(layer.transform)

    def apply_mask(self):
        editing.apply_mask(self.history)
        self.mask_target = False

    def merge(self):
        selected = self.selected.copy()
        if len(selected) == 1:
            layer = self.history.document.layer()
            if layer and not layer.group:
                siblings = [
                    item for item in self.history.document.layers if item.parent == layer.parent
                ]
                index = siblings.index(layer)
                if index > 0:
                    selected.add(siblings[index - 1].id)
        editing.merge(self.history, selected)
        self.selected = {self.history.document.active}

    def reorder(self, delta):
        d = self.history.document
        layer = d.layer()
        if layer:
            siblings = [item for item in d.layers if item.parent == layer.parent]
            index = siblings.index(layer)
            target = index + delta
            if 0 <= target < len(siblings):
                before = (
                    siblings[target].id
                    if delta < 0
                    else siblings[target + 1].id
                    if target + 1 < len(siblings)
                    else None
                )
                editing.move_layer(self.history, layer.id, layer.parent, before)

    def out_of_folder(self):
        layer = self.history.document.layer()
        if layer and layer.parent:
            editing.move_layer(
                self.history, layer.id, self.history.document.layer(layer.parent).parent
            )

    def flip_layer(self, horizontal):
        with self.history.edit("Flip Layer") as d:
            for layer in d.layers:
                if layer.id in d.descendants(self.selected):
                    transform = replace(layer.transform)
                    if horizontal:
                        transform.flip_x = not transform.flip_x
                    else:
                        transform.flip_y = not transform.flip_y
                    editing.set_transform(layer, transform)

    def selection_command(self, command):
        amount = None
        if command in ("expand", "contract", "feather"):
            amount, ok = QInputDialog.getInt(
                self, command.title() + " Selection", "Pixels", 1, 1, 250
            )
            if not ok:
                return
        with self.history.edit("Selection") as d:
            if command == "all":
                d.selection = Image.new("L", (d.width, d.height), 255)
            elif command == "none":
                d.selection = None
            elif command == "inverse":
                d.selection = ImageOps.invert(d.selection or Image.new("L", (d.width, d.height)))
            elif d.selection is not None:
                if command == "feather":
                    d.selection = d.selection.filter(ImageFilter.GaussianBlur(amount))
                else:
                    from scipy.ndimage import distance_transform_edt

                    mask = np.asarray(d.selection) > 0
                    result = (
                        distance_transform_edt(~mask) <= amount
                        if command == "expand"
                        else distance_transform_edt(mask) > amount
                    )
                    d.selection = Image.fromarray(result.astype(np.uint8) * 255)

    def select_color_range(self):
        from . import kernels

        source = run_task(
            self, "Sampling composite colors…", lambda: engine.render(self.history.document)
        )
        point = self.canvas.cursor_point or (source.width // 2, source.height // 2)
        x, y = (
            max(0, min(source.width - 1, int(point[0]))),
            max(0, min(source.height - 1, int(point[1]))),
        )
        dialog = ColorRangeDialog(source.getpixel((x, y))[:3], self)
        original = self.history.document.clone()

        def preview(values):
            try:
                draft = original.clone()
                draft.selection = kernels.color_range(source, **values)
                self.preview_document = draft
                self.canvas.invalidate()
            except Exception as exc:
                self.update_status(str(exc))

        dialog.preview.connect(preview)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                mask = run_task(
                    self,
                    "Selecting colors…",
                    lambda: kernels.color_range(source, **dialog.result_settings()),
                )
                with self.history.edit("Color Range") as document:
                    document.selection = mask
        finally:
            dialog.timer.stop()
            dialog.deleteLater()
            self.preview_document = None
            self.canvas.invalidate()

    def select_object(self):
        path = models.default_path()
        if not models.verified(path):
            chosen, _ = QFileDialog.getOpenFileName(
                self, "Choose a local U2NET object model", str(path.parent), "ONNX model (*.onnx)"
            )
            if not chosen:
                return
            path = Path(chosen)
        source = run_task(
            self, "Rendering object source…", lambda: engine.render(self.history.document)
        )
        try:
            mask = run_task(
                self, "Selecting object locally…", lambda: engine.subject_mask(source, path)
            )
        except ImportError as exc:
            raise ValueError("Install the background extra for local object selection.") from exc
        with self.history.edit("Object Selection") as document:
            document.selection = mask.convert("L")

    def load_selection(self, mask):
        d = self.history.document
        layer = d.layer()
        if layer is None:
            return
        if mask and layer.mask is not None:
            selection = engine.place(
                layer.mask, layer.mask_transform or layer.transform, (d.width, d.height)
            )
        elif layer.image is not None:
            selection = engine.place(
                layer.image.getchannel("A"), layer.transform, (d.width, d.height)
            )
        else:
            return
        self.canvas.set_selection(selection, QApplication.keyboardModifiers())

    def nudge(self, dx, dy, pixels=False):
        def apply():
            if pixels and self.history.document.selection is not None:
                editing.move_pixels(self.history, dx, dy)
                return
            with self.history.edit("Nudge") as d:
                if d.selection is not None and self.tool in (
                    "marquee",
                    "lasso",
                    "wand",
                ):
                    d.selection = d.selection.transform(
                        d.selection.size,
                        Image.Transform.AFFINE,
                        (1, 0, -dx, 0, 1, -dy),
                        Image.Resampling.NEAREST,
                    )
                else:
                    for layer in d.layers:
                        if layer.id in d.descendants(self.selected):
                            transform = (
                                (layer.mask_transform or layer.transform)
                                if self.mask_target and not layer.mask_linked
                                else layer.transform
                            )
                            moved = replace(transform, x=transform.x + dx, y=transform.y + dy)
                            if self.mask_target and not layer.mask_linked:
                                layer.mask_transform = moved
                            else:
                                editing.set_transform(layer, moved)

        self.run(apply)

    def copy(self, merged=False, cut=False):
        d = self.history.document
        image = engine.render(d, only=None if merged else {d.active})
        if d.selection is not None:
            image.putalpha(ImageChops.multiply(image.getchannel("A"), d.selection))
            box = d.selection.getbbox()
            if box:
                image = image.crop(box)
        QApplication.clipboard().setImage(qimage(image))
        if cut:
            editing.fill_pixels(self.history, (0, 0, 0, 0), self.mask_target, True)

    def copy_selected_layers(self):
        self.layer_clipboard = editing.copy_layers(
            self.history.document, self.selected or {self.history.document.active}
        )

    def paste_copied_layers(self):
        if self.history is None or self.layer_clipboard is None:
            raise ValueError("Copy layers before pasting them.")
        self.selected = editing.paste_layers(self.history, self.layer_clipboard)
        self.mask_target, self.mask_alone_id = False, None
        self.refresh()

    def paste(self):
        image = QApplication.clipboard().image()
        if image.isNull():
            mime = QApplication.clipboard().mimeData()
            if mime.hasUrls():
                return self.open_paths(
                    [u.toLocalFile() for u in mime.urls() if u.isLocalFile()], True
                )
            raise ValueError("The clipboard does not contain an image.")
        self.import_qimage(image)

    def import_qimage(self, image):
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        data = (
            np.frombuffer(image.constBits(), dtype=np.uint8, count=image.sizeInBytes())
            .reshape(image.height(), image.bytesPerLine())[:, : image.width() * 4]
            .reshape(image.height(), image.width(), 4)
            .copy()
        )
        imported = Image.fromarray(data)
        if self.history is None:
            self.add_project(Document(imported.width, imported.height))
        editing.import_layers(self.history, [("Pasted Image", imported)])
        self.selected = {self.history.document.active}

    def layer_via_copy(self):
        if self.history.document.selection is None:
            self.selected = editing.duplicate(self.history, self.selected)
        else:
            with self.history.edit("Layer via Copy") as d:
                image, transform = editing.selected_pixels(d)
                layer = editing.inserted(d, Layer("Layer via Copy", transform, image=image))
                self.selected = {layer.id}

    def transfer_layers(self, source_id, ids, parent):
        source = next((h.document for h in self.projects if h.document.id == source_id), None)
        if source is None or source.id == self.history.document.id:
            return
        included = source.descendants(ids)
        remap = {id: new_id() for id in included}
        with self.history.edit("Copy Layers Between Projects") as d:
            for layer in source.layers:
                if layer.id in included:
                    copy = layer.clone()
                    copy.id = remap[layer.id]
                    copy.parent = remap.get(layer.parent, parent)
                    copy.mask_source = remap.get(layer.mask_source)
                    d.layers.append(copy)
            d.active = remap.get(source.active, next(iter(remap), None))
        self.selected = set(remap.values())
        self.refresh()

    def remove_background(self):
        path = models.default_path()
        if not models.verified(path):
            chosen, _ = QFileDialog.getOpenFileName(
                self,
                "Choose a local U2NET model (compositor-install-model installs the default)",
                str(path.parent),
                "ONNX model (*.onnx)",
            )
            if not chosen:
                return
            path = Path(chosen)
        dialog = ValuesDialog("Remove Background", self)
        dialog.add_choice("Quality", ["Basic", "Advanced"])
        dialog.add_number("Refine edges", 12, 0, 40, 1)
        dialog.add_number("Matte contrast", 25, 0, 100, 1)
        dialog.add_number("Shift edge", 0, -10, 10, 1)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        quality = dialog.value("Quality")
        radius, contrast, shift = (
            dialog.value(key) for key in ("Refine edges", "Matte contrast", "Shift edge")
        )
        with self.history.edit("Remove Background") as d:
            layer, image, t = editing.raster_target(d, False)

            def process_subject():
                mask = engine.subject_mask(image, path)
                if quality == "Advanced":
                    mask = engine.guided_matte(mask, image, radius, contrast, shift)
                result = image.copy()
                result.putalpha(ImageChops.multiply(image.getchannel("A"), mask))
                return Image.composite(
                    result, image, editing.local_coverage(d, layer, image.size, t)
                )

            try:
                result = run_task(
                    self, "Finding and refining the subject locally…", process_subject
                )
            except ImportError as exc:
                raise ValueError(
                    "Install the background extra from this checkout, or Arch's python-onnxruntime-cpu package."
                ) from exc
            layer.image, layer.shape = result, None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compositor Linux image editor")
    parser.add_argument("paths", nargs="*", help=".comp project directories or images")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--smoke-test", metavar="OUTPUT_DIRECTORY", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    application = QApplication(sys.argv[:1])
    application.setApplicationName("Compositor Linux")
    application.setDesktopFileName("compositor")
    application.setWindowIcon(QIcon(str(Path(__file__).parent / "assets/compositor.png")))
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    if args.paths:
        window.run(lambda: window.open_paths(args.paths))
        QTimer.singleShot(150, window.canvas.fit)
    if args.smoke_test:
        from .smoke import exercise

        QTimer.singleShot(500, lambda: exercise(application, window, Path(args.smoke_test)))
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
