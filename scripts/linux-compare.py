"""Compare a Linux render with an exported reference from the original app."""

import argparse
import json
from pathlib import Path

import numpy as np
from compositor_linux import engine, store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("reference", type=Path)
    parser.add_argument("--tolerance", type=int, default=0)
    parser.add_argument("--render", type=Path, help="Save the Linux render for inspection")
    args = parser.parse_args()
    if not 0 <= args.tolerance <= 255:
        parser.error("Tolerance must be between 0 and 255")
    actual = engine.render(store.load(args.project))
    expected = store.import_image(args.reference)
    if actual.size != expected.size:
        raise SystemExit(f"Size mismatch: Linux {actual.size}, reference {expected.size}")
    difference = np.abs(np.asarray(actual, dtype=np.int16) - np.asarray(expected, dtype=np.int16))
    changed = np.any(difference > args.tolerance, axis=2)
    report = dict(
        size=actual.size,
        tolerance=args.tolerance,
        max_channel_difference=int(difference.max()),
        mean_channel_difference=float(difference.mean()),
        pixels_above_tolerance=int(changed.sum()),
        total_pixels=int(changed.size),
    )
    print(json.dumps(report, indent=2))
    if args.render:
        actual.save(args.render)
    return int(changed.any())


if __name__ == "__main__":
    raise SystemExit(main())
