"""Undoable editing commands independent of the desktop UI."""

import math
from copy import deepcopy
from dataclasses import replace

import numpy as np
from PIL import Image, ImageChops, ImageOps
from scipy.ndimage import map_coordinates

from . import engine, kernels
from .model import (
    Document,
    Layer,
    TextContent,
    Transform,
    dimensions,
    linux_to_mac_text,
    new_id,
    validate_text,
)

HANDLES = ((0, 0), (0.5, 0), (1, 0), (1, 0.5), (1, 1), (0.5, 1), (0, 1), (0, 0.5))


def resized_transform(original, handle, start, point, shift=False, option=False):
    u, v = HANDLES[handle]
    au, av = (0.5, 0.5) if option else (1 - u, 1 - v)
    ax, ay = original.point(au, av)
    hx, hy = original.point(u, v)
    dx, dy = hx + point[0] - start[0] - ax, hy + point[1] - start[1] - ay
    angle = math.radians(original.rotation % 360)
    span = 2 if option else 1
    local_x = (dx * math.cos(angle) + dy * math.sin(angle)) * span
    local_y = (-dx * math.sin(angle) + dy * math.cos(angle)) * span
    sx, sy = 2 * u - 1, 2 * v - 1
    width = original.width if sx == 0 else max(1, local_x * sx)
    height = original.height if sy == 0 else max(1, local_y * sy)
    if not shift:
        if sx == 0:
            factor = height / original.height
        elif sy == 0:
            factor = width / original.width
        else:
            factor = max(
                1 / min(original.width, original.height),
                (local_x * sx * original.width + local_y * sy * original.height)
                / (original.width**2 + original.height**2),
            )
        width, height = original.width * factor, original.height * factor
    ox, oy = (0.5 - au) * width, (0.5 - av) * height
    cx, cy = (
        ax + ox * math.cos(angle) - oy * math.sin(angle),
        ay + ox * math.sin(angle) + oy * math.cos(angle),
    )
    return replace(original, x=cx - width / 2, y=cy - height / 2, width=width, height=height)


def local_coverage(document, layer, size=None, transform=None):
    size = size or (
        layer.image.size
        if layer.image is not None
        else (round(layer.transform.width), round(layer.transform.height))
    )
    if document.selection is None:
        return Image.new("L", size, 255)
    t = transform or layer.transform

    def point(u, v):
        return t.point(1 - u if t.flip_x else u, 1 - v if t.flip_y else v)

    p, px, py = point(0, 0), point(1, 0), point(0, 1)
    coefficients = (
        (px[0] - p[0]) / size[0],
        (py[0] - p[0]) / size[1],
        p[0],
        (px[1] - p[1]) / size[0],
        (py[1] - p[1]) / size[1],
        p[1],
    )
    return document.selection.transform(
        size, Image.Transform.AFFINE, coefficients, Image.Resampling.BILINEAR
    )


def inserted(document, layer):
    active = document.layer()
    layer.parent = active.id if active and active.group else active.parent if active else None
    index = document.layers.index(active) + 1 if active else len(document.layers)
    document.layers.insert(index, layer)
    document.active = layer.id
    return layer


def import_layers(history, images):
    with history.edit("Import Images") as document:
        for name, image in images:
            inserted(
                document,
                Layer(
                    name,
                    Transform(
                        (document.width - image.width) / 2,
                        (document.height - image.height) / 2,
                        image.width,
                        image.height,
                    ),
                    image=image,
                ),
            )


def new_layer(history, group=False, name=None, color=None):
    if group and color is not None:
        raise ValueError("Folders cannot have a solid fill.")
    label = "New Folder" if group else "New Solid Color Layer" if color is not None else "New Layer"
    with history.edit(label) as document:
        layer = Layer.blank(
            document.width, document.height, name or ("Folder" if group else "Layer")
        )
        layer.group = group
        if color is not None:
            dimensions(document.width, document.height, raster=True)
            layer.shape = solid_style(color)
            layer.image = Image.new("RGBA", (document.width, document.height), (*color, 255))
        inserted(document, layer)
    return layer.id


def solid_style(color):
    if len(color) != 3 or any(type(value) is not int or not 0 <= value <= 255 for value in color):
        raise ValueError("Choose an RGB layer color.")
    return dict(
        kind="Rectangle",
        cornerRadius=0,
        **dict(zip(("red", "green", "blue"), (v / 255 for v in color))),
    )


def put_text(document, settings, image, position=None, layer_id=None):
    """Replace editable text while preserving placement, scale and linked masks."""
    validate_text(settings)
    dimensions(*image.size, raster=True)
    if layer_id is None:
        x, y = position or (document.width * 0.1, document.height * 0.1)
        layer = Layer(
            settings["text"].strip().splitlines()[0][:48],
            Transform(x, y, image.width, image.height),
        )
        inserted(document, layer)
    else:
        layer = document.layer(layer_id)
        if layer is None or layer.live_text is None:
            raise ValueError("Select an editable text layer.")
        old = layer.transform
        updated = replace(
            old,
            width=image.width * old.width / layer.image.width,
            height=image.height * old.height / layer.image.height,
        )
        anchor = (1 if old.flip_x else 0, 1 if old.flip_y else 0)
        before, after = old.point(*anchor), updated.point(*anchor)
        updated = replace(
            updated, x=updated.x + before[0] - after[0], y=updated.y + before[1] - after[1]
        )
        set_transform(layer, updated)
    layer.image = image
    layer.text = TextContent(deepcopy(settings), image)
    if settings.get("macText") or "text" in layer.extras:
        layer.extras["text"] = linux_to_mac_text(settings)
    return layer


