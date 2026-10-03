"""Camera Raw CPU pipeline using upstream AdjustPixels.c kernels."""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

from . import kernels
from .model import finite

LIGHT = (
    "temperature",
    "tint",
    "exposure",
    "contrast",
    "highlights",
    "shadows",
    "whites",
    "blacks",
    "vibrance",
    "saturation",
)
EFFECTS = (
    "texture",
    "clarity",
    "dehaze",
    "glow",
    "glowRange",
    "glowSpread",
    "glowWarmth",
    "vignetteAmount",
    "vignetteMidpoint",
    "vignetteRoundness",
    "vignetteFeather",
    "vignetteHighlights",
    "grainAmount",
    "grainSize",
    "grainRoughness",
)
DEFAULTS = dict.fromkeys(LIGHT + EFFECTS, 0)
DEFAULTS.update(
    vignetteMidpoint=50,
    vignetteFeather=50,
    grainSize=25,
    grainRoughness=50,
    glowStyle="Diffusion",
    vignetteStyle="Highlight Priority",
    grainSeed=0,
)
GROUPS = {"curve", "mixer", "grading", "detail", "optics", "geometry", "calibration"}
IDENTITY_POINTS = [{"x": 0, "y": 0}, {"x": 1, "y": 1}]


def _validate(settings):
    # FilterDialog includes these editor metadata fields in every filter payload.
    allowed = (
        set(DEFAULTS) | GROUPS | {"glowStyle", "vignetteStyle", "whiteBalance", "kind", "preview"}
    )
    if not isinstance(settings, dict) or set(settings) - allowed:
        raise ValueError("Unsupported Camera Raw setting.")
    if "kind" in settings and settings["kind"] != "Camera Raw Filter":
        raise ValueError("Invalid Camera Raw filter kind.")
    if "preview" in settings and type(settings["preview"]) is not bool:
        raise ValueError("Invalid Camera Raw preview flag.")
    for name in LIGHT + EFFECTS:
        value = settings.get(name, DEFAULTS[name])
        low, high = (-5, 5) if name == "exposure" else (-100, 100)
        if name in (
            "glow",
            "vignetteMidpoint",
            "vignetteFeather",
            "vignetteHighlights",
            "grainAmount",
            "grainSize",
            "grainRoughness",
        ):
            low, high = 0, 100
        if not finite(value, low, high):
            raise ValueError(f"Invalid Camera Raw {name}.")
    for name in GROUPS:
        if name in settings and not isinstance(settings[name], dict):
            raise ValueError(f"Invalid Camera Raw {name} settings.")
    if settings.get("glowStyle", "Diffusion") not in ("Diffusion", "Bloom", "Halation"):
        raise ValueError("Invalid Camera Raw glow style.")
    if settings.get("vignetteStyle", "Highlight Priority") not in (
        "Highlight Priority",
        "Color Priority",
        "Paint Overlay",
    ):
        raise ValueError("Invalid Camera Raw vignette style.")
    if settings.get("whiteBalance", "Custom") not in ("Custom", "Auto"):
        raise ValueError("Invalid Camera Raw white balance mode.")
    if (
        type(settings.get("grainSeed", 0)) is not int
        or not 0 <= settings.get("grainSeed", 0) <= 0xFFFFFFFF
    ):
        raise ValueError("Invalid Camera Raw grain seed.")
    for group, ranges in (
        (
            "calibration",
            {
                key: (-100, 100)
                for key in (
                    "shadowTint",
                    "redHue",
                    "redSaturation",
                    "greenHue",
                    "greenSaturation",
                    "blueHue",
                    "blueSaturation",
                )
            },
        ),
        (
            "detail",
            {
                key: ((0, 150) if key == "sharpenAmount" else (0, 100))
                for key in (
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
                )
            },
        ),
        (
            "optics",
            {
                key: (
                    (-100, 100)
                    if key in ("distortion", "vignetteAmount")
                    else (0, 360)
                    if "Hue" in key
                    else (0, 100)
                )
                for key in (
                    "profileDistortion",
                    "profileVignetting",
                    "distortion",
                    "purpleAmount",
                    "purpleHueLow",
                    "purpleHueHigh",
                    "greenAmount",
                    "greenHueLow",
                    "greenHueHigh",
                    "vignetteAmount",
                    "vignetteMidpoint",
                )
            },
        ),
        (
            "curve",
            {
                key: ((0, 100) if key.endswith("Split") else (-100, 100))
                for key in (
                    "shadows",
                    "darks",
                    "lights",
                    "highlights",
                    "shadowSplit",
                    "darkSplit",
                    "lightSplit",
                    "refineSaturation",
                )
            },
        ),
    ):
        for key, value in settings.get(group, {}).items():
            if key in ranges and not finite(value, *ranges[key]):
                raise ValueError(f"Invalid Camera Raw {group} {key}.")
    calibration = settings.get("calibration", {})
    if calibration.get("process", "Version 6") not in tuple(f"Version {i}" for i in range(1, 7)):
        raise ValueError("Invalid Camera Raw calibration process.")
    grading = settings.get("grading", {})
    if not finite(grading.get("blending", 50), 0, 100) or not finite(
        grading.get("balance", 0), -100, 100
    ):
        raise ValueError("Invalid Camera Raw grading control.")
    optics = settings.get("optics", {})
    if any(
        type(optics[key]) is not bool
        for key in ("removeChromaticAberration", "enableLensProfile")
        if key in optics
    ):
        raise ValueError("Invalid Camera Raw optics toggle.")


