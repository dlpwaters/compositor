"""Native canvas event path: tools edit a draft, then commit one undo step."""

import math
from dataclasses import replace

from PIL import Image, ImageChops, ImageDraw, ImageFilter
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from . import editing, engine, kernels
from .model import History, Layer, Transform, dimensions
from .warp import WarpStroke


def qimage(image):
    converted = image.convert("RGBA")
    return QImage(
        converted.tobytes(),
        converted.width,
        converted.height,
        converted.width * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()


class Canvas(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setAccessibleName("Image canvas")
        self.setObjectName("editorCanvas")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.zoom, self.pan = 1, QPointF()
        self.cached, self.selection_image = None, None
        self.draft, self.drag, self.stroke, self.points = None, None, None, []
        self.pending = None
        self.cursor_point, self.space, self.last_stroke_point = None, False, None
        self.guides = []
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.update)
        self.timer.start()

    @property
    def document(self):
        return (
            self.owner.preview_document
            or self.draft
            or (self.pending["document"] if self.pending else None)
            or (self.owner.history.document if self.owner.history else None)
        )

    @property
    def screen_zoom(self):
        return self.zoom / self.devicePixelRatioF()

    def origin(self):
        d = self.document
        if d is None:
            return QPointF()
        return (
            QPointF(
                (self.width() - d.width * self.screen_zoom) / 2,
                (self.height() - d.height * self.screen_zoom) / 2,
            )
            + self.pan
        )

    def document_point(self, event):
        p = (event.position() - self.origin()) / self.screen_zoom
        return p.x(), p.y()

    def screen(self, p):
        return self.origin() + QPointF(*p) * self.screen_zoom

    def fit(self):
        d = self.document
        if d:
            self.zoom = (
                min(self.width() / d.width, self.height() / d.height)
                * 0.9
                * self.devicePixelRatioF()
            )
            self.pan = QPointF()
        self.invalidate()

    def zoom_to(self, value, anchor=None):
        before = self.origin()
        point = anchor or QPointF(self.width() / 2, self.height() / 2)
        document_point = (point - before) / self.screen_zoom
        self.zoom = min(64, max(0.01, value))
        after = self.origin()
        self.pan += point - (after + document_point * self.screen_zoom)
        self.invalidate()
        self.owner.update_status()

    def invalidate(self):
        self.cached, self.selection_image = None, None
        self.update()

    def resizeEvent(self, event):
        self.invalidate()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#202226"))
        d = self.document
        if d is None:
            painter.setPen(QColor("#d5d7dc"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Compositor\n\nCreate a canvas (Ctrl+N) or open a project (Ctrl+O).\nDrop an image here to begin.",
            )
            return
        origin = self.origin()
        canvas = QRectF(
            origin.x(),
            origin.y(),
            d.width * self.screen_zoom,
            d.height * self.screen_zoom,
        )
        painter.save()
        painter.setClipRect(canvas)
        left, top = max(0, round(canvas.left())), max(0, round(canvas.top()))
        right, bottom = (
            min(self.width(), math.ceil(canvas.right())),
            min(self.height(), math.ceil(canvas.bottom())),
        )
        painter.fillRect(canvas, QColor("#a4a4a4"))
        for y in range(top // 12 * 12, bottom, 12):
            for x in range(left // 12 * 12, right, 12):
                if (x // 12 + y // 12) % 2:
                    painter.fillRect(x, y, 12, 12, QColor("#777777"))
        try:
            if self.cached is None:
                scale = self.screen_zoom
                region = (
                    -origin.x() / scale,
                    -origin.y() / scale,
                    self.width() / scale,
                    self.height() / scale,
                )
                image = engine.render(d, region=region, scale=scale)
                self.cached = qimage(image)
                if d.selection is not None:
                    selection = engine.place(
                        d.selection,
                        Transform(width=d.width, height=d.height),
                        image.size,
                        region[:2],
                        scale,
                    )
                    outline = selection.filter(ImageFilter.FIND_EDGES)
                    ants = Image.new("RGBA", image.size, (255, 255, 255, 255))
                    ants.putalpha(outline)
                    self.selection_image = qimage(ants)
            painter.drawImage(QPointF(), self.cached)
        except Exception as exc:
            painter.setPen(QColor("#ffa0a0"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(exc))
        if self.selection_image:
            painter.drawImage(QPointF(), self.selection_image)
        if self.owner.pixel_grid and self.zoom >= 8:
            painter.setPen(QPen(QColor(0, 0, 0, 70), 1))
            for x in range(
                max(0, int(-origin.x() / self.screen_zoom)),
                min(d.width, int((self.width() - origin.x()) / self.screen_zoom) + 1) + 1,
            ):
                sx = round(origin.x() + x * self.screen_zoom)
                painter.drawLine(sx, top, sx, bottom)
            for y in range(
                max(0, int(-origin.y() / self.screen_zoom)),
                min(d.height, int((self.height() - origin.y()) / self.screen_zoom) + 1) + 1,
            ):
                sy = round(origin.y() + y * self.screen_zoom)
                painter.drawLine(left, sy, right, sy)
        painter.restore()
        painter.setPen(QPen(QColor("#151619"), 1))
        painter.drawRect(canvas)
        painter.setPen(QPen(QColor("#df84e8"), 1))
        for axis, coordinate in self.guides:
            if axis == "x":
                painter.drawLine(self.screen((coordinate, 0)), self.screen((coordinate, d.height)))
            else:
                painter.drawLine(self.screen((0, coordinate)), self.screen((d.width, coordinate)))
        layer = d.layer()
        if layer and self.owner.tool == "move" and self.owner.transform_controls:
            t = editing.selection_transform(d, self.owner.selected, self.owner.mask_target)
            handles = self.transform_handles(t)
            points = [self.screen(handles[i]) for i in (0, 2, 4, 6)]
            painter.setPen(QPen(QColor("#79b6ff"), 1))
            painter.drawPolygon(QPolygonF(points))
            painter.setBrush(QColor("#e9f3ff"))
            for point in map(self.screen, handles):
                painter.drawRect(QRectF(point.x() - 3, point.y() - 3, 6, 6))
            if not self.pending or not self.pending.get("corners"):
                rotation_handle = self.screen(t.point(0.5, 0)) - QPointF(0, 22)
                painter.drawEllipse(rotation_handle, 4, 4)
        if self.owner.tool == "crop" and self.pending:
            t = self.pending["rect"]
            rect = QRectF(self.screen((t.x, t.y)), self.screen((t.x + t.width, t.y + t.height)))
            shade = QPainterPath()
            shade.addRect(canvas)
            hole = QPainterPath()
            hole.addRect(rect)
            painter.fillPath(shade.subtracted(hole), QColor(0, 0, 0, 130))
            painter.setPen(QPen(QColor("#e6eefc"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            for fraction in (1 / 3, 2 / 3):
                painter.drawLine(
                    QPointF(rect.left() + rect.width() * fraction, rect.top()),
                    QPointF(rect.left() + rect.width() * fraction, rect.bottom()),
                )
                painter.drawLine(
                    QPointF(rect.left(), rect.top() + rect.height() * fraction),
                    QPointF(rect.right(), rect.top() + rect.height() * fraction),
                )
            painter.setBrush(QColor("#e9f3ff"))
            for u, v in editing.HANDLES:
                point = self.screen(t.point(u, v))
                painter.drawRect(QRectF(point.x() - 3, point.y() - 3, 6, 6))
        if self.pending and self.pending["kind"] == "gradient":
            painter.setPen(QPen(QColor("#e6eefc"), 1))
            a, b = self.screen(self.pending["start"]), self.screen(self.pending["end"])
            painter.drawLine(a, b)
            painter.drawEllipse(a, 4, 4)
            painter.drawEllipse(b, 4, 4)
        if self.drag and self.owner.tool in ("marquee", "crop", "shape", "gradient"):
            painter.setPen(QPen(QColor("#e6eefc"), 1, Qt.PenStyle.DashLine))
            a, b = (
                self.screen(self.drag["start"]),
                self.screen(self.drag.get("end", self.drag["start"])),
            )
            box = QRectF(a, b).normalized()
            if self.owner.tool == "gradient":
                painter.drawLine(a, b)
                painter.drawEllipse(a, 4, 4)
                painter.drawEllipse(b, 4, 4)
            elif (
                self.owner.tool == "shape"
                and self.owner.options["shape"] == "Ellipse"
                or self.owner.tool == "marquee"
                and self.owner.options["marquee"] == "Ellipse"
            ):
                painter.drawEllipse(box)
            else:
                painter.drawRect(box)
        if self.points:
            path = QPainterPath(self.screen(self.points[0]))
            for p in self.points[1:]:
                path.lineTo(self.screen(p))
            if self.cursor_point:
                path.lineTo(self.screen(self.cursor_point))
            painter.setPen(QPen(QColor("#f1f4f8"), 1, Qt.PenStyle.DashLine))
            painter.drawPath(path)
        if self.cursor_point and self.owner.tool in ("brush", "heal", "clone", "smear"):
            radius = self.owner.options["diameter"] * self.screen_zoom / 2
            painter.setPen(QPen(QColor("black"), 2))
            painter.drawEllipse(self.screen(self.cursor_point), radius, radius)
            painter.setPen(QPen(QColor("white"), 1))
            painter.drawEllipse(self.screen(self.cursor_point), radius, radius)

    def mousePressEvent(self, event):
        self.setFocus()
        if self.document is None:
            return
        try:
            self.begin(event)
        except Exception as exc:
            self.cancel()
            self.owner.fail(exc)

    def begin(self, event):
        point = self.document_point(event)
        o = self.owner
        modifiers = event.modifiers()
        if (
            event.button() == Qt.MouseButton.MiddleButton
            or self.space
            or o.tool == "hand"
            and o.filter_dialog is None
        ):
            self.drag = dict(
                start=point, screen=event.position(), pan=QPointF(self.pan), kind="pan"
            )
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if o.content_dialog is not None:
            return
        if o.filter_dialog is not None:
            if o.filter_dialog.kind == "Levels":
                o.filter_dialog.sample_levels(o.filter_dialog.original_sample(point))
                self.drag = dict(start=point, kind="sample")
                return
            image = engine.render(
                self.document, region=(math.floor(point[0]), math.floor(point[1]), 1, 1)
            )
            o.filter_dialog.begin_hue_canvas(image.getpixel((0, 0)), event.position().x())
            self.drag = dict(start=point, kind="hue")
            return
        if o.tool == "zoom":
            self.zoom_to(
                self.zoom / (1.5 if modifiers & Qt.KeyboardModifier.AltModifier else 1 / 1.5),
                event.position(),
            )
            return
        if o.tool == "text":
            target = None
            for layer, ancestors in self.document.entries(top_first=True):
                if layer.group or layer.image is None:
                    continue
                u, v = layer.transform.local(*point, (1, 1))
                if (
                    layer.visible
                    and all(parent.visible for parent in ancestors)
                    and 0 <= u <= 1
                    and 0 <= v <= 1
                ):
                    if layer.live_text is not None:
                        target = layer.id
                    break
            position = (
                max(0, min(point[0], self.document.width - 1)),
                max(0, min(point[1], self.document.height - 1)),
            )
            o.run(lambda: o.text_layer_dialog(position, target))
            return
        if (
            o.tool == "eyedropper"
            or modifiers & Qt.KeyboardModifier.AltModifier
            and o.tool == "brush"
        ):
            image = engine.render(
                self.document, region=(math.floor(point[0]), math.floor(point[1]), 1, 1)
            )
            color = image.getpixel((0, 0))
            o.foreground = (*color[:3], 255)
            o.refresh_options()
            return
        if o.tool == "clone" and modifiers & Qt.KeyboardModifier.AltModifier:
            o.clone_source = point
            o.clone_offset = None
            o.update_status("Clone source set")
            return
        if o.tool == "crop":
            if self.pending is None:
                self.start_crop()
            rect = self.pending["rect"]
            self.drag = dict(kind="crop", start=point, original=replace(rect), mode="create")
            for i, (u, v) in enumerate(editing.HANDLES):
                if math.dist(point, rect.point(u, v)) * self.screen_zoom < 10:
                    self.drag.update(mode="resize", handle=i)
                    break
            else:
                if (
                    rect.x < point[0] < rect.x + rect.width
                    and rect.y < point[1] < rect.y + rect.height
                ):
                    self.drag["mode"] = "move"
            return
        if o.tool == "wand":
            image = engine.render(
                self.document,
                only=None if o.options["sample_all"] else {self.document.active},
            )
            selection = kernels.wand(
                image,
                int(point[0]),
                int(point[1]),
                o.options["tolerance"],
                o.options["contiguous"],
                o.options["sample_radius"],
            )
            self.set_selection(selection, modifiers)
            return
        selection = o.history.document.selection
        inside_selection = (
            selection is not None
            and 0 <= point[0] < selection.width
            and 0 <= point[1] < selection.height
            and selection.getpixel((int(point[0]), int(point[1]))) > 0
        )
        if inside_selection and (
            modifiers & Qt.KeyboardModifier.ControlModifier
            or o.tool in ("marquee", "lasso")
            and o.options["selection_mode"] == "New"
            and not modifiers & Qt.KeyboardModifier.ShiftModifier
        ):
            self.draft = o.history.document.clone()
            kind = (
                "pixels"
                if modifiers & Qt.KeyboardModifier.ControlModifier or o.tool == "move"
                else "selection"
            )
            self.drag = dict(
                start=point,
                end=point,
                kind=kind,
                duplicate=bool(modifiers & Qt.KeyboardModifier.AltModifier),
            )
            return
        if o.tool == "lasso" and o.options["lasso"] == "Polygonal":
            if self.points and math.dist(point, self.points[0]) * self.screen_zoom < 8:
                self.finish_lasso(modifiers)
            else:
                self.points.append(point)
                self.update()
            return
        if (
            o.tool == "move"
            and self.pending is None
            and (o.options["auto_select"] or modifiers & Qt.KeyboardModifier.ControlModifier)
        ):
            d = o.history.document
            current = editing.selection_transform(d, o.selected, o.mask_target)
            hits_handle = any(
                math.dist(point, handle) * self.screen_zoom < 10
                for handle in self.transform_handles(current)
            )
            inside = current.local(*point, (1, 1))
            if not hits_handle and (
                modifiers & Qt.KeyboardModifier.ControlModifier
                or not (0 <= inside[0] <= 1 and 0 <= inside[1] <= 1)
            ):
                for candidate, ancestors in d.entries(top_first=True):
                    u, v = candidate.transform.local(*point, (1, 1))
                    if (
                        candidate.image is not None
                        and candidate.visible
                        and all(parent.visible for parent in ancestors)
                        and 0 <= u <= 1
                        and 0 <= v <= 1
                    ):
                        d.active, o.selected, o.mask_target = candidate.id, {candidate.id}, False
                        o.refresh()
                        break
        self.drag = dict(start=point, end=point, modifiers=modifiers, kind=o.tool)
        if o.tool == "lasso":
            self.points = [point]
        if o.tool in ("brush", "heal", "clone", "smear", "gradient", "move"):
            self.draft = self.document.clone()
        if o.tool == "move":
            if modifiers & Qt.KeyboardModifier.AltModifier:
                copies = editing.duplicate(History(self.draft), o.selected or {self.draft.active})
                self.drag["copy_ids"] = copies
                self.drag["original_document"] = self.draft.clone()
            layer = self.draft.layer()
            if layer is None:
                self.cancel()
                return
            t = editing.selection_transform(
                self.draft, self.drag.get("copy_ids", o.selected), o.mask_target
            )
            distorted = self.pending and self.pending.get("corners")
            if distorted:
                t = self.pending["box"]
            self.drag["original"] = replace(t)
            for i, handle in enumerate(self.transform_handles(t)):
                if math.dist(point, handle) * self.screen_zoom < 10:
                    self.drag["handle"] = i
                    self.drag["kind"] = (
                        "distort"
                        if distorted or modifiers & Qt.KeyboardModifier.ControlModifier
                        else "resize"
                    )
                    break
            rotation = self.screen(t.point(0.5, 0)) - QPointF(0, 22)
            if not distorted and (event.position() - rotation).manhattanLength() < 12:
                self.drag["kind"] = "rotate"
            if self.drag["kind"] != "move" and self.drag.get("copy_ids"):
                # Alt scales around the center on a handle; duplication belongs to a body drag.
                self.draft = o.history.document.clone()
                self.drag.pop("copy_ids")
                self.drag.pop("original_document")
            self.drag.setdefault("original_document", self.draft.clone())
            if self.drag["kind"] == "distort" or distorted:
                if self.pending is None:
                    self.start_transform()
                self.pending.setdefault("distort_base", self.draft.clone())
                self.pending.setdefault("box", replace(t))
                self.drag["original_corners"] = list(
                    self.pending.get("corners")
                    or [t.point(u, v) for u, v in ((0, 0), (1, 0), (1, 1), (0, 1))]
                )
                if self.drag["kind"] == "move":
                    self.drag["kind"] = "distort_move"
        if o.tool in ("brush", "heal", "clone", "smear"):
            options = o.options
            mode = (
                options["brush_mode"]
                if o.tool == "brush"
                else "Heal " + options["heal_mode"]
                if o.tool == "heal"
                else "Clone"
                if o.tool == "clone"
                else options["smear_mode"]
            )
            source = None
            if mode == "Clone":
                if o.clone_source is None:
                    raise ValueError("Alt-click to set the Clone Stamp source first.")
                if o.clone_offset is None or not options["aligned"]:
                    o.clone_offset = (
                        o.clone_source[0] - point[0],
                        o.clone_source[1] - point[1],
                    )
                layer, image, t = editing.raster_target(self.draft, o.mask_target)
                sample = engine.render(
                    o.history.document,
                    only=None if options["sample_all"] else {layer.id},
                )
                # Map document-space source pixels into the target's local raster.
                dx, dy = o.clone_offset
                mapping = replace(t, x=t.x + dx, y=t.y + dy)

                def pt(u, v):
                    return mapping.point(
                        1 - u if mapping.flip_x else u, 1 - v if mapping.flip_y else v
                    )

                p, px, py = pt(0, 0), pt(1, 0), pt(0, 1)
                source = sample.transform(
                    image.size,
                    Image.Transform.AFFINE,
                    (
                        (px[0] - p[0]) / image.width,
                        (py[0] - p[0]) / image.height,
                        p[0],
                        (px[1] - p[1]) / image.width,
                        (py[1] - p[1]) / image.height,
                        p[1],
                    ),
                    Image.Resampling.BILINEAR,
                )
            color = (
                (255, 255, 255, 255)
                if o.mask_target and options["mask_white"]
                else (0, 0, 0, 255)
                if o.mask_target
                else o.foreground
            )
            stroke_class = WarpStroke if mode in ("Smudge", "Liquify") else editing.Stroke
            self.stroke = stroke_class(
                self.draft,
                options["diameter"],
                options["hardness"],
                options["opacity"],
                color,
                o.mask_target,
                mode,
                source,
            )
            if modifiers & Qt.KeyboardModifier.ShiftModifier and self.last_stroke_point:
                self.stroke.append(self.last_stroke_point)
            self.stroke.append(point)
            self.invalidate()

    def mouseMoveEvent(self, event):
        if self.document is None:
            return
        self.cursor_point = self.document_point(event)
        if self.drag is None:
            self.update()
            return
        try:
            self.continue_drag(event)
        except Exception as exc:
            self.cancel()
            self.owner.fail(exc)

    def continue_drag(self, event):
        point = self.document_point(event)
        drag, o = self.drag, self.owner
        if drag["kind"] == "pan":
            self.pan = drag["pan"] + event.position() - drag["screen"]
            self.invalidate()
            return
        if drag["kind"] == "hue":
            o.filter_dialog.drag_hue_canvas(
                event.position().x(), bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            )
            return
        if drag["kind"] == "sample":
            return
        if drag["kind"] == "crop":
            self.update_crop(point, event.modifiers())
            return
        start = drag["start"]
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and o.tool in (
            "marquee",
            "shape",
        ):
            length = max(abs(point[0] - start[0]), abs(point[1] - start[1]))
            point = (
                start[0] + math.copysign(length, point[0] - start[0]),
                start[1] + math.copysign(length, point[1] - start[1]),
            )
        drag["end"] = point
        if o.tool == "crop" and not event.modifiers() & Qt.KeyboardModifier.AltModifier:
            tolerance = 8 / self.screen_zoom
            point = tuple(
                min((0, dimension), key=lambda value: abs(value - coordinate))
                if min(abs(coordinate), abs(coordinate - dimension)) < tolerance
                else coordinate
                for coordinate, dimension in zip(point, (self.document.width, self.document.height))
            )
            drag["end"] = point
        if drag["kind"] in ("selection", "pixels"):
            dx, dy = round(point[0] - start[0]), round(point[1] - start[1])
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                dx, dy = (dx, 0) if abs(dx) >= abs(dy) else (0, dy)
            self.draft = o.history.document.clone()
            if drag["kind"] == "selection":
                selection = self.draft.selection
                self.draft.selection = selection.transform(
                    selection.size,
                    Image.Transform.AFFINE,
                    (1, 0, -dx, 0, 1, -dy),
                    Image.Resampling.NEAREST,
                )
            else:
                editing.move_pixels(History(self.draft), dx, dy, drag["duplicate"])
            self.invalidate()
        elif self.stroke:
            self.stroke.append(point)
            self.invalidate()
        elif o.tool == "gradient":
            self.preview_gradient(start, point)
        elif o.tool == "lasso":
            self.points.append(point)
            self.update()
        elif o.tool == "move":
            self.guides = []
            t = drag["original"]
            dx, dy = point[0] - start[0], point[1] - start[1]
            shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            if drag["kind"] == "resize":
                new = editing.resized_transform(
                    t,
                    drag["handle"],
                    start,
                    point,
                    bool(shift) == o.options["locks_transform_ratio"],
                    bool(event.modifiers() & Qt.KeyboardModifier.AltModifier),
                )
            elif drag["kind"] == "rotate":
                cx, cy = t.center
                rotation = t.rotation + math.degrees(
                    math.atan2(point[1] - cy, point[0] - cx)
                    - math.atan2(start[1] - cy, start[0] - cx)
                )
                new = replace(t, rotation=round(rotation / 15) * 15 if shift else rotation)
            elif drag["kind"] in ("distort", "distort_move"):
                self.preview_distort(point, bool(shift))
                return
            else:
                if shift:
                    dx, dy = (dx, 0) if abs(dx) >= abs(dy) else (0, dy)
                new = replace(t, x=t.x + dx, y=t.y + dy)
                if not event.modifiers() & Qt.KeyboardModifier.AltModifier:
                    tolerance = 8 / self.screen_zoom
                    d = o.history.document
                    xs, ys = [0, d.width / 2, d.width], [0, d.height / 2, d.height]
                    moving = d.descendants(o.selected)
                    for item, ancestors in d.entries():
                        if (
                            item.id not in moving
                            and item.visible
                            and all(parent.visible for parent in ancestors)
                            and not item.group
                        ):
                            corners = [
                                item.transform.point(u, v)
                                for u, v in ((0, 0), (1, 0), (1, 1), (0, 1))
                            ]
                            corner_x, corner_y = zip(*corners)
                            xs.extend(
                                [min(corner_x), (min(corner_x) + max(corner_x)) / 2, max(corner_x)]
                            )
                            ys.extend(
                                [min(corner_y), (min(corner_y) + max(corner_y)) / 2, max(corner_y)]
                            )
                    for key, size, targets in (
                        ("x", new.width, xs),
                        ("y", new.height, ys),
                    ):
                        value = getattr(new, key)
                        closest, target = min(
                            (
                                (target - guide, target)
                                for target in targets
                                for guide in (value, value + size / 2, value + size)
                            ),
                            key=lambda candidate: abs(candidate[0]),
                        )
                        if abs(closest) < tolerance:
                            setattr(new, key, value + closest)
                            self.guides.append((key, target))
            base = self.pending["document"] if self.pending else o.history.document
            self.draft = drag.get("original_document", base).clone()
            selected = o.selected or {self.draft.active}
            if drag.get("copy_ids"):
                selected = drag["copy_ids"]
            editing.transform_layers(History(self.draft), selected, new, o.mask_target)
            self.invalidate()
        else:
            self.update()

    def mouseReleaseEvent(self, event):
        if self.drag is None:
            return
        try:
            self.finish(event)
        except Exception as exc:
            self.cancel()
            self.owner.fail(exc)

    def finish(self, event):
        o, drag = self.owner, self.drag
        if drag["kind"] == "pan":
            self.drag = None
            return
        if drag["kind"] == "hue":
            o.filter_dialog.hue_drag = None
            self.drag = None
            return
        if drag["kind"] == "sample":
            self.drag = None
            return
        if drag["kind"] == "crop":
            self.update_crop(self.document_point(event), event.modifiers())
            self.drag = None
            o.refresh_options()
            return
        start, end = drag["start"], drag.get("end", drag["start"])
        if drag["kind"] in ("selection", "pixels"):
            if self.draft:
                with o.history.edit(
                    "Move Selection" if drag["kind"] == "selection" else "Move Pixels"
                ) as d:
                    d.layers, d.selection = self.draft.layers, self.draft.selection
            self.cancel()
            o.refresh()
        elif self.stroke:
            self.stroke.finish()
            self.commit("Brush Stroke" if o.tool == "brush" else o.tool.title())
            self.last_stroke_point = end
        elif o.tool == "move":
            if drag["kind"] in ("distort", "distort_move"):
                self.preview_distort(
                    end, bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                )
                self.pending["document"] = self.draft
                self.pending["corners"] = drag.get("candidate_corners", drag["original_corners"])
                self.draft, self.drag = None, None
                self.invalidate()
                o.refresh_options()
            else:
                if self.pending and self.pending["kind"] == "transform":
                    self.pending["document"] = self.draft
                    if drag.get("copy_ids"):
                        o.selected = drag["copy_ids"]
                    self.draft, self.drag = None, None
                    self.invalidate()
                    o.refresh_options()
                else:
                    self.commit("Transform")
        elif o.tool in ("marquee", "shape", "crop"):
            left, top = min(start[0], end[0]), min(start[1], end[1])
            width, height = abs(end[0] - start[0]), abs(end[1] - start[1])
            if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                left, top, width, height = (
                    start[0] - width,
                    start[1] - height,
                    width * 2,
                    height * 2,
                )
            if width >= 1 and height >= 1:
                if o.tool == "crop":
                    editing.crop(o.history, (left, top, width, height))
                elif o.tool == "shape":
                    with o.history.edit("Shape") as d:
                        size = round(width), round(height)
                        dimensions(*size, raster=True)
                        style = dict(
                            kind=o.options["shape"],
                            red=o.foreground[0] / 255,
                            green=o.foreground[1] / 255,
                            blue=o.foreground[2] / 255,
                            cornerRadius=o.options["corner_radius"],
                        )
                        layer = Layer(
                            "Shape",
                            Transform(round(left), round(top), *size),
                            image=engine.shape_pixels(size, style),
                            shape=style,
                        )
                        editing.inserted(d, layer)
                        o.selected = {layer.id}
                else:
                    selection = Image.new(
                        "L", (o.history.document.width, o.history.document.height)
                    )
                    draw = ImageDraw.Draw(selection)
                    method = draw.ellipse if o.options["marquee"] == "Ellipse" else draw.rectangle
                    method(
                        (
                            round(left),
                            round(top),
                            round(left + width) - 1,
                            round(top + height) - 1,
                        ),
                        fill=255,
                    )
                    self.set_selection(selection, event.modifiers())
            self.cancel()
            o.refresh()
        elif o.tool == "lasso":
            self.finish_lasso(event.modifiers())
        elif o.tool == "gradient":
            if math.dist(start, end) >= 0.5:
                self.preview_gradient(start, end)
                self.draft, self.drag = None, None
                o.refresh_options()
            else:
                self.cancel()
        else:
            self.cancel()

    def commit(self, name):
        if self.draft:
            with self.owner.history.edit(name) as d:
                d.layers, d.active = self.draft.layers, self.draft.active
            if self.drag and self.drag.get("copy_ids"):
                self.owner.selected = self.drag["copy_ids"]
        self.cancel()
        self.owner.refresh()

    def set_selection(self, selection, modifiers=Qt.KeyboardModifier.NoModifier):
        with self.owner.history.edit("Selection") as d:
            mode = self.owner.options["selection_mode"]
            if modifiers & Qt.KeyboardModifier.AltModifier:
                mode = "Subtract"
            elif modifiers & Qt.KeyboardModifier.ShiftModifier:
                mode = "Add"
            if mode == "Add" and d.selection is not None:
                selection = ImageChops.lighter(d.selection, selection)
            elif mode == "Subtract":
                selection = ImageChops.subtract(
                    d.selection or Image.new("L", selection.size), selection
                )
            # An empty selection clips every edit; only explicit Deselect means edit everywhere.
            d.selection = selection
        self.owner.refresh()

    def finish_lasso(self, modifiers=Qt.KeyboardModifier.NoModifier):
        if len(self.points) > 2:
            d = self.owner.history.document
            selection = Image.new("L", (d.width, d.height))
            ImageDraw.Draw(selection).polygon(self.points, fill=255)
            self.set_selection(selection, modifiers)
        self.points, self.drag = [], None
        self.invalidate()

    def mouseDoubleClickEvent(self, event):
        if self.owner.tool == "lasso":
            self.finish_lasso(event.modifiers())

    def start_crop(self):
        d = self.owner.history.document
        self.pending = dict(
            kind="crop", document=d.clone(), rect=Transform(width=d.width, height=d.height)
        )
        self.invalidate()

    def start_transform(self):
        d = self.owner.history.document
        self.pending = dict(kind="transform", base=d.clone(), document=d.clone())
        self.invalidate()

    def transform_handles(self, transform):
        corners = self.pending.get("corners") if self.pending else None
        if self.drag and self.drag.get("candidate_corners"):
            corners = self.drag["candidate_corners"]
        if corners is None:
            return [transform.point(u, v) for u, v in editing.HANDLES]
        return [
            point
            for i, corner in enumerate(corners)
            for point in (corner, tuple((a + b) / 2 for a, b in zip(corner, corners[(i + 1) % 4])))
        ]

    def preview_distort(self, point, shift=False):
        drag, o = self.drag, self.owner
        corners = list(drag["original_corners"])
        dx, dy = point[0] - drag["start"][0], point[1] - drag["start"][1]
        if shift:
            dx, dy = (dx, 0) if abs(dx) >= abs(dy) else (0, dy)
        i = drag.get("handle", 0)
        moved = (
            range(4)
            if drag["kind"] == "distort_move"
            else [i // 2]
            if i % 2 == 0
            else [i // 2, (i // 2 + 1) % 4]
        )
        for corner in moved:
            corners[corner] = corners[corner][0] + dx, corners[corner][1] + dy
        draft = self.pending["distort_base"].clone()
        try:
            editing.distort(History(draft), corners, o.mask_target, o.selected)
        except ValueError as exc:
            o.update_status(str(exc))
            return
        self.draft, drag["candidate_corners"] = draft, corners
        self.invalidate()

    def preview_transform(self, transform):
        if self.pending is None:
            self.start_transform()
        draft = self.pending["document"].clone()
        editing.transform_layers(
            History(draft), self.owner.selected, transform, self.owner.mask_target
        )
        self.pending["document"] = draft
        self.invalidate()

    def crop_ratio(self):
        d = self.owner.history.document
        return {"Original": d.width / d.height, "1:1": 1, "4:3": 4 / 3, "16:9": 16 / 9}.get(
            self.owner.options["crop_ratio"]
        )

    def update_crop(self, point, modifiers):
        drag = self.drag
        t, start = drag["original"], drag["start"]
        ratio = self.crop_ratio()
        symmetric = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        if drag["mode"] == "move":
            rect = replace(t, x=t.x + point[0] - start[0], y=t.y + point[1] - start[1])
        elif drag["mode"] == "resize":
            rect = editing.resized_transform(
                t, drag["handle"], start, point, shift=ratio is None, option=symmetric
            )
        else:
            dx, dy = point[0] - start[0], point[1] - start[1]
            if ratio:
                if abs(dx) > abs(dy) * ratio:
                    dy = math.copysign(abs(dx) / ratio, dy)
                else:
                    dx = math.copysign(abs(dy) * ratio, dx)
            rect = Transform(
                min(start[0], start[0] + dx),
                min(start[1], start[1] + dy),
                max(1, abs(dx)),
                max(1, abs(dy)),
            )
            if symmetric:
                rect = Transform(
                    start[0] - abs(dx), start[1] - abs(dy), max(1, abs(dx) * 2), max(1, abs(dy) * 2)
                )
        d = self.owner.history.document
        xs, ys = [0, d.width], [0, d.height]
        for layer, ancestors in d.entries():
            if layer.visible and not layer.group and all(parent.visible for parent in ancestors):
                corners = [layer.transform.point(u, v) for u, v in ((0, 0), (1, 0), (1, 1), (0, 1))]
                xs.extend([min(x for x, _ in corners), max(x for x, _ in corners)])
                ys.extend([min(y for _, y in corners), max(y for _, y in corners)])
        tolerance = 0 if modifiers & Qt.KeyboardModifier.ControlModifier else 8 / self.screen_zoom
        if drag["mode"] == "move":
            for attribute, size, targets in (("x", rect.width, xs), ("y", rect.height, ys)):
                edge = getattr(rect, attribute)
                delta = min(
                    (target - value for target in targets for value in (edge, edge + size)), key=abs
                )
                if abs(delta) <= tolerance:
                    setattr(rect, attribute, edge + delta)
        elif ratio is None:
            for attribute, size_attribute, targets, coordinate, center in (
                ("x", "width", xs, point[0], start[0] if drag["mode"] == "create" else t.center[0]),
                (
                    "y",
                    "height",
                    ys,
                    point[1],
                    start[1] if drag["mode"] == "create" else t.center[1],
                ),
            ):
                edge, size = getattr(rect, attribute), getattr(rect, size_attribute)
                low = abs(coordinate - edge) <= abs(coordinate - edge - size)
                value = edge if low else edge + size
                target = min(targets, key=lambda target: abs(target - value))
                if abs(target - value) <= tolerance and (
                    (low and target < edge + size) or (not low and target > edge)
                ):
                    if low:
                        setattr(rect, attribute, target)
                        setattr(rect, size_attribute, edge + size - target)
                    else:
                        setattr(rect, size_attribute, target - edge)
                    if symmetric:
                        half = abs(target - center)
                        setattr(rect, attribute, center - half)
                        setattr(rect, size_attribute, max(1, half * 2))
        left, top, right, bottom = (
            round(rect.x),
            round(rect.y),
            round(rect.x + rect.width),
            round(rect.y + rect.height),
        )
        rect = Transform(left, top, max(1, right - left), max(1, bottom - top))
        dimensions(round(rect.width), round(rect.height))
        self.pending["rect"] = rect
        self.invalidate()

    def preview_gradient(self, start, end):
        o = self.owner
        base = (
            self.pending["base"]
            if self.pending and self.pending["kind"] == "gradient"
            else o.history.document.clone()
        )
        draft = base.clone()
        if math.dist(start, end) >= 0.5:
            editing.gradient(
                draft,
                start,
                end,
                o.foreground,
                o.background,
                o.options["gradient_shape"] == "Radial",
                o.options["gradient_style"] == "Foreground to Transparent",
                o.options["gradient_reverse"],
                o.options["opacity"],
                o.mask_target,
            )
        self.pending = dict(kind="gradient", base=base, document=draft, start=start, end=end)
        self.draft = draft if self.drag else None
        self.invalidate()

    def refresh_pending(self):
        if self.pending and self.pending["kind"] == "gradient":
            self.preview_gradient(self.pending["start"], self.pending["end"])
        elif self.pending and self.pending["kind"] == "crop" and self.crop_ratio():
            rect = self.pending["rect"]
            height = rect.width / self.crop_ratio()
            self.pending["rect"] = replace(rect, y=rect.center[1] - height / 2, height=height)
            self.invalidate()

    def apply_pending(self):
        if self.pending is None:
            return
        if self.pending["kind"] == "crop":
            rect = self.pending["rect"]
            editing.crop(self.owner.history, (rect.x, rect.y, rect.width, rect.height))
        else:
            draft = self.pending["document"]
            with self.owner.history.edit(self.pending["kind"].title()) as d:
                d.layers, d.active = draft.layers, draft.active
        self.cancel()
        self.owner.refresh()

    def resolve_pending(self):
        if self.pending and self.pending["kind"] != "crop":
            self.apply_pending()
        else:
            self.cancel()

    def cancel(self):
        self.draft, self.drag, self.stroke, self.points = None, None, None, []
        self.pending = None
        self.guides = []
        self.invalidate()

    def wheelEvent(self, event):
        self.zoom_to(self.zoom * 1.25 ** (event.angleDelta().y() / 120), event.position())

    def keyPressEvent(self, event):
        key, modifiers = event.key(), event.modifiers()
        if key == Qt.Key.Key_Space:
            self.space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if self.owner.filter_dialog is not None:
            if key == Qt.Key.Key_Escape:
                self.owner.filter_dialog.reject()
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.owner.filter_dialog.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.cancel()
            self.owner.refresh_options()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.pending:
            self.apply_pending()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.points:
            self.finish_lasso()
            return
        if key == Qt.Key.Key_Backspace and self.points:
            self.points.pop()
            self.update()
            return
        if (
            key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down)
            and self.owner.history
        ):
            dx, dy = {
                Qt.Key.Key_Left: (-1, 0),
                Qt.Key.Key_Right: (1, 0),
                Qt.Key.Key_Up: (0, -1),
                Qt.Key.Key_Down: (0, 1),
            }[key]
            factor = 10 if modifiers & Qt.KeyboardModifier.ShiftModifier else 1
            self.owner.nudge(
                dx * factor,
                dy * factor,
                bool(modifiers & Qt.KeyboardModifier.ControlModifier),
            )
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.owner.run(self.owner.delete)
            return
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            super().keyPressEvent(event)
            return
        text = event.text().lower()
        tools = dict(
            v="move",
            m="marquee",
            l="lasso",
            w="wand",
            c="crop",
            b="brush",
            e="brush",
            j="heal",
            s="clone",
            r="smear",
            g="gradient",
            u="shape",
            t="text",
            i="eyedropper",
            h="hand",
            z="zoom",
            a="idle",
        )
        if text in tools:
            if text == "e":
                self.owner.options["brush_mode"] = "Erase"
            elif text == "b":
                self.owner.options["brush_mode"] = "Paint"
            if modifiers & Qt.KeyboardModifier.ShiftModifier and text in (
                "m",
                "l",
                "u",
            ):
                name, choices = {
                    "m": ("marquee", ["Rectangle", "Ellipse"]),
                    "l": ("lasso", ["Freehand", "Polygonal"]),
                    "u": ("shape", ["Rectangle", "Ellipse"]),
                }[text]
                self.owner.options[name] = choices[1 - choices.index(self.owner.options[name])]
            self.owner.set_tool(tools[text])
        elif text == "x":
            self.owner.foreground, self.owner.background = (
                self.owner.background,
                self.owner.foreground,
            )
            self.owner.refresh_options()
        elif text == "d":
            self.owner.foreground, self.owner.background = (
                (0, 0, 0, 255),
                (255, 255, 255, 255),
            )
            self.owner.refresh_options()
        elif text in ("[", "]"):
            option = "hardness" if modifiers & Qt.KeyboardModifier.ShiftModifier else "diameter"
            amount = 0.25 if option == "hardness" else 5
            self.owner.options[option] = max(
                0 if option == "hardness" else 1,
                min(
                    1 if option == "hardness" else 2000,
                    self.owner.options[option] + (amount if text == "]" else -amount),
                ),
            )
            self.owner.refresh_options()
        elif text.isdigit():
            self.owner.options["opacity"] = (int(text) or 10) / 10
            self.owner.refresh_options()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            self.space = False
            self.setCursor(
                Qt.CursorShape.IBeamCursor
                if self.owner.tool == "text"
                else Qt.CursorShape.ArrowCursor
            )

    def leaveEvent(self, event):
        self.cursor_point = None
        self.update()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self.owner.filter_dialog is not None:
            event.ignore()
            return
        mime = event.mimeData()
        if mime.hasUrls():
            self.owner.run(
                lambda: self.owner.open_paths(
                    [u.toLocalFile() for u in mime.urls() if u.isLocalFile()],
                    importing=True,
                )
            )
        elif mime.hasImage():
            self.owner.run(lambda: self.owner.import_qimage(mime.imageData()))
        event.acceptProposedAction()