def duplicate(history, selected):
    with history.edit("Duplicate Layers") as document:
        ids = document.descendants(selected)
        remap = {id: new_id() for id in ids}
        result = []
        for layer in document.layers:
            result.append(layer)
            if layer.id in ids:
                copy = layer.clone()
                copy.id = remap[layer.id]
                copy.name += " copy"
                copy.parent = remap.get(copy.parent, copy.parent)
                copy.mask_source = remap.get(copy.mask_source, copy.mask_source)
                result.append(copy)
        document.layers = result
        document.active = remap.get(document.active, next(iter(remap), document.active))
    return set(remap.values())


def copy_layers(document, selected):
    """Copy selected subtrees and their clipping bases with owned raster data."""
    ids = document.descendants(set(selected))
    if not ids or any(document.layer(id) is None for id in ids):
        raise ValueError("Select layers to copy.")
    for _ in range(len(document.layers) + 1):
        sources = {
            layer.mask_source for layer in document.layers if layer.id in ids and layer.mask_source
        }
        expanded = document.descendants(ids | sources)
        if expanded == ids:
            break
        ids = expanded
    else:
        raise ValueError("Invalid clipping dependency cycle.")
    copies = []
    for layer in document.layers:
        if layer.id in ids:
            cloned = layer.clone()
            if cloned.image is not None:
                cloned.image = cloned.image.copy()
            if cloned.mask is not None:
                cloned.mask = cloned.mask.copy()
            if cloned.live_text is not None:
                cloned.text.image = cloned.image
            copies.append(cloned)
    return copies


def paste_layers(history, copied):
    if not copied or len(copied) > 10_000:
        raise ValueError("The layer clipboard is empty or too large.")
    ids = {layer.id for layer in copied}
    if len(ids) != len(copied):
        raise ValueError("Duplicate clipboard layer IDs.")
    remap = {id: new_id() for id in ids}
    with history.edit("Paste Layers") as document:
        for layer in copied:
            clone = layer.clone()
            clone.id = remap[layer.id]
            clone.parent = remap.get(layer.parent)
            clone.mask_source = remap.get(layer.mask_source)
            if clone.image is not None:
                clone.image = clone.image.copy()
            if clone.mask is not None:
                clone.mask = clone.mask.copy()
            if clone.live_text is not None:
                clone.text.image = clone.image
            document.layers.append(clone)
        document.active = remap[copied[-1].id]
    return set(remap.values())


def delete_layers(history, selected, bake=True):
    with history.edit("Delete Layers") as document:
        removed = document.descendants(selected)
        # Bake source alpha in document space before removing live links.
        for layer in document.layers:
            if layer.id not in removed and layer.mask_source in removed:
                if bake and layer.image is not None:
                    source = document.layer(layer.mask_source).clone()
                    source.visible = True
                    source.parent = None
                    source.blend = "Normal"
                    alpha_doc = Document(
                        document.width,
                        document.height,
                        layers=[item.clone() for item in document.layers],
                    )
                    for item in alpha_doc.layers:
                        item.visible = item.id == source.id
                        if item.id == source.id:
                            item.parent = None
                    alpha = engine.render(alpha_doc, only={source.id}).getchannel("A")
                    mask_doc = replace(document, selection=alpha)
                    coverage = local_coverage(mask_doc, layer)
                    image = layer.image.copy()
                    image.putalpha(ImageChops.multiply(image.getchannel("A"), coverage))
                    layer.image, layer.shape = image, None
                layer.mask_source = None
        document.layers = [item for item in document.layers if item.id not in removed]
        document.active = document.layers[-1].id if document.layers else None


def group_layers(history, selected):
    with history.edit("Group Layers") as document:
        ids = set(selected)
        roots = [
            item
            for item in document.layers
            if item.id in ids and item.parent not in document.descendants(ids - {item.id})
        ]
        if not roots:
            return
        ancestors = []
        parent = roots[0].parent
        while parent:
            ancestors.append(parent)
            parent = document.layer(parent).parent
        ancestors.append(None)
        common = None
        for candidate in ancestors:
            if all(
                candidate is None
                or r.parent == candidate
                or r.id in document.descendants({candidate})
                for r in roots
            ):
                common = candidate
                break
        group = Layer.blank(document.width, document.height, "Folder")
        group.group, group.parent = True, common
        document.layers.insert(max(document.layers.index(r) for r in roots) + 1, group)
        for layer in roots:
            layer.parent = group.id
        document.active = group.id
    return group.id


def ungroup(history, selected):
    with history.edit("Ungroup") as document:
        groups = [layer for layer in document.layers if layer.id in selected and layer.group]
        if not groups:
            raise ValueError("Select a folder to ungroup.")
        for group in groups:
            children = [child for child in document.layers if child.parent == group.id]
            for child in children:
                child.parent = group.parent
            document.layers = [item for item in document.layers if item not in children]
            index = document.layers.index(group)
            document.layers[index : index + 1] = children
        document.active = next(
            (layer.id for layer in document.layers if layer.parent == groups[0].parent), None
        )


def move_layer(history, id, parent, before=None):
    with history.edit("Reorder Layer") as document:
        layer = document.layer(id)
        if not layer:
            raise ValueError("Layer no longer exists.")
        if parent in document.descendants({id}):
            raise ValueError("A folder cannot be moved into its own descendants.")
        if parent is not None and (
            document.layer(parent) is None or not document.layer(parent).group
        ):
            raise ValueError("Destination must be a folder.")
        document.layers.remove(layer)
        layer.parent = parent
        index = next(
            (i for i, item in enumerate(document.layers) if item.id == before),
            len(document.layers),
        )
        document.layers.insert(index, layer)
        release_detached_clipping(document)