def _point_table(points):
    if points is None:
        points = IDENTITY_POINTS
    if not isinstance(points, list) or not 2 <= len(points) <= 32:
        raise ValueError("Invalid Camera Raw curve points.")
    pairs = []
    for point in points:
        if (
            not isinstance(point, dict)
            or not finite(point.get("x"), 0, 1)
            or not finite(point.get("y"), 0, 1)
        ):
            raise ValueError("Invalid Camera Raw curve point.")
        pairs.append((point["x"], point["y"]))
    pairs.sort()
    if any(b[0] <= a[0] for a, b in zip(pairs, pairs[1:])):
        raise ValueError("Camera Raw curve points must have distinct positions.")
    return np.ascontiguousarray(
        np.interp(np.linspace(0, 1, 256), [p[0] for p in pairs], [p[1] for p in pairs]),
        dtype=np.float32,
    )


def _curve_arrays(settings):
    curve = settings.get("curve", {})
    if set(curve) - {
        "rgb",
        "red",
        "green",
        "blue",
        "shadows",
        "darks",
        "lights",
        "highlights",
        "shadowSplit",
        "darkSplit",
        "lightSplit",
        "refineSaturation",
    }:
        raise ValueError("Unsupported Camera Raw curve setting.")
    table = _point_table(curve.get("rgb"))
    # Four parametric bends follow CameraRawCurveSettings.bend. The shared C
    # kernel receives their completed lookup table, just as it does on macOS.
    tone = np.linspace(0, 1, 256)
    for lower, low, upper, high in (
        (
            curve.get("shadowSplit", 25) / 100,
            curve.get("shadows", 0),
            curve.get("lightSplit", 75) / 100,
            curve.get("highlights", 0),
        ),
        (
            curve.get("darkSplit", 50) / 100,
            curve.get("darks", 0),
            curve.get("darkSplit", 50) / 100,
            curve.get("lights", 0),
        ),
    ):
        if not all(finite(v) for v in (lower, low, upper, high)):
            raise ValueError("Invalid Camera Raw parametric curve.")
        if low and lower > 0:
            mask = tone < lower
            tone[mask] = lower * np.power(tone[mask] / lower, 2 ** (-low / 100 * 1.66))
        if high and upper < 1:
            mask = tone > upper
            tone[mask] = 1 - (1 - upper) * np.power(
                (1 - tone[mask]) / (1 - upper), 2 ** (high / 100 * 1.66)
            )
    table = np.ascontiguousarray(np.interp(tone, np.linspace(0, 1, 256), table), dtype=np.float32)
    channels = [_point_table(curve.get(key)) for key in ("red", "green", "blue")]
    return table, *channels


