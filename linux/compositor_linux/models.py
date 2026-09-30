"""Explicit, checksummed installation of a small local subject-segmentation model."""

import argparse
import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"
MODEL_SHA256 = "309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8"
MODEL_BYTES = 4_574_861


def default_path():
    return (
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        / "compositor/models/u2netp.onnx"
    )


def verified(path):
    path = Path(path)
    return (
        path.is_file()
        and path.stat().st_size == MODEL_BYTES
        and hashlib.sha256(path.read_bytes()).hexdigest() == MODEL_SHA256
    )


def install(path=None):
    destination = Path(path) if path else default_path()
    if verified(destination):
        return destination
    if destination.exists():
        raise ValueError(
            "The model path already contains a different file. Choose another destination."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".u2netp-", dir=destination.parent)
    try:
        digest, total = hashlib.sha256(), 0
        with (
            os.fdopen(fd, "wb") as output,
            urllib.request.urlopen(MODEL_URL, timeout=60) as response,
        ):
            while chunk := response.read(64 * 1024):
                total += len(chunk)
                if total > MODEL_BYTES:
                    raise ValueError("The downloaded model exceeds its expected size.")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total != MODEL_BYTES or digest.hexdigest() != MODEL_SHA256:
            raise ValueError("Model checksum verification failed.")
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def main():
    parser = argparse.ArgumentParser(
        description="Install the pinned U2NETP background model for local inference"
    )
    parser.add_argument("--destination", type=Path, default=None)
    args = parser.parse_args()
    print(install(args.destination))


if __name__ == "__main__":
    main()