def release_detached_clipping(document):
    bases = {}
    for layer in document.layers:
        base = bases.get(layer.parent)
        if layer.mask_source:
            if layer.mask_source != base:
                layer.mask_source = None
                bases[layer.parent] = layer.id
        else:
            bases[layer.parent] = None if layer.group else layer.id


def set_transform(layer, transform):
    if layer.mask is not None:
        if layer.mask_linked:
            if layer.mask_transform:
                layer.mask_transform = layer.mask_transform.following(layer.transform, transform)
        elif layer.mask_transform is None:
            layer.mask_transform = replace(layer.transform)
    layer.transform = transform


def selection_transform(document, selected, target_mask=False):
    active = document.layer()
    if active is None:
        return Transform(width=document.width, height=document.height)
    if target_mask and active.mask is not None and not active.mask_linked:
        return active.mask_transform or active.transform
    ids = document.descendants(selected or {active.id})
    layers = [layer for layer in document.layers if layer.id in ids and not layer.group]
    if len(layers) == 1:
        return layers[0].transform
    points = [
        layer.transform.point(u, v) for layer in layers for u, v in ((0, 0), (1, 0), (1, 1), (0, 1))
    ]
    if not points:
        return active.transform
    xs, ys = zip(*points)
    return Transform(min(xs), min(ys), max(1, max(xs) - min(xs)), max(1, max(ys) - min(ys)))


def merge(history, selected):
    with history.edit("Merge Layers") as document:
        ids = document.descendants(selected)
        roots = [item for item in document.layers if item.id in selected and item.parent not in ids]
        if not roots:
            return
        parent = roots[0].parent
        if any(root.parent != parent for root in roots):
            raise ValueError("Merge layers from the same folder, or merge a whole selected folder.")
        # Merge against transparency, as the Mac implementation does.
        detached = document.clone()
        for layer in detached.layers:
            if layer.id in ids and layer.parent not in ids:
                layer.parent = None
            if layer.mask_source not in ids:
                layer.mask_source = None
        image = engine.render(detached, only=ids)
        replacement = Layer.blank(document.width, document.height, "Merged Layers")
        bounds = image.getchannel("A").getbbox()
        if bounds:
            replacement.image = image.crop(bounds)
            replacement.transform = Transform(
                bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1]
            )
        replacement.parent = parent
        index = max(i for i, item in enumerate(document.layers) if item.id in ids)
        document.layers.insert(index + 1, replacement)
        for layer in document.layers:
            if layer.mask_source in ids:
                layer.mask_source = replacement.id
        document.layers = [item for item in document.layers if item.id not in ids]
        document.active = replacement.id


def toggle_clipping(history):
    with history.edit("Clipping Mask") as document:
        layer = document.layer()
        if layer is None or layer.group:
            raise ValueError("Select a pixel or adjustment layer.")
        if layer.mask_source:
            layer.mask_source = None
            return
        siblings = [
            item for item in document.layers if item.parent == layer.parent and not item.group
        ]
        i = siblings.index(layer)
        if i == 0:
            raise ValueError("There is no lower sibling to use as the clipping base.")
        lower = siblings[i - 1]
        layer.mask_source = lower.mask_source or lower.id


def add_mask(history, reveal=True):
    with history.edit("Add Layer Mask") as document:
        layer = document.layer()
        if layer is None:
            raise ValueError("Select a layer first.")
        if document.selection is None:
            layer.mask = Image.new("L", (1, 1), 255 if reveal else 0)
        else:
            layer.mask = local_coverage(document, layer)
            if not reveal:
                layer.mask = ImageOps.invert(layer.mask)
        layer.mask_enabled, layer.mask_linked, layer.mask_transform = True, True, None


def mask_in_source(layer, size):
    if layer.mask is None:
        return Image.new("L", size, 255)
    if layer.mask.size == (1, 1):
        return Image.new("L", size, layer.mask.getpixel((0, 0)))
    placement = layer.mask_transform or layer.transform
    mapping = (
        np.diag([layer.mask.width, layer.mask.height, 1])
        @ np.linalg.inv(placement.matrix())
        @ pixel_matrix(layer.transform, size)
    )
    data = np.asarray(layer.mask)
    edges = np.concatenate((data[0], data[-1], data[1:-1, 0], data[1:-1, -1]))
    background = 255 if layer.mask_transform and edges.mean() >= 127.5 else 0
    return layer.mask.transform(
        size,
        Image.Transform.AFFINE,
        tuple(mapping[:2].ravel()),
        Image.Resampling.BILINEAR,
        fillcolor=background,
    )


def apply_mask(history):
    with history.edit("Apply Mask") as document:
        layer = document.layer()
        if layer is None or layer.mask is None:
            raise ValueError("Select a layer with a raster mask.")
        if layer.group or layer.adjustment is not None:
            raise ValueError("Raster masks can be applied to pixel and shape layers.")
        _, image, _ = raster_target(document)
        output = image.copy()
        output.putalpha(
            ImageChops.multiply(image.getchannel("A"), mask_in_source(layer, image.size))
        )
        layer.image, layer.shape, layer.mask, layer.mask_transform = output, None, None, None


def transform_layers(history, selected, transform, target_mask=False):
    with history.edit("Transform") as document:
        active = document.layer()
        if active is None:
            return
        if target_mask and active.mask is not None and not active.mask_linked:
            active.mask_transform = transform
            return
        old = selection_transform(document, selected)
        for layer in document.layers:
            if layer.id not in document.descendants(selected):
                continue
            new = layer.transform.following(old, transform)
            new.sampling = transform.sampling
            set_transform(layer, new)