def _color_arrays(settings):
    mixer = settings.get("mixer", {})
    if set(mixer) - {"hue", "saturation", "luminance", "points"}:
        raise ValueError("Unsupported Camera Raw mixer setting.")
    groups = []
    for key in ("hue", "saturation", "luminance"):
        values = mixer.get(key, [0] * 8)
        if (
            not isinstance(values, list)
            or len(values) != 8
            or any(not finite(v, -100, 100) for v in values)
        ):
            raise ValueError("Invalid Camera Raw mixer values.")
        groups.extend(v / 100 for v in values)
    colors = np.ascontiguousarray(groups, dtype=np.float32)
    points = mixer.get("points", [])
    if (
        not isinstance(points, list)
        or len(points) > 8
        or any(not isinstance(p, dict) for p in points)
    ):
        raise ValueError("Invalid Camera Raw point colors.")
    point_values = []
    for point in points:
        ranges = {
            "hue": (0, 360),
            "saturation": (0, 1),
            "luminance": (0, 1),
            "hueShift": (-100, 100),
            "saturationShift": (-100, 100),
            "luminanceShift": (-100, 100),
            "hueRange": (5, 180),
            "saturationRange": (0.05, 1),
            "luminanceRange": (0.05, 1),
        }
        if (
            set(point) - set(ranges) - {"visualize"}
            or any(not finite(value, *ranges[key]) for key, value in point.items() if key in ranges)
            or ("visualize" in point and type(point["visualize"]) is not bool)
        ):
            raise ValueError("Invalid Camera Raw point color.")
        for key, scale, default in (
            ("hue", 360, 0),
            ("saturation", 1, 0),
            ("luminance", 1, 0),
            ("hueShift", 100, 0),
            ("saturationShift", 100, 0),
            ("luminanceShift", 100, 0),
            ("hueRange", 360, 30),
            ("saturationRange", 1, 0.4),
            ("luminanceRange", 1, 0.4),
        ):
            value = point.get(key, default)
            if not finite(value, -100, 360):
                raise ValueError("Invalid Camera Raw point color.")
            point_values.append(value / scale)
    point_array = np.ascontiguousarray(point_values or [0] * 9, dtype=np.float32)
    grading = settings.get("grading", {})
    if set(grading) - {"shadows", "midtones", "highlights", "global", "blending", "balance"}:
        raise ValueError("Unsupported Camera Raw grading setting.")
    wheels = []
    for key in ("shadows", "midtones", "highlights", "global"):
        wheel = grading.get(key, {})
        if set(wheel) - {"hue", "saturation", "luminance"}:
            raise ValueError("Unsupported Camera Raw grading wheel setting.")
        for field, scale, low, high in (
            ("hue", 360, 0, 360),
            ("saturation", 100, 0, 100),
            ("luminance", 100, -100, 100),
        ):
            value = wheel.get(field, 0)
            if not finite(value, low, high):
                raise ValueError("Invalid Camera Raw grading wheel.")
            wheels.append(value / scale)
    return colors, len(points), point_array, np.ascontiguousarray(wheels, dtype=np.float32)


