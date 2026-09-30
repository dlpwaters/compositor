"""Validated v1–v7 .comp packages and atomic Linux package replacement."""

from __future__ import annotations

import ctypes
import errno
import io
import json
import os
import shutil
import stat
import tempfile
import uuid
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

from .model import (
    MAX_PIXELS,
    Document,
    Layer,
    TextContent,
    Transform,
    adjustment_record,
    dimensions,
)

FORMAT = "com.compositor.project"
LAYER_KEYS = {
    "id",
    "name",
    "isVisible",
    "transform",
    "imageFile",
    "parentID",
    "isGroup",
    "opacity",
    "blendMode",
    "maskFile",
    "maskEnabled",
    "maskSourceID",
    "adjustment",
    "maskPlacement",
    "maskLinked",
    "shape",
    "linuxText",
}
DOC_KEYS = {
    "format",
    "version",
    "colorSpace",
    "resolution",
    "documentID",
    "width",
    "height",
    "activeLayerID",
    "layers",
}


def uid(value):
    return str(uuid.UUID(value)).upper() if value is not None else None


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field.")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"Invalid JSON number: {value}")


def checked_file(path, root, limit):
    path, root = Path(path), Path(root).resolve()
    if not path.resolve().is_relative_to(root):
        raise ValueError("Asset escapes the project package.")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("Asset is not a regular file or exceeds the size limit.")
    return path


