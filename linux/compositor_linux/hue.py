"""Circular hue-band editing translated from HueSaturation.swift."""

import colorsys

NAMES = ("Master", "Reds", "Yellows", "Greens", "Cyans", "Blues", "Magentas")
KEYS = ("falloffStart", "rangeStart", "rangeEnd", "falloffEnd")
DEFAULTS = (
    (0, 0, 360, 360),
    (315, 345, 15, 45),
    (15, 45, 75, 105),
    (75, 105, 135, 165),
    (135, 165, 195, 225),
    (195, 225, 255, 285),
    (255, 285, 315, 345),
)


def band_for(settings, name):
    return settings.get("bands", {}).get(name, dict(zip(KEYS, DEFAULTS[NAMES.index(name)])))


def weight(band, value):
    a, b, c, d = (band[key] for key in KEYS)
    span, position, lead, plateau = (d - a) % 360, (value - a) % 360, (b - a) % 360, (c - a) % 360
    if span == 0:
        return 1.0
    if position > span:
        return 0.0
    if position < lead:
        return position / lead if lead else 1.0
    if position <= plateau:
        return 1.0
    return (span - position) / (span - plateau) if span > plateau else 1.0


def sample_band(band, value, mode):
    a, b, c, d = (band[key] for key in KEYS)
    lead, core, trail = (b - a) % 360, (c - b) % 360, (d - c) % 360
    if mode == "Sample":
        b = value - core / 2
        a, c, d = b - lead, b + core, b + core + trail
    elif mode == "Add" and weight(band, value) < 1:
        if (b - value) % 360 <= (value - c) % 360:
            b, a = value, value - lead
        else:
            c, d = value, value + trail
    elif mode == "Remove" and weight(band, value) > 0:
        if (value - a) % 360 <= (d - value) % 360:
            a, b = value + 1, value + 1 + lead
        else:
            d, c = value - 1, value - 1 - trail
    values = [a % 360, b % 360, c % 360, d % 360]
    if (values[3] - values[0]) % 360 > 350:
        values[3] = (values[0] + 350) % 360
    return dict(zip(KEYS, values))


def moved_handle(band, index, value):
    result = dict(band)
    result[KEYS[index]] = value % 360
    a, b, c, d = (result[key] for key in KEYS)
    span, start, end = (d - a) % 360, (b - a) % 360, (c - a) % 360
    return result if 1 < span <= 350 and start <= end <= span else dict(band)


def sampled_hue(color):
    h, s, _ = colorsys.rgb_to_hsv(*(value / 255 for value in color[:3]))
    return h * 360 if s > 0.02 and color[3] > 0 else None


def owning_range(settings, value):
    def response(name):
        amount = weight(band_for(settings, name), value)
        return (
            1 - amount if settings.get("invertRange") and settings.get("range") == name else amount
        )

    return max(NAMES[1:], key=response)
