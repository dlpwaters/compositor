"""Compositor's native Linux application and desktop integration entry point."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageFilter, ImageOps
from PySide6.QtCore import QByteArray, QMimeData, QSettings, QSize, Qt, QTimer
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

from . import __version__, editing, engine, kernels, models, store
from .canvas import Canvas, qimage
from .dialogs import FilterDialog, JPEGDialog, SizeDialog, TransformDialog, ValuesDialog, number
from .icons import icon
from .model import ADJUSTMENTS, BLENDS, Document, History, Layer, new_id
from .tasks import run_task

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
        self.setMovable(False)
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
        self.setWindowTitle("Compositor")
        self.setObjectName("compositorEditor")
        self.setMinimumSize(900, 560)
        self.resize(1280, 820)
        self.projects, self.viewports, self.selected = [], [], set()
        self.current = -1
        self.preview_document = None
        self.filter_dialog = None
        self.tool, self.mask_target = "move", False
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
        self.updating = False
        self.document_actions = []
        self.actions = {}
        self.build_ui()
        self.build_menus()
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        self.refresh()

    @property
    def history(self):
        return self.projects[self.current] if 0 <= self.current < len(self.projects) else None

    def run(self, function, resolve=True):
        try:
            if self.filter_dialog is not None:
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
        QMessageBox.warning(self, "Compositor", str(error))

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
            ("new-layer", "New layer", lambda: editing.new_layer(self.history)),
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
            action.setShortcut(QKeySequence(shortcut))
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
        file.addSeparator()
        self.action(file, "Quit", self.close, "Ctrl+Q", document=False)
        edit = self.menuBar().addMenu("Edit")
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
            "Lens Correction",
            "Remove Background",
        ):
            self.action(filter_menu, kind + "…", lambda k=kind: self.filter(k))
        layer = self.menuBar().addMenu("Layer")
        adjustments = layer.addMenu("New Adjustment Layer")
        for kind in ADJUSTMENTS:
            self.action(adjustments, kind, lambda k=kind: self.new_adjustment(k))
        self.action(layer, "Edit Adjustment…", self.edit_adjustment)
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
                lambda: editing.new_layer(self.history),
                "Ctrl+Shift+N",
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
            "About Compositor",
            lambda: QMessageBox.information(
                self,
                "Compositor",
                f"Compositor for Linux {__version__}\nNative Qt/Wayland port of Robbie Tilton's Compositor.\nMIT licensed application; Qt libraries retain their own licenses.\nFull parity validation is in progress.",
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
                if layer.image is not None:
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
                        "Click to edit mask; Shift-click to enable or disable; Ctrl-click to load selection",
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
                self.opacity.setEnabled(not layer.group)
            self.mask_target = self.mask_target and layer is not None and layer.mask is not None
        self.updating = False
        self.refresh_options()
        self.canvas.invalidate()
        self.update_status()
        self.setWindowTitle(
            (h.path.stem if h and h.path else "Untitled") + " — Compositor" if h else "Compositor"
        )

    def refresh_options(self):
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
            self.option_layout.addWidget(QLabel(label))
            box = number(self.options[key] * factor, lo, hi, 1 if factor == 100 else 0)
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
            choice("crop_ratio", ["Free", "Original", "1:1", "4:3", "16:9"], "Ratio")
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
        self.canvas.setFocus()
        self.canvas.update()

    def set_option(self, key, value):
        self.options[key] = value
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
        self.projects.append(history)
        self.viewports.append(None)
        self.switch_project(len(self.projects) - 1)
        self.refresh()
        self.canvas.fit()
        return history

    def switch_project(self, index):
        if index == self.current or not 0 <= index < len(self.projects):
            return
        if 0 <= self.current < len(self.viewports):
            self.viewports[self.current] = self.canvas.zoom, self.canvas.pan
        self.canvas.resolve_pending()
        self.preview_document = None
        self.current, self.mask_target = index, False
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
            "Images (*.png *.jpg *.jpeg *.heic *.heif *.tif *.tiff);;All Files (*)",
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

    def save_project(self, as_new=False):
        if self.history is None:
            return False
        path = self.history.path
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
        store.save(self.history.document, path)
        self.history.saved(path)
        self.refresh()
        return True

    def close_project(self, index):
        if not 0 <= index < len(self.projects):
            return False
        self.switch_project(index)
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
                and not self.save_project()
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
        self.history.document.active = item.data(0, Qt.ItemDataRole.UserRole)
        self.mask_target = column == 1 and self.history.document.layer().mask is not None
        modifiers = QApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            self.run(lambda: self.load_selection(self.mask_target))
        elif column == 1 and modifiers & Qt.KeyboardModifier.ShiftModifier:
            self.run(self.toggle_mask)
        elif column == 2:
            self.run(self.link_mask)
        self.refresh_options()
        self.canvas.invalidate()

    def layer_double_clicked(self, item, column):
        if column == 0 and self.history.document.layer().adjustment:
            self.run(self.edit_adjustment)

    def layer_context(self, point):
        menu = QMenu(self)
        for name in (
            "Duplicate / Layer via Copy",
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
        dialog = FilterDialog(kind, settings, self, histogram)
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
    parser = argparse.ArgumentParser(description="Compositor image editor for Linux")
    parser.add_argument("paths", nargs="*", help=".comp project directories or images")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--smoke-test", metavar="OUTPUT_DIRECTORY", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    application = QApplication(sys.argv[:1])
    application.setApplicationName("Compositor")
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
