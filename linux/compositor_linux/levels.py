"""Original Levels auto modes and black/gray/white calibration."""

import math
from copy import deepcopy

import numpy as np


def automatic(histogram, mode="Contrast"):
    ranges = [{} for _ in range(4)]

    def endpoints(bins):
        cumulative = np.cumsum(bins)
        if cumulative[-1] <= 0:
            return None
        low = int(np.searchsorted(cumulative, cumulative[-1] * 0.001, side="right"))
        high = 255 - int(
            np.searchsorted(np.cumsum(bins[::-1]), cumulative[-1] * 0.001, side="right")
        )
        return (low, high) if low < high else None

    if mode == "Contrast":
        limits = [pair for bins in histogram[1:] if (pair := endpoints(bins))]
        if limits:
            ranges[0] = dict(
                black=min(pair[0] for pair in limits), white=max(pair[1] for pair in limits)
            )
    else:
        for c in range(1, 4):
            if (pair := endpoints(histogram[c])) is None:
                continue
            low, high = pair
            ranges[c] = dict(black=low, white=high)
            if mode == "Color + neutral midtones":
                values = np.clip((np.arange(256) - low) / (high - low), 0, 1)
                mean = float(np.dot(values, histogram[c]) / sum(histogram[c]))
                if 0 < mean < 1:
                    ranges[c]["gamma"] = min(9.99, max(0.1, math.log(mean) / math.log(0.5)))
    return dict(channel="RGB", ranges=ranges)


def sampled(settings, color, mode):
    result = deepcopy(settings)
    result["ranges"][0] = {}
    for c in range(1, 4):
        value = color[c - 1]
        r = result["ranges"][c]
        black, white = r.get("black", 0), r.get("white", 255)
        if mode == "Black":
            r["black"] = min(white - 1, max(0, value))
        elif mode == "White":
            r["white"] = max(black + 1, min(255, value))
        elif mode == "Gray":
            fraction = (value - black) / (white - black)
            if not 0 < fraction < 1:
                continue
            r["gamma"] = min(9.99, max(0.1, math.log(fraction) / math.log(0.5)))
        r.update(outputBlack=0, outputWhite=255)
    return result