def resize_canvas(history, width, height, anchor=(0.5, 0.5)):
    dimensions(width, height)
    with history.edit("Canvas Size") as document:
        dx, dy = (
            (width - document.width) * anchor[0],
            (height - document.height) * anchor[1],
        )
        for layer in document.layers:
            layer.transform = replace(
                layer.transform, x=layer.transform.x + dx, y=layer.transform.y + dy
            )
            if layer.mask_transform:
                layer.mask_transform = replace(
                    layer.mask_transform,
                    x=layer.mask_transform.x + dx,
                    y=layer.mask_transform.y + dy,
                )
        for guide in document.extras.get("guides", []):
            guide["position"] += dx if guide["axis"] == "vertical" else dy
        document.width, document.height = width, height
        document.selection = None


def crop(history, box, name="Crop"):
    x, y, width, height = map(round, box)
    dimensions(width, height)
    with history.edit(name) as document:
        for layer in document.layers:
            layer.transform = replace(
                layer.transform, x=layer.transform.x - x, y=layer.transform.y - y
            )
            if layer.mask_transform:
                layer.mask_transform = replace(
                    layer.mask_transform,
                    x=layer.mask_transform.x - x,
                    y=layer.mask_transform.y - y,
                )
        for guide in document.extras.get("guides", []):
            guide["position"] -= x if guide["axis"] == "vertical" else y
        document.width, document.height = width, height
        document.selection = None