def _geometry(image, settings):
    allowed = {
        "upright",
        "projection",
        "vertical",
        "horizontal",
        "rotate",
        "aspect",
        "scale",
        "offsetX",
        "offsetY",
        "constrainCrop",
        "guides",
    }
    if set(settings) - allowed:
        raise ValueError("Unsupported Camera Raw geometry setting.")
    if settings.get("upright", "Off") not in ("Off", "Guided") or settings.get(
        "projection", "Perspective"
    ) not in ("Perspective", "Rectilinear"):
        raise ValueError("Invalid Camera Raw geometry mode.")
    controls = {
        k: settings.get(k, 0)
        for k in ("vertical", "horizontal", "rotate", "aspect", "scale", "offsetX", "offsetY")
    }
    if any(
        not finite(v, -45 if k == "rotate" else -100, 45 if k == "rotate" else 100)
        for k, v in controls.items()
    ):
        raise ValueError("Invalid Camera Raw geometry control.")
    guides = settings.get("guides", [])
    if (
        not isinstance(guides, list)
        or len(guides) > 8
        or any(not isinstance(g, dict) for g in guides)
    ):
        raise ValueError("Invalid Camera Raw geometry guides.")
    if type(settings.get("constrainCrop", False)) is not bool or any(
        set(guide) - {"startX", "startY", "endX", "endY"}
        or any(not finite(guide.get(key), 0, 1) for key in ("startX", "startY", "endX", "endY"))
        for guide in guides
    ):
        raise ValueError("Invalid Camera Raw geometry guide or crop setting.")
    if settings.get("upright") == "Guided" and guides:
        first = guides[0]
        if not isinstance(first, dict) or any(
            not finite(first.get(k), 0, 1) for k in ("startX", "startY", "endX", "endY")
        ):
            raise ValueError("Invalid Camera Raw guide coordinates.")
        dx, dy = first["endX"] - first["startX"], first["endY"] - first["startY"]
        if math.hypot(dx, dy) > 0.01:
            angle = -math.degrees(math.atan2(dy, dx))
            if angle > 45:
                angle -= 90
            elif angle < -45:
                angle += 90
            controls["rotate"] += angle
    if not any(controls.values()):
        return image
    width, height = image.size
    strength = 1 if settings.get("projection", "Perspective") == "Perspective" else 0.55
    vertical = controls["vertical"] / 100 * width * 0.18 * strength
    horizontal = controls["horizontal"] / 100 * height * 0.18 * strength
    shift_x = controls["offsetX"] / 100 * width * 0.15
    shift_y = controls["offsetY"] / 100 * height * 0.15
    # Top-down coordinates here; Swift's Core Image positions use bottom-up.
    corners = np.array(
        [
            [-vertical + shift_x, -shift_y],
            [width + vertical + shift_x, -shift_y],
            [width + horizontal + shift_x, height + shift_y],
            [-horizontal + shift_x, height + shift_y],
        ],
        dtype=float,
    )
    center = np.array([width / 2 + shift_x, height / 2 + shift_y])
    theta = math.radians(-controls["rotate"])
    rotation = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
    corners = (corners - center) @ rotation.T
    aspect = 1 + controls["aspect"] / 200
    corners *= (aspect * (1 + controls["scale"] / 100), (1 + controls["scale"] / 100) / aspect)
    corners += center
    source = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=float)
    equations, targets = [], []
    for (x, y), (u, v) in zip(corners, source):
        equations.extend(([x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]))
        targets.extend((u, v))
    try:
        coefficients = np.linalg.solve(np.asarray(equations), np.asarray(targets))
    except np.linalg.LinAlgError as exc:
        raise ValueError("Camera Raw geometry cannot be projected.") from exc
    output = image.transform(
        image.size, Image.Transform.PERSPECTIVE, tuple(coefficients), Image.Resampling.BICUBIC
    )
    if settings.get("constrainCrop") and output.getchannel("A").getbbox():
        bounds = output.getchannel("A").getbbox()
        if bounds != (0, 0, width, height):
            cut = output.crop(bounds)
            ratio = min(width / cut.width, height / cut.height)
            resized = cut.resize(
                (max(1, round(cut.width * ratio)), max(1, round(cut.height * ratio))),
                Image.Resampling.BICUBIC,
            )
            output = Image.new("RGBA", image.size)
            output.paste(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    return output


def _auto_white_balance(image):
    pixels = np.asarray(image.convert("RGBA"), dtype=np.float32)
    opaque = pixels[:, :, 3] > 0
    if not opaque.any():
        return None
    channels = pixels[opaque, :3] / 255
    linear = np.where(channels <= 0.04045, channels / 12.92, ((channels + 0.055) / 1.055) ** 2.4)
    red, green, blue = np.mean(linear, axis=0, dtype=np.float64)
    if min(red, green, blue) <= 1e-4:
        return None
    a1, b1, c1 = 0.35 * red, 0.15 * red + 0.30 * green, green - red
    a2, b2, c2 = -0.35 * blue, 0.15 * blue + 0.30 * green, green - blue
    determinant = a1 * b2 - a2 * b1
    if abs(determinant) <= 1e-8:
        return None
    warm = (c1 * b2 - c2 * b1) / determinant
    magenta = (a1 * c2 - a2 * c1) / determinant
    if not math.isfinite(warm) or not math.isfinite(magenta):
        return None
    return max(-100, min(100, warm * 100)), max(-100, min(100, magenta * 100))


def apply(image, settings):
    _validate(settings)
    if settings.get("whiteBalance") == "Auto":
        auto = _auto_white_balance(image)
        if auto is not None:
            settings = dict(settings, temperature=auto[0], tint=auto[1])
    if not settings or not any(
        value
        for key, value in settings.items()
        if key not in ("whiteBalance", "glowStyle", "vignetteStyle")
    ):
        return image.copy()
    image = _geometry(image, settings.get("geometry", {}))
    data = kernels.premultiply(image)
    height, width = data.shape[:2]
    lib = kernels.library()
    ptr, stride = data.ctypes.data, width * 4
    c = settings.get("calibration", {})
    calibration_fields = (
        "shadowTint",
        "redHue",
        "redSaturation",
        "greenHue",
        "greenSaturation",
        "blueHue",
        "blueSaturation",
    )
    if set(c) - set(calibration_fields) - {"process"}:
        raise ValueError("Unsupported Camera Raw calibration setting.")
    if any(c.get(k, 0) for k in calibration_fields):
        values = [c.get(k, 0) for k in calibration_fields]
        if any(not finite(v, -100, 100) for v in values):
            raise ValueError("Invalid Camera Raw calibration value.")
        lib.adjust_camera_raw_calibration(
            ptr,
            width,
            height,
            stride,
            *values,
            int(c.get("process", "Version 6").split()[-1]),
        )
    temperature, tint = settings.get("temperature", 0) / 100, settings.get("tint", 0) / 100
    gains = (
        1 + 0.35 * temperature + 0.15 * tint,
        1 - 0.30 * tint,
        1 - 0.35 * temperature + 0.15 * tint,
    )
    if any(settings.get(k, 0) for k in LIGHT):
        lib.adjust_camera_raw(
            ptr, width, height, stride, *gains, *(settings.get(k, 0) for k in LIGHT[2:]), 0
        )
    if any(settings.get(name) for name in ("curve", "mixer", "grading")):
        tone, red, green, blue = _curve_arrays(settings)
        mixer, point_count, points, grade = _color_arrays(settings)
        lib.adjust_camera_raw_curve_color(
            ptr,
            width,
            height,
            stride,
            tone.ctypes.data,
            red.ctypes.data,
            green.ctypes.data,
            blue.ctypes.data,
            settings.get("curve", {}).get("refineSaturation", 0) / 100,
            mixer.ctypes.data,
            point_count,
            points.ctypes.data,
            grade.ctypes.data,
            settings.get("grading", {}).get("blending", 50) / 100,
            settings.get("grading", {}).get("balance", 0) / 100,
            -1,
        )
    if any(settings.get(k, 0) for k in EFFECTS[:8]) or settings.get("vignetteAmount", 0):
        lib.adjust_camera_raw_effects(
            ptr,
            width,
            height,
            stride,
            *(settings.get(k, 0) for k in ("texture", "clarity", "dehaze", "glow")),
            ("Diffusion", "Bloom", "Halation").index(settings.get("glowStyle", "Diffusion")),
            *(
                settings.get(k, 0)
                for k in ("glowRange", "glowSpread", "glowWarmth", "vignetteAmount")
            ),
            settings.get("vignetteMidpoint", 50),
            settings.get("vignetteRoundness", 0),
            settings.get("vignetteFeather", 50),
            settings.get("vignetteHighlights", 0),
            ("Highlight Priority", "Color Priority", "Paint Overlay").index(
                settings.get("vignetteStyle", "Highlight Priority")
            ),
            1,
        )
    if settings.get("grainAmount", 0):
        lib.adjust_grain(
            ptr,
            width,
            height,
            stride,
            settings["grainAmount"],
            0.5 + settings.get("grainSize", 25) / 100 * 19.5,
            settings.get("grainRoughness", 50),
            settings.get("grainSeed", 0),
            0,
            0,
            1,
        )
    o = settings.get("optics", {})
    optical_fields = (
        "profileDistortion",
        "profileVignetting",
        "distortion",
        "purpleAmount",
        "purpleHueLow",
        "purpleHueHigh",
        "greenAmount",
        "greenHueLow",
        "greenHueHigh",
        "vignetteAmount",
        "vignetteMidpoint",
    )
    if set(o) - set(optical_fields) - {"removeChromaticAberration", "enableLensProfile"}:
        raise ValueError("Unsupported Camera Raw optics setting.")
    if o and (
        o.get("removeChromaticAberration")
        or o.get("enableLensProfile")
        or any(o.get(k, 0) for k in ("distortion", "purpleAmount", "greenAmount", "vignetteAmount"))
    ):
        lib.adjust_camera_raw_optics(
            ptr,
            width,
            height,
            stride,
            int(o.get("removeChromaticAberration", False)),
            int(o.get("enableLensProfile", False)),
            o.get("profileDistortion", 100),
            o.get("profileVignetting", 100),
            o.get("distortion", 0) / 100 * 0.35
            + (o.get("profileDistortion", 100) / 100 * 0.35 if o.get("enableLensProfile") else 0),
            o.get("purpleAmount", 0),
            o.get("purpleHueLow", 270),
            o.get("purpleHueHigh", 310),
            o.get("greenAmount", 0),
            o.get("greenHueLow", 60),
            o.get("greenHueHigh", 120),
            o.get("vignetteAmount", 0),
            o.get("vignetteMidpoint", 50),
            1,
        )
    d = settings.get("detail", {})
    detail_fields = (
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
    )
    if set(d) - set(detail_fields):
        raise ValueError("Unsupported Camera Raw detail setting.")
    if d and any(d.get(k, 0) for k in ("sharpenAmount", "noiseLuminance", "noiseColor")):
        lib.adjust_camera_raw_detail(
            ptr,
            width,
            height,
            stride,
            d.get("sharpenAmount", 0),
            d.get("sharpenRadius", 10),
            d.get("sharpenDetail", 25),
            d.get("sharpenMasking", 0),
            d.get("noiseLuminance", 0),
            d.get("noiseLuminanceDetail", 50),
            d.get("noiseLuminanceContrast", 0),
            d.get("noiseColor", 0),
            d.get("noiseColorDetail", 50),
            d.get("noiseColorSmoothness", 50),
            1,
        )
    return kernels.straight(data)