def load(path):
    root = Path(path)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("A .comp project is a directory containing manifest.json and images/.")
    metadata = checked_file(root / "manifest.json", root, 4 * 1024 * 1024)
    try:
        m = json.loads(
            metadata.read_bytes(),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
        if m["format"] != FORMAT or type(m["version"]) is not int or not 1 <= m["version"] <= 7:
            raise ValueError("Unsupported project format. Compositor supports versions 1–7.")
        if (
            m["colorSpace"] != "sRGB"
            or not isinstance(m["layers"], list)
            or len(m["layers"]) > 10_000
        ):
            raise ValueError("Invalid project metadata.")
        dimensions(m["width"], m["height"])
        document = Document(
            m["width"],
            m["height"],
            id=uid(m["documentID"]),
            resolution=m.get("resolution") if m.get("resolution") is not None else 72,
            active=uid(m.get("activeLayerID")),
            extras={k: v for k, v in m.items() if k not in DOC_KEYS},
        )
        pixels = [0, 0]
        for record in m["layers"]:
            id = uid(record["id"])
            layer = Layer(
                record["name"],
                Transform.from_record(record["transform"]),
                id=id,
                visible=record["isVisible"],
                parent=uid(record.get("parentID")),
                group=record.get("isGroup") if record.get("isGroup") is not None else False,
                opacity=record.get("opacity") if record.get("opacity") is not None else 1,
                blend=record.get("blendMode") if record.get("blendMode") is not None else "Normal",
                mask_enabled=record.get("maskEnabled")
                if record.get("maskEnabled") is not None
                else True,
                mask_source=uid(record.get("maskSourceID")),
                mask_linked=record.get("maskLinked")
                if record.get("maskLinked") is not None
                else True,
                adjustment=record.get("adjustment"),
                shape=record.get("shape"),
                extras={k: v for k, v in record.items() if k not in LAYER_KEYS},
            )
            if type(layer.mask_enabled) is not bool or type(layer.mask_linked) is not bool:
                raise ValueError("Invalid mask flags.")
            if record.get("maskPlacement") is not None:
                layer.mask_transform = Transform.from_record(record["maskPlacement"])
            version = m["version"]
            if (
                version == 1
                and (layer.parent is not None or layer.group)
                or version < 3
                and (layer.opacity != 1 or layer.blend != "Normal")
                or version < 5
                and layer.mask_source is not None
                or version < 7
                and layer.adjustment is not None
            ):
                raise ValueError("Layer features exceed the declared format version.")
            for index, key in enumerate(("imageFile", "maskFile")):
                filename = record.get(key)
                if filename is None:
                    if index and record.get("maskEnabled") is not None:
                        raise ValueError("Mask flag without a mask asset.")
                    continue
                expected = f"{id}{'.mask' if index else ''}.png"
                if filename != expected or index and version < (6 if layer.group else 4):
                    raise ValueError("Invalid asset filename or mask format version.")
                asset_path = checked_file(root / "images" / filename, root, 512 * 1024 * 1024)
                with asset_path.open("rb") as header:
                    png_header = header.read(26)
                if len(png_header) < 26 or png_header[24] > 8:
                    raise ValueError("Projects support PNG channel depths up to 8 bits.")
                with Image.open(asset_path) as asset:
                    if (
                        asset.format != "PNG"
                        or getattr(asset, "n_frames", 1) != 1
                        or asset.mode in ("I", "I;16", "F")
                    ):
                        raise ValueError("Projects must contain single-frame 8-bit PNG assets.")
                    dimensions(*asset.size, raster=True)
                    pixels[index] += asset.width * asset.height
                    if pixels[index] > MAX_PIXELS:
                        raise ValueError("Project exceeds the 100-megapixel image or mask budget.")
                    if index and asset.mode != "L":
                        raise ValueError("Masks must be 8-bit grayscale without alpha.")
                    asset.load()
                    if index:
                        layer.mask = asset.copy()
                    else:
                        layer.image = asset.convert("RGBA")
            if record.get("linuxText") is not None:
                layer.text = TextContent(record["linuxText"], layer.image)
            document.layers.append(layer)
        document.validate()
        return document
    except (
        KeyError,
        TypeError,
        IndexError,
        OverflowError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("Invalid or damaged project metadata.") from exc


def manifest(document):
    document.validate()
    m = dict(document.extras)
    m.update(
        format=FORMAT,
        version=7,
        colorSpace="sRGB",
        resolution=document.resolution,
        documentID=document.id,
        width=document.width,
        height=document.height,
        layers=[],
    )
    if document.active is not None:
        m["activeLayerID"] = document.active
    for layer in document.layers:
        r = dict(layer.extras)
        r.update(
            id=layer.id,
            name=layer.name,
            isVisible=layer.visible,
            transform=layer.transform.record(),
            isGroup=layer.group,
            opacity=layer.opacity,
            blendMode=layer.blend,
        )
        for key, value in (
            ("parentID", layer.parent),
            ("imageFile", f"{layer.id}.png" if layer.image is not None else None),
            ("maskSourceID", layer.mask_source),
            (
                "adjustment",
                adjustment_record(layer.adjustment) if layer.adjustment else None,
            ),
            ("shape", layer.shape),
            ("linuxText", layer.live_text.settings if layer.live_text else None),
        ):
            if value is not None:
                r[key] = value
        if layer.mask is not None:
            r.update(
                maskFile=f"{layer.id}.mask.png",
                maskEnabled=layer.mask_enabled,
                maskLinked=layer.mask_linked,
            )
            if layer.mask_transform:
                r["maskPlacement"] = layer.mask_transform.record()
        m["layers"].append(r)
    return m


def sync_file(path):
    with open(path, "rb") as stream:
        os.fsync(stream.fileno())


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def exchange(source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOSYS, "Atomic directory exchange is unavailable on this platform.")
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 2):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def save(document, path):
    destination = Path(path).absolute()
    if destination.suffix.lower() != ".comp":
        raise ValueError("Project directory names must end in .comp.")
    # Never replace an arbitrary existing folder or follow a destination symlink.
    if destination.is_symlink():
        raise ValueError("Cannot replace a symlink with a project.")
    if destination.exists():
        load(destination)
    metadata = json.dumps(manifest(document), indent=2, sort_keys=True, allow_nan=False).encode()
    if len(metadata) > 4 * 1024 * 1024:
        raise ValueError("Project manifest exceeds 4 MiB.")
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        (stage / "images").mkdir()
        for layer in document.layers:
            for suffix, asset in (("", layer.image), (".mask", layer.mask)):
                if asset is None:
                    continue
                output = stage / "images" / f"{layer.id}{suffix}.png"
                asset.save(output, format="PNG")
                if output.stat().st_size > 512 * 1024 * 1024:
                    raise ValueError("Encoded asset exceeds 512 MiB.")
                sync_file(output)
        (stage / "manifest.json").write_bytes(metadata)
        sync_file(stage / "manifest.json")
        sync_directory(stage / "images")
        sync_directory(stage)
        if destination.exists():
            exchange(stage, destination)
        else:
            os.rename(stage, destination)
        sync_directory(destination.parent)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def import_image(path):
    from pillow_heif import register_heif_opener

    register_heif_opener()
    with Image.open(path) as opened:
        dimensions(*opened.size, raster=True)
        image = ImageOps.exif_transpose(opened)
        profile = image.info.get("icc_profile")
        if profile:
            try:
                image = ImageCms.profileToProfile(
                    image,
                    ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                    ImageCms.createProfile("sRGB"),
                    outputMode="RGBA",
                )
            except (ImageCms.PyCMSError, OSError) as exc:
                raise ValueError(
                    "The image's embedded color profile could not be converted to sRGB."
                ) from exc
        image = image.convert("RGBA")
        image.load()
        return image


def export_image(document, path, format="PNG", quality=90, matte=(255, 255, 255)):
    from .engine import render

    dimensions(document.width, document.height, raster=True)
    image = render(document)
    destination = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}-", dir=destination.parent)
    os.close(fd)
    try:
        options = dict(
            dpi=(document.resolution, document.resolution),
            icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes(),
        )
        if format.upper() == "JPEG":
            white = Image.new("RGBA", image.size, (*matte, 255))
            image = Image.alpha_composite(white, image).convert("RGB")
            options.update(quality=max(1, min(100, quality)), subsampling=0)
        image.save(temporary, format=format, **options)
        sync_file(temporary)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_export_bytes(path, data):
    destination = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