def trim_bounds(
    document,
    based_on="Transparent Pixels",
    sides=(True, True, True, True),
    tolerance=0,
    cancelled=None,
):
    """Find content bounds in rendered canvas strips, keeping temporary rasters finite."""
    if based_on not in ("Transparent Pixels", "Top Left Pixel Color", "Bottom Right Pixel Color"):
        raise ValueError("Invalid trim basis.")
    if (
        len(sides) != 4
        or any(type(side) is not bool for side in sides)
        or not any(sides)
        or type(tolerance) is not int
        or not 0 <= tolerance <= 255
    ):
        raise ValueError("Invalid trim options.")
    width, height = document.width, document.height
    target = None
    if based_on != "Transparent Pixels":
        x, y = (0, 0) if based_on == "Top Left Pixel Color" else (width - 1, height - 1)
        target = np.asarray(engine.render(document, region=(x, y, 1, 1)), dtype=np.int16)[0, 0]
    left, top, right, bottom = width, height, 0, 0
    step = max(1, min(128, 1_000_000 // width))
    for y in range(0, height, step):
        if cancelled is not None and cancelled():
            raise ValueError("Trim cancelled.")
        image = engine.render(document, region=(0, y, width, min(step, height - y)))
        pixels = np.asarray(image)
        if target is None:
            keep = pixels[:, :, 3] > 0
        else:
            keep = np.any(np.abs(pixels.astype(np.int16) - target) > tolerance, axis=2)
        rows, columns = np.nonzero(keep)
        if rows.size:
            left = min(left, int(columns.min()))
            top = min(top, y + int(rows.min()))
            right = max(right, int(columns.max()) + 1)
            bottom = max(bottom, y + int(rows.max()) + 1)
    if right <= left or bottom <= top:
        return None
    trim_top, trim_bottom, trim_left, trim_right = sides
    x0, y0 = (left if trim_left else 0), (top if trim_top else 0)
    x1, y1 = (right if trim_right else width), (bottom if trim_bottom else height)
    return x0, y0, x1 - x0, y1 - y0


def trim(
    history, based_on="Transparent Pixels", sides=(True, True, True, True), tolerance=0, box=None
):
    if box is None:
        box = trim_bounds(history.document, based_on, sides, tolerance)
    if box is None:
        raise ValueError("No content remained after trimming.")
    crop(history, box, name="Trim")


def resize_image(history, width, height, resolution=None, resample=True):
    dimensions(width, height, raster=resample)
    with history.edit("Image Size") as document:
        if resolution is not None:
            document.resolution = resolution
        if not resample:
            return
        sx, sy = width / document.width, height / document.height
        for layer in document.layers:
            t = layer.transform
            layer.transform = replace(
                t, x=t.x * sx, y=t.y * sy, width=t.width * sx, height=t.height * sy
            )
            if layer.image is not None and layer.live_text is None:
                size = (
                    max(1, round(layer.image.width * sx)),
                    max(1, round(layer.image.height * sy)),
                )
                dimensions(*size, raster=True)
                layer.image = layer.image.resize(size, Image.Resampling.LANCZOS)
            if layer.mask is not None and layer.mask.size != (1, 1):
                layer.mask = layer.mask.resize(
                    (
                        max(1, round(layer.mask.width * sx)),
                        max(1, round(layer.mask.height * sy)),
                    ),
                    Image.Resampling.LANCZOS,
                )
            if layer.mask_transform:
                m = layer.mask_transform
                layer.mask_transform = replace(
                    m, x=m.x * sx, y=m.y * sy, width=m.width * sx, height=m.height * sy
                )
        for guide in document.extras.get("guides", []):
            guide["position"] *= sx if guide["axis"] == "vertical" else sy
        document.width, document.height = width, height
        document.selection = None


def flip_canvas(history, horizontal=True):
    with history.edit("Flip Canvas") as document:
        for layer in document.layers:
            for key in ("transform", "mask_transform"):
                t = getattr(layer, key)
                if t is not None:
                    setattr(
                        layer,
                        key,
                        replace(
                            t,
                            x=document.width - t.x - t.width if horizontal else t.x,
                            y=t.y if horizontal else document.height - t.y - t.height,
                            rotation=-t.rotation,
                            flip_x=not t.flip_x if horizontal else t.flip_x,
                            flip_y=t.flip_y if horizontal else not t.flip_y,
                        ),
                    )
        for guide in document.extras.get("guides", []):
            if (guide["axis"] == "vertical") == horizontal:
                guide["position"] = (document.width if horizontal else document.height) - guide[
                    "position"
                ]
        if document.selection is not None:
            document.selection = (
                ImageOps.mirror(document.selection)
                if horizontal
                else ImageOps.flip(document.selection)
            )


def raster_target(document, mask=False):
    layer = document.layer()
    if layer is None or layer.group and not mask or layer.adjustment and not mask:
        raise ValueError("Select a raster layer, or a layer mask, to paint.")
    if mask:
        if layer.mask is None:
            raise ValueError("Add a mask first.")
        t = layer.mask_transform or layer.transform
        if layer.mask.size == (1, 1):
            size = max(1, round(t.width)), max(1, round(t.height))
            dimensions(*size, raster=True)
            image = layer.mask.resize(size, Image.Resampling.NEAREST)
        else:
            image = layer.mask
    else:
        t = layer.transform
        size = max(1, round(t.width)), max(1, round(t.height))
        if layer.image is None:
            dimensions(*size, raster=True)
            image = Image.new("RGBA", size)
        else:
            image = layer.image
    return layer, image, t


def apply_filter(document, kind, settings, mask=False):
    layer, image, t = raster_target(document, mask)
    if kind == "Content-Aware Fill" and document.selection is not None and not mask:
        bounds = document.selection.getbbox()
        if bounds:
            left, top, right, bottom = bounds
            corners = np.array(
                [[left, top, 1], [right, top, 1], [right, bottom, 1], [left, bottom, 1]]
            )
            local = (np.linalg.inv(pixel_matrix(t, image.size)) @ corners.T).T[:, :2]
            x0, y0 = np.minimum(0, np.floor(local.min(0))).astype(int)
            x1, y1 = np.maximum(image.size, np.ceil(local.max(0))).astype(int)
            size = int(x1 - x0), int(y1 - y0)
            if size != image.size:
                dimensions(*size, raster=True)
                padded = Image.new("RGBA", size)
                padded.paste(image, (-int(x0), -int(y0)))
                if (
                    layer.mask is not None
                    and layer.mask_transform is None
                    and layer.mask.size != (1, 1)
                ):
                    layer.mask_transform = replace(t)
                t = extended_transform(t, image.size, int(x0), int(y0), size)
                layer.transform, image = t, padded
    if not mask and kind in ("Gaussian Blur", "Motion Blur"):
        margin = math.ceil(
            settings.get("radius", 1) * 3 + 2
            if kind == "Gaussian Blur"
            else settings.get("distance", 10) / 2 + 2
        )
        size = image.width + margin * 2, image.height + margin * 2
        dimensions(*size, raster=True)
        padded = Image.new("RGBA", size)
        padded.paste(image, (margin, margin))
        # Padding changes the source grid, not the placement of existing content or masks.
        if layer.mask is not None and layer.mask_transform is None and layer.mask.size != (1, 1):
            layer.mask_transform = replace(t)
        t = extended_transform(t, image.size, -margin, -margin, size)
        layer.transform, image = t, padded
    selection = local_coverage(document, layer, image.size, t)
    if kind == "Content-Aware Fill":
        if mask:
            raise ValueError("Content-Aware Fill operates on image pixels.")
        if document.selection is None:
            raise ValueError("Select the region to fill first.")
        result = kernels.fill(image, selection)
    else:
        if mask:
            source = image.convert("RGBA")
            result = engine.filtered(source, kind, settings).convert("L")
        else:
            result = engine.filtered(image, kind, settings)
        result = Image.composite(result, image, selection)
    if mask:
        layer.mask = result
    else:
        layer.image, layer.shape = result, None


def fill_pixels(history, color, mask=False, erase=False):
    with history.edit("Clear" if erase else "Fill") as document:
        layer, image, t = raster_target(document, mask)
        coverage = local_coverage(document, layer, image.size, t)
        if mask:
            filled = Image.new("L", image.size, 0 if erase else color[0])
            layer.mask = Image.composite(filled, image, coverage)
        else:
            result = Image.composite(Image.new("RGBA", image.size, color), image, coverage)
            if erase:
                result = image.copy()
                result.putalpha(
                    ImageChops.multiply(image.getchannel("A"), ImageOps.invert(coverage))
                )
            layer.image, layer.shape = result, None


def gradient(
    document,
    start,
    end,
    foreground,
    background,
    radial=False,
    transparent=True,
    reversed=False,
    opacity=1,
    mask=False,
):
    layer, image, t = raster_target(document, mask)
    width, height = image.size
    yy, xx = np.indices((height, width), dtype=float)
    a = math.radians(t.rotation % 360)
    u, v = (xx + 0.5) / width - 0.5, (yy + 0.5) / height - 0.5
    if t.flip_x:
        u = -u
    if t.flip_y:
        v = -v
    cx, cy = t.center
    dx = cx + u * t.width * math.cos(a) - v * t.height * math.sin(a) - start[0]
    dy = cy + u * t.width * math.sin(a) + v * t.height * math.cos(a) - start[1]
    vx, vy = end[0] - start[0], end[1] - start[1]
    length = max(0.001, math.hypot(vx, vy))
    f = np.clip(
        np.hypot(dx, dy) / length if radial else (dx * vx + dy * vy) / (length * length),
        0,
        1,
    )
    if reversed:
        f = 1 - f
    if mask:
        result = engine.byte_image(foreground[0] * (1 - f) + background[0] * f)
        coverage = local_coverage(document, layer, image.size, t).point(
            lambda x: round(x * opacity)
        )
    else:
        colors = np.asarray([foreground, background], dtype=float)
        if transparent:
            colors[1] = colors[0]
            colors[1, 3] = 0
        result = engine.byte_image(colors[0] * (1 - f[..., None]) + colors[1] * f[..., None])
        result.putalpha(
            ImageChops.multiply(
                result.getchannel("A"), local_coverage(document, layer, image.size, t)
            ).point(lambda x: round(x * opacity))
        )
        result = Image.alpha_composite(image, result)
        coverage = Image.new("L", image.size, 255)
    result = Image.composite(result, image, coverage)
    if mask:
        layer.mask = result
    else:
        layer.image, layer.shape = result, None


def homography(source, destination):
    rows, values = [], []
    for (x, y), (u, v) in zip(source, destination):
        rows.extend([[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]])
        values.extend([u, v])
    coefficients = np.linalg.solve(np.asarray(rows), values)
    return np.append(coefficients, 1).reshape(3, 3)


def carried_corners(transform, mapping):
    points = np.array([(*transform.point(u, v), 1) for u, v in ((0, 0), (1, 0), (1, 1), (0, 1))])
    mapped = points @ mapping.T
    return mapped[:, :2] / mapped[:, 2:]


def warp_pixels(image, transform, corners, background=0):
    points = np.asarray(corners, float)
    if points.shape != (4, 2) or not np.isfinite(points).all() or abs(points).max() > 1_000_000:
        raise ValueError("Invalid distortion corners.")
    edges = np.roll(points, -1, axis=0) - points
    crosses = edges[:, 0] * np.roll(edges[:, 1], -1) - edges[:, 1] * np.roll(edges[:, 0], -1)
    if np.any(abs(crosses) <= 0.01) or not (np.all(crosses > 0) or np.all(crosses < 0)):
        raise ValueError("Distortion must form a convex, uncollapsed quadrilateral.")
    left, top = np.floor(points.min(0)).astype(int)
    right, bottom = np.ceil(points.max(0)).astype(int)
    size = int(right - left), int(bottom - top)
    dimensions(*size, raster=True)
    placed = Transform(int(left), int(top), *size, sampling=transform.sampling)
    if image.mode == "L" and image.size == (1, 1):
        return image, placed
    pixels = [
        (
            image.width * (1 - u if transform.flip_x else u),
            image.height * (1 - v if transform.flip_y else v),
        )
        for u, v in ((0, 0), (1, 0), (1, 1), (0, 1))
    ]
    mapping = homography(points - [left, top], pixels)
    resampling = (
        Image.Resampling.NEAREST if transform.sampling == "Nearest" else Image.Resampling.BICUBIC
    )
    return image.transform(
        size, Image.Transform.PERSPECTIVE, mapping.flat[:8], resampling, fillcolor=background
    ), placed


def distort(history, corners, mask=False, selected=None):
    with history.edit("Distort") as document:
        active = document.active
        selected = selected or {active}
        box = selection_transform(document, selected, mask)
        unit = ((0, 0), (1, 0), (1, 1), (0, 1))
        mapping = homography([box.point(u, v) for u, v in unit], corners)
        if mask and document.layer().mask is not None and not document.layer().mask_linked:
            layer, image, t = raster_target(document, True)
            layer.mask, layer.mask_transform = warp_pixels(image, t, corners)
            return
        ids = document.descendants(selected)
        for layer in document.layers:
            if layer.id not in ids or layer.group or layer.adjustment:
                continue
            document.active = layer.id
            _, image, t = raster_target(document)
            target = carried_corners(t, mapping)
            output, placed = warp_pixels(image, t, target)
            mask_image, mask_transform = layer.mask, layer.mask_transform or replace(t)
            bounds = output.getchannel("A").getbbox()
            if bounds:
                output = output.crop(bounds)
                placed = replace(
                    placed,
                    x=placed.x + bounds[0],
                    y=placed.y + bounds[1],
                    width=output.width,
                    height=output.height,
                )
            if mask_image is not None:
                if layer.mask_linked:
                    background = 0
                    if layer.mask_transform is not None and mask_image.size != (1, 1):
                        array = np.asarray(mask_image)
                        edge = np.concatenate(
                            (array[0], array[-1], array[1:-1, 0], array[1:-1, -1])
                        )
                        background = 255 if edge.mean() >= 127.5 else 0
                    warped, mask_placement = warp_pixels(
                        mask_image,
                        mask_transform,
                        carried_corners(mask_transform, mapping),
                        background,
                    )
                    layer.mask = (
                        warped
                        if warped.size == (1, 1)
                        else engine.place(
                            warped,
                            mask_placement,
                            output.size,
                            (placed.x, placed.y),
                            fill=background,
                        ).convert("L")
                    )
                    layer.mask_transform = None
                else:
                    layer.mask_transform = mask_transform
            layer.image, layer.shape, layer.transform = output, None, placed
        document.active = active


def pixel_matrix(t, size):
    def point(u, v):
        return t.point(1 - u if t.flip_x else u, 1 - v if t.flip_y else v)

    p, px, py = point(0, 0), point(1, 0), point(0, 1)
    return np.array(
        [
            [(px[0] - p[0]) / size[0], (py[0] - p[0]) / size[1], p[0]],
            [(px[1] - p[1]) / size[0], (py[1] - p[1]) / size[1], p[1]],
            [0, 0, 1],
        ],
        float,
    )


def extended_transform(t, old_size, left, top, new_size):
    u, v = (left + new_size[0] / 2) / old_size[0], (top + new_size[1] / 2) / old_size[1]
    cx, cy = t.point(1 - u if t.flip_x else u, 1 - v if t.flip_y else v)
    width, height = (
        t.width * new_size[0] / old_size[0],
        t.height * new_size[1] / old_size[1],
    )
    return replace(t, x=cx - width / 2, y=cy - height / 2, width=width, height=height)


def selected_pixels(document):
    if document.selection is None or not document.selection.getbbox():
        raise ValueError("Select some pixels first.")
    layer, image, t = raster_target(document, False)
    box = document.selection.getbbox()
    left, top, right, bottom = box
    floating = engine.place(image, t, (right - left, bottom - top), (left, top))
    floating.putalpha(ImageChops.multiply(floating.getchannel("A"), document.selection.crop(box)))
    return floating, Transform(left, top, right - left, bottom - top)


def transform_pixels(history, new_transform, duplicate=False):
    """Transform only the selected pixels, retaining the rest of the source raster."""
    with history.edit(
        "Duplicate Selected Pixels" if duplicate else "Transform Selected Pixels"
    ) as document:
        layer, image, t = raster_target(document, False)
        floating, initial = selected_pixels(document)
        new_transform.validate()
        forward = np.linalg.inv(pixel_matrix(t, image.size)) @ pixel_matrix(
            new_transform, floating.size
        )
        corners = np.array(
            [
                [0, 0, 1],
                [floating.width, 0, 1],
                [floating.width, floating.height, 1],
                [0, floating.height, 1],
            ]
        ).T
        destination = (forward @ corners)[:2]
        left, top = np.minimum(0, np.floor(destination.min(1))).astype(int)
        right, bottom = np.maximum(image.size, np.ceil(destination.max(1))).astype(int)
        size = int(right - left), int(bottom - top)
        dimensions(*size, raster=True)
        base = image.copy()
        if not duplicate:
            coverage = local_coverage(document, layer, image.size, t)
            base.putalpha(ImageChops.multiply(base.getchannel("A"), ImageOps.invert(coverage)))
        expanded = Image.new("RGBA", size)
        expanded.paste(base, (-int(left), -int(top)))
        mapping = np.linalg.inv(forward) @ np.array([[1, 0, left], [0, 1, top], [0, 0, 1]])
        moved = floating.transform(
            size,
            Image.Transform.AFFINE,
            tuple(mapping[:2].reshape(-1)),
            Image.Resampling.BICUBIC,
        )
        layer.image = Image.alpha_composite(expanded, moved)
        layer.shape = None
        if layer.mask is not None and layer.mask_transform is None:
            layer.mask_transform = replace(t)
        layer.transform = extended_transform(t, image.size, left, top, size)
        selection = document.selection.crop(
            (
                round(initial.x),
                round(initial.y),
                round(initial.x + initial.width),
                round(initial.y + initial.height),
            )
        )
        document.selection = engine.place(
            selection, new_transform, (document.width, document.height)
        )


def move_pixels(history, dx, dy, duplicate=False):
    _, t = selected_pixels(history.document)
    transform_pixels(history, replace(t, x=t.x + dx, y=t.y + dy), duplicate)


class Stroke:
    """A coverage-capped stroke; opacity applies once even when dabs overlap."""

    def __init__(
        self,
        document,
        diameter=40,
        hardness=1,
        opacity=1,
        color=(0, 0, 0, 255),
        mask=False,
        mode="Paint",
        source=None,
        blur_radius=10,
    ):
        self.document = document
        self.layer, self.original, self.transform = raster_target(document, mask)
        self.diameter, self.hardness, self.opacity = diameter, hardness, opacity
        self.color, self.mask, self.mode, self.source = color, mask, mode, source
        self.blur_radius = blur_radius
        self.coverage = np.zeros((self.original.height, self.original.width), np.uint8)
        self.selection = (
            np.asarray(
                local_coverage(document, self.layer, self.original.size, self.transform),
                np.uint8,
            )
            if document.selection is not None
            else None
        )
        self.last = None
        self.result = self.original.copy()
        self.last_dab = None

    def append(self, point):
        start = self.last or point
        distance = math.dist(start, point)
        steps = max(1, math.ceil(distance / max(0.5, self.diameter * 0.08)))
        for i in range(1, steps + 1):
            at = (
                start[0] + (point[0] - start[0]) * i / steps,
                start[1] + (point[1] - start[1]) * i / steps,
            )
            self.dab(at)
        self.last = point

    def dab(self, point):
        t = self.transform
        x, y = t.local(*point, self.original.size)
        rx, ry = (
            self.diameter / 2 * self.original.width / t.width,
            self.diameter / 2 * self.original.height / t.height,
        )
        if (
            x - rx < 0
            or y - ry < 0
            or x + rx > self.original.width
            or y + ry > self.original.height
        ):
            left, top = min(0, math.floor(x - rx)), min(0, math.floor(y - ry))
            right, bottom = (
                max(self.original.width, math.ceil(x + rx)),
                max(self.original.height, math.ceil(y + ry)),
            )
            size = right - left, bottom - top
            dimensions(*size, raster=True)
            old_size = self.original.size
            background = round(float(np.asarray(self.original)[0, :].mean())) if self.mask else 0
            original = Image.new("L" if self.mask else "RGBA", size, background)
            original.paste(self.original, (-left, -top))
            result = Image.new("L" if self.mask else "RGBA", size, background)
            result.paste(self.result, (-left, -top))
            coverage = np.zeros(size[::-1], np.uint8)
            coverage[-top : -top + old_size[1], -left : -left + old_size[0]] = self.coverage
            if self.source is not None and not self.mask:
                source = Image.new("RGBA", size)
                source.paste(self.source, (-left, -top))
                self.source = source
            if self.layer.mask is not None and self.layer.mask_transform is None:
                self.layer.mask_transform = replace(t)
            self.transform = extended_transform(t, old_size, left, top, size)
            if self.mask:
                self.layer.mask_transform = self.transform
            else:
                self.layer.transform = self.transform
            self.original, self.result, self.coverage = original, result, coverage
            self.selection = (
                np.asarray(
                    local_coverage(self.document, self.layer, size, self.transform),
                    np.uint8,
                )
                if self.document.selection is not None
                else None
            )
            t = self.transform
            x, y = t.local(*point, size)
        left, top = max(0, math.floor(x - rx - 1)), max(0, math.floor(y - ry - 1))
        right, bottom = (
            min(self.original.width, math.ceil(x + rx + 1)),
            min(self.original.height, math.ceil(y + ry + 1)),
        )
        if left >= right or top >= bottom:
            return
        yy, xx = np.mgrid[top:bottom, left:right]
        radius = np.hypot((xx + 0.5 - x) / max(0.5, rx), (yy + 0.5 - y) / max(0.5, ry))
        soft = np.clip((1 - radius) / max(0.001, 1 - self.hardness), 0, 1)
        weight = soft * soft * (3 - 2 * soft)
        mapping = pixel_matrix(t, self.original.size)
        document_x = mapping[0, 0] * (xx + 0.5) + mapping[0, 1] * (yy + 0.5) + mapping[0, 2]
        document_y = mapping[1, 0] * (xx + 0.5) + mapping[1, 1] * (yy + 0.5) + mapping[1, 2]
        weight *= (
            (document_x >= 0)
            & (document_x < self.document.width)
            & (document_y >= 0)
            & (document_y < self.document.height)
        )
        if self.selection is not None:
            weight *= self.selection[top:bottom, left:right] / 255
        dab = np.clip(np.floor(weight * 255 + 0.5), 0, 255).astype(np.uint8)
        self.coverage[top:bottom, left:right] = np.maximum(
            self.coverage[top:bottom, left:right], dab
        )
        box = left, top, right, bottom
        coverage = engine.byte_image(self.coverage[top:bottom, left:right] * self.opacity)
        original = self.original.crop(box)
        if self.mask:
            color = Image.new("L", original.size, 0 if self.mode == "Erase" else self.color[0])
            self.result.paste(Image.composite(color, original, coverage), (left, top))
            self.layer.mask = self.result
        elif self.mode == "Erase":
            original.putalpha(
                ImageChops.multiply(original.getchannel("A"), ImageOps.invert(coverage))
            )
            self.result.paste(original, (left, top))
        elif self.mode.startswith("Heal"):
            wash = Image.new("RGBA", original.size, (0, 0, 0, 255))
            wash.putalpha(coverage.point(lambda n: n // 3))
            self.result.paste(Image.alpha_composite(original, wash), (left, top))
            self.layer.image = self.result
        elif self.mode == "Blur":
            per_pixel = math.sqrt(
                abs(t.width * t.height / (self.original.width * self.original.height))
            )
            sigma = min(
                max(0.5, self.blur_radius) / max(1e-6, per_pixel), max(self.original.size) / 2
            )
            padding = math.ceil(sigma * 3)
            expanded = (
                max(0, left - padding),
                max(0, top - padding),
                min(self.original.width, right + padding),
                min(self.original.height, bottom + padding),
            )
            blurred = engine.filtered(
                self.original.crop(expanded),
                "Gaussian Blur",
                {"radius": sigma},
            )
            cropped = blurred.crop(
                (
                    left - expanded[0],
                    top - expanded[1],
                    right - expanded[0],
                    bottom - expanded[1],
                )
            )
            self.result.paste(Image.composite(cropped, original, coverage), (left, top))
        elif self.mode == "Clone" and self.source is not None:
            source = self.source.crop(box)
            source.putalpha(ImageChops.multiply(source.getchannel("A"), coverage))
            self.result.paste(Image.alpha_composite(original, source), (left, top))
        elif self.mode == "Replace" and self.source is not None:
            self.result.paste(
                Image.composite(self.source.crop(box), original, coverage), (left, top)
            )
        elif self.mode in ("Smudge", "Liquify"):
            if self.last_dab:
                previous = t.local(*self.last_dab, self.original.size)
                dx, dy = x - previous[0], y - previous[1]
                padding = math.ceil(max(abs(dx), abs(dy))) + 2
                expanded = (
                    max(0, left - padding),
                    max(0, top - padding),
                    min(self.original.width, right + padding),
                    min(self.original.height, bottom + padding),
                )
                data = kernels.premultiply(self.result.crop(expanded))
                yy, xx = np.mgrid[top:bottom, left:right]
                displacement = weight * self.opacity
                moved = np.zeros((bottom - top, right - left, 4), np.uint8)
                for c in range(4):
                    moved[..., c] = map_coordinates(
                        data[..., c],
                        [
                            yy - expanded[1] - dy * displacement,
                            xx - expanded[0] - dx * displacement,
                        ],
                        order=1,
                        mode="constant",
                    )
                self.result.paste(kernels.straight(moved), (left, top))
        else:
            color = Image.new("RGBA", original.size, self.color)
            color.putalpha(coverage)
            self.result.paste(Image.alpha_composite(original, color), (left, top))
        self.last_dab = point
        if not self.mask and not self.mode.startswith("Heal"):
            self.layer.image, self.layer.shape = self.result, None

    def finish(self):
        if self.mode.startswith("Heal") and not self.mask:
            mode = {
                "Heal Content-Aware": 0,
                "Heal Create Texture": 1,
                "Heal Proximity Match": 2,
            }[self.mode]
            self.result = kernels.heal(
                self.original, Image.fromarray(self.coverage), self.opacity, mode
            )
            self.layer.image, self.layer.shape = self.result, None
        return self.result
